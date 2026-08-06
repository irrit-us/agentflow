from __future__ import annotations

import json

import pytest

from agentflow.lite import AgentResult, GraphSpec, Usage, load_graph, resolve_prompt
from agentflow.lite.graph import EdgeSpec, NodeSpec


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
