from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterator, Callable
from typing import Any

from app.agent_loop_lib.core.exceptions import TransportError
from app.agent_loop_lib.core.messages import (
    AssistantMessage,
    Message,
    MessageRole,
    TextPart,
    ToolCall,
    as_sent,
)
from app.agent_loop_lib.core.responses import (
    ModelResponse,
    StopReason,
    StructuredResponse,
    TokenUsage,
)
from app.agent_loop_lib.core.streaming import (
    StreamCompleteEvent,
    StreamEvent,
    TextDeltaEvent,
)
from app.agent_loop_lib.core.tool_schema import ToolChoice, ToolSchema
from app.agent_loop_lib.transport.base import LLMTransport
from app.agent_loop_lib.transport.content_filter import (
    CONTENT_FILTER_STOP_REASONS,
    content_filter_error,
)
from app.agent_loop_lib.transport.openai_responses import normalise_tool_call
from app.agent_loop_lib.transport.prompt_cache import (
    anthropic_breakpoint,
    anthropic_top_level_allowed,
)
from app.agent_loop_lib.transport.provider_conflicts import (
    is_prompt_cache_param_conflict,
)

logger = logging.getLogger(__name__)

# Status codes considered transient/retryable regardless of which SDK exception
# type raised them. Kept in sync with RetryConfig.retryable_status_codes default.
# 529 is Anthropic's "overloaded_error" — capacity exhaustion across all
# customers, not a client-side problem, and the single most common
# transient failure in production (see anthropic.APIStatusError.status_code).
_RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504, 529}

_STOP_REASON_MAP: dict[str | None, StopReason] = {
    "end_turn": StopReason.END_TURN,
    "tool_use": StopReason.TOOL_USE,
    "max_tokens": StopReason.MAX_TOKENS,
    "stop_sequence": StopReason.STOP_SEQUENCE,
}


