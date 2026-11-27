"""Google's Gemini API (``generateContent``) on the wire ↔ the kernel's model types (pure; WP-13).

Gemini CLI speaks only this API to a custom ``GOOGLE_GEMINI_BASE_URL``: ``POST
/v1beta/models/{model}:generateContent``, ``:streamGenerateContent?alt=sse`` and ``:countTokens``. The model
is named in the path, not the body, so the server hands the wire an envelope: ``{"model", "stream",
"request"}``, ``request`` being the body as sent. Inbound, ``systemInstruction`` and the ``contents`` (text,
inline images, ``functionCall`` and ``functionResponse`` parts) become the kernel ``Message``s every
``LLMTransport`` speaks, so the org's model is reached whatever its provider. Outbound, a ``ModelResponse`` or
its ``StreamEvent``s become a ``GenerateContentResponse`` or its SSE chunks, and refusals read as Google's error
envelope, tagged with the gateway's code as the other wires' are.

Gemini's ``functionCall`` and ``functionResponse`` carry an optional ``id``; the answer always sets it, so a
call and its response pair by id, and a history without ids pairs them by name, in order.

Stateless: nothing is stored; the caller sends its whole history every time.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, cast

from harness_core.access.types import ModelApi
from harness_core.gateway.chat_wire import image_source
from harness_core.gateway.errors import InvalidGeminiRequest, UnsupportedParameter
from harness_core.kernel.core.messages import (
    MALFORMED_TOOL_CALL_ERROR_KEY,
    AssistantMessage,
    ImagePart,
    TextPart,
    ThinkingPart,
    ToolCall,
    ToolMessage,
    UserMessage,
)
from harness_core.kernel.core.responses import StopReason
from harness_core.kernel.core.streaming import (
    StreamCompleteEvent,
    TextDeltaEvent,
    ThinkingDeltaEvent,
)
from harness_core.kernel.core.tool_schema import ToolSchema

if TYPE_CHECKING:
    from collections.abc import Mapping

    from harness_core.gateway.wire import Json, WireCall
    from harness_core.kernel.core.messages import Message, Part
    from harness_core.kernel.core.responses import ModelResponse, TokenUsage
    from harness_core.kernel.core.streaming import StreamEvent

# What needs Google's own state or tools, or an answer shape the transports cannot produce: refused rather than
# ignored (principle 6).
REFUSED_PARAMETERS = frozenset({"cachedContent"})
REFUSED_GENERATION = frozenset(
    {
        "stopSequences",
        "responseSchema",
        "responseJsonSchema",
        "responseLogprobs",
        "logprobs",
        "speechConfig",
        "audioTimestamp",
        "routingConfig",
        "modelSelectionConfig",
        "enableEnhancedCivicAnswers",
        "imageConfig",
    }
)
# Everything else is accepted but not forwarded (logged as dropped): sampling is pinned by the org's model
# configuration, and safety settings and labels are Google's.
_READ = frozenset({"contents", "systemInstruction", "tools", "toolConfig", "generationConfig"})
_READ_GENERATION = frozenset({"maxOutputTokens", "thinkingConfig"})
_TEXT_TYPES = frozenset({"text/plain", ""})
_EFFORTS: Mapping[str, str] = {"MINIMAL": "minimal", "LOW": "low", "MEDIUM": "medium", "HIGH": "high"}
_FINISH: Mapping[StopReason, str] = {
    StopReason.END_TURN: "STOP",
    StopReason.TOOL_USE: "STOP",
    StopReason.MAX_TOKENS: "MAX_TOKENS",
    StopReason.STOP_SEQUENCE: "STOP",
    StopReason.ERROR: "OTHER",
    StopReason.CANCELLED: "OTHER",
}
_STATUS: Mapping[int, str] = {
    400: "INVALID_ARGUMENT",
    401: "UNAUTHENTICATED",
    402: "RESOURCE_EXHAUSTED",
    403: "PERMISSION_DENIED",
    404: "NOT_FOUND",
    409: "ABORTED",
    413: "INVALID_ARGUMENT",
    429: "RESOURCE_EXHAUSTED",
    499: "CANCELLED",
    503: "UNAVAILABLE",
    504: "DEADLINE_EXCEEDED",
}
# A gateway reason code: what a refusal's message is tagged with (``[gateway:<code>]``), as on the other wires.
_GATEWAY_CODE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
ERROR_DOMAIN = "pipeshub.model-gateway"
# The path's last segment: ``{model}:{method}``.
METHODS = frozenset({"generateContent", "streamGenerateContent", "countTokens"})


@dataclass(frozen=True, slots=True)
class GeminiRequest:
    model: str
    system: str | None
    messages: tuple[Message, ...]
    tools: tuple[ToolSchema, ...]
    stream: bool
    reasoning_effort: str | None
    max_output_tokens: int | None
    dropped: tuple[str, ...] = ()
    reasoning_items: int = 0
    encrypted_reasoning: bool = False

    @property
    def output_limit(self) -> int | None:
        return self.max_output_tokens


def model_method(target: str) -> tuple[str, str] | None:
    """``(model, method)`` from the path's ``{model}:{method}``, ``None`` for anything else."""
    model, sep, method = target.rpartition(":")
    model = model.removeprefix("models/")
    if not sep or not model or method not in METHODS or "/" in model:
        return None
    return model, method


