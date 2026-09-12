# ADR-013 — MIND-1E temporal intentions are time-to-cognition only

## Status

Accepted and implemented on 2026-09-12.

## Decision

MIND-1E adds a narrow provider-neutral `TemporalProposal` to the completed
`CognitionOutcome`. The proposal is inert and contains only a bounded reason,
an optional intention reference, and a timezone-aware requested `not_before`.
It cannot contain a cron expression, queue ID, permission, scope authority,
destination, or executor handle.

`ProposalApplicationCoordinator` validates the complete outcome before any
state or action application. An accepted temporal proposal is converted by
`TemporalCoordinator` into a runtime-owned one-shot `WakeIntent`. The runtime
normalizes the exact UTC deadline, clamps requests to a one-second minimum and
seven-day maximum horizon, owns scope and actor identity, bounds pending wakes
to eight, and deduplicates equivalent pending requests by:

```text
scope + reason + intention_ref
```

The first pending equivalent request wins its deadline. Cancellation and
supersession are explicit runtime operations. A due wake is fenced before it
emits one `CognitionTrigger(source=temporal)`. That trigger carries trusted
wake/source IDs and no observation payload, then enters the existing
serialized `CognitionEpisodeRunner`.

The timer never sends Discord, calls a tool, writes canonical conversation, or
creates an `ActionProposal`. A failed or cancelled episode does not resurrect
the dispatched one-shot wake; a later wake requires a new temporal proposal.

## Persistence boundary

The MIND-1E core is deterministic in-memory temporal semantics and due
dispatch. Durable accepted wakes, restart fencing, and overdue-at-restart
policy are explicitly deferred/`UNVERIFIED`: the current runtime and
`MindState` seams are in-memory and the existing Core SQLite store is not a
small durable agent-state store. No partial persistence is claimed.

## Evidence

`apps/runtime/tests/test_temporal.py` proves:

- future proposal inertness before trusted application;
- invalid input and deterministic past-time clamping;
- due wake to cognition trigger with no action/tool/history path;
- duplicate poll fencing;
- serialization behind an active cognition episode;
- explicit cancellation;
- equivalent-proposal deduplication;
- one-shot failure non-resurrection; and
- trusted wake/source provenance isolation.

The existing runtime, contracts, Core, and Discord suites remain the
authoritative regression checks for MIND-1A–1D and the reactive path.
