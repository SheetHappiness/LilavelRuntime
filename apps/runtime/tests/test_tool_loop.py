"""P4-B deterministic end-to-end tool-loop and joined-settlement scenarios."""

from __future__ import annotations

import sys
import threading
import time
from collections.abc import Callable
from pathlib import Path

import pytest
from lilavel_contracts import ToolCall, ToolEffect, ToolResult, ToolResultStatus, ToolSpec
from lilavel_core import (
    ConversationCancelled,
    ConversationCompleted,
    ConversationCore,
    ConversationFailed,
    ConversationTextDelta,
    GenerationFailed,
    ModelRequest,
    ModelRuntimeV3,
    ProtocolViolation,
    RuntimeNotReady,
    TextDelta,
    ToolBatchCorrelation,
    ToolExecutorUncontained,
)

from lilavel_runtime import DeterministicToolSessionFactory

FIXTURE = Path(__file__).parents[2] / "core" / "tests" / "fixtures" / "fake_v3_sidecar.py"
SPEC = ToolSpec(
    "fake.echo",
    "Return a deterministic fixture value.",
    {"type": "object", "properties": {"value": {"type": "string"}}},
)

type Executor = Callable[[ToolBatchCorrelation, ToolCall, threading.Event], ToolResult]


def ok_executor(
    correlation: ToolBatchCorrelation, call: ToolCall, cancelled: threading.Event
) -> ToolResult:
    del correlation, cancelled
    return ToolResult(call.call_id, ToolResultStatus.OK, {"fixture": "ok"})


def factory(
    executor: Executor = ok_executor,
    *,
    authorize: Callable[[ToolBatchCorrelation, ToolCall], bool] | None = None,
    validate: Callable[[ToolBatchCorrelation, ToolCall], bool] | None = None,
    executor_deadline: float = 1.0,
    containment_deadline: float = 0.2,
) -> DeterministicToolSessionFactory:
    return DeterministicToolSessionFactory(
        (SPEC,),
        {SPEC.name: executor},
        authorize=authorize,
        validate=validate,
        executor_deadline=executor_deadline,
        containment_deadline=containment_deadline,
    )


def runtime(
    mode: str,
    session_factory: DeterministicToolSessionFactory | None,
    *,
    tool_settlement_deadline: float = 0.5,
) -> ModelRuntimeV3:
    return ModelRuntimeV3(
        command=(sys.executable, str(FIXTURE), mode),
        sidecar_dir=FIXTURE.parent,
        tool_session_factory=session_factory,
        startup_timeout=2.0,
        shutdown_timeout=2.0,
        cancellation_timeout=1.0,
        tool_result_wait_deadline=1.0,
        tool_generation_deadline=3.0,
        tool_settlement_deadline=tool_settlement_deadline,
    )


def wait_for(predicate: Callable[[], bool], timeout: float = 2.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.005)
    raise AssertionError("condition did not become true")


def terminal_events(run: object) -> list[object]:
    return [
        event
        for event in run  # type: ignore[union-attr]
        if isinstance(event, (ConversationCompleted, ConversationCancelled, ConversationFailed))
    ]


def close(model: ModelRuntimeV3) -> None:
    try:
        model.shutdown()
    except Exception:
        model.shutdown()


def test_a_no_tool_v3_generation_regression() -> None:
    model = runtime("no-tool", None)
    model.start()
    try:
        handle = model.generate(ModelRequest("hello"))
        assert handle.wait(2.0).text == "streamed"
        events = list(handle)
        assert [event.delta for event in events if isinstance(event, TextDelta)] == ["streamed"]
        assert model.ready
    finally:
        close(model)


def test_b_one_fake_tool_round_completes_and_history_excludes_tool_transcript() -> None:
    sessions = factory()
    model = runtime("one-tool", sessions)
    model.start()
    core = ConversationCore(model, scope_id="scope-b")
    try:
        run = core.start_turn("use the fake tool")
        assert run.wait(2.0).status == "completed"
        terminals = terminal_events(run)
        assert len(terminals) == 1
        assert isinstance(terminals[0], ConversationCompleted)
        assert terminals[0].text == "after"
        assert [(message.role, message.text) for message in core.history] == [
            ("user", "use the fake tool"),
            ("assistant", "after"),
        ]
        assert all("tool" not in record.provenance_kind for record in core.evidence_records)
        assert sessions.sessions[0].settlement == "settled"
        assert model.tool_evidence()[-1].settlement == "settled"
        assert model.ready
    finally:
        close(model)


