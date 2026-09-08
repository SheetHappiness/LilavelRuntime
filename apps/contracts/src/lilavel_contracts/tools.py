"""Immutable, bounded values shared across Lilavel environment boundaries.

This package hosts value contracts only.  It owns no tool policy, authorization,
execution, settlement, provider transport, or conversation history.
"""

from __future__ import annotations

import json
import math
import re
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from types import MappingProxyType
from typing import cast

type JsonScalar = str | int | float | bool | None
type JsonValue = JsonScalar | tuple[JsonValue, ...] | Mapping[str, JsonValue]
type JsonObject = Mapping[str, JsonValue]
type ToolArgumentError = None | str

MAX_TOOL_NAME_BYTES = 128
MAX_TOOL_DESCRIPTION_BYTES = 4 * 1024
MAX_TOOL_ARGUMENT_BYTES = 16 * 1024
MAX_TOOL_RESULT_BYTES = 16 * 1024
MAX_APPLICATION_ERROR_CODE_BYTES = 128

_TOOL_NAME = re.compile(r"^[A-Za-z][A-Za-z0-9_.-]{0,127}$")


class ToolResultStatus(StrEnum):
    OK = "ok"
    DENIED = "denied"
    INVALID = "invalid"
    UNAVAILABLE = "unavailable"
    FAILED = "failed"
    TIMED_OUT = "timed_out"


class ToolEffect(StrEnum):
    NONE = "none"
    CONFIRMED = "confirmed"
    UNKNOWN = "unknown"


def _utf8_size(value: str) -> int:
    try:
        return len(value.encode("utf-8"))
    except UnicodeEncodeError as error:
        raise ValueError("text must be valid Unicode") from error


def _require_text(value: object, name: str, maximum: int) -> str:
    if not isinstance(value, str) or not value or not value.strip() or _utf8_size(value) > maximum:
        raise ValueError(f"{name} must be non-empty and bounded")
    if any(ord(character) < 0x20 or ord(character) == 0x7F for character in value):
        raise ValueError(f"{name} must not contain control characters")
    return value


def _freeze_json(value: object, name: str) -> JsonValue:
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError(f"{name} must contain finite numbers")
        return value
    if isinstance(value, Mapping):
        mapping = cast(Mapping[object, object], value)
        if not all(isinstance(key, str) and key for key in mapping):
            raise TypeError(f"{name} keys must be non-empty strings")
        return cast(
            Mapping[str, JsonValue],
            MappingProxyType(
                {key: _freeze_json(item, f"{name}.{key}") for key, item in mapping.items()}
            ),
        )
    if isinstance(value, (list, tuple)):
        return tuple(
            _freeze_json(item, name) for item in cast(list[object] | tuple[object, ...], value)
        )
    raise TypeError(f"{name} must contain only JSON-compatible values")


def _json_ready(value: JsonValue) -> object:
    if isinstance(value, Mapping):
        return {key: _json_ready(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_json_ready(item) for item in value]
    return value


def _frozen_object(value: Mapping[str, object], name: str, maximum: int) -> JsonObject:
    frozen = _freeze_json(value, name)
    if not isinstance(frozen, Mapping):
        raise TypeError(f"{name} must be an object")
    try:
        encoded = json.dumps(
            _json_ready(frozen), ensure_ascii=False, separators=(",", ":"), allow_nan=False
        )
    except (TypeError, ValueError) as error:
        raise ValueError(f"{name} is not JSON serializable") from error
    if _utf8_size(encoded) > maximum:
        raise ValueError(f"{name} exceeds its byte limit")
    return frozen


def _frozen_value(value: object, name: str, maximum: int) -> JsonValue:
    frozen = _freeze_json(value, name)
    try:
        encoded = json.dumps(
            _json_ready(frozen), ensure_ascii=False, separators=(",", ":"), allow_nan=False
        )
    except (TypeError, ValueError) as error:
        raise ValueError(f"{name} is not JSON serializable") from error
    if _utf8_size(encoded) > maximum:
        raise ValueError(f"{name} exceeds its byte limit")
    return frozen


@dataclass(frozen=True, slots=True, init=False)
class ToolSpec:
    """A registered capability description; it grants no execution authority."""

    name: str
    description: str
    input_schema: JsonObject

    def __init__(self, name: str, description: str, input_schema: Mapping[str, object]) -> None:
        if not _TOOL_NAME.fullmatch(name):
            raise ValueError("tool name must use the Lilavel tool-name wire syntax")
        _require_text(description, "description", MAX_TOOL_DESCRIPTION_BYTES)
        schema = _frozen_object(input_schema, "input_schema", MAX_TOOL_RESULT_BYTES)
        if schema.get("type") not in (None, "object"):
            raise ValueError("input_schema root must describe an object")
        object.__setattr__(self, "name", name)
        object.__setattr__(self, "description", description)
        object.__setattr__(self, "input_schema", schema)


@dataclass(frozen=True, slots=True, init=False)
class ToolCall:
    """A model-originated untrusted request. It is never canonical history."""

    call_id: str
    tool_name: str
    arguments: JsonObject | None
    argument_error: ToolArgumentError
    model_trust: str = "untrusted"

    def __init__(
        self,
        call_id: str,
        tool_name: str,
        arguments: Mapping[str, object] | None,
        argument_error: ToolArgumentError = None,
    ) -> None:
        _require_text(call_id, "call_id", MAX_TOOL_NAME_BYTES)
        if not _TOOL_NAME.fullmatch(tool_name):
            raise ValueError("tool_name must use the Lilavel tool-name wire syntax")
        if argument_error not in (None, "invalid_json", "invalid_shape"):
            raise ValueError("argument_error is invalid")
        if (arguments is None) != (argument_error is not None):
            raise ValueError("arguments and argument_error must agree")
        frozen_arguments = (
            None
            if arguments is None
            else _frozen_object(arguments, "arguments", MAX_TOOL_ARGUMENT_BYTES)
        )
        object.__setattr__(self, "call_id", call_id)
        object.__setattr__(self, "tool_name", tool_name)
        object.__setattr__(self, "arguments", frozen_arguments)
        object.__setattr__(self, "argument_error", argument_error)
        object.__setattr__(self, "model_trust", "untrusted")


@dataclass(frozen=True, slots=True, init=False)
class ToolResult:
    """A bounded application settlement value; it is never canonical history."""

    call_id: str
    status: ToolResultStatus
    reason_code: str | None
    output: JsonValue
    effect: ToolEffect

    def __init__(
        self,
        call_id: str,
        status: ToolResultStatus,
        output: object,
        *,
        reason_code: str | None = None,
        effect: ToolEffect = ToolEffect.NONE,
    ) -> None:
        _require_text(call_id, "call_id", MAX_TOOL_NAME_BYTES)
        if reason_code is not None:
            _require_text(reason_code, "reason_code", MAX_APPLICATION_ERROR_CODE_BYTES)
        object.__setattr__(self, "call_id", call_id)
        object.__setattr__(self, "status", status)
        object.__setattr__(self, "reason_code", reason_code)
        object.__setattr__(self, "output", _frozen_value(output, "output", MAX_TOOL_RESULT_BYTES))
        object.__setattr__(self, "effect", effect)
