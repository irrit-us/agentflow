# Lifecycle evaluation and architecture decision

Review date: 2026-09-21. Baseline: AgentFlow `4f234ee` and authoring
`535407d`. This is a scoped engineering evaluation, not evidence that AgentFlow
outperforms other workflow frameworks.

## Decision

Retain the small local CLI runner for the current single-host authoring workflow.
Apply established subprocess supervision practices, and keep work-item identity
separate from execution attempts. Do not build a new distributed workflow engine
inside the library on the strength of unit-test counts.

Before expanding the control plane, evaluate durable workflow infrastructure
against concrete crash-recovery requirements. Use operating-system containment
for host-level process ownership rather than trying to infer ownership from
process names or recursive PID scans.

## Alternatives and evidence

| Option | Applicable strengths | Limits / cost | Decision for this workload |
| --- | --- | --- | --- |
| Direct-child termination | Minimal implementation | Descendants are outside the termination target | Superseded by owned POSIX groups for local CLI execution |
| Owned POSIX process group | No additional service; targets an explicit execution group | New sessions escape; runner death prevents in-process cleanup; Windows differs | Retain as a local primitive, not a containment guarantee |
| systemd cgroup unit | Manages remaining processes in the unit when stopped | Linux/systemd deployment; unit lifecycle and delegated cgroups need configuration; external Docker daemon resources remain separate | Preferred candidate for whole-run host supervision; not installed by this change |
| LangGraph persistent checkpointer | Thread-scoped state, interruption/recovery and graph abstractions | CLI process lifecycle still requires an adapter; adopting it changes the application runtime and dependency set | Candidate if agent-state/human-interruption requirements dominate |
| Temporal workflows and activities | Durable execution, activity retries and progress heartbeats | Requires a service/worker integration; external side effects still need idempotency | Candidate when surviving orchestrator/host crashes or multi-host work is required |
| Current library plus application scripts | Fits existing CLI adapters and task formats without adding dependencies | No distributed leases, transactionally committed delivery, or complete child-run lifecycle | Keep narrow; do not claim parity with Temporal or LangGraph |

