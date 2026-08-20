# AgentFlow container images

Ready-made images for running AgentFlow nodes inside containers. All images build on
`dockers/base.Dockerfile` (`debian:bookworm-slim` plus `curl`, `git`, `wget`, and
Python 3, with `python` symlinked to the same interpreter as `python3`).

## Build

```bash
docker build -f dockers/base.Dockerfile -t agentflow-base:bookworm-slim dockers
docker build -f dockers/codex.Dockerfile -t agentflow-codex:bookworm-slim dockers
docker build -f dockers/claude.Dockerfile -t agentflow-claude:bookworm-slim dockers
docker build -f dockers/pi.Dockerfile -t agentflow-pi:bookworm-slim dockers
docker build -f dockers/kimi.Dockerfile -t agentflow-kimi:bookworm-slim dockers
docker build -f dockers/opencode.Dockerfile -t agentflow-opencode:bookworm-slim dockers
docker build -f dockers/kilo.Dockerfile -t agentflow-kilo:bookworm-slim dockers
docker build -f dockers/goose.Dockerfile -t agentflow-goose:bookworm-slim dockers
docker build -f dockers/deepseek.Dockerfile -t agentflow-deepseek:bookworm-slim dockers
docker build -f dockers/zcode.Dockerfile -t agentflow-zcode:bookworm-slim dockers
docker build -f dockers/python.Dockerfile -t agentflow-python:bookworm-slim dockers
docker build -f dockers/shell.Dockerfile -t agentflow-shell:bookworm-slim dockers
docker build -f dockers/sync.Dockerfile -t agentflow-sync:bookworm-slim dockers
```

The Node-based images (`codex`, `claude`, `pi`, `opencode`, `kilo`, `deepseek`, `zcode`) install Node.js 22
from NodeSource (the bookworm distro package is too old), keeping the images
slim. The `kimi` image installs `kimi-cli` with `uv tool install`, which reuses
the base image's Python 3.12 in an isolated venv.

## Images

| Image | Agent | Contents |
| --- | --- | --- |
| `agentflow-base` | — | curl, git, wget, Python 3 (+ `python`), pip, venv |
| `agentflow-codex` | codex | base + Node.js + `@openai/codex` |
| `agentflow-claude` | claude | base + Node.js + `@anthropic-ai/claude-code` |
| `agentflow-pi` | pi | base + Node.js + `@earendil-works/pi-coding-agent` |
| `agentflow-kimi` | kimi | base + `kimi-cli` (uv tool install) |
| `agentflow-opencode` | opencode | base + Node.js + `opencode-ai` |
| `agentflow-kilo` | kilo | base + Node.js + `@kilocode/cli` 7.4.22 |
| `agentflow-goose` | goose | base + `goose` v1.45.0 binary |
| `agentflow-deepseek` | deepseek | base + Node.js + DeepSeek Harness + `ddgr` |
| `agentflow-zcode` | zcode | base + Node.js + ZCode CLI |
| `agentflow-python` | python | base (thin) |
| `agentflow-shell` | shell | base (thin) |
| `agentflow-sync` | sync | base + openssh-client, rsync, tar |

## Use in a pipeline

```python
from agentflow import DAG, codex

with DAG(
    "demo",
    container_target_defaults={"image": "agentflow-codex:bookworm-slim"},
) as dag:
    plan = codex(task_id="plan", prompt="Inspect the repo and plan.")

spec = dag.to_spec()
```

Per-node overrides merge on top of the graph defaults, e.g.
`target={"kind": "container", "image": "agentflow-claude:bookworm-slim"}`.
See `docs/pipelines.md` for the full `container` target reference.
