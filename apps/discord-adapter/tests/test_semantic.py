"""Deterministic coverage for the opt-in semantic presentation policy."""

from __future__ import annotations

import asyncio
import json
from typing import Any, cast

from lilavel_discord_edge import (
    FAILED_MARKER,
    INTERRUPTED_MARKER,
    DiscordDiagnostics,
    ReplyPresenter,
)
from lilavel_discord_edge.semantic import (
    DEFAULT_SEMANTIC_MAX_TAIL_CHARS,
    diagnostic_boundary_class,
    repair_markdown_preview,
    select_semantic_prefix,
    split_semantic_content,
)


class FakeMessage:
    def __init__(self, content: str) -> None:
        self.content = content
        self.deleted = False


class FakeSink:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []
        self.messages: list[FakeMessage] = []

    async def create(self, content: str) -> FakeMessage:
        self.calls.append(("create", content))
        message = FakeMessage(content)
        self.messages.append(message)
        return message

    async def edit(self, message: Any, content: str) -> FakeMessage:
        self.calls.append(("edit", content))
        message.content = content
        return message

    async def delete(self, message: Any) -> None:
        self.calls.append(("delete", ""))
        message.deleted = True
        self.messages.remove(message)


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


class FakeClock:
    def __init__(self) -> None:
        self.value = 0.0

    def __call__(self) -> float:
        return self.value


def test_semantic_mode_is_opt_in_and_raw_mode_keeps_latest_snapshot_behavior() -> None:
    async def scenario() -> None:
        sink = FakeSink()
        presenter = ReplyPresenter(sink, edit_interval_s=0.0)
        presenter.append_delta("seed")
        assert await presenter.flush()
        presenter.append_delta(" partial")
        assert await presenter.flush()

        assert sink.calls == [("create", "seed"), ("edit", "seed partial")]

    asyncio.run(scenario())


def test_semantic_selection_avoids_mid_word_endings() -> None:
    selection = select_semantic_prefix(
        "visible partial next",
        "visible",
        max_tail_chars=DEFAULT_SEMANTIC_MAX_TAIL_CHARS,
    )

    assert selection is not None
    assert selection.prefix == "visible partial"
    assert selection.boundary == "word"
    assert not selection.prefix.endswith("partia")


def test_boundary_hierarchy_prefers_stronger_boundaries_inside_the_window() -> None:
    cases = (
        ("seed\nline. clause, word", "newline", "seed\n"),
        ("seed line. clause, word", "sentence", "seed line."),
        ("seed line, word", "clause", "seed line,"),
        ("seed line word", "word", "seed line"),
    )

    for text, boundary, prefix in cases:
        selection = select_semantic_prefix(text, "seed", max_tail_chars=80)
        assert selection is not None
        assert selection.boundary == boundary
        assert selection.prefix == prefix
        assert diagnostic_boundary_class(selection.boundary) == boundary


def test_long_unpunctuated_text_uses_a_bounded_word_boundary() -> None:
    text = "seed alpha beta gamma delta epsilon"

    selection = select_semantic_prefix(
        text,
        "seed",
        max_tail_chars=10,
        allow_fallback=True,
    )

    assert selection is not None
    assert selection.boundary == "word"
    assert selection.prefix == "seed alpha beta gamma delta"
    assert selection.hidden_tail_chars <= 10


def test_grapheme_safe_fallback_keeps_emoji_zwj_sequence_intact() -> None:
    family = "\U0001f469\u200d\U0001f469\u200d\U0001f467\u200d\U0001f466"
    selection = select_semantic_prefix(
        f"x{family}z",
        "x",
        max_tail_chars=5,
        allow_fallback=True,
    )

    assert selection is not None
    assert selection.boundary == "fallback"
    assert selection.prefix == f"x{family}"
    assert selection.hidden_tail_chars == 1


def test_grapheme_safe_fallback_keeps_combining_mark_sequence_intact() -> None:
    selection = select_semantic_prefix(
        "xe\u0301z",
        "x",
        max_tail_chars=1,
        allow_fallback=True,
    )

    assert selection is not None
    assert selection.prefix == "xe\u0301"
    assert selection.hidden_tail_chars == 1


