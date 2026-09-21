from __future__ import annotations

import json
import sys

import pytest

from agentflow import Graph, command, fanout, python_call, python_node
from agentflow.loader import load_pipeline_from_data
from agentflow.orchestrator import Orchestrator
from agentflow.specs import PipelineSpec
from agentflow.store import RunStore


@pytest.mark.parametrize('decision,count', [(0, 0), (2, 2), (3, 3), (False, 0), (True, 3)])
async def test_gate_selects_declared_fanout_and_join_waits(tmp_path, decision, count):
    with Graph('gate', working_dir=str(tmp_path), use_worktree=False) as graph:
        python_node(task_id='gate', code=f'print({json.dumps(json.dumps({"count": decision}))})')
        fanout(python_node(task_id='worker', code='print("executed")', activation={'source': 'gate', 'path': ['count']}), 3)
        python_node(task_id='join', code='print("joined")', depends_on=['worker'], trigger_rule='all_done')
    pipeline = load_pipeline_from_data(graph.to_payload())
    # Round-trip through the persisted public schema, as a remote worker would.
    pipeline = PipelineSpec.model_validate_json(pipeline.model_dump_json())
    runtime = Orchestrator(store=RunStore(tmp_path / 'runs'))
    run = await runtime.submit(pipeline)
    done = await runtime.wait(run.id, timeout=10)
    assert done.status.value == 'completed'
    members = pipeline.fanouts['worker']
    assert sum(done.nodes[node].status.value == 'completed' for node in members) == count
    assert done.nodes['join'].output == 'joined'
    for node in members[count:]:
        assert done.nodes[node].current_attempt == 0
        assert not (runtime.store.base_dir / run.id / 'artifacts' / node).exists()
    events = runtime.store.get_events(run.id)
    assert len([event for event in events if event.type == 'node_activation']) == 3


@pytest.mark.parametrize('decision', ['not json', '{"count": "2"}', '{"count": -1}', '{"count": 4}', '{}'])
async def test_invalid_gate_never_launches_candidate(tmp_path, decision):
    pipeline = load_pipeline_from_data({'name': 'invalid', 'working_dir': str(tmp_path), 'use_worktree': False, 'nodes': [
        {'id': 'gate', 'agent': 'python', 'prompt': f'print({decision!r})'},
        {'id': 'worker', 'agent': 'python', 'prompt': 'raise AssertionError("must not run")',
         'fanout': {'count': 3}, 'activation': {'source': 'gate', 'path': ['count']}},
    ]})
    runtime = Orchestrator(store=RunStore(tmp_path / 'runs'))
    run = await runtime.submit(pipeline)
    done = await runtime.wait(run.id, timeout=10)
    assert done.status.value == 'failed'
    assert all(done.nodes[node].current_attempt == 0 for node in pipeline.fanouts['worker'])
    assert all(done.nodes[node].error_kind == 'activation_invalid' for node in pipeline.fanouts['worker'])


def test_gate_rejects_unknown_sources_and_cycles():
    for source in ['missing', 'worker']:
        with pytest.raises(ValueError):
            PipelineSpec.model_validate({'name': 'bad', 'nodes': [{'id': 'worker', 'agent': 'python',
                'prompt': 'print(1)', 'activation': {'source': source}}]})


async def test_non_agent_python_callable_and_external_program(tmp_path):
    (tmp_path / 'custom_steps.py').write_text('def choose(count):\n    print("checking proposal")\n    return {"count": count}\n')
    with Graph('custom', working_dir=str(tmp_path), use_worktree=False) as graph:
        python_call(task_id='gate', function='custom_steps:choose', arguments={'count': 1})
        command(task_id='external', argv=[sys.executable, '-c', 'import sys; print(sys.argv[1])', '; $(not-a-command)'],
                activation={'source': 'gate', 'path': ['count']})
    runtime = Orchestrator(store=RunStore(tmp_path / 'runs'))
    run = await runtime.submit(load_pipeline_from_data(graph.to_payload()))
    done = await runtime.wait(run.id, timeout=10)
    assert done.status.value == 'completed'
    assert json.loads(done.nodes['gate'].output) == {'count': 1}
    assert 'checking proposal' in runtime.store.read_artifact_text(run.id, 'gate', 'stderr.log')
    assert done.nodes['external'].output == '; $(not-a-command)'


async def test_hybrid_example_with_mock_model_output(tmp_path):
    from pathlib import Path
    from agentflow.loader import load_pipeline_from_path
    pipeline = load_pipeline_from_path(Path(__file__).resolve().parents[1] / 'examples/hybrid_orchestration.py')
    pipeline.working_dir = str(tmp_path)
    proposal = next(node for node in pipeline.nodes if node.id == 'propose')
    proposal.agent = 'python'
    proposal.prompt = '''print('{"count": 2}')'''
    runtime = Orchestrator(store=RunStore(tmp_path / 'runs'))
    run = await runtime.submit(pipeline)
    done = await runtime.wait(run.id, timeout=10)
    assert done.status.value == 'completed'
    assert sum(done.nodes[node].status.value == 'completed' for node in pipeline.fanouts['worker']) == 2
