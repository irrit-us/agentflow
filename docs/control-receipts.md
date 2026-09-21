# Scheduler control policy and receipts

Periodic output is a request to the scheduler, not authority to change the graph.
`PeriodicScheduleSpec.allowed_actions` limits the controller to `cancel` and/or
`rerun`; an empty list denies all actions. The default preserves both actions for
existing graphs. The scheduler resolves target membership from the submitted
schedule's watched fanout, not the output or a caller-supplied group name.

`max_reruns_per_member` optionally bounds accepted periodic reruns of each member
across the entire run. Zero denies reruns; omission retains existing unlimited
behavior. Charges live in `NodeResult.control_rerun_count` and survive RunStore
reload and slot/workspace rotation. All periodic controllers share that member
counter, including actions without a correlation ID. This is a count of accepted
rerun requests, not token spend, node-level retries, cycles, or a global execution
budget. Graph submission and run-store write access remain trusted operations.

An action may include `command_ids`, a list of up to 100 nonempty strings of at
most 128 characters. Results are stored on the controller's
`NodeResult.control_receipts` and exported to its `control-results.json` artifact
after run persistence. Every result includes run/controller identity, tick,
command IDs, target, action, status, reason, and timestamp. Duplicate correlated
IDs for the same controller/action/target reuse the existing decision rather than
actuating twice; fresh IDs in a mixed batch receive their own decision. Rejected
commands also keep their decision. IDs are scoped to a run and controller.

The `scheduler_applied` status has two precise effects:

- `cancel_requested`: the scheduler set the cooperative cancellation flag.
- `rerun_queued`: the scheduler charged the budget and queued/deferred a rerun.

Neither confirms process termination, model acceptance, or task success. Read
node status and attempts to observe those outcomes. Rejections identify an
unauthorized action, out-of-scope target, exhausted budget, pending rerun, or
ineligible node state. A batch is not an atomic transaction: cancel and rerun can
have different outcomes. Existing applied/rejected events include these receipts.

There is no exactly-once claim across process crashes or storage failure. Flags
and pending tasks have in-memory state; persisted receipts describe scheduler
decisions, not crash recovery completion. A missing artifact/receipt is unknown,
never success. An OS-separated service or protected run store is still necessary
when agent processes must be unable to rewrite policy or audit state. Tool schemas,
actor labels, and same-UID files alone do not provide that isolation.
