"""E3b RH-1..RH-4: ``/v1/responses`` over real HTTP, with the ``openai`` SDK, Codex's request and real transports.

The provider is E3's fake HTTP server (``FakeProvider``), here also speaking the
Responses API upstream as Azure does for a reasoning deployment, so the
transports under the gateway are the real SDK ones.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer

from harness_core.access.types import ENGINE_MODEL_ID
from harness_core.gateway.model_server import REFUSAL_HEADER
from harness_core.models.transports.anthropic import AnthropicTransport
from harness_core.models.transports.azure_openai import (
    AzureOpenAITransport,
    RequestDefaults,
)
from harness_core.models.transports.openai import OpenAITransport
from harness_core.testing.model_gateway import GatewayRig, RecordedTransport
from tests.support.loopback_client import loopback_client
from tests.unit.harness_core.gateway.test_model_server import (
    ORG_KEY,
    FakeProvider,
    Served,
    _serve,
)
from tests.unit.harness_core.gateway.test_responses_wire import (
    assemble,
    codex_headers,
    codex_request,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from harness_core.models.transports.base import LLMTransport

TOOL = {
    "type": "function",
    "name": "write",
    "description": "Write a file",
    "parameters": {"type": "object", "properties": {"filePath": {"type": "string"}}},
}


def _responses_upstream(model: str) -> list[dict[str, Any]]:
    """A Responses stream as Azure sends it for one text item and one function call."""
    msg = {"id": "msg_up", "type": "message", "status": "completed", "role": "assistant",
           "content": [{"type": "output_text", "text": "Writing files.", "annotations": []}]}
    call = {"id": "fc_up", "type": "function_call", "status": "completed", "call_id": "call_a", "name": "write",
            "arguments": '{"filePath":"a.txt"}'}
    response = {"id": "resp_up", "object": "response", "created_at": 1, "status": "completed", "model": model,
                "output": [msg, call], "parallel_tool_calls": True, "tool_choice": "auto", "tools": [],
                "usage": {"input_tokens": 120, "input_tokens_details": {"cached_tokens": 100}, "output_tokens": 30,
                          "output_tokens_details": {"reasoning_tokens": 0}, "total_tokens": 150}}
    return [
        {"type": "response.created", "sequence_number": 0, "response": {**response, "status": "in_progress", "output": []}},
        {"type": "response.output_item.added", "sequence_number": 1, "output_index": 0, "item": {**msg, "content": []}},
        {"type": "response.output_text.delta", "sequence_number": 2, "output_index": 0, "item_id": "msg_up",
         "content_index": 0, "delta": "Writing files."},
        {"type": "response.output_item.done", "sequence_number": 3, "output_index": 0, "item": msg},
        {"type": "response.output_item.added", "sequence_number": 4, "output_index": 1, "item": {**call, "arguments": ""}},
        {"type": "response.function_call_arguments.delta", "sequence_number": 5, "output_index": 1, "item_id": "fc_up",
         "delta": '{"filePath":"a.txt"}'},
        {"type": "response.output_item.done", "sequence_number": 6, "output_index": 1, "item": call},
        {"type": "response.completed", "sequence_number": 7, "response": response},
    ]


class ResponsesProvider(FakeProvider):
    async def responses(self, request: web.Request) -> web.StreamResponse:
        body = await request.json()
        self.seen.append((request.path, {k.lower(): v for k, v in request.headers.items()}, body))
        payload = b"".join(f"event: {e['type']}\ndata: {json.dumps(e)}\n\n".encode() for e in _responses_upstream(body["model"]))
        return web.Response(body=payload, content_type="text/event-stream")

    def app(self) -> web.Application:
        app = super().app()
        app.router.add_post("/openai/responses", self.responses)
        app.router.add_post("/openai/v1/responses", self.responses)
        return app


@pytest.fixture
async def provider() -> AsyncIterator[tuple[ResponsesProvider, str]]:
    fake = ResponsesProvider()
    server = TestServer(fake.app(), host="127.0.0.1")
    await server.start_server()
    yield fake, str(server.make_url("")).rstrip("/")
    await server.close()


@pytest.fixture
async def served() -> AsyncIterator[Served]:
    server, out = await _serve(GatewayRig())
    yield out
    await server.close()


def _without_auth(headers: dict[str, str] | None) -> dict[str, str]:
    """The engine's own Authorization is a placeholder the egress proxy replaces with the gateway token."""
    return {k: v for k, v in (headers or {}).items() if k.lower() != "authorization"}


