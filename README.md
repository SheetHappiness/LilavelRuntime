# LilavelRuntime

LilavelRuntime is the canonical repository for Lilavel as a persistent agent
runtime. It combines a small persistent-agent kernel with the controlled
migration of the proven conversational foundation from
`C:\Lilavel-m4c-integration`; it does not claim that autonomous behavior is
implemented.

## Product model

Lilavel's top-level runtime owns persistent process lifecycle, bounded
world-event ingress, registered environment tasks, deterministic explicit-DM
wake/routing, Core conversation-session lifecycle, and typed environment
presentation actions. Autonomous scheduling, production model-selected tools,
attention/decision, and cross-environment state remain deferred.

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

The implemented Discord path is:

```text
Discord one-to-one DM
  → apps/discord-adapter
  → WorldEvent
  → LilavelRuntime wake/route
  → ConversationCore
  → ModelRuntime
  → apps/model-sidecar
  → typed presentation action
  → apps/discord-adapter
```

## Current foundation

- `apps/runtime` contains the persistent kernel, provider-neutral observation
  and action contracts, deterministic explicit-DM policy, and the Core-backed
  conversation router. It still starts cleanly with zero environments and
  performs no autonomous work.
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

It keeps one local process alive, streams canonical user conversation, and can
optionally admit one bounded noncanonical idle action with `--wake-on-idle`.
See the [runtime README](apps/runtime/README.md#p5-b1-local-presence) for the
ownership and safety bounds.

## Explicitly deferred

The current runtime does not add a general scheduler, probabilistic attention
policy, world model, arbitrary model-selected tool activation, MCP integration,
voice/guild behavior, retrieval, or memory semantics. P5-B1 proves only one
bounded local idle opportunity with two terminal presence actions. P4-D proves
only one explicitly composed, trusted-DM Discord send boundary; neither is a
general production capability. Each broader capability remains future scope
requiring an explicit decision and validation.
