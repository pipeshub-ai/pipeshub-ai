"""`UsageReportingTransport`: records the usage of every model call a request
makes through its transports, labelled by `llm_usage_source`.

Every agent of a request resolves its model from the request's transport
registry: the main loop, sub-agents, one-turn helpers (intent, goal), context
compaction, plan and result critics, planners. Counting at the transport
covers all of them in one place, including callers that keep only a
response's data and drop its usage. Always wrapped: each call also writes one
`llm_call.end` line, and the `run_usage` frame stays opt-in (`emit_run_usage`).
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import TYPE_CHECKING, Any

from app.agent_loop_lib.core.exceptions import TransportError
from app.agent_loop_lib.core.streaming import StreamCompleteEvent
from app.agent_loop_lib.core.usage_source import current_llm_usage_source
from app.agent_loop_lib.transport.base import LLMTransport, llm_error_class
from app.utils.logging_fields import log_event

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Callable

    from app.agent_loop_lib.core.messages import Message
    from app.agent_loop_lib.core.responses import ModelResponse, StructuredResponse, TokenUsage
    from app.agent_loop_lib.core.streaming import StreamEvent
    from app.agent_loop_lib.core.tool_schema import ToolChoice, ToolSchema

logger = logging.getLogger(__name__)


class UsageReportingTransport(LLMTransport):
    """Decorates any `LLMTransport`, sending each finished call's usage to
    `sink` as `(source, usage)`. A call that raises, or a stream abandoned
    before its final event, has no usage to report.

    Every call, finished or not, also logs one `llm_call.end`: ids, sizes and
    enums only, never messages or output. INFO when it finished; DEBUG when it
    failed, since the retry hook's `llm_call.retry` or the agent's `llm_call.failed`
    is the line that reports it."""

    def __init__(self, inner: LLMTransport, sink: Callable[[str, TokenUsage], None]) -> None:
        self._inner = inner
        self._sink = sink

    @property
    def provider(self) -> str:
        return self._inner.provider

    @property
    def model_name(self) -> str:
        return self._inner.model_name

    def _report(self, usage: TokenUsage | None) -> None:
        if usage is None:
            return
        try:
            self._sink(current_llm_usage_source(), usage)
        except Exception:
            # Accounting must never cost the call it accounts for.
            logger.warning("usage report failed", exc_info=True)

    def _log_end(
        self, started: float, tools: list[ToolSchema] | None, *,
        response: ModelResponse | StructuredResponse | None = None,
        exc: BaseException | None = None, error_class: str | None = None,
    ) -> None:
        try:
            fields: dict[str, Any] = {
                "source": current_llm_usage_source(), "provider": self.provider, "model": self.model_name or None,
                "duration_ms": round((time.perf_counter() - started) * 1000), "tools_offered": len(tools or ()),
            }
            if response is not None:
                usage = response.usage
                # TokenUsage defaults to 0: a provider that reported nothing is not a real 0.
                if usage is not None and (usage.input_tokens or usage.output_tokens):
                    fields |= {
                        "tokens_in": usage.input_tokens, "tokens_out": usage.output_tokens,
                        "cache_read_tokens": usage.cache_read_tokens or None,
                    }
                stop_reason = getattr(response, "stop_reason", None)
                message = getattr(response, "message", None)
                fields |= {
                    "finish_reason": stop_reason.value if stop_reason is not None else None,
                    "tool_calls": len(message.tool_calls or ()) if message is not None else None,
                    "outcome": "success",
                }
            else:
                fields |= {
                    "outcome": "failure",
                    "error_class": error_class or llm_error_class(exc),
                    "error_type": type(exc).__name__ if exc is not None else None,
                    "status": exc.status_code if isinstance(exc, TransportError) else None,
                }
            log_event(logger, "llm_call.end", level=logging.INFO if response is not None else logging.DEBUG, **fields)
        except Exception:
            # Logging must never cost the call it describes.
            logger.debug("llm_call.end not logged", exc_info=True)

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
        started = time.perf_counter()
        try:
            response = await self._inner.complete(
                messages, tools, system, model, thinking_budget, effort, system_blocks,
                **({"tool_choice": tool_choice} if tool_choice else {}),
            )
        except (Exception, asyncio.CancelledError) as exc:
            self._log_end(started, tools, exc=exc)
            raise
        self._report(response.usage)
        self._log_end(started, tools, response=response)
        return response

    async def complete_structured(
        self,
        messages: list[Message],
        output_schema: dict[str, Any],
        system: str | None = None,
        model: str | None = None,
    ) -> StructuredResponse:
        started = time.perf_counter()
        try:
            response = await self._inner.complete_structured(messages, output_schema, system, model)
        except (Exception, asyncio.CancelledError) as exc:
            self._log_end(started, None, exc=exc)
            raise
        self._report(response.usage)
        self._log_end(started, None, response=response)
        return response

    async def stream(
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
        started = time.perf_counter()
        logged = False
        try:
            async for event in self._inner.stream(
                messages, tools, system, model, thinking_budget, effort, system_blocks,
                **({"tool_choice": tool_choice} if tool_choice else {}),
            ):
                if isinstance(event, StreamCompleteEvent):
                    self._report(event.response.usage)
                    self._log_end(started, tools, response=event.response)
                    logged = True
                yield event
        except (Exception, asyncio.CancelledError) as exc:
            if not logged:
                self._log_end(started, tools, exc=exc)
                logged = True
            raise
        finally:
            # Abandoned before its final event: closed by `aclose()`, or the consumer was cancelled.
            if not logged:
                self._log_end(started, tools, error_class="cancelled")


def with_usage_reporting(
    factory: Callable[[], LLMTransport],
    sink: Callable[[str, TokenUsage], None],
) -> Callable[[], LLMTransport]:
    """The registry factory `factory`, its transport wrapped to report usage to `sink`."""
    return lambda: UsageReportingTransport(factory(), sink)


__all__ = ["UsageReportingTransport", "with_usage_reporting"]
