from __future__ import annotations

import json
from pathlib import Path

import pytest

from agentflow.lite import (
    RUNTIME_CAPABILITY_ADAPTERS,
    CapabilityState,
    RunReadiness,
    load_graph,
    load_paper_architecture_manifest,
)
from examples.paper_architectures.build_all import print_capability_report

REPO_ROOT = Path(__file__).resolve().parent.parent
PAPER_DIR = REPO_ROOT / "examples" / "paper_architectures"
MANIFEST_PATH = PAPER_DIR / "manifest.json"

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


def test_paper_manifest_matches_discovered_yaml_corpus():
    manifest = load_paper_architecture_manifest(MANIFEST_PATH)

    discovered = {path.relative_to(PAPER_DIR).as_posix() for path in YAML_FILES}
    declared = {entry.graph_path for entry in manifest.entries}

    assert declared == discovered
    assert len(manifest.entries) == 47


def test_paper_manifest_does_not_overstate_gap_acceptance_graphs():
    manifest = load_paper_architecture_manifest(MANIFEST_PATH)
    entries = {entry.graph_name: entry for entry in manifest.entries}

    actor = entries["actor"]
    assert actor.readiness is RunReadiness.SPEC_ONLY
    assert actor.capabilities.human_approval is CapabilityState.PROMPT_ONLY

    qasecclaw = entries["qasecclaw"]
    assert qasecclaw.readiness is RunReadiness.SPEC_ONLY
    assert qasecclaw.capabilities.fail_policy is CapabilityState.PROMPT_ONLY

    bradmoon = entries["bradmoon-harness"]
    assert bradmoon.readiness is RunReadiness.SPEC_ONLY
    assert bradmoon.capabilities.deterministic_oracle is CapabilityState.PROMPT_ONLY
    assert bradmoon.capabilities.fail_policy is CapabilityState.PROMPT_ONLY
    assert bradmoon.capabilities.human_approval is CapabilityState.PROMPT_ONLY


def test_placeholder_images_are_never_marked_runnable():
    manifest = load_paper_architecture_manifest(MANIFEST_PATH)
    placeholder_entries = [
        entry
        for entry in manifest.entries
        if any(image.startswith("agentflow-tools/") for image in entry.requirements.images)
    ]

    assert placeholder_entries
    for entry in placeholder_entries:
        assert entry.readiness is not RunReadiness.RUNNABLE
        assert any("placeholder" in reason for reason in entry.non_runnable_reasons)


def test_manifest_rejects_enforced_capability_without_runtime_adapter(tmp_path: Path):
    data = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    actor = next(entry for entry in data["entries"] if entry["graph_name"] == "actor")
    actor["capabilities"]["human_approval"] = "enforced"
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(data), encoding="utf-8")

    assert "human_approval" not in RUNTIME_CAPABILITY_ADAPTERS
    with pytest.raises(ValueError, match="human_approval"):
        load_paper_architecture_manifest(path, validate_graphs=False)


def test_capability_report_includes_matrix_legend_and_exact_reasons(capsys):
    manifest = load_paper_architecture_manifest(MANIFEST_PATH)

    print_capability_report(manifest)

    output = capsys.readouterr().out
    assert "architecture fidelity manifest" in output
    assert "E=enforced, P=prompt-only, -=not required" in output
    assert "02-smart-contract/actor.yaml" in output
    assert "no external approval provider" in output
