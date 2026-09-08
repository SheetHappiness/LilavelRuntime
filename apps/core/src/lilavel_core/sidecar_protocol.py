"""Typed version-two JSONL messages shared with the model sidecar."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Final, Literal, cast

PROTOCOL_VERSION: Final = 2
MAX_FRAME_BYTES: Final = 256 * 1024
MAX_PROMPT_BYTES: Final = 64 * 1024
MAX_CONTEXT_BYTES: Final = MAX_PROMPT_BYTES
MAX_DELTA_BYTES: Final = 16 * 1024
MAX_RESPONSE_BYTES: Final = 256 * 1024
MAX_ID_BYTES: Final = 128
MAX_GUIDANCE_BYTES: Final = 16 * 1024
MAX_GUIDANCE_BLOCKS: Final = 32
MAX_EPOCH: Final = 2**53 - 1

type ProtocolErrorCode = Literal[
    "busy",
    "not_ready",
    "closed",
    "not_active",
    "startup_error",
    "provider_error",
    "unsupported_output",
    "protocol_error",
    "output_limit",
    "cancelled",
    "cleanup_timeout",
    "cleanup_error",
    "shutdown_timeout",
    "cancellation_timeout",
]

type RuntimeState = Literal[
    "new",
    "starting",
    "ready",
    "busy",
    "shutting_down",
    "failed",
    "closed",
]
type HealthState = Literal["starting", "ready", "busy", "failed", "closed"]
type GenerationStatus = Literal["completed", "cancelled"]
type ContextRole = Literal["user", "assistant"]


class ProtocolError(ValueError):
    """Raised when a JSONL frame is not a valid version-two protocol message."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


@dataclass(frozen=True, slots=True)
class ContextMessage:
    """Provider-neutral text in caller-owned context order."""

    role: ContextRole
    text: str

    def __post_init__(self) -> None:
        if self.role not in ("user", "assistant"):
            raise ProtocolError("invalid_field")
        _bounded_string(self.text, MAX_PROMPT_BYTES, "text")


@dataclass(frozen=True, slots=True)
class ModelRequest:
    """The intentionally small Core-owned request sent to the model boundary.

    ``prompt`` keeps the existing single-turn request shape. ``messages`` is
    the structured extension: an immutable, ordered sequence of only user or
    assistant text. ``system_prompt`` is a separately supplied, trusted
    guidance block sequence; the sidecar owns adaptation to any provider
    message type.
    """

    prompt: str | None = None
    messages: tuple[ContextMessage, ...] | None = None
    system_prompt: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if (self.prompt is None) == (self.messages is None):
            raise ProtocolError("invalid_field")
        if self.prompt is not None:
            _bounded_string(self.prompt, MAX_PROMPT_BYTES, "prompt")
        else:
            assert self.messages is not None
            object.__setattr__(self, "messages", tuple(self.messages))
            _validate_context_messages(self.messages)
        object.__setattr__(self, "system_prompt", _validate_guidance(self.system_prompt))


@dataclass(frozen=True, slots=True)
class GenerateCommand:
    generation_id: str
    epoch: int
    prompt: str | None = None
    messages: tuple[ContextMessage, ...] | None = None
    system_prompt: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if (self.prompt is None) == (self.messages is None):
            raise ProtocolError("invalid_field")
        if self.prompt is not None:
            _bounded_string(self.prompt, MAX_PROMPT_BYTES, "prompt")
        else:
            assert self.messages is not None
            object.__setattr__(self, "messages", tuple(self.messages))
            _validate_context_messages(self.messages)
        object.__setattr__(self, "system_prompt", _validate_guidance(self.system_prompt))


@dataclass(frozen=True, slots=True)
class CancelCommand:
    generation_id: str
    epoch: int


@dataclass(frozen=True, slots=True)
class HealthCommand:
    pass


@dataclass(frozen=True, slots=True)
class ShutdownCommand:
    pass


type ProtocolCommand = GenerateCommand | CancelCommand | HealthCommand | ShutdownCommand


@dataclass(frozen=True, slots=True)
class ReadyEvent:
    provider: str
    model_id: str
    api: str


@dataclass(frozen=True, slots=True)
class HealthEvent:
    state: HealthState


@dataclass(frozen=True, slots=True)
class GenerationAccepted:
    generation_id: str
    epoch: int


@dataclass(frozen=True, slots=True)
class TextDelta:
    generation_id: str
    epoch: int
    delta: str


@dataclass(frozen=True, slots=True)
class GenerationCompleted:
    generation_id: str
    epoch: int


@dataclass(frozen=True, slots=True)
class GenerationCancelled:
    generation_id: str
    epoch: int


