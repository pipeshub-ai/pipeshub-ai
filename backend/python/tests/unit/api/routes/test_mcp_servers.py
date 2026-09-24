"""Unit tests for app.api.routes.mcp_servers."""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from app.agents.constants.mcp_server_constants import (
    get_mcp_credentials_path,
    get_mcp_dcr_client_path,
)
from app.agents.mcp.dcr import DiscoveryBlockedError
from app.agents.mcp.models import DCRClient, DiscoveredOAuthMetadata, MCPAuthMode, MCPServerInstanceConfig, MCPTransport
from app.api.routes.mcp_servers import (
    AuthenticateRequest,
    OAuthDiscoveryRequest,
    _assert_admin_owns_shared_credential,
    _assert_can_write_credentials,
    _assert_non_oauth_auth_mode,
    _build_credential_record,
    _build_instance_record,
    _build_oauth_authorization_url,
    _credentials_to_discovery_dict,
    _get_config_service,
    _get_mcp_registry,
    _get_user_context,
    _mask_secret,
    _resolve_effective_user_auth,
    _resolve_oauth_endpoints,
    _resolve_validated_base_url,
    _validate_instance_config,
    discover_oauth_metadata_endpoint,
    handle_oauth_callback,
    remove_credentials,
)


def _mock_request(user=None, headers=None, app_state=None) -> MagicMock:
    request = MagicMock()
    request.state.user = user or {}
    request.headers = headers or {}
    for key, value in (app_state or {}).items():
        setattr(request.app.state, key, value)
    return request


# ---------------------------------------------------------------------------
# _get_user_context
# ---------------------------------------------------------------------------

class TestGetUserContext:
    def test_valid_from_state(self) -> None:
        request = _mock_request(user={"userId": "u1", "orgId": "o1"})
        ctx = _get_user_context(request)
        assert ctx == {"user_id": "u1", "org_id": "o1"}

    def test_headers_are_ignored(self) -> None:
        request = _mock_request(headers={"X-User-Id": "u2", "X-Organization-Id": "o2"})
        with pytest.raises(HTTPException) as exc:
            _get_user_context(request)
        assert exc.value.status_code == 401

    def test_missing_user_id_raises_401(self) -> None:
        request = _mock_request()
        with pytest.raises(HTTPException) as exc:
            _get_user_context(request)
        assert exc.value.status_code == 401


# ---------------------------------------------------------------------------
# _get_config_service / _get_mcp_registry
# ---------------------------------------------------------------------------

class TestGetConfigService:
    def test_returns_configured_service(self) -> None:
        mock_svc = MagicMock()
        request = _mock_request(app_state={"config_service": mock_svc})
        assert _get_config_service(request) is mock_svc

    def test_missing_raises_500(self) -> None:
        request = _mock_request(app_state={"config_service": None})
        with pytest.raises(HTTPException) as exc:
            _get_config_service(request)
        assert exc.value.status_code == 500


class TestGetMcpRegistry:
    def test_returns_registry(self) -> None:
        mock_registry = MagicMock()
        request = _mock_request(app_state={"mcp_registry": mock_registry})
        assert _get_mcp_registry(request) is mock_registry

    def test_missing_raises_500(self) -> None:
        request = _mock_request(app_state={"mcp_registry": None})
        with pytest.raises(HTTPException) as exc:
            _get_mcp_registry(request)
        assert exc.value.status_code == 500


# ---------------------------------------------------------------------------
# _resolve_validated_base_url
# ---------------------------------------------------------------------------

class TestResolveValidatedBaseUrl:
    def test_no_requested_url_uses_configured(self) -> None:
        result = _resolve_validated_base_url(None, "https://app.example.com")
        assert result == "https://app.example.com"

    def test_matching_scheme_and_host_is_trusted(self) -> None:
        result = _resolve_validated_base_url("https://app.example.com/foo", "https://app.example.com")
        assert result == "https://app.example.com/foo"

    def test_mismatched_host_rejected_falls_back(self) -> None:
        result = _resolve_validated_base_url("https://evil.com", "https://app.example.com")
        assert result == "https://app.example.com"

    def test_mismatched_scheme_rejected(self) -> None:
        result = _resolve_validated_base_url("http://app.example.com", "https://app.example.com")
        assert result == "https://app.example.com"

    def test_malformed_url_falls_back(self) -> None:
        result = _resolve_validated_base_url("not a url \x00", "https://app.example.com")
        assert result == "https://app.example.com"


# ---------------------------------------------------------------------------
# _validate_instance_config
# ---------------------------------------------------------------------------