def envelope(model: str, request: object, *, stream: bool) -> Json:
    """What the server hands ``parse``: the model and streaming the path says, and the body as sent.
    ``countTokens`` may wrap its body in ``generateContentRequest``; that inner request is the one counted."""
    if isinstance(request, dict):
        inner = cast("Json", request).get("generateContentRequest")
        if isinstance(inner, dict):
            request = cast("Json", inner)
    return {"model": model, "stream": stream, "request": request}


def _object(value: object, what: str) -> Json:
    if not isinstance(value, dict):
        raise InvalidGeminiRequest(f"{what} must be an object", param=what)
    return cast("Json", value)


def _list(value: object, what: str) -> list[object]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise InvalidGeminiRequest(f"{what} must be a list", param=what)
    return cast("list[object]", value)


def _system(value: object) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        return value or None
    content = _object(value, "systemInstruction")
    texts: list[str] = []
    for item in _list(content.get("parts"), "systemInstruction.parts"):
        part = _object(item, "systemInstruction.parts")
        if set(part) - {"text", "thought", "thoughtSignature"}:
            raise UnsupportedParameter("systemInstruction.parts", "systemInstruction takes text parts only")
        texts.append(str(part.get("text") or ""))
    return "\n\n".join(t for t in texts if t) or None


def _inline(part: Json, what: str) -> ImagePart:
    data = _object(part.get("inlineData"), f"{what}.inlineData")
    mime, payload = data.get("mimeType"), data.get("data")
    if not isinstance(mime, str) or not mime.startswith("image/"):
        raise UnsupportedParameter(f"{what}.inlineData.mimeType", "only image inline data is served")
    if not isinstance(payload, str) or not payload:
        raise InvalidGeminiRequest("inline data without data", param=f"{what}.inlineData.data")
    return ImagePart(source=image_source(f"data:{mime};base64,{payload}", f"{what}.inlineData"))


def _response_text(response: object, parts: list[object]) -> tuple[str, bool]:
    """A ``functionResponse`` as the tool message's text, and whether it reports an error. Gemini CLI sends
    ``{"output": text}`` or ``{"error": text}``; anything else is kept as JSON."""
    texts = [str(_object(p, "functionResponse.parts").get("text") or "") for p in parts]
    if isinstance(response, dict):
        body = cast("Json", response)
        if set(body) == {"output"} and isinstance(body["output"], str):
            return "\n".join([body["output"], *texts]).strip("\n"), False
        if set(body) == {"error"} and isinstance(body["error"], str):
            return body["error"], True
    shown = json.dumps(response, ensure_ascii=False, separators=(",", ":")) if response is not None else ""
    return "\n".join([shown, *texts]).strip("\n"), False


