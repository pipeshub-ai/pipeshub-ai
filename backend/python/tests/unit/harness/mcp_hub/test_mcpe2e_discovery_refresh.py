"""MCPE2E-F5: an expired OAuth access token is refreshed when a request lists an MCP server's tools.

A call to a tool already refreshed the token once on a 401 and retried. Listing the tools at the start of a request
did not: a server that refused the stored token left the request without the server's tools (today's loop) or held
a durable run in ``capability_wait`` until it failed, although the stored refresh token was good. The server here is
the e2e lane's real MCP server with its OAuth provider, on a loopback socket (main's URL guard refuses loopback, so the
test approves 127.0.0.1 in its resolution step); the token exchange and refresh are the product's own.
"""

from __future__ import annotations

import base64
import hashlib
from typing import TYPE_CHECKING, Any
from urllib.parse import parse_qs, urlparse

import httpx
import pytest

from app.agent_loop_lib.tools.registry import ToolRegistry
from app.agents.agent_loop.mcp_session import close_request_sessions
from app.agents.agent_loop.mcp_tool_loader import MCPToolProvider
from app.agents.constants.mcp_server_constants import (
    get_mcp_credentials_path,
    get_mcp_oauth_client_config_path,
)
from app.agents.mcp import oauth_client
from tests.unit.agents.adapter.conftest import make_context
from tests.unit.harness.credentials.support import DictConfig
from tests.unit.harness.mcp_hub.kinds_server import (
    CONTROL,
    loopback_allowed,
    make,
    serving,
    view,
)

if TYPE_CHECKING:
    from collections.abc import Iterator

    from app.agents.agent_loop.context import AgentContext

INSTANCE, OWNER = "mcp-oauth-1", "user-1"
CLIENT, SECRET = "app-1", "sec-f5"
REDIRECT = "http://127.0.0.1:1/mcp-servers/oauth/callback/"
VERIFIER = "v" * 50


@pytest.fixture(autouse=True)
def _loopback() -> Iterator[None]:
    with loopback_allowed():
        yield


@pytest.fixture(scope="module")
def base() -> Iterator[str]:
    with serving() as url:
        yield url


async def _connected(base: str, server_id: str, config: DictConfig) -> dict[str, Any]:
    """An OAuth instance the user connected: its tokens and client stored as the product's routes store them."""
    _ = make(base, server_id, auth="oauth")
    _ = httpx.post(f"{base}/__control/servers/{server_id}/oauth/clients", headers=CONTROL,
                   json={"clientId": CLIENT, "clientSecret": SECRET})
    challenge = base64.urlsafe_b64encode(hashlib.sha256(VERIFIER.encode()).digest()).decode().rstrip("=")
    redirect = httpx.get(f"{base}/oauth/{server_id}/authorize", params={
        "response_type": "code", "client_id": CLIENT, "redirect_uri": REDIRECT, "state": "s",
        "code_challenge": challenge, "code_challenge_method": "S256"}, follow_redirects=False)
    code = parse_qs(urlparse(redirect.headers["location"]).query)["code"][0]
    tokens = await oauth_client.exchange_code_for_token(
        f"{base}/oauth/{server_id}/token", CLIENT, SECRET, code, REDIRECT, VERIFIER, allow_private=True
    )
    auth = {"isAuthenticated": True, "authMode": "oauth", "oauthTokens": tokens.model_dump(by_alias=True, mode="json")}
    config.values[get_mcp_credentials_path(INSTANCE, OWNER)] = auth
    config.values[get_mcp_oauth_client_config_path(INSTANCE)] = {"clientId": CLIENT, "clientSecret": SECRET}
    return auth


