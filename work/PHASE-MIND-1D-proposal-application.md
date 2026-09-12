# MIND-1D — Proposal application boundary

## Status

Implemented on 2026-09-12.

## Objective

Establish the trusted boundary that converts completed MIND-1C inert proposals
into runtime-owned local state changes and authorized external effects while
preserving:

```text
Proposal != Effect
```

## Implementation

- Added `ProposalApplicationCoordinator` as a separate runtime-owned
  application seam. `CognitionEpisodeRunner` remains effect-free.
- Added private completion proof, trusted outcome scope, and captured
  `based_on_state_version` provenance. Failed, cancelled, timed-out, malformed,
  or manually constructed unproved outcomes are ineligible.
- Added narrow runtime-owned `MindStateDelta` commands and atomic, version
  fenced state batches. MIND-1D requires application-supplied Core provenance;
  proposals cannot invent message IDs or actor/scope authority.
- Added application-side action compilation from proposal kind/content to
  trusted canonical tool calls. Existing P4 registry, exposure, validation,
  authorization, liveness, executor, settlement, and effect certainty remain
  authoritative.
- Added deterministic application IDs, stable action call IDs, and a bounded
  fence ledger. Duplicate, failed, partial, and unknown-effect applications
  cannot be automatically repeated.
- Kept state and external-action statuses/evidence separate, with state first,
  deterministic action order, and honest partial-application reporting. No
  rollback of external effects is claimed.
- Kept the current reactive DM/Core route unchanged and added no conversation
  history or hidden cognition-text commit path.

## Decisions

- Multiple state proposals are validated and committed as one all-or-nothing
  local `MindState` batch. The state version increases once per committed
  delta.
- Structural validation of the complete mixed proposal set happens before
  state mutation or P4 session creation. Dynamic P4 authorization and liveness
  remain authoritative at execution time.
- State is applied before external actions, but local state and external
  effects are not globally atomic. Confirmed or unknown external effects are
  never rolled back.
- MIND-1E remains reserved for temporal wake proposals and scheduler
  admission; it is not introduced here.

## Deterministic evidence

`apps/runtime/tests/test_proposal_application.py` proves:

- A — valid typed state proposal becomes one trusted state application and
  deterministically increments the version.
- B — stale state proposal fails closed with zero mutation.
- C — valid action compiles to one trusted P4 call only at application time,
  with result and effect certainty recorded.
- D — denied and schema-invalid actions stop before an executor/effect.
- E — `effect=unknown` is preserved and not retried.
- F — duplicate outcome application is fenced before a second external attempt.
- G — invalid mixed batches are rejected before state or effectful execution.
- H — confirmed first action plus later failure is represented as partial,
  without a rollback claim.
- I — state/action application does not write ConversationCore history.
- J — MIND-1C still returns inert proposals without state or action effects.

## Validation

Executed on Linux with CPython 3.14.7:

- Runtime locked Ruff check: `PASS`.
- Runtime locked Ruff format check: `PASS`.
- Runtime strict Pyright: `PASS`.
- Runtime locked pytest: `PASS` — 121 tests.
- Focused MIND-1D pytest: `PASS` — 13 tests.
- Contracts locked Ruff, format, Pyright, and pytest: `PASS` — 7 tests.
- Core locked Ruff, format, Pyright, and pytest: `PASS` — 180 passed, 4
  platform skips.
- Discord adapter locked Ruff, format, Pyright, and pytest: `PASS` — 95
  tests, 10 existing deprecation warnings.
- Root sync, lock check, and `lilavel --help`: `PASS`.
- Contracts, runtime, Core, and Discord lock checks: `PASS`.
- Repository docs integrity, architecture guard, and `git diff --check`:
  `PASS`.
- Live provider, live Discord, restart persistence, and Windows-specific
  behavior: `UNVERIFIED`; not required and no credentials were accessed.

## Remote status

At preflight, local `origin/main` resolved to the MIND-1C closeout
`3a88ba113cf5e05a945454ed8514671370a5dedb` and was equal to local `main`.
Fresh remote verification remained `BLOCKED` because DNS could not resolve
`github.com`. The implementation was committed locally and was not pushed.

## Exit gate

MIND-1D owns the trusted application boundary. MIND-1C ends at inert proposals;
MIND-1E remains deferred for temporal wake/scheduler work. Implementation SHA:
`4db030cb2520c77476bdbcdf29cfabcbc0a0b7d7`.
