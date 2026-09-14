"""Read-only local source resolution for bounded peripheral awareness."""

from __future__ import annotations

from typing import Protocol

from .context import AwarenessSourceMaterial
from .contracts import ObservationWindow


class AwarenessSourceResolver(Protocol):
    """Resolve one explicit awareness source reference read-only."""

    def resolve(self, source_ref: str) -> AwarenessSourceMaterial | None: ...


class ObservationWindowAwarenessSourceResolver:
    """Resolve existing observation/event references from the local window."""

    def __init__(self, observation_window: ObservationWindow) -> None:
        if type(observation_window) is not ObservationWindow:
            raise TypeError("observation_window must be an ObservationWindow")
        self._observation_window = observation_window

    @property
    def observation_window(self) -> ObservationWindow:
        """Return the existing local source owner dependency."""

        return self._observation_window

    def resolve(self, source_ref: str) -> AwarenessSourceMaterial | None:
        """Resolve only an exact existing observation or event identity."""

        if type(source_ref) is not str:
            return None
        if source_ref.startswith("observation:"):
            source_kind = "observation"
            identity = source_ref.removeprefix("observation:")
            record = self._observation_window.get(identity) if identity.strip() else None
        elif source_ref.startswith("event:"):
            source_kind = "event"
            identity = source_ref.removeprefix("event:")
            if not identity.strip():
                record = None
            else:
                record = next(
                    (
                        observation
                        for observation in self._observation_window.snapshot()
                        if observation.event.event_id == identity
                    ),
                    None,
                )
        else:
            return None
        if record is None:
            return None
        text = record.event.payload.get("text")
        if type(text) is not str or not text:
            return None
        return AwarenessSourceMaterial(source_ref, source_kind, text)


__all__ = ["AwarenessSourceResolver", "ObservationWindowAwarenessSourceResolver"]
