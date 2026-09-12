# Lilavel persistent-agent kernel

`apps/runtime` is the top-level process owner for Lilavel. It can start and
remain healthy with no environments, conversations, model runtime, sidecar, or
provider. It owns bounded observation admission, a recent observation window,
a deterministic observation-to-cognition gate, registered environment tasks,
explicit reactive routing, Core conversation sessions, and the destination
choice for typed environment presentation actions.

MIND-1C adds a separate `CognitionEpisodeRunner`. A positive
`CognitionTrigger` can be given a bounded immutable observation/MindState
snapshot and an effect-free cognition engine, producing one validated
`CognitionOutcome`. The outcome contains only typed inert proposals; MIND-1C
does not apply state, execute actions, schedule work, write memory, or append
conversation messages. The existing explicit DM/Core route remains unchanged;
MIND-1D will own proposal validation, application, and authorization.

MIND-1B establishes both `observation != cognition` and the compatibility path
for the existing DM experience. Admission is a complete operation; it does not
invoke the cognition gate, Core, a model, tools, or a scheduler. The legacy DM
path calls `reactive_step()` explicitly after it receives an admission receipt;
that compatibility alias runs the deterministic gate first. Only a positive
`CognitionTrigger` reaches `CoreConversationRouter`, which owns session/runtime
creation and relays semantic Core events as runtime-generated, trusted
`ToolCall` presentation actions to the source environment. These are
application actions, not model-selected tools.

## Contracts

- `WorldEvent` is an immutable provider-neutral external-event envelope. Its
  payload is deep-frozen and its default trust is `untrusted`.
- `Observation` is the runtime-admitted form of a `WorldEvent`; its
  `ObservationReceipt` proves admission into the bounded recent window. The
  event remains untrusted observation data and is not promoted to memory or
  canonical conversation history.
- `ObservationWindow` is finite in-memory working state. It evicts the oldest
  entries at capacity and is not memory, canonical history, or durable storage.
- `CognitionGate` is a runtime-owned policy seam over admitted observations. It
  returns exactly `NO_COGNITION` or a bounded `CognitionTrigger` containing one
  or more observation IDs. `DirectMessageCognitionGate` recognizes only the
  existing `direct_message` event kind and can coalesce an explicit ordered
  batch without timers or model work.
- `CognitionContext` and `CognitionEpisode` are immutable bounded snapshots.
  They contain the trigger evidence, selected admitted observations, runtime
  scope, and a versioned `MindStateSnapshot`; external payload trust is
  preserved as observation data.
- `CognitionEpisodeRunner` serializes one episode per runtime scope, invokes
  only an effect-free `CognitionEngine`, and returns a `CognitionOutcome` only
  after strict candidate validation. `StateProposal` is currently limited to
  creating a typed intention; `ActionProposal` is limited to speak/silence
  intent and has no destination, permission, scope, or executor authority.
  Failure, cancellation, and timeout return no valid outcome.
- `EnvironmentAdapter.run()` is a long-lived observation source owned by the
  kernel task group; `execute()` is its typed action boundary. Registration
  closes when startup begins.
- `ToolSpec`, `ToolCall`, and `ToolResult` remain provider-neutral envelopes.
  Phase 3 uses trusted runtime-generated calls for presentation only;
  registration grants no model tool authority.
- `admit_observation()` and its `submit()` compatibility alias only validate,
  adopt, and return a receipt for an event. `cognition_step()` is the explicit
  gate-and-route path and accepts only an admitted receipt. `reactive_step()`
  remains its response-compatible alias.
- The cognition gate is not wired into observation admission. The default
  direct-message policy is deterministic and provider-neutral; wake/attention
  policy remains a separate deferred concern.

## MIND-1B cognition gate

The explicit sequence is:

```text
WorldEvent
  → admit_observation()
  → ObservationReceipt
  → cognition_step(receipt)
      ├── NO_COGNITION → stop
      └── CognitionTrigger → existing reactive/Core route
```

