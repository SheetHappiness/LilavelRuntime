"""Deterministic process fixture for caller-owned structured context tests."""

from __future__ import annotations

import json
import sys
from typing import TypeGuard, cast


def emit(value: dict[str, object]) -> None:
    sys.stdout.write(json.dumps(value, separators=(",", ":")) + "\n")
    sys.stdout.flush()


def valid_messages(value: object) -> TypeGuard[list[dict[str, object]]]:
    if not isinstance(value, list) or not value:
        return False
    for raw_message in cast(list[object], value):
        if not isinstance(raw_message, dict):
            return False
        message = cast(dict[str, object], raw_message)
        if (
            set(message) != {"role", "text"}
            or message.get("role") not in {"user", "assistant"}
            or not isinstance(message.get("text"), str)
        ):
            return False
    return True


def valid_guidance(value: object) -> TypeGuard[list[str]]:
    if not isinstance(value, list):
        return False
    raw_blocks = cast(list[object], value)
    if len(raw_blocks) > 32:
        return False
    blocks = [block for block in raw_blocks if isinstance(block, str)]
    return (
        len(blocks) == len(raw_blocks)
        and all(block.strip() for block in blocks)
        and len(set(blocks)) == len(blocks)
    )


def main() -> int:
    emit(
        {
            "protocol_version": 2,
            "type": "ready",
            "provider": "fixture",
            "model_id": "structured-fixture",
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
            raw_messages = command.get("messages")
            raw_guidance = command.get("system_prompt")
            if not valid_messages(raw_messages) or (
                raw_guidance is not None and not valid_guidance(raw_guidance)
            ):
                emit(
                    {
                        "protocol_version": 2,
                        "type": "failed",
                        "generation_id": generation_id,
                        "epoch": epoch,
                        "code": "protocol_error",
                    }
                )
                continue
            messages = raw_messages
            last_text = messages[-1]["text"]
            emit(
                {
                    "protocol_version": 2,
                    "type": "accepted",
                    "generation_id": generation_id,
                    "epoch": epoch,
                }
            )
            if last_text == "cancel":
                active = (generation_id, epoch)
                emit(
                    {
                        "protocol_version": 2,
                        "type": "text_delta",
                        "generation_id": generation_id,
                        "epoch": epoch,
                        "delta": "partial",
                    }
                )
                continue
            if raw_guidance == ["trusted identity"]:
                response = "structured-guided"
            else:
                response = "structured-continuity" if len(messages) == 3 else "structured-recovered"
            emit(
                {
                    "protocol_version": 2,
                    "type": "text_delta",
                    "generation_id": generation_id,
                    "epoch": epoch,
                    "delta": response,
                }
            )
            emit(
                {
                    "protocol_version": 2,
                    "type": "completed",
                    "generation_id": generation_id,
                    "epoch": epoch,
                }
            )
        elif command_type == "cancel":
            if active is not None:
                generation_id, epoch = active
                emit(
                    {
                        "protocol_version": 2,
                        "type": "cancelled",
                        "generation_id": generation_id,
                        "epoch": epoch,
                    }
                )
                emit(
                    {
                        "protocol_version": 2,
                        "type": "text_delta",
                        "generation_id": generation_id,
                        "epoch": epoch,
                        "delta": "STALE_LATE_DELTA",
                    }
                )
                active = None
        elif command_type == "shutdown":
            emit({"protocol_version": 2, "type": "shutdown"})
            return 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
