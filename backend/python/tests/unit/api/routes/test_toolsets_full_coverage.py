import base64
import json
import logging
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException


# ---------------------------------------------------------------------------
# Shared helpers for route-handler tests
# ---------------------------------------------------------------------------

def _make_request(user_id="u1", org_id="o1", body_dict=None, headers=None):
    """Create a mock FastAPI request."""
    req = MagicMock()
    req.state.user = {"userId": user_id, "orgId": org_id}
    req.headers = headers or {"authorization": "Bearer test"}
    if body_dict is not None:
        req.body = AsyncMock(return_value=json.dumps(body_dict).encode())
    else:
        req.body = AsyncMock(return_value=b'{}')
    return req


def _make_registry(toolset_type="jira", supported_auth=None, oauth_cfg=None, tools=None, internal=False):
    """Create a mock toolset registry."""
    if supported_auth is None:
        supported_auth = ["API_TOKEN"]
    if tools is None:
        tools = []
    registry = MagicMock()
    metadata = {
        "name": toolset_type,
        "display_name": toolset_type.title(),
        "description": f"{toolset_type} tools",
        "category": "app",
        "group": "",
        "supported_auth_types": supported_auth,
        "tools": tools,
        "isInternal": internal,
    }
    registry.get_toolset_metadata.return_value = metadata
    registry.list_toolsets.return_value = [toolset_type]
    if oauth_cfg:
        registry.get_toolset_oauth_config.return_value = oauth_cfg
    return registry


def _make_oauth_registry(toolset_type="jira"):
    """Create a mock registry with OAUTH config for route tests."""
    registry = MagicMock()
    mock_oauth = MagicMock()
    mock_oauth.redirect_uri = "/api/v1/toolsets/oauth/callback"
    mock_oauth.authorize_url = "https://auth.example.com/authorize"
    mock_oauth.token_url = "https://auth.example.com/token"
    mock_scopes = MagicMock()
    mock_scopes.get_scopes_for_type.return_value = ["read", "write"]
    mock_oauth.scopes = mock_scopes
    mock_oauth.additional_params = None
    mock_oauth.token_access_type = None
    mock_oauth.scope_parameter_name = "scope"
    mock_oauth.token_response_path = None
    metadata = {
        "name": toolset_type,
        "display_name": toolset_type.title(),
        "description": f"{toolset_type} tools",
        "category": "app",
        "group": "",
        "supported_auth_types": ["OAUTH"],
        "tools": [],
        "isInternal": False,
        "config": {"_oauth_configs": {"OAUTH": mock_oauth}},
    }
    registry.get_toolset_metadata.return_value = metadata
    registry.list_toolsets.return_value = [toolset_type]
    return registry


class TestGetUserContext:
    def test_from_state(self):
        from app.api.routes.toolsets import _get_user_context
        request = MagicMock()
        request.state.user = {"userId": "u1", "orgId": "o1"}
        request.headers = {}
        ctx = _get_user_context(request)
        assert ctx["user_id"] == "u1"
        assert ctx["org_id"] == "o1"

    def test_from_headers_fallback(self):
        from app.api.routes.toolsets import _get_user_context
        request = MagicMock()
        request.state.user = {}
        request.headers = {"X-User-Id": "u2", "X-Organization-Id": "o2"}
        ctx = _get_user_context(request)
        assert ctx["user_id"] == "u2"
        assert ctx["org_id"] == "o2"

    def test_missing_user_id_raises(self):
        from app.api.routes.toolsets import _get_user_context
        request = MagicMock()
        request.state.user = {}
        request.headers = {}
        with pytest.raises(HTTPException) as exc:
            _get_user_context(request)
        assert exc.value.status_code == 401


class TestGetRegistry:
    def test_success(self):
        from app.api.routes.toolsets import _get_registry
        request = MagicMock()
        request.app.state.toolset_registry = MagicMock()
        result = _get_registry(request)
        assert result is not None

    def test_not_initialized_raises(self):
        from app.api.routes.toolsets import _get_registry
        request = MagicMock()
        request.app.state.toolset_registry = None
        with pytest.raises(HTTPException) as exc:
            _get_registry(request)
        assert exc.value.status_code == 500


class TestGetGraphProvider:
    def test_success(self):
        from app.api.routes.toolsets import _get_graph_provider
        request = MagicMock()
        request.app.state.graph_provider = MagicMock()
        result = _get_graph_provider(request)
        assert result is not None

    def test_not_initialized_raises(self):
        from app.api.routes.toolsets import _get_graph_provider
        request = MagicMock()
        request.app.state.graph_provider = None
        with pytest.raises(HTTPException) as exc:
            _get_graph_provider(request)
        assert exc.value.status_code == 500


class TestGetToolsetMetadata:
    def test_success(self):
        from app.api.routes.toolsets import _get_toolset_metadata
        registry = MagicMock()
        registry.get_toolset_metadata.return_value = {"display_name": "Jira", "isInternal": False}
        result = _get_toolset_metadata(registry, "jira")
        assert result["display_name"] == "Jira"

    def test_empty_type_raises(self):
        from app.api.routes.toolsets import _get_toolset_metadata
        registry = MagicMock()
        with pytest.raises(HTTPException) as exc:
            _get_toolset_metadata(registry, "")
        assert exc.value.status_code == 400

    def test_not_found_raises(self):
        from app.api.routes.toolsets import _get_toolset_metadata, ToolsetNotFoundError
        registry = MagicMock()
        registry.get_toolset_metadata.return_value = None
        with pytest.raises(ToolsetNotFoundError):
            _get_toolset_metadata(registry, "nonexistent")

    def test_internal_toolset_raises(self):
        from app.api.routes.toolsets import _get_toolset_metadata, ToolsetNotFoundError
        registry = MagicMock()
        registry.get_toolset_metadata.return_value = {"isInternal": True}
        with pytest.raises(ToolsetNotFoundError):
            _get_toolset_metadata(registry, "internal_tool")


class TestPathHelpers:
    def test_get_instances_path(self):
        from app.api.routes.toolsets import _get_instances_path
        result = _get_instances_path("org1")
        assert isinstance(result, str)

    def test_get_user_auth_path(self):
        from app.api.routes.toolsets import _get_user_auth_path
        result = _get_user_auth_path("inst1", "user1")
        assert "inst1" in result
        assert "user1" in result

    def test_get_instance_users_prefix(self):
        from app.api.routes.toolsets import _get_instance_users_prefix
        result = _get_instance_users_prefix("inst1")
        assert result.endswith("/")

    def test_get_toolset_oauth_config_path(self):
        from app.api.routes.toolsets import _get_toolset_oauth_config_path
        result = _get_toolset_oauth_config_path("JIRA")
        assert "jira" in result

    def test_generate_instance_id(self):
        from app.api.routes.toolsets import _generate_instance_id
        result = _generate_instance_id()
        uuid.UUID(result)

    def test_generate_oauth_config_id(self):
        from app.api.routes.toolsets import _generate_oauth_config_id
        result = _generate_oauth_config_id()
        uuid.UUID(result)


class TestLoadToolsetInstances:
    @pytest.mark.asyncio
    async def test_success(self):
        from app.api.routes.toolsets import _load_toolset_instances
        cs = AsyncMock()
        cs.get_config = AsyncMock(return_value=[{"_id": "i1", "orgId": "org1"}])
        result = await _load_toolset_instances("org1", cs)
        assert len(result) == 1

    @pytest.mark.asyncio
    async def test_exception_raises(self):
        from app.api.routes.toolsets import _load_toolset_instances
        cs = AsyncMock()
        cs.get_config = AsyncMock(side_effect=RuntimeError("etcd down"))
        with pytest.raises(HTTPException) as exc:
            await _load_toolset_instances("org1", cs)
        assert exc.value.status_code == 500


class TestApplyTenantToMicrosoftOAuthUrl:
    def test_non_microsoft_url_unchanged(self):
        from app.api.routes.toolsets import _apply_tenant_to_microsoft_oauth_url
        url = "https://accounts.google.com/oauth2/authorize"
        assert _apply_tenant_to_microsoft_oauth_url(url, "tenant1") == url

    def test_empty_url(self):
        from app.api.routes.toolsets import _apply_tenant_to_microsoft_oauth_url
        assert _apply_tenant_to_microsoft_oauth_url("", "tenant1") == ""

    def test_common_tenant_no_change(self):
        from app.api.routes.toolsets import _apply_tenant_to_microsoft_oauth_url
        url = "https://login.microsoftonline.com/common/oauth2/v2.0/authorize"
        assert _apply_tenant_to_microsoft_oauth_url(url, "common") == url

    def test_blank_tenant_no_change(self):
        from app.api.routes.toolsets import _apply_tenant_to_microsoft_oauth_url
        url = "https://login.microsoftonline.com/common/oauth2/v2.0/authorize"
        assert _apply_tenant_to_microsoft_oauth_url(url, "") == url

    def test_replaces_tenant(self):
        from app.api.routes.toolsets import _apply_tenant_to_microsoft_oauth_url
        url = "https://login.microsoftonline.com/common/oauth2/v2.0/authorize"
        result = _apply_tenant_to_microsoft_oauth_url(url, "my-tenant-id")
        assert "my-tenant-id" in result
        assert "common" not in result


class TestGetOAuthConfigFromRegistry:
    def test_success(self):
        from app.api.routes.toolsets import _get_oauth_config_from_registry
        registry = MagicMock()
        mock_config = MagicMock()
        mock_config.authorize_url = "https://auth.example.com"
        mock_config.token_url = "https://token.example.com"
        registry.get_toolset_metadata.return_value = {
            "config": {"_oauth_configs": {"OAUTH": mock_config}}
        }
        result = _get_oauth_config_from_registry("jira", registry)
        assert result is mock_config

    def test_not_found(self):
        from app.api.routes.toolsets import _get_oauth_config_from_registry, ToolsetNotFoundError
        registry = MagicMock()
        registry.get_toolset_metadata.return_value = None
        with pytest.raises(ToolsetNotFoundError):
            _get_oauth_config_from_registry("jira", registry)

    def test_no_oauth_config(self):
        from app.api.routes.toolsets import _get_oauth_config_from_registry, OAuthConfigError
        registry = MagicMock()
        registry.get_toolset_metadata.return_value = {"config": {"_oauth_configs": {}}}
        with pytest.raises(OAuthConfigError):
            _get_oauth_config_from_registry("jira", registry)

    def test_incomplete_oauth_config(self):
        from app.api.routes.toolsets import _get_oauth_config_from_registry, OAuthConfigError
        registry = MagicMock()
        mock_config = MagicMock(spec=[])
        registry.get_toolset_metadata.return_value = {
            "config": {"_oauth_configs": {"OAUTH": mock_config}}
        }
        with pytest.raises(OAuthConfigError):
            _get_oauth_config_from_registry("jira", registry)


class TestFormatToolsetData:
    def test_without_tools(self):
        from app.api.routes.toolsets import _format_toolset_data
        metadata = {"display_name": "Jira", "description": "Track issues", "tools": [{"name": "search"}]}
        result = _format_toolset_data("jira", metadata)
        assert result["name"] == "jira"
        assert result["toolCount"] == 1
        assert "tools" not in result

    def test_with_tools(self):
        from app.api.routes.toolsets import _format_toolset_data
        metadata = {
            "display_name": "Jira",
            "description": "Track issues",
            "tools": [{"name": "search", "description": "Search issues", "parameters": [], "returns": None, "tags": []}],
        }
        result = _format_toolset_data("jira", metadata, include_tools=True)
        assert len(result["tools"]) == 1
        assert result["tools"][0]["fullName"] == "jira.search"


class TestParseRequestJson:
    def test_valid(self):
        from app.api.routes.toolsets import _parse_request_json
        result = _parse_request_json(MagicMock(), b'{"name": "test"}')
        assert result == {"name": "test"}

    def test_empty_body(self):
        from app.api.routes.toolsets import _parse_request_json
        with pytest.raises(HTTPException) as exc:
            _parse_request_json(MagicMock(), b"")
        assert exc.value.status_code == 400

    def test_invalid_json(self):
        from app.api.routes.toolsets import _parse_request_json
        with pytest.raises(HTTPException) as exc:
            _parse_request_json(MagicMock(), b"not json")
        assert exc.value.status_code == 400


class TestGetOAuthConfigsForType:
    @pytest.mark.asyncio
    async def test_success(self):
        from app.api.routes.toolsets import _get_oauth_configs_for_type
        cs = AsyncMock()
        cs.get_config = AsyncMock(return_value=[{"_id": "cfg1"}])
        result = await _get_oauth_configs_for_type("jira", cs)
        assert len(result) == 1

    @pytest.mark.asyncio
    async def test_non_list_returns_empty(self):
        from app.api.routes.toolsets import _get_oauth_configs_for_type
        cs = AsyncMock()
        cs.get_config = AsyncMock(return_value="not a list")
        result = await _get_oauth_configs_for_type("jira", cs)
        assert result == []

    @pytest.mark.asyncio
    async def test_exception_returns_empty(self):
        from app.api.routes.toolsets import _get_oauth_configs_for_type
        cs = AsyncMock()
        cs.get_config = AsyncMock(side_effect=Exception("fail"))
        result = await _get_oauth_configs_for_type("jira", cs)
        assert result == []


