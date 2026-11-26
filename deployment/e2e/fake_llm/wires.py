"""Every provider wire the e2e matrix names (04 §2.1 `wire`), served by the one scripted model (QA-6).

A wire reads a provider request into what ``ScriptedBrain`` decides from (the messages and the offered tool names) and
writes its reply back in that provider's format, streamed or whole. Reading is lenient where the provider is (every
parameter a real client sends is accepted) and strict where it refuses: a request without the provider's credential,
or without a message, is refused as that provider refuses it. Writing reuses the model gateway's own encoders for
Anthropic Messages and OpenAI Responses (``harness_core.gateway``), so the frames are the ones a real client parses.

=================  ===================================================  ==========================================
wire               path                                                 credential
=================  ===================================================  ==========================================
openai_chat        ``POST /v1/chat/completions``                        ``Authorization: Bearer <key>``
azure_openai       ``POST /openai/deployments/{d}/chat/completions``    ``api-key: <key>``
openai_responses   ``POST /v1/responses``                               ``Authorization: Bearer <key>``
anthropic_messages ``POST /v1/messages``                                ``x-api-key: <key>``
gemini             ``POST /v1beta/models/{m}:(stream)generateContent``  ``x-goog-api-key: <key>`` (or ``?key=``)
bedrock            ``POST /model/{m}/converse(-stream)``                SigV4 with the lane's access key
vertex_ai          ``POST /v1beta1/projects/{p}/locations/{l}/          ``Authorization: Bearer <token>`` from
                   publishers/google/models/{m}:(stream)generateContent``  ``POST /token`` (the service account's
                                                                        JWT-bearer assertion, RS256)
=================  ===================================================  ==========================================

Vertex AI (OPS01-FU-1) is Gemini's body on Google Cloud's path, with the product's sign-in: ``google-auth`` posts a
JWT-bearer assertion signed with the uploaded service account's key to the key file's ``token_uri`` (the lane points
it here), and the access token this server issues for the lane's account is the call's bearer.
"""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, Protocol

from aiohttp import web
from harness_core.gateway.messages_wire import MessageEncoder, MessagesRequest
from harness_core.gateway.responses_wire import ResponseEncoder, ResponsesRequest
from harness_core.gateway.wire import WireCall
from harness_core.kernel.core.messages import AssistantMessage, TextPart
from harness_core.kernel.core.messages import ToolCall as KernelToolCall
from harness_core.kernel.core.responses import ModelResponse, StopReason, TokenUsage
from harness_core.kernel.core.streaming import StreamCompleteEvent, TextDeltaEvent
from harness_core.testing.cloud_providers import converse_body, eventstream_frame
from harness_core.testing.engines.script import ChatMessage, ModelReply, ToolCall

if TYPE_CHECKING:
    from collections.abc import Iterable

Json = dict[str, Any]


@dataclass(frozen=True)
class WireRequest:
    """What the scripted model decides from, whatever the wire."""

    wire: str
    model: str
    messages: list[ChatMessage]
    tools: list[str]
    stream: bool
    schemas: dict[str, Any] = field(default_factory=dict)
    """Each offered tool's parameter schema (a structured-output step asks for a call that conforms to one)."""
    response_schema: dict[str, Any] | None = None
    """The JSON schema the answer's text must follow, when the request asks for structured output that way."""


class Refused(Exception):
    """A request the provider refuses: answered with ``response`` and never scripted."""

    def __init__(self, response: web.Response) -> None:
        super().__init__(response.status)
        self.response = response


@dataclass(frozen=True)
class Credentials:
    api_key: str
    aws_access_key_id: str
    aws_secret_access_key: str
    aws_region: str
    # Vertex AI: the lane's service account, as Google holds it (its public key and email); unset, no sign-in passes.
    vertex_public_key: str | None = None
    vertex_client_email: str | None = None

    @property
    def vertex_access_token(self) -> str:
        """The bearer this server issues to the lane's service account: derived, so nothing is kept between calls."""
        return "ya29.e2e-" + hashlib.sha256(f"vertex:{self.api_key}".encode()).hexdigest()[:32]


class Wire(Protocol):
    name: str

    def routes(self) -> Iterable[str]: ...
    async def read(self, request: web.Request, credentials: Credentials) -> WireRequest: ...
    async def write(self, request: web.Request, asked: WireRequest, reply: ModelReply) -> web.StreamResponse: ...


def _text(content: Any) -> str:  # noqa: ANN401 - each provider's content: a string or typed parts
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(str(p.get("text", "")) for p in content if isinstance(p, dict) and isinstance(p.get("text"), str))
    return ""


