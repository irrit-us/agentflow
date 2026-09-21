from __future__ import annotations

import asyncio
import os
import signal
from unittest.mock import AsyncMock, Mock

import pytest

from agentflow.prepared import ExecutionPaths, PreparedExecution
from agentflow.runners.local import LocalRunner
from agentflow.specs import NodeSpec


@pytest.mark.skipif(os.name != "posix", reason="POSIX process groups")
@pytest.mark.asyncio
async def test_group_cleanup_escalates_even_after_leader_exits(monkeypatch):
    signals = []
    monkeypatch.setattr(os, "killpg", lambda pid, sig: signals.append((pid, sig)))
    monkeypatch.setattr(LocalRunner, "_TERMINATE_GRACE_SECONDS", 0)
    process = Mock(pid=12345)
    task = asyncio.create_task(asyncio.sleep(0, result=0))
    await task
    await LocalRunner()._terminate_with_fallback(process, task)
    assert signals == [(12345, signal.SIGTERM), (12345, signal.SIGKILL)]
    process.terminate.assert_not_called()
    process._transport.close.assert_called_once()


@pytest.mark.skipif(os.name != "posix", reason="POSIX process groups")
@pytest.mark.asyncio
@pytest.mark.parametrize("blocked_stdin", [False, True])
async def test_task_cancellation_cleans_owned_group_and_streams(tmp_path, monkeypatch, blocked_stdin):
    launched = asyncio.Event()
    process = Mock(pid=12345, stdin=None)

    async def pending():
        await asyncio.Event().wait()

    if blocked_stdin:
        process.stdin = Mock()
        process.stdin.drain = pending
    process.stdout.readline = pending
    process.stderr.readline = pending
    wait_finished = asyncio.Event()
    process.wait = wait_finished.wait
    signals = []

    async def launch(*args, **kwargs):
        assert kwargs["start_new_session"] is True
        launched.set()
        return process

    def killpg(pid, sig):
        signals.append((pid, sig))
        wait_finished.set()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", launch)
    monkeypatch.setattr(os, "killpg", killpg)
    monkeypatch.setattr(LocalRunner, "_TERMINATE_GRACE_SECONDS", 0)
    paths = ExecutionPaths(host_workdir=tmp_path, host_runtime_dir=tmp_path / "runtime",
                           target_workdir=str(tmp_path), target_runtime_dir=str(tmp_path / "runtime"),
                           app_root=tmp_path)
    node = NodeSpec.model_validate({"id": "test", "agent": "codex", "prompt": "hi"})
    prepared = PreparedExecution(command=["fake"], env={}, cwd=str(tmp_path), trace_kind="codex",
                                 stdin="prompt" if blocked_stdin else None)
    execution = asyncio.create_task(LocalRunner().execute(
        node, prepared, paths, AsyncMock(), lambda: False))
    await launched.wait()
    await asyncio.sleep(0)
    execution.cancel()
    with pytest.raises(asyncio.CancelledError):
        await execution
    assert signals == [(12345, signal.SIGTERM), (12345, signal.SIGKILL)]
    process._transport.close.assert_called_once()
