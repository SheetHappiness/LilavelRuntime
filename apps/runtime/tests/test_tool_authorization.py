"""P4-C deterministic application registration, policy, and execution proofs."""

from __future__ import annotations

import threading
from collections.abc import Callable

import pytest
from lilavel_contracts import ToolCall, ToolEffect, ToolResult, ToolResultStatus, ToolSpec
from lilavel_core import ToolBatchCorrelation, ToolGenerationContext

from lilavel_runtime import (
    ApplicationToolRegistry,
    DeterministicToolSessionFactory,
    DuplicateToolBinding,
    ToolAuthorization,
    ToolBinding,
    ToolExposureError,
    ToolRegistrationError,
    UnsupportedToolSchema,
)

CONTEXT = ToolGenerationContext("runtime-1", "trusted-scope", "run-1", "generation-1", 1)
ECHO_SPEC = ToolSpec(
    "test.echo",
    "Return a deterministic value.",
    {
        "type": "object",
        "properties": {
            "value": {"type": "string", "minLength": 1, "maxLength": 32},
            "mode": {"type": "string", "enum": ["plain", "upper"]},
        },
        "required": ["value"],
        "additionalProperties": False,
    },
)
EFFECT_SPEC = ToolSpec("test.effect", "Simulate a bounded effect.", {"type": "object"})
HIDDEN_SPEC = ToolSpec("internal.presentation", "Never model-exposed.", {"type": "object"})

type Executor = Callable[[ToolBatchCorrelation, ToolCall, threading.Event], ToolResult]


def _context(round: int = 1) -> ToolBatchCorrelation:
    return ToolBatchCorrelation(CONTEXT, round)


def _ok_executor(
    correlation: ToolBatchCorrelation, call: ToolCall, cancelled: threading.Event
) -> ToolResult:
    del correlation, cancelled
    return ToolResult(call.call_id, ToolResultStatus.OK, {"value": call.arguments["value"]})  # type: ignore[index]


def _factory(
    *bindings: ToolBinding,
    exposed: tuple[str, ...] = (ECHO_SPEC.name,),
    authorize: Callable[[ToolBatchCorrelation, ToolCall], bool] | None = None,
) -> DeterministicToolSessionFactory:
    return DeterministicToolSessionFactory(
        ApplicationToolRegistry(bindings),
        exposed_tool_names=exposed,
        authorize=authorize,
        executor_deadline=0.05,
        containment_deadline=0.2,
    )


def _execute(factory: DeterministicToolSessionFactory, call: ToolCall) -> ToolResult:
    session = factory.create(CONTEXT)
    result = session.execute_batch(_context(), (call,), threading.Event())
    assert len(result) == 1
    return result[0]


def test_valid_registration_and_immutable_per_run_exposure_snapshot() -> None:
    registry = ApplicationToolRegistry([ToolBinding(ECHO_SPEC, _ok_executor)])
    factory = DeterministicToolSessionFactory(registry, exposed_tool_names=(ECHO_SPEC.name,))

    first = factory.create(CONTEXT)
    registry.register(ToolBinding(EFFECT_SPEC, _ok_executor))
    second = factory.create(ToolGenerationContext("runtime-1", "trusted-scope", "run-2", "g2", 2))

    assert [spec.name for spec in first.specs] == [ECHO_SPEC.name]
    assert [spec.name for spec in second.specs] == [ECHO_SPEC.name]
    assert factory.registry.tools() == (ECHO_SPEC, EFFECT_SPEC)


def test_duplicate_name_is_rejected_at_trusted_registration_boundary() -> None:
    registry = ApplicationToolRegistry([ToolBinding(ECHO_SPEC, _ok_executor)])
    with pytest.raises(DuplicateToolBinding):
        registry.register(ToolBinding(ECHO_SPEC, _ok_executor))


