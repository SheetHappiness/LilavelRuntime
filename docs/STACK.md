# Stack

This file records technology roles, not unimplemented product capabilities.
Provider and package-local operational details stay in the component README
that owns them.

| Area | Technology | Role |
| --- | --- | --- |
| Persistent agent kernel | Python 3.12+ `apps/runtime` | Top-level process lifecycle, bounded event ingress, environment task ownership, character-wide semantic admission, bounded temporal wake hosting, and trusted application-tool registration/execution. |
| Conversational Core | Python 3.12+ with standard-library `sqlite3` | Provider-neutral conversation semantics, local generation lifecycle, canonical messages, and provenance-only persistence. |
| Model transport | TypeScript on Bun | Separate provider/process transport sidecar with the version-two JSONL boundary. |
| Provider integration | Pinned implementation inside `apps/model-sidecar` | Direct model transport and supported authentication discovery; see the [sidecar README](../apps/model-sidecar/README.md). |
| Discord environment adapter | Python 3.12+ with `discord.py==2.7.1` | Replaceable one-to-one DM sensor+action and presentation adapter; it depends on Core, never the reverse. |
| Package tooling | `uv` for Python packages; Bun or the pinned `npx bun@1.4.0` entry point for the sidecar | Reproducible package-local installation, locking, checks, and tests. |

The current stack contains no general or recurring scheduler, world model,
attention engine, arbitrary model-selected tool authority,
retrieval/memory layer, or durable cross-environment agent-state store. The
bounded temporal host and trusted application-tool executor are implemented
runtime seams, not general autonomy or model authority.

See [Architecture](ARCHITECTURE.md) for ownership and invariants and
[Validation](VALIDATION.md) for what the checks can establish.
