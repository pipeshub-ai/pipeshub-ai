"""E3c BV-1..BV-3: Anthropic on Bedrock and on Vertex, against fake endpoints (no keys for either here).

The SDK's own cloud clients do the signing (SigV4) and the token exchange (a
JWT-bearer assertion); the fakes refuse what the real services refuse, so a
request that reaches the answer was signed with the account's key.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

import pytest
from aiohttp.test_utils import TestServer

from harness_core.kernel.core.messages import UserMessage
from harness_core.kernel.core.streaming import StreamCompleteEvent, TextDeltaEvent
from harness_core.models.transports.anthropic_cloud import (
    anthropic_on_bedrock,
    anthropic_on_vertex,
    bedrock_anthropic_model,
    vertex_anthropic_model,
    vertex_base_url,
)
from harness_core.testing.cloud_providers import (
    ACCESS_TOKEN,
    FakeBedrock,
    FakeVertex,
    service_account,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

KEY_ID = "AKIA" + "E3CTESTKEYID0000"
SECRET = "e3c/test+secret" + "S" * 25
REGION = "us-east-1"
BEDROCK_MODEL = "us.anthropic.claude-sonnet-4-6-v1:0"
VERTEX_MODEL = "claude-sonnet-4-6@20250929"


@pytest.fixture
async def bedrock() -> AsyncIterator[tuple[FakeBedrock, str]]:
    fake = FakeBedrock(KEY_ID, REGION)
    server = TestServer(fake.app(), host="127.0.0.1")
    await server.start_server()
    yield fake, str(server.make_url("")).rstrip("/")
    await server.close()


@pytest.fixture
async def vertex() -> AsyncIterator[tuple[FakeVertex, str, str]]:
    probe = service_account("pending")
    fake = FakeVertex(probe.public_key_pem, probe.client_email)
    server = TestServer(fake.app(), host="127.0.0.1")
    await server.start_server()
    url = str(server.make_url("")).rstrip("/")
    fake.token_uri = f"{url}/token"
    # The account file names another token endpoint: it must never be called.
    account_json = json.dumps({**json.loads(probe.json), "token_uri": "http://127.0.0.1:9/stolen-assertions"})
    yield fake, url, account_json
    await server.close()


async def _stream_text(transport: object) -> tuple[str, StreamCompleteEvent]:
    text, final = "", None
    async for event in transport.stream([UserMessage(content="hi")], system="be brief"):  # type: ignore[attr-defined]
        if isinstance(event, TextDeltaEvent):
            text += event.delta
        elif isinstance(event, StreamCompleteEvent):
            final = event
    assert final is not None
    return text, final


async def test_bv1_bedrock_complete_and_stream_are_sigv4_signed(bedrock: tuple[FakeBedrock, str]) -> None:
    fake, url = bedrock
    transport = anthropic_on_bedrock(
        region=REGION, access_key_id=KEY_ID, secret_access_key=SECRET, model=BEDROCK_MODEL, base_url=url
    )
    assert transport.provider == "anthropic" and transport.model_name == BEDROCK_MODEL
    whole = await transport.complete([UserMessage(content="hi")], system="be brief")
    assert whole.message.text == "hello from the cloud"
    assert (whole.usage.input_tokens, whole.usage.output_tokens) == (12, 7)
    text, final = await _stream_text(transport)
    assert text == "hello from the cloud" and final.response.usage.output_tokens == 7
    assert [s.path for s in fake.seen] == [
        f"/model/{BEDROCK_MODEL}/invoke",
        f"/model/{BEDROCK_MODEL}/invoke-with-response-stream",
    ]
    for seen in fake.seen:
        assert seen.headers["authorization"].startswith(f"AWS4-HMAC-SHA256 Credential={KEY_ID}/")
        assert f"/{REGION}/bedrock/aws4_request" in seen.headers["authorization"]
        assert seen.body["anthropic_version"] == "bedrock-2023-05-31" and "model" not in seen.body
        assert SECRET not in json.dumps(seen.headers) and SECRET not in json.dumps(seen.body)


async def test_bv1_a_wrong_key_is_refused_by_the_fake(bedrock: tuple[FakeBedrock, str]) -> None:
    from harness_core.kernel.core.exceptions import TransportError

    _, url = bedrock
    transport = anthropic_on_bedrock(
        region=REGION, access_key_id="AKIA" + "Z" * 16, secret_access_key=SECRET, model=BEDROCK_MODEL, base_url=url
    )
    transport._client = transport._client.with_options(max_retries=0)  # pyright: ignore[reportPrivateUsage]
    with pytest.raises(TransportError) as failed:
        await transport.complete([UserMessage(content="hi")])
    assert failed.value.status_code == 403


async def test_bv2_bv3_vertex_exchanges_a_signed_assertion_at_the_pinned_endpoint(
    vertex: tuple[FakeVertex, str, str],
) -> None:
    fake, url, account_json = vertex
    transport = anthropic_on_vertex(
        project_id="proj-e3c-test",
        location="us-east5",
        service_account_json=account_json,
        model=VERTEX_MODEL,
        base_url=f"{url}/v1",
        token_uri=fake.token_uri,
    )
    whole = await transport.complete([UserMessage(content="hi")], system="be brief")
    assert whole.message.text == "hello from the cloud"
    text, _ = await _stream_text(transport)
    assert text == "hello from the cloud"
    base = "/v1/projects/proj-e3c-test/locations/us-east5/publishers/anthropic/models"
    assert [s.path for s in fake.seen] == [f"{base}/{VERTEX_MODEL}:rawPredict", f"{base}/{VERTEX_MODEL}:streamRawPredict"]
    for seen in fake.seen:
        assert seen.headers["authorization"] == f"Bearer {ACCESS_TOKEN}"
        assert seen.body["anthropic_version"] == "vertex-2023-10-16" and "model" not in seen.body
        assert "PRIVATE KEY" not in json.dumps(seen.body)
    # One exchange (the token is cached), at the pinned endpoint: the account file's own token_uri was never used.
    assert fake.token_requests == 1


def test_bv2_vertex_hosts_follow_the_location() -> None:
    assert vertex_base_url("us-east5") == "https://us-east5-aiplatform.googleapis.com/v1"
    assert vertex_base_url("global") == "https://aiplatform.googleapis.com/v1"
    assert vertex_base_url("eu") == "https://aiplatform.eu.rep.googleapis.com/v1"


@pytest.mark.parametrize(
    ("model", "named"),
    [
        ("anthropic.claude-sonnet-4-6", "claude-sonnet-4-6"),
        ("us.anthropic.claude-sonnet-4-6-v1:0", "claude-sonnet-4-6"),
        ("anthropic.claude-3-5-sonnet-20240620-v1:0", "claude-3-5-sonnet"),
        ("global.anthropic.claude-opus-4-8-v1:0", "claude-opus-4-8"),
        ("meta.llama3-70b-instruct-v1:0", None),
        ("amazon.nova-pro-v1:0", None),
    ],
)
def test_bv4_bedrock_model_ids_name_their_anthropic_model(model: str, named: str | None) -> None:
    assert bedrock_anthropic_model(model) == named


@pytest.mark.parametrize(
    ("model", "named"),
    [("claude-sonnet-4-6", "claude-sonnet-4-6"), ("claude-sonnet-4-5@20250929", "claude-sonnet-4-5"), ("gemini-2.5-pro", None)],
)
def test_bv4_vertex_model_ids_name_their_anthropic_model(model: str, named: str | None) -> None:
    assert vertex_anthropic_model(model) == named
