# MIND-1E — Temporal intentions / self-wake

## Status

Implemented on 2026-09-12.

## Objective

Add runtime-owned one-shot temporal intention semantics so:

```text
time → cognition opportunity
```

does not become:

```text
time → external action
```

## Implementation

- Added typed inert `TemporalProposal` values to MIND-1C candidates and
  completed outcomes.
- Added `TemporalCoordinator` with an injectable timezone-aware clock,
  runtime-owned UTC deadline normalization, minimum-delay and horizon clamps,
  bounded pending wakes, deterministic deduplication, cancellation/
  supersession, ordered due polling, and one-shot dispatch fencing.
- Extended `CognitionTrigger` with a trusted source kind and bounded source
  references. Temporal triggers carry no observation payload and converge on
  the existing serialized `CognitionEpisodeRunner`.
- Extended MIND-1D application evidence with temporal settlement. Temporal
  application remains front-loaded and inert until trusted coordinator
  admission; state/action application is not performed when temporal
  validation rejects the complete mixed outcome.
- Replaced the async application bridge's implicit `asyncio.to_thread` use
  with a runtime-owned non-daemon thread and non-blocking completion polling.
  This preserves the existing synchronous application and cancellation
  settlement boundary without depending on the event loop's default executor
  teardown.
- Preserved the existing reactive DM/Core path and P4 action boundary.
- Added no background loop, recurring schedule, Discord action, Core history
  write, model-selected tool authority, or chain-of-thought persistence.

## Policy

- Minimum delay: one second.
- Maximum horizon: seven days.
- Maximum pending wakes per scope: eight.
- Equivalent pending requests deduplicate by scope, normalized reason, and
  optional intention reference; the first deadline wins.
- Past requests clamp to the minimum delay. Requests beyond the horizon clamp
  to the maximum horizon. Malformed/unsupported values reject without a wake.
- `DISPATCHED` is a one-shot fence. Later cognition failure/cancellation does
  not redispatch the same wake.

## Persistence

Accepted `WakeIntent` records are in-memory only. Restart recovery is
`UNVERIFIED`/deferred because adding durable agent-state persistence would
materially broaden this phase beyond the existing runtime seams. Pending wakes
therefore do not survive process restart, and no restart-safe claim is made.

## Deterministic proofs

`apps/runtime/tests/test_temporal.py` contains ten focused proofs covering
future creation, invalid/clamped input, cognition-only due dispatch, duplicate
fencing, serialization, cancellation, deduplication, one-shot failure,
provenance isolation, and preservation of the inert pre-application boundary.
`apps/runtime/tests/test_proposal_application.py` also covers async
application cancellation waiting for sync settlement.

## Validation

- MIND-1E focused pytest: `PASS` — 10 tests.
- Pre-fix runtime regression subset excluding the two async P4 application
  tests: `PASS` — 129 passed, 2 deselected.
- Full runtime pytest: `PASS` — 132 passed in 8.95 seconds. The prior hang was
  traced to the host sandbox denying `send()` on asyncio's cross-thread
  socketpair (`EPERM`). `call_soon_threadsafe()` queues its callback but cannot
  wake the selector, so `asyncio.run()` stalls in default-executor teardown.
  The runtime-owned async bridge avoids that default-executor dependency while
  retaining joined application settlement.
- Runtime Ruff check, format check, and strict Pyright: `PASS`.
- Contracts pytest: `PASS` — 7 tests; Core pytest: `PASS` — 180 passed, 4
  platform skips; Discord adapter pytest: `PASS` — 95 tests, 10 existing
  deprecation warnings.
- Contracts, runtime, Core, and Discord lock checks: `PASS`.
- Repository docs integrity, architecture guard, and `git diff --check`:
  `PASS`; root launcher `--help`: `PASS`.
- Live provider/Discord behavior, Windows-specific behavior, and restart
  persistence: `UNVERIFIED`; no credentials were accessed.

The implementation commit is
`13009570b7080d13679fd42f55a59379b1a72c06`, parent
`e693f7b5d3f8f73ec35e2a745dbc79bd4d126458`. The async bridge fix is
`afb682a1053f4ff7c08b4aec09d314be9543b94e`, parent
`26db861d99bdefb397a799bdcc56bef45e8d67c6`; the documentation closeout is a
separate follow-up commit. The settlement regression test is
`bc6e738d7670148fb3ec1a797efb101c8f446114`, parent
`eabfd8127e72a491743e00bedfd67d1d4cad699f`.

## Exit gate

MIND-1E owns `time → cognition` only. MIND-1F remains responsible for broader
convergence; recurring autonomy, proactive policy, memory, and durable agent
state remain outside this phase.
