"""Optional, safe HTTP and presenter diagnostics for the Discord edge."""

from __future__ import annotations

import contextvars
import hashlib
import json
import math
import os
import sys
import time
from collections.abc import Callable, Generator, Iterable, Mapping
from contextlib import contextmanager, suppress
from dataclasses import dataclass
from itertools import count
from typing import Any, Final, Literal, cast

from aiohttp import TraceConfig

DISCORD_HTTP_DIAGNOSTICS_ENV: Final = "LILAVEL_DISCORD_HTTP_DIAGNOSTICS"
MessageOperation = Literal["POST message", "PATCH message", "DELETE message"]
PresentationStatus = Literal["completed", "interrupted", "failed"]
PresentationReason = Literal[
    "cancelled",
    "superseded",
    "edge_cancelled",
    "bridge_error",
    "missing_terminal",
    "core_failed",
    "presenter_error",
]
DiagnosticEvent = dict[str, object]
DiagnosticEmitter = Callable[[Mapping[str, object]], None]
Clock = Callable[[], float]

_active_operation: contextvars.ContextVar[_OperationState | None] = contextvars.ContextVar(
    "lilavel_discord_active_http_operation", default=None
)
_active_flush_id: contextvars.ContextVar[int | None] = contextvars.ContextVar(
    "lilavel_discord_active_presenter_flush", default=None
)
_active_core_run_id: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "lilavel_discord_active_core_run", default=None
)


@dataclass(slots=True)
class _OperationState:
    operation_id: int
    operation: MessageOperation
    presenter_flush_id: int | None
    core_run_id: str | None
    started_at_s: float
    response_count: int = 0
    last_response_at_s: float | None = None


@dataclass(slots=True)
class _TraceState:
    request_id: int
    operation: MessageOperation
    operation_id: int | None
    presenter_flush_id: int | None
    core_run_id: str | None
    started_at_s: float
    operation_state: _OperationState | None


