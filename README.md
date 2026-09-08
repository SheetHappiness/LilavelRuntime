# LilavelRuntime

LilavelRuntime is the canonical repository for Lilavel as a persistent agent
runtime. It combines a small persistent-agent kernel with the controlled
migration of the proven conversational foundation from
`C:\Lilavel-m4c-integration`; it does not claim that autonomous behavior is
implemented.

## Product model

Lilavel's top-level runtime now owns persistent process lifecycle, bounded
world-event ingress, registered environment tasks, structural tool
registration, and wake classification. Scheduling, model/tool execution,
attention/decision, actions, and cross-environment state remain deferred until
their contracts are designed and validated.

Environment adapters are replaceable sensor+action boundaries. Discord is one
such adapter, not the place where Lilavel lives:

```text
world observations
        │
        ▼
environment adapter ──► LilavelRuntime kernel
        │                                  │
        │                                  ▼
        └──── approved actions ◄── ConversationCore / tools
                                           │
                                           ▼
                                     ModelRuntime
                                           │
                                           ▼
                                    model-sidecar
```

The currently implemented path is narrower:

```text
Discord one-to-one DM
  → apps/discord-adapter
  → ConversationCore
  → ModelRuntime
  → apps/model-sidecar
  → Discord presentation
```

## Current foundation

- `apps/runtime` contains the provider-neutral persistent kernel and its
  `WorldEvent`, `EnvironmentAdapter`, tool-envelope, and wake-policy contracts.
  It starts without any environment, conversation, model runtime, sidecar, or
  provider and performs no autonomous work.
- `apps/core` contains provider-neutral conversational semantics, canonical
  conversation history, conversation-level cancellation/supersession,
  generation lifecycle integration, SQLite message/evidence persistence,
  Character v0 cognition structures, and deterministic evidence/scenario
  fixtures.
- `apps/model-sidecar` is a separate provider/process transport boundary. Its
  local protocol, authentication, provider mapping, cleanup, and Bun details
  are documented in its [component README](apps/model-sidecar/README.md).
- `apps/discord-adapter` is a DM-only replaceable environment adapter. It owns
  Discord admission, delivery, presentation pacing, and edge-local metadata;
  it does not own identity, memory, or canonical conversation state.

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

## Explicitly deferred

The current kernel does not add a scheduler, model wake loop, attention policy,
world model, tool execution, MCP integration, voice/guild behavior, retrieval,
or memory semantics. Each of those is future scope requiring an explicit
decision and validation.
