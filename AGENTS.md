# AGENTS.md

Guidance for AI agents and contributors working in this repository.

## Project overview

- **AgentFlow core** (upstream-inherited): DAG orchestration for external CLI coding agents
  (Codex, Claude, Kimi, etc.). Entry points: `agentflow/dsl.py`, `agentflow/orchestrator.py`,
  `agentflow/cli.py`. Docs in `docs/`.
- **`agentflow/lite/`** (authored in this fork): standalone lightweight agent stack — direct
  OpenAI-compatible LLM client, tool-calling agent loop, model router, Docker container
  execution, YAML graph runner, and monitor HTTP server + web UI. See `docs/lite.md`.
- **`examples/paper_architectures/`**: containerized graph declarations for 47 security-agent
  paper architectures. Build graphs, do not run them by default.

## Commands

```bash
# Python / pytest (Windows venv)
.venv/Scripts/python -m pytest tests/test_lite_*.py -q   # lite suite (must stay green)
.venv/Scripts/python examples/paper_architectures/build_all.py  # build all 47 graphs (no run)

# If `import agentflow` fails outside the repo root, the editable install points at a stale
# path; fix with: .venv/Scripts/pip install -e .
```

The full `tests/` suite has ~177 pre-existing failures from Windows-environment issues in
upstream tests (shell launchers, external CLIs, `~` expansion). They are unrelated to lite —
do not chase them, and do not let them block lite work.

## Constraints

1. **No new dependencies.** Use only what `pyproject.toml` already provides
   (httpx, pydantic v2, FastAPI/uvicorn, PyYAML, typer, jinja2). No OpenAI/Anthropic SDKs —
   talk to OpenAI-compatible endpoints over plain httpx.
2. **`agentflow/lite/` stays independent.** It must not import from any other `agentflow`
   module. Shared behavior is re-implemented inside lite, not imported.
3. **Code style.** Every file starts with `from __future__ import annotations`; pydantic v2
   models use `ConfigDict(extra="forbid")`; synchronous APIs only; subpackage `__init__.py`
   re-exports the public API and nothing else.
4. **Tests are fully mocked.** LLM HTTP goes through `httpx.MockTransport`; docker CLI calls
   through `monkeypatch`ed `subprocess.run`. Tests must pass without network access and
   without a Docker daemon.
5. **Language: English.** All content authored after commit `c1ca005` (code, comments,
   docstrings, UI text, YAML prompts, docs) is written in English. Upstream-inherited
   content (`c1ca005` and earlier) is left as-is.
6. **Docs are executable.** Every Python/YAML code block in `docs/` must be verified by
   actually running it (mock the LLM, never a real Docker daemon) before committing.
7. **Container defaults are audit-safe.** `network="none"`, read-only mounts, `512m` memory,
   1 CPU, 120s timeout. `tmpfs` mounts intentionally emit a `UserWarning` (non-persistent,
   not usable for RAG/KB sharing) — keep that behavior.
8. **Graphs are DAGs.** The runner rejects cycles. Feedback loops from paper architectures
   are collapsed into a single node whose inner iteration is carried by `max_iterations`,
   with the loop noted in the node prompt.
9. **Git discipline.** Commit messages: English, imperative mood, matching repo history.
   Never commit or push unless the user explicitly asks.
10. **Monitor stays read-only.** The lite HTTP server exposes GET/HEAD only, enforced by
    middleware (405 for anything else); it is a monitor, not a control plane — do not add
    mutating endpoints.

## Verification checklist before finishing lite work

```bash
.venv/Scripts/python -m pytest tests/test_lite_*.py -q        # all green
.venv/Scripts/python examples/paper_architectures/build_all.py # built N runner(s), 0 failures
```
