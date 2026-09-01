"""Batch-validate the method-driven workflow graphs in this directory.

Loads every .yaml, validates graph structure, and prints each graph's name,
topological order, and node-to-image mapping. It also constructs a GraphRunner
per graph (proving make_agent_factory can consume them) but does NOT call
run() — these graphs are declarations, executed only with a real LLM endpoint
plus Docker:

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
    GraphRunner,
    LiteLLMClient,
    ToolRegistry,
    load_graph,
    make_agent_factory,
)

HERE = Path(__file__).resolve().parent


def main(argv: list[str] | None = None) -> None:
    args = sys.argv[1:] if argv is None else argv
    yaml_files = sorted(HERE.rglob("*.yaml"))
    if not yaml_files:
        print(f"no .yaml files found under {HERE}")
        return

    client = LiteLLMClient(base_url=os.environ.get("LITE_BASE_URL", "http://localhost:8000/v1"))
    factory = make_agent_factory(client=client, registry=ToolRegistry())
    runners: list[GraphRunner] = []

    for path in yaml_files:
        graph = load_graph(path)
        rel = path.relative_to(HERE)
        print(f"\n== {rel} ==")
        print(f"name: {graph.name}")
        print(f"topo: {' -> '.join(graph.topo_order())}")
        print("nodes:")
        for node in graph.nodes:
            image = node.container.image if node.container else "-"
            print(f"  {node.id:<28} {image}")
        runners.append(GraphRunner(graph, factory))

    if "--run" in args:
        print("\n--run specified: executing all method workflow graphs")
        for runner in runners:
            runner.run()
            print(f"{runner.graph.name}: done={runner.is_done()}")
    else:
        print(
            f"\nbuilt {len(runners)} runner(s), 0 failures; "
            "not executing (pass --run to execute)"
        )


if __name__ == "__main__":
    main()
