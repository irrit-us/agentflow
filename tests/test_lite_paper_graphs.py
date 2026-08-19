from __future__ import annotations

import json
from pathlib import Path

import pytest

from agentflow.lite import (
    RUNTIME_CAPABILITY_ADAPTERS,
    CapabilityState,
    PaperArchitectureManifest,
    RunReadiness,
    load_graph,
    load_paper_architecture_manifest,
)
from examples.paper_architectures import build_all
from examples.paper_architectures.build_all import print_capability_report

REPO_ROOT = Path(__file__).resolve().parent.parent
PAPER_DIR = REPO_ROOT / "examples" / "paper_architectures"
MANIFEST_PATH = PAPER_DIR / "manifest.json"

YAML_FILES = sorted(PAPER_DIR.rglob("*.yaml")) if PAPER_DIR.is_dir() else []


def _base_entry() -> dict:
    return {
        "source": "test fixture",
        "domain": "test",
        "graph_name": "fixture",
        "graph_path": "fixture.yaml",
        "fidelity": "structural",
        "readiness": "runnable",
        "requirements": {"tools": [], "images": [], "licenses": [], "devices": []},
        "capabilities": {
            "real_feedback": "not-required",
            "deterministic_oracle": "not-required",
            "human_approval": "not-required",
            "fail_policy": "not-required",
            "stateful_target": "not-required",
            "evidence_contract": "not-required",
            "sandbox": "not-required",
        },
        "non_runnable_reasons": [],
    }


def _write_manifest(tmp_path: Path, entries: list[dict]) -> Path:
    path = tmp_path / "manifest.json"
    path.write_text(
        json.dumps({"schema_version": 1, "entries": entries}),
        encoding="utf-8",
    )
    return path


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
    assert audit.fanout.max_items == 16
    assert audit.trigger_mode == "input_and_output"
    assert audit.skills == ["repository-read", "security-guidance"]
    assert [(item.name, item.access) for item in audit.resources] == [
        ("model-endpoint", "read"),
        ("guidance-catalog", "read"),
        ("repository", "read"),
    ]
    assert graph.resource_settings["repository"].max_concurrency == 4


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


@pytest.mark.parametrize(
    ("case", "message"),
    [
        ("path_escape", "graph_path"),
        ("runnable_reason", "runnable entries"),
        ("missing_reason", "must state exact reasons"),
        ("structural_enforced", "structural fidelity"),
        ("enforced_prompt", "enforced fidelity"),
        ("partial_without_adapter", "partial fidelity"),
        ("duplicate_requirement", "requirement values must be unique"),
        ("duplicate_reason", "non-runnable reasons must be unique"),
    ],
)
def test_manifest_entry_invariants_reject_contradictory_claims(
    tmp_path: Path,
    case: str,
    message: str,
):
    entry = _base_entry()
    if case == "path_escape":
        entry["graph_path"] = "../fixture.yaml"
    elif case == "runnable_reason":
        entry["non_runnable_reasons"] = ["unexpected reason"]
    elif case == "missing_reason":
        entry["readiness"] = "spec-only"
    elif case == "structural_enforced":
        entry["capabilities"]["sandbox"] = "enforced"
    elif case == "enforced_prompt":
        entry["fidelity"] = "enforced"
        entry["capabilities"]["human_approval"] = "prompt-only"
    elif case == "partial_without_adapter":
        entry["fidelity"] = "partial"
    elif case == "duplicate_requirement":
        entry["requirements"]["tools"] = ["docker", "docker"]
    elif case == "duplicate_reason":
        entry["readiness"] = "spec-only"
        entry["non_runnable_reasons"] = ["not ready", "not ready"]

    with pytest.raises(ValueError, match=message):
        load_paper_architecture_manifest(
            _write_manifest(tmp_path, [entry]), validate_graphs=False
        )


@pytest.mark.parametrize("duplicate_field", ["graph_name", "graph_path"])
def test_manifest_rejects_duplicate_graph_identity(
    tmp_path: Path,
    duplicate_field: str,
):
    first = _base_entry()
    second = _base_entry()
    second["graph_name"] = "second"
    second["graph_path"] = "second.yaml"
    second[duplicate_field] = first[duplicate_field]

    with pytest.raises(ValueError, match="duplicate graph"):
        load_paper_architecture_manifest(
            _write_manifest(tmp_path, [first, second]), validate_graphs=False
        )


