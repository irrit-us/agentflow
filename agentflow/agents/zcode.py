from __future__ import annotations

import os

from agentflow.agents.base import AgentAdapter
from agentflow.env import merge_env_layers
from agentflow.prepared import ExecutionPaths, PreparedExecution
from agentflow.specs import NodeSpec, RepoInstructionsMode, ToolAccess


class ZCodeAdapter(AgentAdapter):
    """Run a node through ZCode's non-interactive prompt interface."""

    _SUPPORTED_MODES = {"build", "edit", "plan", "yolo"}

    def _resolve_mode(self, node: NodeSpec, env: dict[str, str]) -> str:
        override = (env.pop("AGENTFLOW_ZCODE_MODE", "") or "").strip().lower()
        if not override:
            return "plan" if node.tools == ToolAccess.READ_ONLY else "yolo"
        if override not in self._SUPPORTED_MODES:
            raise ValueError(
                "AGENTFLOW_ZCODE_MODE must be one of: "
                + ", ".join(sorted(self._SUPPORTED_MODES))
            )
        return override

    def prepare(self, node: NodeSpec, prompt: str, paths: ExecutionPaths) -> PreparedExecution:
        if node.provider is not None or node.model is not None:
            raise ValueError(
                "ZCode owns provider and model selection in its settings; configure them in "
                "~/.zcode/cli/config.json instead of on the AgentFlow node"
            )
        if node.mcps:
            raise ValueError(
                "ZCode does not accept node-scoped MCP configuration; configure MCP services "
                "in ZCode's user or workspace settings"
            )
        if node.repo_instructions_mode == RepoInstructionsMode.IGNORE:
            raise ValueError(
                "ZCode does not support repo_instructions_mode='ignore'; it loads AGENTS.md "
                "from the user and workspace roots"
            )

        executable = node.executable or os.getenv("AGENTFLOW_ZCODE_EXECUTABLE") or "zcode"
        env = merge_env_layers(node.env)
        mode = self._resolve_mode(node, env)
        command = [
            executable,
            "--json",
            "--no-color",
            "--mode",
            mode,
        ]
        # ZCode's headless parser requires prompt text to remain the final option.
        command.extend(node.extra_args)
        command.extend(["--prompt", prompt])

        return PreparedExecution(
            command=command,
            env=env,
            cwd=paths.target_workdir,
            trace_kind="zcode",
        )
