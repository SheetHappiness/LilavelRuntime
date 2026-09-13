# Validation

This document indexes authoritative checks and their interpretation. It records
what a command can establish; it does not claim that a check has been run.
The root Python project is only the persistent CLI launcher; package validation
remains independently locked because the repository contains separate Python
and Bun components.

## Result vocabulary

- `PASS` — the named check completed successfully for its stated scope.
- `FAIL` — the named check ran and found a failure.
- `BLOCKED` — a required credential, host capability, platform, or external
  service was unavailable. `BLOCKED` is neither `PASS` nor `FAIL`.
- `UNVERIFIED` — no authoritative check or committed evidence establishes the
  claim.

Reports must name the package, command, host/platform, and scope. Do not turn a
deterministic fixture result into live-provider, restart, Windows, or Discord
evidence.

## Migration and static checks

From the repository root:

```powershell
python scripts/check_docs.py
python scripts/check_architecture.py
git diff --check
```

`check_docs.py` verifies local Markdown link targets and heading anchors, plus
explicit `Set-Location`/`cd` paths in fenced command examples. The architecture
guard is a small standard-library check for the implemented Core/runtime,
runtime/environment-specific, and adapter/persistence dependency directions.
`git diff --check` covers whitespace errors in the destination diff. None of
these commands proves live behavior or autonomous model/tool behavior.

## Deterministic package checks

Run the applicable package-local commands from the package directory.

### Root presence launcher

```powershell
uv sync --locked
uv lock --check
uv run --locked lilavel --help
```

These checks prove the root launcher resolves the pinned runtime and
`prompt-toolkit` dependencies and exposes the expected command surface. They do
not invoke a provider or prove an interactive terminal session.

### Shared contracts

```powershell
Set-Location apps/contracts
uv sync --locked
uv lock --check
uv run --locked ruff check .
uv run --locked ruff format --check .
uv run --locked pyright
uv run --locked pytest
```

These checks cover the immutable, bounded provider-neutral tool values. They
do not establish authorization, execution, external effects, or history use.

### Persistent runtime

```powershell
Set-Location apps/runtime
uv sync --locked
uv lock --check
uv run --locked ruff check .
uv run --locked ruff format --check .
uv run --locked pyright
uv run --locked pytest
```

These checks cover the zero-environment lifecycle, runtime-owned inert
`SemanticActor` composition, bounded semantic mailbox/priority/preemption/
settlement/fencing evidence, bounded observation
admission/window behavior, deterministic task settlement, environment/tool
registration, provider-neutral immutable contracts, observation-only admission,
the deterministic `NO_COGNITION`/bounded `CognitionTrigger` gate, the
serialized MIND-1C bounded cognition episode, immutable context snapshots,
typed inert state/action proposals, fail-closed episode lifecycle, and
proposal/conversation/action isolation, the MIND-1D proposal application
coordinator, version-fenced atomic state deltas, front-loaded mixed-proposal
validation, P4 action compilation, effect certainty, partial application, and
duplicate application fencing, the explicit
reactive single-route dispatch to the source action boundary, the application-
owned tool registry/exposure/authorization seam, and deterministic tool-session
execution/containment. P5-B1 coverage adds runtime-owned local presence,
monotonic idle reset/latching, deterministic wake/no-wake, noncanonical
say/silence actions, terminal continuation suppression, user preemption before
generation and during tool execution, joined settlement, bounded admissions,
shutdown, and the thread-safe CLI output boundary. Core-backed Discord routing
remains exercised through the Discord adapter suite. MIND-0 coverage adds
strict appraisal parsing, bounded runtime intentions/self-actions, canonical
provenance binding, appraisal preemption, intention-specific idle admission,
noncanonical expression, and next-turn self-action projection. These checks do
not prove live providers, live Discord, Neuro, general or recurring scheduling,
arbitrary model-selected tools, durable memory, or restart persistence. MIND-1F-C
coverage additionally proves the optional runtime composition of generic and
temporal triggers through `SemanticActor`, trigger replay fencing,
runner-to-application sequencing, deadline-driven temporal hosting with
earlier-wake and cancellation reactions, single-active Mind execution,
quiet/failure/rejection settlement, cooperative cancellation, and shutdown
ordering. MIND-1E
deterministic coverage additionally proves inert temporal proposals, deadline
clamping, bounded wake admission, deduplication, cancellation/supersession,
deterministic due ordering, one-shot dispatch fencing, serialized temporal
cognition, failure non-resurrection, and provenance isolation. It does not
prove durable accepted wakes or restart recovery. MIND-1F-D2 coverage
additionally proves actor-owned CLI USER submission, shared CLI/Discord
serialization, isolated `local-cli` Core history, runtime-owned submission
replay fencing, cancellation/settlement, legacy Presence exclusion, and clean
CLI composition shutdown.

