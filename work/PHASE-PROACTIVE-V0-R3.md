# PROACTIVE-V0-R3 — Deferred Commitment Semantics

## Status

**PASS** for deterministic deferred-commitment semantics and the production-
shaped Discord effect path. Live provider/Discord verification remains
**UNVERIFIED**.

## Baseline and commits

- Expected baseline: `af71acf58effd354a9ba98a06b5ea4bc65fa309`
- Preflight HEAD: `af71acf58effd354a9ba98a06b5ea4bc65fa309`
- Implementation SHA: `c775e44`
- Closeout SHA: recorded at documentation closeout commit
- Push: not performed

## Implemented truth

`IntentionKind` is the single bounded provider-neutral distinction stored with
runtime-owned `MindState` intentions:

- `initiative` remains discretionary and silence-biased;
- `deferred_commitment` is emitted only through explicit appraisal semantics
  for an accepted, concrete, still-unfulfilled USER-requested future action.

The canonical appraisal shape is
`{"action":"create_intention","kind":"initiative|deferred_commitment","text":"..."}`.
The deprecated standalone parser and canonical parser retain the old untyped
shape only as a compatibility mapping to `initiative`; no legacy shape can
create a deferred commitment.

Idle cognition resolves the runtime-owned intention kind from the frozen
MindState snapshot. Initiative accepts only `speak` or `stay_silent`; a due
deferred commitment accepts only internal `fulfill` text, mapped to the
existing inert `ActionProposal(SPEAK)`. Runtime-owned due state and intention
kind supply the trusted effect-time response-obligation/continuity context.
The model still has no destination or effect authority. Confirmed proactive
speech consumes the one-shot intention opportunity and unknown delivery is not
retried.

## Evidence and validation

- Focused R3 runtime tests: **PASS** — 5 passed.
- Existing Runtime presence/MIND convergence tests: **PASS** — 43 passed;
  proposal/application/cognition/intervention focused run: **PASS** — 95
  passed.
- Discord proactive smoke: **PASS** — 25 passed; full Discord adapter:
  **PASS** — 120 passed, 10 existing deprecation warnings.
- Contracts: **PASS** — 7 passed. Core: **PASS** — 195 passed, 4 skipped.
- Runtime residual suite: **PASS** — 399 passed, 3 known host-teardown tests
  deselected. Full Runtime suite: **FAIL** because the repository-documented
  Linux/Python 3.14 async-teardown stall reproduces in
  `test_superseded_prepared_run_cannot_start_stale_generation`; this is host
  behavior and not a focused R3 failure.
- Runtime and Discord Ruff lint/format and strict Pyright: **PASS**.
- Documentation integrity, architecture guard, whitespace, and conflict checks:
  **PASS**.
- Root uv lock/help checks: **PASS**. Model-sidecar `npx bun` check was not
  completed because the command did not produce output in the restricted host
  window; provider/live behavior is not claimed.
- Live provider + Discord send: **UNVERIFIED**; no credentials or live service
  were used.

## Exit gate

**PASS.** Initiative and deferred commitment are distinct; due state is
runtime-owned; fulfillment text remains model-owned; destination, permission,
SemanticActor admission, ProposalApplicationCoordinator application, and
canonical-history isolation remain runtime-owned; no keyword reminder hack,
scheduler, persistence, retry, or second semantic lane was added.
