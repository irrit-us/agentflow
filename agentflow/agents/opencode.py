from __future__ import annotations

import json
import os
import re

from agentflow.agents.base import AgentAdapter
from agentflow.env import merge_env_layers
from agentflow.prepared import ExecutionPaths, PreparedExecution
from agentflow.specs import NodeSpec, RepoInstructionsMode

_PROVIDER_NAME_PATTERN = re.compile(r"[^a-zA-Z0-9_-]+")


def _provider_slug(name: str) -> str:
    slug = _PROVIDER_NAME_PATTERN.sub("-", name.strip().lower()).strip("-")
    return slug or "custom"


class OpenCodeAdapter(AgentAdapter):
    """Adapter for the OpenCode CLI (https://opencode.ai).

    Runs ``opencode run --format json --auto`` and consumes the NDJSON event
    stream. Custom providers are materialized into a per-node ``opencode.json``
    (pointed at via ``OPENCODE_CONFIG``) because the CLI ignores
    ``OPENAI_BASE_URL`` / ``ANTHROPIC_BASE_URL`` for its built-in providers;
    MCP servers share the same config file. Models on a custom provider are
    invoked as ``<provider>/<model>``.
    """

    def prepare(self, node: NodeSpec, prompt: str, paths: ExecutionPaths) -> PreparedExecution:
        provider = self.provider_config(node.provider, node.agent)
        executable = node.executable or "opencode"
        repo_instructions_ignored = node.repo_instructions_mode == RepoInstructionsMode.IGNORE
        command = [executable, "run", "--format", "json", "--auto"]

        env = merge_env_layers(getattr(provider, "env", None), node.env)
        runtime_files: dict[str, str] = {}
        config: dict[str, object] = {}
        provider_slug: str | None = None

        if provider:
            uses_anthropic = provider.name.lower().startswith("anthropic") or (node.model or "").startswith("anthropic/")
            api_key: str | None = None
            if provider.api_key_env:
                api_key = env.get(provider.api_key_env) or os.getenv(provider.api_key_env)
            if provider.base_url:
                env.setdefault("ANTHROPIC_BASE_URL" if uses_anthropic else "OPENAI_BASE_URL", provider.base_url)
            if api_key is not None:
                env.setdefault("ANTHROPIC_API_KEY" if uses_anthropic else "OPENAI_API_KEY", api_key)
            provider_slug = _provider_slug(provider.name)
            if provider.base_url and provider_slug:
                api_key_env = provider.api_key_env or ("ANTHROPIC_API_KEY" if uses_anthropic else "OPENAI_API_KEY")
                options: dict[str, object] = {"baseURL": provider.base_url}
                if api_key is not None:
                    options["apiKey"] = "{env:" + api_key_env + "}"
                provider_entry: dict[str, object] = {
                    "npm": "@ai-sdk/anthropic" if uses_anthropic else "@ai-sdk/openai-compatible",
                    "name": provider.name,
                    "options": options,
                }
                model_id = node.model
                if model_id and "/" in model_id:
                    model_id = None
                if model_id:
                    provider_entry["models"] = {
                        model_id: {"name": model_id, "limit": {"context": 65536, "output": 8192}}
                    }
                config["provider"] = {provider_slug: provider_entry}

        if node.mcps:
            mcp_payload: dict[str, object] = {}
            for mcp in node.mcps:
                if mcp.transport == "stdio":
                    command_list: list[str] = [mcp.command] if mcp.command else []
                    command_list.extend(mcp.args)
                    inner: dict[str, object] = {
                        "type": "local",
                        "command": command_list,
                        "environment": dict(mcp.env),
                    }
                else:
                    inner = {
                        "type": "remote",
                        "url": mcp.url,
                        "headers": dict(mcp.headers),
                    }
                mcp_payload[mcp.name] = inner
            config["mcp"] = mcp_payload

        if config:
            relative_path = self.relative_runtime_file("opencode.json")
            runtime_files[relative_path] = json.dumps(config, ensure_ascii=False, indent=2)
            env["OPENCODE_CONFIG"] = self.target_path(paths, relative_path)

        if node.model:
            model_ref = node.model
            if provider and provider.base_url and "/" not in model_ref and provider_slug:
                model_ref = f"{provider_slug}/{model_ref}"
            command.extend(["--model", model_ref])
        command.extend(node.extra_args)
        command.append(prompt)

        cwd = paths.target_workdir
        if repo_instructions_ignored:
            cwd = self.target_path(paths)
        return PreparedExecution(
            command=command,
            env=env,
            cwd=cwd,
            trace_kind="opencode",
            runtime_files=runtime_files,
        )

