# Runtime lifecycle primitives

Core AgentFlow provides reusable primitives in `agentflow.lifecycle`. Lite
remains independent and does not import this module.

## Local process ownership

On POSIX, each local execution starts a new session/process group. Timeout,
requested cancellation, and cancellation of the executor coroutine send TERM
to that group, allow the termination grace period, then send KILL to remaining
members even if the leader has exited. Stream tasks are cancelled when the
executor coroutine is cancelled. Prompt stdin draining is covered by the node
timeout and cancellation cleanup. All I/O is supervised concurrently, including
cooperative cancellation while stdin is blocked. Callback failures propagate after
cleanup; broken stdin preserves the actual child exit status. Cleanup runs in a
shielded finally path so repeated coroutine cancellation cannot skip escalation.

This covers descendants that remain in the group. A process that deliberately
starts another session, a nested AgentFlow execution (which owns another group),
and external containers require their own cancellation integration. Keep the
application's nested-run and container sweeps until native parent/child run
ownership is implemented. Windows retains direct-process termination.

## Work item and role sessions

`work_item_id(state_dir)` creates a UUID once and reuses it on retry. Concurrent
callers atomically publish one complete identity file; corrupt existing files
raise an error. `session_id(work_item, role, namespace=...)` derives a stable
UUID for a role without exposing arbitrary application identifiers in CLI args.
Archive the state directory with the delivery before starting the next item.
The application owns that transition; do not delete the identity on retries.
The identity file must live on a filesystem supporting atomic hard links.

The authoring integration creates identity during graph construction. Use a
temporary `SLOT_DIR` for graph validation. Existing slot-only Pi sessions are
not automatically migrated; deploy between work items if retaining an in-flight
legacy session is required.

## Content identity

`tree_files` prunes explicitly excluded directories before descent, ignores
symlinks, and raises on traversal errors. Exclusion rules belong to the caller.
`tree_digest` hashes sorted relative paths and streamed file contents using the
existing authoring freeze format. Runtime writes and metadata changes do not
invalidate a digest when those paths are excluded.

Quiesce source writers while listing and hashing. This is a content fingerprint,
not a filesystem snapshot or a complete resume-validity policy. Callers must
also bind any relevant model, prompt, tool and configuration identities.

## Validation and follow-up

Mocked regression tests cover process-group escalation after leader exit and
executor cancellation. Lifecycle tests cover concurrent identity creation,
retry/rotation isolation, invalid identities, excluded-directory pruning,
digest compatibility and path escapes. The authoring repository tests the
actual pipeline and freeze integration.

Next increments are native child-run ownership, typed termination reasons,
durable control-command receipts, version-bound artifact gates, and work-pool
commit/drain semantics. These are not provided by the primitives above.

See [the comparative evaluation](lifecycle-evaluation.md) for measured advantages,
unfavorable cases, remaining gaps and the next acceptance gates.
