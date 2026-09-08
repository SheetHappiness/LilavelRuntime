# P4-A Tool Contracts and Transport Proof

## FACT

- Implementation commit: `f6111b042da8e48ded4b289d08d4195212a692cb`.
- `lilavel-contracts` is a dependency-safe package used by Core and runtime.
  It hosts immutable provider-neutral values and owns no policy, authorization,
  execution, settlement, or canonical history.
- The active JSONL host and `ModelRuntime` path remain protocol V2. V3 is an
  inactive strict contract/parser proof, so this change cannot expose a
  model-selected Discord action or start provider continuation.

## DECISION

For ordinary OpenAI Codex Responses function calls on pinned
`@oh-my-pi/pi-ai@18.1.2`, the sidecar records only the raw terminal
`response.output_item.done` function item through pi-ai's `onSseEvent` hook.
It pairs that native `call_id|item_id` with pi-ai's finalized `toolcall_end`
ID, parses raw arguments independently, and requires exact deep equality with
pi-ai's normalized object. Missing, duplicate, ambiguous, or unequal pairs
fail closed; malformed raw JSON and valid non-object JSON are non-executable.

## RESULT

- Shared Python and TypeScript V3 corpus: PASS.
- Pinned raw-argument, alias, generation-local ID, bounds, and tool-wait state
  proofs: PASS.
- No-tool V2 regression: PASS.
- No production tool execution: PASS by inactive integration boundary.

## UNVERIFIED

- Luna tool selection, real-provider tool-result continuation, endpoint
  multi-call behavior, `code_mode_only`, provider cancellation after a tool
  call, and Discord effects.

## EXIT GATE

P4-B may use these contracts and proofs. It must add deterministic
end-to-end V3 host/Core settlement before production activation and must not
treat this record as provider-backed evidence.
