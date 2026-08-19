"""Google Gemini through the google-genai SDK, without LangChain.

Same rationale as the OpenAI/Anthropic direct transports: LangChain builds an
`AIMessageChunk` per streamed token and pydantic validates each one, which
measured ~9% of query-service CPU under load.

Gemini's wire shape has less in common with OpenAI's than Anthropic's does, so
this is a full transport rather than a subclass:

* messages are `Content` objects with a `parts` list and role `"user"` /
  `"model"` -- there is no assistant role and no system role, the system prompt
  is a separate `system_instruction` on the config;
* tool results go back as a `function_response` part, not a distinct message;
* tool schemas are an OpenAPI subset, so JSON-Schema keywords we emit have to be
  stripped or the request is rejected;
* thinking is `thinking_level` / `thinking_budget` on a `ThinkingConfig`.

Targets AI Studio (API key), not Vertex.
"""

from __future__ import annotations

import json
import logging
import uuid
from typing import TYPE_CHECKING, Any

from app.agent_loop_lib.core.exceptions import TransportError
from app.agent_loop_lib.core.messages import (
    AssistantMessage,
    Message,
    MessageRole,
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
from app.agent_loop_lib.transport.base import LLMTransport
from app.agent_loop_lib.transport.openai_responses import normalise_tool_call

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from app.agent_loop_lib.core.tool_schema import ToolSchema

logger = logging.getLogger(__name__)

# Gemini validates `parameters` against `google.genai.types.Schema`, which is
# declared extra="forbid" -- any key outside its field set raises a pydantic
# ValidationError inside _format_tools, which surfaces as a non-retryable
# TransportError and fails every tool-bearing request. So this is an ALLOW-list
# taken from the SDK's own model rather than a deny-list of keywords seen so
# far: a deny-list silently admits the next unsupported keyword a tool schema
# grows ($comment, multipleOf, prefixItems, uniqueItems all fail today).
def _allowed_schema_keys() -> frozenset[str]:
    """Field names and aliases `types.Schema` accepts, read off the SDK model so
    an SDK upgrade widens or narrows this automatically."""
    try:
        from google.genai import types as genai_types
    except ImportError:  # pragma: no cover - transport raises on construction
        return frozenset()
    fields = genai_types.Schema.model_fields
    keys = set(fields)
    keys.update(f.alias for f in fields.values() if f.alias)
    return frozenset(keys)


_FINISH_REASON_MAP = {
    "STOP": StopReason.END_TURN,
    "MAX_TOKENS": StopReason.MAX_TOKENS,
}

# Gemini stops for reasons that are not "the model finished". Mapping these to
# END_TURN makes a blocked or malformed generation indistinguishable from a
# normal empty answer, which is how a safety block gets misread as the model
# having nothing to say.
_ABNORMAL_FINISH_REASONS = frozenset({
    "SAFETY", "RECITATION", "PROHIBITED_CONTENT", "SPII", "BLOCKLIST",
    "MALFORMED_FUNCTION_CALL", "IMAGE_SAFETY", "UNEXPECTED_TOOL_CALL",
})

_RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504, 529}


def sanitize_schema(schema: Any) -> Any:
    """A JSON Schema into the subset `types.Schema` accepts.

    Recursive because an unsupported keyword at any depth fails the whole
    declaration, not just its own level.

    `$ref`/`$defs` are dropped rather than inlined, which loses the referenced
    structure for a schema built from a nested Pydantic model. langchain-google-genai
    inlines them; matching that is worth doing if a tool ever needs it, but
    dropping is what keeps the request valid today.
    """
    allowed = _allowed_schema_keys()

    if isinstance(schema, list):
        return [sanitize_schema(v) for v in schema]
    if not isinstance(schema, dict):
        return schema

    out: dict[str, Any] = {}
    for key, value in schema.items():
        if key not in allowed:
            continue
        if key in ("anyOf", "any_of"):
            out[key] = [sanitize_schema(v) for v in value]
        elif key == "properties":
            out[key] = {k: sanitize_schema(v) for k, v in value.items()}
        elif key == "items":
            out[key] = sanitize_schema(value)
        else:
            out[key] = sanitize_schema(value) if isinstance(value, (dict, list)) else value

    # An object with no properties still needs the key present, or the
    # declaration is rejected as malformed.
    if out.get("type") == "object" and "properties" not in out:
        out["properties"] = {}
    return out


