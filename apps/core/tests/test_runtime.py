"""Observable lifecycle and stale-event tests for ModelRuntime."""

import sys
from pathlib import Path

import pytest

from lilavel_core import (
    GenerationAccepted,
    GenerationCancelled,
    GenerationCompleted,
    GenerationFailed,
    GenerationFailedEvent,
    ModelRequest,
    ModelRuntime,
    ProtocolViolation,
    RuntimeNotReady,
    RuntimeStartTimeout,
    SidecarCrashed,
    TextDelta,
)
from lilavel_core.sidecar_protocol import MAX_PROMPT_BYTES

FIXTURE = Path(__file__).parent / "fixtures" / "fake_sidecar.py"


def runtime(
    mode: str = "normal",
    *,
    startup_timeout: float = 30.0,
    write_timeout: float = 5.0,
    physical_evidence_capacity: int = 256,
) -> ModelRuntime:
    return ModelRuntime(
        command=(sys.executable, str(FIXTURE), mode),
        sidecar_dir=Path(__file__).parents[1],
        startup_timeout=startup_timeout,
        write_timeout=write_timeout,
        physical_evidence_capacity=physical_evidence_capacity,
    )


def test_start_streams_events_and_shuts_down_cleanly() -> None:
    model = runtime()
    model.start()
    try:
        assert model.ready
        assert model.health().state == "ready"
        handle = model.generate(ModelRequest("stream"))
        result = handle.wait(2.0)
        events = list(handle)

        assert result.text == "streamed"
        assert [type(event) for event in events] == [
            GenerationAccepted,
            TextDelta,
            GenerationCompleted,
        ]
        assert model.health().state == "ready"
    finally:
        model.shutdown()

    assert model.health().state == "closed"


def test_physical_evidence_correlates_protocol_activity_without_raw_output() -> None:
    model = runtime()
    model.start()
    try:
        handle = model.generate(ModelRequest("stream"))
        assert handle.wait(2.0).status == "completed"
        list(handle)

        evidence = model.physical_evidence()
        assert all(record.generation_id == handle.generation_id for record in evidence)
        assert all(record.epoch == handle.epoch for record in evidence)
        assert [(record.kind, record.protocol_event, record.result) for record in evidence] == [
            ("generation_created", None, None),
            ("protocol_event", "accepted", None),
            ("protocol_event", "text_delta", None),
            ("protocol_event", "completed", None),
            ("generation_terminal", None, "completed"),
        ]
        delta = next(record for record in evidence if record.protocol_event == "text_delta")
        assert delta.event_count == 1
        assert delta.event_bytes == len(b"streamed")
        assert "streamed" not in repr(evidence)
        assert "provider" not in repr(evidence)
        assert "session" not in repr(evidence)
    finally:
        model.shutdown()


def test_cancel_waits_for_terminal_event_and_recovers_in_same_process() -> None:
    model = runtime("supersede-race")
    model.start()
    try:
        cancelled = model.generate(ModelRequest("cancel"))
        stream = iter(cancelled)
        assert isinstance(next(stream), GenerationAccepted)
        assert isinstance(next(stream), TextDelta)
        assert model.cancel(cancelled.generation_id)
        assert model.cancel(cancelled.generation_id)
        assert cancelled.wait(2.0).status == "cancelled"
        remaining = list(stream)
        assert any(isinstance(event, GenerationCancelled) for event in remaining)
        assert not any(
            isinstance(event, TextDelta) and event.delta == "STALE_LATE_DELTA"
            for event in remaining
        )
        recovery = model.generate(ModelRequest("recovery"))
        assert recovery.wait(2.0).text == "recovered"
        recovery_events = list(recovery)
        assert [event.delta for event in recovery_events if isinstance(event, TextDelta)] == [
            "recovered"
        ]
    finally:
        model.shutdown()


def test_physical_evidence_correlates_cancellation_to_sidecar_terminal() -> None:
    model = runtime("supersede-race")
    model.start()
    try:
        cancelled = model.generate(ModelRequest("cancel"))
        stream = iter(cancelled)
        assert isinstance(next(stream), GenerationAccepted)
        assert isinstance(next(stream), TextDelta)
        assert model.cancel(cancelled.generation_id)
        assert cancelled.wait(2.0).status == "cancelled"
        list(stream)

        evidence = model.physical_evidence()
        protocol_events = [
            record.protocol_event for record in evidence if record.kind == "protocol_event"
        ]
        assert protocol_events == ["accepted", "text_delta", "cancelled"]
        terminal = evidence[-1]
        assert terminal.kind == "generation_terminal"
        assert terminal.result == "cancelled"
        assert terminal.generation_id == cancelled.generation_id
        assert terminal.epoch == cancelled.epoch
    finally:
        model.shutdown()


