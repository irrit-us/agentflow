from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from agentflow.runners.ecs import ECSRunner
from agentflow.specs import NodeSpec


def _ecs_node(timeout_seconds: int = 1800) -> NodeSpec:
    return NodeSpec.model_validate(
        {
            "id": "probe",
            "agent": "codex",
            "prompt": "run",
            "target": {
                "kind": "ecs",
                "cluster": "test-cluster",
                "image": "ubuntu:24.04",
                "region": "us-east-1",
            },
            "timeout_seconds": timeout_seconds,
        }
    )


class _FakeLogsClient:
    def __init__(self, pages: list[list[dict]]) -> None:
        self.pages = pages
        self.fetch_count = 0

    def describe_log_streams(self, **_kwargs):
        return {"logStreams": [{"logStreamName": "stream-1"}]}

    def get_log_events(self, **_kwargs):
        page = self.pages[min(self.fetch_count, len(self.pages) - 1)]
        self.fetch_count += 1
        return {"events": page}


class _FakeECSClient:
    def __init__(self, statuses: list[str], exit_code: int = 0) -> None:
        self.statuses = statuses
        self.exit_code = exit_code
        self.poll_count = 0
        self.stopped: list[str] = []

    def describe_tasks(self, **_kwargs):
        status = self.statuses[min(self.poll_count, len(self.statuses) - 1)]
        self.poll_count += 1
        return {"tasks": [{"lastStatus": status, "containers": [{"exitCode": self.exit_code}]}]}

    def stop_task(self, **kwargs):
        self.stopped.append(kwargs.get("reason", ""))


def _install_fake_boto3(monkeypatch, ecs_client: _FakeECSClient, logs_client: _FakeLogsClient) -> None:
    clients = {"ecs": ecs_client, "logs": logs_client}
    fake_boto3 = SimpleNamespace(client=lambda service, region_name=None: clients[service])
    monkeypatch.setitem(__import__("sys").modules, "boto3", fake_boto3)
    real_sleep = asyncio.sleep
    monkeypatch.setattr(asyncio, "sleep", lambda *_args, **_kwargs: real_sleep(0))


@pytest.mark.asyncio
async def test_ecs_wait_streams_logs_incrementally(monkeypatch):
    ecs_client = _FakeECSClient(["RUNNING", "RUNNING", "STOPPED"])
    logs_client = _FakeLogsClient(
        [
            [{"timestamp": 1, "message": "line1"}],
            [{"timestamp": 1, "message": "line1"}, {"timestamp": 2, "message": "line2"}],
            [{"timestamp": 1, "message": "line1"}, {"timestamp": 2, "message": "line2"}, {"timestamp": 3, "message": "line3"}],
        ]
    )
    _install_fake_boto3(monkeypatch, ecs_client, logs_client)

    streamed: list[tuple[str, str]] = []
    polls_at_stream: list[int] = []

    async def on_output(stream: str, line: str) -> None:
        streamed.append((stream, line))
        polls_at_stream.append(ecs_client.poll_count)

    exit_code, stdout_lines, stderr_lines, timed_out, cancelled = await ECSRunner()._wait_for_task(
        _ecs_node(), "arn:task", on_output, lambda: False
    )

    assert exit_code == 0
    assert not timed_out and not cancelled
    assert stdout_lines == ["line1", "line2", "line3"]
    # Lines were forwarded as they arrived, not replayed after STOPPED:
    # line1 streamed after the first poll, before the task stopped.
    assert streamed[0] == ("stdout", "line1")
    assert polls_at_stream[0] == 1
    assert polls_at_stream[-1] <= 4  # includes the final post-stop fetch


@pytest.mark.asyncio
async def test_ecs_wait_honors_cancellation(monkeypatch):
    ecs_client = _FakeECSClient(["RUNNING"])
    logs_client = _FakeLogsClient([[]])
    _install_fake_boto3(monkeypatch, ecs_client, logs_client)

    cancel_after = {"polls": 0}

    def should_cancel() -> bool:
        cancel_after["polls"] += 1
        return cancel_after["polls"] >= 1

    async def on_output(stream: str, line: str) -> None:
        pass

    exit_code, _stdout, stderr_lines, timed_out, cancelled = await ECSRunner()._wait_for_task(
        _ecs_node(), "arn:task", on_output, should_cancel
    )

    assert exit_code == 130
    assert cancelled and not timed_out
    assert stderr_lines == ["Cancelled"]
    assert ecs_client.stopped == ["cancelled by agentflow"]


@pytest.mark.asyncio
async def test_ecs_wait_honors_timeout(monkeypatch):
    ecs_client = _FakeECSClient(["RUNNING"])
    logs_client = _FakeLogsClient([[]])
    _install_fake_boto3(monkeypatch, ecs_client, logs_client)

    clock = {"now": 1000.0}

    def fake_monotonic() -> float:
        clock["now"] += 10.0
        return clock["now"]

    monkeypatch.setattr("agentflow.runners.ecs.time.monotonic", fake_monotonic)

    async def on_output(stream: str, line: str) -> None:
        pass

    exit_code, _stdout, stderr_lines, timed_out, cancelled = await ECSRunner()._wait_for_task(
        _ecs_node(timeout_seconds=1), "arn:task", on_output, lambda: False
    )

    assert exit_code == 124
    assert timed_out and not cancelled
    assert "Timed out" in stderr_lines[0]
    assert ecs_client.stopped == ["agentflow node timeout"]
