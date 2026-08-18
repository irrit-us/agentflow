from __future__ import annotations

import hashlib
import json
import threading
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, wait, FIRST_COMPLETED
from pathlib import Path
from typing import Any, Literal, Protocol

from pydantic import BaseModel, ConfigDict

from agentflow.lite.agent import AgentResult, LiteAgent
from agentflow.lite.client import LiteLLMClient
from agentflow.lite.container import DockerExecutor, container_shell_tool
from agentflow.lite.graph import (
    GraphSpec,
    NodeSpec,
    fanout_items,
    render_fanout_prompt,
    resolve_prompt,
)
from agentflow.lite.router import ModelRouter
from agentflow.lite.tools import ToolRegistry
from agentflow.lite.types import Usage

NodeStatus = Literal["preparing", "processing", "finished", "errored"]

_TERMINAL: tuple[NodeStatus, NodeStatus] = ("finished", "errored")


class _RunnableAgent(Protocol):
    def run(self, user_input: str) -> AgentResult: ...


class NodeRun(BaseModel):
    model_config = ConfigDict(extra="forbid")

    spec: NodeSpec
    status: NodeStatus = "preparing"
    started_at: float | None = None
    finished_at: float | None = None
    result: AgentResult | None = None
    error: str | None = None
    attempts: int = 0
    fanout_parent: str | None = None
    fanout_item: Any = None


class RunEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ts: float
    node_id: str
    status: NodeStatus
    detail: str | None = None


class _StoredRun(BaseModel):
    model_config = ConfigDict(extra="forbid")

    graph_hash: str
    nodes: dict[str, NodeRun]
    events: list[RunEvent]


