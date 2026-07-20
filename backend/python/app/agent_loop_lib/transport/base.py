from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from typing import Any

from pydantic import BaseModel

from app.agent_loop_lib.core.messages import Message
from app.agent_loop_lib.core.responses import ModelResponse, StructuredResponse
from app.agent_loop_lib.core.streaming import StreamEvent
from app.agent_loop_lib.core.tool_schema import ToolSchema


class RetryConfig(BaseModel):
    """Exponential backoff retry policy for transport calls."""

    max_retries: int = 3
    initial_delay: float = 1.0      # seconds
    backoff_factor: float = 2.0
    max_delay: float = 60.0
    retryable_status_codes: list[int] = [429, 500, 502, 503, 504]


class LLMTransport(ABC):
    """Provider-agnostic LLM client. Each subclass decides SDK vs raw HTTP internally.

    SDK transports (AnthropicTransport, OpenAITransport, GeminiTransport):
      - Use official SDKs for correct SSE parsing, thinking blocks, streaming tool calls
      - SDK is an implementation detail — never leaks past this interface

    Raw HTTP transports (OllamaTransport, MistralTransport, any OpenAI-compat):
      - Simple NDJSON or straightforward SSE — no SDK needed

    Every call returns its full outcome as an explicit value (`ModelResponse`/
    `StructuredResponse`, or a `StreamEvent` stream terminated by exactly one
    `StreamCompleteEvent`) rather than mutating a side-channel attribute —
    see `core/responses.py`'s module docstring for why this replaced the old
    `self.last_usage` convention.
    """

    @property
    @abstractmethod
    def provider(self) -> str:
        """Identifies this transport (e.g. 'anthropic', 'openai', 'ollama')."""
        ...

    @property
    def model_name(self) -> str:
        """The default model this transport instance was configured with —
        used by `models.transport.TransportModel.model_name`. Concrete
        transports expose their own `_model` attribute; overridden here as
        a convenience default rather than made abstract, since every
        existing transport already stores it under the same private name."""
        return getattr(self, "_model", "") or ""

    @abstractmethod
    async def complete(
        self,
        messages: list[Message],
        tools: list[ToolSchema] | None = None,
        system: str | None = None,
        model: str | None = None,
        thinking_budget: int | None = None,
        effort: str | None = None,
    ) -> ModelResponse:
        """Single-shot completion. Returns the full `ModelResponse`
        (assembled message + usage + stop reason).

        `thinking_budget`/`effort` are optional per-call knobs (extended
        thinking token budget; reasoning effort level) passed through from
        AgentConfig — see agent/prompting.py. Concrete transports that don't
        support a given knob for their provider/model should silently
        ignore it rather than raising, since the agent loop only sends
        these when a caller opted in via AgentConfig, not on every call.
        """
        ...

    @abstractmethod
    async def complete_structured(
        self,
        messages: list[Message],
        output_schema: dict[str, Any],
        system: str | None = None,
        model: str | None = None,
    ) -> StructuredResponse:
        """Force structured JSON output matching output_schema.

        Used by: Planner (→ Plan), IntentParser (→ Intent), any typed agent output.
        Implementations use tool_choice forced call or native JSON mode.
        """
        ...

    @abstractmethod
    def stream(
        self,
        messages: list[Message],
        tools: list[ToolSchema] | None = None,
        system: str | None = None,
        model: str | None = None,
        thinking_budget: int | None = None,
        effort: str | None = None,
    ) -> AsyncIterator[StreamEvent]:
        """Streaming completion. Yields zero or more delta events
        (`TextDeltaEvent`/`ThinkingDeltaEvent`/`ToolCallDeltaEvent`) as they
        arrive, followed by exactly one `StreamCompleteEvent` carrying the
        fully-assembled `ModelResponse` — callers distinguish "delta" from
        "final" by event type, not by inspecting message shape or yield
        position.

        `thinking_budget`/`effort` mirror `complete()`'s knobs — same
        best-effort-ignore-if-unsupported contract. Implementations should
        apply the same prompt-cache breakpoints to the request as
        `complete()` does, for streaming/non-streaming call-cost parity.
        """
        ...
