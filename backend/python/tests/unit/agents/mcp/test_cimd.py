"""PipesHub's OAuth Client ID Metadata Document (`app.agents.mcp.cimd`)."""
from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from app.agents.mcp import cimd
from app.agents.mcp.errors import MCPUrlBlockedError
from app.agents.mcp.models import DiscoveredOAuthMetadata
from app.api.routes.mcp_servers import _get_configured_frontend_base_url
from tests.unit.api.routes.mcp_route_fakes import FakeConfigService

URL = "https://pipeshub.example.com/mcp-servers/oauth/client-metadata.json"
REDIRECT = "https://pipeshub.example.com/mcp-servers/oauth/callback/"
DOCUMENT = {"client_id": URL, "client_name": "PipesHub", "redirect_uris": [REDIRECT], "token_endpoint_auth_method": "none"}

# The same table is in the Node test of the route that serves the document
# (`tests/modules/mcp_servers/routes/mcp_client_metadata.test.ts`): both must build one URL.
SAME_URL_AS_NODE = [
    ("https://pipeshub.example.com", "https://pipeshub.example.com/mcp-servers/oauth/client-metadata.json"),
    ("https://pipeshub.example.com/", "https://pipeshub.example.com/mcp-servers/oauth/client-metadata.json"),
    ("https://pipeshub.example.com//", "https://pipeshub.example.com/mcp-servers/oauth/client-metadata.json"),
    (" https://example.com/pipeshub/ ", "https://example.com/pipeshub/mcp-servers/oauth/client-metadata.json"),
]


@pytest.fixture(autouse=True)
def _no_remembered_checks(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cimd, "_checks", {})


class TestTheClientId:
    @pytest.mark.parametrize("configured,client_id", SAME_URL_AS_NODE)
    async def test_built_from_the_public_address_as_the_node_route_builds_it(self, configured: str, client_id: str) -> None:
        config = FakeConfigService({"/services/endpoints": {"frontend": {"publicEndpoint": configured}}})
        assert cimd.client_metadata_url(await _get_configured_frontend_base_url(config)) == client_id

    @pytest.mark.parametrize("client_id,expected", [
        (URL, True),
        ("https://example.com/sub/mcp-servers/oauth/client-metadata.json", True),
        ("http://pipeshub.example.com/mcp-servers/oauth/client-metadata.json", False),
        ("https://pipeshub.example.com/other.json", False),
        ("dcr-abc123", False),
        (None, False),
    ])
    def test_recognised_as_a_document(self, client_id: Any, expected: bool) -> None:  # noqa: ANN401
        assert cimd.is_client_metadata_url(client_id) is expected

    @pytest.mark.parametrize("value,expected", [(None, True), ("true", True), ("false", False), ("FALSE", False)])
    def test_switched_off_only_on_purpose(self, monkeypatch: pytest.MonkeyPatch, value: str | None, expected: bool) -> None:
        if value is None:
            monkeypatch.delenv(cimd.ENABLED_ENV, raising=False)
        else:
            monkeypatch.setenv(cimd.ENABLED_ENV, value)
        assert cimd.enabled() is expected

    def test_supported_only_when_the_server_says_so(self) -> None:
        assert cimd.supported_by(DiscoveredOAuthMetadata(client_id_metadata_document_supported=True))
        assert not cimd.supported_by(DiscoveredOAuthMetadata())
        assert not cimd.supported_by(None)


