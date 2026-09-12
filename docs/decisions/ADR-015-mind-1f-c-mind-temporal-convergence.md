# ADR-015 — MIND-1F-C Mind and temporal convergence through SemanticActor

## Status

Accepted and implemented on 2026-09-12.

## Decision

The new non-conversational MIND path is admitted by the runtime-owned
`SemanticActor` before it reaches `CognitionEpisodeRunner`. `LilavelRuntime`
exposes an explicit `submit_cognition()` seam for generic and temporal
`CognitionTrigger` values. Every MIND-1F-C request uses the stable
`CognitionTrigger.trigger_id`, `NON_USER` priority, and the actor's current
session fence.

`MindExecutionAdapter` is the only executor composed beneath the actor for
this path. It runs one trigger through the effect-free
`CognitionEpisodeRunner`, then sends only a valid completed outcome to the
`ProposalApplicationCoordinator`. The adapter returns bounded
`MindExecutionResult` metadata only after cognition and application have
settled. Known runner failure maps to terminal actor failure; known application
rejection/failure is terminal and is not retried. Cancellation before a valid
outcome skips application. Cancellation after application begins waits for the
coordinator's existing joined settlement and does not claim rollback.

`TemporalHost` is a runtime-owned lifecycle component. It has no model, runner,
application, environment, or tool access. It waits for the earliest
`TemporalCoordinator.next_deadline()` or a narrow coordinator change
notification, calls `poll_due()` at the deadline, and submits each returned
trigger once to `LilavelRuntime.submit_cognition()`. A failed actor admission
is recorded as bounded dispatch evidence; the already-dispatched wake is not
resurrected. The coordinator retains its one-shot, in-memory, minimum-delay,
horizon, capacity, deduplication, cancellation, supersession, ordering, and
restart-`UNVERIFIED` semantics.

Runtime shutdown stops the temporal host first, closes and settles the actor,
then settles the remaining runtime-owned tasks. The default `LilavelRuntime()`
composition remains inert and requires no engine or application coordinator.

## Non-goals

Discord reactive conversation, CLI conversation, `PersistentPresenceRuntime`,
`MindAppraiser`, `AutonomousCognitionRunner`, and canonical ConversationCore
semantics remain unchanged for later MIND-1F-D/E migration. No durable MindState
or WakeIntent persistence, restart-safe replay, recurring scheduling, salience
policy, or legacy presence retirement is added. The existing 256-entry
proposal-application fence ceiling remains production-liveness debt.

## Evidence boundary

Actor and temporal-host evidence contains only bounded request/trigger/wake IDs,
source, priority, lifecycle/status, application settlement class, sequence, and
safe reason codes. It does not retain user text, temporal reason text, prompts,
model output, tool arguments/results, credentials, or chain-of-thought.
