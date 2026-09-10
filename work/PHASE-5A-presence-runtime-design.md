# PHASE 5A — Presence Runtime Design + OMP Adoption Audit

Status: `CLOSED`
Baseline SHA: `0282031789e253e628ecdb621562a4d014c59ec0`
Implementation SHA at exit: `0282031789e253e628ecdb621562a4d014c59ec0` (no runtime implementation; documentation-only phase)

## Goal

Define the smallest safe persistent-presence boundary for the next phase and
audit the repository-pinned OMP dependency without adopting the OMP agent loop,
stateful Agent, or coding-agent session runtime.

## Scope

- Inspected the clean canonical repository baseline, current runtime/Core/
  sidecar/tool contracts, P4 records, ADRs, and validation guards.
- Mechanically audited the installed dependency graph and pinned
  `@oh-my-pi/pi-ai@18.1.2` package.
- Compared the pinned low-level transport with current upstream OMP agent and
  coding-agent/session layers only where that clarified ownership and timing.
- Designed the PRESENCE-V0 domain, event dispositions, wake/yield behavior,
  user interruption behavior, ephemeral state boundary, and the P5-B CLI
  vertical slice.
- Added [ADR-009](../docs/decisions/ADR-009-presence-runtime-and-omp-boundary.md)
  for the accepted ownership and adoption boundary.

## Non-goals

- No PRESENCE-V0 runtime behavior, scheduler, wake loop, CLI, new protocol,
  memory system, or production tool activation was implemented.
- No OMP package was upgraded, no `pi-agent` source was copied, and no OMP
  session/tool executor was embedded.
- No paid/live provider, Discord, or external-effect probe was run for this
  research phase.
- No current architecture, validation, historical phase record, or roadmap
  document was rewritten. There is no `docs/ROADMAP.md` in this repository.

## Decisions

- `USE` the existing `LilavelRuntime` process as the persistent host. Idle
  means that the host, bounded ingress, environment tasks, event router, and
  ephemeral working state remain alive; it does not mean a provider request is
  held open.
- `USE` discrete runtime-owned `CognitionRun` admissions. A wake is permission
  to consider one bounded run, not permission for an environment to call a
  provider and not an infinite inference loop.
- `USE` `WorldEvent → EventRouter → disposition → WakePolicy → CognitionRun`
  as the future presence control path. A raw event is not canonical history or
  long-term memory.
- `USE` `ConversationCore` as the sole semantic conversation/history owner and
  `ModelRuntime` plus the sidecar as the only provider-generation path.
  Autonomous cognition must receive a narrow Core-owned transient request seam
  in P5-B; it must not fake a user message or call the sidecar directly.
- `USE` the existing application-owned `ToolSession`/registry/executor seam
  for any future `USE_TOOL` outcome. OMP tool execution is not an authority
  boundary and cannot replace P4 authorization, effect certainty, or joined
  settlement.
- `USE` user input as the highest-priority `STEER` disposition while another
  run is active. It reuses Core supersession, physical cancellation, stale
  fencing, and provider/executor joined settlement.
- `USE` silence bias: `NO_WAKE` and successful `NO_ACTION` are normal terminal
  outcomes. A timer/opportunity is not an unconditional LLM heartbeat.
- `TAKE DIRECTLY` the already-adopted `pi-ai` provider abstraction and native
  tool transport only at the model-sidecar boundary.
- `ADAPT DESIGN` OMP steering, follow-up, aside, wake, and pre-yield ideas into
  runtime-owned dispositions and lifecycle gates.
- `SELECTIVELY PORT` only bounded ideas such as non-consuming queue peeks,
  explicit pre-model gates, and a pre-yield admission check. Do not port OMP
  classes, queues, transcript mutation, tool scheduling, or source code.
- `REJECT` low-level OMP `agentLoop`, the stateful `Agent` as Lilavel's host,
  OMP tool execution/concurrency, and OMP transcript/session persistence.

## Architecture consequences

The persistent host and the semantic conversation engine remain separate:

```text
environment
    → bounded WorldEvent ingress
    → EventRouter / ephemeral working state
    → OBSERVE, ASIDE, STEER, FOLLOW_UP, or WAKE
    → WakePolicy (NO_WAKE | WAKE)
    → one admitted CognitionRun
    → ConversationCore
    → ModelRuntime / pinned pi-ai sidecar transport
    → SAY, USE_TOOL, UPDATE_STATE, or NO_ACTION
    → BeforeYield (YIELD | bounded CONTINUE)
```

The diagram is a P5-B design target, not current implementation. In the
current repository, `apps/runtime` has bounded event ingress and deterministic
direct-message wake/routing, but no autonomous scheduler or model wake loop.
The current architecture therefore remains accurate and was not edited.

The runtime may request a provider only after it has admitted a
`CognitionRun` through Core. An environment can submit an observation or a
typed action at its boundary, but cannot force `generate`, bypass
authorization, choose a destination, or inject a second conversation
transcript. `WAKE` is a control decision, not a provider command.

For P5-B's single CLI scope, use one character-wide cognition admission lane.
Do not introduce cross-environment arbitration or concurrent autonomous runs
yet. Existing per-conversation Core sessions remain the semantic unit; the
presence slice targets one CLI session and one active run. A later multi-
environment design must explicitly decide whether a character-wide scheduler
or per-scope schedulers are safe.

### Autonomous Core seam required by P5-B

The current `ConversationCore.start_turn()` accepts and persists a user
message before creating a run. That is correct for user turns but is not an
autonomous admission API. P5-B must add a narrow Core-owned cognition entry
point (name deliberately left to implementation) with these properties:

1. It reads the Core-owned canonical history and trusted identity guidance.
2. It adds the wake cause and bounded working-state snapshot as transient
   request context, not as a canonical user message.
3. It creates the same logical-run/generation identity and cancellation fence
   as a user turn.
4. Its V0 `SAY` result is presented through the trusted CLI action boundary but
   is not written to canonical conversation history until a separate design
   defines an origin/commit policy for autonomous speech. This preserves the
   explicit rule that world events are not silently promoted to history or
   memory.
5. It can enter the existing V3 tool seam only if application composition
   exposes tools; P5-B does not enable model-selected tools in the CLI path.

If this seam cannot be implemented while preserving the existing Core
ownership and commit rules, the safe P5-B fallback is a deterministic
`NO_ACTION` wake proof, not a fake user message, direct sidecar call, or
second transcript.

## Inherited invariants

- `ConversationCore` owns canonical user/assistant role-text history and
  successful assistant commit; streamed, cancelled, superseded, failed, and
  stale output is noncanonical.
- There is one logical active run per Core conversation. A successor is
  admitted only after the predecessor's physical generation settles.
- `ModelRuntime` owns generation identity, epoch, one active generation,
  cancellation, shutdown, fail-closed cleanup, and stale event fencing.
- The sidecar owns provider/process transport and provider normalization; it
  does not own semantic history, character identity, memory, authorization, or
  application tools.
- P4 V3 joined settlement covers provider and application executor work. Exact
  ordered batches, generation/epoch/round/call fencing, unknown effect
  certainty, and uncontainable cleanup poisoning remain authoritative.
- `ToolSpec` grants no execution authority. Application composition owns tool
  exposure, validation, trusted-scope authorization, liveness, execution, and
  typed `ToolResult`/effect certainty.
- `WorldEvent` is an immutable provider-neutral, untrusted-by-default
  observation envelope. Ingestion does not imply memory or canonical history.
- Runtime ingress is bounded and backpressured; shutdown closes admission and
  settles owned tasks. A presence scheduler must use the same lifecycle, not
  detached background tasks.
- The default production path remains no-tool and no-autonomy until a later
  phase explicitly activates and validates it.

