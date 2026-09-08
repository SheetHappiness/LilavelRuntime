# ADR-005 — Canonical conversation history and provenance evidence are separate concerns

## Status

Accepted for the current architecture.

## Origin

Current reaffirmation.

## Scope

The distinction between canonical message content and raw provenance evidence,
including the current local persistence implementation and durability default.

## Decision

**Architectural invariant:** Canonical conversation messages are the content
authority. Provenance evidence is a separate, raw record linked to a canonical
source and must not silently become interpreted memory, a claim, or a second
content store.

**Current implementation:** `SQLiteConversationStore` writes
`conversation_messages` and `evidence_records` in one transaction. Evidence
stores provenance and points to the canonical source without copying its text.
SQLite is the current implementation. The default remains SQLite `:memory:`;
durable/restart-safe history requires an explicit file-backed path and a stable
provider-neutral `scope_id`.

## Historical rationale status

Contemporaneous rationale was not recovered. This ADR does not treat current
interpretation as historical fact.

## Current rationale

Separating source content from provenance preserves a clear authority boundary
while leaving interpretation, retrieval, and future memory semantics out of the
current foundation.

## Consequences

- Canonical message identity, order, role, and text remain Core-owned.
- Evidence can support audit and provenance checks without claiming confidence,
  validity, retrieval, reflection, or memory semantics.
- The default in-memory store is suitable for ephemeral callers; conversation
  history is not restart-safe by default.

## Evidence

- [Architecture: semantic ownership](../ARCHITECTURE.md#semantic-ownership)
- [Core runtime README: local canonical persistence and raw evidence](../../apps/core/README.md#local-canonical-persistence-and-raw-evidence)
- [Persistence implementation](../../apps/core/src/lilavel_core/persistence.py)

## Revisit conditions

Revisit if evidence gains interpretation or retrieval semantics, the canonical
source boundary changes, or the durability/storage policy is explicitly
replaced or expanded.
