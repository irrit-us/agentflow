from __future__ import annotations

import json
import subprocess

import httpx
import pytest

from agentflow.lite import (
    ContainerConfig,
    ContainerError,
    DockerExecutor,
    ExecResult,
    LiteAgent,
    LiteLLMClient,
    container_shell_tool,
)
from agentflow.lite.tools import ToolRegistry
from agentflow.lite.types import ToolCall


def _executor(**overrides) -> DockerExecutor:
    config = ContainerConfig(image="python:3.12-slim", workspace="C:/repo", **overrides)
    return DockerExecutor(config)


class TestBuildArgv:
    def test_default_config(self):
        argv = _executor().build_argv("ls -la")

        assert argv[:5] == ["docker", "run", "--rm", "--init", "-i"]
        assert "--network" in argv
        assert argv[argv.index("--network") + 1] == "none"
        assert argv[argv.index("--memory") + 1] == "512m"
        assert argv[argv.index("--cpus") + 1] == "1.0"
        assert "-v" in argv
        assert argv[argv.index("-v") + 1] == "C:/repo:/workspace:ro"
        assert argv[argv.index("-w") + 1] == "/workspace"
        assert argv[-4:] == ["python:3.12-slim", "sh", "-c", "ls -la"]

    def test_read_only_false_mounts_rw(self):
        argv = _executor(read_only=False).build_argv("ls")

        assert argv[argv.index("-v") + 1] == "C:/repo:/workspace:rw"

    def test_bridge_network(self):
        argv = _executor(network="bridge").build_argv("ls")

        assert argv[argv.index("--network") + 1] == "bridge"

    def test_no_workspace_omits_volume(self):
        executor = DockerExecutor(ContainerConfig(image="python:3.12-slim"))

        argv = executor.build_argv("ls")

        assert "-v" not in argv

    def test_env_whitelist(self):
        argv = _executor(env={"TOKEN": "abc"}).build_argv("ls", env={"EXTRA": "1"})

        entries = [argv[i + 1] for i, item in enumerate(argv) if item == "-e"]
        assert entries == ["TOKEN=abc", "EXTRA=1"]

    def test_extra_args_come_before_image(self):
        argv = _executor(extra_args=["--cap-drop", "ALL"]).build_argv("ls")

        cap_index = argv.index("--cap-drop")
        image_index = argv.index("python:3.12-slim")
        assert argv[cap_index + 1] == "ALL"
        assert cap_index < image_index

    def test_workdir_override_and_no_resource_flags(self):
        argv = _executor(memory=None, cpus=None).build_argv("ls", workdir="/tmp")

        assert argv[argv.index("-w") + 1] == "/tmp"
        assert "--memory" not in argv
        assert "--cpus" not in argv


class TestRun:
    def test_success(self, monkeypatch: pytest.MonkeyPatch):
        seen: dict = {}

        def fake_run(argv, **kwargs):
            seen["argv"] = argv
            seen["kwargs"] = kwargs
            return subprocess.CompletedProcess(argv, 0, stdout="hello\n", stderr="warn\n")

        monkeypatch.setattr("agentflow.lite.container.subprocess.run", fake_run)

        result = _executor().run("echo hello")

        assert result == ExecResult(exit_code=0, stdout="hello\n", stderr="warn\n")
        assert seen["kwargs"]["capture_output"] is True
        assert seen["kwargs"]["text"] is True
        assert seen["kwargs"]["timeout"] == 120

    def test_timeout(self, monkeypatch: pytest.MonkeyPatch):
        def fake_run(argv, **kwargs):
            raise subprocess.TimeoutExpired(argv, kwargs["timeout"], output="partial")

        monkeypatch.setattr("agentflow.lite.container.subprocess.run", fake_run)

        result = _executor().run("sleep 999", timeout=5)

        assert result.timed_out is True
        assert result.exit_code == -1
        assert result.stdout == "partial"
        assert "timed out after 5s" in result.stderr

    def test_output_truncation(self, monkeypatch: pytest.MonkeyPatch):
        big = "x" * 60_000

        def fake_run(argv, **kwargs):
            return subprocess.CompletedProcess(argv, 0, stdout=big, stderr="")

        monkeypatch.setattr("agentflow.lite.container.subprocess.run", fake_run)

        result = _executor().run("spam")

        assert result.stdout.startswith("x" * 100)
        assert result.stdout.endswith("\n... [truncated]")
        assert len(result.stdout) == 50_000 + len("\n... [truncated]")

    def test_missing_docker_raises_container_error(self, monkeypatch: pytest.MonkeyPatch):
        def fake_run(argv, **kwargs):
            raise FileNotFoundError("no such file: docker")

        monkeypatch.setattr("agentflow.lite.container.subprocess.run", fake_run)

        with pytest.raises(ContainerError) as exc_info:
            _executor().run("ls")

        assert "docker" in str(exc_info.value)

    def test_available_uses_shutil_which(self, monkeypatch: pytest.MonkeyPatch):
        executor = _executor()
        monkeypatch.setattr("agentflow.lite.container.shutil.which", lambda name: "/usr/bin/docker")
        assert executor.available() is True
        monkeypatch.setattr("agentflow.lite.container.shutil.which", lambda name: None)
        assert executor.available() is False


