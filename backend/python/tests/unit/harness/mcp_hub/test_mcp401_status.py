"""MCP401: a refused token is decided on the HTTP status the server answered, never on the error text.

Each transport against the scripted servers: a bearer server given the wrong token answers 401 (a refusal a refresh
can mend); a server that does not exist answers 404 although its URL carries "401" (never a refresh).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest

from app.agents.mcp.client import MCPClientManager, MCPConnectionError
from app.agents.mcp.errors import MCPHttpStatusError, is_http_unauthorized
from app.agents.mcp.models import MCPServerConfig
from tests.unit.harness.mcp_hub.kinds_server import loopback_allowed, make, serving

if TYPE_CHECKING:
    from collections.abc import Iterator

TRANSPORTS = ("streamable_http", "sse")


@pytest.fixture(autouse=True)
def _loopback() -> Iterator[None]:
    with loopback_allowed():
        yield


@pytest.fixture(scope="module")
def base() -> Iterator[str]:
    with serving() as url:
        yield url


def _config(base: str, server_id: str, transport: str) -> MCPServerConfig:
    path = "mcp" if transport == "streamable_http" else "sse"
    return MCPServerConfig.model_validate({
        "_id": f"inst-{server_id}", "orgId": "org-1", "createdBy": "u-1", "name": server_id,
        "transport": transport, "authMode": "api_token", "url": f"{base}/s/{server_id}/{path}",
        "createdAt": 0, "updatedAt": 0,
    })


async def _refusal(config: MCPServerConfig, headers: dict[str, str]) -> MCPConnectionError:
    with pytest.raises(MCPConnectionError) as refused:
        await MCPClientManager(config, headers=headers, connect_timeout_seconds=10).list_tools()
    return refused.value


@pytest.mark.parametrize("transport", TRANSPORTS)
async def test_mcp401_1_a_wrong_token_answers_401_and_reads_as_refused(base: str, transport: str) -> None:
    server_id = f"bearer-{transport}"
    _ = make(base, server_id, transport=transport, auth="bearer", credential="right-token")
    error = await _refusal(_config(base, server_id, transport), {"Authorization": "Bearer wrong-token"})
    assert is_http_unauthorized(error) is True


@pytest.mark.parametrize("transport", TRANSPORTS)
async def test_mcp401_2_a_404_is_never_a_refused_token_whatever_its_text(base: str, transport: str) -> None:
    error = await _refusal(_config(base, f"x401-{transport}-missing", transport), {})
    assert "401" in str(error)
    assert is_http_unauthorized(error) is False


def test_mcp401_3_without_a_status_the_text_never_decides() -> None:
    # Main's rule (status only): a refresh follows a 401 the server answered, never a message that mentions one.
    plain: Any = RuntimeError("upstream said 401 Unauthorized")
    assert is_http_unauthorized(plain) is False
    assert is_http_unauthorized(MCPHttpStatusError(401, "refused")) is True