On the current Linux/Python 3.14 host, the complete runtime pytest command has
pre-existing timing-sensitive async-teardown failures. The current recheck
first stalls after
`test_superseded_prepared_run_cannot_start_stale_generation`; after that test
is deselected, the same teardown behavior is observed after
`test_actor_cancellation_contains_planner_and_starts_no_response`; after both
are deselected, the known
`test_temporal_non_user_work_waits_behind_active_user_conversation` also does
not settle. Report the full runtime check as `FAIL`; a residual run with those
three exact tests deselected may be run and reported separately. This is host
behavior, not evidence that the focused E1 policy fails.

The focused D2 proofs can be rerun from `apps/runtime` with:

```powershell
uv run --locked pytest tests/test_conversation_actor_d2.py
```

The focused COG-V1-B deterministic attention proofs can be rerun from
`apps/runtime` with:

```powershell
uv run --locked pytest tests/test_attention.py
```

This focused suite proves the bounded evidence extractor, ordered
`DROP`/`NOTE`/`THINK` policy, payload trust boundary, interest-affinity
anti-keyword behavior, per-observation batch selection, and zero cognition
calls for `NOTE`/`DROP`.

The RUNTIME-H1 bounded replay/liveness proofs can be rerun from `apps/runtime`
with:

```powershell
uv run --locked pytest tests/test_proposal_application_h1.py
```

This focused suite proves 1,001 sequential applications, bounded replay
retention, recent and retired duplicate rejection, concurrent duplicate
serialization, confirmed/unknown effect non-retry, state/temporal/mixed
replay safety, cancellation settlement, canonical permit tamper rejection,
forged-permit rejection, unused-permit retirement, and rotation only after
application settlement. It remains a process-lifetime proof; restart replay
and durable idempotency are `UNVERIFIED`.

The RUNTIME-H2 failure/result and architecture-regression proofs can be rerun
from `apps/runtime` with:

```powershell
uv run --locked pytest tests/test_runtime_h2.py
```

This focused suite proves actor-poison propagation to runtime failure,
fail-closed USER/NON_USER admission, bounded shutdown containment, non-poisoning
normal cancellation, three-way state/temporal/action status aggregation,
temporal-success/action-failure partial reporting, unchanged duplicate fencing,
and rejection of a synthetic direct-generation lane in `PersistentPresenceRuntime`.

The focused COG-V1-C disposition-planner and evaluation proofs can be rerun
from `apps/runtime` with:

```powershell
uv run --locked pytest tests/test_disposition_planner.py
```

This suite proves the strict structured planner parser, bounded recent
canonical context, deterministic Character v0 projection, runtime-owned focus
compilation, fixed direct-user `THINK`/`RESPOND` binding, and reuse of the
direct-user subset of the COG-V1-A policy corpus. It uses a deterministic fake
generation and does not establish live provider behavior or production
USER-route integration.

The focused COG-V1-D1 run-bound USER proofs can be rerun from `apps/runtime`
with:

```powershell
uv run --locked pytest tests/test_cog_v1_d1.py
```

This suite proves post-acceptance planner ordering, immutable per-run behavior
binding, sequential-run isolation, supersession containment, default-only and
explicit planner modes, strict fallback/no-retry behavior, actor-token planner
cancellation containment, raw-output exclusion from trusted guidance, and
bounded D1 evidence. It does not establish the final D2 selective routing
policy or live-provider behavior.

The focused COG-V1-D2 selective-deliberation proofs can be rerun from
`apps/runtime` with:

```powershell
uv run --locked pytest tests/test_cog_v1_d2.py
```

This suite proves the provider-neutral FAST/PLAN contract, zero-versus-one
planner calls, D1 fallback and per-run binding, injected-policy failure fallback,
content-free deliberation telemetry, quality-derived oracle labels, the
`ALWAYS_FAST`, `ALWAYS_PLAN`, and deterministic-rule baselines, confusion-matrix
metrics, counterfactual routing, deep technical FAST behavior, socially nuanced
PLAN behavior, irrelevant tailoring FAST behavior, and bounded delayed context.
The corpus benchmark is deterministic and small; live planner quality and live
latency remain `UNVERIFIED`. A learned semantic classifier is deferred because
the repository has no justified dependency or corpus size for one.

The focused COG-V1-E1 ambient intervention and social-permission proofs can be
rerun from `apps/runtime` with:

```powershell
uv run --locked pytest tests/test_intervention.py
```

