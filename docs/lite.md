# Lite: Direct LLM Agents

`agentflow.lite` is a standalone subpackage: a direct OpenAI-compatible LLM
client, a tool-calling agent loop, YAML-declared graph execution, and a
browser monitor. It is fully independent of the CLI-agent orchestration in the
main framework — use it for high-frequency, lightweight tasks where spawning a
CLI agent process per node is too heavy, or when you want plain function
calling against any OpenAI-compatible endpoint (OpenAI, vLLM, Ollama, LMStudio).

```python
from agentflow.lite import LiteLLMClient, LiteAgent, tool
```

## Client

`LiteLLMClient` posts to `{base_url}/chat/completions` with retries on 429/5xx.
The API key falls back to an environment variable; with no key at all, no
`Authorization` header is sent (handy for local endpoints).

```python
from agentflow.lite import LiteLLMClient, Message

client = LiteLLMClient(base_url="https://api.openai.com/v1", api_key_env="OPENAI_API_KEY")
result = client.chat([Message(role="user", content="ping")], model="gpt-4o-mini")
print(result.message.content, result.usage.total_tokens)
```

## Tools

`@tool` derives a JSON Schema from type annotations and the docstring;
`ToolRegistry.dispatch` runs handlers and reports failures as `"Error: ..."`
strings instead of raising.

Schema requiredness follows the Python signature: an argument without a
default is required even when its annotation allows `None`. Nullable types are
advertised explicitly, for example `str | None` becomes
`{"type": ["string", "null"]}`. A default value controls whether callers may
omit the argument.

```python
from agentflow.lite import ToolRegistry, ToolCall, tool

@tool
def add(a: int, b: int) -> int:
    """Add two integers."""
    return a + b

registry = ToolRegistry([add])
print(registry.dispatch(ToolCall(id="1", name="add", arguments={"a": 2, "b": 3})))  # "5"
```

### Shared tool access

`ToolSharingConfig` protects selected handlers across all registry views made
by `ToolRegistry.subset`. `make_agent_factory` uses those shared views, so
parallel graph nodes coordinate automatically. A per-tool `max_concurrency`
caps CPU- or connection-heavy calls. Tools with the same `group` can instead
declare `read` or `write` access: readers may overlap, while writers are
exclusive and receive preference once queued.

```python
from agentflow.lite import (
    ToolAccessPolicy,
    ToolRegistry,
    ToolSharingConfig,
    tool,
)

@tool
def search_index(query: str) -> list[str]:
    return [query]

@tool
def rebuild_index() -> str:
    return "rebuilt"

registry = ToolRegistry(
    [search_index, rebuild_index],
    sharing=ToolSharingConfig(
        policies={
            "search_index": ToolAccessPolicy(
                group="index", access="read", max_concurrency=4
            ),
            "rebuild_index": ToolAccessPolicy(group="index", access="write"),
        }
    ),
)
worker_tools = registry.subset(["search_index"])
assert worker_tools.get("search_index") is search_index
```

Coordination is synchronous and process-local. Unconfigured tools keep their
previous unrestricted behavior. Read/write protection covers one complete
handler invocation, including result serialization. It does not turn a
sequence of separate tool calls into a transaction: to avoid a check-then-use
race, keep the check and mutation inside one handler configured with `write`
access.

## Agent

`LiteAgent` runs the tool-calling loop: chat → dispatch tool calls → repeat,
until the model answers plainly, `max_iterations` is hit, or the token budget
raises `BudgetExceededError`.

```python
from agentflow.lite import LiteAgent

agent = LiteAgent(
    client=client,
    model="gpt-4o-mini",
    system_prompt="Answer concisely.",
    tools=[add],
    max_iterations=8,
    max_total_tokens=20_000,
)
result = agent.run("What is 40 + 2?")
print(result.text, result.iterations)
```

`max_tool_iterations` caps tool-calling rounds independently of
`max_iterations`; when it is exhausted, tools are withheld and the model is
asked for its final answer. Set it to `0` on pure-LLM nodes as a hard
no-tools guarantee.

A `tool_guard` inspects each tool call before dispatch; returning a string
blocks the call and feeds the string back as the tool result. Guards wire in
per node through `make_agent_factory(tool_guard_factory=...)`:

```python
from agentflow.lite import LiteAgent, make_agent_factory

def guard(call):
    if call.name == "run_command" and "rm " in str(call.arguments):
        return "blocked by policy"
    return None

agent = LiteAgent(client=client, model="gpt-4o-mini", tools=[add],
                  tool_guard=guard)
factory = make_agent_factory(client=client, default_model="gpt-4o-mini",
                             tool_guard_factory=lambda spec: guard)
```

