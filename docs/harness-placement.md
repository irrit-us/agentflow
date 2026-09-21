# Place harness behavior by authority, not by transport

Review date: 2026-09-21. This decision concerns local harness composition:
skills, model-invoked Bash, native tools, MCP, and agent-external execution.
It does not compare workflow frameworks.

## Decision rule

Ask two separate questions: who decides whether the operation happens, and who
validates and performs it? A shell script invoked by the model is optional from
the workflow's perspective. The same script scheduled as a required graph node
with a checked exit status is a mandatory completion gate. Native tools and MCP
are invocation interfaces, not inherently stronger enforcement mechanisms.

| Mechanism | Best fit | What it does not establish |
| --- | --- | --- |
| Skill/instructions + scripts | Procedures, domain guidance, examples, infrequent operations and exploratory composition | That a model invokes the script, that it obeys the result, or that the operation is authorized |
| Model-invoked Bash | Existing CLIs, exploratory commands, human-readable preflight | Typed domain parameters or mandatory execution; host permissions still matter |
| Native domain tool | Repeated bounded actions needing discoverability, typed inputs, structured results and host cancellation | Backend authorization, correctness, or successful completion merely because it returned text |
| MCP tool | The same domain capability shared across different harnesses/processes | Extra enforcement merely from using MCP; the server must validate and authorize |
| Runtime hook / mandatory graph node / backend check | Invariants, deadline enforcement, input validation, release gates and resource ownership | Protection from an agent able to modify the checker, its configuration or its state |

Skills can improve how an agent uses a tool, and hooks can enforce policies on
both native and MCP calls. These mechanisms compose; they are not mutually
exclusive substitutes. A deterministic command hook differs from an LLM-based
judgment hook. Neither should be described as a sandbox without checking every
available execution route and the underlying permissions.

## Primary evidence and installed behavior

- [Agent Skills specification](https://agentskills.io/specification): skills
  package instructions and optional scripts with progressive loading. Its
  `allowed-tools` field is experimental and host-dependent. Our inference is to
  use skills for operational guidance, not as an enforcement boundary.
- [MCP tools specification, 2025-06-18](https://modelcontextprotocol.io/specification/2025-06-18/server/tools):
  tools are model-controlled, servers validate input, results distinguish tool
  errors, and structured results can accompany text. This is the protocol version
  supported by the local adapter; declaring a schema alone does not validate it.
- [Claude Code hooks guide](https://code.claude.com/docs/en/hooks-guide): command
  hooks run at lifecycle events without requiring a model to choose them.
  This supports mandatory lifecycle checks as a general pattern. This application
  uses AgentFlow graph nodes rather than adding Claude-specific hooks.
- [Pi extension documentation](https://raw.githubusercontent.com/badlogic/pi-mono/main/packages/coding-agent/docs/extensions.md):
  custom tools support typed parameters, abort-aware execution and tool-call hooks.
  A failing custom `execute` must throw; returning text or an `isError` property
  does not set the host's tool error flag.
- The installed Pi 0.85.1 documentation and source confirm this error behavior,
  `pi.exec` accepts timeout/AbortSignal, and its process launch uses `shell:false`.
  Its real argument validator was exercised with the shared plain JSON schema.
  Pi may normalize arguments before calling an extension; backend validation is
  still required. No guarantee is inferred for a different host/version.

## Framework and application ownership

AgentFlow supplies execution, lifecycle, serialization, adapter integration,
optional typed node/profile interfaces, and observable scheduler transitions.
It does not select business roles, prompts, goals, task decomposition, handoff
schemas, release criteria, or a preferred collaboration topology. Those belong
to application graphs, external skills, or illustrative examples.

A caller can use ordinary shell/agent nodes without ActorNode or AgentProfile.
Typed actors describe caller-defined behavior; they do not register a fixed set
of roles. Scheduling controls constrain only the actions explicitly delegated by
a submitted graph. A rerun cap is optional and defaults to unset in the library;
no application-specific numeric cap is introduced by the framework.

Use deterministic runtime checks when the application requires an invariant.
The framework supplies the execution mechanism; it must not install a particular
application's handoff gates or success criteria as mandatory behavior for other
applications. Native tools and MCP should adapt the same capability backend when
cross-host reuse is needed; neither transport chooses the workflow's objectives.

The authoring application's role topology, tool catalog, schema gates, and
historical comparative evidence are maintained in its
[application repository](https://github.com/irrit-us/xpertAuthorFlow/blob/master/authoring_workflow/docs/harness-placement.md).
They are an application of these primitives, not the AgentFlow contract.

## Evidence limits

Fault-injection and contract tests establish deterministic runtime properties.
They do not establish that one tool transport improves model task quality, cost,
or latency. A model comparison must keep backend semantics and task distribution
constant and measure repeated runs. No such model A/B superiority claim is made.

See [scheduler receipts](control-receipts.md) for precise acknowledgment,
idempotency scope, and persistence limits. A same-UID filesystem and a container
marker are not independent authorization boundaries. A deployment requiring
adversarial isolation must provide separate identities and controlled write access.
