---
name: agentflow
description: Design, validate, and run AgentFlow core pipelines that coordinate external coding-agent CLIs, including DeepSeek Harness. Use when the user mentions AgentFlow, asks for an AgentFlow DAG or DeepSeek Harness node, or needs AgentFlow-specific sequencing, fan-out/merge, guarded branches, bounded retry loops, graph optimization, utility nodes, or local/container/SSH/EC2/ECS execution. Do not use for generic CI or data pipelines, OpenAI Agents SDK orchestration, or agentflow.lite YAML graphs unless the user explicitly asks to translate them to AgentFlow core.
---

# Build AgentFlow core pipelines

Create the smallest auditable DAG that accomplishes the user's goal. Treat pipeline construction, validation, and execution as separate stages.

## Follow this workflow

1. Confirm the user means AgentFlow core. Do not mix this Python DSL with `agentflow.lite`, which is an independent YAML-based stack.
2. Check the available version before using advanced features. In an AgentFlow checkout, consult `docs/pipelines.md`, `docs/cli.md`, `agentflow/__init__.py`, and relevant files in `examples/`. For an installed package, use `agentflow --help` and `agentflow templates`.
3. Establish the outcome, working directory, available authenticated agent CLIs, permitted write scope, execution target, and stop condition. Ask only when a missing choice would materially change cost, side effects, or the graph.
4. Design the graph before selecting agents. Parallelize independent work, serialize dependent work, and use deterministic utility nodes for deterministic tasks.
5. Write a side-effect-free Python pipeline unless the user requests another supported format. Loading, validating, and inspecting a Python pipeline execute the file.
6. Validate and inspect the launch plan. Run it only when the user requested execution or clearly authorized it.
7. Report the pipeline path, graph shape, checks performed, execution status, and any artifacts or unresolved failures.

## Apply safe authoring defaults

- Import public APIs from `agentflow`; do not rely on private modules without first verifying the installed version.
- Give every graph and node a stable, descriptive, unique ID.
- Use `Graph(...)` as the primary context manager. `DAG` is a compatibility alias.
- Connect ordinary dependencies with `a >> b`, `a >> [b, c]`, and `[b, c] >> d`.
- Reference runtime data with Jinja expressions such as `{{ nodes.plan.output }}` and `{{ nodes.review.status }}`. Do not interpolate upstream results while constructing the graph.
- Default agent nodes to `tools="read_only"`. Grant `tools="read_write"` only to nodes that must modify the workspace.
- Let the Harness headless profile own provider, model, MCP, and repository-instruction composition for `deepseek` nodes. Configure those concerns through Harness profiles or patches, not parallel node-scoped settings.
- Set `use_worktree=True` when parallel writers need isolated Git worktrees. Use `scratchboard=True` only when deliberate shared mutable memory is part of the design.
- Keep secrets out of prompts and pipeline files. Use the supported environment-variable or target credential configuration for the installed version.
- Keep concurrency within provider and machine limits. Add `rate_limits` when a provider requires explicit throttling.
- Bound every retry or repair loop with `max_iterations` and a concrete `success_criteria`.
- Preserve audit-safe target settings. Do not enable networking, writable mounts, broader credentials, or paid cloud targets unless the task requires them and the user authorized them.
- Emit the serialized graph, and no unrelated standard-output text, from a Python pipeline: `print(graph.to_json())`.

## Start with a minimal graph

```python
from agentflow import Graph, claude, codex

with Graph("change-review", working_dir=".", concurrency=2) as graph:
    plan = codex(
        task_id="plan",
        prompt="Inspect the repository and propose a focused implementation plan.",
        tools="read_only",
    )
    implement = claude(
        task_id="implement",
        prompt=(
            "Implement the plan and report the files changed.\n\n"
            "Plan:\n{{ nodes.plan.output }}"
        ),
        tools="read_write",
    )
    review = codex(
        task_id="review",
        prompt=(
            "Review the implementation for correctness and regressions.\n\n"
            "Implementation report:\n{{ nodes.implement.output }}"
        ),
        tools="read_only",
    )

    plan >> implement >> review

print(graph.to_json())
```

Select `codex`, `claude`, `deepseek`, `kimi`, `pi`, `opencode`, or `goose` only when that CLI is available and authenticated. Use `python_node` or `shell` for deterministic local work that does not need an LLM, and use `sync` only for explicit remote synchronization.

## Use DeepSeek Harness nodes

