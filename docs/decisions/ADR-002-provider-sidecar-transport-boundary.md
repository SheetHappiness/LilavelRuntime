# ADR-002 — Provider sidecar is a transport boundary with caller-owned context

## Status

Accepted for the current architecture.

## Origin

Current reaffirmation.

## Scope

The Core-to-provider boundary, request context ownership, authentication,
provider state, and JSONL process transport.

## Decision

**Architectural invariant:** The caller/Core owns semantic context supplied to a
request. The provider sidecar owns provider and process transport state only;
it does not retain semantic conversation history or become a second runtime.

**Current implementation:** Python Core sends version-two JSONL requests with
caller-owned prompt or structured role/text context and optional trusted
guidance. The Bun sidecar performs supported auth discovery, provider mapping,
streaming, and provider cleanup. Its stdout is protocol-only; diagnostics use
stderr.

## Historical rationale status

Contemporaneous rationale was not recovered. This ADR does not treat current
interpretation as historical fact.

## Current rationale

Keeping semantic context in Core makes the provider replaceable and prevents
provider sessions, payloads, or auth state from silently becoming application
memory.

## Consequences

- The sidecar does not own conversation, memory, character, social, voice, or
  tool state, and does not use provider continuation state for this boundary.
- Provider-specific authentication and cleanup remain outside canonical history.
- Protocol changes must preserve strict framing and the stdout/stderr split.

## Evidence

- [Architecture: model transport](../ARCHITECTURE.md#model-transport)
- [Architecture: caller-owned structured text context](../ARCHITECTURE.md#caller-owned-structured-text-context)
- [Model sidecar README](../../apps/model-sidecar/README.md)
- [Sidecar protocol implementation](../../apps/model-sidecar/src/protocol.ts)

## Revisit conditions

Revisit if an approved provider integration requires semantic continuation,
sidecar-owned context retention, provider-specific application semantics, or a
different protocol ownership boundary.