def test_semantic_preview_repairs_are_display_only() -> None:
    fenced = "```python\nprint(1)"
    inline = "before `code"

    assert repair_markdown_preview(fenced) == f"{fenced}\n```"
    assert repair_markdown_preview(inline) == f"{inline}`"
    assert repair_markdown_preview("```python\nprint(1)\n```") == "```python\nprint(1)\n```"


def test_semantic_presenter_preview_repair_does_not_change_final_text() -> None:
    async def scenario() -> None:
        fenced_sink = FakeSink()
        fenced_presenter = ReplyPresenter(
            fenced_sink,
            edit_interval_s=0.0,
            semantic_streaming=True,
            semantic_lookahead_s=0.0,
        )
        fenced_prefix = "```python\nprint(1)"
        fenced_complete = f"{fenced_prefix}\n```\nafter"
        fenced_presenter.append_delta(fenced_prefix)
        assert await fenced_presenter.flush()
        assert fenced_presenter.text == fenced_prefix
        assert fenced_sink.calls[0] == ("create", f"{fenced_prefix}\n```")
        await fenced_presenter.complete(fenced_complete)
        assert fenced_sink.calls[-1] == ("edit", fenced_complete)
        assert fenced_presenter.text == fenced_complete

        inline_sink = FakeSink()
        inline_presenter = ReplyPresenter(
            inline_sink,
            edit_interval_s=0.0,
            semantic_streaming=True,
            semantic_lookahead_s=0.0,
        )
        inline_prefix = "before `code"
        inline_complete = f"{inline_prefix}` tail"
        inline_presenter.append_delta(inline_prefix)
        assert await inline_presenter.flush()
        assert inline_presenter.text == inline_prefix
        assert inline_sink.calls[0] == ("create", f"{inline_prefix}`")
        await inline_presenter.complete(inline_complete)
        assert inline_sink.calls[-1] == ("edit", inline_complete)
        assert inline_presenter.text == inline_complete

        await fenced_presenter.shutdown()
        await inline_presenter.shutdown()

    asyncio.run(scenario())


def test_semantic_final_reconciliation_deletes_preview_only_continuation() -> None:
    async def scenario() -> None:
        sink = FakeSink()
        presenter = ReplyPresenter(
            sink,
            edit_interval_s=0.0,
            semantic_streaming=True,
            semantic_lookahead_s=0.0,
        )
        raw_prefix = "```\n" + "x" * (1997 - len("```\n"))
        completed = raw_prefix + "```"
        presenter.append_delta(raw_prefix)
        assert await presenter.flush()
        assert len(presenter.published_chunks) == 2

        await presenter.complete(completed)

        assert sink.calls[-1] == ("delete", "")
        assert presenter.message_count == 1
        assert presenter.published_chunks == (completed,)
        assert [message.content for message in sink.messages] == [completed]
        await presenter.shutdown()

    asyncio.run(scenario())


def test_semantic_presenter_first_delta_is_immediate_and_hidden_tail_is_not_interrupted() -> None:
    async def scenario() -> None:
        sink = FakeSink()
        presenter = ReplyPresenter(
            sink,
            edit_interval_s=0.0,
            semantic_streaming=True,
            semantic_lookahead_s=0.0,
        )

        presenter.append_delta("visible")
        assert await presenter.flush()
        presenter.append_delta(" hidden-partial")
        assert not await presenter.flush()
        await presenter.interrupted("visible hidden-partial")

        assert sink.calls[0] == ("create", "visible")
        assert sink.calls[-1] == ("edit", "visible" + INTERRUPTED_MARKER)
        assert "hidden-partial" not in sink.calls[-1][1]
        assert presenter.text == "visible" + INTERRUPTED_MARKER
        await presenter.shutdown()

    asyncio.run(scenario())


