# LilavelRuntime

LilavelRuntime is the canonical repository for Lilavel as a persistent agent
runtime. It combines a small persistent-agent kernel with the controlled
migration of the proven conversational foundation from
`C:\Lilavel-m4c-integration`; it does not claim that general autonomous
behavior is implemented.

## Product model

Lilavel's top-level runtime owns persistent process lifecycle, bounded
world-event admission, a recent in-memory observation window, a deterministic
observation-to-cognition gate, registered environment tasks, explicit reactive
DM routing, Core conversation-session lifecycle, and typed environment
presentation actions. Model-based attention, autonomous scheduling, production
model-selected tools, durable memory, and cross-environment state remain
deferred.

Environment adapters are replaceable sensor+action boundaries. Discord is one
such adapter, not the place where Lilavel lives:

```text
world observations
        │
        ▼
environment adapter ──► LilavelRuntime kernel
        │                                  │
        │                                  ├── Observation
        │                                  │       │
        │                                  │       ├── NO_COGNITION → stop
        │                                  │       └── CognitionTrigger
        │                                  │               │
        │                                  │               ▼
        │                                  │        SemanticActor (USER)
        │                                  │               │
        │                                  │               ▼
        │                                  │        ConversationCore / tools
        │                                  │               │
        │                                  │               ▼
        │                                  │        ModelRuntime → model-sidecar
        │                                  │
        └──── approved actions ◄───────────┘
```

The implemented Discord path is:

```text
Discord one-to-one DM
  → apps/discord-adapter
  → WorldEvent
  → LilavelRuntime observation admission
  → explicit cognition gate
  → SemanticActor (USER) via the explicit reactive response step
  → ConversationExecutionAdapter
  → ConversationCore
  → ModelRuntime
  → apps/model-sidecar
  → typed presentation action
  → apps/discord-adapter
```

The normal local CLI path now shares the same character-wide admission point:

```text
CLI input
  → LilavelRuntime.submit_user()
  → SemanticActor (USER)
  → ConversationExecutionAdapter
  → local-cli ConversationCore
  → ModelRuntime
  → PromptToolkitOutputSink
```

CLI and Discord user episodes serialize through one actor while their
`ConversationCore` scopes and histories remain isolated. Legacy local
appraisal and autonomous presence behavior remains a temporary compatibility
lane, excluded while actor-owned user work is active.

The cognition gate decides whether work may begin; it does not select an action.
MIND-1C ends at inert proposals. MIND-1D adds a separate runtime-owned
validation, authorization, and application boundary; temporal wake proposals
remain deferred to MIND-1E.

## Current foundation

- `apps/runtime` contains the persistent kernel, provider-neutral event and
  admitted-observation contracts, the bounded recent observation window,
  deterministic `NO_COGNITION`/`CognitionTrigger` gating, the character-wide
  `SemanticActor`, actor-owned CLI and reactive DM admission, the Core-backed
  conversation router, the local-CLI-only bounded MIND-0 state loop, and the
  MIND-1D proposal application boundary. It still starts cleanly with zero
  environments.
- `apps/core` contains provider-neutral conversational semantics, canonical
  conversation history, conversation-level cancellation/supersession,
  generation lifecycle integration, SQLite message/evidence persistence,
  Character v0 cognition structures, and deterministic evidence/scenario
  fixtures.
- `apps/model-sidecar` is a separate provider/process transport boundary. Its
  local protocol, authentication, provider mapping, cleanup, and Bun details
  are documented in its [component README](apps/model-sidecar/README.md).
- `apps/discord-adapter` is a DM-only replaceable environment adapter. It owns
  Discord admission, delivery, presentation pacing, edge-local metadata, and
  the explicitly opt-in P4-D scoped tool executor; it does not own identity,
  memory, or canonical conversation state.

The default Core store is in-memory for deterministic ephemeral callers.
Restart-safe conversation history requires an explicit file-backed SQLite store
and stable provider-neutral scope. Durable cross-environment agent state is not
implemented.

## Repository orientation

- [`AGENTS.md`](AGENTS.md) — recurring development contract.
- [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) — system ownership and
  invariants.
- [`docs/STACK.md`](docs/STACK.md) — technology roles.
- [`docs/VALIDATION.md`](docs/VALIDATION.md) — deterministic, live, and
  platform-specific checks.
- [`docs/MIGRATION.md`](docs/MIGRATION.md) — controlled source-to-destination
  migration truth.
- [`docs/decisions/`](docs/decisions/) — accepted decisions, including the
  top-level runtime/adapter boundary.
- [`work/TEMPLATE.md`](work/TEMPLATE.md) — minimal durable phase/task record.

## Validation

Run the small static ownership guard from the repository root:

```powershell
python scripts/check_architecture.py
```

Run the package-local deterministic checks listed in
[`docs/VALIDATION.md`](docs/VALIDATION.md). Live provider or Discord checks are
optional and require their supported external authentication; absence of that
prerequisite is reported as `BLOCKED`, not as a migration failure.

## Persistent local CLI

P5-B1 adds the root launcher:

```powershell
uv run --locked lilavel
```

It keeps one local process alive, streams canonical user conversation, appraises
successful turns into bounded in-memory intentions, and can optionally admit
one noncanonical idle action caused by an active intention with
`--wake-on-idle`.
See the [runtime README](apps/runtime/README.md#p5-b1-local-presence) for the
ownership and safety bounds.

## Explicitly deferred

The current runtime does not add a general scheduler, probabilistic attention
policy, world model, arbitrary model-selected tool activation, MCP integration,
voice/guild behavior, retrieval, durable memory semantics, or cross-environment
mind state. MIND-0 proves only one bounded local causal loop on top of P5-B1;
P4-D proves only one explicitly composed, trusted-DM Discord send boundary.
Neither is a general production capability. Each broader capability remains
future scope requiring an explicit decision and validation.
