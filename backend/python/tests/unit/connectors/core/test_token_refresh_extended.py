"""
Extended tests for token_refresh_service covering missing lines:
- _perform_token_refresh full flow (lines 445-485)
- _periodic_refresh_check with CancelledError and Exception (lines 599-610)
- _delayed_refresh CancelledError path (lines 758-759)
- _refresh_all_tokens (lock + internal)
- _build_oauth_flow_from_auth_config
- _extract_scopes with non-dict scopes
"""

import asyncio
import logging
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def _make_service():
    """Create a TokenRefreshService with mocked dependencies."""
    from app.connectors.core.base.token_service.token_refresh_service import (
        TokenRefreshService,
    )

    config_service = AsyncMock()
    graph_provider = AsyncMock()
    svc = TokenRefreshService(config_service, graph_provider)
    return svc, config_service, graph_provider


# ============================================================================
# _perform_token_refresh
# ============================================================================


class TestPerformTokenRefresh:
    @pytest.mark.asyncio
    async def test_full_flow(self):
        svc, config_service, _ = _make_service()

        config_service.get_config = AsyncMock(return_value={
            "auth": {
                "oauthConfigId": "oauth123",
                "connectorScope": "team",
                "clientId": "cid",
                "clientSecret": "csecret",
                "authorizeUrl": "https://example.com/auth",
                "tokenUrl": "https://example.com/token",
                "redirectUri": "http://localhost/callback",
                "scopes": ["scope1"],
            },
            "credentials": {"refresh_token": "rt"},
        })
        config_service.set_config = AsyncMock(return_value=True)

        mock_token = MagicMock()
        mock_token.to_dict.return_value = {
            "access_token": "new_at",
            "refresh_token": "new_rt",
            "expires_in": 3600,
        }

        mock_provider = AsyncMock()
        mock_provider.refresh_access_token = AsyncMock(return_value=mock_token)
        mock_provider.close = AsyncMock()

        with patch(
            "app.connectors.core.base.token_service.token_refresh_service.get_oauth_config",
            return_value=MagicMock(),
        ):
            with patch(
                "app.connectors.core.base.token_service.oauth_service.OAuthProvider",
                return_value=mock_provider,
            ):
                result = await svc._perform_token_refresh("conn1", "Calendar", "old_rt")
                assert result == mock_token
                config_service.set_config.assert_awaited_once()
                mock_provider.close.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_no_config_raises(self):
        svc, config_service, _ = _make_service()
        config_service.get_config = AsyncMock(return_value=None)

        with pytest.raises(ValueError, match="No config found"):
            await svc._perform_token_refresh("conn1", "Calendar", "rt")


# ============================================================================
# _extract_scopes
# ============================================================================


class TestExtractScopes:
    def test_non_dict_scopes_list(self):
        svc, _, _ = _make_service()
        config = {"scopes": ["scope1", "scope2"]}
        result = svc._extract_scopes(config, "personal")
        assert result == ["scope1", "scope2"]

    def test_non_dict_scopes_non_list(self):
        svc, _, _ = _make_service()
        config = {"scopes": "not_a_dict_or_list"}
        result = svc._extract_scopes(config, "personal")
        assert result == []

    def test_dict_scopes_personal(self):
        svc, _, _ = _make_service()
        config = {
            "scopes": {
                "personal_sync": ["s1", "s2"],
                "team_sync": ["s3"],
            }
        }
        result = svc._extract_scopes(config, "personal")
        assert result == ["s1", "s2"]

    def test_dict_scopes_agent(self):
        svc, _, _ = _make_service()
        config = {
            "scopes": {
                "personal_sync": ["s1"],
                "agent": ["a1", "a2"],
            }
        }
        result = svc._extract_scopes(config, "agent")
        assert result == ["a1", "a2"]

    def test_dict_scopes_unknown_fallback(self):
        svc, _, _ = _make_service()
        config = {
            "scopes": {
                "team_sync": ["t1"],
            }
        }
        result = svc._extract_scopes(config, "unknown_scope")
        assert result == ["t1"]  # falls back to team_sync


# ============================================================================
# _build_oauth_flow_from_auth_config
# ============================================================================


