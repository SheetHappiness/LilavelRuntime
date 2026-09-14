# PHASE COG-V1-C — Selective Disposition Cognition

Status: `CLOSED`
Baseline SHA: `624de802c5a5558c9c3c26236ffa94d402c700d5`
Implementation SHA: `1bde5c3`
Parent SHA: `624de802c5a5558c9c3c26236ffa94d402c700d5`

## Goal

Add an offline disposition layer after COG-V1-B `THINK`:

```text
THINK → FAST | DELIBERATE → RESPOND | NONE | INTERJECT
                         → WorkingState + ResponseDisposition
```

COG-V1-C does not generate assistant text, call tools, mutate `CharacterCanon`,
own `Attention`, own `SemanticActor` scheduling, or change canonical
production behavior. COG-V1-D owns production integration.

## Contracts

- `DispositionRoute` has exactly `FAST` and `DELIBERATE`.
- `DispositionRoutingEvidence` is immutable and contains only the eight
  required booleans.
- `DispositionContext` is bounded structured input: source, `THINK`, route
  evidence, and a trusted bounded case classification. It contains no raw
  reasoning, provider handle, or chain-of-thought.
- `CognitionPolicyDecision` remains the single policy output type from
  COG-V1-A. `WorkingState`, `ResponseDisposition`, and
  `InterventionDecision` are reused; no parallel contracts were introduced.
- `DeliberativeDispositionPolicy` is a provider-neutral structured protocol.
  `FakeDeliberativeDispositionPolicy` supplies deterministic test decisions and
  makes no network/provider calls.
- `DispositionResolution` carries the selected route, canonical decision, and
  bounded typed failure metadata when source-safe fallback was required.

## Exact routing rules

The router evaluates these conditions in order:

1. `material_ambiguity` → `DELIBERATE`.
2. `important_contradiction` → `DELIBERATE`.
3. `evidence_update` → `DELIBERATE`.
4. `high_social_stakes` → `DELIBERATE`.
5. `multiple_plausible_moves` → `DELIBERATE`.
6. `vulnerable_context` → `DELIBERATE`.
7. `intervention_uncertain` → `DELIBERATE`.
8. Otherwise → `FAST`.

`cheap_reversible_assumption` alone never selects `DELIBERATE`. There are no
scores, weights, probabilities, thresholds based on message size, or RNG.

## Source and failure invariants

- Direct `USER` always resolves to `RESPOND`; `NONE` and `INTERJECT` are
  rejected and direct policy failure falls back to safe FAST-like `RESPOND`.
- Ambient/NON_USER resolves only to `NONE` or `INTERJECT`; ambient `RESPOND`
  is rejected. Ambient policy failure falls back to `NONE` with
  `working_state=None` and `response_disposition=None`.
- `RESPOND` and `INTERJECT` require both `WorkingState` and
  `ResponseDisposition`. `NONE` requires no `ResponseDisposition`.
- COG-V1-C input and output are valid only after `AttentionDecision.THINK`.
- Reason codes are existing bounded enum values plus only the missing required
  values: `simple_request`, `high_social_stakes`,
  `multiple_plausible_moves`, `intervention_uncertain`, and
  `reversible_assumption`. Existing `ambiguity_material` is reused for
  material ambiguity. Arbitrary user text cannot become a reason code.
- Validation fails closed and records typed failure metadata rather than
  silently swallowing policy failure.

## Policy behavior

`DeterministicFastDispositionPolicy` is pure and provider-free. It returns the
exact direct-user default requested by the phase, specializes simple
definition/factual turns, straightforward technical explanations, ordinary
acknowledgments, clear playful turns, cheap reversible ambiguity, and genuine
tailoring discussion, and never performs additional semantic/model inference.

Question behavior is represented only by `ResponseDisposition.question_policy`:
cheap reversible ambiguity uses `avoid`; material ambiguity may use
`required`/`invite` on the deliberative path. Curiosity does not force a
question, and initiative remains independent from stance, interest, and humor.
Tailoring affinity is an explicit bounded context classification, not keyword
detection; affinity alone does not make ambient cognition interject.

Policy outputs are structured fields only. There is no response prose,
chain-of-thought, inner monologue, persona biography, or provider dependency.

## Evaluation corpus

- Scenario families: `13` total — `F01`–`F06` FAST and `D01`–`D07`
  DELIBERATE.
- Paired counterfactuals: `4` — direct versus ambient permission, cheap versus
  material ambiguity, subjective versus factual conflict, and irrelevant versus
  genuine tailoring context.
- Three-turn trajectories: `3` — evidence update, no repeated question, and
  ambient self-repair.
- Policy-field anti-caricature assertions cover forced humor/sarcasm,
  menswear references, therapy-speak, agreement/disagreement reflexes,
  unnecessary questions, joke explanation, persona biography, smug correction,
  and excessive initiative.

## Production boundary

The new layer is not imported or invoked by `production_cognition.py`,
`ConversationCore`, the Discord adapter, CLI composition, `SemanticActor`, or
COG-V1-B attention. No production provider call count or ambient interjection
behavior changes. The production integration seam is explicitly deferred to
COG-V1-D.

## Validation

Validation ran on Linux with Python 3.14.7. `UV_CACHE_DIR` only relocated the
tool cache because the shared home cache is read-only; lockfiles and commands
were unchanged.

- Root, Contracts, Core, Runtime, and Discord lock checks: `PASS`.
- Core Ruff check, format check, strict Pyright: `PASS`.
- Focused COG-V1-C tests: `PASS` — 18 passed.
- Full Core tests: `PASS` — 210 passed, 4 platform skips.
- Contracts Ruff, format check, strict Pyright, and tests: `PASS` — 7 passed.
- Focused COG-V1-B attention tests: `PASS` — 17 passed.
- Runtime Ruff, format check, strict Pyright: `PASS`.
- Runtime residual tests excluding the inherited timing-sensitive test:
  `PASS` — 220 passed, 1 deselected.
- Full Runtime tests: `BLOCKED` —
  `test_temporal_non_user_work_waits_behind_active_user_conversation` did not
  settle. The exact test timed out in an archived clean baseline copy, so it is
  pre-existing and was not weakened or deleted.
- Discord adapter Ruff, format check, strict Pyright, and tests: `PASS` — 95
  passed, 10 existing deprecation warnings.
- Docs integrity, architecture guard, and `git diff --check`: `PASS` before
  this closeout record; final closeout recheck is required after staging it.
- Live provider behavior, live Discord delivery, Windows behavior, and
  restart/durable guarantees: `UNVERIFIED`; no credentials or live provider
  calls were used.

## Exit gate

`PASS`: FAST/DELIBERATE, exact routing, zero-inference FAST, provider-neutral
DELIBERATE, direct-user RESPOND, ambient NONE/INTERJECT, source-safe fallbacks,
canonical policy contracts, fail-closed validation, curiosity/initiative
separation, non-keyword interest handling, paired and trajectory evals, and
unchanged production composition are implemented and covered. COG-V1-D owns
the future production integration decision.