@dataclass(frozen=True, slots=True)
class GenerationFailed:
    generation_id: str | None
    epoch: int | None
    code: ProtocolErrorCode


@dataclass(frozen=True, slots=True)
class ShutdownEvent:
    pass


type GenerationEvent = (
    ReadyEvent
    | HealthEvent
    | GenerationAccepted
    | TextDelta
    | GenerationCompleted
    | GenerationCancelled
    | GenerationFailed
    | ShutdownEvent
)


def encode_command(command: ProtocolCommand) -> bytes:
    if isinstance(command, GenerateCommand):
        _id(command.generation_id)
        _epoch(command.epoch)
        value: dict[str, object] = {
            "protocol_version": PROTOCOL_VERSION,
            "type": "generate",
            "generation_id": command.generation_id,
            "epoch": command.epoch,
        }
        if command.prompt is not None:
            value["prompt"] = _bounded_string(command.prompt, MAX_PROMPT_BYTES, "prompt")
        else:
            assert command.messages is not None
            value["messages"] = _encode_context_messages(command.messages)
        if command.system_prompt:
            value["system_prompt"] = list(command.system_prompt)
    elif isinstance(command, CancelCommand):
        _id(command.generation_id)
        _epoch(command.epoch)
        value = {
            "protocol_version": PROTOCOL_VERSION,
            "type": "cancel",
            "generation_id": command.generation_id,
            "epoch": command.epoch,
        }
    elif isinstance(command, HealthCommand):
        value = {"protocol_version": PROTOCOL_VERSION, "type": "health"}
    else:
        value = {"protocol_version": PROTOCOL_VERSION, "type": "shutdown"}
    return _encode_frame(value)


def encode_event(event: GenerationEvent) -> bytes:
    if isinstance(event, ReadyEvent):
        value: dict[str, object] = {
            "protocol_version": PROTOCOL_VERSION,
            "type": "ready",
            "provider": _bounded_string(event.provider, MAX_ID_BYTES, "provider"),
            "model_id": _bounded_string(event.model_id, MAX_ID_BYTES, "model_id"),
            "api": _bounded_string(event.api, MAX_ID_BYTES, "api"),
        }
    elif isinstance(event, HealthEvent):
        _health_state(event.state)
        value = {"protocol_version": PROTOCOL_VERSION, "type": "health", "state": event.state}
    elif isinstance(event, GenerationAccepted):
        value = _generation_value("accepted", event.generation_id, event.epoch)
    elif isinstance(event, TextDelta):
        _id(event.generation_id)
        _epoch(event.epoch)
        _bounded_string(event.delta, MAX_DELTA_BYTES, "delta")
        if not event.delta:
            raise ProtocolError("invalid_field")
        value = _generation_value("text_delta", event.generation_id, event.epoch)
        value["delta"] = event.delta
    elif isinstance(event, GenerationCompleted):
        value = _generation_value("completed", event.generation_id, event.epoch)
    elif isinstance(event, GenerationCancelled):
        value = _generation_value("cancelled", event.generation_id, event.epoch)
    elif isinstance(event, GenerationFailed):
        _error_code(event.code)
        if (event.generation_id is None) != (event.epoch is None):
            raise ProtocolError("invalid_field")
        value = {"protocol_version": PROTOCOL_VERSION, "type": "failed", "code": event.code}
        if event.generation_id is not None and event.epoch is not None:
            _id(event.generation_id)
            _epoch(event.epoch)
            value["generation_id"] = event.generation_id
            value["epoch"] = event.epoch
    else:
        value = {"protocol_version": PROTOCOL_VERSION, "type": "shutdown"}
    return _encode_frame(value)


