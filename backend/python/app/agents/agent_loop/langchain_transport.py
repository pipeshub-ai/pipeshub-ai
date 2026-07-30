"""`LangChainTransport`: adapts a LangChain `BaseChatModel` (PipesHub's
existing model layer, covering all 18 configured providers) to agent-loop's
`LLMTransport` ABC.

This is the only place PipesHub's LLM calls flow through agent-loop's model
resolution chain: `ModelSpec(provider="langchain", ...).resolve(registry)`
-> `TransportRegistry.resolve("langchain")` -> this transport -> wrapped in
`TransportModel`. No special-casing anywhere else in the adapter layer or
in agent-loop itself — `Agent`/`LoopStrategy` only ever see a `Model`.

We deliberately keep using LangChain's `BaseChatModel` + `StructuredTool`
here (not agent-loop's own Anthropic/OpenAI transports) per the migration's
design constraint: 18 providers are already wired and etcd-configured
through LangChain, and re-implementing that is out of scope.

Opik note: `OpikTracingTransport` (see `transport/opik_tracing.py`) wraps
THIS class and records its own manual `"llm"` span with a hand-built
message JSON blob — good enough for tool calls/usage, but Opik's frontend
"Pretty" message view is keyed off providers it recognizes from raw
LangChain callback data, which is how the legacy `chatbot.py`/
`utils/streaming.py` path (confirmed rendering the system prompt
correctly) actually gets traced. `_langchain_config()` below attaches
that same native `OpikTracer` callback (see
`opik_tracing.py::build_langchain_opik_callbacks`) to every `ainvoke`/
`astream` call here so agent-loop's LLM calls get that same
well-supported rendering, nested under whatever Opik span/trace is
already active via `opik.context_storage` — additional detail alongside,
not a replacement for, `OpikTracingTransport`'s own summary span.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from langchain_core.messages import AIMessage

from app.agent_loop_lib.core.exceptions import TransportError
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
from app.agent_loop_lib.transport.base import LLMTransport
from app.agent_loop_lib.transport.opik_tracing import build_langchain_opik_callbacks
from app.agents.agent_loop.converters import (
    convert_assistant_message_from_langchain,
    convert_messages_to_langchain,
    convert_tool_schemas_to_langchain,
    output_schema_to_pydantic_model,
    token_usage_from_ai_message,
)
from app.utils.llm_api_mode_store import (
    REASONING_MANDATORY_FALLBACK_EFFORT,
    LLMApiMode,
    get_llm_api_mode_store,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from langchain_core.language_models.chat_models import BaseChatModel

    from app.agent_loop_lib.core.messages import Message
    from app.agent_loop_lib.core.tool_schema import ToolSchema

logger = logging.getLogger(__name__)

_STOP_REASON_TOOL_CALLS = {"tool_calls", "tool_use"}
_STOP_REASON_TRUNCATED = {"length", "max_tokens"}

# No HTTP status code means the failure never reached the provider (DNS,
# TCP reset, read timeout, ...) — these are transient by nature and should
# be retried the same way a 429/5xx would be. Since this transport is
# deliberately provider-agnostic (LangChain wraps 18 different provider
# SDKs — openai, anthropic, google, boto3, httpx-based, ...), we can't
# import every SDK's exception hierarchy here; matching on the standard
# library network/timeout errors plus each SDK's consistent
# "*Connection*"/"*Timeout*" naming convention covers the common transient
# cases without a hard dependency on any one provider's package.
_NETWORK_ERROR_NAME_HINTS = ("connectionerror", "connecttimeout", "readtimeout", "timeouterror", "apitimeouterror", "apiconnectionerror")

# Substrings providers/gateways actually emit when reasoning + bound function
# tools can't both go through the API shape the request used. Matched
# case-insensitively against `str(exc)` — deliberately loose (not a status
# code check) since these come from very different SDKs (openai, httpx-based
# gateway clients, ...) with no shared exception hierarchy.
# - "please use /v1/responses instead": Chat Completions rejected the
#   combination (the exact case that motivated this — see module docstring).
# - "please use /v1/chat/completions instead": the inverse — a Responses-API
#   attempt landed on a backend that only supports this on Chat Completions.
# - "tool_choice.function": Chat-Completions-shaped tool_choice sent to a
#   Responses-only model/deployment.
_API_SHAPE_CONFLICT_MARKERS = (
    "please use /v1/responses instead",
    "please use /v1/chat/completions instead",
    "tool_choice.function",
)


def _is_api_shape_conflict(exc: Exception) -> bool:
    message = str(exc).lower()
    return any(marker in message for marker in _API_SHAPE_CONFLICT_MARKERS)


# Substring a provider/gateway emits when it refuses to let reasoning be
# turned off at all — observed on some OpenRouter-proxied Gemini models:
# "Reasoning is mandatory for this endpoint and cannot be disabled."
_REASONING_MANDATORY_CONFLICT_MARKERS = ("reasoning is mandatory",)


def _is_reasoning_mandatory_conflict(exc: Exception) -> bool:
    message = str(exc).lower()
    return any(marker in message for marker in _REASONING_MANDATORY_CONFLICT_MARKERS)


def _truncate_raw_for_log(raw: Any, max_len: int = 500) -> str:  # noqa: ANN401
    """Best-effort string preview of the raw LLM response for diagnostics."""
    try:
        text = str(raw)
    except Exception:
        return "<unrenderable>"
    if len(text) > max_len:
        return text[:max_len] + "... [truncated]"
    return text


def _extract_json_from_raw(raw: Any) -> dict[str, Any] | None:
    """Last-resort extraction: pull JSON from the raw AIMessage content when
    LangChain's own structured-output parser returns parsed=None.

    Many providers return perfectly valid JSON in the message body that
    LangChain's parser fails to pick up (e.g. the model wraps it in markdown
    fences, or the provider's response envelope differs from what the parser
    expects).  Rather than giving up entirely, we try to salvage it."""
    import json
    import re

    text: str | None = None
    if isinstance(raw, AIMessage):
        content = raw.content
        if isinstance(content, str):
            text = content
        elif isinstance(content, list):
            text = " ".join(
                block.get("text", "") if isinstance(block, dict) else str(block)
                for block in content
            )
    if not text:
        return None

    # Strip markdown code fences if the model wrapped its JSON in them.
    fence_match = re.search(r"```(?:json)?\s*\n?(.*?)```", text, re.DOTALL)
    if fence_match:
        text = fence_match.group(1).strip()

    try:
        obj = json.loads(text)
        return obj if isinstance(obj, dict) else None
    except (json.JSONDecodeError, ValueError):
        return None


def _is_network_error(exc: Exception) -> bool:
    if isinstance(exc, (ConnectionError, TimeoutError, OSError)):
        return True
    type_name = type(exc).__name__.lower()
    return any(hint in type_name for hint in _NETWORK_ERROR_NAME_HINTS)


class LangChainTransport(LLMTransport):
    """Bridges a LangChain `BaseChatModel` to agent-loop's `LLMTransport`."""

    def __init__(
        self,
        chat_model: BaseChatModel,
        model_name: str = "",
        opik_project_name: str | None = None,
        model_key: str | None = None,
    ) -> None:
        self._llm = chat_model
        self._model = model_name
        # etcd `aiModels` config-entry key (see `app/utils/aimodels.py`'s
        # `get_generator_model`) — the identity a learned API-mode fact is
        # recorded/looked-up against (`app/utils/llm_api_mode_store.py`).
        # `None` for call sites that don't thread it through yet, in which
        # case `_record_api_mode` just skips persisting (the in-request
        # runtime fallback below still applies either way).
        self._model_key = model_key
        self._opik_callbacks = build_langchain_opik_callbacks(opik_project_name)

    def _langchain_config(self) -> dict[str, Any]:
        """`config=` kwarg for every `ainvoke`/`astream` call below — see
        module docstring for why this attaches the native `OpikTracer`
        callback rather than relying solely on `OpikTracingTransport`'s
        own manual span."""
        return {"callbacks": self._opik_callbacks} if self._opik_callbacks else {}

    @property
    def provider(self) -> str:
        return "langchain"

    def _resolve_model_name(self, model: str | None) -> str:
        return model or self._model

    def _bind_tools(
        self, tools: list[ToolSchema] | None, llm: BaseChatModel | None = None,
    ) -> BaseChatModel:
        """Binds `tools` onto `llm` (defaults to `self._llm`), or raises a
        `TransportError` if binding fails.

        The optional `llm` override exists for the API-shape fallback in
        `complete()`/`stream()`: the retry attempt rebinds the same tools
        onto a differently-configured model copy (reasoning cleared or
        moved to the Responses API) without touching `self._llm` until
        that retry actually succeeds.

        This USED to fail-soft (`except: pass`, then later a bare WARNING
        log) and silently continue the turn with the model's tools stripped
        — from the outside, that looked identical to "the model just chose
        not to call a tool", which made "why didn't the agent use
        run_code/etc." reports impossible to diagnose from logs alone, and
        worse, let a turn that explicitly needed a tool (e.g. `task_complete`)
        run to `max_turns` with no way to ever satisfy it. When the caller
        asked for tools, a provider/model that can't bind them is a hard
        failure for this call, not a degraded-but-still-valid one — raising
        here lets it flow through the same `TransportError` handling every
        other transport failure already goes through (retry middleware,
        `_wrap_error`'s callers, the adapter's error-response path), instead
        of a distinct silent-degradation code path nothing else knows about.
        """
        llm = llm if llm is not None else self._llm
        if not tools:
            logger.debug("LangChainTransport: no tools offered for this turn")
            return llm
        tool_names = [t.name for t in tools]
        lc_tools = convert_tool_schemas_to_langchain(tools)
        try:
            bound = llm.bind_tools(lc_tools)
        except Exception as exc:
            logger.error(
                "LangChainTransport: bind_tools() failed for %d tool(s) %s — "
                "refusing to silently run this turn without them",
                len(tool_names), tool_names, exc_info=True,
            )
            raise TransportError(
                f"bind_tools() failed for {len(tool_names)} tool(s) {tool_names}: {exc}",
                retryable=False,
            ) from exc
        logger.info(
            "LangChainTransport: bound %d tool(s) to LLM call: %s",
            len(tool_names), tool_names,
        )
        return bound

    def _wrap_error(self, exc: Exception, context: str) -> TransportError:
        status_code = getattr(exc, "status_code", None)
        # 529 is Anthropic's non-standard "overloaded_error" status —
        # capacity exhaustion across all customers, not this request's
        # fault — and in practice the single most common transient failure
        # against the Anthropic API. `anthropic.APIStatusError` (raised
        # directly through langchain_anthropic's `_astream`/`_agenerate`,
        # unwrapped) carries it on `.status_code`, same as any other HTTP
        # status. Kept in sync with `RetryConfig.retryable_status_codes`
        # default (`transport/base.py`) and the native
        # `AnthropicTransport._RETRYABLE_STATUS_CODES` — retryable=True
        # here only gets a retry if that config list also allows the code.
        retryable = (
            status_code in (429, 500, 502, 503, 504, 529) if status_code else _is_network_error(exc)
        )
        return TransportError(
            f"LangChain transport error ({context}): {exc}",
            status_code=status_code,
            retryable=retryable,
        )

    def _api_shape_fallback(self, exc: Exception) -> tuple[BaseChatModel, str] | None:
        """Builds the "other API shape" copy of `self._llm` to retry once
        against, when `exc` looks like the reasoning+tools API-shape
        conflict described at `_API_SHAPE_CONFLICT_MARKERS`. Returns
        `None` when `exc` doesn't match, or the model has no
        `use_responses_api` attribute to flip (non-OpenAI-family
        integrations — Anthropic, Gemini, Bedrock, Ollama, ... — have no
        such conflict to begin with).

        Direction is decided by the model's OWN current state, not by
        which marker matched (both directions can, in principle, produce
        either message depending on the gateway):
        - Already on the Responses API: that attempt itself was rejected,
          so this gateway/backend can't do reasoning + tools at all for
          this model — drop reasoning entirely, keep tools on Chat
          Completions. `use_responses_api=None` (not `False`) leaves
          LangChain's own Responses-only model detection
          (`_model_prefers_responses_api`) in charge, so this never
          force-downgrades a model that only exists on the Responses API
          (`gpt-5-pro`, `codex`, ...).
        - Not yet on the Responses API: flip to it, moving
          `reasoning_effort` into the `reasoning` dict shape the Responses
          API expects (`_default_params` drops `None` values, so clearing
          the other kwarg outright is required, not optional).
        """
        if not _is_api_shape_conflict(exc):
            return None
        llm = self._llm
        if not hasattr(llm, "use_responses_api"):
            return None
        if getattr(llm, "use_responses_api", None):
            return llm.model_copy(update={
                "use_responses_api": None, "reasoning": None, "reasoning_effort": None,
            }), LLMApiMode.NO_REASONING_WITH_TOOLS.value
        effort = getattr(llm, "reasoning_effort", None)
        return llm.model_copy(update={
            "use_responses_api": True,
            "reasoning": {"effort": effort} if effort else None,
            "reasoning_effort": None,
        }), LLMApiMode.RESPONSES.value

    def _reasoning_mandatory_fallback(self, exc: Exception) -> tuple[BaseChatModel, str] | None:
        """Builds a copy of `self._llm` with reasoning bumped off an
        explicitly disabled value, when `exc` looks like a provider
        hard-rejecting "no reasoning" outright (see
        `_REASONING_MANDATORY_CONFLICT_MARKERS`).

        Returns `None` when `exc` doesn't match, or reasoning wasn't
        actually set to a disabled value on this call — a false-positive
        marker match with nothing to bump means the 400 has some other
        cause, so there's nothing useful to retry here.

        `REASONING_MANDATORY_FALLBACK_EFFORT` ("low") rather than
        `DEFAULT_REASONING_EFFORT` ("high"): the user's actual choice was
        "off", so the smallest still-legal step away from that is a closer
        match to their intent than jumping to the platform default.
        """
        if not _is_reasoning_mandatory_conflict(exc):
            return None
        llm = self._llm
        updates: dict[str, Any] = {}
        if getattr(llm, "reasoning_effort", None) == "none":
            updates["reasoning_effort"] = REASONING_MANDATORY_FALLBACK_EFFORT
        current_reasoning = getattr(llm, "reasoning", None)
        if current_reasoning is False:
            updates["reasoning"] = REASONING_MANDATORY_FALLBACK_EFFORT
        elif isinstance(current_reasoning, dict) and current_reasoning.get("effort") == "none":
            updates["reasoning"] = {**current_reasoning, "effort": REASONING_MANDATORY_FALLBACK_EFFORT}
        if not updates:
            return None
        return llm.model_copy(update=updates), LLMApiMode.REASONING_MANDATORY.value

    def _conflict_fallback(self, exc: Exception) -> tuple[BaseChatModel, str] | None:
        """Tries each known provider-conflict fallback in turn against
        `exc`, returning the first that matches — `complete()`/`stream()`
        retry identically regardless of which one fired. See
        `_api_shape_fallback` (Chat Completions vs Responses API shape) and
        `_reasoning_mandatory_fallback` (provider refuses reasoning
        disabled outright)."""
        return self._api_shape_fallback(exc) or self._reasoning_mandatory_fallback(exc)

    async def _record_api_mode(self, mode: str) -> None:
        """Persists a learned API-mode fact once a fallback retry has
        actually succeeded, so `aimodels.get_generator_model()` applies it
        from construction time on every later call for this model — see
        `app/utils/llm_api_mode_store.py`. A no-op when the store hasn't
        been initialized yet (e.g. this transport is used outside the
        query service's normal startup path) or `model_key` was never
        threaded through to this transport; the in-request retry above
        still applies regardless."""
        store = get_llm_api_mode_store()
        if store is None or not self._model_key:
            return
        await store.record(self._model_key, self._model, mode)

    def _log_turn_outcome(
        self, tools: list[ToolSchema] | None, ai_message: AIMessage, stop_reason: StopReason,
    ) -> None:
        """Correlates "how many tools were offered" with "did the model
        call one" — the missing link needed to tell "tool wasn't available"
        apart from "tool was available but the model didn't call it" from
        logs alone (both looked identical before this)."""
        if not tools:
            return
        tool_calls = getattr(ai_message, "tool_calls", None) or []
        if tool_calls:
            logger.info(
                "LangChainTransport: model called %d tool(s): %s",
                len(tool_calls), [tc.get("name") for tc in tool_calls],
            )
        else:
            logger.info(
                "LangChainTransport: model did NOT call any tool this turn "
                "(%d tool(s) were offered, stop_reason=%s)",
                len(tools), stop_reason,
            )

    def _stop_reason_from(self, ai_message: AIMessage) -> StopReason:
        if ai_message.tool_calls:
            return StopReason.TOOL_USE
        metadata = ai_message.response_metadata or {}
        finish_reason = metadata.get("finish_reason") or metadata.get("stop_reason")
        if finish_reason in _STOP_REASON_TRUNCATED:
            return StopReason.MAX_TOKENS
        if finish_reason in _STOP_REASON_TOOL_CALLS:
            return StopReason.TOOL_USE
        return StopReason.END_TURN

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
        # thinking_budget/effort are LangChain-provider-specific `.bind()`
        # kwargs (e.g. ChatAnthropic(thinking=...), ChatOpenAI(reasoning_effort=...))
        # configured once at model-construction time in PipesHub's existing
        # LLM factory, not per-call — silently ignored here per the
        # LLMTransport contract ("unsupported knobs must not raise").
        # system_blocks: LangChain has no cache-breakpoint API; join if needed.
        if system_blocks and not system:
            system = "\n\n".join(b for b in system_blocks if b)
        lc_messages = convert_messages_to_langchain(messages, system)
        lc_llm = self._bind_tools(tools)

        try:
            ai_message = await lc_llm.ainvoke(lc_messages, config=self._langchain_config())
        except Exception as exc:
            fallback = self._conflict_fallback(exc)
            if fallback is None:
                raise self._wrap_error(exc, "complete") from exc
            fallback_llm, mode = fallback
            logger.warning(
                "LangChainTransport.complete: provider conflict for model=%s, "
                "retrying once with api_mode=%s: %s",
                self._model, mode, exc,
            )
            try:
                fallback_bound = self._bind_tools(tools, llm=fallback_llm)
                ai_message = await fallback_bound.ainvoke(lc_messages, config=self._langchain_config())
            except Exception as retry_exc:
                logger.error(
                    "LangChainTransport.complete: retry with api_mode=%s also failed for "
                    "model=%s: %s — raising the ORIGINAL error",
                    mode, self._model, retry_exc,
                )
                raise self._wrap_error(exc, "complete") from exc
            # Pinned for the rest of this agent loop (many more calls will
            # follow) and persisted so the NEXT request for this model
            # skips straight to the working shape.
            self._llm = fallback_llm
            await self._record_api_mode(mode)
            logger.info(
                "LangChainTransport.complete: retry succeeded — pinned api_mode=%s for "
                "model=%s for the rest of this run",
                mode, self._model,
            )

        assistant_message = convert_assistant_message_from_langchain(ai_message)
        usage = token_usage_from_ai_message(ai_message)
        stop_reason = (
            StopReason.MAX_TOKENS if assistant_message.truncated
            else self._stop_reason_from(ai_message)
        )
        self._log_turn_outcome(tools, ai_message, stop_reason)
        return ModelResponse(
            message=assistant_message,
            usage=usage,
            stop_reason=stop_reason,
            model=self._resolve_model_name(model),
        )

    async def complete_structured(
        self,
        messages: list[Message],
        output_schema: dict[str, Any],
        system: str | None = None,
        model: str | None = None,
    ) -> StructuredResponse:
        lc_messages = convert_messages_to_langchain(messages, system)
        resolved_model = self._resolve_model_name(model)

        parsed, raw = await self._invoke_structured(lc_messages, output_schema)
        usage = token_usage_from_ai_message(raw) if isinstance(raw, AIMessage) else TokenUsage()
        return StructuredResponse(
            data=parsed or {},
            usage=usage,
            model=resolved_model,
        )

    async def _invoke_structured(
        self, lc_messages: list, output_schema: dict[str, Any]
    ) -> tuple[dict[str, Any] | None, Any]:
        """Try `with_structured_output()` with the raw JSON-schema dict
        first (supported by most modern LangChain chat model integrations
        per `BaseChatModel.with_structured_output`'s docstring); fall back
        to a dynamically-built Pydantic model for the providers/versions
        that require one — see the module docstring in `converters.py`.

        If both structured attempts yield parsed=None (the LLM returned
        content but LangChain's parser couldn't map it), we try one more
        time to manually extract JSON from the raw AIMessage body.  This
        covers the common case where the model wraps valid JSON in markdown
        fences or the provider's response envelope surprises LangChain's
        output parser."""
        first_attempt_error: Exception | None = None
        first_raw: Any = None
        try:
            structured_llm = self._llm.with_structured_output(output_schema, include_raw=True)
            result = await structured_llm.ainvoke(lc_messages, config=self._langchain_config())
            parsed = result.get("parsed") if isinstance(result, dict) else None
            first_raw = result.get("raw") if isinstance(result, dict) else None
            if parsed is not None:
                return parsed, first_raw
            parsing_error = result.get("parsing_error") if isinstance(result, dict) else None
            logger.warning(
                "LangChainTransport: raw-schema attempt returned parsed=None; "
                "parsing_error=%s; raw=%s",
                parsing_error, _truncate_raw_for_log(first_raw),
            )
        except Exception as exc:
            first_attempt_error = exc
            logger.warning(
                "LangChainTransport: raw-schema attempt raised %s: %s",
                type(exc).__name__, exc,
            )

        second_raw: Any = None
        second_parsing_error: Exception | None = None
        try:
            args_model = output_schema_to_pydantic_model(output_schema)
            structured_llm = self._llm.with_structured_output(args_model, include_raw=True)
            result = await structured_llm.ainvoke(lc_messages, config=self._langchain_config())
        except Exception as exc:
            raise self._wrap_error(exc, "complete_structured") from exc

        second_raw = result.get("raw") if isinstance(result, dict) else None
        parsed_obj = result.get("parsed") if isinstance(result, dict) else None
        second_parsing_error = result.get("parsing_error") if isinstance(result, dict) else None
        parsed = parsed_obj.model_dump() if hasattr(parsed_obj, "model_dump") else parsed_obj
        if parsed is not None:
            return parsed, second_raw
        logger.warning(
            "LangChainTransport: Pydantic-model attempt also returned parsed=None; "
            "parsing_error=%s; raw=%s",
            second_parsing_error, _truncate_raw_for_log(second_raw),
        )

        best_raw = second_raw or first_raw
        manual = _extract_json_from_raw(best_raw)
        if manual is not None:
            logger.info(
                "LangChainTransport: recovered structured output via manual "
                "JSON extraction from raw AIMessage content",
            )
            return manual, best_raw

        raw_preview = _truncate_raw_for_log(best_raw)
        parts = [
            "LangChain structured output parsing failed for both raw-schema and "
            "Pydantic-model attempts",
        ]
        if first_attempt_error:
            parts.append(f"first attempt error: {first_attempt_error}")
        if second_parsing_error:
            parts.append(f"second attempt parsing_error: {second_parsing_error}")
        parts.append(f"raw response: {raw_preview}")
        raise TransportError("; ".join(parts))

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
        if system_blocks and not system:
            system = "\n\n".join(b for b in system_blocks if b)
        lc_messages = convert_messages_to_langchain(messages, system)
        lc_llm = self._bind_tools(tools)

        chunks: list[AIMessage] = []
        fallback_llm: BaseChatModel | None = None
        fallback_mode: str | None = None
        original_exc: Exception | None = None
        retried = False
        current_llm = lc_llm
        while True:
            try:
                async for chunk in current_llm.astream(lc_messages, config=self._langchain_config()):
                    chunks.append(chunk)
                    text = getattr(chunk, "content", None)
                    if isinstance(text, str) and text:
                        yield TextDeltaEvent(delta=text)
                    elif isinstance(text, list):
                        for block in text:
                            if isinstance(block, dict) and block.get("type") == "text":
                                delta = block.get("text", "")
                                if delta:
                                    yield TextDeltaEvent(delta=delta)

                    # Extended-thinking / reasoning deltas (Claude extended
                    # thinking, o-series/DeepSeek reasoning_content). LangChain
                    # normalizes all of these into `content_blocks` — the same
                    # accessor `convert_assistant_message_from_langchain` uses
                    # for the final message — so per-chunk reasoning text
                    # surfaces uniformly here without hand-parsing each
                    # provider's raw delta shape.
                    for block in chunk.content_blocks:
                        if block.get("type") == "reasoning":
                            reasoning_delta = block.get("reasoning", "")
                            if reasoning_delta:
                                yield ThinkingDeltaEvent(delta=reasoning_delta)

                    # Tool-call argument deltas — used by Agent.step()'s streaming
                    # branch to mirror final_answer.answer_markdown into live
                    # TEXT_MESSAGE_CONTENT events (Phase 7).
                    # NOTE: Ollama delivers tool calls whole (no incremental
                    # tool_call_chunks), so local models get exactly one delta
                    # for each tool call when StreamCompleteEvent is processed.
                    for tc_chunk in getattr(chunk, "tool_call_chunks", []) or []:
                        if not isinstance(tc_chunk, dict):
                            continue
                        args_delta = tc_chunk.get("args") or ""
                        if not args_delta:
                            continue
                        yield ToolCallDeltaEvent(
                            index=tc_chunk.get("index") or 0,
                            id=tc_chunk.get("id"),
                            name=tc_chunk.get("name"),
                            arguments_delta=args_delta,
                        )
                break
            except Exception as exc:
                # A 400 raised while opening the stream is safe to retry —
                # nothing has reached the client yet. The same `except`
                # also catches a failure mid-stream (after some chunks
                # already yielded), and retrying THAT would replay text the
                # client already received, so `chunks` (and `retried`, to
                # cap this to exactly one attempt) gate the retry.
                if retried:
                    logger.error(
                        "LangChainTransport.stream: retry with api_mode=%s also failed for "
                        "model=%s: %s — raising the ORIGINAL error",
                        fallback_mode, self._model, exc,
                    )
                    raise self._wrap_error(original_exc, "stream") from original_exc
                if chunks:
                    raise self._wrap_error(exc, "stream") from exc
                fallback = self._conflict_fallback(exc)
                if fallback is None:
                    raise self._wrap_error(exc, "stream") from exc
                original_exc = exc
                fallback_llm, fallback_mode = fallback
                try:
                    current_llm = self._bind_tools(tools, llm=fallback_llm)
                except Exception:
                    # Same rationale as `complete()`'s identical guard: a
                    # bind failure on the fallback model must not replace
                    # the original provider-conflict error with a less
                    # useful "bind_tools() failed" one.
                    raise self._wrap_error(exc, "stream") from exc
                retried = True
                logger.warning(
                    "LangChainTransport.stream: provider conflict for model=%s, "
                    "retrying once with api_mode=%s: %s",
                    self._model, fallback_mode, exc,
                )

        if retried and fallback_llm is not None:
            # Pinned for the rest of this agent loop (many more calls will
            # follow) and persisted so the NEXT request for this model
            # skips straight to the working shape.
            self._llm = fallback_llm
            await self._record_api_mode(fallback_mode)
            logger.info(
                "LangChainTransport.stream: retry succeeded — pinned api_mode=%s for "
                "model=%s for the rest of this run",
                fallback_mode, self._model,
            )

        if not chunks:
            final_ai_message = AIMessage(content="")
        else:
            final_ai_message = chunks[0]
            for chunk in chunks[1:]:
                final_ai_message = final_ai_message + chunk

        assistant_message = convert_assistant_message_from_langchain(final_ai_message)
        usage = token_usage_from_ai_message(final_ai_message)
        stop_reason = (
            StopReason.MAX_TOKENS if assistant_message.truncated
            else self._stop_reason_from(final_ai_message)
        )
        self._log_turn_outcome(tools, final_ai_message, stop_reason)
        yield StreamCompleteEvent(
            response=ModelResponse(
                message=assistant_message,
                usage=usage,
                stop_reason=stop_reason,
                model=self._resolve_model_name(model),
            )
        )


__all__ = ["LangChainTransport"]