Skills are independent instruction/Tool bundles. Pass `skills=[...]` directly
to `LiteAgent`, or pass a `SkillRegistry` to `make_agent_factory` and select
names with `NodeSpec.skills`. `mcp_skill` adapts any synchronous
`MCPToolProvider` to the same abstraction; MCP tools are namespaced by skill by
default and remain subject to normal Tool sharing policies.

## Routing

`ModelRouter` maps roles to ordered fallback chains of `ModelProfile`s; on
`LLMError` it tries the next profile. Profiles carry default
`temperature`/`max_tokens` that callers can override per chat.

```python
from agentflow.lite import ModelProfile, ModelRouter

router = ModelRouter({
    "fast": [
        ModelProfile(name="local", model="qwen3-8b", base_url="http://localhost:8000/v1"),
        ModelProfile(name="cloud", model="gpt-4o-mini", base_url="https://api.openai.com/v1",
                     api_key_env="OPENAI_API_KEY"),
    ],
})
result = router.chat("fast", [Message(role="user", content="ping")])
```

Pass `router=router, role="fast"` to `LiteAgent` instead of `client`/`model`.
Profile names must be unique across every role and fallback chain in one
router. This prevents a cached client for one endpoint or tenant header set
from being reused by a different profile with the same name.

## Container execution

`DockerExecutor` runs commands in ephemeral containers via the `docker` CLI
(no Docker SDK). `container_shell_tool` wraps one as an agent tool named
`run_command`; per-node containers in graphs get it automatically.

```python
from agentflow.lite import ContainerConfig, DockerExecutor, Mount

executor = DockerExecutor(
    ContainerConfig(
        image="python:3.12-slim",
        workspace=".",          # legacy -v mount, read-only by default
        mounts=[Mount(type="bind", source="./kb", target="/kb", read_only=True)],
    )
)
argv = executor.build_argv("ls /kb")  # inspect without Docker running
```

`Mount` covers all four `--mount` types: `bind` (host path), `volume` (named
volume, shared rw between containers), `tmpfs` (in-memory scratch), `npipe`
(Windows pipes). A `tmpfs` mount emits a `UserWarning`: its contents die with
the container, so it cannot persist knowledge-base data or transfer data
between containers. Defaults are audit-safe: `network="none"`, read-only
workspace, `512m` memory, 1 CPU, 120s timeout.

## Graphs from YAML

Pipelines are declared as nodes and edges, loaded with `load_graph`, and
executed by `GraphRunner` with dependency-ordered parallelism. `depends_on`
and explicit `edges` are equivalent; `{{ nodes.<id>.text }}` references
upstream results.

The runner constructs and persists one `NodeInput` for each invocation. New
Agent adapters can implement `run_node(NodeInput)` to receive the resolved
prompt, direct upstream outputs, and fan-out metadata as one validated unit.
Adapters that only implement `run(str)` continue to receive the resolved
prompt.

```yaml
name: audit
nodes:
  - id: scan
    prompt: List suspicious calls in this repo.
  - id: report
    prompt: "Summarize: {{ nodes.scan.text }}"
    depends_on: [scan]
```

```python
from agentflow.lite import GraphRunner, load_graph, make_agent_factory

graph = load_graph("pipeline.yaml")
factory = make_agent_factory(client=client, default_model="gpt-4o-mini")
runner = GraphRunner(graph, factory)
runner.run_in_background()   # or runner.run() to block
print(runner.is_done(), runner.blocked())
```

A node may set `container: {image: ..., mounts: [...]}` to get its own
`run_command` sandbox tool, and `tools: [name, ...]` to pick from a shared
registry passed to `make_agent_factory(registry=...)`.

### Runtime fan-out

Long, data-dependent workloads do not need a statically generated DAG. A node
with `fanout` reads a JSON list from an upstream result and becomes a runtime
barrier: the runner creates one child task per item, runs those tasks in
parallel, then finishes the parent with a JSON array of child item/result
pairs. Downstream nodes continue to depend on the parent node, so the expanded
graph remains acyclic.

```yaml
nodes:
  - id: plan-links
    prompt: 'Return JSON: {"links":[{"id":"L-001"}]}'
  - id: audit-link
    prompt: "Audit {{ link.id }}"
    fanout:
      from: plan-links
      items_path: links
      item_var: link
      max_items: 500
    resource: forge
    max_attempts: 2
  - id: review
    prompt: "Review {{ nodes.audit-link.text }}"
    depends_on: [audit-link]
```

