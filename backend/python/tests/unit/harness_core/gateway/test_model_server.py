"""E3 HS-1..HS-6: the model gateway over real HTTP, with real clients and real provider SDK transports.

The provider is a fake HTTP server speaking OpenAI's (and Azure's, and
Anthropic's) wire format, so the transports under the gateway are the real
``OpenAITransport``, ``AzureOpenAITransport`` and ``AnthropicTransport`` with their
SDKs: it records what the provider receives (headers included).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer

from harness_core.access.types import ENGINE_MODEL_ID, ROLE_MODEL_IDS
from harness_core.gateway.model_gateway import ENGINE_TRACKING_HEADERS
from harness_core.gateway.model_server import REFUSAL_HEADER, create_model_gateway_app
from harness_core.models.transports.anthropic import AnthropicTransport
from harness_core.models.transports.azure_openai import AzureOpenAITransport
from harness_core.models.transports.openai import OpenAITransport
from harness_core.testing.model_gateway import GatewayRig, RecordedTransport
from tests.support.loopback_client import loopback_client
from tests.unit.harness_core.gateway.test_chat_wire import OPENCODE_BODY, assemble

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from harness_core.models.transports.base import LLMTransport

ORG_KEY = "org-provider-key-not-real-0123456789"
# What opencode adds to a model call (F4 [run]) plus what the OpenAI/AI SDK clients add.
ENGINE_HEADERS = {
    "x-opencode-session-id": "ses_1",
    "x-session-id": "ses_1",
    "x-session-affinity": "ses_1",
    "user-agent": "ai-sdk/openai-compatible/1.0 opencode/1.18.34",
    "x-stainless-lang": "js",
    "openai-organization": "org-engine",
    "openai-project": "proj-engine",
    "http-referer": "https://opencode.ai",
    "x-title": "opencode",
    "x-egress-request-id": "egr-1",
}


def _sse(events: list[dict[str, Any]]) -> bytes:
    return b"".join(f"data: {json.dumps(e)}\n\n".encode() for e in events) + b"data: [DONE]\n\n"


def _openai_chunks(model: str) -> list[dict[str, Any]]:
    base = {"id": "chatcmpl-up", "object": "chat.completion.chunk", "created": 1, "model": model}

    def chunk(delta: dict[str, Any], finish: str | None = None) -> dict[str, Any]:
        return {**base, "choices": [{"index": 0, "delta": delta, "finish_reason": finish}]}

    return [
        chunk({"role": "assistant", "content": ""}),
        chunk({"content": "Writing "}),
        chunk({"content": "files."}),
        chunk({"tool_calls": [{"index": 0, "id": "call_a", "type": "function", "function": {"name": "write", "arguments": ""}}]}),
        chunk({"tool_calls": [{"index": 0, "function": {"arguments": '{"filePath":'}}]}),
        chunk({"tool_calls": [{"index": 0, "function": {"arguments": '"a.txt"}'}}]}),
        chunk({}, "tool_calls"),
        {**base, "choices": [], "usage": {"prompt_tokens": 120, "completion_tokens": 30, "total_tokens": 150,
                                          "prompt_tokens_details": {"cached_tokens": 100}}},
    ]


def _openai_completion(model: str) -> dict[str, Any]:
    return {
        "id": "chatcmpl-up",
        "object": "chat.completion",
        "created": 1,
        "model": model,
        "choices": [
            {
                "index": 0,
                "message": {
                    "role": "assistant",
                    "content": "Writing files.",
                    "tool_calls": [{"id": "call_a", "type": "function", "function": {"name": "write", "arguments": '{"filePath":"a.txt"}'}}],
                },
                "finish_reason": "tool_calls",
            }
        ],
        "usage": {"prompt_tokens": 120, "completion_tokens": 30, "total_tokens": 150, "prompt_tokens_details": {"cached_tokens": 100}},
    }


def _anthropic_events() -> list[tuple[str, dict[str, Any]]]:
    message = {
        "id": "msg_1", "type": "message", "role": "assistant", "model": "claude-haiku-4-5", "content": [],
        "stop_reason": None, "stop_sequence": None,
        "usage": {"input_tokens": 20, "output_tokens": 1, "cache_read_input_tokens": 100, "cache_creation_input_tokens": 0},
    }
    return [
        ("message_start", {"type": "message_start", "message": message}),
        ("content_block_start", {"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}}),
        ("content_block_delta", {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": "Writing files."}}),
        ("content_block_stop", {"type": "content_block_stop", "index": 0}),
        ("content_block_start", {"type": "content_block_start", "index": 1,
                                 "content_block": {"type": "tool_use", "id": "call_a", "name": "write", "input": {}}}),
        ("content_block_delta", {"type": "content_block_delta", "index": 1,
                                 "delta": {"type": "input_json_delta", "partial_json": '{"filePath":"a.txt"}'}}),
        ("content_block_stop", {"type": "content_block_stop", "index": 1}),
        ("message_delta", {"type": "message_delta", "delta": {"stop_reason": "tool_use", "stop_sequence": None},
                           "usage": {"output_tokens": 30}}),
        ("message_stop", {"type": "message_stop"}),
    ]


@dataclass
class FakeProvider:
    seen: list[tuple[str, dict[str, str], dict[str, Any]]] = field(default_factory=list[tuple[str, dict[str, str], dict[str, Any]]])

    async def openai(self, request: web.Request) -> web.StreamResponse:
        body = await request.json()
        self.seen.append((request.path, {k.lower(): v for k, v in request.headers.items()}, body))
        if body.get("stream"):
            return web.Response(body=_sse(_openai_chunks(body["model"])), content_type="text/event-stream")
        return web.json_response(_openai_completion(body["model"]))

    async def anthropic(self, request: web.Request) -> web.StreamResponse:
        body = await request.json()
        self.seen.append((request.path, {k.lower(): v for k, v in request.headers.items()}, body))
        payload = b"".join(f"event: {name}\ndata: {json.dumps(data)}\n\n".encode() for name, data in _anthropic_events())
        return web.Response(body=payload, content_type="text/event-stream")

    def app(self) -> web.Application:
        app = web.Application()
        app.router.add_post("/v1/chat/completions", self.openai)
        app.router.add_post("/openai/deployments/{deployment}/chat/completions", self.openai)
        app.router.add_post("/v1/messages", self.anthropic)
        return app


@pytest.fixture
async def provider() -> AsyncIterator[tuple[FakeProvider, str]]:
    fake = FakeProvider()
    server = TestServer(fake.app(), host="127.0.0.1")
    await server.start_server()
    yield fake, str(server.make_url("")).rstrip("/")
    await server.close()


@dataclass
class Served:
    rig: GatewayRig
    url: str
    token: str


async def _serve(rig: GatewayRig, transport: LLMTransport | None = None) -> tuple[TestServer, Served]:
    binding = rig.pin(transport=transport)
    server = TestServer(create_model_gateway_app(rig.gateway), host="127.0.0.1")
    await server.start_server()
    return server, Served(rig, str(server.make_url("")).rstrip("/"), rig.token(binding))


@pytest.fixture
async def served() -> AsyncIterator[Served]:
    server, out = await _serve(GatewayRig())
    yield out
    await server.close()


async def test_hs1_the_openai_sdk_talks_to_the_gateway(provider: tuple[FakeProvider, str]) -> None:
    import openai

    _, upstream = provider
    transport = OpenAITransport(api_key=ORG_KEY, model="gpt-4o", base_url=f"{upstream}/v1")
    server, out = await _serve(GatewayRig(), transport)
    try:
        client = openai.AsyncOpenAI(api_key=out.token, base_url=f"{out.url}/v1", max_retries=0)
        tools = [{"type": "function", "function": {"name": "write", "parameters": {"type": "object"}}}]
        complete = await client.chat.completions.create(
            model=ENGINE_MODEL_ID, messages=[{"role": "user", "content": "go"}], tools=tools  # type: ignore[arg-type, list-item]
        )
        assert complete.model == ENGINE_MODEL_ID
        assert complete.choices[0].message.tool_calls[0].function.arguments == '{"filePath":"a.txt"}'  # type: ignore[index]
        stream = await client.chat.completions.create(
            model=ENGINE_MODEL_ID,
            messages=[{"role": "user", "content": "go"}],
            tools=tools,  # type: ignore[arg-type]
            stream=True,
            stream_options={"include_usage": True},
        )
        names, arguments, text, usage = [], "", "", None
        async for chunk in stream:
            usage = chunk.usage or usage
            for choice in chunk.choices:
                text += choice.delta.content or ""
                for call in choice.delta.tool_calls or []:
                    if call.function and call.function.name:
                        names.append(call.function.name)
                    arguments += (call.function.arguments or "") if call.function else ""
        assert (text, names, json.loads(arguments)) == ("Writing files.", ["write"], {"filePath": "a.txt"})
        assert usage is not None and (usage.prompt_tokens, usage.completion_tokens) == (120, 30)
        models = await client.models.list()
        assert [m.id for m in models.data] == list(ROLE_MODEL_IDS.values())
        await client.close()
    finally:
        await server.close()


async def test_hs2_a_recorded_opencode_request_streams_sse(served: Served) -> None:
    served.rig.transport.events = RecordedTransport().events
    async with loopback_client() as client, client.stream(
        "POST",
        f"{served.url}/v1/chat/completions",
        json=OPENCODE_BODY,
        headers={**ENGINE_HEADERS, "Authorization": f"Bearer {served.token}"},
    ) as response:
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/event-stream")
        lines = [line async for line in response.aiter_lines() if line.startswith("data: ")]
    assert lines[-1] == "data: [DONE]"
    chunks = [json.loads(line[6:]) for line in lines[:-1]]
    assert assemble(chunks)["content"] == "hello"
    call = served.rig.transport.calls[0]
    assert call.stream and call.system == "You are a coding engine."
    assert [t.name for t in call.tools or []] == ["write"]


@pytest.mark.parametrize("family", ["openai", "azure", "anthropic"])
async def test_hs3_real_transports_see_no_engine_header_and_map_back(
    provider: tuple[FakeProvider, str], family: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake, upstream = provider
    transport: LLMTransport
    if family == "openai":
        transport = OpenAITransport(api_key=ORG_KEY, model="gpt-4o", base_url=f"{upstream}/v1")
    elif family == "azure":
        transport = AzureOpenAITransport(
            api_key=ORG_KEY, azure_endpoint=upstream, api_version="2024-10-21", deployment="dep-1", model="gpt-4o"
        )
    else:
        monkeypatch.setenv("ANTHROPIC_BASE_URL", upstream)
        transport = AnthropicTransport(api_key=ORG_KEY, model="claude-haiku-4-5")
    server, out = await _serve(GatewayRig(), transport)
    try:
        async with loopback_client() as client, client.stream(
            "POST",
            f"{out.url}/v1/chat/completions",
            json=OPENCODE_BODY,
            headers={**ENGINE_HEADERS, "Authorization": f"Bearer {out.token}"},
        ) as response:
            assert response.status_code == 200, await response.aread()
            lines = [line async for line in response.aiter_lines() if line.startswith("data: ")]
    finally:
        await server.close()
    assembled = assemble([json.loads(line[6:]) for line in lines[:-1]])
    assert assembled["content"] == "Writing files."
    assert assembled["tool_calls"] == [{"id": "call_a", "name": "write", "arguments": {"filePath": "a.txt"}}]
    assert assembled["finish"] == "tool_calls"
    assert assembled["usage"]["prompt_tokens"] == 120
    assert assembled["usage"]["prompt_tokens_details"]["cached_tokens"] == 100
    path, headers, body = fake.seen[0]
    expected_path = {"openai": "/v1/chat/completions", "azure": "/openai/deployments/dep-1/chat/completions", "anthropic": "/v1/messages"}
    assert path == expected_path[family]
    auth = headers.get("authorization", "") + headers.get("api-key", "") + headers.get("x-api-key", "")
    assert ORG_KEY in auth and out.token not in json.dumps(headers)
    tracking = {name for name in ENGINE_HEADERS if name != "user-agent"} | {"x-stainless-lang"}
    for name in tracking:
        if name.startswith("x-stainless"):
            assert headers.get(name) != ENGINE_HEADERS.get(name)
        else:
            assert name not in headers, name
    assert "opencode" not in headers.get("user-agent", "")
    assert body["model"] in ("gpt-4o", "claude-haiku-4-5")
    assert "ses_1" not in json.dumps(body)


@pytest.mark.parametrize(
    ("headers", "payload", "status", "code"),
    [
        ({}, OPENCODE_BODY, 401, "token_missing"),
        ({"Authorization": "Bearer junk"}, OPENCODE_BODY, 401, "token_malformed"),
        ({"x-api-key": "abc"}, OPENCODE_BODY, 403, "caller_credential_header"),
        ({"x-custom": "sk-ant-api03-" + "A" * 40}, OPENCODE_BODY, 403, "forbidden_credential"),
        ({}, {**OPENCODE_BODY, "model": "gpt-4o"}, 404, "model_not_granted"),
        ({}, {**OPENCODE_BODY, "n": 3}, 400, "unsupported_parameter"),
        ({}, "not json", 400, "invalid_chat_request"),
    ],
)
async def test_hs4_refusals_are_openai_errors(
    served: Served, headers: dict[str, str], payload: object, status: int, code: str
) -> None:
    send = headers if code.startswith("token_") else {"Authorization": f"Bearer {served.token}", **headers}
    async with loopback_client() as client:
        if isinstance(payload, str):
            response = await client.post(f"{served.url}/v1/chat/completions", content=payload, headers=send)
        else:
            response = await client.post(f"{served.url}/v1/chat/completions", json=payload, headers=send)
    assert response.status_code == status
    assert response.headers[REFUSAL_HEADER] == code
    error = response.json()["error"]
    assert error["code"] == code and error["type"] and error["message"]
    assert served.rig.transport.calls == []


async def test_hs4_budget_refusal_is_402(served: Served) -> None:
    from harness_core.budget.types import ZERO, Amount, RunAccountRef

    run = RunAccountRef("org-1", "run-1")
    everything = Amount(2_000_000, 0)
    held = await served.rig.ledger.reserve(run, "earlier", everything, minimum=ZERO, ttl_s=60)
    await served.rig.ledger.commit(held, everything)
    async with loopback_client() as client:
        response = await client.post(
            f"{served.url}/v1/chat/completions", json=OPENCODE_BODY, headers={"Authorization": f"Bearer {served.token}"}
        )
    assert (response.status_code, response.json()["error"]["code"]) == (402, "budget_exhausted")


async def test_hs5_health_needs_no_token(served: Served) -> None:
    async with loopback_client() as client:
        response = await client.get(f"{served.url}/healthz")
    assert (response.status_code, response.json()) == (200, {"ok": True})


async def test_hs6_unknown_paths_and_methods(served: Served) -> None:
    async with loopback_client() as client:
        assert (await client.get(f"{served.url}/v1/embeddings")).status_code == 404
        # E3b serves POST /v1/responses; a GET on it is a wrong method.
        assert (await client.get(f"{served.url}/v1/responses")).status_code == 405
        assert (await client.get(f"{served.url}/v1/chat/completions")).status_code == 405


def test_tracking_header_list_covers_what_the_tests_send() -> None:
    listed = {h.replace("*", "") for h in ENGINE_TRACKING_HEADERS}
    sent = {h for h in ENGINE_HEADERS if h != "x-egress-request-id"}
    assert all(any(s.startswith(name) for name in listed) for s in sent)

