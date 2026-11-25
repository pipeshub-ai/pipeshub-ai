# pyright: basic
from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from typing import Any

from pydantic import BaseModel

from harness_core.kernel.core.exceptions import TransportError
from harness_core.kernel.core.messages import Message
from harness_core.kernel.core.responses import ModelResponse, StructuredResponse
from harness_core.kernel.core.streaming import StreamEvent
from harness_core.kernel.core.tool_schema import ToolChoice, ToolSchema
from harness_core.log_lines import classify_error
from harness_core.models.transports.failures import RETRYABLE_STATUS_CODES


def llm_error_class(exc: BaseException) -> str:
    """`classify_error` for a failed model call. A provider 401/403 refused the key
    this deployment configured, so it is `misconfigured`, not a user's `auth_expired`."""
    if isinstance(exc, TransportError) and exc.status_code in (401, 403):
        return "misconfigured"
    return classify_error(exc)


class RetryConfig(BaseModel):
    """Exponential backoff retry policy for transport calls."""

    max_retries: int = 3
    initial_delay: float = 1.0      # seconds
    backoff_factor: float = 2.0
    max_delay: float = 60.0
    # The retry hooks check a status here after the transport flagged it retryable, so the default has to be the
    # transports' own set or a status one allows the other refuses.
    retryable_status_codes: list[int] = sorted(RETRYABLE_STATUS_CODES)


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
    def accepts_cache_marks(self) -> bool:
        """Whether ``complete``/``stream`` take ``cache_marks`` (message indices that end a
        cacheable band, set by the context compiler, P4.1). A transport that places
        breakpoints overrides this and the keyword; one that caches prefixes on its own
        (OpenAI, Gemini) needs neither."""
        return False

    @property
    def accepts_output_limit(self) -> bool:
        """Whether ``complete``/``stream``/``complete_structured`` take ``max_output_tokens`` and put it on the
        provider's wire (P4.17). A transport that does not is called exactly as before, with no limit."""
        return False

    def keep_provider_state(self) -> bool:
        """Capture what this provider needs back (``AssistantMessage.provider_state``) and send
        it on later requests, for its own wire only (P4.3). ``False``: this transport has none
        to keep, and its requests are unchanged."""
        return False

    @property
    def provider_state_wire(self) -> str | None:
        """The wire whose ``provider_state`` this transport sends back and captures on its next request, as it is
        configured now; ``None``: it carries none (WP-62: the model gateway passes an engine's reasoning through only
        to such a transport)."""
        return None

    @property
    def honoured_call_options(self) -> frozenset[str]:
        """The ``models.call_options`` this transport puts on its provider's wire; the gateway names any other
        option an engine sent as dropped."""
        return frozenset()

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
        system_blocks: list[str] | None = None,
        *,
        tool_choice: ToolChoice | None = None,
    ) -> ModelResponse:
        """Single-shot completion. Returns the full `ModelResponse`
        (assembled message + usage + stop reason).

        `thinking_budget`/`effort` are optional per-call knobs (extended
        thinking token budget; reasoning effort level) passed through from
        AgentConfig — see agent/prompting.py. Concrete transports that don't
        support a given knob for their provider/model should silently
        ignore it rather than raising, since the agent loop only sends
        these when a caller opted in via AgentConfig, not on every call.

        `system_blocks` is an optional stable/volatile split of the system
        prompt. Anthropic attaches `cache_control` to the stable block only;
        other transports may ignore it and use the joined `system` string.

        `tool_choice="none"` asks for a reply without tool calls while `tools`
        is still sent. A provider with no way to say so (Bedrock Converse,
        Ollama) gets the request it would get without it.
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
        system_blocks: list[str] | None = None,
        *,
        tool_choice: ToolChoice | None = None,
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
        `system_blocks` and `tool_choice` mirror `complete()`.
        """
        ...
