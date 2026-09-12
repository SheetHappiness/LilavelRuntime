"""Model-backed cognition engine used beneath ``CognitionEpisodeRunner``.

This module is deliberately an engine, not an admission client.  The only
caller that can reach it in the production composition is the normal
``CognitionEpisodeRunner`` callback installed under ``SemanticActor``.
"""

from __future__ import annotations

import asyncio
import json
import threading
from collections.abc import Callable
from typing import cast

from lilavel_core import (
    ContextMessage,
    GenerationCancelled,
    GenerationCompleted,
    GenerationFailedEvent,
    ModelRequest,
    RunAwareConversationRuntime,
    RuntimeGeneration,
    TextDelta,
)
from lilavel_core.production_cognition import build_character_guidance

from .contracts import (
    ActionProposal,
    ActionProposalKind,
    CognitionCandidate,
    CognitionEpisode,
    CognitionTriggerSource,
    StateProposal,
    StateProposalKind,
)
from .mind import MAX_INTENTION_TEXT_BYTES

MAX_COGNITION_RESULT_BYTES = 4_096
MAX_APPRAISAL_CONTEXT_MESSAGES = 12
APPRAISAL_REASON = "conversation_completion_appraisal"
IDLE_REASON = "idle_opportunity"

APPRAISAL_CONTROL_GUIDANCE: tuple[str, ...] = (
    "This is a transient internal mind appraisal, not a user turn.",
    "Do not speak, call tools, write conversation history, or create memory.",
    'Return exactly one JSON object: {"action":"no_change"} or '
    '{"action":"create_intention","text":"..."}.',
    "Review the complete latest canonical user and assistant turn. The assistant "
    "message is evidence of what Lilavel already did, not just background context.",
    "An unfinished user situation is not automatically an unfinished Lilavel "
    "intention. Create at most one short intention only when a concrete future "
    "action for Lilavel remains after this turn, was not already performed in the "
    "assistant response, and could add new value later.",
    "Do not create an intention to repeat, paraphrase, or re-deliver advice, a "
    "reminder, an explanation, or a follow-up question already given. Do not "
    "create one merely because the topic may continue or because there is no "
    "specific future Lilavel action. Otherwise return no_change.",
)

IDLE_CONTROL_GUIDANCE: tuple[str, ...] = (
    "This is a transient, noncanonical idle cognition opportunity.",
    "Act only on the specific runtime-owned intention supplied in this request.",
    'Return exactly one JSON object: {"action":"speak","text":"..."} or {"action":"stay_silent"}.',
    "Do not answer a current user turn or emit ordinary assistant text.",
)


class CognitionGenerationUncontained(RuntimeError):
    """The physical cognition generation did not settle after cancellation."""

    semantic_uncontained = True


HistoryResolver = Callable[[str], tuple[ContextMessage, ...] | None]