The item placeholder accepts the full item (`{{ item }}`) or dotted object
fields (`{{ item.id }}`). `max_items` is a guardrail against accidentally
unbounded plans. A failed child fails the fan-out barrier; `max_attempts`
retries transient node failures before that happens.

Container mounts and env values are rendered per item too, so each child can
get its own isolated scratch volume instead of sharing one writable volume
with all siblings:

```yaml
  - id: exploit
    prompt: "Exploit {{ item.alert_id }}"
    fanout: {from: triage, items_path: alerts, item_var: item}
    container:
      image: trailofbits/eth-security-toolbox:latest
      env: {ALERT_ID: "{{ item.alert_id }}"}
      mounts:
        - {type: bind, source: ./targets, target: /targets, read_only: true}
        - {type: volume, source: "poc-{{ item.alert_id }}", target: /pocs}
```

Child `poc--0001` mounts volume `poc-<first alert_id>` at `/pocs`, and so on.

### Resource limits and resume

`max_workers` controls total concurrency. Optional resource limits separately
bound scarce executors such as LLM endpoints, Foundry workers, and database
writers. Higher-priority ready nodes are submitted first. A state path enables
atomic snapshots: finished work is reused, while work that was processing when
the process stopped is safely made ready again.

```python
runner = GraphRunner(
    graph,
    factory,
    max_workers=8,
    resource_limits={"llm": 2, "forge": 4, "db-writer": 1},
    state_path="artifacts/runs/audit-state.json",
    resume=True,
)
state = runner.run()
```

For multi-resource or read/write-aware work, declare graph
`resource_settings` and node `resources`. The scheduler acquires the complete
set atomically before submission: read leases share configured capacity and a
write lease excludes every other lease for that resource until the whole node
invocation ends. The older single `resource` plus `resource_limits` form
remains supported.

Nodes also support `trigger_mode`: `input_ready` (the default), `output_idle`
for dependency-free source nodes, and `input_and_output` for dependency
readiness plus downstream-idle backpressure. These modes control a one-shot DAG
invocation; they do not turn a node into a recurring subscription.

The state file belongs to one runner process. Resume rejects a state file if
the graph definition changed, preventing results from a different workflow
from being silently reused. Runtime fan-out is reconciled against the persisted
source items: missing children from an interrupted expansion are restored,
while unexpected children or changed child specifications fail the parent
instead of producing a partial aggregate. See
`examples/lite_dynamic_audit.yaml` for a planner-to-link-audit-to-review
pipeline.

See [Lite Agent graph best practices](best-practices.md) for the recommended
input, trigger, Tool atomicity, resource lease, and skill/MCP patterns.

## Monitor

`create_app` serves a JSON API plus a static single-page UI
(`agentflow/lite/web/index.html`, offline, no CDN):

```python
import uvicorn
from agentflow.lite import create_app, make_llm_health_probe

app = create_app(runner, health_probe=make_llm_health_probe(client))
uvicorn.run(app, host="127.0.0.1", port=8600)
```

- `GET /api/health` — server status plus LLM probe result (latency or error)
- `GET /api/state` — graph name, done flag, per-node status/usage, edge list
- `GET /api/blocked` — preparing nodes and the dependencies they wait on
- `GET /api/nodes/{id}/inspect` — full message history, usage, and error of one node

The UI polls these endpoints, draws the DAG with draggable nodes (layout
persisted in localStorage), a blocked-task sidebar, and a per-node inspect
drawer with the full conversation. The HTTP API is intentionally read-only
(GET/HEAD only); it serves monitoring only, and every other method, including
`OPTIONS`, is rejected with 405.

## Examples

- `examples/lite_agent_demo.py` — single live agent with a bounded, read-only
  repository skill and shared Tool concurrency limits
- `examples/lite_container_demo.py` — tool calls sandboxed in Docker
- `examples/lite_volumes_demo.py` — bind/volume mounts for RAG and data transfer
- `examples/lite_pipeline_demo.py` — offline-by-default feature walkthrough;
  add `--monitor` for the read-only UI or `--live` for a configured endpoint
- `examples/lite_pipeline.yaml` — all trigger modes, atomic multi-resource
  leases, independent local/MCP skills, and an exclusive report publication
- `examples/lite_dynamic_audit.yaml` — bounded runtime fan-out with the same
  trigger, skill, and resource coordination patterns
- `examples/paper_architectures/` — 47 paper architecture graphs (build-only scaffold)
