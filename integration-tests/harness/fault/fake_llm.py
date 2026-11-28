"""A scripted OpenAI-compatible provider for the degraded scenarios (QA-17): chat completions and embeddings.

    python fake_llm.py <port>

A chat request with no tool result in it is answered with one ``create_issue``
call; one with a tool result, with the answer. ``POST /__fault/plan`` queues what
the next requests get instead (``{"status": 429}`` or ``{"status": 500}``; the
plan's ``then`` is what every request after it gets, ``"ok"`` by default). Slow,
stalled and cut streams are toxiproxy's job, on the wire in front of this server.
"""

from __future__ import annotations

import json
import sys
from typing import Any

from aiohttp import web

from serve import serve

ANSWER = "The issue is filed: SENTINEL-ANSWER"
ERRORS: dict[int, dict[str, Any]] = {
    429: {"message": "Rate limit reached for requests", "type": "requests", "code": "rate_limit_exceeded"},
    500: {"message": "The server had an error while processing your request.", "type": "server_error"},
    503: {"message": "The engine is currently overloaded.", "type": "server_error"},
}


class Plan:
    def __init__(self) -> None:
        self.steps: list[dict[str, Any]] = []
        self.then: dict[str, Any] | str = "ok"
        self.calls = 0

    def next(self) -> dict[str, Any] | str:
        self.calls += 1
        return self.steps.pop(0) if self.steps else self.then


def _chunk(delta: dict[str, Any], finish: str | None = None) -> bytes:
    body = {
        "id": "chatcmpl-fault",
        "object": "chat.completion.chunk",
        "created": 0,
        "model": "fault-model",
        "choices": [{"index": 0, "delta": delta, "finish_reason": finish}],
    }
    return f"data: {json.dumps(body)}\n\n".encode()


def _turn(messages: list[dict[str, Any]]) -> list[bytes]:
    if any(m.get("role") == "tool" for m in messages):
        words = ANSWER.split(" ")
        return [
            _chunk({"role": "assistant", "content": ""}),
            *(_chunk({"content": w if i == 0 else " " + w}) for i, w in enumerate(words)),
            _chunk({}, "stop"),
        ]
    call = {"index": 0, "id": "call_fault_1", "type": "function",
            "function": {"name": "create_issue", "arguments": json.dumps({"title": "provider bug"})}}
    return [_chunk({"role": "assistant", "content": None, "tool_calls": [call]}), _chunk({}, "tool_calls")]


async def chat(request: web.Request) -> web.StreamResponse:
    plan: Plan = request.app["plan"]
    body = await request.json()
    step = plan.next()
    if isinstance(step, dict) and "status" in step:
        status = int(step["status"])
        headers = {str(k): str(v) for k, v in (step.get("headers") or {}).items()}
        return web.json_response({"error": ERRORS.get(status, ERRORS[500])}, status=status, headers=headers)
    response = web.StreamResponse(headers={"content-type": "text/event-stream"})
    await response.prepare(request)
    for chunk in _turn(body.get("messages") or []):
        await response.write(chunk)
    usage = {"id": "chatcmpl-fault", "object": "chat.completion.chunk", "created": 0, "model": "fault-model",
             "choices": [], "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}}
    await response.write(f"data: {json.dumps(usage)}\n\ndata: [DONE]\n\n".encode())
    await response.write_eof()
    return response


async def embeddings(request: web.Request) -> web.Response:
    body = await request.json()
    inputs = body.get("input")
    texts = inputs if isinstance(inputs, list) else [inputs]
    data = [{"object": "embedding", "index": i, "embedding": [0.125] * 8} for i, _ in enumerate(texts)]
    return web.json_response({"object": "list", "data": data, "model": "fault-embed",
                              "usage": {"prompt_tokens": 1, "total_tokens": 1}})


async def set_plan(request: web.Request) -> web.Response:
    plan: Plan = request.app["plan"]
    body = await request.json()
    plan.steps = list(body.get("steps") or [])
    plan.then = body.get("then", "ok")
    return web.json_response({"ok": True})


async def calls(request: web.Request) -> web.Response:
    return web.json_response({"calls": request.app["plan"].calls})


def app() -> web.Application:
    application = web.Application()
    application["plan"] = Plan()
    application.router.add_post("/v1/chat/completions", chat)
    application.router.add_post("/v1/embeddings", embeddings)
    application.router.add_post("/__fault/plan", set_plan)
    application.router.add_get("/__fault/calls", calls)
    return application


def main() -> None:
    serve(app(), int(sys.argv[1]), "FAKE LLM READY")

if __name__ == "__main__":
    main()
