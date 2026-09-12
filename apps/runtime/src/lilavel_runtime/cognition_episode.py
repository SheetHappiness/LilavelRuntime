"""Bounded, effect-free cognition episodes for the persistent runtime."""

from __future__ import annotations

import asyncio
from uuid import uuid4

from . import contracts as _contracts
from .contracts import (
    CognitionCandidate,
    CognitionContext,
    CognitionEngine,
    CognitionEpisode,
    CognitionEpisodeStatus,
    CognitionOutcome,
    CognitionTrigger,
    Observation,
    ObservationWindow,
)
from .mind import MindState

__all__ = ["CognitionEpisodeRunner"]


class CognitionEpisodeRunner:
    """Run one serialized cognition episode and return only inert proposals.

    The runner owns no effectful capability. It snapshots the observation window
    and MindState before invoking the effect-free engine seam, validates the
    structured candidate, and stops. Proposal application and action
    authorization belong to MIND-1D.
    """

    def __init__(
        self,
        engine: CognitionEngine,
        observation_window: ObservationWindow,
        mind_state: MindState,
        *,
        scope_id: str = "runtime",
        timeout_s: float | None = None,
    ) -> None:
        if not scope_id.strip():
            raise ValueError("scope_id must be non-empty")
        if timeout_s is not None and (isinstance(timeout_s, bool) or timeout_s <= 0):
            raise ValueError("timeout_s must be positive when provided")
        self._engine = engine
        self._observation_window = observation_window
        self._mind_state = mind_state
        self._scope_id = scope_id
        self._timeout_s = timeout_s
        self._serial = asyncio.Lock()
        self._active_episode: CognitionEpisode | None = None
        self._last_episode: CognitionEpisode | None = None
        self._last_status: CognitionEpisodeStatus | None = None
        self._invocation_count = 0

    @property
    def active_episode(self) -> CognitionEpisode | None:
        return self._active_episode

    @property
    def last_episode(self) -> CognitionEpisode | None:
        return self._last_episode

    @property
    def last_status(self) -> CognitionEpisodeStatus | None:
        return self._last_status

    @property
    def invocation_count(self) -> int:
        return self._invocation_count

    async def run(self, trigger: CognitionTrigger) -> CognitionOutcome | None:
        """Run exactly one episode for ``trigger`` or fail closed.

        ``None`` means that no valid completed outcome exists. Cancellation is
        re-raised so the caller retains normal task-cancellation semantics; it
        can never return a partial or otherwise valid outcome.
        """

        if type(trigger) is not CognitionTrigger:
            raise TypeError("trigger must be a CognitionTrigger")

        async with self._serial:
            try:
                episode = self._build_episode(trigger)
            except Exception:
                self._last_status = CognitionEpisodeStatus.FAILED
                self._active_episode = None
                return None

            self._active_episode = episode
            self._last_episode = episode
            self._last_status = CognitionEpisodeStatus.RUNNING
            self._invocation_count += 1
            try:
                candidate = await self._invoke(episode)
                outcome = self._normalize(episode, candidate)
            except TimeoutError:
                self._last_status = CognitionEpisodeStatus.TIMED_OUT
                return None
            except asyncio.CancelledError:
                self._last_status = CognitionEpisodeStatus.CANCELLED
                raise
            except Exception as error:
                # A provider/model bridge may know that cancellation did not
                # contain the physical generation. Preserve that proof for the
                # SemanticActor so it poisons instead of admitting a successor
                # under uncertain semantic ownership.
                if getattr(error, "semantic_uncontained", False) is True:
                    raise
                self._last_status = CognitionEpisodeStatus.FAILED
                return None
            else:
                self._last_status = CognitionEpisodeStatus.COMPLETED
                return outcome
            finally:
                self._active_episode = None

    def _build_episode(self, trigger: CognitionTrigger) -> CognitionEpisode:
        observations: list[Observation] = []
        for observation_id in trigger.observation_ids:
            observation = self._observation_window.get(observation_id)
            if observation is None:
                raise ValueError(f"trigger observation is unavailable: {observation_id}")
            observations.append(observation)

        episode_id = f"episode:{uuid4()}"
        context = CognitionContext(
            episode_id=episode_id,
            scope_id=self._scope_id,
            trigger=trigger,
            observations=tuple(observations),
            mind_state=self._mind_state.snapshot(),
        )
        return CognitionEpisode(episode_id, context)

    async def _invoke(self, episode: CognitionEpisode) -> object:
        invocation = self._engine.run(episode)
        if self._timeout_s is None:
            return await invocation
        return await asyncio.wait_for(invocation, timeout=self._timeout_s)

    @staticmethod
    def _normalize(episode: CognitionEpisode, candidate: object) -> CognitionOutcome:
        if type(candidate) is not CognitionCandidate:
            raise TypeError("cognition engine returned an invalid candidate")
        return CognitionOutcome(
            episode_id=episode.episode_id,
            trigger_id=episode.trigger.trigger_id,
            state_proposals=candidate.state_proposals,
            action_proposals=candidate.action_proposals,
            temporal_proposals=candidate.temporal_proposals,
            scope_id=episode.scope_id,
            based_on_state_version=episode.context.mind_state_version,
            completion_proof=_contracts._COMPLETED_COGNITION_PROOF,  # pyright: ignore[reportPrivateUsage]
        )
