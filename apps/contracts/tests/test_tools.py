from types import MappingProxyType

import pytest

from lilavel_contracts import (
    ToolArgumentError,
    ToolCall,
    ToolEffect,
    ToolResult,
    ToolResultStatus,
    ToolSpec,
)


def test_contract_values_are_immutable_bounded_and_provider_neutral() -> None:
    spec = ToolSpec("discord.send_message", "Send a bounded message", {"type": "object"})
    call = ToolCall("local-1", spec.name, {"body": ["hello"]})
    result = ToolResult("local-1", ToolResultStatus.OK, {"sent": True}, effect=ToolEffect.CONFIRMED)

    assert isinstance(spec.input_schema, MappingProxyType)
    assert isinstance(call.arguments, MappingProxyType)
    assert call.model_trust == "untrusted"
    assert call.argument_error is None
    assert result.effect is ToolEffect.CONFIRMED
    with pytest.raises(TypeError):
        spec.input_schema["type"] = "array"  # type: ignore[index]


@pytest.mark.parametrize("value", ["tool space", ".leading", "x" * 129])
def test_spec_rejects_names_outside_wire_syntax(value: str) -> None:
    with pytest.raises(ValueError):
        ToolSpec(value, "description", {"type": "object"})


@pytest.mark.parametrize("arguments,error", [({}, "invalid_json"), (None, None), (None, "other")])
def test_call_requires_one_valid_argument_outcome(
    arguments: dict[str, object] | None, error: ToolArgumentError
) -> None:
    with pytest.raises(ValueError):
        ToolCall("local", "fixture.tool", arguments, error)
