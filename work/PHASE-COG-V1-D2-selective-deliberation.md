# PHASE COG-V1-D2 — Evidence-Backed Selective Deliberation

Status: `PASS`

Baseline SHA: `13ec681945c0444954202d3f3793b4a14a1d8dd7`

Parent implementation SHA: `d39081916cb3abecd3bb8bb54e6d6a97a3790275`

Implementation SHA: `c34743b0141cc12f35997c5214fc0c7344ff6c93`

## Goal

Add a provider-neutral FAST/PLAN routing seam above the COG-V1-D1 run-bound
USER disposition lifecycle. Route on expected disposition-planning benefit,
not generic request complexity, and enable SELECTIVE only as an explicit
evidence-backed opt-in mode.

## Implemented contracts

- Core adds immutable `DeliberationDecision` (`FAST`/`PLAN`), bounded
  `DeliberationContext` (current user turn plus at most four canonical
  messages), and a provider-neutral synchronous `DeliberationPolicy` protocol.
- Runtime adds `DeliberationMode.SELECTIVE` while preserving
  `DEFAULT_ONLY` and `ALWAYS_PLAN`.
- `UserDispositionResolver` calls the injected policy only after
  `prepare_turn()` acceptance. FAST binds the D1 default with zero planner
  calls; PLAN invokes the existing `DispositionPlanner` exactly once and
  reuses D1 validation/fallback/cancellation behavior.
- Router failure is explicitly fail-closed to FAST/default with
  `deliberation_policy_failed`; planner failure retains the D1 fallback
  contract. No LLM router, third semantic lane, or shared mutable disposition
  state was added.
- D1 evidence now includes deliberation decision, mode, policy identity, and
  router fallback reason without storing user text or planner output.
- The adapter's blocking bridge uses the same bounded `threading.Event`
  handoff pattern as the adjacent conversation router; this preserves the D1
  seam on the current Linux/Python host.

## Oracle and dataset

`COG_V1_D2_SCENARIOS` contains 21 human-authored cases. It reuses direct-user
COG-V1-A scenario references and the existing disposition-constraint vocabulary,
then adds counterfactual and recent-context cases. Labels are derived from
default/planner constraint satisfaction:

| Label | Count | Meaning |
| --- | ---: | --- |
| `FAST_SAFE` | 10 | default satisfies the authored constraints |
| `PLAN_HELPFUL` | 9 | default fails and planner behavior satisfies them |
| `PLAN_HARMFUL` | 1 | default satisfies them and planner behavior fails |
| `UNRESOLVED` | 1 | neither path satisfies enough constraints |

Structural difference from default is recorded as evidence only and is not the
oracle. The corpus covers simple/factual/technical/formatting FAST cases,
contradiction/agreement/evidence-update/material-ambiguity/social/tone/contextual
PLAN cases, cheap versus material ambiguity, delayed context, relevant versus
irrelevant tailoring context, stable self-concept, harmful planning, and hard
technical requests with obvious posture.

## Baselines and benchmark

All policies were evaluated on the same corpus. Binary precision/recall excludes
`PLAN_HARMFUL` and `UNRESOLVED`; those labels remain separate planner-quality
evidence.

| Policy | PLAN precision | PLAN recall | False PLAN | Missed PLAN_HELPFUL | Planner calls | Selected-path oracle match |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| `ALWAYS_FAST` | 100.0% | 0.0% | 0.0% | 100.0% | 0.0% | 52.6% |
| `ALWAYS_PLAN` | 47.4% | 100.0% | 100.0% | 0.0% | 100.0% | 47.4% |
| `deterministic_rules_v1` | 100.0% | 100.0% | 0.0% | 0.0% | 42.9% | 100.0% |

Confusion matrices are emitted by `evaluate_deliberation_policy()` for all four
oracle labels. The rule policy is selected as the opt-in SELECTIVE candidate:
it dominates ALWAYS_FAST on PLAN_HELPFUL cases and dominates ALWAYS_PLAN on
planner-call rate/latency exposure. The result is provisional because the
dataset is small and no learned semantic classifier is justified by the
current dependency/data boundary; that classifier is explicitly deferred.

False PLAN costs latency/tokens. Missed PLAN_HELPFUL costs behavioral/character
quality. The benchmark reports both separately and does not optimize raw
accuracy as a substitute for that asymmetry.

## Production choice and latency evidence

`SELECTIVE` is implemented and available only through explicit D1 composition.
The production default remains `DEFAULT_ONLY`; no default latency or
planner-call increase is claimed. `ALWAYS_PLAN` remains an explicit validation
mode.

D1 planner duration, response-generation start, and first-delta timing remain
available through the bounded evidence seam. Offline routing reports planner
invocation rate and selected-path utility. Live provider TTFT/planner latency
comparison is `UNVERIFIED` because no provider credentials or live generation
was used in this phase.

## Validation

- Preflight exact baseline and clean worktree: `PASS`.
- Core focused conversation/cognition tests: `PASS` — 26 passed.
- Runtime focused D2 tests: `PASS` — 9 passed.
- Runtime inherited D1 subset excluding the host teardown cases: `PASS` — 7
  passed, 2 deselected.
- Runtime Ruff check and format check: `PASS`.
- Runtime strict Pyright: `PASS`.
- Core full suite: `PASS` — 195 passed, 4 skipped.
- Shared Contracts suite: `PASS` — 7 passed; Discord adapter suite: `PASS` —
  95 passed.
- Architecture guard, docs integrity, lock checks, and `git diff --check`:
  `PASS`.
- Full Runtime suite: `FAIL` — under Python 3.14.7 it hangs during async
  teardown immediately after the inherited D1
  `test_superseded_prepared_run_cannot_start_stale_generation`; the D2 tests
  are not reached. A minimal standalone `asyncio.to_thread` reproduction also
  hangs on this host, so this is recorded as an environment/runtime validation
  limitation rather than a D2 routing failure.
- Live provider, live Discord, Windows, restart, and live latency: `UNVERIFIED`.

## Exit gate review

The explicit FAST/PLAN contract, exact planner call cardinality, quality-based
oracle, common baseline benchmark, cost-sensitive metrics, run-bound D1
fallbacks, and no-LLM-router/third-lane invariants are covered by deterministic
tests. SELECTIVE remains opt-in and the conservative production default is
unchanged. Final phase status and commit SHAs are recorded at closeout.
