"""Unit tests for app.api.routes.mcp_servers."""
from unittest.mock import AsyncMock, MagicMock, patch
from urllib.parse import parse_qs, urlparse

import pytest
from fastapi import HTTPException

from app.agents.constants.mcp_server_constants import (
    get_mcp_credentials_path,
    get_mcp_dcr_client_path,
    get_mcp_step_up_scopes_path,
)
from app.agents.mcp import oauth_client as oauth_client_module
from app.agents.mcp.dcr import DiscoveryBlockedError
from app.agents.mcp.models import (
    DCRClient,
    DiscoveredOAuthMetadata,
    MCPAuthMode,
    MCPServerInstanceConfig,
    MCPTransport,
    OAuthTokens,
)
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
    _mcp_oauth_redirect_uri,
    _resolve_effective_user_auth,
    _resolve_oauth_endpoints,
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
# The OAuth redirect URI
# ---------------------------------------------------------------------------

class TestTheRedirectUriComesFromServerConfig:
    """AUTH-7: one address for everyone, so a shared client's registration fits every sign-in."""

    @staticmethod
    def _config(endpoints: object) -> MagicMock:
        config_service = MagicMock()
        config_service.get_config = AsyncMock(return_value=endpoints)
        return config_service

    @pytest.mark.asyncio
    async def test_a_configured_sub_path_is_kept(self) -> None:
        config_service = self._config({"frontend": {"publicEndpoint": "https://corp.example.com/pipeshub/"}})
        assert await _mcp_oauth_redirect_uri(config_service) == "https://corp.example.com/pipeshub/mcp-servers/oauth/callback/"

    @pytest.mark.asyncio
    async def test_without_configuration_the_default_address_is_used(self) -> None:
        from app.config.constants.service import DefaultEndpoints

        expected = f"{DefaultEndpoints.FRONTEND_ENDPOINT.value.rstrip('/')}/mcp-servers/oauth/callback/"
        assert await _mcp_oauth_redirect_uri(self._config(None)) == expected

    @pytest.mark.asyncio
    @pytest.mark.parametrize("browser_base_url", [
        "https://app.example.com/somewhere-else",
        "https://evil.example.com",
        None,
    ])
    async def test_the_browsers_address_never_shapes_it(self, browser_base_url: "str | None") -> None:
        helpers = TestBuildOauthAuthorizationUrlDcrReuse()
        config_service = helpers._config_service(shared_dcr={"clientId": "cid", "clientSecret": "s", "registeredAt": 1})
        discovered = DiscoveredOAuthMetadata(
            authorization_endpoint="https://auth.example.com/authorize",
            token_endpoint="https://auth.example.com/token",
        )

        patches = helpers._patched(discovered=discovered)
        with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5], patches[6]:
            result = await _build_oauth_authorization_url(
                config_service, helpers._oauth_instance(), "inst-1", "user-1", "org-1", browser_base_url,
                initiated_by="user-1", owner_type="user",
            )

        state_record = next(
            c.args[1] for c in config_service.create_config_if_absent.await_args_list if "oauth-states" in c.args[0]
        )
        assert state_record["redirectUri"] == "https://app.example.com/mcp-servers/oauth/callback/"
        assert "redirect_uri=https%3A%2F%2Fapp.example.com%2Fmcp-servers%2Foauth%2Fcallback%2F" in result["authorizationUrl"]


# ---------------------------------------------------------------------------
# _validate_instance_config
# ---------------------------------------------------------------------------

