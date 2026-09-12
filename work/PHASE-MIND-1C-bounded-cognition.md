# MIND-1C — Bounded cognition episode

## Status

Implemented on 2026-09-12.

## Objective

Establish the invariant:

```text
cognition != state mutation
cognition != external action
```

## Implementation

- Added immutable `MindStateSnapshot` values with runtime mutation versions.
- Added bounded `CognitionContext` and `CognitionEpisode` contracts.
- Added narrow typed `StateProposal` and `ActionProposal` contracts plus a
  structured candidate/outcome boundary.
- Added `CognitionEpisodeRunner`, which freezes context, serializes one
  episode per runtime scope, validates the effect-free engine result, and
  returns only an inert `CognitionOutcome`.
- Kept the existing explicit DM/Core reactive route unchanged; MIND-1C is a
  separate seam and does not invoke the effectful tool loop.
- Added deterministic proofs for quiet cognition, inert state/action
  proposals, malformed output, failure/cancellation/timeout, serialization,
  immutable snapshots, provenance, and conversation isolation.

## Implemented flow

```text
WorldEvent
→ Observation
→ CognitionGate
→ CognitionTrigger
→ bounded CognitionEpisode
→ validated inert CognitionOutcome
→ STOP
```

`NO_COGNITION` remains the MIND-1B result for a gate decision that invokes no
cognition. A quiet MIND-1C outcome means cognition completed and returned no
proposals. MIND-1D will own proposal application and authorization.

## Validation

- Runtime `pytest`: PASS — 108 deterministic tests.
- Runtime Ruff check: PASS.
- Runtime Ruff format check: PASS.
- Runtime Pyright: PASS.
- Repository architecture guard, Markdown integrity guard, and
  `git diff --check`: pending final sweep.
- Live provider and Discord probes: UNVERIFIED — not required and no
  credentials were accessed.

## Unknowns and risks

Live provider/Discord behavior is not established. The engine seam is
provider-neutral and intentionally has no model-backed production composition;
MIND-1D and later convergence must preserve this fail-closed boundary.

## Exit gate

The runtime has one bounded, serialized, immutable cognition episode per
trigger. Only a successfully completed episode can return an inert outcome;
MIND-1C cannot mutate MindState, execute actions, affect Discord, or append
canonical conversation messages.

Implementation SHA: `c001eb089188e3368b5865ab71f8b28afa346b54`.
