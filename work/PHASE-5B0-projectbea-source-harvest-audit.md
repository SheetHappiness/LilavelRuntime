# P5-B0 — ProjectBEA Source Harvest Audit

Status: `CLOSED`
Audit date: 2026-09-10
Baseline SHA: `a1f29cf3746a24b22632d135d5f045be9c1abafc`
Implementation SHA at exit: `a1f29cf3746a24b22632d135d5f045be9c1abafc` (no runtime implementation)
Scope: source and test audit only. No ProjectBEA production code was copied, and no Lilavel production code was changed.

## Executive conclusion

ProjectBEA demonstrates several useful presence-oriented design patterns: a
single perception ingress, a true-idle opportunity, bounded-in-time event
batching, scoped routing, terminal speech/silence actions, and a separate
rhythm for less frequent spontaneous initiative. Its implementation is not a
safe literal fit for Lilavel.

The audit recommendation is:

- TAKE DIRECTLY: nothing from ProjectBEA production code.
- SELECTIVELY PORT: no production source in P5-B0. Port only selected
  behavioral test ideas and design constraints later, with provenance recorded
  if source or derived tests are actually copied.
- ADAPT DESIGN: bounded idle opportunity, deterministic wake policy,
  coalescing/steering concepts, explicit terminal presence actions, prompt
  contribution, structured lifecycle, and a hybrid registration unit.
- REFERENCE ONLY: ProjectBEA attention policy, activity feed, scoped routing
  examples, platform identity ideas, agenda/rhythm policy, and memory-labeling
  ideas.
- REJECT: the literal PerceptionBus, the Consciousness/model loop, the BEA
  transcript and memory ownership, direct tool execution, and the BEA
  platform/skill ownership boundary.

The smallest useful P5-B1 is therefore a narrow presence seam around the
existing Lilavel runtime: one persistent CLI environment, one bounded
runtime-owned idle opportunity, deterministic wake admission, one transient
autonomous cognition lane, and only application-authorized
presence.say(text) and presence.stay_silent() actions. Autonomous output
remains noncanonical. Advanced attention, durable memory, and additional
environments remain out of scope.

## 1. Repository identity and pre-flight

### LilavelRuntime

Repository: /home/sheethappiness/Documents/GitHub/LilavelRuntime

| Item | Evidence |
|---|---|
| Branch | main |
| Exact HEAD before this audit | a1f29cf3746a24b22632d135d5f045be9c1abafc |
| Worktree | clean; branch is ahead of origin/main by one commit |
| P5-A lineage | a1f29cf3746a24b22632d135d5f045be9c1abafc resolves as a commit and is an ancestor of HEAD; it is also the exact current HEAD |
| P5-A parent | 0282031789e253e628ecdb621562a4d014c59ec0 |
| P5-A record | work/PHASE-5A-presence-runtime-design.md |
| P5-A accepted boundary | docs/decisions/ADR-009-presence-runtime-and-omp-boundary.md |
| Current application packages | apps/contracts, apps/runtime, apps/core, apps/model-sidecar, apps/discord-adapter |
| Current docs inspected | README.md, docs/ARCHITECTURE.md, docs/STACK.md, docs/MIGRATION.md, docs/VALIDATION.md |
| Current runtime shape | LilavelRuntime owns persistent lifecycle, bounded ingress, environment tasks, wake/routing policy, and application-owned tool registration; Core owns canonical conversation semantics; ModelRuntime owns generation lifecycle; the model sidecar owns provider/process transport |
| Current P4/P5-A seams | ToolRegistry/ToolSession, strict tool validation and authorization, joined provider/executor settlement, stale-result fencing, explicit effect certainty, and user-work supersession |
| Root CLI status | No root package, root pyproject.toml, root uv.lock, or project.scripts entry was present in the audited HEAD; P5-B1 must add the smallest launcher as a later implementation task |

The P5-A record contains 0282031789e253e628ecdb621562a4d014c59ec0 in its
embedded baseline/implementation fields because that was the implementation
commit before the phase-closing documentation commit. The repository
authority for this audit is the actual verified HEAD a1f29cf3746a24b22632d135d5f045be9c1abafc.
No lineage is inferred from the record alone.

### Lilavel ownership paths inspected

| Boundary | Implementation evidence |
|---|---|
| Runtime host and ingress | apps/runtime/src/lilavel_runtime/kernel.py and contracts.py; bounded queue, lifecycle state, EnvironmentAdapter, WorldEvent, WakePolicy, and shutdown supervision |
| Runtime tool seam | apps/runtime/src/lilavel_runtime/tool_registry.py and tool_session.py; application-owned registration, authorization, validation, execution, and effect certainty |
| Runtime routing | apps/runtime/src/lilavel_runtime/conversation_router.py; direct-message routing remains distinct from canonical Core history |
| Canonical conversation | apps/core/src/lilavel_core/conversation.py; user admission, canonical messages, one logical run, assistant commit, supersession, and stale/cancelled output fencing |
| Model lifecycle | apps/core/src/lilavel_core/runtime.py and runtime_v3.py; generation IDs/epochs, cancellation, stale fencing, opt-in tool loop, and joined provider/executor settlement |
| Core contracts and persistence | apps/contracts/src/lilavel_contracts/tools.py; apps/core/src/lilavel_core/persistence.py and production_cognition.py |
| Model-sidecar ownership | apps/model-sidecar/src/pi-tool-provider.ts, tool-transport.ts, tool-state.ts, protocol-v3.ts, and server.ts; provider/process transport and streaming only, not canonical history or external authorization |
| Existing validation | docs/VALIDATION.md plus scripts/check_docs.py and scripts/check_architecture.py |

### ProjectBEA

Repository: /home/sheethappiness/Documents/GitHub/projectBEA

| Item | Evidence |
|---|---|
| Git checkout | real work tree; .git exists and git rev-parse --is-inside-work-tree returned true |
| Remote | origin https://github.com/emqnuele/projectBEA.git |
| Branch | main |
| Exact audited HEAD | 5b61957d6f94df190e813c720f638000d6a0a37c |
| Parent | 7b1bb8bc7463ef23ab3136feb2f53224186ee13e |
| Worktree | clean; main is aligned with its configured origin tracking state |
| License | MIT License; copyright Emanuele Faraci 2026; verified from LICENSE |
| Runtime declaration | Python >=3.10,<3.13 in pyproject.toml |
| Dependency declaration | pyproject.toml and uv.lock; runtime includes OpenAI/Groq, audio, OBS, FastAPI/Uvicorn, websockets, Telegram, embedding/RAG, SQLite-vector, and other integrations |
| Test declaration | pytest >=8, pytest-asyncio >=0.24, ruff >=0.6; pytest discovers tests and uses asyncio_mode=auto |
| Entrypoint | project script bea = src.cli:run |
| Top-level areas | src/core, src/interfaces, src/modules, src/setup, src/utils, src/web, tests, docs, tools, main.py |

ProjectBEA was not silently treated as an archive. Its exact Git HEAD is
recorded above and all source claims below are relative to that SHA.

### Evidence posture

The ProjectBEA README claim of “1302 tests” is repository prose, not
executable evidence for this audit. The standard offline test environment was
not available on this host: Python is 3.14.7, uv is not installed, no
ProjectBEA .venv pytest executable exists, and python -m pytest --collect-only
-q returned “No module named pytest”. No arbitrary optional integration was
installed. ProjectBEA test execution is therefore BLOCKED, not PASS or FAIL.

## 2. Relevant source and test map

The following map was read from the audited ProjectBEA checkout:

