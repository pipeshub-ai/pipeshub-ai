"""Extended tests for app.api.routes.mcp_servers — targeting uncovered lines:
- _resolve_oauth_client_secret (986-1014)
- handle_oauth_callback: expired state, caller mismatch, client_config_changed (1042-1080)
- refresh_oauth_token: success, MCPTokenRefreshError, MCPRefreshTokenInvalidError, MCPOAuthError (1120-1157)
- get_oauth_config (1160-1179)
- update_oauth_config (1190-1219)
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

MODULE = "app.api.routes.mcp_servers"


def _mock_request(user=None, headers=None, app_state=None) -> MagicMock:
    request = MagicMock()
    request.state.user = user or {}
    request.headers = headers or {}
    for key, value in (app_state or {}).items():
        setattr(request.app.state, key, value)
    return request


# ============================================================================
# _resolve_oauth_client_secret
# ============================================================================


class TestResolveOAuthClientSecret:
    @pytest.mark.asyncio
    async def test_dcr_legacy_path_found(self):
        from app.api.routes.mcp_servers import _resolve_oauth_client_secret

        config_service = AsyncMock()
        config_service.get_config = AsyncMock(side_effect=[
            {"clientId": "cid-1", "clientSecret": "secret-legacy"},
        ])

        result = await _resolve_oauth_client_secret(config_service, "inst-1", "owner-1", "cid-1", is_dcr=True)
        assert result == "secret-legacy"

    @pytest.mark.asyncio
    async def test_dcr_shared_path_found(self):
        from app.api.routes.mcp_servers import _resolve_oauth_client_secret

        config_service = AsyncMock()
        config_service.get_config = AsyncMock(side_effect=[
            None,
            {"clientId": "cid-shared", "clientSecret": "secret-shared"},
        ])

        result = await _resolve_oauth_client_secret(config_service, "inst-1", "owner-1", "cid-shared", is_dcr=True)
        assert result == "secret-shared"

    @pytest.mark.asyncio
    async def test_dcr_not_found_raises_value_error(self):
        from app.api.routes.mcp_servers import _resolve_oauth_client_secret

        config_service = AsyncMock()
        config_service.get_config = AsyncMock(return_value=None)

        with pytest.raises(ValueError, match="dynamically-registered"):
            await _resolve_oauth_client_secret(config_service, "inst-1", "owner-1", "cid-missing", is_dcr=True)

    @pytest.mark.asyncio
    async def test_dcr_client_id_mismatch_raises(self):
        from app.api.routes.mcp_servers import _resolve_oauth_client_secret

        config_service = AsyncMock()
        config_service.get_config = AsyncMock(side_effect=[
            {"clientId": "wrong-id", "clientSecret": "sec"},
            {"clientId": "also-wrong", "clientSecret": "sec"},
        ])

        with pytest.raises(ValueError, match="dynamically-registered"):
            await _resolve_oauth_client_secret(config_service, "inst-1", "owner-1", "target-cid", is_dcr=True)

    @pytest.mark.asyncio
    async def test_static_client_found(self):
        from app.api.routes.mcp_servers import _resolve_oauth_client_secret

        config_service = AsyncMock()
        config_service.get_config = AsyncMock(return_value={"clientId": "cid-static", "clientSecret": "static-sec"})

        result = await _resolve_oauth_client_secret(config_service, "inst-1", "owner-1", "cid-static", is_dcr=False)
        assert result == "static-sec"

    @pytest.mark.asyncio
    async def test_static_client_not_found_raises(self):
        from app.api.routes.mcp_servers import _resolve_oauth_client_secret

        config_service = AsyncMock()
        config_service.get_config = AsyncMock(return_value=None)

        with pytest.raises(ValueError, match="OAuth app configuration"):
            await _resolve_oauth_client_secret(config_service, "inst-1", "owner-1", "cid-missing", is_dcr=False)

    @pytest.mark.asyncio
    async def test_static_client_id_mismatch_raises(self):
        from app.api.routes.mcp_servers import _resolve_oauth_client_secret

        config_service = AsyncMock()
        config_service.get_config = AsyncMock(return_value={"clientId": "other-cid", "clientSecret": "sec"})

        with pytest.raises(ValueError, match="OAuth app configuration"):
            await _resolve_oauth_client_secret(config_service, "inst-1", "owner-1", "target-cid", is_dcr=False)

    @pytest.mark.asyncio
    async def test_static_client_no_secret_returns_none(self):
        from app.api.routes.mcp_servers import _resolve_oauth_client_secret

        config_service = AsyncMock()
        config_service.get_config = AsyncMock(return_value={"clientId": "cid-1"})

        result = await _resolve_oauth_client_secret(config_service, "inst-1", "owner-1", "cid-1", is_dcr=False)
        assert result is None


# ============================================================================
# handle_oauth_callback — expired state, caller mismatch, client_config_changed
# ============================================================================


class TestHandleOAuthCallbackExpiredState:
    @pytest.mark.asyncio
    async def test_expired_state_record(self):
        from app.api.routes.mcp_servers import handle_oauth_callback

        mock_config = AsyncMock()
        mock_config.get_config = AsyncMock(return_value={
            "expiresAt": 0,
            "initiatedBy": "u1",
            "instanceId": "inst-1",
            "userId": "u1",
            "clientId": "cid",
            "tokenUrl": "https://example.com/token",
            "redirectUri": "https://app.example.com/callback",
        })
        mock_config.delete_config = AsyncMock()

        request = _mock_request(
            user={"userId": "u1", "orgId": "o1"},
            app_state={"config_service": mock_config},
        )

        with patch(f"{MODULE}._get_config_service", return_value=mock_config):
            result = await handle_oauth_callback(request, code="abc", state="state123", error=None)

        assert result["success"] is False
        assert result["error"] == "expired_state"


class TestHandleOAuthCallbackCallerMismatch:
    @pytest.mark.asyncio
    async def test_caller_mismatch(self):
        from app.api.routes.mcp_servers import handle_oauth_callback

        future_ms = 9999999999999
        mock_config = AsyncMock()
        mock_config.get_config = AsyncMock(return_value={
            "expiresAt": future_ms,
            "initiatedBy": "user-other",
            "instanceId": "inst-1",
            "userId": "user-other",
            "clientId": "cid",
            "tokenUrl": "https://example.com/token",
            "redirectUri": "https://app.example.com/callback",
        })
        mock_config.delete_config = AsyncMock()

        request = _mock_request(
            user={"userId": "u1", "orgId": "o1"},
            app_state={"config_service": mock_config},
        )

        with patch(f"{MODULE}._get_config_service", return_value=mock_config):
            result = await handle_oauth_callback(request, code="abc", state="state123", error=None)

        assert result["success"] is False
        assert result["error"] == "caller_mismatch"

    @pytest.mark.asyncio
    async def test_no_initiated_by_field(self):
        from app.api.routes.mcp_servers import handle_oauth_callback

        future_ms = 9999999999999
        mock_config = AsyncMock()
        mock_config.get_config = AsyncMock(return_value={
            "expiresAt": future_ms,
            "instanceId": "inst-1",
            "userId": "u1",
            "clientId": "cid",
        })
        mock_config.delete_config = AsyncMock()

        request = _mock_request(
            user={"userId": "u1", "orgId": "o1"},
            app_state={"config_service": mock_config},
        )

        with patch(f"{MODULE}._get_config_service", return_value=mock_config):
            result = await handle_oauth_callback(request, code="abc", state="state123", error=None)

        assert result["success"] is False
        assert result["error"] == "caller_mismatch"


class TestHandleOAuthCallbackClientConfigChanged:
    @pytest.mark.asyncio
    async def test_client_secret_resolution_fails(self):
        from app.api.routes.mcp_servers import handle_oauth_callback

        future_ms = 9999999999999
        mock_config = AsyncMock()
        mock_config.get_config = AsyncMock(return_value={
            "expiresAt": future_ms,
            "initiatedBy": "u1",
            "instanceId": "inst-1",
            "userId": "u1",
            "clientId": "cid",
            "tokenUrl": "https://example.com/token",
            "redirectUri": "https://app.example.com/callback",
        })
        mock_config.delete_config = AsyncMock()

        request = _mock_request(
            user={"userId": "u1", "orgId": "o1"},
            app_state={"config_service": mock_config},
        )

        with patch(f"{MODULE}._get_config_service", return_value=mock_config), \
             patch(f"{MODULE}._resolve_oauth_client_secret", new_callable=AsyncMock,
                   side_effect=ValueError("config changed")):
            result = await handle_oauth_callback(request, code="abc", state="state123", error=None)

        assert result["success"] is False
        assert result["error"] == "client_config_changed"


# ============================================================================
# refresh_oauth_token
# ============================================================================


class TestRefreshOAuthToken:
    @pytest.mark.asyncio
    async def test_success(self):
        from app.api.routes.mcp_servers import refresh_oauth_token

        mock_config = AsyncMock()
        request = _mock_request(
            user={"userId": "u1", "orgId": "o1"},
            app_state={"config_service": mock_config},
        )

        with patch(f"{MODULE}._get_config_service", return_value=mock_config), \
             patch(f"{MODULE}._get_user_context", return_value={"user_id": "u1", "org_id": "o1"}), \
             patch(f"{MODULE}.resolve_instance_owner_config_service", new_callable=AsyncMock, return_value=None), \
             patch(f"{MODULE}.mcp_token_refresh") as mock_refresh:
            mock_refresh.refresh_credential_record = AsyncMock()
            result = await refresh_oauth_token(request, "inst-1")

        assert result == {"success": True}

    @pytest.mark.asyncio
    async def test_mcp_token_refresh_error_raises_400(self):
        from app.agents.mcp.token_refresh import MCPTokenRefreshError
        from app.api.routes.mcp_servers import refresh_oauth_token

        mock_config = AsyncMock()
        request = _mock_request(
            user={"userId": "u1", "orgId": "o1"},
            app_state={"config_service": mock_config},
        )

        with patch(f"{MODULE}._get_config_service", return_value=mock_config), \
             patch(f"{MODULE}._get_user_context", return_value={"user_id": "u1", "org_id": "o1"}), \
             patch(f"{MODULE}.resolve_instance_owner_config_service", new_callable=AsyncMock, return_value=None), \
             patch(f"{MODULE}.mcp_token_refresh") as mock_refresh:
            mock_refresh.MCPTokenRefreshError = MCPTokenRefreshError
            mock_refresh.refresh_credential_record = AsyncMock(
                side_effect=MCPTokenRefreshError("no credential record"))

            with pytest.raises(HTTPException) as exc_info:
                await refresh_oauth_token(request, "inst-1")
            assert exc_info.value.status_code == 400

    @pytest.mark.asyncio
    async def test_refresh_token_invalid_marks_unauthenticated_and_raises_401(self):
        from app.agents.mcp.oauth_client import MCPRefreshTokenInvalidError
        from app.api.routes.mcp_servers import refresh_oauth_token

        mock_config = AsyncMock()
        mock_config.get_config = AsyncMock(return_value={"isAuthenticated": True, "instanceId": "inst-1"})
        mock_config.set_config = AsyncMock(return_value=True)

        request = _mock_request(
            user={"userId": "u1", "orgId": "o1"},
            app_state={"config_service": mock_config},
        )

        with patch(f"{MODULE}._get_config_service", return_value=mock_config), \
             patch(f"{MODULE}._get_user_context", return_value={"user_id": "u1", "org_id": "o1"}), \
             patch(f"{MODULE}.resolve_instance_owner_config_service", new_callable=AsyncMock, return_value=None), \
             patch(f"{MODULE}.mcp_token_refresh") as mock_refresh, \
             patch(f"{MODULE}.oauth_client_module") as mock_oauth:
            mock_refresh.MCPTokenRefreshError = ValueError
            mock_oauth.MCPRefreshTokenInvalidError = MCPRefreshTokenInvalidError
            mock_oauth.MCPOAuthError = Exception
            mock_refresh.refresh_credential_record = AsyncMock(
                side_effect=MCPRefreshTokenInvalidError("token rejected"))

            with pytest.raises(HTTPException) as exc_info:
                await refresh_oauth_token(request, "inst-1")
            assert exc_info.value.status_code == 401

        mock_config.set_config.assert_awaited_once()
        saved_record = mock_config.set_config.call_args[0][1]
        assert saved_record["isAuthenticated"] is False

    @pytest.mark.asyncio
    async def test_refresh_token_invalid_no_record(self):
        from app.agents.mcp.oauth_client import MCPRefreshTokenInvalidError
        from app.api.routes.mcp_servers import refresh_oauth_token

        mock_config = AsyncMock()
        mock_config.get_config = AsyncMock(return_value=None)
        mock_config.set_config = AsyncMock()

        request = _mock_request(
            user={"userId": "u1", "orgId": "o1"},
            app_state={"config_service": mock_config},
        )

        with patch(f"{MODULE}._get_config_service", return_value=mock_config), \
             patch(f"{MODULE}._get_user_context", return_value={"user_id": "u1", "org_id": "o1"}), \
             patch(f"{MODULE}.resolve_instance_owner_config_service", new_callable=AsyncMock, return_value=None), \
             patch(f"{MODULE}.mcp_token_refresh") as mock_refresh, \
             patch(f"{MODULE}.oauth_client_module") as mock_oauth:
            mock_refresh.MCPTokenRefreshError = ValueError
            mock_oauth.MCPRefreshTokenInvalidError = MCPRefreshTokenInvalidError
            mock_oauth.MCPOAuthError = Exception
            mock_refresh.refresh_credential_record = AsyncMock(
                side_effect=MCPRefreshTokenInvalidError("token rejected"))

            with pytest.raises(HTTPException) as exc_info:
                await refresh_oauth_token(request, "inst-1")
            assert exc_info.value.status_code == 401

        mock_config.set_config.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_mcp_oauth_error_raises_502(self):
        from app.agents.mcp.oauth_client import MCPOAuthError
        from app.api.routes.mcp_servers import refresh_oauth_token

        mock_config = AsyncMock()
        request = _mock_request(
            user={"userId": "u1", "orgId": "o1"},
            app_state={"config_service": mock_config},
        )

        with patch(f"{MODULE}._get_config_service", return_value=mock_config), \
             patch(f"{MODULE}._get_user_context", return_value={"user_id": "u1", "org_id": "o1"}), \
             patch(f"{MODULE}.resolve_instance_owner_config_service", new_callable=AsyncMock, return_value=None), \
             patch(f"{MODULE}.mcp_token_refresh") as mock_refresh, \
             patch(f"{MODULE}.oauth_client_module") as mock_oauth:
            mock_refresh.MCPTokenRefreshError = ValueError
            mock_oauth.MCPRefreshTokenInvalidError = type("NotThisError", (Exception,), {})
            mock_oauth.MCPOAuthError = MCPOAuthError
            mock_refresh.refresh_credential_record = AsyncMock(
                side_effect=MCPOAuthError("provider unreachable"))

            with pytest.raises(HTTPException) as exc_info:
                await refresh_oauth_token(request, "inst-1")
            assert exc_info.value.status_code == 502


# ============================================================================
# get_oauth_config
# ============================================================================


class TestGetOAuthConfig:
    @pytest.mark.asyncio
    async def test_non_admin_raises_403(self):
        from app.api.routes.mcp_servers import get_oauth_config

        mock_config = AsyncMock()
        request = _mock_request(
            user={"userId": "u1", "orgId": "o1"},
            app_state={"config_service": mock_config},
        )

        with patch(f"{MODULE}._get_config_service", return_value=mock_config), \
             patch(f"{MODULE}._get_user_context", return_value={"user_id": "u1", "org_id": "o1"}), \
             patch(f"{MODULE}._check_user_is_admin", new_callable=AsyncMock, return_value=False):
            with pytest.raises(HTTPException) as exc_info:
                await get_oauth_config(request, "inst-1")
            assert exc_info.value.status_code == 403

    @pytest.mark.asyncio
    async def test_not_configured(self):
        from app.api.routes.mcp_servers import get_oauth_config

        mock_config = AsyncMock()
        mock_config.get_config = AsyncMock(return_value=None)
        request = _mock_request(
            user={"userId": "u1", "orgId": "o1"},
            app_state={"config_service": mock_config},
        )

        with patch(f"{MODULE}._get_config_service", return_value=mock_config), \
             patch(f"{MODULE}._get_user_context", return_value={"user_id": "u1", "org_id": "o1"}), \
             patch(f"{MODULE}._check_user_is_admin", new_callable=AsyncMock, return_value=True), \
             patch(f"{MODULE}.resolve_instance_owner_config_service", new_callable=AsyncMock, return_value=None):
            result = await get_oauth_config(request, "inst-1")

        assert result == {"configured": False}

    @pytest.mark.asyncio
    async def test_configured_returns_masked_secrets(self):
        from app.api.routes.mcp_servers import get_oauth_config

        mock_config = AsyncMock()
        mock_config.get_config = AsyncMock(return_value={
            "clientId": "1234567890abcdef",
            "clientSecret": "secretvalue1234567",
        })
        request = _mock_request(
            user={"userId": "u1", "orgId": "o1"},
            app_state={"config_service": mock_config},
        )

        with patch(f"{MODULE}._get_config_service", return_value=mock_config), \
             patch(f"{MODULE}._get_user_context", return_value={"user_id": "u1", "org_id": "o1"}), \
             patch(f"{MODULE}._check_user_is_admin", new_callable=AsyncMock, return_value=True):
            result = await get_oauth_config(request, "inst-1")

        assert result["configured"] is True
        assert "••••••••" in result["clientId"]
        assert "••••••••" in result["clientSecret"]


# ============================================================================
# update_oauth_config
# ============================================================================


class TestUpdateOAuthConfig:
    def _payload(self):
        from app.api.routes.mcp_servers import OAuthClientConfigRequest

        return OAuthClientConfigRequest(clientId="new-cid", clientSecret="new-secret")

    @pytest.mark.asyncio
    async def test_non_admin_raises_403(self):
        from app.api.routes.mcp_servers import update_oauth_config

        mock_config = AsyncMock()
        request = _mock_request(
            user={"userId": "u1", "orgId": "o1"},
            app_state={"config_service": mock_config},
        )

        with patch(f"{MODULE}._get_config_service", return_value=mock_config), \
             patch(f"{MODULE}._get_user_context", return_value={"user_id": "u1", "org_id": "o1"}), \
             patch(f"{MODULE}._check_user_is_admin", new_callable=AsyncMock, return_value=False):
            with pytest.raises(HTTPException) as exc_info:
                await update_oauth_config(request, "inst-1", self._payload())
            assert exc_info.value.status_code == 403

    @pytest.mark.asyncio
    async def test_instance_not_found_raises_404(self):
        from app.api.routes.mcp_servers import update_oauth_config

        mock_config = AsyncMock()
        request = _mock_request(
            user={"userId": "u1", "orgId": "o1"},
            app_state={"config_service": mock_config},
        )

        with patch(f"{MODULE}._get_config_service", return_value=mock_config), \
             patch(f"{MODULE}._get_user_context", return_value={"user_id": "u1", "org_id": "o1"}), \
             patch(f"{MODULE}._check_user_is_admin", new_callable=AsyncMock, return_value=True), \
             patch(f"{MODULE}._get_org_instance", new_callable=AsyncMock, return_value=None):
            with pytest.raises(HTTPException) as exc_info:
                await update_oauth_config(request, "inst-1", self._payload())
            assert exc_info.value.status_code == 404

    @pytest.mark.asyncio
    async def test_success(self):
        from app.api.routes.mcp_servers import update_oauth_config

        mock_config = AsyncMock()
        mock_config.set_config = AsyncMock(return_value=True)
        request = _mock_request(
            user={"userId": "u1", "orgId": "o1"},
            app_state={"config_service": mock_config},
        )

        with patch(f"{MODULE}._get_config_service", return_value=mock_config), \
             patch(f"{MODULE}._get_user_context", return_value={"user_id": "u1", "org_id": "o1"}), \
             patch(f"{MODULE}._check_user_is_admin", new_callable=AsyncMock, return_value=True), \
             patch(f"{MODULE}._get_org_instance", new_callable=AsyncMock, return_value={"_id": "inst-1"}):
            result = await update_oauth_config(request, "inst-1", self._payload())

        assert result == {"success": True}
        mock_config.set_config.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_set_config_failure_raises_500(self):
        from app.api.routes.mcp_servers import update_oauth_config

        mock_config = AsyncMock()
        mock_config.set_config = AsyncMock(return_value=False)
        request = _mock_request(
            user={"userId": "u1", "orgId": "o1"},
            app_state={"config_service": mock_config},
        )

        with patch(f"{MODULE}._get_config_service", return_value=mock_config), \
             patch(f"{MODULE}._get_user_context", return_value={"user_id": "u1", "org_id": "o1"}), \
             patch(f"{MODULE}._check_user_is_admin", new_callable=AsyncMock, return_value=True), \
             patch(f"{MODULE}._get_org_instance", new_callable=AsyncMock, return_value={"_id": "inst-1"}):
            with pytest.raises(HTTPException) as exc_info:
                await update_oauth_config(request, "inst-1", self._payload())
            assert exc_info.value.status_code == 500
