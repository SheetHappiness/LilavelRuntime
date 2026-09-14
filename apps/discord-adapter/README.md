# Lilavel Discord environment adapter

This package is a replaceable, DM-only Discord environment adapter and the
application composition root for its live process. Its environment component
owns Discord observation/presentation only; `LilavelRuntime` owns routing and
Core/ModelRuntime session lifecycle. Core does not depend on Discord or
`discord.py`. The Python import namespace remains `lilavel_discord_edge` as a
compatibility detail of the migrated package.

## Stage A: isolated transport probe

The probe does not create a `ConversationCore` or call Luna. It requests only
the Discord `DIRECT_MESSAGES` intent, waits for one human-authored one-to-one
DM, starts a typing context, sends a synthetic response, edits the same
response with coalesced chunks, and exercises safe continuation messages over
2000 characters. It reports operation timings, surfaced HTTP 429 observations,
and reconnect events without printing credentials or message contents.

The only supported credential variable is `LILAVEL_DISCORD_BOT_TOKEN`. If it
is absent, the probe exits successfully with a deterministic `BLOCKED` report;
that is not live evidence.

The event callback only completes the transport exercise and signals its
completion. The application-owned probe runner owns the `Client.start()` task,
closes the client after that signal, awaits both the Discord start/connect task
and the callback settlement, and then computes `shutdown_clean`. A clean value
requires the awaited client close to complete, `client.is_closed()` to be true,
the start/connect task to finish normally, the event callback to have settled,
and the observable websocket to be closed. It does not treat an empty global
`asyncio` task set as a shutdown contract.

```powershell
Set-Location apps/discord-adapter
uv sync --locked
uv lock --check
uv run --locked python scripts/transport_probe.py
```

With the variable configured, start the probe and send one DM to the bot. The
probe closes the client after the transport exercise. discord.py owns gateway
reconnect/resume behavior; this one-shot probe reports whether a reconnect or
resume event actually occurred, but does not manufacture a disconnect.

## Stage B: Runtime-backed DM environment

Normal DM sessions follow
`Discord → WorldEvent → Observation admission → explicit cognition gate →
CognitionTrigger → SemanticActor(USER) → ConversationExecutionAdapter →
CoreConversationRouter → ConversationCore → ModelRuntime → typed presentation
action → Discord`.
Runtime composition uses Core-owned `production_cognition.create_conversation`;
injected custom `core_factory` values remain responsible for their own
guidance. Admission is observation-only; the explicit reactive step runs the
deterministic direct-message gate and submits the resulting user episode to the
character-wide actor.

```powershell
uv run --locked python scripts/run_edge.py
```

Each direct-message channel remains adapter metadata and maps to a private,
opaque runtime subject. Only the opaque subject and message text enter the
`WorldEvent`; Discord channel, message, and author IDs stay adapter-local and
never enter Core history or a `ModelRequest`. Runtime returns a bounded
admission receipt before the adapter requests the reactive response step. The
runtime gate emits one bounded direct-message trigger, then submits a stable
observation-derived request identity to the single character-wide actor. The
actor serializes the episode and maps that subject to its own
`ConversationCore`/`ModelRuntime` session. No provider continuation or new
provider state is added.

Only `MESSAGE_CREATE` is handled. Bot/self-authored messages, guild messages,
group DMs, edits, deletes, and imported Discord history are ignored. A bounded
4096-entry in-memory message-ID LRU admits a message before the Core call, so
Gateway replay within one process cannot create a second semantic turn. The
bound and process lifetime are explicit limitations; there is no durable
Discord idempotency store in this slice.