class TestValidateInstanceConfig:
    def test_known_type_id_passes(self) -> None:
        registry = MagicMock()
        registry.get_template.return_value = MagicMock()
        payload = MCPServerInstanceConfig(name="x", type_id="brave_search", transport=MCPTransport.STDIO, auth_mode=MCPAuthMode.API_TOKEN)
        _validate_instance_config(payload, registry)  # should not raise

    def test_unknown_type_id_raises_400(self) -> None:
        registry = MagicMock()
        registry.get_template.return_value = None
        payload = MCPServerInstanceConfig(name="x", type_id="nonexistent", transport=MCPTransport.STDIO, auth_mode=MCPAuthMode.API_TOKEN)
        with pytest.raises(HTTPException) as exc:
            _validate_instance_config(payload, registry)
        assert exc.value.status_code == 400

    def test_custom_stdio_requires_command(self) -> None:
        registry = MagicMock()
        payload = MCPServerInstanceConfig(name="x", transport=MCPTransport.STDIO, auth_mode=MCPAuthMode.NONE)
        with pytest.raises(HTTPException) as exc:
            _validate_instance_config(payload, registry)
        assert exc.value.status_code == 400

    def test_custom_sse_requires_url(self) -> None:
        registry = MagicMock()
        payload = MCPServerInstanceConfig(name="x", transport=MCPTransport.SSE, auth_mode=MCPAuthMode.NONE)
        with pytest.raises(HTTPException) as exc:
            _validate_instance_config(payload, registry)
        assert exc.value.status_code == 400

    def test_custom_stdio_with_command_passes(self) -> None:
        registry = MagicMock()
        payload = MCPServerInstanceConfig(name="x", transport=MCPTransport.STDIO, auth_mode=MCPAuthMode.NONE, command="npx")
        _validate_instance_config(payload, registry)  # should not raise


# ---------------------------------------------------------------------------
# _build_instance_record
# ---------------------------------------------------------------------------

class TestBuildInstanceRecord:
    def test_custom_instance_fields(self) -> None:
        registry = MagicMock()
        registry.get_template.return_value = None
        payload = MCPServerInstanceConfig(
            name="Custom",
            transport=MCPTransport.SSE,
            auth_mode=MCPAuthMode.API_TOKEN,
            url="https://example.com/sse",
        )
        record = _build_instance_record(payload, "inst-1", "org-1", "user-1", registry)
        assert record["_id"] == "inst-1"
        assert record["orgId"] == "org-1"
        assert record["createdBy"] == "user-1"
        assert record["isCustom"] is True
        assert record["url"] == "https://example.com/sse"

    def test_catalog_instance_fills_from_template(self) -> None:
        template = MagicMock()
        template.command = "npx"
        template.args = ["-y", "server"]
        template.required_env = ["API_KEY"]
        template.optional_env = []
        template.default_url = None
        template.authorization_url = None
        template.token_url = None
        template.default_scopes = []
        registry = MagicMock()
        registry.get_template.return_value = template

        payload = MCPServerInstanceConfig(
            name="Brave",
            type_id="brave_search",
            transport=MCPTransport.STDIO,
            auth_mode=MCPAuthMode.API_TOKEN,
        )
        record = _build_instance_record(payload, "inst-2", "org-1", "user-1", registry)
        assert record["command"] == "npx"
        assert record["args"] == ["-y", "server"]
        assert record["requiredEnv"] == ["API_KEY"]
        assert record["isCustom"] is False

    def test_update_preserves_created_by_and_created_at(self) -> None:
        registry = MagicMock()
        registry.get_template.return_value = None
        payload = MCPServerInstanceConfig(name="Custom", transport=MCPTransport.STDIO, auth_mode=MCPAuthMode.NONE, command="npx")
        existing = {"createdBy": "original-user", "createdAt": 12345}
        record = _build_instance_record(payload, "inst-3", "org-1", "current-user", registry, existing=existing)
        assert record["createdBy"] == "original-user"
        assert record["createdAt"] == 12345

    def test_custom_stdio_persists_required_env(self) -> None:
        registry = MagicMock()
        registry.get_template.return_value = None
        payload = MCPServerInstanceConfig(
            name="Custom Stdio",
            transport=MCPTransport.STDIO,
            auth_mode=MCPAuthMode.API_TOKEN,
            command="npx",
            args=["-y", "my-mcp"],
            required_env=["MY_API_KEY"],
        )
        record = _build_instance_record(payload, "inst-4", "org-1", "user-1", registry)
        assert record["isCustom"] is True
        assert record["args"] == ["-y", "my-mcp"]
        assert record["requiredEnv"] == ["MY_API_KEY"]


# ---------------------------------------------------------------------------
# _assert_non_oauth_auth_mode / _assert_can_write_credentials
# ---------------------------------------------------------------------------

class TestAssertNonOAuthAuthMode:
    def test_oauth_mode_raises(self) -> None:
        with pytest.raises(HTTPException) as exc:
            _assert_non_oauth_auth_mode({"authMode": MCPAuthMode.OAUTH.value})
        assert exc.value.status_code == 400

    def test_none_mode_raises(self) -> None:
        with pytest.raises(HTTPException) as exc:
            _assert_non_oauth_auth_mode({"authMode": MCPAuthMode.NONE.value})
        assert exc.value.status_code == 400

    def test_api_token_mode_passes(self) -> None:
        _assert_non_oauth_auth_mode({"authMode": MCPAuthMode.API_TOKEN.value})  # no raise


