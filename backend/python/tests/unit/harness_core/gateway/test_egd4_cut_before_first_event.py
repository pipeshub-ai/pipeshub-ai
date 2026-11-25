"""FIX-EGD4: a streamed call whose caller leaves before the provider's first event is still recorded and charged.

Durable lane runs kept-qadur2/3, kept-cj5d/e, kept-3da2f258 and kept-tw1..tw3 each counted one engine call at the
scripted model that the model gateway never logged: the call the model held (DE-5's hold) when the worker was killed
or the sandbox reaped. The engine's connection went, the gateway's handler was cancelled while it waited for the
first event, and ``_open_stream`` gave the slot back and re-raised without settling: no ``model_gateway.call``, no
charge, and the reservation left open until it expired, although the provider had the request (ADR-GW-01 D2).
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import re
from typing import TYPE_CHECKING, override

import aiohttp
import pytest

from harness_core.access.types import ENGINE_MODEL_ID
from harness_core.budget.types import RunAccountRef
from harness_core.gateway.model_server import ModelGatewayServer
from harness_core.kernel.core.streaming import TextDeltaEvent
from harness_core.testing.model_gateway import GatewayRig, RecordedTransport

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from harness_core.kernel.core.messages import Message
    from harness_core.kernel.core.streaming import StreamEvent
    from harness_core.kernel.core.tool_schema import ToolChoice, ToolSchema


class HoldingTransport(RecordedTransport):
    """A provider that has the request and sends nothing yet: a model thinking before its first token, or the e2e
    model holding a call until the spec releases it."""

    def __init__(self, *, first: StreamEvent | None = None) -> None:
        super().__init__()
        self.first = first
        self.started = asyncio.Event()
        self.cut = asyncio.Event()

    @override
    async def stream(  # pyright: ignore[reportIncompatibleMethodOverride]
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
        tool_choice: ToolChoice | None = None,
    ) -> AsyncIterator[StreamEvent]:
        self._record(messages, tools, system, effort, stream=True, limit=max_output_tokens, tool_choice=tool_choice)
        if self.first is not None:
            yield self.first
        self.started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            self.cut.set()
            raise
        for event in self.events:
            yield event


def _lines(caplog: pytest.LogCaptureFixture, event: str) -> list[dict[str, str]]:
    found = [r.getMessage() for r in caplog.records if f"{event} " in r.getMessage()]
    return [dict(re.findall(r"(\w+)=(\S+)", line)) for line in found]


@contextlib.asynccontextmanager
async def _production(rig: GatewayRig) -> AsyncIterator[str]:
    # ``ModelGatewayServer`` as ``model_gateway_main`` runs it: its handler is cancelled when the caller hangs up.
    server = ModelGatewayServer(rig.gateway)
    port = await server.start(host="127.0.0.1", port=0)
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        await server.stop()


async def _caller_leaves_while_held(rig: GatewayRig, transport: HoldingTransport, path: str, body: object) -> None:
    token = rig.token(rig.pin(transport=transport))
    async with _production(rig) as url:
        async with aiohttp.ClientSession() as hop:
            sent = asyncio.create_task(hop.post(f"{url}{path}", json=body, headers={"Authorization": f"Bearer {token}"}))
            await asyncio.wait_for(transport.started.wait(), 5.0)
            _ = sent.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await sent
        await asyncio.wait_for(transport.cut.wait(), 5.0)
        await asyncio.wait_for(_settled(rig), 5.0)


async def _settled(rig: GatewayRig) -> None:
    # The ledger has no commit signal to wait on, so its state is polled; the caller bounds the wait.
    while True:
        if (await rig.ledger.remaining(RunAccountRef("org-1", "run-1"))).committed.usd_micros:
            return
        await asyncio.sleep(0.01)


_ASKS = {
    "chat": ("/v1/chat/completions", {"model": ENGINE_MODEL_ID, "stream": True, "messages": [{"role": "user", "content": "hi"}]}),
    "messages": (
        "/v1/messages",
        {"model": ENGINE_MODEL_ID, "max_tokens": 1_000, "stream": True, "messages": [{"role": "user", "content": "hi"}]},
    ),
    "responses": ("/v1/responses", {"model": ENGINE_MODEL_ID, "stream": True, "input": "hi"}),
}


@pytest.mark.parametrize("api", sorted(_ASKS))
async def test_egd4_1_a_stream_its_caller_left_before_the_first_event_is_one_cut_call(
    api: str, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO)
    transport = HoldingTransport()
    rig = GatewayRig(transport=transport)
    path, body = _ASKS[api]
    await _caller_leaves_while_held(rig, transport, path, body)
    (line,) = _lines(caplog, "model_gateway.call")
    assert (line["status"], line["estimated"], line["stream"]) == ("interrupted", "True", "True")
    assert int(line["costMicroUsd"]) > 0
    assert len(transport.calls) == 1


async def test_egd4_2_a_stream_its_caller_left_mid_answer_is_one_call_line_whatever_else_is_logged(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """``check_egress.py`` EG-D4 counts ``model_gateway.call`` lines against the engine calls the model saw: a call
    cut mid-stream may also log ``model_gateway.client_gone``, but it is one call line, never zero or two."""
    caplog.set_level(logging.INFO)
    transport = HoldingTransport(first=TextDeltaEvent(delta="part"))
    rig = GatewayRig(transport=transport)
    token = rig.token(rig.pin(transport=transport))
    path, body = _ASKS["chat"]
    async with _production(rig) as url:
        async with aiohttp.ClientSession() as hop:
            response = await hop.post(f"{url}{path}", json=body, headers={"Authorization": f"Bearer {token}"})
            _ = await response.content.readline()
            await asyncio.wait_for(transport.started.wait(), 5.0)
            response.close()
        await asyncio.wait_for(transport.cut.wait(), 5.0)
        await asyncio.wait_for(_settled(rig), 5.0)
    (line,) = _lines(caplog, "model_gateway.call")
    assert (line["status"], line["estimated"]) == ("interrupted", "True")
