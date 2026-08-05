from __future__ import annotations

import asyncio
import sys
import threading

import pytest

from agentflow.agents.base import AgentAdapter
from agentflow.agents.registry import AdapterRegistry
from agentflow.orchestrator import Orchestrator
from agentflow.prepared import ExecutionPaths, PreparedExecution
from agentflow.runners.local import LocalRunner
from agentflow.runners.registry import RunnerRegistry
from agentflow.specs import AgentKind, PipelineSpec, RunStatus
from agentflow.store import RunStore


class SleepAdapter(AgentAdapter):
    def prepare(self, node, prompt: str, paths: ExecutionPaths) -> PreparedExecution:
        script = (
            "import json, time\n"
            "time.sleep(0.3)\n"
            "print(json.dumps({'type': 'result', 'result': 'ok'}))\n"
        )
        return PreparedExecution(
            command=[sys.executable, "-c", script],
            env={},
            cwd=paths.target_workdir,
            trace_kind="codex",
        )


class TrackingRunner(LocalRunner):
    def __init__(self) -> None:
        self.active = 0
        self.peak = 0
        self._lock = threading.Lock()

    async def execute(self, node, prepared, paths, on_output, should_cancel):
        with self._lock:
            self.active += 1
            self.peak = max(self.peak, self.active)
        try:
            return await super().execute(node, prepared, paths, on_output, should_cancel)
        finally:
            with self._lock:
                self.active -= 1


def _make_orchestrator(tmp_path, runner: TrackingRunner) -> Orchestrator:
    adapters = AdapterRegistry()
    adapters.register(AgentKind.CODEX, SleepAdapter())
    runners = RunnerRegistry()
    runners.register("local", runner)
    return Orchestrator(store=RunStore(tmp_path / "runs"), adapters=adapters, runners=runners)


def _pipeline(tmp_path, *, rate_limits: dict[str, int] | None) -> PipelineSpec:
    payload = {
        "name": "rate-limit",
        "working_dir": str(tmp_path),
        "concurrency": 4,
        "nodes": [
            {"id": f"worker_{index}", "agent": "codex", "prompt": f"work {index}"}
            for index in range(3)
        ],
    }
    if rate_limits is not None:
        payload["rate_limits"] = rate_limits
    return PipelineSpec.model_validate(payload)


def test_rate_limits_cap_concurrent_nodes_per_agent(tmp_path):
    runner = TrackingRunner()
    orchestrator = _make_orchestrator(tmp_path, runner)

    run = asyncio.run(orchestrator.submit(_pipeline(tmp_path, rate_limits={"codex": 1})))
    completed = asyncio.run(orchestrator.wait(run.id, timeout=30))

    assert completed.status == RunStatus.COMPLETED
    assert runner.peak == 1


def test_rate_limits_default_to_unlimited(tmp_path):
    runner = TrackingRunner()
    orchestrator = _make_orchestrator(tmp_path, runner)

    run = asyncio.run(orchestrator.submit(_pipeline(tmp_path, rate_limits=None)))
    completed = asyncio.run(orchestrator.wait(run.id, timeout=30))

    assert completed.status == RunStatus.COMPLETED
    assert runner.peak == 3


def test_rate_limits_rejects_invalid_entries(tmp_path):
    with pytest.raises(ValueError, match="at least 1"):
        _pipeline(tmp_path, rate_limits={"codex": 0})
    with pytest.raises(ValueError, match="non-empty agent names"):
        _pipeline(tmp_path, rate_limits={"  ": 1})