class TestBuildOAuthConfig:
    @pytest.mark.asyncio
    async def test_missing_client_id_raises(self):
        from app.api.routes.toolsets import _build_oauth_config, InvalidAuthConfigError
        with pytest.raises(InvalidAuthConfigError):
            await _build_oauth_config({}, "jira", MagicMock())

    @pytest.mark.asyncio
    async def test_success(self):
        from app.api.routes.toolsets import _build_oauth_config
        registry = MagicMock()
        mock_oauth = MagicMock()
        mock_oauth.redirect_uri = "/callback"
        mock_oauth.authorize_url = "https://auth.example.com"
        mock_oauth.token_url = "https://token.example.com"
        mock_scopes = MagicMock()
        mock_scopes.get_scopes_for_type.return_value = ["read"]
        mock_oauth.scopes = mock_scopes
        mock_oauth.additional_params = None
        mock_oauth.token_access_type = None
        mock_oauth.scope_parameter_name = "scope"
        mock_oauth.token_response_path = None

        registry.get_toolset_metadata.return_value = {
            "config": {"_oauth_configs": {"OAUTH": mock_oauth}}
        }
        auth_config = {"clientId": "cid", "clientSecret": "cs"}
        result = await _build_oauth_config(auth_config, "jira", registry)
        assert result["clientId"] == "cid"
        assert result["clientSecret"] == "cs"

    @pytest.mark.asyncio
    async def test_with_tenant_id(self):
        from app.api.routes.toolsets import _build_oauth_config
        registry = MagicMock()
        mock_oauth = MagicMock()
        mock_oauth.redirect_uri = "/callback"
        mock_oauth.authorize_url = "https://login.microsoftonline.com/common/oauth2/v2.0/authorize"
        mock_oauth.token_url = "https://login.microsoftonline.com/common/oauth2/v2.0/token"
        mock_scopes = MagicMock()
        mock_scopes.get_scopes_for_type.return_value = ["read"]
        mock_oauth.scopes = mock_scopes
        mock_oauth.additional_params = None
        mock_oauth.token_access_type = None
        mock_oauth.scope_parameter_name = "scope"
        mock_oauth.token_response_path = None

        registry.get_toolset_metadata.return_value = {
            "config": {"_oauth_configs": {"OAUTH": mock_oauth}}
        }
        auth_config = {"clientId": "cid", "clientSecret": "cs", "tenantId": "my-tenant"}
        result = await _build_oauth_config(auth_config, "microsoft", registry)
        assert result["tenantId"] == "my-tenant"
        assert "my-tenant" in result["authorizeUrl"]

    @pytest.mark.asyncio
    async def test_with_additional_params(self):
        from app.api.routes.toolsets import _build_oauth_config
        registry = MagicMock()
        mock_oauth = MagicMock()
        mock_oauth.redirect_uri = "/cb"
        mock_oauth.authorize_url = "https://auth.com"
        mock_oauth.token_url = "https://token.com"
        mock_scopes = MagicMock()
        mock_scopes.get_scopes_for_type.return_value = []
        mock_oauth.scopes = mock_scopes
        mock_oauth.additional_params = {"extra": "value"}
        mock_oauth.token_access_type = "offline"
        mock_oauth.scope_parameter_name = "scope"
        mock_oauth.token_response_path = "data.access_token"

        registry.get_toolset_metadata.return_value = {
            "config": {"_oauth_configs": {"OAUTH": mock_oauth}}
        }
        auth_config = {"clientId": "cid", "clientSecret": "cs"}
        result = await _build_oauth_config(auth_config, "google", registry)
        assert result["additionalParams"] == {"extra": "value"}
        assert result["tokenAccessType"] == "offline"
        assert result["tokenResponsePath"] == "data.access_token"


class TestCreateOrUpdateToolsetOAuthConfig:
    @pytest.mark.asyncio
    async def test_update_existing(self):
        from app.api.routes.toolsets import _create_or_update_toolset_oauth_config
        cs = AsyncMock()
        cs.get_config = AsyncMock(return_value=[
            {"_id": "cfg-1", "orgId": "org1", "config": {"clientId": "old"}}
        ])
        cs.set_config = AsyncMock()

        with patch("app.api.routes.toolsets._prepare_toolset_auth_config", new_callable=AsyncMock, return_value={"clientId": "new", "clientSecret": "cs"}):
            result = await _create_or_update_toolset_oauth_config(
                "jira", {"clientId": "new"}, "Jira Instance", "u1", "org1",
                cs, MagicMock(), "http://localhost", oauth_config_id="cfg-1"
            )
            assert result == "cfg-1"

    @pytest.mark.asyncio
    async def test_create_new(self):
        from app.api.routes.toolsets import _create_or_update_toolset_oauth_config
        cs = AsyncMock()
        cs.get_config = AsyncMock(return_value=[])
        cs.set_config = AsyncMock()

        with patch("app.api.routes.toolsets._prepare_toolset_auth_config", new_callable=AsyncMock, return_value={"clientId": "new"}):
            result = await _create_or_update_toolset_oauth_config(
                "jira", {"clientId": "new"}, "Jira", "u1", "org1",
                cs, MagicMock(), "http://localhost"
            )
            assert result is not None

    @pytest.mark.asyncio
    async def test_exception_returns_none(self):
        from app.api.routes.toolsets import _create_or_update_toolset_oauth_config
        cs = AsyncMock()
        cs.get_config = AsyncMock(return_value=[])

        with patch("app.api.routes.toolsets._prepare_toolset_auth_config", new_callable=AsyncMock, side_effect=Exception("fail")):
            result = await _create_or_update_toolset_oauth_config(
                "jira", {}, "Jira", "u1", "org1", cs, MagicMock(), "http://localhost"
            )
            assert result is None


class TestPrepareToolsetAuthConfig:
    @pytest.mark.asyncio
    async def test_non_oauth_returns_as_is(self):
        from app.api.routes.toolsets import _prepare_toolset_auth_config
        result = await _prepare_toolset_auth_config(
            {"type": "API_KEY", "key": "abc"}, "jira", MagicMock(), MagicMock()
        )
        assert result["type"] == "API_KEY"

    @pytest.mark.asyncio
    async def test_oauth_enriches(self):
        from app.api.routes.toolsets import _prepare_toolset_auth_config
        registry = MagicMock()
        mock_oauth = MagicMock()
        mock_oauth.redirect_uri = "/callback"
        mock_oauth.authorize_url = "https://auth.com"
        mock_oauth.token_url = "https://token.com"
        mock_scopes = MagicMock()
        mock_scopes.get_scopes_for_type.return_value = ["read"]
        mock_oauth.scopes = mock_scopes
        mock_oauth.additional_params = None
        mock_oauth.token_access_type = None
        mock_oauth.scope_parameter_name = "scope"
        mock_oauth.token_response_path = None

        registry.get_toolset_metadata.return_value = {
            "config": {"_oauth_configs": {"OAUTH": mock_oauth}}
        }
        cs = AsyncMock()
        result = await _prepare_toolset_auth_config(
            {"type": "OAUTH"}, "jira", registry, cs, "http://localhost:3000"
        )
        assert "authorizeUrl" in result
        assert "redirectUri" in result

    @pytest.mark.asyncio
    async def test_oauth_without_base_url_uses_fallback(self):
        from app.api.routes.toolsets import _prepare_toolset_auth_config
        registry = MagicMock()
        mock_oauth = MagicMock()
        mock_oauth.redirect_uri = "/callback"
        mock_oauth.authorize_url = "https://auth.com"
        mock_oauth.token_url = "https://token.com"
        mock_scopes = MagicMock()
        mock_scopes.get_scopes_for_type.return_value = []
        mock_oauth.scopes = mock_scopes
        mock_oauth.additional_params = None
        mock_oauth.token_access_type = None
        mock_oauth.scope_parameter_name = "scope"
        mock_oauth.token_response_path = None

        registry.get_toolset_metadata.return_value = {
            "config": {"_oauth_configs": {"OAUTH": mock_oauth}}
        }
        cs = AsyncMock()
        cs.get_config = AsyncMock(return_value={"frontend": {"publicEndpoint": "https://app.example.com"}})
        result = await _prepare_toolset_auth_config(
            {"type": "OAUTH"}, "jira", registry, cs
        )
        assert "app.example.com" in result["redirectUri"]

    @pytest.mark.asyncio
    async def test_oauth_without_base_url_endpoints_exception_uses_localhost(self):
        """Line 708-710 — get_config for endpoints raises, fallback to localhost."""
        from app.api.routes.toolsets import _prepare_toolset_auth_config
        registry = MagicMock()
        mock_oauth = MagicMock()
        mock_oauth.redirect_uri = "/callback"
        mock_oauth.authorize_url = "https://auth.com"
        mock_oauth.token_url = "https://token.com"
        mock_scopes = MagicMock()
        mock_scopes.get_scopes_for_type.return_value = []
        mock_oauth.scopes = mock_scopes
        mock_oauth.additional_params = None
        mock_oauth.token_access_type = None
        mock_oauth.scope_parameter_name = "scope"
        mock_oauth.token_response_path = None

        registry.get_toolset_metadata.return_value = {
            "config": {"_oauth_configs": {"OAUTH": mock_oauth}}
        }
        cs = AsyncMock()
        cs.get_config = AsyncMock(side_effect=RuntimeError("etcd down"))
        result = await _prepare_toolset_auth_config(
            {"type": "OAUTH"}, "jira", registry, cs
        )
        assert "localhost:3001" in result["redirectUri"]


