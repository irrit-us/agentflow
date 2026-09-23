# Native CLI execution options

`NodeSpec.cli_options` provides opt-in controls for the native Codex and Claude
Code adapters. It accepts `system_prompt`, `output_schema`, `tool_names`,
`isolate_config` (default false), and `prompt_via_stdin` (default true). Unknown
fields are rejected. Existing nodes that omit `cli_options` retain their legacy
command construction.

The adapters return `PreparedExecution` objects. The normal AgentFlow runner
continues to own process startup, stdin, timeout, cancellation, runtime files and
output collection. Applications need no second process runner.

## Prompts and structured output

Codex prepends `system_prompt` to the task context; this is user-message context,
not a replacement for Codex's internal system instructions. Claude Code appends
it to the native system prompt using a runtime file. With stdin enabled, the
combined task is not placed in process arguments.

Codex receives a runtime schema file and writes its final answer to
`structured-output.json` in the runtime directory. Preparation initializes that
file to empty, preventing reuse of a previous answer. Claude Code receives
`--json-schema` and returns its structured object in its terminal result event.

`agentflow.agents.structured.parse_structured_output` accepts the agent name,
stdout JSONL lines, and optional Codex final-file text. It rejects malformed
streams, missing completion, failed terminal results, and non-object output.
For Codex, any error or failed turn rejects the result; for Claude, the last
result event is authoritative. Callers must also check the process exit code and
validate their application schema. This helper does not convert assistant prose
into execution evidence.

## Tools and configuration

Codex supports its native read-only or workspace-write sandbox, not an exact
named-tool allowlist. Supplying `tool_names` for Codex therefore fails. Under
these options, `AGENTFLOW_CODEX_SANDBOX_MODE` cannot weaken the node's tool policy.

Claude Code uses `dontAsk` permission mode and an explicit `--tools` selection,
with the same tools passed as `--allowedTools`. An empty list disables the
selected tools. Read-only overrides accept only known reading and web tools;
shell, delegation and writing tools are rejected. Claude's tool selection is
not a filesystem sandbox.

With `isolate_config`, Codex uses `--ignore-user-config` and `--ephemeral`, while
explicit model, provider and MCP settings are passed as configuration overrides.
Local authentication and execution policy rules remain available; isolation does
not add `--ignore-rules`. Claude uses `--bare`, empty setting sources, strict MCP
configuration (an empty server map when none is configured), and no session
persistence. Native managed policies may still apply. Configuration isolation
is not OS-level containment. `extra_args` remains a trusted operator escape
hatch and must not be supplied by untrusted task content.

See the [Claude Code CLI reference](https://code.claude.com/docs/en/cli-reference)
and [Codex non-interactive mode](https://learn.chatgpt.com/docs/non-interactive-mode).
CLI installations must support the selected flags; tests use mocked executables
and do not require model access.