def _context(base: str, server_id: str, config: DictConfig, auth: dict[str, Any]) -> AgentContext:
    instance = {"_id": INSTANCE, "orgId": "org-1", "createdBy": "admin-1", "name": "Notes", "transport": "streamable_http",
                "authMode": "oauth", "url": f"{base}/s/{server_id}/mcp", "isCustom": True, "createdAt": 0, "updatedAt": 0}
    return make_context(
        config_service=config, user_id=OWNER,
        mcp_servers=[{"instanceId": INSTANCE, "name": "Notes", "displayName": "Notes", "tools": None}],
        mcp_server_configs={INSTANCE: {"instance": instance, "auth": auth, "ownerId": OWNER}}, run_id="run-1",
    )


async def _loaded(ctx: AgentContext) -> set[str]:
    registry = ToolRegistry()
    await MCPToolProvider().load_into(registry, ctx)
    return {name for name in registry.names() if name.startswith("mcp_")}


async def test_f5_a_refused_token_is_refreshed_once_and_the_tools_load(base: str) -> None:
    config = DictConfig()
    auth = await _connected(base, "f5-expired", config)
    assert httpx.post(f"{base}/__control/servers/f5-expired/oauth/expire", headers=CONTROL).json() == {"expired": 1}
    ctx = _context(base, "f5-expired", config, auth)
    assert await _loaded(ctx) == {"mcp_notes_whoami"}
    assert ctx.mcp_tool_load_failures == []
    seen = view(base, "f5-expired")
    assert [i["grant"] for i in seen["oauth"]["issued"]] == ["authorization_code", "refresh_token"]
    # The refreshed tokens are the stored ones: the next request starts from them.
    stored = config.values[get_mcp_credentials_path(INSTANCE, OWNER)]["oauthTokens"]
    assert stored["accessToken"] != auth["oauthTokens"]["accessToken"]
    _ = await close_request_sessions(ctx)


async def test_f5_a_refresh_the_provider_rejects_leaves_the_server_out_with_its_reason(base: str) -> None:
    config = DictConfig()
    auth = await _connected(base, "f5-revoked", config)
    _ = httpx.post(f"{base}/__control/servers/f5-revoked/oauth/expire", headers=CONTROL)
    # The refresh token is spent too (the provider rotates it): nothing the product can do but ask to reconnect.
    spent = auth["oauthTokens"]["refreshToken"]
    _ = await oauth_client.refresh_access_token(
        f"{base}/oauth/f5-revoked/token", CLIENT, SECRET, spent, allow_private=True
    )
    _ = httpx.post(f"{base}/__control/servers/f5-revoked/oauth/expire", headers=CONTROL)
    ctx = _context(base, "f5-revoked", config, auth)
    assert await _loaded(ctx) == set()
    assert [f["reason"] for f in ctx.mcp_tool_load_failures] == ["auth_expired"]  # main's code: reconnect


async def test_f5_a_server_that_is_down_is_not_a_reason_to_refresh(base: str) -> None:
    config = DictConfig()
    auth = await _connected(base, "f5-down", config)
    ctx = _context(base, "f5-gone", config, auth)
    assert await _loaded(ctx) == set()
    assert [i["grant"] for i in view(base, "f5-down")["oauth"]["issued"]] == ["authorization_code"]


@pytest.mark.parametrize(
    "message",
    [
        # Found by batch 63: the fake server's port was 58401, so a 404 read as a refused token.
        "Session terminated — the server responded 404 Not Found at http://127.0.0.1:58401/s/x/mcp.",
        "Request to http://10.0.0.4:4011/mcp failed after 1401 ms",
        "Client error '401 Unauthorized' for url 'http://127.0.0.1:58402/mcp'",
        "invalid_token: The access token expired",
    ],
)
def test_f5_only_a_refused_token_reads_as_one_never_a_number_in_the_url(message: str) -> None:
    """Main's check (#4003) reads the HTTP status the server answered, never the error's text."""
    import httpx

    from app.agents.agent_loop.mcp_session import is_http_unauthorized

    assert is_http_unauthorized(RuntimeError(message)) is False
    request = httpx.Request("POST", "http://127.0.0.1:58401/mcp")
    refused = httpx.HTTPStatusError(message, request=request, response=httpx.Response(401, request=request))
    assert is_http_unauthorized(refused) is True
