# PROACTIVE-V0 — Discord One-Shot Idle Initiative Smoke

## Status

**PASS** for the task-scoped deterministic smoke gate. Live Discord/provider
verification remains `UNVERIFIED`.

## Baseline and commits

- Expected baseline: `3b0034be999235b0bbf4fe3b8f6d0744f68abe6`
- Preflight HEAD: `3b0034be999235b0bbf4fe3b8f6d0744f68abe6`
- Implementation: `0b34afb2f753dff7f4daa2f85f79ecc1b841333c`
- Closeout: documentation commit follows the implementation commit
- Push: not performed

The preflight worktree was clean and the completed AWARE-V1-D tree was
present. The implementation commit contains only the bounded proactive
composition, the existing-runtime seams needed by it, and deterministic
Discord smoke tests.

## Configuration and canonical path

```text
LILAVEL_DISCORD_PROACTIVE_SMOKE=0|1       default 0
LILAVEL_DISCORD_PROACTIVE_IDLE_S=<float>  default 30.0; range 1.0..600.0
```

```text
successful USER completion
  → APPRAISAL_REASON
  → runtime-owned MindState intention
  → one-shot idle opportunity
  → SemanticActor(NON_USER)
  → IDLE_REASON cognition
  → ProposalApplicationCoordinator
  → DeterministicInterventionPolicy
  → trusted text-only Discord application tool
  → bound one-to-one DM
```

The first eligible one-to-one DM binds one opaque subject and adapter-owned
channel. A second distinct subject disables proactive speech for the process
and cancels pending work; normal reactive DM routing remains operational. No
Discord identifier enters a trigger, prompt, `MindState`, context, or Core
history. Proactive speech is not a canonical conversation turn.

## Decisions and safety

- The existing `ConversationCore` completion event is the only appraisal
  admission point; failed, cancelled, superseded, and partial USER turns do
  not appraise.
- The strict existing appraisal parser creates at most the existing
  `MindState` intention proposal. No intention means no idle timer or model
  call.
- One timer and a monotonic generation/USER epoch fence prevent stale or
  duplicate expiry. USER work is checked at expiry and always has priority.
- Idle cognition uses the existing internal source-ref shape:
  `idle:<opportunity-id>` followed by the exact intention ID.
- The model supplies only semantic speech text or silence. The trusted
  application binding supplies the target, and the existing effect-time social
  guard supplies permission. `ToolEffect.CONFIRMED` is the only successful
  proactive speech accounting event; unknown delivery is not retried.
- There is no new semantic actor, lane, action kind, planner, scheduler,
  memory write, persistence, restart recovery, guild/group-DM path, or
  canonical-history append.

## Bounded evidence

The adapter exposes content-free lifecycle categories for the smoke experiment:

```text
target_bound
target_disabled_multiple_subjects
appraisal_started / appraisal_completed
intention_present / intention_absent
idle_armed / idle_cancelled_by_user / idle_expired / idle_ineligible
cognition_submitted
speech_denied / silence
send_confirmed / send_failed / send_unknown
```

Evidence retains no user text, generated speech, Discord IDs, token/provider
content, or exception bodies.

## Validation

| Check | Result |
| --- | --- |
| Baseline SHA and clean preflight worktree | **PASS** |
| Discord lock check | **PASS** |
| Runtime/Core/Contracts lock checks | **PASS** |
| Discord Ruff, format, strict Pyright | **PASS** |
| Runtime Ruff, format, strict Pyright | **PASS** |
| Core Ruff, format, strict Pyright | **PASS** |
| Contracts Ruff, format, strict Pyright | **PASS** |
| Focused proactive smoke tests | **PASS** — 20 passed |
| Full Discord adapter suite | **PASS** — 114 passed, 10 existing deprecation warnings |
| Focused MIND/COG/intervention regressions | **PASS** — 59 passed |
| AWARE/CTX residual runtime coverage | **PASS** — included in 372-test residual runtime run |
| Residual full runtime suite excluding documented host-stall modules | **PASS** — 372 passed |
| Full Core suite | **PASS** — 195 passed, 4 skipped |
| Full Contracts suite | **PASS** — 7 passed |
| Architecture guard | **PASS** |
| Documentation integrity | **PASS** |
| `git diff --check` | **PASS** |
| Full Runtime command | **FAIL** — documented Linux/Python 3.14 async-teardown stall recurred before completion; no new assertion failure was reported |
| Live Discord/provider verification | **UNVERIFIED** — no credentials or live service used |

The full runtime command was started against the repository-authoritative
suite and stalled at the pre-existing timing-sensitive teardown documented in
`docs/VALIDATION.md`. The residual command excluding the two affected modules
completed successfully, and the focused runtime suites covering the modified
MIND/COG/intervention seams passed.

## Exit gate

**PASS.** Proactive behavior is explicit opt-in, default Discord routing is
unchanged, one live intention gates one bounded idle attempt, cognition and
effects reuse the existing actor/application/guard architecture, silence is a
valid terminal outcome, destination authority remains trusted application
state, stale USER work is fenced, and no recurring or canonical-history path
was added.
