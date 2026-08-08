"""Unit tests for app.agents.mcp.token_refresh."""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.agents.mcp import oauth_client as oauth_client_module
from app.agents.mcp.models import OAuthTokens
from app.agents.mcp.token_refresh import (
    MCPTokenRefreshError,
    refresh_credential_record,
    resolve_client_credentials,
)

CRED_PATH = "/services/mcp/credentials/inst-1/user-1"


def _oauth_record(refresh_token="refresh-1", token_url="https://example.com/token") -> dict:
    return {
        "isAuthenticated": True,
        "oauthTokens": {
            "accessToken": "access-1",
            "tokenType": "Bearer",
            "refreshToken": refresh_token,
            "expiresIn": 3600,
            "tokenUrl": token_url,
        },
    }


class TestResolveClientCredentials:
    @pytest.mark.asyncio
    async def test_prefers_dcr_client(self) -> None:
        cfg = MagicMock()

        async def _get_config(path, default=None):
            if "dcr-client" in path:
                return {"clientId": "dcr-cid", "clientSecret": "dcr-secret"}
            return {"clientId": "shared-cid", "clientSecret": "shared-secret"}

        cfg.get_config = AsyncMock(side_effect=_get_config)
        client_id, client_secret = await resolve_client_credentials("inst-1", "user-1", cfg)
        assert (client_id, client_secret) == ("dcr-cid", "dcr-secret")

    @pytest.mark.asyncio
    async def test_falls_back_to_shared_client(self) -> None:
        cfg = MagicMock()

        async def _get_config(path, default=None):
            if "dcr-client" in path:
                return None
            return {"clientId": "shared-cid", "clientSecret": "shared-secret"}

        cfg.get_config = AsyncMock(side_effect=_get_config)
        client_id, _ = await resolve_client_credentials("inst-1", "user-1", cfg)
        assert client_id == "shared-cid"

    @pytest.mark.asyncio
    async def test_no_client_returns_none_none(self) -> None:
        cfg = MagicMock()
        cfg.get_config = AsyncMock(return_value=None)
        result = await resolve_client_credentials("inst-1", "user-1", cfg)
        assert result == (None, None)


class TestRefreshCredentialRecord:
    @pytest.mark.asyncio
    async def test_missing_record_raises(self) -> None:
        cfg = MagicMock()
        cfg.get_config = AsyncMock(return_value=None)
        with pytest.raises(MCPTokenRefreshError, match="No MCP credential record"):
            await refresh_credential_record("inst-1", "user-1", cfg)

    @pytest.mark.asyncio
    async def test_missing_refresh_token_raises(self) -> None:
        cfg = MagicMock()
        cfg.get_config = AsyncMock(return_value={"oauthTokens": {"accessToken": "tok"}})
        with pytest.raises(MCPTokenRefreshError, match="No refresh token"):
            await refresh_credential_record("inst-1", "user-1", cfg)

    @pytest.mark.asyncio
    async def test_missing_token_url_raises(self) -> None:
        cfg = MagicMock()
        cfg.get_config = AsyncMock(return_value={"oauthTokens": {"refreshToken": "r1"}})
        with pytest.raises(MCPTokenRefreshError, match="tokenUrl"):
            await refresh_credential_record("inst-1", "user-1", cfg)

    @pytest.mark.asyncio
    async def test_missing_client_raises(self) -> None:
        cfg = MagicMock()
        record = _oauth_record()

        async def _get_config(path, default=None, use_cache=False):
            if path == CRED_PATH:
                return record
            return None

        cfg.get_config = AsyncMock(side_effect=_get_config)
        with pytest.raises(MCPTokenRefreshError, match="OAuth client"):
            await refresh_credential_record("inst-1", "user-1", cfg)

    @pytest.mark.asyncio
    async def test_successful_refresh_persists_new_tokens(self) -> None:
        cfg = MagicMock()
        record = _oauth_record()

        async def _get_config(path, default=None, use_cache=False):
            if path == CRED_PATH:
                return record
            if "dcr-client" in path:
                return {"clientId": "dcr-cid", "clientSecret": "dcr-secret"}
            return None

        cfg.get_config = AsyncMock(side_effect=_get_config)
        cfg.set_config = AsyncMock(return_value=True)

        new_tokens = OAuthTokens(
            access_token="new-access", refresh_token="new-refresh", expires_in=3600,
            token_url="https://example.com/token",
        )
        with patch.object(oauth_client_module, "refresh_access_token", new=AsyncMock(return_value=new_tokens)) as mock_refresh:
            result = await refresh_credential_record("inst-1", "user-1", cfg)

        assert result.access_token == "new-access"
        mock_refresh.assert_awaited_once()
        cfg.set_config.assert_awaited_once()
        persisted_path, persisted_record = cfg.set_config.await_args.args
        assert persisted_path == CRED_PATH
        assert persisted_record["oauthTokens"]["accessToken"] == "new-access"

    @pytest.mark.asyncio
    async def test_permanent_rejection_propagates(self) -> None:
        cfg = MagicMock()
        record = _oauth_record()

        async def _get_config(path, default=None, use_cache=False):
            if path == CRED_PATH:
                return record
            if "dcr-client" in path:
                return {"clientId": "dcr-cid", "clientSecret": "dcr-secret"}
            return None

        cfg.get_config = AsyncMock(side_effect=_get_config)
        cfg.set_config = AsyncMock(return_value=True)

        with patch.object(
            oauth_client_module,
            "refresh_access_token",
            new=AsyncMock(side_effect=oauth_client_module.MCPRefreshTokenInvalidError("invalid_grant")),
        ):
            with pytest.raises(oauth_client_module.MCPRefreshTokenInvalidError):
                await refresh_credential_record("inst-1", "user-1", cfg)
