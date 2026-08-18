# Research Architecture Gap Roadmap

> Status: rough implementation plan, not a release commitment
>
> Review date: 2026-08-18
>
> Research input: `F:\flow-notes`
> Target: make the paper architectures enforce their claims instead of merely
> describing them in prompts.

## Scope and method

This roadmap compares the architecture and engineering recommendations in
`flow-notes` with both AgentFlow runtimes and the paper-architecture examples.
The review prioritizes the synthesis and practice documents, then checks the
cryptography, hardware, and mobile domain surveys. Raw paper text is used only
when the curated notes point to a specific practice claim.

The central finding is a runtime split rather than a complete absence of
orchestration features:

- The core runtime already has artifacts and traces, retries, success criteria,
  guarded failure paths, resume support, and graph optimization.
- The paper architectures are loaded by the independent lite runtime. Lite has
  a validated DAG, runtime fan-out, containers, token and worker limits,
  process-local tool sharing, atomic state snapshots, and a read-only monitor.
- Lite does not inherit core behavior, and repository rules require it to stay
  independent. Several paper graphs therefore name a verifier, human gate,
  fail-open step, feedback loop, cache, or capability verifier without runtime
  semantics that enforce the name.

The plan treats a feature as satisfied only when the runtime can validate,
execute, observe, and test it. A prompt that asks an Agent to behave as a gate
is not a gate.

## Evidence anchors

The highest-weight cross-domain requirements are:

- Filter cheaply before expensive reasoning, close the execution-feedback
  loop, separate verifier from reasoner, preserve safe defaults, and retain
  human backstops: `F:\flow-notes\97-synthesis\agent-security-papers-synthesis.md:93-110`.
- Give every role a termination condition, require execution validation,
  retain a reproducible evidence chain, use fail-open or fail-closed behavior
  deliberately, and monitor budgets: the same file at `411-425`.
- Preserve expected behavior, method choice, observed behavior, and operational
  context as one evidence packet; use `Unknown` when artifacts cannot establish
  pass or fail: `F:\flow-notes\97-synthesis\authoritative-baselines-and-evidence-weight.md:151-161`.
- Compile rules into obligations, refresh them before execution and
  verification, use an independent frozen verifier, and block completion when
  any mandatory item is missing: `F:\flow-notes\98-practice\papers\context_rot.txt:100-117,226-234`.
- Keep a frozen kernel and select small versioned protocol cards with trigger,
  scope, replacement group, budget, and TTL metadata:
  `F:\flow-notes\98-practice\papers\ai_audit_methodology_part_2.txt:81-107`.
- Separate finder, verifier, and report judge; provision resettable state; make
  the Agent untrusted; preserve executable evidence:
  `F:\flow-notes\98-practice\agent-assisted-security-practitioner-evidence.md:24-92`.
- Pin versions and parameters, use deterministic formal/simulation tools as
  adjudicators, retain failed attempts, and require human sign-off:
  `F:\flow-notes\98-practice\cryptography-hardware-agent-engineering-notes.md:17-90`.
- Bind mobile findings to an app build, device state, exact actions, runtime
  evidence, approval, rollback, and a separate validation status:
  `F:\flow-notes\07-mobile-application\agent-mobile-application-security.md:76-154`.

## Confirmed gaps

