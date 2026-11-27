"""WP56-PTC: an upstream that rejects ``parallel_tool_calls`` costs one retry, once per route.

WP-62 and WP56-WIRE pass an engine's ``parallel_tool_calls`` to the provider. Some models and OpenAI-compatible
hosts refuse the parameter outright (400 "Unsupported parameter: 'parallel_tool_calls' is not supported with this
model"), which failed the engine's turn. The transport now resends that call once without it and remembers the
refusal for the route (the gateway keeps one transport per configured model and key), so later calls go without it
from the start and the gateway names it dropped. The refused request carried no usage: the call is charged once,
for the answer that came back. A 400 that names anything else is not retried.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer

from harness_core.budget.types import RunAccountRef
from harness_core.models.transports.openai import OpenAITransport
from harness_core.testing.model_gateway import GatewayRig
from tests.support.loopback_client import loopback_client
from tests.unit.harness_core.gateway.test_chat_wire import OPENCODE_BODY
from tests.unit.harness_core.gateway.test_model_server import (
    ENGINE_HEADERS,
    ORG_KEY,
    FakeProvider,
    _serve,
)
from tests.unit.harness_core.gateway.test_responses_wire import (
    codex_headers,
    codex_request,
)
from tests.unit.harness_core.gateway.test_wp62_codex_passthrough import (
    ReasoningProvider,
    _azure,
)

REFUSAL = {"error": {"message": "Unsupported parameter: 'parallel_tool_calls' is not supported with this model.",
                     "type": "invalid_request_error", "param": "parallel_tool_calls", "code": "unsupported_parameter"}}
OTHER = {"error": {"message": "Unsupported parameter: 'logprobs' is not supported with this model.",
                   "type": "invalid_request_error", "param": "logprobs", "code": "unsupported_parameter"}}
TOOL = {"type": "function", "name": "write", "description": "Write a file", "strict": False,
        "parameters": {"type": "object", "properties": {"filePath": {"type": "string"}}}}


def _refusing(base: type[FakeProvider], refusal: dict[str, Any]) -> FakeProvider:
    class Refusing(base):  # type: ignore[valid-type, misc]
        async def openai(self, request: web.Request) -> web.StreamResponse:
            body = await request.json()
            if "parallel_tool_calls" in body:
                self.seen.append((request.path, {}, body))
                return web.json_response(refusal, status=400)
            return await super().openai(request)

        async def responses(self, request: web.Request) -> web.StreamResponse:
            body = await request.json()
            if "parallel_tool_calls" in body:
                self.seen.append((request.path, {}, body))
                return web.json_response(refusal, status=400)
            return await super().responses(request)

    return Refusing()


async def _start(fake: FakeProvider) -> tuple[TestServer, str]:
    server = TestServer(fake.app(), host="127.0.0.1")
    await server.start_server()
    return server, str(server.make_url("")).rstrip("/")


def _calls(caplog: pytest.LogCaptureFixture) -> list[dict[str, str]]:
    lines = [r.getMessage() for r in caplog.records if "model_gateway.call " in r.getMessage()]
    return [dict(re.findall(r"(\w+)=(\S+)", line)) for line in lines]


async def _chat(url: str, token: str, stream: bool) -> int:
    body = {**OPENCODE_BODY, "parallel_tool_calls": True, "stream": stream}
    async with loopback_client() as client:
        response = await client.post(f"{url}/v1/chat/completions", json=body,
                                     headers={**ENGINE_HEADERS, "Authorization": f"Bearer {token}"})
    return response.status_code


@pytest.mark.parametrize("stream", [False, True])
async def test_wp56_ptc_1_the_chat_wire_resends_once_without_it_and_remembers_for_the_route(
    stream: bool, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO)
    fake = _refusing(FakeProvider, REFUSAL)
    upstream_server, upstream = await _start(fake)
    rig = GatewayRig()
    server, out = await _serve(rig, OpenAITransport(api_key=ORG_KEY, model="gpt-4o", base_url=f"{upstream}/v1"))
    try:
        assert await _chat(out.url, out.token, stream) == 200
        assert [("parallel_tool_calls" in b) for _, _, b in fake.seen] == [True, False]
        assert await _chat(out.url, out.token, stream) == 200
        assert [("parallel_tool_calls" in b) for _, _, b in fake.seen] == [True, False, False], "remembered"
    finally:
        await server.close()
        await upstream_server.close()
    calls = _calls(caplog)
    assert len(calls) == 2 and all(c["status"] == "ok" for c in calls)
    used = (await rig.ledger.remaining(RunAccountRef("org-1", "run-1"))).used
    assert used.input_tokens == 2 * 120 and used.output_tokens == 2 * 30, "each call charged once"
    dropped = [r.getMessage() for r in caplog.records if "model_gateway.fields_dropped" in r.getMessage()]
    assert len(dropped) == 1 and "parallel_tool_calls" in dropped[0], "named dropped once the route refused it"


async def test_wp56_ptc_2_the_responses_wire_resends_once_without_it_and_remembers_for_the_route(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO)
    fake = _refusing(ReasoningProvider, REFUSAL)
    upstream_server, upstream = await _start(fake)
    server, out = await _serve(GatewayRig(), _azure(upstream))
    headers = {k: v for k, v in codex_headers().items() if k != "authorization"}
    body = {**codex_request(), "tools": [TOOL]}
    try:
        async with loopback_client() as client:
            for _ in range(2):
                response = await client.post(f"{out.url}/v1/responses", json=body,
                                             headers={**headers, "Authorization": f"Bearer {out.token}"})
                assert response.status_code == 200, response.text
    finally:
        await server.close()
        await upstream_server.close()
    assert [("parallel_tool_calls" in b) for _, _, b in fake.seen] == [True, False, False]
    assert len(_calls(caplog)) == 2


async def test_wp56_ptc_3_a_refusal_naming_another_parameter_is_not_retried() -> None:
    fake = _refusing(FakeProvider, OTHER)
    upstream_server, upstream = await _start(fake)
    transport = OpenAITransport(api_key=ORG_KEY, model="gpt-4o", base_url=f"{upstream}/v1")
    server, out = await _serve(GatewayRig(), transport)
    try:
        assert await _chat(out.url, out.token, False) != 200
    finally:
        await server.close()
        await upstream_server.close()
    assert len(fake.seen) == 1
    assert "parallel_tool_calls" in transport.honoured_call_options
    assert json.dumps(fake.seen[0][2]).count("parallel_tool_calls") == 1
