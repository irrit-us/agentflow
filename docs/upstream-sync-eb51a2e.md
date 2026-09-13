# Manual upstream sync: eb51a2e

Source: [berabuddies/agentflow commit eb51a2e7a5c7d65da5718c4a41843098b60c80e0](https://github.com/berabuddies/agentflow/commit/eb51a2e7a5c7d65da5718c4a41843098b60c80e0),
“Add typed actors, execution profiles, and Harbor Terminus 2 backend (#43)”.
The fork imports this individual commit with cherry-pick provenance, not a merge
of the upstream branch or a replacement of fork files.

The sync adds typed actors, explicit backend capabilities/profiles, model settings,
secret-file references, MCP authentication/timeout fields, content-addressed Pi
extension bundles, the optional pinned Terminus integration, and resilient failure
persistence. These are library mechanisms. Application-specific handoff validation,
retry budgets, operator authority, secret mounts, and outcome evaluation remain
owned by the application harness. Actor artifact schemas are metadata; they do not
replace the author's mandatory handoff gates.

## Conflict resolutions

- Keep all fork adapters, trace parsers, README context, and path handling while
  adding Terminus and custom registry resolution.
- Keep Pi pinned-session behavior, stdin prompts, and extension-tool allowlists;
  add upstream extension materialization and model settings.
- Render the complete Codex configuration into the profile file so typed model
  settings and MCP configuration are not dropped by the fork's older renderer.
- Preserve scheduler feedback, truncation warnings, error reporting, periodic
  output preservation, and worktree cleanup. Adopt typed model-copy worktree
  preparation and pass the adapter registry to execution resolution.
- Keep the fork's stdlib lifecycle helpers. Place upstream failure handling in
  `agentflow.resilient`, lazily exported as
  `agentflow.lifecycle.ResilientOrchestrator`, retaining the upstream import path.
- Preserve both lifecycle test suites. No Lite implementation is replaced.

The upstream `terminus` optional extra pins Harbor 0.23.0 for Python 3.12+.
No new mandatory dependency is added; Harbor is not installed in the host test
venv. No image build, model invocation, or Docker execution is part of this sync.

## Validation

566 tests passed across actors/profiles, extensions, lifecycle, adapters, Docker
preparation, scheduler, store/validation, DSL, traces, tuned agents, process
supervision, and the complete Lite suite. One installed-Harbor contract test is
skipped because that optional dependency is absent. The actor documentation's
Python example executed. All 47 paper graphs and both method graphs built without
execution. Parent workflow regression tests separately cover the actual handoff,
session, and command surfaces used by this application.
