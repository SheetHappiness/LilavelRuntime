"""Deterministic Core lifecycle adversary fixtures.

These modes intentionally model wire interleavings that are difficult to
reproduce against a live provider. They stay transport-only and never emit
semantic conversation state.
"""

from __future__ import annotations

import json
import sys


def emit(value: dict[str, object]) -> None:
    sys.stdout.write(json.dumps(value, separators=(",", ":")) + "\n")
    sys.stdout.flush()


def generation_event(event_type: str, generation_id: object, epoch: object) -> dict[str, object]:
    return {
        "protocol_version": 2,
        "type": event_type,
        "generation_id": generation_id,
        "epoch": epoch,
    }


def main() -> int:
    mode = sys.argv[1] if len(sys.argv) > 1 else "race"
    emit(
        {
            "protocol_version": 2,
            "type": "ready",
            "provider": "fixture",
            "model_id": "fixture-model",
            "api": "fixture-api",
        }
    )

    pending_race: tuple[object, object] | None = None
    for line in sys.stdin:
        command = json.loads(line)
        command_type = command.get("type")
        if command_type == "generate":
            generation_id = command["generation_id"]
            epoch = command["epoch"]
            emit(generation_event("accepted", generation_id, epoch))
            if mode == "late-poison":
                emit(generation_event("completed", generation_id, epoch))
                emit(
                    {
                        **generation_event("failed", generation_id, epoch),
                        "code": "cleanup_error",
                    }
                )
            elif mode == "global-poison":
                emit(generation_event("completed", generation_id, epoch))
                emit(
                    {
                        "protocol_version": 2,
                        "type": "failed",
                        "code": "cleanup_error",
                    }
                )
            elif mode == "cancel-completion-race" and command.get("prompt") == "race":
                pending_race = (generation_id, epoch)
            else:
                emit(generation_event("text_delta", generation_id, epoch) | {"delta": "recovered"})
                emit(generation_event("completed", generation_id, epoch))
        elif command_type == "cancel":
            if (
                mode == "cancel-completion-race"
                and pending_race is not None
                and command["generation_id"] == pending_race[0]
                and command["epoch"] == pending_race[1]
            ):
                # Completion won the provider-side race; the later cancelled
                # frame is a duplicate terminal attempt and must not create a
                # second settlement in Core.
                emit(generation_event("completed", pending_race[0], pending_race[1]))
                emit(generation_event("cancelled", pending_race[0], pending_race[1]))
                pending_race = None
            else:
                emit(generation_event("cancelled", command["generation_id"], command["epoch"]))
        elif command_type == "shutdown":
            emit({"protocol_version": 2, "type": "shutdown"})
            if mode == "malformed-after-shutdown":
                emit({"protocol_version": 2, "type": "failed", "code": []})
            return 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
