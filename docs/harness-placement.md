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

## Applying the decision in the authoring harness

| Operation | Placement now | Reason |
| --- | --- | --- |
| Choosing a challenge, reasoning about difficulty, explaining evidence | Role instructions; skills remain an option for reusable guidance | Requires judgment; the domain procedure evolves |
| Listing agents or requesting steer/kill/resume | Pi native tools, DSH MCP tools, human/skill CLI | Agent decides when to intervene; all interfaces reach the same backend |
| Validating management arguments and target slot | Shared backend, repeated when watchdog reads persisted requests | Changing interfaces or writing malformed request files must not remove the check |
| Determining whether a role handoff satisfies its schema | Required deterministic graph node after every role | Independent of model-selected preflight and claimed success markers |
| Deciding whether to archive a delivery | Existing final upload gate plus schema revalidation | Prevent direct watchdog archival from bypassing graph checks |
| Process timeouts and cancellation cleanup | Runner code | Must work while the agent is blocked or unavailable |
| Operator guidance | Prompt/tool descriptions | Guidance is not authenticated operator identity or a hard budget ceiling |

Use one shared capability catalog and domain backend, then thin adapters.
`authoring_workflow/tools/agent_tools.json` defines the six management tools.
Pi registers those schemas and invokes the JSON CLI asynchronously. MCP returns
structured results and delegates to the same dispatcher. The shell CLI delegates
to that backend too. No extra MCP server was inserted in Pi, where native tools
already fit; no parallel skill was added just to duplicate six tool descriptions.

The model can still run the handoff validator for early feedback. The runtime
always reruns it before advancing. Invalid output stops the slot; the existing
bounded, session-aware recovery policy owns retries. We reuse AgentFlow's shell
node and dependency machinery rather than inventing a new general hook API.

Receipts have precise meanings: `queued` means a request file was written;
`forwarded` means the Pi bridge accepted it for forwarding; a bridge timeout
means `unknown`, not success. None proves the target model applied the steering.
The `role` argument is audit metadata; cancellation currently targets the whole
slot. Tool descriptions now make this distinction visible to the caller.

## Adversarial contract evaluation

Twenty cases from `test_harness_boundaries.py` were run against a temporary copy
of authoring baseline `8ab9bfe`, then against the correction. All twenty failed
on the baseline and pass now. They cover twelve invalid submission cases across
MCP and direct backend, bridge timeout/command mapping, mandatory graph topology,
archive revalidation, and four schema-validator failure cases. The baseline copy
uses only temporary state and mocked external calls; no live agents were killed.

Additional tests cover JSON CLI parity, valid request receipts, persisted-request
validation at the watchdog, and five Pi extension cases: catalog equality,
nonblocking argv invocation/cancellation forwarding, backend error reporting,
abort behavior, and prevention of already-cancelled dispatch. The required graph paths are inspected and their validator
entry point is executed on nonempty but invalid JSON without a model call.

This is evidence of deterministic interface/enforcement behavior. It is not an
LLM A/B study of whether agents select native tools more accurately than Bash,
or a measurement of token cost, MCP overhead, or production steering success.
A model-controlled tool's schema cannot establish that the model will call it.
For a mandatory invariant, the runtime gate is therefore the deciding mechanism.

## Limits and next focused work

The workflow still grants agents host filesystem/shell access. The checker,
control files and harness configuration are not isolated from the same OS user.
These are enforced transitions in a trusted workflow, not tamper-proof security
boundaries. `actor` is an audit label, not authenticated authorization. The
existing resume action can override the automatic rerun budget; it is not a
non-bypassable spending cap.

Request files still use one pending slot per action, so concurrent submissions
can overwrite each other. Receipt persistence is not a transactional command
queue, and watcher acceptance is not end-to-end application acknowledgment.
The JSON validator intentionally supports only the repository's schema subset;
unknown assertions fail closed, while `format` is metadata, not validation.
The bridge's forwarding acknowledgment is not Pi's correlated RPC response.

The next increment should target these concrete boundaries: an owner-controlled
budget/authorization service, unique command IDs with consume/ack semantics, and
correlated Pi responses. If adversarial tampering is in scope, separate runtime
identity and writable paths, remove alternate execution routes, and verify the
OS boundary before calling a rule non-bypassable. Expanding the skill text or
adding MCP alone does not establish that boundary.

A later model experiment should hold backend semantics and tasks constant while
varying only skill+Bash versus native/MCP exposure, measuring tool selection,
argument repair, completion, tokens and latency over repeated runs. Until then,
choose native Pi and MCP DSH based on verified host compatibility and reuse,
not an unmeasured assertion that one transport is universally better.
