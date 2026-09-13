"""Model-backed cognition engine used beneath ``CognitionEpisodeRunner``.

This module is deliberately an engine, not an admission client.  The only
caller that can reach it in the production composition is the normal
``CognitionEpisodeRunner`` callback installed under ``SemanticActor``.
"""

from __future__ import annotations

import asyncio
import json
import threading
from collections.abc import Callable, Sequence
from typing import cast

from lilavel_core import (
    MAX_COGNITION_REASON_CODES,
    AttentionDecision,
    CognitionPolicyDecision,
    CognitionReasonCode,
    ContextMessage,
    DispositionCandidate,
    GenerationCancelled,
    GenerationCompleted,
    GenerationFailedEvent,
    InterventionDecision,
    ModelRequest,
    ResponseDisposition,
    RunAwareConversationRuntime,
    RuntimeGeneration,
    TextDelta,
    WorkingState,
)
from lilavel_core.cognition import Aim, Level, QuestionPolicy, Stance
from lilavel_core.production_cognition import (
    build_character_guidance,
    build_disposition_planner_guidance,
)
from lilavel_core.sidecar_protocol import MAX_CONTEXT_BYTES

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
MAX_DISPOSITION_CONTEXT_MESSAGES = 4
MAX_DISPOSITION_PLANNER_RESULT_BYTES = MAX_COGNITION_RESULT_BYTES
APPRAISAL_REASON = "conversation_completion_appraisal"
IDLE_REASON = "idle_opportunity"
_REASON_CODE_VALUES = "|".join(code.value for code in CognitionReasonCode)

DISPOSITION_PLANNER_CONTROL_GUIDANCE: tuple[str, ...] = (
    "This is a direct user turn disposition-planning request.",
    "The direct user invariant is fixed outside model control: attention is THINK "
    "and intervention is RESPOND.",
    "Choose only the behavioral disposition beneath that invariant. Do not decide "
    "whether to respond, whether to interject, or whether cognition is warranted.",
    "Return exactly one strict JSON object with these keys and no others: aim, "
    "stance, engagement, directness, desired_length, humor_allowed, "
    "question_policy, initiative, reason_codes.",
    "Allowed values: aim=answer|acknowledge|clarify|challenge|tease|comfort|"
    "disagree|explore|close; stance=neutral|curious|skeptical|playful|supportive; "
    "all level fields=low|normal|high; question_policy=avoid|required|invite.",
    f"Allowed reason_codes: {_REASON_CODE_VALUES}; do not invent labels.",
    "Use only the exact enum values supplied below. reason_codes must be a short "
    "unique list of supported reason-code values.",
    "Do not return prose, markdown, a reasoning field, confidence, focus text, "
    "tools, action proposals, memory instructions, or temporal instructions.",
)

_DISPOSITION_KEYS = frozenset(
    {
        "aim",
        "stance",
        "engagement",
        "directness",
        "desired_length",
        "humor_allowed",
        "question_policy",
        "initiative",
        "reason_codes",
    }
)
_AIM_VALUES = frozenset(
    {
        "answer",
        "acknowledge",
        "clarify",
        "challenge",
        "tease",
        "comfort",
        "disagree",
        "explore",
        "close",
    }
)
_STANCE_VALUES = frozenset({"neutral", "curious", "skeptical", "playful", "supportive"})
_LEVEL_VALUES = frozenset({"low", "normal", "high"})
_QUESTION_POLICY_VALUES = frozenset({"avoid", "required", "invite"})
_FOCUS_BY_AIM = {
    "answer": "answer the current request",
    "acknowledge": "acknowledge the user's point with useful care",
    "clarify": "resolve materially missing information",
    "challenge": "address the important conflict in the user's premise",
    "tease": "meet the playful turn lightly",
    "comfort": "respond to the user's difficulty with practical warmth",
    "disagree": "address the important conflict in the user's premise",
    "explore": "explore the useful uncertainty or implication",
    "close": "close with the smallest useful next step",
}

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


class _ScopedModelGenerationMixin:
    """Keep bounded model collection under an explicitly scoped engine owner."""

    _runtime: RunAwareConversationRuntime

    async def _generate_text(
        self,
        request: ModelRequest,
        *,
        scope_id: str,
        logical_run_id: str,
        name: str,
    ) -> str:
        """Collect one bounded generation while preserving cancellation containment."""

        handle = self._runtime.generate_for_run(
            request,
            scope_id=scope_id,
            logical_run_id=logical_run_id,
        )
        collector = asyncio.create_task(_collect_generation(handle), name=name)
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