| Area | Source | Tests |
|---|---|---|
| Perception | src/core/perception/bus.py, src/core/perception/types.py | No dedicated direct bus test found; adjacent coverage in tests/test_consciousness_attention.py, tests/test_routing.py, tests/test_floor.py |
| Consciousness | src/core/consciousness.py | tests/test_consciousness_speech.py, tests/test_consciousness_attention.py, tests/test_barge_in.py, tests/test_concurrency_e2e.py |
| Idle/monologue | src/core/skills/idle.py, data/prompts/monologue.md, docs/skills/monologue.md | tests/test_spontaneous.py, tests/test_initiative.py, tests/test_consciousness_speech.py; no dedicated direct wait_or_idle timeout test found |
| Spontaneous initiative | src/core/mind/spontaneous.py, src/core/social/rhythm.py, src/core/brain.py | tests/test_spontaneous.py, tests/test_initiative.py |
| Attention | src/core/attention/types.py, rules.py, gate.py | tests/test_attention_gate.py, tests/test_attention_rules.py, tests/test_consciousness_attention.py |
| Voice floor | src/core/floor/controller.py, rules.py | tests/test_floor.py |
| Routing and scheduling | src/core/mind/routing.py, scheduler.py, conversation.py | tests/test_routing.py, tests/test_scheduler.py, tests/test_conversation.py |
| Skills | src/core/skills/base.py, platform.py, idle.py, presence/surface.py | tests/test_brain_wiring.py, tests/test_presence_skill.py, tests/test_send_limits.py |
| Capability/tool registry | src/core/mind/tools.py, src/core/agent/tools.py, src/core/agent/registry.py | tests/test_registry.py, tests/test_prompts.py, tests/test_presence_skill.py |
| Activity feed | src/core/events.py, src/web/app.py | tests/test_events_stream.py |
| Memory and transcript | src/core/memory/schema.sql, src/core/memory/store.py, src/core/skills/memory/memory.py, src/utils/history_manager.py, src/core/mind/recap.py | tests/test_memory_store.py, tests/test_not_losing_the_thread.py, tests/test_conversation.py |
| Lifecycle | src/core/brain.py, src/core/consciousness.py, src/core/skills/base.py | tests/test_brain_wiring.py, tests/test_concurrency_e2e.py, tests/test_barge_in.py |

## 3. Verdict meanings

The verdicts are ownership decisions, not code-size rankings:

- TAKE DIRECTLY means the implementation can cross the boundary without
  changing its authority or invariants. No ProjectBEA production subsystem
  meets this bar.
- SELECTIVELY PORT means a bounded implementation fragment could be copied
  after isolating dependencies and preserving license/provenance. No such
  production fragment is approved in P5-B0; only behavioral patterns are
  recommended for later re-expression in Lilavel.
- ADAPT DESIGN means the behavior or contract is useful, but the Lilavel
  owner, lifecycle, state, or effect boundary must remain authoritative.
- REFERENCE ONLY means the source is useful for policy examples or test
  scenarios, but should not become a Lilavel implementation dependency.
- REJECT means literal adoption would violate current Lilavel ownership or
  safety invariants.

## 4. Candidate subsystem audit ledger

Each entry records the source symbols, test evidence, responsibility and
ownership, concurrency and lifecycle behavior, effects and persistence,
extraction cost, generic-versus-policy classification, conflict, and verdict.

### 4.1 PerceptionBus

- Source: BEA/src/core/perception/bus.py, class PerceptionBus; BEA/src/core/perception/types.py, PerceptionKind and mutable Perception.
- Tests: no dedicated direct test of PerceptionBus.put, drain, drain_nowait, or wait_or_idle was found; related behavior is exercised indirectly by BEA/tests/test_consciousness_attention.py, BEA/tests/test_routing.py, and BEA/tests/test_floor.py.
- Responsibility and state owner: accepts all perception producers and owns a single in-memory queue. Perception objects own UUID identity, wall-clock timestamp, metadata, author, and content.
- Lifecycle and concurrency: producers call put_nowait; one Consciousness loop is the assumed consumer. There is no explicit close state, no consumer registration, and no fairness contract among producers.
- Dependencies and effects: asyncio.Queue, time.time, and Perception dataclass; no external effect and no durable persistence.
- Cancellation and extraction: waiting consumer cancellation follows asyncio cancellation, but the queue has no shutdown wakeup or drain-on-close behavior. Extraction is difficult because the unbounded queue and mutable event type must be replaced to satisfy Lilavel’s bounded WorldEvent and lifecycle contracts.
- Generic infrastructure or BEA policy: batching ingress is generic; mutable Perception shape, wall-clock ordering, and the one-mind consumer assumption are BEA policy/infrastructure coupling.
- Conflict and verdict: unbounded ingress, no explicit backpressure, no close, and no stale/admission fencing conflict with LilavelRuntime. Verdict: REJECT literal implementation; retain only the single ingress/consumer and bounded batching design as ADAPT DESIGN.
- Lilavel target: existing LilavelRuntime bounded event queue and immutable WorldEvent/EnvironmentAdapter path.

### 4.2 wait_or_idle and the idle event

- Source: BEA/src/core/perception/bus.py, PerceptionBus.wait_or_idle; BEA/src/core/skills/idle.py, IdleSurface; BEA/src/core/perception/types.py, PerceptionKind.IDLE.
- Tests: no direct wait_or_idle timeout test found; adjacent tests are BEA/tests/test_consciousness_speech.py, BEA/tests/test_attention_gate.py for IDLE attention, and BEA/tests/test_floor.py for a separate true-silence reflex.
- Responsibility and state owner: the bus waits for one event or synthesizes a mutable IDLE Perception with surface idle, low salience, and fixed content. The caller controls idle_after.
- Lifecycle and concurrency: one consumer awaits asyncio.wait_for(queue.get(), timeout=idle_after); on success it drains currently available items and sorts by timestamp. The timeout is not extended by later arrivals after the first event, and the method has no stop token.
- Dependencies and effects: asyncio queue and wall-clock timeout; synthetic event only, no persistence or external effect.
- Cancellation and extraction: task cancellation is possible while waiting, but shutdown needs a separate cancellation path. Literal extraction would import the queue’s unbounded semantics.
- Generic infrastructure or BEA policy: “event or bounded idle opportunity” is generic; the exact Perception shape and the absence of a lifecycle owner are BEA-specific.
- Conflict and verdict: Lilavel needs a bounded, runtime-owned IdleOpportunity with explicit admission, reset, and shutdown semantics. Verdict: ADAPT DESIGN, not source port.
- Lilavel target: a runtime-produced WorldEvent/IdleOpportunity routed through the existing Core admission boundary; never make a timeout itself a canonical conversation event.

### 4.3 Monologue and other spontaneous initiative

- Source: BEA/src/core/skills/idle.py, IdleSurface; BEA/src/core/mind/spontaneous.py, SpontaneousPresence.run_once and is_eligible; BEA/src/core/social/rhythm.py, RhythmTick; BEA/src/core/brain.py, rhythm task; data/prompts/monologue.md.
- Tests: BEA/tests/test_spontaneous.py covers activity, quiet hours, recent speech, disabled state, random gate, stage exclusion, and multiple live conversations; BEA/tests/test_initiative.py covers initiative conversation behavior; BEA/tests/test_consciousness_speech.py covers terminal speech/silence behavior.
- Responsibility and state owner: IdleSurface contributes the monologue prompt. Consciousness owns each immediate idle reasoning burst. SpontaneousPresence owns a separate policy over persistent SQLite conversation candidates and can open initiative turns. RhythmTick owns the 900-second task cadence.
- Lifecycle and concurrency: the idle path repeats whenever Consciousness re-enters wait_or_idle; the rhythm path runs independently and may initiate several scoped conversations in one pass. Random probability is used only by SpontaneousPresence, not by the immediate idle timeout.
- Dependencies and effects: model calls, conversation scheduler, SQLite messages, config, quiet-hours clock, and platform conversation state. The rhythm path can create durable conversation rows and platform-facing initiative.
- Cancellation and extraction: rhythm task cancellation is attempted by Brain.stop_skills; initiative turns are scheduler tasks. Immediate idle reasoning has no dedicated cooldown or one-shot latch. Extraction requires separating policy from the runtime clock and adding user-preemption semantics.
- Generic infrastructure or BEA policy: the idea of a transient initiative run is generic; 900-second rhythm, 6-hour/30-minute activity windows, probability .15, minimum activity three, and quiet hours are BEA policy.
- Conflict and verdict: repeated idle opportunities can become an unconditional model heartbeat and the rhythm path crosses BEA conversation/memory ownership. Verdict: ADAPT DESIGN for one bounded deterministic idle opportunity; REFERENCE ONLY for rhythm/spontaneous policy in P5-B0.
- Lilavel target: one runtime-owned idle gate with cooldown/latch, no random wake in V0, one active autonomous run, no 900-second scanner.

### 4.4 Attention

