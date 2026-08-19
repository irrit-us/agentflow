from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator

from agentflow.lite.agent import AgentResult
from agentflow.lite.container import ContainerConfig

_PROMPT_REF = re.compile(r"\{\{\s*nodes\.([A-Za-z0-9_\-]+)\.text\s*\}\}")
_ITEM_VAR = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


class EdgeSpec(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    from_: str = Field(alias="from")
    to: str


class FanOutSpec(BaseModel):
    """Expand one node into one runtime task per upstream JSON item."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    from_: str = Field(alias="from")
    items_path: str | None = None
    item_var: str = "item"
    max_items: int | None = Field(default=None, ge=1)


class NestedConcurrencySpec(BaseModel):
    """Concurrency policy enforced inside a nested worker runtime."""

    model_config = ConfigDict(extra="forbid")

    max_concurrent_requests: int = Field(ge=1)
    pools: dict[str, int] = Field(default_factory=dict)

    @field_validator("pools")
    @classmethod
    def validate_pools(cls, pools: dict[str, int]) -> dict[str, int]:
        invalid_names = sorted(name for name in pools if not name.strip())
        if invalid_names:
            raise ValueError("nested concurrency pool names must not be empty")
        invalid_limits = sorted(name for name, limit in pools.items() if limit < 1)
        if invalid_limits:
            raise ValueError(
                "nested concurrency pool limits must be positive: "
                + ", ".join(invalid_limits)
            )
        return pools

    def pool_limit(self, name: str, default: int) -> int:
        return self.pools.get(name, default)


class NodeSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    prompt: str
    system_prompt: str | None = None
    role: str | None = None
    model: str | None = None
    tools: list[str] = Field(default_factory=list)
    depends_on: list[str] = Field(default_factory=list)
    max_iterations: int | None = None
    max_total_tokens: int | None = None
    container: ContainerConfig | None = None
    fanout: FanOutSpec | None = None
    resource: str = Field(default="default", min_length=1)
    priority: int = 0
    max_attempts: int = Field(default=1, ge=1)
    nested_concurrency: NestedConcurrencySpec | None = None


class GraphSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = "pipeline"
    nodes: list[NodeSpec]
    edges: list[EdgeSpec] = Field(default_factory=list)

    def all_edges(self) -> list[tuple[str, str]]:
        seen: set[tuple[str, str]] = set()
        merged: list[tuple[str, str]] = []
        for edge in self.edges:
            pair = (edge.from_, edge.to)
            if pair not in seen:
                seen.add(pair)
                merged.append(pair)
        for node in self.nodes:
            for dep in node.depends_on:
                pair = (dep, node.id)
                if pair not in seen:
                    seen.add(pair)
                    merged.append(pair)
            if node.fanout is not None:
                pair = (node.fanout.from_, node.id)
                if pair not in seen:
                    seen.add(pair)
                    merged.append(pair)
        return merged

    def dependencies(self, node_id: str) -> list[str]:
        return [src for src, dst in self.all_edges() if dst == node_id]

    def validate_graph(self) -> None:
        ids = [node.id for node in self.nodes]
        duplicates = sorted({nid for nid in ids if ids.count(nid) > 1})
        if duplicates:
            raise ValueError(f"duplicate node ids: {', '.join(duplicates)}")
        known = set(ids)
        unknown = sorted({ep for edge in self.all_edges() for ep in edge} - known)
        if unknown:
            raise ValueError(f"edges reference unknown nodes: {', '.join(unknown)}")
        invalid_vars = sorted(
            node.id
            for node in self.nodes
            if node.fanout is not None and not _ITEM_VAR.fullmatch(node.fanout.item_var)
        )
        if invalid_vars:
            raise ValueError(f"fanout item_var is invalid for nodes: {', '.join(invalid_vars)}")
        self.topo_order()  # raises on cycles

    def topo_order(self) -> list[str]:
        indegree = {node.id: 0 for node in self.nodes}
        adjacent: dict[str, list[str]] = {node.id: [] for node in self.nodes}
        for src, dst in self.all_edges():
            if src not in indegree or dst not in indegree:
                continue  # unknown endpoints are reported by validate_graph
            indegree[dst] += 1
            adjacent[src].append(dst)
        ready = [node.id for node in self.nodes if indegree[node.id] == 0]
        order: list[str] = []
        while ready:
            nid = ready.pop(0)
            order.append(nid)
            for nxt in adjacent[nid]:
                indegree[nxt] -= 1
                if indegree[nxt] == 0:
                    ready.append(nxt)
        if len(order) != len(self.nodes):
            remaining = sorted(nid for nid, deg in indegree.items() if deg > 0)
            raise ValueError(f"graph contains a cycle involving nodes: {', '.join(remaining)}")
        return order


def load_graph(path: str | Path) -> GraphSpec:
    source = Path(path)
    text = source.read_text(encoding="utf-8")
    suffix = source.suffix.lower()
    if suffix in (".yaml", ".yml"):
        data = yaml.safe_load(text)
    elif suffix == ".json":
        data = json.loads(text)
    else:
        raise ValueError(f"unsupported graph file extension: {source.suffix}")
    graph = GraphSpec.model_validate(data)
    graph.validate_graph()
    return graph


def resolve_prompt(node: NodeSpec, results: dict[str, AgentResult]) -> str:
    def replace(match: re.Match[str]) -> str:
        node_id = match.group(1)
        result = results.get(node_id)
        if result is None:
            raise ValueError(
                f"prompt of node '{node.id}' references node '{node_id}' "
                "which has no completed result"
            )
        return result.text

    return _PROMPT_REF.sub(replace, node.prompt)


def fanout_items(node: NodeSpec, results: dict[str, AgentResult]) -> list[Any]:
    """Read and validate the JSON item list declared by ``node.fanout``."""

    spec = node.fanout
    if spec is None:
        raise ValueError(f"node '{node.id}' does not declare fanout")
    result = results.get(spec.from_)
    if result is None:
        raise ValueError(
            f"fanout of node '{node.id}' references node '{spec.from_}' "
            "which has no completed result"
        )
    try:
        value: Any = json.loads(result.text)
    except json.JSONDecodeError as exc:
        raise ValueError(
            f"fanout source '{spec.from_}' did not return valid JSON: {exc.msg}"
        ) from exc
    if spec.items_path:
        for part in spec.items_path.split("."):
            if not isinstance(value, dict) or part not in value:
                raise ValueError(
                    f"fanout items_path '{spec.items_path}' is missing in source '{spec.from_}'"
                )
            value = value[part]
    if not isinstance(value, list):
        raise ValueError(f"fanout source for node '{node.id}' must resolve to a JSON list")
    if spec.max_items is not None and len(value) > spec.max_items:
        raise ValueError(
            f"fanout node '{node.id}' received {len(value)} items, above max_items={spec.max_items}"
        )
    return value


def render_fanout_prompt(node: NodeSpec, item: Any) -> str:
    """Render ``{{ item }}`` and dotted item fields for one fan-out child."""

    spec = node.fanout
    if spec is None:
        raise ValueError(f"node '{node.id}' does not declare fanout")
    pattern = re.compile(
        r"\{\{\s*" + re.escape(spec.item_var) + r"(?:\.([A-Za-z0-9_.\-]+))?\s*\}\}"
    )

    def replace(match: re.Match[str]) -> str:
        value = item
        path = match.group(1)
        if path:
            for part in path.split("."):
                if not isinstance(value, dict) or part not in value:
                    raise ValueError(
                        f"fanout item for node '{node.id}' has no field '{path}'"
                    )
                value = value[part]
        if isinstance(value, str):
            return value
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))

    return pattern.sub(replace, node.prompt)
