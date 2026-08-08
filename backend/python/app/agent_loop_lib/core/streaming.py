"""Streaming event contract: what `Model.stream()`/`LLMTransport.stream()`
yield, replacing the old "yield partial Messages, last one is the real one,
tell them apart positionally" convention (undiscoverable from the type
signature alone, and impossible to express a mid-stream tool-call delta
in — a `Message` has no notion of "partial tool call").

Every event is explicitly tagged (`type` discriminator) so a consumer
(`Agent.step()`'s streaming branch, the CLI's incremental renderer) can
`match`/dispatch on shape instead of on yield position.
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, Field

from app.agent_loop_lib.core.responses import ModelResponse

__all__ = [
    "TextDeltaEvent",
    "ThinkingDeltaEvent",
    "ToolCallDeltaEvent",
    "StreamCompleteEvent",
    "StreamEvent",
]


class TextDeltaEvent(BaseModel):
    type: Literal["text_delta"] = "text_delta"
    delta: str


class ThinkingDeltaEvent(BaseModel):
    type: Literal["thinking_delta"] = "thinking_delta"
    delta: str


class ToolCallDeltaEvent(BaseModel):
    """A fragment of a tool call under construction. `index` disambiguates
    multiple concurrent tool calls in one response (providers stream tool
    call arguments incrementally, indexed by position) — accumulate by
    `index` until `StreamCompleteEvent` arrives with the fully-assembled
    `ToolCall`s on `response.message.tool_calls`."""

    type: Literal["tool_call_delta"] = "tool_call_delta"
    index: int
    id: str | None = None
    name: str | None = None
    arguments_delta: str = ""


class StreamCompleteEvent(BaseModel):
    """Terminal event: exactly one per `stream()` call, carrying the fully
    assembled `ModelResponse` (same shape `complete()` would have returned
    for the same input) — same "always exactly one, always last" contract
    the old design had for its final `Message`, now made explicit in the type."""

    type: Literal["complete"] = "complete"
    response: ModelResponse


StreamEvent = Annotated[
    TextDeltaEvent | ThinkingDeltaEvent | ToolCallDeltaEvent | StreamCompleteEvent,
    Field(discriminator="type"),
]
