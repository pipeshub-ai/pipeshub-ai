from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any

from app.agent_loop_lib.core.exceptions import TransportError
from app.agent_loop_lib.core.messages import (
    AssistantMessage,
    Message,
    MessageRole,
    TextPart,
    ToolCall,
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
from app.agent_loop_lib.core.tool_schema import ToolSchema
from app.agent_loop_lib.transport.base import LLMTransport
from app.agent_loop_lib.transport.openai_responses import normalise_tool_call

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

    DEFAULT_MODEL = "claude-sonnet-4-6"

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
        self._client = _anthropic.AsyncAnthropic(**client_kwargs)
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
        cls, llm: Any, model_name: str = "", model_key: str | None = None,
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

    def _format_message(self, msg: Message) -> dict[str, Any]:
        """Convert a framework Message to Anthropic API message dict."""
        if msg.role == MessageRole.TOOL:
            serialized_content = (msg.content or "") + getattr(msg, "step_footer", "")
            block: dict[str, Any] = {
                "type": "tool_result",
                "tool_use_id": msg.tool_call_id,
                "content": serialized_content,
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
            return {
                "type": "image",
                "source": {"type": source.type, "media_type": source.media_type, "data": source.data},
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

        input_tokens = _int_field(usage, "input_tokens")
        output_tokens = _int_field(usage, "output_tokens")
        cache_read = _int_field(usage, "cache_read_input_tokens")
        cache_write = _int_field(usage, "cache_creation_input_tokens")

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

    def _apply_prompt_cache(self, formatted: list[dict]) -> None:
        """
        Mark the most recent stable tool result for prompt caching.

        Anthropic charges 10% of normal price for cache reads vs 100% for fresh
        reads. Cache TTL is 5 minutes. We mark the last large tool result that
        is NOT in the final 2 messages (which change every turn), so successive
        turns get a cache hit on everything up to that point.

        We only add ONE breakpoint here (plus one for the system below) to stay
        safely within the 4-breakpoint limit.
        """
        if len(formatted) <= 2:
            return
        _MIN = 300  # chars — don't bother caching tiny results
        for fmsg in reversed(formatted[:-2]):
            content = fmsg.get("content")
            if not isinstance(content, list):
                continue
            for block in content:
                btype = block.get("type")
                raw   = block.get("content", "") if btype == "tool_result" else block.get("text", "")
                if isinstance(raw, str) and len(raw) >= _MIN and "cache_control" not in block:
                    block["cache_control"] = {"type": "ephemeral"}
                    return  # one breakpoint is enough

    def _apply_tool_cache(self, tools: list[dict]) -> list[dict]:
        """Mark the last tool schema as a cache breakpoint.

        Tool schemas are usually stable turn-to-turn (even with lazy
        toolsets, they only change when `fetch_tools` runs) so this is
        normally a 100% cache-hit boundary, same rationale as the system
        prompt breakpoint. Copies rather than mutates caller-owned dicts.
        """
        if not tools:
            return tools
        copied = [dict(t) for t in tools]
        copied[-1] = {**copied[-1], "cache_control": {"type": "ephemeral"}}
        return copied

    def _format_tools(self, tools: list[ToolSchema] | None) -> list[dict] | None:
        if not tools:
            return None
        return [
            {"name": t.name, "description": t.description, "input_schema": t.input_schema}
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

        Returns ``None`` when there is no system content at all.
        Uses all 4 Anthropic cache breakpoints:
          ``_apply_prompt_cache`` (message) + system (1 or 2 here) + ``_apply_tool_cache`` = 3 or 4.
        """
        if system_blocks:
            blocks: list[dict] = []
            for i, text in enumerate(system_blocks):
                if not text:
                    continue
                block: dict = {"type": "text", "text": text}
                # Only the stable block gets the cache marker — the volatile
                # tail must stay uncached so it doesn't evict the stable block.
                if i == 0:
                    block["cache_control"] = {"type": "ephemeral"}
                blocks.append(block)
            return blocks or None
        if system:
            return [{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}]
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
    ) -> ModelResponse:
        formatted = [self._format_message(m) for m in messages]
        self._apply_prompt_cache(formatted)

        kwargs: dict[str, Any] = {
            "model": model or self._model,
            "max_tokens": self._max_tokens,
            "messages": formatted,
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

        try:
            response = await self._client.messages.create(**kwargs)
        except Exception as exc:
            raise self._wrap_error(exc, "complete") from exc

        usage = self._usage_from(response)
        message = self._parse_response(response)
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
        formatted = [self._format_message(m) for m in messages]
        self._apply_prompt_cache(formatted)

        resolved_model = model or self._model
        kwargs: dict[str, Any] = {
            "model": resolved_model,
            "max_tokens": self._max_tokens,
            "messages": formatted,
            "tools": [tool_def],
            "tool_choice": {"type": "tool", "name": "structured_output"},
        }
        if system:
            kwargs["system"] = [
                {"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}
            ]

        try:
            response = await self._client.messages.create(**kwargs)
        except Exception as exc:
            raise self._wrap_error(exc, "complete_structured") from exc

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
    ) -> AsyncIterator[StreamEvent]:
        formatted = [self._format_message(m) for m in messages]
        self._apply_prompt_cache(formatted)

        resolved_model = model or self._model
        kwargs: dict[str, Any] = {
            "model": resolved_model,
            "max_tokens": self._max_tokens,
            "messages": formatted,
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

        try:
            async with self._client.messages.stream(**kwargs) as stream:
                async for text_chunk in stream.text_stream:
                    yield TextDeltaEvent(delta=text_chunk)
                # Yield the final assembled message (includes tool calls if any)
                final = await stream.get_final_message()
                usage = self._usage_from(final)
                message = self._parse_response(final)
                stop_reason = _STOP_REASON_MAP.get(getattr(final, "stop_reason", None), StopReason.END_TURN)
                yield StreamCompleteEvent(
                    response=ModelResponse(message=message, usage=usage, stop_reason=stop_reason, model=resolved_model)
                )
        except Exception as exc:
            raise self._wrap_error(exc, "stream") from exc
