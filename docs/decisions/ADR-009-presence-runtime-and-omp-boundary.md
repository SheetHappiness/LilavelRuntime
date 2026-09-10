# ADR-009 — Presence runtime ownership and OMP adoption boundary

## Status

Accepted for the PRESENCE-V0 design; not implemented by P5-A.

## Date

2026-09-10

## Scope

The persistent host, event dispositions, wake admission, discrete cognition
runs, and boundary between Lilavel's existing Core/tool lifecycle and OMP
packages.

## Decision

PRESENCE-V0 will be coordinated by the Lilavel runtime as a persistent host.
Idle keeps the host, bounded event ingress, environment tasks, managed
opportunity clock, event router, and bounded ephemeral working state alive. It
does not keep a provider inference alive. A `WakePolicy` may return `NO_WAKE` or
`WAKE`; `WAKE` is only permission to admit one bounded `CognitionRun` after
runtime, user-priority, cooldown, and lifecycle checks.

`WorldEvent` remains an immutable untrusted observation and is not automatically
canonical conversation history or long-term memory. Runtime-owned dispositions
are:

- `OBSERVE` — bounded working-state observation, no cognition;
- `ASIDE` — passive transient context at a safe boundary;
- `STEER` — priority supersession/control, with user input entering Core's
  canonical user-turn path;
- `FOLLOW_UP` — queued successor work after settlement; and
- `WAKE` — an admission signal, not a provider command.

`ConversationCore` remains the sole owner of canonical semantic history and
conversation-level supersession. `ModelRuntime` and the model sidecar remain
the only provider-generation path. Application-owned registry/authorization,
`ToolSession`, bound executor, effect certainty, and joined settlement remain
the only path to external tools. An environment cannot directly force a
provider request.

PRESENCE-V0 will take the repository-pinned `@oh-my-pi/pi-ai@18.1.2` provider
abstraction and native tool transport directly at the existing sidecar
boundary. It will adapt the useful semantics of steering, follow-up, aside,
pre-model gating, and pre-yield checks into Lilavel-owned runtime/Core types.
It will not adopt the low-level OMP `agentLoop`, stateful `Agent`, OMP tool
executor/concurrency, or OMP transcript/session persistence as runtime
authorities.

User input while autonomous work is active is always priority `STEER`: Core
supersedes the autonomous logical run, physical provider/tool work is
cancelled, stale output is fenced, provider and application settlement are
joined, and only then may the user's successor run proceed. Cancellation does
not claim rollback or known effect reversal.

## Current implementation boundary

P5-A is a design/research phase. The current repository still has no
autonomous scheduler, model wake loop, presence CLI, or durable agent state.
P5-B must add a narrow Core-owned transient autonomous cognition admission seam
without faking a user message, creating a second transcript, or calling the
sidecar directly. V0 autonomous `SAY` output is presented but is not committed
to canonical history until a later origin/commit decision is accepted.

## Rationale

The repository already proves the hard production semantics at the Core,
ModelRuntime, sidecar, and application tool boundaries. The pinned dependency
contains `pi-ai` transport but not the OMP agent loop. Current upstream OMP
`Agent`/`agentLoop` owns a mutable AgentMessage context, repeated provider
turns, tool execution/result insertion, queue semantics, and cancellation
details. Embedding it would require a proxy executor and two lifecycle engines
to preserve Core history, Lilavel authorization, effect certainty, generation
identity, and joined settlement. That complexity would make the existing
ownership contract less reliable, not smaller.

The OMP queue and boundary ideas are still useful design references. Lilavel
must deliberately make them subordinate to runtime admission and Core
supersession, retain silence bias, and keep observation/working state out of
canonical history and memory.

## Consequences

- P5-B has one CLI cognition lane and no simultaneous autonomous/user
  generation in that scope.
- `NO_WAKE` and `NO_ACTION` are successful normal outcomes, not errors or
  reasons for immediate resampling.
- P5-B requires a root launcher for the requested `uv run lilavel` shape; the
  baseline has no root Python project or script entry point.
- OMP current-upstream source is audit evidence only. No dependency upgrade is
  implied by this ADR.
- If OMP source is copied or substantially ported later, its MIT license,
  copyright, and required notice obligations must be preserved. P5-A copied no
  source.
- Any future autonomous durable state, memory, multi-environment arbitration,
  or model-selected tool activation requires a separate decision and
  deterministic validation.

## Evidence

- [P5-A research/design record](../../work/PHASE-5A-presence-runtime-design.md)
- [System architecture](../ARCHITECTURE.md)
- [Runtime boundary](ADR-006-lilavelruntime-agent-boundary.md)
- [Core canonical history](ADR-001-core-owns-canonical-conversation-state.md)
- [Core generation lifecycle](ADR-003-core-generation-lifecycle.md)
- [Tool transport/authority](ADR-007-model-tool-calls-remain-transport-only.md)
- [Pinned sidecar dependency](../../apps/model-sidecar/package.json)
- [Pinned lockfile](../../apps/model-sidecar/bun.lock)

## Revisit conditions

Revisit if an approved design adds multiple active cognition lanes, assigns
canonical history or memory to a new owner, changes joined-settlement/effect
certainty, activates autonomous external tools, or intentionally adopts a
version-pinned OMP agent/session package with a demonstrated ownership and IPC
plan.