This suite proves the bounded `NONE`/`RESPOND`/`INTERJECT` candidate and
context contracts, direct USER exclusion, deterministic hard denials,
per-class freshness/revalidation, recent-speech/backoff and budget placement,
trusted response obligation, strict unsolicited initiative, interest/joke and
handled-state traps, the absence of model/effect/presentation paths, the
human-authored counterfactual corpus, intervention-specific confusion and
precision/recall metrics, and the explicit `ALWAYS_NONE` silence baseline. It
does not establish production ambient speech; E1 does not wire such a path.

### Core

```powershell
Set-Location apps/core
uv sync --locked
uv lock --check
uv run --locked ruff check .
uv run --locked ruff format --check .
uv run --locked pyright
uv run --locked pytest
```

These checks cover Core conversation semantics, Character v0 fixtures and
evaluation harnesses, persistence/evidence, runtime lifecycle, protocol
fixtures, the opt-in V3 tool-wait/joined-settlement lifecycle, and
process-containment tests selected by the host platform.

### Model sidecar

```powershell
Set-Location apps/model-sidecar
npx --yes bun@1.4.0 install --frozen-lockfile
npx --yes bun@1.4.0 run check
npx --yes bun@1.4.0 test
```

These checks cover the TypeScript/Bun sidecar API, protocol parsing, provider
adapter fixtures, cleanup, lifecycle tests, the shared V3 tool-frame
corpus, generation-local ID/alias mapping, pinned pi-ai raw-argument
correspondence, and the opt-in bounded V3 continuation harness without requiring
a live provider. They do not establish Luna tool selection, live provider
continuation, endpoint multi-call behavior,
code-mode-only behavior, real-provider cancellation, or Discord effects.

### Discord adapter

```powershell
Set-Location apps/discord-adapter
uv sync --locked
uv lock --check
uv run --locked ruff check .
uv run --locked ruff format --check .
uv run --locked pyright
uv run --locked pytest
```

These checks cover the replaceable DM adapter, the complete
`WorldEvent → Observation admission → cognition gate → explicit reactive step
→ Core → action` path, deterministic presenter,
transport, diagnostics, scenario runner, supersession, semantic streaming, and
the explicit P4-D registry/authorization/one-shot Discord executor proof with
fake transport. They do not imply live provider selection or live Discord
delivery.

### Shared protocol boundary

Run both the Core and model-sidecar command sets when validating the shared
JSONL boundary. Core covers the Python schema/runtime side; the sidecar covers
the TypeScript host/provider-adapter side. This remains deterministic evidence,
not live-provider evidence.

## Platform-specific checks

The Core pytest suite includes Windows-only checks for the real `npx` → Bun
launcher, process containment, and the Windows protocol path. A Windows run of
the same Core pytest command is required for a Windows `PASS`. On other
platforms, marked tests may be skipped; a skip is not a Windows `PASS`.
The POSIX fallback does not establish the Windows descendant-containment
guarantee.

## Live, provider, and credential-dependent checks

These commands require supported authentication or an external service and
must be reported separately from deterministic checks:

- Core Luna probe: from `apps/core`,
  `uv run --locked python scripts/conversation_live.py`.
- Persistent presence CLI: from the repository root,
  `uv run --locked lilavel --idle-seconds 15 --wake-after-opportunities 2 --debug-presence`.
- Sidecar smoke and probes: from `apps/model-sidecar`,
  `npx --yes bun@1.4.0 run smoke -- "Reply exactly STREAM_OK."`,
  `npx --yes bun@1.4.0 run probe -- cancel-recovery`, and
  `npx --yes bun@1.4.0 run probe -- isolation`.
- Discord Stage A transport probe: from `apps/discord-adapter`,
  `uv run --locked python scripts/transport_probe.py`.
- Discord Stage B Core-backed adapter: from `apps/discord-adapter`,
  `uv run --locked python scripts/run_edge.py`.
- P4-D scoped provider-backed Discord proof: only through an explicitly
  enabled test composition and one admitted test DM; this is not a default
  package command and must be recorded separately from D1 deterministic
  evidence.

Discord probes require the supported `LILAVEL_DISCORD_BOT_TOKEN` mechanism and
an actual Discord interaction. Missing credentials produce `BLOCKED`, not live
evidence. Live Core and sidecar checks require the provider's supported
auth-discovery setup. Do not extract, replay, or persist credentials to make a
check run. A sidecar `ready` or `health` event describes local state; it does
not prove provider endpoint availability, quota, entitlement, or successful
live interaction.

No live/provider result is claimed unless the exact command and result are
separately executed and recorded. This migration does not require live checks.