async def _events(url: str, token: str, body: dict[str, Any], headers: dict[str, str] | None = None) -> list[dict[str, Any]]:
    """The SSE frames as sent: each ``event:`` line must name its ``data:`` payload's type."""
    async with loopback_client() as client, client.stream(
        "POST", f"{url}/v1/responses", json=body, headers={**_without_auth(headers), "Authorization": f"Bearer {token}"}
    ) as response:
        assert response.status_code == 200, await response.aread()
        assert response.headers["content-type"].startswith("text/event-stream")
        raw = (await response.aread()).decode()
    assert "[DONE]" not in raw
    frames = [f for f in raw.split("\n\n") if f.strip()]
    events: list[dict[str, Any]] = []
    for frame in frames:
        name, data = frame.split("\n")
        event = json.loads(data.removeprefix("data: "))
        assert name == f"event: {event['type']}"
        events.append(event)
    return events


async def test_rh1_the_openai_sdk_reads_both_answers(provider: tuple[ResponsesProvider, str]) -> None:
    import openai

    _, upstream = provider
    transport = OpenAITransport(api_key=ORG_KEY, model="gpt-4o", base_url=f"{upstream}/v1")
    server, out = await _serve(GatewayRig(), transport)
    try:
        client = openai.AsyncOpenAI(api_key=out.token, base_url=f"{out.url}/v1", max_retries=0)
        whole = await client.responses.create(model=ENGINE_MODEL_ID, input="go", tools=[TOOL], store=False)  # type: ignore[list-item]
        assert whole.model == ENGINE_MODEL_ID and whole.status == "completed"
        assert whole.output_text == "Writing files."
        calls = [item for item in whole.output if item.type == "function_call"]
        assert [(c.call_id, c.name, c.arguments) for c in calls] == [("call_a", "write", '{"filePath":"a.txt"}')]
        async with client.responses.stream(model=ENGINE_MODEL_ID, input="go", tools=[TOOL], store=False) as stream:  # type: ignore[list-item]
            kinds = [event.type async for event in stream]
            final = await stream.get_final_response()
        assert kinds[0] == "response.created" and kinds[-1] == "response.completed"
        assert "response.function_call_arguments.delta" in kinds
        assert final.output_text == "Writing files."
        assert [item.type for item in final.output] == [item.type for item in whole.output]
        assert final.usage is not None and (final.usage.input_tokens, final.usage.output_tokens) == (120, 30)
        await client.close()
    finally:
        await server.close()


async def test_rh2_codex_request_replays_over_http(served: Served) -> None:
    served.rig.transport.events = RecordedTransport().events
    events = await _events(served.url, served.token, codex_request(), codex_headers())
    assembled = assemble(events)
    assert assembled["status"] == "completed"
    assert assembled["output"][0]["content"][0]["text"] == "hello"
    call = served.rig.transport.calls[0]
    assert call.stream and call.system is not None and call.system.startswith("You are Codex")
    assert [t.name for t in call.tools or []] == ["exec_command", "pipeshub_search", "skills__list", "skills__read"]


