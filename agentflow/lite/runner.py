from __future__ import annotations

import threading
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, wait, FIRST_COMPLETED
from typing import Literal

from pydantic import BaseModel, ConfigDict

from agentflow.lite.agent import AgentResult, LiteAgent
from agentflow.lite.client import LiteLLMClient
from agentflow.lite.container import DockerExecutor, container_shell_tool
from agentflow.lite.graph import GraphSpec, NodeSpec, resolve_prompt
from agentflow.lite.router import ModelRouter
from agentflow.lite.tools import ToolRegistry

NodeStatus = Literal["preparing", "processing", "finished", "errored"]

_TERMINAL: tuple[NodeStatus, NodeStatus] = ("finished", "errored")


class NodeRun(BaseModel):
    model_config = ConfigDict(extra="forbid")

    spec: NodeSpec
    status: NodeStatus = "preparing"
    started_at: float | None = None
    finished_at: float | None = None
    result: AgentResult | None = None
    error: str | None = None


class RunEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ts: float
    node_id: str
    status: NodeStatus
    detail: str | None = None


class RunState:
    """Thread-safe mutable state for one graph run."""

    def __init__(self, graph: GraphSpec):
        self._lock = threading.Lock()
        self.nodes: dict[str, NodeRun] = {node.id: NodeRun(spec=node) for node in graph.nodes}
        self.events: list[RunEvent] = []

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

    def set_result(self, node_id: str, result: AgentResult) -> None:
        with self._lock:
            self.nodes[node_id].result = result

    def set_error(self, node_id: str, error: str) -> None:
        with self._lock:
            self.nodes[node_id].error = error

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
        agent_factory: Callable[[NodeSpec], LiteAgent],
        max_workers: int = 4,
    ):
        self.graph = graph
        self.agent_factory = agent_factory
        self.max_workers = max_workers
        self.state = RunState(graph)

    def is_done(self) -> bool:
        return all(nrun.status in _TERMINAL for nrun in self.state.snapshot()["nodes"].values())

    def blocked(self) -> list[dict]:
        snapshot = self.state.snapshot()["nodes"]
        blocked: list[dict] = []
        for nid, nrun in snapshot.items():
            if nrun.status != "preparing":
                continue
            waiting_on = [
                dep for dep in self.graph.dependencies(nid) if snapshot[dep].status != "finished"
            ]
            if waiting_on:
                blocked.append({"node_id": nid, "waiting_on": waiting_on})
        return blocked

    def _run_node(self, node_id: str) -> None:
        nrun = self.state.snapshot()["nodes"][node_id]
        self.state.set_status(node_id, "processing")
        try:
            agent = self.agent_factory(nrun.spec)
            prompt = resolve_prompt(nrun.spec, self.state.results())
            result = agent.run(prompt)
            self.state.set_result(node_id, result)
            self.state.set_status(node_id, "finished")
        except Exception as exc:  # noqa: BLE001 - node failures are captured in state
            self.state.set_error(node_id, str(exc))
            self.state.set_status(node_id, "errored", detail=str(exc))

    def run(self) -> RunState:
        futures: dict = {}
        with ThreadPoolExecutor(max_workers=self.max_workers) as pool:
            while not self.is_done():
                progressed = False
                snapshot = self.state.snapshot()["nodes"]
                for nid, nrun in snapshot.items():
                    if nrun.status != "preparing" or nid in futures.values():
                        continue
                    deps = self.graph.dependencies(nid)
                    failed = [d for d in deps if snapshot[d].status == "errored"]
                    if failed:
                        error = f"upstream failed: {', '.join(failed)}"
                        self.state.set_error(nid, error)
                        self.state.set_status(nid, "errored", detail=error)
                        progressed = True
                    elif all(snapshot[d].status == "finished" for d in deps):
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
        selected = []
        if registry is not None and spec.tools:
            for name in spec.tools:
                item = registry.get(name)
                if item is None:
                    raise ValueError(f"unknown tool '{name}' requested by node '{spec.id}'")
                selected.append(item)
        if spec.container is not None:
            # Per-node sandbox: the agent can run shell commands in its own container.
            selected.append(container_shell_tool(DockerExecutor(spec.container)))
        tools = ToolRegistry(selected) if selected else None
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
