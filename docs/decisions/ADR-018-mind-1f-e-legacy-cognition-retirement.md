# ADR-018 — MIND-1F-E legacy cognition retirement

- Status: Accepted and implemented on 2026-09-12
- Phase: MIND-1F-E
- Parent: MIND-1F-D2

## Context

MIND-1F-B/C/D1/D2 established one character-wide `SemanticActor` for
Discord, CLI, temporal, and generic semantic work, but the local Presence
composition still had two direct model-facing runners: `MindAppraiser` after a
conversation and `AutonomousCognitionRunner` after an idle wake. Keeping those
lanes beside the actor left appraisal and local autonomy with separate
admission and cancellation ownership.

## Decision

The canonical local composition has exactly two semantic model routes:

```text
USER     → SemanticActor → ConversationExecutionAdapter → ConversationCore
NON_USER → SemanticActor → CognitionEpisodeRunner → CognitionOutcome
                              → ProposalApplicationCoordinator
```

`PersistentPresenceRuntime` is now a narrow local-surface component. After a
successful Core turn it creates one bounded runtime-owned `INTERNAL` appraisal
trigger. A deterministic idle opportunity creates one bounded `INTERNAL`
trigger referring to the selected runtime-owned intention. Both are admitted
as `NON_USER` work by the same `SemanticActor`, and both use the normal
`CognitionEpisodeRunner` and trusted proposal application path.

The `INTERNAL` trigger source cannot carry observations or wake IDs. Its
bounded source references are runtime-owned identifiers only. Conversation
completion stores a bounded history snapshot and trusted Core message
provenance beside the trigger; model output cannot invent provenance and
internal cognition never writes synthetic Core messages.

Presence `SPEAK` and `STAY_SILENT` proposals are mapped to the existing P4
application registry/session seam. The sink and destination remain
application-owned. `STAY_SILENT` is a terminal no-effect action. A confirmed
`SPEAK` updates the bounded local intention/self-action state only after P4
settlement.

`MindAppraiser` and `AutonomousCognitionRunner` are retired from production
composition. Their direct-generation implementations are retained only as
explicitly deprecated standalone P5-B1 compatibility fixtures for existing
callers/tests during migration. The launcher does not construct them, and
`PersistentPresenceRuntime` has no direct semantic generation path in the
canonical composition.

## Consequences

- A pending appraisal or active idle cognition is actor-owned and can be
  preempted by `USER` work with settlement-before-successor semantics.
- Admission of post-conversation appraisal is not awaited as hidden mandatory
  post-processing, so it cannot delay a new user turn.
- Canonical Core history remains limited to real user/assistant turns.
- Internal request identities are stable within an actor session and replayed
  opportunities are fenced by the existing actor/application fences.
- The existing 256-entry `ProposalApplicationCoordinator` lifetime fence
  ceiling remains unchanged technical debt.

## Evidence boundary

The deterministic E proofs cover actor entry, trusted state application, quiet
appraisal, user priority, P4 speak/silence behavior, application settlement,
replay fencing, distinct opportunities, canonical-history isolation, and
shutdown. Existing D1/D2, MIND-1F-C, MIND-1C/D, P4, and Core tests remain
regression evidence.

Live provider behavior, live Discord transport, restart-safe actor replay,
durable MindState/WakeIntent recovery, and Windows-specific behavior remain
`UNVERIFIED` unless separately exercised.