- Source: BEA/src/core/attention/gate.py, Attention.judge, mark_spoke, digest, remember; BEA/src/core/attention/rules.py; BEA/src/core/attention/types.py.
- Tests: BEA/tests/test_attention_gate.py, BEA/tests/test_attention_rules.py, and BEA/tests/test_consciousness_attention.py.
- Responsibility and state owner: Attention owns per-surface activity deques, last-spoke cooldown, transient NOTE entries, threshold settings, quiet hours, and the REACT/NOTE/DROP decision.
- Lifecycle and concurrency: synchronous deterministic state updates around each batch; score includes random uniform jitter of plus or minus .1. NOTE state is held in memory until digest is requested; no event identity dedupe is present.
- Dependencies and effects: clock, config, salience/author metadata, hot names, and optional conversation facts; it emits EventManager diagnostics and controls whether Consciousness receives a cognition batch.
- Persistence and cancellation: no durable attention persistence; no background task; cancellation is not applicable to the pure gate.
- Extraction and footprint: medium extraction cost because rules reference BEA author metadata, attention state, conversations, and event manager. The pure score functions are small; the policy is not generic.
- Generic infrastructure or BEA policy: gating and bounded digest are generic; names, donations, known-person lookups, quiet hours, threshold .45, and 20-second cooldown are BEA policy.
- Conflict and verdict: useful future policy but beyond P5-B1 and not aligned with Lilavel’s first deterministic wake contract. Verdict: REFERENCE ONLY now; ADAPT DESIGN later.
- Lilavel target: defer advanced attention; keep the option of an application-owned deterministic admission policy after the idle slice.

### 4.5 REACT/NOTE/DROP

- Source: BEA/src/core/attention/types.py, Reaction and Verdict; BEA/src/core/attention/gate.py, Attention.judge; BEA/src/core/attention/rules.py.
- Tests: BEA/tests/test_attention_gate.py proves addressed REACT, peripheral NOTE, noise DROP, disabled behavior, IDLE behavior, cooldown, expiry, and ordering; BEA/tests/test_consciousness_attention.py proves selected high-volume outcomes.
- Responsibility and state owner: Attention owns the reaction decision; Consciousness consumes only REACT items while NOTE entries become a digest and DROP entries disappear.
- Lifecycle and concurrency: one batch is judged synchronously. If any item reacts, notes in the same batch are promoted to the reacted batch. Different scoped conversations can later run concurrently through ConversationScheduler.
- Dependencies and effects: reaction rules depend on metadata and time; REACT can admit a model call, NOTE changes future prompt context, DROP has no effect. No durable state.
- Cancellation and extraction: no cancellation inside the gate; a model call admitted by REACT is controlled by Consciousness and its scheduler. Extraction requires a new Lilavel event disposition type rather than importing Reaction.
- Generic infrastructure or BEA policy: three-way disposition is reusable; its scores and social heuristics are BEA policy.
- Conflict and verdict: direct adoption would conflate Lilavel WakeDecision with BEA’s social attention semantics. Verdict: ADAPT DESIGN later; DEFER implementation in P5-B1.
- Lilavel target: initial V0 has only deterministic wake/no-wake for a true idle opportunity; no REACT/NOTE/DROP surface policy.

### 4.6 Event coalescing and scheduler

- Source: BEA/src/core/perception/bus.py, drain and drain_nowait; BEA/src/core/mind/scheduler.py, ConversationScheduler.submit and drain; BEA/src/core/mind/routing.py.
- Tests: BEA/tests/test_scheduler.py proves coalescing, maximum pending reruns, parallel different channels, failure release, and drain; BEA/tests/test_routing.py proves mixed-batch grouping and one-event-one-place.
- Responsibility and state owner: the bus batches by a fixed time window; routing assigns each perception to one key; ConversationScheduler owns per-key running/pending state and folds bursts into at most max_coalesced reruns.
- Lifecycle and concurrency: one task per conversation key; same-key work serializes, different keys run in parallel. The bus itself is not bounded and has no stop lifecycle.
- Dependencies and effects: asyncio tasks, time, routing keys, ConversationMind dispatch; model calls happen downstream.
- Persistence and cancellation: scheduler state is in memory; drain waits for tasks with a timeout; cancellation/failure releases the key, but the bus does not provide a global shutdown contract.
- Extraction and footprint: medium; the per-key state machine is generic, but BEA ConversationMind and event types are coupled. Dependency footprint is small if rewritten around Lilavel IDs and Core admission.
- Generic infrastructure or BEA policy: fixed-window batch and one-active-per-key are generic; max three reruns and platform key rules are policy.
- Conflict and verdict: coalescing is compatible with Lilavel only after runtime backpressure and Core run ownership remain authoritative. Verdict: ADAPT DESIGN; no source copy.
- Lilavel target: one-event/one-cognition admission and bounded pending work behind existing ConversationCore/ModelRuntime supersession.

### 4.7 speak

- Source: BEA/src/core/consciousness.py, Consciousness._speak; BEA/src/core/mind/tools.py, MindTools.registry.
- Tests: BEA/tests/test_consciousness_speech.py covers clean speech, token sanitization, scaffolding suppression, history inclusion, and missing mood normalization; BEA/tests/test_prompts.py covers terminal tool exposure.
- Responsibility and state owner: Consciousness owns the speech tool handler. It sanitizes model text, updates BEA JSON history, publishes an output event, invokes expression output, delivers to a scoped conversation, resolves correlation, and marks attention as spoken.
- Lifecycle and concurrency: invoked inside the model tool loop; expression output is launched as a task while conversation delivery is awaited. Terminal tool semantics stop the current reasoning burst.
- Dependencies and effects: HistoryManager, EventManager, expression, routing/correlation, attention, platform delivery, and model tool schema. It has many external effects.
- Persistence and cancellation: writes durable JSON session history synchronously through HistoryManager; cancellation can interrupt surrounding Consciousness work but no Lilavel-style joined effect settlement is present.
- Extraction and footprint: high if copied; the behavior can be re-expressed through Lilavel ToolSpec/ToolSession with a CLI-only output sink.
- Generic infrastructure or BEA policy: terminal output action is generic; mood, expression, correlation, and direct history/platform writes are BEA-specific.
- Conflict and verdict: direct adoption violates canonical-history, authorization, effect-certainty, and noncanonical autonomous-output boundaries. Verdict: ADAPT DESIGN only as a Lilavel application tool named presence.say.
- Lilavel target: ToolSpec plus strict validation, application authorization, executor, ToolResult, and joined settlement; V0 renders only to the CLI and does not commit autonomous output to canonical conversation history.

### 4.8 stay_silent

- Source: BEA/src/core/consciousness.py, Consciousness._stay_silent; BEA/src/core/mind/tools.py, MindTools.registry; BEA/docs/skills/monologue.md.
- Tests: BEA/tests/test_consciousness_speech.py proves scaffolding and empty content stay silent; BEA/tests/test_consciousness_attention.py covers ignored/no-output attention paths.
- Responsibility and state owner: Consciousness resolves the current correlations and abandons voice latency; it emits no user-visible text and returns a terminal tool result.
- Lifecycle and concurrency: terminal action exits the current burst; there is no durable action record establishing a no-op beyond the returned string.
- Dependencies and effects: correlation registry and optional expression/voice state; no durable persistence.
- Cancellation and extraction: cancellation is inherited from Consciousness; no separate effect settlement.
- Extraction and footprint: low for the semantic action, high if the surrounding BEA handler is copied.
- Generic infrastructure or BEA policy: deliberate silence is generic; correlation cleanup is BEA-specific.
- Conflict and verdict: safe concept, unsafe literal owner. Verdict: ADAPT DESIGN as presence.stay_silent through Lilavel’s native tool path.
- Lilavel target: explicit successful no-output ToolResult, no canonical history write, no immediate idle requeue.

### 4.9 Skill

- Source: BEA/src/core/skills/base.py, Skill and SkillRegistry.
- Tests: BEA/tests/test_brain_wiring.py covers registry construction, lifecycle and settings toggles; BEA/tests/test_presence_skill.py covers tool and prompt wiring.
- Responsibility and state owner: a Skill can push perceptions, expose tools, contribute static and dynamic prompt context, expose live state, and start/stop its own infrastructure. SkillRegistry owns mutable registration, active state, and aggregation.
- Lifecycle and concurrency: start and stop are called by Brain; each skill may create background tasks. Registry toggles mutate shared state and rebuild tool/prompt views.
- Dependencies and effects: Skill receives a Brain-shaped Context containing bus, expression, config, memory, attention, conversations, and event manager. This gives direct access to many effects.
- Persistence and cancellation: individual skills decide; base class does not provide task groups or joined shutdown. Configuration and skill activity are mutable.
- Extraction and footprint: high literal extraction cost because the API is coupled to Brain and BEA services; a registration descriptor can be rewritten cheaply.
- Generic infrastructure or BEA policy: contribution hooks are generic; Brain-shaped context, toggleable skill names, and direct infrastructure ownership are BEA policy.
- Conflict and verdict: literal adoption risks making a Skill an identity, memory, authorization, and lifecycle owner. Verdict: ADAPT DESIGN through a narrow composition/registration unit.
- Lilavel target: a non-authoritative grouping of EnvironmentAdapter, ToolSpec, PromptContribution, configuration metadata, and lifecycle handles; runtime/Core remain owners.

### 4.10 PlatformSkill

