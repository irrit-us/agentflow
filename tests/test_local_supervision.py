from __future__ import annotations

import asyncio
import os
import signal
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from agentflow.prepared import ExecutionPaths, PreparedExecution
from agentflow.runners.local import LocalRunner
from agentflow.specs import NodeSpec


@pytest.fixture
def harness(tmp_path, monkeypatch):
    exited = asyncio.Event()
    launched = asyncio.Event()
    stdin_started = asyncio.Event()
    term_sent = asyncio.Event()
    stream_started = asyncio.Event()
    signals = []
    process = Mock(pid=54321, returncode=None, stdin=None)

    async def wait():
        await exited.wait()
        return process.returncode

    async def pending_read():
        stream_started.set()
        await asyncio.Event().wait()

    async def pending_stdin():
        stdin_started.set()
        await asyncio.Event().wait()

    async def launch(*args, **kwargs):
        launched.set()
        return process

    def killpg(pid, sig):
        signals.append(sig)
        if sig == signal.SIGTERM:
            term_sent.set()
        process.returncode = -sig
        exited.set()

    async def output(*args):
        pass

    process.wait = wait
    process.stdout.readline = pending_read
    process.stderr.readline = pending_read
    monkeypatch.setattr(asyncio, "create_subprocess_exec", launch)
    monkeypatch.setattr(os, "killpg", killpg)
    monkeypatch.setattr(LocalRunner, "_TERMINATE_GRACE_SECONDS", 0.01)
    paths = ExecutionPaths(host_workdir=tmp_path, host_runtime_dir=tmp_path / "runtime",
                           target_workdir=str(tmp_path), target_runtime_dir=str(tmp_path / "runtime"),
                           app_root=tmp_path)
    node = NodeSpec.model_validate({"id": "probe", "agent": "codex", "prompt": "hi"})
    prepared = PreparedExecution(command=["fake"], env={}, cwd=str(tmp_path), trace_kind="codex")
    return SimpleNamespace(**locals())


pytestmark = [pytest.mark.asyncio, pytest.mark.skipif(os.name != "posix", reason="POSIX supervision")]


async def test_cooperative_cancel_during_blocked_stdin(harness):
    h = harness
    h.process.stdin = Mock(drain=h.pending_stdin)
    h.prepared.stdin = "prompt"
    task = asyncio.create_task(LocalRunner().execute(h.node, h.prepared, h.paths, h.output,
                                                    h.stdin_started.is_set))
    result = await asyncio.wait_for(task, 0.5)
    assert result.cancelled and not result.timed_out
    assert result.exit_code == 130
    assert signal.SIGKILL in h.signals


async def test_eof_does_not_override_node_deadline(harness):
    h = harness

    async def eof():
        return b""

    h.process.stdout.readline = eof
    h.process.stderr.readline = eof
    h.node.timeout_seconds = 1
    result = await asyncio.wait_for(LocalRunner().execute(
        h.node, h.prepared, h.paths, h.output, lambda: False), 1.5)
    assert result.timed_out and result.exit_code == 124


async def test_callback_error_propagates_after_cleanup(harness):
    h = harness

    async def line():
        return b"hello\n"

    async def fail(*args):
        raise RuntimeError("artifact store unavailable")

    h.process.stdout.readline = line
    with pytest.raises(RuntimeError, match="artifact store unavailable"):
        await asyncio.wait_for(LocalRunner().execute(
            h.node, h.prepared, h.paths, fail, lambda: False), 0.5)
    assert signal.SIGKILL in h.signals


async def test_repeated_cancel_cannot_interrupt_cleanup(harness):
    h = harness
    task = asyncio.create_task(LocalRunner().execute(h.node, h.prepared, h.paths, h.output, lambda: False))
    await h.stream_started.wait()
    task.cancel()
    await h.term_sent.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert signal.SIGKILL in h.signals
    h.process._transport.close.assert_called_once()


async def test_cancel_during_final_stream_drain_cleans_group(harness):
    h = harness
    h.process.returncode = 0
    h.exited.set()
    task = asyncio.create_task(LocalRunner().execute(h.node, h.prepared, h.paths, h.output, lambda: False))
    await h.stream_started.wait()
    await asyncio.sleep(0.02)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert signal.SIGKILL in h.signals


async def test_broken_stdin_preserves_real_exit_code(harness):
    h = harness

    async def broken():
        raise BrokenPipeError()

    async def eof():
        return b""

    h.process.stdin = Mock(drain=broken)
    h.process.stdout.readline = eof
    h.process.stderr.readline = eof
    h.process.returncode = 7
    h.exited.set()
    h.prepared.stdin = "prompt"
    result = await LocalRunner().execute(h.node, h.prepared, h.paths, h.output, lambda: False)
    assert result.exit_code == 7 and not result.timed_out


async def test_external_completion_setup_error_cleans_child(harness):
    h = harness

    class BrokenRunner(LocalRunner):
        def _external_completion(self, *args):
            raise RuntimeError("monitor setup failed")

    with pytest.raises(RuntimeError, match="monitor setup failed"):
        await BrokenRunner().execute(h.node, h.prepared, h.paths, h.output, lambda: False)
    assert signal.SIGKILL in h.signals


async def test_recorded_exit_does_not_wait_for_pipe_bound_wait_task(harness):
    h = harness

    async def eof():
        return b""

    h.process.stdout.readline = eof
    h.process.stderr.readline = eof
    h.process.returncode = 0
    # The child watcher knows the exit code, but Process.wait is still pending.
    result = await asyncio.wait_for(LocalRunner().execute(
        h.node, h.prepared, h.paths, h.output, lambda: False), 0.5)
    assert result.exit_code == 0 and not result.timed_out
    assert not h.signals
