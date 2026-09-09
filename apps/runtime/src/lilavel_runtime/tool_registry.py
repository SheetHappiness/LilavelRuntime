"""Trusted application-owned tool bindings and bounded argument validation."""

from __future__ import annotations

import math
import threading
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from types import MappingProxyType
from typing import cast

from lilavel_contracts import JsonValue, ToolCall, ToolResult, ToolSpec
from lilavel_core.tool_runtime import ToolBatchCorrelation

MAX_EXPOSED_TOOLS = 16
MAX_EXPOSED_TOOL_BYTES = 64 * 1024
_INTERNAL_PRESENTATION_PREFIX = "conversation.presentation."

type ToolExecutor = Callable[[ToolBatchCorrelation, ToolCall, threading.Event], ToolResult]
type ToolAuthorizationHook = Callable[[ToolBatchCorrelation, ToolCall], "ToolAuthorization"]
type ToolAvailabilityHook = Callable[[ToolBatchCorrelation], bool]


class ToolRegistrationError(ValueError):
    """A trusted application binding cannot be registered."""


class DuplicateToolBinding(ToolRegistrationError):
    """A canonical application tool name is already registered."""


class UnsupportedToolSchema(ToolRegistrationError):
    """A schema uses a feature outside the deliberately bounded subset."""


class ToolExposureError(ToolRegistrationError):
    """A trusted composition requested an invalid model exposure snapshot."""


class ToolAuthorization(StrEnum):
    ALLOWED = "allowed"
    DENIED = "denied"
    UNAVAILABLE = "unavailable"


@dataclass(frozen=True, slots=True)
class ToolBinding:
    """One trusted canonical tool definition and its application executor."""

    spec: ToolSpec
    executor: ToolExecutor
    authorize: ToolAuthorizationHook | None = None
    is_available: ToolAvailabilityHook | None = None
    model_exposable: bool = True

    def __post_init__(self) -> None:
        if not callable(self.executor):
            raise TypeError("tool executor must be callable")
        if type(self.model_exposable) is not bool:
            raise TypeError("model_exposable must be a bool")


@dataclass(frozen=True, slots=True)
class ToolExposureSnapshot:
    """Immutable per-generation membership and binding snapshot."""

    _bindings: Mapping[str, ToolBinding]
    specs: tuple[ToolSpec, ...]

    @property
    def names(self) -> tuple[str, ...]:
        return tuple(self._bindings)

    def binding_for(self, canonical_name: str) -> ToolBinding | None:
        return self._bindings.get(canonical_name)


class ApplicationToolRegistry:
    """Trusted registry used only by explicit application composition."""

    def __init__(self, bindings: Sequence[ToolBinding] = ()) -> None:
        self._bindings: dict[str, ToolBinding] = {}
        for binding in bindings:
            self.register(binding)

    def register(self, binding: ToolBinding) -> None:
        name = binding.spec.name
        if name in self._bindings:
            raise DuplicateToolBinding(name)
        if name.startswith(_INTERNAL_PRESENTATION_PREFIX) and binding.model_exposable:
            raise ToolRegistrationError("internal presentation action cannot be model-exposed")
        _validate_schema(binding.spec.input_schema, root=True)
        self._bindings[name] = binding

    def bindings(self) -> tuple[ToolBinding, ...]:
        return tuple(self._bindings.values())

    def tools(self) -> tuple[ToolSpec, ...]:
        return tuple(binding.spec for binding in self._bindings.values())

    def snapshot(self, exposed_names: Sequence[str] = ()) -> ToolExposureSnapshot:
        names = tuple(exposed_names)
        if len(names) > MAX_EXPOSED_TOOLS:
            raise ToolExposureError("exposed tool count exceeds bound")
        if len(set(names)) != len(names):
            raise ToolExposureError("exposed tool names must be unique")

        selected: dict[str, ToolBinding] = {}
        total_bytes = 0
        for name in names:
            binding = self._bindings.get(name)
            if binding is None:
                raise ToolExposureError("exposed tool is not registered")
            if not binding.model_exposable:
                raise ToolExposureError("internal tool cannot be model-exposed")
            selected[name] = binding
            total_bytes += _tool_wire_size(binding.spec)
        if total_bytes > MAX_EXPOSED_TOOL_BYTES:
            raise ToolExposureError("exposed tool descriptions exceed bound")
        frozen = cast(Mapping[str, ToolBinding], MappingProxyType(selected))
        return ToolExposureSnapshot(frozen, tuple(binding.spec for binding in selected.values()))


