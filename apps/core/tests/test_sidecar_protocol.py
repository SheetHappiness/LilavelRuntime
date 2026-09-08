"""Deterministic coverage for the version-two sidecar wire contract."""

import json
from pathlib import Path
from typing import cast

import pytest

from lilavel_core.sidecar_protocol import (
    MAX_CONTEXT_BYTES,
    MAX_FRAME_BYTES,
    MAX_GUIDANCE_BLOCKS,
    MAX_GUIDANCE_BYTES,
    MAX_PROMPT_BYTES,
    CancelCommand,
    ContextMessage,
    GenerateCommand,
    GenerationAccepted,
    GenerationCancelled,
    GenerationCompleted,
    GenerationFailed,
    HealthCommand,
    HealthEvent,
    ModelRequest,
    ProtocolError,
    ReadyEvent,
    ShutdownCommand,
    ShutdownEvent,
    TextDelta,
    encode_command,
    encode_event,
    parse_command,
    parse_event,
)


def _object_dict(value: object) -> dict[str, object]:
    assert isinstance(value, dict)
    return cast(dict[str, object], value)


_SHARED_CORPUS = Path(__file__).resolve().parents[3] / "testdata" / "protocol-v2" / "cases.json"


def _shared_cases() -> list[dict[str, object]]:
    payload = _object_dict(json.loads(_SHARED_CORPUS.read_text(encoding="utf-8")))
    assert payload.get("format_version") == 1
    cases = payload.get("cases")
    assert isinstance(cases, list)
    return [cast(dict[str, object], case) for case in cast(list[object], cases)]


def _case_id(case: object) -> str:
    value = _object_dict(case).get("id")
    assert isinstance(value, str)
    return value


def _normalize(value: object) -> dict[str, object]:
    if isinstance(value, GenerateCommand):
        result: dict[str, object] = {
            "protocol_version": 2,
            "type": "generate",
            "generation_id": value.generation_id,
            "epoch": value.epoch,
        }
        if value.prompt is not None:
            result["prompt"] = value.prompt
        elif value.messages is not None:
            result["messages"] = [
                {"role": message.role, "text": message.text} for message in value.messages
            ]
        if value.system_prompt:
            result["system_prompt"] = list(value.system_prompt)
        return result
    if isinstance(value, CancelCommand):
        return {
            "protocol_version": 2,
            "type": "cancel",
            "generation_id": value.generation_id,
            "epoch": value.epoch,
        }
    if isinstance(value, HealthCommand):
        return {"protocol_version": 2, "type": "health"}
    if isinstance(value, ShutdownCommand):
        return {"protocol_version": 2, "type": "shutdown"}
    if isinstance(value, ReadyEvent):
        return {
            "protocol_version": 2,
            "type": "ready",
            "provider": value.provider,
            "model_id": value.model_id,
            "api": value.api,
        }
    if isinstance(value, HealthEvent):
        return {"protocol_version": 2, "type": "health", "state": value.state}
    if isinstance(value, GenerationAccepted):
        return {
            "protocol_version": 2,
            "type": "accepted",
            "generation_id": value.generation_id,
            "epoch": value.epoch,
        }
    if isinstance(value, TextDelta):
        return {
            "protocol_version": 2,
            "type": "text_delta",
            "generation_id": value.generation_id,
            "epoch": value.epoch,
            "delta": value.delta,
        }
    if isinstance(value, GenerationCompleted):
        return {
            "protocol_version": 2,
            "type": "completed",
            "generation_id": value.generation_id,
            "epoch": value.epoch,
        }
    if isinstance(value, GenerationCancelled):
        return {
            "protocol_version": 2,
            "type": "cancelled",
            "generation_id": value.generation_id,
            "epoch": value.epoch,
        }
    if isinstance(value, GenerationFailed):
        result = {"protocol_version": 2, "type": "failed", "code": value.code}
        if value.generation_id is not None and value.epoch is not None:
            result["generation_id"] = value.generation_id
            result["epoch"] = value.epoch
        return result
    if isinstance(value, ShutdownEvent):
        return {"protocol_version": 2, "type": "shutdown"}
    raise AssertionError(f"unsupported parsed protocol value: {type(value)!r}")


