# Lilavel persistent-agent kernel

`apps/runtime` is the top-level, provider-neutral process owner for Lilavel.
It can start and remain healthy with no environments, conversations, model
runtime, sidecar, or provider. It owns a bounded observation queue and the
tasks of registered environment adapters. Shutdown closes admission, cancels
adapters, drains accepted observations through the wake-policy seam, and
settles the kernel task group.

The Phase 2 kernel classifies wake decisions only. It does not start a
conversation or generation, execute tools, perform actions, schedule work, or
persist observations. The default `NeverWakePolicy` is deliberately inert.

## Contracts

- `WorldEvent` is an immutable provider-neutral observation envelope. Its
  payload is deep-frozen and its default trust is `untrusted`. Ingestion does
  not make an event memory or canonical conversation history.
- `EnvironmentAdapter.run()` is a long-lived observation source owned by the
  kernel task group. Registration closes when startup begins.
- `ToolSpec`, `ToolCall`, and `ToolResult` are structural contracts only.
  Registration grants no execution or authorization capability.
- `WakePolicy` maps an observation to a `WakeDecision`. A positive decision
  is counted for health evidence but has no Phase 2 side effect.

`submit()` waits when the bounded queue is full. It does not drop, overwrite,
or silently accumulate observations. Events are rejected before startup and
after shutdown begins.

## Lifecycle

The normal states are `new → starting → running → stopping → stopped`.
Unexpected failures in runtime-owned tasks move the kernel to `failed`; a
stopped or failed instance cannot be restarted. Clean shutdown is idempotent.

## Deterministic checks

From this directory:

```powershell
uv sync --locked
uv lock --check
uv run --locked ruff check .
uv run --locked ruff format --check .
uv run --locked pyright
uv run --locked pytest
```
