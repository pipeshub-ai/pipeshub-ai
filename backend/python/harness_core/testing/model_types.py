"""Provider-neutral request, event and error shapes used by ScriptedModel.

Deliberately minimal: product adapters (e.g. ``app.harness.testing_adapters``)
translate their own message types to and from these.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Literal, Protocol

from pydantic import BaseModel, ConfigDict

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Mapping

    from pydantic import JsonValue

Role = Literal["system", "user", "assistant", "tool"]
ErrorKind = Literal["server_error", "rate_limited", "content_filter", "timeout", "malformed"]


class Usage(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0

    def __add__(self, other: Usage) -> Usage:
        return Usage(
            input_tokens=self.input_tokens + other.input_tokens,
            output_tokens=self.output_tokens + other.output_tokens,
            cache_read_tokens=self.cache_read_tokens + other.cache_read_tokens,
            cache_write_tokens=self.cache_write_tokens + other.cache_write_tokens,
        )


@dataclass(frozen=True)
class RequestMessage:
    role: Role
    text: str = ""
    tool_name: str | None = None
    tool_status: Literal["succeeded", "failed"] | None = None
    # A user-role message the harness wrote (a context tail, a recalled note, a nudge), not the user.
    injected: bool = False


@dataclass(frozen=True)
class ModelRequest:
    messages: tuple[RequestMessage, ...]
    tools: tuple[str, ...] = ()

    def last(self, role: Role) -> RequestMessage | None:
        return next((m for m in reversed(self.messages) if m.role == role), None)

    def last_said_by_user(self) -> RequestMessage | None:
        """The user's own latest message: harness-written user-role messages are skipped."""
        return next((m for m in reversed(self.messages) if m.role == "user" and not m.injected), None)


@dataclass(frozen=True)
class ToolCall:
    id: str
    name: str
    args: Mapping[str, JsonValue] = field(default_factory=dict[str, "JsonValue"])


@dataclass(frozen=True)
class ModelResponse:
    turn_index: int
    text: str
    reasoning: str
    tool_calls: tuple[ToolCall, ...]
    usage: Usage
    stop_reason: str


@dataclass(frozen=True)
class TextDelta:
    text: str


@dataclass(frozen=True)
class ReasoningDelta:
    text: str


@dataclass(frozen=True)
class ToolCallEvent:
    call: ToolCall


@dataclass(frozen=True)
class Completed:
    response: ModelResponse


ModelEvent = TextDelta | ReasoningDelta | ToolCallEvent | Completed


class StreamingModel(Protocol):
    """What product adapters need from a model double: ``ScriptedModel`` and
    ``replay.RecordingModel`` both satisfy it."""

    def stream(self, request: ModelRequest) -> AsyncIterator[ModelEvent]: ...

    async def complete(self, request: ModelRequest) -> ModelResponse: ...


class ModelCallError(Exception):
    def __init__(
        self,
        kind: ErrorKind,
        message: str,
        *,
        status: int | None,
        retry_after_s: float | None,
        retryable: bool,
    ) -> None:
        super().__init__(message)
        self.kind: ErrorKind = kind
        self.status = status
        self.retry_after_s = retry_after_s
        self.retryable = retryable


class ScriptMismatch(AssertionError):
    """The harness sent a request the script's ``expect`` guard did not allow."""


class ScriptExhausted(AssertionError):
    """The harness called the model more times than the script has turns."""


__all__ = [
    "Completed",
    "ErrorKind",
    "ModelCallError",
    "ModelEvent",
    "ModelRequest",
    "ModelResponse",
    "ReasoningDelta",
    "RequestMessage",
    "Role",
    "ScriptExhausted",
    "ScriptMismatch",
    "StreamingModel",
    "TextDelta",
    "ToolCall",
    "ToolCallEvent",
    "Usage",
]
