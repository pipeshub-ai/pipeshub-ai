"""Save-time and OAuth-callback SSRF checks in `app.api.routes.mcp_servers`."""
from __future__ import annotations

from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException

from app.agents.mcp.models import (
    MCPAuthMode,
    MCPServerInstanceConfig,
    MCPTransport,
    OAuthTokens,
)
from app.api.routes import mcp_servers
from app.api.routes.mcp_servers import (
    create_instance,
    handle_oauth_callback,
    update_instance,
)
from app.utils.time_conversion import get_epoch_timestamp_in_ms
from tests.unit.api.routes.mcp_route_fakes import FakeConfigService, route_request

if TYPE_CHECKING:
    from collections.abc import Iterator


@pytest.fixture(autouse=True)
def _caller_is_admin() -> "Iterator[None]":
    with patch.object(mcp_servers, "_check_user_is_admin", new=AsyncMock(return_value=True)):
        yield


def _payload(**overrides: Any) -> MCPServerInstanceConfig:  # noqa: ANN401
    fields: dict[str, Any] = {
        "name": "Custom", "transport": MCPTransport.STREAMABLE_HTTP, "auth_mode": MCPAuthMode.NONE,
        "url": "https://mcp.example.com/mcp",
    }
    fields.update(overrides)
    return MCPServerInstanceConfig(**fields)


async def _assert_bad_request(coro: Any) -> None:  # noqa: ANN401
    with pytest.raises(HTTPException) as exc:
        await coro
    assert exc.value.status_code == 400
    assert "not allowed" in exc.value.detail


class TestSaveTimeUrlCheck:

    @pytest.fixture(autouse=True)
    def _custom_stdio_on(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("MCP_ALLOW_CUSTOM_STDIO", "true")

    async def test_create_with_a_loopback_url_is_rejected(self) -> None:
        store = FakeConfigService()

        await _assert_bad_request(create_instance(route_request(store), _payload(url="http://127.0.0.1:9000/mcp")))

        assert store.writes == []

    async def test_create_with_a_metadata_token_url_is_rejected(self) -> None:
        store = FakeConfigService()
        payload = _payload(auth_mode=MCPAuthMode.OAUTH, token_url="http://169.254.169.254/latest/meta-data")

        await _assert_bad_request(create_instance(route_request(store), payload))

        assert store.writes == []

    async def test_update_to_a_link_local_url_is_rejected_and_the_record_is_unchanged(self) -> None:
        original = {
            "_id": "inst-1", "orgId": "org-1", "createdBy": "admin-1", "name": "Custom",
            "transport": "streamable_http", "authMode": "none", "url": "https://mcp.example.com/mcp",
            "isCustom": True, "createdAt": 1, "updatedAt": 1,
        }
        store = FakeConfigService({"/services/mcp/instances/inst-1": dict(original)})

        await _assert_bad_request(update_instance(route_request(store), "inst-1", _payload(url="http://[fe80::1]/mcp")))

        assert store.data["/services/mcp/instances/inst-1"] == original

    async def test_create_with_a_public_url_is_saved_without_a_dns_lookup(self) -> None:
        store = FakeConfigService()

        with patch("socket.getaddrinfo", side_effect=AssertionError("no DNS at save time")):
            record = await create_instance(route_request(store), _payload())

        assert store.data[f"/services/mcp/instances/{record['_id']}"]["url"] == "https://mcp.example.com/mcp"

    async def test_stdio_instances_are_not_url_checked(self) -> None:
        store = FakeConfigService()
        payload = _payload(transport=MCPTransport.STDIO, command="npx", url="http://localhost/ignored")

        record = await create_instance(route_request(store), payload)

        assert record["transport"] == "stdio"


class TestOAuthCallbackUsesTheInstancePolicy:
    @pytest.mark.parametrize(
        "token_url,expected",
        [
            ("http://mcp.internal:8443/token", True),  # the host the instance was configured with
            ("http://10.0.3.7/token", False),  # named only by the server's metadata
        ],
    )
    async def test_a_private_token_url_is_allowed_only_on_a_configured_host(
        self, monkeypatch: pytest.MonkeyPatch, token_url: str, expected: bool,
    ) -> None:
        monkeypatch.delenv("MCP_ALLOW_PRIVATE_NETWORK_URLS", raising=False)
        store = FakeConfigService({
            "/services/mcp/instances/inst-1": {
                "_id": "inst-1", "orgId": "org-1", "authMode": "oauth", "url": "http://mcp.internal/mcp",
            },
            "/services/mcp/oauth-states/s1": {
                "instanceId": "inst-1", "userId": "admin-1", "orgId": "org-1", "initiatedBy": "admin-1",
                "clientId": "client", "isDcr": False, "tokenUrl": token_url,
                "redirectUri": "https://app/cb", "expiresAt": get_epoch_timestamp_in_ms() + 60_000,
            },
        })
        exchange = AsyncMock(return_value=OAuthTokens(access_token="a"))

        with (
            patch.object(mcp_servers, "_resolve_oauth_client_secret", new=AsyncMock(return_value="secret")),
            patch.object(mcp_servers.oauth_client_module, "exchange_code_for_token", new=exchange),
            patch(
                "app.connectors.core.base.token_service.startup_service.startup_service.get_mcp_token_refresh_service",
                return_value=None,
            ),
        ):
            await handle_oauth_callback(route_request(store), code="c", state="s1", error=None)

        assert exchange.await_args.kwargs["allow_private"] is expected
