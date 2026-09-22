# Hybrid orchestration

AgentFlow keeps business roles, authorization policies, and capacity decisions in
the application. An optional activation gate connects deterministic code with
Agent-generated suggestions without evaluating a model response as code.

## Decision contract

Set a candidate node's `activation` to an object containing `source` (a node ID)
and `path` (JSON object keys, defaulting to the document root). The source is
added as a dependency. Its final captured output must be JSON; the selected value
must be a boolean or a non-negative integer.

A boolean enables or skips the candidate. For a fanout, an integer enables the
first N members in declaration order. Capacity comes from the application's
existing fanout declaration; a count exceeding it fails before candidate
execution. Non-integers, negative counts, missing keys, malformed JSON, and
activation dependency cycles are rejected. `node_activation` events record the
source, path, decision, and selected state. Unselected candidates are skipped
without launching processes or creating their artifact directories.

These are declared candidate definitions whose execution is deferred. They are
not arbitrary new node specifications accepted from an Agent. A later stage can
have its own gate and capacity to enable additional work. Each stage uses the
same optional primitive; no mandatory controller roles or global workflow
budgets are installed by the framework.

Downstream nodes default to `trigger_rule="all_success"`. An optional
`trigger_rule="all_done"` waits for every dependency to become terminal, including
skipped or failed candidates. Use it for a join that must inspect all outcomes;
it does not turn failed work into successful work.

## Non-Agent execution

- `python_node(task_id=..., code=...)` runs application-owned Python code.
- `python_call(task_id=..., function="package.module:function", arguments={...})`
  imports and calls a function inside the execution target, then captures its
  JSON return value. Function stdout is redirected to stderr so diagnostics do
  not corrupt that result. The function and its dependencies must be installed or
  importable there. Arguments are data, not Python source.
- `command(task_id=..., argv=[...])` invokes an external program with explicit
  arguments. It does not insert a shell; explicitly configured target shell
  wrappers still apply. This supports service clients, scripts, and other
  non-Agent tools without requiring a model or an MCP server.
- `AdapterRegistry.register(name, adapter)` remains available for custom
  execution preparation. The target runner owns process execution, timeouts,
  cancellation, and artifacts.

Normal target isolation, dependency scheduling, and retry settings apply to
these nodes. Code with external effects should be idempotent before retries are
enabled. Dynamic template data must be JSON-encoded before insertion into code
or argument documents; the example uses the `tojson` filter.

## Recommended composition

An Agent may propose whether more work is useful. A code gate validates that
proposal against application permissions and capacity, then emits the activation
value. The runtime enforces the declared dependencies and launches only selected
work. Inspect the optional [example](../examples/hybrid_orchestration.py): running
that file prints a pipeline; it does not invoke an Agent. Its capacity of three
is an example policy, not a framework limit.

This separation is consistent with conditional routing and per-item execution in
[AWS Step Functions Choice](https://docs.aws.amazon.com/step-functions/latest/dg/state-choice.html)
and [Map](https://docs.aws.amazon.com/step-functions/latest/dg/state-map-distributed.html),
and with the separation of deterministic workflow decisions from effectful
activities in [Temporal's architecture](https://github.com/temporalio/temporal/blob/main/docs/architecture/README.md).
These are design precedents, not performance or reliability equivalence claims.
AgentFlow records decisions but does not claim Temporal-style durable replay.

## Monitor notation

The monitor remains read-only. By default it shows reached nodes and downstream
possibilities, including alternative branches and restart targets. Reached nodes
include queued, ready, active, completed or failed nodes, and nodes with execution
history even when a restart resets their status. Before any node is reached, the
monitor previews the declared workflow. Never-started skipped or cancelled nodes
and unrelated disconnected future work are omitted after execution starts.
Python and external-command nodes use the same graph and inspector.

Unreached members of each declared parallel fanout appear as one `worker ×N`
representative; reached workers remain individually inspectable. This grouping
does not merge distinct branches. There is no ellipsis or path-length folding.

**Show full**, in the graph corner, displays the complete declared graph, including
already-skipped alternatives, cancelled nodes and disconnected components. Every
parallel worker and dependency arrow is shown individually, so each node can be
inspected. Turning it off restores the default projection.

Dashed edges connect possible future nodes. Directed return arcs express restart
paths, including self-loops. Skipped and cancelled nodes retain their status
without future-node dashes. Solid arrows represent declared dependencies, not
proof that a branch ran. These arcs
show declared restart relationships, not proof that a restart has occurred.
The toggle, node dragging, and inspection change only the local display.

History progress uses the longest acyclic dependency path in the declared graph
as its denominator, measured in node stages rather than total parallel workers.
Its numerator is the deepest settled prefix, advancing a stage only when its
dependencies are settled. Completed, failed, cancelled, and skipped nodes settle
a stage; this measures scheduling progress, not success or elapsed-time estimates.
Restart edges and retry counts do not add stages. Dependency back edges in legacy
cyclic input are defensively excluded during traversal. The denominator is
independent of Show full and of which branches have been chosen.

The following screenshots are historical previews from 2026-09-21, before the
default projection and Show full toggle were introduced. They show the former
reached-only view and the projection that is now the default, respectively.

![Historical reached-only view](images/monitor/hybrid-reached.png)

![Historical downstream preview, now the default projection](images/monitor/hybrid-candidates.png)
