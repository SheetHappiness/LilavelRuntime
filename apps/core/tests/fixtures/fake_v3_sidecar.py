"""Deterministic V3 process fixture; it performs no provider or tool effects."""

from __future__ import annotations

import json
import os
import sys
import threading
import time

_WRITE_LOCK = threading.Lock()


def emit(value: dict[str, object]) -> None:
    with _WRITE_LOCK:
        sys.stdout.write(json.dumps(value, separators=(",", ":")) + "\n")
        sys.stdout.flush()


def identity(frame_type: str, generation_id: object, epoch: object) -> dict[str, object]:
    return {
        "protocol_version": 3,
        "type": frame_type,
        "generation_id": generation_id,
        "epoch": epoch,
    }


def call(call_id: str, name: str = "fake.echo") -> dict[str, object]:
    return {
        "local_call_id": call_id,
        "tool_name": name,
        "arguments": {"value": call_id},
        "argument_error": None,
    }


def tool_calls(
    generation_id: object,
    epoch: object,
    round_value: int,
    calls: list[dict[str, object]],
) -> None:
    emit(
        {
            **identity("tool_calls", generation_id, epoch),
            "round": round_value,
            "calls": calls,
        }
    )


def main() -> int:
    mode = sys.argv[1] if len(sys.argv) > 1 else "one-tool"
    emit(
        {
            "protocol_version": 3,
            "type": "ready",
            "provider": "fixture",
            "model_id": "fixture-model",
            "api": "fixture-api",
        }
    )
    active: tuple[object, object] | None = None
    next_round = 1
    for line in sys.stdin:
        command = json.loads(line)
        command_type = command.get("type")
        if command_type == "generate":
            generation_id = command["generation_id"]
            epoch = command["epoch"]
            active = (generation_id, epoch)
            next_round = 1
            emit(identity("accepted", generation_id, epoch))
            if mode == "malformed":
                sys.stdout.write('{"protocol_version":3,"type":"tool_calls"}\n')
                sys.stdout.flush()
                continue
            if mode == "crash-while-tool":
                tool_calls(generation_id, epoch, 1, [call("call-1")])
                time.sleep(0.03)
                os._exit(3)
            if mode == "failed-while-tool":
                tool_calls(generation_id, epoch, 1, [call("call-1")])

                def fail_later(
                    failed_id: object = generation_id, failed_epoch: object = epoch
                ) -> None:
                    time.sleep(0.03)
                    emit(
                        {
                            **identity("failed", failed_id, failed_epoch),
                            "code": "provider_error",
                        }
                    )

                threading.Thread(target=fail_later, daemon=True).start()
                continue
            if mode == "no-tool":
                emit({**identity("text_delta", generation_id, epoch), "delta": "streamed"})
                emit(identity("completed", generation_id, epoch))
                active = None
                continue
            if mode == "wait-before-tool":
                continue
            if mode == "wrong-round":
                tool_calls(generation_id, epoch, 2, [call("call-1")])
                continue
            if mode == "second-pending-batch":
                tool_calls(generation_id, epoch, 1, [call("call-1")])
                tool_calls(generation_id, epoch, 1, [call("call-2")])
                continue
            if mode == "completion-while-pending":
                tool_calls(generation_id, epoch, 1, [call("call-1")])
                emit(identity("completed", generation_id, epoch))
                continue
            if mode == "pre-tool-text":
                emit({**identity("text_delta", generation_id, epoch), "delta": "before "})
            batch = [call("call-1"), call("call-2")] if mode == "multi-call" else [call("call-1")]
            tool_calls(generation_id, epoch, 1, batch)
        elif command_type == "tool_results" and active is not None:
            generation_id, epoch = active
            assert command["generation_id"] == generation_id
            assert command["epoch"] == epoch
            assert command["round"] == next_round
            statuses = [result["status"] for result in command["results"]]
            if mode == "multi-round" and next_round == 1:
                next_round = 2
                emit({**identity("text_delta", generation_id, epoch), "delta": "middle "})
                tool_calls(generation_id, epoch, 2, [call("call-2")])
                continue
            if mode == "wait-after-results":
                emit({**identity("text_delta", generation_id, epoch), "delta": "continuing"})
                continue
            text = "after"
            if mode == "pre-tool-text":
                text = "after"
            elif any(status != "ok" for status in statuses):
                text = "explained"
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
        elif command_type == "health":
            emit({"protocol_version": 3, "type": "health", "state": "ready"})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
