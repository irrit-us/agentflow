from __future__ import annotations

import json

import pytest

from agentflow.lite import (
    AgentResult,
    ExternalResourceSettings,
    GraphSpec,
    NestedConcurrencySpec,
    ResourceRequest,
    Usage,
    fanout_items,
    load_graph,
    render_fanout_prompt,
    resolve_prompt,
)
from agentflow.lite.graph import EdgeSpec, FanOutSpec, NodeSpec


def _result(text: str) -> AgentResult:
    return AgentResult(text=text, messages=[], usage=Usage(), iterations=1)


def test_load_graph_from_yaml(tmp_path):
    path = tmp_path / "g.yaml"
    path.write_text(
        """
name: demo
nodes:
  - id: scan
    prompt: scan it
  - id: report
    prompt: "summarize {{ nodes.scan.text }}"
edges:
  - from: scan
    to: report
""",
        encoding="utf-8",
    )

    graph = load_graph(path)

    assert graph.name == "demo"
    assert [n.id for n in graph.nodes] == ["scan", "report"]
    assert graph.all_edges() == [("scan", "report")]


def test_load_graph_from_json(tmp_path):
    path = tmp_path / "g.json"
    path.write_text(
        json.dumps({
            "name": "jsongraph",
            "nodes": [{"id": "a", "prompt": "pa"}, {"id": "b", "prompt": "pb", "depends_on": ["a"]}],
        }),
        encoding="utf-8",
    )

    graph = load_graph(path)

    assert graph.name == "jsongraph"
    assert graph.dependencies("b") == ["a"]


def test_load_graph_rejects_unknown_extension(tmp_path):
    path = tmp_path / "g.txt"
    path.write_text("name: x", encoding="utf-8")

    with pytest.raises(ValueError, match="extension"):
        load_graph(path)


def test_all_edges_merges_edges_and_depends_on_deduped():
    graph = GraphSpec(
        nodes=[
            NodeSpec(id="a", prompt="pa"),
            NodeSpec(id="b", prompt="pb", depends_on=["a"]),
            NodeSpec(id="c", prompt="pc", depends_on=["a", "b"]),
        ],
        edges=[EdgeSpec(from_="a", to="b")],
    )

    assert graph.all_edges() == [("a", "b"), ("a", "c"), ("b", "c")]
    assert graph.dependencies("c") == ["a", "b"]
    graph.validate_graph()  # explicit validation passes


def test_validate_rejects_duplicate_ids():
    graph = GraphSpec(nodes=[NodeSpec(id="a", prompt="1"), NodeSpec(id="a", prompt="2")])

    with pytest.raises(ValueError, match="duplicate node ids: a"):
        graph.validate_graph()


def test_validate_rejects_unknown_references():
    graph = GraphSpec(
        nodes=[NodeSpec(id="a", prompt="1"), NodeSpec(id="b", prompt="2", depends_on=["ghost"])]
    )

    with pytest.raises(ValueError, match="unknown nodes: ghost"):
        graph.validate_graph()


def test_validate_detects_cycle():
    graph = GraphSpec(
        nodes=[NodeSpec(id="a", prompt="1"), NodeSpec(id="b", prompt="2")],
        edges=[EdgeSpec(from_="a", to="b"), EdgeSpec(from_="b", to="a")],
    )

    with pytest.raises(ValueError, match="cycle involving nodes: a, b"):
        graph.validate_graph()


def test_topo_order_respects_dependencies():
    graph = GraphSpec(
        nodes=[
            NodeSpec(id="c", prompt="3", depends_on=["b"]),
            NodeSpec(id="a", prompt="1"),
            NodeSpec(id="b", prompt="2", depends_on=["a"]),
        ]
    )

    assert graph.topo_order() == ["a", "b", "c"]


