# MIND-1F-C — Mind + Temporal → SemanticActor

## Status

Implemented on 2026-09-12.

## Objective

Make `SemanticActor` the real admission and serialization authority for the
new generic cognition and temporal self-wake paths without migrating
conversation, Discord, CLI, presence, appraisal, or autonomous production
lanes.

## Implementation

- Added `MindExecutionAdapter` and bounded `MindExecutionResult` settlement.
- Added `LilavelRuntime.submit_cognition()` with stable trigger-ID fencing and
  `NON_USER` priority for external and temporal triggers.
- Added runtime-owned deadline-driven `TemporalHost` with coordinator change
  notifications, earlier-deadline recomputation, cancellation awareness, and
  one-shot due dispatch evidence.
- Added read-only `TemporalCoordinator.now()` and `next_deadline()` seams plus
  a narrow subscription hook for wake-state changes.
- Composed optional MIND/temporal components into runtime lifecycle. Plain
  `LilavelRuntime()` remains inert and healthy.
- Preserved runner effect freedom and the
  `ProposalApplicationCoordinator` trusted proposal-to-reality boundary.

## Deterministic proofs

`apps/runtime/tests/test_mind_convergence.py` proves:

- generic admission through the actor and duplicate trigger/application fencing;
- temporal proposal → wake → host → actor → runner → application convergence;
- earlier wake interruption and pending-wake cancellation;
- one active Mind episode across external and temporal triggers;
- runner cancellation before application and successor admission;
- quiet cognition, runner failure, and application rejection settlement; and
- shutdown ordering with active/queued Mind work and pending temporal state.

## Validation

- MIND-1F-C proof suite: `PASS` — 11 passed.
- Persistent runtime full pytest: `PASS` — 154 passed.
- Runtime Ruff, format, strict Pyright, and lock checks: `PASS`.
- Contracts Ruff, format, strict Pyright, lock, and pytest: `PASS` — 7 passed.
- Core Ruff, format, strict Pyright, lock, and pytest: `PASS` — 180 passed,
  4 platform skips.
- Discord adapter Ruff, format, strict Pyright, lock, and pytest: `PASS` — 95
  passed, 10 existing deprecation warnings.
- Root lock check and `lilavel --help`: `PASS`.
- Repository docs integrity, architecture guard, and `git diff --check`: `PASS`.

## Unknowns and deferred work

Restart-safe replay/idempotency and durable wake recovery remain `UNVERIFIED`.
Live provider and live Discord behavior remain `UNVERIFIED`. Conversation,
presence, appraisal, and autonomous production migration remains MIND-1F-D/E.
The existing 256-entry proposal-application fence lifetime ceiling remains
known production-liveness debt.

## Exit gate

`TemporalCoordinator produces opportunities. SemanticActor owns admission.
CognitionEpisodeRunner thinks. ProposalApplicationCoordinator changes reality.`

Implementation and documentation closeout SHAs are reported with the phase
commit; no history rewrite or push is performed.
