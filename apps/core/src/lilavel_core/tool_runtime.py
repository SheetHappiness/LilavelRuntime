"""Provider-neutral application tool-session seam for an opt-in V3 generation."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from threading import Event
from typing import Literal, Protocol

from lilavel_contracts import ToolCall, ToolResult, ToolSpec

MAX_TOOL_CALLS_PER_BATCH = 4
MAX_TOOL_CALLS_PER_GENERATION = 8
MAX_TOOL_ROUNDS_PER_GENERATION = 4
DEFAULT_TOOL_EXECUTOR_DEADLINE = 10.0
DEFAULT_TOOL_RESULT_WAIT_DEADLINE = 60.0
DEFAULT_TOOL_GENERATION_DEADLINE = 120.0

type ToolSessionSettlement = Literal[
    "idle",
    "running",
    "settled",
    "cancelled",
    "failed",
    "uncontained",
]


@dataclass(frozen=True, slots=True)
class ToolGenerationContext:
    """Application-local identity; only its transport subset crosses the sidecar."""

    runtime_instance_id: str
    scope_id: str
    logical_run_id: str
    generation_id: str
    epoch: int


@dataclass(frozen=True, slots=True)
class ToolBatchCorrelation:
    """Full local fence for one immutable provider-ordered tool batch."""

    context: ToolGenerationContext
    round: int


class ToolSessionUncontained(RuntimeError):
    """An application executor did not settle within its containment deadline."""


class ApplicationToolSession(Protocol):
    """Application-owned execution lifecycle consumed by the V3 model host."""

    @property
    def specs(self) -> tuple[ToolSpec, ...]: ...

    @property
    def settlement(self) -> ToolSessionSettlement: ...

    def execute_batch(
        self,
        correlation: ToolBatchCorrelation,
        calls: Sequence[ToolCall],
        cancelled: Event,
    ) -> tuple[ToolResult, ...]: ...

    def cancel(self) -> None: ...

    def wait_settled(self, timeout: float | None = None) -> bool: ...


class ApplicationToolSessionFactory(Protocol):
    """Create one generation-scoped session from application-owned policy."""

    def create(self, context: ToolGenerationContext) -> ApplicationToolSession: ...