class TestAssertCanWriteCredentials:
    @pytest.mark.asyncio
    async def test_use_admin_auth_requires_admin(self) -> None:
        instance = {"authMode": MCPAuthMode.API_TOKEN.value, "useAdminAuth": True}
        request = _mock_request()
        user_context = {"user_id": "u1", "org_id": "o1"}
        with patch("app.api.routes.mcp_servers._check_user_is_admin", new=AsyncMock(return_value=False)):
            with pytest.raises(HTTPException) as exc:
                await _assert_can_write_credentials(instance, user_context, request, MagicMock())
        assert exc.value.status_code == 403

    @pytest.mark.asyncio
    async def test_use_admin_auth_admin_allowed(self) -> None:
        instance = {"authMode": MCPAuthMode.API_TOKEN.value, "useAdminAuth": True}
        request = _mock_request()
        user_context = {"user_id": "u1", "org_id": "o1"}
        with patch("app.api.routes.mcp_servers._check_user_is_admin", new=AsyncMock(return_value=True)):
            await _assert_can_write_credentials(instance, user_context, request, MagicMock())  # no raise

    @pytest.mark.asyncio
    async def test_non_admin_auth_no_admin_check_needed(self) -> None:
        instance = {"authMode": MCPAuthMode.API_TOKEN.value, "useAdminAuth": False}
        request = _mock_request()
        user_context = {"user_id": "u1", "org_id": "o1"}
        await _assert_can_write_credentials(instance, user_context, request, MagicMock())  # no raise


class TestAssertAdminOwnsSharedCredential:
    """Disconnect (`DELETE /credentials`) must still gate a shared admin credential, but —
    unlike writes — must not reject based on auth mode (OAuth instances need to be
    disconnectable too).
    """

    @pytest.mark.asyncio
    async def test_oauth_mode_with_admin_auth_requires_admin(self) -> None:
        instance = {"authMode": MCPAuthMode.OAUTH.value, "useAdminAuth": True}
        request = _mock_request()
        user_context = {"user_id": "u1", "org_id": "o1"}
        with patch("app.api.routes.mcp_servers._check_user_is_admin", new=AsyncMock(return_value=False)):
            with pytest.raises(HTTPException) as exc:
                await _assert_admin_owns_shared_credential(instance, user_context, request, MagicMock())
        assert exc.value.status_code == 403

    @pytest.mark.asyncio
    async def test_oauth_mode_without_admin_auth_passes(self) -> None:
        instance = {"authMode": MCPAuthMode.OAUTH.value, "useAdminAuth": False}
        request = _mock_request()
        user_context = {"user_id": "u1", "org_id": "o1"}
        await _assert_admin_owns_shared_credential(instance, user_context, request, MagicMock())  # no raise


# ---------------------------------------------------------------------------
# remove_credentials (DELETE /instances/{instance_id}/credentials — disconnect)
# ---------------------------------------------------------------------------

class TestRemoveCredentials:
    """Regression test: disconnecting an OAuth instance used to 400 via the same
    `_assert_non_oauth_auth_mode` guard as writes; it must succeed and clear both the
    credential record and the caller's legacy per-owner DCR client.
    """

    @pytest.mark.asyncio
    async def test_oauth_instance_disconnect_succeeds(self) -> None:
        instance = {"_id": "inst-1", "authMode": MCPAuthMode.OAUTH.value, "useAdminAuth": False}
        request = _mock_request(user={"userId": "u1", "orgId": "o1"}, app_state={"config_service": MagicMock()})
        config_service = MagicMock()
        config_service.delete_config = AsyncMock(return_value=True)
        request.app.state.config_service = config_service
        mock_refresh_service = MagicMock()
        mock_refresh_service.cancel_refresh_task = MagicMock()

        with patch("app.api.routes.mcp_servers._get_org_instance", new=AsyncMock(return_value=instance)), \
                patch("app.connectors.core.base.token_service.startup_service.startup_service") as mock_startup:
            mock_startup.get_mcp_token_refresh_service.return_value = mock_refresh_service
            result = await remove_credentials(request, "inst-1")

        assert result == {"success": True}
        deleted_paths = [call.args[0] for call in config_service.delete_config.call_args_list]
        # Both the credential record and the legacy per-owner DCR client are cleared.
        assert deleted_paths == [
            get_mcp_credentials_path("inst-1", "u1"),
            get_mcp_dcr_client_path("inst-1", "u1"),
        ]

    @pytest.mark.asyncio
    async def test_api_token_instance_disconnect_does_not_touch_dcr_client(self) -> None:
        instance = {"_id": "inst-2", "authMode": MCPAuthMode.API_TOKEN.value, "useAdminAuth": False}
        request = _mock_request(user={"userId": "u1", "orgId": "o1"}, app_state={"config_service": MagicMock()})
        config_service = MagicMock()
        config_service.delete_config = AsyncMock(return_value=True)
        request.app.state.config_service = config_service

        with patch("app.api.routes.mcp_servers._get_org_instance", new=AsyncMock(return_value=instance)):
            result = await remove_credentials(request, "inst-2")

        assert result == {"success": True}
        assert config_service.delete_config.await_count == 1

    @pytest.mark.asyncio
    async def test_shared_admin_credential_blocks_non_admin(self) -> None:
        instance = {"_id": "inst-3", "authMode": MCPAuthMode.OAUTH.value, "useAdminAuth": True}
        request = _mock_request(user={"userId": "u1", "orgId": "o1"}, app_state={"config_service": MagicMock()})

        with patch("app.api.routes.mcp_servers._get_org_instance", new=AsyncMock(return_value=instance)), patch(
            "app.api.routes.mcp_servers._check_user_is_admin", new=AsyncMock(return_value=False)
        ):
            with pytest.raises(HTTPException) as exc:
                await remove_credentials(request, "inst-3")
        assert exc.value.status_code == 403


# ---------------------------------------------------------------------------
# _build_credential_record
# ---------------------------------------------------------------------------

