import json
import logging
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException


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