def test_physical_evidence_correlates_sidecar_failure_code() -> None:
    model = runtime("cleanup-timeout")
    model.start()
    try:
        failed = model.generate(ModelRequest("cancel"))
        stream = iter(failed)
        assert isinstance(next(stream), GenerationAccepted)
        assert isinstance(next(stream), TextDelta)
        assert model.cancel(failed.generation_id)
        with pytest.raises(GenerationFailed):
            failed.wait(2.0)
        assert any(isinstance(event, GenerationFailedEvent) for event in stream)

        evidence = model.physical_evidence()
        failure_event = next(record for record in evidence if record.protocol_event == "failed")
        assert failure_event.generation_id == failed.generation_id
        assert failure_event.epoch == failed.epoch
        assert failure_event.failure_code == "cleanup_timeout"
        terminal = evidence[-1]
        assert terminal.kind == "generation_terminal"
        assert terminal.result == "failed"
        assert terminal.failure_code == "cleanup_timeout"
    finally:
        model.shutdown()


def test_unexpected_sidecar_death_fails_pending_generation() -> None:
    model = runtime("crash-after-accepted")
    model.start()
    handle = model.generate(ModelRequest("crash"))

    with pytest.raises(SidecarCrashed):
        handle.wait(2.0)
    assert model.health().state == "failed"
    with pytest.raises(RuntimeNotReady):
        model.generate(ModelRequest("not-reusable"))

    evidence = model.physical_evidence()
    assert [record.protocol_event for record in evidence if record.kind == "protocol_event"] == [
        "accepted"
    ]
    terminal = evidence[-1]
    assert terminal.kind == "generation_terminal"
    assert terminal.result == "failed"
    assert terminal.failure_code == "sidecar_crashed"
    assert terminal.generation_id == handle.generation_id
    assert terminal.epoch == handle.epoch


def test_physical_evidence_is_bounded_and_snapshot_isolated() -> None:
    model = runtime(physical_evidence_capacity=5)
    model.start()
    try:
        first = model.generate(ModelRequest("stream"))
        assert first.wait(2.0).status == "completed"
        list(first)
        second = model.generate(ModelRequest("stream"))
        assert second.wait(2.0).status == "completed"
        list(second)

        snapshot = model.physical_evidence()
        assert len(snapshot) == 5
        assert all(record.generation_id == second.generation_id for record in snapshot)
        object.__setattr__(snapshot[0], "kind", "tampered")
        assert model.physical_evidence()[0].kind == "generation_created"
    finally:
        model.shutdown()


def test_malformed_sidecar_output_fails_closed() -> None:
    model = runtime("malformed-after-accepted")
    model.start()
    handle = model.generate(ModelRequest("malformed"))

    with pytest.raises(ProtocolViolation):
        handle.wait(2.0)
    assert model.health().state == "failed"


def test_truncated_shutdown_frame_is_rejected_at_eof() -> None:
    """A partial frame during teardown remains a protocol failure."""

    model = runtime("shutdown-truncated")
    model.start()

    with pytest.raises(ProtocolViolation, match="invalid sidecar protocol: malformed"):
        model.shutdown()

    assert model.health().state == "failed"
    # Failed runtimes retain an idempotent, non-raising cleanup path.
    model.shutdown()


def test_startup_readiness_is_bounded() -> None:
    model = runtime("no-ready", startup_timeout=0.1)

    with pytest.raises(RuntimeStartTimeout):
        model.start()
    assert model.health().state == "failed"


def test_command_write_is_bounded_when_sidecar_stops_reading() -> None:
    model = runtime("no-read-after-ready", write_timeout=0.1)
    model.start()
    try:
        with pytest.raises(SidecarCrashed):
            model.generate(ModelRequest("x" * MAX_PROMPT_BYTES))
        assert model.health().state == "failed"
    finally:
        model.shutdown()