def test_c_multiple_sequential_rounds_keep_one_generation_identity() -> None:
    sessions = factory()
    model = runtime("multi-round", sessions)
    model.start()
    try:
        handle = model.generate_for_run(ModelRequest("rounds"), scope_id="s", logical_run_id="r")
        assert handle.wait(2.0).text == "middle after"
        requested = [record for record in model.tool_evidence() if record.kind == "tool_requested"]
        assert [(record.generation_id, record.epoch, record.round) for record in requested] == [
            (handle.generation_id, handle.epoch, 1),
            (handle.generation_id, handle.epoch, 2),
        ]
        assert len(sessions.sessions) == 1
    finally:
        close(model)


def test_d_multi_call_batch_executes_sequentially_in_provider_order() -> None:
    order: list[str] = []

    def record_order(
        correlation: ToolBatchCorrelation, call: ToolCall, cancelled: threading.Event
    ) -> ToolResult:
        del correlation, cancelled
        order.append(call.call_id)
        return ToolResult(call.call_id, ToolResultStatus.OK, None)

    model = runtime("multi-call", factory(record_order))
    model.start()
    try:
        assert model.generate(ModelRequest("batch")).wait(2.0).status == "completed"
        assert order == ["call-1", "call-2"]
    finally:
        close(model)


def test_e_pre_tool_and_continuation_text_are_one_append_only_candidate() -> None:
    model = runtime("pre-tool-text", factory())
    model.start()
    core = ConversationCore(model)
    try:
        run = core.start_turn("partial")
        assert run.wait(2.0).text == "before after"
        events = list(run)
        assert [event.delta for event in events if isinstance(event, ConversationTextDelta)] == [
            "before ",
            "after",
        ]
        assert len([event for event in events if isinstance(event, ConversationCompleted)]) == 1
    finally:
        close(model)


@pytest.mark.parametrize("decision", ["denied", "invalid", "unavailable"])
def test_f_non_ok_fake_result_is_consumed_and_model_explanation_can_complete(
    decision: str,
) -> None:
    if decision == "denied":
        sessions = factory(authorize=lambda correlation, call: False)
    elif decision == "invalid":
        sessions = factory(validate=lambda correlation, call: False)
    else:
        sessions = DeterministicToolSessionFactory((SPEC,), {})
    model = runtime("one-tool", sessions)
    model.start()
    try:
        assert model.generate(ModelRequest("explain")).wait(2.0).text == "explained"
        evidence = sessions.sessions[0].evidence()
        assert evidence[-1].status_code in {"denied", "invalid", "unavailable"}
        assert model.ready
    finally:
        close(model)


def test_g_executor_exception_is_contained_as_failed_tool_result() -> None:
    def fail(
        correlation: ToolBatchCorrelation, call: ToolCall, cancelled: threading.Event
    ) -> ToolResult:
        del correlation, call, cancelled
        raise RuntimeError("raw exception must not escape")

    sessions = factory(fail)
    model = runtime("one-tool", sessions)
    model.start()
    try:
        assert (
            model.generate(
                ModelRequest("fail"),
            )
            .wait(2.0)
            .text
            == "explained"
        )
        assert sessions.sessions[0].evidence()[-1].status_code == "failed"
        assert "raw exception" not in repr(sessions.sessions[0].evidence())
    finally:
        close(model)


def test_h_executor_timeout_with_confirmed_settlement_returns_timed_out() -> None:
    def settle_on_cancel(
        correlation: ToolBatchCorrelation, call: ToolCall, cancelled: threading.Event
    ) -> ToolResult:
        del correlation
        cancelled.wait(1.0)
        return ToolResult(call.call_id, ToolResultStatus.OK, None)

    sessions = factory(settle_on_cancel, executor_deadline=0.03, containment_deadline=0.2)
    model = runtime("one-tool", sessions)
    model.start()
    try:
        assert model.generate(ModelRequest("timeout")).wait(2.0).text == "explained"
        assert sessions.sessions[0].evidence()[-1].status_code == "timed_out"
        assert sessions.sessions[0].wait_settled(0)
        assert model.ready
    finally:
        close(model)


def test_i_cancellation_before_tool_dispatch_has_no_executor_activity() -> None:
    sessions = factory()
    model = runtime("wait-before-tool", sessions)
    model.start()
    try:
        handle = model.generate(ModelRequest("cancel"))
        wait_for(
            lambda: any(record.protocol_event == "accepted" for record in model.physical_evidence())
        )
        assert model.cancel(handle.generation_id)
        assert handle.wait(2.0).status == "cancelled"
        assert sessions.sessions[0].evidence() == ()
        assert sessions.sessions[0].settlement == "cancelled"
        assert model.ready
    finally:
        close(model)