class TestBuildCredentialRecord:
    def test_api_token_mode(self) -> None:
        instance = {"_id": "inst-1", "authMode": MCPAuthMode.API_TOKEN.value}
        payload = AuthenticateRequest(apiToken="secret-token")
        record = _build_credential_record(instance, payload, "user-1", "org-1")
        assert record["credentials"] == {"apiToken": "secret-token"}
        assert record["isAuthenticated"] is True

    def test_api_token_missing_raises_400(self) -> None:
        instance = {"_id": "inst-1", "authMode": MCPAuthMode.API_TOKEN.value}
        payload = AuthenticateRequest()
        with pytest.raises(HTTPException) as exc:
            _build_credential_record(instance, payload, "user-1", "org-1")
        assert exc.value.status_code == 400

    def test_api_token_stdio_multi_env(self) -> None:
        instance = {
            "_id": "inst-1",
            "authMode": MCPAuthMode.API_TOKEN.value,
            "transport": MCPTransport.STDIO.value,
            "requiredEnv": ["SLACK_BOT_TOKEN", "SLACK_TEAM_ID"],
            "optionalEnv": ["SLACK_CHANNEL_IDS"],
        }
        payload = AuthenticateRequest(
            apiToken="xoxb-token",
            env={
                "SLACK_BOT_TOKEN": "xoxb-token",
                "SLACK_TEAM_ID": "T01234567",
                "SLACK_CHANNEL_IDS": "C1",
                "IGNORED": "nope",
            },
        )
        record = _build_credential_record(instance, payload, "user-1", "org-1")
        assert record["credentials"] == {
            "apiToken": "xoxb-token",
            "env": {
                "SLACK_BOT_TOKEN": "xoxb-token",
                "SLACK_TEAM_ID": "T01234567",
                "SLACK_CHANNEL_IDS": "C1",
            },
        }

    def test_api_token_stdio_multi_env_missing_raises_400(self) -> None:
        instance = {
            "_id": "inst-1",
            "authMode": MCPAuthMode.API_TOKEN.value,
            "transport": MCPTransport.STDIO.value,
            "requiredEnv": ["SLACK_BOT_TOKEN", "SLACK_TEAM_ID"],
        }
        payload = AuthenticateRequest(apiToken="xoxb-token")
        with pytest.raises(HTTPException) as exc:
            _build_credential_record(instance, payload, "user-1", "org-1")
        assert exc.value.status_code == 400
        assert "SLACK_TEAM_ID" in str(exc.value.detail)

    def test_headers_mode_uses_payload_header_name(self) -> None:
        instance = {"_id": "inst-1", "authMode": MCPAuthMode.HEADERS.value, "headerName": "X-Default"}
        payload = AuthenticateRequest(headerName="X-Api-Key", headerValue="val123")
        record = _build_credential_record(instance, payload, "user-1", "org-1")
        assert record["credentials"] == {"headerName": "X-Api-Key", "headerValue": "val123"}

    def test_headers_mode_falls_back_to_instance_header_name(self) -> None:
        instance = {"_id": "inst-1", "authMode": MCPAuthMode.HEADERS.value, "headerName": "X-Default"}
        payload = AuthenticateRequest(headerValue="val123")
        record = _build_credential_record(instance, payload, "user-1", "org-1")
        assert record["credentials"]["headerName"] == "X-Default"

    def test_headers_mode_missing_value_raises_400(self) -> None:
        instance = {"_id": "inst-1", "authMode": MCPAuthMode.HEADERS.value}
        payload = AuthenticateRequest()
        with pytest.raises(HTTPException) as exc:
            _build_credential_record(instance, payload, "user-1", "org-1")
        assert exc.value.status_code == 400


# ---------------------------------------------------------------------------
# _resolve_effective_user_auth
# ---------------------------------------------------------------------------

class TestResolveEffectiveUserAuth:
    @pytest.mark.asyncio
    async def test_none_auth_mode_returns_empty_dict(self) -> None:
        instance = {"_id": "inst-1", "authMode": MCPAuthMode.NONE.value}
        config_service = MagicMock()
        result = await _resolve_effective_user_auth(instance, "user-1", config_service)
        assert result == {}

    @pytest.mark.asyncio
    async def test_use_admin_auth_resolves_creator_record(self) -> None:
        instance = {"_id": "inst-1", "authMode": MCPAuthMode.API_TOKEN.value, "useAdminAuth": True, "createdBy": "admin-1"}
        config_service = MagicMock()
        config_service.get_config = AsyncMock(return_value={"isAuthenticated": True})
        result = await _resolve_effective_user_auth(instance, "user-2", config_service)
        assert result == {"isAuthenticated": True}
        called_path = config_service.get_config.await_args.args[0]
        assert called_path.endswith("/inst-1/admin-1")

    @pytest.mark.asyncio
    async def test_use_admin_auth_ignored_for_oauth(self) -> None:
        instance = {"_id": "inst-1", "authMode": MCPAuthMode.OAUTH.value, "useAdminAuth": True, "createdBy": "admin-1"}
        config_service = MagicMock()
        config_service.get_config = AsyncMock(return_value={"isAuthenticated": True})
        await _resolve_effective_user_auth(instance, "user-2", config_service)
        called_path = config_service.get_config.await_args.args[0]
        assert called_path.endswith("/inst-1/user-2")

    @pytest.mark.asyncio
    async def test_own_record_when_no_admin_auth(self) -> None:
        instance = {"_id": "inst-1", "authMode": MCPAuthMode.API_TOKEN.value, "useAdminAuth": False}
        config_service = MagicMock()
        config_service.get_config = AsyncMock(return_value={"isAuthenticated": True})
        await _resolve_effective_user_auth(instance, "user-2", config_service)
        called_path = config_service.get_config.await_args.args[0]
        assert called_path.endswith("/inst-1/user-2")

    @pytest.mark.asyncio
    async def test_no_record_returns_none(self) -> None:
        instance = {"_id": "inst-1", "authMode": MCPAuthMode.API_TOKEN.value}
        config_service = MagicMock()
        config_service.get_config = AsyncMock(return_value=None)
        result = await _resolve_effective_user_auth(instance, "user-2", config_service)
        assert result is None