@dataclass(slots=True)
class _Pairing:
    """Calls awaiting their response, so a ``functionResponse`` without an id finds its call by name."""

    open: list[ToolCall] = field(default_factory=list[ToolCall])
    answered: set[str] = field(default_factory=set[str])
    count: int = 0
    repeats: int = 0

    def call(self, raw: Json) -> ToolCall:
        name, call_id, args = raw.get("name"), raw.get("id"), raw.get("args")
        if not isinstance(name, str) or not name:
            raise InvalidGeminiRequest("a function call needs a name", param="contents.parts.functionCall.name")
        if args is not None and not isinstance(args, dict):
            raise InvalidGeminiRequest("function call args must be an object", param="contents.parts.functionCall.args")
        self.count += 1
        identifier = call_id if isinstance(call_id, str) and call_id else f"call_{self.count}"
        made = ToolCall(id=identifier, name=name, arguments=cast("dict[str, object]", args or {}))
        self.open.append(made)
        return made

    def response(self, raw: Json) -> str | None:
        """The call this response answers; ``None`` for a repeat of a response already given (Gemini CLI 0.63.0
        replays a resumed session's tool results twice), which is dropped rather than refused."""
        name, call_id = raw.get("name"), raw.get("id")
        match = None
        if isinstance(call_id, str) and call_id and not any(c.id == call_id for c in self.open) and call_id in self.answered:
            self.repeats += 1
            return None
        if isinstance(call_id, str) and call_id:
            # Gemini CLI renames a call ``<name>__<id>`` once it runs it; a resumed history can carry either form.
            bare = call_id.removeprefix(f"{name}__")
            match = next((c for c in self.open if c.id in (call_id, bare) or c.id.removeprefix(f"{c.name}__") == bare), None)
        if match is None:
            match = next((c for c in self.open if c.name == name), None)
        if match is None:
            raise InvalidGeminiRequest("a function response names no open call", param="contents.parts.functionResponse")
        self.open.remove(match)
        self.answered.update({match.id, call_id} if isinstance(call_id, str) and call_id else {match.id})
        return match.id


def _user(parts: list[object], pairing: _Pairing) -> list[Message]:
    tools: list[Message] = []
    content: list[Part] = []
    for item in parts:
        part = _object(item, "contents.parts")
        if "functionResponse" in part:
            raw = _object(part["functionResponse"], "contents.parts.functionResponse")
            text, error = _response_text(raw.get("response"), _list(raw.get("parts"), "functionResponse.parts"))
            answers = pairing.response(raw)
            if answers is not None:
                tools.append(ToolMessage(content=text, tool_call_id=answers, is_error=error))
        elif "inlineData" in part:
            content.append(_inline(part, "contents.parts"))
        elif "text" in part:
            content.append(TextPart(text=str(part.get("text") or "")))
        else:
            raise UnsupportedParameter(f"contents.parts.{_kind(part)}")
    if not content:
        return tools
    if any(isinstance(p, ImagePart) for p in content):
        return [*tools, UserMessage(content=content)]
    # Gemini CLI sends its session context and the prompt as separate parts, the first without a trailing newline:
    # joined bare, the prompt's first line would run on from the context's closing tag.
    return [*tools, UserMessage(content="\n".join(p.text for p in content if isinstance(p, TextPart)))]


def _model(parts: list[object], pairing: _Pairing) -> tuple[AssistantMessage, int]:
    text: list[str] = []
    calls: list[ToolCall] = []
    thoughts = 0
    for item in parts:
        part = _object(item, "contents.parts")
        if "functionCall" in part:
            calls.append(pairing.call(_object(part["functionCall"], "contents.parts.functionCall")))
        elif part.get("thought") is True:
            thoughts += 1
        elif "text" in part:
            text.append(str(part.get("text") or ""))
        elif set(part) <= {"thoughtSignature"}:
            continue
        else:
            raise UnsupportedParameter(f"contents.parts.{_kind(part)}")
    joined = "".join(text)
    message = AssistantMessage(
        content=[TextPart(text=joined)] if joined else [],
        tool_calls=calls or None,
    )
    return message, thoughts


