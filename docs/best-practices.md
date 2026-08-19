# Lite Agent Graph Best Practices

These recommendations apply to `agentflow.lite` graphs and Agent adapters. They
focus on preserving data boundaries, preventing shared-state races, applying
backpressure deliberately, and keeping skills independently selectable.

The runnable `examples/lite_pipeline_demo.py` applies these recommendations in
one offline-by-default graph. Its YAML shows all three trigger modes and
read/write resource leases; its Python setup shows bounded repository Tools,
shared Tool policies, independent local skills, and a namespaced in-process MCP
provider. The deterministic model transport makes the example safe to run in
tests without network access.

## Pass one validated input unit to every Agent node

Treat `NodeInput` as the boundary between the graph runtime and an Agent. It
contains the resolved model-facing prompt, direct upstream outputs keyed by
node ID, and runtime fan-out metadata. The runner persists this input before it
invokes the Agent, which makes inspection and resume behavior reproducible.

New Agent adapters should implement `run_node(node_input: NodeInput)`. The
runner still calls `run(prompt: str)` for older adapters, so existing custom
Agents remain compatible. Do not make a new adapter parse prompt prose to
rediscover predecessors or fan-out items; use the structured fields instead.

Keep large artifacts outside `NodeInput`. Pass a bounded summary and a stable
artifact reference in an upstream output. The input unit is orchestration data,
not a replacement for an evidence store.

## Choose trigger modes from data requirements

Each node has one `trigger_mode`:

- `input_ready` (default) runs after every declared dependency finishes.
- `output_idle` runs when every outgoing consumer is idle. It is restricted to
  dependency-free source or proactive nodes so it cannot observe partial input.
- `input_and_output` runs only when dependencies are finished and outgoing
  consumers are idle. Use it when downstream backpressure matters.

Output-idle is a scheduling condition, not a recurring subscription. Lite
graphs are one-shot DAGs: a node still runs at most once, except for its
explicit retry attempts or bounded internal Agent iterations.

Prefer `input_ready` for transformations and reports, `output_idle` for source
work that should not overwrite a busy consumer path, and `input_and_output`
for expensive producers whose output must have an idle downstream path.

## Coordinate Tools at the handler boundary

Build one root `ToolRegistry`, configure `ToolSharingConfig`, and give nodes
registry subsets. Subsets and combined registries preserve the root
coordinator.

- Set `max_concurrency` for expensive APIs, connection pools, and executors.
- Put related read and write Tools in one `group`. Reads may overlap; writes are
  exclusive and queued writers receive preference.
- Treat one handler call as the atomic unit. If correctness requires
  check-then-write, implement both operations inside one write Tool. Multiple
  model-selected Tool calls are not a transaction.
- Keep handlers idempotent when retries are possible and return receipts for
  external mutations.

These locks are synchronous and process-local. Use an external lock, lease, or
transaction service when separate AgentFlow processes share the same target.

## Lease external resources atomically

Declare capacities in `GraphSpec.resource_settings` and list every resource a
node needs in `NodeSpec.resources`. A read request shares capacity with other
readers; a write request is exclusive for the entire node invocation.
From highest to lowest precedence, capacities come from runner-level
`resource_settings`, the legacy `resource_limits` mapping, then the graph
declaration.

The scheduler checks and reserves a node's complete resource set under one
lock. If any request is unavailable, it reserves none. This prevents partial
acquisition and lock-order deadlocks.

```yaml
name: indexed-review
resource_settings:
  index:
    max_concurrency: 4
  signing-device:
    max_concurrency: 1
nodes:
  - id: inventory
    prompt: Build the inventory.
    trigger_mode: output_idle
  - id: review
    prompt: "Review {{ nodes.inventory.text }}"
    depends_on: [inventory]
    trigger_mode: input_and_output
    resources:
      - name: index
        access: read
      - name: signing-device
        access: write
```

Use write access for a mutable database, device session, named volume, license,
or other state whose full node operation must be atomic. A capacity of one
alone limits concurrency but does not communicate read/write intent. Resource
leases are process-local; external systems should still enforce their own
transactions and fencing tokens.

## Keep skills independent and treat MCP as a skill provider

A `Skill` is an independently selectable bundle of instructions and Tools.
Register skills once in `SkillRegistry`, then select them by name on each node.
Do not copy skill instructions into every system prompt or expose every Tool to
every Agent.

`mcp_skill` adapts a synchronous `MCPToolProvider` into the same abstraction.
It discovers tools once and delegates calls lazily. MCP tool names are
namespaced by skill by default, preventing silent collisions between servers.
The provider boundary is intentionally transport-independent: a project can
use stdio, streamable HTTP, an in-process fake, or another MCP client without
coupling the graph schema to that transport.

Apply Tool sharing policies to the `SkillRegistry` when skill Tools access a
shared service. MCP does not bypass Tool concurrency, atomicity, approval, or
audit requirements merely because discovery is remote.

## Review checklist

Before running a graph, verify that:

- Every new Agent adapter consumes `NodeInput` directly.
- Every dependency-bearing node uses `input_ready` or `input_and_output`.
- Every mutable Tool operation fits inside one write-locked handler.
- Every scarce or stateful external target has a capacity and explicit access
  mode.
- Multi-resource nodes declare their complete lease set.
- Each node receives only the skills and Tools required for its role.
- MCP tools are namespaced and use the same coordination and audit policy as
  local Tools.
- Cross-process resources have an external coordinator; lite's in-memory
  coordination is not mistaken for a distributed lock.
