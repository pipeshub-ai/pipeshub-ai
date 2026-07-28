"""Response envelopes returned by a `Model`/`LLMTransport` call.

Before this module, transports returned a bare `Message` and stashed usage
on a mutable `self.last_usage` side-channel the caller had to read
immediately afterwards via `getattr(transport, "last_usage", None)` —
fragile (nothing stops a caller from reading it at the wrong time, or an
implementation from forgetting to set it) and unable to carry per-call
metadata (stop reason, which model actually served the request) at all.
`ModelResponse`/`StructuredResponse` make every call's full outcome an
explicit, immutable return value instead.
"""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field

from app.agent_loop_lib.core.messages import AssistantMessage

__all__ = ["StopReason", "TokenUsage", "ModelResponse", "StructuredResponse", "RunUsage"]


class StopReason(str, Enum):
    END_TURN = "end_turn"
    TOOL_USE = "tool_use"
    MAX_TOKENS = "max_tokens"
    STOP_SEQUENCE = "stop_sequence"
    ERROR = "error"


class TokenUsage(BaseModel):
    """Usage for a single `complete()`/`complete_structured()`/`stream()` call."""

    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0


class ModelResponse(BaseModel):
    """Full outcome of one `Model.complete()`/`LLMTransport.complete()` call."""

    message: AssistantMessage
    usage: TokenUsage = Field(default_factory=TokenUsage)
    stop_reason: StopReason = StopReason.END_TURN
    model: str = ""


class StructuredResponse(BaseModel):
    """Full outcome of one `complete_structured()` call — `data` is the
    already-parsed JSON object matching the caller's `output_schema`."""

    data: dict
    usage: TokenUsage = Field(default_factory=TokenUsage)
    model: str = ""


class RunUsage(BaseModel):
    """Cumulative usage across every LLM call in one `Agent.run()` — the
    live accumulator `Agent` maintains and exposes via `Agent.usage`,
    replacing the old pattern of reading cumulative counters off a
    concrete transport instance (`agent.transport.total_input_tokens`, ...)."""

    requests: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0

    def add(self, request_usage: TokenUsage) -> None:
        self.requests += 1
        self.input_tokens += request_usage.input_tokens
        self.output_tokens += request_usage.output_tokens
        self.cache_read_tokens += request_usage.cache_read_tokens
        self.cache_write_tokens += request_usage.cache_write_tokens
