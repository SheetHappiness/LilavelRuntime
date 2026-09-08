"""Deterministic tests for coalescing and bounded Discord presentation."""

from __future__ import annotations

import asyncio
from typing import Any

from lilavel_discord_edge import (
    FAILED_MARKER,
    INTERRUPTED_MARKER,
    ReplyPresenter,
    split_discord_content,
)


class FakeClock:
    def __init__(self) -> None:
        self.value = 0.0

    def __call__(self) -> float:
        return self.value


class FakeMessage:
    def __init__(self, content: str) -> None:
        self.content = content
        self.deleted = False


class FakeSink:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    async def create(self, content: str) -> FakeMessage:
        self.calls.append(("create", content))
        return FakeMessage(content)

    async def edit(self, message: Any, content: str) -> FakeMessage:
        self.calls.append(("edit", content))
        message.content = content
        return message

    async def delete(self, message: Any) -> None:
        message.deleted = True


class BlockingEditSink(FakeSink):
    def __init__(self) -> None:
        super().__init__()
        self.edit_started = asyncio.Event()
        self.release_edit = asyncio.Event()
        self.active_edits = 0
        self.max_active_edits = 0

    async def edit(self, message: Any, content: str) -> FakeMessage:
        self.calls.append(("edit", content))
        self.active_edits += 1
        self.max_active_edits = max(self.max_active_edits, self.active_edits)
        self.edit_started.set()
        try:
            await self.release_edit.wait()
            message.content = content
            return message
        finally:
            self.active_edits -= 1


class FailingEditSink(FakeSink):
    async def edit(self, message: Any, content: str) -> FakeMessage:
        del message
        self.calls.append(("edit", content))
        raise RuntimeError("synthetic sink failure")


async def wait_until(check: Any) -> None:
    for _ in range(1000):
        if check():
            return
        await asyncio.sleep(0)
    raise AssertionError("condition did not become true")


def test_split_preserves_unicode_and_never_exceeds_limit() -> None:
    text = "é" * 2001 + "🙂"

    chunks = split_discord_content(text)

    assert "".join(chunks) == text
    assert len(chunks) == 2
    assert all(len(chunk) <= 2000 for chunk in chunks)


def test_bursty_deltas_are_coalesced_and_completion_flushes_once() -> None:
    async def scenario() -> None:
        clock = FakeClock()
        sink = FakeSink()
        presenter = ReplyPresenter(sink, edit_interval_s=1.0, clock=clock)

        presenter.append_delta("a")
        assert await presenter.flush()
        presenter.append_delta("b")
        presenter.append_delta("c")
        presenter.append_delta("d")
        assert not presenter.ready_to_flush()
        assert not await presenter.flush()

        clock.value = 1.0
        assert await presenter.flush()
        assert sink.calls == [("create", "a"), ("edit", "abcd")]

        await presenter.complete("abcd!")
        await presenter.complete("ignored")
        assert presenter.terminal_flush_count == 1
        assert presenter.finalized
        assert sink.calls == [("create", "a"), ("edit", "abcd"), ("edit", "abcd!")]

    asyncio.run(scenario())


def test_adaptive_pump_collapses_rapid_deltas_into_latest_snapshot() -> None:
    async def scenario() -> None:
        clock = FakeClock()
        sink = FakeSink()
        presenter = ReplyPresenter(sink, edit_interval_s=0.2, clock=clock)
        presenter.start()
        presenter.append_delta("a")
        await wait_until(lambda: sink.calls == [("create", "a")])

        clock.value = 0.2
        for delta in "bcdefghijklmnopqrstuvwxyz":
            presenter.append_delta(delta)
        await wait_until(lambda: sink.calls[-1] == ("edit", "abcdefghijklmnopqrstuvwxyz"))

        await presenter.complete("abcdefghijklmnopqrstuvwxyz")
        assert sink.calls == [
            ("create", "a"),
            ("edit", "abcdefghijklmnopqrstuvwxyz"),
        ]
        assert presenter.terminal_flush_count == 1
        await presenter.shutdown()

    asyncio.run(scenario())


def test_paced_pump_waits_for_interval_then_publishes_latest_snapshot() -> None:
    async def scenario() -> None:
        clock = FakeClock()
        sink = FakeSink()
        presenter = ReplyPresenter(sink, edit_interval_s=1.0, clock=clock)
        presenter.start()
        presenter.append_delta("a")
        await wait_until(lambda: sink.calls == [("create", "a")])

        presenter.append_delta("b")
        await asyncio.sleep(0)
        assert sink.calls == [("create", "a")]

        clock.value = 0.99
        presenter.append_delta("c")
        await asyncio.sleep(0)
        assert sink.calls == [("create", "a")]

        clock.value = 1.0
        presenter.append_delta("d")
        await wait_until(lambda: sink.calls[-1] == ("edit", "abcd"))
        assert sink.calls == [("create", "a"), ("edit", "abcd")]

        await presenter.complete("abcd")
        await presenter.shutdown()

    asyncio.run(scenario())


