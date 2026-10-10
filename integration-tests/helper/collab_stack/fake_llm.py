"""A small OpenAI-compatible model server for the real-Python mode.

The query service is configured with this server as its LLM and embedding provider (``openAICompatible``), so its agent loop
runs for real and only the model's words are scripted. Mounted on the lane's fake backend under ``/v1``:

* ``POST /v1/chat/completions`` (streaming and not), ``POST /v1/embeddings``, ``GET /v1/models``;
* every call is recorded (``fake.requests_for("llm_chat")``); ``messages_text(rec)`` flattens what the model was shown;
* a script is a list of turns, each answering one call: ``llm_turn("text")`` or ``llm_turn(tool_calls=[tool_call(name, args)])``.

``fake.script_llm(...)`` queues turns for matching calls only, so the query service's own side calls (titles, follow-ups,
structured-output helpers) cannot eat a turn meant for the main loop: a turn applies to the first call whose request is
accepted by ``when`` (default: a call that offers tools, or the first plain call).
"""

from __future__ import annotations

import hashlib
import json
import math
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

EMBEDDING_DIMS = 64
DEFAULT_TEXT = "Fake model answer."

LlmWhen = Callable[[Any], bool]


def tool_call(name: str, args: dict[str, Any], call_id: str | None = None) -> dict[str, Any]:
    return {"id": call_id or f"call_{uuid.uuid4().hex[:12]}", "type": "function", "function": {"name": name, "arguments": json.dumps(args)}}


def tool_call_ending(suffix: str, args: dict[str, Any]) -> dict[str, Any]:
    """A call to whichever offered tool's name ends with ``suffix`` (the toolset prefix is the product's to choose); resolved when the turn is rendered."""
    call = tool_call(suffix, args)
    call["_suffix"] = suffix
    return call


@dataclass
class LlmTurn:
    """What the model says on one call: text, tool calls, or both."""

    text: str = ""
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    when: LlmWhen | None = None
    delay: float = 0.0

    def render(self, request_body: dict[str, Any]) -> tuple[bytes, bool]:
        """(payload, is_sse). The payload is an SSE body for ``stream: true`` requests, else a JSON completion."""
        model = request_body.get("model", "fake-llm")
        offered = tool_names(request_body)
        calls = []
        for call in self.tool_calls:
            call = {**call}
            suffix = call.pop("_suffix", None)
            if suffix is not None:
                call["function"] = {**call["function"], "name": next((n for n in offered if n.endswith(suffix)), suffix)}
            calls.append(call)
        tool_calls = calls
        cid = f"chatcmpl-{uuid.uuid4().hex[:16]}"
        finish = "tool_calls" if tool_calls else "stop"
        usage = {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}
        if not request_body.get("stream"):
            message: dict[str, Any] = {"role": "assistant", "content": self.text or None}
            if tool_calls:
                message["tool_calls"] = tool_calls
            body = {"id": cid, "object": "chat.completion", "created": int(time.time()), "model": model, "choices": [{"index": 0, "message": message, "finish_reason": finish}], "usage": usage}
            return json.dumps(body).encode(), False

        def chunk(delta: dict[str, Any], finish_reason: str | None = None, **extra: Any) -> bytes:
            payload = {"id": cid, "object": "chat.completion.chunk", "created": int(time.time()), "model": model, "choices": [{"index": 0, "delta": delta, "finish_reason": finish_reason}], **extra}
            return f"data: {json.dumps(payload)}\n\n".encode()

        out = [chunk({"role": "assistant", "content": ""})]
        if self.text:
            step = max(1, len(self.text) // 3)
            out += [chunk({"content": self.text[i : i + step]}) for i in range(0, len(self.text), step)]
        for index, call in enumerate(tool_calls):
            out.append(chunk({"tool_calls": [{"index": index, "id": call["id"], "type": "function", "function": call["function"]}]}))
        out.append(chunk({}, finish))
        include_usage = bool((request_body.get("stream_options") or {}).get("include_usage"))
        if include_usage:
            out.append(f"data: {json.dumps({'id': cid, 'object': 'chat.completion.chunk', 'created': int(time.time()), 'model': model, 'choices': [], 'usage': usage})}\n\n".encode())
        out.append(b"data: [DONE]\n\n")
        return b"".join(out), True


def llm_turn(text: str = "", tool_calls: list[dict[str, Any]] | None = None, *, when: LlmWhen | None = None, delay: float = 0.0) -> LlmTurn:
    return LlmTurn(text=text, tool_calls=tool_calls or [], when=when, delay=delay)


def embed(text: str, dims: int = EMBEDDING_DIMS) -> list[float]:
    """A deterministic unit vector per text (hash-seeded), so the same query always lands on the same point."""
    seed = hashlib.sha256(text.encode()).digest()
    raw = [(seed[i % len(seed)] - 127.5) / 127.5 for i in range(dims)]
    norm = math.sqrt(sum(v * v for v in raw)) or 1.0
    return [v / norm for v in raw]


def embeddings_response(request_body: dict[str, Any]) -> dict[str, Any]:
    inputs = request_body.get("input", [])
    if isinstance(inputs, str):
        inputs = [inputs]
    data = [{"object": "embedding", "index": i, "embedding": embed(item if isinstance(item, str) else json.dumps(item))} for i, item in enumerate(inputs)]
    return {"object": "list", "data": data, "model": request_body.get("model", "fake-embedding"), "usage": {"prompt_tokens": 1, "total_tokens": 1}}


def models_response() -> dict[str, Any]:
    return {"object": "list", "data": [{"id": "fake-llm", "object": "model", "created": 0, "owned_by": "pcc-e2e"}, {"id": "fake-embedding", "object": "model", "created": 0, "owned_by": "pcc-e2e"}]}


def message_text(message: dict[str, Any]) -> str:
    content = message.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(part.get("text", "") for part in content if isinstance(part, dict))
    return ""


def messages_text(request_body: Any, role: str | None = None) -> str:
    """Everything the model was shown in one request (optionally one role), joined."""
    if not isinstance(request_body, dict):
        return ""
    return "\n".join(message_text(m) for m in request_body.get("messages", []) if role is None or m.get("role") == role)


def offers_tools(request_body: Any) -> bool:
    return isinstance(request_body, dict) and bool(request_body.get("tools"))


def tool_names(request_body: Any) -> list[str]:
    if not isinstance(request_body, dict):
        return []
    return [(t.get("function") or {}).get("name", "") for t in request_body.get("tools", [])]
