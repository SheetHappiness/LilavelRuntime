# MIND-1F-B — SemanticActor kernel

## Status

Implemented on 2026-09-12.

## Objective

Introduce one runtime-owned, character-wide semantic admission authority for a
single `LilavelRuntime` instance while leaving existing production semantic
lanes unchanged.

## Implementation

- Added provider-neutral `SemanticActor`, `SemanticEpisodeRequest`,
  `SemanticEpisode`, `SemanticCancellationToken`, `SemanticAdmission`, and
  `SemanticSettlement` contracts.
- Added one actor-owned worker with a bounded mailbox, deterministic
  `USER`/`NON_USER` priority ordering, FIFO within each class, and exactly one
  active semantic episode.
- Added cooperative cancellation/preemption. User work requests cancellation
  of active non-user work and waits for settlement before successor admission;
  the same token covers the pre-handle race.
- Added fail-closed poison behavior when cancellation/containment cannot be
  confirmed, plus bounded actor-owned shutdown settlement.
- Added session-scoped duplicate fencing. Settled identities are not evicted
  within a session; settled-plus-in-flight capacity rejects new unique work
  until the actor is idle, then the actor retires a full session and rejects
  retired session descriptors, avoiding a permanent lifetime fence ceiling.
- Added bounded correlation-safe evidence with no raw content, provider state,
  tool authority, credentials, or exception bodies.
- Composed one inert actor into `LilavelRuntime` lifecycle and health without
  routing Discord, CLI, presence, appraisal, temporal, or autonomous
  production paths through it.

## Architectural decision

The actor is character-wide for one runtime instance, not keyed by
`(environment, subject)`. Conversation state remains per conversation and is
still owned by `ConversationCore`. The actor owns admission and lifecycle only;
specialized conversation/cognition executors and the trusted proposal effect
boundary remain separate.

## Deterministic proofs

`apps/runtime/tests/test_semantic_actor.py` proves:

- one active semantic episode across distinct source kinds;
- user-priority cancellation and ordering before older non-user work;
- cancellation intent before executor handle binding;
- duplicate fencing while active and after settlement;
- deterministic bounded-mailbox overflow with no silent drop;
- failed executor settlement followed by successor progress;
- uncontainable cancellation poisoning and successor rejection;
- shutdown cancellation of active/queued work with no owned orphan;
- bounded session rollover without unsafe in-session eviction; and
- evidence bounds/privacy without raw result or content persistence.

## Validation

- Baseline runtime pytest: `PASS` — 132 passed in 8.95 seconds.
- Runtime Ruff check, format check, strict Pyright, and lock check: `PASS`.
- Full runtime pytest: `PASS` — 143 passed in 9.14 seconds.
- Contracts checks and pytest: `PASS` — 7 passed.
- Core checks and pytest: `PASS` — 180 passed, 4 platform skips.
- Discord adapter checks and pytest: `PASS` — 95 passed, 10 existing
  deprecation warnings.
- Root lock check and `lilavel --help`: `PASS`.
- Repository docs integrity, architecture guard, and `git diff --check`:
  `PASS`.

Implementation SHA: `2fd77c31aff3c63d594e64a7b705778687cc85ef`.
Parent SHA: `3c760eaa6f67402c0cbe3fb9e9c9873440ceae5e`.
Existing production paths were not migrated. Live provider/Discord behavior,
restart-safe replay/idempotency, external-effect rollback, Windows-specific
behavior, and durable agent state remain `UNVERIFIED`.

## Exit gate

`One Lilavel → one semantic admission authority` exists as a runtime-owned
opt-in/inert kernel. MIND-1F-C/D/E remain responsible for routing temporal,
reactive/conversation, and autonomous production lanes through it.
