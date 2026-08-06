from __future__ import annotations

import json
import re
from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field

from agentflow.lite.agent import AgentResult

_PROMPT_REF = re.compile(r"\{\{\s*nodes\.([A-Za-z0-9_\-]+)\.text\s*\}\}")


class EdgeSpec(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    from_: str = Field(alias="from")
    to: str


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
