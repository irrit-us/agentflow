from __future__ import annotations

__doc__ = """Lite agent demo: direct LLM call with a safe local skill, no orchestrator.

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

import os

from agentflow.lite import (
    LiteAgent,
    LiteLLMClient,
    SharedConcurrencyBudget,
    SkillRegistry,
    ToolSharingConfig,
)

if __package__:
    from .lite_repository_skill import (
        repository_skill,
        repository_tool_policies,
    )
else:
    from lite_repository_skill import repository_skill, repository_tool_policies


def main() -> None:
    base_url = os.environ.get("LITE_BASE_URL", "https://api.openai.com/v1")
    api_key = os.environ.get("OPENAI_API_KEY") or os.environ.get("LITE_API_KEY")
    if base_url == "https://api.openai.com/v1" and not api_key:
        raise SystemExit(
            "OPENAI_API_KEY is required for the default endpoint; set "
            "LITE_BASE_URL to use an explicitly configured local endpoint."
        )
    skills = SkillRegistry(
        [repository_skill()],
        tool_sharing=ToolSharingConfig(policies=repository_tool_policies()),
    )
    client = LiteLLMClient(
        base_url=base_url,
        api_key=api_key,
        timeout=30,
        max_retries=1,
        request_budget=SharedConcurrencyBudget(capacity=2),
    )
    try:
        agent = LiteAgent(
            client=client,
            model=os.environ.get("LITE_MODEL", "gpt-4o-mini"),
            system_prompt="You are a concise code assistant for this repository.",
            skills=skills,
            max_iterations=6,
            max_total_tokens=6000,
        )
        result = agent.run(
            "Where is the lite pipeline DAG executed? Name the module and key class."
        )
        print(result.text)
        print(
            f"\n(iterations={result.iterations}, "
            f"total_tokens={result.usage.total_tokens})"
        )
    finally:
        client.close()


if __name__ == "__main__":
    main()
