# PHASE COG-V1-A — Cognition Policy Contracts & Eval Corpus

Status: `CLOSED`
Baseline SHA: `f227193cda266f050873067eb49f5208935e047b`
Implementation SHA: `1bc1c30b9a4928aa9fe46b2144e1da5c3c2a60da`
Parent SHA: `c902cd78086143056902ee53d043d62982776363`

## Goal

Define explicit, provider-neutral attention, intervention, and disposition
contracts plus a deterministic policy-level scenario corpus without changing
production runtime behavior.

## Scope

- Added `AttentionDecision` (`DROP`, `NOTE`, `THINK`) and
  `InterventionDecision` (`NONE`, `RESPOND`, `INTERJECT`) in Core.
- Added bounded `CognitionReasonCode` values and fail-closed
  `CognitionPolicyDecision` validation.
- Reused the existing Core `WorkingState` and `ResponseDisposition` types;
  `ResponseDisposition` was sufficient and was not extended.
- Added a 20-scenario, data-driven COG-V1-A corpus and deterministic evaluator
  for policy decisions, including anti-caricature and interest anti-keyword
  cases.
- Added focused deterministic tests for impossible states, direct-user
  invariants, bounded reasons, selective curiosity, vulnerable-turn behavior,
  and corpus antipatterns.

## Non-goals

Production policy modeling, salience scoring, runtime integration, automatic
NOTE handling, routing changes, USER→USER interruption, memory/retrieval,
relationships, affect, randomness, provider calls, live prompt tuning, UI, and
new tools/actions remain out of scope. This phase has no memory dependency.

## Decisions

- `USE` three separate decisions: worth thinking about, worth speaking about,
  and how to speak.
- `USE` a direct-user invariant in the corpus and evaluator: direct addressed
  user turns require `THINK` plus `RESPOND`; they are not ambient optional
  interventions.
- `USE` bounded reason codes rather than an opaque salience scalar. Reasons
  identify dimensions such as addressedness, contradiction, novelty,
  relevance, interest affinity, interruption cost, repetition, and social or
  vulnerable context without storing arbitrary user text.
- `USE` character interests as salience modifiers only. The corpus explicitly
  distinguishes an irrelevant tailoring keyword from genuine tailoring
  affinity.
- `USE` existing `WorkingState` and `ResponseDisposition` vocabulary. No
  parallel reaction enum and no disposition extension were necessary.
- No ADR was added: this is a phase-scoped, non-production contract/eval
  slice, and the existing Core ownership decisions remain unchanged.

## Architecture consequences

`IdentityCanon` remains stable identity. `WorkingState` remains dynamic
run-local focus/engagement/stance, and `ResponseDisposition` remains dynamic
per-response behavior. `CognitionPolicyDecision` carries only the latter two
when appropriate; it cannot mutate identity. `DROP`/`NOTE` cannot carry
dynamic behavior, `THINK` plus `NONE` is valid for silent cognition, and
speaking decisions require `THINK` plus a disposition.

The policy contracts and evaluator are not wired into `LilavelRuntime`,
`CognitionGate`, `CognitionEpisodeRunner`, `ConversationCore`, or production
conversation composition. Proposal/effect, semantic admission, and Core
ownership invariants are unchanged.

## Evidence

- Core policy contracts: `apps/core/src/lilavel_core/cognition.py`
- COG-V1-A corpus/evaluator: `apps/core/src/lilavel_core/cognition_eval.py`
- Deterministic proofs: `apps/core/tests/test_cognition_policy.py`
- Existing runtime admission/episode contracts were inspected and left
  unchanged.

## Validation

Executed on Linux with Python 3.14.7. The `UV_CACHE_DIR` and
`RUFF_CACHE_DIR` overrides only relocate tool caches because the shared
environment cache is read-only; the locked commands and dependency graphs are
unchanged.

- Core `uv sync --locked` and `uv lock --check`: `PASS`.
- Core Ruff check, format check, and strict Pyright: `PASS`.
- Focused COG-V1-A tests (`tests/test_cognition_policy.py`): `PASS` — 12
  passed.
- Full Core pytest: `PASS` — 192 passed, 4 platform skips.
- Contracts sync/lock, Ruff, format check, strict Pyright, and full pytest:
  `PASS` — 7 passed.
- Discord adapter sync/lock, Ruff, format check, strict Pyright, and full
  pytest: `PASS` — 95 passed, 10 existing deprecation warnings.
- Runtime sync/lock, Ruff check, strict Pyright, and source-only format check:
  `PASS`. Runtime tests excluding the unchanged D1 conversation file:
  `PASS` — 199 passed.
- Full runtime format check: `FAIL` on two pre-existing formatting issues in
  unchanged `apps/runtime/tests/test_runtime_h2.py`; no runtime file belongs
  to this phase.
- Full runtime pytest: `UNVERIFIED` — the unchanged
  `test_temporal_non_user_work_waits_behind_active_user_conversation` did not
  settle. The same hang reproduced against a temporary baseline Core checkout
  at the expected starting state, so it is not attributed to this change.
- Root sync/lock, launcher help, docs integrity, architecture guard, and
  `git diff --check`: `PASS`.

Live provider/Discord behavior, Windows-specific behavior, durable recovery,
and future policy quality remain `UNVERIFIED`; no provider calls were made.

## Unknowns

How a future production policy derives these decisions from richer evidence,
how NOTE is represented, and how policy decisions interact with future memory,
relationship, affect, or scheduling systems remain `UNVERIFIED` and deferred.

## Exit gate

The phase passes when the three contracts are distinct, direct-user silence is
not representable by the corpus, Core disposition vocabulary is reused,
positive and anti-caricature cases are covered, no production behavior
changes, and all deterministic validation passes.