- Source: BEA/src/core/skills/platform.py, PlatformSkill.perceive_text, conversation_key, send_text, deliver, conversation_tools.
- Tests: BEA/tests/test_routing.py, BEA/tests/test_conversation.py, BEA/tests/test_send_limits.py, BEA/tests/test_presence_skill.py.
- Responsibility and state owner: PlatformSkill owns a platform name, scoped channel identity, author construction, input conversion, text delivery, typing, direct-message support, and platform-specific tools.
- Lifecycle and concurrency: it emits directly to PerceptionBus and calls external handlers; handlers are closed over in tool functions. There is no separate Lilavel authorization session.
- Dependencies and effects: platform client/handlers, Humanizer, config, bus, conversation state, and direct sends/reactions. Effects are immediate and platform-specific.
- Persistence and cancellation: conversation history and messages are stored by BEA memory services; direct sends have no joined effect certainty or durable outbox. Cancellation depends on handlers.
- Extraction and footprint: high literal extraction cost; stable identity/routing concepts are portable, direct handlers are not.
- Generic infrastructure or BEA policy: stable platform/native/channel key is generic; platform names, limits, Humanizer, and send/reply/react tools are policy.
- Conflict and verdict: environments must not own Lilavel identity, canonical memory, or trusted authorization. Verdict: REFERENCE ONLY for identity/routing ideas; ADAPT DESIGN in future adapters, not a source port.
- Lilavel target: EnvironmentAdapter observes and acts; Runtime/Core select destination and ToolSession authorizes effects.

### 4.11 Capability registration

- Source: BEA/src/core/agent/tools.py, ToolRegistry.register/get/dispatch; BEA/src/core/mind/tools.py, MindTools.invalidate, registry, and schemas; BEA/src/core/skills/base.py, SkillRegistry.tools.
- Tests: BEA/tests/test_registry.py, BEA/tests/test_brain_wiring.py, BEA/tests/test_prompts.py, BEA/tests/test_presence_skill.py.
- Responsibility and state owner: mutable registries aggregate tools from core and active skills; duplicate names are overwritten; dispatch calls handlers and turns exceptions into strings.
- Lifecycle and concurrency: registries are rebuilt when active skills change; handlers may be synchronous, async, or long-running; no immutable per-generation snapshot or joined settlement.
- Dependencies and effects: handler closures can reach platform clients, memory, agenda, OBS, voice, and arbitrary Brain context. Dispatch is the authorization boundary in practice, but does not implement Lilavel authorization.
- Persistence and cancellation: registry state is in memory; handler side effects decide persistence; long-running tasks are tracked ad hoc.
- Extraction and footprint: medium for the aggregation shape, high for direct dispatch. Lilavel already has ToolRegistry/ToolSession and a stricter contract.
- Generic infrastructure or BEA policy: registration is generic; dynamic skill exposure and direct handler dispatch are BEA policy.
- Conflict and verdict: direct adoption breaks strict validation, application authorization, effect certainty, stale fencing, and provider/executor joined settlement. Verdict: REJECT literal; use existing Lilavel capability/tool contracts.
- Lilavel target: immutable per-run allowed ToolSpec set, ToolSession authorization, executor ownership, and ToolResult settlement.

### 4.12 Prompt contribution

- Source: BEA/src/core/skills/base.py, Skill.context_section, context_for, live_state; BEA/src/core/consciousness.py, _system_message; BEA/src/core/skills/idle.py and data/prompts/monologue.md.
- Tests: BEA/tests/test_prompts.py, BEA/tests/test_consciousness_attention.py, BEA/tests/test_presence_skill.py.
- Responsibility and state owner: active skills contribute static sections, batch-sensitive dynamic context, and live state; Consciousness assembles them into the model system message. Idle prompt rules are included only for pure idle batches.
- Lifecycle and concurrency: prompt contributions are read during each cognition run; dynamic providers can observe mutable Brain state. No immutable prompt snapshot contract is explicit.
- Dependencies and effects: config, memory, attention, skills, active conversations, and current batch; generally no external effects.
- Persistence and cancellation: prompt text is transient; dynamic provider cancellation follows the model call.
- Extraction and footprint: low-to-medium if converted to immutable contribution objects; current implementation is coupled to Brain and skill activity.
- Generic infrastructure or BEA policy: contribution slots are generic; monologue wording, soul/operating files, roster and memory labels are policy.
- Conflict and verdict: useful only if Core controls canonical context and per-run snapshotting. Verdict: ADAPT DESIGN.
- Lilavel target: explicit PromptContribution owned by the runtime/Core composition boundary; environment may contribute bounded rules but not canonical history or hidden authority.

### 4.13 Activity feed

- Source: BEA/src/core/events.py, EventManager, BrainEvent, EventCategory; BEA/src/web/app.py, events and SSE endpoints.
- Tests: BEA/tests/test_events_stream.py covers ring history ceiling, subscriber backlog, fanout, unsubscribe, stalled subscriber removal, and metadata.
- Responsibility and state owner: EventManager is an in-memory diagnostic feed with a max_history ring and bounded subscriber queues. Web clients consume it through SSE.
- Lifecycle and concurrency: publish appends and fanouts; a full subscriber queue causes that subscriber to be dropped. It has explicit unsubscribe, but no durable event store.
- Dependencies and effects: asyncio queues and web/SSE layer; output is diagnostic/UI external effect, not model execution.
- Persistence and cancellation: no persistence; subscriber cancellation/unsubscribe is supported, and stalled subscribers are dropped.
- Extraction and footprint: low for a diagnostic feed; no need to import BEA source. Dependency footprint is web-specific if SSE is included.
- Generic infrastructure or BEA policy: bounded diagnostic feed is generic; categories and dashboard endpoints are BEA policy.
- Conflict and verdict: must not be treated as canonical history, memory, or proof of an external effect. Verdict: REFERENCE ONLY now; ADAPT DESIGN if Lilavel later exposes an evidence feed.
- Lilavel target: a separately named bounded diagnostic/evidence stream with explicit event identity and effect certainty.

### 4.14 Scoped conversation and platform routing

- Source: BEA/src/core/mind/routing.py, route and conversation_key; BEA/src/core/mind/conversation.py, ConversationMind; BEA/src/core/skills/platform.py.
- Tests: BEA/tests/test_routing.py proves stage/scoped grouping, explicit keys, channel separation, correlation routing, mixed batches, and one-event-one-place; BEA/tests/test_conversation.py proves scoped history and parallel channels.
- Responsibility and state owner: routing maps each perception to exactly one stage or platform/channel key; ConversationMind owns a separate context/history and turns per key.
- Lifecycle and concurrency: ConversationScheduler serializes each key and runs different keys in parallel; routing is deterministic for a batch.
- Dependencies and effects: platform metadata, conversation store, scheduler, MemoryStore, direct platform sends, and model calls.
- Persistence and cancellation: per-key histories persist in SQLite; scheduler drain/cancel is best effort; direct effects are not Lilavel-settled.
- Extraction and footprint: medium for the one-key-per-event property; high for ConversationMind because it owns transcript, memory, tools, and effects.
- Generic infrastructure or BEA policy: exact-one-route and stable scope key are generic; stage semantics and platform conversation ownership are BEA policy.
- Conflict and verdict: Lilavel already has CoreConversationRouter and runtime routing; importing ConversationMind would create a second canonical history owner. Verdict: ADAPT DESIGN for the routing invariant; REFERENCE ONLY for the BEA conversation implementation.
- Lilavel target: retain Lilavel conversation identity and canonical history; use environment metadata only as routing input.

### 4.15 Consciousness

- Source: BEA/src/core/consciousness.py, Consciousness.__init__, start, stop, run, _system_message, _dispatch, _speak, _stay_silent; BEA/src/core/brain.py composition.
- Tests: BEA/tests/test_consciousness_speech.py, BEA/tests/test_consciousness_attention.py, BEA/tests/test_barge_in.py, BEA/tests/test_concurrency_e2e.py.
- Responsibility and state owner: the class owns the always-on mind, context transcript, prompt composition, model calls, burst loop, tool loop, expression output, correlation registry, attention calls, scoped delivery, idle behavior, and task state.
- Lifecycle and concurrency: start launches the main loop; loop batches bus input and can launch body/recap tasks; stop cancels and awaits the loop and stops surfaces, but the source does not show a complete join for every detached body, recap, or warmup task.
- Dependencies and effects: it directly depends on llm clients, bus, skills, history, attention, conversations, affect, expression, EventManager, and tool registry; it can write history and send platform output.
- Persistence and cancellation: canonical-ish BEA history is written by _speak and ConversationMind; cancellation catches CancelledError in the loop but does not implement Lilavel provider/executor joined settlement or stale fencing.
- Extraction and footprint: very high; it is the composition root for almost all BEA semantics.
- Generic infrastructure or BEA policy: a central cognition coordinator is generic; this implementation’s authority bundle is BEA-specific and conflicts with Lilavel.
- Conflict and verdict: it would replace Core, ModelRuntime, ToolSession, canonical conversation, and runtime lifecycle. Verdict: REJECT wholesale adoption.
- Lilavel target: retain LilavelRuntime, ConversationCore, ModelRuntime, sidecar, and application tool boundaries; add only a narrow autonomous admission seam.

