from __future__ import annotations

import json
import os

from agentflow.agents.base import AgentAdapter
from agentflow.agents.secrets import wrap_secret_files
from agentflow.env import merge_env_layers
from agentflow.prepared import ExecutionPaths, PreparedExecution
from agentflow.specs import NodeSpec, RepoInstructionsMode, ToolAccess


_CLAUDE_READ_ONLY_TOOLS = [
    "Read",
    "Glob",
    "Grep",
    "LS",
    "NotebookRead",
    "Task",
    "TaskOutput",
    "TodoRead",
    "WebFetch",
    "WebSearch",
]

_CLAUDE_READ_WRITE_TOOLS = _CLAUDE_READ_ONLY_TOOLS + [
    "Write",
    "Edit",
    "MultiEdit",
    "NotebookEdit",
    "TodoWrite",
    "Bash",
]


class ClaudeAdapter(AgentAdapter):
    def prepare(self, node: NodeSpec, prompt: str, paths: ExecutionPaths) -> PreparedExecution:
        self.validate_node_features(node)
        options = node.cli_options
        if options and options.external_sandbox:
            raise ValueError("external_sandbox is a Codex-only CLI option")
        provider = self.provider_config(node.provider, node.agent)
        executable = node.executable or "claude"
        repo_instructions_ignored = node.repo_instructions_mode == RepoInstructionsMode.IGNORE
        command = [
            executable,
            "-p",
            "--output-format",
            "stream-json",
            "--verbose",
            "--permission-mode",
            "dontAsk" if options is not None else "bypassPermissions",
        ]
        if not (options and options.prompt_via_stdin):
            command.insert(2, prompt)
        if options and options.isolate_config:
            # --bare also enables SIMPLE mode: it removes requested tools and
            # skips explicitly supplied policy hooks. Isolate configuration
            # without changing the agent's tool or hook capabilities.
            command.extend(["--setting-sources", "", "--strict-mcp-config", "--no-session-persistence",
                            "--disable-slash-commands"])
        if repo_instructions_ignored:
            command.extend(["--add-dir", paths.target_workdir])
        if options:
            for root in options.readable_roots:
                command.extend(["--add-dir", root])
        if node.model:
            command.extend(["--model", node.model])
        allowed_tools = _CLAUDE_READ_ONLY_TOOLS if node.tools == ToolAccess.READ_ONLY else _CLAUDE_READ_WRITE_TOOLS
        if options is not None:
            safe_read_tools = {"Read", "Glob", "Grep", "LS", "NotebookRead", "WebFetch", "WebSearch"}
            if options.tool_names is not None:
                allowed_tools = list(options.tool_names)
                if any(not tool or not tool.replace("_", "").isalnum() for tool in allowed_tools):
                    raise ValueError("tool_names must contain exact tool names, not permission patterns")
                if node.tools == ToolAccess.READ_ONLY and not set(allowed_tools) <= safe_read_tools:
                    raise ValueError("Read-only Claude tool_names cannot include writing, delegation, or shell tools")
            elif node.tools == ToolAccess.READ_ONLY:
                allowed_tools = [tool for tool in allowed_tools if tool in safe_read_tools]
        if node.model_settings.web_search == "disabled" or (options and options.network_access is False):
            allowed_tools = [tool for tool in allowed_tools if tool not in {"WebFetch", "WebSearch"}]
        command.extend(["--tools", ",".join(allowed_tools)])
        if options is not None:
            command.extend(["--allowedTools", ",".join(allowed_tools)])
        if node.model_settings.max_turns:
            command.extend(["--max-turns", str(node.model_settings.max_turns)])
        if node.model_settings.reasoning_effort:
            command.extend(["--effort", node.model_settings.reasoning_effort])
        runtime_files: dict[str, str] = {}
        if options and options.system_prompt is not None:
            runtime_files["system-prompt.md"] = options.system_prompt
            command.extend(["--append-system-prompt-file", self.target_path(paths, "system-prompt.md")])
        if options and options.output_schema is not None:
            command.extend(["--json-schema", json.dumps(options.output_schema)])
        if node.mcps or (options and options.isolate_config):
            mcp_payload: dict[str, object] = {"mcpServers": {}}
            for mcp in node.mcps:
                inner: dict[str, object] = {}
                if mcp.transport == "stdio":
                    if mcp.command:
                        inner["command"] = mcp.command
                    if mcp.args:
                        inner["args"] = mcp.args
                    if mcp.env:
                        inner["env"] = dict(mcp.env)
                    if mcp.secret_env:
                        inner.setdefault("env", {}).update({key: "${" + key + "}" for key in mcp.secret_env})
                else:
                    if mcp.url:
                        inner["url"] = mcp.url
                    if mcp.headers:
                        inner["headers"] = dict(mcp.headers)
                    inner.setdefault("headers", {}).update({key: "${" + value + "}" for key, value in mcp.env_http_headers.items()})
                    if mcp.bearer_token_env_var:
                        inner["headers"]["Authorization"] = "Bearer ${" + mcp.bearer_token_env_var + "}"
                    inner["type"] = "http"
                mcp_payload["mcpServers"][mcp.name] = inner
            relative_path = self.relative_runtime_file("claude-mcp.json")
            runtime_files[relative_path] = json.dumps(mcp_payload, ensure_ascii=False, indent=2)
            command.extend(["--mcp-config", self.target_path(paths, relative_path)])
        env = merge_env_layers(getattr(provider, "env", None), node.env)
        if options and options.isolate_config:
            env.update({"CLAUDE_CODE_SIMPLE": "0", "CLAUDE_CODE_DISABLE_AUTO_MEMORY": "1",
                        "CLAUDE_CODE_DISABLE_CLAUDE_MDS": "1"})
        elif repo_instructions_ignored:
            env["CLAUDE_CODE_DISABLE_CLAUDE_MDS"] = "1"
        if node.model_settings.max_output_tokens:
            env["CLAUDE_CODE_MAX_OUTPUT_TOKENS"] = str(node.model_settings.max_output_tokens)
        is_docker = node.target.kind == "docker"
        if is_docker and node.target.inherit_credentials:
            raise ValueError("Docker agent nodes must use explicit secret files, not host credentials")
        if is_docker:
            env["CLAUDE_CONFIG_DIR"] = self.target_path(paths, "claude-home")
            env["HOME"] = self.target_path(paths, "claude-home")
        if provider:
            if provider.base_url:
                env.setdefault("ANTHROPIC_BASE_URL", provider.base_url)
            if provider.headers:
                env.setdefault("ANTHROPIC_CUSTOM_HEADERS", json.dumps(provider.headers, ensure_ascii=False))
            if provider.api_key_env:
                if provider.api_key_env in env:
                    api_key = env[provider.api_key_env]
                elif not is_docker:
                    api_key = os.getenv(provider.api_key_env)
                else:
                    api_key = None
                if api_key is not None:
                    env.setdefault("ANTHROPIC_API_KEY", api_key)
        command.extend(node.extra_args)
        cwd = paths.target_workdir
        if repo_instructions_ignored:
            cwd = self.target_path(paths)
        prepared = PreparedExecution(
            command=command,
            stdin=prompt if options and options.prompt_via_stdin else None,
            env=env,
            cwd=cwd,
            trace_kind="claude",
            runtime_files=runtime_files,
        )
        aliases = {"ANTHROPIC_API_KEY": provider.api_key_env} if is_docker and provider and provider.api_key_env and provider.api_key_env != "ANTHROPIC_API_KEY" else None
        return wrap_secret_files(node, prepared, paths, aliases=aliases)
