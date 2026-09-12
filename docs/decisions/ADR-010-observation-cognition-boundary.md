# ADR-010 — Observation is not cognition

## Status

Accepted and implemented for the MIND-1B slice.

## Date

2026-09-12

## Scope

The provider-neutral runtime boundary between an admitted `Observation` and a
runtime decision to begin one cognition episode.

## Decision

`Observation` means that the runtime admitted and retained external event data;
it does not mean that the runtime should think, call a model, enter
`ConversationCore`, or act.

`CognitionGate` is a small runtime-owned policy seam over admitted observations.
It returns exactly one of:

- `NO_COGNITION`, a successful terminal decision with no cognition side
  effects; or
- a bounded `CognitionTrigger` containing only one or more admitted
  observation IDs and a runtime-owned reason.

The initial `DirectMessageCognitionGate` is deterministic and recognizes only
the existing `direct_message` event kind. It can coalesce an explicit ordered
batch into one bounded trigger, without timers, scheduler state, model-based
salience, or payload interpretation. The runtime's compatibility
`cognition_step()` evaluates one receipt at a time so the current DM route
remains unchanged. `reactive_step()` remains an alias for that explicit step.

## Rationale

Admission and cognition have different side-effect contracts. Keeping the gate
after admission makes the negative path mechanically testable and keeps the
runtime able to observe events without creating Core sessions or provider
work. Keeping the trigger provider-neutral prevents Discord transport details
or untrusted payload guidance from becoming cognition authority.

## Consequences

- Direct DM observations still reach the existing explicit Core route.
- Unsupported event kinds end at `NO_COGNITION` until a later policy explicitly
  supports them.
- A positive trigger does not select an action; cognition/action separation is
  deferred to MIND-1C.
- Model-based salience, proactive autonomy, timers, memory, relationships,
  social backoff, and guild ambient behavior remain out of scope.

## Evidence

- [Runtime cognition gate](../../apps/runtime/src/lilavel_runtime/contracts.py)
- [Runtime explicit cognition step](../../apps/runtime/src/lilavel_runtime/kernel.py)
- [Runtime gate and side-effect tests](../../apps/runtime/tests/test_kernel.py)
- [Provider-neutral gate tests](../../apps/runtime/tests/test_contracts.py)
- [Current architecture](../ARCHITECTURE.md)

## Revisit conditions

Revisit when a later milestone needs additional event classes, grouped runtime
admission, time-based cognition opportunities, model-based salience, or a
different cognition/action boundary.