class TestBuildOAuthFlowFromAuthConfig:
    def test_fills_missing_fields(self):
        svc, _, _ = _make_service()
        auth_config = {
            "authorizeUrl": "https://auth.example.com",
            "tokenUrl": "https://token.example.com",
            "redirectUri": "http://localhost/callback",
            "scopes": ["s1"],
        }
        result = svc._build_oauth_flow_from_auth_config(auth_config, {})
        assert result["authorizeUrl"] == "https://auth.example.com"
        assert result["tokenUrl"] == "https://token.example.com"
        assert result["scopes"] == ["s1"]

    def test_derives_authorize_and_token_urls_from_instance_url(self):
        """Self-managed GitLab EE: instanceUrl fills missing OAuth endpoints."""
        svc, _, _ = _make_service()
        auth_config = {
            "instanceUrl": "https://gitlab.mycompany.com",
            "redirectUri": "http://localhost/cb",
            "scopes": ["read_api"],
        }
        result = svc._build_oauth_flow_from_auth_config(auth_config, {})
        assert result["authorizeUrl"] == "https://gitlab.mycompany.com/oauth/authorize"
        assert result["tokenUrl"] == "https://gitlab.mycompany.com/oauth/token"

    def test_instance_url_trailing_slash_stripped_before_derivation(self):
        svc, _, _ = _make_service()
        auth_config = {"instanceUrl": "https://gitlab.mycompany.com/"}
        result = svc._build_oauth_flow_from_auth_config(auth_config, {})
        assert result["authorizeUrl"] == "https://gitlab.mycompany.com/oauth/authorize"
        assert result["tokenUrl"] == "https://gitlab.mycompany.com/oauth/token"

    def test_explicit_authorize_token_urls_win_over_instance_url(self):
        svc, _, _ = _make_service()
        auth_config = {
            "instanceUrl": "https://gitlab.mycompany.com",
            "authorizeUrl": "https://custom.example.com/oauth/authorize",
            "tokenUrl": "https://custom.example.com/oauth/token",
        }
        result = svc._build_oauth_flow_from_auth_config(auth_config, {})
        assert result["authorizeUrl"] == "https://custom.example.com/oauth/authorize"
        assert result["tokenUrl"] == "https://custom.example.com/oauth/token"

    def test_empty_instance_url_yields_empty_oauth_urls_when_unset(self):
        svc, _, _ = _make_service()
        auth_config: dict = {}
        result = svc._build_oauth_flow_from_auth_config(auth_config, {})
        assert result.get("authorizeUrl") == ""
        assert result.get("tokenUrl") == ""

    def test_does_not_overwrite_existing(self):
        svc, _, _ = _make_service()
        auth_config = {
            "authorizeUrl": "https://other.example.com",
            "tokenUrl": "https://other-token.example.com",
        }
        base = {
            "authorizeUrl": "https://existing.example.com",
            "tokenUrl": "https://existing-token.example.com",
        }
        result = svc._build_oauth_flow_from_auth_config(auth_config, base)
        assert result["authorizeUrl"] == "https://existing.example.com"


# ============================================================================
# _build_complete_oauth_config (shared OAuth config path)
# ============================================================================


