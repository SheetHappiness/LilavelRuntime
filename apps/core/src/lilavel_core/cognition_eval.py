"""Deterministic COG-V1-A cognition-policy corpus and evaluator.

The corpus evaluates policy decisions, not rendered text.  It contains no
provider calls, randomization, user memory, or model chain-of-thought.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from types import MappingProxyType

from .cognition import (
    Aim,
    AttentionDecision,
    CognitionPolicyDecision,
    CognitionReasonCode,
    InterventionDecision,
    Level,
    QuestionPolicy,
    ResponseDisposition,
    Stance,
    WorkingState,
)


class CognitionScenarioSource(StrEnum):
    """Stable source classes used by the v1-A corpus."""

    DIRECT_USER = "direct_user"
    AMBIENT = "ambient"


@dataclass(frozen=True, slots=True)
class CognitionDispositionConstraints:
    """Allowed disposition values for a scenario.

    Empty sets mean unconstrained.  Optional constraints are useful for an
    ambient case where an interjection may be justified, but silence remains
    an equally valid outcome.
    """

    required: bool = True
    aims: frozenset[Aim] = frozenset()
    directness: frozenset[Level] = frozenset()
    desired_length: frozenset[Level] = frozenset()
    humor_allowed: bool | None = None
    question_policy: frozenset[QuestionPolicy] = frozenset()
    initiative: frozenset[Level] = frozenset()
    stances: frozenset[Stance] = frozenset()
    engagements: frozenset[Level] = frozenset()

    def __post_init__(self) -> None:
        if type(self.required) is not bool:
            raise TypeError("disposition constraint required must be a bool")
        allowed_values = {
            "aims": {
                "answer",
                "acknowledge",
                "clarify",
                "challenge",
                "tease",
                "comfort",
                "disagree",
                "explore",
                "close",
            },
            "directness": {"low", "normal", "high"},
            "desired_length": {"low", "normal", "high"},
            "question_policy": {"avoid", "required", "invite"},
            "initiative": {"low", "normal", "high"},
            "stances": {"neutral", "curious", "skeptical", "playful", "supportive"},
            "engagements": {"low", "normal", "high"},
        }
        for field_name in (
            "aims",
            "directness",
            "desired_length",
            "question_policy",
            "initiative",
            "stances",
            "engagements",
        ):
            value = frozenset(getattr(self, field_name))
            if not value.issubset(allowed_values[field_name]):
                raise ValueError(f"disposition constraint {field_name} contains an invalid value")
            object.__setattr__(self, field_name, value)
        if self.humor_allowed is not None and type(self.humor_allowed) is not bool:
            raise TypeError("disposition constraint humor_allowed must be a bool or None")


@dataclass(frozen=True, slots=True)
class CognitionScenario:
    """One short, reviewable policy case in the COG-V1-A corpus."""

    id: str
    source_class: CognitionScenarioSource
    input_context: str
    expected_attention: frozenset[AttentionDecision]
    expected_intervention: frozenset[InterventionDecision]
    required_reason_codes: frozenset[CognitionReasonCode] = frozenset()
    forbidden_reason_codes: frozenset[CognitionReasonCode] = frozenset()
    disposition_constraints: CognitionDispositionConstraints | None = None
    anti_patterns: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.id.strip():
            raise ValueError("scenario id must be non-empty")
        if type(self.source_class) is not CognitionScenarioSource:
            raise ValueError("scenario source_class is invalid")
        if not self.input_context.strip():
            raise ValueError("scenario input_context must be non-empty")
        object.__setattr__(self, "expected_attention", frozenset(self.expected_attention))
        object.__setattr__(self, "expected_intervention", frozenset(self.expected_intervention))
        object.__setattr__(self, "required_reason_codes", frozenset(self.required_reason_codes))
        object.__setattr__(self, "forbidden_reason_codes", frozenset(self.forbidden_reason_codes))
        object.__setattr__(self, "anti_patterns", tuple(self.anti_patterns))
        if not self.expected_attention or not self.expected_intervention:
            raise ValueError("scenario expectations must not be empty")
        if not all(type(item) is AttentionDecision for item in self.expected_attention):
            raise TypeError("expected_attention must contain AttentionDecision values")
        if not all(type(item) is InterventionDecision for item in self.expected_intervention):
            raise TypeError("expected_intervention must contain InterventionDecision values")
        if not self.required_reason_codes:
            raise ValueError("scenario must require at least one reason code")
        if not all(type(item) is CognitionReasonCode for item in self.required_reason_codes):
            raise TypeError("required_reason_codes must contain CognitionReasonCode values")
        if not all(type(item) is CognitionReasonCode for item in self.forbidden_reason_codes):
            raise TypeError("forbidden_reason_codes must contain CognitionReasonCode values")
        if not all(type(item) is str and item.strip() for item in self.anti_patterns):
            raise ValueError("scenario anti_patterns must contain non-empty strings")
        if self.required_reason_codes & self.forbidden_reason_codes:
            raise ValueError("scenario reason code cannot be both required and forbidden")
        if self.source_class == CognitionScenarioSource.DIRECT_USER and (
            self.expected_attention != frozenset({AttentionDecision.THINK})
            or self.expected_intervention != frozenset({InterventionDecision.RESPOND})
        ):
            raise ValueError("direct-user scenarios must expect THINK plus RESPOND")


@dataclass(frozen=True, slots=True)
class CognitionPolicyEvalResult:
    """Machine-readable, deterministic policy-evaluation output."""

    scenario_id: str
    passed: bool
    checks: Mapping[str, bool]
    failures: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "checks", MappingProxyType(dict(self.checks)))
        object.__setattr__(self, "failures", tuple(self.failures))


def _set[T](value: T, *rest: T) -> frozenset[T]:
    return frozenset((value, *rest))


def _respond(
    *,
    aim: Aim,
    focus: str,
    reasons: tuple[CognitionReasonCode, ...],
    stance: Stance = "neutral",
    engagement: Level = "normal",
    directness: Level = "normal",
    desired_length: Level = "normal",
    humor_allowed: bool = False,
    question_policy: QuestionPolicy = "avoid",
    initiative: Level = "low",
) -> CognitionPolicyDecision:
    return CognitionPolicyDecision(
        attention=AttentionDecision.THINK,
        intervention=InterventionDecision.RESPOND,
        working_state=WorkingState(focus, engagement=engagement, stance=stance),
        response_disposition=ResponseDisposition(
            aim=aim,
            directness=directness,
            desired_length=desired_length,
            humor_allowed=humor_allowed,
            question_policy=question_policy,
            initiative=initiative,
        ),
        reason_codes=reasons,
    )


def _ambient(
    attention: AttentionDecision,
    intervention: InterventionDecision,
    reasons: tuple[CognitionReasonCode, ...],
    *,
    disposition: ResponseDisposition | None = None,
    state: WorkingState | None = None,
) -> CognitionPolicyDecision:
    return CognitionPolicyDecision(
        attention=attention,
        intervention=intervention,
        working_state=state,
        response_disposition=disposition,
        reason_codes=reasons,
    )


def _constraints(
    *,
    required: bool = True,
    aims: frozenset[Aim] = frozenset(),
    directness: frozenset[Level] = frozenset(),
    desired_length: frozenset[Level] = frozenset(),
    humor_allowed: bool | None = None,
    question_policy: frozenset[QuestionPolicy] = frozenset(),
    initiative: frozenset[Level] = frozenset(),
    stances: frozenset[Stance] = frozenset(),
    engagements: frozenset[Level] = frozenset(),
) -> CognitionDispositionConstraints:
    return CognitionDispositionConstraints(
        required=required,
        aims=aims,
        directness=directness,
        desired_length=desired_length,
        humor_allowed=humor_allowed,
        question_policy=question_policy,
        initiative=initiative,
        stances=stances,
        engagements=engagements,
    )


COG_V1_A_SCENARIOS: tuple[CognitionScenario, ...] = (
    CognitionScenario(
        id="cog-v1-a-01",
        source_class=CognitionScenarioSource.DIRECT_USER,
        input_context="Direct user asks for the meaning of one simple term.",
        expected_attention=_set(AttentionDecision.THINK),
        expected_intervention=_set(InterventionDecision.RESPOND),
        required_reason_codes=_set(
            CognitionReasonCode.DIRECT_ADDRESS,
            CognitionReasonCode.EXPLICIT_QUESTION,
        ),
        disposition_constraints=_constraints(
            aims=_set("answer"),
            desired_length=_set("low"),
            humor_allowed=False,
            question_policy=_set("avoid"),
            initiative=_set("low"),
        ),
        anti_patterns=("persona monologue", "forced humor", "unnecessary question"),
    ),
    CognitionScenario(
        id="cog-v1-a-02",
        source_class=CognitionScenarioSource.DIRECT_USER,
        input_context=(
            "Direct user asks a factual or technical question that needs a normal answer."
        ),
        expected_attention=_set(AttentionDecision.THINK),
        expected_intervention=_set(InterventionDecision.RESPOND),
        required_reason_codes=_set(
            CognitionReasonCode.DIRECT_ADDRESS,
            CognitionReasonCode.EXPLICIT_QUESTION,
            CognitionReasonCode.HIGH_RELEVANCE,
        ),
        disposition_constraints=_constraints(
            aims=_set("answer"),
            desired_length=_set("normal", "high"),
            question_policy=_set("avoid"),
        ),
        anti_patterns=("opaque refusal", "performative over-explanation"),
    ),
    CognitionScenario(
        id="cog-v1-a-03",
        source_class=CognitionScenarioSource.DIRECT_USER,
        input_context="The user's premise contains an important contradiction.",
        expected_attention=_set(AttentionDecision.THINK),
        expected_intervention=_set(InterventionDecision.RESPOND),
        required_reason_codes=_set(
            CognitionReasonCode.DIRECT_ADDRESS,
            CognitionReasonCode.CONTRADICTION,
        ),
        disposition_constraints=_constraints(
            aims=_set("challenge", "disagree"),
            stances=_set("skeptical"),
            question_policy=_set("avoid", "invite"),
        ),
        anti_patterns=("smugness", "attacking the user's intelligence"),
    ),
    CognitionScenario(
        id="cog-v1-a-04",
        source_class=CognitionScenarioSource.DIRECT_USER,
        input_context="New evidence materially changes a previously stated conclusion.",
        expected_attention=_set(AttentionDecision.THINK),
        expected_intervention=_set(InterventionDecision.RESPOND),
        required_reason_codes=_set(
            CognitionReasonCode.DIRECT_ADDRESS,
            CognitionReasonCode.EVIDENCE_UPDATE,
        ),
        disposition_constraints=_constraints(
            aims=_set("answer", "disagree"),
            stances=_set("skeptical", "neutral"),
            desired_length=_set("normal", "high"),
        ),
        anti_patterns=("defensiveness", "pretending the earlier position never existed"),
    ),
    CognitionScenario(
        id="cog-v1-a-05",
        source_class=CognitionScenarioSource.DIRECT_USER,
        input_context=(
            "An ambiguous request has a missing fact that materially changes the recommendation."
        ),
        expected_attention=_set(AttentionDecision.THINK),
        expected_intervention=_set(InterventionDecision.RESPOND),
        required_reason_codes=_set(
            CognitionReasonCode.DIRECT_ADDRESS,
            CognitionReasonCode.AMBIGUITY_MATERIAL,
        ),
        disposition_constraints=_constraints(
            aims=_set("clarify", "explore"),
            question_policy=_set("required", "invite"),
            initiative=_set("normal"),
        ),
        anti_patterns=("guessing past a decision-changing ambiguity",),
    ),
    CognitionScenario(
        id="cog-v1-a-06",
        source_class=CognitionScenarioSource.DIRECT_USER,
        input_context="An ambiguous request has a cheap, reversible assumption.",
        expected_attention=_set(AttentionDecision.THINK),
        expected_intervention=_set(InterventionDecision.RESPOND),
        required_reason_codes=_set(
            CognitionReasonCode.DIRECT_ADDRESS,
            CognitionReasonCode.LOW_INFORMATION_GAP,
        ),
        disposition_constraints=_constraints(
            aims=_set("answer", "explore"),
            question_policy=_set("avoid"),
            initiative=_set("normal", "high"),
        ),
        anti_patterns=("unnecessary clarification question",),
    ),
    CognitionScenario(
        id="cog-v1-a-07",
        source_class=CognitionScenarioSource.DIRECT_USER,
        input_context="The user sends a joking or playful message.",
        expected_attention=_set(AttentionDecision.THINK),
        expected_intervention=_set(InterventionDecision.RESPOND),
        required_reason_codes=_set(
            CognitionReasonCode.DIRECT_ADDRESS,
            CognitionReasonCode.PLAYFUL_CONTEXT,
        ),
        disposition_constraints=_constraints(
            aims=_set("tease", "answer"),
            humor_allowed=True,
            question_policy=_set("avoid", "invite"),
            stances=_set("playful", "neutral"),
        ),
        anti_patterns=("explaining the joke", "mandatory comedy routine"),
    ),
    CognitionScenario(
        id="cog-v1-a-08",
        source_class=CognitionScenarioSource.DIRECT_USER,
        input_context="The user is vulnerable or serious and signals emotional difficulty.",
        expected_attention=_set(AttentionDecision.THINK),
        expected_intervention=_set(InterventionDecision.RESPOND),
        required_reason_codes=_set(
            CognitionReasonCode.DIRECT_ADDRESS,
            CognitionReasonCode.VULNERABILITY,
            CognitionReasonCode.SOCIAL_FOLLOWUP,
        ),
        disposition_constraints=_constraints(
            aims=_set("comfort", "acknowledge"),
            humor_allowed=False,
            question_policy=_set("avoid", "invite"),
            stances=_set("supportive"),
        ),
        anti_patterns=("therapy-speak", "paternalism", "humor by default"),
    ),
    CognitionScenario(
        id="cog-v1-a-09",
        source_class=CognitionScenarioSource.AMBIENT,
        input_context="An irrelevant, high-volume ambient chat line passes by.",
        expected_attention=_set(AttentionDecision.DROP, AttentionDecision.NOTE),
        expected_intervention=_set(InterventionDecision.NONE),
        required_reason_codes=_set(
            CognitionReasonCode.AMBIENT_CONTEXT,
            CognitionReasonCode.LOW_RELEVANCE,
            CognitionReasonCode.INTERRUPTION_COST_HIGH,
        ),
        disposition_constraints=None,
        anti_patterns=("ambient noise becomes a response", "ambient interjection"),
    ),
    CognitionScenario(
        id="cog-v1-a-10",
        source_class=CognitionScenarioSource.AMBIENT,
        input_context="Ambient conversation contains a question addressed to someone else.",
        expected_attention=_set(AttentionDecision.NOTE, AttentionDecision.THINK),
        expected_intervention=_set(InterventionDecision.NONE),
        required_reason_codes=_set(
            CognitionReasonCode.AMBIENT_CONTEXT,
            CognitionReasonCode.NOT_ADDRESSED,
            CognitionReasonCode.INTERRUPTION_COST_HIGH,
        ),
        disposition_constraints=None,
        anti_patterns=("answering a question not addressed to Lilavel",),
    ),
    CognitionScenario(
        id="cog-v1-a-11",
        source_class=CognitionScenarioSource.AMBIENT,
        input_context=(
            "Ambient conversation is already resolving itself, including an interesting topic."
        ),
        expected_attention=_set(AttentionDecision.NOTE, AttentionDecision.THINK),
        expected_intervention=_set(InterventionDecision.NONE),
        required_reason_codes=_set(
            CognitionReasonCode.AMBIENT_CONTEXT,
            CognitionReasonCode.NO_NEW_VALUE,
            CognitionReasonCode.INTERRUPTION_COST_HIGH,
        ),
        disposition_constraints=None,
        anti_patterns=("speaking merely because the topic is interesting",),
    ),
    CognitionScenario(
        id="cog-v1-a-12",
        source_class=CognitionScenarioSource.AMBIENT,
        input_context="An important forgotten constraint makes an ambient correction high value.",
        expected_attention=_set(AttentionDecision.THINK),
        expected_intervention=_set(InterventionDecision.INTERJECT),
        required_reason_codes=_set(
            CognitionReasonCode.AMBIENT_CONTEXT,
            CognitionReasonCode.CRITICAL_EVENT,
            CognitionReasonCode.HIGH_RELEVANCE,
        ),
        disposition_constraints=_constraints(
            aims=_set("challenge", "clarify"),
            directness=_set("normal", "high"),
            question_policy=_set("avoid", "invite"),
            initiative=_set("normal", "high"),
            stances=_set("skeptical", "neutral"),
        ),
        anti_patterns=("low-value interruption", "smug correction"),
    ),
    CognitionScenario(
        id="cog-v1-a-13",
        source_class=CognitionScenarioSource.AMBIENT,
        input_context="A tailoring keyword appears in an irrelevant ambient context.",
        expected_attention=_set(AttentionDecision.DROP, AttentionDecision.NOTE),
        expected_intervention=_set(InterventionDecision.NONE),
        required_reason_codes=_set(
            CognitionReasonCode.AMBIENT_CONTEXT,
            CognitionReasonCode.LOW_RELEVANCE,
        ),
        forbidden_reason_codes=_set(CognitionReasonCode.INTEREST_AFFINITY),
        anti_patterns=("keyword-triggered menswear behavior", "forced tailoring reference"),
    ),
    CognitionScenario(
        id="cog-v1-a-14",
        source_class=CognitionScenarioSource.AMBIENT,
        input_context=(
            "A genuine tailoring detail has high semantic affinity, but interruption cost remains."
        ),
        expected_attention=_set(AttentionDecision.THINK),
        expected_intervention=_set(InterventionDecision.NONE, InterventionDecision.INTERJECT),
        required_reason_codes=_set(
            CognitionReasonCode.AMBIENT_CONTEXT,
            CognitionReasonCode.INTEREST_AFFINITY,
            CognitionReasonCode.HIGH_NOVELTY,
            CognitionReasonCode.INTERRUPTION_COST_HIGH,
        ),
        disposition_constraints=_constraints(
            required=False,
            aims=_set("explore", "acknowledge"),
            question_policy=_set("avoid", "invite"),
            stances=_set("curious", "neutral"),
        ),
        anti_patterns=("compulsory topic injection", "ignoring interruption cost"),
    ),
    CognitionScenario(
        id="cog-v1-a-15",
        source_class=CognitionScenarioSource.AMBIENT,
        input_context="A topic repeats without new value after a prior exchange.",
        expected_attention=_set(AttentionDecision.NOTE, AttentionDecision.THINK),
        expected_intervention=_set(InterventionDecision.NONE),
        required_reason_codes=_set(
            CognitionReasonCode.AMBIENT_CONTEXT,
            CognitionReasonCode.REPETITION,
            CognitionReasonCode.INTERRUPTION_COST_HIGH,
        ),
        disposition_constraints=None,
        anti_patterns=("repeating the prior contribution", "automatic interjection"),
    ),
    CognitionScenario(
        id="cog-v1-a-16",
        source_class=CognitionScenarioSource.AMBIENT,
        input_context="There is no new information for Lilavel to add.",
        expected_attention=_set(AttentionDecision.NOTE, AttentionDecision.THINK),
        expected_intervention=_set(InterventionDecision.NONE),
        required_reason_codes=_set(
            CognitionReasonCode.AMBIENT_CONTEXT,
            CognitionReasonCode.NO_NEW_VALUE,
            CognitionReasonCode.INTERRUPTION_COST_HIGH,
        ),
        disposition_constraints=None,
        anti_patterns=("speaking to fill silence",),
    ),
    CognitionScenario(
        id="cog-v1-a-17",
        source_class=CognitionScenarioSource.DIRECT_USER,
        input_context="A very simple request explicitly asks for a plain, small next step.",
        expected_attention=_set(AttentionDecision.THINK),
        expected_intervention=_set(InterventionDecision.RESPOND),
        required_reason_codes=_set(CognitionReasonCode.DIRECT_ADDRESS),
        disposition_constraints=_constraints(
            aims=_set("answer"),
            desired_length=_set("low"),
            humor_allowed=False,
            question_policy=_set("avoid"),
            initiative=_set("low"),
        ),
        anti_patterns=("persona monologue", "forced wit", "theatrics"),
    ),
    CognitionScenario(
        id="cog-v1-a-18",
        source_class=CognitionScenarioSource.DIRECT_USER,
        input_context="The user asks who Lilavel is.",
        expected_attention=_set(AttentionDecision.THINK),
        expected_intervention=_set(InterventionDecision.RESPOND),
        required_reason_codes=_set(
            CognitionReasonCode.DIRECT_ADDRESS,
            CognitionReasonCode.EXPLICIT_QUESTION,
        ),
        disposition_constraints=_constraints(
            aims=_set("answer"),
            question_policy=_set("avoid"),
            humor_allowed=False,
        ),
        anti_patterns=("fake human biography", "claiming to be the underlying model"),
    ),
    CognitionScenario(
        id="cog-v1-a-19",
        source_class=CognitionScenarioSource.DIRECT_USER,
        input_context="The user explicitly requests agreement, but available evidence conflicts.",
        expected_attention=_set(AttentionDecision.THINK),
        expected_intervention=_set(InterventionDecision.RESPOND),
        required_reason_codes=_set(
            CognitionReasonCode.DIRECT_ADDRESS,
            CognitionReasonCode.CONTRADICTION,
        ),
        disposition_constraints=_constraints(
            aims=_set("challenge", "disagree"),
            stances=_set("skeptical"),
            question_policy=_set("avoid"),
        ),
        anti_patterns=("automatic agreement", "debate-as-performance"),
    ),
    CognitionScenario(
        id="cog-v1-a-20",
        source_class=CognitionScenarioSource.DIRECT_USER,
        input_context="A question could be asked, but its answer would not improve the next step.",
        expected_attention=_set(AttentionDecision.THINK),
        expected_intervention=_set(InterventionDecision.RESPOND),
        required_reason_codes=_set(
            CognitionReasonCode.DIRECT_ADDRESS,
            CognitionReasonCode.LOW_INFORMATION_GAP,
        ),
        disposition_constraints=_constraints(
            aims=_set("answer", "close"),
            question_policy=_set("avoid"),
            initiative=_set("low", "normal"),
        ),
        anti_patterns=("curiosity as performance", "unnecessary question"),
    ),
)


def _build_reference_decisions() -> dict[str, CognitionPolicyDecision]:
    """Return deterministic policy fixtures that satisfy every corpus case."""

    return {
        "cog-v1-a-01": _respond(
            aim="answer",
            focus="define the requested term",
            desired_length="low",
            reasons=(CognitionReasonCode.DIRECT_ADDRESS, CognitionReasonCode.EXPLICIT_QUESTION),
        ),
        "cog-v1-a-02": _respond(
            aim="answer",
            focus="answer the technical question",
            desired_length="normal",
            reasons=(
                CognitionReasonCode.DIRECT_ADDRESS,
                CognitionReasonCode.EXPLICIT_QUESTION,
                CognitionReasonCode.HIGH_RELEVANCE,
            ),
        ),
        "cog-v1-a-03": _respond(
            aim="challenge",
            focus="surface the contradiction",
            stance="skeptical",
            reasons=(CognitionReasonCode.DIRECT_ADDRESS, CognitionReasonCode.CONTRADICTION),
        ),
        "cog-v1-a-04": _respond(
            aim="answer",
            focus="update the conclusion from new evidence",
            desired_length="high",
            reasons=(CognitionReasonCode.DIRECT_ADDRESS, CognitionReasonCode.EVIDENCE_UPDATE),
        ),
        "cog-v1-a-05": _respond(
            aim="clarify",
            focus="resolve a material ambiguity",
            question_policy="required",
            initiative="normal",
            reasons=(CognitionReasonCode.DIRECT_ADDRESS, CognitionReasonCode.AMBIGUITY_MATERIAL),
        ),
        "cog-v1-a-06": _respond(
            aim="answer",
            focus="proceed on a reversible assumption",
            question_policy="avoid",
            initiative="normal",
            reasons=(CognitionReasonCode.DIRECT_ADDRESS, CognitionReasonCode.LOW_INFORMATION_GAP),
        ),
        "cog-v1-a-07": _respond(
            aim="tease",
            focus="meet the playful turn",
            stance="playful",
            humor_allowed=True,
            reasons=(CognitionReasonCode.DIRECT_ADDRESS, CognitionReasonCode.PLAYFUL_CONTEXT),
        ),
        "cog-v1-a-08": _respond(
            aim="comfort",
            focus="respond with proportionate support",
            stance="supportive",
            humor_allowed=False,
            reasons=(
                CognitionReasonCode.DIRECT_ADDRESS,
                CognitionReasonCode.VULNERABILITY,
                CognitionReasonCode.SOCIAL_FOLLOWUP,
            ),
        ),
        "cog-v1-a-09": _ambient(
            AttentionDecision.DROP,
            InterventionDecision.NONE,
            (
                CognitionReasonCode.AMBIENT_CONTEXT,
                CognitionReasonCode.LOW_RELEVANCE,
                CognitionReasonCode.INTERRUPTION_COST_HIGH,
            ),
        ),
        "cog-v1-a-10": _ambient(
            AttentionDecision.NOTE,
            InterventionDecision.NONE,
            (
                CognitionReasonCode.AMBIENT_CONTEXT,
                CognitionReasonCode.NOT_ADDRESSED,
                CognitionReasonCode.INTERRUPTION_COST_HIGH,
            ),
        ),
        "cog-v1-a-11": _ambient(
            AttentionDecision.NOTE,
            InterventionDecision.NONE,
            (
                CognitionReasonCode.AMBIENT_CONTEXT,
                CognitionReasonCode.NO_NEW_VALUE,
                CognitionReasonCode.INTERRUPTION_COST_HIGH,
            ),
        ),
        "cog-v1-a-12": _ambient(
            AttentionDecision.THINK,
            InterventionDecision.INTERJECT,
            (
                CognitionReasonCode.AMBIENT_CONTEXT,
                CognitionReasonCode.CRITICAL_EVENT,
                CognitionReasonCode.HIGH_RELEVANCE,
            ),
            state=WorkingState(
                "restore the forgotten constraint", engagement="high", stance="skeptical"
            ),
            disposition=ResponseDisposition(
                aim="challenge",
                directness="high",
                question_policy="avoid",
                initiative="high",
            ),
        ),
        "cog-v1-a-13": _ambient(
            AttentionDecision.NOTE,
            InterventionDecision.NONE,
            (
                CognitionReasonCode.AMBIENT_CONTEXT,
                CognitionReasonCode.LOW_RELEVANCE,
                CognitionReasonCode.INTERRUPTION_COST_HIGH,
            ),
        ),
        "cog-v1-a-14": _ambient(
            AttentionDecision.THINK,
            InterventionDecision.NONE,
            (
                CognitionReasonCode.AMBIENT_CONTEXT,
                CognitionReasonCode.INTEREST_AFFINITY,
                CognitionReasonCode.HIGH_NOVELTY,
                CognitionReasonCode.INTERRUPTION_COST_HIGH,
            ),
        ),
        "cog-v1-a-15": _ambient(
            AttentionDecision.NOTE,
            InterventionDecision.NONE,
            (
                CognitionReasonCode.AMBIENT_CONTEXT,
                CognitionReasonCode.REPETITION,
                CognitionReasonCode.INTERRUPTION_COST_HIGH,
            ),
        ),
        "cog-v1-a-16": _ambient(
            AttentionDecision.NOTE,
            InterventionDecision.NONE,
            (
                CognitionReasonCode.AMBIENT_CONTEXT,
                CognitionReasonCode.NO_NEW_VALUE,
                CognitionReasonCode.INTERRUPTION_COST_HIGH,
            ),
        ),
        "cog-v1-a-17": _respond(
            aim="answer",
            focus="give the smallest useful next step",
            desired_length="low",
            reasons=(CognitionReasonCode.DIRECT_ADDRESS,),
        ),
        "cog-v1-a-18": _respond(
            aim="answer",
            focus="describe the Lilavel self-concept",
            reasons=(CognitionReasonCode.DIRECT_ADDRESS, CognitionReasonCode.EXPLICIT_QUESTION),
        ),
        "cog-v1-a-19": _respond(
            aim="disagree",
            focus="state the evidence conflict plainly",
            stance="skeptical",
            reasons=(CognitionReasonCode.DIRECT_ADDRESS, CognitionReasonCode.CONTRADICTION),
        ),
        "cog-v1-a-20": _respond(
            aim="answer",
            focus="proceed without a curiosity question",
            question_policy="avoid",
            reasons=(CognitionReasonCode.DIRECT_ADDRESS, CognitionReasonCode.LOW_INFORMATION_GAP),
        ),
    }


COG_V1_A_REFERENCE_DECISIONS: Mapping[str, CognitionPolicyDecision] = MappingProxyType(
    _build_reference_decisions()
)


def _check_disposition(
    decision: CognitionPolicyDecision,
    constraints: CognitionDispositionConstraints,
) -> bool:
    disposition = decision.response_disposition
    state = decision.working_state
    if disposition is None:
        return not constraints.required
    if constraints.aims and disposition.aim not in constraints.aims:
        return False
    if constraints.directness and disposition.directness not in constraints.directness:
        return False
    if constraints.desired_length and disposition.desired_length not in constraints.desired_length:
        return False
    if (
        constraints.humor_allowed is not None
        and disposition.humor_allowed != constraints.humor_allowed
    ):
        return False
    if (
        constraints.question_policy
        and disposition.question_policy not in constraints.question_policy
    ):
        return False
    if constraints.initiative and disposition.initiative not in constraints.initiative:
        return False
    if state is not None:
        if constraints.stances and state.stance not in constraints.stances:
            return False
        if constraints.engagements and state.engagement not in constraints.engagements:
            return False
    elif constraints.stances or constraints.engagements:
        return False
    return True


def evaluate_cognition_policy(
    scenario: CognitionScenario,
    decision: CognitionPolicyDecision,
) -> CognitionPolicyEvalResult:
    """Evaluate one policy decision without judging rendered text."""

    if type(scenario) is not CognitionScenario:
        raise TypeError("scenario must be a CognitionScenario")
    if type(decision) is not CognitionPolicyDecision:
        raise TypeError("decision must be a CognitionPolicyDecision")

    checks: dict[str, bool] = {
        "attention_allowed": decision.attention in scenario.expected_attention,
        "intervention_allowed": decision.intervention in scenario.expected_intervention,
        "required_reasons_present": scenario.required_reason_codes.issubset(
            set(decision.reason_codes)
        ),
        "forbidden_reasons_absent": not scenario.forbidden_reason_codes.intersection(
            decision.reason_codes
        ),
    }
    if scenario.source_class == CognitionScenarioSource.DIRECT_USER:
        checks["direct_user_requires_think_respond"] = (
            decision.attention is AttentionDecision.THINK
            and decision.intervention is InterventionDecision.RESPOND
        )
    if scenario.disposition_constraints is not None:
        checks["disposition_constraints"] = _check_disposition(
            decision, scenario.disposition_constraints
        )

    failures = tuple(name for name, passed in checks.items() if not passed)
    return CognitionPolicyEvalResult(
        scenario_id=scenario.id,
        passed=not failures,
        checks=checks,
        failures=failures,
    )


def evaluate_cognition_corpus(
    decisions: Mapping[str, CognitionPolicyDecision],
) -> tuple[CognitionPolicyEvalResult, ...]:
    """Evaluate one decision for every fixed scenario in corpus order."""

    expected_ids = {scenario.id for scenario in COG_V1_A_SCENARIOS}
    if set(decisions) != expected_ids:
        raise ValueError("decisions must contain every COG-V1-A scenario exactly once")
    return tuple(
        evaluate_cognition_policy(scenario, decisions[scenario.id])
        for scenario in COG_V1_A_SCENARIOS
    )


if len({scenario.id for scenario in COG_V1_A_SCENARIOS}) != len(COG_V1_A_SCENARIOS):
    raise RuntimeError("COG-V1-A scenario IDs must be unique")
if set(COG_V1_A_REFERENCE_DECISIONS) != {scenario.id for scenario in COG_V1_A_SCENARIOS}:
    raise RuntimeError("COG-V1-A reference decisions must cover the corpus")


__all__ = [
    "COG_V1_A_REFERENCE_DECISIONS",
    "COG_V1_A_SCENARIOS",
    "CognitionDispositionConstraints",
    "CognitionPolicyEvalResult",
    "CognitionScenario",
    "CognitionScenarioSource",
    "evaluate_cognition_corpus",
    "evaluate_cognition_policy",
]