def _graph_hash(graph: GraphSpec) -> str:
    payload = json.dumps(
        graph.model_dump(mode="json", by_alias=True),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


class RunState:
    """Thread-safe mutable state for one graph run."""

    def __init__(
        self,
        graph: GraphSpec,
        *,
        state_path: str | Path | None = None,
        resume: bool = False,
    ):
        self._lock = threading.Lock()
        self._graph_hash = _graph_hash(graph)
        self._state_path = Path(state_path) if state_path is not None else None
        if resume and self._state_path is not None and self._state_path.is_file():
            stored = _StoredRun.model_validate_json(self._state_path.read_text(encoding="utf-8"))
            if stored.graph_hash != self._graph_hash:
                raise ValueError("saved run state does not match the current graph")
            self.nodes = stored.nodes
            self.events = stored.events
            for nrun in self.nodes.values():
                if nrun.status == "processing":
                    nrun.status = "preparing"
                    nrun.started_at = None
                    nrun.finished_at = None
        else:
            self.nodes = {node.id: NodeRun(spec=node) for node in graph.nodes}
            self.events = []
        with self._lock:
            self._persist_locked()

    def _persist_locked(self) -> None:
        if self._state_path is None:
            return
        self._state_path.parent.mkdir(parents=True, exist_ok=True)
        stored = _StoredRun(
            graph_hash=self._graph_hash,
            nodes=self.nodes,
            events=self.events,
        )
        temporary = self._state_path.with_name(self._state_path.name + ".tmp")
        temporary.write_text(stored.model_dump_json(indent=2), encoding="utf-8")
        temporary.replace(self._state_path)

    def add_node(self, node: NodeRun) -> None:
        with self._lock:
            if node.spec.id in self.nodes:
                raise ValueError(f"run state already contains node '{node.spec.id}'")
            self.nodes[node.spec.id] = node
            self._persist_locked()

    def start_attempt(self, node_id: str) -> None:
        with self._lock:
            self.nodes[node_id].attempts += 1
            self._persist_locked()

    def set_status(self, node_id: str, status: NodeStatus, detail: str | None = None) -> None:
        with self._lock:
            nrun = self.nodes[node_id]
            nrun.status = status
            ts = time.time()
            if status == "processing" and nrun.started_at is None:
                nrun.started_at = ts
            if status in _TERMINAL:
                nrun.finished_at = ts
            self.events.append(RunEvent(ts=ts, node_id=node_id, status=status, detail=detail))
            self._persist_locked()

    def set_result(self, node_id: str, result: AgentResult) -> None:
        with self._lock:
            self.nodes[node_id].result = result
            self.nodes[node_id].error = None
            self._persist_locked()

    def set_error(self, node_id: str, error: str) -> None:
        with self._lock:
            self.nodes[node_id].error = error
            self._persist_locked()

    def results(self) -> dict[str, AgentResult]:
        with self._lock:
            return {
                nid: nrun.result
                for nid, nrun in self.nodes.items()
                if nrun.result is not None
            }

    def snapshot(self) -> dict:
        with self._lock:
            return {
                "nodes": {nid: nrun.model_copy(deep=True) for nid, nrun in self.nodes.items()},
                "events": [event.model_copy(deep=True) for event in self.events],
            }


class GraphRunner:
    """Executes a :class:`GraphSpec` with dependency-ordered parallelism."""

    def __init__(
        self,
        graph: GraphSpec,
        agent_factory: Callable[[NodeSpec], _RunnableAgent],
        max_workers: int = 4,
        *,
        resource_limits: dict[str, int] | None = None,
        state_path: str | Path | None = None,
        resume: bool = False,
    ):
        if max_workers < 1:
            raise ValueError("max_workers must be at least 1")
        invalid_limits = sorted(
            name for name, limit in (resource_limits or {}).items() if not name or limit < 1
        )
        if invalid_limits:
            raise ValueError(f"resource limits must be positive: {', '.join(invalid_limits)}")
        if resume and state_path is None:
            raise ValueError("resume requires state_path")
        self.graph = graph
        self.agent_factory = agent_factory
        self.max_workers = max_workers
        self.resource_limits = dict(resource_limits or {})
        self.state = RunState(graph, state_path=state_path, resume=resume)

    def is_done(self) -> bool:
        return all(nrun.status in _TERMINAL for nrun in self.state.snapshot()["nodes"].values())

    def blocked(self) -> list[dict]:
        snapshot = self.state.snapshot()["nodes"]
        blocked: list[dict] = []
        for nid, nrun in snapshot.items():
            if nrun.status != "preparing":
                continue
            waiting_on = [
                dep for dep in self._dependencies(nrun) if snapshot[dep].status != "finished"
            ]
            if waiting_on:
                blocked.append({"node_id": nid, "waiting_on": waiting_on})
        return blocked

    def runtime_edges(self) -> list[tuple[str, str]]:
        """Return declared edges plus relationships created by runtime fan-out."""

        edges = list(self.graph.all_edges())
        seen = set(edges)
        for nrun in self.state.snapshot()["nodes"].values():
            if nrun.fanout_parent is None:
                continue
            for dependency in nrun.spec.depends_on:
                edge = (dependency, nrun.spec.id)
                if edge not in seen:
                    seen.add(edge)
                    edges.append(edge)
            barrier = (nrun.spec.id, nrun.fanout_parent)
            if barrier not in seen:
                seen.add(barrier)
                edges.append(barrier)
        return edges

    def _run_node(self, node_id: str) -> None:
        nrun = self.state.snapshot()["nodes"][node_id]
        self.state.start_attempt(node_id)
        self.state.set_status(node_id, "processing")
        try:
            agent = self.agent_factory(nrun.spec)
            prompt = resolve_prompt(nrun.spec, self.state.results())
            result = agent.run(prompt)
            self.state.set_result(node_id, result)
            self.state.set_status(node_id, "finished")
        except Exception as exc:  # noqa: BLE001 - node failures are captured in state
            self.state.set_error(node_id, str(exc))
            attempts = self.state.snapshot()["nodes"][node_id].attempts
            if attempts < nrun.spec.max_attempts:
                detail = f"attempt {attempts}/{nrun.spec.max_attempts} failed: {exc}"
                self.state.set_status(node_id, "preparing", detail=detail)
            else:
                self.state.set_status(node_id, "errored", detail=str(exc))

    def _dependencies(self, nrun: NodeRun) -> list[str]:
        if nrun.fanout_parent is not None:
            return list(nrun.spec.depends_on)
        return self.graph.dependencies(nrun.spec.id)

    def _children(self, parent_id: str, snapshot: dict[str, NodeRun]) -> list[NodeRun]:
        return [nrun for nrun in snapshot.values() if nrun.fanout_parent == parent_id]

    def _expand_fanout(self, node_id: str, nrun: NodeRun) -> None:
        items = fanout_items(nrun.spec, self.state.results())
        dependencies = self.graph.dependencies(node_id)
        expected: list[tuple[str, Any, NodeSpec]] = []
        for index, item in enumerate(items, start=1):
            child_id = f"{node_id}--{index:04d}"
            child_spec = nrun.spec.model_copy(
                update={
                    "id": child_id,
                    "prompt": render_fanout_prompt(nrun.spec, item),
                    "depends_on": dependencies,
                    "fanout": None,
                },
                deep=True,
            )
            expected.append((child_id, item, child_spec))

        existing = {
            child.spec.id: child
            for child in self._children(node_id, self.state.snapshot()["nodes"])
        }
        expected_ids = {child_id for child_id, _, _ in expected}
        unexpected = sorted(set(existing) - expected_ids)
        if unexpected:
            raise ValueError(
                f"fanout node '{node_id}' has unexpected persisted tasks: "
                + ", ".join(unexpected)
            )
        for child_id, item, child_spec in expected:
            child = existing.get(child_id)
            if child is not None and (
                child.fanout_item != item or child.spec != child_spec
            ):
                raise ValueError(
                    f"fanout task '{child_id}' does not match its persisted source item"
                )

        created = 0
        for child_id, item, child_spec in expected:
            if child_id in existing:
                continue
            self.state.add_node(
                NodeRun(spec=child_spec, fanout_parent=node_id, fanout_item=item)
            )
            created += 1

        if existing:
            detail = (
                f"resumed {len(existing)} fanout task(s); "
                f"restored {created} missing task(s)"
            )
        else:
            detail = f"expanded {len(items)} fanout task(s)"
        self.state.set_status(
            node_id,
            "processing",
            detail=detail,
        )
        if not items:
            self.state.set_result(node_id, self._aggregate_fanout([]))
            self.state.set_status(node_id, "finished", detail="fanout completed with no items")

    @staticmethod
    def _aggregate_fanout(children: list[NodeRun]) -> AgentResult:
        payload = [
            {
                "node_id": child.spec.id,
                "item": child.fanout_item,
                "text": child.result.text if child.result is not None else "",
            }
            for child in children
        ]
        usage = Usage()
        iterations = 0
        for child in children:
            if child.result is not None:
                usage += child.result.usage
                iterations += child.result.iterations
        return AgentResult(
            text=json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
            messages=[],
            usage=usage,
            iterations=iterations,
            finish_reason="fanout",
        )

    def _finalize_fanouts(self) -> bool:
        progressed = False
        snapshot = self.state.snapshot()["nodes"]
        for node_id, nrun in snapshot.items():
            if nrun.spec.fanout is None or nrun.status != "processing":
                continue
            children = self._children(node_id, snapshot)
            failed = [child.spec.id for child in children if child.status == "errored"]
            if failed:
                error = f"fanout tasks failed: {', '.join(failed)}"
                self.state.set_error(node_id, error)
                self.state.set_status(node_id, "errored", detail=error)
                progressed = True
            elif children and all(child.status == "finished" for child in children):
                self.state.set_result(node_id, self._aggregate_fanout(children))
                self.state.set_status(
                    node_id,
                    "finished",
                    detail=f"completed {len(children)} fanout task(s)",
                )
                progressed = True
        return progressed

    def _resource_available(
        self,
        nrun: NodeRun,
        futures: dict,
        snapshot: dict[str, NodeRun],
    ) -> bool:
        limit = self.resource_limits.get(nrun.spec.resource, self.max_workers)
        in_use = sum(
            1
            for running_id in futures.values()
            if snapshot[running_id].spec.resource == nrun.spec.resource
        )
        return in_use < limit

    def run(self) -> RunState:
        futures: dict = {}
        with ThreadPoolExecutor(max_workers=self.max_workers) as pool:
            while not self.is_done():
                progressed = self._finalize_fanouts()
                snapshot = self.state.snapshot()["nodes"]
                candidates = sorted(
                    snapshot.items(),
                    key=lambda item: (-item[1].spec.priority, item[0]),
                )
                for nid, nrun in candidates:
                    if len(futures) >= self.max_workers:
                        break
                    if nrun.status != "preparing" or nid in futures.values():
                        continue
                    deps = self._dependencies(nrun)
                    failed = [d for d in deps if snapshot[d].status == "errored"]
                    if failed:
                        error = f"upstream failed: {', '.join(failed)}"
                        self.state.set_error(nid, error)
                        self.state.set_status(nid, "errored", detail=error)
                        progressed = True
                    elif all(snapshot[d].status == "finished" for d in deps):
                        if nrun.spec.fanout is not None:
                            try:
                                self._expand_fanout(nid, nrun)
                            except Exception as exc:  # noqa: BLE001 - expansion is node work
                                self.state.set_error(nid, str(exc))
                                self.state.set_status(nid, "errored", detail=str(exc))
                            progressed = True
                            snapshot = self.state.snapshot()["nodes"]
                        elif self._resource_available(nrun, futures, snapshot):
                            futures[pool.submit(self._run_node, nid)] = nid
                            progressed = True
                if futures:
                    done, _ = wait(list(futures), return_when=FIRST_COMPLETED)
                    for future in done:
                        del futures[future]
                elif not progressed:
                    break  # defensive: invalid graph would otherwise spin forever
        return self.state

    def run_in_background(self) -> threading.Thread:
        thread = threading.Thread(target=self.run, daemon=True)
        thread.start()
        return thread


def make_agent_factory(
    *,
    client: LiteLLMClient | None = None,
    router: ModelRouter | None = None,
    default_model: str | None = None,
    registry: ToolRegistry | None = None,
    default_role: str | None = None,
) -> Callable[[NodeSpec], LiteAgent]:
    if (client is None) == (router is None):
        raise ValueError("pass exactly one of `client` or `router`")

    def factory(spec: NodeSpec) -> LiteAgent:
        tools = None
        if registry is not None and spec.tools:
            for name in spec.tools:
                if registry.get(name) is None:
                    raise ValueError(f"unknown tool '{name}' requested by node '{spec.id}'")
            tools = registry.subset(spec.tools)
        if spec.container is not None:
            # Per-node sandbox: the agent can run shell commands in its own container.
            if tools is None:
                tools = ToolRegistry()
            tools.register(container_shell_tool(DockerExecutor(spec.container)))
        kwargs: dict = {
            "system_prompt": spec.system_prompt,
            "tools": tools,
            "max_iterations": spec.max_iterations or 8,
            "max_total_tokens": spec.max_total_tokens,
        }
        if router is not None:
            return LiteAgent(router=router, role=spec.role or default_role, **kwargs)
        model = spec.model or default_model
        if model is None:
            raise ValueError(f"node '{spec.id}' needs a model (node.model or default_model)")
        return LiteAgent(client=client, model=model, **kwargs)

    return factory
