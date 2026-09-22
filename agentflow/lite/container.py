from __future__ import annotations

import shutil
import subprocess
import uuid
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from agentflow.lite.tools import Tool
from agentflow.lite.volumes import Mount

_OUTPUT_LIMIT = 50_000


class ContainerError(Exception):
    """Raised when the container runtime cannot be invoked."""


class ExecResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    exit_code: int
    stdout: str
    stderr: str
    timed_out: bool = False


class ContainerConfig(BaseModel):
    """Ephemeral container settings with safe defaults for security-audit workloads.

    Mounting recipes:
    - RAG / knowledge base: ``Mount(type="bind", source=..., read_only=True)``
      for a read-only host directory.
    - Data transfer between containers: a shared named volume,
      ``Mount(type="volume", source="shared", ...)`` mounted rw in each container.
    """

    model_config = ConfigDict(extra="forbid")

    image: str
    workspace: str | None = None
    container_workdir: str = "/workspace"
    read_only: bool = True
    network: Literal["none", "bridge", "host"] = "none"
    memory: str | None = "512m"
    cpus: float | None = 1.0
    env: dict[str, str] = Field(default_factory=dict)
    timeout: int = 120
    extra_args: list[str] = Field(default_factory=list)
    mounts: list[Mount] = Field(default_factory=list)


def _truncate(text: str) -> str:
    if len(text) > _OUTPUT_LIMIT:
        return text[:_OUTPUT_LIMIT] + "\n... [truncated]"
    return text


def _as_text(value: Any) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value or ""


class DockerExecutor:
    """Runs shell commands in ephemeral Docker containers via the docker CLI."""

    def __init__(self, config: ContainerConfig, docker_bin: str = "docker"):
        self.config = config
        self.docker_bin = docker_bin

    def available(self) -> bool:
        return shutil.which(self.docker_bin) is not None

    def build_argv(
        self,
        command: str,
        workdir: str | None = None,
        env: dict[str, str] | None = None,
        container_name: str | None = None,
    ) -> list[str]:
        config = self.config
        argv = [self.docker_bin, "run", "--rm", "--init", "-i"]
        if container_name is not None:
            # A stable name lets a timeout path find and remove the container:
            # killing the foreground CLI does not stop the daemon-side
            # container, so an unnamed one would leak.
            argv += ["--name", container_name]
        argv += ["--network", config.network]
        if config.memory is not None:
            argv += ["--memory", config.memory]
        if config.cpus is not None:
            argv += ["--cpus", str(config.cpus)]
        for mount in config.mounts:
            argv += ["--mount", mount.to_docker_mount()]
        if config.workspace is not None:
            mode = "ro" if config.read_only else "rw"
            argv += ["-v", f"{config.workspace}:{config.container_workdir}:{mode}"]
        argv += ["-w", workdir or config.container_workdir]
        merged_env = {**config.env, **(env or {})}
        for key, value in merged_env.items():
            argv += ["-e", f"{key}={value}"]
        argv += config.extra_args
        argv += [config.image, "sh", "-c", command]
        return argv

    def _force_remove(self, container_name: str) -> str:
        """Best-effort container cleanup after a timeout; never raises."""
        try:
            completed = subprocess.run(
                [self.docker_bin, "rm", "-f", container_name],
                capture_output=True,
                timeout=10,
            )
        except (subprocess.TimeoutExpired, OSError) as exc:
            return f"\n[warning: failed to remove container {container_name}: {exc}]"
        if completed.returncode != 0:
            diagnostic = _as_text(completed.stderr).strip()[:1000]
            return (f"\n[warning: failed to remove container {container_name}: "
                    f"exit code {completed.returncode}; {diagnostic or 'no diagnostic'}]")
        return f"\n[container {container_name} force-removed after timeout]"

    def run(
        self,
        command: str,
        workdir: str | None = None,
        env: dict[str, str] | None = None,
        timeout: int | None = None,
    ) -> ExecResult:
        effective_timeout = timeout if timeout is not None else self.config.timeout
        container_name = f"agentflow-lite-{uuid.uuid4().hex[:12]}"
        argv = self.build_argv(command, workdir=workdir, env=env,
                               container_name=container_name)
        try:
            completed = subprocess.run(
                argv,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=effective_timeout,
            )
        except subprocess.TimeoutExpired as exc:
            # subprocess.run kills the foreground docker CLI on timeout, but
            # the daemon-side container keeps running; remove it by name.
            cleanup_note = self._force_remove(container_name)
            return ExecResult(
                exit_code=-1,
                stdout=_truncate(_as_text(exc.stdout)),
                stderr=_truncate(_as_text(exc.stderr))
                + f"\n[command timed out after {effective_timeout}s]"
                + cleanup_note,
                timed_out=True,
            )
        except (FileNotFoundError, OSError) as exc:
            raise ContainerError(f"failed to invoke '{self.docker_bin}': {exc}") from exc
        return ExecResult(
            exit_code=completed.returncode,
            stdout=_truncate(completed.stdout or ""),
            stderr=_truncate(completed.stderr or ""),
        )


def container_shell_tool(
    executor: DockerExecutor,
    name: str = "run_command",
    description: str | None = None,
) -> Tool:
    """Build a shell-command :class:`Tool` backed by a :class:`DockerExecutor`."""

    def handler(command: str, workdir: str | None = None) -> str:
        try:
            result = executor.run(command, workdir=workdir)
        except Exception as exc:  # noqa: BLE001 - tool failures are reported, not raised
            return f"Error: {exc}"
        parts: list[str] = []
        if result.timed_out:
            parts.append(f"[TIMEOUT after {executor.config.timeout}s]")
        parts.append(f"exit_code: {result.exit_code}")
        parts.append(f"stdout:\n{result.stdout}")
        parts.append(f"stderr:\n{result.stderr}")
        return "\n".join(parts)

    return Tool(
        name=name,
        description=(
            description
            if description is not None
            else "Run a shell command inside an isolated container and return its output."
        ),
        parameters={
            "type": "object",
            "properties": {
                "command": {"type": "string", "description": "Shell command to execute."},
                "workdir": {
                    "type": "string",
                    "description": "Working directory inside the container.",
                },
            },
            "required": ["command"],
        },
        handler=handler,
    )
