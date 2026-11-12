"""Token-refresh failures as the real code raises them, for the tests of everything that reports them."""
from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.agents.mcp import oauth_client as oauth_client_module
from app.agents.mcp import token_refresh
from app.agents.mcp.oauth_client import MCPOAuthError, refresh_access_token
from tests.support.mcp_oauth_fakes import json_response, mock_async_client
from tests.unit.api.routes.mcp_route_fakes import FakeConfigService

# Planted in token-endpoint bodies: what nobody but the server log may see.
BODY_SECRET = "trace-id-s3cr3t"
TOKEN_HOST = "auth.example.com"


async def _refresh_raising(post: AsyncMock) -> BaseException:
    inner = MagicMock()
    inner.post = post
    with mock_async_client(inner), pytest.raises(Exception) as exc_info:
        await refresh_access_token(
            allow_private=True, token_url=f"https://{TOKEN_HOST}/token",
            client_id="cid", client_secret="csec", refresh_token="r",
        )
    return exc_info.value


async def token_endpoint_error(status: int, body: dict[str, Any]) -> MCPOAuthError:
    """What `refresh_access_token` raises when the token endpoint answers `status` with `body`."""
    error = await _refresh_raising(AsyncMock(return_value=json_response(status, body)))
    assert isinstance(error, MCPOAuthError)
    return error


async def token_request_error(error: Exception) -> BaseException:
    """What escapes `refresh_access_token` when the request itself fails (DNS, refused, timeout)."""
    return await _refresh_raising(AsyncMock(side_effect=error))


async def lock_contention_error() -> token_refresh.MCPTokenRefreshError:
    """What `refresh_credential_record` raises when another process never lets go of the refresh lock."""
    store = FakeConfigService({
        "/services/mcp/credentials/inst-1/user-1": {
            "isAuthenticated": True,
            "oauthTokens": {"accessToken": "old", "refreshToken": "r", "tokenUrl": f"https://{TOKEN_HOST}/token"},
        },
        "/services/mcp/dcr-clients/inst-1": {"clientId": "client"},
        "/services/mcp/refresh-locks/inst-1/user-1": {"owner": "another-replica"},
    })
    endpoint = AsyncMock()
    with patch.object(oauth_client_module, "refresh_access_token", new=endpoint), \
         patch.object(token_refresh, "CLUSTER_LOCK_WAIT_SECONDS", 0.05), \
         patch.object(token_refresh, "_CLUSTER_LOCK_POLL_SECONDS", 0.01), \
         pytest.raises(token_refresh.MCPTokenRefreshError) as exc_info:
        await token_refresh.refresh_credential_record("inst-1", "user-1", store)
    endpoint.assert_not_awaited()
    return exc_info.value
