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
    for line in sys.stdin:
        command = json.loads(line)
        command_type = command["type"]
        if command_type == "generate":
            generation_id = command["generation_id"]
            epoch = command["epoch"]
            tools = command.get("tools", [])
            autonomous = bool(tools)
            active = (generation_id, epoch, autonomous)
            emit(identity("accepted", generation_id, epoch))
            if not autonomous:
                user_generations += 1
                messages = command.get("messages", [])
                latest = messages[-1]["text"] if messages else command.get("prompt", "")
                emit({**identity("text_delta", generation_id, epoch), "delta": f"reply:{latest}"})
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
