"""Runtime-owned convergence of MIND cognition and trusted application."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from enum import StrEnum

from .cognition_episode import CognitionEpisodeRunner
from .contracts import (
    CognitionEpisodeStatus,
    CognitionOutcome,
    CognitionTrigger,
)
from .proposal_application import (
    ProposalApplicationCoordinator,
    ProposalApplicationResult,
    ProposalApplicationStatus,
)
from .semantic_actor import SemanticCancellationToken, SemanticEpisodeStatus

__all__ = [
    "MindExecutionAdapter",
    "MindExecutionResult",
    "MindExecutionStatus",
]


class MindExecutionStatus(StrEnum):
    """Terminal status returned by the actor-visible MIND executor."""

    COMPLETED_WITH_APPLICATION = "completed_with_application"
    COMPLETED_QUIET = "completed_quiet"
    APPLICATION_REJECTED = "application_rejected"
    APPLICATION_FAILED = "application_failed"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass(frozen=True, slots=True)
class MindExecutionResult:
    """Bounded MIND settlement metadata returned through actor settlement."""

    status: MindExecutionStatus
    application_status: ProposalApplicationStatus | None = None
    cognition_status: CognitionEpisodeStatus | None = None
    reason_code: str | None = None

    def __post_init__(self) -> None:
        if type(self.status) is not MindExecutionStatus:
            raise TypeError("status must be a MindExecutionStatus")
        if (
            self.application_status is not None
            and type(self.application_status) is not ProposalApplicationStatus
        ):
            raise TypeError("application_status must be a ProposalApplicationStatus")
        if (
            self.cognition_status is not None
            and type(self.cognition_status) is not CognitionEpisodeStatus
        ):
            raise TypeError("cognition_status must be a CognitionEpisodeStatus")
        if self.reason_code is not None and (
            type(self.reason_code) is not str
            or not self.reason_code.strip()
            or len(self.reason_code.encode("utf-8")) > 128
        ):
            raise ValueError("reason_code must be non-empty bounded text")

    @property
    def semantic_status(self) -> SemanticEpisodeStatus:
        """Map known MIND terminal outcomes into generic actor settlement."""

        if self.status is MindExecutionStatus.CANCELLED:
            return SemanticEpisodeStatus.CANCELLED
        if self.status in {
            MindExecutionStatus.FAILED,
            MindExecutionStatus.APPLICATION_FAILED,
        }:
            return SemanticEpisodeStatus.FAILED
        return SemanticEpisodeStatus.COMPLETED


class MindExecutionAdapter:
    """Run one trigger through cognition and then trusted proposal application.

    The adapter owns sequencing only. ``CognitionEpisodeRunner`` remains
    effect-free, and ``ProposalApplicationCoordinator`` remains the only
    proposal-to-reality boundary. The actor supplies the cancellation token;
    cancellation before a valid outcome skips application, while cancellation
    after application begins waits for the coordinator's joined settlement.
    """

    def __init__(
        self,
        runner: CognitionEpisodeRunner,
        application: ProposalApplicationCoordinator,
    ) -> None:
        if not isinstance(runner, CognitionEpisodeRunner):  # pyright: ignore[reportUnnecessaryIsInstance]
            raise TypeError("runner must be a CognitionEpisodeRunner")
        if not isinstance(application, ProposalApplicationCoordinator):  # pyright: ignore[reportUnnecessaryIsInstance]
            raise TypeError("application must be a ProposalApplicationCoordinator")
        self._runner = runner
        self._application = application

    @property
    def runner(self) -> CognitionEpisodeRunner:
        return self._runner

    @property
    def application(self) -> ProposalApplicationCoordinator:
        return self._application

    async def execute(
        self,
        trigger: CognitionTrigger,
        cancellation: SemanticCancellationToken,
    ) -> MindExecutionResult:
        """Execute and settle one trigger without retaining semantic content."""

        if type(trigger) is not CognitionTrigger:
            raise TypeError("trigger must be a CognitionTrigger")
        if type(cancellation) is not SemanticCancellationToken:
            raise TypeError("cancellation must be a SemanticCancellationToken")
        if cancellation.is_requested:
            return MindExecutionResult(
                MindExecutionStatus.CANCELLED, reason_code="cancelled_before_run"
            )

        runner_task = asyncio.create_task(
            self._runner.run(trigger), name=f"lilavel-mind-runner-{trigger.trigger_id}"
        )
        cancellation_task = asyncio.create_task(
            cancellation.wait(), name=f"lilavel-mind-cancel-{trigger.trigger_id}"
        )
        application_task: asyncio.Task[ProposalApplicationResult] | None = None
        try:
            done, _ = await asyncio.wait(
                (runner_task, cancellation_task), return_when=asyncio.FIRST_COMPLETED
            )
            if cancellation_task in done and runner_task not in done:
                await _cancel_and_join(runner_task)
                return MindExecutionResult(MindExecutionStatus.CANCELLED, reason_code="cancelled")

            try:
                outcome = runner_task.result()
            except asyncio.CancelledError:
                return MindExecutionResult(
                    MindExecutionStatus.CANCELLED,
                    cognition_status=CognitionEpisodeStatus.CANCELLED,
                    reason_code="runner_cancelled",
                )
            except Exception:
                return MindExecutionResult(
                    MindExecutionStatus.FAILED,
                    cognition_status=self._runner.last_status,
                    reason_code="runner_failed",
                )

            if outcome is None:
                return self._runner_failure()
            if cancellation.is_requested:
                return MindExecutionResult(
                    MindExecutionStatus.CANCELLED,
                    cognition_status=CognitionEpisodeStatus.CANCELLED,
                    reason_code="cancelled_before_application",
                )
            if type(outcome) is not CognitionOutcome or not outcome.is_completed:
                return MindExecutionResult(
                    MindExecutionStatus.FAILED,
                    cognition_status=self._runner.last_status,
                    reason_code="invalid_completed_outcome",
                )

            application_task = asyncio.create_task(
                self._application.apply(outcome),
                name=f"lilavel-mind-application-{trigger.trigger_id}",
            )
            done, _ = await asyncio.wait(
                (application_task, cancellation_task), return_when=asyncio.FIRST_COMPLETED
            )
            if cancellation_task in done and application_task not in done:
                await _cancel_and_join(application_task)
                return MindExecutionResult(
                    MindExecutionStatus.CANCELLED,
                    reason_code="cancelled_after_application_start",
                )

            try:
                result = application_task.result()
            except asyncio.CancelledError:
                return MindExecutionResult(
                    MindExecutionStatus.CANCELLED,
                    reason_code="application_cancelled_after_settlement",
                )
            except Exception:
                return MindExecutionResult(
                    MindExecutionStatus.APPLICATION_FAILED,
                    reason_code="application_failed",
                )
            return self._application_result(result, outcome)
        except asyncio.CancelledError:
            cancellation.request()
            await _cancel_and_join(runner_task)
            if application_task is not None:
                await _cancel_and_join(application_task)
            raise
        finally:
            cancellation_task.cancel()
            await asyncio.gather(cancellation_task, return_exceptions=True)

    def _runner_failure(self) -> MindExecutionResult:
        status = self._runner.last_status
        if status is CognitionEpisodeStatus.CANCELLED:
            return MindExecutionResult(
                MindExecutionStatus.CANCELLED,
                cognition_status=status,
                reason_code="runner_cancelled",
            )
        return MindExecutionResult(
            MindExecutionStatus.FAILED,
            cognition_status=status,
            reason_code=(
                "runner_timed_out"
                if status is CognitionEpisodeStatus.TIMED_OUT
                else "runner_failed"
            ),
        )

    @staticmethod
    def _application_result(
        result: ProposalApplicationResult, outcome: CognitionOutcome
    ) -> MindExecutionResult:
        if result.status is ProposalApplicationStatus.NO_PROPOSALS or outcome.is_quiet:
            return MindExecutionResult(
                MindExecutionStatus.COMPLETED_QUIET,
                application_status=result.status,
            )
        if result.status is ProposalApplicationStatus.APPLIED:
            return MindExecutionResult(
                MindExecutionStatus.COMPLETED_WITH_APPLICATION,
                application_status=result.status,
            )
        if result.status in {
            ProposalApplicationStatus.REJECTED,
            ProposalApplicationStatus.STALE,
            ProposalApplicationStatus.DUPLICATE,
            ProposalApplicationStatus.INELIGIBLE,
        }:
            return MindExecutionResult(
                MindExecutionStatus.APPLICATION_REJECTED,
                application_status=result.status,
                reason_code=result.reason_code or "application_rejected",
            )
        return MindExecutionResult(
            MindExecutionStatus.APPLICATION_FAILED,
            application_status=result.status,
            reason_code=result.reason_code or "application_failed",
        )


async def _cancel_and_join(task: asyncio.Task[object]) -> None:
    if not task.done():
        task.cancel()
    await asyncio.gather(task, return_exceptions=True)
