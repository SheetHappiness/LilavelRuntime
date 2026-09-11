# PHASE MIND-0 — First persistent semantic state

Status: `CLOSED`
Baseline SHA: `286574bb263a331db3adebff67628079ecafb003`
Implementation SHA at exit: `aeebf79` (implementation tree before the final
phase-record metadata amendment)

## Goal

Prove one bounded local causal loop from a completed user turn through an
invisible appraisal, runtime-owned intention, idle expression, self-action, and
next-turn continuity.

## Scope

- Added local-CLI-only in-memory `MindState` with bounded intentions and recent
  noncanonical self-actions.
- Added one transient tool-free `MindAppraiser` V3 generation with a strict
  `no_change` / `create_intention` JSON parser.
- Routed intention-specific idle cognition through the existing authorized
  presence action path and composed read-only mind context into normal CLI
  guidance.
- Added deterministic fixture and causal-loop tests without changing Core
  canonical semantics, V3 lifecycle, sidecar, Discord, SQLite, or scheduling.

## Non-goals

No durable memory, restart persistence, general scheduler, attention policy,
additional environment, arbitrary autonomous tool authority, or autonomous
speech in canonical conversation history was added.

## Decisions

- `USE` runtime-owned immutable intention records with application-generated IDs
  and actual completed Core user/assistant message provenance.
- `USE` one bounded appraisal generation after each successful normal turn;
  invalid, missing, oversized, or extra-field output is `no_change` with no
  retry.
- `USE` user priority cancellation for both appraisal and autonomous lanes;
  the existing single queue consumer keeps successor Core admission after
  settlement.
- `USE` immutable Character v0 guidance separately from normal-turn,
  appraisal, and autonomous behavior guidance.

## Architecture consequences

`MindState` is owned by the local presence runtime and is deliberately not a
Core store, canonical history, model transcript, or durable memory. A normal
CLI request receives a bounded read-only `MindProjection`; autonomous cognition
receives only the selected active intention. Successful `presence.say` marks
that intention expressed and records one bounded `SelfAction`; silence leaves
it active.

## Inherited invariants

ConversationCore remains the sole owner of canonical user/assistant history and
assistant commit semantics. ModelRuntimeV3 remains the sole V3 generation and
provider/tool settlement owner. Application tool authorization, effect
certainty, joined settlement, bounded queues, and stale-output fencing remain
authoritative.

## Evidence

- `apps/runtime/tests/test_presence.py` proves no active intention means no
  autonomous model admission, strict parser fail-closed behavior, bounded
  state, provenance binding, appraisal preemption, intention-specific idle
  admission, noncanonical expression, and next-turn self-action projection.
- The causal fixture scenario uses a concrete unfinished future matter, creates
  one intention invisibly, leaves it active until idle, says exactly once, and
  preserves only canonical user conversation in Core history.
- Character guidance separation is covered by
  `apps/core/tests/test_cognition.py`.

## Validation

Executed on Linux with CPython 3.14.7:

- Contracts Ruff, format, Pyright, and Pytest: `PASS` — 7 tests.
- Runtime lock, Ruff, format, Pyright, and Pytest: `PASS` — 90 tests.
- Core lock, Ruff, format, Pyright, and Pytest: `PASS` — 180 passed, 4
  Windows-only skips.
- Discord adapter lock, Ruff, format, Pyright, and Pytest: `PASS` — 95 tests,
  10 existing deprecation warnings.
- Model-sidecar frozen install, TypeScript check, and Bun tests: `PASS` — 94
  tests.
- Root sync, lock, and `lilavel --help`: `PASS`.
- Repository docs, architecture, and whitespace checks: `PASS`.
- Live provider/local CLI proof: `UNVERIFIED`; not required for this
  deterministic fixture phase and no credentials were accessed.

## Unknowns

Production provider appraisal quality, production idle tuning, and behavior
after restart remain `UNKNOWN`. Durable state and broader autonomous behavior
remain deferred.

## Exit gate

All required deterministic checks pass, the causal loop is covered by focused
tests, no autonomous speech enters Core history, and one clean phase commit is
created without push.
