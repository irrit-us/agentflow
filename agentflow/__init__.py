"""AgentFlow public package surface."""

from agentflow.dsl import (
    DAG,
    Graph,
    InferenceSetup,
    agent,
    claude,
    codex,
    deepseek,
    evolve,
    fanout,
    goose,
    kilo,
    kimi,
    merge,
    opencode,
    pi,
    python_node,
    shell,
    sync,
    zcode,
)


def create_app(*args, **kwargs):
    from agentflow.app import create_app as _create_app

    return _create_app(*args, **kwargs)


__all__ = [
    "DAG",
    "Graph",
    "InferenceSetup",
    "agent",
    "claude",
    "codex",
    "deepseek",
    "evolve",
    "fanout",
    "goose",
    "kilo",
    "kimi",
    "merge",
    "opencode",
    "pi",
    "python_node",
    "shell",
    "sync",
    "zcode",
    "create_app",
]
