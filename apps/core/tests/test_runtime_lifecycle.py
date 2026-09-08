"""Deterministic lifecycle-boundary regressions for the Core runtime."""

from __future__ import annotations

import sys
import threading
import time
from math import inf, nan
from pathlib import Path

import pytest

from lilavel_core import (
    CancellationTimeout,
    EventQueueOverflow,
    GenerationAccepted,
    GenerationCancelled,
    GenerationFailedEvent,
    ModelRequest,
    ModelRuntime,
    ProtocolViolation,
    RuntimeShuttingDown,
    SidecarCrashed,
)
from lilavel_core import GenerationFailed as RuntimeGenerationFailed
from lilavel_core.sidecar_protocol import MAX_PROMPT_BYTES

FIXTURE = Path(__file__).parent / "fixtures" / "fake_sidecar.py"


def runtime(
    mode: str = "normal",
    *,
    startup_timeout: float = 30.0,
    cancellation_timeout: float = 5.0,
    shutdown_timeout: float = 5.0,
    event_queue_size: int = 128,
) -> ModelRuntime:
    return ModelRuntime(
        command=(sys.executable, str(FIXTURE), mode),
        sidecar_dir=Path(__file__).parents[1],
        startup_timeout=startup_timeout,
        cancellation_timeout=cancellation_timeout,
        shutdown_timeout=shutdown_timeout,
        event_queue_size=event_queue_size,
    )


def wait_for_state(model: ModelRuntime, state: str) -> None:
    deadline = time.monotonic() + 2.0
    while model.health().state != state:
        if time.monotonic() >= deadline:
            raise AssertionError(f"runtime did not reach {state!r}: {model.health()!r}")
        time.sleep(0.005)


def test_accepted_after_cancel_is_legal_and_terminal_once() -> None:
    model = runtime("cancel-before-accepted")
    model.start()
    try:
        handle = model.generate(ModelRequest("cancel-before-accepted"))
        assert model.cancel(handle.generation_id)

        assert handle.wait(2.0).status == "cancelled"
        events = list(handle)
        assert [type(event) for event in events] == [GenerationAccepted, GenerationCancelled]
        assert list(handle) == []
        assert model.health().state == "ready"
    finally:
        model.shutdown()


def test_duplicate_accepted_fails_runtime_without_double_settlement() -> None:
    model = runtime("duplicate-accepted")
    model.start()
    try:
        handle = model.generate(ModelRequest("duplicate"))
        with pytest.raises(ProtocolViolation):
            handle.wait(2.0)

        events = list(handle)
        assert sum(isinstance(event, GenerationFailedEvent) for event in events) == 1
        assert model.health().state == "failed"
    finally:
        model.shutdown()


def test_shutdown_closes_admission_before_teardown_and_settles_pending() -> None:
    model = runtime("pending-shutdown", shutdown_timeout=1.0)
    model.start()
    handle = model.generate(ModelRequest("pending"))
    shutdown_errors: list[BaseException] = []

    def close() -> None:
        try:
            model.shutdown()
        except BaseException as error:  # pragma: no cover - assertion below reports it
            shutdown_errors.append(error)

    thread = threading.Thread(target=close)
    thread.start()
    wait_for_state(model, "shutting_down")
    with pytest.raises(RuntimeShuttingDown):
        model.generate(ModelRequest("admission-closed"))
    thread.join(2.0)

    assert not thread.is_alive()
    assert len(shutdown_errors) == 1
    assert isinstance(shutdown_errors[0], SidecarCrashed)
    with pytest.raises(SidecarCrashed):
        handle.wait(1.0)
    assert model.health().state == "failed"


def test_shutdown_during_startup_wakes_start_waiter_without_reopening_admission() -> None:
    model = runtime("delayed-ready", startup_timeout=1.0, shutdown_timeout=1.0)
    start_errors: list[BaseException] = []

    def start() -> None:
        try:
            model.start()
        except BaseException as error:  # pragma: no cover - assertion below reports it
            start_errors.append(error)

    thread = threading.Thread(target=start)
    thread.start()
    wait_for_state(model, "starting")
    model.shutdown()
    thread.join(1.0)

    assert not thread.is_alive()
    assert len(start_errors) == 1
    assert isinstance(start_errors[0], RuntimeShuttingDown)
    assert model.health().state == "closed"


def test_shutdown_nonzero_child_exit_is_not_reported_as_clean() -> None:
    model = runtime("shutdown-crash", shutdown_timeout=1.0)
    model.start()

    with pytest.raises(SidecarCrashed):
        model.shutdown()

    assert model.health().state == "failed"


def test_shutdown_without_ack_is_not_reported_as_clean() -> None:
    model = runtime("shutdown-no-ack", shutdown_timeout=1.0)
    model.start()

    with pytest.raises(SidecarCrashed):
        model.shutdown()

    assert model.health().state == "failed"


def test_cancel_watchdog_settles_handle_and_poison_runtime() -> None:
    model = runtime("cancel-never-terminal", cancellation_timeout=0.1, shutdown_timeout=0.5)
    model.start()
    handle = model.generate(ModelRequest("cancel"))
    assert model.cancel(handle.generation_id)

    with pytest.raises(CancellationTimeout):
        handle.wait(1.0)
    assert model.health().state == "failed"
    model.shutdown()


def test_cleanup_timeout_poison_does_not_return_runtime_to_ready() -> None:
    model = runtime("cleanup-timeout")
    model.start()
    handle = model.generate(ModelRequest("cancel"))
    assert model.cancel(handle.generation_id)

    with pytest.raises(RuntimeGenerationFailed) as raised:
        handle.wait(1.0)
    assert raised.value.code == "cleanup_timeout"
    assert model.health().state == "failed"
    model.shutdown()


def test_wait_only_consumer_overflow_is_bounded_and_retains_terminal_failure() -> None:
    model = runtime("burst", event_queue_size=2)
    model.start()
    handle = model.generate(ModelRequest("burst"))

    with pytest.raises(EventQueueOverflow):
        handle.wait(2.0)
    events = list(handle)
    assert len(events) == 3
    assert isinstance(events[-1], GenerationFailedEvent)
    assert model.health().state == "failed"
    model.shutdown()


def test_request_serialization_failure_does_not_strand_admission() -> None:
    model = runtime()
    model.start()
    try:
        invalid = ModelRequest("\x00" * MAX_PROMPT_BYTES)
        with pytest.raises(ProtocolViolation):
            model.generate(invalid)
        assert model.health().state == "ready"

        recovery = model.generate(ModelRequest("recovery"))
        assert recovery.wait(2.0).text == "recovered"
    finally:
        model.shutdown()


def test_concurrent_shutdown_calls_share_one_teardown() -> None:
    model = runtime()
    model.start()
    errors: list[BaseException] = []

    def close() -> None:
        try:
            model.shutdown()
        except BaseException as error:  # pragma: no cover - assertion below reports it
            errors.append(error)

    threads = [threading.Thread(target=close) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(2.0)

    assert all(not thread.is_alive() for thread in threads)
    assert errors == []
    assert model.health().state == "closed"


@pytest.mark.parametrize("timeout", [0.0, -1.0, inf, nan])
def test_runtime_timeouts_must_be_finite_and_positive(timeout: float) -> None:
    with pytest.raises(ValueError):
        ModelRuntime(
            startup_timeout=timeout,
            command=(sys.executable, str(FIXTURE), "normal"),
            sidecar_dir=Path(__file__).parents[1],
        )
