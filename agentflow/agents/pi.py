from __future__ import annotations

import json
import os
from pathlib import Path

from agentflow.agents.base import AgentAdapter
from agentflow.agents.secrets import wrap_secret_files
from agentflow.env import merge_env_layers
from agentflow.extensions import prepare_extensions
from agentflow.prepared import ExecutionPaths, PreparedExecution
from agentflow.specs import NodeSpec, ProviderConfig, RepoInstructionsMode, ToolAccess


_PI_READ_ONLY_TOOLS = "read,grep,find,ls"
_PI_READ_WRITE_TOOLS = "read,bash,edit,write,grep,find,ls"

# Session-resumption flags Pi accepts. When the caller pins one of these, the
# adapter must NOT pass ``--no-session``: Pi would then skip persistence and a
# later rerun of the node could not continue the interrupted session (the
# long-running authoring roles rely on this after a watchdog rerun).
_PI_SESSION_FLAGS = {
    "--session-id",
    "--session",
    "--resume",
    "-r",
    "--continue",
    "-c",
}


class PiAdapter(AgentAdapter):
    def prepare(self, node: NodeSpec, prompt: str, paths: ExecutionPaths) -> PreparedExecution:
        if node.mcps:
            raise ValueError(
                "pi adapter does not support `mcps`. Pi uses extensions, not MCP servers; "
                "pass `--extension <path>` via `extra_args` instead."
            )

        self.validate_node_features(node)

        provider = self.provider_config(node.provider, node.agent)
        executable = node.executable or "pi"
        env = merge_env_layers(getattr(provider, "env", None), node.env)
        repo_instructions_ignored = node.repo_instructions_mode == RepoInstructionsMode.IGNORE

        # Keep the session on disk when the node pins a session (id/resume) or
        # when the operator explicitly opts in, so an interrupted role can be
        # resumed with its own context instead of restarting from scratch.
        pinned_session = bool(
            os.getenv("AGENTFLOW_PI_KEEP_SESSION") == "1"
            or any(
                arg in _PI_SESSION_FLAGS
                or arg.startswith("--session-id=")
                or arg.startswith("--session=")
                for arg in node.extra_args
            )
        )

        command: list[str] = [
            executable,
            "--print",
            "--mode",
            "json",
        ]
        if not pinned_session:
            command.append("--no-session")

        tools = _PI_READ_ONLY_TOOLS if node.tools == ToolAccess.READ_ONLY else _PI_READ_WRITE_TOOLS
        # `--tools` is an allowlist that also filters extension tools, so a node
        # that loads an extension must name its tools explicitly. The authoring
        # workflow's agent-management extension does this through
        # AGENTFLOW_PI_EXTRA_TOOLS.
        extra_tools = [
            name.strip()
            for name in os.getenv("AGENTFLOW_PI_EXTRA_TOOLS", "").split(",")
            if name.strip()
        ]
        if extra_tools:
            tools = f"{tools},{','.join(extra_tools)}"
        command.extend(["--tools", tools])

        runtime_files: dict[str, str] = {}
        is_docker = node.target.kind == "docker"
        if is_docker and node.target.inherit_credentials:
            raise ValueError("Docker agent nodes must use explicit secret files, not host credentials")
        scoped_home_needed = bool(provider and (provider.base_url or provider.headers))

        if scoped_home_needed:
            pi_home_relative = Path("pi-home") / "agent"
            models_rel = self.relative_runtime_file(*pi_home_relative.parts, "models.json")
            settings_rel = self.relative_runtime_file(*pi_home_relative.parts, "settings.json")
            payload = json.loads(self._render_models_json(provider, node.model))
            for model in payload["providers"][provider.name]["models"]:
                if node.model_settings.context_window:
                    model["contextWindow"] = node.model_settings.context_window
                if node.model_settings.max_output_tokens:
                    model["maxTokens"] = node.model_settings.max_output_tokens
            runtime_files[models_rel] = json.dumps(payload, indent=2) + "\n"
            runtime_files[settings_rel] = "{}\n"
            env["PI_CODING_AGENT_DIR"] = self.target_path(paths, *pi_home_relative.parts)
        elif provider and provider.name and "/" not in (node.model or ""):
            command.extend(["--provider", provider.name])

        if provider and provider.api_key_env and provider.api_key_env not in env and not is_docker:
            # Surface the key into the subprocess env so Pi can read it by name.
            resolved = os.getenv(provider.api_key_env)
            if resolved is not None:
                env.setdefault(provider.api_key_env, resolved)

        if node.model:
            command.extend(["--model", node.model])
        if node.model_settings.reasoning_effort:
            command.extend(["--thinking", node.model_settings.reasoning_effort])
        extension_paths, extension_files = prepare_extensions(
            node.extensions, source_root=paths.host_workdir, target_runtime_dir=paths.target_runtime_dir,
        )
        runtime_files.update(extension_files)
        for extension in extension_paths:
            command.extend(["--extension", extension])
        if is_docker:
            env.setdefault("PI_CODING_AGENT_DIR", self.target_path(paths, "pi-home", "agent"))
            env["HOME"] = self.target_path(paths, "pi-home")

        if repo_instructions_ignored:
            command.extend(["--no-skills", "--no-extensions", "--no-prompt-templates"])

        command.extend(node.extra_args)

        cwd = paths.target_workdir
        if repo_instructions_ignored:
            cwd = self.target_path(paths)

        # Pass the prompt via stdin so it is never parsed as a flag or `@file`
        # reference by Pi's positional-message argument handling.
        prepared = PreparedExecution(
            command=command,
            env=env,
            cwd=cwd,
            trace_kind="pi",
            runtime_files=runtime_files,
            stdin=prompt,
        )
        return wrap_secret_files(node, prepared, paths)

    def _render_models_json(self, provider: ProviderConfig, model: str | None) -> str:
        """Render a scoped ``models.json`` containing only the declared provider.

        Pi resolves custom providers from its agent directory's ``models.json``. When the
        caller supplies a full ``ProviderConfig`` (with ``base_url``), we materialize a
        minimal ``models.json`` under a scoped ``PI_CODING_AGENT_DIR`` so the run does
        not depend on the user's ``~/.pi/agent/models.json``.
        """
        entry: dict[str, object] = {
            "baseUrl": provider.base_url,
            "api": provider.wire_api or "openai-completions",
        }
        if provider.api_key_env:
            # Pi interpolates `$NAME` / `${NAME}` references against the process
            # environment; a bare name would be treated as a literal key value.
            entry["apiKey"] = f"${{{provider.api_key_env}}}"
        if provider.headers:
            entry["headers"] = dict(provider.headers)
            entry["authHeader"] = True
        model_id = self._extract_model_id(model, provider.name)
        entry["models"] = [{"id": model_id}] if model_id else []

        payload = {"providers": {provider.name: entry}}
        return json.dumps(payload, ensure_ascii=False, indent=2) + "\n"

    @staticmethod
    def _extract_model_id(model: str | None, provider_name: str) -> str | None:
        if not model:
            return None
        ident = model
        if "/" in ident:
            prefix, _, rest = ident.partition("/")
            if prefix == provider_name:
                ident = rest
        if ":" in ident:
            ident = ident.split(":", 1)[0]
        return ident or None
