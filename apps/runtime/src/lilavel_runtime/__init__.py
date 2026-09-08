"""Public package boundary for the Lilavel persistent-agent kernel."""

from .contracts import (
    EnvironmentAdapter,
    EventSource,
    EventSubmitter,
    EventTrust,
    JsonValue,
    NeverWakePolicy,
    ToolCall,
    ToolResult,
    ToolSpec,
    WakeDecision,
    WakePolicy,
    WorldEvent,
)
from .kernel import (
    DuplicateEnvironment,
    DuplicateTool,
    LilavelRuntime,
    LilavelRuntimeError,
    LilavelRuntimeHealth,
    RuntimeFailed,
    RuntimeNotRunning,
    RuntimeRegistrationClosed,
    RuntimeShutdownTimeout,
    RuntimeState,
)

__version__ = "0.1.0"

__all__ = [
    "DuplicateEnvironment",
    "DuplicateTool",
    "EnvironmentAdapter",
    "EventSource",
    "EventSubmitter",
    "EventTrust",
    "JsonValue",
    "LilavelRuntime",
    "LilavelRuntimeError",
    "LilavelRuntimeHealth",
    "NeverWakePolicy",
    "RuntimeFailed",
    "RuntimeNotRunning",
    "RuntimeRegistrationClosed",
    "RuntimeShutdownTimeout",
    "RuntimeState",
    "ToolCall",
    "ToolResult",
    "ToolSpec",
    "WakeDecision",
    "WakePolicy",
    "WorldEvent",
    "__version__",
]