# ---------------------------------------------------------------------------
# _credentials_to_discovery_dict
# ---------------------------------------------------------------------------

class TestCredentialsToDiscoveryDict:
    def test_oauth_extracts_access_token(self) -> None:
        record = {"oauthTokens": {"accessToken": "tok"}}
        result = _credentials_to_discovery_dict(MCPAuthMode.OAUTH.value, record)
        assert result == {"accessToken": "tok"}

    def test_oauth_missing_tokens_returns_none_access_token(self) -> None:
        result = _credentials_to_discovery_dict(MCPAuthMode.OAUTH.value, {})
        assert result == {"accessToken": None}

    def test_non_oauth_returns_credentials_subdict(self) -> None:
        record = {"credentials": {"apiToken": "secret"}}
        result = _credentials_to_discovery_dict(MCPAuthMode.API_TOKEN.value, record)
        assert result == {"apiToken": "secret"}

    def test_non_oauth_empty_record_returns_empty_dict(self) -> None:
        result = _credentials_to_discovery_dict(MCPAuthMode.NONE.value, {})
        assert result == {}


# ---------------------------------------------------------------------------
# _mask_secret
# ---------------------------------------------------------------------------

class TestMaskSecret:
    def test_none_passthrough(self) -> None:
        assert _mask_secret(None) is None

    def test_short_value_fully_masked(self) -> None:
        assert _mask_secret("abcd") == "••••"

    def test_long_value_shows_prefix_and_suffix(self) -> None:
        result = _mask_secret("clientid1234567890")
        assert result.startswith("clie")
        assert result.endswith("7890")
        assert "•" in result


# ---------------------------------------------------------------------------
# _resolve_oauth_endpoints
# ---------------------------------------------------------------------------

class TestResolveOauthEndpoints:
    def test_catalog_instance_prefers_discovered_over_stored_template_urls(self) -> None:
        """Notion/Atlassian catalog templates historically stored the provider's *direct*
        OAuth endpoints, not the ones fronting the MCP server — a live discovery result must
        win for non-custom instances."""
        instance = {
            "isCustom": False,
            "authorizationUrl": "https://stale.example.com/authorize",
            "tokenUrl": "https://stale.example.com/token",
        }
        discovered = DiscoveredOAuthMetadata(
            authorization_endpoint="https://real.example.com/authorize",
            token_endpoint="https://real.example.com/token",
        )
        auth_url, token_url = _resolve_oauth_endpoints(instance, discovered)
        assert auth_url == "https://real.example.com/authorize"
        assert token_url == "https://real.example.com/token"

    def test_catalog_instance_falls_back_to_stored_when_discovery_fails(self) -> None:
        instance = {
            "isCustom": False,
            "authorizationUrl": "https://stale.example.com/authorize",
            "tokenUrl": "https://stale.example.com/token",
        }
        auth_url, token_url = _resolve_oauth_endpoints(instance, None)
        assert auth_url == "https://stale.example.com/authorize"
        assert token_url == "https://stale.example.com/token"

    def test_custom_instance_keeps_admin_configured_url_over_discovery(self) -> None:
        instance = {
            "isCustom": True,
            "authorizationUrl": "https://admin-set.example.com/authorize",
            "tokenUrl": "https://admin-set.example.com/token",
        }
        discovered = DiscoveredOAuthMetadata(
            authorization_endpoint="https://discovered.example.com/authorize",
            token_endpoint="https://discovered.example.com/token",
        )
        auth_url, token_url = _resolve_oauth_endpoints(instance, discovered)
        assert auth_url == "https://admin-set.example.com/authorize"
        assert token_url == "https://admin-set.example.com/token"

    def test_custom_instance_fills_blank_fields_from_discovery(self) -> None:
        instance = {"isCustom": True}
        discovered = DiscoveredOAuthMetadata(
            authorization_endpoint="https://discovered.example.com/authorize",
            token_endpoint="https://discovered.example.com/token",
        )
        auth_url, token_url = _resolve_oauth_endpoints(instance, discovered)
        assert auth_url == "https://discovered.example.com/authorize"
        assert token_url == "https://discovered.example.com/token"


# ---------------------------------------------------------------------------
# _build_oauth_authorization_url — endpoint/client resolution
# ---------------------------------------------------------------------------

