from __future__ import annotations

import os

from agentflow.agents.base import AgentAdapter
from agentflow.env import merge_env_layers
from agentflow.prepared import ExecutionPaths, PreparedExecution
from agentflow.specs import NodeSpec, RepoInstructionsMode, ToolAccess


class DeepSeekAdapter(AgentAdapter):
    """Run a node through DeepSeek Harness's shipped headless profile."""

    def prepare(self, node: NodeSpec, prompt: str, paths: ExecutionPaths) -> PreparedExecution:
        if node.provider is not None or node.model is not None:
            raise ValueError(
                "DeepSeek Harness owns provider and model selection in its headless profile; "
                "configure them in the Harness profile instead of on the AgentFlow node"
            )
        if node.mcps:
            raise ValueError(
                "DeepSeek Harness does not accept node-scoped MCP configuration; "
                "configure MCP services in the Harness profile"
            )
        if node.repo_instructions_mode == RepoInstructionsMode.IGNORE:
            raise ValueError(
                "DeepSeek Harness does not support repo_instructions_mode='ignore'; "
                "repository instructions are composed by the Harness profile"
            )

        executable = node.executable or os.getenv("AGENTFLOW_DEEPSEEK_EXECUTABLE") or "dsh"
        command = [
            executable,
            "--profile",
            "headless",
            "--output-format",
            "stream-json",
        ]
        command.extend(node.extra_args)
        command.append(prompt)

        env = merge_env_layers(node.env)
        env.setdefault(
            "DSH_PERMISSION_MODE",
            "read-only" if node.tools == ToolAccess.READ_ONLY else "workspace-write",
        )
        return PreparedExecution(
            command=command,
            env=env,
            cwd=paths.target_workdir,
            trace_kind="deepseek",
        )