def _kind(part: Json) -> str:
    return next((k for k in sorted(part) if k not in ("thought", "thoughtSignature")), "unknown")


def _schema(value: object) -> object:
    """Gemini's OpenAPI subset as JSON Schema: its upper-case ``type`` names are JSON Schema's in lower case."""
    if isinstance(value, dict):
        out: dict[str, object] = {}
        for key, item in cast("Json", value).items():
            if key == "type" and isinstance(item, str):
                out[key] = item.lower()
            elif key in ("properties", "$defs", "definitions") and isinstance(item, dict):
                out[key] = {name: _schema(sub) for name, sub in cast("Json", item).items()}
            else:
                out[key] = _schema(item)
        return out
    if isinstance(value, list):
        return [_schema(item) for item in cast("list[object]", value)]
    return value


def _tools(value: object) -> tuple[ToolSchema, ...]:
    schemas: list[ToolSchema] = []
    for item in _list(value, "tools"):
        tool = _object(item, "tools")
        other = sorted(set(tool) - {"functionDeclarations"})
        if other:
            raise UnsupportedParameter(f"tools.{other[0]}")
        for entry in _list(tool.get("functionDeclarations"), "tools.functionDeclarations"):
            declared = _object(entry, "tools.functionDeclarations")
            name = declared.get("name")
            if not isinstance(name, str) or not name:
                raise InvalidGeminiRequest("a function declaration needs a name", param="tools.functionDeclarations.name")
            raw = declared.get("parametersJsonSchema")
            if raw is None:
                raw = _schema(declared.get("parameters"))
            schemas.append(
                ToolSchema(
                    name=name,
                    description=str(declared.get("description") or ""),
                    input_schema=cast("dict[str, object]", raw) if isinstance(raw, dict) else {},
                )
            )
    return tuple(schemas)


def _tool_config(value: object) -> None:
    if value is None:
        return
    config = _object(value, "toolConfig")
    calling = config.get("functionCallingConfig")
    mode = _object(calling, "toolConfig.functionCallingConfig").get("mode") if calling is not None else None
    if mode not in (None, "AUTO", "MODE_UNSPECIFIED"):
        raise UnsupportedParameter("toolConfig.functionCallingConfig.mode")
    if set(config) - {"functionCallingConfig"}:
        raise UnsupportedParameter(f"toolConfig.{sorted(set(config) - {'functionCallingConfig'})[0]}")


def _generation(value: object) -> tuple[int | None, str | None, tuple[str, ...]]:
    if value is None:
        return None, None, ()
    config = _object(value, "generationConfig")
    for name in sorted(REFUSED_GENERATION & config.keys()):
        if config[name] is not None:
            raise UnsupportedParameter(f"generationConfig.{name}")
    mime = config.get("responseMimeType")
    if mime is not None and mime not in _TEXT_TYPES:
        raise UnsupportedParameter("generationConfig.responseMimeType")
    modalities = config.get("responseModalities")
    if modalities is not None and _list(modalities, "generationConfig.responseModalities") not in ([], ["TEXT"]):
        raise UnsupportedParameter("generationConfig.responseModalities")
    count = config.get("candidateCount")
    if count is not None and count != 1:
        raise UnsupportedParameter("generationConfig.candidateCount")
    limit = config.get("maxOutputTokens")
    if limit is not None and (isinstance(limit, bool) or not isinstance(limit, int) or limit <= 0):
        raise InvalidGeminiRequest("maxOutputTokens must be a positive integer", param="generationConfig.maxOutputTokens")
    effort: str | None = None
    thinking = config.get("thinkingConfig")
    if thinking is not None:
        level = _object(thinking, "generationConfig.thinkingConfig").get("thinkingLevel")
        effort = _EFFORTS.get(level) if isinstance(level, str) else None
    dropped = tuple(
        f"generationConfig.{name}" for name in sorted(config) if name not in _READ_GENERATION and name not in REFUSED_GENERATION
    )
    return limit, effort, dropped


