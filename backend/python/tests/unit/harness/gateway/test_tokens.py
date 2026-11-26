"""TS-1, TS-2 and the configured key source: tokens from Node, keys from configuration."""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING, Any

import httpx
import pytest

from app.config.constants.service import config_node_constants
from app.harness.gateway.keys import ConfiguredGatewayKeys
from app.harness.gateway.tokens import (
    GatewayTokenUnavailable,
    NodeGatewayTokens,
)
from harness_core.testing.contracts.gateway import GatewayKeySourceContract

if TYPE_CHECKING:
    from harness_core.gateway.ports import PublishedKeys

TOKEN = "eyJhbGciOiJFZERTQSJ9.e30.c2lnbmF0dXJl-gateway-token-value"


class Credential:
    async def worker_credential(self) -> str:
        return "worker-credential"


def _node(handler: Any) -> NodeGatewayTokens:  # noqa: ANN401 - an httpx MockTransport handler
    client = httpx.AsyncClient(base_url="http://node.internal", transport=httpx.MockTransport(handler))
    return NodeGatewayTokens(base_url="http://node.internal", credentials=Credential(), http_client=client)


async def test_ts1_durable_issuance_presents_the_worker_credential_and_the_attempt(
    caplog: pytest.LogCaptureFixture,
) -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"token": TOKEN, "expiresAt": 1_790_000_600_000})

    with caplog.at_level(logging.INFO):
        issued = await _node(handler).for_attempt("org-1", "run/1", "run-1.a2", "v1.proof")
    (request,) = seen
    assert request.url.raw_path == b"/api/v1/agent-harness/internal/runs/run%2F1/gateway-token"
    assert request.headers["authorization"] == "Bearer worker-credential"
    # Node's route body is strict and needs all three (C-13 found the org missing against the real route).
    assert json.loads(request.content) == {"orgId": "org-1", "attemptId": "run-1.a2", "bindingProof": "v1.proof"}
    assert issued.value == TOKEN and int(issued.expires_at.timestamp()) == 1_790_000_600
    assert TOKEN not in repr(issued) and TOKEN not in caplog.text


@pytest.mark.parametrize(
    ("response", "reason"),
    [
        (httpx.Response(409, json={"error": {"code": "HTTP_AGENT_HARNESS_RUN_NOT_LEASED"}}), "RUN_NOT_LEASED"),
        (httpx.Response(401, json={"error": {"code": "AGENT_HARNESS_WORKER_CREDENTIAL"}}), "WORKER_CREDENTIAL"),
        (httpx.Response(503, text="down"), "http_503"),
    ],
)
async def test_ts1_a_refusal_is_token_unavailable(response: httpx.Response, reason: str) -> None:
    with pytest.raises(GatewayTokenUnavailable) as refused:
        await _node(lambda request: response).for_attempt("org-1", "run-1", "run-1.a1", "v1.proof")
    assert refused.value.reason == reason


class FakeConfiguration:
    def __init__(self) -> None:
        self.values: dict[str, object] = {}
        self.reads = 0

    async def get_config(self, key: str, *args: object, **kwargs: object) -> object:
        self.reads += 1
        return self.values.get(key)


def _document(keys: PublishedKeys) -> dict[str, object]:
    """What Node's ``EncryptedConfigDocumentStore`` holds, as ``ConfigurationService`` returns it."""
    return {
        "issuer": keys.issuer,
        "audience": keys.audience,
        "keys": [
            {"kid": k.kid, "alg": "EdDSA", "publicKey": k.public_key_pem, "retiredAt": k.retired_at_ms}
            for k in keys.keys
        ],
    }


class ConfiguredKeys:
    def __init__(self) -> None:
        self.config = FakeConfiguration()
        self.source = ConfiguredGatewayKeys(self.config)  # type: ignore[arg-type]

    async def publish(self, keys: PublishedKeys) -> None:
        self.config.values[config_node_constants.AGENT_HARNESS_GATEWAY_PUBLIC_KEYS.value] = _document(keys)


class TestConfiguredGatewayKeys(GatewayKeySourceContract):
    @pytest.fixture
    def key_harness(self) -> ConfiguredKeys:
        return ConfiguredKeys()


