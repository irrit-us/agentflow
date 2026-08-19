# Examples Guide

## Bundled Templates

| Template | Use it when | Key features |
| --- | --- | --- |
| `pipeline` | You want the smallest generic starter. | Codex plan, Claude implementation, Kimi review, final Codex merge. |
| `codex-repo-sweep-batched` | You want a large repo audit that still produces a readable handoff. | `fanout`, `merge`, `node_defaults`, `agent_defaults`, staged reducers. |
| `local-kimi-smoke` | You want the shortest real-agent local smoke path. | `bootstrap: kimi`. |
| `local-kimi-shell-init-smoke` | You want the explicit shell-init equivalent. | `shell: bash`, login shell flags, `shell_init: kimi`. |
| `local-kimi-shell-wrapper-smoke` | You want the bootstrap expressed as an explicit wrapper. | `target.shell` with `{command}` injection. |

## Python Examples

| Example | Use it when | Key features |
| --- | --- | --- |
| `airflow_like.py` | You want the smallest Python-authored DAG reference. | Static dependencies with `plan >> [implement, review]`. |
| `airflow_like_fuzz_batched.py` | You want a large shard campaign driven by count fanout, batch merge, and a periodic monitor. | `fanout(node, 128)`, `merge(node, src, size=16)`, `schedule.every_seconds`. |
| `airflow_like_fuzz_grouped.py` | You want a large shard campaign driven by matrix fanout and grouped merge. | `fanout(node, {...})`, `merge(node, src, by=[...])`. |

## Lite Examples

| Example | Use it when | Safety and performance |
| --- | --- | --- |
| `lite_agent_demo.py` | You want one live OpenAI-compatible Agent with an independently selectable local skill. | Repository reads and searches are path-contained and bounded; requests have retry, timeout, iteration, token, and concurrency limits. |
| `lite_pipeline_demo.py` + `lite_pipeline.yaml` | You want an executable reference for `NodeInput`, all trigger modes, atomic resources, Tool sharing, local skills, and MCP-as-a-skill. | Runs against a deterministic mock by default, makes no network or Docker calls, publishes only to memory, and exits after one DAG run. Live access and the read-only monitor are opt-in flags. |
| `lite_dynamic_audit.yaml` | You want runtime fan-out using the same coordination patterns. | Fan-out is capped at 16, work is read-only, iterations and tokens are bounded, and resources have explicit capacities. Build or inspect it by default; use a deliberately configured live runner to execute it. |
| `lite_container_demo.py` | You want Agent-selected commands isolated from the host. | Docker has no network, a read-only workspace, bounded CPU/memory/time, and ephemeral containers. |
| `lite_volumes_demo.py` | You need a mount and named-volume reference. | The knowledge base is read-only and containers have no network; running it intentionally creates or reuses one named Docker volume. |
