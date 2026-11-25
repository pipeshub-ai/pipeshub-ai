"""PF-07's rig: what the model gateway adds to an engine's time to first token, on every engine wire.

A scripted provider (OpenAI's wire, chat completions and Responses) streams a first token after ``first_s`` and the
rest after ``hold_s``. An engine's recorded request goes to the gateway (production ``ModelGateway`` and
``create_model_gateway_app``: token check, grant, budget reservation, the org's route, the real ``OpenAITransport``
SDK); the provider's own time to its first token is taken off what the engine waited, on one clock in one process:

    added = (engine saw the first token - engine sent) - (provider wrote it - provider received the request)

The same request straight to the provider gives the floor (loopback HTTP alone). ``first_token_lead_s`` is how long
before the provider's rest the engine had its first token: a gateway that buffers the stream has none.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import time
import uuid
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

import httpx
from aiohttp import web
from aiohttp.test_utils import TestServer

from app.harness.access.budget import NodeBudgetLedger
from harness_core.gateway.model_server import create_model_gateway_app
from harness_core.models.transports.openai import OpenAITransport
from harness_core.testing.model_gateway import GatewayRig
from tests.unit.harness_core.gateway.test_chat_wire import OPENCODE_BODY
from tests.unit.harness_core.gateway.test_messages_wire import claude_code_request
from tests.unit.harness_core.gateway.test_model_server import ENGINE_HEADERS, ORG_KEY
from tests.unit.harness_core.gateway.test_responses_wire import (
    codex_headers,
    codex_request,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

# A token no request or wire framing contains: the engine's first token is where it first appears.
FIRST = "PXHFIRSTTOKEN"
REST = " and the rest of the answer."
WIRES = ("chat", "messages", "responses")


@dataclass
class Mark:
    received: float
    first_written: float = 0.0


@dataclass
class ScriptedProvider:
    """OpenAI's wire: the first token after ``first_s``, the rest ``hold_s`` later."""

    first_s: float = 0.05
    hold_s: float = 0.0
    marks: list[Mark] = field(default_factory=list[Mark])

    async def _stream(self, request: web.Request, events: list[bytes]) -> web.StreamResponse:
        mark = Mark(time.perf_counter())
        self.marks.append(mark)
        _ = await request.read()
        response = web.StreamResponse(headers={"Content-Type": "text/event-stream", "Cache-Control": "no-cache"})
        await response.prepare(request)
        await asyncio.sleep(self.first_s)
        await response.write(events[0])
        mark.first_written = time.perf_counter()
        if self.hold_s:
            await asyncio.sleep(self.hold_s)
        for event in events[1:]:
            await response.write(event)
        await response.write_eof()
        return response

    async def chat(self, request: web.Request) -> web.StreamResponse:
        base = {"id": "chatcmpl-pf07", "object": "chat.completion.chunk", "created": 1, "model": "gpt-x"}

        def chunk(delta: dict[str, Any], finish: str | None = None) -> bytes:
            body = {**base, "choices": [{"index": 0, "delta": delta, "finish_reason": finish}]}
            return f"data: {json.dumps(body)}\n\n".encode()

        usage = {**base, "choices": [], "usage": {"prompt_tokens": 50, "completion_tokens": 8, "total_tokens": 58}}
        return await self._stream(
            request,
            [
                chunk({"role": "assistant", "content": FIRST}),
                chunk({"content": REST}),
                chunk({}, "stop"),
                f"data: {json.dumps(usage)}\n\n".encode(),
                b"data: [DONE]\n\n",
            ],
        )

    async def responses(self, request: web.Request) -> web.StreamResponse:
        text = FIRST + REST
        msg = {"id": "msg_pf07", "type": "message", "status": "completed", "role": "assistant",
               "content": [{"type": "output_text", "text": text, "annotations": []}]}
        response = {"id": "resp_pf07", "object": "response", "created_at": 1, "status": "completed", "model": "gpt-x",
                    "output": [msg], "parallel_tool_calls": True, "tool_choice": "auto", "tools": [],
                    "usage": {"input_tokens": 50, "input_tokens_details": {"cached_tokens": 0}, "output_tokens": 8,
                              "output_tokens_details": {"reasoning_tokens": 0}, "total_tokens": 58}}
        events = [
            {"type": "response.created", "sequence_number": 0, "response": {**response, "status": "in_progress", "output": []}},
            {"type": "response.output_item.added", "sequence_number": 1, "output_index": 0, "item": {**msg, "content": []}},
            {"type": "response.output_text.delta", "sequence_number": 2, "output_index": 0, "item_id": "msg_pf07",
             "content_index": 0, "delta": FIRST},
            {"type": "response.output_text.delta", "sequence_number": 3, "output_index": 0, "item_id": "msg_pf07",
             "content_index": 0, "delta": REST},
            {"type": "response.output_item.done", "sequence_number": 4, "output_index": 0, "item": msg},
            {"type": "response.completed", "sequence_number": 5, "response": response},
        ]
        framed = [f"event: {e['type']}\ndata: {json.dumps(e)}\n\n".encode() for e in events]
        # The first token's own event leads; what comes before it is framing the engine never waits on.
        return await self._stream(request, [b"".join(framed[:3]), *framed[3:]])

    def app(self) -> web.Application:
        app = web.Application()
        app.router.add_post("/v1/chat/completions", self.chat)
        app.router.add_post("/v1/responses", self.responses)
        return app