class GeminiTransport(LLMTransport):
    """Gemini via `google-genai` (AI Studio).

    Install: pip install google-genai
    """

    DEFAULT_MODEL = "gemini-3-flash-preview"

    def __init__(
        self,
        api_key: str,
        model: str = DEFAULT_MODEL,
        temperature: float | None = None,
        thinking_level: str | None = None,
        thinking_budget: int | None = None,
        max_output_tokens: int | None = None,
        timeout: float | None = None,
        model_key: str | None = None,
    ) -> None:
        super().__init__()
        try:
            from google import genai
            from google.genai import types as genai_types
        except ImportError as exc:
            raise ImportError(
                "google-genai is required for GeminiTransport. "
                "Install it with: pip install google-genai"
            ) from exc
        self._genai = genai
        self._types = genai_types
        self._model = model
        self._temperature = temperature
        self._thinking_level = thinking_level
        self._thinking_budget = thinking_budget
        self._max_output_tokens = max_output_tokens
        self._model_key = model_key
        # Gemini 3 rejects a replayed function_call that has lost its
        # thought_signature ("Function call is missing a thought_signature in
        # functionCall parts. This is required for tools to work correctly"),
        # so the signature has to survive the round trip. It is opaque bytes
        # with nowhere to live on our provider-neutral `ToolCall`, so it is kept
        # here, keyed by call id, and re-attached when history is rebuilt. The
        # transport instance outlives the turn (the registry caches it), which
        # is the scope this needs.
        self._thought_signatures: dict[str, bytes] = {}
        # http_options carries the timeout in milliseconds; LangChain pins
        # DEFAULT_LLM_TIMEOUT, so a slow turn must fail at the same point.
        http_options = (
            genai_types.HttpOptions(timeout=int(timeout * 1000))
            if isinstance(timeout, (int, float)) else None
        )
        self._client = genai.Client(api_key=api_key, http_options=http_options)
        self.total_input_tokens: int = 0
        self.total_output_tokens: int = 0
        self.total_llm_calls: int = 0
        self.total_cache_read_tokens: int = 0
        self.total_cache_write_tokens: int = 0

    @staticmethod
    def _new_call_id() -> str:
        """A unique id for a call the provider did not identify.

        AI Studio leaves `function_call.id` unset. A positional fallback like
        `call_{index}` restarts at 0 on every request to the model, so the second
        turn's id collided with the first's: the signature map overwrote the
        earlier entry, and the tool-name lookup -- a last-wins dict -- resolved an
        older tool result to the newer call's name, handing the model a result
        labelled as a different function.
        """
        return f"call_{uuid.uuid4().hex[:16]}"

    @classmethod
    def from_langchain_model(
        cls, llm: Any, model_name: str = "", model_key: str | None = None,
    ) -> "GeminiTransport":
        """Build from an already-configured `ChatGoogleGenerativeAI`.

        Same contract as the other direct transports: every knob comes off the
        model the LangChain arm uses. `aimodels` sets `reasoning_effort`, which
        langchain-google-genai exposes as `thinking_level` (verified on the live
        deployment: effort "high" -> thinking_level "high"), so reading the
        attribute is enough here -- unlike Anthropic, where the translation only
        happens at request-build time.
        """
        def _val(name: str) -> str:
            raw = getattr(llm, name, None)
            secret = getattr(raw, "get_secret_value", None)
            return (secret() if callable(secret) else raw) or ""

        api_key = _val("google_api_key") or _val("api_key")
        if not api_key:
            raise ValueError(
                f"{type(llm).__name__} has no google_api_key; the direct Gemini "
                "transport only supports ChatGoogleGenerativeAI-configured models"
            )

        timeout = getattr(llm, "timeout", None)
        return cls(
            api_key=api_key,
            model=model_name or getattr(llm, "model", "") or cls.DEFAULT_MODEL,
            temperature=getattr(llm, "temperature", None),
            thinking_level=getattr(llm, "thinking_level", None),
            thinking_budget=getattr(llm, "thinking_budget", None),
            max_output_tokens=getattr(llm, "max_output_tokens", None)
            or getattr(llm, "max_tokens", None),
            timeout=timeout if isinstance(timeout, (int, float)) else None,
            model_key=model_key,
        )

    @property
    def provider(self) -> str:
        return "gemini"

    @property
    def model_name(self) -> str:
        return self._model

    # ------------------------------------------------------------------
    # Request shaping
    # ------------------------------------------------------------------

    def _format_contents(self, messages: list[Message]) -> list[Any]:
        """Framework messages into Gemini `Content` objects.

        Gemini has two roles, `user` and `model`. A tool result is a `user`
        message carrying a `function_response` part, not a role of its own, and
        an assistant tool call is a `model` message carrying `function_call`
        parts -- the history has to round-trip in that shape or the model loses
        track of what it already called.
        """
        types = self._types
        contents: list[Any] = []

        # Gemini correlates a function_response to its function_call by NAME, and
        # `ToolMessage` carries only `tool_call_id` -- there is no name on it. So
        # the name is recovered from the assistant message that made the call.
        # Sending the id here instead leaves the model unable to see its own tool
        # result: it answers as if the tool never ran, or not at all.
        call_names: dict[str, str] = {
            tc.id: tc.name
            for msg in messages
            if msg.role == MessageRole.ASSISTANT
            for tc in (msg.tool_calls or [])
            if tc.id
        }

        # Calls replayed as text because their signature is unknown; their
        # results have to be replayed as text too.
        flattened: set[str] = set()

        for msg in messages:
            if msg.role == MessageRole.TOOL:
                payload = (msg.content or "") + getattr(msg, "step_footer", "")
                name = call_names.get(msg.tool_call_id or "")
                if not name:
                    meta = getattr(msg, "artifact_meta", None)
                    name = getattr(meta, "tool_name", "") or "tool"
                if (msg.tool_call_id or "") in flattened:
                    # Its call was replayed as text, so a function_response here
                    # would have no function_call to attach to.
                    contents.append(types.Content(
                        role="user",
                        parts=[types.Part(
                            text="[previous tool result] " + name + ": " + payload
                        )],
                    ))
                    continue
                contents.append(types.Content(
                    role="user",
                    parts=[types.Part(function_response=types.FunctionResponse(
                        name=name,
                        response={"result": payload},
                    ))],
                ))
                continue

            if msg.role == MessageRole.ASSISTANT:
                parts: list[Any] = []
                if msg.text:
                    parts.append(types.Part(text=msg.text))
                for tc in msg.tool_calls or []:
                    signature = self._thought_signatures.get(tc.id or "")
                    if signature:
                        part = types.Part(function_call=types.FunctionCall(
                            name=tc.name, args=tc.arguments or {},
                        ))
                        part.thought_signature = signature
                        parts.append(part)
                        continue
                    # No signature: this call came from an earlier HTTP request,
                    # whose transport instance (and its signature map) is gone --
                    # PipesHubAgentFactory.create builds a fresh TransportRegistry
                    # per request. Gemini 3 rejects a function_call part without
                    # one, so the exchange is replayed as plain text instead. The
                    # model still sees what it called and what came back; it just
                    # cannot resume the earlier thought.
                    flattened.add(tc.id or "")
                    parts.append(types.Part(text=(
                        "[previous tool call] "
                        + tc.name
                        + "("
                        + json.dumps(tc.arguments or {}, default=str)
                        + ")"
                    )))
                if parts:
                    contents.append(types.Content(role="model", parts=parts))
                continue

            content = msg.content
            if isinstance(content, list):
                user_parts = self._content_parts(content)
                if user_parts:
                    contents.append(types.Content(role="user", parts=user_parts))
                    continue
                content = ""
            contents.append(types.Content(
                role="user", parts=[types.Part(text=content or "")],
            ))
        return contents

    def _content_parts(self, parts: list[Any]) -> list[Any]:
        """`list[Part]` into Gemini parts, keeping images.

        Gemini takes raw bytes with a mime type (`from_bytes`) or a URI
        (`from_uri`), not a data: URL, so the base64 has to be decoded here.
        """
        import base64

        types = self._types
        out: list[Any] = []
        for part in parts:
            if getattr(part, "type", None) == "image" and getattr(part, "source", None):
                source = part.source
                mime = source.media_type or "image/jpeg"
                if source.type == "base64":
                    try:
                        out.append(types.Part.from_bytes(
                            data=base64.b64decode(source.data), mime_type=mime,
                        ))
                    except Exception:
                        # A malformed attachment must not take the turn down;
                        # the text of the message is still worth sending.
                        logger.warning("gemini: dropping undecodable image attachment")
                    continue
                out.append(types.Part.from_uri(file_uri=source.data, mime_type=mime))
                continue
            text = getattr(part, "text", "")
            if text:
                out.append(types.Part(text=text))
        return out

    def _format_tools(self, tools: list[ToolSchema] | None) -> list[Any] | None:
        if not tools:
            return None
        types = self._types
        declarations = [
            types.FunctionDeclaration(
                name=t.name,
                description=t.description,
                parameters=sanitize_schema(
                    t.input_schema or {"type": "object", "properties": {}}
                ),
            )
            for t in tools
        ]
        return [types.Tool(function_declarations=declarations)]

    def _build_config(
        self, tools: list[ToolSchema] | None, system: str | None,
        system_blocks: list[str] | None, force_tool: str | None = None,
    ) -> Any:
        types = self._types
        if system_blocks and not system:
            system = "\n\n".join(b for b in system_blocks if b)

        thinking = None
        if self._thinking_level or self._thinking_budget:
            thinking = types.ThinkingConfig(
                thinking_level=self._thinking_level,
                thinking_budget=self._thinking_budget,
            )

        kwargs: dict[str, Any] = {}
        if system:
            kwargs["system_instruction"] = system
        if self._temperature is not None:
            kwargs["temperature"] = self._temperature
        if self._max_output_tokens:
            kwargs["max_output_tokens"] = self._max_output_tokens
        if thinking is not None:
            kwargs["thinking_config"] = thinking
        formatted = self._format_tools(tools)
        if formatted:
            kwargs["tools"] = formatted
            if force_tool:
                kwargs["tool_config"] = types.ToolConfig(
                    function_calling_config=types.FunctionCallingConfig(
                        mode="ANY", allowed_function_names=[force_tool],
                    )
                )
        return types.GenerateContentConfig(**kwargs)

    # ------------------------------------------------------------------
    # Response parsing
    # ------------------------------------------------------------------

    @staticmethod
    def _candidate_parts(chunk: Any) -> list[Any]:
        candidates = getattr(chunk, "candidates", None) or []
        if not candidates:
            return []
        content = getattr(candidates[0], "content", None)
        return list(getattr(content, "parts", None) or [])

    def _record_usage_from(self, response: Any) -> TokenUsage:
        usage = getattr(response, "usage_metadata", None)

        def _int(name: str) -> int:
            value = getattr(usage, name, 0) if usage is not None else 0
            return value if isinstance(value, int) else 0

        # thoughts_token_count is billed output that never appears as text, so
        # excluding it would under-report what a reasoning turn actually cost.
        output = _int("candidates_token_count") + _int("thoughts_token_count")
        return self._record_usage(
            _int("prompt_token_count"), output, _int("cached_content_token_count"),
        )

    def _record_usage(
        self, input_tokens: int, output_tokens: int, cache_read: int,
    ) -> TokenUsage:
        self.total_input_tokens += input_tokens
        self.total_output_tokens += output_tokens
        self.total_cache_read_tokens += cache_read
        self.total_llm_calls += 1
        return TokenUsage(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cache_read_tokens=cache_read,
            cache_write_tokens=0,
        )

    def _stop_reason(self, chunk: Any, has_tool_calls: bool) -> StopReason:
        candidates = getattr(chunk, "candidates", None) or []
        raw = getattr(candidates[0], "finish_reason", None) if candidates else None
        name = getattr(raw, "name", None) or (str(raw) if raw else None)
        # Truncation first, then tool calls, matching the OpenAI paths: a reply
        # cut off mid-call must not be reported as a usable TOOL_USE.
        if name == "MAX_TOKENS":
            return StopReason.MAX_TOKENS
        if name in _ABNORMAL_FINISH_REASONS:
            # Surfaced rather than silently ended: the caller sees a failed turn
            # instead of an empty answer it cannot explain.
            raise TransportError(
                f"Gemini stopped generating: {name}", retryable=False,
            )
        if has_tool_calls:
            return StopReason.TOOL_USE
        return _FINISH_REASON_MAP.get(name, StopReason.END_TURN)

    def _wrap_error(self, exc: Exception, context: str) -> TransportError:
        status = getattr(exc, "code", None) or getattr(exc, "status_code", None)
        retryable = status in _RETRYABLE_STATUS_CODES if isinstance(status, int) else False
        if not retryable:
            text = str(exc).lower()
            retryable = any(
                marker in text
                for marker in ("rate limit", "resource_exhausted", "unavailable",
                               "deadline", "timeout", "overloaded")
            )
        return TransportError(
            f"Gemini transport error ({context}): {exc}", retryable=retryable,
        )

    # ------------------------------------------------------------------
    # LLMTransport interface
    # ------------------------------------------------------------------

    async def complete(
        self,
        messages: list[Message],
        tools: list[ToolSchema] | None = None,
        system: str | None = None,
        model: str | None = None,
        thinking_budget: int | None = None,
        effort: str | None = None,
        system_blocks: list[str] | None = None,
        force_tool: str | None = None,
    ) -> ModelResponse:
        try:
            response = await self._client.aio.models.generate_content(
                model=model or self._model,
                contents=self._format_contents(messages),
                config=self._build_config(tools, system, system_blocks, force_tool),
            )
        except Exception as exc:
            raise self._wrap_error(exc, "complete") from exc

        return self._parse_complete(response, model or self._model)

    def _parse_complete(self, response: Any, resolved_model: str) -> ModelResponse:
        """Parsing sits behind the same error wrapping as the request itself:
        a malformed `args` payload should surface as a TransportError, not as a
        raw exception escaping the transport."""
        text_parts: list[str] = []
        tool_calls = []
        for idx, part in enumerate(self._candidate_parts(response)):
            call = getattr(part, "function_call", None)
            if call is not None:
                call_id = getattr(call, "id", None) or self._new_call_id()
                signature = getattr(part, "thought_signature", None)
                if signature:
                    self._thought_signatures[call_id] = signature
                tool_calls.append(normalise_tool_call(
                    call_id,
                    getattr(call, "name", "") or "",
                    json.dumps(dict(getattr(call, "args", None) or {})),
                ))
            elif getattr(part, "text", None) and not getattr(part, "thought", False):
                text_parts.append(part.text)

        message = AssistantMessage(
            content="".join(text_parts) or None, tool_calls=tool_calls or None,
        )
        stop_reason = self._stop_reason(response, bool(tool_calls))
        if stop_reason == StopReason.MAX_TOKENS:
            message.truncated = True
        return ModelResponse(
            message=message,
            usage=self._record_usage_from(response),
            stop_reason=stop_reason,
            model=resolved_model,
        )

    async def complete_structured(
        self,
        messages: list[Message],
        output_schema: dict[str, Any],
        system: str | None = None,
        model: str | None = None,
        system_blocks: list[str] | None = None,
    ) -> StructuredResponse:
        """Structured output via a single forced tool, matching how the other
        transports do it -- Gemini's native `response_schema` accepts a narrower
        schema subset than our callers pass."""
        from app.agent_loop_lib.core.tool_schema import ToolSchema as _ToolSchema

        tool = _ToolSchema(
            name="respond", description="Respond with the required structure.",
            input_schema=output_schema,
        )
        response = await self.complete(
            messages=messages, tools=[tool], system=system, model=model,
            system_blocks=system_blocks, force_tool="respond",
        )
        calls = response.message.tool_calls or []
        if not calls:
            # Returning {} would hand the planner, critic and skill extractor an
            # empty structure with no error -- a silent failure that reads as an
            # empty result. OpenAITransport raises here for the same reason.
            raise TransportError(
                "Gemini returned no structured response: the model answered with "
                "text instead of calling the forced tool",
                retryable=False,
            )
        return StructuredResponse(
            data=calls[0].arguments, usage=response.usage, model=response.model,
        )

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
        resolved_model = model or self._model
        text_parts: list[str] = []
        # Gemini sends a whole function_call in one chunk rather than streaming
        # its arguments, so calls are collected by index and emitted as a single
        # fragment carrying both name and arguments -- the agent loop reads the
        # name off the first delta for an index, which still holds.
        calls: list[tuple[str, str, str]] = []
        last_chunk: Any = None
        usage_chunk: Any = None

        try:
            stream = await self._client.aio.models.generate_content_stream(
                model=resolved_model,
                contents=self._format_contents(messages),
                config=self._build_config(tools, system, system_blocks),
            )
            async for chunk in stream:
                last_chunk = chunk
                if getattr(chunk, "usage_metadata", None) is not None:
                    usage_chunk = chunk
                for part in self._candidate_parts(chunk):
                    call = getattr(part, "function_call", None)
                    if call is not None:
                        index = len(calls)
                        call_id = getattr(call, "id", None) or self._new_call_id()
                        name = getattr(call, "name", "") or ""
                        args = json.dumps(dict(getattr(call, "args", None) or {}))
                        calls.append((call_id, name, args))
                        signature = getattr(part, "thought_signature", None)
                        if signature:
                            self._thought_signatures[call_id] = signature
                        yield ToolCallDeltaEvent(
                            index=index, id=call_id, name=name, arguments_delta=args,
                        )
                        continue
                    text = getattr(part, "text", None)
                    if not text:
                        continue
                    if getattr(part, "thought", False):
                        yield ThinkingDeltaEvent(delta=text)
                    else:
                        text_parts.append(text)
                        yield TextDeltaEvent(delta=text)
        except Exception as exc:
            raise self._wrap_error(exc, "stream") from exc

        tool_calls = [normalise_tool_call(cid, name, args) for cid, name, args in calls]
        message = AssistantMessage(
            content="".join(text_parts) or None, tool_calls=tool_calls or None,
        )
        stop_reason = self._stop_reason(last_chunk, bool(tool_calls))
        if stop_reason == StopReason.MAX_TOKENS:
            message.truncated = True
        usage = (
            self._record_usage_from(usage_chunk) if usage_chunk is not None
            else TokenUsage()
        )
        yield StreamCompleteEvent(
            response=ModelResponse(
                message=message, usage=usage, stop_reason=stop_reason,
                model=resolved_model,
            )
        )