The actor owns semantic admission, priority, serialization, preemption, replay
fencing, and settlement. Discord user work has `USER` priority; queued non-user
temporal/MIND work waits behind an active conversation, and active non-user work
is cooperatively cancelled before a user successor begins. Core remains
responsible for canonical history, successful assistant commit, one active run
per conversation, supersession, and physical cancellation.
Runtime consumes semantic run events and issues trusted presentation
`ToolCall`s to the source environment; these are not model-selected tools.
The adapter starts typing before Core/model work and gives each response an
owned presenter pump. The first meaningful
delta materializes a message as soon as practical; later deltas update one
latest-text snapshot. At most one Discord reconciliation is in flight, and a
configurable minimum interval (currently `1.0` second by default) between
publish starts limits new intermediate attempts without imposing a separate
fixed wait after every edit. When an edit completes, a newer snapshot is
published on the next eligible wake-up; a timer wake-up prevents queued deltas
from waiting for an unrelated future delta. A terminal event is the only
operation that can bypass the interval and forces exactly one final
reconciliation. Every create and edit passes `AllowedMentions.none()`.
Baseline text is split on Unicode code-point boundaries into messages of at
most 2000 characters. Cancelled or superseded output is marked
`[response interrupted]`;
failed output is marked `[response unavailable]` and failed assistant text is
never committed by Core.
When diagnostics are enabled, each run's presentation scope carries
`core_run_id`, an edge-local alias of the opaque Core `ConversationRun.run_id`.
It is attached only to the allowlisted presentation/HTTP evidence and never
enters Core history or canonical Discord state.

The default presentation mode is semantic streaming. It publishes the first
meaningful delta immediately, then prefers safe newline/sentence/clause/word
boundaries while retaining the raw latest-snapshot text internally. Set
`LILAVEL_DISCORD_SEMANTIC_STREAMING=0` to disable semantic presentation for a
diagnostic run or rollback to the supported raw latest-snapshot baseline. Set
the variable to `1` to state the normal default explicitly. The semantic
algorithm and its bounded lookahead are otherwise unchanged.

Semantic mode still publishes the first meaningful delta immediately. At later
publish opportunities it chooses a raw prefix near the tail, keeping a small
hidden tail (80 characters by default). Normal prose prefers newline/paragraph,
then sentence punctuation, clause punctuation, and finally a word boundary.
It waits at most 125 ms for a stronger boundary and then uses a safe word or
grapheme fallback; it never waits for a complete sentence indefinitely. Inside
fenced code, newline and whitespace are preferred and prose punctuation is
ignored. Prefix selection and continuation splitting use Unicode grapheme
boundaries, including emoji ZWJ sequences and combining marks.

For display only, an unmatched fenced-code delimiter is temporarily closed and
an unmatched inline backtick is temporarily paired. The raw generated text is
rebuilt for every publish and successful terminal completion publishes the
exact full text without repair. If a temporary preview needed an extra
continuation message, terminal reconciliation deletes only that presenter-owned
extra message so the final Discord message sequence remains exact. Link and
emphasis balancing is intentionally out of scope for this small experiment.

The current `ModelRuntime` admits one physical generation at a time. Keeping
one runtime with each mapped Core session preserves that existing boundary and
isolates histories; the actor serializes those sessions character-wide without
collapsing their Core histories. A future shared-runtime scheduler would be a
separate design decision.

## P4-D explicit Discord tool proof

The default `DiscordTextEdge` remains V2/no-tool. The only model-facing
capability in the explicit P4-D composition is:

```text
discord.send_message({"text": string})
```

Its schema requires only non-empty text bounded to Discord's 2000-character
message limit and rejects additional properties. The provider alias
`discord_send_message` is a sidecar transport detail; it is not an authority
or registry key. The adapter binds the executor to the current admitted
one-to-one DM's local channel reference through the opaque runtime subject and
the Core scope. The model can select the action and supply text, but cannot
select a channel, user, recipient, or other authority.