def _args(raw: Any) -> str:  # noqa: ANN401 - a provider's tool input: an object or its JSON
    return raw if isinstance(raw, str) else json.dumps(raw or {})


def _kernel_response(reply: ModelReply, model: str) -> ModelResponse:
    calls = [KernelToolCall(id=c.id, name=c.name, arguments=json.loads(c.arguments or "{}")) for c in reply.tool_calls]
    return ModelResponse(
        message=AssistantMessage(content=[TextPart(text=reply.content)] if reply.content else [], tool_calls=calls or None),
        usage=TokenUsage(input_tokens=reply.usage.input_tokens, output_tokens=reply.usage.output_tokens),
        stop_reason=StopReason.TOOL_USE if calls else StopReason.END_TURN,
        model=model,
    )


def _wire_call(model: str, prefix: str) -> WireCall:
    return WireCall(model_id=model, response_id=f"{prefix}_e2e_{time.monotonic_ns()}", created=int(time.time()))


async def _sse(request: web.Request, frames: Iterable[bytes]) -> web.StreamResponse:
    response = web.StreamResponse(headers={"Content-Type": "text/event-stream", "Cache-Control": "no-cache"})
    await response.prepare(request)
    for frame in frames:
        await response.write(frame)
    await response.write_eof()
    return response


def _error(status: int, body: Json) -> Refused:
    return Refused(web.json_response(body, status=status))


def _openai_refusal(status: int, message: str, code: str) -> Refused:
    return _error(status, {"error": {"message": message, "type": "invalid_request_error", "code": code}})


async def _json(request: web.Request, refuse: Refused) -> Json:
    try:
        body = await request.json()
    except ValueError:
        raise refuse from None
    if not isinstance(body, dict):
        raise refuse
    return body


class OpenAIResponses:
    name = "openai_responses"

    def routes(self) -> Iterable[str]:
        return ("/v1/responses",)

    async def read(self, request: web.Request, credentials: Credentials) -> WireRequest:
        if request.headers.get("Authorization") != f"Bearer {credentials.api_key}":
            raise _openai_refusal(401, "Incorrect API key provided", "invalid_api_key")
        body = await _json(request, _openai_refusal(400, "We could not parse the JSON body", "invalid_json"))
        raw = body.get("input")
        items: list[Any] = [{"role": "user", "content": raw}] if isinstance(raw, str) else list(raw or [])
        messages: list[ChatMessage] = []
        if isinstance(body.get("instructions"), str):
            messages.append(ChatMessage(role="system", content=body["instructions"]))
        for item in items:
            if not isinstance(item, dict):
                continue
            kind = item.get("type", "message")
            if kind == "function_call":
                call = ToolCall(str(item.get("call_id", "")), str(item.get("name", "")), _args(item.get("arguments")))
                messages.append(ChatMessage(role="assistant", tool_calls=(call,)))
            elif kind == "function_call_output":
                messages.append(ChatMessage(role="tool", content=_text(item.get("output")), tool_call_id=str(item.get("call_id", ""))))
            elif kind == "message":
                role = {"developer": "system"}.get(str(item.get("role")), str(item.get("role")))
                messages.append(ChatMessage(role=role, content=_text(item.get("content"))))  # type: ignore[arg-type]
        if not any(m.role == "user" for m in messages):
            raise _openai_refusal(400, "input must hold a message", "invalid_input")
        offered = [t for t in body.get("tools") or [] if isinstance(t, dict) and t.get("name")]
        return WireRequest(self.name, str(body.get("model", "")), messages, [str(t["name"]) for t in offered],
                           body.get("stream") is True, {str(t["name"]): t.get("parameters") or {} for t in offered})

    async def write(self, request: web.Request, asked: WireRequest, reply: ModelReply) -> web.StreamResponse:
        gateway = ResponsesRequest(model=asked.model, system=None, messages=(), tools=(), stream=asked.stream,
                                   reasoning_effort=None, max_output_tokens=None)
        call = _wire_call(asked.model, "resp")
        response = _kernel_response(reply, asked.model)
        encoder = ResponseEncoder(gateway, call)
        if not asked.stream:
            return web.json_response(encoder.finish(response)[-1]["response"])
        events = [*encoder.start(), *(encoder.on_event(TextDeltaEvent(delta=reply.content)) if reply.content else []),
                  *encoder.on_event(StreamCompleteEvent(response=response))]
        return await _sse(request, (f"event: {e['type']}\ndata: {json.dumps(e)}\n\n".encode() for e in events))