| ID | Requirement that is not currently enforceable | Current status | Priority |
|---|---|---|---|
| G1 | Frozen task card, threat model, authorization, oracle, obligations, witnesses, failure predicate, and completion contract | Missing from both graph schemas; carried in free-text prompts | P0 |
| G2 | Typed claims and evidence with provenance, content digests, initial/final state, tool/model versions, and `candidate/reproduced/verified/rejected/unknown` states | Core stores useful files and traces; lite stores text/messages/events; neither supplies the complete evidence contract | P0 |
| G3 | Independent deterministic verifier and fail-closed completion gate | Verifier nodes are conventional Agent nodes; any non-throwing lite result becomes `finished` | P0 |
| G4 | Real human approval and safe `on_error`/`on_unknown` behavior | `actor` human review and `qasecclaw` fail-open are prompt-only; no approval-wait state exists | P0 |
| G5 | Bounded generate-execute-verify-revise control flow with typed feedback and semantic stopping | Paper loops are collapsed into one node; `max_iterations` only continues while the model emits tool calls | P0 |
| G6 | Tool mediation for identity, capabilities, parameter policy, response sanitization, sensitive-action approval, receipts, idempotency, and cross-process coordination | Tool sharing currently handles only process-local concurrency and read/write exclusion | P0 |
| G7 | Non-bypassable sandbox ceiling, data labels, redaction, and pinned execution identity | Containers have useful defaults, but graphs may request host networking, writable mounts, arbitrary extra arguments, and `:latest` images | P0 |
| G8 | Run-wide and obligation-level budget reservation for tokens, cost, time, tool calls, CPU, storage, devices, and licenses | Mostly node-local limits and post-call token accounting | P1 |
| G9 | Frozen kernel, runtime protocol selector, obligation refresh, fresh contexts, versioned fact ledger, cache provenance, and structured handoff/merge | Core statically prepends selected skills and has a free-form scratchboard; lite has no corresponding abstraction | P1 |
| G10 | Resettable, long-lived execution target lifecycle with leases, snapshots, replay, rollback, and cleanup | Lite uses `docker run --rm` per command; named volumes preserve files, not emulator/device/service state | P1 |
| G11 | Reproducible experiment matrix, evidence-aware oracle, private/temporal splits, mutation tests, ablations, repeated runs, variance, and promotion/rollback | Core has graph optimization and a score, but no complete trial/evidence model; lite monitor reports status and tokens | P1 |
| G12 | Restricted runtime task trees with typed plans, role/tool templates, depth limits, budgets, and merge checks | Lite fan-out creates homogeneous copies of one node template; dynamic forests are prompt-only | P2 |
| G13 | Executable cryptography, hardware, Android, and iOS architecture coverage | The 47 examples cover binary, smart contract, open-source, protocol, synthesis, and one practice graph; the three newer domain directories are absent | P1/P2 |

### Important concrete mismatches

- `examples/paper_architectures/README.md:99` explicitly says feedback loops
  are collapsed. `agentflow/lite/agent.py:99-128` does not implement an
  oracle-driven graph loop.
- `examples/paper_architectures/02-smart-contract/actor.yaml:14-18` describes
  human approval, but `agentflow/lite/runner.py:357-396` automatically schedules
  every ready node.
- `examples/paper_architectures/03-open-source-code/qasecclaw.yaml:14-17`
  describes fail-open behavior, but `agentflow/lite/runner.py:373-377` converts
  an upstream error into downstream errors.
- `examples/paper_architectures/05-synthesis/bradmoon_harness.yaml:28-39`
  describes a frozen verifier and fail-closed gate, but lite results are free
  text and `agentflow/lite/runner.py:240-244` marks any normal return finished.
- `agentflow/lite/tools.py:70-87` models concurrency/read-write policy, while
  `agentflow/lite/tools.py:277-288` otherwise invokes the handler directly and
  returns its value to the model.
- Container `run_command` tools are created dynamically per node in
  `agentflow/lite/runner.py:417-427`. They do not currently inherit a shared
  policy for scarce EDA licenses, emulators, physical devices, or other named
  executors.
- `agentflow/lite/container.py:82-99` starts an ephemeral container for every
  command. This cannot represent a multi-step Android session, a forked-chain
  service, or a stateful EDA target without an external lifecycle adapter.
- `agentflow/lite/runner.py:68-105` binds resume to the graph hash, not to the
  target revision, model/router, tool implementation, image digest, prompt
  protocol versions, or dataset split.
- Core facilities are useful precedents, not a solution for lite paper graphs:
  see `agentflow/store.py:55-109`, `agentflow/success.py:36-82`, and
  `agentflow/specs.py:861-889`.

## Proposed architecture direction

Define one documented wire contract for task, evidence, verdict, approval,
budget, and execution identity. Implement it independently in core and lite;
do not make lite import core. Cross-runtime JSON fixtures should ensure the two
implementations agree on serialized meaning.

The minimum contract family is:

| Contract | Required meaning |
|---|---|
| `TaskContract` | Target identity, scope, authorization, assets, adversary, prohibited actions, success oracle, disclosure class, and frozen hash |
| `Obligation` | Stable ID, invariant/question, witness, failure predicate, required evidence, owner, budget, status, and unresolved reason |
| `ArtifactRef` | Content digest, size, media type, producer, source version, sensitivity, retention, and lineage |
| `EvidenceRecord` | Obligation IDs, input/output artifacts, command/tool/model identity, parameters, seed, initial/final state, oracle, and receipt |
| `Finding` | Root-cause key, affected target/version, assumptions, impact claim, evidence references, and disposition |
| `VerificationResult` | `pass`, `fail`, or `unknown`, with checked obligations, deterministic evidence, and reason |
| `ApprovalDecision` | Approver identity, decision, reason, task hash, action/parameter digest, scope, and expiry |
| `RunManifest` | Graph and task hashes, repository/input state, model/router settings, tool and image versions, protocols, environment, and dataset split |
| `HandoffRecord` | Contract hash, obligation ownership, assumptions, verdicts, evidence, conflicts, and unresolved work |
| `RunBudget` | Reserved and consumed tokens, cost, wall time, tool calls, CPU/storage, and named device/license leases |