def test_semantic_presenter_failure_does_not_reveal_hidden_tail() -> None:
    async def scenario() -> None:
        sink = FakeSink()
        presenter = ReplyPresenter(
            sink,
            edit_interval_s=0.0,
            semantic_streaming=True,
            semantic_lookahead_s=0.0,
        )
        presenter.append_delta("visible")
        assert await presenter.flush()
        presenter.append_delta(" hidden-partial")
        await presenter.failed("visible hidden-partial")

        assert sink.calls[-1] == ("edit", "visible" + FAILED_MARKER)
        assert "hidden-partial" not in sink.calls[-1][1]
        await presenter.shutdown()

    asyncio.run(scenario())


def test_semantic_interruption_wakes_and_cancels_pending_lookahead_without_leak() -> None:
    async def scenario() -> None:
        sink = FakeSink()
        presenter = ReplyPresenter(
            sink,
            edit_interval_s=0.0,
            semantic_streaming=True,
            semantic_lookahead_s=1.0,
        )
        presenter.start()
        presenter.append_delta("seen")
        for _ in range(100):
            if sink.calls == [("create", "seen")]:
                break
            await asyncio.sleep(0)
        presenter.append_delta(" hidden-partial")
        await asyncio.sleep(0)
        await presenter.interrupted("seen hidden-partial")

        assert sink.calls[-1] == ("edit", "seen" + INTERRUPTED_MARKER)
        assert "hidden-partial" not in sink.calls[-1][1]
        await presenter.shutdown()

    asyncio.run(scenario())


def test_semantic_terminal_completion_reconciles_exact_full_text_once() -> None:
    async def scenario() -> None:
        sink = FakeSink()
        presenter = ReplyPresenter(
            sink,
            edit_interval_s=0.0,
            semantic_streaming=True,
            semantic_lookahead_s=0.0,
        )
        presenter.append_delta("visible")
        assert await presenter.flush()
        presenter.append_delta(" hidden-partial")
        full_text = "visible hidden-partial and complete."
        await presenter.complete(full_text)
        await presenter.complete("ignored")

        assert presenter.text == full_text
        assert sink.calls[-1] == ("edit", full_text)
        assert sum(content == full_text for _, content in sink.calls) == 1
        assert presenter.terminal_flush_count == 1
        await presenter.shutdown()

    asyncio.run(scenario())


def test_semantic_lookahead_is_bounded_with_fake_pacing_clock() -> None:
    async def scenario() -> None:
        clock = FakeClock()
        sink = FakeSink()
        diagnostics = DiscordDiagnostics(clock=clock)
        presenter = ReplyPresenter(
            sink,
            edit_interval_s=0.0,
            clock=clock,
            diagnostics=diagnostics,
            semantic_streaming=True,
            semantic_lookahead_s=0.02,
        )
        presenter.append_delta("seed")
        assert await presenter.flush()
        presenter.append_delta(" partial ")
        assert await presenter.flush()

        event = next(
            event
            for event in diagnostics.events
            if event["kind"] == "semantic_selection" and cast(int, event["raw_chars"]) > 4
        )
        max_lookahead_s = cast(float, event["max_lookahead_s"])
        lookahead_s = cast(float, event["lookahead_s"])
        assert max_lookahead_s == 0.02
        assert lookahead_s <= max_lookahead_s
        await presenter.shutdown()

    asyncio.run(scenario())


def test_semantic_diagnostics_are_observational_and_do_not_store_response_text() -> None:
    async def scenario() -> None:
        diagnostics = DiscordDiagnostics()
        sink = FakeSink()
        presenter = ReplyPresenter(
            sink,
            edit_interval_s=0.0,
            diagnostics=diagnostics,
            semantic_streaming=True,
            semantic_lookahead_s=0.0,
        )
        presenter.append_delta("visible hidden-secret")
        assert await presenter.flush()

        semantic_events = [
            event for event in diagnostics.events if event["kind"] == "semantic_selection"
        ]
        assert semantic_events
        assert "hidden-secret" not in json.dumps(semantic_events)
        assert all("text" not in event for event in semantic_events)
        await presenter.shutdown()

    asyncio.run(scenario())