class AnthropicMessages:
    name = "anthropic_messages"

    def routes(self) -> Iterable[str]:
        return ("/v1/messages",)

    @staticmethod
    def _refusal(status: int, kind: str, message: str) -> Refused:
        return _error(status, {"type": "error", "error": {"type": kind, "message": message}})

    async def read(self, request: web.Request, credentials: Credentials) -> WireRequest:
        if request.headers.get("x-api-key") != credentials.api_key:
            raise self._refusal(401, "authentication_error", "invalid x-api-key")
        body = await _json(request, self._refusal(400, "invalid_request_error", "the body is not JSON"))
        if not isinstance(body.get("max_tokens"), int):
            raise self._refusal(400, "invalid_request_error", "max_tokens: Field required")
        messages: list[ChatMessage] = []
        system = body.get("system")
        if system:
            messages.append(ChatMessage(role="system", content=_text(system)))
        for raw in body.get("messages") or []:
            role = str(raw.get("role"))
            content = raw.get("content")
            blocks = content if isinstance(content, list) else [{"type": "text", "text": content or ""}]
            calls = tuple(ToolCall(str(b.get("id")), str(b.get("name")), _args(b.get("input")))
                          for b in blocks if b.get("type") == "tool_use")
            for b in blocks:
                if b.get("type") == "tool_result":
                    messages.append(ChatMessage(role="tool", content=_text(b.get("content")), tool_call_id=str(b.get("tool_use_id"))))
            text = "".join(str(b.get("text", "")) for b in blocks if b.get("type") == "text")
            if text or calls:
                messages.append(ChatMessage(role=role, content=text, tool_calls=calls))  # type: ignore[arg-type]
        if not messages or messages[-1].role == "assistant" and not messages[-1].tool_calls:
            raise self._refusal(400, "invalid_request_error", "messages: the last message must be the user's")
        offered = [t for t in body.get("tools") or [] if isinstance(t, dict) and t.get("name")]
        return WireRequest(self.name, str(body.get("model", "")), messages, [str(t["name"]) for t in offered],
                           body.get("stream") is True, {str(t["name"]): t.get("input_schema") or {} for t in offered})

    async def write(self, request: web.Request, asked: WireRequest, reply: ModelReply) -> web.StreamResponse:
        from harness_core.gateway.messages_wire import message_body

        gateway = MessagesRequest(model=asked.model, system=None, messages=(), tools=(), stream=asked.stream,
                                  reasoning_effort=None, max_tokens=None)
        call = _wire_call(asked.model, "msg")
        response = _kernel_response(reply, asked.model)
        if not asked.stream:
            return web.json_response(message_body(gateway, response, call))
        encoder = MessageEncoder(gateway, call)
        events = [*encoder.start(), *(encoder.on_event(TextDeltaEvent(delta=reply.content)) if reply.content else []),
                  *encoder.on_event(StreamCompleteEvent(response=response))]
        return await _sse(request, (f"event: {e['type']}\ndata: {json.dumps(e)}\n\n".encode() for e in events))


