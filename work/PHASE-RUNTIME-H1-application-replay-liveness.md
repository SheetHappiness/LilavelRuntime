# Phase RUNTIME-H1 — Application replay and long-run liveness

## Status

Implemented and validated on 2026-09-12.

Implementation SHA: `b2e0753a1fcb7709fd44204f467d3b12d1f57e6e`.

Parent SHA: `22b10672929197d020bbd98c8a703eb02b66eba9`.

## Design confirmation

The selected design is B+C: a runtime-owned authentic application permit
combined with a coordinator-owned application epoch, monotonic sequence, and
bounded settled replay window.

The trusted replay authority is the coordinator's private capability object,
validated by identity, together with the current epoch/sequence and the
canonical proposal digest. The digest is versioned and domain-separated, uses
deterministic field ordering and ordered proposal lists, represents optional
values explicitly as JSON `null`, and normalizes temporal datetimes to UTC.
The runner only validates a candidate, creates a completed inert outcome, and
passes it through the narrow bind-only issuer; it does not own effects,
settlement, tools, or replay retention.

At a safe lock-held boundary, the coordinator retires the settled current
epoch and starts a new one. An issued-but-never-applied permit is permanently
invalid after that rotation. No outstanding-permit set is retained to preserve
first-use availability. The same lock spans application settlement, so an
active state mutation, wake admission, or P4 effect cannot be forgotten while
unsettled. `SemanticActor` remains independent semantic admission authority.

## Implemented bounds and guarantee

- Settled replay metadata: at most 256 current-epoch records.
- Rejection evidence: at most 256 records.
- Retired history: no retained retired-ID set.
- Epoch identity: one process-local runtime-owned UUID at a time.
- Sequence/retirement state: fixed scalar metadata; no unbounded permit set.

The implemented guarantee is exactly:

`PASS:` at-most-once application effects for valid runtime-issued permits
during one `ProposalApplicationCoordinator`/runtime lifetime, with bounded
replay metadata and continued liveness beyond 256 applications.

`UNVERIFIED:` replay/idempotency across process restart.

External effects remain non-rollbackable. Confirmed and unknown effects are
never retried. Cancellation after application begins waits for settlement
before propagating, and settled replay records remain in the bounded window.

## Deterministic evidence

Focused H1 suite: `PASS` — 14 tests in 1.25 seconds, including:

- 1,001 sequential applications with a four-entry replay window;
- recent duplicate and duplicate-after-rotation rejection;
- bounded replay retention and continued new work after rotation;
- concurrent duplicate with exactly one executor call;
- confirmed-effect and unknown-effect no-retry;
- state-only, temporal-only, and mixed retired replay with zero repeated
  effects;
- cancellation during application with settlement before propagation;
- action, state, and temporal permit tampering rejection;
- forged visible permit rejection and process-local non-serialization;
- unused-permit retirement; and
- rotation safety and new-epoch liveness after settlement.

Runtime full suite: `PASS` — 192 tests.

## Validation

Executed on Linux with CPython 3.14.7:

- Runtime `uv sync --locked`: `PASS`.
- Runtime `uv lock --check`: `PASS`.
- Runtime locked Ruff check: `PASS`.
- Runtime locked Ruff format check: `PASS`.
- Runtime strict Pyright: `PASS`.
- Runtime focused H1 pytest: `PASS` — 14 passed.
- Runtime full pytest: `PASS` — 192 passed.
- Repository docs integrity: `PASS` — 53 Markdown files, 104 local links,
  12 anchors, and 6 command paths.
- Architecture guard: `PASS`.
- Root `uv sync --locked`, `uv lock --check`, and `lilavel --help`: `PASS`.
- Final `git diff --check`: `PASS`.

Contracts/Core/Discord package reruns were not required because no files in
those packages or shared package contracts changed. Live provider, Discord,
process-restart replay, durable idempotency, and Windows-specific behavior
remain `UNVERIFIED` unless separately exercised.

## Exit gate

RUNTIME-H1 is `PASS`: one healthy coordinator applied 1,001 distinct
applications, replay metadata stayed bounded, and an effectful old permit was
rejected after epoch rotation before any second effect boundary crossing.