class LocalCognitionEngine:
    """Translate two runtime-owned local opportunities into inert candidates."""

    def __init__(
        self,
        runtime: RunAwareConversationRuntime,
        history_for_trigger: HistoryResolver,
    ) -> None:
        if not callable(getattr(runtime, "generate_for_run", None)):
            raise TypeError("runtime must provide generate_for_run")
        if not callable(history_for_trigger):
            raise TypeError("history_for_trigger must be callable")
        self._runtime = runtime
        self._history_for_trigger = history_for_trigger

    async def run(self, episode: CognitionEpisode) -> object:
        trigger = episode.trigger
        if trigger.source is not CognitionTriggerSource.INTERNAL:
            return CognitionCandidate()

        if trigger.reason == APPRAISAL_REASON:
            request = self._appraisal_request(trigger.trigger_id)
            if request is None:
                return CognitionCandidate()
        elif trigger.reason == IDLE_REASON:
            intention_id = trigger.source_refs[-1] if trigger.source_refs else ""
            intention = next(
                (
                    item
                    for item in episode.context.mind_state.active_intentions
                    if item.intention_id == intention_id
                ),
                None,
            )
            if intention is None:
                return CognitionCandidate()
            request = ModelRequest(
                prompt=(
                    f"Runtime-owned active intention (context, not a user turn):\n{intention.text}"
                ),
                system_prompt=(*build_character_guidance(), *IDLE_CONTROL_GUIDANCE),
            )
        else:
            # The local production engine has no policy for generic/temporal
            # triggers.  Those callers supply their own CognitionEngine.
            return CognitionCandidate()

        raw = await self._generate(request, episode)
        return _parse_candidate(raw, trigger.reason)

    def _appraisal_request(self, trigger_id: str) -> ModelRequest | None:
        history = self._history_for_trigger(trigger_id)
        if history is None:
            return None
        recent = tuple(history[-MAX_APPRAISAL_CONTEXT_MESSAGES:])
        return ModelRequest(
            messages=recent if recent else None,
            prompt=None if recent else "Review the available canonical conversation.",
            system_prompt=(*build_character_guidance(), *APPRAISAL_CONTROL_GUIDANCE),
        )

    async def _generate(self, request: ModelRequest, episode: CognitionEpisode) -> str:
        handle = self._runtime.generate_for_run(
            request,
            scope_id=episode.scope_id,
            logical_run_id=f"cognition:{episode.episode_id}",
        )
        collector = asyncio.create_task(
            _collect_generation(handle), name=f"lilavel-cognition-generation-{episode.episode_id}"
        )
        try:
            events = await asyncio.shield(collector)
        except asyncio.CancelledError:
            try:
                self._runtime.cancel(handle.generation_id)
            finally:
                try:
                    await asyncio.wait_for(asyncio.shield(collector), timeout=1.0)
                except TimeoutError as error:
                    raise CognitionGenerationUncontained(
                        "cognition generation did not settle after cancellation"
                    ) from error
            raise

        parts: list[str] = []
        byte_count = 0
        completed = False
        for event in events:
            if isinstance(event, TextDelta):
                byte_count += len(event.delta.encode("utf-8"))
                if byte_count <= MAX_COGNITION_RESULT_BYTES:
                    parts.append(event.delta)
            elif isinstance(event, GenerationCompleted):
                completed = True
            elif isinstance(event, GenerationCancelled):
                raise asyncio.CancelledError
            elif isinstance(event, GenerationFailedEvent):
                raise RuntimeError("cognition generation failed")
        if not completed or byte_count > MAX_COGNITION_RESULT_BYTES:
            return ""
        return "".join(parts).strip()


async def _collect_generation(handle: RuntimeGeneration) -> tuple[object, ...]:
    completed = threading.Event()
    events: list[object] = []

    def consume() -> None:
        try:
            events.extend(handle.events())
        finally:
            completed.set()

    threading.Thread(
        target=consume,
        name="lilavel-cognition-generation-bridge",
        daemon=True,
    ).start()
    while not completed.is_set():
        await asyncio.sleep(0)
    return tuple(events)


def _parse_candidate(raw: str, reason: str) -> CognitionCandidate:
    if len(raw.encode("utf-8")) > MAX_COGNITION_RESULT_BYTES:
        return CognitionCandidate()
    try:
        value = json.loads(raw, object_pairs_hook=_reject_duplicate_keys)
    except (TypeError, ValueError):
        return CognitionCandidate()
    if not isinstance(value, dict):
        return CognitionCandidate()
    parsed = cast(dict[str, object], value)
    action = parsed.get("action")
    if not isinstance(action, str):
        return CognitionCandidate()

    if reason == APPRAISAL_REASON:
        if action == "no_change" and set(parsed) == {"action"}:
            return CognitionCandidate()
        text = parsed.get("text")
        if (
            action == "create_intention"
            and set(parsed) == {"action", "text"}
            and isinstance(text, str)
            and text.strip()
            and len(text.encode("utf-8")) <= MAX_INTENTION_TEXT_BYTES
        ):
            return CognitionCandidate(
                state_proposals=(StateProposal(StateProposalKind.CREATE_INTENTION, text.strip()),)
            )
        return CognitionCandidate()

    if reason == IDLE_REASON:
        if action == "stay_silent" and set(parsed) == {"action"}:
            return CognitionCandidate(
                action_proposals=(ActionProposal(ActionProposalKind.STAY_SILENT),)
            )
        text = parsed.get("text")
        if (
            action == "speak"
            and set(parsed) == {"action", "text"}
            and isinstance(text, str)
            and text.strip()
        ):
            return CognitionCandidate(
                action_proposals=(ActionProposal(ActionProposalKind.SPEAK, text.strip()),)
            )
    return CognitionCandidate()


def _reject_duplicate_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    if len({key for key, _ in pairs}) != len(pairs):
        raise ValueError("duplicate JSON key")
    return dict(pairs)


__all__ = [
    "APPRAISAL_REASON",
    "IDLE_REASON",
    "LocalCognitionEngine",
]