def test_j_cancellation_while_executor_runs_waits_for_both_legs() -> None:
    started = threading.Event()
    release = threading.Event()

    def blocking(
        correlation: ToolBatchCorrelation, call: ToolCall, cancelled: threading.Event
    ) -> ToolResult:
        del correlation
        started.set()
        cancelled.wait(1.0)
        release.wait(1.0)
        return ToolResult(call.call_id, ToolResultStatus.OK, None)

    sessions = factory(blocking)
    model = runtime("one-tool", sessions)
    model.start()
    try:
        handle = model.generate(ModelRequest("cancel running"))
        assert started.wait(1.0)
        assert model.cancel(handle.generation_id)
        with pytest.raises(TimeoutError):
            handle.wait(0.03)
        release.set()
        assert handle.wait(2.0).status == "cancelled"
        assert sessions.sessions[0].wait_settled(0)
        assert model.ready
    finally:
        release.set()
        close(model)


def test_k_cancellation_after_result_submission_fences_final_continuation() -> None:
    model = runtime("wait-after-results", factory())
    model.start()
    try:
        handle = model.generate(ModelRequest("cancel continuation"))
        wait_for(lambda: any(record.kind == "result_consumed" for record in model.tool_evidence()))
        assert model.cancel(handle.generation_id)
        assert handle.wait(2.0).status == "cancelled"
        assert handle.text == "continuing"
        assert model.ready
    finally:
        close(model)


def test_l_supersession_while_executor_runs_joins_before_successor_generation() -> None:
    first_started = threading.Event()
    invocations = 0
    lock = threading.Lock()

    def first_blocks(
        correlation: ToolBatchCorrelation, call: ToolCall, cancelled: threading.Event
    ) -> ToolResult:
        nonlocal invocations
        del correlation
        with lock:
            invocations += 1
            number = invocations
        if number == 1:
            first_started.set()
            cancelled.wait(1.0)
        return ToolResult(call.call_id, ToolResultStatus.OK, None)

    sessions = factory(first_blocks)
    model = runtime("one-tool", sessions)
    model.start()
    core = ConversationCore(model)
    try:
        first = core.start_turn("first")
        assert first_started.wait(1.0)
        second = core.start_turn("second", supersede=True)
        assert first.wait(2.0).status == "superseded"
        assert second.wait(2.0).status == "completed"
        assert len(sessions.sessions) == 2
        tool_evidence = model.tool_evidence()
        first_join_index = next(
            index
            for index, record in enumerate(tool_evidence)
            if record.kind == "joined_settlement" and record.settlement == "cancelled"
        )
        second_request_index = next(
            index
            for index, record in enumerate(tool_evidence)
            if index > first_join_index and record.kind == "tool_requested"
        )
        assert first_join_index < second_request_index
        assert [(message.role, message.text) for message in core.history] == [
            ("user", "first"),
            ("user", "second"),
            ("assistant", "after"),
        ]
    finally:
        close(model)


def test_m_late_result_from_superseded_run_is_discarded_before_reuse() -> None:
    started = threading.Event()
    invocation = 0
    invocation_lock = threading.Lock()

    def late(
        correlation: ToolBatchCorrelation, call: ToolCall, cancelled: threading.Event
    ) -> ToolResult:
        nonlocal invocation
        del correlation
        with invocation_lock:
            invocation += 1
            current = invocation
        if current == 1:
            started.set()
            cancelled.wait(1.0)
            time.sleep(0.03)
        return ToolResult(call.call_id, ToolResultStatus.OK, None)

    sessions = factory(late)
    model = runtime("one-tool", sessions)
    model.start()
    try:
        first = model.generate_for_run(ModelRequest("first"), scope_id="s", logical_run_id="r1")
        assert started.wait(1.0)
        assert model.cancel(first.generation_id)
        assert first.wait(2.0).status == "cancelled"
        discarded = [
            record for record in model.tool_evidence() if record.kind == "result_discarded"
        ]
        assert len(discarded) == 1
        second = model.generate_for_run(ModelRequest("second"), scope_id="s", logical_run_id="r2")
        assert second.generation_id != first.generation_id
        assert second.wait(2.0).status == "completed"
    finally:
        close(model)


def test_n_shutdown_while_tool_execution_is_outstanding_joins_executor() -> None:
    started = threading.Event()

    def stop_on_cancel(
        correlation: ToolBatchCorrelation, call: ToolCall, cancelled: threading.Event
    ) -> ToolResult:
        del correlation
        started.set()
        cancelled.wait(1.0)
        return ToolResult(call.call_id, ToolResultStatus.OK, None)

    sessions = factory(stop_on_cancel)
    model = runtime("one-tool", sessions)
    model.start()
    handle = model.generate(ModelRequest("shutdown"))
    assert started.wait(1.0)
    model.shutdown()
    assert handle.wait(1.0).status == "cancelled"
    assert sessions.sessions[0].wait_settled(0)
    assert model.health().state == "closed"


