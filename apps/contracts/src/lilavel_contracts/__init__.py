"""Dependency-safe provider-neutral tool value contracts."""

from .tools import (
    MAX_APPLICATION_ERROR_CODE_BYTES,
    MAX_TOOL_ARGUMENT_BYTES,
    MAX_TOOL_DESCRIPTION_BYTES,
    MAX_TOOL_NAME_BYTES,
    MAX_TOOL_RESULT_BYTES,
    JsonObject,
    JsonValue,
    ToolArgumentError,
    ToolCall,
    ToolEffect,
    ToolResult,
    ToolResultStatus,
    ToolSpec,
)

__all__ = [
    "MAX_APPLICATION_ERROR_CODE_BYTES",
    "MAX_TOOL_ARGUMENT_BYTES",
    "MAX_TOOL_DESCRIPTION_BYTES",
    "MAX_TOOL_NAME_BYTES",
    "MAX_TOOL_RESULT_BYTES",
    "JsonObject",
    "JsonValue",
    "ToolArgumentError",
    "ToolCall",
    "ToolEffect",
    "ToolResult",
    "ToolResultStatus",
    "ToolSpec",
]
