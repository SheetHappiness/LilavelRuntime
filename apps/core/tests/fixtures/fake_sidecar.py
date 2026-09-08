"""Small deterministic process fixture for Core subprocess contract tests."""

from __future__ import annotations

import json
import os
import sys
import time


def emit(value: dict[str, object]) -> None:
    sys.stdout.write(json.dumps(value, separators=(",", ":")) + "\n")
    sys.stdout.flush()


def main() -> int:
    mode = sys.argv[1] if len(sys.argv) > 1 else "normal"
    if mode == "no-ready":
        time.sleep(30)
        return 0

    emit(
        {
            "protocol_version": 2,
            "type": "ready",
            "provider": "fixture",
            "model_id": "fixture-model",
            "api": "fixture-api",
        }
    )
    if mode == "no-read-after-ready":
        time.sleep(30)
        return 0
    if mode == "delayed-ready":
        time.sleep(0.1)
    cancelled_id: object | None = None
    cancelled_epoch: object | None = None
    delayed_generation: tuple[object, object] | None = None
    for line in sys.stdin:
        command = json.loads(line)
        command_type = command.get("type")
        if command_type == "generate":
            generation_id = command["generation_id"]
            epoch = command["epoch"]
            if mode == "cancel-before-accepted":
                delayed_generation = (generation_id, epoch)
                continue
            emit(
                {
                    "protocol_version": 2,
                    "type": "accepted",
                    "generation_id": generation_id,
                    "epoch": epoch,
                }
            )
            if mode == "crash-after-accepted":
                os._exit(3)
            if mode == "malformed-after-accepted":
                sys.stdout.write('{"protocol_version":2,"type":"unexpected"}\n')
                sys.stdout.flush()
                return 0
            if mode == "duplicate-accepted":
                emit(
                    {
                        "protocol_version": 2,
                        "type": "accepted",
                        "generation_id": generation_id,
                        "epoch": epoch,
                    }
                )
                continue
            if mode in {"pending-shutdown", "cancel-never-terminal"}:
                continue
            if mode == "burst":
                for index in range(140):
                    emit(
                        {
                            "protocol_version": 2,
                            "type": "text_delta",
                            "generation_id": generation_id,
                            "epoch": epoch,
                            "delta": f"delta-{index}",
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
                continue
            if command.get("prompt") == "cancel":
                emit(
                    {
                        "protocol_version": 2,
                        "type": "text_delta",
                        "generation_id": generation_id,
                        "epoch": epoch,
                        "delta": "before-cancel",
                    }
                )
                continue
            if (
                mode == "supersede-race"
                and cancelled_id is not None
                and cancelled_epoch is not None
            ):
                emit(
                    {
                        "protocol_version": 2,
                        "type": "text_delta",
                        "generation_id": cancelled_id,
                        "epoch": cancelled_epoch,
                        "delta": "STALE_SUPERSEDED_DELTA",
                    }
                )
            text = "recovered" if command.get("prompt") == "recovery" else "streamed"
            emit(
                {
                    "protocol_version": 2,
                    "type": "text_delta",
                    "generation_id": generation_id,
                    "epoch": epoch,
                    "delta": text,
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
            cancelled_id = command["generation_id"]
            cancelled_epoch = command["epoch"]
            if mode == "cancel-before-accepted" and delayed_generation is not None:
                emit(
                    {
                        "protocol_version": 2,
                        "type": "accepted",
                        "generation_id": delayed_generation[0],
                        "epoch": delayed_generation[1],
                    }
                )
                emit(
                    {
                        "protocol_version": 2,
                        "type": "cancelled",
                        "generation_id": delayed_generation[0],
                        "epoch": delayed_generation[1],
                    }
                )
                continue
            if mode == "cancel-never-terminal":
                time.sleep(30)
                continue
            if mode == "pending-shutdown":
                continue
            if mode == "cleanup-timeout":
                emit(
                    {
                        "protocol_version": 2,
                        "type": "failed",
                        "generation_id": command["generation_id"],
                        "epoch": command["epoch"],
                        "code": "cleanup_timeout",
                    }
                )
                continue
            emit(
                {
                    "protocol_version": 2,
                    "type": "cancelled",
                    "generation_id": command["generation_id"],
                    "epoch": command["epoch"],
                }
            )
            emit(
                {
                    "protocol_version": 2,
                    "type": "text_delta",
                    "generation_id": command["generation_id"],
                    "epoch": command["epoch"],
                    "delta": "STALE_LATE_DELTA",
                }
            )
        elif command_type == "health":
            emit({"protocol_version": 2, "type": "health", "state": "ready"})
        elif command_type == "shutdown":
            if mode == "shutdown-crash":
                os._exit(3)
            if mode == "shutdown-no-ack":
                return 0
            if mode == "shutdown-truncated":
                sys.stdout.write('{"protocol_version":2,"type":"shutdown"')
                sys.stdout.flush()
                return 0
            emit({"protocol_version": 2, "type": "shutdown"})
            return 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