def test_shutdown_waits_for_delayed_executor_settlement_after_provider_exit() -> None:
    started = threading.Event()

    def settle_after_provider_exit(
        correlation: ToolBatchCorrelation, call: ToolCall, cancelled: threading.Event
    ) -> ToolResult:
        del correlation
        started.set()
        cancelled.wait(1.0)
        time.sleep(0.05)
        return ToolResult(call.call_id, ToolResultStatus.OK, None)

    sessions = factory(settle_after_provider_exit)
    model = runtime("one-tool", sessions)
    model.start()
    handle = model.generate(ModelRequest("shutdown joined"))
    assert started.wait(1.0)

    model.shutdown()

    assert handle.wait(1.0).status == "cancelled"
    assert sessions.sessions[0].wait_settled(0)
    assert model.health().state == "closed"


def test_shutdown_with_uncontainable_executor_fails_runtime_closed() -> None:
    started = threading.Event()
    never = threading.Event()

    def ignores_shutdown(
        correlation: ToolBatchCorrelation, call: ToolCall, cancelled: threading.Event
    ) -> ToolResult:
        del correlation, cancelled
        started.set()
        never.wait()
        return ToolResult(call.call_id, ToolResultStatus.OK, None)

    sessions = factory(
        ignores_shutdown,
        executor_deadline=5.0,
        containment_deadline=0.02,
    )
    model = runtime("one-tool", sessions, tool_settlement_deadline=0.05)
    model.start()
    handle = model.generate(ModelRequest("shutdown uncontained"))
    assert started.wait(1.0)

    with pytest.raises(ToolExecutorUncontained):
        model.shutdown()
    with pytest.raises(ToolExecutorUncontained):
        handle.wait(1.0)
    assert sessions.sessions[0].settlement == "uncontained"
    assert model.health().state == "failed"
    assert (
        len(
            [record for record in model.physical_evidence() if record.kind == "generation_terminal"]
        )
        == 1
    )


@pytest.mark.parametrize("mode", ["failed-while-tool", "crash-while-tool"])
def test_o_sidecar_failure_while_executor_outstanding_settles_application_first(
    mode: str,
) -> None:
    started = threading.Event()

    def stop_on_cancel(
        correlation: ToolBatchCorrelation, call: ToolCall, cancelled: threading.Event
    ) -> ToolResult:
        del correlation
        started.set()
        cancelled.wait(1.0)
        return ToolResult(call.call_id, ToolResultStatus.OK, None)

    sessions = factory(stop_on_cancel)
    model = runtime(mode, sessions)
    model.start()
    handle = model.generate(ModelRequest("failure"))
    assert started.wait(1.0)
    if mode == "failed-while-tool":
        with pytest.raises(GenerationFailed):
            handle.wait(2.0)
    else:
        with pytest.raises(Exception, match="sidecar exited"):
            handle.wait(2.0)
    assert sessions.sessions[0].wait_settled(0)
    assert (
        len(
            [record for record in model.physical_evidence() if record.kind == "generation_terminal"]
        )
        == 1
    )
    if mode == "crash-while-tool":
        assert model.health().state == "failed"
        with pytest.raises(RuntimeNotReady):
            model.generate(ModelRequest("blocked"))
    close(model)


@pytest.mark.parametrize(
    "mode",
    ["malformed", "wrong-round", "second-pending-batch", "completion-while-pending"],
)
def test_p_malformed_or_illegal_v3_lifecycle_fails_closed(mode: str) -> None:
    model = runtime(mode, factory())
    model.start()
    handle = model.generate(ModelRequest("illegal"))
    with pytest.raises(ProtocolViolation):
        handle.wait(2.0)
    assert model.health().state == "failed"
    assert (
        len(
            [record for record in model.physical_evidence() if record.kind == "generation_terminal"]
        )
        == 1
    )
    close(model)


def test_uncontainable_executor_poison_is_terminal_and_blocks_successor_admission() -> None:
    never = threading.Event()

    def ignores_cancel(
        correlation: ToolBatchCorrelation, call: ToolCall, cancelled: threading.Event
    ) -> ToolResult:
        del correlation, cancelled
        never.wait()
        return ToolResult(call.call_id, ToolResultStatus.OK, None, effect=ToolEffect.NONE)

    sessions = factory(ignores_cancel, executor_deadline=0.02, containment_deadline=0.02)
    model = runtime("one-tool", sessions, tool_settlement_deadline=0.05)
    model.start()
    handle = model.generate(ModelRequest("uncontained"))
    with pytest.raises(ToolExecutorUncontained):
        handle.wait(2.0)
    assert sessions.sessions[0].settlement == "uncontained"
    assert model.health().state == "failed"
    with pytest.raises(RuntimeNotReady):
        model.generate(ModelRequest("must not admit"))
    assert (
        len(
            [record for record in model.physical_evidence() if record.kind == "generation_terminal"]
        )
        == 1
    )
    close(model)