## Current repo evidence

### Preflight

The read-only preflight was performed at the canonical repository root:

| Check | Result |
| --- | --- |
| `git status --short --branch` | `PASS` — `## main...origin/main`; no changed files. |
| `git branch --show-current` | `PASS` — `main`. |
| `git rev-parse HEAD` | `PASS` — `0282031789e253e628ecdb621562a4d014c59ec0`. |
| `AGENTS.md` discovery | `PASS` — only the repository-root `AGENTS.md` applies; no nested scoped file was present. |
| root orientation | `PASS` — `README.md`, `docs/ARCHITECTURE.md`, `docs/VALIDATION.md`, `docs/STACK.md`, and `docs/MIGRATION.md` were read. |
| decision history | `PASS` — ADR-001 through ADR-008 and the P4-A through P4-E records were inspected. |
| roadmap | `PASS` — no `docs/ROADMAP.md` exists; no speculative roadmap was created. |

### Implemented boundaries at the baseline

| Area | Repository evidence | P5-A consequence |
| --- | --- | --- |
| Runtime host | [`apps/runtime/README.md`](../apps/runtime/README.md) and [`kernel.py`](../apps/runtime/src/lilavel_runtime/kernel.py) describe a persistent process owner, bounded `WorldEvent` queue, environment task group, lifecycle, and deterministic wake/routing. | The host is the correct future idle owner. A scheduler/wake loop is still absent. |
| Current runtime gap | [`README.md`](../README.md#explicitly-deferred), [`ARCHITECTURE.md`](../docs/ARCHITECTURE.md#deferred-boundaries), and [`apps/runtime`](../apps/runtime/README.md) defer scheduling, autonomous model wake, attention, memory, and durable agent state. | Presence is a new design boundary, not an existing capability to “turn on.” |
| Semantic Core | [`conversation.py`](../apps/core/src/lilavel_core/conversation.py) owns canonical history, user admission, one logical active run, streaming candidates, assistant commit, supersession, and stale-result fencing. | Autonomous admission must be a Core-owned extension, not a second runtime. |
| Physical model runtime | [`runtime.py`](../apps/core/src/lilavel_core/runtime.py) owns one active generation, IDs/epochs, cancellation, shutdown, containment, and fail-closed cleanup. | Every cognition run must use this lifecycle. |
| Model sidecar | [`apps/model-sidecar/README.md`](../apps/model-sidecar/README.md), [`index.ts`](../apps/model-sidecar/src/index.ts), and [`tool-loop.ts`](../apps/model-sidecar/src/tool-loop.ts) define provider/process transport, pinned pi-ai streaming, and an opt-in bounded V3 transport loop. | The sidecar remains a transport service; it is not the presence scheduler or transcript owner. |
| World-event seam | [`contracts.py`](../apps/runtime/src/lilavel_runtime/contracts.py) defines immutable untrusted `WorldEvent`; [`kernel.py`](../apps/runtime/src/lilavel_runtime/kernel.py) bounds ingress; [`conversation_router.py`](../apps/runtime/src/lilavel_runtime/conversation_router.py) maps accepted direct messages to Core turns. | Event ingestion and routing are reusable, but direct-message wake is not autonomous cognition. |
| Tools | [`apps/contracts/src/lilavel_contracts/tools.py`](../apps/contracts/src/lilavel_contracts/tools.py), [`tool_registry.py`](../apps/runtime/src/lilavel_runtime/tool_registry.py), [`tool_session.py`](../apps/runtime/src/lilavel_runtime/tool_session.py), and P4-C/P4-D/P4-E records define application-owned authorization/execution and conservative effect certainty. | `USE_TOOL` can only be a proposal entering this existing seam. |
| Character identity | [`character.py`](../apps/core/src/lilavel_core/character.py) and [`production_cognition.py`](../apps/core/src/lilavel_core/production_cognition.py) define static identity/guidance fixtures; [`scripts/character_runner.py`](../apps/core/scripts/character_runner.py) is a one-shot 30-record evaluation harness. | Identity exists as a Core-owned fixture but is not a persistent presence loop or durable memory. |
| CLI packaging | `find`/`rg` over tracked project files found package-local `pyproject.toml` files but no root `pyproject.toml`, root `uv.lock`, or `project.scripts` entry point. | P5-B must add the smallest launcher/package composition that makes `uv run lilavel` real from the canonical root, or explicitly document an equivalent invocation. |

### P4 evidence carried into presence design

P4-A through P4-E establish the constraints that an autonomous path must not
weaken:

- P4-A/ADR-007 adopts native pi-ai tool transport while keeping calls/results
  outside canonical history and keeping authorization/execution in Lilavel.
- P4-B closes the bounded V3 multi-turn loop with joined provider/application
  settlement, exact ordered result batches, stale fencing, and fail-closed
  uncontainable cleanup.
- P4-C adds immutable per-generation exposure, strict validation, trusted
  scope, bound sequential execution, and typed effect certainty.
- P4-D proves one explicitly composed Discord effect without making Discord
  the host or enabling tools by default.
- P4-E closes deterministic/provider evidence for its scope while leaving
  autonomous scheduling, restart-safe agent state, and other presence behavior
  outside the implemented contract.

## Current OMP pinned-version evidence

### What is actually pinned and installed

The authoritative dependency declaration is
[`apps/model-sidecar/package.json`](../apps/model-sidecar/package.json), and
the lock is [`bun.lock`](../apps/model-sidecar/bun.lock):

- Direct OMP dependencies are `@oh-my-pi/pi-ai@18.1.2` and
  `@oh-my-pi/pi-catalog@18.1.2`.
- The direct lock entries carry pi-ai integrity hash
  `sha512-ifMrkvMkSmgqGVpRP9TXZa7wLPuMBQ6csrRhsv4QXKYHsP+KMsbdc261gqbky2QzipK866yQl9zhaYJNriD6HA==`
  and pi-catalog integrity hash
  `sha512-68OHAsouEGhqzSnRPtntoSi7E15jDh9wkU5S4PsHwFun48G1X3QHvPCPEBNHvcZKs3Px+olOX7QpXrBOcStRsA==`.
- The locked graph contains `@oh-my-pi/omptype`, `pi-utils`, `pi-wire`,
  `pi-natives`, and the Linux native addon at `18.1.2`.
- Installed package metadata identifies the repository as
  `git+https://github.com/can1357/oh-my-pi.git`, `packages/ai`, and license
  `MIT`.
- The installed `@oh-my-pi` package roots were mechanically enumerated. No
  `pi-agent`, `pi-agent-core`, or `pi-coding-agent` package is installed by
  this repository's sidecar dependency graph.

The installed package was audited, not inferred from the name “OMP.” The
following searches were run under
`apps/model-sidecar/node_modules/@oh-my-pi/pi-ai`, excluding its changelog and
source maps:

| Search term | Result in pinned `pi-ai@18.1.2` | Interpretation |
| --- | --- | --- |
| `getSteeringMessages` | `0` implementation/source hits | Not a pi-ai API. |
| `hasSteeringMessages` | `0` | Not a pi-ai API. |
| `getFollowUpMessages` | `0` | Not a pi-ai API. |
| `getAsideMessages` | `0` | Not a pi-ai API. |
| `onBeforeYield` | `0` | Not a pi-ai API. |
| `beforeModelCall` | `0` | Not a pi-ai API. |
| `triggerTurn` | `0` | Not a pi-ai API. |
| `agentLoop` | Textual references in README/comments only; no implementation/export | The pinned package does not contain the agent loop. |
| `AgentSession` | Textual references in comments only; no class/export | The pinned package does not contain a session runtime. |

The pinned package does contain the lower-level mechanisms the current sidecar
uses:

- `stream`, `complete`, and `streamSimple` are per-request provider streaming
  and completion functions. `AbortSignal` is a request cancellation input.
- Provider-facing native tool types, streamed `toolcall_*` events, tool-call
  argument normalization, and validation helpers are available. `pi-ai` does
  not execute Lilavel application tools.
- `sessionId` and `providerSessionState` are provider/request transport
  options. They are not a semantic conversation store, and the current
  sidecar does not use them as canonical transcript persistence.
- `onSseEvent` is diagnostic/provider-event observation, not a transcript or
  agent scheduling hook.

The pinned package README's direct usage shape is a caller-owned loop: create
context, call the provider, inspect a native tool call, execute application
logic, append a tool result, and make the next provider call. The current
repository instead places that bounded continuation at the Core/sidecar plus
application `ToolSession` seam already proven by P4.

### Layer distinction

| Layer | Pinned repository status | What it owns | What it does not establish |
| --- | --- | --- | --- |
| `pi-ai` | Installed and pinned at `18.1.2` | Provider abstraction, provider request/response types, native tool transport, stream cancellation, normalization helpers, optional provider session metadata. | No persistent character, steering queues, agent transcript, application authorization, or tool executor. |
| Low-level OMP `@oh-my-pi/pi-agent-core` / `agentLoop` | Not installed or pinned. Current upstream package metadata inspected at `18.1.16`, a newer non-pinned version. | A higher loop around `pi-ai`: repeated provider turns, AgentMessage context, tool validation/execution, steering/follow-up/aside queues, interruption and deadlines. | No authority to replace Lilavel Core or P4 application settlement if embedded. |
| Stateful `Agent` | Part of the separate low-level agent package, not pinned here. | In-memory mutable `AgentState`, messages, active run state, steering/follow-up queues, abort/wait-for-idle controls, loop configuration. | It is not the repository's canonical history, durable memory, or trusted tool policy. |
| Higher-level coding-agent/session/extension APIs | Not installed or pinned. Current upstream docs/source inspected only for layer attribution. | On-disk coding-agent transcript/session management, reload/branch/compaction, extension delivery (`steer`, `followUp`, `aside`, `triggerTurn`), managed timers, and session hooks. | These APIs are not evidence that the pinned sidecar has presence behavior. |
| Lilavel sidecar/Core/runtime | Implemented locally | Core history/run semantics, ModelRuntime lifecycle, provider transport, application tool authorization/execution, bounded runtime ingress. | Autonomous scheduling/wake, durable agent state, and presence behavior are not implemented yet. |

### Current-upstream comparison used for the audit

Current upstream OMP `main` was inspected on 2026-09-10 for source shape only;
it was not added to the lockfile and does not change the pinned-version result.
The useful primary references are the
[`agent-loop.ts`](https://raw.githubusercontent.com/can1357/oh-my-pi/main/packages/agent/src/agent-loop.ts),
[`agent.ts`](https://raw.githubusercontent.com/can1357/oh-my-pi/main/packages/agent/src/agent.ts),
[`agent` types](https://raw.githubusercontent.com/can1357/oh-my-pi/main/packages/agent/src/types.ts),
separate [`pi-agent-core` package metadata](https://raw.githubusercontent.com/can1357/oh-my-pi/main/packages/agent/package.json),
the higher-level [`coding-agent AgentSession`](https://raw.githubusercontent.com/can1357/oh-my-pi/main/packages/coding-agent/src/session/agent-session.ts),
and the [`extension delivery documentation`](https://raw.githubusercontent.com/can1357/oh-my-pi/main/docs/extensions.md).

Observed upstream behavior:

- `agentLoop()` creates an `EventStream`, copies prompts into
  `currentContext.messages`, and owns a repeated `runLoop`; continuation can
  resume a context with an unpaired tool-call tail.
- `getSteeringMessages()` is consumed at loop-start and after a settled tool
  batch. `hasSteeringMessages()` is a non-consuming peek used by the
  interrupt watcher while tools run. Current code uses an internal 250 ms
  fallback where the event-driven wait hook is not supplied.
- Steering can abort in-flight interruptible waits and skip not-yet-started
  interruptible work, while already-emitted calls still execute. The queue
  retains ownership until the loop reaches an injection boundary.
- `getFollowUpMessages()` can continue the loop after it would otherwise stop;
  `getAsideMessages()` injects non-interrupting context at a step boundary and
  keeps the loop running. Both become AgentMessage entries in the OMP context.
- `onBeforeYield()` runs just before the low-level loop would end and before
  follow-up polling. `beforeModelCall()` runs after provider-bound context is
  prepared and can stop the next request without billing it.
- The low-level tool path validates and executes `AgentTool.execute`, emits
  tool events, adds tool results to the live AgentMessage context, and supports
  shared/exclusive/function concurrency. This is more authority and more
  transcript ownership than Lilavel can safely delegate.
- The separate coding-agent extension layer documents `deliverAs` values and
  `triggerTurn: true` for idle/internal continuation. Its session layer also
  persists/reloads its own transcript and supports branching/compaction. Those
  are not low-level `pi-ai` or pinned sidecar features.

If OMP source is copied or substantially ported in a later phase, OMP's MIT
license and required copyright/license notices must be preserved. P5-A copied
no source and therefore adds no third-party source notice.

## Low-level `agentLoop()` embedding investigation

Embedding the upstream low-level loop is not safe as a direct replacement for
the current boundary. `agentLoop()` would own a second logical loop around
`pi-ai`, including `AgentMessage` context mutation, repeated provider calls,
tool-call validation, tool-result insertion, tool scheduling, and internal
abort/steering state. `ConversationCore` would still need to own canonical
history, user admission, generation identity, stale fencing, and terminal
settlement. That is two lifecycle engines and two possible sources of
conversation truth.

A proxy integration would not remove the conflict. It would need to:

1. translate OMP AgentTool calls to Lilavel `ToolCall` values;
2. route every call through the immutable exposure snapshot, trusted scope,
   application executor, and P4 `ToolSession`;
3. hold OMP's tool-loop continuation until the exact ordered Lilavel batch has
   settled, including unknown effects and uncontainable cleanup;
4. fence OMP events against Core's run/generation/epoch/round identity;
5. prevent OMP from persisting or treating its own AgentMessage context as
   canonical history; and
6. translate additional queue, aside, yield, and cancellation events over a
   new sidecar IPC contract if the OMP loop runs in the sidecar process.

That proxy would make OMP's executor and transcript a competing lifecycle
engine while adding a protocol adapter. It could also let OMP's concurrency or
interrupt semantics claim progress before Lilavel has joined provider and
executor settlement. The safe conclusion is `REJECT` direct embedding and
`SELECTIVELY PORT` only the sequencing ideas into a Lilavel-owned coordinator.
The existing `pi-ai` stream/native-tool transport remains sufficient.

## OMP adoption matrix

The matrix uses these verdict meanings:

- `TAKE DIRECTLY` — already safe and intentionally used at the transport
  boundary; it does not transfer semantic authority.
- `ADAPT DESIGN` — useful behavior, but re-express it in Lilavel-owned types,
  queues, and lifecycle.
- `SELECTIVELY PORT` — retain a bounded idea, not the OMP API/class/source.
- `REJECT` — do not adopt the mechanism as a runtime authority.
- `UNKNOWN` — no pinned package/version exists from which behavior can be
  established; do not infer it from current upstream.

| Candidate mechanism | Actual layer and evidence | State owner | When it runs / can it launch a provider request? | Transcript ownership | Tool scheduling/execution | Cancellation | P4/auth/history/IPC consequence | Verdict |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| pi-ai provider abstraction | Pinned `@oh-my-pi/pi-ai@18.1.2`; `stream*`/`complete*` and provider types. | pi-ai stream plus Lilavel sidecar/Core lifecycle. | On an explicitly admitted sidecar generation; yes, it is the low-level provider request path. | No semantic transcript; caller supplies context. | No Lilavel application execution. | `AbortSignal` is passed to the stream; sidecar/Core own cleanup and settlement. | Matches P4 if kept below Core; no authorization bypass, second history, or IPC change beyond existing protocol. | `TAKE DIRECTLY` |
| pi-ai native tools | Pinned pi-ai native tool types, `toolcall_*` transport, argument normalization/validation; P4-A/ADR-007. | Sidecar owns provider representation; Lilavel owns exposure/auth/executor. | During an admitted provider turn; may return tool calls but does not itself schedule the next application action. | Calls/results remain transient evidence, not Core history. | pi-ai transports/normalizes; Lilavel `ToolSession` executes. | Sidecar/Core cancellation joins provider and app work at the P4 boundary. | Safe only with immutable snapshot, trusted scope, effect certainty, stale fencing, and joined settlement; no new IPC. | `TAKE DIRECTLY` |
| pi-ai `sessionId` / `providerSessionState` | Pinned `pi-ai` request options; provider-session/cache metadata, not AgentSession. | Provider adapter/stream caller. | Read/written per provider request; can affect provider-side state but is not a wake trigger. | No semantic transcript in Lilavel; current sidecar does not persist it. | None. | Request signal only; provider state cleanup semantics are provider-specific. | Do not make it canonical or use it as memory; using it would need explicit provider evidence, not an OMP adoption. | `REJECT` as semantic state; retain only if a later transport decision proves safe |
| Stateful `Agent` class | Separate upstream `@oh-my-pi/pi-agent-core`; absent from lock. Current source has mutable `AgentState`, queues, abort, and run controls. | OMP Agent instance. | `prompt`/`continue` starts the low-level loop and therefore provider calls. | Agent owns mutable AgentMessage state and append/replace operations. | Delegates to the agent loop's tools and results. | Agent abort controller plus loop signals; no automatic P4 joined-settlement guarantee. | Would create a second run/history owner and could bypass Lilavel auth unless every tool is proxied; no direct fit without a new IPC/state bridge. | `REJECT` |
| Low-level `agentLoop` / `agentLoopContinue` | Separate upstream `packages/agent`; absent from pinned graph; current source owns `runLoop`, context copy, repeated turns, and continuation. | The loop's `currentContext`, `EventStream`, config, and caller callbacks. | Runs at prompt/continuation and after tools/queues; yes, it can issue multiple provider requests under one OMP stream. | It appends prompts, assistant/tool messages, and results to OMP context. | Owns validation, scheduling, `AgentTool.execute`, result insertion, and continuation. | External signal plus internal steering/soft controllers/deadline; tool-specific interruptibility differs from P4. | Direct embedding risks two lifecycle engines, premature continuation, tool-auth bypass, second transcript, and likely sidecar IPC expansion. | `REJECT` direct; `SELECTIVELY PORT` ordering ideas only |
| Steering queues / `getSteeringMessages` | Low-level upstream agent types/loop; absent from pinned pi-ai. Consumed at loop start and after settled tool batches. | OMP Agent/session queue callback. | Queue drain can cause the loop to issue another provider request; the callback itself is not a provider call. | Drained messages are injected into OMP AgentMessage context. | OMP loop owns any following tool batch. | External abort leaves queue intact; internal immediate mode can abort eligible waits. | OMP queue semantics do not perform Core canonical user admission or P4 join; direct injection would create a second truth. | `ADAPT DESIGN` as Lilavel `STEER` |
| `hasSteeringMessages` | Low-level upstream non-consuming peek; absent from pinned pi-ai. Used by a 250 ms/event-driven interrupt watcher. | OMP queue owner. | Poll/check during a running tool batch; may cause the OMP loop to continue later, but does not itself call provider. | No mutation at peek; eventual dequeue mutates OMP context. | OMP decides which interruptible waits to abort/skip; already-emitted work still runs. | Cooperative/interruptible only; not a joined application settlement barrier. | Useful non-consuming observation, but exact semantics are weaker than P4 unknown-effect/join guarantees. | `SELECTIVELY PORT` a bounded peek, with Lilavel fences |
| Follow-up / `getFollowUpMessages` | Low-level upstream callback; absent from pinned pi-ai. Polled after no tool calls and no steering. | OMP queue/session. | Returned messages keep the same OMP loop alive and cause another provider request; not a direct call by the callback. | Appended to OMP AgentMessage context. | OMP tool loop continues if the next response selects tools. | Usually waits for the current run; subject to OMP abort/deadline, not Core successor join. | Direct use would make “same OMP run” compete with Core's discrete cognition identity and could admit after incomplete app settlement. | `ADAPT DESIGN` as a runtime queued next `CognitionRun` |
| Aside / `getAsideMessages` | Low-level upstream callback; absent from pinned pi-ai. Polled after tool batches and at yield; never aborts in-flight tools. | OMP aside queue/session. | Injection can keep OMP loop alive and cause another provider request; callback itself is not a provider call. | OMP appends it as ordinary AgentMessage context and may commit/discard via OMP rules. | Does not interrupt current batch; OMP schedules the next batch. | Non-interrupting by design, but not a Lilavel executor barrier. | Direct use would turn observations into a second prompt/history; Lilavel must keep aside transient and noncanonical. | `ADAPT DESIGN` as runtime `ASIDE` |
| `onBeforeYield` | Low-level upstream hook; absent from pinned pi-ai. Called just before the OMP loop exits and before follow-up polling. | OMP Agent configuration/callback. | Hook itself need not call provider, but its result/side effects can cause another OMP iteration or external work. | OMP context remains owner. | OMP tool loop remains owner. | Signal/deadline handling belongs to OMP loop; no P4 joined-settlement contract. | A direct hook could launch work after Core thinks a run is done or mutate a second transcript. | `SELECTIVELY PORT` as a trusted, bounded Lilavel `BeforeYield` gate |
| `beforeModelCall` | Low-level upstream pre-model hook; absent from pinned pi-ai. Runs after provider context preparation and can return `stop` before request/billing. | OMP loop/config callback. | It can prevent a request; the loop launches the provider if it returns proceed. | OMP context already prepared/owned. | Tool state is still OMP-owned. | Receives loop signal/deadline; not enough to join external app executors. | Useful no-request gate idea, but provider-bound context and callback authority must not replace WakePolicy/Core admission. | `SELECTIVELY PORT` as an application-owned preflight, not the OMP hook |
| `triggerTurn` / idle turn triggering | Current upstream coding-agent extension API, not low-level pinned pi-ai; documented with `pi.sendMessage`/managed timers. Exact term absent from pinned package. | Higher-level coding-agent session/extension context. | Idle delivery can start a provider turn; streaming delivery queues an internal continuation. | Higher-level session/Agent context owns injected messages and persistence. | Higher-level session delegates to OMP Agent loop/tools. | Session `abort`/shutdown plus loop semantics; not Lilavel joined settlement. | Direct adoption would let extensions/environment callbacks launch providers and violate “WAKE is permission, not force.” | `ADAPT DESIGN` as runtime `WakePolicy` and scheduler admission |
| OMP transcript/session persistence | Low-level Agent is in-memory; durable transcript/reload/branch/compaction is separate coding-agent `AgentSession`; neither is pinned. | OMP Agent state or coding-agent `sessionManager`. | Reload/branch prepares a future OMP call; persistence itself does not need a provider. | OMP owns a competing AgentMessage/session transcript. | Session replays persisted tool messages through OMP tool machinery. | Session shutdown/abort semantics are separate from Core's physical settlement. | Would create a second conversation truth and memory boundary; no direct IPC fit and no reason to migrate existing SQLite Core history. | `REJECT` |
| OMP tool executor | Low-level upstream `AgentTool.execute` plus validation/result coercion; absent from pinned pi-ai. | OMP agent loop/session and tool definitions. | Runs after OMP model tool calls; does not itself call provider, but results can cause another OMP provider turn. | OMP appends tool calls/results. | OMP validates, invokes, normalizes, emits, and schedules tools. | Per-call signal/interruptibility; OMP does not know Lilavel effect certainty or uncontainable poison. | Would bypass or duplicate application authorization, destination binding, effect status, and P4 joined settlement unless proxied; proxy creates two engines. | `REJECT` |
| OMP concurrency/tool scheduling | Low-level upstream `shared`/`exclusive`/function concurrency and `Promise.allSettled`; absent from pinned pi-ai. | OMP loop's batch scheduler. | During each OMP tool batch; next provider call follows settlement as OMP defines it. | OMP batch/result messages are transcript-owned. | Can overlap shared calls and use cooperative steering for interruptible work. | Immediate steering is selective; already-emitted calls continue; no universal app settlement barrier. | Conflicts with P4's deliberately sequential exact ordered batch and conservative unknown effects; risks external effects before joined settlement. | `REJECT` direct; revisit only under a separate effect/concurrency ADR |
| Pinned `pi-agent` version behavior | No `pi-agent`/`pi-agent-core` package is declared or installed in this repository. | No repository owner/evidence. | Cannot establish pinned behavior. | Cannot establish pinned transcript semantics. | Cannot establish pinned tools/scheduling. | Cannot establish pinned cancellation. | Do not upgrade or infer this from upstream main during P5-A. | `UNKNOWN` |

### Embedding conclusion

Low-level `agentLoop()` cannot be embedded “safely” while Lilavel continues to
own canonical history, authorization, executor lifecycle, effect certainty,
generation identity, and settlement without a proxy executor and two competing
lifecycle engines. The correct P5-A outcome is to keep the current
`ModelRuntime`/sidecar boundary and reimplement only the required presence
coordination in the Python runtime/Core boundary.

## PRESENCE-V0 state and lifecycle model

### Host states

PRESENCE-V0 adds no provider-held idle state. The host lifecycle is the existing
runtime lifecycle with a presence-specific substate:

```text
NEW → STARTING → IDLE
              ↘ ACTIVE_COGNITION
IDLE ↔ OPPORTUNITY_CHECK
ACTIVE_COGNITION → SETTLING → IDLE
any live state → STOPPING → STOPPED
failure/uncertain cleanup → FAILED (no silent restart)
```

- `IDLE`: process, event ingress, CLI reader, router, wake clock, and bounded
  working state are alive; no provider request is in flight.
- `OPPORTUNITY_CHECK`: a timer or explicit opportunity wakes the cheap policy
  evaluator. It is not a model run.
- `ACTIVE_COGNITION`: exactly one bounded `CognitionRun` is admitted for the
  V0 CLI scope. A V3 tool continuation remains subject to P4 bounded rounds.
- `SETTLING`: cancellation, provider, executor, and presentation tasks are
  joined before a successor is admitted.
- `STOPPING`: ingress closes, timers/adapters cancel, active run is
  superseded/cancelled, and owned tasks settle. No new wake/provider request
  is admitted.

### Domain ownership table

| Concept | Owner and lifetime | Trust / persistence | ConversationCore relationship | ToolSession relationship | Cancellation and provider authority |
| --- | --- | --- | --- | --- | --- |
| `WorldEvent` | Environment adapter creates the immutable envelope; runtime owns it from bounded ingress through disposition. | Payload is untrusted by default; event lifetime is bounded queue/route lifetime; no raw-event persistence or memory promotion. | Never automatically appended. A user event becomes a Core user turn only after trusted route admission. | No tool authority. An event cannot carry scope, destination, permission, or executor. | Dropping/cancelling a queued route cancels handling only. It cannot claim an external effect was undone. It cannot call a provider. |
| `EventRouter` | Runtime composition boundary; process lifetime; registered before startup and closed during shutdown. | Router policy/code is trusted; event payload remains untrusted. No semantic persistence. | Selects the Core-facing action; does not mutate history. | May select an application route but cannot execute a model tool. | Route tasks inherit runtime cancellation; no direct provider request. |
| `OBSERVE` | Runtime working-state update; event/derived record lifetime. | Control metadata trusted; source content untrusted; bounded ephemeral only. | No history or memory write. | None. | Does not preempt or create a provider request; can be dropped on shutdown. |
| `ASIDE` | Runtime queue attached to the active cognition scope until the next safe boundary or run terminal. | Ephemeral, noncanonical context; source remains untrusted; no persistence. | Core may receive it through a future transient request seam; it is never silently appended as a user/assistant message. | Cannot interrupt or alter a running tool batch. | If run is superseded, aside is dropped or reclassified by runtime; no provider call by itself. |
| `STEER` | Runtime admission/control; user input is the primary source; lifetime until superseded run accepts/settles it. | Priority/origin is trusted after route authentication; message text is untrusted model input; user text is canonical only when Core accepts the user turn. | User `STEER` calls Core's existing `start_turn(..., supersede=True)`, appends the user message, fences old output, and starts the successor after join. Non-user steering must use an explicit transient policy. | Cancels/join-waits an active session if the run owns tools; never bypasses authorization. | Highest active-work priority below shutdown. It requests cancellation; it does not claim immediate physical stop or rollback. |
| `FOLLOW_UP` | Runtime queue for an internal/autonomous next opportunity; consumed after the current run settles. | Ephemeral control/context; no persistence; user input is not silently downgraded to this mode. | Starts a new Core-owned discrete cognition/user run only at a legal successor boundary. No direct transcript injection. | Any next tool use re-enters a fresh P4-authorized session/generation or the explicitly bounded existing V3 continuation. | Does not preempt current work. Shutdown drops it; user `STEER` supersedes it. |
| `WAKE` | Runtime scheduler/policy signal; lifetime is one opportunity/admission decision. | Trusted policy result, bounded evidence only; not history or memory. | May request a Core-owned autonomous cognition seam; wake itself is not a message. | No tool access until a cognition run produces a model proposal and the application admits it. | No direct provider request. It is ignored when stopping, when user input is pending, during cooldown, or when the lane is active. |
| `WakePolicy` / `NO_WAKE ∣ WAKE` | Runtime-owned deterministic policy; process lifetime/config lifetime. | Trusted code/config; policy inputs are bounded working-state summaries; no run transcript persistence. | Reads facts needed for admission but does not compose semantic history itself. | No tool access. | Cheap and cancellable. `NO_WAKE` is the normal result; `WAKE` only permits one bounded run. |
| `CognitionRun` | Runtime owns orchestration metadata; Core owns semantic run admission; ModelRuntime owns physical generation; lifetime ends only after joined settlement. | Correlation IDs/status are trusted runtime evidence; model text/tool args are untrusted until normal Core/application checks. No separate transcript or durable state. | Reads Core history through a Core-owned request path; interactive turns use existing Core semantics; V0 autonomous output is noncanonical unless a later commit policy is accepted. | `USE_TOOL` enters the existing immutable snapshot, authorization, executor, effect-certainty, and joined-settlement seam. | One logical identity/fence. User input supersedes it through Core; cancellation joins provider and app work; stale output is discarded. Only Core/ModelRuntime can launch provider work. |
| `SAY` | Runtime/environment presentation outcome after an accepted completed candidate; destination is trusted application binding. | Text is model output, not authority; no separate persistence in V0. Safe aggregate activity evidence only. | User-turn SAY follows Core assistant commit. Autonomous SAY is presented but not canonical in V0. | None. | A pre-send cancellation fence may suppress it; once an output write is accepted, it cannot be retracted. It does not initiate a provider request. |
| `USE_TOOL` | Application-owned tool admission result; runtime/Core coordinates, registry/session/executor owns effect. | Model call/args untrusted; typed result and effect certainty are bounded evidence; no canonical history. | Core does not turn calls/results into conversation messages. | Sole path to application execution; P4 auth, scope, sequential order, settlement, and unknown-effect rules remain. | Existing P4 cancellation/join/fence; no retry for unknown effect; successor waits or runtime is poisoned if uncontainable. |
| `UPDATE_STATE` | Runtime owns bounded ephemeral working-state mutation. | Only validated, bounded summaries; no durable memory in V0. | Not canonical history; may be compiled into later transient guidance only through a Core-owned seam. | None unless a future explicit state tool is authorized; model cannot write arbitrary state. | Cancelled/stale runs cannot update state after the fence; shutdown discards unpersisted state. |
| `NO_ACTION` | Cognition coordinator terminal outcome. | Trusted outcome code; no persistence. | No history mutation or assistant commit. | No tool execution. | Successful normal termination; no retry or failure escalation. |
| `BeforeYield` / `YIELD ∣ CONTINUE` | Runtime-owned bounded decision after a cognition run/turn; lifetime one terminal boundary. | Trusted control; no persistence. | `YIELD` lets Core/run settle. `CONTINUE` admits a new bounded discrete run; it does not create an OMP-style infinite loop. | Any continuation must pass normal P4 admission and join. | If cancelled/stopping, force `YIELD`/settlement. `CONTINUE` is allowed only with pending higher-priority context and remaining budget. |

### Event disposition semantics

An event may produce an `OBSERVE` update and, independently, one control
disposition (`ASIDE`, `STEER`, or `FOLLOW_UP`) plus a `WAKE` decision. The
router must not turn every event into all five actions. The deterministic order
is:

1. accept or reject the bounded `WorldEvent`;
2. record safe arrival metadata and update bounded working state (`OBSERVE`);
3. classify any message/control disposition using source and runtime state;
4. run the cheap `WakePolicy` if the event is an opportunity;
5. enqueue or admit only the disposition permitted by the current lifecycle;
6. let Core/ModelRuntime/ToolSession own any actual cognition or effect.

| Disposition | Meaning in Lilavel | Deliberate difference from OMP |
| --- | --- | --- |
| `OBSERVE` | Record/derive a bounded runtime fact; never wakes cognition by itself and never interrupts. | OMP context hooks often prepare messages; Lilavel keeps observation outside history and can remain silent. |
| `ASIDE` | Passive context for the next safe cognition boundary; does not cancel text generation or a tool batch. | Similar timing to OMP aside, but not an OMP AgentMessage and never a second transcript. |
| `STEER` | Priority input/control. User input supersedes active work through Core and is admitted as the user's canonical turn. | OMP steering may selectively interrupt only interruptible waits inside a tool batch; Lilavel makes the logical run supersession and joined settlement authoritative. |
| `FOLLOW_UP` | Queue work for after the current run settles. It creates a separate bounded successor opportunity. | OMP follow-up continues the same agent loop/context; Lilavel intentionally uses a new Core-controlled discrete run. |
| `WAKE` | Permission to evaluate/admit an autonomous run if all gates pass. It contains no prompt authority and no provider call. | OMP `triggerTurn` can launch an idle turn from a higher-level session; Lilavel requires runtime-owned policy and admission. |

Raw payloads remain untrusted in every case. Trusted source binding can decide
priority and destination, but it cannot make model content, tool arguments, or
external IDs authoritative.

## Wake and yield semantics

The intended idle sequence is:

```text
IDLE
  → timer/opportunity or explicitly admitted environmental opportunity
  → cheap deterministic WakePolicy
  → NO_WAKE → IDLE
  → WAKE + lane/cooldown/user-pending checks
  → one bounded CognitionRun
  → NO_ACTION | SAY | authorized USE_TOOL | bounded UPDATE_STATE
  → BeforeYield
      → YIELD → joined settlement → IDLE
      → CONTINUE only with pending context and remaining run budget
```

Rules:

- The timer is a managed, bounded runtime task and is cancelled during
  shutdown. It is an opportunity clock, not a rapid provider heartbeat.
- `NO_WAKE` is normal. A policy that lacks a strong opportunity should do
  nothing; silence is not a failed cognition.
- A wake cannot overlap an active user/autonomous run. It may queue a bounded
  follow-up or be dropped by cooldown, but it cannot start a second provider
  request.
- A `WAKE` cannot bypass user input, Core admission, ToolSession authorization,
  or ModelRuntime readiness. Provider work starts only after all gates pass.
- `BeforeYield.CONTINUE` is not a hidden infinite loop. It is a bounded
  successor admission with a run budget and the same cancellation/join fence.
- A provider response that yields no useful action maps to successful
  `NO_ACTION`; it does not trigger immediate resampling.

## User interruption semantics

User input always wins over autonomous activity. The existing Core
`start_turn(..., supersede=True)` path is the semantic authority.

| Situation | Required behavior |
| --- | --- |
| User input while idle | Admit a `WorldEvent` through the CLI/environment boundary, classify it as user `STEER`, append the canonical user message in Core, and start one Core/ModelRuntime run. Any pending autonomous wake/follow-up is cleared or superseded. |
| User input while generating text | Core accepts/appends the new user message and makes its logical run active immediately; request physical cancellation of the old generation; fence old deltas; the new run worker waits for old runtime settlement before asking the single-generation runtime to start. Only the successful successor may commit assistant text. |
| User input while a tool executes | Core accepts/appends the user's canonical message and makes its run logically active; request ToolSession/ModelRuntime cancellation; wait for provider and executor joined settlement. Do not claim rollback or retry an unknown effect. The successor's provider request waits for that barrier; an uncontainable executor poisons reuse. |
| User input while an autonomous cognition run is active | Treat it as user `STEER`, never as a passive OMP aside/follow-up. Core accepts the user turn and makes it logically active, cancel/supersede the autonomous run, fence its candidate/output, join all owned work, and let the user's run request the provider after the barrier. |
| User input while an autonomous follow-up is queued | Remove the pending autonomous successor and admit the user turn. The autonomous item cannot jump ahead of the user. |
| Shutdown during any situation | Close admission, cancel timers/adapters/runs, join provider/tool/presentation work within existing deadlines, and fail closed on uncertainty. No new provider request is admitted after shutdown begins. |

The deliberate difference from OMP immediate steering is important: OMP's
interruptible-tool behavior is a useful cooperative latency optimization, but
it is not the authority for Lilavel's logical supersession or effect
settlement. Lilavel may later add cooperative tool hints, but it must retain
the P4 join and unknown-effect rules.

## Short-lived state boundary

Before durable memory exists, PRESENCE-V0 needs only one bounded,
process-ephemeral `WorkingState` per CLI character scope:

| Field | Purpose | Boundary |
| --- | --- | --- |
| `last_interaction_at` | Apply cooldown and determine whether an opportunity is recent enough to consider. | Timestamp/monotonic value; not memory and not model text by itself. |
| `current_activity` | Small application-owned status such as `idle`, `awaiting_user`, or `finishing_response`. | Enum/status only; no raw event or provider payload. |
| `open_thread` | Whether the last accepted interaction left a bounded unresolved follow-up opportunity. | Boolean or bounded code; cleared by resolution, `NO_ACTION`, cooldown, or shutdown. |
| `current_disposition` | Current runtime control stance for routing, if needed. | Enum such as `neutral`, `awaiting_reply`, `cooldown`; not inferred as durable emotion or personality memory. |
| `recent_autonomous_action` | Last autonomous outcome/status for duplicate suppression and evidence. | Bounded code/time/effect certainty; no raw speech, args, destination, or IDs. |
| `cooldown_until` | Prevent repeated wake attempts. | Monotonic deadline; never a provider heartbeat. |

`recent_topics`, rich relationship state, long-term preferences, autobiographic
claims, and durable memory are deliberately absent. If a later Core request
compiler includes a working-state field in provider context, it must include
only the bounded validated summary and must not silently turn the field into
canonical history. Working state is discarded on shutdown/restart in V0;
restart-safe persistence requires a later explicit memory/state decision.

## P5-B proposed CLI vertical slice

P5-B should implement only the visible path below:

1. Add the smallest root launcher that makes `uv run lilavel` invoke a
   `lilavel_runtime` CLI. Keep the CLI composition in `apps/runtime`; use the
   existing package-local Core and contracts rather than a new agent framework.
2. Start one persistent runtime/CLI process with one CLI environment adapter,
   one Core scope, one cognition lane, the existing model-sidecar lifecycle,
   and a managed input reader.
3. Convert each non-empty input line into a provider-neutral `WorldEvent` and
   route it as a user `STEER`/Core turn. Stream accepted assistant text to
   stdout; keep safe runtime activity/evidence (state, run IDs/status codes,
   wake/no-action) on a separate stderr or explicitly selected event stream.
4. Add the narrow Core-owned transient autonomous cognition seam described
   above. Keep model-selected tools disabled in the CLI composition.
5. Add one bounded timer/opportunity path after an interaction. Its cheap
   policy can emit `WAKE` once when `open_thread` and cooldown gates permit;
   it must otherwise emit `NO_WAKE` and remain idle.
6. Map one successful self-initiated text candidate to CLI `SAY`; map an empty
   or explicit safe no-op result to deterministic `NO_ACTION`. Do not commit
   autonomous output to canonical history in V0.
7. Make Ctrl-D/Ctrl-C and an explicit shutdown path close admission and settle
   the runtime cleanly. A user line during generation must supersede it.

P5-B is not a general scheduler, multi-environment character bus, memory
system, OMP session, or autonomous tool runtime. It should use fake/injected
clock and model/runtime seams for deterministic tests. An optional manually
authenticated provider run may demonstrate the CLI, but provider success is
not substituted for the deterministic lifecycle proof.

## P5-B deterministic scenario matrix

These are exact provider-independent scenarios P5-B must later prove. Each
scenario should assert safe activity/evidence codes and bounded counts rather
than recording raw prompts, model payloads, credentials, or exception bodies.

| ID | Deterministic sequence | Required proof |
| --- | --- | --- |
| B1 idle liveness | Start CLI with a fake model and no input; advance the fake clock through at least one opportunity. | Process/runtime remains alive; `IDLE` is observable; `NO_WAKE` produces zero provider requests and zero output; no detached task or rapid retry appears. |
| B2 user turn | Submit one CLI line while idle; fake generation emits accepted/deltas/completed. | Exactly one `WorldEvent`, one canonical user message, one Core run/generation, streamed output, one successful assistant commit, and return to idle. |
| B3 input preempts text | Start a long fake generation; submit a second CLI line before completion; release the first cancellation/settlement. | Second user run becomes logically active; first deltas after supersession are discarded; first run settles once; second run commits; no duplicate provider or history entry. |
| B4 wake to SAY | Establish bounded `open_thread`, advance clock past cooldown, let policy return `WAKE`, and fake the autonomous response. | One wake opportunity creates at most one autonomous `CognitionRun`; CLI receives one self-initiated `SAY`; no synthetic canonical user message and no autonomous history commit; return to idle/cooldown. |
| B5 wake to NO_ACTION | Same as B4 but fake cognition returns the explicit safe no-op/empty result. | `NO_ACTION` is successful evidence; zero CLI output, no history mutation, no immediate resample, and normal idle state. |
| B6 wake suppression | Generate repeated timer opportunities during cooldown, with no new user event. | All extra opportunities are `NO_WAKE`/suppressed; at most one provider request for the bounded opportunity; no heartbeat loop. |
| B7 user beats autonomous | Start an autonomous run, then submit a user line before its candidate completes. | User is classified as `STEER`, autonomous run is cancelled/superseded and fenced, user turn is admitted only after joined settlement, autonomous `SAY` is not emitted after supersession, and user output is the only committed response. |
| B8 tool settlement inheritance | In a test-only composition expose one P4 fake slow tool; start tool execution, submit user input, then make the executor settle confirmed or unknown. | User input requests cancellation but does not claim rollback; provider and executor join; unknown effect is never retried; successor waits for settlement; uncontainable cleanup poisons reuse. This must reuse/inherit P4 tests, not introduce a weaker presence cancellation path. |
| B9 disposition boundaries | Submit equivalent fake events that classify as `OBSERVE`, `ASIDE`, `STEER`, `FOLLOW_UP`, and `WAKE` in controlled active/idle states. | Each disposition has the documented queue/interrupt/provider/history effect; `OBSERVE` never creates history/provider work; `ASIDE` does not abort tools; `FOLLOW_UP` waits; `WAKE` is only permission. |
| B10 no environment force | Have the CLI adapter submit an observation with a wake-like payload while policy returns `NO_WAKE`; then repeat with policy `WAKE`. | First case causes no provider request. Second case causes exactly one request only after runtime admission. Payload cannot select model, tool, scope, destination, or provider command. |
| B11 clean shutdown idle | Start, remain idle, request shutdown with timers pending. | Ingress closes; timers cancel; no wake/provider request occurs after shutdown; process exits cleanly and repeatedly requested shutdown is harmless. |
| B12 clean shutdown active | Start a generation (and, in the inherited tool fixture, an executing tool), then shutdown. | Cancellation/joined settlement and existing fail-closed deadlines are observed; no stale CLI output after shutdown; no successor is admitted; uncertain cleanup is `FAILED`, not falsely clean. |
| B13 CLI packaging | Run the exact root command `uv run lilavel` in a controlled test environment and send one line followed by EOF. | The command resolves without an alternate shell script, produces the expected streamed/final output and safe activity evidence, then exits cleanly at EOF. |

## Alternatives considered

| Alternative | Result | Reason |
| --- | --- | --- |
| Adopt the whole OMP `Agent`/`agentLoop` runtime | `REJECT` | It owns transcript mutation, tool execution/scheduling, repeated provider calls, and cancellation semantics that conflict with Core/P4 joined settlement. |
| Put presence scheduling in the sidecar | `REJECT` | Sidecar must remain provider/process transport; scheduler state, identity, authorization, and canonical history would cross the wrong boundary and require new IPC. |
| Call `ModelRuntime` directly from an environment adapter | `REJECT` | Bypasses runtime wake policy, Core conversation semantics, user supersession, and trusted destination/action routing. |
| Treat every WorldEvent as a user message | `REJECT` | Violates observation/history separation and makes ambient events canonical without policy. |
| Use an unconditional periodic LLM heartbeat | `REJECT` | Violates silence bias, spends provider requests without an opportunity, and makes `NO_ACTION` an expensive loop outcome. |
| Use OMP `aside`/`followUp` messages verbatim | `REJECT` direct; `ADAPT DESIGN` semantics | OMP messages become its Agent transcript and can keep one loop alive; Lilavel needs noncanonical bounded dispositions and separate Core runs. |
| Persist OMP AgentSession alongside Core SQLite history | `REJECT` | Creates two conversation truths and an unapproved memory/session boundary. |
| Make autonomous speech immediately canonical assistant history | `REJECT` for V0 | Current Core commit is user-turn anchored; V0 has no approved autonomous-origin commit schema. Present first, design memory/history origin later. |
| Add a dedicated presence memory database now | `REJECT` | Durable agent state and memory remain explicitly deferred; bounded working state is enough for the vertical slice. |
| Keep P5-A as design-only with no new ADR | `REJECT` for this phase | ADR-006 explicitly requires a separate decision for future wake/state boundaries; ADR-009 records the accepted consequence without implementing it. |

## Unknowns

- `UNVERIFIED` — the exact P5-B Core API shape for transient autonomous
  request context and whether autonomous `SAY` should eventually have a
  canonical assistant-origin record. P5-A deliberately chooses noncanonical
  V0 output until that decision is made.
- `UNVERIFIED` — whether a single character-wide lane remains sufficient once
  multiple environment scopes can be active, and how cross-environment
  priority should be arbitrated.
- `UNVERIFIED` — production wake-policy signals, opportunity sources, and the
  default cooldown/quiet-hours policy. P5-B should use injected clock and one
  bounded timer, not claim a general scheduler.
- `UNVERIFIED` — live provider latency/cost/entitlement behavior for repeated
  presence opportunities; no live probe was authorized or needed for P5-A.
- `UNVERIFIED` — provider-specific behavior if `pi-ai` provider session state
  is later enabled; it is not semantic persistence in the current design.
- `UNVERIFIED` — whether any future OMP version changes the cited agent or
  coding-agent semantics; the repository has no pinned agent package and P5-A
  does not upgrade dependencies.
- `UNVERIFIED` — Windows-specific CLI/process-containment evidence for the
  future presence launcher; P5-A ran on Linux and changed no launcher.
- `UNVERIFIED` — autonomous tool activation. P5-B intentionally keeps it off;
  any later activation requires the existing P4 application-owned path and a
  new deterministic/live proof.

## Evidence

Repository authority:

- [`README.md`](../README.md)
- [`docs/ARCHITECTURE.md`](../docs/ARCHITECTURE.md)
- [`docs/VALIDATION.md`](../docs/VALIDATION.md)
- [`docs/STACK.md`](../docs/STACK.md)
- [`docs/MIGRATION.md`](../docs/MIGRATION.md)
- [`apps/core/README.md`](../apps/core/README.md)
- [`apps/runtime/README.md`](../apps/runtime/README.md)
- [`apps/model-sidecar/README.md`](../apps/model-sidecar/README.md)
- [`apps/model-sidecar/package.json`](../apps/model-sidecar/package.json)
- [`apps/model-sidecar/bun.lock`](../apps/model-sidecar/bun.lock)
- [`ADR-001`](../docs/decisions/ADR-001-core-owns-canonical-conversation-state.md),
  [`ADR-003`](../docs/decisions/ADR-003-core-generation-lifecycle.md),
  [`ADR-006`](../docs/decisions/ADR-006-lilavelruntime-agent-boundary.md),
  [`ADR-007`](../docs/decisions/ADR-007-model-tool-calls-remain-transport-only.md)
- [`P4-A`](PHASE-4A-tool-contracts-transport-proof.md),
  [`P4-B`](PHASE-4B-deterministic-tool-loop.md),
  [`P4-C`](PHASE-4C-application-tool-authorization.md),
  [`P4-D`](PHASE-4D-real-discord-tool-proof.md), and
  [`P4-E`](PHASE-4E-provider-live-tool-validation.md)

OMP references used for layer attribution only:

- [OMP `pi-agent-core` package metadata](https://raw.githubusercontent.com/can1357/oh-my-pi/main/packages/agent/package.json)
- [OMP low-level agent loop](https://raw.githubusercontent.com/can1357/oh-my-pi/main/packages/agent/src/agent-loop.ts)
- [OMP stateful Agent](https://raw.githubusercontent.com/can1357/oh-my-pi/main/packages/agent/src/agent.ts)
- [OMP low-level agent types](https://raw.githubusercontent.com/can1357/oh-my-pi/main/packages/agent/src/types.ts)
- [OMP coding-agent session](https://raw.githubusercontent.com/can1357/oh-my-pi/main/packages/coding-agent/src/session/agent-session.ts)
- [OMP extension delivery semantics](https://raw.githubusercontent.com/can1357/oh-my-pi/main/docs/extensions.md)

The upstream links describe current `main`, not the repository's pinned
dependency. The pinned-version conclusion comes from the local package
declaration, lockfile, installed package metadata, and mechanical symbol
search described above.

## Validation

- `PASS` — preflight `git status`, branch, and exact baseline SHA as recorded
  above.
- `PASS` — repository source/lock/package inspection and mechanical OMP symbol
  search completed without modifying tracked files.
- `PASS` — `git diff --check` after the documentation changes.
- `PASS` — `python scripts/check_docs.py` after the documentation changes.
- `PASS` — `python scripts/check_architecture.py` after the documentation
  changes; no source ownership boundary changed.
- `PASS` — no paid/live provider, Discord, or platform-specific probe was run;
  none was required to establish the local architecture or pinned package
  facts.

No package code or dependency graph changed, so package test suites were not
rerun as a consequence of P5-A. Existing P4 validation evidence remains the
authoritative evidence for the inherited lifecycle/tool contracts; it is not
relabelled as presence behavior.

## Exit gate

The phase is complete when a fresh engineer can answer from this record and
the linked repository docs:

| Question | Answer |
| --- | --- |
| What remains alive while idle? | The Lilavel host, bounded ingress, environment/CLI tasks, router, managed opportunity clock, and bounded ephemeral working state; no provider inference. |
| What causes cognition to wake? | A bounded timer/opportunity or explicit policy input that passes `WakePolicy`; `WAKE` permits one run, while `NO_WAKE` is normal. |
| What are the dispositions? | `OBSERVE` records bounded state; `ASIDE` adds passive transient context; `STEER` supersedes active work; `FOLLOW_UP` queues a later run; `WAKE` only permits evaluation/admission. |
| Who owns canonical history? | `ConversationCore` and its Core-owned store. |
| Who owns tool authorization/execution? | Lilavel application composition, registry, trusted scope, `ToolSession`, and bound executor; not OMP or the model. |
| Can an environment force a provider request? | No. It can submit a `WorldEvent`; only runtime admission through Core/ModelRuntime can launch provider work. |
| How does user input preempt autonomous activity? | User input is priority `STEER`; Core makes it logically active, cancels the old run, fences stale output, joins provider/tool settlement, then admits the user run. |
| What state exists before durable memory? | Bounded process-ephemeral interaction/activity/open-thread/disposition/action/cooldown state; no durable memory or raw event transcript. |
| Which OMP mechanics are reused? | Pinned pi-ai provider/native-tool transport directly; queue peek, pre-model gate, and pre-yield ordering only as Lilavel-owned design ideas. |
| Which OMP mechanics are not reused? | `Agent`, `agentLoop`, OMP transcript/session persistence, OMP tool executor, direct steering/follow-up/aside queues, `triggerTurn`, and OMP concurrency scheduling as authorities. |
| What must P5-B demonstrate? | `uv run lilavel` persistent CLI liveness, user turn/streaming, idle/NO_WAKE, bounded wake to SAY, deterministic NO_ACTION, user preemption, inherited tool settlement, safe activity evidence, and clean EOF/Ctrl-C shutdown. |

## Accepted decisions

- ADR-009 accepts the runtime/Core/ToolSession ownership boundary and the
  rejection of OMP agent-loop/session adoption for PRESENCE-V0.
- No current implementation architecture was changed in P5-A. The next code
  phase must satisfy this record before claiming presence behavior.
