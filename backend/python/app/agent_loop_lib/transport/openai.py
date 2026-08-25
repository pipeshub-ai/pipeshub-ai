from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any

# Private SDK symbol, imported deliberately rather than reimplemented: SSE
# framing is fiddly (multi-line data, chunk boundaries) and the SDK's decoder is
# the tested one. test_openai_responses.py pins it, so an SDK upgrade that moves
# it fails the build instead of the stream.
from openai._streaming import SSEDecoder

from app.agent_loop_lib.core.exceptions import TransportError
from app.agent_loop_lib.core.messages import (
    AssistantMessage,
    Message,
    MessageRole,
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
    ThinkingDeltaEvent,
    ToolCallDeltaEvent,
)
from app.agent_loop_lib.core.tool_schema import ToolSchema
from app.agent_loop_lib.transport.base import LLMTransport
from app.agent_loop_lib.transport.openai_responses import (
    EVT_COMPLETED,
    EVT_ERROR,
    EVT_FAILED,
    EVT_FUNCTION_ARGS_DELTA,
    EVT_INCOMPLETE,
    EVT_OUTPUT_ITEM_ADDED,
    EVT_REASONING_DELTA,
    EVT_REASONING_SUMMARY_DELTA,
    EVT_REFUSAL_DELTA,
    EVT_TEXT_DELTA,
    RawEvent,
    format_responses_input,
    format_responses_tools,
    normalise_tool_call,
    parse_responses_output,
    responses_usage_fields,
    stop_reason_from_responses,
)
from app.agent_loop_lib.transport.provider_conflicts import (
    is_api_shape_conflict,
    is_reasoning_mandatory_conflict,
)
from app.utils.llm_api_mode_store import LLMApiMode, get_llm_api_mode_store

# Uses `openai` SDK internally.
# Install: pip install 'agent-loop[openai]'

logger = logging.getLogger(__name__)

_RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504, 529}

# Reasoning values a provider may reject outright when it refuses to let
# reasoning be disabled. Matches LangChainTransport._reasoning_mandatory_fallback,
# which bumps off a disabled value rather than clearing the field.
_DISABLED_REASONING_VALUES = ("none", "off", "disabled", "minimal")
_REASONING_BUMP_TARGET = "low"

_FINISH_REASON_MAP: dict[str | None, StopReason] = {
    "stop": StopReason.END_TURN,
    "tool_calls": StopReason.TOOL_USE,
    "length": StopReason.MAX_TOKENS,
}


@dataclass
class RequestDefaults:
    """Per-request settings taken off the configured LangChain model.

    These are the knobs `aimodels.get_generator_model` sets on the model object
    (`aimodels.py:1081-1095`) rather than passing per call. `reasoning` and
    `use_responses_api` travel together: for Azure plus a reasoning model,
    `_reasoning_effort_kwargs` returns both, and the Responses API is required
    because Chat Completions rejects reasoning combined with bound tools for the
    gpt-5.x family.
    """

    temperature: float | None = None
    reasoning: dict[str, Any] | None = None
    reasoning_effort: str | None = None
    use_responses_api: bool = False
    model: str = ""

    def _drops_temperature(self) -> bool:
        """gpt-5 (non-chat) rejects temperature on the Responses API unless
        reasoning is off.

        langchain_openai applies the same rule before POSTing
        (`chat_models/base.py`, "Remove temperature parameter for models that
        don't support it in responses API"), so sending it would make our
        request differ from the one the LangChain arm issues -- and 400 on the
        deployment this transport actually targets.

        One deliberate difference: langchain tests the request's `model` field,
        which on Azure carries the DEPLOYMENT name; this tests the model name.
        They coincide on deployments named after their model (the case here),
        and where they don't, testing the model name is the one that matches
        what the service will accept.
        """
        model = self.model or ""
        return (
            self.use_responses_api
            and model.startswith("gpt-5")
            and "chat" not in model
            and (self.reasoning or {}).get("effort") != "none"
        )

    def request_kwargs(self) -> dict[str, Any]:
        """The subset that goes into a request body."""
        out: dict[str, Any] = {}
        if self.temperature is not None and not self._drops_temperature():
            out["temperature"] = self.temperature
        if self.use_responses_api:
            if self.reasoning:
                out["reasoning"] = dict(self.reasoning)
        elif self.reasoning_effort:
            out["reasoning_effort"] = self.reasoning_effort
        return out


