# MIND-1F-D1 — Discord Conversation → SemanticActor

## Status

Implemented and validated on 2026-09-12. Implementation commit:
`5a87f8b62ef40fccfe80cdd763ec6049c544f394` (parent
`f1f45b67fa93dad71a00cd9eb6b5241a33fc387d`).

## Decision and ownership

The production Discord DM path is now:

```text
Discord DM → WorldEvent → Observation admission → cognition gate
→ CognitionTrigger identity → SemanticActor(USER)
→ ConversationExecutionAdapter → CoreConversationRouter
→ ConversationCore → ModelRuntime → typed Discord presentation actions
```

`LilavelRuntime` owns exactly one character-wide `SemanticActor`. It owns
admission, the USER/NON_USER priority decision, serialization, user preemption
of active non-user work, actor-session replay fencing, and settlement. The
runtime-owned `ConversationExecutionAdapter` contains one router/presentation
episode and joins cancellation settlement before releasing the actor lane.

`CoreConversationRouter` retains `(environment, subject)` session continuity
and its defensive subject-local lock. `ConversationCore` remains the sole
authority for canonical user append, assistant candidate streaming, successful
assistant commit, cancellation/supersession semantics, and canonical history.
Actor admission and cancellation add no synthetic conversation messages.

Discord edge deduplication, opaque subject mapping, DM-only filtering, typing,
streaming, chunking, pacing, and typed presentation routing remain unchanged.
CLI conversation, `PersistentPresenceRuntime`, appraisal, autonomous
production behavior, idle wake policy, and the MIND-1F-C non-user path outside
the actor composition are not migrated by D1.

## Deterministic evidence

- Runtime D1 proofs: `tests/test_conversation_actor.py` — 5 passed. This covers
  actor admission and duplicate fencing, canonical Core history, USER
  preemption and settlement-before-successor, temporal queueing, shutdown,
  and fail-closed poisoning when containment is uncertain.
- Runtime full suite: 159 passed.
- Discord adapter full suite: 95 passed, 10 existing deprecation warnings.
  This includes edge deduplication, opaque per-subject sessions, serialized
  same-subject continuity, cross-subject history isolation, presentation
  correlation, interruption/partial-output behavior, and scenario proofs.
- Shared contracts: 7 passed. Core: 180 passed, 4 skipped.

## Validation

`ruff check`, `ruff format --check`, strict `pyright`, and `uv lock --check`
passed for runtime, Discord, contracts, and Core. Root lock validation and
`lilavel --help` passed. Documentation integrity, architecture guard, and
`git diff --check` passed.

## Unknowns, debts, and exit gate

Live provider behavior and live Discord transport remain UNVERIFIED. Restart-
safe actor replay, durable MindState/wake recovery, and Windows-specific
behavior remain UNVERIFIED. The existing 256-entry lifetime
`ProposalApplicationCoordinator` fence ceiling remains technical debt.
CLI/presence convergence remains deferred to MIND-1F-D2 / 1F-E.

Exit gate: one actor-owned Discord admission path, no independent legacy route
task, Core-only canonical conversation semantics, contained shutdown, and
existing MIND-1F-C behavior retained.

## Implementation SHA

Implementation commit: `5a87f8b62ef40fccfe80cdd763ec6049c544f394`.