def validate_tool_arguments(
    spec: ToolSpec, arguments: Mapping[str, JsonValue] | None
) -> str | None:
    """Return one bounded reason code, or ``None`` when arguments are valid."""

    if arguments is None:
        return "invalid_shape"
    return _validate_value(arguments, spec.input_schema, root=True)


_SCHEMA_KEYS = frozenset(
    {
        "type",
        "properties",
        "required",
        "enum",
        "additionalProperties",
        "minLength",
        "maxLength",
        "minimum",
        "maximum",
    }
)
_SCALAR_TYPES = frozenset({"string", "integer", "number", "boolean", "null"})
_SUPPORTED_TYPES = _SCALAR_TYPES | {"object"}


def _validate_schema(schema: Mapping[str, JsonValue], *, root: bool = False) -> None:
    unsupported = set(schema) - _SCHEMA_KEYS
    if unsupported:
        raise UnsupportedToolSchema("schema contains unsupported keywords")

    schema_type = schema.get("type")
    if schema_type is not None and schema_type not in _SUPPORTED_TYPES:
        raise UnsupportedToolSchema("schema type is unsupported")
    if root and schema_type not in (None, "object"):
        raise UnsupportedToolSchema("tool root schema must be an object")

    enum = schema.get("enum")
    if enum is not None:
        if not isinstance(enum, (tuple, list)) or not enum:
            raise UnsupportedToolSchema("enum must be a non-empty sequence")
        if any(not _is_scalar(item) for item in enum):
            raise UnsupportedToolSchema("enum values must be primitive scalars")

    properties = schema.get("properties")
    required = schema.get("required")
    additional = schema.get("additionalProperties")
    if properties is not None:
        if schema_type not in (None, "object") or not isinstance(properties, Mapping):
            raise UnsupportedToolSchema("properties require an object schema")
        for name, child in properties.items():
            if not name or any(ord(c) < 0x20 for c in name):
                raise UnsupportedToolSchema("property names must be bounded text")
            if not isinstance(child, Mapping):
                raise UnsupportedToolSchema("property schemas must be objects")
            _validate_schema(cast(Mapping[str, JsonValue], child))
    if required is not None and (
        schema_type not in (None, "object")
        or not isinstance(required, (tuple, list))
        or len(set(required)) != len(required)
        or any(not isinstance(name, str) for name in required)
        or properties is None
        or any(name not in properties for name in required)
    ):
        raise UnsupportedToolSchema("required must name object properties")
    if additional is not None and (
        schema_type not in (None, "object") or type(additional) is not bool
    ):
        raise UnsupportedToolSchema("additionalProperties must be a boolean")

    for key in ("minLength", "maxLength"):
        value = schema.get(key)
        integer_value = _integer_value(value)
        if value is not None and (
            schema_type != "string" or integer_value is None or integer_value < 0
        ):
            raise UnsupportedToolSchema("string bounds are invalid")
    minimum = schema.get("minimum")
    maximum = schema.get("maximum")
    minimum_number = _number_value(minimum)
    maximum_number = _number_value(maximum)
    for value in (minimum, maximum):
        if value is not None and (
            schema_type not in {"integer", "number"}
            or _number_value(value) is None
            or not math.isfinite(float(cast(int | float, value)))
        ):
            raise UnsupportedToolSchema("numeric bounds are invalid")
    if (
        minimum_number is not None
        and maximum_number is not None
        and minimum_number > maximum_number
    ):
        raise UnsupportedToolSchema("numeric bounds are inverted")
    min_length = schema.get("minLength")
    max_length = schema.get("maxLength")
    min_length_value = _integer_value(min_length)
    max_length_value = _integer_value(max_length)
    if (
        min_length_value is not None
        and max_length_value is not None
        and min_length_value > max_length_value
    ):
        raise UnsupportedToolSchema("string bounds are inverted")

    if schema_type in _SCALAR_TYPES and any(
        key in schema for key in ("properties", "required", "additionalProperties")
    ):
        raise UnsupportedToolSchema("object keywords require an object schema")