def parse_gemini_request(body: object) -> GeminiRequest:
    outer = _object(body, "body")
    model, stream = outer.get("model"), outer.get("stream") is True
    if not isinstance(model, str) or not model:
        raise InvalidGeminiRequest("model is required", param="model")
    request = _object(outer.get("request"), "body")
    for name in sorted(REFUSED_PARAMETERS & request.keys()):
        if request[name] is not None:
            raise UnsupportedParameter(name)
    contents = _list(request.get("contents"), "contents")
    if not contents:
        raise InvalidGeminiRequest("contents must be a non-empty list", param="contents")
    pairing = _Pairing()
    messages: list[Message] = []
    thoughts = 0
    for item in contents:
        content = _object(item, "contents")
        role = content.get("role", "user")
        parts = _list(content.get("parts"), "contents.parts")
        if role in ("user", "function", None):
            messages.extend(_user(parts, pairing))
        elif role == "model":
            message, seen = _model(parts, pairing)
            thoughts += seen
            if message.content or message.tool_calls:
                messages.append(message)
        else:
            raise InvalidGeminiRequest(f"unknown content role {role!r}", param="contents.role")
    _tool_config(request.get("toolConfig"))
    limit, effort, generation_dropped = _generation(request.get("generationConfig"))
    dropped = tuple(sorted(name for name in request if name not in _READ and name not in REFUSED_PARAMETERS))
    if pairing.repeats:
        dropped = (*dropped, "contents.parts.functionResponse.repeated")
    return GeminiRequest(
        model=model,
        system=_system(request.get("systemInstruction")),
        messages=tuple(messages),
        tools=_tools(request.get("tools")),
        stream=stream,
        reasoning_effort=effort,
        max_output_tokens=limit,
        dropped=(*dropped, *generation_dropped),
        reasoning_items=thoughts,
    )


def usage_metadata(usage: TokenUsage) -> Json:
    out: Json = {
        "promptTokenCount": usage.input_tokens,
        "candidatesTokenCount": usage.output_tokens,
        "totalTokenCount": usage.input_tokens + usage.output_tokens,
    }
    if usage.cache_read_tokens:
        out["cachedContentTokenCount"] = usage.cache_read_tokens
    if usage.reasoning_tokens:
        out["thoughtsTokenCount"] = usage.reasoning_tokens
    return out


def _malformed(call: ToolCall) -> bool:
    return MALFORMED_TOOL_CALL_ERROR_KEY in call.arguments


def finish_reason(response: ModelResponse) -> str:
    if any(_malformed(c) for c in response.message.tool_calls or ()):
        return "MALFORMED_FUNCTION_CALL"
    return _FINISH.get(response.stop_reason, "STOP")


def _call_part(call: ToolCall) -> Json:
    return {"functionCall": {"id": call.id, "name": call.name, "args": {} if _malformed(call) else call.arguments}}


def _thinking(message: AssistantMessage) -> str:
    return "".join(p.thinking for p in message.content if isinstance(p, ThinkingPart))


def _candidate(parts: list[Json], finish: str | None = None) -> Json:
    candidate: Json = {"content": {"role": "model", "parts": parts}, "index": 0}
    if finish is not None:
        candidate["finishReason"] = finish
    return candidate


def content_body(response: ModelResponse, *, model_id: str, response_id: str) -> Json:
    message = response.message
    parts: list[Json] = []
    thinking = _thinking(message)
    if thinking:
        parts.append({"text": thinking, "thought": True})
    if message.text:
        parts.append({"text": message.text})
    parts.extend(_call_part(c) for c in message.tool_calls or ())
    return {
        "candidates": [_candidate(parts, finish_reason(response))],
        "usageMetadata": usage_metadata(response.usage),
        "modelVersion": model_id,
        "responseId": response_id,
    }


