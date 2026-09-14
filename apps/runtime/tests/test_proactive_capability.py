"""Focused PROACTIVE-V0-R2 capability projection proofs."""

from __future__ import annotations

from datetime import UTC, datetime

from lilavel_runtime import (
    CapabilityId,
    ContextAvailability,
    ContextBuildRequest,
    ContextFrameBuilder,
    ContextPurpose,
    MindState,
    MindStateIntentionResolver,
    ProactiveCapabilityResolver,
    ProactiveCapabilityState,
    compile_context_projection,
)


class FixedClock:
    def now(self) -> datetime:
        return datetime(2026, 9, 15, 12, tzinfo=UTC)


def _request() -> ContextBuildRequest:
    return ContextBuildRequest(scope_id="scope:trusted-dm")


def _builder(state: ProactiveCapabilityState, mind: MindState | None = None) -> ContextFrameBuilder:
    return ContextFrameBuilder(
        clock=FixedClock(),
        capability_resolver=ProactiveCapabilityResolver(lambda: state),
        intention_resolver=(None if mind is None else MindStateIntentionResolver(mind)),
    )


def test_proactive_capability_is_absent_when_disabled_or_target_is_unbound() -> None:
    for state in (
        ProactiveCapabilityState(False, True, 20.0),
        ProactiveCapabilityState(True, False, 20.0),
    ):
        frame = _builder(state).build(ContextPurpose.USER_RESPONSE, _request())
        projection = compile_context_projection(frame).rendered

        assert frame.capabilities.availability is ContextAvailability.KNOWN_EMPTY
        assert "one-shot idle reconsideration" not in projection.casefold()


def test_bound_proactive_capability_is_bounded_provider_neutral_and_non_authoritative() -> None:
    frame = _builder(ProactiveCapabilityState(True, True, 20.0)).build(
        ContextPurpose.USER_RESPONSE,
        _request(),
    )
    projection = compile_context_projection(frame).rendered
    detail = projection.casefold()

    assert frame.capabilities.availability is ContextAvailability.KNOWN
    assert [item.capability for item in frame.capabilities.capabilities] == [
        CapabilityId.TEMPORAL_RECONSIDERATION
    ]
    assert "one-shot idle reconsideration is available" in detail
    assert "20 seconds" in detail
    assert "silence remains valid" in detail
    assert "subject to runtime validation" in detail
    assert "does not control destination or permission" in detail
    assert "does not grant execution authority" in detail
    assert not any(
        forbidden in detail
        for forbidden in (
            "discord",
            "channel",
            "guarantee",
            "delivery",
            "durable timer",
            "scheduler",
            "recipient",
            "restart",
            "reminder",
        )
    )


def test_internal_appraisal_receives_only_the_relevant_proactive_capability() -> None:
    frame = _builder(ProactiveCapabilityState(True, True, 20.0)).build(
        ContextPurpose.INTERNAL_APPRAISAL,
        _request(),
    )
    projection = compile_context_projection(frame).rendered

    assert frame.capabilities.availability is ContextAvailability.KNOWN
    assert "[Capabilities]" in projection
    assert "one-shot idle reconsideration is available" in projection.casefold()
    assert "discord" not in projection.casefold()


def test_capability_projection_does_not_create_or_mutate_mind_state() -> None:
    mind = MindState()
    before = mind.snapshot()
    builder = _builder(ProactiveCapabilityState(True, True, 20.0), mind)

    frame = builder.build(ContextPurpose.USER_RESPONSE, _request())

    assert frame.capabilities.availability is ContextAvailability.KNOWN
    assert mind.snapshot() == before
    assert mind.snapshot().intentions == ()
