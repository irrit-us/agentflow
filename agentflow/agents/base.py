from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path, PurePosixPath
from typing import Any

from agentflow.prepared import ExecutionPaths, PreparedExecution
from agentflow.specs import AgentKind, NodeSpec, ProviderConfig, resolve_execution_provider


class AgentAdapter(ABC):
    @abstractmethod
    def prepare(self, node: NodeSpec, prompt: str, paths: ExecutionPaths) -> PreparedExecution:
        raise NotImplementedError

    def provider_config(self, value: str | ProviderConfig | None, agent: str | AgentKind) -> ProviderConfig | None:
        return resolve_execution_provider(value, agent)

    def validate_node_features(self, node: NodeSpec) -> None:
        """Validate direct NodeSpec callers as well as ActorNode profile bindings."""
        from agentflow.profiles import AgentProfile, BACKEND_CAPABILITIES
        kind = str(node.agent)
        if kind not in BACKEND_CAPABILITIES:
            return
        provider = self.provider_config(node.provider, node.agent)
        construct = AgentProfile if node.target.kind == "docker" or node.agent_profile else AgentProfile.model_construct
        profile = construct(name="resolved-node", agent=kind, model=node.model,
                               provider=provider, model_settings=node.model_settings,
                               mcps=node.mcps, extensions=node.extensions, skills=node.skills,
                               secret_env=node.secret_env, tools=node.tools, extra_args=node.extra_args)
        profile.validate_capabilities(target_kind=node.target.kind)
        if node.target.kind == "docker" and provider and provider.api_key_env:
            if provider.api_key_env in node.env:
                raise ValueError("Docker provider credentials must use explicit secret file references")

    def merge_env(self, *parts: dict[str, str]) -> dict[str, str]:
        merged: dict[str, str] = {}
        for part in parts:
            merged.update({key: value for key, value in part.items() if value is not None})
        return merged

    def quote_json(self, value: Any) -> str:
        import json

        return json.dumps(value, ensure_ascii=False)

    def relative_runtime_file(self, *parts: str) -> str:
        """Return a runtime-file key in POSIX form (target-side relative path)."""
        return str(PurePosixPath(*parts))

    def target_path(self, paths: ExecutionPaths, *parts: str) -> str:
        """Join a path as the execution target sees it.

        Remote targets (container/ssh/ec2/ecs) use POSIX-style paths even when
        the orchestrator host is Windows; local targets share the host
        filesystem.
        """
        base = paths.target_runtime_dir
        if "/" in base and not (len(base) >= 2 and base[1] == ":"):
            return str(PurePosixPath(base, *parts))
        return str(Path(base, *parts))