### 4.16 BEA model loop

- Source: BEA/src/core/consciousness.py, run and the configured bounded burst; BEA/src/core/agent/runner.py, AgentRunner.run; BEA/src/core/agent/registry.py.
- Tests: BEA/tests/test_consciousness_attention.py, BEA/tests/test_barge_in.py, BEA/tests/test_concurrency_e2e.py, and BEA/tests/test_registry.py.
- Responsibility and state owner: model loop owns message context, repeated completions, steering drains, tool calls, terminal detection, and error retry behavior. AgentRunner is a generic think-act-observe loop used by subagents.
- Lifecycle and concurrency: main loop is long-lived; tool/body tasks and recap tasks are separate; steering is checked between model calls, not as a guaranteed physical cancellation of the provider call.
- Dependencies and effects: direct OpenAI/Groq/model clients, mutable message arrays, ToolRegistry, platform/memory handlers, and EventManager.
- Persistence and cancellation: context is in memory and then written through BEA history/conversation services; cancellation is cooperative and lacks joined effect certainty.
- Extraction and footprint: very high; direct provider and handler dependencies plus semantic transcript ownership.
- Generic infrastructure or BEA policy: bounded reasoning steps and steering checkpoints are generic; direct model/tool/history ownership is BEA-specific.
- Conflict and verdict: conflicts with Lilavel generation IDs/epochs, sidecar transport boundary, stale fencing, strict tool authorization, and joined settlement. Verdict: REJECT.
- Lilavel target: ModelRuntime and sidecar remain the only model-generation path; autonomous cognition uses the existing run machinery.

### 4.17 BEA transcript and context

- Source: BEA/src/core/consciousness.py, mutable context list and _trim/_schedule_recap; BEA/src/utils/history_manager.py, HistoryManager; BEA/src/core/mind/conversation.py; BEA/src/core/mind/recap.py.
- Tests: BEA/tests/test_consciousness_speech.py, BEA/tests/test_conversation.py, BEA/tests/test_not_losing_the_thread.py, BEA/tests/test_memory_store.py, BEA/tests/test_prompts.py.
- Responsibility and state owner: BEA splits transcript ownership across Consciousness.context, JSON session history, scoped SQLite messages, ConversationMind history, and recap summaries. Context assembly also reads memory, roster, agenda, soul, operating rules, and live state.
- Lifecycle and concurrency: context is mutated in the loop; recap is generated in a background task after trimming; ConversationMind has per-key histories and scheduler tasks.
- Dependencies and effects: JSON files, SQLite, optional RAG embeddings, background LLM, prompts, and platform identity.
- Persistence and cancellation: persistence is direct and multi-store; recap and background work are not a single joined settlement. There is no one canonical “assistant commit” equivalent.
- Extraction and footprint: very high and semantically unsafe to merge.
- Generic infrastructure or BEA policy: bounded context/recap is generic; the split stores and prompt/person/memory semantics are BEA-specific.
- Conflict and verdict: Lilavel Core owns canonical conversation state and ModelRuntime owns generation lifecycle; importing a second transcript is prohibited. Verdict: REJECT.
- Lilavel target: autonomous output remains transient/noncanonical in P5-B1 unless a future accepted decision changes that.

### 4.18 BEA tool execution

- Source: BEA/src/core/agent/tools.py, Tool, ToolRegistry.dispatch; BEA/src/core/consciousness.py, _dispatch; BEA/src/core/mind/conversation.py, _reason; BEA/src/core/skills/platform.py and presence/surface.py tool closures.
- Tests: BEA/tests/test_registry.py, BEA/tests/test_consciousness_speech.py, BEA/tests/test_presence_skill.py, BEA/tests/test_conversation.py, BEA/tests/test_send_limits.py.
- Responsibility and state owner: model-facing tool registry owns name/schema/handler and directly calls handlers; exceptions are converted to strings. Platform and presence tools can send, react, remember, schedule, or access services.
- Lifecycle and concurrency: long-running tools are tracked in a single body task and can preempt the previous body; other tools run in the model loop. Tool schemas are dynamically rebuilt.
- Dependencies and effects: direct platform clients, memory, agenda, OBS, expression, voice, and network integrations.
- Persistence and cancellation: effects decide their own persistence; no application authorization object, no provider/executor joined settlement, and no explicit effect certainty.
- Extraction and footprint: high; only the shape of a model tool call is portable.
- Generic infrastructure or BEA policy: schema/handler registration is generic; direct dispatch and external capabilities are BEA-specific.
- Conflict and verdict: direct adoption violates the P4 native tool path and external-effect ownership. Verdict: REJECT.
- Lilavel target: existing ToolSpec → validated ToolCall → application authorization → executor → ToolResult → joined settlement.

### 4.19 BEA memory

- Source: BEA/src/core/memory/schema.sql; BEA/src/core/memory/store.py, MemoryStore; BEA/src/core/skills/memory/memory.py, MemorySkill; BEA/src/utils/history_manager.py; BEA/src/core/mind/recap.py.
- Tests: BEA/tests/test_memory_store.py, BEA/tests/test_not_losing_the_thread.py, BEA/tests/test_conversation.py, BEA/tests/test_prompts.py.
- Responsibility and state owner: SQLite stores people/identities, scoped messages, summaries, long-term memories, facts, self facts, sessions, and agenda; JSON stores sessions; MemorySkill performs RAG and diary processing; recap summarizes dropped context.
- Lifecycle and concurrency: synchronous writes plus background RAG/diary/recap model work; MemoryStore closes one SQLite connection, while individual callers own timing and retries.
- Dependencies and effects: SQLite, sqlite-vec, fastembed/RAG, background LLM, filesystem JSON, prompts, and model-generated diary text.
- Persistence and cancellation: durable state is created directly by the mind/skills; cancellation and write certainty are application-specific, not joined to Lilavel model settlement.
- Extraction and footprint: very high; includes BEA product policy and a broad dependency footprint.
- Generic infrastructure or BEA policy: separation of fact/history/agenda is a useful policy reference; schema, RAG, diary, and identity semantics are BEA-specific.
- Conflict and verdict: Lilavel P5-B1 explicitly excludes durable memory, and Core remains the canonical conversation owner. Verdict: REJECT as implementation; REFERENCE ONLY for future memory-boundary policy.
- Lilavel target: no memory implementation in P5-B1.

### 4.20 Shutdown and cancellation

- Source: BEA/src/core/brain.py, start_skills, stop_skills, shutdown; BEA/src/core/consciousness.py, start, stop, _loop; BEA/src/core/skills/base.py, start/stop; BEA/src/core/mind/scheduler.py, drain.
- Tests: BEA/tests/test_concurrency_e2e.py, BEA/tests/test_barge_in.py, BEA/tests/test_scheduler.py, BEA/tests/test_brain_wiring.py.
- Responsibility and state owner: Brain orchestrates skill/Consciousness/rhythm shutdown; Consciousness cancels its loop; scheduler drains per-key tasks; individual services close their own resources.
- Lifecycle and concurrency: there are useful explicit start/stop hooks, but detached warmup, body, and recap tasks are not all shown as joined by Brain.stop_skills. Provider cancellation and external-effect settlement are not centralized.
- Dependencies and effects: asyncio tasks, model clients, platforms, memory DB, voice/OBS, and web services.
- Persistence and cancellation: best-effort cooperative cancellation; direct effects can outlive the cognition task unless their own handlers stop them.
- Extraction and footprint: medium for the explicit lifecycle shape; high for the implementation because of service fan-out.
- Generic infrastructure or BEA policy: structured stop and task drain are generic; service ordering and detached work are BEA-specific.
- Conflict and verdict: Lilavel already has a stricter fail-closed runtime shutdown and joined settlement boundary. Verdict: ADAPT DESIGN for explicit lifecycle ownership; reject literal cancellation code.
- Lilavel target: Runtime-owned TaskGroup/supervision, Core/ModelRuntime cancellation and stale fencing, ToolSession joined settlement, and clean CLI shutdown.