@pytest.mark.parametrize(
    "schema",
    [
        {"type": "object", "additionalItems": True},
        {"type": "object", "properties": {"value": {"type": "string", "pattern": "x"}}},
        {"type": "object", "properties": {"value": {"type": "array"}}},
        {"type": "object", "required": ["missing"]},
    ],
)
def test_unsupported_schema_is_rejected_before_exposure(schema: dict[str, object]) -> None:
    with pytest.raises(UnsupportedToolSchema):
        ApplicationToolRegistry(
            [ToolBinding(ToolSpec("test.invalid", "invalid", schema), _ok_executor)]
        )


def test_internal_presentation_binding_cannot_be_model_exposed() -> None:
    registry = ApplicationToolRegistry(
        [ToolBinding(HIDDEN_SPEC, _ok_executor, model_exposable=False)]
    )
    with pytest.raises(ToolExposureError):
        registry.snapshot((HIDDEN_SPEC.name,))

    with pytest.raises(ToolRegistrationError):
        ApplicationToolRegistry(
            [
                ToolBinding(
                    ToolSpec("conversation.presentation.complete", "Internal", {"type": "object"}),
                    _ok_executor,
                )
            ]
        )


def test_unknown_and_known_but_unexposed_tools_never_dispatch() -> None:
    calls: list[str] = []

    def record(
        correlation: ToolBatchCorrelation, call: ToolCall, cancelled: threading.Event
    ) -> ToolResult:
        del correlation, cancelled
        calls.append(call.tool_name)
        return ToolResult(call.call_id, ToolResultStatus.OK, None)

    factory = _factory(
        ToolBinding(ECHO_SPEC, record),
        ToolBinding(EFFECT_SPEC, record),
    )
    hidden = _execute(factory, ToolCall("hidden", EFFECT_SPEC.name, {}))
    unknown = _execute(factory, ToolCall("unknown", "test.unknown", {}))

    assert hidden.status is ToolResultStatus.UNAVAILABLE
    assert unknown.status is ToolResultStatus.UNAVAILABLE
    assert calls == []


@pytest.mark.parametrize(
    ("arguments", "reason"),
    [
        ({}, "missing_required"),
        ({"value": 1}, "invalid_type"),
        ({"value": "ok", "mode": "bad"}, "enum_violation"),
        ({"value": "ok", "extra": True}, "unexpected_property"),
        ({"value": ""}, "string_too_short"),
    ],
)
def test_strict_non_coercive_validation_returns_typed_invalid_result(
    arguments: dict[str, object], reason: str
) -> None:
    executed: list[str] = []

    def should_not_execute(
        correlation: ToolBatchCorrelation, call: ToolCall, cancelled: threading.Event
    ) -> ToolResult:
        del correlation, cancelled
        executed.append(call.call_id)
        return ToolResult(call.call_id, ToolResultStatus.OK, None)

    result = _execute(
        _factory(ToolBinding(ECHO_SPEC, should_not_execute)),
        ToolCall("invalid", ECHO_SPEC.name, arguments),
    )

    assert result.status is ToolResultStatus.INVALID
    assert result.reason_code == reason
    assert result.effect is ToolEffect.NONE
    assert executed == []


def test_allowed_authorization_uses_trusted_scope_not_model_supplied_scope() -> None:
    observed: list[str] = []

    def authorize(correlation: ToolBatchCorrelation, call: ToolCall) -> ToolAuthorization:
        assert call.arguments is not None
        assert call.arguments.get("scope_id") == "attacker-supplied"
        return (
            ToolAuthorization.ALLOWED
            if correlation.context.scope_id == "trusted-scope"
            else ToolAuthorization.DENIED
        )

    def executor(
        correlation: ToolBatchCorrelation, call: ToolCall, cancelled: threading.Event
    ) -> ToolResult:
        del cancelled, call
        observed.append(correlation.context.scope_id)
        return ToolResult("call", ToolResultStatus.OK, {"trusted": True})

    spec = ToolSpec("test.scope", "Trusted scope fixture.", {"type": "object"})
    result = _execute(
        _factory(ToolBinding(spec, executor, authorize=authorize), exposed=(spec.name,)),
        ToolCall("call", spec.name, {"scope_id": "attacker-supplied"}),
    )

    assert result.status is ToolResultStatus.OK
    assert observed == ["trusted-scope"]