def parse_event(payload: bytes | str) -> GenerationEvent:
    value = _parse_object(payload)
    _require_keys(value, "protocol_version", "type")
    _version(value)
    event_type = value["type"]
    if not isinstance(event_type, str):
        raise ProtocolError("invalid_field")

    if event_type == "ready":
        _expect_keys(value, "protocol_version", "type", "provider", "model_id", "api")
        return ReadyEvent(
            _bounded_string(value["provider"], MAX_ID_BYTES, "provider"),
            _bounded_string(value["model_id"], MAX_ID_BYTES, "model_id"),
            _bounded_string(value["api"], MAX_ID_BYTES, "api"),
        )
    if event_type == "health":
        _expect_keys(value, "protocol_version", "type", "state")
        state = value["state"]
        if not isinstance(state, str):
            raise ProtocolError("invalid_field")
        _health_state(state)
        return HealthEvent(cast(HealthState, state))
    if event_type in {"accepted", "completed", "cancelled"}:
        _expect_keys(value, "protocol_version", "type", "generation_id", "epoch")
        generation_id = _id(value["generation_id"])
        epoch = _epoch(value["epoch"])
        if event_type == "accepted":
            return GenerationAccepted(generation_id, epoch)
        if event_type == "completed":
            return GenerationCompleted(generation_id, epoch)
        return GenerationCancelled(generation_id, epoch)
    if event_type == "text_delta":
        _expect_keys(value, "protocol_version", "type", "generation_id", "epoch", "delta")
        delta = _bounded_string(value["delta"], MAX_DELTA_BYTES, "delta")
        if not delta:
            raise ProtocolError("invalid_field")
        return TextDelta(_id(value["generation_id"]), _epoch(value["epoch"]), delta)
    if event_type == "failed":
        has_id = "generation_id" in value or "epoch" in value
        if has_id:
            _expect_keys(value, "protocol_version", "type", "generation_id", "epoch", "code")
            generation_id: str | None = _id(value["generation_id"])
            epoch: int | None = _epoch(value["epoch"])
        else:
            _expect_keys(value, "protocol_version", "type", "code")
            generation_id = None
            epoch = None
        code = value["code"]
        _error_code(code)
        return GenerationFailed(generation_id, epoch, cast(ProtocolErrorCode, code))
    if event_type == "shutdown":
        _expect_keys(value, "protocol_version", "type")
        return ShutdownEvent()
    raise ProtocolError("invalid_field")


def parse_command(payload: bytes | str) -> ProtocolCommand:
    value = _parse_object(payload)
    _require_keys(value, "protocol_version", "type")
    _version(value)
    command_type = value["type"]
    if not isinstance(command_type, str):
        raise ProtocolError("invalid_field")
    if command_type == "generate":
        _require_keys(value, "generation_id", "epoch")
        generation_id = _id(value["generation_id"])
        epoch = _epoch(value["epoch"])
        if "prompt" in value and "messages" not in value:
            _expect_keys(
                value,
                "protocol_version",
                "type",
                "generation_id",
                "epoch",
                "prompt",
                "system_prompt",
            ) if "system_prompt" in value else _expect_keys(
                value, "protocol_version", "type", "generation_id", "epoch", "prompt"
            )
            return GenerateCommand(
                generation_id,
                epoch,
                prompt=_bounded_string(value["prompt"], MAX_PROMPT_BYTES, "prompt"),
                system_prompt=_parse_guidance(value.get("system_prompt", [])),
            )
        if "messages" in value and "prompt" not in value:
            _expect_keys(
                value,
                "protocol_version",
                "type",
                "generation_id",
                "epoch",
                "messages",
                "system_prompt",
            ) if "system_prompt" in value else _expect_keys(
                value, "protocol_version", "type", "generation_id", "epoch", "messages"
            )
            return GenerateCommand(
                generation_id,
                epoch,
                messages=_parse_context_messages(value["messages"]),
                system_prompt=_parse_guidance(value.get("system_prompt", [])),
            )
        raise ProtocolError("unknown_field")
    if command_type == "cancel":
        _expect_keys(value, "protocol_version", "type", "generation_id", "epoch")
        return CancelCommand(_id(value["generation_id"]), _epoch(value["epoch"]))
    if command_type == "health":
        _expect_keys(value, "protocol_version", "type")
        return HealthCommand()
    if command_type == "shutdown":
        _expect_keys(value, "protocol_version", "type")
        return ShutdownCommand()
    raise ProtocolError("invalid_field")


def _generation_value(event_type: str, generation_id: str, epoch: int) -> dict[str, object]:
    _id(generation_id)
    _epoch(epoch)
    return {
        "protocol_version": PROTOCOL_VERSION,
        "type": event_type,
        "generation_id": generation_id,
        "epoch": epoch,
    }


def _validate_context_messages(messages: tuple[ContextMessage, ...]) -> None:
    if not messages:
        raise ProtocolError("invalid_field")
    total_bytes = 0
    for message in messages:
        total_bytes += len(message.text.encode("utf-8"))
    if total_bytes > MAX_CONTEXT_BYTES:
        raise ProtocolError("invalid_field")


def _encode_context_messages(messages: tuple[ContextMessage, ...]) -> list[dict[str, str]]:
    _validate_context_messages(messages)
    return [{"role": message.role, "text": message.text} for message in messages]


