# PHASE COG-V1-E1 — Ambient Intervention Contracts, Social Permission & Evals

Status: `PASS`

Baseline SHA: `c73f95fa81fdb74b5018fa1d1437b7b67a379d54`

Parent SHA: `c73f95fa81fdb74b5018fa1d1437b7b67a379d54`

Implementation SHA: `164224e57dfa6082a3c32c9382e8697735ca1973`

## Goal

Establish the ambient cognition layer that answers whether a semantically
valid cognition candidate may become external speech now, without adding
autonomous speech or changing the direct USER route.

The implemented separation is:

```text
Attention     is this worth cognition?          DROP | NOTE | THINK
Intervention  should cognition become speech?   NONE | RESPOND | INTERJECT
Disposition   how should Lilavel speak?         existing Core HOW contract
```

Thinking creates a candidate; current runtime-owned social permission decides
whether that candidate remains eligible to become speech. Silence is the
baseline. A `NONE` intervention does not invalidate internal state,
intention, or temporal proposals.

## Implemented contracts

Runtime adds `intervention.py` with:

- `InterventionCandidate`, which reuses Core's existing
  `InterventionDecision` and carries only a bounded semantic-value enum plus
  bounded `CognitionReasonCode` values. It contains no raw user/planner text,
  prose, model handle, action, or presentation authority.
- `SocialPermissionContext`, also available through the single alias
  `InterventionContext`, containing only trusted runtime-owned bounded signals:
  semantic priority/source, speaking-surface availability, per-class
  freshness bucket, current activity, optional floor state, recent speech,
  intervention budget, unresolved/handled state, social sensitivity, and
  independently established response continuity/obligation.
- `SocialPermissionResult`, which records the candidate decision, the
  revalidated allowed decision, permission boolean, and bounded reason codes.
- `DeterministicInterventionPolicy.evaluate()` / `revalidate()` / `decide()`.
  `revalidate()` is the explicit future E2 effect-time seam; it does not
  mutate state or emit an effect.

`InterventionDecision` remains the one Core enum. E1 has no `INTERRUPT` value:
voice/VAD/barge-in floor timing is a separate future domain.

The ordered policy is:

1. Exclude direct USER priority/source; direct USER remains fixed THINK plus
   RESPOND in the existing conversation route.
2. Deny current hard conditions: unavailable surface, busy trusted floor,
   non-current activity, stale freshness, or exhausted intervention budget.
3. Allow `RESPOND` only when trusted current response obligation or continuity
   exists. Recent speech does not suppress this obligation path.
4. Allow `INTERJECT` only for a fresh, current, unresolved, ordinary-sensitivity
   material contribution with no recent-speech backoff and explicit supporting
   semantic evidence. Interest affinity, a witty thought, or high relevance
   alone is insufficient.
5. Return `NONE` otherwise.

Freshness is a reviewable per-class mapping of `FRESH`, `AGING`, and `STALE`
 buckets, not a universal timeout. Default continuity/temporal profiles allow
 an aging `RESPOND` opportunity while unsolicited `INTERJECT` still requires
 `FRESH`; reactive/internal profiles require `FRESH` for both. The caller that
 owns the event route is responsible for minting the bucket. E1 does not add a
 wall-clock scheduler.

Recent speech and the bounded intervention budget are social-permission
controls, not Attention controls. A recent speech state suppresses only
unsolicited `INTERJECT`; an independently trusted `RESPOND` can still pass.
The budget is a hard denial for both speaking outcomes. No RNG, weighted
salience score, cooldown timer, keyword personality trigger, or model call is
used.

## Reason codes

Existing vocabulary is reused for semantic evidence and interruption cost.
The only additions to Core's bounded `CognitionReasonCode` enum are:

- `RESPONSE_OBLIGATION`, `NO_RESPONSE_OBLIGATION`
- `STALE_CONTEXT`, `SOCIAL_BACKOFF`, `FLOOR_BUSY`,
  `NO_SPEAKING_SURFACE`, `INTERVENTION_BUDGET_EXHAUSTED`, `ALREADY_HANDLED`
- `CONSTRAINT_FORGOTTEN`, `DISCUSSION_STUCK`, `UNIQUE_INFORMATION`

These codes are needed by the E1 denial and counterfactual cases. No raw
reason text is accepted.

## Eval corpus and metrics

`COG_V1_E1_SCENARIOS` contains 30 human-authored deterministic scenarios:

- 29 evaluable ambient cases and one explicitly excluded direct USER case;
- 18 expected `NONE`, 3 expected `RESPOND`, and 8 expected `INTERJECT` cases;
- all required families: think-without-speaking, trusted follow-up,
  material contradiction, harmless difference, forgotten/handled constraint,
  stuck/progress discussion, unique information, interest-only and keyword
  traps, witty thought, vulnerable moment, recent speech, freshness,
  no-surface, busy floor, response-vs-interject backoff, budget exhaustion,
  delayed continuity, and critical-event backoff documentation;
- seven matched counterfactual groups covering response obligation, semantic
  materiality, handled state, recent speech, freshness, lexical handling, and
  floor state.