class Gemini:
    name = "gemini"

    def routes(self) -> Iterable[str]:
        return ("/v1beta/models/{model}:generateContent", "/v1beta/models/{model}:streamGenerateContent")

    @staticmethod
    def _refusal(status: int, state: str, message: str) -> Refused:
        return _error(status, {"error": {"code": status, "message": message, "status": state}})

    async def read(self, request: web.Request, credentials: Credentials) -> WireRequest:
        key = request.headers.get("x-goog-api-key") or request.query.get("key")
        if key != credentials.api_key:
            raise self._refusal(400, "INVALID_ARGUMENT", "API key not valid. Please pass a valid API key.")
        return await self._request(request)

    async def _request(self, request: web.Request) -> WireRequest:
        """A ``generateContent`` body, as Gemini's API and Vertex AI's both take it."""
        body = await _json(request, self._refusal(400, "INVALID_ARGUMENT", "Invalid JSON payload received."))
        messages: list[ChatMessage] = []
        system = body.get("systemInstruction") or body.get("system_instruction")
        if isinstance(system, dict):
            messages.append(ChatMessage(role="system", content=_text(system.get("parts"))))
        calls_seen = 0
        for raw in body.get("contents") or []:
            parts = raw.get("parts") or []
            calls = []
            for p in parts:
                call = p.get("functionCall") or p.get("function_call")
                if isinstance(call, dict):
                    calls_seen += 1
                    calls.append(ToolCall(str(call.get("id") or f"gemini_{calls_seen}"), str(call.get("name")), _args(call.get("args"))))
                result = p.get("functionResponse") or p.get("function_response")
                if isinstance(result, dict):
                    messages.append(ChatMessage(role="tool", content=json.dumps(result.get("response")), tool_call_id=str(result.get("id") or "")))
            text = _text(parts)
            if text or calls:
                role = "assistant" if raw.get("role") == "model" else "user"
                messages.append(ChatMessage(role=role, content=text, tool_calls=tuple(calls)))
        if not any(m.role == "user" for m in messages):
            raise self._refusal(400, "INVALID_ARGUMENT", "* GenerateContentRequest.contents: contents is not specified")
        declared = [f for t in body.get("tools") or [] if isinstance(t, dict)
                    for f in (t.get("functionDeclarations") or t.get("function_declarations") or []) if f.get("name")]
        model = request.match_info["model"]
        config = body.get("generationConfig") or body.get("generation_config") or {}
        schema = config.get("responseJsonSchema") or config.get("responseSchema") or config.get("response_schema")
        return WireRequest(self.name, model, messages, [str(f["name"]) for f in declared],
                           request.path.endswith(":streamGenerateContent"),
                           {str(f["name"]): f.get("parameters") or f.get("parametersJsonSchema") or {} for f in declared},
                           schema if isinstance(schema, dict) else None)

    async def write(self, request: web.Request, asked: WireRequest, reply: ModelReply) -> web.StreamResponse:
        parts: list[Json] = [{"text": reply.content}] if reply.content else []
        parts += [{"functionCall": {"name": c.name, "args": json.loads(c.arguments or "{}")}} for c in reply.tool_calls]
        body = {
            "candidates": [{"content": {"role": "model", "parts": parts}, "finishReason": "STOP", "index": 0}],
            "usageMetadata": {"promptTokenCount": reply.usage.input_tokens, "candidatesTokenCount": reply.usage.output_tokens,
                              "totalTokenCount": reply.usage.input_tokens + reply.usage.output_tokens},
            "modelVersion": asked.model,
        }
        if not asked.stream:
            return web.json_response(body)
        return await _sse(request, [f"data: {json.dumps(body)}\r\n\r\n".encode()])


class BedrockConverse:
    name = "bedrock"

    def routes(self) -> Iterable[str]:
        return ("/model/{model}/converse", "/model/{model}/converse-stream")

    @staticmethod
    def _refusal(status: int, kind: str, message: str) -> Refused:
        return Refused(web.json_response({"message": message}, status=status, headers={"x-amzn-ErrorType": kind}))

    def _signed(self, request: web.Request, raw: bytes, credentials: Credentials) -> bool:
        from harness_core.models.sigv4 import AwsCredentials, sign

        headers = {k.lower(): v for k, v in request.headers.items()}
        auth = headers.get("authorization", "")
        if f"Credential={credentials.aws_access_key_id}/" not in auth:
            return False
        signed = auth.partition("SignedHeaders=")[2].partition(",")[0].split(";")
        try:
            now = datetime.strptime(headers.get("x-amz-date", ""), "%Y%m%dT%H%M%SZ").replace(tzinfo=UTC)
        except ValueError:
            return False
        expected = sign(
            request.method, f"{request.scheme}://{request.host}{request.raw_path}",
            {name: headers.get(name, "") for name in signed if name != "x-amz-date"}, raw,
            credentials=AwsCredentials(credentials.aws_access_key_id, credentials.aws_secret_access_key),
            region=credentials.aws_region, service="bedrock", now=now,
        )
        return expected["authorization"] == auth

    async def read(self, request: web.Request, credentials: Credentials) -> WireRequest:
        raw = await request.read()
        if not self._signed(request, raw, credentials):
            raise self._refusal(403, "InvalidSignatureException",
                                "The request signature we calculated does not match the signature you provided.")
        try:
            body = json.loads(raw)
        except ValueError:
            raise self._refusal(400, "ValidationException", "Malformed input request") from None
        messages: list[ChatMessage] = [ChatMessage(role="system", content=_text(body.get("system")))] if body.get("system") else []
        for raw_message in body.get("messages") or []:
            blocks = raw_message.get("content") or []
            calls = tuple(ToolCall(str(b["toolUse"].get("toolUseId")), str(b["toolUse"].get("name")), _args(b["toolUse"].get("input")))
                          for b in blocks if isinstance(b.get("toolUse"), dict))
            for b in blocks:
                if isinstance(b.get("toolResult"), dict):
                    result = b["toolResult"]
                    messages.append(ChatMessage(role="tool", content=_text(result.get("content")), tool_call_id=str(result.get("toolUseId"))))
            text = _text(blocks)
            if text or calls:
                messages.append(ChatMessage(role=str(raw_message.get("role")), content=text, tool_calls=calls))  # type: ignore[arg-type]
        if not any(m.role == "user" for m in messages):
            raise self._refusal(400, "ValidationException", "A conversation must start with a user message.")
        specs = [t["toolSpec"] for t in (body.get("toolConfig") or {}).get("tools") or [] if isinstance(t.get("toolSpec"), dict)]
        return WireRequest(self.name, request.match_info["model"], messages, [str(t.get("name")) for t in specs],
                           request.path.endswith("-stream"),
                           {str(t.get("name")): (t.get("inputSchema") or {}).get("json") or {} for t in specs})

    async def write(self, request: web.Request, asked: WireRequest, reply: ModelReply) -> web.StreamResponse:
        stop = "tool_use" if reply.tool_calls else "end_turn"
        content: list[Json] = [{"text": reply.content}] if reply.content else []
        content += [{"toolUse": {"toolUseId": c.id, "name": c.name, "input": json.loads(c.arguments or "{}")}} for c in reply.tool_calls]
        if not asked.stream:
            return web.json_response(converse_body(stop=stop, content=content))
        events: list[tuple[str, Json]] = [("messageStart", {"role": "assistant"})]
        for index, block in enumerate(content):
            if "text" in block:
                events.append(("contentBlockDelta", {"contentBlockIndex": index, "delta": {"text": block["text"]}}))
            else:
                use = block["toolUse"]
                events.append(("contentBlockStart", {"contentBlockIndex": index,
                                                     "start": {"toolUse": {"toolUseId": use["toolUseId"], "name": use["name"]}}}))
                events.append(("contentBlockDelta", {"contentBlockIndex": index, "delta": {"toolUse": {"input": json.dumps(use["input"])}}}))
            events.append(("contentBlockStop", {"contentBlockIndex": index}))
        events.append(("messageStop", {"stopReason": stop}))
        events.append(("metadata", {"usage": {"inputTokens": reply.usage.input_tokens, "outputTokens": reply.usage.output_tokens,
                                              "totalTokens": reply.usage.input_tokens + reply.usage.output_tokens},
                                    "metrics": {"latencyMs": 1}}))
        body = b"".join(eventstream_frame(json.dumps(payload).encode(), kind) for kind, payload in events)
        return web.Response(body=body, content_type="application/vnd.amazon.eventstream")