@pytest.mark.parametrize("family", ["openai_chat", "azure_responses", "anthropic"])
async def test_rh3_real_transports_answer_the_same_on_the_responses_wire(
    provider: tuple[ResponsesProvider, str], family: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake, upstream = provider
    transport: LLMTransport
    if family == "openai_chat":
        transport = OpenAITransport(api_key=ORG_KEY, model="gpt-4o", base_url=f"{upstream}/v1")
    elif family == "azure_responses":
        transport = AzureOpenAITransport(
            api_key=ORG_KEY, azure_endpoint=upstream, api_version="2025-04-01-preview", deployment="dep-1",
            model="gpt-5.4", defaults=RequestDefaults(use_responses_api=True, reasoning={"effort": "low"}, model="gpt-5.4"),
        )
    else:
        monkeypatch.setenv("ANTHROPIC_BASE_URL", upstream)
        transport = AnthropicTransport(api_key=ORG_KEY, model="claude-haiku-4-5")
    server, out = await _serve(GatewayRig(), transport)
    try:
        body = {**codex_request(), "tools": [TOOL]}
        events = await _events(out.url, out.token, body, codex_headers())
    finally:
        await server.close()
    assembled = assemble(events)
    texts = [i["content"][0]["text"] for i in assembled["output"] if i["type"] == "message"]
    calls = [(i["call_id"], i["name"], json.loads(i["arguments"])) for i in assembled["output"] if i["type"] == "function_call"]
    assert texts == ["Writing files."] and calls == [("call_a", "write", {"filePath": "a.txt"})]
    assert assembled["usage"]["input_tokens"] == 120 and assembled["usage"]["input_tokens_details"]["cached_tokens"] == 100
    path, headers, sent = fake.seen[0]
    expected = {"openai_chat": "/v1/chat/completions", "anthropic": "/v1/messages"}
    assert path == expected.get(family, path) and (family != "azure_responses" or path.endswith("/responses"))
    auth = headers.get("authorization", "") + headers.get("api-key", "") + headers.get("x-api-key", "")
    assert ORG_KEY in auth and out.token not in json.dumps(headers)
    for name in codex_headers():
        if name not in ("content-type", "accept", "authorization", "user-agent"):
            assert name not in headers, name
    assert "codex" not in headers.get("user-agent", "")
    if family == "azure_responses":
        assert sent["store"] is False and sent["model"] == "dep-1"
    assert sent.get("store") is not True
    text = json.dumps(sent)
    assert "11111111-2222-4333-8444-555555555555" not in text and "prompt_cache_key" not in text


@pytest.mark.parametrize(
    ("payload", "status", "code"),
    [
        ({"previous_response_id": "resp_1"}, 400, "response_state_unsupported"),
        ({"store": True}, 400, "response_state_unsupported"),
        ({"tools": [{"type": "web_search", "external_web_access": False}]}, 400, "unsupported_parameter"),
        ({"model": "gpt-4o"}, 404, "model_not_granted"),
    ],
)
async def test_rh4_refusals_are_openai_errors(served: Served, payload: dict[str, Any], status: int, code: str) -> None:
    async with loopback_client() as client:
        response = await client.post(
            f"{served.url}/v1/responses",
            json={"model": ENGINE_MODEL_ID, "input": "hi", **payload},
            headers={"Authorization": f"Bearer {served.token}"},
        )
    assert response.status_code == status
    assert response.headers[REFUSAL_HEADER] == code
    assert response.json()["error"]["code"] == code
    assert served.rig.transport.calls == []


async def test_rh4_invalid_json_is_refused(served: Served) -> None:
    async with loopback_client() as client:
        response = await client.post(
            f"{served.url}/v1/responses", content="not json", headers={"Authorization": f"Bearer {served.token}"}
        )
    assert (response.status_code, response.headers[REFUSAL_HEADER]) == (400, "invalid_responses_request")


@pytest.mark.parametrize(
    ("method", "path"),
    [("GET", "/v1/responses/resp_1"), ("DELETE", "/v1/responses/resp_1"), ("POST", "/v1/responses/resp_1/cancel"),
     ("GET", "/v1/responses/resp_1/input_items")],
)
async def test_rh4_stored_response_routes_are_refused(served: Served, method: str, path: str) -> None:
    async with loopback_client() as client:
        anonymous = await client.request(method, f"{served.url}{path}")
        authorized = await client.request(method, f"{served.url}{path}", headers={"Authorization": f"Bearer {served.token}"})
    assert (anonymous.status_code, anonymous.headers[REFUSAL_HEADER]) == (401, "token_missing")
    assert (authorized.status_code, authorized.headers[REFUSAL_HEADER]) == (400, "response_state_unsupported")
