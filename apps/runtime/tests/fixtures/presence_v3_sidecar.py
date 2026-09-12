"""Deterministic presence V3 sidecar fixture; no provider or real effect."""

from __future__ import annotations

import json
import sys


def emit(value: dict[str, object]) -> None:
    sys.stdout.write(json.dumps(value, separators=(",", ":")) + "\n")
    sys.stdout.flush()


def identity(kind: str, generation_id: object, epoch: object) -> dict[str, object]:
    return {
        "protocol_version": 3,
        "type": kind,
        "generation_id": generation_id,
        "epoch": epoch,
    }


def call(call_id: str, name: str, arguments: dict[str, object]) -> dict[str, object]:
    return {
        "local_call_id": call_id,
        "tool_name": name,
        "arguments": arguments,
        "argument_error": None,
    }


def turn_semantics_reply(latest: str) -> str:
    normalized = latest.casefold()
    if "moving fully to linux" in normalized:
        return "Use a Linux Live USB to test whether the mouse works."
    if "ask me whether" in normalized:
        return "Did the mouse test succeed?"
    if "check with me" in normalized:
        return "Okay, tell me how the test goes."
    if "2 + 2" in normalized:
        return "4"
    return f"reply:{latest}"


def main() -> int:
    mode = sys.argv[1]
    emit(
        {
            "protocol_version": 3,
            "type": "ready",
            "provider": "fixture",
            "model_id": "fixture",
            "api": "fixture",
        }
    )
    active: tuple[object, object, bool] | None = None
    user_generations = 0
    appraisal_generations = 0
    for line in sys.stdin:
        command = json.loads(line)
        command_type = command["type"]
        if command_type == "generate":
            generation_id = command["generation_id"]
            epoch = command["epoch"]
            tools = command.get("tools", [])
            system_prompt = command.get("system_prompt", [])
            appraisal = any("internal mind appraisal" in block for block in system_prompt)
            autonomous = bool(tools)
            active = (generation_id, epoch, autonomous)
            emit(identity("accepted", generation_id, epoch))
            if appraisal:
                appraisal_generations += 1
                if mode == "block-first-appraisal" and appraisal_generations == 1:
                    continue
                messages = command.get("messages", [])
                latest_user = next(
                    (
                        message["text"]
                        for message in reversed(messages)
                        if message["role"] == "user"
                    ),
                    "",
                )
                latest_assistant = next(
                    (
                        message["text"]
                        for message in reversed(messages)
                        if message["role"] == "assistant"
                    ),
                    "",
                )
                if mode == "turn-semantics":
                    future_lilavel_action = "check with me" in latest_user.casefold()
                    assistant_already_acted = (
                        "live usb" in latest_assistant.casefold() or "?" in latest_assistant
                    )
                    create_intention = future_lilavel_action and not assistant_already_acted
                else:
                    create_intention = any(
                        marker in latest_user.casefold()
                        for marker in (
                            "future",
                            "unfinished",
                            "tomorrow",
                            "later",
                            "finish",
                            "потом",
                            "заверш",
                        )
                    )
                result = (
                    {"action": "create_intention", "text": "Finish the concrete matter later."}
                    if create_intention
                    else {"action": "no_change"}
                )
                emit({**identity("text_delta", generation_id, epoch), "delta": json.dumps(result)})
                emit(identity("completed", generation_id, epoch))
                active = None
                continue
            if not autonomous:
                user_generations += 1
                messages = command.get("messages", [])
                latest = messages[-1]["text"] if messages else command.get("prompt", "")
                reply = (
                    turn_semantics_reply(latest) if mode == "turn-semantics" else f"reply:{latest}"
                )
                emit({**identity("text_delta", generation_id, epoch), "delta": reply})
                if mode != "block-first-user" or user_generations > 1:
                    emit(identity("completed", generation_id, epoch))
                    active = None
                continue
            if mode in {"block-generation", "shutdown-block"}:
                continue
            if mode == "invalid":
                emit({**identity("text_delta", generation_id, epoch), "delta": "ordinary text"})
                emit(identity("completed", generation_id, epoch))
                active = None
                continue
            calls = (
                [
                    call("say", "presence.say", {"text": "hello from idle"}),
                    call("silent", "presence.stay_silent", {}),
                ]
                if mode == "two-actions"
                else [call("silent", "presence.stay_silent", {})]
                if mode == "silent"
                else [call("say", "presence.say", {"text": "hello from idle"})]
            )
            emit({**identity("tool_calls", generation_id, epoch), "round": 1, "calls": calls})
        elif command_type == "tool_results" and active is not None:
            generation_id, epoch, _ = active
            emit(
                {
                    **identity("text_delta", generation_id, epoch),
                    "delta": "duplicate continuation must be discarded",
                }
            )
            emit(identity("completed", generation_id, epoch))
            active = None
        elif command_type == "cancel" and active is not None:
            generation_id, epoch, _ = active
            emit(identity("cancelled", generation_id, epoch))
            active = None
        elif command_type == "shutdown":
            emit({"protocol_version": 3, "type": "shutdown"})
            return 0
        elif command_type == "health":
            emit({"protocol_version": 3, "type": "health", "state": "ready"})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