### 4.21 Voice floor as an adjacent silence gate

- Source: BEA/src/core/floor/controller.py, FloorController.tick; BEA/src/core/floor/rules.py.
- Tests: BEA/tests/test_floor.py covers first-heard gating, quiet-room threshold, explicit silence, reset on heard, minimum gap, rolling max, disabled state, and jitter.
- Responsibility and state owner: FloorController owns a deterministic reflex that may create a VOICE perception after silence, with a per-minute bound and randomized threshold jitter.
- Lifecycle and concurrency: a separate periodic tick; it is not a second PerceptionBus consumer. It emits at most one event per qualifying tick.
- Dependencies and effects: call state, listeners, expression speaking state, clock, and bus; effect is a synthetic voice perception.
- Persistence and cancellation: in-memory counters only; tick cancellation is caller-owned.
- Extraction and footprint: low for the decision-table idea, but voice-specific dependencies make literal reuse unnecessary.
- Generic vs policy/conflict: bounded silence gating is generic; voice floor thresholds and jitter are BEA policy. Verdict: REFERENCE ONLY for a future silence gate; not part of P5-B1.

## 5. Mandatory deep dives

### A. Perception bus

The implementation is a small asynchronous batcher, not a complete event
runtime:

1. Queue structure: PerceptionBus creates an unbounded asyncio.Queue. put
   calls put_nowait. There is no maxsize and therefore no producer
   backpressure.
2. Ordering: drain waits for one item, uses a wall-clock deadline for the
   window, drains available items, and sorts the result by Perception.ts.
   Equal timestamps have no explicit sequence number. Event identity is a
   UUID on Perception, but identity is not used for dedupe.
3. Fairness and producers: any producer can enqueue. The design assumes one
   Consciousness consumer; it does not define fairness across producers or
   consumer ownership.
4. Cancellation and shutdown: a waiting get can be cancelled by its task.
   There is no close sentinel, admission gate, queue-drain protocol, or
   shutdown state.
5. Backpressure and burst handling: there is no backpressure. drain(window)
   captures one bounded wall-clock window after the first item; later items
   do not extend the deadline. drain_nowait captures the currently available
   burst.
6. Dedupe/coalescing: there is no event-ID dedupe and no semantic coalescing.
   Coalescing occurs only because callers consume a burst as one batch.
7. One-event/one-route: the bus does not enforce this. BEA routing.py later
   maps each event to one bucket; tests/test_routing.py proves that downstream
   invariant.
8. True idle: wait_or_idle waits for one queue item up to idle_after, then
   returns one synthetic IDLE Perception. It does not reset the timeout from
   every arrival; after a real event it returns and the caller decides when to
   wait again.

Could Lilavel selectively port this implementation with minor adaptation?
No. Replacing Queue with a bounded queue would not fix the missing close
contract, event immutability, admission/backpressure semantics, stale fencing,
and one-consumer ownership. The useful design to retain is:

- one explicit ingress owner;
- a bounded batch window after the first admitted event;
- a non-consuming immediate observation point for steering;
- a synthetic idle opportunity that is not canonical history;
- one-event/one-cognition admission enforced downstream.

The exact files/functions and test ideas worth re-expressing are
BEA/src/core/perception/bus.py: PerceptionBus.drain, drain_nowait, and
wait_or_idle; BEA/tests/test_routing.py; BEA/tests/test_scheduler.py;
BEA/tests/test_floor.py; and the high-volume cases in
BEA/tests/test_consciousness_attention.py. They should be adapted to
Lilavel WorldEvent, WakePolicy, ConversationCore, and ModelRuntime rather
than copied.

### B. Idle and monologue

BEA has two different spontaneous mechanisms:

- Immediate idle monologue: when the IdleSurface is active,
  Consciousness.run calls wait_or_idle with config consciousness.idle_after,
  whose default is 240 seconds. The timeout creates IDLE. Attention normally
  reacts to IDLE outside configured quiet hours. A model call then occurs on
  each admitted idle opportunity. The monologue prompt is contributed only
  when the batch is pure idle. The model must choose speak or stay_silent to
  terminate the burst. After the turn, the loop can wait again and eventually
  create another IDLE event. There is no separate idle cooldown or one-shot
  latch in this path.
- Rhythm initiative: SpontaneousPresence runs from a 900-second RhythmTick.
  It inspects persistent SQLite messages from the last six hours, requires
  recent activity in a thirty-minute window, suppresses recently spoken
  conversations and quiet hours, and applies random probability .15. It can
  initiate several scoped conversations in one pass. This is not equivalent
  to true-idle detection.

User input is drained as steering between model calls and can interrupt
expression output. The audited source and tests do not prove that a new input
physically cancels an in-flight provider call or joins a tool effect. That
stronger guarantee belongs to Lilavel’s existing supersession, generation
fencing, and joined settlement.

Smallest PRESENCE-V0 policy:

- one persistent host and one runtime-owned idle clock;
- real activity resets the idle clock and any one-shot wake latch;
- true silence produces at most one bounded IdleOpportunity;
- deterministic WakePolicy, with no random wake in V0;
- a cooldown/latch prevents immediate repeated cognition;
- at most one active autonomous cognition run;
- user input has priority and uses existing Core/ModelRuntime cancellation;
- no 900-second candidate scan, no durable agenda, and no unconditional LLM
  heartbeat;
- after no-wake or deliberate silence, return to idle without immediate
  requeue.

### C. Speak and silence

BEA’s terminal actions are exactly the relevant shape: MindTools exposes
core tools named speak and stay_silent, and Consciousness treats them as
terminal. speak sanitizes text and then writes history, publishes output,
drives expression, delivers to a conversation, and resolves correlations.
stay_silent emits no visible text and resolves pending correlation state.

For Lilavel, the safer design is to expose only these autonomous tools under
the existing native tool path:

- presence.say(text)
- presence.stay_silent()

The model tool selection must be constrained for the autonomous generation
to this immutable two-tool set, and application authorization must still be
required. The execution path remains:

ToolSpec → model ToolCall → strict validation → application authorization →
executor → ToolResult → provider/executor joined settlement.

This is simpler and safer than inventing a parallel SAY/NO_ACTION response
protocol because it reuses the already-proven P4 contract, makes terminal
selection explicit, keeps arbitrary capabilities out of autonomous cognition,
and preserves effect certainty. The V0 say executor writes only to the CLI
output sink; it does not append a user or assistant message to canonical
conversation history. The tool result and run metadata remain transient.

### D. Attention

BEA Attention is a stateful application policy, not a generic transport:

- deterministic checks handle addressed messages, follow-ups, noise, quiet
  hours, cooldown, activity expiry, and some metadata;
- score() uses salience, recency, known names, promoted questions, donations,
  and related BEA facts;
- random uniform jitter plus or minus .1 is applied before the threshold;
- REACT admits the current item; NOTE stores a transient one-line item;
  DROP discards it;
- if a batch has a REACT, NOTE items in that same batch are promoted;
- digest groups notes by surface, aggregates more than two entries, caps
  visible lines by default at eight, and clears the note buffer after read;
- no event-ID dedupe is present.

The covering high-volume tests are concrete:

- 60 noise heartbeats result in zero model calls;
- one addressed message results in one call;
- an addressed message buried in 60 noise items still results in one call;
- two small-talk items result in zero calls;
- a lively room is bounded by the spoke/cooldown behavior;
- a later context includes a digest and ignored items are not replayed.

This is useful evidence for a future attention layer, but it is not a reason
to add advanced attention to P5-B1. Verdict: DEFER implementation and retain
the bounded-admission and digest concepts as design references.

### E. Skill and PlatformSkill API

Skill in BEA combines five concerns: perception production, tool exposure,
prompt contribution, live state, and optional infrastructure start/stop.
SkillRegistry also owns active toggles and aggregation. PlatformSkill then
adds platform identity, channel routing, input conversion, output handlers,
typing, direct messages, and platform-specific tools.

Option A — port/adapt BEA Skill as the main abstraction: reject. It gives a
Brain-shaped context broad authority and makes a skill a possible owner of
memory, identity, tools, and background tasks.

Option B — keep Lilavel EnvironmentAdapter and capability abstractions
separate: required baseline. It preserves the rule that an environment is
replaceable and not the host of Lilavel.

Option C — hybrid: recommend. A non-authoritative Skill or composition unit
may group:

- one or more EnvironmentAdapter registrations;
- ToolSpec declarations and PromptContribution declarations;
- configuration descriptors;
- lifecycle handles supervised by Runtime.

