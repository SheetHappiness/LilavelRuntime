"""Rate-aware, mention-safe rendering of transient Core output to Discord."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Final, Protocol

from .diagnostics import DiscordDiagnostics
from .semantic import (
    DEFAULT_SEMANTIC_LOOKAHEAD_S,
    DEFAULT_SEMANTIC_MAX_TAIL_CHARS,
    SemanticSelection,
    boundary_rank,
    diagnostic_boundary_class,
    make_first_selection,
    repair_markdown_preview,
    select_semantic_prefix,
    split_semantic_content,
)
from .transport import MAX_DISCORD_MESSAGE_CHARS, split_discord_content, validate_edit_interval

INTERRUPTED_MARKER: Final = "\n\n[response interrupted]"
FAILED_MARKER: Final = "\n\n[response unavailable]"
NO_RESPONSE_MARKER: Final = "[no response]"
DEFAULT_EDIT_INTERVAL_S: Final = 1.0


class MessageSink(Protocol):
    async def create(self, content: str) -> Any: ...

    async def edit(self, message: Any, content: str) -> Any: ...

    async def delete(self, message: Any) -> None: ...


@dataclass(frozen=True, slots=True)
class _SemanticSnapshot:
    raw_text: str
    chunks: tuple[str, ...]
    selection: SemanticSelection
    lookahead_s: float
    hidden_tail_age_s: float | None
    preview_repaired: bool


class ReplyPresenter:
    """Materialize one growing response as bounded Discord messages.

    The first meaningful delta is published immediately. Later snapshots are
    published by one owned pump after a configurable minimum interval. Deltas
    arriving while Discord is busy only update the latest snapshot; the pump
    never starts a second reconciliation concurrently. Terminal methods force
    one final reconciliation and are idempotent. Semantic presentation is an
    opt-in display-only policy layered before Discord chunking.
    """

    def __init__(
        self,
        sink: MessageSink,
        *,
        edit_interval_s: float = DEFAULT_EDIT_INTERVAL_S,
        clock: Callable[[], float] = time.perf_counter,
        diagnostics: DiscordDiagnostics | None = None,
        semantic_streaming: bool = False,
        semantic_lookahead_s: float = DEFAULT_SEMANTIC_LOOKAHEAD_S,
        semantic_max_tail_chars: int = DEFAULT_SEMANTIC_MAX_TAIL_CHARS,
    ) -> None:
        validate_edit_interval(edit_interval_s)
        validate_edit_interval(semantic_lookahead_s)
        if isinstance(semantic_max_tail_chars, bool) or semantic_max_tail_chars <= 0:
            raise ValueError("semantic_max_tail_chars must be positive")
        self._sink = sink
        self._edit_interval_s = edit_interval_s
        self._clock = clock
        self._diagnostics = diagnostics
        self._semantic_streaming = semantic_streaming
        self._semantic_lookahead_s = semantic_lookahead_s
        self._semantic_max_tail_chars = semantic_max_tail_chars
        self._text = ""
        self._published_chunks: tuple[str, ...] = ()
        self._messages: list[Any] = []
        self._last_visible_raw_text = ""
        self._semantic_attempted_raw_text: str | None = None
        self._semantic_hidden_since: float | None = None
        self._last_publish_started_at: float | None = None
        self._publish_lock = asyncio.Lock()
        self._wake = asyncio.Event()
        self._pump_failed = asyncio.Event()
        self._pump_task: asyncio.Task[None] | None = None
        self._pump_error: BaseException | None = None
        self._terminal_requested = False
        self._terminal_preview_repair = False
        self._terminal_marker = ""
        self._finalized = False
        self.flush_count = 0
        self.terminal_flush_count = 0

    @property
    def text(self) -> str:
        return self._text

    @property
    def finalized(self) -> bool:
        return self._finalized

    @property
    def message_count(self) -> int:
        return len(self._messages)

    @property
    def published_chunks(self) -> tuple[str, ...]:
        return self._published_chunks

    def start(self) -> None:
        """Start the single-flight publisher owned by this presenter."""

        if self._pump_task is None:
            self._pump_task = asyncio.create_task(
                self._run_pump(),
                name="lilavel-discord-reply-presenter-pump",
            )

    def append_delta(self, delta: str) -> None:
        if self._finalized or self._terminal_requested:
            return
        if delta:
            self._text += delta
            self._note_semantic_growth()
            self._wake.set()

    def replace_text(self, text: str) -> None:
        if self._finalized or self._terminal_requested:
            return
        if self._text != text:
            self._text = text
            self._note_semantic_growth()
            self._wake.set()

    def ready_to_flush(self) -> bool:
        if self._finalized or self._terminal_requested or not self._has_pending_snapshot():
            return False
        if self._last_publish_started_at is None:
            return True
        return self._clock() - self._last_publish_started_at >= self._edit_interval_s

    async def flush(self, *, force: bool = False) -> bool:
        """Reconcile once for callers that do not use the owned pump."""

        if self._finalized or self._terminal_requested:
            return False
        if not force and not self.ready_to_flush():
            return False
        return await self._flush_once(force=force)

    async def complete(self, text: str) -> None:
        await self._finalize(text, "", empty_fallback=NO_RESPONSE_MARKER)

    async def interrupted(self, text: str) -> None:
        await self._finalize(text, INTERRUPTED_MARKER)

    async def failed(self, text: str) -> None:
        await self._finalize(text, FAILED_MARKER)

    async def shutdown(self) -> None:
        """Settle the owned pump when the surrounding response task is cancelled."""

        task = self._pump_task
        if task is None:
            return
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)

    async def wait_for_error(self) -> BaseException:
        """Wait for an unexpected sink error from the owned pump."""

        await self._pump_failed.wait()
        if self._pump_error is None:
            raise RuntimeError("reply presenter pump failed without an error")
        return self._pump_error

    async def _finalize(
        self,
        text: str,
        marker: str,
        *,
        empty_fallback: str | None = None,
    ) -> None:
        if self._finalized:
            return
        if self._terminal_requested:
            await self._await_pump()
            return
        if self._semantic_streaming and marker:
            visible_text = self._last_visible_raw_text
            self._text = (
                visible_text + marker if visible_text else (empty_fallback or marker.lstrip())
            )
            self._terminal_preview_repair = True
            self._terminal_marker = marker
        else:
            self._text = text + marker if text else (empty_fallback or marker.lstrip())
            self._terminal_preview_repair = False
            self._terminal_marker = ""
        self._terminal_requested = True
        self.terminal_flush_count += 1
        self.start()
        self._wake.set()
        await self._await_pump()

    async def _await_pump(self) -> None:
        task = self._pump_task
        if task is None:
            raise RuntimeError("reply presenter pump was not started")
        await task

    async def _run_pump(self) -> None:
        try:
            while not self._finalized:
                if self._terminal_requested:
                    await self._flush_once(force=True)
                    self._finalized = True
                    return

                if self.ready_to_flush():
                    await self._flush_once(force=False)
                    continue

                self._wake.clear()
                if self._terminal_requested or self.ready_to_flush():
                    continue

                timeout = self._time_until_eligible()
                try:
                    if timeout is None:
                        await self._wake.wait()
                    else:
                        await asyncio.wait_for(self._wake.wait(), timeout)
                except TimeoutError:
                    continue
        except asyncio.CancelledError:
            raise
        except BaseException as error:
            self._pump_error = error
            self._pump_failed.set()
            raise

    def _time_until_eligible(self) -> float | None:
        if not self._has_pending_snapshot():
            return None
        if self._last_publish_started_at is None:
            return 0.0
        elapsed = self._clock() - self._last_publish_started_at
        return max(0.0, self._edit_interval_s - elapsed)

    def _has_pending_snapshot(self) -> bool:
        if self._semantic_streaming:
            return self._semantic_has_pending_snapshot()
        chunks = split_discord_content(self._text, max_chars=MAX_DISCORD_MESSAGE_CHARS)
        return bool(chunks) and chunks != self._published_chunks

    async def _flush_once(self, *, force: bool) -> bool:
        async with self._publish_lock:
            if self._finalized:
                return False
            if not force and self._terminal_requested:
                return False
            if not force and not self.ready_to_flush():
                return False

            semantic_snapshot: _SemanticSnapshot | None = None
            if self._semantic_streaming and not force:
                semantic_snapshot = await self._prepare_semantic_snapshot()
                if semantic_snapshot is None:
                    if not self._terminal_requested:
                        self._semantic_attempted_raw_text = self._text
                    return False
                chunks = semantic_snapshot.chunks
            elif self._semantic_streaming:
                if self._terminal_preview_repair:
                    if self._last_visible_raw_text:
                        display_text = (
                            repair_markdown_preview(self._last_visible_raw_text)
                            + self._terminal_marker
                        )
                    else:
                        display_text = self._text
                else:
                    display_text = self._text
                chunks = split_semantic_content(
                    display_text,
                    max_chars=MAX_DISCORD_MESSAGE_CHARS,
                )
            else:
                chunks = split_discord_content(self._text, max_chars=MAX_DISCORD_MESSAGE_CHARS)
            if not chunks:
                return False

            if semantic_snapshot is not None and self._diagnostics is not None:
                self._diagnostics.semantic_selection(
                    boundary=diagnostic_boundary_class(semantic_snapshot.selection.boundary),
                    raw_chars=len(semantic_snapshot.raw_text),
                    visible_chars=semantic_snapshot.selection.position,
                    hidden_tail_chars=semantic_snapshot.selection.hidden_tail_chars,
                    hidden_tail_age_s=semantic_snapshot.hidden_tail_age_s,
                    lookahead_s=semantic_snapshot.lookahead_s,
                    max_lookahead_s=self._semantic_lookahead_s,
                    preview_repaired=semantic_snapshot.preview_repaired,
                    published=chunks != self._published_chunks,
                )
            if not force and chunks == self._published_chunks:
                if semantic_snapshot is not None:
                    self._semantic_attempted_raw_text = semantic_snapshot.raw_text
                return False

            # A slow request consumes part of the floor; do not add another full wait.
            self._last_publish_started_at = self._clock()
            if self._diagnostics is None:
                await self._reconcile(chunks)
            else:
                with self._diagnostics.presenter_flush(
                    chunk_count=len(chunks),
                    force=force,
                ):
                    await self._reconcile(chunks)
            self._published_chunks = chunks
            self.flush_count += 1
            if semantic_snapshot is not None:
                self._last_visible_raw_text = semantic_snapshot.selection.prefix
                self._semantic_attempted_raw_text = semantic_snapshot.raw_text
                self._update_semantic_hidden_state(semantic_snapshot.selection)
            return True

    async def _reconcile(self, chunks: tuple[str, ...]) -> None:
        for index, chunk in enumerate(chunks):
            if index < len(self._messages):
                if index >= len(self._published_chunks) or self._published_chunks[index] != chunk:
                    self._messages[index] = await self._sink.edit(self._messages[index], chunk)
            else:
                self._messages.append(await self._sink.create(chunk))
        if self._semantic_streaming and len(chunks) < len(self._messages):
            for message in self._messages[len(chunks) :]:
                await self._sink.delete(message)
            del self._messages[len(chunks) :]

    def _semantic_has_pending_snapshot(self) -> bool:
        if not self._text:
            return False
        return self._semantic_attempted_raw_text != self._text

    def _note_semantic_growth(self) -> None:
        if not self._semantic_streaming or not self._last_visible_raw_text:
            return
        if (
            self._text.startswith(self._last_visible_raw_text)
            and len(self._text) > len(self._last_visible_raw_text)
            and self._semantic_hidden_since is None
        ):
            self._semantic_hidden_since = self._clock()

    def _update_semantic_hidden_state(self, selection: SemanticSelection) -> None:
        if selection.hidden_tail_chars == 0 and self._text == selection.prefix:
            self._semantic_hidden_since = None
        elif self._semantic_hidden_since is None:
            self._semantic_hidden_since = self._clock()

    def _hidden_tail_age(self, selection: SemanticSelection) -> float | None:
        if selection.hidden_tail_chars == 0:
            return None
        if self._semantic_hidden_since is None:
            self._semantic_hidden_since = self._clock()
        return max(0.0, self._clock() - self._semantic_hidden_since)

    async def _prepare_semantic_snapshot(self) -> _SemanticSnapshot | None:
        raw_text = self._text
        if not raw_text:
            return None

        if not self._published_chunks:
            selection = make_first_selection(raw_text)
            lookahead_s = 0.0
        else:
            selection = select_semantic_prefix(
                raw_text,
                self._last_visible_raw_text,
                max_tail_chars=self._semantic_max_tail_chars,
            )
            lookahead_s = 0.0
            if self._needs_semantic_lookahead(selection):
                selection, lookahead_s = await self._await_semantic_boundary(selection)
                if self._terminal_requested:
                    return None
            if selection is None:
                selection = select_semantic_prefix(
                    self._text,
                    self._last_visible_raw_text,
                    max_tail_chars=self._semantic_max_tail_chars,
                    allow_fallback=True,
                )
            if selection is None:
                return None

            raw_text = self._text

        if self._terminal_requested:
            return None
        display_text = repair_markdown_preview(selection.prefix)
        return _SemanticSnapshot(
            raw_text=raw_text,
            chunks=split_semantic_content(
                display_text,
                max_chars=MAX_DISCORD_MESSAGE_CHARS,
            ),
            selection=selection,
            lookahead_s=lookahead_s,
            hidden_tail_age_s=self._hidden_tail_age(selection),
            preview_repaired=display_text != selection.prefix,
        )

    @staticmethod
    def _needs_semantic_lookahead(selection: SemanticSelection | None) -> bool:
        return selection is None or selection.boundary in {
            "clause",
            "word",
            "code_word",
        }

    async def _await_semantic_boundary(
        self,
        initial: SemanticSelection | None,
    ) -> tuple[SemanticSelection | None, float]:
        if self._semantic_lookahead_s == 0:
            return initial, 0.0

        loop = asyncio.get_running_loop()
        started_at = loop.time()
        deadline = started_at + self._semantic_lookahead_s
        current = initial
        observed_raw_text = self._text
        while True:
            if loop.time() >= deadline:
                break
            if self._terminal_requested:
                return None, min(self._semantic_lookahead_s, max(0.0, loop.time() - started_at))

            self._wake.clear()
            if self._terminal_requested:
                return None, min(self._semantic_lookahead_s, max(0.0, loop.time() - started_at))
            if self._text != observed_raw_text:
                observed_raw_text = self._text
                candidate = select_semantic_prefix(
                    observed_raw_text,
                    self._last_visible_raw_text,
                    max_tail_chars=self._semantic_max_tail_chars,
                )
                if self._candidate_improves(candidate, initial):
                    current = candidate
                    break
                current = candidate
                continue

            remaining = deadline - loop.time()
            if remaining <= 0:
                break
            try:
                await asyncio.wait_for(self._wake.wait(), remaining)
            except TimeoutError:
                break

        elapsed = max(0.0, loop.time() - started_at)
        return current, min(self._semantic_lookahead_s, elapsed)

    @staticmethod
    def _candidate_improves(
        candidate: SemanticSelection | None,
        initial: SemanticSelection | None,
    ) -> bool:
        if candidate is None:
            return False
        if initial is None:
            return True
        return (boundary_rank(candidate.boundary), candidate.position) > (
            boundary_rank(initial.boundary),
            initial.position,
        )
