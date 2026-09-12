# ADR-006 — Lilavel is a persistent agent runtime; Discord is an environment adapter

## Status

Accepted for the canonical repository bootstrap.

## Date

2026-09-08

## Scope

Top-level product ownership and the boundary between the future persistent
agent runtime and environment-specific observation/action adapters.

## Decision

Lilavel is modeled at the top level as a persistent agent runtime. The runtime
boundary is the future owner of cross-environment lifecycle, world
observations, scheduling and wake, attention/decision, tools/actions, and
agent-state orchestration.

Discord is a replaceable environment adapter: it translates in-scope Discord
observations into runtime/conversation inputs and presents approved outputs as
Discord actions. Discord is not where Lilavel lives and does not own agent
identity, canonical conversation history, memory, provider state, scheduling,
or the agent lifecycle.

`ConversationCore` remains the owner of conversational semantics within the
foundation: canonical role/text history, turn admission, context composition,
assistant commit semantics, and conversation-level cancellation/supersession.
`ModelRuntime` and the model sidecar retain their existing narrower physical
generation and provider/process transport boundaries.

## Current implementation state

`apps/runtime` implements the top-level process owner and now realizes the
explicit Discord DM route: bounded `WorldEvent` admission into a recent
in-memory `ObservationWindow`, an explicit deterministic cognition gate,
reactive response step, Core/ModelRuntime conversation sessions, and trusted
runtime-generated presentation actions back to the source adapter. Admission
alone has no response or cognition side effect. The current gate recognizes
only the existing direct-message event kind and returns either
`NO_COGNITION` or a bounded observation-ID `CognitionTrigger`; model-based
attention remains deferred. It remains healthy with zero environments and
does not implement a scheduler, autonomous model wake loop, model-selected
tools, world model, memory, or durable cross-environment agent state.

## Rationale status

No earlier repository record establishing this top-level boundary was found.
The rationale below is a current architectural decision, not reconstructed
history.

## Current rationale

Separating the agent runtime from environment adapters keeps Discord
replaceable and prevents transport identifiers, delivery concerns, or provider
sessions from becoming the agent's semantic identity or memory. It also lets
the proven conversation subsystem migrate without claiming that autonomous
runtime behavior already exists.

## Consequences

- Observations enter through the runtime-owned, provider-neutral `WorldEvent`
  seam; Discord-specific details must stay at the adapter boundary.
- Conversation history and assistant commit semantics remain governed by the
  existing Core decisions, not by a new top-level rewrite.
- The absence of a runtime scheduler or world-state package is explicit,
  rather than an implicit contract.
- Any future persistent agent state, wake policy, attention policy, or tool
  authority requires a separate design decision and validation.

## Evidence

- [System architecture](../ARCHITECTURE.md)
- [Controlled migration map](../MIGRATION.md)
- [ADR-001: Core owns canonical conversation state](ADR-001-core-owns-canonical-conversation-state.md)
- [ADR-004: Discord transport/presentation boundary](ADR-004-discord-transport-presentation-edge.md)

## Revisit conditions

Revisit if an approved design changes the top-level agent owner, makes an
environment adapter authoritative for agent state, or establishes a different
cross-environment lifecycle boundary.
