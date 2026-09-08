"""Live-free-by-default Discord transport probe."""

from __future__ import annotations

import asyncio
import time
from contextlib import suppress
from typing import Any

import discord

from .diagnostics import DiscordDiagnostics, attach_http_trace
from .edge import make_dm_intents, read_discord_token
from .presenter import ReplyPresenter
from .transport import DiscordMessageSink, TransportMetrics

_PROBE_TIMEOUT_S = 180.0
_PROBE_SHUTDOWN_TIMEOUT_S = 15.0
_INITIAL_TEXT = "Lilavel Discord transport probe: @everyone and <@123456789> must stay inert."
_COALESCED_PARTS = (
    " coalesced-1",
    " coalesced-2",
    " coalesced-3",
    " coalesced-4",
)
_LONG_PART = " continuation-block-0123456789" * 100


class _LiveTransportProbe:
    def __init__(
        self,
        *,
        client: discord.Client | None = None,
        shutdown_timeout_s: float = _PROBE_SHUTDOWN_TIMEOUT_S,
        diagnostics: DiscordDiagnostics | None = None,
    ) -> None:
        if shutdown_timeout_s <= 0:
            raise ValueError("shutdown_timeout_s must be positive")
        if client is None:
            client_options: dict[str, Any] = {"intents": make_dm_intents()}
            if diagnostics is not None:
                client_options["http_trace"] = diagnostics.trace_config
            self.client = discord.Client(**client_options)
        else:
            self.client = client
            attach_http_trace(self.client, diagnostics)
        self._shutdown_timeout_s = shutdown_timeout_s
        self._diagnostics = diagnostics
        self.started_at = time.perf_counter()
        self.ready_at: float | None = None
        self.received_at: float | None = None
        self.typing_entered_at: float | None = None
        self.result: dict[str, Any] | None = None
        self.resumed_count = 0
        self.disconnect_count = 0
        self._exercise_complete = asyncio.Event()
        self._start_task: asyncio.Task[None] | None = None
        self._client_close_completed = False
        self._start_task_settled = False
        self._start_task_completed_normally = False
        self._start_error: BaseException | None = None
        self._shutdown_error: BaseException | None = None
        self._exercise_error: BaseException | None = None
        self._register_handlers()

    async def run(self, token: str) -> dict[str, Any]:
        start_task = asyncio.create_task(
            self.client.start(token, reconnect=True),
            name="lilavel-discord-transport-probe-start",
        )
        self._start_task = start_task
        exercise_waiter = asyncio.create_task(
            self._exercise_complete.wait(),
            name="lilavel-discord-transport-probe-exercise-waiter",
        )
        try:
            done, _ = await asyncio.wait(
                (start_task, exercise_waiter),
                return_when=asyncio.FIRST_COMPLETED,
            )
            if start_task in done and not self._exercise_complete.is_set():
                self._record_start_failure(start_task)
            await self._shutdown_client_and_start(start_task)
        except asyncio.CancelledError:
            await self._shutdown_client_and_start(start_task)
            raise
        finally:
            exercise_waiter.cancel()
            await asyncio.gather(exercise_waiter, return_exceptions=True)
        return self._build_report()

    def _register_handlers(self) -> None:
        self.client.event(self.on_ready)
        self.client.event(self.on_resumed)
        self.client.event(self.on_disconnect)
        self.client.event(self.on_message)

    async def on_ready(self) -> None:
        self.ready_at = time.perf_counter()

    async def on_resumed(self) -> None:
        self.resumed_count += 1

    async def on_disconnect(self) -> None:
        self.disconnect_count += 1

    async def on_message(self, message: discord.Message) -> None:
        if self.result is not None or not isinstance(message.channel, discord.DMChannel):
            return
        if message.author.bot:
            return
        if self.client.user is not None and message.author.id == self.client.user.id:
            return
        try:
            await self._exercise(message)
        except asyncio.CancelledError:
            raise
        except Exception as error:
            self._exercise_error = error
            if self.result is None:
                self.result = {
                    "status": "FAIL",
                    "error_type": type(error).__name__,
                }
        finally:
            self._exercise_complete.set()

    async def _exercise(self, message: discord.Message) -> None:
        received_at = time.perf_counter()
        self.received_at = received_at
        metrics = TransportMetrics()
        presenter = ReplyPresenter(
            DiscordMessageSink(message.channel, metrics=metrics, diagnostics=self._diagnostics),
            edit_interval_s=60.0,
            diagnostics=self._diagnostics,
        )
        async with message.channel.typing():
            typing_entered_at = time.perf_counter()
            self.typing_entered_at = typing_entered_at
            presenter.append_delta(_INITIAL_TEXT)
            first_flush_started = time.perf_counter()
            await presenter.flush(force=True)
            first_visible_at = time.perf_counter()
            for part in _COALESCED_PARTS:
                presenter.append_delta(part)
            await presenter.flush(force=True)
            presenter.append_delta(_LONG_PART)
            await presenter.flush(force=True)
            await presenter.complete(presenter.text)

        now = time.perf_counter()
        self.result = {
            "status": "PASS",
            "dependency": discord.__version__,
            "intents_value": self.client.intents.value,
            "ready_observed": self.ready_at is not None,
            "ready_after_start_s": (
                None if self.ready_at is None else self.ready_at - self.started_at
            ),
            "received_dm": True,
            "typing_context_entered": True,
            "typing_context_latency_s": (typing_entered_at - received_at),
            "first_visible_content_s": first_visible_at - received_at,
            "first_send_operation_s": first_visible_at - first_flush_started,
            "synthetic_delta_count": len(_COALESCED_PARTS) + 2,
            "presenter_flush_count": presenter.flush_count,
            "terminal_flush_count": presenter.terminal_flush_count,
            "message_count": presenter.message_count,
            "max_published_chars": max(
                (len(chunk) for chunk in presenter.published_chunks), default=0
            ),
            "published_total_chars": len(presenter.text),
            "over_2000_continuation_exercised": len(presenter.text) > 2000
            and presenter.message_count > 1,
            "allowed_mentions_disabled": True,
            "send_latency_s": metrics.send_latencies_s,
            "edit_latency_s": metrics.edit_latencies_s,
            "surfaced_429": [
                {"operation": item.operation, "retry_after_s": item.retry_after_s}
                for item in metrics.rate_limits
            ],
            "reconnect_observed": self.resumed_count > 0,
            "disconnect_events_observed": self.disconnect_count,
            "elapsed_s": now - self.started_at,
        }

    async def _shutdown_client_and_start(self, start_task: asyncio.Task[None]) -> None:
        if not self._client_close_completed:
            try:
                await asyncio.wait_for(self.client.close(), self._shutdown_timeout_s)
            except asyncio.CancelledError:
                raise
            except BaseException as error:
                self._shutdown_error = error
            else:
                self._client_close_completed = True
        await self._settle_start_task(start_task)

    async def _settle_start_task(self, task: asyncio.Task[None]) -> None:
        if not task.done():
            try:
                await asyncio.wait_for(asyncio.shield(task), self._shutdown_timeout_s)
            except TimeoutError as error:
                self._start_error = error
                task.cancel()
                try:
                    await asyncio.wait_for(asyncio.shield(task), self._shutdown_timeout_s)
                except TimeoutError:
                    pass
                except asyncio.CancelledError:
                    if not task.cancelled():
                        raise
                except BaseException as cancellation_error:
                    self._start_error = cancellation_error
            except asyncio.CancelledError:
                raise
            except BaseException as error:
                self._start_error = error

        self._start_task_settled = task.done()
        self._start_task_completed_normally = (
            task.done() and not task.cancelled() and task.exception() is None
        )
        if not self._start_task_completed_normally and self._start_error is None:
            self._start_error = self._task_error(task)
        self._record_start_failure(task)

    def _record_start_failure(self, task: asyncio.Task[None]) -> None:
        if self.result is not None:
            return
        error = self._start_error or self._task_error(task)
        self.result = {
            "status": "FAIL",
            "error_type": type(error).__name__ if error is not None else "start_task_failed",
        }

    @staticmethod
    def _task_error(task: asyncio.Task[Any]) -> BaseException | None:
        if not task.done() or task.cancelled():
            return None
        with suppress(BaseException):
            return task.exception()
        return None

    def _build_report(self) -> dict[str, Any]:
        report = self.result or {"status": "FAIL", "error_type": "probe_ended_without_result"}
        start_task = self._start_task
        start_task_settled = self._start_task_settled and (
            start_task is not None and start_task.done()
        )
        start_task_completed_normally = self._start_task_completed_normally
        client_closed = self.client.is_closed()
        websocket_closed = self._websocket_closed()
        event_callback_settled = self._exercise_complete.is_set()
        report.update(
            {
                "shutdown_client_closed": client_closed,
                "shutdown_client_close_completed": self._client_close_completed,
                "shutdown_start_task_settled": start_task_settled,
                "shutdown_start_task_completed_normally": start_task_completed_normally,
                "shutdown_event_callback_settled": event_callback_settled,
                "shutdown_websocket_closed": websocket_closed,
                "shutdown_clean": (
                    self._client_close_completed
                    and client_closed
                    and start_task_settled
                    and start_task_completed_normally
                    and event_callback_settled
                    and websocket_closed
                ),
            }
        )
        if self._start_error is not None:
            report["shutdown_start_task_error_type"] = type(self._start_error).__name__
        if self._shutdown_error is not None:
            report["shutdown_error_type"] = type(self._shutdown_error).__name__
        if self._exercise_error is not None:
            report["exercise_error_type"] = type(self._exercise_error).__name__
        return report

    def _websocket_closed(self) -> bool:
        websocket = getattr(self.client, "ws", None)
        if websocket is None:
            return True
        return getattr(websocket, "open", None) is False


async def run_transport_probe(
    *,
    token: str | None = None,
    timeout_s: float = _PROBE_TIMEOUT_S,
    diagnostics: DiscordDiagnostics | None = None,
) -> dict[str, Any]:
    """Run Stage A, or return deterministic BLOCKED when the token is absent."""

    if timeout_s <= 0:
        raise ValueError("timeout_s must be positive")
    resolved = token or read_discord_token()
    if not resolved:
        return {
            "status": "BLOCKED",
            "reason": "missing LILAVEL_DISCORD_BOT_TOKEN",
            "live_probe": False,
        }
    probe = _LiveTransportProbe(
        shutdown_timeout_s=min(_PROBE_SHUTDOWN_TIMEOUT_S, timeout_s),
        diagnostics=diagnostics,
    )
    try:
        return await asyncio.wait_for(probe.run(resolved), timeout_s)
    except TimeoutError:
        if not probe.client.is_closed():
            await probe.client.close()
        return {"status": "FAIL", "error_type": "probe_timeout"}
