"""Tests for provider-neutral runtime contracts."""

from __future__ import annotations

import pytest

from lilavel_runtime import (
    DirectMessageWakePolicy,
    EventSource,
    EventTrust,
    ToolCall,
    ToolResult,
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


def test_tool_contracts_freeze_structured_values_without_executing_them() -> None:
    spec = ToolSpec("lookup", "Look up a value", {"type": "object", "required": ["key"]})
    call = ToolCall("call-1", "lookup", {"key": "value"})
    result = ToolResult("call-1", {"value": [1, 2]})

    assert spec.input_schema["required"] == ("key",)
    assert call.arguments == {"key": "value"}
    assert call.trust is EventTrust.UNTRUSTED
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
        WorldEvent("event-1", EventSource("fixture", "opaque-subject"), "direct_message")
    )
    noise = await policy.decide(
        WorldEvent("event-2", EventSource("fixture", "opaque-subject"), "ambient_message")
    )

    assert direct.wake is True
    assert direct.reason == "explicit_direct_message"
    assert noise.wake is False
    assert noise.reason == "not_direct_message"
