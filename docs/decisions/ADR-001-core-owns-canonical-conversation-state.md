# ADR-001 — Core owns canonical conversation state

## Status

Accepted for the current architecture.

## Origin

Current reaffirmation.

## Scope

Semantic conversation history, context composition, assistant commit, and the
boundary between canonical state and transient streamed output.

## Decision

**Architectural invariant:** Core is the sole owner of canonical semantic
conversation state. A transport or provider edge may present or deliver a run,
but it may not become a second history owner.

**Current implementation:** `ConversationCore` accepts the user message into a
Core-owned store, exposes assistant deltas as transient run output, and commits
assistant text only after successful completion. The complete provider-neutral
role/text context is composed by Core.

## Historical rationale status

Contemporaneous rationale was not recovered. This ADR does not treat current
interpretation as historical fact.

## Current rationale

One canonical commit path keeps cancellation, supersession, provider isolation,
and replaceable delivery surfaces from creating divergent semantic histories.

## Consequences

- Partial, cancelled, superseded, failed, or stale assistant output is not
  canonical history.
- Presenters consume semantic run events and cannot commit history themselves.
- Restart-safe history is not implicit; the current default store is in-memory,
  while durable use requires an explicit file-backed store and stable scope.

## Evidence

- [Architecture: ownership map](../ARCHITECTURE.md#ownership-map)
- [Core runtime README: semantic conversation layer](../../apps/core/README.md#semantic-conversation-layer)
- [ConversationCore implementation](../../apps/core/src/lilavel_core/conversation.py)

## Revisit conditions

Revisit if an approved design assigns canonical semantic state to another
component, changes assistant commit semantics, or introduces a different
multi-process/multi-surface ownership model.