async def test_configured_keys_are_cached_until_a_refresh() -> None:
    harness = ConfiguredKeys()
    await harness.source.keys()
    await harness.source.keys()
    assert harness.config.reads == 1
    await harness.source.keys(refresh=True)
    assert harness.config.reads == 2


async def test_eg4_the_proxy_token_source_asks_node_for_the_bound_attempt(caplog: pytest.LogCaptureFixture) -> None:
    from app.harness.gateway.tokens import NodeGatewayTokenSource
    from harness_core.sandbox.egress.types import EgressBinding
    from harness_core.sandbox.types import TokenRoute

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        assert body["orgId"] == "o1"
        if body["attemptId"] == "run-1.a1":
            return httpx.Response(409, json={"error": {"code": "RUN_NOT_LEASED"}})
        return httpx.Response(200, json={"token": TOKEN, "expiresAt": 1_790_000_600_000})

    source = NodeGatewayTokenSource(_node(handler))
    route = TokenRoute("pxh-tools-0123456789abcdef", "tool-gateway", "gateway:tools")

    def bound(**labels: str) -> EgressBinding:
        return EgressBinding("sbx-1", "10.213.0.2", "10.213.0.3", labels={"orgId": "o1", **labels}, attestation="v1.p")

    with caplog.at_level(logging.DEBUG):
        issued = await source.issue(route, bound(runId="run-1", attemptId="run-1.a2"))
    assert issued.value == TOKEN
    assert TOKEN not in caplog.text
    from harness_core.sandbox.egress.errors import TokenUnavailable

    with pytest.raises(TokenUnavailable):
        await source.issue(route, bound(runId="run-1", attemptId="run-1.a1"))
    with pytest.raises(TokenUnavailable):
        await source.issue(route, bound(runId="run-1"))
    without_org = EgressBinding("sbx-1", "10.213.0.2", "10.213.0.3", labels={"runId": "run-1", "attemptId": "run-1.a2"})
    with pytest.raises(TokenUnavailable):
        await source.issue(route, without_org)


async def test_sr_10_a_sandbox_bound_to_a_request_gets_no_token_and_node_is_not_asked() -> None:
    from app.harness.gateway.tokens import NodeGatewayTokenSource
    from harness_core.sandbox.egress.errors import TokenUnavailable
    from harness_core.sandbox.egress.types import EgressBinding
    from harness_core.sandbox.types import TokenRoute

    asked: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        asked.append(request)
        return httpx.Response(200, json={"token": TOKEN, "expiresAt": 1_790_000_600_000})

    source = NodeGatewayTokenSource(_node(handler))
    route = TokenRoute("pxh-tools-0123456789abcdef", "tool-gateway", "gateway:tools")
    stage1 = EgressBinding("sbx-1", "10.213.0.2", "10.213.0.3", labels={"orgId": "o1", "requestId": "req-1", "userId": "u1"})
    with pytest.raises(TokenUnavailable, match="names no run attempt"):
        await source.issue(route, stage1)
    assert asked == []


async def test_sf6_a_kept_sign_in_sandbox_gets_the_token_of_the_attempt_it_is_bound_to_now() -> None:
    """SIGNFIX: a kept sandbox's binding names its attempt under the bound keys (its labels carry no run id);
    the proxy's token source asks Node for that attempt instead of refusing ``token_unavailable``."""
    from app.harness.gateway.tokens import NodeGatewayTokenSource
    from harness_core.engines.attempt_labels import bound_labels
    from harness_core.sandbox.egress.types import EgressBinding
    from harness_core.sandbox.types import TokenRoute

    asked: list[tuple[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        asked.append((request.url.path.rsplit("/", 2)[-2], json.loads(request.content)["attemptId"]))
        return httpx.Response(200, json={"token": TOKEN, "expiresAt": 1_790_000_600_000})

    source = NodeGatewayTokenSource(_node(handler))
    route = TokenRoute("pxh-tools-0123456789abcdef", "tool-gateway", "gateway:tools")
    kept = {"orgId": "o1", "sessionId": "s1", "pipeshub.signin.kept": "1", **bound_labels("run-2", "run-2.a1")}
    issued = await source.issue(route, EgressBinding("sbx-1", "10.213.0.2", "10.213.0.3", labels=kept, attestation="v1.p"))
    assert issued.value == TOKEN
    assert asked == [("run-2", "run-2.a1")]