def _validate_value(
    value: JsonValue | Mapping[str, JsonValue],
    schema: Mapping[str, JsonValue],
    *,
    root: bool = False,
) -> str | None:
    schema_type = schema.get("type")
    if root:
        schema_type = "object" if schema_type is None else schema_type
    if schema_type == "object":
        if not isinstance(value, Mapping):
            return "invalid_type"
        properties = schema.get("properties")
        if isinstance(properties, Mapping):
            required = schema.get("required", ())
            if isinstance(required, (tuple, list)) and any(name not in value for name in required):
                return "missing_required"
            if schema.get("additionalProperties") is False and any(
                name not in properties for name in value
            ):
                return "unexpected_property"
            for name, child in properties.items():
                if name in value and isinstance(child, Mapping):
                    reason = _validate_value(value[name], cast(Mapping[str, JsonValue], child))
                    if reason is not None:
                        return reason
    elif schema_type is not None and not _matches_type(value, cast(str, schema_type)):
        return "invalid_type"

    enum = schema.get("enum")
    if isinstance(enum, (tuple, list)) and value not in enum:
        return "enum_violation"
    if isinstance(value, str):
        minimum = _integer_value(schema.get("minLength"))
        maximum = _integer_value(schema.get("maxLength"))
        if minimum is not None and len(value) < minimum:
            return "string_too_short"
        if maximum is not None and len(value) > maximum:
            return "string_too_long"
    if type(value) in {int, float}:
        numeric_value = cast(int | float, value)
        minimum = _number_value(schema.get("minimum"))
        maximum = _number_value(schema.get("maximum"))
        if minimum is not None and numeric_value < minimum:
            return "number_below_minimum"
        if maximum is not None and numeric_value > maximum:
            return "number_above_maximum"
    return None


def _matches_type(value: JsonValue | Mapping[str, JsonValue], schema_type: str) -> bool:
    if schema_type == "string":
        return isinstance(value, str)
    if schema_type == "integer":
        return type(value) is int
    if schema_type == "number":
        return type(value) in {int, float}
    if schema_type == "boolean":
        return type(value) is bool
    if schema_type == "null":
        return value is None
    return False


def _is_scalar(value: object) -> bool:
    return value is None or isinstance(value, (str, bool)) or type(value) in {int, float}


def _integer_value(value: JsonValue | None) -> int | None:
    return value if type(value) is int else None


def _number_value(value: JsonValue | None) -> int | float | None:
    return cast(int | float, value) if type(value) in {int, float} else None


def _tool_wire_size(spec: ToolSpec) -> int:
    """Bound exposure without importing provider serialization helpers."""

    return (
        len(spec.name.encode("utf-8"))
        + len(spec.description.encode("utf-8"))
        + _json_size(spec.input_schema)
    )


def _json_size(value: object) -> int:
    if value is None or isinstance(value, (str, bool, int, float)):
        return len(str(value).encode("utf-8"))
    if isinstance(value, Mapping):
        mapping = cast(Mapping[object, object], value)
        return sum(
            len(str(key).encode("utf-8")) + _json_size(item) for key, item in mapping.items()
        )
    if isinstance(value, (tuple, list)):
        sequence = cast(tuple[object, ...] | list[object], value)
        return sum(_json_size(item) for item in sequence)
    return 0


__all__ = [
    "ApplicationToolRegistry",
    "DuplicateToolBinding",
    "MAX_EXPOSED_TOOL_BYTES",
    "MAX_EXPOSED_TOOLS",
    "ToolAuthorization",
    "ToolAvailabilityHook",
    "ToolBinding",
    "ToolExecutor",
    "ToolExposureError",
    "ToolExposureSnapshot",
    "ToolRegistrationError",
    "ToolAuthorizationHook",
    "UnsupportedToolSchema",
    "validate_tool_arguments",
]
