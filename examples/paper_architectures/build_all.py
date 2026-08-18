"""Batch-validate/build the example graphs under paper_architectures.

Builds graphs without running them: by default this recursively loads every
.yaml in this directory, validates graph structure, and prints each graph's
name, topological order, and node-to-image mapping. It also constructs a
GraphRunner per graph (proving make_agent_factory can consume them) but does
NOT call run().

Execution requires --run (and a real LLM endpoint plus Docker), and is limited
to manifest entries marked runnable:
    LITE_BASE_URL=http://localhost:8000/v1 python build_all.py --run
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

# Allow running as a plain script: the venv's editable install may point at a
# stale checkout, so put this repo's root on sys.path explicitly.
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from agentflow.lite import (  # noqa: E402
    CAPABILITY_NAMES,
    CapabilityState,
    GraphRunner,
    LiteLLMClient,
    PaperArchitectureManifest,
    RunReadiness,
    ToolRegistry,
    load_graph,
    load_paper_architecture_manifest,
    make_agent_factory,
)

HERE = Path(__file__).resolve().parent
MANIFEST_PATH = HERE / "manifest.json"

CAPABILITY_LABELS = {
    "real_feedback": "feedback",
    "deterministic_oracle": "oracle",
    "human_approval": "approval",
    "fail_policy": "fail-policy",
    "stateful_target": "stateful",
    "evidence_contract": "evidence",
    "sandbox": "sandbox",
}


def discover_graph_paths() -> list[Path]:
    return sorted(HERE.rglob("*.yaml"))


def validate_manifest_corpus(
    manifest: PaperArchitectureManifest,
    yaml_files: list[Path],
) -> None:
    discovered = {path.relative_to(HERE).as_posix() for path in yaml_files}
    declared = {entry.graph_path for entry in manifest.entries}
    missing = sorted(discovered - declared)
    stale = sorted(declared - discovered)
    if missing or stale:
        details: list[str] = []
        if missing:
            details.append("missing manifest entries: " + ", ".join(missing))
        if stale:
            details.append("manifest entries without graphs: " + ", ".join(stale))
        raise ValueError("; ".join(details))


def _capability_marker(state: CapabilityState) -> str:
    return {
        CapabilityState.NOT_REQUIRED: "-",
        CapabilityState.PROMPT_ONLY: "P",
        CapabilityState.ENFORCED: "E",
    }[state]


def print_capability_report(manifest: PaperArchitectureManifest) -> None:
    print("\n== architecture fidelity manifest ==")
    path_width = max(len("graph"), *(len(entry.graph_path) for entry in manifest.entries))
    columns = [CAPABILITY_LABELS[name] for name in CAPABILITY_NAMES]
    print(
        f"{'graph':<{path_width}}  {'readiness':<13}  {'fidelity':<10}  "
        + "  ".join(f"{label:<11}" for label in columns)
    )
    for entry in manifest.entries:
        markers = [
            _capability_marker(getattr(entry.capabilities, name))
            for name in CAPABILITY_NAMES
        ]
        print(
            f"{entry.graph_path:<{path_width}}  {entry.readiness.value:<13}  "
            f"{entry.fidelity.value:<10}  "
            + "  ".join(f"{marker:<11}" for marker in markers)
        )

    print("\ncapability legend: E=enforced, P=prompt-only, -=not required")
    print("\nnon-runnable reasons:")
    for entry in manifest.entries:
        if entry.readiness is RunReadiness.RUNNABLE:
            continue
        print(f"  {entry.graph_path} [{entry.readiness.value}]")
        for reason in entry.non_runnable_reasons:
            print(f"    - {reason}")


def main(argv: list[str] | None = None) -> None:
    args = sys.argv[1:] if argv is None else argv
    yaml_files = discover_graph_paths()
    if not yaml_files:
        print(f"no .yaml files found under {HERE}")
        return
    manifest = load_paper_architecture_manifest(MANIFEST_PATH)
    validate_manifest_corpus(manifest, yaml_files)
    manifest_by_path = {entry.graph_path: entry for entry in manifest.entries}

    client = LiteLLMClient(base_url=os.environ.get("LITE_BASE_URL", "http://localhost:8000/v1"))
    factory = make_agent_factory(client=client, registry=ToolRegistry())
    runners: list[tuple[GraphRunner, str]] = []

    for path in yaml_files:
        graph = load_graph(path)
        rel = path.relative_to(HERE)
        entry = manifest_by_path[rel.as_posix()]
        print(f"\n== {rel} ==")
        print(f"name: {graph.name}")
        print(f"fidelity: {entry.fidelity.value}")
        print(f"readiness: {entry.readiness.value}")
        print(f"topo: {' -> '.join(graph.topo_order())}")
        print("nodes:")
        for node in graph.nodes:
            image = node.container.image if node.container else "-"
            print(f"  {node.id:<20} {image}")
        runners.append((GraphRunner(graph, factory), entry.graph_path))

    print_capability_report(manifest)

    if "--run" in args:
        print("\n--run specified: executing only manifest entries marked runnable")
        for runner, graph_path in runners:
            entry = manifest_by_path[graph_path]
            if entry.readiness is not RunReadiness.RUNNABLE:
                print(f"{runner.graph.name}: skipped ({entry.readiness.value})")
                continue
            runner.run()
            print(f"{runner.graph.name}: done={runner.is_done()}")
    else:
        print(
            f"\nbuilt {len(runners)} runner(s), 0 failures; "
            "not executing (pass --run to execute runnable entries)"
        )


if __name__ == "__main__":
    main()
