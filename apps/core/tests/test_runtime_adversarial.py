"""Independent deterministic adversarial checks for the P0.3 lifecycle contract."""

from __future__ import annotations

import math
import sys
import threading
import time
from pathlib import Path
from typing import Any

import pytest

import lilavel_core.runtime as runtime_module
from lilavel_core import (
    EventQueueOverflow,
    GenerationAccepted,
    GenerationCancelled,
    GenerationCompleted,
    GenerationFailedEvent,
    ModelRequest,
    ModelRuntime,
    RuntimeNotReady,
    ShutdownTimeout,
    SidecarCrashed,
    TextDelta,
)

FIXTURE = Path(__file__).parent / "fixtures" / "adversarial_sidecar.py"
BASE_FIXTURE = Path(__file__).parent / "fixtures" / "fake_sidecar.py"


def runtime(
    fixture: Path,
    mode: str,
    *,
    shutdown_timeout: float = 1.0,
    cancellation_timeout: float = 1.0,
    event_queue_size: int = 128,
) -> ModelRuntime:
    return ModelRuntime(
        command=(sys.executable, str(fixture), mode),
        sidecar_dir=Path(__file__).parents[1],
        shutdown_timeout=shutdown_timeout,
        cancellation_timeout=cancellation_timeout,
        event_queue_size=event_queue_size,
    )


def wait_for_state(model: ModelRuntime, state: str, timeout: float = 2.0) -> None:
    deadline = time.monotonic() + timeout
    while model.health().state != state:
        if time.monotonic() >= deadline:
            raise AssertionError(f"runtime did not reach {state!r}: {model.health()!r}")
        time.sleep(0.005)


def test_contradictory_same_epoch_poison_after_terminal_is_ignored() -> None:
    """A duplicate generation terminal does not create a second settlement."""

    model = runtime(FIXTURE, "late-poison")
    model.start()
    try:
        handle = model.generate(ModelRequest("late poison"))
        assert handle.wait(2.0).status == "completed"
        # The canonical sidecar emits cleanup errors before its terminal
        # event. A same-epoch cleanup error after completion is contradictory
        # and follows the established stale/duplicate event policy.
        wait_for_state(model, "ready")

        events = list(handle)
        assert [type(event) for event in events] == [GenerationAccepted, GenerationCompleted]
        assert sum(isinstance(event, GenerationFailedEvent) for event in events) == 0
    finally:
        model.shutdown()


def test_global_poison_after_terminal_fails_without_second_terminal() -> None:
    model = runtime(FIXTURE, "global-poison")
    model.start()
    try:
        handle = model.generate(ModelRequest("global poison"))
        assert handle.wait(2.0).status == "completed"
        wait_for_state(model, "failed")

        events = list(handle)
        assert [type(event) for event in events] == [GenerationAccepted, GenerationCompleted]
        assert sum(isinstance(event, GenerationFailedEvent) for event in events) == 0
        with pytest.raises(RuntimeNotReady):
            model.generate(ModelRequest("must not reuse"))
    finally:
        model.shutdown()


