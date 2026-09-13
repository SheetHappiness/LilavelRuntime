"""Deterministic CTX-V1-C production-composition scenario corpus."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from lilavel_core.production_cognition import build_stable_runtime_guidance

from .context import ContextPurpose
from .context_builder import ContextFrameBuilder
from .context_integration import ProductionContextComposer


@dataclass(frozen=True, slots=True)
class ContextIntegrationEvalScenario:
    id: str
    description: str


@dataclass(frozen=True, slots=True)
class ContextIntegrationEvalResult:
    id: str
    passed: bool
    description: str


@dataclass(frozen=True, slots=True)
class ContextIntegrationEvalReport:
    results: tuple[ContextIntegrationEvalResult, ...]

    @property
    def passed(self) -> int:
        return sum(item.passed for item in self.results)

    @property
    def total(self) -> int:
        return len(self.results)

    @property
    def failed(self) -> tuple[ContextIntegrationEvalResult, ...]:
        return tuple(item for item in self.results if not item.passed)


CTX_V1_C_SCENARIOS: tuple[ContextIntegrationEvalScenario, ...] = (
    ContextIntegrationEvalScenario(
        "stable_operating_canon_all_purposes",
        "The one stable OperatingCanon projection precedes every purpose frame.",
    ),
    ContextIntegrationEvalScenario(
        "purpose_specific_projection",
        "Every production purpose receives its own deterministic purpose block.",
    ),
    ContextIntegrationEvalScenario(
        "volatile_not_in_stable_canon",
        "Runtime frame data is never compiled into stable guidance.",
    ),
    ContextIntegrationEvalScenario(
        "ordinary_user_omits_precise_time",
        "USER_RESPONSE does not render the current timestamp by default.",
    ),
    ContextIntegrationEvalScenario(
        "temporal_renders_current_time",
        "TEMPORAL_WAKE renders the builder's current clock value.",
    ),
    ContextIntegrationEvalScenario(
        "identifiers_not_rendered",
        "Frame, scope, and source identifiers remain out of model-facing blocks.",
    ),
    ContextIntegrationEvalScenario(
        "unknown_is_not_known_empty",
        "Unknown participant state is not rendered as known-empty state.",
    ),
    ContextIntegrationEvalScenario(
        "content_free_evidence",
        "Assembly evidence contains block kinds and byte counts, not projection text.",
    ),
)


def evaluate_context_integration_corpus() -> ContextIntegrationEvalReport:
    """Run the small human-authored CTX-V1-C corpus without a model call."""

    fixed = datetime(2030, 1, 2, 3, 4, 5, tzinfo=UTC)
    composer = ProductionContextComposer(ContextFrameBuilder(clock=lambda: fixed))
    stable = build_stable_runtime_guidance()
    projections = {
        purpose: composer.compose_projection(
            purpose,
            scope_id="scope:current",
            existing_guidance=stable,
        )
        for purpose in ContextPurpose
    }
    rendered = {purpose: "\n\n".join(blocks) for purpose, blocks in projections.items()}
    evidence = composer.evidence()
    checks = {
        "stable_operating_canon_all_purposes": all(
            item.operating_canon_injected for item in evidence
        ),
        "purpose_specific_projection": all(
            f"purpose: {purpose.value}" in rendered[purpose] for purpose in ContextPurpose
        ),
        "volatile_not_in_stable_canon": all(
            all(block not in stable for block in projections[purpose]) for purpose in ContextPurpose
        ),
        "ordinary_user_omits_precise_time": "now:" not in rendered[ContextPurpose.USER_RESPONSE],
        "temporal_renders_current_time": fixed.isoformat()
        in rendered[ContextPurpose.TEMPORAL_WAKE],
        "identifiers_not_rendered": all(
            value not in rendered[ContextPurpose.TEMPORAL_WAKE]
            for value in ("frame:current", "scope:current", "source:current")
        ),
        "unknown_is_not_known_empty": (
            "participants: unknown" in rendered[ContextPurpose.USER_RESPONSE]
            and "participants: known empty" not in rendered[ContextPurpose.USER_RESPONSE]
        ),
        "content_free_evidence": all(
            "\n" not in kind and "current" not in kind
            for item in evidence
            for kind in item.projection_block_kinds
        ),
    }
    return ContextIntegrationEvalReport(
        tuple(
            ContextIntegrationEvalResult(item.id, checks[item.id], item.description)
            for item in CTX_V1_C_SCENARIOS
        )
    )


__all__ = [
    "CTX_V1_C_SCENARIOS",
    "ContextIntegrationEvalReport",
    "ContextIntegrationEvalResult",
    "ContextIntegrationEvalScenario",
    "evaluate_context_integration_corpus",
]
