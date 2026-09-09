"""Strict opt-in V3 JSONL host protocol with bounded tool continuation frames."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Final, cast

from lilavel_contracts import ToolCall, ToolResult, ToolSpec

from .sidecar_protocol import (
    MAX_DELTA_BYTES,
    MAX_FRAME_BYTES,
    MAX_ID_BYTES,
    CancelCommand,
    GenerationAccepted,
    GenerationCancelled,
    GenerationCompleted,
    GenerationEvent,
    GenerationFailed,
    HealthCommand,
    HealthEvent,
    ModelRequest,
    ProtocolError,
    ProtocolErrorCode,
    ReadyEvent,
    ShutdownCommand,
    ShutdownEvent,
    TextDelta,
)

PROTOCOL_VERSION_V3: Final = 3


@dataclass(frozen=True, slots=True)
class GenerateToolsCommand:
    generation_id: str
    epoch: int
    request: ModelRequest
    tools: tuple[ToolSpec, ...]


@dataclass(frozen=True, slots=True)
class ToolResultsCommand:
    generation_id: str
    epoch: int
    round: int
    results: tuple[ToolResult, ...]


@dataclass(frozen=True, slots=True)
class ToolCallsEvent:
    generation_id: str
    epoch: int
    round: int
    calls: tuple[ToolCall, ...]


type V3Command = (
    GenerateToolsCommand | ToolResultsCommand | CancelCommand | HealthCommand | ShutdownCommand
)
type V3Event = GenerationEvent | ToolCallsEvent


def encode_v3_command(command: V3Command) -> bytes:
    if isinstance(command, GenerateToolsCommand):
        value: dict[str, object] = {
            "protocol_version": PROTOCOL_VERSION_V3,
            "type": "generate",
            "generation_id": command.generation_id,
            "epoch": command.epoch,
        }
        request = command.request
        if request.messages is not None:
            value["messages"] = [
                {"role": message.role, "text": message.text} for message in request.messages
            ]
        else:
            value["prompt"] = request.prompt
        if request.system_prompt:
            value["system_prompt"] = list(request.system_prompt)
        if command.tools:
            value["tools"] = [
                {
                    "name": tool.name,
                    "description": tool.description,
                    "input_schema": _json_ready(tool.input_schema),
                }
                for tool in command.tools
            ]
        return _frame(value)
    if isinstance(command, ToolResultsCommand):
        return _frame(
            {
                "protocol_version": PROTOCOL_VERSION_V3,
                "type": "tool_results",
                "generation_id": command.generation_id,
                "epoch": command.epoch,
                "round": command.round,
                "results": [
                    {
                        "call_id": result.call_id,
                        "status": result.status.value,
                        "reason_code": result.reason_code,
                        "output": _json_ready(result.output),
                        "effect": result.effect.value,
                    }
                    for result in command.results
                ],
            }
        )
    if isinstance(command, CancelCommand):
        return _frame(_identity("cancel", command.generation_id, command.epoch))
    if isinstance(command, HealthCommand):
        return _frame({"protocol_version": PROTOCOL_VERSION_V3, "type": "health"})
    return _frame({"protocol_version": PROTOCOL_VERSION_V3, "type": "shutdown"})


def parse_v3_event(payload: bytes | str) -> V3Event:
    value = _object(payload)
    if value.get("protocol_version") != PROTOCOL_VERSION_V3:
        raise ProtocolError("unsupported_version")
    event_type = value.get("type")
    if event_type == "ready":
        _exact(value, "protocol_version", "type", "provider", "model_id", "api")
        return ReadyEvent(
            _bounded_text(value.get("provider"), MAX_ID_BYTES),
            _bounded_text(value.get("model_id"), MAX_ID_BYTES),
            _bounded_text(value.get("api"), MAX_ID_BYTES),
        )
    if event_type == "health":
        _exact(value, "protocol_version", "type", "state")
        state = value.get("state")
        if state not in {"starting", "ready", "busy", "failed", "closed"}:
            raise ProtocolError("invalid_field")
        return HealthEvent(cast(str, state))  # type: ignore[arg-type]
    if event_type == "shutdown":
        _exact(value, "protocol_version", "type")
        return ShutdownEvent()
    if event_type == "failed" and "generation_id" not in value:
        _exact(value, "protocol_version", "type", "code")
        return GenerationFailed(None, None, _error_code(value.get("code")))

    generation_id, epoch = _parse_identity(value)
    if event_type == "accepted":
        _exact(value, "protocol_version", "type", "generation_id", "epoch")
        return GenerationAccepted(generation_id, epoch)
    if event_type == "text_delta":
        _exact(value, "protocol_version", "type", "generation_id", "epoch", "delta")
        return TextDelta(
            generation_id,
            epoch,
            _bounded_text(value.get("delta"), MAX_DELTA_BYTES, allow_empty=True),
        )
    if event_type == "completed":
        _exact(value, "protocol_version", "type", "generation_id", "epoch")
        return GenerationCompleted(generation_id, epoch)
    if event_type == "cancelled":
        _exact(value, "protocol_version", "type", "generation_id", "epoch")
        return GenerationCancelled(generation_id, epoch)
    if event_type == "failed":
        _exact(value, "protocol_version", "type", "generation_id", "epoch", "code")
        return GenerationFailed(generation_id, epoch, _error_code(value.get("code")))
    if event_type == "tool_calls":
        _exact(value, "protocol_version", "type", "generation_id", "epoch", "round", "calls")
        round_value = value.get("round")
        calls_value = value.get("calls")
        if type(round_value) is not int or round_value < 1 or not isinstance(calls_value, list):
            raise ProtocolError("invalid_field")
        calls: list[ToolCall] = []
        for raw_value in cast(list[object], calls_value):
            if not isinstance(raw_value, dict):
                raise ProtocolError("invalid_field")
            raw = cast(dict[str, object], raw_value)
            _exact(raw, "local_call_id", "tool_name", "arguments", "argument_error")
            arguments = raw.get("arguments")
            if arguments is not None and not isinstance(arguments, dict):
                raise ProtocolError("invalid_field")
            try:
                calls.append(
                    ToolCall(
                        _id_text(raw.get("local_call_id")),
                        _id_text(raw.get("tool_name")),
                        cast(Mapping[str, object] | None, arguments),
                        cast(str | None, raw.get("argument_error")),
                    )
                )
            except (TypeError, ValueError) as error:
                raise ProtocolError("invalid_field") from error
        if not calls:
            raise ProtocolError("invalid_field")
        return ToolCallsEvent(generation_id, epoch, round_value, tuple(calls))
    raise ProtocolError("invalid_field")


def _identity(event_type: str, generation_id: str, epoch: int) -> dict[str, object]:
    return {
        "protocol_version": PROTOCOL_VERSION_V3,
        "type": event_type,
        "generation_id": generation_id,
        "epoch": epoch,
    }


def _parse_identity(value: dict[str, object]) -> tuple[str, int]:
    generation_id = _id_text(value.get("generation_id"))
    epoch = value.get("epoch")
    if type(epoch) is not int or epoch < 0 or epoch > 2**53 - 1:
        raise ProtocolError("invalid_field")
    return generation_id, epoch


def _object(payload: bytes | str) -> dict[str, object]:
    try:
        raw = payload.decode("utf-8") if isinstance(payload, bytes) else payload
        value = json.loads(raw, object_pairs_hook=_pairs)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ProtocolError("malformed") from error
    if not isinstance(value, dict):
        raise ProtocolError("invalid_field")
    return cast(dict[str, object], value)


def _pairs(items: list[tuple[str, object]]) -> dict[str, object]:
    value: dict[str, object] = {}
    for key, item in items:
        if key in value:
            raise ProtocolError("duplicate_field")
        value[key] = item
    return value


def _exact(value: dict[str, object], *keys: str) -> None:
    if set(value) != set(keys):
        raise ProtocolError("unknown_field")


def _id_text(value: object) -> str:
    text = _bounded_text(value, MAX_ID_BYTES)
    if any(ord(character) < 0x20 or ord(character) == 0x7F for character in text):
        raise ProtocolError("invalid_field")
    return text


def _bounded_text(value: object, maximum: int, *, allow_empty: bool = False) -> str:
    if (
        not isinstance(value, str)
        or (not allow_empty and not value)
        or len(value.encode("utf-8")) > maximum
    ):
        raise ProtocolError("invalid_field")
    return value


def _error_code(value: object) -> ProtocolErrorCode:
    if value not in {
        "busy",
        "not_ready",
        "closed",
        "not_active",
        "startup_error",
        "provider_error",
        "unsupported_output",
        "protocol_error",
        "output_limit",
        "cleanup_timeout",
        "cleanup_error",
        "shutdown_timeout",
        "cancellation_timeout",
        "cancelled",
    }:
        raise ProtocolError("invalid_field")
    return cast(ProtocolErrorCode, value)


def _json_ready(value: object) -> object:
    if isinstance(value, Mapping):
        encoded: dict[str, object] = {}
        for key, item in cast(Mapping[object, object], value).items():
            if not isinstance(key, str):
                raise TypeError("JSON object keys must be strings")
            encoded[key] = _json_ready(item)
        return encoded
    if isinstance(value, tuple):
        return [_json_ready(item) for item in cast(tuple[object, ...], value)]
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [_json_ready(item) for item in cast(Sequence[object], value)]
    return value


def _frame(value: dict[str, object]) -> bytes:
    try:
        payload = json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
    except (TypeError, ValueError) as error:
        raise ProtocolError("invalid_field") from error
    encoded = payload.encode("utf-8") + b"\n"
    if len(encoded) > MAX_FRAME_BYTES:
        raise ProtocolError("frame_too_large")
    return encoded
