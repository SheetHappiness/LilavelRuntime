LilavelRuntime may implement Neuro SDK protocol compatibility
as an EnvironmentAdapter.

Neuro SDK does not own Lilavel lifecycle, identity,
memory, cognition, or ToolRegistry.

Protocol mappings:
context -> WorldEvent
register -> ToolRegistry
action -> ToolCall
result -> ToolResult
priority -> wake/interrupt hint