def engine_request(wire: str) -> tuple[str, dict[str, Any], dict[str, str]]:
    """Path, body and headers of a recorded engine request on ``wire`` (opencode, Claude Code, Codex)."""
    if wire == "chat":
        return "/v1/chat/completions", {**OPENCODE_BODY, "stream": True}, dict(ENGINE_HEADERS)
    if wire == "messages":
        body, headers = claude_code_request()
        return "/v1/messages", {**body, "stream": True}, headers
    return "/v1/responses", {**codex_request(), "stream": True}, codex_headers()


@dataclass
class Sample:
    wire: str
    added_ms: float
    first_token_lead_s: float


@dataclass
class TtftRig:
    provider: ScriptedProvider
    provider_url: str
    gateway_url: str
    token: str
    client: httpx.AsyncClient

    async def _first_token(self, url: str, body: dict[str, Any], headers: dict[str, str]) -> tuple[float, float]:
        """Seconds from send to the first token, and the clock reading when it arrived."""
        sent = time.perf_counter()
        seen = b""
        arrived: float | None = None
        async with self.client.stream("POST", url, json=body, headers=headers) as response:
            if response.status_code != 200:
                raise AssertionError(f"{url} answered {response.status_code}: {(await response.aread())[:400]!r}")
            async for piece in response.aiter_raw():
                if arrived is None:
                    seen += piece
                    if FIRST.encode() in seen:
                        arrived = time.perf_counter()
        if arrived is None:
            raise AssertionError(f"{url} never streamed the first token: {seen[:400]!r}")
        return arrived - sent, arrived

    async def through_gateway(self, wire: str) -> Sample:
        path, body, headers = engine_request(wire)
        headers = {k: v for k, v in headers.items() if k.lower() not in ("authorization", "x-api-key")}
        headers["Authorization"] = f"Bearer {self.token}"
        before = len(self.provider.marks)
        waited, arrived = await self._first_token(self.gateway_url + path, body, headers)
        (mark,) = self.provider.marks[before:]
        provider_s = mark.first_written - mark.received
        return Sample(wire, (waited - provider_s) * 1000, self.provider.hold_s - (arrived - mark.first_written))

    async def direct(self) -> Sample:
        """The floor: an OpenAI chat request straight to the provider."""
        body = {"model": "gpt-x", "messages": [{"role": "user", "content": "hi"}], "stream": True}
        before = len(self.provider.marks)
        waited, arrived = await self._first_token(self.provider_url + "/v1/chat/completions", body, {})
        (mark,) = self.provider.marks[before:]
        provider_s = mark.first_written - mark.received
        return Sample("direct", (waited - provider_s) * 1000, self.provider.hold_s - (arrived - mark.first_written))


async def _start(app: web.Application) -> TestServer:
    server = TestServer(app, host="127.0.0.1")
    await server.start_server()
    return server


class NodeBudget:
    """Production's ``NodeBudgetLedger`` on Node's ledger routes (``journal-http-server.ts``), the run's account opened
    as admission opens it. The test server keeps the ledger's token authority apart from the gateway's, so the
    run's ledger token stands in for the engine's gateway token on these routes; the round trip is the same."""

    def __init__(self, base_url: str, control: httpx.Client, run_id: str) -> None:
        _ = control.post("/budget/open-root", json={"runId": run_id, "cap": {"usd": 10_000_000, "tokens": 10_000_000}})
        self._token = str(control.post("/budget/token", json={"runId": run_id}).raise_for_status().json()["token"])
        self._inner = NodeBudgetLedger(base_url=base_url)

    def __getattr__(self, name: str) -> Any:  # noqa: ANN401
        method = getattr(self._inner, name)

        async def call(*args: Any, **kwargs: Any) -> Any:  # noqa: ANN401
            if "credential" in kwargs:
                kwargs["credential"] = self._token
            return await method(*args, **kwargs)

        return call


@contextlib.asynccontextmanager
async def ttft_rig(provider: ScriptedProvider, node: Any = None) -> AsyncIterator[TtftRig]:  # noqa: ANN401
    """``node``: a running journal server (``base_url``, ``control``) whose ledger routes the gateway reserves on, as
    production's does; ``None``: the in-memory reference ledger."""
    upstream = await _start(provider.app())
    provider_url = str(upstream.make_url("")).rstrip("/")
    run_id = f"pf07-{uuid.uuid4().hex[:8]}"
    rig = GatewayRig(budget=NodeBudget(node.base_url, node.control, run_id) if node is not None else None)  # type: ignore[arg-type]
    binding = rig.pin(
        run_id, transport=OpenAITransport(api_key=ORG_KEY, model="gpt-x", base_url=f"{provider_url}/v1")
    )
    gateway = await _start(create_model_gateway_app(rig.gateway))
    client = httpx.AsyncClient(timeout=30)
    try:
        yield TtftRig(provider, provider_url, str(gateway.make_url("")).rstrip("/"), rig.token(binding), client)
    finally:
        await client.aclose()
        await gateway.close()
        await upstream.close()


__all__ = ["FIRST", "WIRES", "NodeBudget", "Sample", "ScriptedProvider", "TtftRig", "engine_request", "ttft_rig"]
