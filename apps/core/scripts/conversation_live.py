"""Opt-in live Luna validation for the Core-owned conversation layer."""

from __future__ import annotations

import json
import threading
import time
from collections.abc import Callable
from typing import Any

from lilavel_core import (
    ConversationCore,
    ConversationRun,
    ConversationTextDelta,
    ModelRuntime,
)


def collect(
    run: ConversationRun,
    *,
    on_delta: Callable[[], None] | None = None,
) -> dict[str, Any]:
    started = time.perf_counter()
    first_delta_at: float | None = None
    delta_count = 0
    for event in run:
        if isinstance(event, ConversationTextDelta):
            delta_count += 1
            if first_delta_at is None:
                first_delta_at = time.perf_counter()
                if on_delta is not None:
                    on_delta()
    outcome = run.wait(0)
    return {
        "status": outcome.status,
        "text": outcome.text,
        "delta_count": delta_count,
        "ttft_s": None if first_delta_at is None else first_delta_at - started,
        "duration_s": time.perf_counter() - started,
    }


def collect_with_timeout(run: ConversationRun, timeout: float = 120.0) -> dict[str, Any]:
    result: dict[str, Any] = {}
    error: list[BaseException] = []

    def worker() -> None:
        try:
            result.update(collect(run))
        except BaseException as exception:  # pragma: no cover - live timeout guard
            error.append(exception)

    thread = threading.Thread(target=worker, name="lilavel-live-collector", daemon=True)
    thread.start()
    thread.join(timeout)
    if thread.is_alive():
        run.cancel()
        thread.join(20.0)
        raise TimeoutError("live conversation run did not settle before the deadline")
    if error:
        raise error[0]
    return result


def main() -> int:
    runtime = ModelRuntime()
    conversation = ConversationCore(runtime)
    runtime.start()
    pid_before = runtime.health().pid
    run1_result: dict[str, Any]
    run2_result: dict[str, Any] = {}
    run3_result: dict[str, Any]
    supersede_triggered = False
    try:
        run1 = conversation.start_turn("Remember the word quartz. Reply briefly with ACK_QUARTZ.")
        run1_result = collect_with_timeout(run1)

        run2 = conversation.start_turn(
            "Write a long, detailed explanation of why the sky is blue. "
            "Continue for many paragraphs so this generation can be cancelled."
        )
        first_delta = threading.Event()
        run2_done = threading.Event()

        def collect_run2() -> None:
            try:
                run2_result.update(collect(run2, on_delta=first_delta.set))
            finally:
                run2_done.set()

        run2_thread = threading.Thread(
            target=collect_run2,
            name="lilavel-live-cancelled-collector",
            daemon=True,
        )
        run2_thread.start()
        if first_delta.wait(60.0):
            supersede_triggered = True
            run3 = conversation.start_turn(
                "What exact word did I ask you to remember in my first message? "
                "Reply with only that word."
            )
        else:
            run2.cancel()
            if not run2_done.wait(30.0):
                raise TimeoutError("live cancellation did not settle")
            run3 = conversation.start_turn(
                "What exact word did I ask you to remember in my first message? "
                "Reply with only that word."
            )

        run2_thread.join(30.0)
        if run2_thread.is_alive():
            run2.cancel()
            run2_thread.join(20.0)
            raise TimeoutError("superseded live run did not settle")

        run3_result = collect_with_timeout(run3)
        pid_after = runtime.health().pid
        report = {
            "pid_before": pid_before,
            "pid_after": pid_after,
            "pid_reused": pid_before is not None and pid_before == pid_after,
            "supersede_triggered_after_delta": supersede_triggered,
            "turn1": run1_result,
            "turn2": run2_result,
            "turn3": run3_result,
            "history": [{"role": item.role, "text": item.text} for item in conversation.history],
            "continuity_observed": "quartz" in str(run3_result["text"]).lower(),
        }
        print(json.dumps(report, ensure_ascii=False, sort_keys=True))
        return (
            0
            if run1_result["status"] == "completed" and run3_result["status"] == "completed"
            else 1
        )
    finally:
        runtime.shutdown()


if __name__ == "__main__":
    raise SystemExit(main())
