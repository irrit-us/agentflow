"""Lite agent demo: direct LLM call with function calling, no orchestrator.

Runs a small code-Q&A task against any OpenAI-compatible endpoint using the
standalone ``agentflow.lite`` subpackage.

Usage:
    export OPENAI_API_KEY=sk-...
    python examples/lite_agent_demo.py

To use a local endpoint instead (vLLM, Ollama, LMStudio), point the base URL
at it -- no API key is required when the server does not enforce auth:

    export LITE_BASE_URL=http://localhost:8000/v1
    export LITE_MODEL=qwen3-8b
    python examples/lite_agent_demo.py
"""

from __future__ import annotations

import os
from pathlib import Path

from agentflow.lite import LiteAgent, LiteLLMClient, tool

ROOT = Path(__file__).resolve().parent.parent


@tool
def read_file(path: str) -> str:
    """Read a text file inside the repository and return its contents."""
    target = (ROOT / path).resolve()
    if not str(target).startswith(str(ROOT)):
        return "Error: path escapes the repository"
    return target.read_text(encoding="utf-8")


@tool
def grep_code(pattern: str, path: str = "agentflow") -> str:
    """Find lines containing ``pattern`` in Python files under ``path``."""
    base = (ROOT / path).resolve()
    if not str(base).startswith(str(ROOT)):
        return "Error: path escapes the repository"
    hits: list[str] = []
    for file in sorted(base.rglob("*.py")):
        for lineno, line in enumerate(file.read_text(encoding="utf-8").splitlines(), 1):
            if pattern in line:
                hits.append(f"{file.relative_to(ROOT)}:{lineno}: {line.strip()}")
    return "\n".join(hits[:50]) or "no matches"


def main() -> None:
    client = LiteLLMClient(
        base_url=os.environ.get("LITE_BASE_URL", "https://api.openai.com/v1"),
        api_key=os.environ.get("OPENAI_API_KEY"),
        api_key_env="LITE_API_KEY",
    )
    agent = LiteAgent(
        client=client,
        model=os.environ.get("LITE_MODEL", "gpt-4o-mini"),
        system_prompt=(
            "You are a code assistant for this repository. "
            "Use the provided tools to inspect code before answering."
        ),
        tools=[read_file, grep_code],
        max_iterations=8,
    )
    result = agent.run("Where is the pipeline DAG executed? Name the module and the key class.")
    print(result.text)
    print(f"\n(iterations={result.iterations}, total_tokens={result.usage.total_tokens})")


if __name__ == "__main__":
    main()