def test_adaptive_pump_is_single_flight_and_publishes_text_arriving_during_edit() -> None:
    async def scenario() -> None:
        clock = FakeClock()
        sink = BlockingEditSink()
        presenter = ReplyPresenter(sink, edit_interval_s=0.2, clock=clock)
        presenter.start()
        presenter.append_delta("a")
        await wait_until(lambda: sink.calls == [("create", "a")])

        clock.value = 0.3
        presenter.append_delta("b")
        await asyncio.wait_for(sink.edit_started.wait(), 1.0)
        assert sink.active_edits == 1

        presenter.append_delta("c")
        clock.value = 0.5
        sink.release_edit.set()
        await wait_until(lambda: sink.calls[-1] == ("edit", "abc"))

        assert sink.max_active_edits == 1
        assert presenter.published_chunks == ("abc",)
        await presenter.complete("abc")
        assert presenter.terminal_flush_count == 1
        await presenter.shutdown()

    asyncio.run(scenario())


def test_terminal_flush_waits_for_in_flight_edit_and_reconciles_latest_once() -> None:
    async def scenario() -> None:
        clock = FakeClock()
        sink = BlockingEditSink()
        presenter = ReplyPresenter(sink, edit_interval_s=0.2, clock=clock)
        presenter.start()
        presenter.append_delta("a")
        await wait_until(lambda: sink.calls == [("create", "a")])

        clock.value = 0.3
        presenter.append_delta("b")
        await asyncio.wait_for(sink.edit_started.wait(), 1.0)
        presenter.append_delta("c")
        completion = asyncio.create_task(presenter.complete("abc"))
        await asyncio.sleep(0)
        assert not completion.done()

        sink.release_edit.set()
        await completion

        assert sink.calls == [("create", "a"), ("edit", "ab"), ("edit", "abc")]
        assert presenter.terminal_flush_count == 1
        assert presenter.finalized
        await presenter.complete("ignored")
        assert sink.calls == [("create", "a"), ("edit", "ab"), ("edit", "abc")]
        await presenter.shutdown()

    asyncio.run(scenario())


def test_duplicate_snapshot_does_not_trigger_redundant_edit() -> None:
    async def scenario() -> None:
        clock = FakeClock()
        sink = FakeSink()
        presenter = ReplyPresenter(sink, edit_interval_s=0.2, clock=clock)

        presenter.append_delta("same")
        assert await presenter.flush()
        clock.value = 1.0
        presenter.replace_text("same")

        assert not presenter.ready_to_flush()
        assert not await presenter.flush()
        assert sink.calls == [("create", "same")]
        await presenter.shutdown()

    asyncio.run(scenario())


def test_shutdown_cancels_owned_pump_without_leaving_edit_in_flight() -> None:
    async def scenario() -> None:
        clock = FakeClock()
        sink = BlockingEditSink()
        presenter = ReplyPresenter(sink, edit_interval_s=0.0, clock=clock)
        presenter.start()
        presenter.append_delta("a")
        await wait_until(lambda: sink.calls == [("create", "a")])

        presenter.append_delta("b")
        await asyncio.wait_for(sink.edit_started.wait(), 1.0)
        await presenter.shutdown()

        assert sink.active_edits == 0
        assert not presenter.finalized

    asyncio.run(scenario())


def test_sink_failure_is_observable_and_owned_pump_can_be_settled() -> None:
    async def scenario() -> None:
        clock = FakeClock()
        sink = FailingEditSink()
        presenter = ReplyPresenter(sink, edit_interval_s=0.0, clock=clock)
        presenter.start()
        presenter.append_delta("a")
        await wait_until(lambda: sink.calls == [("create", "a")])

        presenter.append_delta("b")
        error = await presenter.wait_for_error()

        assert isinstance(error, RuntimeError)
        assert sink.calls == [("create", "a"), ("edit", "ab")]
        await presenter.shutdown()

    asyncio.run(scenario())


def test_long_response_creates_safe_continuation_messages() -> None:
    async def scenario() -> None:
        sink = FakeSink()
        presenter = ReplyPresenter(sink, edit_interval_s=0.0)
        text = "x" * 4001

        presenter.append_delta(text)
        await presenter.complete(text)

        assert [kind for kind, _ in sink.calls] == ["create", "create", "create"]
        assert all(len(content) <= 2000 for _, content in sink.calls)
        assert "".join(content for _, content in sink.calls) == text
        assert presenter.published_chunks == ("x" * 2000, "x" * 2000, "x")

    asyncio.run(scenario())


def test_terminal_markers_are_visible_and_idempotent() -> None:
    async def scenario() -> None:
        sink = FakeSink()
        presenter = ReplyPresenter(sink, edit_interval_s=0.0)
        presenter.append_delta("partial")
        await presenter.flush()
        await presenter.interrupted("partial")
        await presenter.failed("different")

        assert presenter.finalized
        assert presenter.terminal_flush_count == 1
        assert presenter.text == "partial" + INTERRUPTED_MARKER
        assert sink.calls[-1] == ("edit", "partial" + INTERRUPTED_MARKER)
        assert FAILED_MARKER not in presenter.text

    asyncio.run(scenario())