The executor sends at most once for one accepted `ToolCall`, always passes
`AllowedMentions.none()`, and returns only bounded typed outcome metadata. A
known pre-send failure is `failed/effect=none`; a confirmed client success is
`ok/effect=confirmed`; an HTTP rejection is `failed/effect=none`; and a timeout,
cancellation during an in-flight request, or opaque post-submit failure is
`failed/effect=unknown`. Unknown delivery is never retried and cancellation is
never described as rollback. Tool calls/results, Discord IDs, payloads, and
exception text remain outside canonical history, trusted guidance, and model
evidence.

The deterministic D1 proof uses the real registry, immutable exposure,
authorization, adapter executor, fake Discord transport, and explicit V3
fixture. It covers allowed, unexposed, wrong-scope, injected-destination,
oversized, mention-like, pre-send, ambiguous, cancellation, no-retry, history
separation, and provider continuation behavior. A provider-backed/live D2 DM
is not part of the deterministic package suite and remains `UNVERIFIED` unless
the supported provider and Discord credentials are deliberately available for
one scoped test DM.

## PROACTIVE-V0 opt-in one-shot idle smoke

The bounded proactive smoke experiment is disabled by default. Enable it only
with `LILAVEL_DISCORD_PROACTIVE_SMOKE=1`; the idle interval is configured with
`LILAVEL_DISCORD_PROACTIVE_IDLE_S`, defaulting to `30.0` seconds and accepting
only `1.0` through `600.0`. Invalid values fail before Discord client startup.

When enabled, the first eligible one-to-one DM binds one process-local trusted
subject and channel. A successful USER turn reuses the existing completion
`APPRAISAL_REASON` path; only a live runtime-owned intention arms one one-shot
idle opportunity. Expiry submits the existing `INTERNAL` `IDLE_REASON` through
the character-wide `SemanticActor` and existing
`ProposalApplicationCoordinator`. The model can choose silence or speech text,
but cannot choose a Discord destination. Permitted speech uses the existing
text-only `discord.send_message` application tool and effect-time intervention
guard; silence, denial, failure, cancellation, and unknown delivery consume the
opportunity without retry. A second DM subject disables proactive external
speech for the process while normal reactive DM routing continues.

There is no recurring loop, guild/group-DM support, persistence, memory write,
restart recovery, or proactive canonical conversation-history turn. The
content-free `DiscordTextEdge.proactive_evidence()` surface reports only
bounded lifecycle/effect categories for deterministic smoke diagnostics.

Proactive cognition is effect-free in the production composition. Its
`ModelRuntimeV3` is created without a `tool_session_factory`, so both
`APPRAISAL_REASON` and `IDLE_REASON` provider requests expose zero application
tools. The existing `DiscordToolSessionFactory` is created separately and is
owned only by `ProposalApplicationCoordinator`; it remains the trusted,
effect-time path for the text-only `discord.send_message` action.

For a live, content-free terminal-stage trace, set
`LILAVEL_DISCORD_PROACTIVE_DIAGNOSTICS=1` together with the smoke switch. The
normal `scripts/run_edge.py` process emits bounded JSONL records to stderr,
including startup configuration, generation completion/failure, strict
appraisal (`no_change`/`create_intention`/`invalid`) and idle
(`speak`/`stay_silent`/`invalid`) parse outcomes, permission, and delivery
results. The default is `0`; diagnostics contain no user text, model output,
intention text, Discord identifiers, provider response, credentials, or raw
exception text. Sink failures are ignored by the runtime.

## Thin deterministic JSONL scenario runner

The repo-local runner exposes the existing deterministic evidence surfaces in
one agent-facing stream. Run it from this package directory:

```powershell
uv run --locked python scripts/scenario_runner.py success
uv run --locked python scripts/scenario_runner.py supersession
```