def test_repeated_global_failures_start_only_one_containment_worker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Repeated fatal frames remain idempotent while the first kill is running."""

    class FakeProcess:
        pid = 123

        def poll(self) -> None:
            return None

    started = threading.Event()
    release = threading.Event()
    workers: list[threading.Thread] = []

    def terminate(_process: object, _containment: object) -> bool:
        started.set()
        release.wait(1.0)
        return True

    original_thread = runtime_module.threading.Thread

    def tracking_thread(*args: Any, **kwargs: Any) -> threading.Thread:
        thread = original_thread(*args, **kwargs)
        workers.append(thread)
        return thread

    monkeypatch.setattr(runtime_module, "_terminate_owned_process", terminate)
    monkeypatch.setattr(runtime_module.threading, "Thread", tracking_thread)

    model = runtime(FIXTURE, "global-poison")
    model._state = "ready"  # type: ignore[attr-defined]  # deliberate worker fake
    model._process = FakeProcess()  # type: ignore[attr-defined]  # deliberate worker fake
    model._fail_runtime(  # pyright: ignore[reportPrivateUsage]
        runtime_module.ProtocolViolation("first fatal frame")
    )
    assert started.wait(1.0)

    for _ in range(20):
        model._fail_runtime(  # pyright: ignore[reportPrivateUsage]
            runtime_module.ProtocolViolation("duplicate fatal frame")
        )

    assert len(workers) == 1
    release.set()
    workers[0].join(1.0)
    assert not workers[0].is_alive()
    assert model.health().state == "failed"


def test_cancel_completion_race_has_one_terminal_and_preserves_reuse() -> None:
    model = runtime(FIXTURE, "cancel-completion-race")
    model.start()
    try:
        handle = model.generate(ModelRequest("race"))
        events: list[object] = []
        accepted = threading.Event()
        consumed = threading.Event()

        def consume() -> None:
            try:
                for event in handle:
                    events.append(event)
                    if isinstance(event, GenerationAccepted):
                        accepted.set()
            finally:
                consumed.set()

        consumer = threading.Thread(target=consume, daemon=True)
        consumer.start()
        assert accepted.wait(2.0)
        assert model.cancel(handle.generation_id)

        assert consumed.wait(2.0)
        consumer.join(1.0)
        assert [type(event) for event in events] == [GenerationAccepted, GenerationCompleted]
        assert handle.wait(2.0).status == "completed"
        assert model.health().state == "ready"

        recovery = model.generate(ModelRequest("recovery"))
        recovery_events = list(recovery)
        assert recovery.wait(2.0).text == "recovered"
        assert (
            sum(
                isinstance(event, (GenerationCompleted, GenerationCancelled))
                for event in recovery_events
            )
            == 1
        )
    finally:
        model.shutdown()


def test_crash_with_pending_generation_retains_exactly_one_terminal_fallback() -> None:
    model = runtime(BASE_FIXTURE, "crash-after-accepted")
    model.start()
    try:
        handle = model.generate(ModelRequest("crash"))
        with pytest.raises(SidecarCrashed):
            handle.wait(2.0)

        events = list(handle)
        terminals = [
            event
            for event in events
            if isinstance(event, (GenerationCompleted, GenerationCancelled, GenerationFailedEvent))
        ]
        assert len(terminals) == 1
        assert isinstance(terminals[0], GenerationFailedEvent)
        assert model.health().state == "failed"
    finally:
        model.shutdown()


def test_default_queue_boundary_fails_fast_and_retains_queued_order() -> None:
    model = runtime(BASE_FIXTURE, "burst", event_queue_size=128)
    model.start()
    try:
        handle = model.generate(ModelRequest("burst"))
        with pytest.raises(EventQueueOverflow):
            handle.wait(2.0)

        events = list(handle)
        assert len(events) == 129  # accepted + 127 deltas + terminal fallback
        assert isinstance(events[0], GenerationAccepted)
        assert isinstance(events[1], TextDelta)
        assert events[1].delta == "delta-0"
        assert isinstance(events[127], TextDelta)
        assert events[127].delta == "delta-126"
        assert isinstance(events[-1], GenerationFailedEvent)
        assert (
            sum(
                isinstance(event, (GenerationCompleted, GenerationCancelled, GenerationFailedEvent))
                for event in events
            )
            == 1
        )
        assert model.health().state == "failed"
    finally:
        model.shutdown()


@pytest.mark.parametrize(
    "event_queue_size",
    [0, -1, 1.5, math.nan, math.inf, -math.inf],
    ids=["zero", "negative", "fraction", "nan", "positive_infinity", "negative_infinity"],
)
def test_event_queue_size_requires_a_finite_positive_integer(
    event_queue_size: int | float,
) -> None:
    with pytest.raises(ValueError, match="event_queue_size must be a positive integer"):
        ModelRuntime(
            command=(sys.executable, str(BASE_FIXTURE), "burst"),
            sidecar_dir=Path(__file__).parents[1],
            event_queue_size=event_queue_size,  # type: ignore[arg-type]
        )


def test_shutdown_timeout_settles_pending_handle_with_shutdown_code() -> None:
    model = runtime(BASE_FIXTURE, "cancel-never-terminal", shutdown_timeout=0.1)
    model.start()
    handle = model.generate(ModelRequest("pending"))
    try:
        with pytest.raises(ShutdownTimeout):
            model.shutdown()

        with pytest.raises(ShutdownTimeout):
            handle.wait(1.0)
        events = list(handle)
        failures = [event for event in events if isinstance(event, GenerationFailedEvent)]
        assert len(failures) == 1
        assert failures[0].code == "shutdown_timeout"
    finally:
        model.shutdown()


@pytest.mark.parametrize("attempt", range(3))
def test_malformed_frame_after_shutdown_ack_fails_closed(
    attempt: int,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    model = runtime(FIXTURE, "malformed-after-shutdown")
    original = runtime_module.parse_event

    def parse_after_exit(payload: bytes | str) -> runtime_module.GenerationEvent:
        if b'"failed"' in (payload.encode() if isinstance(payload, str) else payload):
            process = model._process  # pyright: ignore[reportPrivateUsage]
            assert process is not None
            assert process.wait(timeout=2.0) == 0
        return original(payload)

    monkeypatch.setattr(runtime_module, "parse_event", parse_after_exit)
    model.start()
    with pytest.raises(runtime_module.ProtocolViolation):
        model.shutdown()
    assert model.health().state == "failed"
    assert not model.ready
    with pytest.raises(runtime_module.ModelRuntimeError):
        model.generate(ModelRequest("must not reuse"))
    model.shutdown()


@pytest.mark.parametrize("error_type", [TypeError, RuntimeError, SystemExit])
def test_unexpected_reader_exception_after_ack_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
    error_type: type[BaseException],
) -> None:
    original = runtime_module.parse_event

    def parse_with_failure(payload: bytes | str) -> runtime_module.GenerationEvent:
        if b'"failed"' in (payload.encode() if isinstance(payload, str) else payload):
            raise error_type("synthetic reader failure")
        return original(payload)

    monkeypatch.setattr(runtime_module, "parse_event", parse_with_failure)
    model = runtime(FIXTURE, "malformed-after-shutdown")
    model.start()
    with pytest.raises(SidecarCrashed, match=error_type.__name__):
        model.shutdown()
    assert model.health().state == "failed"
    assert not model.ready
    model.shutdown()