Prompt text should be a rendered view of these records, never their only copy.
Large raw outputs should be stored as artifacts; the model should receive a
bounded summary plus an artifact reference.

## Delivery plan

### Phase 0: Make architecture fidelity visible

Add a paper-architecture manifest before expanding the runtime.

Deliverables:

- A manifest entry for every paper graph with source, domain, graph path,
  fidelity level, required tools/images/licenses/devices, and one of
  `runnable`, `mock-runnable`, or `spec-only`.
- Explicit capability flags for real feedback, deterministic oracle, human
  approval, fail policy, stateful target, evidence contract, and sandbox.
- `build_all.py` reports the capability matrix and exact non-runnable reasons.
- Tests compare the manifest with the discovered YAML corpus and reject claims
  of an enforced capability when the runtime adapter is absent.

Acceptance slice: the existing 47 graphs still build, but `actor`,
`qasecclaw`, `bradmoon_harness`, and placeholder-image graphs are no longer
mistakable for fully executable implementations.

### Phase 1: Build the trust data plane

Implement the contract family and content-addressed evidence storage in lite,
using only the existing standard library and Pydantic. Map the same wire format
onto core artifacts without importing core code into lite.

Deliverables:

- `TaskContract`, obligations, findings, verification results, artifact refs,
  and run manifests with `extra="forbid"` validation.
- Explicit terminal semantics: `complete`, `incomplete`, `unknown`,
  `budget_exhausted`, and `failed`; `max_iterations` is not success.
- Append-only evidence manifest and full stdout/stderr artifacts with content
  hashes; truncation applies only to the model-facing view.
- Resume/replay checks the execution fingerprint, not only the graph hash.
- Candidate-only mode when authorization, version, or oracle requirements are
  incomplete.

Acceptance slice: a mocked verifier failure or missing artifact cannot produce
a confirmed finding or a completed report.

### Phase 2: Add executable gates and bounded control flow

Keep the outer graph acyclic. Represent feedback as a bounded composite state
machine inside one DAG node, with every round observable and persisted.

Deliverables:

- Node kinds for generator, deterministic tool, verifier, approval, and report;
  typed output schemas and completion predicates.
- A bounded `attempt -> execute -> verify -> revise` composite with typed
  feedback, maximum rounds, stop policy, checkpoints, and an inconclusive path.
- Failure policies such as retain-upstream/fail-open, reject/fail-closed,
  fallback, retry, skip, and route-to-review.
- `awaiting_approval`, `rejected`, and `skipped` states plus an injected
  approval provider. Approval enters through a CLI/file/provider control path;
  the lite monitor remains GET/HEAD-only.
- Verifier context isolation: frozen contract and allowlisted evidence refs,
  with an option to require a different model profile or deterministic tool.

Acceptance slices:

- `qasecclaw` retains original SAST alerts when LLM adjudication fails.
- `actor` pauses until an external approval record matches the task and action.
- `smartpoc` revises only after a typed Foundry failure and ends as unknown when
  its budget is exhausted.

### Phase 3: Secure tools, contexts, and execution targets

Deliverables:

- Tool middleware with `ToolCallContext`, immutable manifest hash, strict input
  and output schemas, pre-call policy, post-call sanitizer, audit receipt,
  idempotency key, and approval hook.
- Extend shared Tool coordination to dynamically registered container tools,
  acquire deadlines/cancellation, named resource leases, and a replaceable
  cross-process backend. A file-lock implementation is sufficient initially.
- A non-bypassable `SandboxPolicy` ceiling for image digests, network, mount
  roots, capabilities, user, root filesystem, PIDs, secrets, and forbidden
  Docker arguments. Graphs may only tighten this policy.
- `ExecutionTargetAdapter` lifecycle: probe, acquire lease, prepare/snapshot,
  execute, collect, rollback/reset, release. First targets are a long-lived
  container session, an SSH-backed device host, and an Android emulator.
- Versioned `ProtocolCard` registry and deterministic selector with trigger,
  scope, replacement group, budget, TTL, activation reason, and retirement.
- Obligation refresh before execution, verification, and completion; fresh
  context where required; versioned fact records that distinguish source facts
  from inference.
- Sensitivity labels and redaction on prompt construction, Tool responses,
  artifact views, state files, and monitor output.

Acceptance slice: two parallel hardware nodes cannot overbook one EDA license;
an Android validation attempt always restores its snapshot; a write/egress
Tool cannot run on an expired or mismatched approval.

