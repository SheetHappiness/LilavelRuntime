# Validation

This document indexes authoritative checks and their interpretation. It records
what a command can establish; it does not claim that a check has been run.
There is no aggregate root package command because the repository contains
independently locked Python and Bun components.

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

These checks cover the zero-environment lifecycle, bounded observation
backpressure, deterministic task settlement, environment/tool registration,
provider-neutral immutable contracts, deterministic direct-message wake, and
single-route dispatch to the source action boundary. Core-backed routing is
exercised through the Discord adapter suite. They do not prove live providers,
live Discord, Neuro, scheduling, model-selected tools, or memory.

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
fixtures, and process-containment tests selected by the host platform.

### Model sidecar

```powershell
Set-Location apps/model-sidecar
npx --yes bun@1.4.0 install --frozen-lockfile
npx --yes bun@1.4.0 run check
npx --yes bun@1.4.0 test
```

These checks cover the TypeScript/Bun sidecar API, protocol parsing, provider
adapter fixtures, cleanup, lifecycle tests, the shared inactive V3 tool-frame
corpus, generation-local ID/alias mapping, and pinned pi-ai raw-argument
correspondence without requiring a live provider. They do not establish Luna
tool selection, provider continuation, endpoint multi-call behavior,
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
`WorldEvent → runtime → Core → action` path, deterministic presenter,
transport, diagnostics, scenario runner, supersession, and semantic streaming.

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
- Sidecar smoke and probes: from `apps/model-sidecar`,
  `npx --yes bun@1.4.0 run smoke -- "Reply exactly STREAM_OK."`,
  `npx --yes bun@1.4.0 run probe -- cancel-recovery`, and
  `npx --yes bun@1.4.0 run probe -- isolation`.
- Discord Stage A transport probe: from `apps/discord-adapter`,
  `uv run --locked python scripts/transport_probe.py`.
- Discord Stage B Core-backed adapter: from `apps/discord-adapter`,
  `uv run --locked python scripts/run_edge.py`.

Discord probes require the supported `LILAVEL_DISCORD_BOT_TOKEN` mechanism and
an actual Discord interaction. Missing credentials produce `BLOCKED`, not live
evidence. Live Core and sidecar checks require the provider's supported
auth-discovery setup. Do not extract, replay, or persist credentials to make a
check run. A sidecar `ready` or `health` event describes local state; it does
not prove provider endpoint availability, quota, entitlement, or successful
live interaction.

No live/provider result is claimed unless the exact command and result are
separately executed and recorded. This migration does not require live checks.