def test_denied_policy_is_non_effect_and_unavailable_liveness_is_rechecked() -> None:
    executed: list[str] = []
    availability_checks = 0

    def unavailable_after_snapshot(correlation: ToolBatchCorrelation) -> bool:
        nonlocal availability_checks
        del correlation
        availability_checks += 1
        return availability_checks == 1

    def executor(
        correlation: ToolBatchCorrelation, call: ToolCall, cancelled: threading.Event
    ) -> ToolResult:
        del correlation, cancelled
        executed.append(call.call_id)
        return ToolResult(call.call_id, ToolResultStatus.OK, None)

    dynamic = ToolSpec("test.dynamic", "Dynamic availability fixture.", {"type": "object"})
    unavailable = _execute(
        _factory(
            ToolBinding(dynamic, executor, is_available=unavailable_after_snapshot),
            exposed=(dynamic.name,),
        ),
        ToolCall("unavailable", dynamic.name, {}),
    )
    denied = _execute(
        _factory(
            ToolBinding(
                ECHO_SPEC,
                executor,
                authorize=lambda correlation, call: ToolAuthorization.DENIED,
            ),
            exposed=(ECHO_SPEC.name,),
        ),
        ToolCall("denied", ECHO_SPEC.name, {"value": "ok"}),
    )

    assert unavailable.status is ToolResultStatus.UNAVAILABLE
    assert unavailable.effect is ToolEffect.NONE
    assert denied.status is ToolResultStatus.DENIED
    assert denied.effect is ToolEffect.NONE
    assert executed == []


def test_pure_and_effect_like_fixtures_use_bound_executor_and_effect_certainty() -> None:
    pure = _execute(
        _factory(ToolBinding(ECHO_SPEC, _ok_executor)),
        ToolCall("pure", ECHO_SPEC.name, {"value": "hello"}),
    )

    effects: list[str] = []

    def effect_executor(
        correlation: ToolBatchCorrelation, call: ToolCall, cancelled: threading.Event
    ) -> ToolResult:
        del cancelled
        effects.append(correlation.context.scope_id)
        return ToolResult(
            call.call_id,
            ToolResultStatus.OK,
            {"committed": True},
            effect=ToolEffect.CONFIRMED,
        )

    effect = _execute(
        _factory(ToolBinding(EFFECT_SPEC, effect_executor), exposed=(EFFECT_SPEC.name,)),
        ToolCall("effect", EFFECT_SPEC.name, {}),
    )

    assert pure.status is ToolResultStatus.OK
    assert pure.effect is ToolEffect.NONE
    assert effect.status is ToolResultStatus.OK
    assert effect.effect is ToolEffect.CONFIRMED
    assert effects == ["trusted-scope"]


def test_runtime_evidence_is_bounded_metadata_without_raw_arguments_or_results() -> None:
    raw = "secret-argument-value"
    spec = ToolSpec("test.secret", "No raw evidence fixture.", {"type": "object"})

    def executor(
        correlation: ToolBatchCorrelation, call: ToolCall, cancelled: threading.Event
    ) -> ToolResult:
        del correlation, cancelled
        return ToolResult(call.call_id, ToolResultStatus.OK, {"secret": raw})

    factory = _factory(ToolBinding(spec, executor), exposed=(spec.name,))
    session = factory.create(CONTEXT)
    session.execute_batch(
        _context(),
        (ToolCall("secret-call", spec.name, {"value": raw}),),
        threading.Event(),
    )

    evidence = repr(session.evidence())
    assert raw not in evidence
    assert "secret-call" in evidence
