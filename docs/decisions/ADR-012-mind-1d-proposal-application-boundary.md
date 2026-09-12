# ADR-012 — MIND-1D proposal application boundary

## Status

Accepted and implemented on 2026-09-12.

## Decision

Keep cognition proposals inert until a separate runtime-owned
`ProposalApplicationCoordinator` receives a successfully completed MIND-1C
outcome. The coordinator is the trusted boundary for the invariant:

```text
Proposal != Effect
```

It rejects outcomes without the runner-owned completion proof, scope-mismatched
outcomes, stale state versions, unsupported state operations, missing trusted
provenance, and invalid action compilation before effectful work.

State and external actions are separate subpaths. A state proposal is compiled
into the narrow runtime-owned `MindStateDelta` command and committed as one
version-fenced `MindState` batch. The model proposal never mutates state and
cannot provide Core message provenance. Application composition supplies the
actual provenance. A committed delta increments the local state version once;
the complete batch fails closed on conflict or capacity exhaustion.

An action proposal contains only a narrow kind and model-owned content. Trusted
composition maps the kind to an application-selected canonical tool name and
arguments. The coordinator then uses the existing P4 registry, exposure,
strict validation, authorization, liveness, sequential executor, settlement,
and `ToolResult` effect certainty. It does not create a second executor or
allow proposal content to select destinations, scope, permissions, or handles.

## Ordering and lifecycle

The complete proposal set is structurally validated first. Local state is
applied before external actions. External actions then execute in their
deterministic proposal order. This is not a distributed transaction: confirmed
or unknown external effects are never rolled back. Per-path statuses and
per-proposal results record partial application honestly, and `effect=unknown`
is never automatically retried.

Each outcome has a deterministic application identity derived from its trusted
scope and episode ID. The coordinator keeps a bounded fence ledger and records
rejected, completed, partial, and failed attempts. A repeated identity returns
`DUPLICATE` without creating another P4 session or external attempt.

Application results are runtime evidence, not ConversationCore messages or
trusted guidance. Applying a state or action proposal does not append hidden
cognition text to canonical history. The existing reactive DM path remains
unchanged.

## Consequences

- MIND-1C remains effect-free and ends at validated inert proposals.
- MIND-1D can make a proposal real only after runtime validation and policy.
- MIND-1E may later add temporal wake proposals and scheduler admission; it is
  not part of this boundary.
- A trusted application composition must provide state provenance and explicit
  action-tool mappings. No default composition silently grants capabilities.
- Live provider, Discord, restart, and broader autonomous behavior remain
  `UNVERIFIED` or deferred.

## Evidence

`apps/runtime/tests/test_proposal_application.py` proves valid and stale state
application, P4 action routing, denied/invalid pre-effect rejection, unknown
effect fencing, duplicate protection, front-loaded mixed validation, honest
partial settlement, conversation isolation, and preservation of MIND-1C
inertness.