The grouped unit must not own canonical conversation history, Lilavel
identity, memory, trusted authorization, or unsupervised background tasks.
EnvironmentAdapter observes and acts; ToolSession authorizes external effects;
Core owns conversation semantics; Runtime owns lifecycle and cross-environment
orchestration. Skill registration is composition, not authority.

This is an ADAPT DESIGN decision, not a code port.

### F. Consciousness as a negative-boundary study

BEA Consciousness owns all of the following in one class:

| Responsibility | BEA owner | Lilavel owner | Result |
|---|---|---|---|
| Transcript/context | Consciousness.context, HistoryManager, ConversationMind | ConversationCore canonical history and context composition | Reject BEA ownership |
| Model calls | Consciousness directly calls llm.complete | ModelRuntime and model sidecar | Reject |
| Tool loop | Consciousness._dispatch and ToolRegistry | Core/ModelRuntime plus ApplicationToolSession | Reject |
| External execution | direct platform/expression/memory handlers | application authorization and executor | Reject |
| State | alive, sleeping, batch, correlations, counters, body task | Runtime/Core/ModelRuntime separately | Reject |
| Memory interaction | MemoryStore, MemorySkill, recap, diary | no P5-B1 durable memory; future explicit owner only | Reject |
| Interruption | steering drains and expression interrupt | Core supersession, generation cancellation, stale fencing | Reject literal behavior |
| Lifecycle | Brain and Consciousness start/stop | LilavelRuntime lifecycle and supervision | Adapt only the explicit ownership principle |

The expected negative-boundary conclusion is supported by source evidence.
Wholescale adoption would replace rather than extend Lilavel’s current
architecture. Verdict: REJECT.

## 6. Required adoption matrix

BEA paths in this table are rooted at
/home/sheethappiness/Documents/GitHub/projectBEA. Test execution status for
all listed tests is BLOCKED by the unavailable standard offline environment;
the test names are source-level coverage claims, not PASS results.

| Candidate | BEA source and tests | Verdict | Reason | Lilavel target and adaptation | Dependency cost | Risk |
|---|---|---|---|---|---|---|
| PerceptionBus | src/core/perception/bus.py: PerceptionBus.put, drain, drain_nowait; related tests/test_routing.py and tests/test_consciousness_attention.py | REJECT | Unbounded, no close/backpressure/dedupe, mutable events, one-consumer assumption | Existing Runtime bounded ingress and immutable WorldEvent | Low to rewrite; high to copy | Queue overload and ownership drift |
| wait_or_idle | src/core/perception/bus.py: wait_or_idle; no direct dedicated test; adjacent tests/test_floor.py and tests/test_attention_gate.py | ADAPT DESIGN | Useful event-or-timeout shape, incomplete lifecycle | Runtime IdleOpportunity with explicit reset, latch, shutdown, and Core admission | Low | Repeated heartbeat if no cooldown |
| idle event | src/core/perception/types.py: PerceptionKind.IDLE; tests/test_consciousness_attention.py | ADAPT DESIGN | Synthetic event is useful but BEA mutable and canonicality is unclear | Untrusted noncanonical WorldEvent/IdleOpportunity | Low | Accidental transcript/history write |
| Monologue | src/core/skills/idle.py, data/prompts/monologue.md, consciousness loop; tests/test_consciousness_speech.py, tests/test_initiative.py | ADAPT DESIGN | Prompt and terminal-action concept useful; immediate BEA loop is too eager | One bounded autonomous cognition run with transient prompt | Medium | Rapid repeated LLM calls |
| SpontaneousPresence/rhythm | src/core/mind/spontaneous.py, social/rhythm.py; tests/test_spontaneous.py | REFERENCE ONLY | Durable candidate scan, probability, and multi-conversation policy are BEA-specific | No P5-B1 equivalent | High | Scheduler/memory scope creep |
| Attention | src/core/attention/gate.py, rules.py; tests/test_attention_gate.py, test_attention_rules.py, test_consciousness_attention.py | REFERENCE ONLY | Useful future heuristics, not required for CLI presence | Defer; deterministic wake only | Medium-high | Hidden policy and nondeterminism |
| REACT/NOTE/DROP | src/core/attention/types.py and gate.py; tests/test_attention_gate.py | ADAPT DESIGN | Three-way disposition may later help, but not V0 | Future application admission policy | Medium | Conflating attention with canonical Core |
| Event coalescing | perception/bus.py, mind/scheduler.py, routing.py; tests/test_scheduler.py, test_routing.py | ADAPT DESIGN | Fixed window and per-key bounded pending are useful | Runtime backpressure plus Core run admission | Medium | Unbounded pending or duplicate cognition |
| speak | consciousness.py: _speak; mind/tools.py; tests/test_consciousness_speech.py | ADAPT DESIGN | Terminal output action is useful; direct BEA effects are not | presence.say via ToolSpec/ToolSession; CLI sink only; noncanonical | Low if rewritten | Unauthorized or canonical autonomous output |
| stay_silent | consciousness.py: _stay_silent; tests/test_consciousness_speech.py | ADAPT DESIGN | Deliberate silence is a safe explicit outcome | presence.stay_silent via native tool path | Low | Hidden side effects if not explicit |
| Skill | skills/base.py: Skill, SkillRegistry; tests/test_brain_wiring.py | ADAPT DESIGN | Contribution grouping is useful; Brain authority is not | Hybrid non-authoritative composition unit | Medium | Skill becomes lifecycle/memory owner |
| PlatformSkill | skills/platform.py; tests/test_routing.py, test_conversation.py, test_send_limits.py | REFERENCE ONLY | Stable routing ideas useful; direct effects/identity ownership conflict | Future EnvironmentAdapter plus explicit ToolSession | High | Environment becomes where Lilavel lives |
| Capability registration | agent/tools.py, mind/tools.py, skills/base.py; tests/test_registry.py | REJECT | Direct mutable dispatch lacks strict auth, effect certainty, and stale fencing | Existing ToolRegistry/ToolSession | Low to keep existing; high to replace | Arbitrary autonomous effects |
| Prompt contribution | skills/base.py, consciousness.py, idle.py; tests/test_prompts.py | ADAPT DESIGN | Contribution slots are useful; context owner must remain Core | Per-run PromptContribution snapshot | Low-medium | Hidden mutable prompt authority |
| Activity feed | events.py, web/app.py; tests/test_events_stream.py | REFERENCE ONLY | Bounded diagnostics useful; not history or effect proof | Optional separate evidence stream later | Low | Treating diagnostics as canonical state |
| Scoped conversation routing | mind/routing.py, conversation.py; tests/test_routing.py, test_conversation.py | ADAPT DESIGN | Exact-one-route and stable scope are useful; BEA transcript is not | Existing CoreConversationRouter/runtime routes | Low-medium | Second canonical history owner |
| Consciousness | consciousness.py, brain.py; tests/test_consciousness_speech.py, test_barge_in.py, test_concurrency_e2e.py | REJECT | Owns every Lilavel boundary at once | Keep Runtime/Core/ModelRuntime/sidecar split | Very high | Architecture replacement |
| BEA model loop | consciousness.py, agent/runner.py; tests/test_concurrency_e2e.py, test_barge_in.py | REJECT | Direct provider call, mutable transcript, ad hoc steering | Existing ModelRuntime and sidecar | Very high | Lost generation fencing/settlement |
| BEA transcript | consciousness.context, history_manager.py, mind/conversation.py, recap.py; tests/test_not_losing_the_thread.py, test_conversation.py | REJECT | Multiple durable/context owners | ConversationCore only | Very high | Divergent canonical history |
| BEA tool execution | agent/tools.py, consciousness._dispatch, platform.py; tests/test_registry.py, test_presence_skill.py | REJECT | Direct handler effects and stringified errors | Existing strict ToolSession path | High | External effects without auth/certainty |
| BEA memory | memory/schema.sql, memory/store.py, skills/memory/memory.py; tests/test_memory_store.py | REFERENCE ONLY | Product-specific durable stores and RAG | No P5-B1 memory; future explicit subsystem | Very high | Unrequested persistence and identity leakage |
| Shutdown/cancellation | brain.py, consciousness.py, scheduler.py; tests/test_scheduler.py, test_concurrency_e2e.py | ADAPT DESIGN | Explicit lifecycle hooks useful; detached work and effects are incomplete | Runtime supervision, Core/ModelRuntime cancellation, joined settlement | Medium | Orphaned cognition or effects |

No row receives TAKE DIRECTLY. No production-source row receives
SELECTIVELY PORT in this phase. The selective work product is the set of
behavioral constraints and test scenarios that will be reimplemented against
Lilavel contracts.

## 7. License and provenance obligations

