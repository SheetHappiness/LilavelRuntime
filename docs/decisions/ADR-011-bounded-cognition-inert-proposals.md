# ADR-011 — Cognition ends at inert proposals

## Status

Accepted and implemented for the MIND-1C slice.

## Date

2026-09-12

## Scope

The runtime-owned boundary after a positive `CognitionTrigger` and before any
state application, action authorization, scheduler, memory, or conversation
effect.

## Decision

`CognitionEpisodeRunner` creates exactly one bounded `CognitionEpisode` for a
trigger in its serialized runtime scope. Before invoking the effect-free
`CognitionEngine` seam, it freezes a `CognitionContext` containing only the
episode ID, runtime scope, trigger evidence, selected admitted observations,
and a versioned immutable `MindStateSnapshot`.

The engine returns a strictly typed `CognitionCandidate`. The runner validates
and binds it to an inert `CognitionOutcome` containing only narrow
`StateProposal` and `ActionProposal` values. MIND-1C never applies state,
executes a tool, sends an environment message, schedules a wake, writes
memory, or appends an assistant message. A quiet completed outcome is distinct
from MIND-1B `NO_COGNITION`: cognition occurred, but both proposal sets are
empty.

Only a successfully completed episode returns a valid outcome. Malformed
output, engine failure, cancellation, and timeout fail closed with no valid
outcome. A new observation or MindState mutation after episode start cannot
change its frozen context.

## Rationale

The model/mind may propose intent, but the runtime owns reality. Keeping
proposal induction separate from application and authorization prevents an
inference result from acquiring runtime authority merely because it crossed
the cognition boundary. The narrow MIND-1C seam also preserves the existing
explicit DM/Core route until a later convergence phase can make that change
deliberately.

## Consequences

- `StateProposal` currently supports only one typed intention operation.
- `ActionProposal` contains only speak/silence intent and optional bounded
  content; it has no destination, user/channel ID, permission, scope, or
  executor handle.
- The runner's lock serializes episodes for one runtime scope without adding a
  scheduler or changing observation admission.
- MIND-1D owns proposal validation/application and action authorization.
- Character composition, memory, relationships, scheduling, and production
  provider calls remain outside this slice.

## Evidence

- [MIND-1C contracts](../../apps/runtime/src/lilavel_runtime/contracts.py)
- [MIND-1C runner](../../apps/runtime/src/lilavel_runtime/cognition_episode.py)
- [MIND-1C deterministic proofs](../../apps/runtime/tests/test_cognition_episode.py)
- [Runtime architecture](../ARCHITECTURE.md)

## Revisit conditions

Revisit when MIND-1D needs a broader proposal vocabulary, when production
conversation should converge on the cognition runner, or when a supported
Character/context composition seam is deliberately introduced.