class DispositionPlanner(_ScopedModelGenerationMixin):
    """Plan direct-user response behavior without owning response admission.

    The planner is an explicit caller-owned seam. It is not wired into
    ``ConversationCore`` or any production USER route in COG-V1-C.
    """

    def __init__(
        self,
        runtime: RunAwareConversationRuntime,
        *,
        planner_guidance: Sequence[str] | None = None,
    ) -> None:
        if not callable(getattr(runtime, "generate_for_run", None)):
            raise TypeError("runtime must provide generate_for_run")
        self._runtime = runtime
        self._planner_guidance = (
            build_disposition_planner_guidance()
            if planner_guidance is None
            else tuple(planner_guidance)
        )
        # ModelRequest performs the authoritative guidance bounds check. Do it
        # at construction time so an invalid composition fails before a call.
        ModelRequest(
            prompt="validate planner guidance",
            system_prompt=(*self._planner_guidance, *DISPOSITION_PLANNER_CONTROL_GUIDANCE),
        )

    def build_request(
        self,
        current_user_text: str,
        *,
        recent_context: Sequence[ContextMessage] = (),
    ) -> ModelRequest:
        """Build a bounded provider-neutral planner request.

        ``recent_context`` is prior canonical context. If a caller supplies a
        canonical tail that already ends in the current user message, it is
        reused without duplication; otherwise the current message is appended.
        At most four messages reach the model.
        """

        if type(current_user_text) is not str or not current_user_text.strip():
            raise ValueError("current_user_text must be a non-empty string")
        current = ContextMessage("user", current_user_text)
        context = tuple(recent_context)
        recent = context[-MAX_DISPOSITION_CONTEXT_MESSAGES:]
        if recent and recent[-1] == current:
            messages = list(recent)
        else:
            messages = [*recent[-(MAX_DISPOSITION_CONTEXT_MESSAGES - 1) :], current]
        if not all(type(message) is ContextMessage for message in messages):
            raise TypeError("recent_context must contain only ContextMessage values")
        messages = _fit_disposition_context(messages)
        return ModelRequest(
            messages=tuple(messages),
            system_prompt=(*self._planner_guidance, *DISPOSITION_PLANNER_CONTROL_GUIDANCE),
        )

    async def plan_candidate(
        self,
        current_user_text: str,
        *,
        recent_context: Sequence[ContextMessage] = (),
        scope_id: str = "disposition-planner",
        logical_run_id: str = "disposition-planner",
    ) -> DispositionCandidate | None:
        """Generate and strictly validate one untrusted disposition candidate."""

        request = self.build_request(current_user_text, recent_context=recent_context)
        raw = await self._generate_text(
            request,
            scope_id=scope_id,
            logical_run_id=logical_run_id,
            name="lilavel-disposition-planner",
        )
        return parse_disposition_candidate(raw)

    async def plan(
        self,
        current_user_text: str,
        *,
        recent_context: Sequence[ContextMessage] = (),
        scope_id: str = "disposition-planner",
        logical_run_id: str = "disposition-planner",
    ) -> DispositionCandidate | None:
        """Alias for the candidate-producing planner operation."""

        return await self.plan_candidate(
            current_user_text,
            recent_context=recent_context,
            scope_id=scope_id,
            logical_run_id=logical_run_id,
        )

    async def plan_policy(
        self,
        current_user_text: str,
        *,
        recent_context: Sequence[ContextMessage] = (),
        scope_id: str = "disposition-planner",
        logical_run_id: str = "disposition-planner",
    ) -> CognitionPolicyDecision | None:
        """Plan and compile the fixed direct-user policy decision."""

        candidate = await self.plan_candidate(
            current_user_text,
            recent_context=recent_context,
            scope_id=scope_id,
            logical_run_id=logical_run_id,
        )
        return None if candidate is None else candidate_to_policy_decision(candidate)


ModelBackedDispositionPlanner = DispositionPlanner


def compile_disposition_focus(candidate: DispositionCandidate) -> str:
    """Compile trusted focus only from validated enums and reason codes."""

    if type(candidate) is not DispositionCandidate:
        raise TypeError("candidate must be a DispositionCandidate")
    reasons = set(candidate.reason_codes)
    if candidate.aim in {"challenge", "disagree"} and CognitionReasonCode.CONTRADICTION in reasons:
        return "address the important conflict in the user's premise"
    if candidate.aim == "clarify" and CognitionReasonCode.AMBIGUITY_MATERIAL in reasons:
        return "resolve materially missing information"
    if candidate.aim in {"comfort", "acknowledge"} and CognitionReasonCode.VULNERABILITY in reasons:
        return "respond to the user's difficulty with practical warmth"
    return _FOCUS_BY_AIM[candidate.aim]


