# PHASE COG-V1-C — Model-backed Disposition Planner & Eval Harness

Status: `CLOSED`
Baseline SHA: `624de802c5a5558c9c3c26236ffa94d402c700d5`
Implementation SHA at exit: `6500c8d`

## Goal

Add a bounded, provider-neutral model-backed planner for how Lilavel should
approach a direct USER turn, without changing production USER conversation
behavior.

## Scope

- Added Core-owned `DispositionCandidate` validation using the existing
  `ResponseDisposition`, `WorkingState`, and `CognitionReasonCode` vocabulary.
- Added deterministic planner guidance derived from `IdentityCanon`/Character v0
  values, temperament, interests, behavioral anchors, and anti-patterns;
  voice and representative dialogue examples are excluded.
- Added the opt-in runtime `DispositionPlanner` model seam with strict JSON
  parsing, bounded four-message recent canonical context, and cancellation
  containment shared with local cognition generation.
- Added runtime-owned focus compilation and fixed
  `CognitionPolicyDecision(THINK, RESPOND)` binding.
- Added an offline evaluator over the direct-user subset of the COG-V1-A corpus.

## Non-goals

Production USER integration, response admission, attention/intervention policy,
final prose generation, tools/actions, memory, temporal work, confidence,
chain-of-thought, identity mutation, latency changes, and new dialogue-act
taxonomy remain out of scope.

## Decisions

- `USE` a validated `DispositionCandidate`; the model cannot emit attention,
  intervention, focus, guidance, prose, confidence, tools, or proposals.
- `USE` strict fail-closed JSON with duplicate-key rejection, exact keys,
  existing enum/reason-code values, primitive-type checks, and output bounds.
- `USE` a deterministic planner projection from the canonical Character v0;
  do not maintain a second persona definition.
- `USE` a runtime-owned focus compiler. Model output is validated enum/data
  only; it never becomes trusted `WorkingState.focus` text.
- `USE` the existing direct-user invariant: valid planner candidates bind only
  to `attention=THINK` and `intervention=RESPOND`.
- `USE` the existing COG-V1-A direct-user corpus subset for planner evaluation;
  ambient scenarios remain outside this direct-user planner.

## Architecture consequences

The planner is a caller-owned runtime evaluation seam and is not constructed
or invoked by `ConversationCore`, the CLI composition, Discord, or
`SemanticActor`. Core remains the owner of canonical identity and behavioral
value contracts. Runtime owns model transport, bounded context assembly,
focus compilation, and candidate-to-policy binding. No third production
semantic/model lane was added.

## Inherited invariants

IdentityCanon remains immutable stable identity. WorkingState remains dynamic
run-local state. ResponseDisposition remains dynamic per-response posture.
SemanticActor remains the single semantic admission authority, USER and
NON_USER lanes remain distinct, ConversationCore remains canonical conversation
owner, and LocalCognitionEngine remains NON_USER/internal cognition machinery.

## Evidence

- Core candidate and projection: `apps/core/src/lilavel_core/cognition.py` and
  `production_cognition.py`.
- Runtime planner, parser, focus compiler, and evaluator:
  `apps/runtime/src/lilavel_runtime/cognition_model.py` and
  `disposition_eval.py`.
- Focused deterministic proofs: `apps/runtime/tests/test_disposition_planner.py`.
- Architecture guard explicitly recognizes only scoped cognition engines and
  continues to reject unowned generation calls.

## Validation

- Preflight exact baseline and clean worktree: `PASS`.
- Core Ruff check, format check, strict Pyright: `PASS`.
- Core focused cognition tests: `PASS` — 24 passed.
- Core full pytest: `PASS` — 195 passed, 4 Linux platform skips.
- Runtime Ruff check, format check, strict Pyright: `PASS`.
- Focused COG-V1-C tests: `PASS` — 19 passed.
- Runtime pytest excluding the documented pre-existing timing-sensitive
  `temporal_non_user_work_waits_behind_active_user_conversation` test:
  `PASS` — 239 passed, 1 deselected.
- Full runtime pytest: `FAIL` — the run stalled in the existing
  conversation-actor timing area and was interrupted; this does not establish
  a full-suite pass. The limitation is documented in `docs/VALIDATION.md`.
- Architecture guard, docs integrity, and `git diff --check`: `PASS`.
- Live provider, Discord, Windows, restart, and production planner integration:
  `UNVERIFIED`.

## Unknowns

Provider-specific structured-output adherence, live planner quality,
production integration behavior, cross-process replay, and platform-specific
behavior remain `UNVERIFIED`.

## Exit gate

The bounded candidate, strict parser, deterministic Character projection,
runtime-owned focus compiler, fixed direct-user invariants, corpus evaluator,
and non-production integration boundary are implemented and covered by
deterministic tests. The known full-runtime timing limitation remains outside
this phase's planner behavior and is reported separately.
