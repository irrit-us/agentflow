from __future__ import annotations

from pathlib import Path

import pytest

from agentflow.lite import load_graph

REPO_ROOT = Path(__file__).resolve().parent.parent
PAPER_DIR = REPO_ROOT / "examples" / "paper_architectures"

YAML_FILES = sorted(PAPER_DIR.rglob("*.yaml")) if PAPER_DIR.is_dir() else []


@pytest.mark.parametrize(
    "path",
    YAML_FILES,
    ids=[str(p.relative_to(PAPER_DIR)) for p in YAML_FILES],
)
def test_paper_graph_loads_and_is_acyclic(path: Path):
    graph = load_graph(path)

    assert len(graph.nodes) > 0
    assert len(graph.topo_order()) == len(graph.nodes)


if not YAML_FILES:
    # Empty scaffold directory: skip loudly instead of silently passing zero tests.
    def test_paper_graphs_placeholder():
        pytest.skip("no paper architecture YAMLs under examples/paper_architectures yet")


def test_dynamic_audit_example_loads_with_fanout():
    path = REPO_ROOT / "examples" / "lite_dynamic_audit.yaml"

    graph = load_graph(path)

    assert graph.topo_order() == ["plan-links", "audit-link", "review-findings"]
    audit = next(node for node in graph.nodes if node.id == "audit-link")
    assert audit.fanout is not None
    assert audit.fanout.items_path == "links"
    assert audit.resource == "forge"
