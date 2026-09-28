from __future__ import annotations

from pathlib import Path
import json
import tomllib
import re

from agentflow.agents.base import AgentAdapter
from agentflow.agents.secrets import wrap_secret_files
from agentflow.env import merge_env_layers
from agentflow.prepared import ExecutionPaths, PreparedExecution
from agentflow.specs import DockerTarget, NodeSpec, ProviderConfig, RepoInstructionsMode, ToolAccess


class CodexAdapter(AgentAdapter):
    _SUPPORTED_SANDBOX_MODES = {"read-only", "workspace-write", "danger-full-access"}

    def _toml_key(self, value: str) -> str:
        return value if re.fullmatch(r"[A-Za-z0-9_-]+", value) else self._format_toml_value(value)

    def _format_toml_value(self, value: object) -> str:
        import json

        if isinstance(value, bool):
            return "true" if value else "false"
        if isinstance(value, (int, float)):
            return str(value)
        if isinstance(value, list):
            return "[" + ", ".join(self._format_toml_value(item) for item in value) + "]"
        if isinstance(value, dict):
            items = ", ".join(f"{self._format_toml_value(key)} = {self._format_toml_value(inner)}" for key, inner in value.items())
            return "{" + items + "}"
        return json.dumps(str(value), ensure_ascii=False)

    def _render_config(self, node: NodeSpec, provider: ProviderConfig | None, sandbox_mode: str) -> str:
        lines: list[str] = []
        if node.model:
            lines.append(f"model = {self._format_toml_value(node.model)}")
        lines.append(f"approval_policy = {self._format_toml_value('never')}")
        options = node.cli_options
        if options and options.external_sandbox:
            lines.append('sandbox_mode = "danger-full-access"')
        elif options and (options.network_access is not None or options.readable_roots):
            profile = {"extends": ":read-only" if sandbox_mode == "read-only" else ":workspace",
                       "network": {"enabled": bool(options.network_access)},
                       "filesystem": {root: "read" for root in options.readable_roots}}
            lines.append('default_permissions = "agentflow_native"')
            lines.append("permissions = " + self._format_toml_value({"agentflow_native": profile}))
        else:
            lines.append(f"sandbox_mode = {self._format_toml_value(sandbox_mode)}")
        settings = node.model_settings
        for key, value in {"model_context_window": settings.context_window,
                           "model_reasoning_effort": settings.reasoning_effort,
                           "web_search": settings.web_search}.items():
            if value is not None:
                lines.append(f"{key} = {self._format_toml_value(value)}")
        if provider:
            lines.append(f"model_provider = {self._format_toml_value(provider.name)}")
        if provider and (provider.base_url or provider.api_key_env or provider.wire_api):
            lines.append("")
            lines.append(f"[model_providers.{self._toml_key(provider.name)}]")
            lines.append(f"name = {self._format_toml_value(provider.name)}")
            if provider.base_url:
                lines.append(f"base_url = {self._format_toml_value(provider.base_url)}")
            if provider.api_key_env:
                lines.append(f"env_key = {self._format_toml_value(provider.api_key_env)}")
            if provider.wire_api:
                lines.append(f"wire_api = {self._format_toml_value(provider.wire_api)}")
        if node.mcps:
            for mcp in node.mcps:
                lines.append("")
                lines.append(f"[mcp_servers.{self._toml_key(mcp.name)}]")
                for key in ("startup_timeout_sec", "tool_timeout_sec"):
                    value = getattr(mcp, key)
                    if value is not None:
                        lines.append(f"{key} = {self._format_toml_value(value)}")
                if mcp.transport == "stdio":
                    if mcp.command:
                        lines.append(f"command = {self._format_toml_value(mcp.command)}")
                    if mcp.args:
                        lines.append(f"args = {self._format_toml_value(mcp.args)}")
                    if mcp.env:
                        lines.append(f"env = {self._format_toml_value(mcp.env)}")
                    if mcp.secret_env:
                        lines.append(f"env_vars = {self._format_toml_value(list(mcp.secret_env))}")
                else:
                    if mcp.url:
                        lines.append(f"url = {self._format_toml_value(mcp.url)}")
                    if mcp.headers:
                        lines.append(f"http_headers = {self._format_toml_value(mcp.headers)}")
                    if mcp.env_http_headers:
                        lines.append(f"env_http_headers = {self._format_toml_value(mcp.env_http_headers)}")
                    if mcp.bearer_token_env_var:
                        lines.append(f"bearer_token_env_var = {self._format_toml_value(mcp.bearer_token_env_var)}")
        return "\n".join(lines) + "\n"

    def _resolve_sandbox_mode(self, node: NodeSpec, env: dict[str, str]) -> str:
        override = (env.pop("AGENTFLOW_CODEX_SANDBOX_MODE", "") or "").strip()
        if not override:
            return "read-only" if node.tools == ToolAccess.READ_ONLY else "workspace-write"
        if override not in self._SUPPORTED_SANDBOX_MODES:
            raise ValueError(
                "AGENTFLOW_CODEX_SANDBOX_MODE must be one of: "
                + ", ".join(sorted(self._SUPPORTED_SANDBOX_MODES))
            )
        if node.cli_options is not None:
            permitted = {"read-only"} if node.tools == ToolAccess.READ_ONLY else {"read-only", "workspace-write"}
            if override not in permitted:
                raise ValueError("Codex sandbox override would weaken the explicit tool policy")
        return override

    _WRAPPER_FILENAME = "agentflow_wrapper.md"
    _WRAPPER_SEPARATOR = "\n\n---\n\n"

    def _maybe_prepend_wrapper(self, node: NodeSpec, prompt: str) -> str:
        """Prepend an agentflow-side wrapper to the user prompt if one exists.

        For tuned codex builds the executable lives at
        ``<version>/repo/codex-rs/target/debug/codex``; we look for
        ``<version>/repo/codex-rs/agentflow_wrapper.md`` next to it. This is
        the most reliable evolution surface because the wrapper text becomes
        part of the user message — gateways that override server-side system
        prompts cannot strip it.
        """
        executable = node.executable
        if not executable:
            return prompt
        exec_path = Path(executable).expanduser()
        if not exec_path.is_absolute():
            return prompt
        # codex_tuned binary path: .../codex-rs/target/debug/codex
        # → walk up three parents to reach codex-rs/
        if len(exec_path.parents) < 3:
            return prompt
        codex_rs_root = exec_path.parents[2]
        wrapper_path = codex_rs_root / self._WRAPPER_FILENAME
        if not wrapper_path.is_file():
            return prompt
        try:
            wrapper_text = wrapper_path.read_text(encoding="utf-8").strip()
        except OSError:
            return prompt
        if not wrapper_text:
            return prompt
        return wrapper_text + self._WRAPPER_SEPARATOR + prompt

    def prepare(self, node: NodeSpec, prompt: str, paths: ExecutionPaths) -> PreparedExecution:
        self.validate_node_features(node)
        options = node.cli_options
        if options is not None and options.tool_names is not None:
            raise ValueError("Codex does not support an exact tool_names allowlist")
        if options and options.external_sandbox:
            target = node.target
            if not isinstance(target, DockerTarget) or target.privileged or target.mount_docker_daemon or target.dind:
                raise ValueError("External sandbox requires an isolated Docker target")
            if node.tools == ToolAccess.READ_ONLY and not target.workdir_read_only:
                raise ValueError("External read-only sandbox requires a read-only Docker workspace")
            if any(not mount.read_only for mount in target.mounts):
                raise ValueError("External sandbox context mounts must be read-only")
            if not set(options.readable_roots) <= {mount.target for mount in target.mounts}:
                raise ValueError("External sandbox read roots must have explicit read-only Docker mounts")
            if options.network_access is not None and options.network_access != (target.network_policy.mode != "none"):
                raise ValueError("External sandbox network permission must match the Docker network policy")
        provider = self.provider_config(node.provider, node.agent)
        executable = node.executable or "codex"
        env = merge_env_layers(getattr(provider, "env", None), node.env)
        sandbox = self._resolve_sandbox_mode(node, env)
        repo_instructions_ignored = node.repo_instructions_mode == RepoInstructionsMode.IGNORE
        command = [
            executable,
            "exec",
            "--json",
            "--skip-git-repo-check",
            "-c",
            'approval_policy="never"',
            "-c",
            "suppress_unstable_features_warning=true",
        ]
        external_sandbox = bool(options and options.external_sandbox)
        permission_profile = bool(options and not external_sandbox and (options.network_access is not None or options.readable_roots))
        if external_sandbox:
            command.append("--dangerously-bypass-approvals-and-sandbox")
        elif not permission_profile:
            command.extend(["--sandbox", sandbox])
        elif not options.isolate_config:
            # Apply the same generated profile even without a scoped config home.
            for key in ("default_permissions", "permissions"):
                value = tomllib.loads(self._render_config(node, provider, sandbox))[key]
                command.extend(["-c", f"{key}={self._format_toml_value(value)}"])
        if node.model and not provider:
            command.extend(["--model", node.model])
        if provider and not (options and options.isolate_config):
            command.extend(["--profile", "agentflow"])
        if options and options.isolate_config:
            command.extend(["--ignore-user-config", "--ephemeral"])
            for key, value in tomllib.loads(self._render_config(node, provider, sandbox)).items():
                command.extend(["-c", f"{self._toml_key(key)}={self._format_toml_value(value)}"])

        if repo_instructions_ignored:
            command.extend(["--disable", "plugins"])
            command.extend(["--add-dir", paths.target_workdir])
        command.extend(node.extra_args)
        prompt = self._maybe_prepend_wrapper(node, prompt)
        if options and options.system_prompt:
            prompt = options.system_prompt + self._WRAPPER_SEPARATOR + prompt
        if node.model_settings.max_output_tokens is not None:
            prompt += f"\nKeep the final response within approximately {node.model_settings.max_output_tokens} tokens. This is an advisory output budget."
        command.append("-" if options and options.prompt_via_stdin else prompt)

        runtime_files: dict[str, str] = {}
        if options and options.output_schema is not None:
            runtime_files["output-schema.json"] = json.dumps(options.output_schema)
            runtime_files["structured-output.json"] = ""
            command[-1:-1] = ["--output-schema", self.target_path(paths, "output-schema.json"),
                              "--output-last-message", self.target_path(paths, "structured-output.json")]

        runtime_symlinks: dict[str, str] = {}
        is_docker_target = getattr(node.target, "kind", None) == "docker"
        inherit_host_credentials = not is_docker_target or bool(
            getattr(node.target, "inherit_credentials", False)
        )
        if getattr(node.target, "kind", None) == "docker" and inherit_host_credentials:
            raise ValueError("Docker agent nodes must use explicit secret files, not host credentials")
        needs_scoped_home = bool(
            provider
            or node.mcps
            or node.model_settings.model_dump(exclude_none=True)
            or is_docker_target
            or repo_instructions_ignored
            or (is_docker_target and inherit_host_credentials)
        )
        if needs_scoped_home and not (options and options.isolate_config and not is_docker_target):
            codex_home = self.target_path(paths, "codex_home")
            host_config = Path.home() / ".codex" / "config.toml"
            inherit_host_config = (
                inherit_host_credentials
                and not (options and options.isolate_config)
                and provider is None
                and not node.mcps
                and host_config.is_file()
            )
            if inherit_host_config:
                runtime_symlinks[self.relative_runtime_file("codex_home", "config.toml")] = str(host_config)
            else:
                runtime_files[self.relative_runtime_file("codex_home", "config.toml")] = self._render_config(
                    node,
                    provider,
                    sandbox,
                )
            if provider:
                runtime_files[self.relative_runtime_file("codex_home", "agentflow.config.toml")] = (
                    self._render_config(node, provider, sandbox)
                )
            host_auth = Path.home() / ".codex" / "auth.json"
            if inherit_host_credentials and host_auth.is_file():
                runtime_symlinks[self.relative_runtime_file("codex_home", "auth.json")] = str(host_auth)
            env["CODEX_HOME"] = codex_home
            env["HOME"] = codex_home
        cwd = paths.target_workdir
        if repo_instructions_ignored:
            cwd = self.target_path(paths)
        prepared = PreparedExecution(
            command=command,
            stdin=prompt if options and options.prompt_via_stdin else None,
            env=env,
            cwd=cwd,
            trace_kind="codex",
            runtime_files=runtime_files,
            runtime_symlinks=runtime_symlinks,
        )
        return wrap_secret_files(node, prepared, paths)
