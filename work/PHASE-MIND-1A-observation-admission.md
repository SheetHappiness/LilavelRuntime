# PHASE MIND-1A — Observation admission

Status: `CLOSED`
Baseline SHA: `3e349be770823d6f4080247b08a1164155ff319d`
Implementation SHA at exit: `7973cc4` (implementation tree before the final
phase-record metadata amendment)

## Goal

Establish `observation != response` at the runtime boundary while preserving
the existing explicit DM conversation experience.

## Scope

- Added provider-neutral `Observation`, `ObservationReceipt`, and bounded
  in-memory `ObservationWindow` contracts in `apps/runtime`.
- Made `LilavelRuntime.admit_observation()` (and adapter-facing `submit()`)
  admission-only, with bounded ingress and recent-observation retention.
- Added the explicit runtime `reactive_step()` and made the Core router accept
  only admitted observations.
- Routed Discord DM admission through the receipt before scheduling the legacy
  reactive Core/model/presentation path.
- Added deterministic observation-only and explicit-reactive proofs.

## Non-goals

No scheduler, wake policy, salience, model-based attention, cognition episode,
memory/retrieval, relationships, guild/social behavior, proactive Discord
behavior, dynamic cognition, or durable observation store was added.

## Decisions

- `USE` an immutable runtime `Observation` that wraps the existing immutable
  provider-neutral `WorldEvent` without promoting its untrusted payload.
- `USE` a finite recent window that evicts its oldest transient entry at
  capacity; receipts do not establish durable availability.
- `USE` an explicit reactive response step after admission. It is the only
  MIND-1A path allowed to invoke the existing Core router.
- `REJECT` Discord-specific types in runtime contracts and `REJECT` any
  automatic wake/cognition behavior in admission.

## Architecture consequences

The runtime now has a mechanically visible sequence:

```text
WorldEvent → admit_observation() → Observation/Receipt
                                  → explicit reactive_step() → ConversationCore
```

Admission alone creates no Core session, canonical conversation message,
model generation, tool call, scheduler work, or wake decision. Discord remains
an adapter that converts provider events and explicitly requests the legacy
response step after receipt.

## Inherited invariants

`ConversationCore` remains the sole owner of canonical conversation history and
assistant commit semantics. World-event payloads remain deeply frozen and
untrusted. Discord IDs remain adapter-local. Model/provider lifecycle,
application tool authorization, effect certainty, and joined settlement remain
unchanged.

## Evidence

- `apps/runtime/tests/test_contracts.py` proves distinct immutable event,
  observation, receipt, and bounded-window semantics.
- `apps/runtime/tests/test_kernel.py` proves admission-only behavior leaves the
  real Core router without a session/model runtime and proves routing starts
  only after an explicit receipt-based step.
- `apps/discord-adapter/tests/test_edge.py` proves a DM is counted as admitted
  before the legacy fake model generation and preserves the existing
  Core-backed presentation behavior.

## Validation

- Runtime locked Ruff check, format check, Pyright, and Pytest: `PASS` — 94
  tests.
- Discord adapter locked Ruff check, format check, Pyright, and Pytest:
  `PASS` — 95 tests, 10 existing deprecation warnings.
- Repository architecture guard, Markdown integrity guard, and
  `git diff --check`: `PASS`.
- Live provider and Discord probes: `UNVERIFIED` — not required and no
  credentials were accessed.

## Unknowns

Live provider/Discord behavior and production tuning of the transient window
remain `UNKNOWN`. Receipt use after window eviction is intentionally rejected;
durable observation continuity is deferred.

## Exit gate

All relevant deterministic checks pass, admission-only and explicit-reactive
scenarios are covered, trust/provenance boundaries remain intact, and one
clean task commit contains only MIND-1A changes.