class TestBuildOauthAuthorizationUrlDcrReuse:
    def _oauth_instance(self, *, is_custom: bool = False) -> dict:
        return {
            "_id": "inst-1",
            "authMode": MCPAuthMode.OAUTH.value,
            "url": "https://mcp.example.com/v1/sse",
            "scopes": ["read"],
            "isCustom": is_custom,
        }

    def _config_service(self, *, static_client=None, legacy_dcr=None, shared_dcr=None) -> MagicMock:
        async def _get_config(path, default=None, **_kwargs):
            if path.endswith("/oauth-clients/inst-1"):
                return static_client
            if path.endswith("/dcr-clients/inst-1"):
                return shared_dcr
            if path.endswith("/dcr-client"):
                return legacy_dcr
            return default

        svc = MagicMock()
        svc.get_config = AsyncMock(side_effect=_get_config)
        svc.set_config = AsyncMock()
        return svc

    def _patched(self, *, discovered=None, registered=None):
        return (
            patch("app.api.routes.mcp_servers._get_configured_frontend_base_url", new=AsyncMock(return_value="https://app.example.com")),
            patch("app.api.routes.mcp_servers.dcr_module.discover_oauth_metadata", new=AsyncMock(return_value=discovered)),
            patch("app.api.routes.mcp_servers.dcr_module.register_dynamic_client", new=AsyncMock(return_value=registered)),
            patch("app.api.routes.mcp_servers.dcr_module.generate_state", return_value="state-1"),
            patch("app.api.routes.mcp_servers.dcr_module.generate_code_verifier", return_value="verifier-1"),
            patch("app.api.routes.mcp_servers.dcr_module.generate_code_challenge", return_value="challenge-1"),
            patch("app.api.routes.mcp_servers.asyncio.create_task", side_effect=lambda coro: coro.close() or MagicMock()),
        )

    @pytest.mark.asyncio
    async def test_reuses_shared_dcr_client_without_reregistering(self) -> None:
        existing = {
            "clientId": "existing-cid",
            "clientSecret": "existing-secret",
            "registeredAt": 1,
        }
        config_service = self._config_service(shared_dcr=existing)
        discovered = DiscoveredOAuthMetadata(
            authorization_endpoint="https://auth.example.com/authorize",
            token_endpoint="https://auth.example.com/token",
        )

        patches = self._patched(discovered=discovered)
        with patches[0], patches[1] as discover, patches[2] as register, patches[3], patches[4], patches[5], patches[6]:
            result = await _build_oauth_authorization_url(
                config_service, self._oauth_instance(), "inst-1", "user-1", "org-1", "https://app.example.com",
                initiated_by="user-1", owner_type="user",
            )

        discover.assert_awaited_once_with("https://mcp.example.com/v1/sse")
        register.assert_not_awaited()
        written_paths = [call.args[0] for call in config_service.set_config.await_args_list]
        assert not any("dcr-client" in path for path in written_paths)
        assert "client_id=existing-cid" in result["authorizationUrl"]
        assert result["authorizationUrl"].startswith("https://auth.example.com/authorize?")

    @pytest.mark.asyncio
    async def test_legacy_per_owner_dcr_client_still_wins_over_shared(self) -> None:
        """Existing tokens are bound to the legacy per-owner client — it must keep being used
        for that owner even after the shared-client path exists."""
        legacy = {"clientId": "legacy-cid", "clientSecret": "legacy-secret", "registeredAt": 1}
        shared = {"clientId": "shared-cid", "clientSecret": "shared-secret", "registeredAt": 1}
        config_service = self._config_service(legacy_dcr=legacy, shared_dcr=shared)
        discovered = DiscoveredOAuthMetadata(
            authorization_endpoint="https://auth.example.com/authorize",
            token_endpoint="https://auth.example.com/token",
        )

        patches = self._patched(discovered=discovered)
        with patches[0], patches[1], patches[2] as register, patches[3], patches[4], patches[5], patches[6]:
            result = await _build_oauth_authorization_url(
                config_service, self._oauth_instance(), "inst-1", "user-1", "org-1", "https://app.example.com",
                initiated_by="user-1", owner_type="user",
            )

        register.assert_not_awaited()
        assert "client_id=legacy-cid" in result["authorizationUrl"]

    @pytest.mark.asyncio
    async def test_registers_shared_dcr_client_when_none_exists(self) -> None:
        config_service = self._config_service()
        registered = DCRClient(
            client_id="new-cid",
            client_secret="new-secret",
            authorization_url="https://auth.example.com/authorize",
            token_url="https://auth.example.com/token",
            registered_at=1,
        )
        discovered = DiscoveredOAuthMetadata(
            authorization_endpoint="https://auth.example.com/authorize",
            token_endpoint="https://auth.example.com/token",
            registration_endpoint="https://auth.example.com/register",
        )

        patches = self._patched(discovered=discovered, registered=registered)
        with patches[0], patches[1] as discover, patches[2] as register, patches[3], patches[4], patches[5], patches[6]:
            result = await _build_oauth_authorization_url(
                config_service, self._oauth_instance(), "inst-1", "user-1", "org-1", "https://app.example.com",
                initiated_by="user-1", owner_type="user",
            )

        discover.assert_awaited_once()
        register.assert_awaited_once()
        written_paths = [call.args[0] for call in config_service.set_config.await_args_list]
        assert any(path.endswith("/dcr-clients/inst-1") for path in written_paths)
        assert "client_id=new-cid" in result["authorizationUrl"]

    @pytest.mark.asyncio
    async def test_state_record_carries_callback_binding_and_omits_secret(self) -> None:
        config_service = self._config_service(shared_dcr={"clientId": "cid", "clientSecret": "secret", "registeredAt": 1})
        discovered = DiscoveredOAuthMetadata(
            authorization_endpoint="https://auth.example.com/authorize",
            token_endpoint="https://auth.example.com/token",
        )

        patches = self._patched(discovered=discovered)
        with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5], patches[6]:
            await _build_oauth_authorization_url(
                config_service, self._oauth_instance(), "inst-1", "agent-owner", "org-1", "https://app.example.com",
                initiated_by="real-user-1", owner_type="agent",
            )

        state_call = next(c for c in config_service.set_config.await_args_list if "oauth-states" in c.args[0])
        state_record = state_call.args[1]
        assert state_record["initiatedBy"] == "real-user-1"
        assert state_record["ownerType"] == "agent"
        assert state_record["userId"] == "agent-owner"
        assert "clientSecret" not in state_record

    @pytest.mark.asyncio
    async def test_prefers_discovered_endpoints_over_stale_catalog_urls(self) -> None:
        instance = self._oauth_instance(is_custom=False)
        instance["authorizationUrl"] = "https://stale.example.com/authorize"
        instance["tokenUrl"] = "https://stale.example.com/token"
        config_service = self._config_service(shared_dcr={"clientId": "cid", "clientSecret": "secret", "registeredAt": 1})
        discovered = DiscoveredOAuthMetadata(
            authorization_endpoint="https://real.example.com/authorize",
            token_endpoint="https://real.example.com/token",
        )

        patches = self._patched(discovered=discovered)
        with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5], patches[6]:
            result = await _build_oauth_authorization_url(
                config_service, instance, "inst-1", "user-1", "org-1", "https://app.example.com",
                initiated_by="user-1", owner_type="user",
            )

        assert result["authorizationUrl"].startswith("https://real.example.com/authorize?")

    @pytest.mark.asyncio
    async def test_raises_409_when_no_client_and_dcr_unsupported(self) -> None:
        config_service = self._config_service()
        discovered = DiscoveredOAuthMetadata(
            authorization_endpoint="https://auth.example.com/authorize",
            token_endpoint="https://auth.example.com/token",
        )  # no registration_endpoint => DCR unsupported

        patches = self._patched(discovered=discovered)
        with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5], patches[6]:
            with pytest.raises(HTTPException) as exc:
                await _build_oauth_authorization_url(
                    config_service, self._oauth_instance(), "inst-1", "user-1", "org-1", "https://app.example.com",
                    initiated_by="user-1", owner_type="user",
                )

        assert exc.value.status_code == 409
        assert "redirect URI" in str(exc.value.detail) or "https://app.example.com/mcp-servers/oauth/callback/" in str(exc.value.detail)


