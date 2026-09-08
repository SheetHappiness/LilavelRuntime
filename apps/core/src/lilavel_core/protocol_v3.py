"""Strict, transport-only V3 tool frame parser.

This parser is deliberately separate from the active V2 host until a later
activation slice wires the complete Core tool continuation loop.
"""

from __future__ import annotations

import json
from typing import cast


class ProtocolV3Error(ValueError):
    pass


def _object(payload: str) -> dict[str, object]:
    try:
        value = json.loads(payload, object_pairs_hook=_pairs)
    except (TypeError, ValueError) as error:
        raise ProtocolV3Error("malformed") from error
    if not isinstance(value, dict):
        raise ProtocolV3Error("invalid_frame")
    return cast(dict[str, object], value)


def _pairs(items: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in items:
        if key in result:
            raise ProtocolV3Error("duplicate_field")
        result[key] = value
    return result


def _exact(value: dict[str, object], *keys: str) -> None:
    if set(value) != set(keys):
        raise ProtocolV3Error("unknown_field")


def _identity(value: dict[str, object]) -> None:
    if value.get("protocol_version") != 3 or not isinstance(value.get("generation_id"), str):
        raise ProtocolV3Error("invalid_frame")
    if (
        not value["generation_id"]
        or type(value.get("epoch")) is not int
        or cast(int, value["epoch"]) < 0
    ):
        raise ProtocolV3Error("invalid_frame")


def parse_v3_command(payload: str) -> dict[str, object]:
    value = _object(payload)
    _identity(value)
    if value.get("type") == "generate":
        _exact(
            value,
            "protocol_version",
            "type",
            "generation_id",
            "epoch",
            "prompt",
            *("tools",) if "tools" in value else (),
        )
        if not isinstance(value.get("prompt"), str) or (
            "tools" in value and not _tools(value["tools"])
        ):
            raise ProtocolV3Error("invalid_frame")
        return value
    if value.get("type") == "tool_results":
        _exact(value, "protocol_version", "type", "generation_id", "epoch", "round", "results")
        if (
            type(value.get("round")) is not int
            or cast(int, value["round"]) < 1
            or not isinstance(value.get("results"), list)
        ):
            raise ProtocolV3Error("invalid_frame")
        for result in cast(list[object], value["results"]):
            if not isinstance(result, dict):
                raise ProtocolV3Error("invalid_frame")
            _exact(
                cast(dict[str, object], result),
                "call_id",
                "status",
                "reason_code",
                "output",
                "effect",
            )
        return value
    raise ProtocolV3Error("invalid_frame")


def _tools(value: object) -> bool:
    if not isinstance(value, list):
        return False
    for tool in cast(list[object], value):
        if not isinstance(tool, dict):
            return False
        item = cast(dict[str, object], tool)
        if set(item) != {"name", "description", "input_schema"}:
            return False
        if not isinstance(item["name"], str) or not isinstance(item["description"], str):
            return False
        if not isinstance(item["input_schema"], dict):
            return False
    return True


def parse_v3_event(payload: str) -> dict[str, object]:
    value = _object(payload)
    _identity(value)
    _exact(value, "protocol_version", "type", "generation_id", "epoch", "round", "calls")
    if (
        value.get("type") != "tool_calls"
        or type(value.get("round")) is not int
        or cast(int, value["round"]) < 1
        or not isinstance(value.get("calls"), list)
    ):
        raise ProtocolV3Error("invalid_frame")
    for call in cast(list[object], value["calls"]):
        if not isinstance(call, dict):
            raise ProtocolV3Error("invalid_frame")
        item = cast(dict[str, object], call)
        _exact(item, "local_call_id", "tool_name", "arguments", "argument_error")
        valid = isinstance(item.get("local_call_id"), str) and isinstance(
            item.get("tool_name"), str
        )
        valid = valid and (
            (isinstance(item.get("arguments"), dict) and item.get("argument_error") is None)
            or (
                item.get("arguments") is None
                and item.get("argument_error") in {"invalid_json", "invalid_shape"}
            )
        )
        if not valid:
            raise ProtocolV3Error("invalid_frame")
    return value