def candidate_to_policy_decision(candidate: DispositionCandidate) -> CognitionPolicyDecision:
    """Bind a validated candidate to the fixed direct-user invariants."""

    if type(candidate) is not DispositionCandidate:
        raise TypeError("candidate must be a DispositionCandidate")
    return CognitionPolicyDecision(
        attention=AttentionDecision.THINK,
        intervention=InterventionDecision.RESPOND,
        working_state=WorkingState(
            focus=compile_disposition_focus(candidate),
            stance=candidate.stance,
            engagement=candidate.engagement,
        ),
        response_disposition=ResponseDisposition(
            aim=candidate.aim,
            directness=candidate.directness,
            desired_length=candidate.desired_length,
            humor_allowed=candidate.humor_allowed,
            question_policy=candidate.question_policy,
            initiative=candidate.initiative,
        ),
        reason_codes=candidate.reason_codes,
    )


def parse_disposition_candidate(raw: str) -> DispositionCandidate | None:
    """Parse one exact planner JSON object, returning ``None`` on rejection."""

    if type(raw) is not str:
        return None
    try:
        if len(raw.encode("utf-8")) > MAX_DISPOSITION_PLANNER_RESULT_BYTES:
            return None
    except UnicodeEncodeError:
        return None
    try:
        value = json.loads(raw, object_pairs_hook=_reject_duplicate_keys)
    except (TypeError, ValueError, RecursionError):
        return None
    if type(value) is not dict:
        return None
    parsed = cast(dict[str, object], value)
    if frozenset(parsed) != _DISPOSITION_KEYS:
        return None

    aim = _string_value(parsed["aim"], _AIM_VALUES)
    stance = _string_value(parsed["stance"], _STANCE_VALUES)
    engagement = _string_value(parsed["engagement"], _LEVEL_VALUES)
    directness = _string_value(parsed["directness"], _LEVEL_VALUES)
    desired_length = _string_value(parsed["desired_length"], _LEVEL_VALUES)
    question_policy = _string_value(parsed["question_policy"], _QUESTION_POLICY_VALUES)
    initiative = _string_value(parsed["initiative"], _LEVEL_VALUES)
    humor_allowed = parsed["humor_allowed"]
    raw_reasons = parsed["reason_codes"]
    if (
        aim is None
        or stance is None
        or engagement is None
        or directness is None
        or desired_length is None
        or question_policy is None
        or initiative is None
        or type(humor_allowed) is not bool
        or type(raw_reasons) is not list
    ):
        return None
    reasons: list[CognitionReasonCode] = []
    for raw_reason in cast(list[object], raw_reasons):
        if type(raw_reason) is not str:
            return None
        try:
            reason = CognitionReasonCode(raw_reason)
        except ValueError:
            return None
        if reason in reasons:
            return None
        reasons.append(reason)
    if not reasons or len(reasons) > MAX_COGNITION_REASON_CODES:
        return None
    try:
        return DispositionCandidate(
            aim=cast(Aim, aim),
            stance=cast(Stance, stance),
            engagement=cast(Level, engagement),
            directness=cast(Level, directness),
            desired_length=cast(Level, desired_length),
            humor_allowed=humor_allowed,
            question_policy=cast(QuestionPolicy, question_policy),
            initiative=cast(Level, initiative),
            reason_codes=tuple(reasons),
        )
    except (TypeError, ValueError):
        return None


_parse_disposition_candidate = parse_disposition_candidate


def _fit_disposition_context(messages: list[ContextMessage]) -> list[ContextMessage]:
    """Keep a most-recent contiguous context suffix within the wire bound."""

    if sum(len(message.text.encode("utf-8")) for message in messages) <= MAX_CONTEXT_BYTES:
        return messages
    while (
        len(messages) > 1
        and sum(len(message.text.encode("utf-8")) for message in messages) > MAX_CONTEXT_BYTES
    ):
        messages.pop(0)
    if sum(len(message.text.encode("utf-8")) for message in messages) > MAX_CONTEXT_BYTES:
        raise ValueError("disposition planner context exceeds the bounded context size")
    return messages


def _string_value(value: object, allowed: frozenset[str]) -> str | None:
    return value if type(value) is str and value in allowed else None


class LocalCognitionEngine(_ScopedModelGenerationMixin):
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
        return await self._generate_text(
            request,
            scope_id=episode.scope_id,
            logical_run_id=f"cognition:{episode.episode_id}",
            name=f"lilavel-cognition-generation-{episode.episode_id}",
        )


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
    "DISPOSITION_PLANNER_CONTROL_GUIDANCE",
    "IDLE_REASON",
    "MAX_DISPOSITION_CONTEXT_MESSAGES",
    "MAX_DISPOSITION_PLANNER_RESULT_BYTES",
    "DispositionPlanner",
    "LocalCognitionEngine",
    "ModelBackedDispositionPlanner",
    "candidate_to_policy_decision",
    "compile_disposition_focus",
    "parse_disposition_candidate",
]
