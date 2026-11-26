"""E2E3-F6's doubles: a tool gateway that comes up late, and a model that records the tools each request carried.

The tool gateway is a minimal MCP Streamable HTTP server with one tool. It is late in one of three ways: it answers
``initialize`` only after ``late_s`` (a worker under load), it answers 503 until ``refuse_s`` after the first request
it sees (what the egress proxy answers while nothing listens behind it), or it never comes up. The model answers
every request with one short text, in whichever wire the engine speaks (Chat Completions, Messages, Gemini
``streamGenerateContent``), and records the tool names it was offered.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import math
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from aiohttp import web

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

TOOL = "knowledgegraph__search"
MODEL_PREFIX = "/m"
TOOLS_PREFIX = "/t"
NEVER = math.inf
# The name the driver's gateway probe gives itself in ``initialize`` (``harness_engines.acp.gateway_probe``).
PROBE_CLIENT = "pipeshub-gateway-probe"


@dataclass
class LateGateway:
    """``late_s``: ``initialize`` answers after this long. ``refuse_s``: 503 to everything until this long after the
    first request (``NEVER``: always). ``tools``: what ``tools/list`` answers.

    WP-59: ``refuse_after_probe``: once the driver's probe has been served the tool list, 503 to everything (the
    engine's own connect a moment later is refused). ``gemini_calls``: the Gemini model's first answer calls this
    tool, later ones are text. ``tool_hang_s``: ``tools/call`` answers after this long. ``listed()`` is the count
    of tool lists served, as the Tool Gateway's ``ToolListings``."""

    late_s: float = 0.0
    refuse_s: float = 0.0
    tools: tuple[str, ...] = (TOOL,)
    refuse_after_probe: bool = False
    gemini_calls: str | None = None
    tool_hang_s: float = 0.0
    requested_tools: list[list[str]] = field(default_factory=list[list[str]])
    # The conversation each Gemini request carried (its ``contents``, as JSON).
    request_bodies: list[str] = field(default_factory=list[str])
    mcp_methods: list[str] = field(default_factory=list[str])
    # Every MCP request's path and lower-cased header names, refused or not (who the engine reached, and how).
    mcp_requests: list[tuple[str, frozenset[str]]] = field(default_factory=list[tuple[str, frozenset[str]]])
    # When (monotonic) each model request and each ``tools/call`` arrived.
    model_at: list[float] = field(default_factory=list[float])
    tool_calls_at: list[float] = field(default_factory=list[float])
    _first_seen: float | None = None
    _lists: int = 0
    _probe_client: bool = False
    _probe_served: bool = False

    @property
    def model_requests(self) -> int:
        return len(self.requested_tools)

    def listed(self) -> int:
        return self._lists

    def first_request_tools(self) -> list[str]:
        assert self.requested_tools, "the engine made no model request"
        return self.requested_tools[0]

    def app(self) -> web.Application:
        app = web.Application()
        _ = app.router.add_post(f"{MODEL_PREFIX}/v1/chat/completions", self._chat)
        _ = app.router.add_post(f"{MODEL_PREFIX}/v1/messages", self._messages)
        # Gemini CLI posts ``/v1beta/models/{model}:streamGenerateContent?alt=sse`` under the model root.
        _ = app.router.add_post(MODEL_PREFIX + "/v1beta/models/{call}", self._gemini)
        _ = app.router.add_route("*", f"{TOOLS_PREFIX}/{{path:.*}}", self._tools)
        return app

    @contextlib.asynccontextmanager
    async def serving(self, host: str) -> AsyncIterator[str]:
        """Serves on ``host``; yields the base URL the upstreams hang off."""
        runner = web.AppRunner(self.app(), access_log=None)
        await runner.setup()
        site = web.TCPSite(runner, host, 0)
        await site.start()
        port: int = site._server.sockets[0].getsockname()[1]  # type: ignore[union-attr]  # pyright: ignore[reportPrivateUsage, reportOptionalMemberAccess]
        try:
            yield f"http://{host}:{port}"
        finally:
            await runner.cleanup()

    def _refusing(self) -> bool:
        now = time.monotonic()
        if self._first_seen is None:
            self._first_seen = now
        return now - self._first_seen < self.refuse_s

    async def _tools(self, request: web.Request) -> web.Response:
        self.mcp_requests.append((request.match_info["path"], frozenset(k.lower() for k in request.headers)))
        if request.match_info["path"] != "mcp":
            return web.Response(status=404)
        if self._refusing() or self._probe_served:
            return web.Response(status=503)
        if request.method != "POST":
            return web.Response(status=405)
        message: dict[str, Any] = await request.json()
        method = str(message.get("method"))
        self.mcp_methods.append(method)
        if "id" not in message:
            return web.Response(status=202)
        result: dict[str, Any] = {}
        if method == "initialize":
            self._probe_client = message["params"].get("clientInfo", {}).get("name") == PROBE_CLIENT
            await asyncio.sleep(self.late_s)
            result = {
                "protocolVersion": message["params"]["protocolVersion"],
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "pipeshub", "version": "1"},
            }
        elif method == "tools/list":
            result = {"tools": [{"name": t, "description": "Search", "inputSchema": {"type": "object"}} for t in self.tools]}
            self._lists += 1
            # Stateless: the probe's list follows the probe's initialize.
            self._probe_served = self.refuse_after_probe and self._probe_client
        elif method == "tools/call":
            self.tool_calls_at.append(time.monotonic())
            await asyncio.sleep(self.tool_hang_s)
            result = {"content": [{"type": "text", "text": "found"}]}
        return web.json_response({"jsonrpc": "2.0", "id": message["id"], "result": result})

    async def _chat(self, request: web.Request) -> web.StreamResponse:
        body: dict[str, Any] = await request.json()
        self.requested_tools.append([str(t.get("function", {}).get("name")) for t in body.get("tools", [])])
        response = web.StreamResponse(headers={"content-type": "text/event-stream"})
        _ = await response.prepare(request)
        base: dict[str, Any] = {"id": "c1", "object": "chat.completion.chunk", "created": 1, "model": body.get("model")}
        usage: dict[str, Any] = {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}
        chunks: list[dict[str, Any]] = [
            {**base, "choices": [{"index": 0, "delta": {"role": "assistant", "content": "ok"}, "finish_reason": None}]},
            {**base, "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}], "usage": usage},
        ]
        for chunk in chunks:
            await response.write(f"data: {json.dumps(chunk)}\n\n".encode())
        await response.write(b"data: [DONE]\n\n")
        await response.write_eof()
        return response

    async def _messages(self, request: web.Request) -> web.StreamResponse:
        body: dict[str, Any] = await request.json()
        self.requested_tools.append([str(t.get("name")) for t in body.get("tools", [])])
        response = web.StreamResponse(headers={"content-type": "text/event-stream"})
        _ = await response.prepare(request)
        message: dict[str, Any] = {
            "id": "msg_1", "type": "message", "role": "assistant", "model": body.get("model"), "content": [],
            "stop_reason": None, "stop_sequence": None, "usage": {"input_tokens": 1, "output_tokens": 1},
        }
        events: list[dict[str, Any]] = [
            {"type": "message_start", "message": message},
            {"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}},
            {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": "ok"}},
            {"type": "content_block_stop", "index": 0},
            {"type": "message_delta", "delta": {"stop_reason": "end_turn", "stop_sequence": None}, "usage": {"output_tokens": 1}},
            {"type": "message_stop"},
        ]
        for event in events:
            await response.write(f"event: {event['type']}\ndata: {json.dumps(event)}\n\n".encode())
        await response.write_eof()
        return response

    async def _gemini(self, request: web.Request) -> web.StreamResponse:
        if not request.match_info["call"].endswith(":streamGenerateContent"):
            return web.Response(status=404)
        body: dict[str, Any] = await request.json()
        self.request_bodies.append(json.dumps(body.get("contents")))
        self.requested_tools.append(
            [str(d.get("name")) for t in body.get("tools", []) for d in t.get("functionDeclarations", [])]
        )
        self.model_at.append(time.monotonic())
        part: dict[str, Any] = {"text": "ok"}
        if self.gemini_calls is not None and len(self.model_at) == 1:
            part = {"functionCall": {"name": self.gemini_calls, "args": {"query": "x"}}}
        response = web.StreamResponse(headers={"content-type": "text/event-stream"})
        _ = await response.prepare(request)
        chunk: dict[str, Any] = {
            "candidates": [{"content": {"role": "model", "parts": [part]}, "finishReason": "STOP", "index": 0}],
            "usageMetadata": {"promptTokenCount": 1, "candidatesTokenCount": 1, "totalTokenCount": 2},
            "modelVersion": "engine-model",
            "responseId": "r1",
        }
        await response.write(f"data: {json.dumps(chunk)}\r\n\r\n".encode())
        await response.write_eof()
        return response


__all__ = ["MODEL_PREFIX", "NEVER", "PROBE_CLIENT", "TOOL", "TOOLS_PREFIX", "LateGateway"]
