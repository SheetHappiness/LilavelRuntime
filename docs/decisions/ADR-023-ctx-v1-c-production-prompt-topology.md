# ADR-023: CTX-V1-C production context injection and prompt topology

## Status

Accepted for CTX-V1-C.

## Context

CTX-V1-A defined the ownership boundary between CharacterCanon,
OperatingCanon, ContextFrame, ConversationCore, MindState, and future Memory.
CTX-V1-B proved deterministic ContextFrame construction and purpose-specific
projections, but deliberately left production model requests unchanged.

Production requests now need current runtime orientation without making volatile
state look like character law, copying canonical history into ContextFrame, or
turning advisory social/capability context into effect authority. The existing
Core, cognition, sidecar, and D2/E2 paths must retain their call and lifecycle
semantics.

## Decision

Use one Runtime-owned `ProductionContextComposer` for all production
ContextFrame-to-guidance composition. It calls the existing deterministic
`ContextFrameBuilder` and `compile_context_projection`; it does not call a
model, mutate runtime state, execute tools, or infer capabilities from package
or environment names. A typed runtime provenance factory supplies only bounded
scope, trigger, wake, intention, and source references.

Compile model requests in stable-to-volatile order:

```text
CharacterCanon guidance
OperatingCanon
stable task/control policy
ConversationCore canonical messages where applicable
purpose-specific ContextFrame projection
current user, observation, or wake input
```

CharacterCanon and OperatingCanon remain separate compiler outputs. The
OperatingCanon projection is stable for the same canon and contains no current
frame metadata. ContextFrame blocks are appended only after stable guidance.
Canonical messages remain owned by ConversationCore and are never copied into a
ContextFrame. The planner may receive the same minimal USER_RESPONSE
projection when D2 calls it, but remains advisory and cannot alter fixed USER
attention/intervention admission.

The four production purposes use the existing model calls: Core's direct USER
request, the optional D2 disposition planner, LocalCognitionEngine ambient E2,
LocalCognitionEngine internal appraisal, and its temporal-wake path. Temporal
wake interprets old provenance against a newly built current frame and current
clock; it never reconstructs the historical world.

Use the existing 64 KiB prompt/context and 16 KiB/32-block guidance limits,
with an explicit 80 KiB combined request accounting bound. Optional projection
blocks are reduced from the end of the deterministic projection order and
omitted as whole blocks. No arbitrary UTF-8 byte slicing is permitted. Stable
canon and current required input remain ahead of optional volatile projection.
Builder/projection failures fail closed for the context contribution only; the
safe request continues with stable policy and existing canonical evidence.

Record bounded, content-free assembly evidence: purpose, whether OperatingCanon
and context were injected, projection block kinds, history/input/projection/
total bytes, omission/truncation counts, and outcome. Do not record raw user or
observation text, intention text, frame/source identifiers, model JSON, or
chain-of-thought.

## Consequences

- Production requests receive a minimal deterministic current-runtime view
  without a context-generation inference.
- Stable Character/Operating prefixes remain reusable ahead of volatile frame
  data where the provider transport supports that ordering.
- Conversation history, context, memory, and effect authority retain separate
  owners.
- Social context remains advisory; E2/P4 effect-time SPEAK revalidation is
  still authoritative when state changes after cognition.
- The default ambient speech rollout remains `OFF`.
- Combined request sizes and structural omissions are observable without
  content telemetry.

## Rejected alternatives

- A new context-generation model call was rejected because deterministic
  builder/projection output is sufficient and CTX-V1-C must add zero calls.
- Copying canonical messages, observation payloads, or memory into ContextFrame
  was rejected because those owners already have distinct contracts.
- Injecting volatile context before CharacterCanon or OperatingCanon was
  rejected because it creates avoidable stable-prefix churn.
- Treating unknown context as known absence was rejected because it invents
  runtime truth.
- Letting social or capability text authorize an effect was rejected because
  only existing runtime application/revalidation authority can do so.