class AnthropicTransport(LLMTransport):
    """Anthropic Messages API via the official SDK.

    Install: pip install 'agent-loop[anthropic]'

    The SDK is used internally to handle SSE event ordering, extended thinking
    blocks, and input_json_delta accumulation correctly. It never leaks past
    this class — all callers see only the LLMTransport interface.
    """

    DEFAULT_MODEL = "claude-sonnet-5"

    # 20k stays under the SDK's ~21.3k non-streaming ceiling (it refuses
    # non-streaming requests expected to run past its 10-minute timeout)
    # while giving synthesis-style roles enough room to return a long
    # report through a task_complete argument without truncation.
    DEFAULT_MAX_TOKENS = 20_000

    def __init__(
        self,
        api_key: str,
        model: str = DEFAULT_MODEL,
        max_tokens: int = DEFAULT_MAX_TOKENS,
        thinking: dict[str, Any] | None = None,
        temperature: float | None = None,
        timeout: float | None = None,
        max_retries: int | None = None,
        model_key: str | None = None,
        cache_ttl: str | None = "5m",
        on_cache_rejected: Callable[[], None] | None = None,
        http_client: Any | None = None,
    ) -> None:
        super().__init__()
        try:
            import anthropic as _anthropic
        except ImportError as exc:
            raise ImportError(
                "anthropic SDK is required for AnthropicTransport. "
                "Install it with: pip install 'agent-loop[anthropic]'"
            ) from exc
        self._anthropic = _anthropic
        self._api_key = api_key
        self._model = model
        self._max_tokens = max_tokens
        # Captured from the configured model, not passed per call: LangChain
        # bakes thinking into the model object, so threading it per call would
        # itself be a behavioural difference. See from_langchain_model.
        self._thinking = thinking
        self._temperature = temperature
        self._model_key = model_key
        client_kwargs: dict[str, Any] = {"api_key": api_key}
        if timeout is not None:
            client_kwargs["timeout"] = timeout
        if max_retries is not None:
            client_kwargs["max_retries"] = max_retries
        if http_client is not None:
            client_kwargs["http_client"] = http_client
        self._client = _anthropic.AsyncAnthropic(**client_kwargs)
        # One marker value for the tools, the stable system block and the
        # request's last block; None sends no cache markers at all.
        self._breakpoint = anthropic_breakpoint(cache_ttl) if cache_ttl else None
        self._on_cache_rejected = on_cache_rejected
        # The client can be pointed elsewhere (ANTHROPIC_BASE_URL); only
        # Anthropic's own API is known to take the top-level marker.
        self._top_level_cache = anthropic_top_level_allowed(str(self._client.base_url))
        # Cumulative usage across all calls on this transport instance —
        # diagnostic only; `Agent.usage` (a `RunUsage` built from each call's
        # returned `ModelResponse.usage`) is the source of truth callers
        # should read from.
        self.total_input_tokens: int = 0
        self.total_output_tokens: int = 0
        self.total_llm_calls: int = 0
        self.total_cache_read_tokens: int = 0
        self.total_cache_write_tokens: int = 0


    @classmethod
    def from_langchain_model(
        cls, llm: Any, model_name: str = "", model_key: str | None = None, cache_ttl: str | None = "5m",
        on_cache_rejected: Callable[[], None] | None = None,
        http_client: Any | None = None,
    ) -> "AnthropicTransport":
        """Build from an already-configured `ChatAnthropic`.

        Same contract as the OpenAI/Azure transports: capture what the LangChain
        arm would send rather than re-deriving it.

        Thinking is read out of the request payload LangChain builds, not off an
        attribute. `aimodels` sets `reasoning_effort`, and langchain-anthropic
        translates that into `thinking` only at request-build time -- on the live
        deployment it produces `{"type": "adaptive", "display": "summarized"}`,
        which is not something to hand-derive from the effort string without
        drifting the moment that mapping changes. Falls back to an explicit
        `thinking` attribute, then to nothing.
        """
        def _val(name: str) -> str:
            raw = getattr(llm, name, None)
            secret = getattr(raw, "get_secret_value", None)
            return (secret() if callable(secret) else raw) or ""

        api_key = _val("anthropic_api_key") or _val("api_key")
        if not api_key:
            raise ValueError(
                f"{type(llm).__name__} has no anthropic_api_key; the direct "
                "Anthropic transport only supports ChatAnthropic-configured models"
            )

        max_tokens = getattr(llm, "max_tokens", None) or cls.DEFAULT_MAX_TOKENS
        thinking = cls._thinking_from(llm)
        if thinking is None:
            configured = getattr(llm, "thinking", None)
            thinking = configured if isinstance(configured, dict) else None

        timeout = getattr(llm, "default_request_timeout", None)
        if timeout is None:
            timeout = getattr(llm, "timeout", None)
        max_retries = getattr(llm, "max_retries", None)

        return cls(
            api_key=api_key,
            model=model_name or getattr(llm, "model", "") or cls.DEFAULT_MODEL,
            max_tokens=int(max_tokens),
            thinking=thinking,
            # Anthropic rejects temperature together with extended thinking, and
            # aimodels already leaves it unset for thinking models -- carry
            # whatever it decided rather than second-guessing it here.
            temperature=getattr(llm, "temperature", None),
            timeout=timeout if isinstance(timeout, (int, float)) else None,
            max_retries=max_retries if isinstance(max_retries, int) else None,
            model_key=model_key,
            cache_ttl=cache_ttl,
            on_cache_rejected=on_cache_rejected,
            http_client=http_client,
        )

    @staticmethod
    def _thinking_from(llm: Any) -> dict[str, Any] | None:
        """`thinking` out of the payload langchain-anthropic would POST.

        Private SDK surface, so it is wrapped: a langchain-anthropic upgrade that
        renames it degrades to no thinking rather than raising mid-request, and
        the parity test pins the behaviour so the build catches it first.
        """
        builder = getattr(llm, "_get_request_payload", None)
        if not callable(builder):
            return None
        try:
            from langchain_core.messages import HumanMessage

            payload = builder([HumanMessage(content="x")], stop=None)
        except Exception:
            return None
        thinking = payload.get("thinking") if isinstance(payload, dict) else None
        return thinking if isinstance(thinking, dict) else None

    @property
    def provider(self) -> str:
        return "anthropic"

    @property
    def model_name(self) -> str:
        return self._model

    # ------------------------------------------------------------------
    # Message formatting helpers
    # ------------------------------------------------------------------

    def _format_messages(self, messages: list[Message]) -> list[dict[str, Any]]:
        """Framework messages into the request's ``messages``, one user message
        per run of tool results.

        The API would accept one user message per result (it merges consecutive
        user turns), but Anthropic's parallel tool use guide calls that shape
        wrong: it "teaches" Claude to stop calling tools in parallel, and the
        loop then takes one round trip per tool.
        """
        formatted: list[dict[str, Any]] = []
        grouped = 0
        previous_was_tool = False
        for msg in messages:
            entry = self._format_message(msg)
            is_tool = msg.role == MessageRole.TOOL
            if is_tool and previous_was_tool:
                formatted[-1]["content"].extend(entry["content"])
                grouped += 1
            else:
                formatted.append(entry)
            previous_was_tool = is_tool
        if grouped:
            logger.debug("anthropic: grouped %d parallel tool result(s) into their run's user message", grouped)
        return formatted

    def _format_message(self, msg: Message) -> dict[str, Any]:
        """Convert a framework Message to Anthropic API message dict."""
        msg = as_sent(msg)
        if msg.role == MessageRole.TOOL:
            step_footer = getattr(msg, "step_footer", "")
            content = msg.content
            if isinstance(content, list):
                formatted_parts = [self._format_part(p) for p in content]
                if step_footer:
                    formatted_parts.append({"type": "text", "text": step_footer})
                block_content: Any = formatted_parts
            else:
                block_content = (content or "") + step_footer
            block: dict[str, Any] = {
                "type": "tool_result",
                "tool_use_id": msg.tool_call_id,
                "content": block_content,
            }
            if getattr(msg, "is_error", False):
                block["is_error"] = True
            return {"role": "user", "content": [block]}
        if msg.role == MessageRole.ASSISTANT:
            content: list[dict] = []
            text = msg.text
            if text:
                content.append({"type": "text", "text": text})
            for tc in msg.tool_calls or []:
                content.append(
                    {
                        "type": "tool_use",
                        "id": tc.id,
                        "name": tc.name,
                        "input": tc.arguments,
                    }
                )
            return {"role": "assistant", "content": content or [{"type": "text", "text": ""}]}
        if msg.role == MessageRole.USER and isinstance(msg.content, list):
            return {"role": "user", "content": [self._format_part(p) for p in msg.content]}
        return {"role": msg.role.value, "content": msg.content or ""}

    @staticmethod
    def _format_part(part: Any) -> dict[str, Any]:
        if isinstance(part, TextPart):
            return {"type": "text", "text": part.text}
        if part.__class__.__name__ == "ImagePart":
            source = part.source
            # The two source shapes are not interchangeable: a url source
            # carries `url`, a base64 source carries `media_type` + `data`.
            # Sending `data` under `type: "url"` is a 400.
            if source.type == "url":
                return {"type": "image", "source": {"type": "url", "url": source.data}}
            return {
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": source.media_type or "image/png",
                    "data": source.data,
                },
            }
        return {"type": "text", "text": getattr(part, "thinking", "")}

    def _parse_response(self, response: Any) -> AssistantMessage:
        """Convert an Anthropic SDK response to a framework AssistantMessage."""
        tool_calls: list[ToolCall] = []
        text: str | None = None
        for block in response.content:
            if block.type == "tool_use":
                # Through the shared normaliser for the same 128-char name clamp
                # the LangChain arm applies (converters._clamp_tool_call_name):
                # an over-long hallucinated name otherwise reaches history and is
                # re-sent every turn. Arguments arrive already parsed here -- the
                # SDK accumulates input_json_delta itself -- so the JSON repair
                # half is a no-op, unlike the OpenAI paths.
                tool_calls.append(
                    normalise_tool_call(
                        block.id,
                        block.name,
                        json.dumps(dict(block.input or {})),
                    )
                )
            elif block.type == "text":
                text = block.text
            # thinking blocks are intentionally ignored

        # stop_reason == "max_tokens" means the response was cut off at the
        # output-token cap — text is incomplete and any trailing tool_use
        # block's input may be empty or partial (the API can't return
        # half-generated JSON). Surface it so the turn loop can recover
        # instead of executing a tool call with silently-missing arguments.
        truncated = getattr(response, "stop_reason", None) == "max_tokens"

        return AssistantMessage(
            content=text,
            tool_calls=tool_calls or None,
            truncated=truncated,
        )

    def _usage_from(self, response: Any) -> TokenUsage:
        """Extract this single call's `TokenUsage`, defensively coercing
        missing/malformed usage fields to 0 rather than raising — a provider
        response without well-formed usage should never crash the turn loop,
        it should just record zero usage for that call."""
        usage = getattr(response, "usage", None)

        def _int_field(obj: Any, name: str) -> int:
            value = getattr(obj, name, 0) if obj is not None else 0
            return value if isinstance(value, int) else 0

        output_tokens = _int_field(usage, "output_tokens")
        cache_read = _int_field(usage, "cache_read_input_tokens")
        cache_write = _int_field(usage, "cache_creation_input_tokens")
        # Anthropic's input_tokens counts only what came after the last cache
        # breakpoint; TokenUsage.input_tokens includes cached tokens.
        input_tokens = _int_field(usage, "input_tokens") + cache_read + cache_write

        self.total_input_tokens += input_tokens
        self.total_output_tokens += output_tokens
        self.total_llm_calls += 1
        self.total_cache_read_tokens += cache_read
        self.total_cache_write_tokens += cache_write

        return TokenUsage(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cache_read_tokens=cache_read,
            cache_write_tokens=cache_write,
        )

    def _check_output(
        self, response: Any, message: AssistantMessage, usage: TokenUsage, context: str,
    ) -> None:
        if message.text or message.tool_calls:
            return
        stop_reason = getattr(response, "stop_reason", None)
        logger.warning(
            "AnthropicTransport.%s: model=%s returned no output (response_id=%s "
            "stop_reason=%s input_tokens=%d output_tokens=%d)",
            context, self._model, getattr(response, "id", None), stop_reason,
            usage.input_tokens, usage.output_tokens,
        )
        if stop_reason in CONTENT_FILTER_STOP_REASONS:
            raise content_filter_error(context, f"stop_reason={stop_reason}")

    def _wrap_error(self, exc: Exception, context: str) -> TransportError:
        """Classify an SDK exception into a TransportError with retry metadata."""
        status_code = getattr(exc, "status_code", None)
        is_network = isinstance(
            exc,
            (self._anthropic.APIConnectionError, self._anthropic.APITimeoutError),
        )
        retryable = is_network or (
            status_code is not None and status_code in _RETRYABLE_STATUS_CODES
        )
        return TransportError(
            f"Anthropic API error ({context}): {exc}",
            status_code=status_code,
            retryable=retryable,
        )

    # ------------------------------------------------------------------
    # Public LLMTransport interface
    # ------------------------------------------------------------------

    def _drop_cache_params(self, exc: Exception) -> bool:
        """Whether `exc` is the API refusing the cache markers this transport
        sends (a proxy behind ANTHROPIC_BASE_URL, say); if so, this and every
        later request go without them."""
        if self._breakpoint is None or not is_prompt_cache_param_conflict(exc, {"cache_control"}):
            return False
        logger.warning("AnthropicTransport: cache_control rejected; resending without cache markers: %s", exc)
        self._breakpoint = None
        if self._on_cache_rejected is not None:
            self._on_cache_rejected()
        return True

    def _message_cache(self, formatted: list[dict]) -> dict[str, Any]:
        """Cache everything up to the request's last block: the top-level
        ``cache_control`` (returned as a request kwarg) where the API takes it,
        otherwise a marker on the last block of the last non-assistant message.
        Each turn then reads every earlier turn from cache."""
        if self._breakpoint is None:
            return {}
        if self._top_level_cache:
            return {"cache_control": dict(self._breakpoint)}
        if formatted and formatted[-1].get("role") != "assistant":
            content = formatted[-1].get("content")
            if isinstance(content, str) and content:
                formatted[-1]["content"] = [{"type": "text", "text": content, "cache_control": dict(self._breakpoint)}]
            elif isinstance(content, list) and content and isinstance(content[-1], dict):
                content[-1] = {**content[-1], "cache_control": dict(self._breakpoint)}
        return {}

    def _apply_tool_cache(self, tools: list[dict]) -> list[dict]:
        """Mark the last tool schema as a cache breakpoint.

        Tool schemas are usually stable turn-to-turn (even with lazy
        toolsets, they only change when `fetch_tools` runs) so this is
        normally a 100% cache-hit boundary, same rationale as the system
        prompt breakpoint. Copies rather than mutates caller-owned dicts.
        """
        if not tools or self._breakpoint is None:
            return tools
        copied = [dict(t) for t in tools]
        copied[-1] = {**copied[-1], "cache_control": dict(self._breakpoint)}
        return copied

    def _format_tools(self, tools: list[ToolSchema] | None) -> list[dict] | None:
        if not tools:
            return None
        return [
            {
                "name": t.name,
                "description": t.description,
                # The API rejects an empty schema, and a tool taking no
                # arguments legitimately has one — same substitution the
                # OpenAI/Gemini/Ollama transports make.
                "input_schema": t.input_schema or {"type": "object", "properties": {}},
            }
            for t in tools
        ]

    def _build_system_kwargs(
        self,
        system: str | None,
        system_blocks: list[str] | None,
    ) -> list[dict] | None:
        """Build the Anthropic ``system`` list with the correct cache split.

        When ``system_blocks`` is supplied (two-block split from
        ``PipesHubPromptBuilder.build_blocks()``):
          - block[0] (stable: Band A + Band B) gets ``cache_control``
          - block[1] (volatile: Band C) is uncached

        When only ``system`` is supplied (legacy / non-PipesHub builder):
          - one cached block, same as today

        Returns ``None`` when there is no system content at all. With
        ``_message_cache`` and ``_apply_tool_cache`` a request carries three of
        Anthropic's four breakpoints.
        """
        if system_blocks:
            blocks: list[dict] = []
            for i, text in enumerate(system_blocks):
                if not text:
                    continue
                # Adjacent system blocks reach the model with nothing between
                # them; the separator goes on the volatile side so the cached
                # stable block is the same bytes on every request.
                block: dict = {"type": "text", "text": f"\n\n{text}" if blocks else text}
                # Only the stable block gets the cache marker — the volatile
                # tail must stay uncached so it doesn't evict the stable block.
                if i == 0 and self._breakpoint is not None:
                    block["cache_control"] = dict(self._breakpoint)
                blocks.append(block)
            return blocks or None
        if system:
            block = {"type": "text", "text": system}
            if self._breakpoint is not None:
                block["cache_control"] = dict(self._breakpoint)
            return [block]
        return None

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
        formatted = self._format_messages(messages)
        message_cache = self._message_cache(formatted)

        kwargs: dict[str, Any] = {
            "model": model or self._model,
            "max_tokens": self._max_tokens,
            "messages": formatted,
            **message_cache,
        }
        if thinking_budget:
            # Anthropic extended thinking requires max_tokens to exceed the
            # thinking budget — grow it rather than silently truncating output.
            kwargs["thinking"] = {"type": "enabled", "budget_tokens": thinking_budget}
            kwargs["max_tokens"] = max(self._max_tokens, thinking_budget + 1024)
        elif self._thinking:
            kwargs["thinking"] = dict(self._thinking)
            # A captured {"type": "enabled", "budget_tokens": N} needs headroom
            # above N for the same reason the per-call branch above does, or the
            # request 400s. "adaptive" carries no budget and needs nothing.
            budget = self._thinking.get("budget_tokens")
            if isinstance(budget, int) and budget:
                kwargs["max_tokens"] = max(self._max_tokens, budget + 1024)
        # Anthropic rejects temperature alongside extended thinking (enabled /
        # adaptive). {"type": "disabled"} is truthy but allows temperature —
        # match LangChain, which still sends it for that configuration.
        thinking_cfg = kwargs.get("thinking")
        thinking_active = (
            isinstance(thinking_cfg, dict)
            and thinking_cfg.get("type") in ("enabled", "adaptive")
        )
        if self._temperature is not None and not thinking_active:
            kwargs["temperature"] = self._temperature
        # effort has no Anthropic equivalent today — accepted for interface
        # parity with other providers and intentionally a no-op here.
        system_list = self._build_system_kwargs(system, system_blocks)
        if system_list:
            kwargs["system"] = system_list
        formatted_tools = self._format_tools(tools)
        if formatted_tools:
            kwargs["tools"] = self._apply_tool_cache(formatted_tools)
            if tool_choice == "none":
                kwargs["tool_choice"] = {"type": "none"}

        try:
            response = await self._client.messages.create(**kwargs)
        except Exception as exc:
            if not self._drop_cache_params(exc):
                raise self._wrap_error(exc, "complete") from exc
            return await self.complete(
                messages, tools=tools, system=system, model=model, thinking_budget=thinking_budget,
                effort=effort, system_blocks=system_blocks, tool_choice=tool_choice,
            )

        usage = self._usage_from(response)
        message = self._parse_response(response)
        self._check_output(response, message, usage, "complete")
        stop_reason = _STOP_REASON_MAP.get(getattr(response, "stop_reason", None), StopReason.END_TURN)
        return ModelResponse(message=message, usage=usage, stop_reason=stop_reason, model=kwargs["model"])

    async def complete_structured(
        self,
        messages: list[Message],
        output_schema: dict,
        system: str | None = None,
        model: str | None = None,
    ) -> StructuredResponse:
        """Force structured JSON output using the tool-use trick."""
        tool_def = {
            "name": "structured_output",
            "description": "Return the structured result.",
            "input_schema": output_schema,
        }
        formatted = self._format_messages(messages)
        message_cache = self._message_cache(formatted)

        resolved_model = model or self._model
        kwargs: dict[str, Any] = {
            "model": resolved_model,
            "max_tokens": self._max_tokens,
            "messages": formatted,
            "tools": [tool_def],
            "tool_choice": {"type": "tool", "name": "structured_output"},
            **message_cache,
        }
        system_list = self._build_system_kwargs(system, None)
        if system_list:
            kwargs["system"] = system_list

        try:
            response = await self._client.messages.create(**kwargs)
        except Exception as exc:
            if not self._drop_cache_params(exc):
                raise self._wrap_error(exc, "complete_structured") from exc
            return await self.complete_structured(messages, output_schema, system=system, model=model)

        usage = self._usage_from(response)

        for block in response.content:
            if block.type == "tool_use" and block.name == "structured_output":
                return StructuredResponse(data=dict(block.input), usage=usage, model=resolved_model)

        raise TransportError("Structured output tool_use block not found in response")

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
        formatted = self._format_messages(messages)
        message_cache = self._message_cache(formatted)

        resolved_model = model or self._model
        kwargs: dict[str, Any] = {
            "model": resolved_model,
            "max_tokens": self._max_tokens,
            "messages": formatted,
            **message_cache,
        }
        if thinking_budget:
            kwargs["thinking"] = {"type": "enabled", "budget_tokens": thinking_budget}
            kwargs["max_tokens"] = max(self._max_tokens, thinking_budget + 1024)
        elif self._thinking:
            kwargs["thinking"] = dict(self._thinking)
            # A captured {"type": "enabled", "budget_tokens": N} needs headroom
            # above N for the same reason the per-call branch above does, or the
            # request 400s. "adaptive" carries no budget and needs nothing.
            budget = self._thinking.get("budget_tokens")
            if isinstance(budget, int) and budget:
                kwargs["max_tokens"] = max(self._max_tokens, budget + 1024)
        thinking_cfg = kwargs.get("thinking")
        thinking_active = (
            isinstance(thinking_cfg, dict)
            and thinking_cfg.get("type") in ("enabled", "adaptive")
        )
        if self._temperature is not None and not thinking_active:
            kwargs["temperature"] = self._temperature
        # effort has no Anthropic equivalent today — same no-op as complete().
        system_list = self._build_system_kwargs(system, system_blocks)
        if system_list:
            kwargs["system"] = system_list
        formatted_tools = self._format_tools(tools)
        if formatted_tools:
            kwargs["tools"] = self._apply_tool_cache(formatted_tools)
            if tool_choice == "none":
                kwargs["tool_choice"] = {"type": "none"}

        emitted = False
        try:
            async with self._client.messages.stream(**kwargs) as stream:
                async for text_chunk in stream.text_stream:
                    emitted = True
                    yield TextDeltaEvent(delta=text_chunk)
                # Yield the final assembled message (includes tool calls if any)
                final = await stream.get_final_message()
                usage = self._usage_from(final)
                message = self._parse_response(final)
                self._check_output(final, message, usage, "stream")
                stop_reason = _STOP_REASON_MAP.get(getattr(final, "stop_reason", None), StopReason.END_TURN)
                emitted = True
                yield StreamCompleteEvent(
                    response=ModelResponse(message=message, usage=usage, stop_reason=stop_reason, model=resolved_model)
                )
        except TransportError:
            raise
        except Exception as exc:
            # Resending after deltas reached the caller would replay them.
            if emitted or not self._drop_cache_params(exc):
                raise self._wrap_error(exc, "stream") from exc
        else:
            return
        async for event in self.stream(
            messages, tools=tools, system=system, model=model, thinking_budget=thinking_budget,
            effort=effort, system_blocks=system_blocks, tool_choice=tool_choice,
        ):
            yield event
