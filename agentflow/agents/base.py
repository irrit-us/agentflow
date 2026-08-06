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
