"""The MCP token client's HTTP layer, faked: `app.agents.mcp.oauth_client.refresh_access_token` runs for real
and gets the scripted answer (tests of the client itself, and of everything that reports its failures)."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch


def mock_async_client(inner: MagicMock):  # noqa: ANN201 - a patch context manager
    """Patches the client the token request opens; `inner.post` is the token endpoint."""
    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=inner)
    cm.__aexit__ = AsyncMock(return_value=False)
    return patch("app.agents.mcp.oauth_client.httpx.AsyncClient", return_value=cm)


def json_response(status_code: int, body: dict) -> MagicMock:
    resp = MagicMock()
    resp.status_code = status_code
    resp.headers = {"content-type": "application/json"}
    resp.text = str(body)
    resp.json.return_value = body
    return resp