@pytest.mark.parametrize(
    ("case", "message"),
    [
        ("missing_file", "does not exist"),
        ("name_drift", "declares graph name"),
        ("image_drift", "image requirements"),
        ("container_without_sandbox", "does not mark sandbox"),
        ("container_without_docker", "does not require docker"),
        ("sandbox_without_container", "without a container node"),
    ],
)
def test_manifest_cross_checks_graph_runtime_requirements(
    tmp_path: Path,
    case: str,
    message: str,
):
    entry = _base_entry()
    entry.update(
        {
            "fidelity": "partial",
            "readiness": "spec-only",
            "requirements": {
                "tools": ["docker"],
                "images": ["python:3.12-slim"],
                "licenses": [],
                "devices": [],
            },
            "non_runnable_reasons": ["fixture only"],
        }
    )
    entry["capabilities"]["sandbox"] = "enforced"

    graph_text = """name: fixture
nodes:
  - id: work
    prompt: work
    container:
      image: python:3.12-slim
"""
    if case == "name_drift":
        entry["graph_name"] = "different"
    elif case == "image_drift":
        entry["requirements"]["images"] = ["python:wrong"]
    elif case == "container_without_sandbox":
        entry["fidelity"] = "structural"
        entry["capabilities"]["sandbox"] = "prompt-only"
    elif case == "container_without_docker":
        entry["requirements"]["tools"] = []
    elif case == "sandbox_without_container":
        entry["requirements"]["images"] = []
        graph_text = """name: fixture
nodes:
  - id: work
    prompt: work
"""

    if case != "missing_file":
        (tmp_path / "fixture.yaml").write_text(graph_text, encoding="utf-8")

    with pytest.raises(ValueError, match=message):
        load_paper_architecture_manifest(_write_manifest(tmp_path, [entry]))


def test_build_all_run_executes_only_runnable_manifest_entries(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
):
    runnable_path = tmp_path / "runnable.yaml"
    skipped_path = tmp_path / "skipped.yaml"
    runnable_path.write_text(
        "name: runnable\nnodes:\n  - id: run\n    prompt: run\n    model: m\n",
        encoding="utf-8",
    )
    skipped_path.write_text(
        "name: skipped\nnodes:\n  - id: skip\n    prompt: skip\n    model: m\n",
        encoding="utf-8",
    )

    runnable = _base_entry()
    runnable.update({"graph_name": "runnable", "graph_path": "runnable.yaml"})
    skipped = _base_entry()
    skipped.update(
        {
            "graph_name": "skipped",
            "graph_path": "skipped.yaml",
            "readiness": "spec-only",
            "non_runnable_reasons": ["fixture only"],
        }
    )
    manifest = PaperArchitectureManifest.model_validate(
        {"schema_version": 1, "entries": [runnable, skipped]}
    )
    executed: list[str] = []

    monkeypatch.setattr(build_all, "HERE", tmp_path)
    monkeypatch.setattr(build_all, "MANIFEST_PATH", tmp_path / "manifest.json")
    monkeypatch.setattr(
        build_all,
        "load_paper_architecture_manifest",
        lambda path: manifest,
    )
    monkeypatch.setattr(
        build_all.GraphRunner,
        "run",
        lambda self: executed.append(self.graph.name) or self.state,
    )

    build_all.main(["--run"])

    assert executed == ["runnable"]
    output = capsys.readouterr().out
    assert "runnable: done=False" in output
    assert "skipped: skipped (spec-only)" in output


def test_capability_report_includes_matrix_legend_and_exact_reasons(capsys):
    manifest = load_paper_architecture_manifest(MANIFEST_PATH)

    print_capability_report(manifest)

    output = capsys.readouterr().out
    assert "architecture fidelity manifest" in output
    assert "E=enforced, P=prompt-only, -=not required" in output
    assert "02-smart-contract/actor.yaml" in output
    assert "no external approval provider" in output
