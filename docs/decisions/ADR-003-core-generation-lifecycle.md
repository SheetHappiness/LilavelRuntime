# ADR-003 — Core owns generation lifecycle and fail-closed cleanup

## Status

Accepted for the current architecture.

## Origin

Current reaffirmation.

## Scope

Generation admission, identity, cancellation, settlement, shutdown, provider
cleanup, and failure handling between Core and the sidecar.

## Decision

**Architectural invariant:** Core owns the application-visible generation
lifecycle and accepts only lifecycle events that match its active generation.
Cleanup uncertainty fails closed; it must not be presented as successful reuse.

**Current implementation:** `ModelRuntime` owns generation IDs, epochs, one
active generation, admission, cancellation, terminal settlement, and shutdown.
The sidecar owns provider iterator cleanup. A rejected or timed-out cleanup
poisons reuse, and protocol, containment, or shutdown failures do not trigger
an automatic restart.

## Historical rationale status

Contemporaneous rationale was not recovered. This ADR does not treat current
interpretation as historical fact.

## Current rationale

Explicit ownership and fail-closed cleanup prevent stale output, double
settlement, leaked provider work, and false claims of recovery at a streaming
boundary.

## Consequences

- A valid terminal event settles a generation once; late older events are
  ignored and malformed or mismatched events fail closed.
- Cancellation is an intent with an explicit cleanup boundary, not a claim that
  provider work stopped immediately.
- A failed runtime or poisoned sidecar requires an explicit new lifecycle; it is
  not silently restarted.

## Evidence

- [Architecture: model transport](../ARCHITECTURE.md#model-transport)
- [Core runtime README: lifecycle and admission](../../apps/core/README.md#lifecycle-and-admission)
- [ModelRuntime implementation](../../apps/core/src/lilavel_core/runtime.py)

## Revisit conditions

Revisit if an approved scheduler permits multiple active generations, lifecycle
ownership moves out of Core, cleanup guarantees change, or platform containment
requires a different authority model.
