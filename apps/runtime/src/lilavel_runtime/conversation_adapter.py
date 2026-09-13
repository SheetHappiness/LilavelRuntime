"""Actor-owned execution seam for reactive ConversationCore episodes."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass
from threading import Lock, Thread
from typing import cast

from lilavel_core import (
    ConversationCore,
    ConversationEvent,
    ConversationOutcome,
    ConversationRun,
    TurnBehavior,
)

from .contracts import ActionExecutor, EventRouter, Observation
from .semantic_actor import SemanticCancellationToken, SemanticEpisodeStatus
from .user_disposition import UserDispositionResolver


class _ConversationExecutionUncontained(RuntimeError):
    """The router could not prove Core/presentation containment on cancel."""

    semantic_uncontained = True


ConversationPresenter = Callable[[ConversationEvent], None]


@dataclass(frozen=True, slots=True)
class ConversationExecutionResult:
    """A Core turn result kept outside actor evidence and canonical state."""

    run: ConversationRun
    outcome: ConversationOutcome

    @property
    def semantic_status(self) -> SemanticEpisodeStatus:
        """Translate the Core terminal status into actor settlement vocabulary."""

        if self.outcome.status == "completed":
            return SemanticEpisodeStatus.COMPLETED
        if self.outcome.status in {"cancelled", "superseded"}:
            return SemanticEpisodeStatus.CANCELLED
        return SemanticEpisodeStatus.FAILED


class _CoreRunHolder:
    """Bind a Core run after start_turn while retaining the cancellation race."""

    def __init__(self) -> None:
        self._lock = Lock()
        self._run: ConversationRun | None = None
        self._cancel_requested = False

    def bind(self, run: ConversationRun) -> None:
        with self._lock:
            self._run = run
            cancel_requested = self._cancel_requested
        if cancel_requested:
            run.cancel()

    def cancel(self) -> None:
        with self._lock:
            self._cancel_requested = True
            run = self._run
        if run is not None:
            run.cancel()


async def _await_blocking[BlockingResult](
    function: Callable[[], BlockingResult],
) -> BlockingResult:
    """Run one bounded Core bridge without borrowing the loop executor."""

    loop = asyncio.get_running_loop()
    completed = asyncio.Event()
    outcome: list[tuple[BlockingResult | None, BaseException | None]] = []

    def invoke() -> None:
        try:
            result = function()
        except BaseException as error:
            outcome.append((None, error))
        else:
            outcome.append((result, None))
        finally:
            loop.call_soon_threadsafe(completed.set)

    # Keep the bridge daemon-owned so an uncontainable provider cannot strand
    # the asyncio default executor or the process shutdown path.
    Thread(
        target=invoke,
        name="lilavel-runtime-core-bridge",
        daemon=True,
    ).start()
    await completed.wait()
    result, error = outcome[0]
    if error is not None:
        raise error
    return cast(BlockingResult, result)


def _consume_core_turn(
    core: ConversationCore,
    text: str,
    present: ConversationPresenter,
    holder: _CoreRunHolder | None = None,
    on_run: Callable[[ConversationRun], None] | None = None,
) -> ConversationExecutionResult:
    """Start, stream, and settle one Core turn on a bridge thread."""

    run = core.start_turn(text, supersede=True)
    if holder is not None:
        holder.bind(run)
    if on_run is not None:
        on_run(run)
    try:
        for event in run.events():
            present(event)
        return ConversationExecutionResult(run, run.wait(0))
    except BaseException:
        if not run.settled:
            run.cancel()
            run.wait()
        raise


def _prepare_core_turn(
    core: ConversationCore,
    text: str,
    holder: _CoreRunHolder,
) -> ConversationRun:
    run = core.prepare_turn(text, supersede=True)
    holder.bind(run)
    return run


def _start_and_consume_prepared(
    core: ConversationCore,
    run: ConversationRun,
    behavior: TurnBehavior,
    present: ConversationPresenter,
) -> ConversationExecutionResult:
    core.start_prepared_run(run, behavior)
    try:
        for event in run.events():
            present(event)
        return ConversationExecutionResult(run, run.wait(0))
    except BaseException:
        if not run.settled:
            run.cancel()
            run.wait()
        raise


async def _await_prepared_result(
    prepare_task: asyncio.Task[ConversationRun],
    holder: _CoreRunHolder,
) -> ConversationExecutionResult:
    del holder
    run = await prepare_task
    return ConversationExecutionResult(run, await _await_blocking(run.wait))


async def _join_resolution(resolution_task: asyncio.Task[object]) -> None:
    result = await asyncio.gather(resolution_task, return_exceptions=True)
    error = result[0]
    if isinstance(error, BaseException) and getattr(error, "semantic_uncontained", False):
        raise _ConversationExecutionUncontained from error


class ConversationExecutionAdapter:
    """Contain one router/presentation run beneath ``SemanticActor``.

    The adapter owns no conversation state.  ``CoreConversationRouter`` keeps
    per-environment/subject sessions and ``ConversationCore`` remains the only
    authority for canonical history and assistant commit.  This seam only
    translates the actor's cooperative cancellation token into cancellation
    of the existing router task and joins that task before returning.
    """

    def __init__(
        self,
        router: EventRouter | None = None,
        *,
        disposition_resolver: UserDispositionResolver | None = None,
    ) -> None:
        if router is not None and not callable(getattr(router, "route", None)):
            raise TypeError("router must provide route")
        self._router = router
        self._disposition_resolver = disposition_resolver or UserDispositionResolver()

    @property
    def router(self) -> EventRouter | None:
        return self._router

    async def execute(
        self,
        observation: Observation,
        execute: ActionExecutor,
        cancellation: SemanticCancellationToken,
    ) -> object:
        """Run and contain one reactive conversation episode."""

        router = self._router
        if router is None:
            raise RuntimeError("conversation router is not configured")

        route_task = asyncio.create_task(
            router.route(observation, execute),
            name=f"lilavel-conversation-episode-{observation.event.event_id}",
        )
        cancellation_task = asyncio.create_task(
            cancellation.wait(),
            name=f"lilavel-conversation-cancel-{observation.event.event_id}",
        )
        try:
            done, _ = await asyncio.wait(
                (route_task, cancellation_task), return_when=asyncio.FIRST_COMPLETED
            )
            if route_task in done:
                return route_task.result()

            route_task.cancel()
            await asyncio.gather(route_task, return_exceptions=True)
            self._raise_if_uncontained(route_task)
            return None
        except asyncio.CancelledError:
            cancellation.request()
            route_task.cancel()
            await asyncio.gather(route_task, return_exceptions=True)
            self._raise_if_uncontained(route_task)
            raise
        finally:
            cancellation_task.cancel()
            await asyncio.gather(cancellation_task, return_exceptions=True)

    async def execute_core_turn(
        self,
        core: ConversationCore,
        text: str,
        present: ConversationPresenter,
        cancellation: SemanticCancellationToken,
    ) -> ConversationExecutionResult:
        """Run a local Core turn beneath the same actor-owned lifecycle seam."""

        if not text or not text.strip():
            raise ValueError("conversation text must be non-empty")
        if not callable(present):
            raise TypeError("present must be callable")
        holder = _CoreRunHolder()
        prepare_task = asyncio.create_task(
            _await_blocking(
                lambda: _prepare_core_turn(core, text, holder),
            ),
            name=f"lilavel-local-core-prepare-{core.scope_id}",
        )
        cancellation_task = asyncio.create_task(
            cancellation.wait(), name=f"lilavel-local-core-cancel-{core.scope_id}"
        )
        run: ConversationRun | None = None
        try:
            done, _ = await asyncio.wait(
                (prepare_task, cancellation_task), return_when=asyncio.FIRST_COMPLETED
            )
            if prepare_task in done:
                run = prepare_task.result()
            else:
                holder.cancel()
                return await _await_prepared_result(prepare_task, holder)

            if cancellation.is_requested:
                holder.cancel()
                return ConversationExecutionResult(run, await _await_blocking(run.wait))

            resolution_task = asyncio.create_task(
                self._disposition_resolver.resolve(core, run, cancellation),
                name=f"lilavel-local-disposition-{run.run_id}",
            )
            done, _ = await asyncio.wait(
                (resolution_task, cancellation_task), return_when=asyncio.FIRST_COMPLETED
            )
            if cancellation_task in done:
                resolution_task.cancel()
                await _join_resolution(resolution_task)
                holder.cancel()
                return ConversationExecutionResult(run, await _await_blocking(run.wait))
            resolution = resolution_task.result()
            if cancellation.is_requested:
                holder.cancel()
                return ConversationExecutionResult(run, await _await_blocking(run.wait))

            turn_task = asyncio.create_task(
                _await_blocking(
                    lambda: _start_and_consume_prepared(core, run, resolution.behavior, present)
                ),
                name=f"lilavel-local-core-turn-{core.scope_id}",
            )
            done, _ = await asyncio.wait(
                (turn_task, cancellation_task), return_when=asyncio.FIRST_COMPLETED
            )
            if turn_task in done:
                return turn_task.result()
            holder.cancel()
            return await turn_task
        except asyncio.CancelledError:
            cancellation.request()
            holder.cancel()
            await asyncio.gather(prepare_task, return_exceptions=True)
            if run is not None:
                await _await_blocking(run.wait)
            raise
        except BaseException:
            holder.cancel()
            await asyncio.gather(prepare_task, return_exceptions=True)
            if run is not None and not run.settled:
                await _await_blocking(run.wait)
            raise
        finally:
            cancellation_task.cancel()
            await asyncio.gather(cancellation_task, return_exceptions=True)

    def execute_core_turn_sync(
        self,
        core: ConversationCore,
        text: str,
        present: ConversationPresenter,
        on_run: Callable[[ConversationRun], None] | None = None,
    ) -> ConversationExecutionResult:
        """Compatibility bridge for standalone legacy Presence tests."""

        if not text or not text.strip():
            raise ValueError("conversation text must be non-empty")
        return _consume_core_turn(core, text, present, on_run=on_run)

    @staticmethod
    def _raise_if_uncontained(route_task: asyncio.Task[object]) -> None:
        if route_task.cancelled():
            return
        error = route_task.exception()
        if error is not None:
            raise _ConversationExecutionUncontained from error


__all__ = [
    "ConversationExecutionAdapter",
    "ConversationExecutionResult",
    "ConversationPresenter",
]