The evaluator reports a full `NONE`/`RESPOND`/`INTERJECT` confusion matrix,
`INTERJECT` precision and recall, `RESPOND` recall, false `INTERJECT` count
and rate, silence-preservation rate, hard social violations, and intervention
rate. It also includes an explicit `ALWAYS_NONE` baseline. It does not use
LLM-as-judge or raw accuracy as the acceptance criterion.

The deterministic policy benchmark is:

| Policy | INTERJECT precision | INTERJECT recall | RESPOND recall | False INTERJECT | Silence preservation | Hard violations | Intervention rate |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| `deterministic_intervention_v1` | 100.0% | 100.0% | 100.0% | 0 | 100.0% | 0 | 37.9% |
| `always_none` | 0.0% | 0.0% | 0.0% | 0 | 100.0% | 0 | 0.0% |

The confusion matrix for the deterministic policy is:

| Expected \\ Predicted | NONE | RESPOND | INTERJECT |
| --- | ---: | ---: | ---: |
| NONE | 18 | 0 | 0 |
| RESPOND | 0 | 3 | 0 |
| INTERJECT | 0 | 0 | 8 |

These are small repository-local contract results, not live-provider or live
environment evidence.

## Architecture and security consequences

Attention remains limited to `DROP`/`NOTE`/`THINK`; no social timing, floor,
backoff, or budget logic was added there. Disposition remains a separate HOW
layer. Direct USER FAST/PLAN/D1 behavior is untouched. E1 does not call
`LocalCognitionEngine`, `DispositionPlanner`, `ConversationCore`, or any
provider/runtime generation seam.

`CognitionEpisodeRunner` remains effect-free and proposal application remains
owned by `ProposalApplicationCoordinator`. E1 does not create a presentation
action, speak to Discord/CLI, modify `CharacterCanon`, add a semantic lane,
or change NON_USER proposal application authority. There is no production
ambient external speech wiring in this phase.

External payloads cannot mint response obligation, floor state, criticality,
unresolved state, or freshness. The context is runtime-owned and the
candidate is advisory. E1 deliberately does not support an urgent/critical
backoff bypass because the current repository has no independent urgency
authority beyond attention evidence. Such a bypass remains future work.

## Validation

Executed on Linux with Python `3.14.7`:

- Baseline/ancestry and clean worktree before editing: `PASS` — `HEAD` was
  exactly `c73f95f…`, parent `c34743b…`, and `main` matched `origin/main`.
- Runtime focused E1 tests: `PASS` — 22 passed.
- Runtime Ruff check: `PASS`.
- Runtime strict Pyright: `PASS`.
- Architecture guard: `PASS`.
- Docs integrity: `PASS`.
- `git diff --check`: `PASS`.
- Core sync/lock, Ruff check/format, strict Pyright, focused cognition tests,
  and full Core pytest: `PASS` — 195 passed, 4 platform skips.
- Shared Contracts sync/lock, Ruff check/format, strict Pyright, and full
  pytest: `PASS` — 7 passed.
- Discord adapter sync/lock, Ruff check/format, strict Pyright, and full
  pytest: `PASS` — 95 passed, 11 deprecation warnings.
- Runtime sync/lock, Ruff check/format, strict Pyright, focused E1 tests,
  and focused D2 regression tests: `PASS` — 22 E1 tests and 9 D2 tests.
- Runtime residual suite with the three observed timing-sensitive tests
  deselected: `PASS` — 276 passed, 3 deselected.
- Root sync/lock, launcher help, architecture guard, docs integrity, and
  `git diff --check`: `PASS`.
- Full Runtime pytest: `FAIL` — the current Python 3.14.7 host stalls during
  async teardown after `test_superseded_prepared_run_cannot_start_stale_generation`.
  In isolation, after that test is deselected the same teardown behavior is
  observed after `test_actor_cancellation_contains_planner_and_starts_no_response`;
  after both are deselected, the existing
  `test_temporal_non_user_work_waits_behind_active_user_conversation` also
  stalls. This is current evidence, not a historical-only classification.
- Live provider, live Discord, Windows, restart, and live latency behavior:
  `UNVERIFIED`.

The full Runtime limitation is an inherited host/runtime teardown issue and
does not touch the E1 module; E1 and D2 focused tests and the residual Runtime
suite pass. No required check was `BLOCKED`.

## Non-goals and exit review

E1 does not implement production ambient externalization, voice `INTERRUPT`,
VAD/barge-in, millisecond floor timing, NOTE digests, memory/retrieval,
affect, relationships, OperatingCanon/ContextFrame, timers, stochastic
initiative, learned intervention classification, model calls, or an
LLM-as-judge oracle.

The exit gate is satisfied by the typed three-way contract, direct USER
exclusion, deterministic current-state revalidation, stale denial, stricter
unsolicited backoff, human-authored counterfactual corpus, intervention
metrics, silence baseline, and preserved authority/lifecycle boundaries. The
production ambient speaking capability remains disabled.
