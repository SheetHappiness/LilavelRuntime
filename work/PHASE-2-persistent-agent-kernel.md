# PHASE 2 — Persistent Agent Kernel

Status: `CLOSED`
Baseline SHA: `84e5c8a50ba4d475b604a03560816a410dd40917`
Implementation SHA at exit: `941d041903aa7787bd6c0db749996681e646bd1e`

## Goal

Introduce the smallest production-quality top-level persistent-agent kernel so
Lilavel can exist as a healthy process independently of Discord, conversations,
and model generation.

## Scope

- Provider-neutral runtime contracts in `apps/runtime/src/lilavel_runtime/contracts.py`.
- Explicit kernel lifecycle and bounded event ingress in
  `apps/runtime/src/lilavel_runtime/kernel.py`.
- Pre-start environment and structural tool registration seams.
- Inert wake classification with no downstream action.
- Deterministic lifecycle, backpressure, settlement, and architecture tests.
- Current architecture, stack, validation, and existing runtime-boundary ADR updates.

## Non-goals

Discord behavior migration, protocol v3, model tools, a Neuro adapter,
autonomous scheduling/model calls, cognition/intervention policy, MCP, memory,
retrieval, voice, Twitch, avatars, and live provider behavior.

## Decisions

- `USE apps/runtime`: the top-level kernel is a separate Python 3.12 package.
- `USE asyncio.TaskGroup`: the kernel owns its event consumer and registered
  long-lived environment tasks.
- `USE bounded asyncio.Queue`: `submit()` waits for capacity and never silently
  drops or accumulates observations. Shutdown closes admission and rejects a
  submission still blocked by backpressure.
- `USE immutable provider-neutral envelopes`: `WorldEvent`, `ToolSpec`,
  `ToolCall`, and `ToolResult` deep-freeze JSON-compatible values. External and
  tool-call inputs are untrusted by default.
- `USE inert wake classification`: `WakePolicy` returns `WakeDecision`; Phase 2
  records positive decisions but starts no conversation or generation.
- `DEFER scheduler/timer interface`: no Phase 2 behavior needs time-driven
  work, so an unused abstraction would be speculative.
- `DEFER dynamic registration`: environments and tools are configured before
  startup; registration closes when startup begins.
- `UPDATE ADR-006`: the accepted top-level ownership decision is now realized
  in code. No new consequential architecture decision required a new ADR.

## Architecture consequences

`LilavelRuntime` now owns the top-level process lifecycle, bounded observation
ingress, environment task settlement, structural tool registration, and wake
classification. It has no required distribution dependencies and no code path
to Core, Discord, Neuro, the sidecar, or a provider.

`ConversationCore` retains canonical conversation/history and turn semantics.
`ModelRuntime` retains physical generation lifecycle. Environment adapters
remain replaceable observation/action boundaries. Tool registration grants no
authorization or execution capability. Ingested events do not become memory or
canonical history.

## Inherited invariants

- Accepted conversation user messages remain canonical immediately; assistant
  text commits only after successful completion.
- Cancellation, supersession, provider failure, and partial assistant output
  retain their existing Core semantics.
- The model sidecar remains provider/process transport only with JSONL stdout.
- Discord remains an adapter and owns no agent identity, memory, provider
  state, lifecycle, or canonical persistence.
- Tool calls/results are not automatically canonical history or memory.

## Evidence

- Zero-environment proof starts the real `LilavelRuntime`, yields once while it
  remains `running` and healthy with zero environments/tools/events, then
  reaches `stopped` without failure.
- Bounded-ingress tests fill a one-slot queue, prove a third submit blocks, and
  prove shutdown rejects a submit still blocked by backpressure.
- Settlement tests cover registered adapter cancellation, concurrent stop
  callers sharing one shutdown, accepted-event drain, and a missed shutdown
  deadline failing the instance closed.
- Package metadata proves the runtime has no required distribution dependency.
- Static guards reject Core importing its parent runtime, runtime importing
  Discord/Neuro implementations, and the Discord adapter importing Core
  persistence ownership.

The first complete Core test attempt had one one-second startup/shutdown timing
test fail while 170 tests passed. Without any Core runtime implementation
change, the focused test then passed and the complete suite passed 171 tests.
This is recorded as a transient initial `FAIL`, followed by the authoritative
final `PASS`; it is not presented as an unobserved success.

## Validation

| Check | Result | Evidence |
| --- | --- | --- |
| Runtime locked sync and lock check | `PASS` | `uv sync --locked`; `uv lock --check` |
| Runtime Ruff and formatting | `PASS` | all checks passed; 7 files formatted |
| Runtime Pyright strict | `PASS` | 0 errors, 0 warnings |
| Runtime pytest | `PASS` | 19 passed on Windows/Python 3.12.10 |
| Core lock, Ruff, formatting, Pyright | `PASS` | final canonical checks passed |
| Core pytest | `PASS` | focused rerun passed; final full run 171 passed |
| Model sidecar TypeScript/Bun | `PASS` | check passed; 67 tests and 223 assertions passed |
| Discord adapter deterministic package | `PASS` | lock/Ruff/format/Pyright passed; 80 tests passed |
| Documentation integrity | `PASS` | `scripts/check_docs.py` |
| Architecture guard | `PASS` | `scripts/check_architecture.py` |
| Whitespace guard | `PASS` | `git diff --check` |
| Live provider behavior | `UNVERIFIED` | excluded; no live command run |
| Live Discord behavior | `UNVERIFIED` | excluded; no live command run |
| Live Neuro interoperability | `UNVERIFIED` | production adapter not implemented |

No required validation item is `BLOCKED`.

## Unknowns

- The Phase 3 conversation-dispatch and positive-wake behavior is `UNKNOWN`.
- Tool authorization, execution, side-effect, cancellation, and continuation
  semantics are `UNKNOWN` and remain unimplemented.
- Scheduler/timer, durable world state, memory, and retrieval contracts are
  `UNKNOWN` and remain unimplemented.
- Whether later phases require dynamic environment/tool registration is
  `UNKNOWN`.
- Environment coroutines must cooperate with Python cancellation. A task that
  refuses cancellation makes shutdown hit its deadline and fail closed; Python
  cannot forcibly terminate such an in-process coroutine.

## Exit gate

| Requirement | Result |
| --- | --- |
| Real top-level persistent runtime exists | `PASS` |
| Starts without Discord/model/conversation activity | `PASS` |
| Remains healthy with zero environments | `PASS` |
| Shuts down cleanly and deterministically | `PASS` |
| Event ingress is bounded and tested | `PASS` |
| Existing Core/Conversation semantics are unchanged | `PASS` |
| Architecture docs and guards reflect ownership | `PASS` |
| Phase-close documentation is durable and agent-first | `PASS` |

PHASE 2 is `CLOSED` at implementation SHA
`941d041903aa7787bd6c0db749996681e646bd1e`.