Supported scenarios are `success`, `supersession`, `sidecar-failure`,
`malformed-frame`, and `discord-interruption`. They are offline: the first,
third, and fourth use the committed Core fake-sidecar fixture through the real
`ModelRuntime`; supersession and Discord interruption reuse the existing Core
semantic test double and the production edge/presenter diagnostics. The
successful and sidecar-failure scenarios join Core semantic evidence to
physical generation evidence. The Discord scenario joins Core run IDs to the
edge-local presenter flush, operation, HTTP request, and presentation outcome
chain. The semantic-double scenarios intentionally have no physical evidence
surface in their result.

Every stdout line is one JSON object. The first line is scenario metadata,
source evidence retains `core`, `physical`, and `discord` distinctions, and the
last line is a deterministic runner result. It includes only Core-owned or
edge-local correlation IDs, epochs, safe event categories, aggregate counts,
and typed outcomes. Timestamps, request text, delta text, URLs, headers,
bodies, exception messages, credentials, provider/session IDs, and Discord
channel/message IDs are omitted. Unexpected runner failures use stderr and a
nonzero exit; an expected sidecar failure is a successful scenario observation
with a typed `failed` result.

This is a bounded fixture/evidence surface, not a simulator, replay framework,
durable evidence store, live provider/Discord check, restart proof, or M4D
evaluation. It proves only the selected deterministic path and its recorded
joins.

## Package checks

```powershell
uv lock --check
uv run --locked ruff check .
uv run --locked ruff format --check .
uv run --locked pyright
uv run --locked pytest
```

The package deliberately contains no guild ambient listening, slash commands,
voice, DAVE, persistence, memory, character behavior beyond the single
explicit P4-D proof tool, MCP, or `AgentSession` integration.

## Optional HTTP diagnostics

The edge can emit safe JSONL diagnostics to stderr for message `POST`, `PATCH`,
and semantic-preview cleanup `DELETE` operations. This is disabled by default
and uses discord.py's public `http_trace` hook; it does not change presenter
cadence or retry behavior.

```powershell
$env:LILAVEL_DISCORD_HTTP_DIAGNOSTICS = "1"
uv run --locked python scripts/transport_probe.py 2> diagnostics.jsonl
uv run --locked python scripts/run_edge.py 2> diagnostics.jsonl
```

Records include monotonic timing markers for the presenter flush, awaited
discord.py operation, HTTP request start, and the response status/headers
available at aiohttp's response trace point. Correlated presentation records
join `core_run_id` to the local `flush_id`, `operation_id`, and HTTP
`request_id`; a safe `presentation_outcome` records completion, interruption,
or failure and its structured reason. Only the message route category is
retained. URLs, authorization headers, request/response bodies, message text,
exception messages, channel/message IDs, and raw bucket identifiers are never
emitted.
`post_response_gap_s` is the measured time from the last traced HTTP response
to the awaited discord.py operation return; it includes body handling and any
library lifecycle wait, so it is evidence for (not a direct hook into) a
discord.py pre-emptive rate-limit sleep.

When semantic mode and diagnostics are both enabled, `semantic_selection`
records add only boundary class, raw/visible/hidden character counts,
lookahead duration and cap, hidden-tail age, preview-repair status, and whether
the selected snapshot changed Discord. Response text is never included.

## Pacing experiment override

`run_edge.py` accepts the optional `LILAVEL_DISCORD_EDIT_INTERVAL_S` environment
variable. It is intended for controlled pacing experiments such as `0.8`,
`1.0`, and `1.2` seconds; invalid values fail before the Discord client starts.
The first POST remains immediate, while intermediate updates use the configured
interval and terminal reconciliation retains its force semantics.

The semantic experiment is independent of this interval override. For example:

```powershell
$env:LILAVEL_DISCORD_EDIT_INTERVAL_S = "1.0"
uv run --locked python scripts/run_edge.py
```

To run the raw baseline explicitly:

```powershell
$env:LILAVEL_DISCORD_EDIT_INTERVAL_S = "1.0"
$env:LILAVEL_DISCORD_SEMANTIC_STREAMING = "0"
uv run --locked python scripts/run_edge.py
```