These comparisons are architectural inferences from the local implementation and
primary documentation, not measured framework performance. Systemd documents
control-group termination and discourages process-only cleanup in its
[official manual source](https://raw.githubusercontent.com/systemd/systemd/main/man/systemd.kill.xml).
Python documents [new-session creation](https://docs.python.org/3.12/library/subprocess.html)
and [subprocess pipe/deadlock and broken-stdin semantics](https://docs.python.org/3/library/asyncio-subprocess.html).
Its [task cancellation guidance](https://docs.python.org/3.12/library/asyncio-task.html#task-cancellation)
calls for cleanup in `finally`, propagation of cancellation, and retaining a
reference to shielded tasks.

LangGraph documents [persistent thread state](https://docs.langchain.com/oss/python/langgraph/persistence)
and [re-execution/idempotency](https://docs.langchain.com/oss/python/langgraph/graph-api).
Temporal documents [retry-safe activities](https://docs.temporal.io/activity-definition)
and [activity cancellation/progress](https://docs.temporal.io/activity-execution).
A session ID or a workflow checkpoint alone cannot make an external side effect
exactly once. The receiving operation must deduplicate an idempotency key or
support a transactional commit. Provider cancellation behavior must be checked
against the selected SDK/server version.

## Fault-injection comparison

The same seven mocked acceptance cases in `tests/test_local_supervision.py`
were first run against `4f234ee`: all seven failed (2.87 seconds total). They
then passed with the runner correction. The test additions preceded the code
change; failures are recorded in [the evidence record](evidence/lifecycle-supervision.json).

| Injected fault | Baseline observation | Required corrected behavior |
| --- | --- | --- |
| Stdin never drains, cooperative cancel arrives | Does not finish within the 0.5s probe bound | Poll cancellation while stdin is pending; return 130 |
| Both output streams EOF while process remains alive | Fixed 5s wait misses a 1s node deadline | Keep observing the same monotonic deadline; return 124 |
| Artifact/output callback raises | Error is not surfaced promptly | Preserve original exception after cleanup |
| Second coroutine cancellation during TERM grace | Cleanup is interrupted before KILL | Shield owned cleanup, then propagate cancellation |
| Cancellation during final pipe drain | Owned group is not signalled | Cleanup covers final draining as well as execution |
| Child closes stdin and exits 7 | Misreported as timeout/124 | Preserve actual process status |
| External-completion hook raises during setup | Child cleanup is skipped | Cleanup runs on setup failures |

An additional regression covers a recorded child exit while `Process.wait()` is
still pending on inherited pipes. All eight now pass. Subprocesses/signals in
these new tests are mocked. This establishes control-flow correctness for the
injected schedules, not a measured production orphan rate or real-kernel cgroup
comparison. Existing runner regressions also execute small local subprocesses.

The implementation now supervises stdin, stdout, stderr, exit and external
completion concurrently; excludes finished I/O tasks from the polling set;
propagates callback errors rather than converting all exceptions to timeout;
and keeps cleanup in a shielded `finally` path. Cancellation is still propagated
after cleanup. No new runtime dependency was added.

## Filesystem comparison, including an unfavorable case

Run `python scripts/benchmark_lifecycle.py` using the repository environment.
The script creates only temporary local files. Use `--runtime-dirs 0` for the
clean-tree control, and `--output <path>` to retain results. Each result uses
seven warm-cache measurements with alternating implementation order. Directory
scan counts are collected separately from timings. Inputs are identical and
output digests are asserted equal.

| Synthetic fixture | Legacy traversal + digest median | Pruned traversal + digest median | Directory scans, old -> new |
| --- | --- | --- | --- |
| 100 task files + 2,000 runtime files | 105.450 ms | 15.907 ms | 806 -> 2 |
| 100 task files, no runtime files | 10.149 ms | 15.915 ms | 4 -> 2 |

Raw samples, Python/platform metadata and matching digests are retained in
[the runtime-heavy result](evidence/lifecycle-traversal.json) and
[the clean-tree result](evidence/lifecycle-traversal-clean.json).

The runtime-heavy case is about 6.6 times faster for this operation and avoids
all 802 scans inside excluded runtime directories. The clean-tree case is about
57% slower (5.8 ms absolute). The implementation includes containment checking
and streamed hashing, so fewer directory scans do not imply less total work.
Resolving the root once per digest avoids redundant work without removing the
checks. These numbers do not predict overall authoring latency, token cost,
cache-cold storage performance or results on another host.

The supported conclusion is narrower: prune excluded runtime trees before
traversing them, retain digest compatibility, and accept the observed small
clean-tree cost for this workload. It is not a universal speed improvement.

## Remaining gaps and next acceptance gates

1. **Whole-run ownership:** prototype a systemd unit/cgroup adapter on the actual
   deployment host. Inject nested `setsid`, runner crash and stop during tool
   execution. Require zero remaining owned processes after the stop deadline;
   verify unrelated runs survive. Continue separate labeled-container cleanup.
   Do not install it until host capability and recovery behavior are verified.
2. **Resume identity:** add a versioned manifest binding task content, prompt,
   model/provider configuration (without secrets), tools and verifier versions.
   Change each independently and require invalidation of affected cached gates.
   Existing content-only digests remain insufficient for those changes.
3. **Delivery transaction:** use a stable work-item idempotency key at commit.
   Inject a crash before commit, after commit but before acknowledgment, and
   concurrent retries. Require one committed delivery and preserved provenance.
   Decide local transactional storage versus a durable engine before building
   another file-based queue. A random session UUID is not the transaction.
4. **Recovery trial:** compare the existing runner and one selected durable
   backend with the same deterministic fake work, not paid LLM calls. Measure
   duplicate effects, recovery time, cancellation latency, lost acknowledged
   commands and operational setup. Only then decide whether migration is worth
   its deployment and maintenance cost.
5. **Production pilot:** record orphan count, duplicated gate evaluations,
   recovery time and intervention count over a fixed workload before expanding
   rollout. No such before/after production evidence was collected here.

The lite runtime remains independent and its monitor remains read-only.
