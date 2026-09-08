"""Measure local SQLite canonical append/load latency on a temporary database."""

from __future__ import annotations

import math
import statistics
import tempfile
from pathlib import Path
from time import perf_counter_ns
from typing import Final

from lilavel_core import SQLiteConversationStore

SAMPLE_COUNT: Final = 200
WARMUP_COUNT: Final = 20


def percentile(samples: list[float], percentile_value: float) -> float:
    ordered = sorted(samples)
    index = max(0, min(len(ordered) - 1, math.ceil(len(ordered) * percentile_value / 100) - 1))
    return ordered[index]


def report(label: str, samples: list[float]) -> None:
    print(f"{label}: p50={statistics.median(samples):.3f} ms p95={percentile(samples, 95):.3f} ms")


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="lilavel-persistence-") as directory:
        store = SQLiteConversationStore(Path(directory) / "latency.sqlite3")
        try:
            for index in range(WARMUP_COUNT):
                store.append_user_message(
                    scope_id="latency",
                    message_id=f"warmup-user-{index}",
                    text="warmup user",
                )
                store.append_assistant_message(
                    scope_id="latency",
                    message_id=f"warmup-assistant-{index}",
                    text="warmup assistant",
                )

            append_samples: list[float] = []
            load_samples: list[float] = []
            for index in range(SAMPLE_COUNT):
                started = perf_counter_ns()
                store.append_user_message(
                    scope_id="latency",
                    message_id=f"sample-user-{index}",
                    text="sample user",
                )
                store.append_assistant_message(
                    scope_id="latency",
                    message_id=f"sample-assistant-{index}",
                    text="sample assistant",
                )
                append_samples.append((perf_counter_ns() - started) / 1_000_000)

                started = perf_counter_ns()
                store.load_canonical_messages("latency")
                load_samples.append((perf_counter_ns() - started) / 1_000_000)

            print(
                f"SQLite journal_mode={store.journal_mode}; "
                f"warmup={WARMUP_COUNT}; samples={SAMPLE_COUNT}"
            )
            report("append user+assistant with evidence", append_samples)
            report("load canonical history", load_samples)
        finally:
            store.close()


if __name__ == "__main__":
    main()