### Phase 4: Add evaluation and controlled evolution

Deliverables:

- `ExperimentSpec`/trial matrix for model, scaffold, knowledge, context, prompt,
  target split, seed, and repetition.
- Evidence-aware oracle output of `EvidencePass`, `EvidenceFail`, or `Unknown`,
  retaining conflicts with any native scalar score.
- Run-wide budget reservation and settlement plus coverage, wall time, cost,
  unsafe-action rate, confirmed evidence per analyst-hour, and raw repeated-run
  records needed for confidence intervals.
- Private/temporal split metadata, canaries, mutation-injection hooks,
  baselines, and ablations. No private cases need to be committed.
- Restrict graph evolution to versioned local patches of protocol cards, task
  compiler rules, or verifiers. Each patch names its failure family and affected
  obligations, runs in an isolated workspace, and passes local plus hidden
  regression before promotion; otherwise it rolls back to the incumbent.
- Structured fan-out handoff and merge checks for obligation ownership,
  conflict resolution, root-cause deduplication, and evidence reachability.

Acceptance slice: the same graph can run a mocked multi-seed ablation, report
variance and unknown evidence, and refuse to promote a protocol patch that
regresses the hidden fixture set.

### Phase 5: Add domain adapters and honest examples

Domain algorithms remain adapters and examples, not hard-coded runtime logic.
All external tools stay in pinned containers or explicit external targets, so
no new Python dependencies are required.

Cryptography, P1:

- Sage/Python, SMT, Tamarin, and ProVerif/CPSA adapters that report tool version,
  parameter set, seed, status, and artifact refs.
- First graphs: formal-model generate/compile/verify/correct and a
  CryptanalysisBench-style hypothesis/replay/failure-taxonomy workflow.
- Every conclusion labels toy, experimental, or standardized parameters.

Hardware, P1 with proprietary EDA remaining P2/spec-only:

- Elaboration/index, lint, Verilator/iverilog, Yosys, native regression,
  SVA/non-vacuity, equivalence, and paired timing-oracle adapters.
- First graphs: open-source CWE-to-SVA gate and one HWE-Bench smoke task.
- Preserve `bug`, `warning`, and `hallucination`; require patch + SVA + native
  regression before verified repair.

Android, P1:

- APK identity/signature/manifest, decompile/slice/MobSF-style static adapters.
- Emulator snapshot, ADB, Frida/proxy, UI-action and network-trace dynamic
  adapters with approval before mutation, egress, or cross-app actions.
- First graph: static inventory and candidate generation followed by
  human-approved emulator validation and a fixed-build regression.

iOS, P2:

- Capability manifest plus SSH-to-macOS/physical-device target, bundle/signing
  and entitlement inspection, SDK provenance, and constrained network capture.
- Mark IDA-, device-, credential-, or proprietary-data-dependent graphs
  non-runnable when their capability is absent; never substitute a prompt-only
  fallback and call it validation.

## Recommended first three implementation slices

1. **Truthful corpus manifest:** small, low-risk, and immediately exposes which
   paper claims are executable, mocked, or prompt-only.
2. **Task/evidence/verdict contracts:** the common foundation for every gate,
   domain adapter, evaluation, and replay feature.
3. **Verifier + approval + bounded-loop vertical slice:** convert
   `qasecclaw`, `actor`, and one compile/verify example to real semantics before
   adding more architecture YAML.

Each slice must use fully mocked tests. LLM calls use `httpx.MockTransport`;
Docker, device, EDA, formal-tool, lock-backend, and approval-provider calls are
mocked. After each complete slice, run the full lite suite and build every
paper graph.

## Non-goals and invariants

- Do not run paper graphs, Docker, devices, or real LLM endpoints by default.
- Do not add cyclic outer graphs; bounded composite iteration preserves the DAG
  invariant and records inner rounds explicitly.
- Do not make the lite monitor a control plane. It remains read-only and may
  only display gates, evidence, budgets, and approval receipts.
- Do not treat model confidence, fluent prose, compilation alone, or a scalar
  benchmark reward as verified security evidence.
- Do not automate legal, marketplace, disclosure, signing, deployment, or
  irreversible decisions without an explicit external approval policy.
- Do not bundle proprietary EDA, IDA, physical-device access, credentials, or
  restricted datasets. Declare and probe those capabilities.
- Do not add Python dependencies. Use current libraries and subprocess-backed,
  pinned external tools.
- Keep `agentflow/lite` independent. Reproduce behavior from the documented
  wire contract and shared fixture corpus, not by importing core modules.