class VertexGemini(Gemini):
    """Gemini on Vertex AI: the same body on Google Cloud's path, behind the service account's access token."""

    name = "vertex_ai"

    def routes(self) -> Iterable[str]:
        base = "/{version}/projects/{project}/locations/{location}/publishers/google/models/{model}"
        return (f"{base}:generateContent", f"{base}:streamGenerateContent")

    async def read(self, request: web.Request, credentials: Credentials) -> WireRequest:
        if request.headers.get("Authorization") != f"Bearer {credentials.vertex_access_token}":
            raise self._refusal(401, "UNAUTHENTICATED", "Request had invalid authentication credentials.")
        return await self._request(request)


GOOGLE_TOKEN_AUDIENCE = "https://oauth2.googleapis.com/token"


async def vertex_token(request: web.Request, credentials: Credentials) -> web.Response:
    """Google's token endpoint for the lane's service account: a JWT-bearer assertion signed with its key, addressed
    to Google's token endpoint (``google-auth`` does so wherever it posts it), answered with the access token."""
    import jwt

    form = await request.post()
    if form.get("grant_type") != "urn:ietf:params:oauth:grant-type:jwt-bearer":
        raise _error(400, {"error": "unsupported_grant_type"})
    if credentials.vertex_public_key is None:
        raise _error(400, {"error": "invalid_grant", "error_description": "Invalid JWT Signature."})
    try:
        claims = jwt.decode(str(form.get("assertion")), credentials.vertex_public_key, algorithms=["RS256"],
                            audience=GOOGLE_TOKEN_AUDIENCE)
    except jwt.PyJWTError:
        raise _error(400, {"error": "invalid_grant", "error_description": "Invalid JWT Signature."}) from None
    if claims.get("iss") != credentials.vertex_client_email:
        raise _error(400, {"error": "invalid_grant", "error_description": "Invalid JWT Signature."})
    return web.json_response({"access_token": credentials.vertex_access_token, "expires_in": 3600, "token_type": "Bearer"})


WIRES: tuple[Wire, ...] = (OpenAIResponses(), AnthropicMessages(), Gemini(), BedrockConverse(), VertexGemini())