@pytest.mark.parametrize("case", _shared_cases(), ids=_case_id)
def test_shared_protocol_v2_corpus(case: dict[str, object]) -> None:
    surface = case["surface"]
    frame = case["frame"]
    assert isinstance(surface, str)
    assert isinstance(frame, str)
    expected = _object_dict(case["expected"])
    accepted = expected.get("accepted")
    assert isinstance(accepted, bool)

    try:
        if surface == "command":
            parsed = parse_command(frame)
        elif surface == "event":
            parsed = parse_event(frame)
        else:
            raise AssertionError(f"unsupported shared protocol surface: {surface}")
    except ProtocolError as error:
        assert accepted is False, case["id"]
        reason = expected.get("reason")
        if reason is not None:
            assert isinstance(reason, str)
            assert error.reason == reason
        return

    assert accepted is True, case["id"]
    semantic = _object_dict(expected.get("semantic"))
    assert _normalize(parsed) == semantic


def test_command_round_trip_keeps_small_request_shape() -> None:
    command = GenerateCommand("generation-1", 7, "Reply exactly OK.")

    assert parse_command(encode_command(command)) == command
    assert json.loads(encode_command(command)) == {
        "protocol_version": 2,
        "type": "generate",
        "generation_id": "generation-1",
        "epoch": 7,
        "prompt": "Reply exactly OK.",
    }


def test_structured_context_round_trip_contains_only_role_and_text() -> None:
    messages = (
        ContextMessage("user", "Remember the word quartz."),
        ContextMessage("assistant", "ACK_1"),
        ContextMessage("user", "What was the word?"),
    )
    command = GenerateCommand("generation-structured", 8, messages=messages)

    assert parse_command(encode_command(command)) == command
    assert json.loads(encode_command(command)) == {
        "protocol_version": 2,
        "type": "generate",
        "generation_id": "generation-structured",
        "epoch": 8,
        "messages": [
            {"role": "user", "text": "Remember the word quartz."},
            {"role": "assistant", "text": "ACK_1"},
            {"role": "user", "text": "What was the word?"},
        ],
    }


def test_guidance_round_trip_is_optional_and_messages_only_shape_stays_small() -> None:
    messages = (ContextMessage("user", "hello"),)
    baseline = GenerateCommand("generation-baseline", 1, messages=messages)
    guided = GenerateCommand(
        "generation-guided",
        2,
        messages=messages,
        system_prompt=("trusted identity", "trusted behavior"),
    )

    assert json.loads(encode_command(baseline)) == {
        "protocol_version": 2,
        "type": "generate",
        "generation_id": "generation-baseline",
        "epoch": 1,
        "messages": [{"role": "user", "text": "hello"}],
    }
    assert parse_command(encode_command(guided)) == guided
    assert json.loads(encode_command(guided))["system_prompt"] == [
        "trusted identity",
        "trusted behavior",
    ]


def test_guidance_rejects_malformed_empty_duplicate_and_unbounded_blocks() -> None:
    messages = (ContextMessage("user", "hello"),)
    with pytest.raises(ProtocolError):
        ModelRequest(messages=messages, system_prompt=("",))
    with pytest.raises(ProtocolError):
        ModelRequest(messages=messages, system_prompt=("same", "same"))
    with pytest.raises(ProtocolError):
        ModelRequest(messages=messages, system_prompt=("x",) * (MAX_GUIDANCE_BLOCKS + 1))
    with pytest.raises(ProtocolError):
        ModelRequest(messages=messages, system_prompt=("x" * (MAX_GUIDANCE_BYTES + 1),))
    with pytest.raises(ProtocolError):
        ModelRequest(messages=messages, system_prompt="not-a-block-list")  # type: ignore[arg-type]

    with pytest.raises(ProtocolError):
        parse_command(
            b'{"protocol_version":2,"type":"generate","generation_id":"x","epoch":1,'
            b'"messages":[{"role":"user","text":"hello"}],"system_prompt":[" "]}\n'
        )


def test_structured_context_rejects_unsupported_roles_and_unbounded_context() -> None:
    with pytest.raises(ProtocolError):
        parse_command(
            b'{"protocol_version":2,"type":"generate","generation_id":"x","epoch":1,'
            b'"messages":[{"role":"developer","text":"not supported"}]}\n'
        )

    with pytest.raises(ProtocolError):
        ModelRequest(messages=(ContextMessage("user", "x" * (MAX_CONTEXT_BYTES + 1)),))

    with pytest.raises(ProtocolError):
        ModelRequest(messages=())


def test_structured_context_cannot_mix_legacy_prompt() -> None:
    with pytest.raises(ProtocolError):
        GenerateCommand(
            "generation-1", 1, prompt="prompt", messages=(ContextMessage("user", "text"),)
        )