# ===================================================================
# get_oauth_credentials_for_toolset — lines 144->151, 190->193
# ===================================================================
class TestGetOauthCredentialsLegacyPath:
    """Cover the early-return when auth already contains clientId + clientSecret."""

    @pytest.mark.asyncio
    async def test_inline_credentials_returned_directly(self):
        """Line 144-148 — legacy/override path returns entire auth config."""
        from app.api.routes.toolsets import get_oauth_credentials_for_toolset
        cs = AsyncMock()
        config = {
            "toolsetType": "jira",
            "auth": {"clientId": "cid", "clientSecret": "cs", "tenantId": "t1"},
        }
        result = await get_oauth_credentials_for_toolset(config, cs)
        assert result["clientId"] == "cid"
        assert result["tenantId"] == "t1"
        cs.get_config.assert_not_called()

    @pytest.mark.asyncio
    async def test_inline_credentials_with_logger(self):
        """Line 145-146 — debug log when returning inline creds."""
        from app.api.routes.toolsets import get_oauth_credentials_for_toolset
        cs = AsyncMock()
        lgr = MagicMock()
        config = {
            "toolsetType": "jira",
            "auth": {"clientId": "cid", "clientSecret": "cs"},
        }
        result = await get_oauth_credentials_for_toolset(config, cs, logger=lgr)
        assert result["clientId"] == "cid"
        lgr.debug.assert_called()

    @pytest.mark.asyncio
    async def test_instance_fetch_exception_continues(self):
        """Line 190-193 — exception fetching instance to recover oauthConfigId."""
        from app.api.routes.toolsets import get_oauth_credentials_for_toolset
        cs = AsyncMock()
        lgr = MagicMock()
        cs.get_config = AsyncMock(side_effect=RuntimeError("etcd down"))
        config = {
            "toolsetType": "jira",
            "instanceId": "inst-1",
            "auth": {},
        }
        with pytest.raises(ValueError, match="No oauthConfigId"):
            await get_oauth_credentials_for_toolset(config, cs, logger=lgr)
        lgr.warning.assert_called()

    @pytest.mark.asyncio
    async def test_instance_fetch_recovers_oauth_config_id(self):
        """Line 172-188 — oauthConfigId recovered from instance list."""
        from app.api.routes.toolsets import get_oauth_credentials_for_toolset

        cs = AsyncMock()
        call_count = 0

        async def side_effect_get_config(path, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return [{"_id": "inst-1", "oauthConfigId": "cfg-1"}]
            return [{"_id": "cfg-1", "orgId": "o1", "config": {"clientId": "c", "clientSecret": "s"}}]

        cs.get_config = AsyncMock(side_effect=side_effect_get_config)
        config = {
            "toolsetType": "jira",
            "instanceId": "inst-1",
            "auth": {},
        }
        result = await get_oauth_credentials_for_toolset(config, cs)
        assert result["clientId"] == "c"


# ===================================================================
# create_toolset_instance — lines 1284-1289, 1377-1379
# ===================================================================
class TestCreateToolsetInstanceEdgeCases:

    @pytest.mark.asyncio
    async def test_oauth_endpoints_non_dict_frontend_uses_default(self):
        """Line 1284 — frontend config is not a dict."""
        from app.api.routes.toolsets import create_toolset_instance
        cs = AsyncMock()

        async def mock_get(path, **kw):
            if "endpoints" in str(path):
                return {"frontend": "not-a-dict"}
            if "oauths" in str(path):
                return []
            return []
        cs.get_config = AsyncMock(side_effect=mock_get)
        cs.set_config = AsyncMock()

        req = _make_request(body_dict={
            "instanceName": "My OAuth",
            "toolsetType": "jira",
            "authType": "OAUTH",
        })
        req.app.state.toolset_registry = _make_oauth_registry("jira")

        with patch("app.api.routes.toolsets._check_user_is_admin", new_callable=AsyncMock, return_value=True), \
             patch("app.api.routes.toolsets._get_user_context", return_value={"user_id": "u1", "org_id": "o1"}), \
             patch("app.api.routes.toolsets._load_toolset_instances", new_callable=AsyncMock, return_value=[]), \
             patch("app.api.routes.toolsets._check_instance_name_conflict", return_value=False):
            result = await create_toolset_instance(req, config_service=cs)
        assert result["status"] == "success"

    @pytest.mark.asyncio
    async def test_oauth_endpoints_exception_uses_default(self):
        """Line 1287-1289 — get_config for endpoints raises."""
        from app.api.routes.toolsets import create_toolset_instance

        async def side_effect(path, **kwargs):
            if "endpoints" in str(path):
                raise RuntimeError("etcd down")
            if "oauths" in str(path):
                return []
            return []

        cs = AsyncMock()
        cs.get_config = AsyncMock(side_effect=side_effect)
        cs.set_config = AsyncMock()

        req = _make_request(body_dict={
            "instanceName": "My OAuth",
            "toolsetType": "jira",
            "authType": "OAUTH",
        })
        req.app.state.toolset_registry = _make_oauth_registry("jira")

        with patch("app.api.routes.toolsets._check_user_is_admin", new_callable=AsyncMock, return_value=True), \
             patch("app.api.routes.toolsets._get_user_context", return_value={"user_id": "u1", "org_id": "o1"}), \
             patch("app.api.routes.toolsets._load_toolset_instances", new_callable=AsyncMock, return_value=[]), \
             patch("app.api.routes.toolsets._check_instance_name_conflict", return_value=False):
            result = await create_toolset_instance(req, config_service=cs)
        assert result["status"] == "success"

    @pytest.mark.asyncio
    async def test_set_config_failure_raises_500(self):
        """Line 1377-1379 — set_config raises during instance save."""
        from app.api.routes.toolsets import create_toolset_instance
        cs = AsyncMock()
        cs.get_config = AsyncMock(return_value=[])
        cs.set_config = AsyncMock(side_effect=RuntimeError("storage error"))

        req = _make_request(body_dict={
            "instanceName": "My Jira",
            "toolsetType": "jira",
            "authType": "API_TOKEN",
        })
        req.app.state.toolset_registry = _make_registry("jira")

        with patch("app.api.routes.toolsets._check_user_is_admin", new_callable=AsyncMock, return_value=True), \
             patch("app.api.routes.toolsets._get_user_context", return_value={"user_id": "u1", "org_id": "o1"}), \
             patch("app.api.routes.toolsets._load_toolset_instances", new_callable=AsyncMock, return_value=[]), \
             patch("app.api.routes.toolsets._check_instance_name_conflict", return_value=False):
            with pytest.raises(HTTPException) as exc:
                await create_toolset_instance(req, config_service=cs)
            assert exc.value.status_code == 500


# ===================================================================
# get_toolset_instance — lines 1487->1508, 1490->1508, 1501->1503
# ===================================================================
class TestGetToolsetInstanceAdminOAuth:

    @pytest.mark.asyncio
    async def test_admin_oauth_instance_includes_oauth_config(self):
        """Lines 1487-1503 — admin sees oauthConfig with clientSecretSet."""
        from app.api.routes.toolsets import get_toolset_instance
        cs = AsyncMock()
        cs.list_keys_in_directory = AsyncMock(return_value=["k1", "k2"])
        instances = [{"_id": "i1", "orgId": "o1", "toolsetType": "jira", "authType": "OAUTH", "oauthConfigId": "cfg-1"}]

        req = _make_request()
        req.app.state.toolset_registry = _make_registry("jira", supported_auth=["OAUTH"])

        oauth_cfg = {"_id": "cfg-1", "oauthInstanceName": "My OAuth", "config": {"clientId": "cid", "clientSecret": "secret"}}

        with patch("app.api.routes.toolsets._check_user_is_admin", new_callable=AsyncMock, return_value=True), \
             patch("app.api.routes.toolsets._get_user_context", return_value={"user_id": "u1", "org_id": "o1"}), \
             patch("app.api.routes.toolsets._load_toolset_instances", new_callable=AsyncMock, return_value=instances), \
             patch("app.api.routes.toolsets._get_oauth_config_by_id", new_callable=AsyncMock, return_value=oauth_cfg):
            result = await get_toolset_instance("i1", req, config_service=cs)

        assert result["instance"]["oauthConfig"]["clientSecretSet"] is True
        assert result["instance"]["authenticatedUserCount"] == 2

    @pytest.mark.asyncio
    async def test_admin_oauth_config_load_exception(self):
        """Lines 1504-1505 — exception loading OAuth config is swallowed."""
        from app.api.routes.toolsets import get_toolset_instance
        cs = AsyncMock()
        cs.list_keys_in_directory = AsyncMock(return_value=[])
        instances = [{"_id": "i1", "orgId": "o1", "toolsetType": "jira", "authType": "OAUTH", "oauthConfigId": "cfg-1"}]

        req = _make_request()
        req.app.state.toolset_registry = _make_registry("jira", supported_auth=["OAUTH"])

        with patch("app.api.routes.toolsets._check_user_is_admin", new_callable=AsyncMock, return_value=True), \
             patch("app.api.routes.toolsets._get_user_context", return_value={"user_id": "u1", "org_id": "o1"}), \
             patch("app.api.routes.toolsets._load_toolset_instances", new_callable=AsyncMock, return_value=instances), \
             patch("app.api.routes.toolsets._get_oauth_config_by_id", new_callable=AsyncMock, side_effect=RuntimeError("fail")):
            result = await get_toolset_instance("i1", req, config_service=cs)

        assert "oauthConfig" not in result["instance"]

    @pytest.mark.asyncio
    async def test_admin_user_count_exception_returns_zero(self):
        """Lines 1512-1514 — list_keys_in_directory exception yields count 0."""
        from app.api.routes.toolsets import get_toolset_instance
        cs = AsyncMock()
        cs.list_keys_in_directory = AsyncMock(side_effect=RuntimeError("etcd fail"))
        instances = [{"_id": "i1", "orgId": "o1", "toolsetType": "jira", "authType": "OAUTH", "oauthConfigId": "cfg-1"}]

        req = _make_request()
        req.app.state.toolset_registry = _make_registry("jira", supported_auth=["OAUTH"])

        with patch("app.api.routes.toolsets._check_user_is_admin", new_callable=AsyncMock, return_value=True), \
             patch("app.api.routes.toolsets._get_user_context", return_value={"user_id": "u1", "org_id": "o1"}), \
             patch("app.api.routes.toolsets._load_toolset_instances", new_callable=AsyncMock, return_value=instances), \
             patch("app.api.routes.toolsets._get_oauth_config_by_id", new_callable=AsyncMock, return_value=None):
            result = await get_toolset_instance("i1", req, config_service=cs)

        assert result["instance"]["authenticatedUserCount"] == 0


# ===================================================================
# update_toolset_instance — lines 1625, 1661->1674
# ===================================================================
class TestUpdateToolsetInstanceEdgeCases:

    @pytest.mark.asyncio
    async def test_oauth_frontend_not_dict_uses_default(self):
        """Line 1625 — frontend config is not a dict."""
        from app.api.routes.toolsets import update_toolset_instance
        cs = AsyncMock()
        instances = [{"_id": "i1", "orgId": "o1", "toolsetType": "jira", "authType": "OAUTH",
                       "oauthConfigId": "cfg-1", "instanceName": "J"}]

        async def mock_get(path, **kw):
            if "endpoints" in str(path):
                return {"frontend": "not-a-dict"}
            return None
        cs.get_config = AsyncMock(side_effect=mock_get)
        cs.set_config = AsyncMock()

        req = _make_request(body_dict={"instanceName": "New J"})
        req.app.state.toolset_registry = _make_registry("jira", supported_auth=["OAUTH"])

        with patch("app.api.routes.toolsets._check_user_is_admin", new_callable=AsyncMock, return_value=True), \
             patch("app.api.routes.toolsets._get_user_context", return_value={"user_id": "u1", "org_id": "o1"}), \
             patch("app.api.routes.toolsets._load_toolset_instances", new_callable=AsyncMock, return_value=instances):
            result = await update_toolset_instance("i1", req, config_service=cs)
        assert result["status"] == "success"

    @pytest.mark.asyncio
    async def test_oauth_credential_update_changes_id(self):
        """Line 1661-1664 — new OAuth ID from credential update."""
        from app.api.routes.toolsets import update_toolset_instance
        cs = AsyncMock()
        instances = [{"_id": "i1", "orgId": "o1", "toolsetType": "jira", "authType": "OAUTH",
                       "oauthConfigId": "cfg-1", "instanceName": "J"}]
        cs.get_config = AsyncMock(return_value=None)
        cs.set_config = AsyncMock()
        cs.list_keys_in_directory = AsyncMock(return_value=[])

        req = _make_request(body_dict={"authConfig": {"clientId": "new-c", "clientSecret": "new-s"}})
        req.app.state.toolset_registry = _make_oauth_registry("jira")

        with patch("app.api.routes.toolsets._check_user_is_admin", new_callable=AsyncMock, return_value=True), \
             patch("app.api.routes.toolsets._get_user_context", return_value={"user_id": "u1", "org_id": "o1"}), \
             patch("app.api.routes.toolsets._load_toolset_instances", new_callable=AsyncMock, return_value=instances), \
             patch("app.api.routes.toolsets._create_or_update_toolset_oauth_config", new_callable=AsyncMock, return_value="cfg-2"), \
             patch("app.api.routes.toolsets._deauth_all_instance_users", new_callable=AsyncMock, return_value=3):
            result = await update_toolset_instance("i1", req, config_service=cs)

        assert result["deauthenticatedUserCount"] == 3
        assert result["instance"]["oauthConfigId"] == "cfg-2"

    @pytest.mark.asyncio
    async def test_non_oauth_auth_config_stored_on_instance(self):
        """Line 1666-1672 — non-OAuth authConfig goes to instance['auth']."""
        from app.api.routes.toolsets import update_toolset_instance
        cs = AsyncMock()
        instances = [{"_id": "i1", "orgId": "o1", "toolsetType": "jira", "authType": "BASIC_AUTH",
                       "instanceName": "J"}]
        cs.set_config = AsyncMock()

        req = _make_request(body_dict={"authConfig": {"username": "u", "password": "p"}})
        req.app.state.toolset_registry = _make_registry("jira", supported_auth=["BASIC_AUTH"])

        with patch("app.api.routes.toolsets._check_user_is_admin", new_callable=AsyncMock, return_value=True), \
             patch("app.api.routes.toolsets._get_user_context", return_value={"user_id": "u1", "org_id": "o1"}), \
             patch("app.api.routes.toolsets._load_toolset_instances", new_callable=AsyncMock, return_value=instances):
            result = await update_toolset_instance("i1", req, config_service=cs)

        assert result["instance"]["auth"]["username"] == "u"


# ===================================================================
# delete_toolset_instance — lines 1774-1777, 1800, 1805-1819
# ===================================================================
class TestDeleteToolsetInstanceCredentialCleanup:

    def _mock_graph_provider(self):
        gp = AsyncMock()
        gp.check_toolset_instance_in_use = AsyncMock(return_value=[])
        return gp

    @pytest.mark.asyncio
    async def test_refresh_cancel_exception_continues(self):
        """Lines 1776-1777 — refresh service exception doesn't block deletion."""
        from app.api.routes.toolsets import delete_toolset_instance
        cs = AsyncMock()
        instances = [{"_id": "i1", "orgId": "o1", "toolsetType": "jira", "authType": "API_TOKEN",
                       "instanceName": "J"}]
        cs.list_keys_in_directory = AsyncMock(return_value=[])
        cs.set_config = AsyncMock()

        req = _make_request()
        gp = self._mock_graph_provider()

        with patch("app.api.routes.toolsets._check_user_is_admin", new_callable=AsyncMock, return_value=True), \
             patch("app.api.routes.toolsets._get_user_context", return_value={"user_id": "u1", "org_id": "o1"}), \
             patch("app.api.routes.toolsets._load_toolset_instances", new_callable=AsyncMock, return_value=instances), \
             patch("app.api.routes.toolsets._get_graph_provider", return_value=gp), \
             patch.dict("sys.modules", {"app.connectors.core.base.token_service.startup_service": MagicMock(startup_service=MagicMock(get_toolset_token_refresh_service=MagicMock(side_effect=RuntimeError("no service"))))}):
            result = await delete_toolset_instance("i1", req, config_service=cs)
        assert result["status"] == "success"

    @pytest.mark.asyncio
    async def test_malformed_key_skipped(self):
        """Line 1800 — key with wrong format is skipped with warning."""
        from app.api.routes.toolsets import delete_toolset_instance
        cs = AsyncMock()
        instances = [{"_id": "i1", "orgId": "o1", "toolsetType": "jira", "authType": "API_TOKEN",
                       "instanceName": "J"}]
        cs.list_keys_in_directory = AsyncMock(return_value=[
            "/services/toolsets/i1/u1",
            "/services/toolsets/i1",
            "/wrong/prefix/key",
        ])
        cs.delete_config = AsyncMock(return_value=True)
        cs.set_config = AsyncMock()

        req = _make_request()
        gp = self._mock_graph_provider()

        with patch("app.api.routes.toolsets._check_user_is_admin", new_callable=AsyncMock, return_value=True), \
             patch("app.api.routes.toolsets._get_user_context", return_value={"user_id": "u1", "org_id": "o1"}), \
             patch("app.api.routes.toolsets._load_toolset_instances", new_callable=AsyncMock, return_value=instances), \
             patch("app.api.routes.toolsets._get_graph_provider", return_value=gp), \
             patch.dict("sys.modules", {"app.connectors.core.base.token_service.startup_service": MagicMock(startup_service=MagicMock(get_toolset_token_refresh_service=MagicMock(return_value=None)))}):
            result = await delete_toolset_instance("i1", req, config_service=cs)
        assert result["deletedCredentialsCount"] == 1

    @pytest.mark.asyncio
    async def test_individual_delete_failure(self):
        """Lines 1811-1814 — one delete fails, rest succeed."""
        from app.api.routes.toolsets import delete_toolset_instance
        cs = AsyncMock()
        instances = [{"_id": "i1", "orgId": "o1", "toolsetType": "jira", "authType": "API_TOKEN",
                       "instanceName": "J"}]
        cs.list_keys_in_directory = AsyncMock(return_value=[
            "/services/toolsets/i1/u1",
            "/services/toolsets/i1/u2",
        ])
        cs.delete_config = AsyncMock(side_effect=[RuntimeError("fail"), True])
        cs.set_config = AsyncMock()

        req = _make_request()
        gp = self._mock_graph_provider()

        with patch("app.api.routes.toolsets._check_user_is_admin", new_callable=AsyncMock, return_value=True), \
             patch("app.api.routes.toolsets._get_user_context", return_value={"user_id": "u1", "org_id": "o1"}), \
             patch("app.api.routes.toolsets._load_toolset_instances", new_callable=AsyncMock, return_value=instances), \
             patch("app.api.routes.toolsets._get_graph_provider", return_value=gp), \
             patch.dict("sys.modules", {"app.connectors.core.base.token_service.startup_service": MagicMock(startup_service=MagicMock(get_toolset_token_refresh_service=MagicMock(return_value=None)))}):
            result = await delete_toolset_instance("i1", req, config_service=cs)
        assert result["deletedCredentialsCount"] == 1

    @pytest.mark.asyncio
    async def test_credential_cleanup_outer_exception(self):
        """Lines 1818-1819 — outer exception during cleanup doesn't block deletion."""
        from app.api.routes.toolsets import delete_toolset_instance
        cs = AsyncMock()
        instances = [{"_id": "i1", "orgId": "o1", "toolsetType": "jira", "authType": "API_TOKEN",
                       "instanceName": "J"}]
        cs.list_keys_in_directory = AsyncMock(side_effect=RuntimeError("etcd fail"))
        cs.set_config = AsyncMock()

        req = _make_request()
        gp = self._mock_graph_provider()

        with patch("app.api.routes.toolsets._check_user_is_admin", new_callable=AsyncMock, return_value=True), \
             patch("app.api.routes.toolsets._get_user_context", return_value={"user_id": "u1", "org_id": "o1"}), \
             patch("app.api.routes.toolsets._load_toolset_instances", new_callable=AsyncMock, return_value=instances), \
             patch("app.api.routes.toolsets._get_graph_provider", return_value=gp), \
             patch.dict("sys.modules", {"app.connectors.core.base.token_service.startup_service": MagicMock(startup_service=MagicMock(get_toolset_token_refresh_service=MagicMock(return_value=None)))}):
            result = await delete_toolset_instance("i1", req, config_service=cs)
        assert result["status"] == "success"
        assert result["deletedCredentialsCount"] == 0


# ===================================================================
# get_authenticated_toolsets — lines 1948-1949
# ===================================================================
class TestGetAuthenticatedToolsetsException:

    @pytest.mark.asyncio
    async def test_per_instance_auth_fetch_exception_returns_none(self):
        """Lines 1948-1949 — exception yields (inst, None), instance excluded."""
        from app.api.routes.toolsets import get_authenticated_toolsets
        cs = AsyncMock()
        instances = [
            {"_id": "i1", "orgId": "o1", "toolsetType": "jira"},
            {"_id": "i2", "orgId": "o1", "toolsetType": "slack"},
        ]
        async def mock_get(path, **kw):
            if "i1" in path:
                raise RuntimeError("etcd fail for i1")
            return {"isAuthenticated": True}

        cs.get_config = AsyncMock(side_effect=mock_get)

        registry = _make_registry("slack")

        with patch("app.api.routes.toolsets._load_toolset_instances", new_callable=AsyncMock, return_value=instances):
            result = await get_authenticated_toolsets("u1", "o1", cs, registry)

        assert len(result) == 1
        assert result[0]["toolsetType"] == "slack"


# ===================================================================
# authenticate_toolset_instance — lines 2038->2044
# ===================================================================
class TestAuthenticateToolsetBasicAuth:

    @pytest.mark.asyncio
    async def test_basic_auth_missing_username_raises(self):
        """Lines 2038-2042 — BASIC_AUTH with empty username."""
        from app.api.routes.toolsets import (
            InvalidAuthConfigError,
            authenticate_toolset_instance,
        )
        cs = AsyncMock()
        instances = [{"_id": "i1", "orgId": "o1", "toolsetType": "jira", "authType": "BASIC_AUTH"}]

        req = _make_request(body_dict={"auth": {"username": "", "password": "pass"}})

        with patch("app.api.routes.toolsets._get_user_context", return_value={"user_id": "u1", "org_id": "o1"}), \
             patch("app.api.routes.toolsets._load_toolset_instances", new_callable=AsyncMock, return_value=instances):
            with pytest.raises(InvalidAuthConfigError):
                await authenticate_toolset_instance("i1", req, config_service=cs)

    @pytest.mark.asyncio
    async def test_basic_auth_missing_password_raises(self):
        from app.api.routes.toolsets import (
            InvalidAuthConfigError,
            authenticate_toolset_instance,
        )
        cs = AsyncMock()
        instances = [{"_id": "i1", "orgId": "o1", "toolsetType": "jira", "authType": "BASIC_AUTH"}]

        req = _make_request(body_dict={"auth": {"username": "user", "password": ""}})

        with patch("app.api.routes.toolsets._get_user_context", return_value={"user_id": "u1", "org_id": "o1"}), \
             patch("app.api.routes.toolsets._load_toolset_instances", new_callable=AsyncMock, return_value=instances):
            with pytest.raises(InvalidAuthConfigError):
                await authenticate_toolset_instance("i1", req, config_service=cs)

    @pytest.mark.asyncio
    async def test_basic_auth_success(self):
        from app.api.routes.toolsets import authenticate_toolset_instance
        cs = AsyncMock()
        cs.set_config = AsyncMock()
        instances = [{"_id": "i1", "orgId": "o1", "toolsetType": "jira", "authType": "BASIC_AUTH"}]

        req = _make_request(body_dict={"auth": {"username": "admin", "password": "secret"}})

        with patch("app.api.routes.toolsets._get_user_context", return_value={"user_id": "u1", "org_id": "o1"}), \
             patch("app.api.routes.toolsets._load_toolset_instances", new_callable=AsyncMock, return_value=instances):
            result = await authenticate_toolset_instance("i1", req, config_service=cs)
        assert result["isAuthenticated"] is True


# ===================================================================
# get_instance_oauth_authorization_url — lines 2199-2279
# ===================================================================
class TestGetInstanceOAuthAuthorizationUrl:

    @pytest.mark.asyncio
    async def test_happy_path(self):
        """Lines 2199-2272 — full happy path returning authorization URL."""
        from app.api.routes.toolsets import get_instance_oauth_authorization_url
        cs = AsyncMock()
        instances = [{"_id": "i1", "orgId": "o1", "toolsetType": "jira", "authType": "OAUTH", "oauthConfigId": "cfg-1"}]
        oauth_cfg = {"_id": "cfg-1", "config": {"clientId": "cid", "clientSecret": "cs"}}

        req = _make_request()
        req.app.state.toolset_registry = _make_oauth_registry("jira")

        mock_provider = AsyncMock()
        mock_provider.start_authorization = AsyncMock(
            return_value="https://auth.example.com/authorize?state=abc123&scope=read"
        )
        mock_provider.close = AsyncMock()

        with patch("app.api.routes.toolsets._get_user_context", return_value={"user_id": "u1", "org_id": "o1"}), \
             patch("app.api.routes.toolsets._load_toolset_instances", new_callable=AsyncMock, return_value=instances), \
             patch("app.api.routes.toolsets._get_oauth_config_by_id", new_callable=AsyncMock, return_value=oauth_cfg), \
             patch("app.api.routes.toolsets._build_oauth_config", new_callable=AsyncMock, return_value={"clientId": "cid", "clientSecret": "cs", "scopes": ["read"]}), \
             patch("app.api.routes.toolsets.OAuthProvider", return_value=mock_provider):
            result = await get_instance_oauth_authorization_url("i1", req, config_service=cs)

        assert result["success"] is True
        assert "authorizationUrl" in result
        mock_provider.close.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_non_oauth_instance_raises(self):
        """Line 2212 — non-OAUTH instance raises OAuthConfigError."""
        from app.api.routes.toolsets import OAuthConfigError, get_instance_oauth_authorization_url
        cs = AsyncMock()
        instances = [{"_id": "i1", "orgId": "o1", "toolsetType": "jira", "authType": "API_TOKEN"}]

        req = _make_request()

        with patch("app.api.routes.toolsets._get_user_context", return_value={"user_id": "u1", "org_id": "o1"}), \
             patch("app.api.routes.toolsets._load_toolset_instances", new_callable=AsyncMock, return_value=instances):
            with pytest.raises(OAuthConfigError):
                await get_instance_oauth_authorization_url("i1", req, config_service=cs)

    @pytest.mark.asyncio
    async def test_missing_oauth_config_id_raises(self):
        from app.api.routes.toolsets import OAuthConfigError, get_instance_oauth_authorization_url
        cs = AsyncMock()
        instances = [{"_id": "i1", "orgId": "o1", "toolsetType": "jira", "authType": "OAUTH"}]

        req = _make_request()

        with patch("app.api.routes.toolsets._get_user_context", return_value={"user_id": "u1", "org_id": "o1"}), \
             patch("app.api.routes.toolsets._load_toolset_instances", new_callable=AsyncMock, return_value=instances):
            with pytest.raises(OAuthConfigError):
                await get_instance_oauth_authorization_url("i1", req, config_service=cs)

    @pytest.mark.asyncio
    async def test_deleted_oauth_config_raises(self):
        from app.api.routes.toolsets import OAuthConfigError, get_instance_oauth_authorization_url
        cs = AsyncMock()
        instances = [{"_id": "i1", "orgId": "o1", "toolsetType": "jira", "authType": "OAUTH", "oauthConfigId": "cfg-1"}]

        req = _make_request()

        with patch("app.api.routes.toolsets._get_user_context", return_value={"user_id": "u1", "org_id": "o1"}), \
             patch("app.api.routes.toolsets._load_toolset_instances", new_callable=AsyncMock, return_value=instances), \
             patch("app.api.routes.toolsets._get_oauth_config_by_id", new_callable=AsyncMock, return_value=None):
            with pytest.raises(OAuthConfigError, match="not found"):
                await get_instance_oauth_authorization_url("i1", req, config_service=cs)

    @pytest.mark.asyncio
    async def test_start_authorization_generic_exception_raises_500(self):
        from app.api.routes.toolsets import get_instance_oauth_authorization_url
        cs = AsyncMock()
        instances = [{"_id": "i1", "orgId": "o1", "toolsetType": "jira", "authType": "OAUTH", "oauthConfigId": "cfg-1"}]
        oauth_cfg = {"_id": "cfg-1", "config": {"clientId": "cid", "clientSecret": "cs"}}

        req = _make_request()
        req.app.state.toolset_registry = _make_oauth_registry("jira")

        mock_provider = AsyncMock()
        mock_provider.start_authorization = AsyncMock(side_effect=RuntimeError("network fail"))
        mock_provider.close = AsyncMock()

        with patch("app.api.routes.toolsets._get_user_context", return_value={"user_id": "u1", "org_id": "o1"}), \
             patch("app.api.routes.toolsets._load_toolset_instances", new_callable=AsyncMock, return_value=instances), \
             patch("app.api.routes.toolsets._get_oauth_config_by_id", new_callable=AsyncMock, return_value=oauth_cfg), \
             patch("app.api.routes.toolsets._build_oauth_config", new_callable=AsyncMock, return_value={"clientId": "cid", "clientSecret": "cs"}), \
             patch("app.api.routes.toolsets.OAuthProvider", return_value=mock_provider):
            with pytest.raises(HTTPException) as exc:
                await get_instance_oauth_authorization_url("i1", req, config_service=cs)
            assert exc.value.status_code == 500

    @pytest.mark.asyncio
    async def test_empty_auth_url_raises(self):
        """Line 2255 — empty/None URL from start_authorization."""
        from app.api.routes.toolsets import OAuthConfigError, get_instance_oauth_authorization_url
        cs = AsyncMock()
        instances = [{"_id": "i1", "orgId": "o1", "toolsetType": "jira", "authType": "OAUTH", "oauthConfigId": "cfg-1"}]
        oauth_cfg = {"_id": "cfg-1", "config": {"clientId": "cid", "clientSecret": "cs"}}

        req = _make_request()
        req.app.state.toolset_registry = _make_oauth_registry("jira")

        mock_provider = AsyncMock()
        mock_provider.start_authorization = AsyncMock(return_value="")
        mock_provider.close = AsyncMock()

        with patch("app.api.routes.toolsets._get_user_context", return_value={"user_id": "u1", "org_id": "o1"}), \
             patch("app.api.routes.toolsets._load_toolset_instances", new_callable=AsyncMock, return_value=instances), \
             patch("app.api.routes.toolsets._get_oauth_config_by_id", new_callable=AsyncMock, return_value=oauth_cfg), \
             patch("app.api.routes.toolsets._build_oauth_config", new_callable=AsyncMock, return_value={"clientId": "cid", "clientSecret": "cs"}), \
             patch("app.api.routes.toolsets.OAuthProvider", return_value=mock_provider):
            with pytest.raises(OAuthConfigError, match="empty"):
                await get_instance_oauth_authorization_url("i1", req, config_service=cs)
        mock_provider.close.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_missing_state_param_raises(self):
        """Line 2265 — no state in auth URL query params."""
        from app.api.routes.toolsets import OAuthConfigError, get_instance_oauth_authorization_url
        cs = AsyncMock()
        instances = [{"_id": "i1", "orgId": "o1", "toolsetType": "jira", "authType": "OAUTH", "oauthConfigId": "cfg-1"}]
        oauth_cfg = {"_id": "cfg-1", "config": {"clientId": "cid", "clientSecret": "cs"}}

        req = _make_request()
        req.app.state.toolset_registry = _make_oauth_registry("jira")

        mock_provider = AsyncMock()
        mock_provider.start_authorization = AsyncMock(
            return_value="https://auth.example.com/authorize?scope=read"
        )
        mock_provider.close = AsyncMock()

        with patch("app.api.routes.toolsets._get_user_context", return_value={"user_id": "u1", "org_id": "o1"}), \
             patch("app.api.routes.toolsets._load_toolset_instances", new_callable=AsyncMock, return_value=instances), \
             patch("app.api.routes.toolsets._get_oauth_config_by_id", new_callable=AsyncMock, return_value=oauth_cfg), \
             patch("app.api.routes.toolsets._build_oauth_config", new_callable=AsyncMock, return_value={"clientId": "cid", "clientSecret": "cs"}), \
             patch("app.api.routes.toolsets.OAuthProvider", return_value=mock_provider):
            with pytest.raises(OAuthConfigError, match="missing"):
                await get_instance_oauth_authorization_url("i1", req, config_service=cs)

    @pytest.mark.asyncio
    async def test_instance_not_found_raises_404(self):
        """Line 2208 — instance not found."""
        from app.api.routes.toolsets import get_instance_oauth_authorization_url
        cs = AsyncMock()
        req = _make_request()

        with patch("app.api.routes.toolsets._get_user_context", return_value={"user_id": "u1", "org_id": "o1"}), \
             patch("app.api.routes.toolsets._load_toolset_instances", new_callable=AsyncMock, return_value=[]):
            with pytest.raises(HTTPException) as exc:
                await get_instance_oauth_authorization_url("no-such", req, config_service=cs)
            assert exc.value.status_code == 404

    @pytest.mark.asyncio
    async def test_strips_invalid_token_access_type(self):
        """Lines 2260-2261 — token_access_type=None stripped from URL."""
        from app.api.routes.toolsets import get_instance_oauth_authorization_url
        cs = AsyncMock()
        instances = [{"_id": "i1", "orgId": "o1", "toolsetType": "jira", "authType": "OAUTH", "oauthConfigId": "cfg-1"}]
        oauth_cfg = {"_id": "cfg-1", "config": {"clientId": "cid", "clientSecret": "cs"}}

        req = _make_request()
        req.app.state.toolset_registry = _make_oauth_registry("jira")

        mock_provider = AsyncMock()
        mock_provider.start_authorization = AsyncMock(
            return_value="https://auth.example.com/authorize?state=abc&token_access_type=None"
        )
        mock_provider.close = AsyncMock()

        with patch("app.api.routes.toolsets._get_user_context", return_value={"user_id": "u1", "org_id": "o1"}), \
             patch("app.api.routes.toolsets._load_toolset_instances", new_callable=AsyncMock, return_value=instances), \
             patch("app.api.routes.toolsets._get_oauth_config_by_id", new_callable=AsyncMock, return_value=oauth_cfg), \
             patch("app.api.routes.toolsets._build_oauth_config", new_callable=AsyncMock, return_value={"clientId": "cid", "clientSecret": "cs"}), \
             patch("app.api.routes.toolsets.OAuthProvider", return_value=mock_provider):
            result = await get_instance_oauth_authorization_url("i1", req, config_service=cs)

        assert "token_access_type" not in result["authorizationUrl"]


# ===================================================================
# handle_toolset_oauth_callback — lines 2301-2429
# ===================================================================
class TestHandleToolsetOAuthCallbackFlows:

    def _encode_state(self, state, instance_id, user_id, is_agent=False):
        from app.api.routes.toolsets import _encode_state_with_instance
        return _encode_state_with_instance(state, instance_id, user_id, is_agent=is_agent)

    @pytest.mark.asyncio
    async def test_user_flow_happy_path(self):
        """Lines 2301-2407 — successful user OAuth callback."""
        from app.api.routes.toolsets import handle_toolset_oauth_callback
        cs = AsyncMock()
        cs.get_config = AsyncMock(return_value={})
        cs.set_config = AsyncMock()

        encoded_state = self._encode_state("orig-state", "i1", "u1")
        instances = [{"_id": "i1", "orgId": "o1", "toolsetType": "jira", "authType": "OAUTH", "oauthConfigId": "cfg-1"}]
        oauth_cfg = {"_id": "cfg-1", "config": {"clientId": "cid", "clientSecret": "cs"}}

        req = _make_request()
        req.app.state.toolset_registry = _make_oauth_registry("jira")

        mock_token = MagicMock()
        mock_token.access_token = "token123"
        mock_provider = AsyncMock()
        mock_provider.handle_callback = AsyncMock(return_value=mock_token)
        mock_provider.close = AsyncMock()

        with patch("app.api.routes.toolsets._get_user_context", return_value={"user_id": "u1", "org_id": "o1"}), \
             patch("app.api.routes.toolsets._load_toolset_instances", new_callable=AsyncMock, return_value=instances), \
             patch("app.api.routes.toolsets._get_oauth_config_by_id", new_callable=AsyncMock, return_value=oauth_cfg), \
             patch("app.api.routes.toolsets._build_oauth_config", new_callable=AsyncMock, return_value={"clientId": "cid", "clientSecret": "cs"}), \
             patch("app.api.routes.toolsets.OAuthProvider", return_value=mock_provider), \
             patch.dict("sys.modules", {"app.connectors.core.base.token_service.startup_service": MagicMock(startup_service=MagicMock(get_toolset_token_refresh_service=MagicMock(return_value=None)))}):
            result = await handle_toolset_oauth_callback(req, code="auth-code", state=encoded_state, error=None, base_url=None, config_service=cs)

        assert result["success"] is True
        assert "oauth_success=true" in result["redirect_url"]

    @pytest.mark.asyncio
    async def test_user_flow_user_mismatch(self):
        """Lines 2317-2319 — state user != callback user raises 403."""
        from app.api.routes.toolsets import handle_toolset_oauth_callback
        cs = AsyncMock()
        encoded_state = self._encode_state("orig-state", "i1", "other-user")

        req = _make_request()

        with patch("app.api.routes.toolsets._get_user_context", return_value={"user_id": "u1", "org_id": "o1"}):
            result = await handle_toolset_oauth_callback(req, code="code", state=encoded_state, error=None, base_url=None, config_service=cs)
        assert result["success"] is False
        assert "auth_failed" in result.get("error", "")

    @pytest.mark.asyncio
    async def test_agent_flow_permission_denied(self):
        """Lines 2320-2326 — agent flow with no edit access redirects."""
        from app.api.routes.toolsets import handle_toolset_oauth_callback
        cs = AsyncMock()
        encoded_state = self._encode_state("orig-state", "i1", "agent-1", is_agent=True)

        req = _make_request()

        with patch("app.api.routes.toolsets._get_user_context", return_value={"user_id": "u1", "org_id": "o1"}), \
             patch("app.api.routes.toolsets._require_agent_edit_access", new_callable=AsyncMock, side_effect=HTTPException(status_code=403, detail="no access")):
            result = await handle_toolset_oauth_callback(req, code="code", state=encoded_state, error=None, base_url=None, config_service=cs)
        location = result.headers.get("location", "")
        assert "agent_permission_denied" in location

    @pytest.mark.asyncio
    async def test_agent_flow_success(self):
        """Lines 2350-2388 — agent flow stores creds at agent path."""
        from app.api.routes.toolsets import handle_toolset_oauth_callback
        cs = AsyncMock()
        cs.get_config = AsyncMock(return_value={})
        cs.set_config = AsyncMock()

        encoded_state = self._encode_state("orig-state", "i1", "agent-1", is_agent=True)
        instances = [{"_id": "i1", "orgId": "o1", "toolsetType": "jira", "authType": "OAUTH", "oauthConfigId": "cfg-1"}]
        oauth_cfg = {"_id": "cfg-1", "config": {"clientId": "cid", "clientSecret": "cs"}}

        req = _make_request()
        req.app.state.toolset_registry = _make_oauth_registry("jira")

        mock_token = MagicMock()
        mock_token.access_token = "token123"
        mock_provider = AsyncMock()
        mock_provider.handle_callback = AsyncMock(return_value=mock_token)
        mock_provider.close = AsyncMock()

        with patch("app.api.routes.toolsets._get_user_context", return_value={"user_id": "u1", "org_id": "o1"}), \
             patch("app.api.routes.toolsets._require_agent_edit_access", new_callable=AsyncMock, return_value={"_key": "a1"}), \
             patch("app.api.routes.toolsets._load_toolset_instances", new_callable=AsyncMock, return_value=instances), \
             patch("app.api.routes.toolsets._get_oauth_config_by_id", new_callable=AsyncMock, return_value=oauth_cfg), \
             patch("app.api.routes.toolsets._build_oauth_config", new_callable=AsyncMock, return_value={"clientId": "cid", "clientSecret": "cs"}), \
             patch("app.api.routes.toolsets.OAuthProvider", return_value=mock_provider), \
             patch.dict("sys.modules", {"app.connectors.core.base.token_service.startup_service": MagicMock(startup_service=MagicMock(get_toolset_token_refresh_service=MagicMock(return_value=None)))}):
            result = await handle_toolset_oauth_callback(req, code="auth-code", state=encoded_state, error=None, base_url=None, config_service=cs)

        assert result["success"] is True
        saved_val = cs.set_config.call_args[0][1]
        assert saved_val["agentKey"] == "agent-1"

    @pytest.mark.asyncio
    async def test_handle_callback_raises_generic_exception(self):
        """Lines 2426-2434 — generic exception uses extract_oauth_error_message."""
        from app.api.routes.toolsets import handle_toolset_oauth_callback
        cs = AsyncMock()
        encoded_state = self._encode_state("orig-state", "i1", "u1")
        instances = [{"_id": "i1", "orgId": "o1", "toolsetType": "jira", "authType": "OAUTH", "oauthConfigId": "cfg-1"}]
        oauth_cfg = {"_id": "cfg-1", "config": {"clientId": "cid", "clientSecret": "cs"}}

        req = _make_request()
        req.app.state.toolset_registry = _make_oauth_registry("jira")

        mock_provider = AsyncMock()
        mock_provider.handle_callback = AsyncMock(side_effect=RuntimeError("token exchange failed"))
        mock_provider.close = AsyncMock()

        with patch("app.api.routes.toolsets._get_user_context", return_value={"user_id": "u1", "org_id": "o1"}), \
             patch("app.api.routes.toolsets._load_toolset_instances", new_callable=AsyncMock, return_value=instances), \
             patch("app.api.routes.toolsets._get_oauth_config_by_id", new_callable=AsyncMock, return_value=oauth_cfg), \
             patch("app.api.routes.toolsets._build_oauth_config", new_callable=AsyncMock, return_value={"clientId": "cid", "clientSecret": "cs"}), \
             patch("app.api.routes.toolsets.OAuthProvider", return_value=mock_provider):
            result = await handle_toolset_oauth_callback(req, code="code", state=encoded_state, error=None, base_url=None, config_service=cs)

        assert result["success"] is False
        assert result["error"] == "server_error"

    @pytest.mark.asyncio
    async def test_callback_instance_not_found(self):
        """Line 2333 — instance not found during callback."""
        from app.api.routes.toolsets import handle_toolset_oauth_callback
        cs = AsyncMock()
        encoded_state = self._encode_state("orig-state", "no-such", "u1")

        req = _make_request()

        with patch("app.api.routes.toolsets._get_user_context", return_value={"user_id": "u1", "org_id": "o1"}), \
             patch("app.api.routes.toolsets._load_toolset_instances", new_callable=AsyncMock, return_value=[]):
            result = await handle_toolset_oauth_callback(req, code="code", state=encoded_state, error=None, base_url=None, config_service=cs)
        assert result["success"] is False
        assert "auth_failed" in result["error"]

    @pytest.mark.asyncio
    async def test_callback_no_oauth_config_id(self):
        """Line 2339 — instance has no oauthConfigId."""
        from app.api.routes.toolsets import handle_toolset_oauth_callback
        cs = AsyncMock()
        encoded_state = self._encode_state("orig-state", "i1", "u1")
        instances = [{"_id": "i1", "orgId": "o1", "toolsetType": "jira", "authType": "OAUTH"}]

        req = _make_request()

        with patch("app.api.routes.toolsets._get_user_context", return_value={"user_id": "u1", "org_id": "o1"}), \
             patch("app.api.routes.toolsets._load_toolset_instances", new_callable=AsyncMock, return_value=instances):
            result = await handle_toolset_oauth_callback(req, code="code", state=encoded_state, error=None, base_url=None, config_service=cs)
        assert result["success"] is False
        assert "OAuthConfigError" in result["error"]

    @pytest.mark.asyncio
    async def test_callback_oauth_config_not_found(self):
        """Line 2343 — oauth config deleted."""
        from app.api.routes.toolsets import handle_toolset_oauth_callback
        cs = AsyncMock()
        encoded_state = self._encode_state("orig-state", "i1", "u1")
        instances = [{"_id": "i1", "orgId": "o1", "toolsetType": "jira", "authType": "OAUTH", "oauthConfigId": "cfg-1"}]

        req = _make_request()

        with patch("app.api.routes.toolsets._get_user_context", return_value={"user_id": "u1", "org_id": "o1"}), \
             patch("app.api.routes.toolsets._load_toolset_instances", new_callable=AsyncMock, return_value=instances), \
             patch("app.api.routes.toolsets._get_oauth_config_by_id", new_callable=AsyncMock, return_value=None):
            result = await handle_toolset_oauth_callback(req, code="code", state=encoded_state, error=None, base_url=None, config_service=cs)
        assert result["success"] is False

    @pytest.mark.asyncio
    async def test_callback_no_token_returned(self):
        """Line 2370 — token exchange returns no access token."""
        from app.api.routes.toolsets import handle_toolset_oauth_callback
        cs = AsyncMock()
        encoded_state = self._encode_state("orig-state", "i1", "u1")
        instances = [{"_id": "i1", "orgId": "o1", "toolsetType": "jira", "authType": "OAUTH", "oauthConfigId": "cfg-1"}]
        oauth_cfg = {"_id": "cfg-1", "config": {"clientId": "cid", "clientSecret": "cs"}}

        req = _make_request()
        req.app.state.toolset_registry = _make_oauth_registry("jira")

        mock_token = MagicMock()
        mock_token.access_token = None
        mock_provider = AsyncMock()
        mock_provider.handle_callback = AsyncMock(return_value=mock_token)
        mock_provider.close = AsyncMock()

        with patch("app.api.routes.toolsets._get_user_context", return_value={"user_id": "u1", "org_id": "o1"}), \
             patch("app.api.routes.toolsets._load_toolset_instances", new_callable=AsyncMock, return_value=instances), \
             patch("app.api.routes.toolsets._get_oauth_config_by_id", new_callable=AsyncMock, return_value=oauth_cfg), \
             patch("app.api.routes.toolsets._build_oauth_config", new_callable=AsyncMock, return_value={"clientId": "cid", "clientSecret": "cs"}), \
             patch("app.api.routes.toolsets.OAuthProvider", return_value=mock_provider):
            result = await handle_toolset_oauth_callback(req, code="code", state=encoded_state, error=None, base_url=None, config_service=cs)
        assert result["success"] is False

    @pytest.mark.asyncio
    async def test_factory_test_connection(self):
        """Jira/Confluence factory test_connection: base_url set or single site → no-op;
        multiple sites without base_url → ToolsetAuthError."""
        from app.agents.tools.factories.base import ToolsetAuthError
        from app.agents.tools.factories.confluence import ConfluenceClientFactory
        from app.agents.tools.factories.jira import JiraClientFactory
        from app.sources.external.common.atlassian import AtlassianCloudResource
        log = MagicMock()

        # baseUrl set in auth_config → no accessible-resources call, no error
        await JiraClientFactory().test_connection(
            access_token="tok", auth_config={"baseUrl": "https://x.atlassian.net"},
            config_service=None, logger=log,
        )

        two = [
            AtlassianCloudResource(id="a", name="a", url="https://a.atlassian.net", scopes=[]),
            AtlassianCloudResource(id="b", name="b", url="https://b.atlassian.net", scopes=[]),
        ]
        with patch("app.agents.tools.factories.jira.JiraClient.get_accessible_resources",
                   new_callable=AsyncMock, return_value=two):
            with pytest.raises(ToolsetAuthError, match="multiple Jira sites"):
                await JiraClientFactory().test_connection(
                    access_token="tok", auth_config={}, config_service=None, logger=log,
                )

        one = [AtlassianCloudResource(id="a", name="a", url="https://a.atlassian.net", scopes=[])]
        with patch("app.agents.tools.factories.confluence.ConfluenceClient.get_accessible_resources",
                   new_callable=AsyncMock, return_value=one):
            # single site → no error
            await ConfluenceClientFactory().test_connection(
                access_token="tok", auth_config={}, config_service=None, logger=log,
            )

    @pytest.mark.asyncio
    async def test_callback_setup_error_blocks_and_notifies(self):
        """A factory's test_connection raising ToolsetAuthError → block auth, notify
        user, error redirect (generic; no toolset-specific code in the route)."""
        from app.agents.tools.factories.base import ToolsetAuthError
        from app.api.routes.toolsets import handle_toolset_oauth_callback
        cs = AsyncMock()
        cs.get_config = AsyncMock(return_value={})
        cs.set_config = AsyncMock()
        encoded_state = self._encode_state("orig-state", "i1", "u1")
        instances = [{"_id": "i1", "orgId": "o1", "toolsetType": "jira", "authType": "OAUTH", "oauthConfigId": "cfg-1"}]
        oauth_cfg = {"_id": "cfg-1", "config": {"clientId": "cid", "clientSecret": "cs"}}

        req = _make_request()
        req.app.state.toolset_registry = _make_oauth_registry("jira")

        mock_token = MagicMock()
        mock_token.access_token = "token123"
        mock_provider = AsyncMock()
        mock_provider.handle_callback = AsyncMock(return_value=mock_token)
        mock_provider.close = AsyncMock()

        notif = AsyncMock()
        fake_factory = MagicMock()
        fake_factory.test_connection = AsyncMock(
            side_effect=ToolsetAuthError("This OAuth app has access to multiple Jira sites.")
        )

        with patch("app.api.routes.toolsets._get_user_context", return_value={"user_id": "u1", "org_id": "o1"}), \
             patch("app.api.routes.toolsets._load_toolset_instances", new_callable=AsyncMock, return_value=instances), \
             patch("app.api.routes.toolsets._get_oauth_config_by_id", new_callable=AsyncMock, return_value=oauth_cfg), \
             patch("app.api.routes.toolsets._build_oauth_config", new_callable=AsyncMock, return_value={"clientId": "cid", "clientSecret": "cs"}), \
             patch("app.api.routes.toolsets.OAuthProvider", return_value=mock_provider), \
             patch("app.agents.tools.factories.registry.ClientFactoryRegistry.get_factory", return_value=fake_factory):
            result = await handle_toolset_oauth_callback(
                req, code="code", state=encoded_state, error=None, base_url=None,
                config_service=cs, notification_service=notif,
            )
        assert result["success"] is False
        assert result["error"] == "toolset_setup_error"
        assert "multiple Jira sites" in result["error_message"]
        notif.publish_notification.assert_awaited_once()
        cs.set_config.assert_not_called()

    @pytest.mark.asyncio
    async def test_callback_auth_status_update_exception_still_succeeds(self):
        """Lines 2390-2391 — auth status update exception doesn't block."""
        from app.api.routes.toolsets import handle_toolset_oauth_callback
        cs = AsyncMock()
        cs.get_config = AsyncMock(side_effect=RuntimeError("etcd fail"))
        cs.set_config = AsyncMock()

        encoded_state = self._encode_state("orig-state", "i1", "u1")
        instances = [{"_id": "i1", "orgId": "o1", "toolsetType": "jira", "authType": "OAUTH", "oauthConfigId": "cfg-1"}]
        oauth_cfg = {"_id": "cfg-1", "config": {"clientId": "cid", "clientSecret": "cs"}}

        req = _make_request()
        req.app.state.toolset_registry = _make_oauth_registry("jira")

        mock_token = MagicMock()
        mock_token.access_token = "token123"
        mock_provider = AsyncMock()
        mock_provider.handle_callback = AsyncMock(return_value=mock_token)
        mock_provider.close = AsyncMock()

        with patch("app.api.routes.toolsets._get_user_context", return_value={"user_id": "u1", "org_id": "o1"}), \
             patch("app.api.routes.toolsets._load_toolset_instances", new_callable=AsyncMock, return_value=instances), \
             patch("app.api.routes.toolsets._get_oauth_config_by_id", new_callable=AsyncMock, return_value=oauth_cfg), \
             patch("app.api.routes.toolsets._build_oauth_config", new_callable=AsyncMock, return_value={"clientId": "cid", "clientSecret": "cs"}), \
             patch("app.api.routes.toolsets.OAuthProvider", return_value=mock_provider), \
             patch.dict("sys.modules", {"app.connectors.core.base.token_service.startup_service": MagicMock(startup_service=MagicMock(get_toolset_token_refresh_service=MagicMock(return_value=None)))}):
            result = await handle_toolset_oauth_callback(req, code="code", state=encoded_state, error=None, base_url=None, config_service=cs)
        assert result["success"] is True

    @pytest.mark.asyncio
    async def test_refresh_scheduling_failure_still_succeeds(self):
        """Lines 2393-2402 — refresh scheduling failure doesn't break success."""
        from app.api.routes.toolsets import handle_toolset_oauth_callback
        cs = AsyncMock()
        cs.get_config = AsyncMock(return_value={})
        cs.set_config = AsyncMock()

        encoded_state = self._encode_state("orig-state", "i1", "u1")
        instances = [{"_id": "i1", "orgId": "o1", "toolsetType": "jira", "authType": "OAUTH", "oauthConfigId": "cfg-1"}]
        oauth_cfg = {"_id": "cfg-1", "config": {"clientId": "cid", "clientSecret": "cs"}}

        req = _make_request()
        req.app.state.toolset_registry = _make_oauth_registry("jira")

        mock_token = MagicMock()
        mock_token.access_token = "token123"
        mock_provider = AsyncMock()
        mock_provider.handle_callback = AsyncMock(return_value=mock_token)
        mock_provider.close = AsyncMock()

        mock_refresh = MagicMock()
        mock_refresh.schedule_token_refresh = AsyncMock(side_effect=RuntimeError("scheduler fail"))
        mock_startup = MagicMock()
        mock_startup.startup_service.get_toolset_token_refresh_service.return_value = mock_refresh

        with patch("app.api.routes.toolsets._get_user_context", return_value={"user_id": "u1", "org_id": "o1"}), \
             patch("app.api.routes.toolsets._load_toolset_instances", new_callable=AsyncMock, return_value=instances), \
             patch("app.api.routes.toolsets._get_oauth_config_by_id", new_callable=AsyncMock, return_value=oauth_cfg), \
             patch("app.api.routes.toolsets._build_oauth_config", new_callable=AsyncMock, return_value={"clientId": "cid", "clientSecret": "cs"}), \
             patch("app.api.routes.toolsets.OAuthProvider", return_value=mock_provider), \
             patch.dict("sys.modules", {"app.connectors.core.base.token_service.startup_service": mock_startup}):
            result = await handle_toolset_oauth_callback(req, code="code", state=encoded_state, error=None, base_url=None, config_service=cs)

        assert result["success"] is True


# ===================================================================
# list_toolset_oauth_configs — lines 2460, 2468->2470
# ===================================================================
class TestListToolsetOAuthConfigsBranches:

    @pytest.mark.asyncio
    async def test_filters_configs_by_org(self):
        """Line 2460 — configs from other orgs are skipped."""
        from app.api.routes.toolsets import list_toolset_oauth_configs
        cs = AsyncMock()

        req = _make_request()

        configs = [
            {"_id": "c1", "orgId": "o1", "config": {"clientId": "x"}},
            {"_id": "c2", "orgId": "other-org", "config": {"clientId": "y"}},
        ]

        with patch("app.api.routes.toolsets._get_user_context", return_value={"user_id": "u1", "org_id": "o1"}), \
             patch("app.api.routes.toolsets._check_user_is_admin", new_callable=AsyncMock, return_value=False), \
             patch("app.api.routes.toolsets._get_oauth_configs_for_type", new_callable=AsyncMock, return_value=configs):
            result = await list_toolset_oauth_configs("jira", req, config_service=cs)

        assert result["total"] == 1
        assert result["oauthConfigs"][0]["_id"] == "c1"

    @pytest.mark.asyncio
    async def test_admin_sees_client_secret_set_flag(self):
        """Lines 2468-2469 — admin gets clientSecretSet when secret present."""
        from app.api.routes.toolsets import list_toolset_oauth_configs
        cs = AsyncMock()

        req = _make_request()

        configs = [
            {"_id": "c1", "orgId": "o1", "config": {"clientId": "x", "clientSecret": "secret"}},
        ]

        with patch("app.api.routes.toolsets._get_user_context", return_value={"user_id": "u1", "org_id": "o1"}), \
             patch("app.api.routes.toolsets._check_user_is_admin", new_callable=AsyncMock, return_value=True), \
             patch("app.api.routes.toolsets._get_oauth_configs_for_type", new_callable=AsyncMock, return_value=configs):
            result = await list_toolset_oauth_configs("jira", req, config_service=cs)

        assert result["oauthConfigs"][0]["clientSecretSet"] is True


# ===================================================================
# update_toolset_oauth_config — lines 2504->2511, 2539->2538
# ===================================================================
class TestUpdateToolsetOAuthConfigBranches:

    @pytest.mark.asyncio
    async def test_base_url_fallback_on_exception(self):
        """Lines 2508-2509 — endpoints fetch fails, falls back to localhost."""
        from app.api.routes.toolsets import update_toolset_oauth_config
        cs = AsyncMock()

        async def mock_get(path, **kw):
            if "endpoints" in str(path):
                raise RuntimeError("etcd down")
            return None
        cs.get_config = AsyncMock(side_effect=mock_get)
        cs.set_config = AsyncMock()

        req = _make_request(body_dict={"authConfig": {"clientId": "c", "clientSecret": "s"}})
        req.app.state.toolset_registry = _make_oauth_registry("jira")

        cfg = {"_id": "cfg-1", "orgId": "o1", "oauthInstanceName": "J", "config": {"clientId": "old"}}

        with patch("app.api.routes.toolsets._get_user_context", return_value={"user_id": "u1", "org_id": "o1"}), \
             patch("app.api.routes.toolsets._check_user_is_admin", new_callable=AsyncMock, return_value=True), \
             patch("app.api.routes.toolsets._get_oauth_config_by_id", new_callable=AsyncMock, return_value=cfg), \
             patch("app.api.routes.toolsets._create_or_update_toolset_oauth_config", new_callable=AsyncMock, return_value="cfg-1"), \
             patch("app.api.routes.toolsets._load_toolset_instances", new_callable=AsyncMock, return_value=[]):
            result = await update_toolset_oauth_config("jira", "cfg-1", req, config_service=cs)

        assert result["status"] == "success"

    @pytest.mark.asyncio
    async def test_deauth_exception_filtered_out(self):
        """Lines 2538-2540 — isinstance(r, int) filters out exceptions."""
        from app.api.routes.toolsets import update_toolset_oauth_config
        cs = AsyncMock()
        cs.get_config = AsyncMock(return_value=None)
        cs.set_config = AsyncMock()

        req = _make_request(body_dict={"authConfig": {"clientId": "c", "clientSecret": "s"}, "baseUrl": "http://localhost"})
        req.app.state.toolset_registry = _make_oauth_registry("jira")

        cfg = {"_id": "cfg-1", "orgId": "o1", "oauthInstanceName": "J", "config": {"clientId": "old"}}
        instances = [
            {"_id": "i1", "orgId": "o1", "oauthConfigId": "cfg-1"},
            {"_id": "i2", "orgId": "o1", "oauthConfigId": "cfg-1"},
        ]

        with patch("app.api.routes.toolsets._get_user_context", return_value={"user_id": "u1", "org_id": "o1"}), \
             patch("app.api.routes.toolsets._check_user_is_admin", new_callable=AsyncMock, return_value=True), \
             patch("app.api.routes.toolsets._get_oauth_config_by_id", new_callable=AsyncMock, return_value=cfg), \
             patch("app.api.routes.toolsets._create_or_update_toolset_oauth_config", new_callable=AsyncMock, return_value="cfg-1"), \
             patch("app.api.routes.toolsets._load_toolset_instances", new_callable=AsyncMock, return_value=instances), \
             patch("app.api.routes.toolsets._deauth_all_instance_users", new_callable=AsyncMock, side_effect=[2, RuntimeError("fail")]):
            result = await update_toolset_oauth_config("jira", "cfg-1", req, config_service=cs)

        assert result["deauthenticatedUserCount"] == 2


# ===================================================================
# _build_toolsets_list_response — lines 2717-2718, 2732, 2763, 2829-2879
# ===================================================================
class TestBuildToolsetsListResponseBranches:

    @pytest.mark.asyncio
    async def test_toolset_type_filter(self):
        """Lines 2716-2721 — filtering by toolsetType."""
        from app.api.routes.toolsets import _build_toolsets_list_response
        cs = AsyncMock()
        instances = [
            {"_id": "i1", "orgId": "o1", "toolsetType": "jira", "authType": "API_TOKEN", "instanceName": "J"},
            {"_id": "i2", "orgId": "o1", "toolsetType": "slack", "authType": "OAUTH", "instanceName": "S"},
        ]
        req = _make_request()
        registry = MagicMock()
        registry.get_toolset_metadata.return_value = {"display_name": "Jira", "description": "d", "category": "app",
                                                       "supported_auth_types": ["API_TOKEN"], "tools": [], "icon_path": ""}
        registry.list_toolsets.return_value = ["jira", "slack"]
        req.app.state.toolset_registry = registry

        with patch("app.api.routes.toolsets._load_toolset_instances", new_callable=AsyncMock, return_value=instances):
            result = await _build_toolsets_list_response(
                request=req, org_id="o1", config_service=cs,
                search=None, page=1, limit=20, include_registry=False,
                fetch_auth_for_instance=AsyncMock(return_value=None),
                toolset_type_filter="jira",
            )
        assert all(t["toolsetType"] == "jira" for t in result["toolsets"])

    @pytest.mark.asyncio
    async def test_search_matches_display_name_from_registry(self):
        """Lines 2730-2735 — search matches via registry metadata display_name."""
        from app.api.routes.toolsets import _build_toolsets_list_response
        cs = AsyncMock()
        instances = [{"_id": "i1", "orgId": "o1", "toolsetType": "jira", "authType": "API_TOKEN", "instanceName": "my-inst"}]
        req = _make_request()
        registry = MagicMock()
        registry.get_toolset_metadata.return_value = {
            "display_name": "Jira Issue Tracker", "description": "track", "category": "app",
            "supported_auth_types": [], "tools": [], "icon_path": "", "name": "jira",
        }
        registry.list_toolsets.return_value = ["jira"]
        req.app.state.toolset_registry = registry

        with patch("app.api.routes.toolsets._load_toolset_instances", new_callable=AsyncMock, return_value=instances):
            result = await _build_toolsets_list_response(
                request=req, org_id="o1", config_service=cs,
                search="Issue Tracker", page=1, limit=20, include_registry=False,
                fetch_auth_for_instance=AsyncMock(return_value=None),
            )
        assert len(result["toolsets"]) == 1

    @pytest.mark.asyncio
    async def test_search_no_metadata_returns_false(self):
        """Line 2731-2732 — registry returns None for metadata, search returns False."""
        from app.api.routes.toolsets import _build_toolsets_list_response
        cs = AsyncMock()
        instances = [{"_id": "i1", "orgId": "o1", "toolsetType": "unknown", "authType": "API_TOKEN", "instanceName": "x"}]
        req = _make_request()
        registry = MagicMock()
        registry.get_toolset_metadata.return_value = None
        registry.list_toolsets.return_value = []
        req.app.state.toolset_registry = registry

        with patch("app.api.routes.toolsets._load_toolset_instances", new_callable=AsyncMock, return_value=instances):
            result = await _build_toolsets_list_response(
                request=req, org_id="o1", config_service=cs,
                search="something", page=1, limit=20, include_registry=False,
                fetch_auth_for_instance=AsyncMock(return_value=None),
            )
        assert len(result["toolsets"]) == 0

    @pytest.mark.asyncio
    async def test_non_list_doc_links_normalized(self):
        """Lines 2762-2763 — documentationLinks not a list, normalized to []."""
        from app.api.routes.toolsets import _build_toolsets_list_response
        cs = AsyncMock()
        instances = [{"_id": "i1", "orgId": "o1", "toolsetType": "jira", "authType": "API_TOKEN", "instanceName": "J"}]
        req = _make_request()
        registry = MagicMock()
        registry.get_toolset_metadata.return_value = {
            "display_name": "Jira", "description": "d", "category": "app",
            "supported_auth_types": [], "tools": [], "icon_path": "",
            "config": {"documentationLinks": "not-a-list"},
        }
        registry.list_toolsets.return_value = ["jira"]
        req.app.state.toolset_registry = registry

        with patch("app.api.routes.toolsets._load_toolset_instances", new_callable=AsyncMock, return_value=instances):
            result = await _build_toolsets_list_response(
                request=req, org_id="o1", config_service=cs,
                search=None, page=1, limit=20, include_registry=False,
                fetch_auth_for_instance=AsyncMock(return_value=None),
            )
        assert result["toolsets"][0]["documentationLinks"] == []

    @pytest.mark.asyncio
    async def test_registry_synthetic_entries_with_type_filter(self):
        """Lines 2828-2829 — registry_scope filter skips non-matching types."""
        from app.api.routes.toolsets import _build_toolsets_list_response
        cs = AsyncMock()
        req = _make_request()
        registry = MagicMock()

        def meta_for(name):
            return {
                "name": name, "display_name": name.title(), "description": f"{name} desc",
                "category": "app", "supported_auth_types": ["OAUTH"], "tools": [],
                "icon_path": "", "isInternal": False,
            }
        registry.get_toolset_metadata.side_effect = meta_for
        registry.list_toolsets.return_value = ["jira", "slack", "github"]
        req.app.state.toolset_registry = registry

        with patch("app.api.routes.toolsets._load_toolset_instances", new_callable=AsyncMock, return_value=[]):
            result = await _build_toolsets_list_response(
                request=req, org_id="o1", config_service=cs,
                search=None, page=1, limit=20, include_registry=True,
                fetch_auth_for_instance=AsyncMock(return_value=None),
                toolset_type_filter="jira",
            )
        types = [t["toolsetType"] for t in result["toolsets"]]
        assert "jira" in types
        assert "slack" not in types

    @pytest.mark.asyncio
    async def test_registry_synthetic_entry_search_filter(self):
        """Lines 2833-2841 — search filter on synthetic entries."""
        from app.api.routes.toolsets import _build_toolsets_list_response
        cs = AsyncMock()
        req = _make_request()
        registry = MagicMock()

        def meta_for(name):
            return {
                "name": name, "display_name": name.title(), "description": f"{name} tools",
                "category": "app", "supported_auth_types": ["OAUTH"], "tools": [],
                "icon_path": "", "isInternal": False,
            }
        registry.get_toolset_metadata.side_effect = meta_for
        registry.list_toolsets.return_value = ["jira", "slack"]
        req.app.state.toolset_registry = registry

        with patch("app.api.routes.toolsets._load_toolset_instances", new_callable=AsyncMock, return_value=[]):
            result = await _build_toolsets_list_response(
                request=req, org_id="o1", config_service=cs,
                search="jira", page=1, limit=20, include_registry=True,
                fetch_auth_for_instance=AsyncMock(return_value=None),
            )
        assert len(result["toolsets"]) == 1
        assert result["toolsets"][0]["toolsetType"] == "jira"

    @pytest.mark.asyncio
    async def test_registry_synthetic_has_credentials_false_and_auth_null(self):
        """Lines 2876-2879 — synthetic entries get hasCredentials=False and auth=None."""
        from app.api.routes.toolsets import _build_toolsets_list_response
        cs = AsyncMock()
        req = _make_request()
        registry = MagicMock()
        registry.get_toolset_metadata.return_value = {
            "name": "jira", "display_name": "Jira", "description": "d", "category": "app",
            "supported_auth_types": ["OAUTH"], "tools": [], "icon_path": "", "isInternal": False,
        }
        registry.list_toolsets.return_value = ["jira"]
        req.app.state.toolset_registry = registry

        with patch("app.api.routes.toolsets._load_toolset_instances", new_callable=AsyncMock, return_value=[]):
            result = await _build_toolsets_list_response(
                request=req, org_id="o1", config_service=cs,
                search=None, page=1, limit=20, include_registry=True,
                fetch_auth_for_instance=AsyncMock(return_value=None),
                include_has_credentials=True,
                include_auth_key_for_registry=True,
            )
        entry = result["toolsets"][0]
        assert entry["hasCredentials"] is False
        assert entry["auth"] is None

    @pytest.mark.asyncio
    async def test_registry_synthetic_non_list_doc_links(self):
        """Lines 2848-2849 — synthetic entry with non-list documentationLinks."""
        from app.api.routes.toolsets import _build_toolsets_list_response
        cs = AsyncMock()
        req = _make_request()
        registry = MagicMock()
        registry.get_toolset_metadata.return_value = {
            "name": "jira", "display_name": "Jira", "description": "d", "category": "app",
            "supported_auth_types": ["OAUTH"], "tools": [], "icon_path": "", "isInternal": False,
            "config": {"documentationLinks": "not-a-list"},
        }
        registry.list_toolsets.return_value = ["jira"]
        req.app.state.toolset_registry = registry

        with patch("app.api.routes.toolsets._load_toolset_instances", new_callable=AsyncMock, return_value=[]):
            result = await _build_toolsets_list_response(
                request=req, org_id="o1", config_service=cs,
                search=None, page=1, limit=20, include_registry=True,
                fetch_auth_for_instance=AsyncMock(return_value=None),
            )
        assert result["toolsets"][0]["documentationLinks"] == []


# ===================================================================
# _resolve_agent_with_permission — line 2934
# ===================================================================
class TestResolveAgentMalformedUser:

    @pytest.mark.asyncio
    async def test_user_without_key_or_id_raises_401(self):
        """Line 2934 — user record has no _key or id."""
        from app.api.routes.toolsets import _resolve_agent_with_permission
        req = _make_request()
        gp = AsyncMock()
        gp.get_user_by_user_id = AsyncMock(return_value={"name": "user-without-key"})

        with patch("app.api.routes.toolsets._get_user_context", return_value={"user_id": "u1", "org_id": "o1"}), \
             patch("app.api.routes.toolsets._get_graph_provider", return_value=gp):
            with pytest.raises(HTTPException) as exc:
                await _resolve_agent_with_permission("a1", req)
        assert exc.value.status_code == 401
        assert "malformed" in exc.value.detail


# ===================================================================
# Agent credential endpoints — lines 3100-3102, 3137-3139, 3163-3170
# ===================================================================
class TestAgentCredentialEndpointErrors:

    @pytest.mark.asyncio
    async def test_authenticate_agent_set_config_failure(self):
        """Lines 3100-3102 — set_config raises on agent authenticate."""
        from app.api.routes.toolsets import authenticate_agent_toolset
        cs = AsyncMock()
        cs.set_config = AsyncMock(side_effect=RuntimeError("storage fail"))
        instances = [{"_id": "i1", "orgId": "o1", "toolsetType": "jira", "authType": "API_TOKEN"}]

        req = _make_request(body_dict={"auth": {"apiToken": "tok"}})

        with patch("app.api.routes.toolsets._require_agent_edit_access", new_callable=AsyncMock, return_value={"_key": "a1"}), \
             patch("app.api.routes.toolsets._get_user_context", return_value={"user_id": "u1", "org_id": "o1"}), \
             patch("app.api.routes.toolsets._load_toolset_instances", new_callable=AsyncMock, return_value=instances):
            with pytest.raises(HTTPException) as exc:
                await authenticate_agent_toolset("a1", "i1", req, config_service=cs)
            assert exc.value.status_code == 500

    @pytest.mark.asyncio
    async def test_update_agent_creds_set_config_failure(self):
        """Lines 3137-3139 — set_config raises on agent credential update."""
        from app.api.routes.toolsets import update_agent_toolset_credentials
        cs = AsyncMock()
        cs.get_config = AsyncMock(return_value={"isAuthenticated": True, "auth": {}})
        cs.set_config = AsyncMock(side_effect=RuntimeError("storage fail"))

        req = _make_request(body_dict={"auth": {"apiToken": "new-tok"}})

        with patch("app.api.routes.toolsets._require_agent_edit_access", new_callable=AsyncMock, return_value={"_key": "a1"}):
            with pytest.raises(HTTPException) as exc:
                await update_agent_toolset_credentials("a1", "i1", req, config_service=cs)
            assert exc.value.status_code == 500

    @pytest.mark.asyncio
    async def test_remove_agent_creds_refresh_cancel_exception(self):
        """Lines 3163-3165 — cancel_refresh_task raises, still returns 200."""
        from app.api.routes.toolsets import remove_agent_toolset_credentials
        cs = AsyncMock()
        cs.delete_config = AsyncMock()

        req = _make_request()

        mock_refresh = MagicMock()
        mock_refresh.cancel_refresh_task = MagicMock(side_effect=RuntimeError("no task"))

        with patch("app.api.routes.toolsets._require_agent_edit_access", new_callable=AsyncMock, return_value={"_key": "a1"}), \
             patch.dict("sys.modules", {"app.connectors.core.base.token_service.startup_service": MagicMock(startup_service=MagicMock(get_toolset_token_refresh_service=MagicMock(return_value=mock_refresh)))}):
            result = await remove_agent_toolset_credentials("a1", "i1", req, config_service=cs)
        assert result["status"] == "success"

    @pytest.mark.asyncio
    async def test_remove_agent_creds_delete_config_exception(self):
        """Lines 3169-3170 — delete_config raises, still returns 200."""
        from app.api.routes.toolsets import remove_agent_toolset_credentials
        cs = AsyncMock()
        cs.delete_config = AsyncMock(side_effect=RuntimeError("etcd fail"))

        req = _make_request()

        with patch("app.api.routes.toolsets._require_agent_edit_access", new_callable=AsyncMock, return_value={"_key": "a1"}), \
             patch.dict("sys.modules", {"app.connectors.core.base.token_service.startup_service": MagicMock(startup_service=MagicMock(get_toolset_token_refresh_service=MagicMock(return_value=None)))}):
            result = await remove_agent_toolset_credentials("a1", "i1", req, config_service=cs)
        assert result["status"] == "success"


# ===================================================================
# reauthenticate_agent_toolset — lines 3203-3205, 3209-3211
# ===================================================================
class TestReauthenticateAgentToolsetErrors:

    @pytest.mark.asyncio
    async def test_refresh_cancel_exception_still_succeeds(self):
        """Lines 3203-3205 — cancel exception is warning-only."""
        from app.api.routes.toolsets import reauthenticate_agent_toolset
        cs = AsyncMock()
        cs.delete_config = AsyncMock()
        instances = [{"_id": "i1", "orgId": "o1"}]

        req = _make_request()

        with patch("app.api.routes.toolsets._require_agent_edit_access", new_callable=AsyncMock, return_value={"_key": "a1"}), \
             patch("app.api.routes.toolsets._get_user_context", return_value={"user_id": "u1", "org_id": "o1"}), \
             patch("app.api.routes.toolsets._load_toolset_instances", new_callable=AsyncMock, return_value=instances), \
             patch.dict("sys.modules", {"app.connectors.core.base.token_service.startup_service": MagicMock(startup_service=MagicMock(get_toolset_token_refresh_service=MagicMock(side_effect=RuntimeError("no service"))))}):
            result = await reauthenticate_agent_toolset("a1", "i1", req, config_service=cs)
        assert result["status"] == "success"

    @pytest.mark.asyncio
    async def test_delete_config_failure_raises_500(self):
        """Lines 3209-3211 — delete_config failure raises 500."""
        from app.api.routes.toolsets import reauthenticate_agent_toolset
        cs = AsyncMock()
        cs.delete_config = AsyncMock(side_effect=RuntimeError("etcd fail"))
        instances = [{"_id": "i1", "orgId": "o1"}]

        req = _make_request()

        with patch("app.api.routes.toolsets._require_agent_edit_access", new_callable=AsyncMock, return_value={"_key": "a1"}), \
             patch("app.api.routes.toolsets._get_user_context", return_value={"user_id": "u1", "org_id": "o1"}), \
             patch("app.api.routes.toolsets._load_toolset_instances", new_callable=AsyncMock, return_value=instances), \
             patch.dict("sys.modules", {"app.connectors.core.base.token_service.startup_service": MagicMock(startup_service=MagicMock(get_toolset_token_refresh_service=MagicMock(return_value=None)))}):
            with pytest.raises(HTTPException) as exc:
                await reauthenticate_agent_toolset("a1", "i1", req, config_service=cs)
            assert exc.value.status_code == 500


# ===================================================================
# get_agent_toolset_oauth_url — lines 3243-3300
# ===================================================================
class TestGetAgentToolsetOAuthUrlFlows:

    @pytest.mark.asyncio
    async def test_happy_path(self):
        """Lines 3243-3293 — agent OAuth authorize returns URL with is_agent state."""
        from app.api.routes.toolsets import get_agent_toolset_oauth_url
        cs = AsyncMock()
        instances = [{"_id": "i1", "orgId": "o1", "toolsetType": "jira", "authType": "OAUTH", "oauthConfigId": "cfg-1"}]
        oauth_cfg = {"_id": "cfg-1", "config": {"clientId": "cid", "clientSecret": "cs"}}

        req = _make_request()
        req.app.state.toolset_registry = _make_oauth_registry("jira")

        mock_provider = AsyncMock()
        mock_provider.start_authorization = AsyncMock(
            return_value="https://auth.example.com/authorize?state=abc123&scope=read"
        )
        mock_provider.close = AsyncMock()

        with patch("app.api.routes.toolsets._require_agent_edit_access", new_callable=AsyncMock, return_value={"_key": "a1"}), \
             patch("app.api.routes.toolsets._get_user_context", return_value={"user_id": "u1", "org_id": "o1"}), \
             patch("app.api.routes.toolsets._load_toolset_instances", new_callable=AsyncMock, return_value=instances), \
             patch("app.api.routes.toolsets._get_oauth_config_by_id", new_callable=AsyncMock, return_value=oauth_cfg), \
             patch("app.api.routes.toolsets._build_oauth_config", new_callable=AsyncMock, return_value={"clientId": "cid", "clientSecret": "cs", "scopes": ["read"]}), \
             patch("app.api.routes.toolsets.OAuthProvider", return_value=mock_provider):
            result = await get_agent_toolset_oauth_url("a1", "i1", req, config_service=cs)

        assert result["success"] is True
        assert "authorizationUrl" in result
        from app.api.routes.toolsets import _decode_state_with_instance
        decoded = _decode_state_with_instance(result["state"])
        assert decoded["is_agent"] is True
        mock_provider.close.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_non_oauth_raises(self):
        from app.api.routes.toolsets import OAuthConfigError, get_agent_toolset_oauth_url
        cs = AsyncMock()
        instances = [{"_id": "i1", "orgId": "o1", "toolsetType": "jira", "authType": "API_TOKEN"}]
        req = _make_request()

        with patch("app.api.routes.toolsets._require_agent_edit_access", new_callable=AsyncMock, return_value={"_key": "a1"}), \
             patch("app.api.routes.toolsets._get_user_context", return_value={"user_id": "u1", "org_id": "o1"}), \
             patch("app.api.routes.toolsets._load_toolset_instances", new_callable=AsyncMock, return_value=instances):
            with pytest.raises(OAuthConfigError):
                await get_agent_toolset_oauth_url("a1", "i1", req, config_service=cs)

    @pytest.mark.asyncio
    async def test_missing_oauth_config_raises(self):
        from app.api.routes.toolsets import OAuthConfigError, get_agent_toolset_oauth_url
        cs = AsyncMock()
        instances = [{"_id": "i1", "orgId": "o1", "toolsetType": "jira", "authType": "OAUTH"}]
        req = _make_request()

        with patch("app.api.routes.toolsets._require_agent_edit_access", new_callable=AsyncMock, return_value={"_key": "a1"}), \
             patch("app.api.routes.toolsets._get_user_context", return_value={"user_id": "u1", "org_id": "o1"}), \
             patch("app.api.routes.toolsets._load_toolset_instances", new_callable=AsyncMock, return_value=instances):
            with pytest.raises(OAuthConfigError):
                await get_agent_toolset_oauth_url("a1", "i1", req, config_service=cs)

    @pytest.mark.asyncio
    async def test_start_authorization_failure_raises_500(self):
        from app.api.routes.toolsets import get_agent_toolset_oauth_url
        cs = AsyncMock()
        instances = [{"_id": "i1", "orgId": "o1", "toolsetType": "jira", "authType": "OAUTH", "oauthConfigId": "cfg-1"}]
        oauth_cfg = {"_id": "cfg-1", "config": {"clientId": "cid", "clientSecret": "cs"}}

        req = _make_request()
        req.app.state.toolset_registry = _make_oauth_registry("jira")

        mock_provider = AsyncMock()
        mock_provider.start_authorization = AsyncMock(side_effect=RuntimeError("network fail"))
        mock_provider.close = AsyncMock()

        with patch("app.api.routes.toolsets._require_agent_edit_access", new_callable=AsyncMock, return_value={"_key": "a1"}), \
             patch("app.api.routes.toolsets._get_user_context", return_value={"user_id": "u1", "org_id": "o1"}), \
             patch("app.api.routes.toolsets._load_toolset_instances", new_callable=AsyncMock, return_value=instances), \
             patch("app.api.routes.toolsets._get_oauth_config_by_id", new_callable=AsyncMock, return_value=oauth_cfg), \
             patch("app.api.routes.toolsets._build_oauth_config", new_callable=AsyncMock, return_value={"clientId": "cid", "clientSecret": "cs"}), \
             patch("app.api.routes.toolsets.OAuthProvider", return_value=mock_provider):
            with pytest.raises(HTTPException) as exc:
                await get_agent_toolset_oauth_url("a1", "i1", req, config_service=cs)
            assert exc.value.status_code == 500

    @pytest.mark.asyncio
    async def test_empty_auth_url_raises(self):
        """Line 3275 — empty URL from start_authorization."""
        from app.api.routes.toolsets import OAuthConfigError, get_agent_toolset_oauth_url
        cs = AsyncMock()
        instances = [{"_id": "i1", "orgId": "o1", "toolsetType": "jira", "authType": "OAUTH", "oauthConfigId": "cfg-1"}]
        oauth_cfg = {"_id": "cfg-1", "config": {"clientId": "cid", "clientSecret": "cs"}}
        req = _make_request()
        req.app.state.toolset_registry = _make_oauth_registry("jira")
        mock_provider = AsyncMock()
        mock_provider.start_authorization = AsyncMock(return_value=None)
        mock_provider.close = AsyncMock()

        with patch("app.api.routes.toolsets._require_agent_edit_access", new_callable=AsyncMock, return_value={"_key": "a1"}), \
             patch("app.api.routes.toolsets._get_user_context", return_value={"user_id": "u1", "org_id": "o1"}), \
             patch("app.api.routes.toolsets._load_toolset_instances", new_callable=AsyncMock, return_value=instances), \
             patch("app.api.routes.toolsets._get_oauth_config_by_id", new_callable=AsyncMock, return_value=oauth_cfg), \
             patch("app.api.routes.toolsets._build_oauth_config", new_callable=AsyncMock, return_value={"clientId": "cid", "clientSecret": "cs"}), \
             patch("app.api.routes.toolsets.OAuthProvider", return_value=mock_provider):
            with pytest.raises(OAuthConfigError, match="empty"):
                await get_agent_toolset_oauth_url("a1", "i1", req, config_service=cs)

    @pytest.mark.asyncio
    async def test_missing_state_param_raises(self):
        """Line 3285 — auth URL without state param."""
        from app.api.routes.toolsets import OAuthConfigError, get_agent_toolset_oauth_url
        cs = AsyncMock()
        instances = [{"_id": "i1", "orgId": "o1", "toolsetType": "jira", "authType": "OAUTH", "oauthConfigId": "cfg-1"}]
        oauth_cfg = {"_id": "cfg-1", "config": {"clientId": "cid", "clientSecret": "cs"}}
        req = _make_request()
        req.app.state.toolset_registry = _make_oauth_registry("jira")
        mock_provider = AsyncMock()
        mock_provider.start_authorization = AsyncMock(return_value="https://auth.com/authorize?scope=read")
        mock_provider.close = AsyncMock()

        with patch("app.api.routes.toolsets._require_agent_edit_access", new_callable=AsyncMock, return_value={"_key": "a1"}), \
             patch("app.api.routes.toolsets._get_user_context", return_value={"user_id": "u1", "org_id": "o1"}), \
             patch("app.api.routes.toolsets._load_toolset_instances", new_callable=AsyncMock, return_value=instances), \
             patch("app.api.routes.toolsets._get_oauth_config_by_id", new_callable=AsyncMock, return_value=oauth_cfg), \
             patch("app.api.routes.toolsets._build_oauth_config", new_callable=AsyncMock, return_value={"clientId": "cid", "clientSecret": "cs"}), \
             patch("app.api.routes.toolsets.OAuthProvider", return_value=mock_provider):
            with pytest.raises(OAuthConfigError, match="missing"):
                await get_agent_toolset_oauth_url("a1", "i1", req, config_service=cs)

    @pytest.mark.asyncio
    async def test_deleted_oauth_config_raises(self):
        """Line 3254 — OAuth config not found."""
        from app.api.routes.toolsets import OAuthConfigError, get_agent_toolset_oauth_url
        cs = AsyncMock()
        instances = [{"_id": "i1", "orgId": "o1", "toolsetType": "jira", "authType": "OAUTH", "oauthConfigId": "cfg-1"}]
        req = _make_request()

        with patch("app.api.routes.toolsets._require_agent_edit_access", new_callable=AsyncMock, return_value={"_key": "a1"}), \
             patch("app.api.routes.toolsets._get_user_context", return_value={"user_id": "u1", "org_id": "o1"}), \
             patch("app.api.routes.toolsets._load_toolset_instances", new_callable=AsyncMock, return_value=instances), \
             patch("app.api.routes.toolsets._get_oauth_config_by_id", new_callable=AsyncMock, return_value=None):
            with pytest.raises(OAuthConfigError, match="not found"):
                await get_agent_toolset_oauth_url("a1", "i1", req, config_service=cs)

    @pytest.mark.asyncio
    async def test_instance_not_found_raises_404(self):
        from app.api.routes.toolsets import get_agent_toolset_oauth_url
        cs = AsyncMock()
        req = _make_request()

        with patch("app.api.routes.toolsets._require_agent_edit_access", new_callable=AsyncMock, return_value={"_key": "a1"}), \
             patch("app.api.routes.toolsets._get_user_context", return_value={"user_id": "u1", "org_id": "o1"}), \
             patch("app.api.routes.toolsets._load_toolset_instances", new_callable=AsyncMock, return_value=[]):
            with pytest.raises(HTTPException) as exc:
                await get_agent_toolset_oauth_url("a1", "no-such", req, config_service=cs)
            assert exc.value.status_code == 404

    @pytest.mark.asyncio
    async def test_strips_invalid_token_access_type(self):
        """Lines 3280-3281 — token_access_type=null stripped from URL."""
        from app.api.routes.toolsets import get_agent_toolset_oauth_url
        cs = AsyncMock()
        instances = [{"_id": "i1", "orgId": "o1", "toolsetType": "jira", "authType": "OAUTH", "oauthConfigId": "cfg-1"}]
        oauth_cfg = {"_id": "cfg-1", "config": {"clientId": "cid", "clientSecret": "cs"}}

        req = _make_request()
        req.app.state.toolset_registry = _make_oauth_registry("jira")

        mock_provider = AsyncMock()
        mock_provider.start_authorization = AsyncMock(
            return_value="https://auth.example.com/authorize?state=abc&token_access_type=null"
        )
        mock_provider.close = AsyncMock()

        with patch("app.api.routes.toolsets._require_agent_edit_access", new_callable=AsyncMock, return_value={"_key": "a1"}), \
             patch("app.api.routes.toolsets._get_user_context", return_value={"user_id": "u1", "org_id": "o1"}), \
             patch("app.api.routes.toolsets._load_toolset_instances", new_callable=AsyncMock, return_value=instances), \
             patch("app.api.routes.toolsets._get_oauth_config_by_id", new_callable=AsyncMock, return_value=oauth_cfg), \
             patch("app.api.routes.toolsets._build_oauth_config", new_callable=AsyncMock, return_value={"clientId": "cid", "clientSecret": "cs"}), \
             patch("app.api.routes.toolsets.OAuthProvider", return_value=mock_provider):
            result = await get_agent_toolset_oauth_url("a1", "i1", req, config_service=cs)

        assert "token_access_type" not in result["authorizationUrl"]