def test_resolve_prompt_substitutes_upstream_text():
    node = NodeSpec(id="b", prompt="use {{ nodes.a.text }} and {{nodes.a.text}}")

    resolved = resolve_prompt(node, {"a": _result("hello")})

    assert resolved == "use hello and hello"


def test_resolve_prompt_missing_upstream_raises():
    node = NodeSpec(id="b", prompt="use {{ nodes.a.text }}")

    with pytest.raises(ValueError, match="'a'"):
        resolve_prompt(node, {})


def test_load_graph_validates_automatically(tmp_path):
    path = tmp_path / "bad.yaml"
    path.write_text(
        "nodes:\n  - id: a\n    prompt: x\n  - id: b\n    prompt: y\n    depends_on: [nope]\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="unknown nodes: nope"):
        load_graph(path)


def test_load_graph_with_node_container_config(tmp_path):
    path = tmp_path / "container.yaml"
    path.write_text(
        """
nodes:
  - id: scan
    prompt: scan it
    container:
      image: semgrep/semgrep:latest
      network: none
      memory: 1g
      mounts:
        - type: bind
          source: /host/kb
          target: /kb
          read_only: true
""",
        encoding="utf-8",
    )

    graph = load_graph(path)
    container = graph.nodes[0].container

    assert container is not None
    assert container.image == "semgrep/semgrep:latest"
    assert container.network == "none"
    assert container.memory == "1g"
    assert len(container.mounts) == 1
    mount = container.mounts[0]
    assert mount.type == "bind"
    assert mount.source == "/host/kb"
    assert mount.target == "/kb"
    assert mount.read_only is True
    assert graph.nodes[0].model is None  # container block does not affect other fields


def test_load_graph_tmpfs_mount_warns(tmp_path):
    path = tmp_path / "tmpfs.yaml"
    path.write_text(
        """
nodes:
  - id: a
    prompt: x
    container:
      image: python:3.12-slim
      mounts:
        - type: tmpfs
          target: /scratch
          tmpfs_size: 64m
""",
        encoding="utf-8",
    )

    with pytest.warns(UserWarning, match="tmpfs"):
        graph = load_graph(path)

    mount = graph.nodes[0].container.mounts[0]
    assert mount.type == "tmpfs"
    assert mount.tmpfs_size == "64m"


def test_node_without_container_defaults_to_none():
    node = NodeSpec(id="a", prompt="x")

    assert node.container is None


def test_nested_concurrency_policy_loads_and_rejects_invalid_limits(tmp_path):
    path = tmp_path / "nested.yaml"
    path.write_text(
        """
nodes:
  - id: train
    prompt: train
    nested_concurrency:
      max_concurrent_requests: 20
      pools:
        merge: 20
        retro_link: 4
""",
        encoding="utf-8",
    )

    policy = load_graph(path).nodes[0].nested_concurrency
    assert policy == NestedConcurrencySpec(
        max_concurrent_requests=20,
        pools={"merge": 20, "retro_link": 4},
    )
    assert policy.pool_limit("missing", 3) == 3

    with pytest.raises(ValueError):
        NestedConcurrencySpec(max_concurrent_requests=0)
    with pytest.raises(ValueError):
        NestedConcurrencySpec(max_concurrent_requests=1, pools={"retro_link": 0})


def test_fanout_adds_implicit_dependency_and_extracts_nested_items():
    graph = GraphSpec(
        nodes=[
            NodeSpec(id="plan", prompt="return links"),
            NodeSpec(
                id="audit",
                prompt="audit {{ link.id }}: {{ link }}",
                fanout=FanOutSpec(from_="plan", items_path="plan.links", item_var="link"),
            ),
        ]
    )
    graph.validate_graph()

    assert graph.dependencies("audit") == ["plan"]
    items = fanout_items(
        graph.nodes[1],
        {"plan": _result('{"plan":{"links":[{"id":"L-1","kind":"reentry"}]}}')},
    )
    assert items == [{"id": "L-1", "kind": "reentry"}]
    assert render_fanout_prompt(graph.nodes[1], items[0]) == (
        'audit L-1: {"id":"L-1","kind":"reentry"}'
    )


def test_fanout_rejects_invalid_source_shape_and_limit():
    node = NodeSpec(
        id="audit",
        prompt="{{ item }}",
        fanout=FanOutSpec(from_="plan", max_items=1),
    )

    with pytest.raises(ValueError, match="valid JSON"):
        fanout_items(node, {"plan": _result("not-json")})
    with pytest.raises(ValueError, match="must resolve to a JSON list"):
        fanout_items(node, {"plan": _result('{"links": []}')})
    with pytest.raises(ValueError, match="above max_items=1"):
        fanout_items(node, {"plan": _result("[1, 2]")})


def test_fanout_accepts_one_json_markdown_fence():
    node = NodeSpec(
        id="audit",
        prompt="{{ item }}",
        fanout=FanOutSpec(from_="plan", items_path="tasks"),
    )
    result = _result('Here is the result:\n```json\n{"tasks":[{"id":"A"}]}\n```')

    assert fanout_items(node, {"plan": result}) == [{"id": "A"}]


def test_fanout_rejects_ambiguous_multiple_fences():
    node = NodeSpec(
        id="audit",
        prompt="{{ item }}",
        fanout=FanOutSpec(from_="plan", items_path="tasks"),
    )
    result = _result('```json\n{"tasks":[]}\n```\n```json\n{"tasks":[]}\n```')

    with pytest.raises(ValueError, match="valid JSON"):
        fanout_items(node, {"plan": result})


def test_validate_rejects_invalid_fanout_item_variable():
    graph = GraphSpec(
        nodes=[
            NodeSpec(id="plan", prompt="plan"),
            NodeSpec(
                id="audit",
                prompt="audit",
                fanout=FanOutSpec(from_="plan", item_var="not valid"),
            ),
        ]
    )

    with pytest.raises(ValueError, match="item_var is invalid"):
        graph.validate_graph()


def test_graph_loads_resource_settings_requests_skills_and_trigger_mode(tmp_path):
    path = tmp_path / "resources.yaml"
    path.write_text(
        """
resource_settings:
  knowledge-base:
    max_concurrency: 3
nodes:
  - id: refresh
    prompt: refresh
    skills: [index-maintenance]
    trigger_mode: input_and_output
    resources:
      - name: knowledge-base
        access: write
""",
        encoding="utf-8",
    )

    graph = load_graph(path)
    node = graph.nodes[0]

    assert graph.resource_settings == {
        "knowledge-base": ExternalResourceSettings(max_concurrency=3)
    }
    assert node.resources == [ResourceRequest(name="knowledge-base", access="write")]
    assert node.skills == ["index-maintenance"]
    assert node.trigger_mode == "input_and_output"


def test_resource_requests_reject_duplicates_and_conflicting_access():
    with pytest.raises(ValueError, match="resource name must not be blank"):
        NodeSpec(id="blank", prompt="x", resource="  ")

    with pytest.raises(ValueError, match="requested more than once"):
        NodeSpec(
            id="duplicate",
            prompt="x",
            resources=[{"name": "db"}, {"name": "db"}],
        )

    with pytest.raises(ValueError, match="conflicting access modes"):
        NodeSpec(
            id="conflict",
            prompt="x",
            resources=[
                {"name": "db", "access": "read"},
                {"name": "db", "access": "write"},
            ],
        )


def test_output_idle_trigger_rejects_input_dependencies():
    graph = GraphSpec(
        nodes=[
            NodeSpec(id="source", prompt="source"),
            NodeSpec(
                id="invalid",
                prompt="invalid",
                depends_on=["source"],
                trigger_mode="output_idle",
            ),
        ]
    )

    with pytest.raises(ValueError, match="cannot declare input dependencies: invalid"):
        graph.validate_graph()
