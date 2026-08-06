from __future__ import annotations

import os

import yaml

from agentflow.agents.base import AgentAdapter
from agentflow.env import merge_env_layers
from agentflow.prepared import ExecutionPaths, PreparedExecution
from agentflow.specs import NodeSpec, RepoInstructionsMode


class GooseAdapter(AgentAdapter):
    """Adapter for the Goose CLI (https://block.github.io/goose).

    Runs ``goose run --no-session --output-format stream-json`` and consumes the
    NDJSON event stream. Provider credentials and models pass through the
    environment variables Goose reads natively (``GOOSE_PROVIDER``,
    ``GOOSE_MODEL``, ``OPENAI_API_KEY``, ``ANTHROPIC_API_KEY``). MCP servers are
    materialized into an isolated home directory so Goose loads them from
    ``~/.config/goose/config.yaml`` without touching the user's real config.
    """

    def prepare(self, node: NodeSpec, prompt: str, paths: ExecutionPaths) -> PreparedExecution:
        provider = self.provider_config(node.provider, node.agent)
        executable = node.executable or "goose"
        repo_instructions_ignored = node.repo_instructions_mode == RepoInstructionsMode.IGNORE
        command = [executable, "run", "--no-session", "--output-format", "stream-json"]
        if node.model:
            command.extend(["--model", node.model])
        if provider and provider.name and "/" not in (node.model or ""):
            command.extend(["--provider", provider.name])
        command.extend(["-t", prompt])
        command.extend(node.extra_args)

        env = merge_env_layers(getattr(provider, "env", None), node.env)
        env.setdefault("GOOSE_MODE", "auto")
        env.setdefault("GOOSE_DISABLE_KEYRING", "1")
        env.setdefault("GOOSE_TELEMETRY_OFF", "1")
        if not repo_instructions_ignored:
            env.setdefault("GOOSE_WORKING_DIR", paths.target_workdir)
        if provider:
            if provider.base_url:
                uses_anthropic = (
                    provider.wire_api == "anthropic"
                    or provider.name.lower().startswith("anthropic")
                    or (node.model or "").startswith("anthropic/")
                )
                env.setdefault(
                    "ANTHROPIC_BASE_URL" if uses_anthropic else "OPENAI_BASE_URL",
                    provider.base_url,
                )
            if provider.api_key_env:
                if provider.api_key_env not in env:
                    resolved = os.getenv(provider.api_key_env)
                    if resolved is not None:
                        env.setdefault(provider.api_key_env, resolved)

        runtime_files: dict[str, str] = {}
        if node.mcps:
            goose_home = self.target_path(paths, "goose_home")
            config: dict[str, object] = {"extensions": {}}
            for mcp in node.mcps:
                if mcp.transport == "stdio":
                    extension: dict[str, object] = {
                        "type": "stdio",
                        "cmd": mcp.command,
                        "args": list(mcp.args),
                        "envs": dict(mcp.env),
                    }
                else:
                    extension = {
                        "type": "streamable_http",
                        "uri": mcp.url,
                        "headers": dict(mcp.headers),
                    }
                config["extensions"][mcp.name] = extension
            relative_path = self.relative_runtime_file("goose_home", ".config", "goose", "config.yaml")
            runtime_files[relative_path] = yaml.safe_dump(config, sort_keys=False)
            env["HOME"] = goose_home

        cwd = paths.target_workdir
        if repo_instructions_ignored:
            cwd = self.target_path(paths)
        return PreparedExecution(
            command=command,
            env=env,
            cwd=cwd,
            trace_kind="goose",
            runtime_files=runtime_files,
        )
