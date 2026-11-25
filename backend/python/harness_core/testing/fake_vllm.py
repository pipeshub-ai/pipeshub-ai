"""A fake vLLM OpenAI-compatible server for one pinned open model (C13.04), as an ``httpx`` handler.

It answers ``POST /v1/chat/completions`` the way vLLM does once its tool-call and reasoning parsers have run for that
model (``harness_core.models.open_models``): a tool call streamed as one opening fragment with the id and name and
an empty argument string, then argument fragments; the row's tool-call id form (Kimi K2's ``functions.<name>:<i>``);
the reasoning in the row's field ahead of the content; ``finish_reason`` ``tool_calls``; usage in a last chunk with no
choices. It refuses what vLLM refuses for such a server: another model name, a tool without a parameters object, a
``tool_choice`` it does not take, and a tool message whose id no earlier assistant call carried. No GPU, no network.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

import httpx

if TYPE_CHECKING:
    from harness_core.models.open_models import OpenModelPin

_TOOL_CHOICES = frozenset({"auto", "none", "required"})


@dataclass(frozen=True, slots=True)
class ScriptedTurn:
    """What the model says on one request: reasoning, then text, then tool calls (name and arguments)."""

    reasoning: str = ""
    text: str = ""
    calls: tuple[tuple[str, dict[str, Any]], ...] = ()


@dataclass
class FakeVllm:
    pin: OpenModelPin
    served_name: str
    turns: list[ScriptedTurn]
    requests: list[dict[str, Any]] = field(default_factory=list[dict[str, Any]])

    def call_id(self, name: str, index: int, turn: int) -> str:
        if self.pin.tool_call_id is not None:
            return f"functions.{name}:{index}"
        return f"chatcmpl-tool-{turn}{index:02d}"

    def __call__(self, request: httpx.Request) -> httpx.Response:
        if request.url.path != "/v1/chat/completions":
            return _error(404, "NotFoundError", f"no route {request.url.path}")
        body = json.loads(request.content)
        self.requests.append(body)
        refusal = self._refusal(body)
        if refusal is not None:
            return _error(400, "BadRequestError", refusal)
        index = len(self.requests) - 1
        turn = self.turns[min(index, len(self.turns) - 1)]
        if body.get("stream"):
            return httpx.Response(200, headers={"content-type": "text/event-stream"}, content=self._sse(turn, index))
        return httpx.Response(200, json=self._whole(turn, index))

    def _refusal(self, body: dict[str, Any]) -> str | None:
        if body.get("model") != self.served_name:
            return f"The model `{body.get('model')}` does not exist."
        tools: list[dict[str, Any]] = body.get("tools") or []
        for tool in tools:
            function: dict[str, Any] = tool.get("function") or {}
            if tool.get("type") != "function" or not isinstance(function.get("parameters"), dict):
                return "a tool needs a function with a parameters object"
        choice = body.get("tool_choice")
        if choice is not None and (not tools or (isinstance(choice, str) and choice not in _TOOL_CHOICES)):
            return f"tool_choice {choice!r} is not supported here"
        issued: set[str] = set()
        messages: list[dict[str, Any]] = body.get("messages", [])
        for message in messages:
            if message.get("role") == "assistant":
                calls: list[dict[str, Any]] = message.get("tool_calls") or []
                issued.update(str(call["id"]) for call in calls)
            if message.get("role") == "tool" and message.get("tool_call_id") not in issued:
                return f"tool message for unknown call {message.get('tool_call_id')!r}"
        return None

    def _calls(self, turn: ScriptedTurn, index: int) -> list[dict[str, Any]]:
        return [
            {"id": self.call_id(name, i, index), "type": "function",
             "function": {"name": name, "arguments": json.dumps(args)}}
            for i, (name, args) in enumerate(turn.calls)
        ]

    def _whole(self, turn: ScriptedTurn, index: int) -> dict[str, Any]:
        message: dict[str, Any] = {"role": "assistant", "content": turn.text or None}
        if turn.reasoning:
            message[self.pin.reasoning_field] = turn.reasoning
        if turn.calls:
            message["tool_calls"] = self._calls(turn, index)
        return {
            "id": f"chatcmpl-{index}", "object": "chat.completion", "model": self.served_name,
            "choices": [{"index": 0, "message": message, "finish_reason": "tool_calls" if turn.calls else "stop"}],
            "usage": {"prompt_tokens": 40, "completion_tokens": 12, "total_tokens": 52, "prompt_tokens_details": None},
        }

    def _sse(self, turn: ScriptedTurn, index: int) -> bytes:
        def chunk(delta: dict[str, Any], finish: str | None = None) -> dict[str, Any]:
            return {"id": f"chatcmpl-{index}", "object": "chat.completion.chunk", "model": self.served_name,
                    "choices": [{"index": 0, "delta": delta, "finish_reason": finish}]}

        chunks: list[dict[str, Any]] = [chunk({"role": "assistant", "content": ""})]
        chunks.extend(chunk({self.pin.reasoning_field: piece}) for piece in _pieces(turn.reasoning))
        chunks.extend(chunk({"content": piece}) for piece in _pieces(turn.text))
        for i, call in enumerate(self._calls(turn, index)):
            opening = {"index": i, "id": call["id"], "type": "function",
                       "function": {"name": call["function"]["name"], "arguments": ""}}
            chunks.append(chunk({"tool_calls": [opening]}))
            chunks.extend(
                chunk({"tool_calls": [{"index": i, "function": {"arguments": piece}}]})
                for piece in _pieces(call["function"]["arguments"], 7)
            )
        chunks.append(chunk({}, "tool_calls" if turn.calls else "stop"))
        chunks.append({"id": f"chatcmpl-{index}", "object": "chat.completion.chunk", "model": self.served_name,
                       "choices": [], "usage": {"prompt_tokens": 40, "completion_tokens": 12, "total_tokens": 52}})
        lines = [f"data: {json.dumps(c)}\n\n" for c in chunks]
        return ("".join(lines) + "data: [DONE]\n\n").encode()


def _pieces(text: str, size: int = 5) -> list[str]:
    return [text[i : i + size] for i in range(0, len(text), size)]


def _error(status: int, kind: str, message: str) -> httpx.Response:
    return httpx.Response(status, json={"object": "error", "message": message, "type": kind, "param": None,
                                        "code": status})


__all__ = ["FakeVllm", "ScriptedTurn"]
