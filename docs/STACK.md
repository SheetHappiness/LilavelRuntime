# Stack

This file records technology roles, not unimplemented product capabilities.
Provider and package-local operational details stay in the component README
that owns them.

| Area | Technology | Role |
| --- | --- | --- |
| Product runtime boundary | Repository-level `LilavelRuntime` composition | Future persistent agent lifecycle and cross-environment orchestration; no dedicated runtime package is implemented yet. |
| Conversational Core | Python 3.12+ with standard-library `sqlite3` | Provider-neutral conversation semantics, local generation lifecycle, canonical messages, and provenance-only persistence. |
| Model transport | TypeScript on Bun | Separate provider/process transport sidecar with the version-two JSONL boundary. |
| Provider integration | Pinned implementation inside `apps/model-sidecar` | Direct model transport and supported authentication discovery; see the [sidecar README](../apps/model-sidecar/README.md). |
| Discord environment adapter | Python 3.12+ with `discord.py==2.7.1` | Replaceable one-to-one DM sensor+action and presentation adapter; it depends on Core, never the reverse. |
| Package tooling | `uv` for Python packages; Bun or the pinned `npx bun@1.4.0` entry point for the sidecar | Reproducible package-local installation, locking, checks, and tests. |

The current stack contains no dedicated scheduler, world model, attention
engine, tool runtime, retrieval/memory layer, or durable cross-environment
agent-state store. Those are future design surfaces, not hidden dependencies.

See [Architecture](ARCHITECTURE.md) for ownership and invariants and
[Validation](VALIDATION.md) for what the checks can establish.
