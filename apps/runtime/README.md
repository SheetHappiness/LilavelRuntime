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

## P4-C application tool authorization seam

P4-C extends the deterministic session seam with an explicit
`ApplicationToolRegistry`. Trusted application composition registers one
canonical `ToolSpec`, bound executor, and optional trusted authorization and
availability hooks per name. Registration rejects duplicate names and schema
keywords outside the bounded object/property/required/scalar/enum/string and
numeric constraint subset. Provider aliases never enter the registry key.

Each explicit tool-enabled generation receives an immutable exposure snapshot
selected by canonical application names. Exposure is not authorization: every
call is checked for snapshot membership, strict non-coercive arguments,
trusted-scope authorization, and liveness immediately before sequential
executor admission. Internal presentation bindings are not model-exposable.
Unknown, unexposed, unavailable, denied, and invalid calls produce bounded
typed `ToolResult` values and never dispatch an executor. Pure and effect-like
fixtures use the same registry path; no fixture binding is registered by the
production Discord composition.

The existing P4-B containment seam remains authoritative. Cancellation is
fenced before executor admission, calls execute sequentially in provider order,
executor timeout is bounded and reports effect uncertainty, and an executor
that ignores bounded cancellation is `uncontained` and poisons the consuming
model runtime. The exact ordered batch and later legal rounds can still settle
after a contained per-call timeout.

Session evidence contains only generation/epoch/round/call correlation and safe
lifecycle/status/reason/effect codes. It excludes raw arguments, raw results,
provider payloads, external identifiers, credentials, and exception bodies.
This seam is not activated by the production kernel or Discord adapter and
grants no real model-selected external effect.

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