class DiscordDiagnostics:
    """Collect safe, opt-in timings for message HTTP operations and flushes.

    The recorder never stores URLs, request headers, response bodies, message
    text, or exception messages. It is deliberately separate from transport
    semantics: when no instance is passed to the edge, no trace config or
    diagnostic context is installed.
    """

    def __init__(
        self,
        *,
        clock: Clock = time.perf_counter,
        emit: DiagnosticEmitter | None = None,
        retain_events: bool = True,
    ) -> None:
        self._clock = clock
        self._origin = clock()
        self._emit = emit
        self._retain_events = retain_events
        self.events: list[DiagnosticEvent] = []
        self._operation_ids = count(1)
        self._request_ids = count(1)
        self._flush_ids = count(1)
        self._trace_config = TraceConfig()
        self._trace_config.on_request_start.append(self._on_request_start)
        self._trace_config.on_request_end.append(self._on_request_end)
        self._trace_config.on_request_exception.append(self._on_request_exception)

    @property
    def trace_config(self) -> TraceConfig:
        """Return the aiohttp trace config for discord.py's public hook."""

        return self._trace_config

    @contextmanager
    def operation(self, operation: MessageOperation) -> Generator[int, None, None]:
        """Record one awaited discord.py send or edit operation."""

        state = _OperationState(
            operation_id=next(self._operation_ids),
            operation=operation,
            presenter_flush_id=_active_flush_id.get(),
            core_run_id=_active_core_run_id.get(),
            started_at_s=self._timestamp(),
        )
        token = _active_operation.set(state)
        self._record(
            "operation_start",
            timestamp_s=state.started_at_s,
            operation=state.operation,
            operation_id=state.operation_id,
            presenter_flush_id=state.presenter_flush_id,
            **_core_run_fields(state.core_run_id),
        )
        try:
            yield state.operation_id
        except BaseException as error:
            finished_at_s = self._timestamp()
            self._record_operation_return(state, finished_at_s, "raised", error)
            raise
        else:
            finished_at_s = self._timestamp()
            self._record_operation_return(state, finished_at_s, "returned", None)
        finally:
            _active_operation.reset(token)

    @contextmanager
    def presenter_flush(self, *, chunk_count: int, force: bool) -> Generator[int, None, None]:
        """Record the visible reconciliation window without changing it."""

        flush_id = next(self._flush_ids)
        started_at_s = self._timestamp()
        core_run_id = _active_core_run_id.get()
        token = _active_flush_id.set(flush_id)
        self._record(
            "presenter_flush_start",
            timestamp_s=started_at_s,
            flush_id=flush_id,
            chunk_count=chunk_count,
            force=force,
            **_core_run_fields(core_run_id),
        )
        try:
            yield flush_id
        except BaseException as error:
            finished_at_s = self._timestamp()
            self._record(
                "presenter_flush_return",
                timestamp_s=finished_at_s,
                flush_id=flush_id,
                duration_s=finished_at_s - started_at_s,
                outcome="raised",
                error_type=type(error).__name__,
                **_core_run_fields(core_run_id),
            )
            raise
        else:
            finished_at_s = self._timestamp()
            self._record(
                "presenter_flush_return",
                timestamp_s=finished_at_s,
                flush_id=flush_id,
                duration_s=finished_at_s - started_at_s,
                outcome="returned",
                **_core_run_fields(core_run_id),
            )
        finally:
            _active_flush_id.reset(token)

    def semantic_selection(
        self,
        *,
        boundary: str,
        raw_chars: int,
        visible_chars: int,
        hidden_tail_chars: int,
        hidden_tail_age_s: float | None,
        lookahead_s: float,
        max_lookahead_s: float,
        preview_repaired: bool,
        published: bool,
    ) -> None:
        """Record safe semantic presentation measurements without response text."""

        self._record(
            "semantic_selection",
            raw_chars=raw_chars,
            visible_chars=visible_chars,
            hidden_tail_chars=hidden_tail_chars,
            hidden_tail_age_s=hidden_tail_age_s,
            lookahead_s=lookahead_s,
            max_lookahead_s=max_lookahead_s,
            boundary=boundary,
            preview_repaired=preview_repaired,
            published=published,
        )

    @contextmanager
    def presentation_context(self, core_run_id: str) -> Generator[None, None, None]:
        """Bind one Core run to this edge-local diagnostic scope."""

        if not core_run_id:
            raise ValueError("core_run_id must be a non-empty string")
        token = _active_core_run_id.set(core_run_id)
        try:
            yield
        finally:
            _active_core_run_id.reset(token)

    def presentation_outcome(
        self,
        *,
        status: PresentationStatus,
        reason: PresentationReason | None,
        flush_count: int,
        terminal_flush_count: int,
    ) -> None:
        """Record one safe terminal outcome for a correlated presentation."""

        core_run_id = _active_core_run_id.get()
        if core_run_id is None:
            return
        self._record(
            "presentation_outcome",
            core_run_id=core_run_id,
            status=status,
            reason=reason,
            flush_count=flush_count,
            terminal_flush_count=terminal_flush_count,
        )

    def _record_operation_return(
        self,
        state: _OperationState,
        finished_at_s: float,
        outcome: Literal["returned", "raised"],
        error: BaseException | None,
    ) -> None:
        last_response_at_s = state.last_response_at_s
        self._record(
            "operation_return",
            timestamp_s=finished_at_s,
            operation=state.operation,
            operation_id=state.operation_id,
            presenter_flush_id=state.presenter_flush_id,
            **_core_run_fields(state.core_run_id),
            outcome=outcome,
            await_duration_s=finished_at_s - state.started_at_s,
            http_response_count=state.response_count,
            last_http_response_at_s=last_response_at_s,
            post_response_gap_s=(
                None if last_response_at_s is None else finished_at_s - last_response_at_s
            ),
            error_type=None if error is None else type(error).__name__,
        )

    async def _on_request_start(self, _session: Any, trace_context: Any, params: Any) -> None:
        operation = _classify_message_route(
            getattr(params, "method", None), getattr(getattr(params, "url", None), "path", None)
        )
        if operation is None:
            return

        operation_state = _active_operation.get()
        presenter_flush_id = _active_flush_id.get()
        if presenter_flush_id is None and operation_state is not None:
            presenter_flush_id = operation_state.presenter_flush_id
        core_run_id = _active_core_run_id.get()
        if core_run_id is None and operation_state is not None:
            core_run_id = operation_state.core_run_id
        state = _TraceState(
            request_id=next(self._request_ids),
            operation=operation,
            operation_id=(None if operation_state is None else operation_state.operation_id),
            presenter_flush_id=presenter_flush_id,
            core_run_id=core_run_id,
            started_at_s=self._timestamp(),
            operation_state=operation_state,
        )
        trace_context.lilavel_message_trace = state
        self._record(
            "http_request_start",
            timestamp_s=state.started_at_s,
            request_id=state.request_id,
            operation=state.operation,
            operation_id=state.operation_id,
            presenter_flush_id=state.presenter_flush_id,
            **_core_run_fields(state.core_run_id),
        )

    async def _on_request_end(self, _session: Any, trace_context: Any, params: Any) -> None:
        state = getattr(trace_context, "lilavel_message_trace", None)
        if not isinstance(state, _TraceState):
            return

        finished_at_s = self._timestamp()
        response = getattr(params, "response", None)
        status = _safe_status(getattr(response, "status", None))
        if state.operation_state is not None:
            state.operation_state.response_count += 1
            state.operation_state.last_response_at_s = finished_at_s
        self._record(
            "http_response",
            timestamp_s=finished_at_s,
            request_id=state.request_id,
            operation=state.operation,
            operation_id=state.operation_id,
            presenter_flush_id=state.presenter_flush_id,
            **_core_run_fields(state.core_run_id),
            response_duration_s=finished_at_s - state.started_at_s,
            response_status=status,
            rate_limited=status == 429,
            **_safe_rate_limit_fields(getattr(response, "headers", None)),
        )

    async def _on_request_exception(self, _session: Any, trace_context: Any, params: Any) -> None:
        state = getattr(trace_context, "lilavel_message_trace", None)
        if not isinstance(state, _TraceState):
            return
        finished_at_s = self._timestamp()
        self._record(
            "http_request_exception",
            timestamp_s=finished_at_s,
            request_id=state.request_id,
            operation=state.operation,
            operation_id=state.operation_id,
            presenter_flush_id=state.presenter_flush_id,
            **_core_run_fields(state.core_run_id),
            duration_s=finished_at_s - state.started_at_s,
            error_type=type(getattr(params, "exception", RuntimeError())).__name__,
        )

    def _record(self, kind: str, *, timestamp_s: float | None = None, **fields: object) -> None:
        event: DiagnosticEvent = {
            "kind": kind,
            "at_s": self._timestamp() if timestamp_s is None else timestamp_s,
            **fields,
        }
        if self._retain_events:
            self.events.append(event)
        if self._emit is not None:
            with suppress(Exception):
                self._emit(dict(event))

    def _timestamp(self) -> float:
        return self._clock() - self._origin


