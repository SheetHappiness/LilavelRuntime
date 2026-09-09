# Lilavel persistent-agent kernel

`apps/runtime` is the top-level process owner for Lilavel. It can start and
remain healthy with no environments, conversations, model runtime, sidecar, or
provider. It owns a bounded observation queue, registered environment tasks,
positive-wake routing, Core conversation sessions, and the destination choice
for typed environment presentation actions.

The default `NeverWakePolicy` remains inert. The Phase 3
`DirectMessageWakePolicy` deterministically wakes only for the typed
`direct_message` event kind; it performs no LLM wake decision. A
`CoreConversationRouter` then owns session/runtime creation and relays semantic
Core events as runtime-generated, trusted `ToolCall` presentation actions to
the source environment. These are application actions, not model-selected
tools.

## Contracts

- `WorldEvent` is an immutable provider-neutral observation envelope. Its
  payload is deep-frozen and its default trust is `untrusted`. Ingestion does
  not make an event memory or canonical conversation history.
- `EnvironmentAdapter.run()` is a long-lived observation source owned by the
  kernel task group; `execute()` is its typed action boundary. Registration
  closes when startup begins.
- `ToolSpec`, `ToolCall`, and `ToolResult` remain provider-neutral envelopes.
  Phase 3 uses trusted runtime-generated calls for presentation only;
  registration grants no model tool authority.
- `WakePolicy` maps an observation to a `WakeDecision`; an optional
  `EventRouter` owns the positive-wake conversational route.

## Deterministic tool-session seam

P4-B adds `DeterministicToolSessionFactory` as the application-owned execution
seam for fixtures only. It snapshots exposed `ToolSpec` values per admitted
generation, validates and authorizes calls through injected deterministic
decisions, executes a batch sequentially in provider order, and creates bounded
`ToolResult` values. Cancellation is rechecked before every call. Executor
timeout and containment are explicit; an executor that ignores bounded
cancellation is `uncontained` and must poison the consuming model runtime. A
contained per-call timeout produces `timed_out` for that call without cancelling
the session, so the exact ordered batch and later legal rounds can still settle.

Session evidence contains only generation/epoch/round/call correlation and safe
lifecycle/status/effect codes. It excludes raw arguments, raw results, provider
payloads, external identifiers, credentials, and exception bodies. This seam is
not registered with the production kernel or Discord adapter and grants no real
model-selected external effect.

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
