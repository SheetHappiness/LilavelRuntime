"""Deterministic V3 provider fixture for the Discord tool composition proof."""

from __future__ import annotations

import json
import sys


def emit(value: dict[str, object]) -> None:
    sys.stdout.write(json.dumps(value, separators=(",", ":")) + "\n")
    sys.stdout.flush()


def identity(frame_type: str, generation_id: object, epoch: object) -> dict[str, object]:
    return {
        "protocol_version": 3,
        "type": frame_type,
        "generation_id": generation_id,
        "epoch": epoch,
    }


def call(
    call_id: str,
    arguments: dict[str, object],
    *,
    tool_name: str = "discord.send_message",
) -> dict[str, object]:
    return {
        "local_call_id": call_id,
        "tool_name": tool_name,
        "arguments": arguments,
        "argument_error": None,
    }


def main() -> int:
    mode = sys.argv[1] if len(sys.argv) > 1 else "allowed"
    emit(
        {
            "protocol_version": 3,
            "type": "ready",
            "provider": "fixture",
            "model_id": "discord-tool-fixture",
            "api": "fixture-api",
        }
    )
    active: tuple[object, object] | None = None
    for line in sys.stdin:
        command = json.loads(line)
        command_type = command.get("type")
        if command_type == "generate":
            generation_id = command["generation_id"]
            epoch = command["epoch"]
            active = (generation_id, epoch)
            emit(identity("accepted", generation_id, epoch))
            if mode == "no-tool":
                emit({**identity("text_delta", generation_id, epoch), "delta": "ordinary"})
                emit(identity("completed", generation_id, epoch))
                active = None
                continue
            arguments: dict[str, object] = {
                "text": "tool message <@everyone>",
            }
            if mode == "inject-destination":
                arguments["channel_id"] = "attacker-channel"
            elif mode == "oversized":
                arguments["text"] = "x" * 2001
            elif mode == "unexposed":
                emit(
                    {
                        **identity("tool_calls", generation_id, epoch),
                        "round": 1,
                        "calls": [call("call-1", arguments, tool_name="discord.other")],
                    }
                )
                continue
            emit(
                {
                    **identity("tool_calls", generation_id, epoch),
                    "round": 1,
                    "calls": [call("call-1", arguments)],
                }
            )
        elif command_type == "tool_results" and active is not None:
            generation_id, epoch = active
            statuses = [result["status"] for result in command["results"]]
            text = "sent" if statuses == ["ok"] else "tool rejected"
            emit({**identity("text_delta", generation_id, epoch), "delta": text})
            emit(identity("completed", generation_id, epoch))
            active = None
        elif command_type == "cancel":
            generation_id = command["generation_id"]
            epoch = command["epoch"]
            emit(identity("cancelled", generation_id, epoch))
            active = None
        elif command_type == "shutdown":
            emit({"protocol_version": 3, "type": "shutdown"})
            return 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