ProjectBEA is MIT-licensed at audited SHA
5b61957d6f94df190e813c720f638000d6a0a37c. The verified notice is in
ProjectBEA/LICENSE and identifies Emanuele Faraci, 2026.

P5-B0 makes no source copy and therefore requires no Lilavel notice change.
If a later task copies a ProjectBEA fragment or derives a test substantially
from it, that task must record:

1. upstream repository:
   https://github.com/emqnuele/projectBEA
2. exact audited upstream SHA;
3. original source path and symbol;
4. MIT license and copyright notice handling;
5. whether tests are copied, translated, or independently reimplemented;
6. the Lilavel adaptation and the ownership boundary that prevents BEA code
   from becoming an authority.

The preferred future path is independent re-expression of behavior against
Lilavel contracts, avoiding copied production source and minimizing
attribution surface.

## 8. P5-B1 design output

### Smallest visible PRESENCE slice

The proposed P5-B1 flow is:

1. uv run lilavel starts one persistent LilavelRuntime and one CLI
   EnvironmentAdapter.
2. The CLI accepts typed input while the process remains alive.
3. Normal user input enters the existing Core conversation path, streams
   through ModelRuntime and the sidecar, and returns the process to idle.
4. Runtime-owned idle tracking observes real activity. Activity resets the
   idle deadline and pending idle admission.
5. True silence creates one bounded, noncanonical IdleOpportunity.
6. A deterministic wake policy admits or rejects the opportunity.
7. If admitted, Core starts one transient autonomous cognition run. Its
   allowed model tools are only presence.say(text) and
   presence.stay_silent().
8. say writes visible text to the CLI sink but does not append autonomous
   text to canonical conversation history. stay_silent completes with no
   user-visible output.
9. New user input has priority, uses existing supersession/physical
   cancellation/stale fencing, and does not bypass provider/executor joined
   settlement.
10. The run settles, the process returns to idle, and shutdown closes input,
    cancels supervised tasks, drains or settles required work, and exits
    cleanly.

The idle opportunity should be represented as a runtime event/admission
concept, not as a synthetic user message. The autonomous run should have a
distinct origin and transient output status, while Core remains the only
canonical conversation owner.

### prompt_toolkit evaluation

prompt_toolkit is a reasonable implementation dependency for the
asynchronous CLI because its patch_stdout mechanism can route background
output without destroying a partially typed prompt. The P5-B1 use should
remain a single prompt session and output sink, not a TUI: no panes,
dashboard, alternate screen, or application framework is required. The
dependency version, root packaging shape, and exact integration API are
UNKNOWN until implementation work begins and must be pinned through the
normal Lilavel dependency workflow.

The official prompt-toolkit documentation recommends
`PromptSession.prompt_async()` for asyncio applications and `patch_stdout()`
when other coroutines may print while a prompt is active:

- <https://python-prompt-toolkit.readthedocs.io/en/stable/pages/asking_for_input.html#prompt-in-an-asyncio-application>
- <https://python-prompt-toolkit.readthedocs.io/en/stable/pages/reference.html#prompt-toolkit.patch_stdout.patch_stdout>

### Explicit exclusions

P5-B1 must not include durable memory, advanced attention, Discord migration,
Twitch, Telegram, voice, vision, Neuro SDK, avatar, MCP expansion,
autonomous arbitrary tools, a scheduler framework, or a high-frequency LLM
heartbeat.

## 9. P5-B1 deterministic scenario plan

Each scenario should become a deterministic test with fake clocks,
controlled model streams, and no paid provider or live environment:

| Scenario | Required proof |
|---|---|
| Cold start → idle → clean shutdown | Runtime starts, CLI task and supervision close, no orphan task or unjoined settlement remains |
| User input while idle | User input is admitted, streams through existing conversation semantics, and returns to idle |
| Real activity resets idle timeout | A qualifying input prevents the pending idle opportunity and restarts the clock |
| Idle timeout → IdleOpportunity | Exactly one bounded noncanonical opportunity is emitted/admitted after the configured true-idle interval |
| Idle opportunity → NO_WAKE | Deterministic policy rejects it without a model call and without immediate retry |
| Wake → presence.stay_silent | Autonomous run uses only the allowed terminal tools, emits no visible output, and returns idle |
| Wake → presence.say | Visible CLI output appears, autonomous output is marked noncanonical, and canonical conversation history is unchanged |
| User input during autonomous generation | Existing supersession/physical cancellation semantics win; stale autonomous output is fenced; joined settlement remains intact; user work is admitted |
| Multiple idle opportunities | Cooldown/latch bounds admissions and prevents a runaway cognition loop |
| Shutdown during autonomous cognition | Cancellation and settlement complete cleanly; no task or tool effect is left unaccounted for |
| One event → at most one cognition admission | Duplicate routing or repeated timeout observations cannot create two runs for one opportunity |
| Background output while typing | CLI output does not corrupt partially typed user input when a background say arrives |

The source-level BEA tests most worth translating into Lilavel-owned tests are
the one-event-one-route assertions in tests/test_routing.py, coalescing and
per-key bound assertions in tests/test_scheduler.py, silence-bound assertions
in tests/test_floor.py, and high-volume no-call assertions in
tests/test_consciousness_attention.py. They are test ideas, not source to
copy wholesale.

## 10. Validation and executable evidence

### ProjectBEA

- Git checkout identity: PASS. Real work tree and exact HEAD recorded.
- Clean state after read-only audit: PASS at the audited SHA.
- License inspection: PASS. LICENSE is MIT.
- Standard offline test setup: BLOCKED. Python 3.10–3.12/uv environment and
  pytest were unavailable; python -m pytest --collect-only -q could not start
  because pytest is not installed. No upstream test was reported as PASS or
  FAIL.
- Paid providers and live integrations: not used.

### Lilavel

The authoritative documentation checks required by docs/VALIDATION.md are:

- git diff --check
- python scripts/check_docs.py
- python scripts/check_architecture.py

Executed from the Lilavel repository root after this record was written:

- git diff --check: PASS.
- python scripts/check_docs.py: PASS
  (DOCS_INTEGRITY=PASS; 30 Markdown files, 94 local links, 11 anchors, and
  6 command paths checked).
- python scripts/check_architecture.py: PASS
  (Core/runtime and adapter dependency-direction guard passed).

No additional package command is required by docs/VALIDATION.md for a
documentation-only change. No provider call or Discord live call was made.

## 11. Remaining UNKNOWNs

- The exact root packaging and dependency pin for prompt_toolkit.
- The exact Core API for a transient autonomous CognitionRun and its origin
  metadata.
- The final idle timeout, cooldown, latch reset, and deterministic WakePolicy
  values.
- Whether autonomous say should have any future canonical audit record beyond
  transient run metadata.
- Live/provider-level evidence for cancellation timing under a real sidecar;
  P5-B0 uses existing deterministic Lilavel contracts but does not run paid
  providers.
- A direct ProjectBEA wait_or_idle timeout test; none was found in the
  audited test tree.
- Any future need to copy or derive a ProjectBEA test and the exact
  attribution form if that occurs.
- Whether a future attention layer should use REACT/NOTE/DROP names or a
  Lilavel-native disposition vocabulary.

## 12. Phase record and exit gate

### Decisions

- No ProjectBEA production subsystem is accepted for literal adoption.
- P5-B1 should use a narrow presence tool seam over Lilavel’s existing native
  tool path.
- Skill API direction is the hybrid composition/registration model, with
  EnvironmentAdapter, ToolSpec/ToolSession, PromptContribution, and
  Runtime-supervised lifecycle kept as separate authorities.
- Consciousness, transcript, memory, direct execution, and provider loop
  remain rejected as BEA ownership patterns.
- No new ADR is added: the audit confirms and sharpens the already accepted
  P5-A boundary in ADR-009; P5-B1 remains a proposal, not an implemented
  durable architecture decision.

### Evidence

- Lilavel current HEAD and P5-A ancestry were verified.
- ProjectBEA is a real clean Git checkout at exact SHA
  5b61957d6f94df190e813c720f638000d6a0a37c.
- Source and named test files were inspected for every required candidate.
- Upstream executable tests were blocked by the unavailable standard
  environment and are not represented as passing.

### Scope and implementation SHA

- Lilavel baseline/implementation SHA for this audit: a1f29cf3746a24b22632d135d5f045be9c1abafc.
- Production implementation changes: none.
- Durable output: this work record only.
- Final documentation commit SHA: reported by the final handoff after
  validation.

### Exit gate

The phase is complete when the three required Lilavel documentation checks
pass, the final diff contains only this audit record, the ProjectBEA worktree
remains clean, and one documentation-only commit is created without push.