def _parse_context_messages(value: object) -> tuple[ContextMessage, ...]:
    if not isinstance(value, list):
        raise ProtocolError("invalid_field")
    messages: list[ContextMessage] = []
    for raw_item in cast(list[object], value):
        if not isinstance(raw_item, dict):
            raise ProtocolError("invalid_field")
        item = cast(dict[str, object], raw_item)
        _expect_keys(item, "role", "text")
        role = item["role"]
        if not isinstance(role, str) or role not in {"user", "assistant"}:
            raise ProtocolError("invalid_field")
        messages.append(
            ContextMessage(
                cast(ContextRole, role),
                _bounded_string(item["text"], MAX_PROMPT_BYTES, "text"),
            )
        )
    parsed = tuple(messages)
    _validate_context_messages(parsed)
    return parsed


def _parse_guidance(value: object) -> tuple[str, ...]:
    if not isinstance(value, list):
        raise ProtocolError("invalid_field")
    return _validate_guidance(cast(list[object], value))


def _validate_guidance(value: object) -> tuple[str, ...]:
    if isinstance(value, list):
        items = cast(list[object], value)
    elif isinstance(value, tuple):
        items = cast(tuple[object, ...], value)
    else:
        raise ProtocolError("invalid_field")
    if len(items) > MAX_GUIDANCE_BLOCKS:
        raise ProtocolError("invalid_field")

    result: list[str] = []
    seen: set[str] = set()
    total = 0
    for item in items:
        if not isinstance(item, str):
            raise ProtocolError("invalid_field")
        block = _bounded_string(item, MAX_PROMPT_BYTES, "system_prompt")
        if not block.strip() or block in seen:
            raise ProtocolError("invalid_field")
        seen.add(block)
        result.append(block)
        total += len(block.encode("utf-8"))
    if total > MAX_GUIDANCE_BYTES:
        raise ProtocolError("invalid_field")
    return tuple(result)


def _parse_object(payload: bytes | str) -> dict[str, object]:
    try:
        if isinstance(payload, bytes):
            if len(payload) > MAX_FRAME_BYTES:
                raise ProtocolError("frame_too_large")
            payload = payload.decode("utf-8")
        elif _utf8_size(payload) > MAX_FRAME_BYTES:
            raise ProtocolError("frame_too_large")
        value = json.loads(payload, object_pairs_hook=_pairs)
    except ProtocolError:
        raise
    except (UnicodeError, ValueError, TypeError, RecursionError) as error:
        raise ProtocolError("malformed") from error
    if not isinstance(value, dict):
        raise ProtocolError("malformed")
    return cast(dict[str, object], value)


def _pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ProtocolError("duplicate_field")
        result[key] = value
    return result


def _encode_frame(value: dict[str, object]) -> bytes:
    try:
        line = json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode(
            "utf-8"
        )
    except (TypeError, ValueError) as error:
        raise ProtocolError("invalid_field") from error
    if len(line) > MAX_FRAME_BYTES:
        raise ProtocolError("frame_too_large")
    return line + b"\n"


def _expect_keys(value: dict[str, object], *expected: str) -> None:
    if set(value) != set(expected):
        raise ProtocolError("unknown_field")


def _require_keys(value: dict[str, object], *required: str) -> None:
    if any(key not in value for key in required):
        raise ProtocolError("unknown_field")


def _version(value: dict[str, object]) -> None:
    version = value["protocol_version"]
    if type(version) is not int or version != PROTOCOL_VERSION:
        raise ProtocolError("unsupported_version")


def _id(value: object) -> str:
    if not isinstance(value, str) or not value or _utf8_size(value) > MAX_ID_BYTES:
        raise ProtocolError("invalid_field")
    if any(ord(character) < 0x20 or ord(character) == 0x7F for character in value):
        raise ProtocolError("invalid_field")
    return value


def _epoch(value: object) -> int:
    if type(value) is not int or value < 0 or value > MAX_EPOCH:
        raise ProtocolError("invalid_field")
    return value


def _utf8_size(value: str) -> int:
    try:
        return len(value.encode("utf-8"))
    except UnicodeEncodeError as error:
        raise ProtocolError("invalid_field") from error


def _bounded_string(value: object, maximum: int, field: str) -> str:
    if not isinstance(value, str) or _utf8_size(value) > maximum:
        raise ProtocolError(f"invalid_{field}")
    return value


def _health_state(value: object) -> None:
    if not isinstance(value, str) or value not in {"starting", "ready", "busy", "failed", "closed"}:
        raise ProtocolError("invalid_field")


def _error_code(value: object) -> None:
    if not isinstance(value, str) or value not in {
        "busy",
        "not_ready",
        "closed",
        "not_active",
        "startup_error",
        "provider_error",
        "unsupported_output",
        "protocol_error",
        "output_limit",
        "cancelled",
        "cleanup_timeout",
        "cleanup_error",
        "shutdown_timeout",
        "cancellation_timeout",
    }:
        raise ProtocolError("invalid_field")
