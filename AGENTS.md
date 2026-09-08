# LilavelRuntime Development Contract

## Project

Lilavel is modeled at the product boundary as a persistent agent runtime. This
repository currently contains the migrated conversational foundation and its
replaceable environment adapter; it does not yet implement autonomous
scheduling, wake, attention, tools, or world-state behavior.

Do not invent requirements for capabilities that are not implemented or
documented. Product direction is context, not an implementation specification.

## Authority and scope

- Tracked repository contents and Git history are authoritative. Reconstructed
  chat logs are secondary leads, not historical evidence.
- Never invent requirements or historical rationale; use `UNKNOWN` when the
  repository and approved evidence do not support a claim.
- The future `LilavelRuntime` composition boundary owns agent lifecycle,
  cross-environment orchestration, and agent state only when those contracts
  are explicitly designed and implemented.
- `ConversationCore` owns conversational semantics and canonical conversation
  state. `ModelRuntime` owns the local generation lifecycle. The model sidecar
  owns provider/process transport. Environment adapters observe and act; they
  do not own agent identity, memory, or canonical history.
- Discord is a replaceable environment adapter, not the host of Lilavel.

## Repository authority

This repository's implementation and documentation are authoritative. External
projects may inform consequential decisions, but their architecture and
assumptions do not automatically apply to LilavelRuntime.

## Research and multi-agent work

For consequential decisions involving model providers, inference transports,
streaming protocols, external runtimes, or future agent orchestration, verify
the relevant behavior with targeted research or a controlled experiment before
committing to architecture. Use multiple agents only when independent
investigation or verification materially reduces uncertainty.

When agents modify files concurrently, their ownership must not overlap.

## Secrets

Never place credentials, OAuth tokens, API keys, service tokens, cookies, or
other secrets in source code, prompts, committed configuration, test fixtures,
logs, or documentation.

Use only the supported authentication mechanism or auth-discovery boundary of
the relevant provider or runtime. Never manually extract, copy, replay,
persist, or expose authentication material merely to simplify an integration.

## Git

Inspect the worktree before making changes. Agents normally commit completed,
validated task work unless the task explicitly requires an uncommitted result.

Before committing:

- review the final diff;
- run the relevant validation;
- ensure the commit contains only work belonging to the completed task.

If a worktree already contains changes, classify them before acting. Never
sweep ambiguous, unrelated, incomplete, or user-owned changes into a commit.
Stage only task-owned files.

## Verification

Do not report a validation step as passing unless it was actually executed. Use
the authoritative package commands in [`docs/VALIDATION.md`](docs/VALIDATION.md)
and distinguish deterministic, live/provider-dependent, and platform-specific
evidence.

Use exactly these result meanings:

- `PASS` — the named check completed successfully for its stated scope.
- `FAIL` — the named check ran and found a failure.
- `BLOCKED` — a required credential, host capability, platform, or external
  service was unavailable; this is neither `PASS` nor `FAIL`.
- `UNVERIFIED` — no authoritative check or committed evidence establishes the
  claim.

The model-sidecar host reserves stdout for version-two JSONL frames. Human
diagnostics use stderr. Keep deterministic evidence separate from live,
restart, external-service, and platform claims.

## Navigation

- [README](README.md) — product model and repository orientation.
- [Architecture](docs/ARCHITECTURE.md) — ownership, flows, and invariants.
- [Stack](docs/STACK.md) — technology roles and boundaries.
- [Validation](docs/VALIDATION.md) — checks and evidence semantics.
- [Migration](docs/MIGRATION.md) — source selection and destination mapping.
- [Decisions](docs/decisions/) — accepted architectural decisions.
- Scoped package contracts: [Core](apps/core/README.md), [model sidecar](apps/model-sidecar/README.md), and [Discord adapter](apps/discord-adapter/README.md).

## Maintaining this file

Keep this file limited to recurring LilavelRuntime-specific rules. Move
subsystem-specific guidance to a nearby scoped `AGENTS.md` only when recurring
rules justify one; do not grow a second architecture document here.

## Phase completion

At phase close, the agent automatically updates durable repository
documentation; the user does not manually author the phase record. A large
phase must have a concise `work/` record containing decisions, evidence,
validation, unknowns, exit gate, and implementation SHA. Add or update an ADR
only for a consequential durable architecture decision. Update
`docs/ARCHITECTURE.md` only when current implemented architecture truth
changes, and `docs/VALIDATION.md` only when canonical commands or evidence
semantics change. Update an existing canonical roadmap/status document when
appropriate; do not create one solely for phase closure. Keep each truth in
one canonical location and link or reference it elsewhere.