def test_semantic_code_boundaries_ignore_prose_punctuation() -> None:
    visible = "```python\n"
    selection = select_semantic_prefix(
        f"{visible}value, next\nthird line",
        visible,
        max_tail_chars=80,
    )

    assert selection is not None
    assert selection.boundary == "code_newline"
    assert selection.prefix == f"{visible}value, next\n"

    no_newline = select_semantic_prefix(
        f"{visible}value, next",
        visible,
        max_tail_chars=80,
    )
    assert no_newline is not None
    assert no_newline.boundary == "code_word"
    assert no_newline.prefix.endswith("value,")


def test_semantic_continuation_split_preserves_text_and_graphemes() -> None:
    family = "\U0001f469\u200d\U0001f469\u200d\U0001f467\u200d\U0001f466"
    text = "x" * 1998 + family + "tail"

    chunks = split_semantic_content(text, max_chars=2000)

    assert "".join(chunks) == text
    assert all(len(chunk) <= 2000 for chunk in chunks)
    assert chunks[0] == "x" * 1998
    assert chunks[1].startswith(family)


def test_semantic_terminal_continuation_reconciliation_is_exact_and_grapheme_safe() -> None:
    async def scenario() -> None:
        family = "\U0001f469\u200d\U0001f469\u200d\U0001f467\u200d\U0001f466"
        text = "x" * 1998 + family + "tail"
        sink = FakeSink()
        presenter = ReplyPresenter(
            sink,
            edit_interval_s=0.0,
            semantic_streaming=True,
            semantic_lookahead_s=0.0,
        )
        await presenter.complete(text)

        assert "".join(content for _, content in sink.calls) == text
        assert all(len(content) <= 2000 for _, content in sink.calls)
        assert sink.calls[0] == ("create", "x" * 1998)
        assert sink.calls[1][0] == "create"
        assert sink.calls[1][1].startswith(family)
        await presenter.shutdown()

    asyncio.run(scenario())


def test_semantic_pump_remains_single_flight_and_terminal_flush_is_not_duplicated() -> None:
    async def scenario() -> None:
        sink = BlockingEditSink()
        presenter = ReplyPresenter(
            sink,
            edit_interval_s=0.0,
            semantic_streaming=True,
            semantic_lookahead_s=0.0,
        )
        presenter.start()
        presenter.append_delta("seed")
        for _ in range(100):
            if sink.calls == [("create", "seed")]:
                break
            await asyncio.sleep(0)
        assert sink.calls == [("create", "seed")]

        presenter.append_delta(" partial ")
        await asyncio.wait_for(sink.edit_started.wait(), 1.0)
        presenter.append_delta(" hidden")
        completion = asyncio.create_task(presenter.complete("seed partial  hidden"))
        await asyncio.sleep(0)
        assert not completion.done()

        sink.release_edit.set()
        await completion
        assert sink.max_active_edits == 1
        assert sink.calls == [
            ("create", "seed"),
            ("edit", "seed partial"),
            ("edit", "seed partial  hidden"),
        ]
        assert presenter.terminal_flush_count == 1
        await presenter.shutdown()

    asyncio.run(scenario())


def test_semantic_shutdown_cancels_lookahead_owned_by_presenter_pump() -> None:
    async def scenario() -> None:
        sink = FakeSink()
        presenter = ReplyPresenter(
            sink,
            edit_interval_s=0.0,
            semantic_streaming=True,
            semantic_lookahead_s=1.0,
        )
        presenter.start()
        presenter.append_delta("seed")
        for _ in range(100):
            if sink.calls == [("create", "seed")]:
                break
            await asyncio.sleep(0)
        presenter.append_delta(" partial")
        await asyncio.sleep(0)
        await presenter.shutdown()

        assert not any(
            task.get_name() == "lilavel-discord-reply-presenter-pump"
            for task in asyncio.all_tasks()
            if not task.done()
        )
        assert not presenter.finalized

    asyncio.run(scenario())
