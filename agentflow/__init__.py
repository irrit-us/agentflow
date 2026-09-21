"""AgentFlow public package surface."""

from agentflow.actors import ActorNode, ActorRequirements, ArtifactContract, InstructionBundle
from agentflow.profiles import AgentProfile, BackendCapabilities, register_backend_capabilities

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
    python_call,
    command,
    shell,
    sync,
    zcode,
)


def create_app(*args, **kwargs):
    from agentflow.app import create_app as _create_app

    return _create_app(*args, **kwargs)


__all__ = [
    "ActorNode", "ActorRequirements", "ArtifactContract", "InstructionBundle",
    "AgentProfile", "BackendCapabilities", "register_backend_capabilities",
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
    "python_call",
    "command",
    "shell",
    "sync",
    "zcode",
    "create_app",
]
