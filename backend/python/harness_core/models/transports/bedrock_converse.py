"""Amazon Bedrock's Converse API: one wire for every model Bedrock hosts (P4.3).

``/model/{id}/converse`` answers whole, ``/model/{id}/converse-stream`` streams AWS
event-stream frames; both signed with SigV4 by ``harness_core.models.sigv4`` with the
account's static keys. Gateway-only (A1 VR-9): no product path builds this transport
today; Anthropic models on a Bedrock account keep ``anthropic_on_bedrock``.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Callable
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, cast, override
from urllib.parse import quote

import httpx

from harness_core.kernel.core.exceptions import TransportError
from harness_core.kernel.core.messages import (
    AssistantMessage,
    ImagePart,
    Message,
    MessageRole,
    TextPart,
    ToolCall,
    as_sent,
)
from harness_core.kernel.core.responses import (
    ModelResponse,
    StopReason,
    StructuredResponse,
    TokenUsage,
)
from harness_core.kernel.core.streaming import (
    StreamCompleteEvent,
    StreamEvent,
    TextDeltaEvent,
    ThinkingDeltaEvent,
    ToolCallDeltaEvent,
)
from harness_core.models.eventstream import EventStreamDecoder, EventStreamError
from harness_core.models.output_bound import thinking_within, within
from harness_core.models.provider_state import (
    BEDROCK_CONVERSE,
    state_blocks,
    with_state,
)
from harness_core.models.sigv4 import AwsCredentials, sign
from harness_core.models.transports.base import LLMTransport
from harness_core.models.transports.failures import is_retryable, transport_error
from harness_core.models.transports.openai_responses import normalise_tool_call

if TYPE_CHECKING:
    from harness_core.kernel.core.tool_schema import ToolChoice, ToolSchema

SERVICE = "bedrock"
_STOP: dict[str, StopReason] = {
    "end_turn": StopReason.END_TURN,
    "tool_use": StopReason.TOOL_USE,
    "max_tokens": StopReason.MAX_TOKENS,
    "stop_sequence": StopReason.STOP_SEQUENCE,
}
# A refusal by the model's or the account's filter: retrying repeats it, another route may not.
_FILTERED = frozenset({"content_filtered", "guardrail_intervened"})
_EXCEPTION_STATUS: dict[str, int] = {
    "throttlingException": 429,
    "validationException": 400,
    "accessDeniedException": 403,
    "modelTimeoutException": 408,
    "serviceUnavailableException": 503,
    "internalServerException": 500,
    "modelStreamErrorException": 500,
}
_IMAGE_FORMATS = {"image/png": "png", "image/jpeg": "jpeg", "image/jpg": "jpeg", "image/gif": "gif", "image/webp": "webp"}
_STRUCTURED_TOOL = "structured_output"


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _obj(value: object) -> dict[str, Any]:
    """A JSON object of the answer, or an empty one when the field is absent or not an object."""
    return cast("dict[str, Any]", value) if isinstance(value, dict) else {}


def _answer_blocks(answer: dict[str, Any]) -> list[dict[str, Any]]:
    content = _obj(_obj(answer.get("output")).get("message")).get("content")
    return [_obj(b) for b in cast("list[object]", content)] if isinstance(content, list) else []


class BedrockConverseTransport(LLMTransport):
    DEFAULT_MAX_TOKENS = 4096

    def __init__(
        self,
        *,
        region: str,
        credentials: AwsCredentials,
        model: str,
        max_tokens: int = DEFAULT_MAX_TOKENS,
        temperature: float | None = None,
        base_url: str | None = None,
        http: httpx.AsyncClient | None = None,
        timeout: float = 600.0,
        clock: Callable[[], datetime] = _utc_now,
    ) -> None:
        self._region = region
        self._credentials = credentials
        self._model = model
        self._max_tokens = max_tokens
        self._temperature = temperature
        self._base_url = (base_url or f"https://bedrock-runtime.{region}.amazonaws.com").rstrip("/")
        self._http = http or httpx.AsyncClient(timeout=timeout)
        self._clock = clock
        self._keep_state = False

    @property
    @override
    def provider(self) -> str:
        return "bedrock"

    @property
    @override
    def model_name(self) -> str:
        return self._model

    @override
    def keep_provider_state(self) -> bool:
        self._keep_state = True
        return True

    # ---- request ----

    def _url(self, model: str, action: str) -> str:
        return f"{self._base_url}/model/{quote(model, safe='')}/{action}"

    def _signed(self, url: str, body: bytes, accept: str) -> dict[str, str]:
        headers = {"content-type": "application/json", "accept": accept}
        return sign(
            "POST", url, headers, body, credentials=self._credentials, region=self._region, service=SERVICE, now=self._clock()
        )

    def _content(self, msg: Message) -> list[dict[str, Any]]:
        if msg.role == MessageRole.TOOL:
            content = msg.content
            blocks = self._parts(content) if isinstance(content, list) else [{"text": content}] if content else []
            if msg.step_footer:
                blocks.append({"text": msg.step_footer})
            result: dict[str, Any] = {"toolUseId": msg.tool_call_id, "content": blocks or [{"text": "(empty)"}]}
            if msg.is_error:
                result["status"] = "error"
            return [{"toolResult": result}]
        if msg.role == MessageRole.ASSISTANT:
            blocks: list[dict[str, Any]] = state_blocks(msg, BEDROCK_CONVERSE) if self._keep_state else []
            if msg.text:
                blocks.append({"text": msg.text})
            blocks.extend(
                {"toolUse": {"toolUseId": c.id, "name": c.name, "input": c.arguments}} for c in msg.tool_calls or []
            )
            # Converse refuses an empty or blank content list; a turn with nothing to say keeps its place.
            return blocks or [{"text": "."}]
        content = msg.content
        if isinstance(content, list):
            return self._parts(content) or [{"text": "."}]
        return [{"text": content or "."}]

    @staticmethod
    def _parts(parts: list[Any]) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for part in parts:
            if isinstance(part, ImagePart):
                source = part.source
                if source.type == "base64":
                    fmt = _IMAGE_FORMATS.get((source.media_type or "image/png").lower(), "png")
                    out.append({"image": {"format": fmt, "source": {"bytes": source.data}}})
                else:
                    # Converse takes image bytes (or an S3 location), never a web URL.
                    out.append({"text": f"[image: {source.data}]"})
            elif isinstance(part, TextPart) and part.text:
                out.append({"text": part.text})
        return out

    def _messages(self, messages: list[Message]) -> list[dict[str, Any]]:
        """Converse alternates user and assistant turns: consecutive tool results and user
        text join one user turn."""
        out: list[dict[str, Any]] = []
        for msg in messages:
            # Text the loop added goes out as a context note, as on every other wire (`as_sent`).
            msg = as_sent(msg)
            role = "assistant" if msg.role == MessageRole.ASSISTANT else "user"
            content = self._content(msg)
            if out and out[-1]["role"] == role:
                out[-1]["content"].extend(content)
            else:
                out.append({"role": role, "content": content})
        return out

    def _body(
        self,
        messages: list[Message],
        tools: list[ToolSchema] | None,
        system: str | None,
        system_blocks: list[str] | None,
        thinking_budget: int | None,
        limit: int | None = None,
    ) -> dict[str, Any]:
        texts = [b for b in system_blocks or [] if b] or ([system] if system else [])
        cap = within(self._max_tokens, limit) or self._max_tokens
        inference: dict[str, Any] = {"maxTokens": cap}
        body: dict[str, Any] = {"messages": self._messages(messages)}
        if texts:
            body["system"] = [{"text": t} for t in texts]
        if thinking_budget:
            # Claude on Bedrock takes Anthropic's thinking block through the model's own fields.
            budget = thinking_within(limit, thinking_budget) or thinking_budget
            body["additionalModelRequestFields"] = {"thinking": {"type": "enabled", "budget_tokens": budget}}
            inference["maxTokens"] = max(cap, budget + 1024)
        elif self._temperature is not None:
            inference["temperature"] = self._temperature
        body["inferenceConfig"] = inference
        if tools:
            body["toolConfig"] = {"tools": [self._tool_spec(t.name, t.description, t.input_schema) for t in tools]}
        return body

    @staticmethod
    def _tool_spec(name: str, description: str, schema: dict[str, Any] | None) -> dict[str, Any]:
        spec: dict[str, Any] = {"name": name, "inputSchema": {"json": schema or {"type": "object", "properties": {}}}}
        if description:
            spec["description"] = description
        return {"toolSpec": spec}

    # ---- response ----

    @staticmethod
    def _error(status: int, raw: bytes, error_type: str, context: str) -> TransportError:
        try:
            detail = json.loads(raw).get("message", "")
        except (ValueError, AttributeError):
            detail = ""
        return TransportError(
            f"Bedrock API error ({context}): {status} {error_type or 'error'}: {detail}".rstrip(": "),
            status_code=status,
            retryable=is_retryable(status, network=False),
        )

    @staticmethod
    def _usage(raw: dict[str, Any] | None) -> TokenUsage:
        raw = raw or {}

        def count(name: str) -> int:
            value = raw.get(name, 0)
            return value if isinstance(value, int) else 0

        cache_read, cache_write = count("cacheReadInputTokens"), count("cacheWriteInputTokens")
        return TokenUsage(
            # Converse counts cached tokens apart from inputTokens; TokenUsage.input_tokens includes them.
            input_tokens=count("inputTokens") + cache_read + cache_write,
            output_tokens=count("outputTokens"),
            cache_read_tokens=cache_read,
            cache_write_tokens=cache_write,
        )

    def _stop(self, reason: str | None, context: str) -> StopReason:
        if reason in _FILTERED:
            raise TransportError(
                f"Bedrock API error ({context}): content_filter ({reason})", status_code=400, retryable=False
            )
        return _STOP.get(reason or "", StopReason.END_TURN)

    def _message(self, blocks: list[dict[str, Any]], stop: StopReason) -> AssistantMessage:
        text: list[str] = []
        calls: list[ToolCall] = []
        state: list[dict[str, Any]] = []
        for block in blocks:
            if "text" in block:
                text.append(block["text"])
            elif "toolUse" in block:
                use = block["toolUse"]
                arguments = use.get("input")
                raw = arguments if isinstance(arguments, str) else json.dumps(arguments or {})
                calls.append(normalise_tool_call(use.get("toolUseId", ""), use.get("name", ""), raw))
            elif "reasoningContent" in block and self._keep_state:
                state.append({"reasoningContent": block["reasoningContent"]})
        message = AssistantMessage(
            content="".join(text) or None,  # pyright: ignore[reportArgumentType]
            tool_calls=calls or None,
            truncated=stop == StopReason.MAX_TOKENS,
        )
        return with_state(message, BEDROCK_CONVERSE, state)

    # ---- calls ----

    async def _post(self, url: str, body: dict[str, Any], context: str) -> dict[str, Any]:
        data = json.dumps(body).encode()
        try:
            response = await self._http.post(url, content=data, headers=self._signed(url, data, "application/json"))
        except (httpx.HTTPError, OSError) as exc:
            raise _network_error(exc, context) from exc
        if response.status_code != 200:
            raise self._error(response.status_code, response.content, response.headers.get("x-amzn-errortype", ""), context)
        return response.json()

    @property
    @override
    def accepts_output_limit(self) -> bool:
        return True

    @override
    async def complete(
        self,
        messages: list[Message],
        tools: list[ToolSchema] | None = None,
        system: str | None = None,
        model: str | None = None,
        thinking_budget: int | None = None,
        effort: str | None = None,
        system_blocks: list[str] | None = None,
        max_output_tokens: int | None = None,
        *,
        tool_choice: ToolChoice | None = None,  # Converse's toolChoice has no "none"; the agent drops any call
    ) -> ModelResponse:
        resolved = model or self._model
        body = self._body(messages, tools, system, system_blocks, thinking_budget, max_output_tokens)
        answer = await self._post(self._url(resolved, "converse"), body, "complete")
        stop = self._stop(answer.get("stopReason"), "complete")
        blocks = _answer_blocks(answer)
        return ModelResponse(
            message=self._message(blocks, stop), usage=self._usage(answer.get("usage")), stop_reason=stop, model=resolved
        )

    @override
    async def complete_structured(
        self,
        messages: list[Message],
        output_schema: dict[str, Any],
        system: str | None = None,
        model: str | None = None,
        max_output_tokens: int | None = None,
    ) -> StructuredResponse:
        resolved = model or self._model
        body = self._body(messages, None, system, None, None, max_output_tokens)
        body["toolConfig"] = {
            "tools": [self._tool_spec(_STRUCTURED_TOOL, "Return the structured result.", output_schema)],
            "toolChoice": {"tool": {"name": _STRUCTURED_TOOL}},
        }
        answer = await self._post(self._url(resolved, "converse"), body, "complete_structured")
        for block in _answer_blocks(answer):
            use = _obj(block.get("toolUse"))
            if use.get("name") == _STRUCTURED_TOOL:
                return StructuredResponse(data=_obj(use.get("input")), usage=self._usage(answer.get("usage")), model=resolved)
        raise TransportError("Structured output toolUse block not found in response")

    @override
    async def stream(
        self,
        messages: list[Message],
        tools: list[ToolSchema] | None = None,
        system: str | None = None,
        model: str | None = None,
        thinking_budget: int | None = None,
        effort: str | None = None,
        system_blocks: list[str] | None = None,
        max_output_tokens: int | None = None,
        *,
        tool_choice: ToolChoice | None = None,  # as in complete()
    ) -> AsyncIterator[StreamEvent]:
        resolved = model or self._model
        url = self._url(resolved, "converse-stream")
        data = json.dumps(
            self._body(messages, tools, system, system_blocks, thinking_budget, max_output_tokens)
        ).encode()
        assembly = _Assembly()
        try:
            async with self._http.stream(
                "POST", url, content=data, headers=self._signed(url, data, "application/vnd.amazon.eventstream")
            ) as response:
                if response.status_code != 200:
                    raw = await response.aread()
                    raise self._error(response.status_code, raw, response.headers.get("x-amzn-errortype", ""), "stream")
                decoder = EventStreamDecoder()
                async for chunk in response.aiter_bytes():
                    for frame in decoder.feed(chunk):
                        if frame.message_type == "exception":
                            status = _EXCEPTION_STATUS.get(frame.event_type, 500)
                            raise self._error(status, frame.payload, frame.event_type, "stream")
                        for event in assembly.apply(frame.event_type, json.loads(frame.payload or b"{}")):
                            yield event
                decoder.finish()
        except TransportError:
            raise
        except EventStreamError as exc:
            raise TransportError(
                f"Bedrock API error (stream): {exc}", status_code=502, retryable=is_retryable(502, network=False)
            ) from exc
        except (httpx.HTTPError, OSError) as exc:
            raise _network_error(exc, "stream") from exc
        stop = self._stop(assembly.stop_reason, "stream")
        yield StreamCompleteEvent(
            response=ModelResponse(
                message=self._message(assembly.blocks(), stop), usage=self._usage(assembly.usage), stop_reason=stop, model=resolved
            )
        )


def _network_error(exc: BaseException, context: str) -> TransportError:
    what = "timeout" if isinstance(exc, httpx.TimeoutException) else type(exc).__name__
    return transport_error(f"Bedrock API error ({context}): {what}", exc)


class _Assembly:
    """The streamed blocks, put back together in the shape ``converse`` returns them."""

    def __init__(self) -> None:
        self._blocks: dict[int, dict[str, Any]] = {}
        self.stop_reason: str | None = None
        self.usage: dict[str, Any] | None = None

    def apply(self, event_type: str, payload: dict[str, Any]) -> list[StreamEvent]:
        index = int(payload.get("contentBlockIndex", 0) or 0)
        if event_type == "contentBlockStart":
            use = _obj(_obj(payload.get("start")).get("toolUse"))
            if use:
                self._blocks[index] = {"toolUse": {"toolUseId": use.get("toolUseId", ""), "name": use.get("name", ""), "input": ""}}
                return [ToolCallDeltaEvent(index=index, id=use.get("toolUseId"), name=use.get("name"), arguments_delta="")]
        elif event_type == "contentBlockDelta":
            return self._delta(index, _obj(payload.get("delta")))
        elif event_type == "messageStop":
            self.stop_reason = payload.get("stopReason")
        elif event_type == "metadata":
            self.usage = payload.get("usage")
        return []

    def _delta(self, index: int, delta: dict[str, Any]) -> list[StreamEvent]:
        if "text" in delta:
            block = self._blocks.setdefault(index, {"text": ""})
            block["text"] += delta["text"]
            return [TextDeltaEvent(delta=delta["text"])] if delta["text"] else []
        if "toolUse" in delta:
            block = self._blocks.setdefault(index, {"toolUse": {"toolUseId": "", "name": "", "input": ""}})
            piece = str(_obj(delta["toolUse"]).get("input") or "")
            block["toolUse"]["input"] += piece
            return [ToolCallDeltaEvent(index=index, arguments_delta=piece)] if piece else []
        if "reasoningContent" in delta:
            reasoning = self._blocks.setdefault(index, {"reasoningContent": {"reasoningText": {"text": ""}}})["reasoningContent"]
            part = _obj(delta["reasoningContent"])
            if "redactedContent" in part:
                reasoning.pop("reasoningText", None)
                reasoning["redactedContent"] = part["redactedContent"]
                return []
            text = reasoning.setdefault("reasoningText", {"text": ""})
            if "signature" in part:
                text["signature"] = part["signature"]
            if part.get("text"):
                text["text"] += part["text"]
                return [ThinkingDeltaEvent(delta=part["text"])]
        return []

    def blocks(self) -> list[dict[str, Any]]:
        return [self._blocks[i] for i in sorted(self._blocks)]


__all__ = ["SERVICE", "BedrockConverseTransport"]
