"""Actor-owned execution seam for reactive ConversationCore episodes."""

from __future__ import annotations

import asyncio

from .contracts import ActionExecutor, EventRouter, Observation
from .semantic_actor import SemanticCancellationToken


class _ConversationExecutionUncontained(RuntimeError):
    """The router could not prove Core/presentation containment on cancel."""

    semantic_uncontained = True


class ConversationExecutionAdapter:
    """Contain one router/presentation run beneath ``SemanticActor``.

    The adapter owns no conversation state.  ``CoreConversationRouter`` keeps
    per-environment/subject sessions and ``ConversationCore`` remains the only
    authority for canonical history and assistant commit.  This seam only
    translates the actor's cooperative cancellation token into cancellation
    of the existing router task and joins that task before returning.
    """

    def __init__(self, router: EventRouter) -> None:
        if not callable(getattr(router, "route", None)):
            raise TypeError("router must provide route")
        self._router = router

    @property
    def router(self) -> EventRouter:
        return self._router

    async def execute(
        self,
        observation: Observation,
        execute: ActionExecutor,
        cancellation: SemanticCancellationToken,
    ) -> object:
        """Run and contain one reactive conversation episode."""

        route_task = asyncio.create_task(
            self._router.route(observation, execute),
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

    @staticmethod
    def _raise_if_uncontained(route_task: asyncio.Task[object]) -> None:
        if route_task.cancelled():
            return
        error = route_task.exception()
        if error is not None:
            raise _ConversationExecutionUncontained from error


__all__ = ["ConversationExecutionAdapter"]