class OpenAITransport(LLMTransport):
    """OpenAI Chat Completions API via the official SDK.

    Install: pip install 'agent-loop[openai]'

    The SDK is used internally to handle SSE parsing and streaming
    tool-call-argument accumulation correctly. It never leaks past this
    class — all callers see only the LLMTransport interface. `base_url`
    lets this same transport talk to any OpenAI-compatible endpoint
    (vLLM, LiteLLM proxy, Azure OpenAI's OpenAI-compat surface, etc.).
    """

    DEFAULT_MODEL = "gpt-4o"

    def __init__(
        self,
        api_key: str,
        model: str = DEFAULT_MODEL,
        base_url: str | None = None,
        defaults: "RequestDefaults | None" = None,
        timeout: float | None = None,
        max_retries: int | None = None,
        model_key: str | None = None,
    ) -> None:
        super().__init__()
        try:
            import openai as _openai
        except ImportError as exc:
            raise ImportError(
                "openai SDK is required for OpenAITransport. "
                "Install it with: pip install 'agent-loop[openai]'"
            ) from exc
        self._openai = _openai
        self._model = model
        self._defaults = defaults or RequestDefaults(model=model)
        self._model_key = model_key
        client_kwargs: dict[str, Any] = {"api_key": api_key}
        if base_url:
            client_kwargs["base_url"] = base_url
        # Unset, the SDK applies its own (longer) default; LangChain pins
        # DEFAULT_LLM_TIMEOUT, so a slow turn has to fail at the same point.
        if timeout is not None:
            client_kwargs["timeout"] = timeout
        if max_retries is not None:
            client_kwargs["max_retries"] = max_retries
        self._client = _openai.AsyncOpenAI(**client_kwargs)
        # Cumulative usage across all calls on this transport instance —
        # diagnostic only; see AnthropicTransport's equivalent fields.
        self.total_input_tokens: int = 0
        self.total_output_tokens: int = 0
        self.total_llm_calls: int = 0
        self.total_cache_read_tokens: int = 0
        self.total_cache_write_tokens: int = 0


    @classmethod
    def from_langchain_model(
        cls, llm: Any, model_name: str = "", model_key: str | None = None,
    ) -> "OpenAITransport":
        """Build from an already-configured `ChatOpenAI`.

        Same contract as `AzureOpenAITransport.from_langchain_model`: read every
        knob off the model the LangChain path already uses, not just the
        credentials. `aimodels.get_generator_model` bakes temperature, timeout
        and the reasoning configuration into the model object, and for a gpt-5
        model `_reasoning_effort_kwargs` also sets `use_responses_api`, so a
        transport that copied only the api key would quietly send Chat
        Completions with no reasoning -- the exact drift this contract exists to
        prevent.
        """
        def _val(name: str) -> str:
            raw = getattr(llm, name, None)
            secret = getattr(raw, "get_secret_value", None)
            return (secret() if callable(secret) else raw) or ""

        api_key = _val("openai_api_key")
        if not api_key:
            raise ValueError(
                f"{type(llm).__name__} has no openai_api_key; the direct OpenAI "
                "transport only supports ChatOpenAI-configured models"
            )
        resolved_model = model_name or getattr(llm, "model_name", "") or cls.DEFAULT_MODEL

        reasoning = getattr(llm, "reasoning", None)
        defaults = RequestDefaults(
            temperature=getattr(llm, "temperature", None),
            reasoning=reasoning if isinstance(reasoning, dict) else None,
            reasoning_effort=getattr(llm, "reasoning_effort", None),
            use_responses_api=bool(getattr(llm, "use_responses_api", False)),
            model=resolved_model,
        )
        timeout = getattr(llm, "request_timeout", None)
        if timeout is None:
            timeout = getattr(llm, "timeout", None)
        max_retries = getattr(llm, "max_retries", None)
        base_url = _val("openai_api_base") or None

        return cls(
            api_key=api_key,
            model=resolved_model,
            base_url=base_url,
            defaults=defaults,
            timeout=timeout if isinstance(timeout, (int, float)) else None,
            max_retries=max_retries if isinstance(max_retries, int) else None,
            model_key=model_key,
        )

    @property
    def provider(self) -> str:
        return "openai"

    @property
    def model_name(self) -> str:
        return self._model

    # ------------------------------------------------------------------
    # Message / tool formatting helpers
    # ------------------------------------------------------------------

    def _format_message(self, msg: Message) -> dict[str, Any]:
        """Convert a framework Message to an OpenAI chat message dict."""
        if msg.role == MessageRole.TOOL:
            step_footer = getattr(msg, "step_footer", "")
            content = msg.content
            if isinstance(content, list):
                blocks = [self._format_tool_result_part(p) for p in content]
                if step_footer:
                    blocks.append({"type": "text", "text": step_footer})
                tool_content: Any = blocks
            else:
                tool_content = (content or "") + step_footer
            return {
                "role": "tool",
                "tool_call_id": msg.tool_call_id,
                "content": tool_content,
            }
        if msg.role == MessageRole.ASSISTANT:
            return {
                "role": "assistant",
                "content": msg.text or None,
                "tool_calls": [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {"name": tc.name, "arguments": json.dumps(tc.arguments)},
                    }
                    for tc in msg.tool_calls or []
                ] or None,
            }
        content = msg.content
        if isinstance(content, list):
            blocks = self._format_content_blocks(content)
            # Only send the array form when it carries something a plain string
            # cannot: an image. Text-only messages stay strings, which is what
            # every existing test and prompt cache expects.
            if any(b["type"] != "text" for b in blocks):
                return {"role": msg.role.value, "content": blocks}
            content = " ".join(b["text"] for b in blocks)
        return {"role": msg.role.value, "content": content or ""}

    @staticmethod
    def _format_tool_result_part(part: Any) -> dict[str, Any]:
        # GPT-5+ tool-result content accepts the same `image_url` block
        # shape as user-message vision input.
        from app.agent_loop_lib.core.messages import image_data_url

        if getattr(part, "type", None) == "image" and getattr(part, "source", None):
            return {"type": "image_url", "image_url": {"url": image_data_url(part.source)}}
        return {"type": "text", "text": getattr(part, "text", "") or getattr(part, "thinking", "")}

    @staticmethod
    def _format_content_blocks(parts: list[Any]) -> list[dict[str, Any]]:
        """`list[Part]` into Chat Completions content blocks.

        ImageParts used to be flattened away here by `getattr(p, "text", "")`,
        which silently dropped every attached image -- the model answered about
        a picture it never received.
        """
        from app.agent_loop_lib.core.messages import image_data_url

        blocks: list[dict[str, Any]] = []
        for part in parts:
            if getattr(part, "type", None) == "image" and getattr(part, "source", None):
                blocks.append({
                    "type": "image_url",
                    "image_url": {"url": image_data_url(part.source)},
                })
                continue
            text = getattr(part, "text", "")
            if text:
                blocks.append({"type": "text", "text": text})
        return blocks

    def _format_messages(self, messages: list[Message], system: str | None) -> list[dict[str, Any]]:
        formatted: list[dict[str, Any]] = []
        if system:
            formatted.append({"role": "system", "content": system})
        formatted.extend(self._format_message(m) for m in messages)
        return formatted

    # ------------------------------------------------------------------
    # Responses API
    #
    # A different wire shape, not a variant: `input` instead of `messages`,
    # flat tools, renamed usage fields, no finish_reason. Reasoning models on
    # Azure require it, because Chat Completions rejects reasoning combined
    # with bound tools for the gpt-5.x family.
    # ------------------------------------------------------------------

    def _wants_responses(self) -> bool:
        """Whether requests should go to the Responses API.

        Taken from the captured configuration, never re-derived:
        `aimodels._reasoning_effort_kwargs` has already folded the model-name
        heuristic and the learned `LLMApiMode` fact into `use_responses_api`.
        Recomputing it here would be a second source of truth that drifts from
        the LangChain arm the first time either rule changes. Defaults to False
        for a transport constructed without a configured model.
        """
        return self._defaults.use_responses_api

    def _responses_model_id(self) -> str:
        """Value for the `model` field on a Responses request."""
        return self._model

    def _build_responses_kwargs(
        self,
        messages: list[Message],
        tools: list[ToolSchema] | None,
        system: str | None,
        system_blocks: list[str] | None,
        stream: bool,
    ) -> dict[str, Any]:
        if system_blocks and not system:
            system = "\n\n".join(b for b in system_blocks if b)
        kwargs: dict[str, Any] = {
            "model": self._responses_model_id(),
            "input": format_responses_input(messages, system),
            **self._default_request_kwargs(),
        }
        formatted_tools = format_responses_tools(tools)
        if formatted_tools:
            kwargs["tools"] = formatted_tools
        if stream:
            # No stream_options: include_usage is Chat-Completions-only, and
            # usage always arrives on response.completed.
            kwargs["stream"] = True
        return kwargs

    async def _complete_responses(
        self,
        messages: list[Message],
        tools: list[ToolSchema] | None = None,
        system: str | None = None,
        system_blocks: list[str] | None = None,
    ) -> ModelResponse:
        kwargs = self._build_responses_kwargs(messages, tools, system, system_blocks, stream=False)
        try:
            response = await self._client.responses.create(**kwargs)
        except Exception as exc:
            raise self._wrap_error(exc, "complete") from exc

        message = parse_responses_output(response)
        usage = self._record_usage(*responses_usage_fields(response))
        stop_reason = stop_reason_from_responses(response, bool(message.tool_calls))
        if stop_reason == StopReason.MAX_TOKENS:
            message.truncated = True
        return ModelResponse(
            message=message, usage=usage, stop_reason=stop_reason,
            model=self._responses_model_id(),
        )

    async def _raw_response_events(self, kwargs: dict[str, Any]) -> AsyncIterator[Any]:
        """Responses stream events as `RawEvent`, skipping the SDK's per-event
        pydantic validation (see `RawEvent` for why -- ~12% of query CPU).

        `with_streaming_response` and `iter_bytes` are public SDK API; only the
        cast to `ResponseStreamEvent` is bypassed. HTTP, auth, retries and SSE
        framing all still come from the SDK.

        Two behaviours of `AsyncStream.__stream__` are reproduced deliberately:

        * A non-2xx raises on entering the context manager, before any event is
          yielded (`_base_client.py`, `_make_status_error_from_response`, after
          its own retry loop). `AzureOpenAITransport.stream` refuses to retry a
          request-shape conflict once anything has been emitted, so an error
          arriving late instead of early would silently disable that recovery.
        * An event carrying `error` raises rather than ending the stream, or a
          mid-stream failure would look like a short answer.
        """
        decoder = SSEDecoder()
        async with self._client.responses.with_streaming_response.create(**kwargs) as response:
            async for sse in decoder.aiter_bytes(response.iter_bytes()):
                if sse.data.startswith("[DONE]"):
                    break
                data = json.loads(sse.data)
                if isinstance(data, dict) and data.get("error"):
                    error = data["error"]
                    message = error.get("message") if isinstance(error, dict) else None
                    raise TransportError(
                        "OpenAI API error (stream): "
                        f"{message or 'An error occurred during streaming'}",
                        retryable=False,
                    )
                yield RawEvent(data)

    async def _stream_responses(
        self,
        messages: list[Message],
        tools: list[ToolSchema] | None = None,
        system: str | None = None,
        system_blocks: list[str] | None = None,
    ) -> AsyncIterator[StreamEvent]:
        kwargs = self._build_responses_kwargs(messages, tools, system, system_blocks, stream=True)
        final_response: Any = None
        # Fallback only. The terminal event carries the fully-formed Response,
        # so the same parser as _complete_responses builds the final message;
        # this is used solely when a stream dies before response.completed.
        partial: dict[int, dict[str, Any]] = {}

        try:
            async for event in self._raw_response_events(kwargs):
                etype = getattr(event, "type", "")

                if etype in (EVT_TEXT_DELTA, EVT_REFUSAL_DELTA):
                    delta = getattr(event, "delta", "") or ""
                    if delta:
                        yield TextDeltaEvent(delta=delta)

                elif etype in (EVT_REASONING_DELTA, EVT_REASONING_SUMMARY_DELTA):
                    delta = getattr(event, "delta", "") or ""
                    if delta:
                        yield ThinkingDeltaEvent(delta=delta)

                elif etype == EVT_OUTPUT_ITEM_ADDED:
                    item = getattr(event, "item", None)
                    if getattr(item, "type", None) == "function_call":
                        index = getattr(event, "output_index", 0) or 0
                        call_id = getattr(item, "call_id", None)
                        name = getattr(item, "name", None)
                        partial[index] = {"id": call_id, "name": name, "arguments": ""}
                        # The opener is where the name arrives, and the agent
                        # loop reads it off the FIRST delta for an index to
                        # recognise final_answer. Withholding it until
                        # arguments arrive is what breaks live streaming.
                        yield ToolCallDeltaEvent(
                            index=index, id=call_id, name=name, arguments_delta="",
                        )

                elif etype == EVT_FUNCTION_ARGS_DELTA:
                    index = getattr(event, "output_index", 0) or 0
                    delta = getattr(event, "delta", "") or ""
                    if delta:
                        entry = partial.setdefault(
                            index, {"id": None, "name": None, "arguments": ""}
                        )
                        entry["arguments"] += delta
                        yield ToolCallDeltaEvent(
                            index=index, id=None, name=None, arguments_delta=delta,
                        )

                elif etype in (EVT_COMPLETED, EVT_INCOMPLETE):
                    final_response = getattr(event, "response", None)

                elif etype in (EVT_FAILED, EVT_ERROR):
                    detail = getattr(getattr(event, "response", None), "error", None)
                    raise TransportError(
                        "OpenAI API error (stream): "
                        f"{detail or getattr(event, 'message', event)}",
                        retryable=False,
                    )
        except TransportError:
            raise
        except Exception as exc:
            raise self._wrap_error(exc, "stream") from exc

        if final_response is not None:
            message = parse_responses_output(final_response)
            usage = self._record_usage(*responses_usage_fields(final_response))
            stop_reason = stop_reason_from_responses(final_response, bool(message.tool_calls))
        else:
            # Stream ended without a terminal event; salvage what arrived.
            salvaged: list[ToolCall] = []
            for idx, entry in sorted(partial.items()):
                salvaged.append(
                    normalise_tool_call(
                        entry["id"] or f"call_{idx}", entry["name"] or "", entry["arguments"]
                    )
                )
            message = AssistantMessage(content=None, tool_calls=salvaged or None)
            usage = TokenUsage()
            stop_reason = StopReason.TOOL_USE if salvaged else StopReason.END_TURN

        if stop_reason == StopReason.MAX_TOKENS:
            message.truncated = True
        yield StreamCompleteEvent(
            response=ModelResponse(
                message=message, usage=usage, stop_reason=stop_reason,
                model=self._responses_model_id(),
            )
        )

    def _default_request_kwargs(self) -> dict[str, Any]:
        """Settings applied to every request, on top of the per-call arguments.

        Empty for a transport built without a configured model; populated from
        `RequestDefaults` when one was captured, so all three request builders
        pick it up without each having to remember.
        """
        return self._defaults.request_kwargs()

    def _format_tools(self, tools: list[ToolSchema] | None) -> list[dict] | None:
        """Our internal, provider-agnostic `ToolSchema` into OpenAI's
        function-calling shape."""
        if not tools:
            return None
        return [
            {
                "type": "function",
                "function": {
                    "name": t.name,
                    "description": t.description,
                    "parameters": t.input_schema or {"type": "object", "properties": {}},
                },
            }
            for t in tools
        ]

    def _parse_tool_calls(self, raw_tool_calls: Any) -> list[ToolCall]:
        tool_calls: list[ToolCall] = []
        for tc in raw_tool_calls or []:
            tool_calls.append(
                normalise_tool_call(tc.id, tc.function.name, tc.function.arguments)
            )
        return tool_calls

    def _parse_response(self, choice_message: Any) -> AssistantMessage:
        """Convert an OpenAI SDK response message to a framework AssistantMessage."""
        tool_calls = self._parse_tool_calls(getattr(choice_message, "tool_calls", None))
        text = getattr(choice_message, "content", None)
        return AssistantMessage(content=text, tool_calls=tool_calls or None)

    def _record_usage(
        self, input_tokens: int, output_tokens: int, cache_read: int, cache_write: int = 0
    ) -> TokenUsage:
        """Bump the cumulative counters and build the per-call TokenUsage.

        Shared by both API shapes: the Responses API names every usage field
        differently, so only the extraction differs, never the accounting.
        """
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

    @staticmethod
    def _chat_stop_reason(finish_reason: str | None, has_tool_calls: bool) -> StopReason:
        """Chat Completions finish_reason -> StopReason, ordered as
        LangChainTransport orders it (`langchain_transport.py:484,414-423`):
        truncation first, then tool calls, then the literal mapping.

        Tool calls have to beat a missing/unknown finish_reason, or a provider
        that omits it turns a turn holding tool calls into END_TURN and the
        loop stops with the calls unexecuted.
        """
        if finish_reason == "length":
            return StopReason.MAX_TOKENS
        if has_tool_calls:
            return StopReason.TOOL_USE
        return _FINISH_REASON_MAP.get(finish_reason, StopReason.END_TURN)

    def _usage_from(self, response: Any) -> TokenUsage:
        usage = getattr(response, "usage", None)

        def _int_field(obj: Any, name: str) -> int:
            value = getattr(obj, name, 0) if obj is not None else 0
            return value if isinstance(value, int) else 0

        input_tokens = _int_field(usage, "prompt_tokens")
        output_tokens = _int_field(usage, "completion_tokens")
        details = getattr(usage, "prompt_tokens_details", None)
        cached = _int_field(details, "cached_tokens")

        return self._record_usage(input_tokens, output_tokens, cached)

    def _wrap_error(self, exc: Exception, context: str) -> TransportError:
        status_code = getattr(exc, "status_code", None)
        # OSError included to match LangChainTransport._is_network_error: a
        # dropped socket surfaces as a bare OSError often enough that treating
        # it as fatal here would retry-gap the direct path against that one.
        is_network = isinstance(
            exc,
            (self._openai.APIConnectionError, self._openai.APITimeoutError, OSError),
        )
        retryable = is_network or (
            status_code is not None and status_code in _RETRYABLE_STATUS_CODES
        )
        return TransportError(
            f"OpenAI API error ({context}): {exc}",
            status_code=status_code,
            retryable=retryable,
        )

    # ------------------------------------------------------------------
    # Public LLMTransport interface
    # ------------------------------------------------------------------

    # `complete`/`stream` below wrap these with the one-shot request-shape
    # retry. Split rather than inlined so the retry can re-run the whole request
    # without recursing into itself.
    async def _complete_once(
        self,
        messages: list[Message],
        tools: list[ToolSchema] | None = None,
        system: str | None = None,
        model: str | None = None,
        thinking_budget: int | None = None,
        effort: str | None = None,
        system_blocks: list[str] | None = None,
    ) -> ModelResponse:
        if self._wants_responses():
            return await self._complete_responses(
                messages, tools=tools, system=system, system_blocks=system_blocks,
            )
        # OpenAI automatic prefix caching benefits from stable-first ordering
        # alone; join blocks when the caller omitted the already-joined string.
        if system_blocks and not system:
            system = "\n\n".join(b for b in system_blocks if b)
        resolved_model = model or self._model
        kwargs: dict[str, Any] = {
            "model": resolved_model,
            "messages": self._format_messages(messages, system),
            **self._default_request_kwargs(),
        }
        formatted_tools = self._format_tools(tools)
        if formatted_tools:
            kwargs["tools"] = formatted_tools
        if effort:
            # o-series/gpt-5-series "reasoning effort" knob — accepted as a
            # no-op parameter by models that don't support it.
            kwargs["reasoning_effort"] = effort
        # thinking_budget has no Chat Completions equivalent — no-op, kept
        # for interface parity with AnthropicTransport.

        try:
            response = await self._client.chat.completions.create(**kwargs)
        except Exception as exc:
            raise self._wrap_error(exc, "complete") from exc

        usage = self._usage_from(response)
        message = self._parse_response(response.choices[0].message)
        finish_reason = getattr(response.choices[0], "finish_reason", None)
        stop_reason = self._chat_stop_reason(finish_reason, bool(message.tool_calls))
        if stop_reason == StopReason.MAX_TOKENS:
            message.truncated = True
        return ModelResponse(message=message, usage=usage, stop_reason=stop_reason, model=resolved_model)

    async def complete_structured(
        self,
        messages: list[Message],
        output_schema: dict,
        system: str | None = None,
        model: str | None = None,
    ) -> StructuredResponse:
        """Force structured JSON output using the tool-call trick (same
        approach as AnthropicTransport.complete_structured)."""
        tool_def = {
            "type": "function",
            "function": {
                "name": "structured_output",
                "description": "Return the structured result.",
                "parameters": output_schema,
            },
        }
        resolved_model = model or self._model
        kwargs: dict[str, Any] = {
            "model": resolved_model,
            "messages": self._format_messages(messages, system),
            "tools": [tool_def],
            "tool_choice": {"type": "function", "function": {"name": "structured_output"}},
            **self._default_request_kwargs(),
        }

        try:
            response = await self._client.chat.completions.create(**kwargs)
        except Exception as exc:
            raise self._wrap_error(exc, "complete_structured") from exc

        usage = self._usage_from(response)
        message = response.choices[0].message
        for tc in getattr(message, "tool_calls", None) or []:
            if tc.function.name == "structured_output":
                try:
                    data = json.loads(tc.function.arguments)
                except (json.JSONDecodeError, ValueError):
                    data = {}
                return StructuredResponse(data=data, usage=usage, model=resolved_model)

        raise TransportError("structured_output tool call not found in response")

    async def _stream_once(
        self,
        messages: list[Message],
        tools: list[ToolSchema] | None = None,
        system: str | None = None,
        model: str | None = None,
        thinking_budget: int | None = None,
        effort: str | None = None,
        system_blocks: list[str] | None = None,
    ) -> AsyncIterator[StreamEvent]:
        if self._wants_responses():
            async for event in self._stream_responses(
                messages, tools=tools, system=system, system_blocks=system_blocks,
            ):
                yield event
            return
        if system_blocks and not system:
            system = "\n\n".join(b for b in system_blocks if b)
        resolved_model = model or self._model
        kwargs: dict[str, Any] = {
            "model": resolved_model,
            "messages": self._format_messages(messages, system),
            "stream": True,
            "stream_options": {"include_usage": True},
            **self._default_request_kwargs(),
        }
        formatted_tools = self._format_tools(tools)
        if formatted_tools:
            kwargs["tools"] = formatted_tools
        if effort:
            kwargs["reasoning_effort"] = effort

        text_parts: list[str] = []
        usage = TokenUsage()
        finish_reason: str | None = None
        # Tool call deltas arrive fragmented across chunks, indexed by
        # position in the assistant's tool_calls list — accumulate by
        # index until the stream ends, same pattern every OpenAI streaming
        # client uses for function calling.
        tool_call_accum: dict[int, dict[str, Any]] = {}

        try:
            stream = await self._client.chat.completions.create(**kwargs)
            async for chunk in stream:
                if getattr(chunk, "usage", None) is not None:
                    usage = self._usage_from(chunk)
                if not chunk.choices:
                    continue
                if chunk.choices[0].finish_reason:
                    finish_reason = chunk.choices[0].finish_reason
                delta = chunk.choices[0].delta
                # Reasoning text is not a declared field on ChoiceDelta, but the
                # SDK models allow extras, so providers that emit it (Azure
                # gpt-5.x) surface it here. Yielded before content so the agent
                # loop closes its reasoning block on the first text delta.
                reasoning_delta = getattr(delta, "reasoning_content", None)
                if reasoning_delta:
                    yield ThinkingDeltaEvent(delta=reasoning_delta)
                if delta.content:
                    text_parts.append(delta.content)
                    yield TextDeltaEvent(delta=delta.content)
                for tc_delta in delta.tool_calls or []:
                    entry = tool_call_accum.setdefault(
                        tc_delta.index, {"id": None, "name": None, "arguments": ""}
                    )
                    if tc_delta.id:
                        entry["id"] = tc_delta.id
                    if tc_delta.function and tc_delta.function.name:
                        entry["name"] = tc_delta.function.name
                    args_delta = ""
                    if tc_delta.function and tc_delta.function.arguments:
                        args_delta = tc_delta.function.arguments
                        entry["arguments"] += args_delta
                    # The agent loop decides whether a call is final_answer from
                    # `name` on the FIRST delta it sees for an index, and streams
                    # the answer live off that.
                    #
                    # Azure opens a tool call with a metadata-only chunk -- name
                    # and id set, arguments "" -- verified against the live
                    # service. Skipping empty-argument fragments therefore threw
                    # the name away and the first delta the loop saw carried
                    # name=None, so final_answer went unrecognised and the answer
                    # only appeared once the turn ended.
                    #
                    # id/name still carry what THIS fragment carried; providers
                    # send them once and omit them afterwards.
                    fragment_name = tc_delta.function.name if tc_delta.function else None
                    if args_delta or fragment_name or tc_delta.id:
                        yield ToolCallDeltaEvent(
                            index=tc_delta.index or 0,
                            id=tc_delta.id,
                            name=fragment_name,
                            arguments_delta=args_delta or "",
                        )
        except Exception as exc:
            raise self._wrap_error(exc, "stream") from exc

        final_tool_calls: list[ToolCall] = []
        for idx, entry in sorted(tool_call_accum.items()):
            final_tool_calls.append(
                normalise_tool_call(
                    entry["id"] or f"call_{idx}", entry["name"] or "", entry["arguments"]
                )
            )
        final_text = "".join(text_parts) or None
        message = AssistantMessage(content=final_text, tool_calls=final_tool_calls or None)
        stop_reason = self._chat_stop_reason(finish_reason, bool(final_tool_calls))
        if stop_reason == StopReason.MAX_TOKENS:
            message.truncated = True
        yield StreamCompleteEvent(
            response=ModelResponse(message=message, usage=usage, stop_reason=stop_reason, model=resolved_model)
        )

    # -- request-shape recovery -------------------------------------------
    #
    # LangChainTransport retries once against a differently-configured model
    # copy. There is no model object here, so the equivalent is retrying the
    # same call with the offending parameter adjusted.

    def _shape_fallback(self, exc: Exception) -> str | None:
        """Adjust this transport's request shape after a provider rejection.

        Mirrors `LangChainTransport._api_shape_fallback`: the direction is
        decided by our own current state rather than by reading more out of the
        error text. Returns the `LLMApiMode` worth recording if the retry
        succeeds, or None when the error is not a shape conflict or nothing can
        be adjusted -- a marker match with no available adjustment means the 400
        has some other cause and retrying would just repeat it.

        Mutates `self._defaults`, so the retry and every later call on this
        instance use the working shape. That is the same pinning LangChain does
        by swapping in the fallback model copy.
        """
        d = self._defaults
        if is_api_shape_conflict(exc):
            if d.use_responses_api:
                # The Responses attempt itself was rejected. Keep the tools --
                # a turn that needs one must still get it -- and drop reasoning.
                d.use_responses_api = False
                d.reasoning = None
                d.reasoning_effort = None
                return LLMApiMode.NO_REASONING_WITH_TOOLS.value
            # Reasoning combined with bound tools on Chat Completions: this
            # deployment wants the Responses API for that combination.
            effort = d.reasoning_effort or (d.reasoning or {}).get("effort")
            if not effort:
                return None
            d.use_responses_api = True
            d.reasoning = {"effort": effort}
            d.reasoning_effort = None
            return LLMApiMode.RESPONSES.value
        if is_reasoning_mandatory_conflict(exc):
            current = (d.reasoning or {}).get("effort") or d.reasoning_effort
            # Only bump off an explicitly disabled value, like LangChain. Bumping
            # when nothing was configured would turn reasoning ON for a caller
            # who never asked for it.
            if current is None or str(current).lower() not in _DISABLED_REASONING_VALUES:
                return None
            if d.use_responses_api:
                d.reasoning = {"effort": _REASONING_BUMP_TARGET}
            else:
                d.reasoning_effort = _REASONING_BUMP_TARGET
            return LLMApiMode.REASONING_MANDATORY.value
        return None

    async def _record_api_mode(self, mode: str) -> None:
        """Persist a learned shape once a retry has actually succeeded, so
        `aimodels.get_generator_model` builds the model that way from the start
        on later requests -- and so the LangChain arm learns it too.

        A no-op when the store was never initialised or no `model_key` was
        threaded through, matching `LangChainTransport._record_api_mode`. The
        in-request retry above applies either way.
        """
        try:
            store = get_llm_api_mode_store()
        except Exception:
            return
        if store is None or not self._model_key:
            return
        try:
            # keyed on the configured model name, not the deployment
            await store.record(self._model_key, self._model, mode)
        except Exception as exc:
            logger.warning("Could not record learned api mode %s: %s", mode, exc)

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
        try:
            return await self._complete_once(
                messages=messages, tools=tools, system=system, model=model,
                thinking_budget=thinking_budget, effort=effort,
                system_blocks=system_blocks,
            )
        except Exception as exc:
            mode = self._shape_fallback(exc)
            if mode is None:
                raise
        result = await self._complete_once(
            messages=messages, tools=tools, system=system, model=model,
            thinking_budget=thinking_budget, effort=effort,
            system_blocks=system_blocks,
        )
        # Only after the retry actually worked -- recording a guess would make
        # every later request build the wrong shape from the start.
        await self._record_api_mode(mode)
        return result

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
        """Stream, retrying once on a request-shape conflict.

        The retry only happens if it fires before anything was yielded --
        re-opening a stream that already emitted deltas would replay them to the
        client.
        """
        emitted = False
        try:
            async for event in self._stream_once(
                messages=messages, tools=tools, system=system, model=model,
                thinking_budget=thinking_budget, effort=effort,
                system_blocks=system_blocks,
            ):
                emitted = True
                yield event
            return
        except Exception as exc:
            # Retrying after deltas reached the client would replay them.
            if emitted:
                raise
            mode = self._shape_fallback(exc)
            if mode is None:
                raise

        async for event in self._stream_once(
            messages=messages, tools=tools, system=system, model=model,
            thinking_budget=thinking_budget, effort=effort,
            system_blocks=system_blocks,
        ):
            yield event
        await self._record_api_mode(mode)
