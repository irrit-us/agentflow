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
from agentflow.specs import AgentKind, PipelineSpec, RunStatus
from agentflow.store import RunStore


class FileProducingAdapter(AgentAdapter):
    def prepare(self, node, prompt: str, paths: ExecutionPaths) -> PreparedExecution:
        if node.id == "producer":
            script = (
                "import json\n"
                "from pathlib import Path\n"
                "Path('coverage.txt').write_text('cov: convert_colsp 87%', encoding='utf-8')\n"
                "print(json.dumps({'type': 'result', 'result': 'produced'}))\n"
            )
            command = [sys.executable, "-c", script]
        else:
            script = (
                "import json, sys\n"
                "print(json.dumps({'type': 'result', 'result': sys.argv[1]}))\n"
            )
            command = [sys.executable, "-c", script, prompt]
        return PreparedExecution(command=command, env={}, cwd=paths.target_workdir, trace_kind="codex")


def _make_orchestrator(tmp_path) -> Orchestrator:
    adapters = AdapterRegistry()
    adapters.register(AgentKind.CODEX, FileProducingAdapter())
    return Orchestrator(store=RunStore(tmp_path / "runs"), adapters=adapters, runners=RunnerRegistry())


def test_feedback_channel_flows_into_downstream_prompt(tmp_path):
    orchestrator = _make_orchestrator(tmp_path)
    pipeline = PipelineSpec.model_validate(
        {
            "name": "feedback",
            "working_dir": str(tmp_path),
            "feedback_channels": {
                "cov": {"after": "producer", "path": "coverage.txt"},
            },
            "nodes": [
                {"id": "producer", "agent": "codex", "prompt": "produce coverage"},
                {
                    "id": "consumer",
                    "agent": "codex",
                    "depends_on": ["producer"],
                    "capture": "trace",
                    "prompt": "coverage is: {{ feedback.cov }}",
                },
            ],
        }
    )

    run = asyncio.run(orchestrator.submit(pipeline))
    completed = asyncio.run(orchestrator.wait(run.id, timeout=30))

    assert completed.status == RunStatus.COMPLETED
    assert "cov: convert_colsp 87%" in completed.nodes["consumer"].output


def test_feedback_channel_validation_rejects_unknown_reference(tmp_path):
    with pytest.raises(ValueError, match="unknown feedback channels"):
        PipelineSpec.model_validate(
            {
                "name": "bad-feedback-ref",
                "working_dir": str(tmp_path),
                "nodes": [{"id": "a", "agent": "codex", "prompt": "read {{ feedback.san }}"}],
            }
        )


def test_feedback_channel_validation_rejects_unknown_anchor(tmp_path):
    with pytest.raises(ValueError, match="unknown nodes in `after`"):
        PipelineSpec.model_validate(
            {
                "name": "bad-feedback-anchor",
                "working_dir": str(tmp_path),
                "feedback_channels": {"cov": {"after": "missing", "path": "cov.txt"}},
                "nodes": [{"id": "a", "agent": "codex", "prompt": "hi"}],
            }
        )


def test_feedback_channel_validation_requires_exactly_one_source(tmp_path):
    with pytest.raises(ValueError, match="exactly one of `path` or `command`"):
        PipelineSpec.model_validate(
            {
                "name": "bad-feedback-source",
                "working_dir": str(tmp_path),
                "feedback_channels": {"cov": {"after": "a", "path": "cov.txt", "command": "cat cov.txt"}},
                "nodes": [{"id": "a", "agent": "codex", "prompt": "hi"}],
            }
        )


def test_dsl_feedback_channels_payload():
    with Graph("feedback-dsl", feedback_channels={"san": {"after": "build", "command": "cat asan.log"}}) as graph:
        agent("codex", task_id="build", prompt="build")

    payload = graph.to_payload()
    assert payload["feedback_channels"] == {"san": {"after": "build", "command": "cat asan.log"}}
    spec = graph.to_spec()
    assert spec.feedback_channels["san"].command == "cat asan.log"