The default gate recognizes only the already implemented `direct_message`
event kind, preserving the current Discord DM route. Other event kinds end in
`NO_COGNITION`. A caller may give the policy an explicit ordered batch; the
policy emits at most the configured number of unique observation IDs in one
trigger. The runtime compatibility step intentionally evaluates one receipt at
a time, so no timer, debounce, scheduler, salience model, or autonomous loop
is introduced here.

## MIND-1C bounded cognition episode

MIND-1B and MIND-1C have deliberately different negative meanings:
`NO_COGNITION` means that no cognition engine was invoked, while a quiet
`CognitionOutcome` means cognition completed and returned zero proposals. The
MIND-1C path is:

```text
CognitionTrigger
  → freeze bounded CognitionContext
  → one serialized CognitionEpisode
  → validate CognitionCandidate
  → inert CognitionOutcome
  → STOP
```

The runner snapshots the trigger's admitted observations and current bounded
`MindState` before inference. Later admissions cannot change that episode's
context. It has no Core, Discord, tool executor, scheduler, memory, or
conversation-history capability, so successful state/action proposals remain
inert. MIND-1D is the future owner of proposal validation/application and
effect authorization. No Character snapshot is added here because the runtime
repository has no supported Character composition seam at this boundary.

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
This seam is not activated by the default production kernel or ordinary
Discord composition. P4-D adds one explicit adapter-owned composition path
for a scoped deterministic Discord proof; it does not change the default
V2/no-tool route or grant general model-selected external authority.

## P4-D route-specific composition seam

`CoreConversationRouter` retains its zero-argument runtime factory for the
ordinary path and also supports a trusted route-keyed runtime factory plus a
session configurator. `DiscordTextEdge(tool_enabled=True)` uses that seam only
for an explicitly enabled run: the adapter resolves the already admitted
opaque DM subject to its local channel reference, creates the one-tool
`DiscordToolSessionFactory`, constructs the explicit V3 runtime, and binds the
factory to the newly created Core scope before generation. The trusted channel
reference and Core scope never enter model history or the provider request.

The route-specific seam is composition, not policy discovery. It exposes only
the application-selected snapshot supplied by the adapter, while the existing
P4-B joined provider/executor settlement, cancellation barrier, and stale-result
fencing remain authoritative.

`admit_observation()`/`submit()` waits when bounded ingress is full. It does not
drop, overwrite, or silently accumulate pending events. The recent observation
window is separately bounded and evicts its oldest transient entry when full.
Events are rejected before startup and after shutdown begins. Admission does
not run the cognition gate or route; only an explicit `cognition_step()` (or
its `reactive_step()` compatibility alias) can start the legacy response path.

## P5-B1 local presence

The optional local presence component is started, monitored, and stopped by
`LilavelRuntime`. It owns one bounded input queue and one cognition lane over a
shared `ConversationCore`/`ModelRuntimeV3`. Normal user input remains canonical;
idle cognition never calls `ConversationCore.start_turn()` and never writes
history.

True idle is a monotonic event-or-timeout wait with a one-shot latch. The safe
default policy is `NO_WAKE`; explicit deterministic configuration may admit one
run. Autonomous generations receive only `presence.say` and
`presence.stay_silent`, while normal conversation generations receive no
presence tools. The action executes through the existing application registry
and tool-session path. Provider continuation text is consumed but suppressed,
so one `say` produces exactly one noncanonical local utterance.

MIND-0 adds a bounded in-memory `MindState` to this local composition. After a
successful normal turn, one tool-free transient `MindAppraiser` may return
strict JSON for `no_change` or one short `create_intention` result. The runtime
binds the resulting intention to the completed Core user/assistant message IDs.
Idle admission requires an active intention and passes that specific intention
to autonomous cognition. A successful `presence.say` marks it expressed and
records one bounded recent `SelfAction`; `presence.stay_silent` leaves it
active. The read-only `MindProjection` is composed into later normal-turn
guidance, so self-action context is available without adding autonomous speech
to canonical history. This state is intentionally not persisted and does not
apply to the Discord adapter.

From the repository root, run:

```powershell
uv run --locked lilavel
```

`--wake-on-idle` enables deterministic autonomous admission for a controlled
proof. `--wake-after-opportunities N` deterministically records earlier
`NO_WAKE` decisions before waking on opportunity `N`; production defaults
remain silent.

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
