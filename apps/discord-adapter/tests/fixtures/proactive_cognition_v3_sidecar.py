"""Deterministic V3 provider fixture for proactive cognition isolation."""

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


def main() -> int:
    emit(
        {
            "protocol_version": 3,
            "type": "ready",
            "provider": "fixture",
            "model_id": "proactive-cognition-fixture",
            "api": "fixture-api",
        }
    )
    for line in sys.stdin:
        command = json.loads(line)
        command_type = command.get("type")
        if command_type == "generate":
            generation_id = command["generation_id"]
            epoch = command["epoch"]
            if "tools" in command:
                emit(
                    {
                        **identity("failed", generation_id, epoch),
                        "code": "tools_exposed",
                    }
                )
                continue
            emit(identity("accepted", generation_id, epoch))
            guidance = "\n".join(command.get("system_prompt", ()))
            if "internal mind appraisal" in guidance:
                response = '{"action":"create_intention","text":"check later"}'
            else:
                response = '{"action":"speak","text":"one follow-up"}'
            emit({**identity("text_delta", generation_id, epoch), "delta": response})
            emit(identity("completed", generation_id, epoch))
        elif command_type == "cancel":
            emit(identity("cancelled", command["generation_id"], command["epoch"]))
        elif command_type == "shutdown":
            emit({"protocol_version": 3, "type": "shutdown"})
            return 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