class TestBuildCompleteOAuthConfigSharedPath:
    @pytest.mark.asyncio
    async def test_shared_config_propagates_instance_url_for_ee(self):
        """GitLab EE with shared OAuth config: instanceUrl from auth_config must
        be carried into the resulting oauth_flow_config so get_oauth_config()
        can swap the SaaS host out for the user's instance during refresh."""
        svc, _, _ = _make_service()
        shared_oauth_config = {
            "_id": "oauth-shared-1",
            "authorizeUrl": "https://gitlab.com/oauth/authorize",
            "tokenUrl": "https://gitlab.com/oauth/token",
            "config": {"clientId": "cid", "clientSecret": "csecret"},
            "scopes": {"team_sync": ["read_api"]},
        }
        svc._fetch_shared_oauth_config = AsyncMock(return_value=shared_oauth_config)
        auth_config = {
            "oauthConfigId": "oauth-shared-1",
            "connectorScope": "team",
            "instanceUrl": "https://gitlab.mycompany.com",
        }
        result = await svc._build_complete_oauth_config("conn1", "GITLAB", auth_config)
        assert result["instanceUrl"] == "https://gitlab.mycompany.com"
        assert result["clientId"] == "cid"
        assert result["clientSecret"] == "csecret"

    @pytest.mark.asyncio
    async def test_shared_config_without_instance_url_does_not_set_key(self):
        svc, _, _ = _make_service()
        shared_oauth_config = {
            "_id": "oauth-shared-1",
            "authorizeUrl": "https://gitlab.com/oauth/authorize",
            "tokenUrl": "https://gitlab.com/oauth/token",
            "config": {"clientId": "cid", "clientSecret": "csecret"},
            "scopes": {"team_sync": ["read_api"]},
        }
        svc._fetch_shared_oauth_config = AsyncMock(return_value=shared_oauth_config)
        auth_config = {
            "oauthConfigId": "oauth-shared-1",
            "connectorScope": "team",
        }
        result = await svc._build_complete_oauth_config("conn1", "GITLAB", auth_config)
        assert "instanceUrl" not in result

    @pytest.mark.asyncio
    async def test_shared_config_instance_url_propagated_when_auth_config_missing(self):
        """Legacy connectors created before instanceUrl stayed on the instance
        auth must still refresh against the EE host — fall back to the shared
        OAuth-app config's ``config.instanceUrl``."""
        svc, _, _ = _make_service()
        shared_oauth_config = {
            "_id": "oauth-shared-1",
            "authorizeUrl": "https://gitlab.com/oauth/authorize",
            "tokenUrl": "https://gitlab.com/oauth/token",
            "config": {
                "clientId": "cid",
                "clientSecret": "csecret",
                "instanceUrl": "https://git.acmecorp.com",
            },
            "scopes": {"team_sync": ["read_api"]},
        }
        svc._fetch_shared_oauth_config = AsyncMock(return_value=shared_oauth_config)
        auth_config = {
            "oauthConfigId": "oauth-shared-1",
            "connectorScope": "team",
            # No instanceUrl on the connector-instance auth (legacy install)
        }
        result = await svc._build_complete_oauth_config("conn1", "GITLAB", auth_config)
        assert result["instanceUrl"] == "https://git.acmecorp.com"

    @pytest.mark.asyncio
    async def test_instance_url_on_auth_config_overrides_shared(self):
        svc, _, _ = _make_service()
        shared_oauth_config = {
            "_id": "oauth-shared-1",
            "authorizeUrl": "https://gitlab.com/oauth/authorize",
            "tokenUrl": "https://gitlab.com/oauth/token",
            "config": {
                "clientId": "cid",
                "clientSecret": "csecret",
                "instanceUrl": "https://shared.example.com",
            },
            "scopes": {"team_sync": ["read_api"]},
        }
        svc._fetch_shared_oauth_config = AsyncMock(return_value=shared_oauth_config)
        auth_config = {
            "oauthConfigId": "oauth-shared-1",
            "connectorScope": "team",
            "instanceUrl": "https://instance.example.com",
        }
        result = await svc._build_complete_oauth_config("conn1", "GITLAB", auth_config)
        assert result["instanceUrl"] == "https://instance.example.com"


# ============================================================================
# _periodic_refresh_check
# ============================================================================


class TestPeriodicRefreshCheck:
    @pytest.mark.asyncio
    async def test_cancelled_error(self):
        svc, _, _ = _make_service()
        svc._running = True

        with patch("asyncio.sleep", new_callable=AsyncMock, side_effect=asyncio.CancelledError()):
            await svc._periodic_refresh_check()

    @pytest.mark.asyncio
    async def test_exception_handled(self):
        svc, _, _ = _make_service()
        svc._running = True
        call_count = 0

        async def mock_sleep(seconds):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise Exception("periodic error")
            svc._running = False

        with patch("asyncio.sleep", new_callable=AsyncMock, side_effect=mock_sleep):
            with patch.object(svc, "_refresh_all_tokens", new_callable=AsyncMock):
                await svc._periodic_refresh_check()


# ============================================================================
# _delayed_refresh CancelledError
# ============================================================================


class TestDelayedRefreshCancelled:
    @pytest.mark.asyncio
    async def test_cancelled_error_propagates(self):
        svc, _, _ = _make_service()
        svc._refresh_tasks["conn1"] = MagicMock()

        with patch("asyncio.sleep", new_callable=AsyncMock, side_effect=asyncio.CancelledError()):
            with pytest.raises(asyncio.CancelledError):
                await svc._delayed_refresh("conn1", "Calendar", 10.0)


# ============================================================================
# _refresh_all_tokens (with lock)
# ============================================================================


class TestRefreshAllTokens:
    @pytest.mark.asyncio
    async def test_delegates_to_internal(self):
        svc, _, _ = _make_service()
        with patch.object(svc, "_refresh_all_tokens_internal", new_callable=AsyncMock) as mock_internal:
            await svc._refresh_all_tokens()
            mock_internal.assert_awaited_once()
