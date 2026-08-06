"""Lite pipeline demo: YAML-described DAG + background execution + built-in monitor UI.

Usage:
    export OPENAI_API_KEY=sk-...
    python examples/lite_pipeline_demo.py

Then open http://127.0.0.1:8600/ for the live monitor (node status, blocked
list, click a node to inspect its full conversation). Nodes can be dragged;
the layout persists in the browser's localStorage.

Environment variables:
    OPENAI_API_KEY    API key (required unless using a local endpoint)
    OPENAI_BASE_URL   Overrides the default https://api.openai.com/v1 (can point at local vLLM/Ollama)
    LITE_MODEL        Model name, default gpt-4o-mini
    LITE_GRAPH        Path to the graph definition, default examples/lite_pipeline.yaml
"""

from __future__ import annotations

import os
from pathlib import Path

import uvicorn

from agentflow.lite import (
    GraphRunner,
    LiteLLMClient,
    create_app,
    load_graph,
    make_agent_factory,
    make_llm_health_probe,
)

ROOT = Path(__file__).resolve().parent.parent


def main() -> None:
    client = LiteLLMClient(
        base_url=os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1"),
        api_key=os.environ.get("OPENAI_API_KEY"),
        api_key_env="LITE_API_KEY",
    )
    graph = load_graph(os.environ.get("LITE_GRAPH", str(ROOT / "examples" / "lite_pipeline.yaml")))
    factory = make_agent_factory(
        client=client,
        default_model=os.environ.get("LITE_MODEL", "gpt-4o-mini"),
    )
    runner = GraphRunner(graph, factory)
    runner.run_in_background()  # the pipeline runs in a daemon thread; the HTTP server stays responsive

    app = create_app(runner, health_probe=make_llm_health_probe(client))
    print("monitor UI: http://127.0.0.1:8600/")
    uvicorn.run(app, host="127.0.0.1", port=8600, log_level="warning")


if __name__ == "__main__":
    main()