def test_all_event_variants_serialize_and_parse() -> None:
    events = [
        ReadyEvent("openai-codex", "gpt-5.6-luna", "openai-codex-responses"),
        HealthEvent("ready"),
        GenerationAccepted("generation-1", 1),
        TextDelta("generation-1", 1, "first"),
        GenerationCompleted("generation-1", 1),
        GenerationCancelled("generation-2", 2),
        GenerationFailed("generation-3", 3, "provider_error"),
        GenerationFailed(None, None, "startup_error"),
        ShutdownEvent(),
    ]

    assert [parse_event(encode_event(event)) for event in events] == events


@pytest.mark.parametrize(
    "payload",
    [
        b"not-json\n",
        b"[]\n",
        b'{"protocol_version":2,"type":"health","extra":true}\n',
        b'{"protocol_version":1,"type":"health"}\n',
        b'{"protocol_version":3,"type":"health"}\n',
        b'{"protocol_version":2,"type":"generate","generation_id":"x","epoch":1,"prompt":"x","generation_id":"y"}\n',
        b'{"protocol_version":2,"type":"text_delta","generation_id":"x","epoch":1,"delta":""}\n',
    ],
)
def test_malformed_or_unknown_frames_are_rejected(payload: bytes) -> None:
    with pytest.raises(ProtocolError):
        parse_event(payload)


def test_parser_rejects_non_integer_protocol_numbers() -> None:
    with pytest.raises(ProtocolError):
        parse_command(b'{"protocol_version":2.0,"type":"health"}\n')
    with pytest.raises(ProtocolError):
        parse_command(
            b'{"protocol_version":2,"type":"generate","generation_id":"x",'
            b'"epoch":1.0,"prompt":"x"}\n'
        )


def test_command_parser_rejects_oversized_prompt_and_frame() -> None:
    with pytest.raises(ProtocolError):
        ModelRequest("x" * (MAX_PROMPT_BYTES + 1))

    oversized = b"{" + b'"x":' + b'"' + b"x" * MAX_FRAME_BYTES + b'"}'
    with pytest.raises(ProtocolError):
        parse_command(oversized)


def test_command_variants_have_no_extra_semantics() -> None:
    assert parse_command(encode_command(CancelCommand("generation-1", 1))) == CancelCommand(
        "generation-1", 1
    )
    assert isinstance(parse_command(encode_command(HealthCommand())), HealthCommand)
    assert isinstance(parse_command(encode_command(ShutdownCommand())), ShutdownCommand)


@pytest.mark.parametrize("parser", [parse_command, parse_event])
@pytest.mark.parametrize("encoding", ["utf-16", "utf-16-le", "utf-32", "utf-32-be"])
def test_parser_requires_utf8_bytes(parser: object, encoding: str) -> None:
    payload = '{"protocol_version":2,"type":"shutdown"}'.encode(encoding)
    with pytest.raises(ProtocolError):
        parser(payload)  # type: ignore[operator]


@pytest.mark.parametrize("parser", [parse_command, parse_event])
@pytest.mark.parametrize("payload", [b"\xff", '"\ud800"', "[" * 2000, "1" * 5000])
def test_parser_normalizes_malformed_input(parser: object, payload: bytes | str) -> None:
    with pytest.raises(ProtocolError):
        parser(payload)  # type: ignore[operator]


@pytest.mark.parametrize("field", ["prompt", "generation_id", "messages", "system_prompt"])
@pytest.mark.parametrize("surrogate", ["\ud800", "\udfff"])
def test_command_normalizes_escaped_surrogates(field: str, surrogate: str) -> None:
    frame: dict[str, object] = {
        "protocol_version": 2,
        "type": "generate",
        "generation_id": "x",
        "epoch": 1,
        "prompt": "hello",
    }
    if field == "messages":
        del frame["prompt"]
        frame[field] = [{"role": "user", "text": surrogate}]
    elif field == "system_prompt":
        frame[field] = [surrogate]
    else:
        frame[field] = surrogate
    with pytest.raises(ProtocolError):
        parse_command(json.dumps(frame))


def test_event_normalizes_escaped_surrogate_and_preserves_valid_pair() -> None:
    frame = {
        "protocol_version": 2,
        "type": "text_delta",
        "generation_id": "x",
        "epoch": 1,
        "delta": "\ud800",
    }
    with pytest.raises(ProtocolError):
        parse_event(json.dumps(frame))
    frame["delta"] = "\U0001f600"
    assert parse_event(json.dumps(frame)) == TextDelta("x", 1, "\U0001f600")
