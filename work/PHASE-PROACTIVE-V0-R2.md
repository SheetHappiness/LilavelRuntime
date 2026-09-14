# PROACTIVE-V0-R2 — Capability Projection + Stable Discord Target Binding

## Status

**PASS** for deterministic capability awareness, target rebinding, and the
existing one-shot effect path. Live provider/Discord verification remains
**UNVERIFIED**.

## Baseline and commits

- Expected baseline: `b649ee737574590985e49da45697f1568b2fa540`
- Preflight HEAD: `b649ee737574590985e49da45697f1568b2fa540`
- Implementation SHA: `70b2193`
- Closeout SHA: documentation closeout commit follows the implementation commit
- Push: not performed

## Implemented truth

`ProactiveCapabilityResolver` is a bounded runtime-owned context contribution.
It is composed into the existing `ProductionContextComposer` only for the
opt-in proactive Discord runtime. Its state callback reports only smoke-enabled
and trusted-target-bound state plus the bounded idle interval. When valid, the
resolver projects one provider-neutral `TEMPORAL_RECONSIDERATION` capability
to `USER_RESPONSE` and `INTERNAL_APPRAISAL`:

```text
one-shot idle reconsideration is available
a successful USER interaction may leave one current runtime-owned intention
the runtime may reconsider it after the configured idle interval
silence remains valid
eventual external speech remains subject to runtime validation
the model does not control destination or permission
```

The projection contains no Discord identifiers and makes no delivery, timer,
restart, scheduling, recipient, or future-speech guarantee. It does not create
or mutate `MindState`; appraisal remains the only model-controlled admission
of a real intention, and the existing `ProposalApplicationCoordinator` remains
the only effect path. Smoke-off and unbound/invalid-target states do not
surface the proactive fact.

`DiscordProactivePresence` now keys its single target by opaque subject
identity. Same-subject binding refreshes the trusted channel reference,
including when the adapter supplies a replacement Python object. A genuinely
distinct subject still disables proactive speech fail-closed and cancels the
pending idle opportunity. No channel identifier crosses into cognition,
context, `MindState`, or Core history.

## Evidence and validation

- Direct R2 capability projection tests: **PASS** — four focused runtime tests
  cover default-off/unbound behavior, bounded provider-neutral content,
  INTERNAL_APPRAISAL composition, and MindState non-mutation.
- Production Discord capability and rebinding tests: **PASS** — the proactive
  smoke suite covers USER_RESPONSE and INTERNAL_APPRAISAL context, absence of
  destination metadata, same-subject channel refresh, distinct-subject
  fail-closed behavior, and the existing one-shot/no-change paths.
- Full deterministic packages: **PASS** — Contracts 7 passed, Core 195 passed
  and 4 skipped, Runtime 397 passed, and Discord adapter 119 passed with 10
  existing Discord deprecation warnings.
- Ruff lint, format, and strict Pyright for Runtime and Discord adapter:
  **PASS**.
- Documentation integrity and architecture guards: **PASS**.
- `git diff --check`: **PASS**.
- Live provider + Discord send: **UNVERIFIED**; no credentials or live service
  were used.

## Exit gate

**PASS.** Lilavel receives truthful bounded proactive capability context only
when the opt-in runtime has a trusted target; capability awareness grants no
effect authority; appraisal remains model-driven; same-subject channel object
replacement preserves and refreshes the target; distinct subjects disable
proactive speech; and existing one-shot semantics remain unchanged.
