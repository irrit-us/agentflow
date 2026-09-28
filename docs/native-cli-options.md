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
not add `--ignore-rules`. Claude uses empty setting sources, strict MCP
configuration (an empty server map when none is configured), disabled slash
commands, and no session persistence. Its isolated environment disables automatic
instruction-file and memory loading. It explicitly disables SIMPLE mode instead
of using `--bare`, because bare mode removes search/web tools and skips supplied
policy hooks in supported CLI versions. Explicit tools and hooks remain active.
Native managed policies may still apply. Configuration isolation
is not OS-level containment. `extra_args` remains a trusted operator escape
hatch and must not be supplied by untrusted task content.

See the [Claude Code CLI reference](https://code.claude.com/docs/en/cli-reference)
and [Codex non-interactive mode](https://learn.chatgpt.com/docs/non-interactive-mode).
CLI installations must support the selected flags; tests use mocked executables
and do not require model access.

## Read context and network access

`cli_options.readable_roots` accepts unique absolute target directory paths.
`network_access` is optional and independent of filesystem write policy. With
neither option supplied, legacy sandbox rendering remains unchanged.

When either is supplied, Codex uses an `agentflow_native` named permissions
profile extending `:read-only` or `:workspace`, adds read-only filesystem roots,
and sets `network.enabled`. This requires a Codex installation supporting named
permission profiles (locally verified with 0.156.1); unsupported versions fail
rather than falling back to unrestricted access. Web search remains separately
configured through `model_settings.web_search`.

Claude adds each root with `--add-dir`. Setting `network_access=false` removes
WebSearch and WebFetch from its selected tools; otherwise their availability
follows the normal tool selection. Use native tool selection for Claude web
research rather than Codex's `web_search="live"` model setting. Claude directory
and tool settings are not an OS filesystem or network sandbox.

## Docker as the Codex sandbox

`cli_options.external_sandbox=true` is a Codex-only option for hosts where nested
Codex user namespaces are unavailable. It is accepted only with an isolated
DockerTarget: no privileged mode, Docker daemon mount or DinD; context mounts
must be read-only, and read-only nodes require a read-only workspace. Every
readable root must name an explicit Docker context mount, and an explicit network
permission must match Docker's network policy.

The adapter then selects `--dangerously-bypass-approvals-and-sandbox` inside that
container and omits named Codex permission profiles. Docker owns filesystem and
network isolation; this never enables the flag for a local target. Trusted
application hooks still run. See the official
[container sandbox guidance](https://learn.chatgpt.com/docs/agent-approvals-security).


Codex 0.156.1 does not recognize `model_max_output_tokens`. AgentFlow therefore
expresses `model_settings.max_output_tokens` as advisory final-response guidance
for Codex rather than emitting that ineffective configuration key. Applications
must not describe it as a hard provider token ceiling.
