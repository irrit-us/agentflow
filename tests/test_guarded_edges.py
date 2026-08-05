from __future__ import annotations

import asyncio
import sys

import pytest

from agentflow import Graph, agent
from agentflow.agents.base import AgentAdapter
from agentflow.agents.registry import AdapterRegistry
from agentflow.orchestrator import Orchestrator
from agentflow.prepared import ExecutionPaths, PreparedExecution
from agentflow.runners.registry import RunnerRegistry
from agentflow.specs import AgentKind, NodeStatus, PipelineSpec, RunStatus
from agentflow.store import RunStore


class OutcomeAdapter(AgentAdapter):
    def __init__(self, failing: set[str]) -> None:
        self.failing = failing

    def prepare(self, node, prompt: str, paths: ExecutionPaths) -> PreparedExecution:
        exit_code = 1 if node.id in self.failing else 0
        script = (
            "import json\n"
            f"print(json.dumps({{'type': 'result', 'result': '{node.id} done'}}))\n"
            f"raise SystemExit({exit_code})\n"
        )
        return PreparedExecution(
            command=[sys.executable, "-c", script],
            env={},
            cwd=paths.target_workdir,
            trace_kind="codex",
        )


def _make_orchestrator(tmp_path, failing: set[str]) -> Orchestrator:
    adapters = AdapterRegistry()
    adapters.register(AgentKind.CODEX, OutcomeAdapter(failing))
    return Orchestrator(store=RunStore(tmp_path / "runs"), adapters=adapters, runners=RunnerRegistry())


def _guarded_pipeline(tmp_path) -> PipelineSpec:
    return PipelineSpec.model_validate(
        {
            "name": "guarded",
            "working_dir": str(tmp_path),
            "nodes": [
                {"id": "risky", "agent": "codex", "prompt": "try the risky thing"},
                {
                    "id": "fallback",
                    "agent": "codex",
                    "depends_on_failure": ["risky"],
                    "prompt": "handle {{ nodes.risky.status }}",
                },
                {
                    "id": "report",
                    "agent": "codex",
                    "depends_on": ["fallback"],
                    "prompt": "report {{ nodes.fallback.output }}",
                },
            ],
        }
    )


def test_dsl_on_fail_and_on_ok_edges():
    with Graph("guards") as graph:
        risky = agent("codex", task_id="risky", prompt="try")
        fallback = agent("codex", task_id="fallback", prompt="fix")
        report = agent("codex", task_id="report", prompt="report")
        risky.on_fail >> fallback
        risky.on_ok >> report

    payload = graph.to_payload()
    nodes = {node["id"]: node for node in payload["nodes"]}
    assert nodes["fallback"]["depends_on_failure"] == ["risky"]
    assert "depends_on_failure" not in nodes["report"]
    assert nodes["report"]["depends_on"] == ["risky"]


def test_pipeline_validation_rejects_unknown_failure_dependency(tmp_path):
    with pytest.raises(ValueError, match="unknown dependencies"):
        PipelineSpec.model_validate(
            {
                "name": "bad-guard",
                "working_dir": str(tmp_path),
                "nodes": [
                    {
                        "id": "fallback",
                        "agent": "codex",
                        "depends_on_failure": ["missing"],
                        "prompt": "fix",
                    },
                ],
            }
        )


def test_failure_guarded_edge_runs_handler_and_recovers_run(tmp_path):
    orchestrator = _make_orchestrator(tmp_path, failing={"risky"})

    run = asyncio.run(orchestrator.submit(_guarded_pipeline(tmp_path)))
    completed = asyncio.run(orchestrator.wait(run.id, timeout=30))

    assert completed.status == RunStatus.COMPLETED
    assert completed.nodes["risky"].status == NodeStatus.FAILED
    assert completed.nodes["fallback"].status == NodeStatus.COMPLETED
    assert completed.nodes["report"].status == NodeStatus.COMPLETED


def test_failure_guarded_edge_skips_handler_on_success(tmp_path):
    orchestrator = _make_orchestrator(tmp_path, failing=set())

    run = asyncio.run(orchestrator.submit(_guarded_pipeline(tmp_path)))
    completed = asyncio.run(orchestrator.wait(run.id, timeout=30))

    assert completed.status == RunStatus.COMPLETED
    assert completed.nodes["risky"].status == NodeStatus.COMPLETED
    assert completed.nodes["fallback"].status == NodeStatus.SKIPPED
    assert completed.nodes["report"].status == NodeStatus.SKIPPED