# ---------------------------------------------------------------------------
# POST /oauth/discover
# ---------------------------------------------------------------------------

class TestDiscoverOauthMetadataEndpoint:
    @pytest.mark.asyncio
    async def test_non_admin_rejected(self) -> None:
        request = _mock_request(user={"userId": "u1", "orgId": "o1"}, app_state={"config_service": MagicMock()})
        with patch("app.api.routes.mcp_servers._check_user_is_admin", new=AsyncMock(return_value=False)):
            with pytest.raises(HTTPException) as exc:
                await discover_oauth_metadata_endpoint(request, OAuthDiscoveryRequest(url="https://mcp.example.com"))
        assert exc.value.status_code == 403

    @pytest.mark.asyncio
    async def test_blocked_target_rejected_as_400(self) -> None:
        request = _mock_request(user={"userId": "u1", "orgId": "o1"}, app_state={"config_service": MagicMock()})
        with (
            patch("app.api.routes.mcp_servers._check_user_is_admin", new=AsyncMock(return_value=True)),
            patch(
                "app.api.routes.mcp_servers.dcr_module.assert_discovery_target_allowed",
                new=AsyncMock(side_effect=DiscoveryBlockedError("blocked host")),
            ),
        ):
            with pytest.raises(HTTPException) as exc:
                await discover_oauth_metadata_endpoint(request, OAuthDiscoveryRequest(url="http://169.254.169.254/"))
        assert exc.value.status_code == 400

    @pytest.mark.asyncio
    async def test_metadata_found_reports_dcr_support(self) -> None:
        request = _mock_request(user={"userId": "u1", "orgId": "o1"}, app_state={"config_service": MagicMock()})
        discovered = DiscoveredOAuthMetadata(
            authorization_endpoint="https://mcp.notion.com/authorize",
            token_endpoint="https://mcp.notion.com/token",
            registration_endpoint="https://mcp.notion.com/register",
            scopes_supported=["default"],
        )
        with (
            patch("app.api.routes.mcp_servers._check_user_is_admin", new=AsyncMock(return_value=True)),
            patch("app.api.routes.mcp_servers.dcr_module.assert_discovery_target_allowed", new=AsyncMock(return_value=None)),
            patch("app.api.routes.mcp_servers.dcr_module.discover_oauth_metadata", new=AsyncMock(return_value=discovered)),
        ):
            result = await discover_oauth_metadata_endpoint(request, OAuthDiscoveryRequest(url="https://mcp.notion.com"))

        assert result["metadataFound"] is True
        assert result["supportsDcr"] is True
        assert result["registrationEndpoint"] == "https://mcp.notion.com/register"

    @pytest.mark.asyncio
    async def test_no_metadata_reports_unknown_not_false(self) -> None:
        """`metadataFound: False` must be distinguishable from a confirmed 'no DCR' — the
        config panel treats them differently (unknown vs. definitely manual)."""
        request = _mock_request(user={"userId": "u1", "orgId": "o1"}, app_state={"config_service": MagicMock()})
        with (
            patch("app.api.routes.mcp_servers._check_user_is_admin", new=AsyncMock(return_value=True)),
            patch("app.api.routes.mcp_servers.dcr_module.assert_discovery_target_allowed", new=AsyncMock(return_value=None)),
            patch("app.api.routes.mcp_servers.dcr_module.discover_oauth_metadata", new=AsyncMock(return_value=None)),
        ):
            result = await discover_oauth_metadata_endpoint(request, OAuthDiscoveryRequest(url="https://custom.example.com"))

        assert result["metadataFound"] is False
        assert result["supportsDcr"] is False