class TestContainerShellTool:
    def test_schema(self):
        tool = container_shell_tool(_executor())

        assert tool.name == "run_command"
        assert tool.description
        assert tool.parameters["required"] == ["command"]
        assert set(tool.parameters["properties"]) == {"command", "workdir"}

    def test_handler_formats_result(self, monkeypatch: pytest.MonkeyPatch):
        executor = _executor()
        monkeypatch.setattr(
            executor,
            "run",
            lambda command, workdir=None: ExecResult(exit_code=0, stdout="ok\n", stderr=""),
        )
        tool = container_shell_tool(executor)

        output = tool.handler(command="echo ok")

        assert output == "exit_code: 0\nstdout:\nok\n\nstderr:\n"

    def test_handler_marks_timeouts(self, monkeypatch: pytest.MonkeyPatch):
        executor = _executor()
        monkeypatch.setattr(
            executor,
            "run",
            lambda command, workdir=None: ExecResult(
                exit_code=-1, stdout="", stderr="boom", timed_out=True
            ),
        )
        tool = container_shell_tool(executor)

        output = tool.handler(command="sleep 999")

        assert output.startswith("[TIMEOUT after 120s]\n")

    def test_handler_catches_container_error(self, monkeypatch: pytest.MonkeyPatch):
        executor = _executor()

        def boom(command, workdir=None):
            raise ContainerError("failed to invoke 'docker'")

        monkeypatch.setattr(executor, "run", boom)
        tool = container_shell_tool(executor)

        output = tool.handler(command="ls")

        assert output.startswith("Error: ")
        assert "docker" in output


class TestAgentIntegration:
    def test_agent_tool_loop_through_container(self, monkeypatch: pytest.MonkeyPatch):
        responses = [
            {
                "model": "m",
                "choices": [
                    {
                        "index": 0,
                        "message": {
                            "role": "assistant",
                            "content": None,
                            "tool_calls": [
                                {
                                    "id": "call_1",
                                    "type": "function",
                                    "function": {
                                        "name": "run_command",
                                        "arguments": json.dumps({"command": "cat app.py"}),
                                    },
                                }
                            ],
                        },
                        "finish_reason": "tool_calls",
                    }
                ],
                "usage": {"prompt_tokens": 5, "completion_tokens": 5, "total_tokens": 10},
            },
            {
                "model": "m",
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": "the file is clean"},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"prompt_tokens": 5, "completion_tokens": 5, "total_tokens": 10},
            },
        ]

        def http_handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=responses.pop(0))

        seen_commands: list[str] = []

        def fake_run(argv, **kwargs):
            seen_commands.append(argv[-1])
            return subprocess.CompletedProcess(argv, 0, stdout="print('hi')\n", stderr="")

        monkeypatch.setattr("agentflow.lite.container.subprocess.run", fake_run)

        client = LiteLLMClient(
            base_url="http://testserver/v1",
            transport=httpx.MockTransport(http_handler),
        )
        executor = DockerExecutor(
            ContainerConfig(image="python:3.12-slim", workspace="C:/repo")
        )
        registry = ToolRegistry([container_shell_tool(executor)])
        agent = LiteAgent(client=client, model="m", tools=registry)

        result = agent.run("read app.py in the sandbox")

        assert result.text == "the file is clean"
        assert result.iterations == 2
        assert seen_commands == ["cat app.py"]
        tool_message = result.messages[2]
        assert tool_message.role == "tool"
        assert "exit_code: 0" in (tool_message.content or "")
        assert "print('hi')" in (tool_message.content or "")