@dataclass(slots=True)
class ContentEncoder:
    """One answer's SSE chunks. Gemini sends a function call whole, never in fragments, so calls go out with the
    last chunk, from the final response; text and thinking stream as they arrive."""

    model_id: str
    response_id: str
    _streamed_text: bool = False
    _streamed_thinking: bool = False

    def _chunk(self, parts: list[Json], finish: str | None = None, usage: TokenUsage | None = None) -> Json:
        chunk: Json = {"candidates": [_candidate(parts, finish)], "modelVersion": self.model_id, "responseId": self.response_id}
        if usage is not None:
            chunk["usageMetadata"] = usage_metadata(usage)
        return chunk

    def start(self) -> list[Json]:
        return []

    def on_event(self, event: StreamEvent) -> list[Json]:
        match event:
            case TextDeltaEvent(delta=text) if text:
                self._streamed_text = True
                return [self._chunk([{"text": text}])]
            case ThinkingDeltaEvent(delta=text) if text:
                self._streamed_thinking = True
                return [self._chunk([{"text": text, "thought": True}])]
            case StreamCompleteEvent(response=response):
                return self.finish(response)
            case _:
                return []

    def finish(self, response: ModelResponse) -> list[Json]:
        parts: list[Json] = []
        thinking = _thinking(response.message)
        if thinking and not self._streamed_thinking:
            parts.append({"text": thinking, "thought": True})
        if response.message.text and not self._streamed_text:
            parts.append({"text": response.message.text})
        parts.extend(_call_part(c) for c in response.message.tool_calls or ())
        return [self._chunk(parts, finish_reason(response), response.usage)]

    def failed(self, code: str, message: str) -> list[Json]:
        body, _ = gemini_error(502, code, message, provider=True)
        return [body]


def gemini_error(
    status: int, code: str, message: str, *, param: str | None = None, provider: bool = False
) -> tuple[Json, dict[str, str]]:
    """Google's error envelope (``google.rpc.Status``); the gateway's code is its ``ErrorInfo`` reason and tags the
    message, which is all an ACP agent passes on."""
    tagged = f"{message} [gateway:{code}]" if _GATEWAY_CODE.match(code) else message
    detail: Json = {"@type": "type.googleapis.com/google.rpc.ErrorInfo", "reason": code, "domain": ERROR_DOMAIN}
    if param is not None:
        detail["metadata"] = {"param": param}
    status_name = _STATUS.get(status, "INTERNAL" if provider or status >= 500 else "FAILED_PRECONDITION")
    return {"error": {"code": status, "message": tagged, "status": status_name, "details": [detail]}}, {}


class GeminiWire:
    """Gemini ``generateContent`` (Gemini CLI): ``data:`` chunks with ``alt=sse``, no terminal event."""

    api = ModelApi.GEMINI_GENERATE_CONTENT
    id_prefix = "gemini"
    terminal = b""
    # An empty response, not a comment: the ``@google/genai`` in Gemini CLI 0.63 takes an event only from ``data: `` at
    # the start of its buffer, so a comment would stall every chunk after it. ``{}`` holds no candidate and no usage,
    # so Gemini CLI's turn adds nothing for it (WP127-FU-1).
    keepalive = b"data: {}\r\n\r\n"
    provider_state = None

    def parse(self, body: object, headers: Mapping[str, str] | None = None) -> GeminiRequest:
        return parse_gemini_request(body)

    def body(self, request: GeminiRequest, response: ModelResponse, call: WireCall) -> Json:
        return content_body(response, model_id=call.model_id, response_id=call.response_id)

    def encoder(self, request: GeminiRequest, call: WireCall) -> ContentEncoder:
        return ContentEncoder(call.model_id, call.response_id)

    def frame(self, event: Json) -> bytes:
        return f"data: {json.dumps(event, separators=(',', ':'))}\r\n\r\n".encode()

    def error(
        self, status: int, code: str, message: str, *, param: str | None = None, provider: bool = False
    ) -> tuple[Json, dict[str, str]]:
        return gemini_error(status, code, message, param=param, provider=provider)


GEMINI_WIRE = GeminiWire()


__all__ = [
    "GEMINI_WIRE",
    "METHODS",
    "REFUSED_GENERATION",
    "REFUSED_PARAMETERS",
    "ContentEncoder",
    "GeminiRequest",
    "GeminiWire",
    "content_body",
    "envelope",
    "finish_reason",
    "gemini_error",
    "model_method",
    "parse_gemini_request",
    "usage_metadata",
]
