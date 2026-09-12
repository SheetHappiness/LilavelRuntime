# ADR-019 — RUNTIME-H1 bounded application replay

- Status: Accepted and implemented on 2026-09-12
- Phase: RUNTIME-H1
- Parent: MIND-1F-E

## Context

`ProposalApplicationCoordinator` previously retained a lifetime fence ledger
with a 256-entry ceiling. That bounded the replay proof, but eventually made a
healthy persistent runtime reject every new application with
`application_fence_full`. Forgetting old fences without changing the trusted
identity would allow an old completed outcome to execute again.

RUNTIME-H1 needs liveness beyond 256 applications without claiming durable
idempotency or introducing a second proposal-to-effect path.

## Decision

Use a coordinator-owned application epoch with a bounded settled replay window
and a monotonic per-epoch sequence. The trusted application identity is not the
visible sequence or digest alone. A valid permit must contain:

- the current coordinator epoch and issued sequence;
- the trusted scope, episode, and trigger binding;
- a SHA-256 digest over a versioned, domain-separated canonical encoding of
  every effect-relevant field; and
- a private process-local capability object whose identity is held only by the
  coordinator.

`ApplicationPermit` hides the capability from `repr`, equality, and evidence,
and rejects serialization as authority. A dataclass with matching visible
fields but a different capability is rejected before state, temporal, or tool
application. Changing action text, state text, temporal reason, deadline, or
intention reference changes the digest and rejects reuse of the permit.

The canonical digest encoding uses deterministic JSON field order, explicit
proposal kinds, ordered proposal arrays, JSON `null` for optional values,
bounded text content, and UTC ISO-8601 timestamps with microseconds and `Z`.
The domain/version marker is `lilavel-application-permit-v1`; the digest is
SHA-256 over the UTF-8 canonical bytes. It does not use `repr`, pickle, or
arbitrary object stringification.

The completed cognition runner remains effect-free. It validates the
`CognitionCandidate`, creates the runner-completed `CognitionOutcome`, and
passes it through a narrow bind-only `ApplicationPermitIssuer`. The issuer
does not expose application, tool, temporal, or settlement operations. The
`SemanticActor` session fence remains semantic admission authority; the
coordinator permit remains application-effect authority.

## Rotation and retirement

The coordinator retains at most 256 settled replay results in the current
epoch. A second bounded deque retains at most 256 rejection results as
diagnostic evidence. It retains no historical retired-ID set and no
unbounded outstanding-permit set. The epoch UUID, next sequence, and retired
high-water mark are bounded scalar metadata.

When the settled replay window is full, rotation clears only settled current
epoch metadata while holding the same coordinator lock used for the complete
application operation. Therefore no active application can be forgotten while
state, a `WakeIntent`, or an external effect is unsettled.

An issued permit that is never applied remains usable only while its epoch is
current. If safe rotation happens first, that permit is permanently retired;
its later first-use attempt is rejected before any state mutation, wake
creation, tool call, or external effect. New permits in the new epoch remain
live. This fail-closed retirement rule intentionally gives up first-use
availability for abandoned permits in exchange for bounded replay state.

## Alternatives considered

- **A — application session/epoch rotation alone:** useful for liveness, but
  insufficient by itself. An epoch field can be forged and a forgotten old
  descriptor can be replayed unless old epochs are made invalid and the permit
  is authentic. The selected design uses epoch rotation as one component.
- **B — runtime-owned application permit/capability alone:** provides
  authenticity and scope, but does not bound settled replay records. The
  selected design uses a private coordinator capability as the authority.
- **C — monotonic sequence with compaction/high-watermark:** provides the
  bounded replay window and safe retirement boundary. It is selected together
  with B; the epoch makes the compacted window's old descriptors invalid.
- **D — durable idempotency records:** would be the stronger restart-safe
  answer, but durable agent/replay storage is outside H1. It remains future
  work and is not silently claimed here.
- **LRU/oldest eviction or clearing fences:** rejected. Forgetting an old
  application descriptor without making it invalid can repeat an already
  effectful outcome.

## Guarantee and boundary

`PASS:` at-most-once application effects for valid runtime-issued permits
during one `ProposalApplicationCoordinator`/runtime lifetime, with bounded
replay metadata and continued liveness beyond 256 applications.

`UNVERIFIED:` replay/idempotency across process restart.

The implementation preserves the existing state-first, temporal-runtime-owned,
P4 tool-session, effect-certainty, cancellation-settlement, and no-rollback
semantics. There remains exactly one proposal-to-effect boundary:

```text
CognitionOutcome → ProposalApplicationCoordinator → state / temporal / P4 tool application
```

## Evidence

`apps/runtime/tests/test_proposal_application_h1.py` proves 1,001 sequential
applications, bounded retention, recent and retired duplicate rejection,
concurrent duplicate serialization, confirmed and unknown effect non-retry,
state/temporal/mixed replay safety, cancellation settlement, action/state/
temporal tamper rejection, forged visible permit rejection, unused permit
retirement, and post-settlement rotation. Restart replay and live-provider
behavior are not established by these deterministic tests.
