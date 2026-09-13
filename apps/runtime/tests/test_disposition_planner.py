"""COG-V1-C disposition-planner and evaluation-boundary proofs."""

from __future__ import annotations

import json
from collections.abc import Iterator
from dataclasses import dataclass

import pytest
from lilavel_core import (
    AttentionDecision,
    CognitionReasonCode,
    ContextMessage,
    DispositionCandidate,
    GenerationCompleted,
    GenerationEvent,
    InterventionDecision,
    ModelRequest,
    TextDelta,
)
from lilavel_core.cognition_eval import COG_V1_A_SCENARIOS

from lilavel_runtime import (
    MAX_DISPOSITION_CONTEXT_MESSAGES,
    DispositionPlanner,
    candidate_to_policy_decision,
    compile_disposition_focus,
    parse_disposition_candidate,
)
from lilavel_runtime.cognition_model import (
    DISPOSITION_PLANNER_CONTROL_GUIDANCE,
    MAX_DISPOSITION_PLANNER_RESULT_BYTES,
)
from lilavel_runtime.disposition_eval import evaluate_disposition_candidate


def _payload(**changes: object) -> str:
    value: dict[str, object] = {
        "aim": "answer",
        "stance": "neutral",
        "engagement": "normal",
        "directness": "normal",
        "desired_length": "normal",
        "humor_allowed": False,
        "question_policy": "avoid",
        "initiative": "normal",
        "reason_codes": ["direct_address"],
    }
    value.update(changes)
    return json.dumps(value, separators=(",", ":"))


def test_parser_returns_bounded_candidate_and_rejects_untrusted_fields() -> None:
    candidate = parse_disposition_candidate(
        _payload(
            aim="clarify",
            stance="curious",
            question_policy="required",
            reason_codes=["direct_address", "ambiguity_material"],
        )
    )

    assert candidate == DispositionCandidate(
        "clarify",
        "curious",
        "normal",
        "normal",
        "normal",
        False,
        "required",
        "normal",
        (CognitionReasonCode.DIRECT_ADDRESS, CognitionReasonCode.AMBIGUITY_MATERIAL),
    )
    assert parse_disposition_candidate(_payload(confidence=0.9)) is None
    assert parse_disposition_candidate(_payload(reasoning="because")) is None
    assert parse_disposition_candidate(_payload(aim={"value": "answer"})) is None


@pytest.mark.parametrize(
    "raw",
    (
        "not json",
        _payload(reason_codes=[]),
        _payload(reason_codes=["direct_address", "direct_address"]),
        _payload(reason_codes=["unsupported"]),
        _payload(reason_codes=["direct_address"] * 9),
        _payload(humor_allowed="false"),
        _payload(initiative=None),
        _payload(reason_codes={"value": "direct_address"}),
        _payload() + " {}",
        '{"aim":"answer","aim":"answer","stance":"neutral",'
        '"engagement":"normal","directness":"normal","desired_length":"normal",'
        '"humor_allowed":false,"question_policy":"avoid","initiative":"normal",'
        '"reason_codes":["direct_address"]}',
        "{}",
        "[]",
        _payload()[:-1] + ',"unknown":true}',
    ),
)
def test_parser_fails_closed_for_malformed_or_overspecified_output(raw: str) -> None:
    assert parse_disposition_candidate(raw) is None


def test_parser_rejects_oversized_output() -> None:
    assert parse_disposition_candidate("x" * (MAX_DISPOSITION_PLANNER_RESULT_BYTES + 1)) is None


def test_focus_compiler_never_uses_model_text_and_policy_keeps_invariants() -> None:
    candidate = DispositionCandidate(
        "disagree",
        "skeptical",
        "high",
        "high",
        "normal",
        False,
        "avoid",
        "normal",
        (CognitionReasonCode.CONTRADICTION,),
    )

    assert compile_disposition_focus(candidate) == (
        "address the important conflict in the user's premise"
    )
    decision = candidate_to_policy_decision(candidate)
    assert decision.attention is AttentionDecision.THINK
    assert decision.intervention is InterventionDecision.RESPOND
    assert decision.working_state is not None
    assert decision.working_state.focus == compile_disposition_focus(candidate)


def test_eval_harness_reuses_the_existing_policy_corpus() -> None:
    scenario = next(item for item in COG_V1_A_SCENARIOS if item.id == "cog-v1-a-17")
    candidate = DispositionCandidate(
        "answer",
        "neutral",
        "normal",
        "normal",
        "low",
        False,
        "avoid",
        "low",
        (CognitionReasonCode.DIRECT_ADDRESS,),
    )

    result = evaluate_disposition_candidate(scenario, candidate)

    assert result.passed
    assert result.policy_result is not None
    assert result.policy_result.passed


@dataclass
class _Generation:
    generation_id: str
    events_to_emit: tuple[GenerationEvent, ...]
    epoch: int = 1

    def events(self) -> Iterator[GenerationEvent]:
        yield from self.events_to_emit


class _Model:
    def __init__(self, response: str) -> None:
        self.response = response
        self.requests: list[ModelRequest] = []

    def generate(self, request: ModelRequest) -> _Generation:
        return self.generate_for_run(request, scope_id="test", logical_run_id="test")

    def generate_for_run(
        self,
        request: ModelRequest,
        *,
        scope_id: str,
        logical_run_id: str,
    ) -> _Generation:
        del scope_id, logical_run_id
        self.requests.append(request)
        generation_id = f"generation-{len(self.requests)}"
        return _Generation(
            generation_id,
            (
                TextDelta(generation_id, 1, self.response),
                GenerationCompleted(generation_id, 1),
            ),
        )

    def cancel(self, generation_id: str) -> bool:
        del generation_id
        return True


@pytest.mark.asyncio
async def test_planner_uses_only_a_bounded_recent_canonical_tail() -> None:
    model = _Model(_payload(aim="answer"))
    planner = DispositionPlanner(model)
    history = tuple(
        ContextMessage("user" if index % 2 == 0 else "assistant", str(index)) for index in range(6)
    )

    candidate = await planner.plan_candidate("current", recent_context=history)

    assert candidate is not None
    request = model.requests[0]
    assert request.messages is not None
    assert len(request.messages) == MAX_DISPOSITION_CONTEXT_MESSAGES
    assert [message.text for message in request.messages] == ["3", "4", "5", "current"]
    assert all("Voice" not in block for block in request.system_prompt)
    assert all("Representative dialogue" not in block for block in request.system_prompt)
    assert DISPOSITION_PLANNER_CONTROL_GUIDANCE[-1] in request.system_prompt


@pytest.mark.asyncio
async def test_planner_policy_is_optional_and_invalid_model_output_does_not_create_behavior() -> (
    None
):
    model = _Model(_payload(aim="comfort", stance="supportive", reason_codes=["vulnerability"]))
    planner = DispositionPlanner(model)

    decision = await planner.plan_policy("I am having a difficult day.")

    assert decision is not None
    assert decision.attention is AttentionDecision.THINK
    assert decision.intervention is InterventionDecision.RESPOND
    assert decision.working_state is not None
    assert decision.working_state.focus == "respond to the user's difficulty with practical warmth"

    invalid_model = _Model(_payload(confidence=0.5))
    invalid_planner = DispositionPlanner(invalid_model)
    assert await invalid_planner.plan("hello") is None
