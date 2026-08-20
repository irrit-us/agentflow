# AgentFlow

AgentFlow orchestrates external coding agents in dependency graphs with parallel
fan-out, iterative workflows, and local or remote execution. This fork also
includes `agentflow.lite`, a standalone OpenAI-compatible agent stack, and a
catalog of 47 security-agent paper architectures.

This repository is derived from
[`berabuddies/agentflow`](https://github.com/berabuddies/agentflow). The
upstream README at commit
[`c1ca005`](https://github.com/berabuddies/agentflow/commit/c1ca0057ef00975beb899aad19864e9ef83f5a83)
is preserved in [`docs/readme.old.md`](docs/readme.old.md).

## Components

- **AgentFlow core** — DAG orchestration for Codex, Claude, Kimi, Kilo Code,
  OpenCode, DeepSeek Harness, ZCode, and other external coding-agent CLIs.
- **AgentFlow Lite** — direct OpenAI-compatible HTTP, tool calling, YAML DAGs,
  optional Docker execution, and a read-only monitor.
- **Paper architectures** — buildable graph declarations for 47 published
  security-agent architectures.

## Install

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -e .[dev]
```

## Quick start

```python
from agentflow import Graph, claude, codex

with Graph("review", concurrency=2) as graph:
    plan = codex(task_id="plan", prompt="Plan the change.", tools="read_only")
    implement = claude(
        task_id="implement",
        prompt="Implement this plan:\n{{ nodes.plan.output }}",
        tools="read_write",
    )
    plan >> implement

print(graph.to_json())
```

```bash
agentflow run pipeline.py --output summary
```

## Structured Docker execution

Core AgentFlow nodes can run in the bundled Docker target as well as through
the older per-agent container images. Build the bundled runtime once:

```bash
docker build -t agentflow-agents:latest .
```

`target={"kind": "docker"}` starts audit-safe by default: no network, a
read-only workspace and explicit mounts, 512 MiB of memory, 1 CPU, a
120-second node timeout, no inherited host credentials, and no Docker-daemon
access. The image includes Codex, Claude, Kimi, Kilo Code, Pi, and Docker
tooling. See the [Docker target reference](docs/pipelines.md#docker) for
deliberate opt-ins such as provider network access, writable mounts, host
daemon mounting, and Docker-in-Docker.

## Documentation

- [Documentation index](docs/README.md)
- [CLI and operations](docs/cli.md)
- [Pipeline reference](docs/pipelines.md)
- [Lite agent stack](docs/lite.md)
- [Examples guide](docs/examples.md)
- [Paper architecture catalog](examples/paper_architectures/README.md)
- [Testing and maintainer workflows](docs/testing.md)
- [Research architecture gap roadmap](docs/research-gap-roadmap.md)
- [Background and sources](docs/background.md)
- [Archived upstream README](docs/readme.old.md)