class TestARefusalIsRemembered:
    SERVER = DiscoveredOAuthMetadata(issuer="https://auth.example.com", authorization_endpoint="https://auth.example.com/a")

    def test_by_the_servers_issuer_then_its_endpoints(self) -> None:
        assert cimd.server_key(self.SERVER) == "https://auth.example.com"
        assert cimd.server_key(DiscoveredOAuthMetadata(token_endpoint="https://auth.example.com/t")) == "https://auth.example.com/t"

    async def test_for_a_while(self) -> None:
        config = FakeConfigService()
        assert not await cimd.refused_by(config, "https://auth.example.com")

        await cimd.remember_refusal(config, "https://auth.example.com")

        assert await cimd.refused_by(config, "https://auth.example.com")
        assert not await cimd.refused_by(config, "https://other.example.com")
        (key,) = config.writes
        assert config.ttls[key] == 7 * 24 * 3600
        assert "auth.example.com" not in key

    async def test_without_a_server_nothing_is_written(self) -> None:
        config = FakeConfigService()
        await cimd.remember_refusal(config, "")
        assert config.writes == []

    async def test_a_failed_write_is_only_logged(self, caplog: pytest.LogCaptureFixture) -> None:
        config = MagicMock()
        config.set_config = AsyncMock(side_effect=RuntimeError("etcd is down"))
        await cimd.remember_refusal(config, "https://auth.example.com")
        assert "Couldn't remember" in caplog.text


def _serving(handler: Any) -> Any:  # noqa: ANN401
    """The document fetched from `handler` instead of the network; the address check passes."""
    return (
        patch.object(cimd, "PerOriginGuardedTransport", return_value=httpx.MockTransport(handler)),
        patch.object(cimd, "assert_mcp_url_allowed", new=AsyncMock()),
    )


def _json(body: object, *, status: int = 200, content_type: str = "application/json") -> Any:  # noqa: ANN401
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, headers={"content-type": content_type}, content=json.dumps(body).encode())

    return handler


class TestTheSelfCheck:
    async def _check(self, handler: Any) -> bool:  # noqa: ANN401
        transport, guard = _serving(handler)
        with transport, guard:
            return await cimd.document_is_served(URL, REDIRECT)

    async def test_the_document_as_node_serves_it_passes(self) -> None:
        assert await self._check(_json(DOCUMENT)) is True

    @pytest.mark.parametrize("handler", [
        _json({**DOCUMENT, "client_id": "https://elsewhere.example/doc.json"}),
        _json({**DOCUMENT, "redirect_uris": ["https://pipeshub.example.com/other/"]}),
        _json({**DOCUMENT, "redirect_uris": REDIRECT}),
        _json([DOCUMENT]),
        _json(DOCUMENT, content_type="text/html"),
        _json(DOCUMENT, status=404),
        lambda _request: httpx.Response(302, headers={"location": "https://pipeshub.example.com/login"}),
        lambda _request: httpx.Response(200, headers={"content-type": "application/json"}, content=b"{" + b" " * 70_000),
        lambda _request: httpx.Response(200, headers={"content-type": "application/json"}, content=b"not json"),
    ], ids=["another-client-id", "redirect-missing", "redirects-not-a-list", "not-an-object", "html", "404",
            "redirected", "too-big", "not-json"])
    async def test_anything_else_fails(self, handler: Any) -> None:  # noqa: ANN401
        assert await self._check(handler) is False

    async def test_a_private_address_fails_before_any_request(self) -> None:
        requests: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            requests.append(request)
            return httpx.Response(200)

        with patch.object(cimd, "PerOriginGuardedTransport", return_value=httpx.MockTransport(handler)), \
             patch.object(cimd, "assert_mcp_url_allowed", new=AsyncMock(side_effect=MCPUrlBlockedError("private"))) as guard:
            assert await cimd.document_is_served(URL, REDIRECT) is False
        assert guard.await_args.kwargs == {"allow_private": False}
        assert requests == []

    async def test_the_answer_is_remembered_a_failure_too(self, monkeypatch: pytest.MonkeyPatch) -> None:
        fetch = AsyncMock(return_value=False)
        monkeypatch.setattr(cimd, "_fetch_and_check", fetch)

        assert await cimd.document_is_served(URL, REDIRECT) is False
        assert await cimd.document_is_served(URL, REDIRECT) is False
        assert fetch.await_count == 1
        # Ten minutes later it is checked again.
        checked_at, served = cimd._checks[(URL, REDIRECT)]
        cimd._checks[(URL, REDIRECT)] = (checked_at - 601, served)
        fetch.return_value = True
        assert await cimd.document_is_served(URL, REDIRECT) is True
        assert fetch.await_count == 2
