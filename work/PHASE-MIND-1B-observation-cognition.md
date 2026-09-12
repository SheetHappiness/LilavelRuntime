# PHASE MIND-1B — Observation is not cognition

Status: `CLOSED`
Baseline SHA: `2cf71ba5510142f00b8360f6c0f4e6b81ef02428`
Implementation SHA at exit: `2de50592727a45527bbf9a46b224385ed44fe695`
  (implementation tree before the final phase-record metadata amendment)

## Goal

Add a deterministic runtime cognition gate without changing observation
admission semantics or the existing direct-message experience.

## Scope

- Added provider-neutral `CognitionTrigger`, `CognitionDecision`, and
  `NO_COGNITION` contracts.
- Added the runtime-owned `CognitionGate` seam and bounded deterministic
  `DirectMessageCognitionGate`.
- Added the explicit `cognition_step()` gate-and-route operation while keeping
  `reactive_step()` as the compatibility alias used by the Discord adapter.
- Proved negative, positive, and bounded ordered-batch outcomes.
- Updated current architecture, validation, scoped README, ADR, and phase
  records.

## Non-goals

No model-based salience, personality interest, menswear fixation, proactive
autonomy, scheduler/timers, memory/retrieval, relationships, social backoff,
dynamic response disposition, action selection, or new guild/ambient behavior.

## Decisions

- `USE` a runtime-owned gate that returns exactly `NO_COGNITION` or a bounded
  `CognitionTrigger`.
- `USE` only the existing `direct_message` event kind for the first policy;
  unsupported kinds return `NO_COGNITION`.
- `USE` observation IDs as trigger evidence references; external payloads and
  Discord-specific types remain outside the cognition contract.
- `USE` explicit ordered-batch coalescing in the policy only, with no timer or
  scheduler semantics. The compatibility runtime step remains singleton.

## Architecture consequences

The current runtime sequence is:

```text
WorldEvent → admit_observation() → ObservationReceipt
                                  → cognition_step(receipt)
                                      ├── NO_COGNITION → STOP
                                      └── CognitionTrigger
                                          → explicit reactive/Core route
```

Admission does not run the gate. A negative cognition decision creates no Core
session, model runtime, model call, conversation mutation, or action. A
positive trigger only permits the existing explicit route; it does not select
or authorize an action.

## Inherited invariants

World-event payloads remain deeply frozen and untrusted. `ConversationCore`
remains the owner of canonical history and assistant commit semantics. Discord
identifiers remain adapter-local. Existing provider lifecycle, tool
authorization, effect certainty, and shutdown boundaries are unchanged.

## Evidence

- `apps/runtime/tests/test_contracts.py` proves the explicit negative outcome,
  provider-neutral trigger, and bounded ordered coalescing.
- `apps/runtime/tests/test_kernel.py` proves admission does not invoke the
  gate, `NO_COGNITION` has zero model/Core/action side effects, and a direct
  message produces one trigger only on the explicit next step.
- `apps/discord-adapter/tests/test_edge.py` exercises the preserved DM
  adapter-to-runtime compatibility route.

## Validation

- Runtime lock check, Ruff check/format check, Pyright, and Pytest: `PASS` —
  97 deterministic tests.
- Discord adapter lock check, Ruff check/format check, Pyright, and Pytest:
  `PASS` — 95 deterministic tests, 10 existing deprecation warnings.
- Repository architecture guard, Markdown integrity guard, and
  `git diff --check`: `PASS`.
- Live provider and Discord probes: `UNVERIFIED` — not required and no
  credentials were accessed.

## Unknowns

Live provider and live Discord behavior remain `UNVERIFIED`. Production tuning
of trigger grouping and support for event classes beyond the existing direct
message remain deferred.

## Exit gate

All relevant deterministic checks pass, the negative and positive paths remain
mechanically distinct, trust/provenance boundaries remain intact, and the final
commit contains only MIND-1B changes. The final metadata amendment preserves
the implementation SHA above as the pre-amendment implementation tree.