```python
from agentflow import Graph, deepseek

with Graph("deepseek-change", working_dir=".") as graph:
    deepseek(
        task_id="implement",
        prompt="Inspect the repository, implement the requested change, and run focused tests.",
        tools="read_write",
    )

print(graph.to_json())
```

- Expect the adapter to launch `dsh --profile headless --output-format stream-json`. Override the executable with the node's `executable` field or `AGENTFLOW_DEEPSEEK_EXECUTABLE` only when needed.
- Use `tools="read_only"` or `tools="read_write"`; AgentFlow maps them to `DSH_PERMISSION_MODE=read-only` or `workspace-write`. An explicit node environment value takes precedence.
- Rely on the shipped headless profile for Bash, file read/write/edit, and `glob`/`grep` search through its packaged ripgrep. Its `web_search` uses DeepSeek's official search first and falls back to local `ddgr` only when the primary is unavailable or lacks credentials; `web_fetch` is disabled by default. Ensure `ddgr` is on `PATH` for local runs that need the fallback.
- Do not set node-scoped `provider`, `model`, `mcps`, or `repo_instructions_mode="ignore"`; the adapter rejects them. Compose Harness configuration with a patch such as `extra_args=["--patch", "team.yml"]`.
- For container execution, build and use `agentflow-deepseek:bookworm-slim` from `dockers/deepseek.Dockerfile`. It shares the AgentFlow base image, pins the verified Harness revision, and includes `ddgr`.

## Choose the right graph primitive

### Fan out independent work

Use `fanout(node, source)` when the same task should run over a count, a value list, or a parameter matrix. Inside the template, access the current item through `item`.

```python
from agentflow import fanout

reviews = fanout(
    codex(
        task_id="review-area",
        prompt="Review the implementation from the {{ item.area }} perspective.",
        tools="read_only",
    ),
    [
        {"area": "correctness"},
        {"area": "security"},
        {"area": "tests"},
    ],
)

implement >> reviews
```

Use `merge(node, source, by=[...])` for key-based groups or `merge(node, source, size=N)` for fixed-size groups. Supply exactly one of `by` and `size`. A merge automatically depends on its source; inside its prompt, use the merge scope exposed through `item.scope`.

### Add success and failure paths deliberately

- Use `node.on_ok >> next_node` for a success-only branch.
- Use `node.on_fail >> fallback` for a handled failure branch.
- Use `review.on_failure >> implement` only for a retry back-edge. Give `review` an objective `success_criteria`, and set a finite graph-level `max_iterations`.
- Keep ordinary DAG edges separate from guarded edges so the intended launch conditions are obvious.

The runner rejects cycles except for its explicit bounded retry mechanism. Collapse conversational feedback loops into a node's bounded inner iteration when a graph-level back-edge is unnecessary.

### Use runtime feedback only for real signals

Declare `feedback_channels` when a local target program produces a file or command-readable signal after an anchor node. Reference only declared channels. Do not use feedback channels as a substitute for normal node outputs, and do not assume local collection works on remote targets.

### Treat optimization as a separate workflow

Use `optimizer`, `score`, `n_run`, or `evolve` only when the user explicitly asks to optimize or evolve a graph. Define a measurable objective first. Multiple runs can consume substantial time and provider budget, and schema validation does not prove that an optimized graph is semantically better.

## Validate before execution

For a new pipeline, run the narrowest applicable checks:

```bash
agentflow validate pipeline.py
agentflow inspect pipeline.py --output summary
agentflow doctor pipeline.py
```

- `validate` checks that the file loads and the resulting graph satisfies the schema.
- `inspect` shows the resolved launch plan without running agent work.
- `doctor` checks local prerequisites such as required CLIs and configuration.
- Because these commands load Python pipelines, keep graph construction free of network calls, subprocess launches, filesystem mutations, and other import-time side effects.

If the user authorized execution, run:

```bash
agentflow run pipeline.py --output summary
```

Do not launch paid cloud infrastructure or remote execution merely to test a pipeline. Stop after validation and inspection when execution was not requested. Never claim a graph was validated or run unless the corresponding command actually succeeded.

To scaffold against the installed version, prefer:

```bash
agentflow templates
agentflow init pipeline.py --template pipeline
```

## Deliver a verifiable result

Return:

- the created or updated pipeline path;
- a concise description of its nodes, dependencies, branches, and iteration bounds;
- the exact validation, inspection, or run commands performed and their outcomes;
- any assumptions about installed CLIs, credentials, network access, targets, cost, or permissions;
- any output artifacts and the first actionable failure if execution did not complete.

Keep the answer proportional to the task. Link to current repository docs instead of reproducing a complete API reference.
