"""Lite container demo: agent tool calls executed inside a Docker sandbox.

Runs a code-analysis task where every shell command the agent issues is
executed in an ephemeral Docker container instead of on the host.

Prerequisites:
    - Docker Desktop (or any docker CLI on PATH) running locally
    - export OPENAI_API_KEY=sk-...   (or point LITE_BASE_URL at a local endpoint)

Safety defaults (OpenAnt-style ephemeral audit containers):
    - no network access (``--network none``)
    - workspace mounted read-only
    - 512MB memory / 1 CPU limit, 120s per-command timeout
    - container removed after each command (``--rm``)
"""

from __future__ import annotations

import os
from pathlib import Path

from agentflow.lite import (
    ContainerConfig,
    DockerExecutor,
    LiteAgent,
    LiteLLMClient,
    container_shell_tool,
)

ROOT = Path(__file__).resolve().parent.parent


def main() -> None:
    executor = DockerExecutor(
        ContainerConfig(
            image="python:3.12-slim",
            workspace=str(ROOT),
            # Safe defaults kept explicit for readability: no network,
            # read-only mount, bounded resources, per-command timeout.
            network="none",
            read_only=True,
            memory="512m",
            cpus=1.0,
            timeout=120,
        )
    )
    if not executor.available():
        raise SystemExit("docker CLI not found on PATH; install Docker Desktop first.")

    client = LiteLLMClient(
        base_url=os.environ.get("LITE_BASE_URL", "https://api.openai.com/v1"),
        api_key=os.environ.get("OPENAI_API_KEY"),
        api_key_env="LITE_API_KEY",
    )
    agent = LiteAgent(
        client=client,
        model=os.environ.get("LITE_MODEL", "gpt-4o-mini"),
        system_prompt=(
            "You are a security auditor. The repository is mounted read-only at "
            "/workspace inside the sandbox. Use run_command to inspect code; "
            "you have no network access and cannot modify files."
        ),
        tools=[container_shell_tool(executor)],
        max_iterations=10,
    )
    result = agent.run(
        "List the Python files under /workspace/agentflow/lite, then read "
        "container.py and summarize how commands are sandboxed."
    )
    print(result.text)
    print(f"\n(iterations={result.iterations}, total_tokens={result.usage.total_tokens})")


if __name__ == "__main__":
    main()
