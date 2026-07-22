"""Error hierarchy for the path-based tool/middleware/hook system.

Merged with `agent_loop.core.exceptions` rather than duplicating it: existing
code that catches `ToolError` or `RegistryError` broadly keeps working
unchanged, while new call sites can catch the more specific subclasses below.
"""

from __future__ import annotations

from app.agent_loop_lib.core.exceptions import AgentLoopError, RegistryError, ToolError

__all__ = [
    "AgentOSError",
    "ToolError",
    "ToolValidationError",
    "ToolNotFoundError",
    "DuplicateToolPathError",
    "DuplicateToolNameError",
    "ToolPathMismatchError",
    "InvalidToolPathError",
    "ToolDeniedError",
    "UnknownHookEventError",
]

# Alias rather than a new base class: every tool/middleware/hook error IS an
# AgentLoopError, so `except AgentLoopError` (already used at several call
# sites) keeps catching them without change.
AgentOSError = AgentLoopError


class ToolValidationError(ToolError):
    """Raised when tool call arguments fail validation against declared parameters."""


class ToolNotFoundError(RegistryError):
    """Raised when a tool path or name cannot be resolved in the registry."""

    def __init__(self, path: str):
        super().__init__(f"no tool registered at path: {path!r}")
        self.path = path


class DuplicateToolPathError(ToolError):
    """Raised when two tools attempt to register under the same path."""

    def __init__(self, path: str):
        super().__init__(f"a tool is already registered at path: {path!r}")
        self.path = path


class DuplicateToolNameError(ToolError):
    """Raised when two tools attempt to register under the same short name.

    Short names must stay globally unique because they are what the LLM's
    function-calling interface addresses (`ToolCall.name`); paths are for
    middleware routing, not model-facing addressing.
    """

    def __init__(self, name: str):
        super().__init__(f"a tool is already registered under the name: {name!r}")
        self.name = name


class ToolPathMismatchError(ToolError):
    """Raised when a tool's path is inconsistent with its owning toolset's path_prefix."""

    def __init__(self, tool_path: str, expected_prefix: str, tool_name: str):
        super().__init__(
            f"tool path {tool_path!r} does not match expected path "
            f"'{expected_prefix}/{tool_name}' derived from toolset path_prefix + tool name"
        )
        self.tool_path = tool_path
        self.expected_prefix = expected_prefix
        self.tool_name = tool_name


class InvalidToolPathError(ToolError):
    """Raised when a path does not conform to the '/segment/segment/...' convention."""

    def __init__(self, path: str, reason: str):
        super().__init__(f"invalid tool path {path!r}: {reason}")
        self.path = path
        self.reason = reason


class ToolDeniedError(ToolError):
    """Raised by strict callers that opt into exceptions instead of a failed ToolResult."""

    def __init__(self, path: str, reason: str | None):
        super().__init__(f"tool call to {path!r} denied: {reason}")
        self.path = path
        self.reason = reason


class UnknownHookEventError(AgentLoopError):
    """Raised when `HookRegistry.on(event)` is called with an unregistered event."""

    def __init__(self, event: object, known_events: list[str]):
        super().__init__(
            f"unknown hook event: {event!r}. Known events: {known_events}. "
            "Use HookRegistry.register_event(name, pipeline) to add custom events."
        )
        self.event = event
        self.known_events = known_events