class TestValidateInstanceConfig:

    @pytest.fixture(autouse=True)
    def _custom_stdio_on(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("MCP_ALLOW_CUSTOM_STDIO", "true")

    def test_known_type_id_passes(self) -> None:
        registry = MagicMock()
        registry.get_template.return_value = MagicMock(
            transport=MCPTransport.STDIO, command="npx", args=[], required_env=[], optional_env=[],
            supported_auth_modes=[MCPAuthMode.API_TOKEN],
        )
        payload = MCPServerInstanceConfig(name="x", type_id="brave_search", transport=MCPTransport.STDIO, auth_mode=MCPAuthMode.API_TOKEN)
        _validate_instance_config(payload, registry)  # should not raise

    def test_unknown_type_id_raises_400(self) -> None:
        registry = MagicMock()
        registry.get_template.return_value = None
        payload = MCPServerInstanceConfig(name="x", type_id="nonexistent", transport=MCPTransport.STDIO, auth_mode=MCPAuthMode.API_TOKEN)
        with pytest.raises(HTTPException) as exc:
            _validate_instance_config(payload, registry)
        assert exc.value.status_code == 400

    def test_custom_stdio_requires_command(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("MCP_ALLOW_CUSTOM_STDIO", "true")
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

    def test_custom_stdio_with_command_passes(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("MCP_ALLOW_CUSTOM_STDIO", "true")
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
        template.transport = MCPTransport.STDIO
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
    async def test_oauth_mode_is_per_caller_even_with_admin_auth_set(self) -> None:
        """`useAdminAuth` only shares api_token/headers credentials (the read path never
        shares OAuth tokens), so users keep managing their own OAuth tokens."""
        instance = {"authMode": MCPAuthMode.OAUTH.value, "useAdminAuth": True}
        request = _mock_request()
        user_context = {"user_id": "u1", "org_id": "o1"}
        with patch("app.api.routes.mcp_servers._check_user_is_admin", new=AsyncMock(return_value=False)):
            await _assert_admin_owns_shared_credential(instance, user_context, request, MagicMock())  # no raise

    @pytest.mark.asyncio
    async def test_shared_api_token_requires_admin(self) -> None:
        instance = {"authMode": MCPAuthMode.API_TOKEN.value, "useAdminAuth": True}
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
        instance = {"_id": "inst-3", "authMode": MCPAuthMode.API_TOKEN.value, "useAdminAuth": True}
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
        # A new record: tools cached with an earlier one are a miss (`tool_cache.credential_stamp`).
        assert record["connectedAt"] == record["updatedAt"]

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

    def test_headers_mode_ignores_a_header_name_in_the_payload(self) -> None:
        """The instance decides which header carries the credential (SEC-9)."""
        instance = {"_id": "inst-1", "authMode": MCPAuthMode.HEADERS.value, "headerName": "X-Default"}
        payload = AuthenticateRequest(headerName="Host", headerValue="val123")
        record = _build_credential_record(instance, payload, "user-1", "org-1")
        assert record["credentials"] == {"headerName": "X-Default", "headerValue": "val123"}

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
    async def test_use_admin_auth_resolves_the_shared_slot(self) -> None:
        instance = {"_id": "inst-1", "authMode": MCPAuthMode.API_TOKEN.value, "useAdminAuth": True, "createdBy": "admin-1"}
        config_service = MagicMock()
        config_service.get_config = AsyncMock(return_value={"isAuthenticated": True})
        result = await _resolve_effective_user_auth(instance, "user-2", config_service)
        assert result == {"isAuthenticated": True}
        called_path = config_service.get_config.await_args.args[0]
        assert called_path.endswith("/inst-1/_shared")

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
        svc.create_config_if_absent = AsyncMock(return_value=True)
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

        (url,), kwargs = discover.await_args
        assert url == "https://mcp.example.com/v1/sse"
        assert kwargs["policy"].private_ok("https://mcp.example.com/token")
        assert not kwargs["policy"].private_ok("https://auth.elsewhere.example/token")
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
        # Created only if absent: a concurrent registration isn't overwritten.
        created_paths = [call.args[0] for call in config_service.create_config_if_absent.await_args_list]
        assert any(path.endswith("/dcr-clients/inst-1") for path in created_paths)
        config_service.set_config.assert_not_awaited()
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

        state_call = next(c for c in config_service.create_config_if_absent.await_args_list if "oauth-states" in c.args[0])
        state_record = state_call.args[1]
        # Stored under the state's hash: store keys are logged.
        assert "state-1" not in state_call.args[0]
        # The store expires abandoned states itself.
        assert state_call.kwargs["ttl_seconds"] == 600
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
    async def test_a_sign_in_address_that_is_not_a_web_url_is_refused_before_any_work(self) -> None:
        # The discovery filter already drops such metadata; this is the route's own backstop.
        config_service = self._config_service()
        discovered = DiscoveredOAuthMetadata(
            authorization_endpoint="javascript:alert(document.domain)//",
            token_endpoint="https://auth.example.com/token",
            registration_endpoint="https://auth.example.com/register",
        )

        patches = self._patched(discovered=discovered)
        with patches[0], patches[1], patches[2] as register, patches[3], patches[4], patches[5], patches[6]:
            with pytest.raises(HTTPException) as exc:
                await _build_oauth_authorization_url(
                    config_service, self._oauth_instance(), "inst-1", "user-1", "org-1", "https://app.example.com",
                    initiated_by="user-1", owner_type="user",
                )

        assert exc.value.status_code == 502
        assert "not a web address" in exc.value.detail
        register.assert_not_awaited()
        config_service.create_config_if_absent.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_a_stored_custom_sign_in_address_that_is_not_a_web_url_is_refused(self) -> None:
        # Records saved before the model validated these fields.
        instance = self._oauth_instance(is_custom=True)
        instance["authorizationUrl"] = "javascript:alert(1)"
        instance["tokenUrl"] = "https://auth.example.com/token"
        config_service = self._config_service(static_client={"clientId": "cid", "clientSecret": "s"})

        patches = self._patched(discovered=None)
        with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5], patches[6]:
            with pytest.raises(HTTPException) as exc:
                await _build_oauth_authorization_url(
                    config_service, instance, "inst-1", "user-1", "org-1", "https://app.example.com",
                    initiated_by="user-1", owner_type="user",
                )

        assert exc.value.status_code == 502
        config_service.create_config_if_absent.assert_not_awaited()

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


class TestWhichClientASignInUses:
    """AUTH-7: the admin's static app first; a shared registration only while it still fits."""

    _helpers = TestBuildOauthAuthorizationUrlDcrReuse()
    REDIRECT = "https://app.example.com/mcp-servers/oauth/callback/"
    DISCOVERED = DiscoveredOAuthMetadata(
        authorization_endpoint="https://auth.example.com/authorize",
        token_endpoint="https://auth.example.com/token",
        registration_endpoint="https://auth.example.com/register",
    )
    NEW_CLIENT = DCRClient(
        client_id="new-cid",
        client_secret="new-secret",
        authorization_url="https://auth.example.com/authorize",
        token_url="https://auth.example.com/token",
        registered_at=2,
        redirect_uri="https://app.example.com/mcp-servers/oauth/callback/",
    )

    async def _sign_in(
        self, config_service: MagicMock, *, discovered: object = None, registered: object = None,
    ) -> tuple[dict, AsyncMock]:
        patches = self._helpers._patched(discovered=discovered or self.DISCOVERED, registered=registered or self.NEW_CLIENT)
        with patches[0], patches[1], patches[2] as register, patches[3], patches[4], patches[5], patches[6]:
            result = await _build_oauth_authorization_url(
                config_service, self._helpers._oauth_instance(), "inst-1", "user-1", "org-1", None,
                initiated_by="user-1", owner_type="user",
            )
        return result, register

    @staticmethod
    def _state_record(config_service: object) -> dict:
        calls = config_service.create_config_if_absent.await_args_list
        return next(c.args[1] for c in calls if "oauth-states" in c.args[0])

    @staticmethod
    def _shared_write(config_service: MagicMock) -> dict:
        (record,) = [c.args[1] for c in config_service.set_config.await_args_list if c.args[0].endswith("/dcr-clients/inst-1")]
        return record

    @pytest.mark.asyncio
    async def test_the_admins_static_app_wins_over_registered_clients(self) -> None:
        config_service = self._helpers._config_service(
            static_client={"clientId": "static-cid", "clientSecret": "s"},
            legacy_dcr={"clientId": "legacy-cid", "clientSecret": "l", "registeredAt": 1},
            shared_dcr={"clientId": "shared-cid", "clientSecret": "sh", "registeredAt": 1},
        )
        result, register = await self._sign_in(config_service)

        assert "client_id=static-cid" in result["authorizationUrl"]
        assert self._state_record(config_service)["isDcr"] is False
        register.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_a_static_app_authenticates_the_way_the_server_lists(self) -> None:
        config_service = self._helpers._config_service(static_client={"clientId": "static-cid", "clientSecret": "s"})
        basic_only = self.DISCOVERED.model_copy(update={"token_endpoint_auth_methods_supported": ["client_secret_basic"]})

        await self._sign_in(config_service, discovered=basic_only)

        assert self._state_record(config_service)["tokenEndpointAuthMethod"] == "client_secret_basic"

    @pytest.mark.asyncio
    async def test_a_new_registration_uses_the_method_it_was_registered_for(self) -> None:
        config_service = self._helpers._config_service()
        basic_only = self.DISCOVERED.model_copy(update={"token_endpoint_auth_methods_supported": ["client_secret_basic"]})
        registered = self.NEW_CLIENT.model_copy(update={"token_endpoint_auth_method": "client_secret_basic"})

        _result, register = await self._sign_in(config_service, discovered=basic_only, registered=registered)

        assert register.await_args.kwargs["auth_methods_supported"] == ["client_secret_basic"]
        assert self._state_record(config_service)["tokenEndpointAuthMethod"] == "client_secret_basic"

    @pytest.mark.asyncio
    async def test_a_client_registered_before_the_method_was_recorded_posts_its_secret(self) -> None:
        config_service = self._helpers._config_service(shared_dcr={
            "clientId": "shared-cid", "clientSecret": "sh", "registeredAt": 1,
            "redirectUri": self.REDIRECT, "clientSecretExpiresAt": 0,
        })

        await self._sign_in(config_service)

        assert self._state_record(config_service)["tokenEndpointAuthMethod"] == "client_secret_post"

    @pytest.mark.asyncio
    async def test_a_provider_without_s256_pkce_is_refused_before_any_client_is_registered(self) -> None:
        config_service = self._helpers._config_service()
        plain_only = self.DISCOVERED.model_copy(update={"code_challenge_methods_supported": ["plain"]})

        patches = self._helpers._patched(discovered=plain_only, registered=self.NEW_CLIENT)
        with patches[0], patches[1], patches[2] as register, patches[3], patches[4], patches[5], patches[6]:
            with pytest.raises(HTTPException) as caught:
                await _build_oauth_authorization_url(
                    config_service, self._helpers._oauth_instance(), "inst-1", "user-1", "org-1", None,
                    initiated_by="user-1", owner_type="user",
                )

        assert caught.value.status_code == 502
        assert "PKCE" in caught.value.detail
        register.assert_not_awaited()
        config_service.set_config.assert_not_awaited()
        config_service.create_config_if_absent.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_a_provider_that_doesnt_list_its_pkce_methods_still_signs_in(self) -> None:
        # Entra ID publishes no code_challenge_methods_supported, yet does PKCE.
        config_service = self._helpers._config_service()

        result, _register = await self._sign_in(config_service)

        assert "code_challenge_method=S256" in result["authorizationUrl"]

    @pytest.mark.asyncio
    async def test_an_administrators_own_endpoints_are_not_judged_by_the_discovered_list(self) -> None:
        config_service = self._helpers._config_service(static_client={"clientId": "static-cid", "clientSecret": "s"})
        instance = {
            **self._helpers._oauth_instance(is_custom=True),
            "authorizationUrl": "https://own.example.com/authorize", "tokenUrl": "https://own.example.com/token",
        }
        plain_only = self.DISCOVERED.model_copy(update={"code_challenge_methods_supported": ["plain"]})

        patches = self._helpers._patched(discovered=plain_only, registered=self.NEW_CLIENT)
        with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5], patches[6]:
            result = await _build_oauth_authorization_url(
                config_service, instance, "inst-1", "user-1", "org-1", None, initiated_by="user-1", owner_type="user",
            )

        assert result["authorizationUrl"].startswith("https://own.example.com/authorize?")

    @staticmethod
    def _with_step_up(config_service: MagicMock, *, granted: str | None) -> MagicMock:
        """`config_service` holding the scopes a 403 asked for and the current tokens' grant."""
        earlier = config_service.get_config.side_effect

        async def _get_config(path: str, default: object = None, **kwargs: object) -> object:
            if path == get_mcp_step_up_scopes_path("inst-1", "user-1"):
                return {"scopes": ["files.write", "read"]}
            if path == get_mcp_credentials_path("inst-1", "user-1"):
                return {"oauthTokens": {"accessToken": "a", "scope": granted}} if granted is not None else None
            return await earlier(path, default, **kwargs)

        config_service.get_config = AsyncMock(side_effect=_get_config)
        return config_service

    @pytest.mark.asyncio
    async def test_scopes_a_403_asked_for_come_on_top_of_the_usual_and_the_granted(self) -> None:
        config_service = self._with_step_up(
            self._helpers._config_service(static_client={"clientId": "static-cid", "clientSecret": "s"}), granted="read profile",
        )

        result, _register = await self._sign_in(config_service)

        # The instance's own "read", what the tokens have, then what the 403 asked for.
        assert parse_qs(urlparse(result["authorizationUrl"]).query)["scope"] == ["read profile files.write"]

    @pytest.mark.asyncio
    async def test_without_usual_scopes_the_grant_is_kept_too(self) -> None:
        config_service = self._with_step_up(
            self._helpers._config_service(static_client={"clientId": "static-cid", "clientSecret": "s"}), granted="profile",
        )
        instance = {**self._helpers._oauth_instance(), "scopes": []}

        patches = self._helpers._patched(discovered=self.DISCOVERED, registered=self.NEW_CLIENT)
        with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5], patches[6]:
            result = await _build_oauth_authorization_url(
                config_service, instance, "inst-1", "user-1", "org-1", None, initiated_by="user-1", owner_type="user",
            )

        assert parse_qs(urlparse(result["authorizationUrl"]).query)["scope"] == ["profile files.write read"]

    DOCUMENT_URL = "https://app.example.com/mcp-servers/oauth/client-metadata.json"
    TAKES_DOCUMENTS = DISCOVERED.model_copy(update={"client_id_metadata_document_supported": True, "issuer": "https://auth.example.com"})

    async def _sign_in_where_documents_work(
        self, config_service: MagicMock, *, served: bool = True, refused: bool = False,
    ) -> tuple[dict, AsyncMock]:
        with patch("app.api.routes.mcp_servers.cimd.document_is_served", new=AsyncMock(return_value=served)), \
             patch("app.api.routes.mcp_servers.cimd.refused_by", new=AsyncMock(return_value=refused)):
            return await self._sign_in(config_service, discovered=self.TAKES_DOCUMENTS)

    @pytest.mark.asyncio
    async def test_a_server_that_takes_client_documents_gets_pipeshubs_instead_of_a_registration(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.delenv("MCP_OAUTH_CLIENT_METADATA_DOCUMENT", raising=False)
        config_service = self._helpers._config_service()

        result, register = await self._sign_in_where_documents_work(config_service)

        register.assert_not_awaited()
        query = parse_qs(urlparse(result["authorizationUrl"]).query)
        assert query["client_id"] == [self.DOCUMENT_URL]
        assert query["code_challenge_method"] == ["S256"]
        state = self._state_record(config_service)
        assert state["clientId"] == self.DOCUMENT_URL
        assert state["clientKind"] == "cimd" and state["authorizationServer"] == "https://auth.example.com"
        assert state["tokenEndpointAuthMethod"] == "none" and state["isDcr"] is False

    @pytest.mark.asyncio
    async def test_a_client_that_works_is_kept_over_the_document(self) -> None:
        static = self._helpers._config_service(static_client={"clientId": "static-cid", "clientSecret": "s"})
        result, _register = await self._sign_in_where_documents_work(static)
        assert "client_id=static-cid" in result["authorizationUrl"]

        shared = self._helpers._config_service(shared_dcr={
            "clientId": "shared-cid", "clientSecret": "sh", "registeredAt": 1, "redirectUri": self.REDIRECT, "clientSecretExpiresAt": 0,
        })
        result, _register = await self._sign_in_where_documents_work(shared)
        assert "client_id=shared-cid" in result["authorizationUrl"]

    @pytest.mark.asyncio
    @pytest.mark.parametrize("case", ["switched-off", "not-served", "refused-before", "http-address"])
    async def test_otherwise_a_client_is_registered(self, monkeypatch: pytest.MonkeyPatch, case: str) -> None:
        monkeypatch.delenv("MCP_OAUTH_CLIENT_METADATA_DOCUMENT", raising=False)
        if case == "switched-off":
            monkeypatch.setenv("MCP_OAUTH_CLIENT_METADATA_DOCUMENT", "false")
        config_service = self._helpers._config_service()
        frontend = "http://app.example.com" if case == "http-address" else "https://app.example.com"

        with patch("app.api.routes.mcp_servers._get_configured_frontend_base_url", new=AsyncMock(return_value=frontend)), \
             patch("app.api.routes.mcp_servers.cimd.document_is_served", new=AsyncMock(return_value=case != "not-served")), \
             patch("app.api.routes.mcp_servers.cimd.refused_by", new=AsyncMock(return_value=case == "refused-before")):
            patches = self._helpers._patched(discovered=self.TAKES_DOCUMENTS, registered=self.NEW_CLIENT)
            with patches[1], patches[2] as register, patches[3], patches[4], patches[5], patches[6]:
                result = await _build_oauth_authorization_url(
                    config_service, self._helpers._oauth_instance(), "inst-1", "user-1", "org-1", None,
                    initiated_by="user-1", owner_type="user",
                )

        register.assert_awaited_once()
        assert "client_id=new-cid" in result["authorizationUrl"]
        assert "clientKind" not in self._state_record(config_service)

    @pytest.mark.asyncio
    async def test_a_static_app_inherited_from_the_parent_org_wins_too(self) -> None:
        config_service = self._helpers._config_service(shared_dcr={"clientId": "shared-cid", "clientSecret": "sh", "registeredAt": 1})
        parent = self._helpers._config_service(static_client={"clientId": "parent-cid", "clientSecret": "p"})
        with patch("app.edition_config.resolve_instance_owner_config_service", new=AsyncMock(return_value=parent)):
            result, _register = await self._sign_in(config_service)

        assert "client_id=parent-cid" in result["authorizationUrl"]

    @pytest.mark.asyncio
    async def test_a_client_registered_for_this_address_is_reused(self) -> None:
        config_service = self._helpers._config_service(shared_dcr={
            "clientId": "shared-cid", "clientSecret": "sh", "registeredAt": 1,
            "redirectUri": self.REDIRECT, "clientSecretExpiresAt": 0,
        })
        result, register = await self._sign_in(config_service)

        register.assert_not_awaited()
        assert "client_id=shared-cid" in result["authorizationUrl"]

    @pytest.mark.asyncio
    async def test_a_client_registered_for_another_address_is_replaced_and_kept_for_its_tokens(self) -> None:
        older = {"clientId": "oldest-cid", "clientSecret": "o"}
        config_service = self._helpers._config_service(shared_dcr={
            "clientId": "old-cid", "clientSecret": "old", "registeredAt": 1,
            "redirectUri": "https://old.example.com/mcp-servers/oauth/callback/", "previousClient": older,
        })
        result, register = await self._sign_in(config_service)

        assert register.await_args.kwargs["redirect_uri"] == self.REDIRECT
        assert "client_id=new-cid" in result["authorizationUrl"]
        stored = self._shared_write(config_service)
        assert stored["clientId"] == "new-cid"
        # Only one generation is kept.
        assert stored["previousClient"]["clientId"] == "old-cid"
        assert "previousClient" not in stored["previousClient"]

    @pytest.mark.asyncio
    async def test_a_client_the_provider_rejected_is_replaced_and_kept_for_its_tokens(self) -> None:
        config_service = self._helpers._config_service(shared_dcr={
            "clientId": "old-cid", "clientSecret": "old", "registeredAt": 1,
            "redirectUri": self.REDIRECT, "rejectedAt": 5,
        })
        result, register = await self._sign_in(config_service)

        register.assert_awaited_once()
        assert "client_id=new-cid" in result["authorizationUrl"]
        stored = self._shared_write(config_service)
        assert "rejectedAt" not in stored
        assert stored["previousClient"]["clientId"] == "old-cid"

    @pytest.mark.asyncio
    async def test_a_client_whose_secret_expired_is_replaced_and_not_kept(self) -> None:
        config_service = self._helpers._config_service(shared_dcr={
            "clientId": "old-cid", "clientSecret": "old", "registeredAt": 1,
            "redirectUri": self.REDIRECT, "clientSecretExpiresAt": 1,
        })
        _result, register = await self._sign_in(config_service)

        register.assert_awaited_once()
        stored = self._shared_write(config_service)
        assert stored["clientId"] == "new-cid"
        assert "previousClient" not in stored

    @pytest.mark.asyncio
    async def test_a_secret_about_to_expire_counts_as_expired(self) -> None:
        from app.utils.time_conversion import get_epoch_timestamp_in_ms

        config_service = self._helpers._config_service(shared_dcr={
            "clientId": "old-cid", "clientSecret": "old", "registeredAt": 1,
            "clientSecretExpiresAt": get_epoch_timestamp_in_ms() // 1000 + 60,
        })
        _result, register = await self._sign_in(config_service)

        register.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_an_unreadable_public_address_fails_the_sign_in_and_replaces_nothing(self) -> None:
        """Read as the default, a store hiccup would make the shared client look registered for
        the wrong address and replace it, signing out everyone who signed in through it."""
        config_service = self._helpers._config_service(shared_dcr={
            "clientId": "shared-cid", "clientSecret": "sh", "registeredAt": 1, "redirectUri": self.REDIRECT,
        })
        stored = config_service.get_config.side_effect

        async def _endpoints_unreadable(path: str, default: object = None, **kwargs: object) -> object:
            if path == "/services/endpoints":
                raise ConnectionError("store down")
            return await stored(path, default, **kwargs)
        config_service.get_config.side_effect = _endpoints_unreadable

        patches = self._helpers._patched(discovered=self.DISCOVERED, registered=self.NEW_CLIENT)
        # patches[0] would stand in for the address; this test reads the real one.
        with patches[1], patches[2] as register, patches[3], patches[4], patches[5], patches[6]:
            with pytest.raises(HTTPException) as exc:
                await _build_oauth_authorization_url(
                    config_service, self._helpers._oauth_instance(), "inst-1", "user-1", "org-1", None,
                    initiated_by="user-1", owner_type="user",
                )

        assert exc.value.status_code == 503
        register.assert_not_awaited()
        config_service.set_config.assert_not_awaited()
        config_service.create_config_if_absent.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_a_client_registered_at_the_same_moment_is_used_instead_of_ours(self) -> None:
        from tests.unit.api.routes.mcp_route_fakes import FakeConfigService

        winner = {"clientId": "winner-cid", "clientSecret": "w", "registeredAt": 1, "redirectUri": self.REDIRECT}

        class _RacingStore(FakeConfigService):
            async def create_config_if_absent(self, key: str, value: object, *, ttl_seconds: "int | None" = None) -> bool:
                if key.endswith("/dcr-clients/inst-1"):
                    self.data.setdefault(key, winner)
                return await super().create_config_if_absent(key, value, ttl_seconds=ttl_seconds)

        store = _RacingStore()
        result, register = await self._sign_in(store)

        register.assert_awaited_once()
        assert "client_id=winner-cid" in result["authorizationUrl"]
        assert store.data["/services/mcp/dcr-clients/inst-1"] == winner


# ---------------------------------------------------------------------------
# POST /oauth/discover
# ---------------------------------------------------------------------------

class TestDiscoverOauthMetadataEndpoint:
    @pytest.mark.asyncio
    @pytest.mark.parametrize("document,expected", [("https://app.example.com/mcp-servers/oauth/client-metadata.json", True), (None, False)])
    async def test_a_client_document_needs_no_app_like_registration(self, document: str | None, expected: bool) -> None:
        """The admin form asks for an OAuth app only when PipesHub can't get a client by itself."""
        request = _mock_request(user={"userId": "u1", "orgId": "o1"}, app_state={"config_service": MagicMock()})
        documents_only = DiscoveredOAuthMetadata(
            authorization_endpoint="https://auth.example.com/authorize", token_endpoint="https://auth.example.com/token",
            client_id_metadata_document_supported=True,
        )
        with (
            patch("app.api.routes.mcp_servers._check_user_is_admin", new=AsyncMock(return_value=True)),
            patch("app.api.routes.mcp_servers.dcr_module.assert_discovery_target_allowed", new=AsyncMock()),
            patch("app.api.routes.mcp_servers.dcr_module.discover_oauth_metadata", new=AsyncMock(return_value=documents_only)),
            patch("app.api.routes.mcp_servers._displayed_redirect_uri", new=AsyncMock(return_value="https://app.example.com/mcp-servers/oauth/callback/")),
            patch("app.api.routes.mcp_servers._client_metadata_document", new=AsyncMock(return_value=document)),
        ):
            result = await discover_oauth_metadata_endpoint(request, OAuthDiscoveryRequest(url="https://mcp.example.com"))

        assert result["supportsDcr"] is expected
        assert result["registrationEndpoint"] is None

    @pytest.mark.asyncio
    async def test_non_admin_probes_under_the_public_only_policy(self) -> None:
        """Users probe for their personal servers, which may only be public."""
        request = _mock_request(user={"userId": "u1", "orgId": "o1"}, app_state={"config_service": MagicMock()})
        with (
            patch("app.api.routes.mcp_servers._check_user_is_admin", new=AsyncMock(return_value=False)),
            patch("app.api.routes.mcp_servers.dcr_module.assert_discovery_target_allowed", new=AsyncMock()) as mock_check,
            patch("app.api.routes.mcp_servers.dcr_module.discover_oauth_metadata", new=AsyncMock(return_value=None)) as mock_discover,
        ):
            await discover_oauth_metadata_endpoint(request, OAuthDiscoveryRequest(url="https://mcp.example.com"))
        mock_check.assert_awaited_once_with("https://mcp.example.com", allow_private=False)
        assert not mock_discover.await_args.kwargs["policy"].private_ok("https://mcp.example.com")

    @pytest.mark.asyncio
    async def test_non_admin_private_url_rejected_as_400(self) -> None:
        request = _mock_request(user={"userId": "u1", "orgId": "o1"}, app_state={"config_service": MagicMock()})
        with patch("app.api.routes.mcp_servers._check_user_is_admin", new=AsyncMock(return_value=False)):
            with pytest.raises(HTTPException) as exc:
                await discover_oauth_metadata_endpoint(request, OAuthDiscoveryRequest(url="http://10.0.0.5/mcp"))
        assert exc.value.status_code == 400

    @pytest.mark.asyncio
    async def test_admin_probes_under_the_deployment_policy(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("MCP_ALLOW_PRIVATE_NETWORK_URLS", raising=False)
        request = _mock_request(user={"userId": "u1", "orgId": "o1"}, app_state={"config_service": MagicMock()})
        with (
            patch("app.api.routes.mcp_servers._check_user_is_admin", new=AsyncMock(return_value=True)),
            patch("app.api.routes.mcp_servers.dcr_module.discover_oauth_metadata", new=AsyncMock(return_value=None)) as mock_discover,
        ):
            await discover_oauth_metadata_endpoint(request, OAuthDiscoveryRequest(url="http://10.0.0.5/mcp"))
        policy = mock_discover.await_args.kwargs["policy"]
        assert policy.private_ok("http://10.0.0.5/mcp")
        assert not policy.private_ok("http://10.0.3.7/register")

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
        config_service = MagicMock()
        config_service.get_config = AsyncMock(return_value=None)
        request = _mock_request(user={"userId": "u1", "orgId": "o1"}, app_state={"config_service": config_service})
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
        # The address to register with the provider, as the server will send it.
        assert result["redirectUri"].endswith("/mcp-servers/oauth/callback/")

    @pytest.mark.asyncio
    async def test_no_metadata_reports_unknown_not_false(self) -> None:
        """`metadataFound: False` must be distinguishable from a confirmed 'no DCR' — the
        config panel treats them differently (unknown vs. definitely manual)."""
        config_service = MagicMock()
        config_service.get_config = AsyncMock(return_value=None)
        request = _mock_request(user={"userId": "u1", "orgId": "o1"}, app_state={"config_service": config_service})
        with (
            patch("app.api.routes.mcp_servers._check_user_is_admin", new=AsyncMock(return_value=True)),
            patch("app.api.routes.mcp_servers.dcr_module.assert_discovery_target_allowed", new=AsyncMock(return_value=None)),
            patch("app.api.routes.mcp_servers.dcr_module.discover_oauth_metadata", new=AsyncMock(return_value=None)),
        ):
            result = await discover_oauth_metadata_endpoint(request, OAuthDiscoveryRequest(url="https://custom.example.com"))

        assert result["metadataFound"] is False
        assert result["supportsDcr"] is False
        assert result["redirectUri"].endswith("/mcp-servers/oauth/callback/")

    @pytest.mark.asyncio
    async def test_an_unreadable_address_leaves_the_redirect_uri_out_instead_of_failing(self) -> None:
        config_service = MagicMock()
        config_service.get_config = AsyncMock(side_effect=RuntimeError("store down"))
        request = _mock_request(user={"userId": "u1", "orgId": "o1"}, app_state={"config_service": config_service})
        with (
            patch("app.api.routes.mcp_servers._check_user_is_admin", new=AsyncMock(return_value=True)),
            patch("app.api.routes.mcp_servers.dcr_module.assert_discovery_target_allowed", new=AsyncMock(return_value=None)),
            patch("app.api.routes.mcp_servers.dcr_module.discover_oauth_metadata", new=AsyncMock(return_value=None)),
        ):
            result = await discover_oauth_metadata_endpoint(request, OAuthDiscoveryRequest(url="https://custom.example.com"))

        assert result["redirectUri"] is None


# ---------------------------------------------------------------------------
# GET /oauth/callback — caller binding + secret re-resolution
# ---------------------------------------------------------------------------


@pytest.fixture
def _instance_in_callers_org():
    """The handler under test looks the instance up (org-scoped); this one exists."""
    with patch("app.api.routes.mcp_servers._get_org_instance", new=AsyncMock(return_value={"_id": "inst-1"})):
        yield


@pytest.mark.usefixtures("_instance_in_callers_org")
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
        config_service.create_config_if_absent = AsyncMock(return_value=True)
        request = _mock_request(user={"userId": "attacker"}, app_state={"config_service": config_service})

        result = await handle_oauth_callback(request, code="code-1", state="state-1", error=None)

        assert result["success"] is False
        assert result["error"] == "caller_mismatch"
        # Someone else's callback must not consume the state: that would cancel the
        # initiator's sign-in (SEC-13).
        config_service.delete_config.assert_not_awaited()
        config_service.create_config_if_absent.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_rejects_state_record_missing_initiated_by(self) -> None:
        """A state record predating callback binding (or a tampered one) must be rejected,
        not silently treated as unbound."""
        config_service = MagicMock()
        config_service.get_config = AsyncMock(return_value=self._state_record(initiatedBy=None))
        config_service.delete_config = AsyncMock()
        config_service.create_config_if_absent = AsyncMock(return_value=True)
        request = _mock_request(user={"userId": "user-1", "orgId": "org-1"}, app_state={"config_service": config_service})

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
        config_service.create_config_if_absent = AsyncMock(return_value=True)
        config_service.set_config = AsyncMock()
        request = _mock_request(user={"userId": "user-1", "orgId": "org-1"}, app_state={"config_service": config_service})

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

    DOCUMENT_URL = "https://app.example.com/mcp-servers/oauth/client-metadata.json"

    def _document_sign_in(self, *, initiated_by: str = "user-1") -> tuple[MagicMock, dict]:
        state_record = self._state_record(
            isDcr=False, clientId=self.DOCUMENT_URL, tokenEndpointAuthMethod="none",
            clientKind="cimd", authorizationServer="https://auth.example.com", initiatedBy=initiated_by,
        )
        config_service = MagicMock()
        config_service.get_config = AsyncMock(
            side_effect=lambda path, default=None, **_kw: state_record if "oauth-states" in path else default,
        )
        config_service.delete_config = AsyncMock()
        config_service.create_config_if_absent = AsyncMock(return_value=True)
        config_service.set_config = AsyncMock()
        return config_service, state_record

    @pytest.mark.asyncio
    async def test_a_client_document_sign_in_sends_no_secret(self) -> None:
        config_service, _state = self._document_sign_in()
        request = _mock_request(user={"userId": "user-1", "orgId": "org-1"}, app_state={"config_service": config_service})
        tokens = OAuthTokens(access_token="at", refresh_token="rt", token_endpoint_auth_method="none")

        with (
            patch("app.api.routes.mcp_servers.oauth_client_module.exchange_code_for_token", new=AsyncMock(return_value=tokens)) as exchange,
            patch(
                "app.connectors.core.base.token_service.startup_service.startup_service.get_mcp_token_refresh_service",
                return_value=None,
            ),
        ):
            result = await handle_oauth_callback(request, code="code-1", state="state-1", error=None)

        assert result == {"success": True, "instanceId": "inst-1"}
        kwargs = exchange.await_args.kwargs
        assert (kwargs["client_id"], kwargs["client_secret"], kwargs["auth_method"]) == (self.DOCUMENT_URL, None, "none")
        saved = next(c.args[1] for c in config_service.set_config.await_args_list if "credentials" in c.args[0])
        assert saved["oauthTokens"]["clientId"] == self.DOCUMENT_URL

    @pytest.mark.asyncio
    async def test_a_provider_that_rejects_the_document_at_the_exchange_gets_dcr_next_time(self) -> None:
        config_service, _state = self._document_sign_in()
        request = _mock_request(user={"userId": "user-1", "orgId": "org-1"}, app_state={"config_service": config_service})
        rejected = oauth_client_module.MCPOAuthError("invalid_client", error_code="invalid_client")

        with patch("app.api.routes.mcp_servers.oauth_client_module.exchange_code_for_token", new=AsyncMock(side_effect=rejected)), \
             patch("app.api.routes.mcp_servers.cimd.remember_refusal", new=AsyncMock()) as remember:
            result = await handle_oauth_callback(request, code="code-1", state="state-1", error=None)

        assert result["error"] == "token_exchange_failed"
        remember.assert_awaited_once_with(config_service, "https://auth.example.com")

    @pytest.mark.asyncio
    @pytest.mark.parametrize("error,started_by,remembered", [
        ("invalid_client", "user-1", True),
        ("unauthorized_client", "user-1", True),
        ("access_denied", "user-1", False),
        ("invalid_client", "someone-else", False),
    ])
    async def test_a_provider_that_refuses_the_document_on_the_way_back(
        self, error: str, started_by: str, remembered: bool,
    ) -> None:
        config_service, _state = self._document_sign_in(initiated_by=started_by)
        request = _mock_request(user={"userId": "user-1", "orgId": "org-1"}, app_state={"config_service": config_service})

        with patch("app.api.routes.mcp_servers.cimd.remember_refusal", new=AsyncMock()) as remember:
            result = await handle_oauth_callback(request, code=None, state="state-1", error=error)

        assert result["success"] is False and result["error"] == error
        assert remember.await_count == (1 if remembered else 0)
        # The state is left for the sign-in it belongs to.
        config_service.delete_config.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_a_sign_in_clears_the_scopes_a_403_asked_for(self) -> None:
        state_record = self._state_record()
        config_service = MagicMock()
        config_service.get_config = AsyncMock(
            side_effect=lambda path, default=None, **_kw: (
                state_record if "oauth-states" in path else
                {"clientId": "cid-1", "clientSecret": "s"} if "dcr-clients" in path else
                default
            )
        )
        config_service.delete_config = AsyncMock()
        config_service.create_config_if_absent = AsyncMock(return_value=True)
        config_service.set_config = AsyncMock()
        request = _mock_request(user={"userId": "user-1", "orgId": "org-1"}, app_state={"config_service": config_service})

        with (
            patch("app.api.routes.mcp_servers.oauth_client_module.exchange_code_for_token", new=AsyncMock(return_value=OAuthTokens(access_token="at"))),
            patch(
                "app.connectors.core.base.token_service.startup_service.startup_service.get_mcp_token_refresh_service",
                return_value=None,
            ),
        ):
            assert (await handle_oauth_callback(request, code="code-1", state="state-1", error=None))["success"] is True

        deleted = [c.args[0] for c in config_service.delete_config.await_args_list]
        assert get_mcp_step_up_scopes_path("inst-1", "user-1") in deleted

    @pytest.mark.asyncio
    async def test_the_code_exchange_authenticates_the_way_the_sign_in_chose(self) -> None:
        state_record = self._state_record(tokenEndpointAuthMethod="client_secret_basic")
        config_service = MagicMock()
        config_service.get_config = AsyncMock(
            side_effect=lambda path, default=None, **_kw: (
                state_record if "oauth-states" in path else
                {"clientId": "cid-1", "clientSecret": "fresh-secret"} if "dcr-clients" in path else
                default
            )
        )
        config_service.delete_config = AsyncMock()
        config_service.create_config_if_absent = AsyncMock(return_value=True)
        config_service.set_config = AsyncMock()
        request = _mock_request(user={"userId": "user-1", "orgId": "org-1"}, app_state={"config_service": config_service})
        tokens = OAuthTokens(access_token="at", refresh_token="rt", token_endpoint_auth_method="client_secret_basic")

        with (
            patch("app.api.routes.mcp_servers.oauth_client_module.exchange_code_for_token", new=AsyncMock(return_value=tokens)) as exchange,
            patch(
                "app.connectors.core.base.token_service.startup_service.startup_service.get_mcp_token_refresh_service",
                return_value=None,
            ),
        ):
            result = await handle_oauth_callback(request, code="code-1", state="state-1", error=None)

        assert result["success"] is True
        assert exchange.await_args.kwargs["auth_method"] == "client_secret_basic"
        saved = next(c.args[1] for c in config_service.set_config.await_args_list if "credentials" in c.args[0])
        assert saved["oauthTokens"]["tokenEndpointAuthMethod"] == "client_secret_basic"
        assert saved["oauthTokens"]["clientId"] == "cid-1"

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
        config_service.create_config_if_absent = AsyncMock(return_value=True)
        request = _mock_request(user={"userId": "user-1", "orgId": "org-1"}, app_state={"config_service": config_service})

        result = await handle_oauth_callback(request, code="code-1", state="state-1", error=None)

        assert result["success"] is False
        assert result["error"] == "client_config_changed"

    @pytest.mark.asyncio
    async def test_expired_state_rejected(self) -> None:
        config_service = MagicMock()
        config_service.get_config = AsyncMock(return_value=self._state_record(expiresAt=1))
        config_service.delete_config = AsyncMock()
        config_service.create_config_if_absent = AsyncMock(return_value=True)
        request = _mock_request(user={"userId": "user-1", "orgId": "org-1"}, app_state={"config_service": config_service})

        result = await handle_oauth_callback(request, code="code-1", state="state-1", error=None)

        assert result["success"] is False
        assert result["error"] == "expired_state"

    def _callback_store(self, state_record: dict, clients: dict) -> MagicMock:
        def _get(path: str, default: object = None, **_kw: object) -> object:
            if "oauth-states" in path:
                return state_record
            return next((record for suffix, record in clients.items() if path.endswith(suffix)), default)

        config_service = MagicMock()
        config_service.get_config = AsyncMock(side_effect=_get)
        config_service.delete_config = AsyncMock()
        config_service.create_config_if_absent = AsyncMock(return_value=True)
        config_service.set_config = AsyncMock()
        return config_service

    async def _call_back(self, config_service: MagicMock, exchange: AsyncMock) -> dict:
        request = _mock_request(user={"userId": "user-1", "orgId": "org-1"}, app_state={"config_service": config_service})
        with (
            patch("app.api.routes.mcp_servers.oauth_client_module.exchange_code_for_token", new=exchange),
            patch(
                "app.connectors.core.base.token_service.startup_service.startup_service.get_mcp_token_refresh_service",
                return_value=None,
            ),
        ):
            return await handle_oauth_callback(request, code="code-1", state="state-1", error=None)

    @pytest.mark.asyncio
    async def test_the_tokens_record_the_client_that_issued_them(self) -> None:
        from app.agents.mcp.models import OAuthTokens

        config_service = self._callback_store(self._state_record(), {"/dcr-clients/inst-1": {"clientId": "cid-1", "clientSecret": "s"}})
        exchange = AsyncMock(return_value=OAuthTokens(access_token="at", refresh_token="rt"))

        result = await self._call_back(config_service, exchange)

        assert result["success"] is True
        (record,) = [c.args[1] for c in config_service.set_config.await_args_list if c.args[0] == get_mcp_credentials_path("inst-1", "user-1")]
        assert record["oauthTokens"]["clientId"] == "cid-1"
        assert record["connectedAt"] == record["updatedAt"]

    @pytest.mark.asyncio
    async def test_a_registered_client_the_provider_rejects_is_retired(self) -> None:
        from app.agents.mcp.oauth_client import MCPOAuthError

        config_service = self._callback_store(self._state_record(), {"/dcr-clients/inst-1": {"clientId": "cid-1", "clientSecret": "s"}})
        exchange = AsyncMock(side_effect=MCPOAuthError("rejected", error_code="invalid_client"))

        result = await self._call_back(config_service, exchange)

        assert result["error"] == "token_exchange_failed"
        (marked,) = [c.args[1] for c in config_service.set_config.await_args_list if c.args[0] == "/services/mcp/dcr-clients/inst-1"]
        assert marked["clientId"] == "cid-1"
        assert marked["rejectedAt"] > 0

    @pytest.mark.asyncio
    async def test_a_rejected_static_app_is_not_touched(self) -> None:
        from app.agents.mcp.oauth_client import MCPOAuthError

        config_service = self._callback_store(
            self._state_record(isDcr=False, clientId="static-cid"),
            {"/oauth-clients/inst-1": {"clientId": "static-cid", "clientSecret": "s"}},
        )
        exchange = AsyncMock(side_effect=MCPOAuthError("rejected", error_code="invalid_client"))

        result = await self._call_back(config_service, exchange)

        assert result["error"] == "token_exchange_failed"
        deleted = [c.args[0] for c in config_service.delete_config.await_args_list]
        written = [c.args[0] for c in config_service.set_config.await_args_list]
        assert not any("client" in path for path in deleted + written)

    @pytest.mark.asyncio
    async def test_another_exchange_failure_keeps_the_registered_client(self) -> None:
        from app.agents.mcp.oauth_client import MCPOAuthError

        config_service = self._callback_store(self._state_record(), {"/dcr-clients/inst-1": {"clientId": "cid-1", "clientSecret": "s"}})
        exchange = AsyncMock(side_effect=MCPOAuthError("bad code", error_code="invalid_grant"))

        await self._call_back(config_service, exchange)

        deleted = [c.args[0] for c in config_service.delete_config.await_args_list]
        written = [c.args[0] for c in config_service.set_config.await_args_list]
        assert not any("dcr-client" in path for path in deleted + written)

    @pytest.mark.asyncio
    async def test_a_sign_in_started_with_a_since_retired_client_completes(self) -> None:
        from app.agents.mcp.models import OAuthTokens

        shared = {"clientId": "new-cid", "clientSecret": "n", "previousClient": {"clientId": "cid-1", "clientSecret": "retired-secret"}}
        config_service = self._callback_store(self._state_record(), {"/dcr-clients/inst-1": shared})
        exchange = AsyncMock(return_value=OAuthTokens(access_token="at"))

        result = await self._call_back(config_service, exchange)

        assert result["success"] is True
        assert exchange.await_args.kwargs["client_secret"] == "retired-secret"


class TestSignInAsksForTheServersScopesAndNamesIt:
    """AUTH-5/8: the sign-in requests the MCP server's own scopes, never the authorization
    server's whole list, and names the server as the RFC 8707 resource."""

    # Composed, not inherited: a subclass would run every inherited test again.
    _helpers = TestBuildOauthAuthorizationUrlDcrReuse()

    async def _sign_in(self, instance: dict, discovered: DiscoveredOAuthMetadata) -> tuple[dict, dict]:
        from urllib.parse import parse_qs, urlparse

        config_service = self._helpers._config_service(shared_dcr={"clientId": "cid", "clientSecret": "s", "registeredAt": 1})
        patches = self._helpers._patched(discovered=discovered)
        with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5], patches[6]:
            result = await _build_oauth_authorization_url(
                config_service, instance, "inst-1", "user-1", "org-1", "https://app.example.com",
                initiated_by="user-1", owner_type="user",
            )
        state_record = config_service.create_config_if_absent.await_args.args[1]
        return parse_qs(urlparse(result["authorizationUrl"]).query), state_record

    @staticmethod
    def _discovered(**overrides: object) -> DiscoveredOAuthMetadata:
        return DiscoveredOAuthMetadata(
            authorization_endpoint="https://auth.example.com/authorize",
            token_endpoint="https://auth.example.com/token",
            scopes_supported=["openid", "mail.read", "files.readwrite.all"],
            **overrides,
        )

    @pytest.mark.asyncio
    async def test_the_servers_own_scopes_and_resource(self) -> None:
        instance = {**self._helpers._oauth_instance(), "scopes": []}
        query, state_record = await self._sign_in(
            instance, self._discovered(resource="https://mcp.example.com/v1/sse", resource_scopes=["mcp:read"]),
        )

        assert query["scope"] == ["mcp:read"]
        assert query["resource"] == ["https://mcp.example.com/v1/sse"]
        assert state_record["resource"] == "https://mcp.example.com/v1/sse"

    @pytest.mark.asyncio
    async def test_without_the_servers_metadata_no_scope_and_no_resource(self) -> None:
        instance = {**self._helpers._oauth_instance(), "scopes": []}
        query, state_record = await self._sign_in(instance, self._discovered())

        assert "scope" not in query
        assert "resource" not in query
        assert state_record["resource"] is None

    @pytest.mark.asyncio
    async def test_scopes_an_admin_set_still_win(self) -> None:
        query, _ = await self._sign_in(
            self._helpers._oauth_instance(), self._discovered(resource="https://mcp.example.com/v1/sse", resource_scopes=["mcp:read"]),
        )

        assert query["scope"] == ["read"]

    @pytest.mark.asyncio
    async def test_the_scopes_the_servers_401_asked_for_come_before_its_metadatas(self) -> None:
        instance = {**self._helpers._oauth_instance(), "scopes": []}
        discovered = self._discovered(
            resource="https://mcp.example.com/v1/sse", resource_scopes=["mcp:read", "mcp:write"], challenge_scopes=["mcp:read"],
        )
        query, _ = await self._sign_in(instance, discovered)

        assert query["scope"] == ["mcp:read"]

    @pytest.mark.asyncio
    async def test_scopes_an_admin_set_win_over_the_401s_too(self) -> None:
        query, _ = await self._sign_in(self._helpers._oauth_instance(), self._discovered(challenge_scopes=["mcp:all"]))

        assert query["scope"] == ["read"]


class TestTheSignInStateBelongsToItsInitiator:
    """SEC-13: a state value in the wrong hands can't cancel the sign-in, and an agent's
    sign-in is saved only while the caller can still edit the agent."""

    _helpers = TestHandleOauthCallback()

    def _config_service(self, state_record: dict) -> MagicMock:
        config_service = MagicMock()
        config_service.get_config = AsyncMock(
            side_effect=lambda path, default=None, **_kw: (
                state_record if "oauth-states" in path else
                {"clientId": "cid-1", "clientSecret": "secret"} if "dcr-clients" in path or "dcr-client" in path else
                {"_id": "inst-1", "orgId": "org-1", "authMode": "oauth", "url": "https://mcp.example.com/mcp"}
                if "/instances/" in path else default
            )
        )
        config_service.delete_config = AsyncMock()
        config_service.create_config_if_absent = AsyncMock(return_value=True)
        config_service.set_config = AsyncMock()
        return config_service

    @staticmethod
    def _tokens() -> MagicMock:
        tokens = MagicMock()
        tokens.model_dump.return_value = {"accessToken": "at", "refreshToken": "rt"}
        return tokens

    @pytest.mark.asyncio
    async def test_the_initiator_still_completes_after_someone_else_tried_the_state(self) -> None:
        config_service = self._config_service(self._helpers._state_record())
        attacker = _mock_request(user={"userId": "attacker", "orgId": "org-1"}, app_state={"config_service": config_service})
        owner = _mock_request(user={"userId": "user-1", "orgId": "org-1"}, app_state={"config_service": config_service})

        with (
            patch("app.api.routes.mcp_servers.oauth_client_module.exchange_code_for_token", new=AsyncMock(return_value=self._tokens())),
            patch(
                "app.connectors.core.base.token_service.startup_service.startup_service.get_mcp_token_refresh_service",
                return_value=None,
            ),
        ):
            rejected = await handle_oauth_callback(attacker, code="code-1", state="state-1", error=None)
            completed = await handle_oauth_callback(owner, code="code-1", state="state-1", error=None)

        assert rejected["error"] == "caller_mismatch"
        assert completed == {"success": True, "instanceId": "inst-1"}
        config_service.create_config_if_absent.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_an_agent_sign_in_is_not_saved_once_edit_access_is_gone(self) -> None:
        from fastapi import HTTPException

        config_service = self._config_service(self._helpers._state_record(ownerType="agent", userId="agent-1"))
        request = _mock_request(user={"userId": "user-1", "orgId": "org-1"}, app_state={"config_service": config_service})

        with (
            patch(
                "app.api.routes.mcp_servers._require_mcp_agent_edit_access",
                new=AsyncMock(side_effect=HTTPException(status_code=403, detail="no")),
            ) as access,
            patch("app.api.routes.mcp_servers.oauth_client_module.exchange_code_for_token", new=AsyncMock()) as exchange,
        ):
            result = await handle_oauth_callback(request, code="code-1", state="state-1", error=None)

        assert result["error"] == "agent_access_revoked"
        access.assert_awaited_once()
        assert access.await_args.args[0] == "agent-1"
        exchange.assert_not_awaited()
        config_service.set_config.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_an_agent_sign_in_by_an_editor_is_saved(self) -> None:
        config_service = self._config_service(self._helpers._state_record(ownerType="agent", userId="agent-1"))
        request = _mock_request(user={"userId": "user-1", "orgId": "org-1"}, app_state={"config_service": config_service})

        with (
            patch("app.api.routes.mcp_servers._require_mcp_agent_edit_access", new=AsyncMock(return_value={"can_edit": True})),
            patch("app.api.routes.mcp_servers.oauth_client_module.exchange_code_for_token", new=AsyncMock(return_value=self._tokens())),
            patch(
                "app.connectors.core.base.token_service.startup_service.startup_service.get_mcp_token_refresh_service",
                return_value=None,
            ),
        ):
            result = await handle_oauth_callback(request, code="code-1", state="state-1", error=None)

        assert result == {"success": True, "instanceId": "inst-1"}
        saved_path = config_service.set_config.await_args.args[0]
        assert saved_path.endswith("/inst-1/agent-1")


class TestCredentialInputIsBounded:
    """SEC-9: credentials are bounded, a header value is one line, and a shared admin credential
    only exists for token or header sign-in."""

    def test_a_header_value_with_a_line_break_is_refused(self) -> None:
        from pydantic import ValidationError

        with pytest.raises(ValidationError, match="single line"):
            AuthenticateRequest(headerValue="token\r\nX-Injected: 1")

    @pytest.mark.parametrize("payload", [
        {"apiToken": "t" * 16_385},
        {"headerValue": "v" * 16_385},
        {"env": {f"K{n}": "v" for n in range(65)}},
        {"env": {"K": "v" * 16_385}},
    ])
    def test_oversized_credentials_are_refused(self, payload: dict) -> None:
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            AuthenticateRequest.model_validate(payload)

    @pytest.mark.parametrize("auth_mode", ["oauth", "none"])
    def test_a_shared_admin_credential_needs_token_or_header_sign_in(self, auth_mode: str) -> None:
        from fastapi import HTTPException

        from app.agents.mcp.models import MCPServerInstanceConfig
        from app.api.routes.mcp_servers import _validate_instance_config

        payload = MCPServerInstanceConfig.model_validate({
            "name": "S", "transport": "streamable_http", "authMode": auth_mode,
            "url": "https://mcp.example.com/mcp", "useAdminAuth": True,
        })
        with pytest.raises(HTTPException) as exc_info:
            _validate_instance_config(payload, MagicMock())
        assert exc_info.value.status_code == 400
        assert "only to API token or header sign-in" in exc_info.value.detail
