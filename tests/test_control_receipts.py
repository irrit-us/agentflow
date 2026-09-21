from __future__ import annotations

import json

import pytest

from agentflow.orchestrator import Orchestrator, _PeriodicActionEnvelope
from agentflow.specs import NodeResult, NodeStatus, PipelineSpec, RunRecord
from agentflow.store import RunStore


async def harness(tmp_path, *, limit=1, allowed=None):
    pipeline = PipelineSpec.model_validate({"name": "control", "working_dir": str(tmp_path), "nodes": [
        {"id": "worker", "agent": "shell", "prompt": "true", "fanout": {"count": 1, "as": "item"}},
        {"id": "outside", "agent": "shell", "prompt": "true"},
        {"id": "controller", "agent": "shell", "prompt": "true", "schedule": {
            "every_seconds": 1, "until_fanout_settles_from": "worker", "actuation": "output_json",
            "allowed_actions": ["cancel", "rerun"] if allowed is None else allowed,
            "max_reruns_per_member": limit}},
    ]})
    store = RunStore(tmp_path / "runs")
    record = RunRecord(id="test", pipeline=pipeline,
        nodes={node.id: NodeResult(node_id=node.id, status=NodeStatus.FAILED) for node in pipeline.nodes})
    await store.create_run(record)
    return Orchestrator(store=store), record


async def apply(orchestrator, *, kind="rerun", target="worker_0", command="command", **extra):
    await orchestrator._apply_periodic_actions("test", "controller", watched_group="outside",
        actions=_PeriodicActionEnvelope.model_validate({"actions": [
            {"kind": kind, "node_ids": [target], "command_ids": [command], **extra}]}),
        remaining=set(), in_progress={})


@pytest.mark.asyncio
async def test_receipts_charge_once_and_survive_store_reload(tmp_path):
    orchestrator, record = await harness(tmp_path)
    await apply(orchestrator)
    target = record.nodes["worker_0"]
    assert target.control_rerun_count == 1
    assert target.status == NodeStatus.PENDING
    await apply(orchestrator)
    assert len(record.nodes["controller"].control_receipts) == 1
    store = RunStore(tmp_path / "runs")
    loaded = store.get_run("test")
    assert loaded.nodes["worker_0"].control_rerun_count == 1
    loaded.nodes["worker_0"].status = NodeStatus.FAILED
    other = Orchestrator(store=store)
    await apply(other, command="second")
    receipt = loaded.nodes["controller"].control_receipts[-1]
    assert receipt["status"] == "rejected"
    assert receipt["reason"] == "rerun_budget_exhausted"
    result = json.loads(store.read_artifact_text("test", "controller", "control-results.json"))
    assert result["receipts"][0]["effect"] == "rerun_queued"
    assert result["receipts"][0]["command_ids"] == ["command"]


@pytest.mark.asyncio
async def test_submitted_policy_controls_scope_and_action(tmp_path):
    orchestrator, record = await harness(tmp_path, allowed=["cancel"])
    await apply(orchestrator)
    await apply(orchestrator, kind="cancel", target="outside", command="outside")
    receipts = record.nodes["controller"].control_receipts
    assert [r["reason"] for r in receipts] == ["action_not_authorized", "outside_watched_fanout"]
    assert record.nodes["worker_0"].control_rerun_count == 0
    assert not orchestrator._node_cancel_flags.get("test")


@pytest.mark.asyncio
async def test_cancel_receipt_does_not_claim_process_termination(tmp_path):
    orchestrator, record = await harness(tmp_path)
    record.nodes["worker_0"].status = NodeStatus.RUNNING
    await apply(orchestrator, kind="cancel")
    receipt = record.nodes["controller"].control_receipts[0]
    assert receipt["effect"] == "cancel_requested"
    assert record.nodes["worker_0"].status == NodeStatus.RUNNING
    assert "worker_0" in orchestrator._node_cancel_flags["test"]


def test_controller_cannot_override_policy_in_envelope(tmp_path):
    _, error = Orchestrator(store=RunStore(tmp_path / "runs"))._parse_periodic_actions(json.dumps({"actions": [
        {"kind": "rerun", "node_ids": ["worker_0"], "max_reruns_per_member": 999}]}))
    assert error


@pytest.mark.asyncio
async def test_zero_budget_blocks_uncorrelated_actions_too(tmp_path):
    orchestrator, record = await harness(tmp_path, limit=0)
    await orchestrator._apply_periodic_actions('test', 'controller', watched_group='worker',
        actions=_PeriodicActionEnvelope.model_validate({'actions': [{'kind': 'rerun', 'node_ids': ['worker_0']}]}),
        remaining=set(), in_progress={})
    assert record.nodes['worker_0'].status == NodeStatus.FAILED
    assert record.nodes['controller'].control_receipts[-1]['reason'] == 'rerun_budget_exhausted'


@pytest.mark.asyncio
async def test_mixed_replay_reports_fresh_command_without_double_charge(tmp_path):
    orchestrator, record = await harness(tmp_path, limit=5)
    await apply(orchestrator)
    await orchestrator._apply_periodic_actions('test', 'controller', watched_group='worker',
        actions=_PeriodicActionEnvelope.model_validate({'actions': [
            {'kind': 'rerun', 'node_ids': ['worker_0'], 'command_ids': ['command', 'fresh']}]}),
        remaining=set(), in_progress={})
    receipt = record.nodes['controller'].control_receipts[-1]
    assert receipt['command_ids'] == ['fresh']
    assert receipt['status'] == 'rejected'
    assert record.nodes['worker_0'].control_rerun_count == 1