# ---------------------------------------------------------------------------
# GET /oauth/callback — caller binding + secret re-resolution
# ---------------------------------------------------------------------------

class TestHandleOauthCallback:
    def _state_record(self, **overrides) -> dict:
        record = {
            "instanceId": "inst-1",
            "userId": "user-1",
            "orgId": "org-1",
            "initiatedBy": "user-1",
            "ownerType": "user",
            "codeVerifier": "verifier-1",
            "isDcr": True,
            "clientId": "cid-1",
            "tokenUrl": "https://auth.example.com/token",
            "redirectUri": "https://app.example.com/mcp-servers/oauth/callback/",
            "expiresAt": 9_999_999_999_999,
        }
        record.update(overrides)
        return record

    @pytest.mark.asyncio
    async def test_rejects_caller_that_does_not_match_initiator(self) -> None:
        config_service = MagicMock()
        config_service.get_config = AsyncMock(return_value=self._state_record(initiatedBy="user-1"))
        config_service.delete_config = AsyncMock()
        request = _mock_request(user={"userId": "attacker"}, app_state={"config_service": config_service})

        result = await handle_oauth_callback(request, code="code-1", state="state-1", error=None)

        assert result["success"] is False
        assert result["error"] == "caller_mismatch"
        # Single-use: the state must still be deleted even on a rejected callback.
        config_service.delete_config.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_rejects_state_record_missing_initiated_by(self) -> None:
        """A state record predating callback binding (or a tampered one) must be rejected,
        not silently treated as unbound."""
        config_service = MagicMock()
        config_service.get_config = AsyncMock(return_value=self._state_record(initiatedBy=None))
        config_service.delete_config = AsyncMock()
        request = _mock_request(user={"userId": "user-1"}, app_state={"config_service": config_service})

        result = await handle_oauth_callback(request, code="code-1", state="state-1", error=None)

        assert result["success"] is False
        assert result["error"] == "caller_mismatch"

    @pytest.mark.asyncio
    async def test_successful_callback_reresolves_secret_from_client_store(self) -> None:
        state_record = self._state_record()
        config_service = MagicMock()
        config_service.get_config = AsyncMock(
            side_effect=lambda path, default=None, **_kw: (
                state_record if "oauth-states" in path else
                {"clientId": "cid-1", "clientSecret": "fresh-secret"} if "dcr-clients" in path else
                default
            )
        )
        config_service.delete_config = AsyncMock()
        config_service.set_config = AsyncMock()
        request = _mock_request(user={"userId": "user-1"}, app_state={"config_service": config_service})

        tokens = MagicMock()
        tokens.model_dump.return_value = {"accessToken": "at", "refreshToken": "rt"}

        with (
            patch("app.api.routes.mcp_servers.oauth_client_module.exchange_code_for_token", new=AsyncMock(return_value=tokens)) as exchange,
            patch(
                "app.connectors.core.base.token_service.startup_service.startup_service.get_mcp_token_refresh_service",
                return_value=None,
            ),
        ):
            result = await handle_oauth_callback(request, code="code-1", state="state-1", error=None)

        assert result == {"success": True, "instanceId": "inst-1"}
        # The secret passed to the token exchange came from the client store, never from state.
        assert exchange.await_args.kwargs["client_secret"] == "fresh-secret"
        assert "clientSecret" not in state_record

    @pytest.mark.asyncio
    async def test_rotated_client_secret_fails_gracefully_instead_of_500(self) -> None:
        state_record = self._state_record()
        config_service = MagicMock()
        config_service.get_config = AsyncMock(
            side_effect=lambda path, default=None, **_kw: (
                state_record if "oauth-states" in path else default
            )
        )
        config_service.delete_config = AsyncMock()
        request = _mock_request(user={"userId": "user-1"}, app_state={"config_service": config_service})

        result = await handle_oauth_callback(request, code="code-1", state="state-1", error=None)

        assert result["success"] is False
        assert result["error"] == "client_config_changed"

    @pytest.mark.asyncio
    async def test_expired_state_rejected(self) -> None:
        config_service = MagicMock()
        config_service.get_config = AsyncMock(return_value=self._state_record(expiresAt=1))
        config_service.delete_config = AsyncMock()
        request = _mock_request(user={"userId": "user-1"}, app_state={"config_service": config_service})

        result = await handle_oauth_callback(request, code="code-1", state="state-1", error=None)

        assert result["success"] is False
        assert result["error"] == "expired_state"
