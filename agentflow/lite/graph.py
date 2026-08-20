from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from agentflow.lite.agent import AgentResult
from agentflow.lite.concurrency import ExternalResourceSettings, ResourceRequest
from agentflow.lite.container import ContainerConfig

_PROMPT_REF = re.compile(r"\{\{\s*nodes\.([A-Za-z0-9_\-]+)\.text\s*\}\}")
_ITEM_VAR = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

NodeTriggerMode = Literal["input_ready", "output_idle", "input_and_output"]


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
    skills: list[str] = Field(default_factory=list)
    depends_on: list[str] = Field(default_factory=list)
    max_iterations: int | None = Field(default=None, ge=1)
    max_tool_iterations: int | None = Field(default=None, ge=0)
    max_total_tokens: int | None = Field(default=None, ge=1)
    container: ContainerConfig | None = None
    fanout: FanOutSpec | None = None
    resource: str = Field(default="default", min_length=1)
    resources: list[ResourceRequest] = Field(default_factory=list)
    trigger_mode: NodeTriggerMode = "input_ready"
    priority: int = 0
    max_attempts: int = Field(default=1, ge=1)
    nested_concurrency: NestedConcurrencySpec | None = None

    @field_validator("resource")
    @classmethod
    def validate_resource(cls, resource: str) -> str:
        if not resource.strip():
            raise ValueError("resource name must not be blank")
        return resource

    @model_validator(mode="after")
    def validate_resource_requests(self) -> NodeSpec:
        seen: dict[str, str] = {}
        for request in self.resources:
            previous = seen.get(request.name)
            if previous is not None:
                if previous != request.access:
                    raise ValueError(
                        f"resource '{request.name}' has conflicting access modes"
                    )
                raise ValueError(f"resource '{request.name}' is requested more than once")
            seen[request.name] = request.access
        return self

    def resource_requests(self) -> list[ResourceRequest]:
        """Return explicit requests or the legacy single resource request."""

        if self.resources:
            return list(self.resources)
        return [ResourceRequest(name=self.resource)]


class GraphSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = "pipeline"
    nodes: list[NodeSpec]
    edges: list[EdgeSpec] = Field(default_factory=list)
    resource_settings: dict[str, ExternalResourceSettings] = Field(default_factory=dict)

    @field_validator("resource_settings")
    @classmethod
    def validate_resource_settings(
        cls, settings: dict[str, ExternalResourceSettings]
    ) -> dict[str, ExternalResourceSettings]:
        if any(not name.strip() for name in settings):
            raise ValueError("external resource names must not be blank")
        return settings

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
        invalid_output_triggers = sorted(
            node.id
            for node in self.nodes
            if node.trigger_mode == "output_idle" and self.dependencies(node.id)
        )
        if invalid_output_triggers:
            raise ValueError(
                "output_idle nodes cannot declare input dependencies: "
                + ", ".join(invalid_output_triggers)
            )
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
    except json.JSONDecodeError as direct_exc:
        # OpenAI-compatible models sometimes obey the JSON schema but wrap it
        # in one Markdown code fence, occasionally with a short preamble. Keep
        # this fallback narrow: accept only a single fenced block that itself
        # parses as JSON; never heuristically slice arbitrary braces.
        fences = re.findall(r"```(?:json)?\s*(.*?)```", result.text, re.DOTALL | re.IGNORECASE)
        if len(fences) != 1:
            raise ValueError(
                f"fanout source '{spec.from_}' did not return valid JSON: "
                f"{direct_exc.msg}"
            ) from direct_exc
        try:
            value = json.loads(fences[0])
        except json.JSONDecodeError as fenced_exc:
            raise ValueError(
                f"fanout source '{spec.from_}' did not return valid JSON: "
                f"{fenced_exc.msg}"
            ) from fenced_exc
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


def render_item_template(text: str, item_var: str, item: Any, node_id: str) -> str:
    """Render ``{{ item }}`` and dotted item fields in an arbitrary string."""
    pattern = re.compile(
        r"\{\{\s*" + re.escape(item_var) + r"(?:\.([A-Za-z0-9_.\-]+))?\s*\}\}"
    )

    def replace(match: re.Match[str]) -> str:
        value = item
        path = match.group(1)
        if path:
            for part in path.split("."):
                if not isinstance(value, dict) or part not in value:
                    raise ValueError(
                        f"fanout item for node '{node_id}' has no field '{path}'"
                    )
                value = value[part]
        if isinstance(value, str):
            return value
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))

    return pattern.sub(replace, text)


def render_fanout_prompt(node: NodeSpec, item: Any) -> str:
    """Render ``{{ item }}`` and dotted item fields for one fan-out child."""

    spec = node.fanout
    if spec is None:
        raise ValueError(f"node '{node.id}' does not declare fanout")
    return render_item_template(node.prompt, spec.item_var, item, node.id)


def render_fanout_container(
    container: ContainerConfig, node: NodeSpec, item: Any
) -> ContainerConfig:
    """Render item placeholders in mount sources/targets and env values.

    Lets each fan-out child receive its own isolated volumes (e.g. a
    per-item scratch volume ``vuln-poc-{{ item.alert_id }}``) instead of
    sharing one writable volume with all siblings.
    """
    spec = node.fanout
    if spec is None:
        raise ValueError(f"node '{node.id}' does not declare fanout")
    rendered = container.model_copy(deep=True)
    for mount in rendered.mounts:
        if mount.source is not None:
            mount.source = render_item_template(mount.source, spec.item_var, item, node.id)
        mount.target = render_item_template(mount.target, spec.item_var, item, node.id)
    rendered.env = {
        key: render_item_template(value, spec.item_var, item, node.id)
        for key, value in rendered.env.items()
    }
    return rendered
