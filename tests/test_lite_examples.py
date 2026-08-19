from __future__ import annotations

import pytest

from agentflow.lite import (
    GraphRunner,
    ToolCall,
    load_graph,
    make_agent_factory,
)
from examples import lite_pipeline_demo, lite_repository_skill
from examples.lite_repository_skill import (
    _repository_path,
    read_repository_file,
    search_python,
)


def test_repository_example_tools_are_bounded_and_contained(monkeypatch):
    with pytest.raises(ValueError, match="escapes the repository"):
        _repository_path("../outside-agentflow")

    project = read_repository_file.handler(path="pyproject.toml")
    matches = search_python.handler(
        pattern="class GraphRunner",
        path="agentflow/lite",
    )

    assert 'name = "agentflow"' in project
    assert "agentflow/lite/runner.py" in matches

    with pytest.raises(ValueError, match="pattern must not be empty"):
        search_python.handler(pattern="")

    monkeypatch.setattr(lite_repository_skill, "MAX_FILE_BYTES", 16)
    with pytest.raises(ValueError, match="file is larger"):
        read_repository_file.handler(path="pyproject.toml")


def test_pipeline_example_declares_triggers_resources_and_skills():
    graph = load_graph(lite_pipeline_demo.DEFAULT_GRAPH)
    nodes = {node.id: node for node in graph.nodes}

    assert {
        name: settings.max_concurrency
        for name, settings in graph.resource_settings.items()
    } == {
        "model-endpoint": 2,
        "repository": 4,
        "guidance-catalog": 2,
        "report-output": 1,
    }
    assert nodes["scan"].trigger_mode == "output_idle"
    assert nodes["scan"].skills == ["repository-read"]
    assert nodes["analyze"].trigger_mode == "input_and_output"
    assert nodes["analyze"].skills == ["security-guidance"]
    assert nodes["report"].trigger_mode == "input_ready"
    assert nodes["report"].skills == ["report-publishing"]
    assert [(item.name, item.access) for item in nodes["report"].resources] == [
        ("model-endpoint", "read"),
        ("report-output", "write"),
    ]


def test_example_skill_registry_dispatches_local_mcp_and_atomic_write_tools():
    reports: list[str] = []
    registry = lite_pipeline_demo.build_skill_registry(reports)
    tools = registry.tool_registry()

    local = tools.dispatch(
        ToolCall(
            id="local",
            name="read_repository_file",
            arguments={"path": "pyproject.toml"},
        )
    )
    remote = tools.dispatch(
        ToolCall(
            id="mcp",
            name="security-guidance__lookup_control",
            arguments={"risk": "process launch"},
        )
    )
    receipt = tools.dispatch(
        ToolCall(
            id="write",
            name="publish_report",
            arguments={"summary": "bounded demo"},
        )
    )

    assert 'name = "agentflow"' in local
    assert '"risk": "process launch"' in remote
    assert receipt == "published report 1 (12 characters)"
    assert reports == ["bounded demo"]


def test_offline_pipeline_example_runs_without_network_and_preserves_node_inputs():
    graph = load_graph(lite_pipeline_demo.DEFAULT_GRAPH)
    reports: list[str] = []
    skills = lite_pipeline_demo.build_skill_registry(reports)
    client = lite_pipeline_demo.build_offline_client()
    try:
        runner = GraphRunner(
            graph,
            make_agent_factory(
                client=client,
                default_model="offline-demo",
                skills=skills,
            ),
            max_workers=4,
        )
        nodes = runner.run().snapshot()["nodes"]
    finally:
        client.close()

    assert all(node.status == "finished" for node in nodes.values())
    assert nodes["scan"].input is not None
    assert nodes["scan"].input.upstream == {}
    assert nodes["analyze"].input is not None
    assert set(nodes["analyze"].input.upstream) == {"scan"}
    assert nodes["report"].input is not None
    assert set(nodes["report"].input.upstream) == {"analyze"}
    assert reports == ["Demo audit: review process launches; no confirmed exploit."]


def test_live_pipeline_example_rejects_accidental_default_endpoint(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("LITE_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_BASE_URL", raising=False)

    with pytest.raises(SystemExit, match="OPENAI_API_KEY is required"):
        lite_pipeline_demo.build_live_client()
