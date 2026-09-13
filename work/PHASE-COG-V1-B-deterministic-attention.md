# PHASE COG-V1-B — Deterministic Attention Policy

Status: `CLOSED`
Implementation SHA: `edef390`
Parent SHA: `ab2be655f3738cc688958b010e51e64d2f90b7fe`

## Goal

Add a deterministic runtime attention layer for admitted ambient and
non-user observations. Attention answers only whether an observation merits
cognition:

```text
Observation → trusted evidence → ordered attention policy → DROP | NOTE | THINK
```

Attention is not intervention and is not disposition. This phase does not
decide whether Lilavel speaks, interrupts, or how Lilavel responds.

## Implementation

- Added immutable, bounded `AttentionSignalProfile`, `AttentionEvidence`, and
  `AttentionVerdict` values in `apps/runtime/src/lilavel_runtime/attention.py`.
- Added `AttentionEvidenceExtractor`, which maps exact runtime-owned event
  route kinds to trusted profiles and never reads `WorldEvent.payload` or uses
  payload claims as authority.
- Added `DeterministicAttentionPolicy` with ordered boolean rules. It has no
  float score, weighted threshold, random jitter, model call, cooldown, quiet
  hours, conversation-floor, or interruption logic.
- Added `DeterministicAttentionCognitionGate` behind the existing MIND-1B
  `CognitionGate` seam. It emits `NO_COGNITION` for `DROP` and `NOTE`, and
  creates a bounded `CognitionTrigger` only for `THINK` observations.
- Preserved `DirectMessageCognitionGate` as the compatibility/specialized
  direct-message gate. The default runtime gate retains direct USER behavior.
- Ambient `THINK` observations use the existing `MindExecutionAdapter` path
  at `NON_USER` priority. No semantic/model lane or action/effect path was
  added.

## Evidence and trust boundary

The supported evidence fields are direct address, critical event, continuity,
relevance, novelty, interest affinity, repetition, and no-new-value. Unknown
or unsupported event kinds receive absent/unknown evidence and conservatively
produce `NOTE`.

The default direct-message route is a runtime-known route and mints
`DIRECT_ADDRESS` from its event kind. Custom profiles are an explicit runtime
composition seam for adapter-owned route semantics. The extractor does not
inspect event payload text, `EventTrust`, or payload fields such as
`direct_address`, `critical_event`, `relevance`, or `interest_affinity`.

Interest affinity is supported only as a bounded evidence seam. Affinity alone
produces `NOTE`; no clothing, tailoring, menswear, or other keyword rule can
mint affinity.

## Ordered policy rules

Rules are evaluated in this order:

1. `direct_address` or `critical_event` or `continuity` → `THINK`.
2. `HIGH relevance` plus `HIGH novelty` → `THINK`.
3. `LOW relevance` → `DROP`.
4. `repetition` plus `no_new_value` → `DROP`.
5. Everything else, including `HIGH relevance` alone, `HIGH novelty` alone,
   and interest affinity alone → `NOTE`.

Hard signals are evaluated before suppressors. Social permission and
intervention concerns are intentionally absent; a later intervention policy
may turn a cognition result into `NONE`.

## MIND-1B integration

The existing boundary remains:

```text
ObservationReceipt → CognitionGate.decide()
  DROP/NOTE → NO_COGNITION → no trigger, no model/Core/action call
  THINK     → CognitionTrigger
                ├─ direct_message → existing USER/Core route
                └─ ambient        → existing NON_USER/MIND route
```

The trigger contains only bounded observation IDs and a bounded reason. In a
batch, only observations whose own verdict is `THINK` are selected; `NOTE` and
`DROP` observations are never pulled into a trigger because another item
thinks.

`NOTE` means noticed but no cognition episode now. It is not memory,
relationship state, a deferred instruction, or a persistent digest. This
phase adds no NOTE persistence.

## Tests and validation

`apps/runtime/tests/test_attention.py` covers 17 focused tests for direct
address, critical events, high relevance/novelty, low relevance, repetition,
unknown context, interest affinity, keyword anti-caricature, payload forgery,
determinism, malformed evidence, bounded reasons, mixed batches, direct-user
invariance, zero cognition calls for `NOTE`/`DROP`, and the existing MIND route
for ambient `THINK`.

Validation was executed on Linux with Python 3.14.7. The `UV_CACHE_DIR` and
`RUFF_CACHE_DIR` overrides only relocated tool caches because the shared
environment cache is read-only; dependency graphs and locked commands were
unchanged.

- Runtime lock check, Ruff check, Ruff format check, and strict Pyright:
  `PASS`.
- Focused COG-V1-B tests (`tests/test_attention.py`): `PASS` — 17 passed.
- Remaining runtime tests excluding the unrelated timing-sensitive
  `test_temporal_non_user_work_waits_behind_active_user_conversation`:
  `PASS` — 220 passed, 1 deselected.
- Full runtime pytest: `FAIL` — the named pre-existing conversation timing
  test did not settle within the validation timeout. The hang reproduced with
  an explicitly injected legacy `DirectMessageCognitionGate`; no attention
  test or attention code path was involved.
- Core lock/Ruff/format/Pyright/pytest: `PASS` — 192 passed, 4 skipped.
- Contracts lock/Ruff/format/Pyright/pytest: `PASS` — 7 passed.
- Discord adapter lock/Ruff/format/Pyright/pytest: `PASS` — 95 passed,
  11 warnings (10 Discord deprecation warnings and one read-only pytest cache
  warning).
- Root lock check and launcher help: `PASS`.
- Docs integrity, architecture guard, and `git diff --check`: `PASS`.
- Live provider/Discord behavior, Windows behavior, durable recovery, and
  restart-safe idempotency: `UNVERIFIED`; no credentials or provider calls
  were used.

## Non-goals and unknowns

Intervention production policy, response disposition, affect, social floor,
quiet hours, cooldowns, memory, relationship state, embeddings, provider
calls, Twitch-specific heuristics, voice barge-in, and user-to-user steering
remain out of scope. No current production adapter establishes semantic
relevance, novelty, or interest affinity beyond configured trusted route
profiles; those values remain `UNKNOWN` unless a runtime-owned route mints
them. Live provider/Discord behavior and cross-process idempotency remain
`UNVERIFIED`.

## Exit gate

COG-V1-B implementation passes when ambient observations can deterministically
produce all three attention verdicts, only `THINK` crosses the existing trigger seam,
`NOTE`/`DROP` have zero cognition calls, payloads cannot self-promote,
interest affinity is not keyword-driven or compulsory, mixed batches remain
per-observation, and the existing SemanticActor/MIND/Core ownership boundaries
remain intact.