def _core_run_fields(core_run_id: str | None) -> dict[str, object]:
    if core_run_id is None:
        return {}
    return {"core_run_id": core_run_id}


def attach_http_trace(client: Any, diagnostics: DiscordDiagnostics | None) -> None:
    """Attach diagnostics to an already-created discord.py client safely."""

    if diagnostics is None:
        return
    http = getattr(client, "http", None)
    if http is None:
        return
    existing = getattr(http, "http_trace", None)
    if existing is None:
        http.http_trace = diagnostics.trace_config
    elif existing is not diagnostics.trace_config:
        raise ValueError("client already has a different http_trace configuration")


def diagnostics_from_environment(*, clock: Clock = time.perf_counter) -> DiscordDiagnostics | None:
    """Enable JSONL diagnostics only when explicitly requested by the operator."""

    if os.environ.get(DISCORD_HTTP_DIAGNOSTICS_ENV) != "1":
        return None

    def emit(event: Mapping[str, object]) -> None:
        print(
            json.dumps(dict(event), ensure_ascii=False, sort_keys=True, separators=(",", ":")),
            file=sys.stderr,
            flush=True,
        )

    return DiscordDiagnostics(clock=clock, emit=emit, retain_events=False)


def _classify_message_route(method: object, path: object) -> MessageOperation | None:
    if not isinstance(method, str) or not isinstance(path, str):
        return None
    parts = tuple(part for part in path.split("/") if part)
    if len(parts) < 5 or parts[0] != "api" or not parts[1].startswith("v"):
        return None
    if parts[2] != "channels" or not parts[3] or parts[4] != "messages":
        return None
    normalized_method = method.upper()
    if normalized_method == "POST" and len(parts) == 5:
        return "POST message"
    if normalized_method == "PATCH" and len(parts) == 6 and parts[5]:
        return "PATCH message"
    if normalized_method == "DELETE" and len(parts) == 6 and parts[5]:
        return "DELETE message"
    return None


def _safe_status(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value


def _safe_rate_limit_fields(headers: object) -> dict[str, object]:
    bucket = _header(headers, "X-RateLimit-Bucket")
    return {
        "rate_limit_limit": _parse_int(_header(headers, "X-RateLimit-Limit")),
        "rate_limit_remaining": _parse_int(_header(headers, "X-RateLimit-Remaining")),
        "rate_limit_reset_after_s": _parse_float(_header(headers, "X-RateLimit-Reset-After")),
        "rate_limit_bucket_hash": _hash_bucket(bucket),
        "retry_after_s": _parse_float(_header(headers, "Retry-After")),
    }


def _header(headers: object, name: str) -> object:
    getter = getattr(headers, "get", None)
    if callable(getter):
        try:
            value = getter(name)
        except Exception:
            value = None
        if value is not None:
            return value
    items = getattr(headers, "items", None)
    if callable(items):
        item_pairs = cast(Callable[[], Iterable[tuple[object, object]]], items)()
        try:
            for key, value in item_pairs:
                if isinstance(key, str) and key.lower() == name.lower():
                    return value
        except Exception:
            return None
    return None


def _parse_int(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, (int, str)):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _parse_float(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def _hash_bucket(value: object) -> str | None:
    if not isinstance(value, str) or not value:
        return None
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:12]
