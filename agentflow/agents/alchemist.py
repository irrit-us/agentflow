from __future__ import annotations

import json
import os

from agentflow.agents.base import AgentAdapter
from agentflow.agents.secrets import wrap_secret_files
from agentflow.env import merge_env_layers
from agentflow.prepared import ExecutionPaths, PreparedExecution
from agentflow.specs import NodeSpec, ToolAccess

# Audit Alchemist ships a fixed native catalog; these are the exact tool names
# its ``[tools] enabled`` allowlist accepts. Bash, write_file and edit_file can
# mutate the audit root, so a read-only AgentFlow node withdraws them while the
# remaining tools still support exploration and evidence collection.
_READ_ONLY_TOOLS = ["read_file", "list_files", "search", "load_skill"]
_SUPPORTED_WIRE_APIS = {"chat-completions", "responses", "anthropic"}
_SUPPORTED_REASONING_EFFORTS = {"minimal", "low", "medium", "high"}
_MAX_TIMEOUT_MS = 3_600_000


def _toml_string(value: str) -> str:
    # TOML basic strings and JSON strings share the same escaping for the
    # characters Audit Alchemist config values may contain.
    return json.dumps(value, ensure_ascii=False)


def _toml_key(value: str) -> str:
    return _toml_string(value)


class AlchemistAdapter(AgentAdapter):
    """Run a node through the Audit Alchemist CLI.

    Uses the documented workflow contract: ``alchemist agent`` reads exactly one
    JSON request on stdin, writes exactly one JSON response on stdout, and keeps
    progress on stderr. Provider, budget, tool and MCP settings are rendered
    into an isolated, versioned TOML node configuration passed with ``--config``
    so the run never depends on ambient files. See
    https://github.com/irrit-us/Audit-Alchemist/blob/main/docs/api.md.
    """

    def _resolve_optional_text(self, env: dict[str, str], name: str) -> str | None:
        value = (env.pop(name, "") or "").strip()
        return value or None

    def _resolve_target(self, env: dict[str, str]) -> str:
        target = self._resolve_optional_text(env, "AGENTFLOW_ALCHEMIST_TARGET") or "."
        if target.startswith("/") or (len(target) >= 2 and target[1] == ":"):
            raise ValueError(
                "AGENTFLOW_ALCHEMIST_TARGET must be a path relative to the node workdir"
            )
        return target

    def _resolve_auth(self, env: dict[str, str]) -> str:
        auth = (self._resolve_optional_text(env, "AGENTFLOW_ALCHEMIST_AUTH") or "api-key").lower()
        if auth not in {"api-key", "codex"}:
            raise ValueError("AGENTFLOW_ALCHEMIST_AUTH must be `api-key` or `codex`")
        return auth

    def _resolve_timeout_ms(self, node: NodeSpec) -> int:
        timeout_ms = int(node.timeout_seconds * 1000)
        return max(1, min(timeout_ms, _MAX_TIMEOUT_MS))

    def _render_config(self, node: NodeSpec, env: dict[str, str]) -> dict[str, object]:
        config: dict[str, object] = {"schema_version": 1}
        if node.tools == ToolAccess.READ_ONLY:
            config["tools"] = {"enabled": list(_READ_ONLY_TOOLS)}
        if node.mcps:
            servers: dict[str, object] = {}
            for mcp in node.mcps:
                # ``mcp:streamable_http`` is rejected by capability validation;
                # only stdio servers reach this point.
                if not _is_valid_mcp_name(mcp.name):
                    raise ValueError(
                        "Audit Alchemist MCP server names must be 1-48 ASCII letters, "
                        f"digits, `_`, or `-`: {mcp.name!r}"
                    )
                entry: dict[str, object] = {
                    "enabled": True,
                    "command": mcp.command or "",
                    "args": list(mcp.args),
                }
                # Audit Alchemist keeps credential values out of the file: it
                # forwards named parent variables via ``env_from``. Literal
                # values and injected secret files both become process
                # environment variables first.
                mappings: dict[str, str] = {}
                for key, value in mcp.env.items():
                    env[key] = value
                    mappings[key] = key
                for key in mcp.secret_env:
                    mappings[key] = key
                if mappings:
                    entry["env_from"] = mappings
                call_timeout = _bounded_timeout_ms(mcp.tool_timeout_sec)
                if call_timeout is not None:
                    entry["timeout_ms"] = call_timeout
                startup_timeout = _bounded_timeout_ms(mcp.startup_timeout_sec)
                if startup_timeout is not None:
                    entry["startup_timeout_ms"] = startup_timeout
                servers[mcp.name] = entry
            config["mcp"] = servers
        return config

    def _render_toml(self, config: dict[str, object]) -> str:
        lines = [f"schema_version = {int(config['schema_version'])}"]
        tools = config.get("tools")
        if isinstance(tools, dict):
            lines.extend(["", "[tools]", f"enabled = {_toml_string_list(tools['enabled'])}"])
        servers = config.get("mcp")
        if isinstance(servers, dict):
            for name, entry in servers.items():
                lines.extend(["", f"[mcp.{_toml_key(name)}]"])
                lines.append("enabled = true")
                lines.append(f"command = {_toml_string(str(entry['command']))}")
                args = entry.get("args")
                if args:
                    lines.append(f"args = {_toml_string_list(args)}")
                timeout_ms = entry.get("timeout_ms")
                if timeout_ms:
                    lines.append(f"timeout_ms = {int(timeout_ms)}")
                startup_timeout_ms = entry.get("startup_timeout_ms")
                if startup_timeout_ms:
                    lines.append(f"startup_timeout_ms = {int(startup_timeout_ms)}")
                mappings = entry.get("env_from")
                if isinstance(mappings, dict) and mappings:
                    lines.extend(["", f"[mcp.{_toml_key(name)}.env_from]"])
                    for key, value in mappings.items():
                        lines.append(f"{_toml_key(key)} = {_toml_string(str(value))}")
        return "\n".join(lines) + "\n"

    def prepare(self, node: NodeSpec, prompt: str, paths: ExecutionPaths) -> PreparedExecution:
        self.validate_node_features(node)
        provider = self.provider_config(node.provider, node.agent)
        if not node.model:
            raise ValueError("Audit Alchemist nodes require an explicit `model`")

        executable = node.executable or os.getenv("AGENTFLOW_ALCHEMIST_EXECUTABLE") or "alchemist"
        env = merge_env_layers(getattr(provider, "env", None), node.env)
        auth = self._resolve_auth(env)
        target = self._resolve_target(env)
        codex_auth_file = self._resolve_optional_text(env, "AGENTFLOW_ALCHEMIST_CODEX_AUTH_FILE")

        wire_api = (provider.wire_api if provider else None) or "chat-completions"
        if wire_api not in _SUPPORTED_WIRE_APIS:
            raise ValueError(
                "Audit Alchemist --wire-api must be one of: "
                + ", ".join(sorted(_SUPPORTED_WIRE_APIS))
            )
        if auth == "api-key" and (provider is None or not provider.base_url):
            raise ValueError(
                "Audit Alchemist api-key auth requires a provider with an explicit `base_url` "
                "(the full chat-completions, Responses, or Messages endpoint)"
            )

        settings = node.model_settings
        if settings.reasoning_effort and settings.reasoning_effort not in _SUPPORTED_REASONING_EFFORTS:
            raise ValueError(
                "Audit Alchemist --reasoning-effort must be one of: "
                + ", ".join(sorted(_SUPPORTED_REASONING_EFFORTS))
            )

        command: list[str] = [executable]
        runtime_files: dict[str, str] = {}
        config = self._render_config(node, env)
        if len(config) > 1:
            relative_path = self.relative_runtime_file("alchemist.toml")
            runtime_files[relative_path] = self._render_toml(config)
            command.extend(["--config", self.target_path(paths, relative_path)])

        command.extend(["agent", "--model", node.model, "--timeout-ms", str(self._resolve_timeout_ms(node))])
        if provider and provider.base_url:
            command.extend(["--endpoint", provider.base_url])
        if provider and provider.api_key_env and auth == "api-key":
            command.extend(["--api-key-env", provider.api_key_env])
        if provider and provider.wire_api:
            command.extend(["--wire-api", wire_api])
        command.extend(["--auth", auth])
        if auth == "codex":
            if codex_auth_file:
                command.extend(["--codex-auth-file", codex_auth_file])
        if provider:
            for name, value in provider.headers.items():
                command.extend(["--header", f"{name}:{value}"])
        if settings.reasoning_effort:
            command.extend(["--reasoning-effort", settings.reasoning_effort])
        if settings.max_output_tokens:
            command.extend(["--max-tokens", str(settings.max_output_tokens)])
        command.extend(node.extra_args)

        request = {
            "schema_version": 1,
            "case_id": node.id,
            "target": target,
            "instruction": prompt,
        }
        prepared = PreparedExecution(
            command=command,
            env=env,
            cwd=paths.target_workdir,
            trace_kind="alchemist",
            runtime_files=runtime_files,
            stdin=json.dumps(request, ensure_ascii=False),
        )
        return wrap_secret_files(node, prepared, paths)


def _bounded_timeout_ms(seconds: float | None) -> int | None:
    if not seconds:
        return None
    return max(1, min(int(seconds * 1000), _MAX_TIMEOUT_MS))


def _toml_string_list(values: object) -> str:
    items = [str(value) for value in values] if isinstance(values, (list, tuple)) else []
    return "[" + ", ".join(_toml_string(item) for item in items) + "]"


def _is_valid_mcp_name(name: str) -> bool:
    return bool(name) and len(name) <= 48 and all(
        character.isascii() and (character.isalnum() or character in "_-") for character in name
    )
