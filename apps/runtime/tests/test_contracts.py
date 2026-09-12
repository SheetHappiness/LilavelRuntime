"""Tests for provider-neutral runtime contracts."""

from __future__ import annotations

import pytest

from lilavel_runtime import (
    DirectMessageWakePolicy,
    EventSource,
    EventTrust,
    Observation,
    ObservationReceipt,
    ObservationReceiptStatus,
    ObservationWindow,
    ToolCall,
    ToolEffect,
    ToolResult,
    ToolResultStatus,
    ToolSpec,
    WorldEvent,
)


def test_world_event_is_untrusted_and_deeply_immutable_by_default() -> None:
    original = {"message": {"parts": ["hello"]}}
    event = WorldEvent("event-1", EventSource("fixture"), "message", original)

    original["message"] = {"parts": ["changed"]}

    assert event.trust is EventTrust.UNTRUSTED
    assert event.payload["message"] == {"parts": ("hello",)}
    with pytest.raises(TypeError):
        event.payload["new"] = "value"  # type: ignore[index]


def test_admitted_observation_is_distinct_from_its_world_event_and_receipt() -> None:
    event = WorldEvent("event-1", EventSource("fixture"), "message", {"text": "hello"})
    observation = Observation("observation-1", 1, event)
    receipt = ObservationReceipt("observation-1", "event-1", 1)

    assert observation.event is event
    assert observation.world_event is event
    assert observation.event.trust is EventTrust.UNTRUSTED
    assert receipt.status is ObservationReceiptStatus.ADMITTED
    assert receipt.event_id == observation.event.event_id


def test_observation_window_evicts_oldest_transient_entries() -> None:
    window = ObservationWindow(1)
    first = Observation("observation-1", 1, WorldEvent("event-1", EventSource("fixture"), "one"))
    second = Observation("observation-2", 2, WorldEvent("event-2", EventSource("fixture"), "two"))

    window.admit(first)
    window.admit(second)

    assert window.snapshot() == (second,)
    assert window.get(first.observation_id) is None


def test_tool_contracts_freeze_structured_values_without_executing_them() -> None:
    spec = ToolSpec("lookup", "Look up a value", {"type": "object", "required": ["key"]})
    call = ToolCall("call-1", "lookup", {"key": "value"})
    result = ToolResult("call-1", ToolResultStatus.OK, {"value": [1, 2]}, effect=ToolEffect.NONE)

    assert spec.input_schema["required"] == ("key",)
    assert call.arguments == {"key": "value"}
    assert call.model_trust == "untrusted"
    assert result.output == {"value": (1, 2)}


@pytest.mark.parametrize("value", ["", "   "])
def test_contract_identifiers_must_be_non_empty(value: str) -> None:
    with pytest.raises(ValueError):
        EventSource(value)


def test_contract_payload_rejects_non_json_objects() -> None:
    with pytest.raises(TypeError):
        WorldEvent("event-1", EventSource("fixture"), "bad", {"value": object()})


@pytest.mark.asyncio
async def test_direct_message_wake_policy_is_deterministic_and_non_llm() -> None:
    policy = DirectMessageWakePolicy()

    direct = await policy.decide(
        Observation(
            "observation-1",
            1,
            WorldEvent("event-1", EventSource("fixture", "opaque-subject"), "direct_message"),
        )
    )
    noise = await policy.decide(
        Observation(
            "observation-2",
            2,
            WorldEvent("event-2", EventSource("fixture", "opaque-subject"), "ambient_message"),
        )
    )

    assert direct.wake is True
    assert direct.reason == "explicit_direct_message"
    assert noise.wake is False
    assert noise.reason == "not_direct_message"
