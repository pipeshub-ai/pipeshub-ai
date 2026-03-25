"""Tests for app.api.routes.toolsets helper functions, models, and exceptions."""
import json
import logging
import uuid
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException


# ---------------------------------------------------------------------------
# Custom Exceptions
# ---------------------------------------------------------------------------

class TestToolsetError:
    def test_base_error(self):
        from app.api.routes.toolsets import ToolsetError
        e = ToolsetError("something broke", 500)
        assert e.status_code == 500
        assert e.detail == "something broke"

    def test_default_status_code(self):
        from app.api.routes.toolsets import ToolsetError
        e = ToolsetError("oops")
        assert e.status_code == 500


class TestToolsetNotFoundError:
    def test_message_and_status(self):
        from app.api.routes.toolsets import ToolsetNotFoundError
        e = ToolsetNotFoundError("jira")
        assert e.status_code == 404
        assert "jira" in e.detail


class TestToolsetConfigNotFoundError:
    def test_message_and_status(self):
        from app.api.routes.toolsets import ToolsetConfigNotFoundError
        e = ToolsetConfigNotFoundError("slack")
        assert e.status_code == 404
        assert "slack" in e.detail
        assert "not configured" in e.detail


class TestToolsetAlreadyExistsError:
    def test_message_and_status(self):
        from app.api.routes.toolsets import ToolsetAlreadyExistsError
        e = ToolsetAlreadyExistsError("jira")
        assert e.status_code == 409
        assert "already configured" in e.detail


class TestToolsetInUseError:
    def test_single_agent(self):
        from app.api.routes.toolsets import ToolsetInUseError
        e = ToolsetInUseError("jira", ["Agent A"])
        assert e.status_code == 409
        assert "Agent A" in e.detail
        assert "Cannot delete" in e.detail

    def test_multiple_agents(self):
        from app.api.routes.toolsets import ToolsetInUseError
        e = ToolsetInUseError("jira", ["Agent A", "Agent B"])
        assert e.status_code == 409
        assert "2 agents" in e.detail

    def test_many_agents_truncated(self):
        from app.api.routes.toolsets import ToolsetInUseError, MAX_AGENT_NAMES_DISPLAY
        agents = [f"Agent {i}" for i in range(MAX_AGENT_NAMES_DISPLAY + 3)]
        e = ToolsetInUseError("jira", agents)
        assert "and 3 more" in e.detail


class TestInvalidAuthConfigError:
    def test_message_and_status(self):
        from app.api.routes.toolsets import InvalidAuthConfigError
        e = InvalidAuthConfigError("missing client ID")
        assert e.status_code == 400
        assert "missing client ID" in e.detail


class TestOAuthConfigError:
    def test_message_and_status(self):
        from app.api.routes.toolsets import OAuthConfigError
        e = OAuthConfigError("token URL invalid")
        assert e.status_code == 400
        assert "token URL invalid" in e.detail


# ---------------------------------------------------------------------------
# Validation Functions
# ---------------------------------------------------------------------------

class TestValidateNonEmptyString:
    def test_valid_string(self):
        from app.api.routes.toolsets import _validate_non_empty_string
        result = _validate_non_empty_string("  hello  ", "name")
        assert result == "hello"

    def test_empty_string_raises(self):
        from app.api.routes.toolsets import _validate_non_empty_string
        with pytest.raises(HTTPException) as exc:
            _validate_non_empty_string("", "field")
        assert exc.value.status_code == 400

    def test_whitespace_string_raises(self):
        from app.api.routes.toolsets import _validate_non_empty_string
        with pytest.raises(HTTPException):
            _validate_non_empty_string("   ", "field")

    def test_none_raises(self):
        from app.api.routes.toolsets import _validate_non_empty_string
        with pytest.raises(HTTPException):
            _validate_non_empty_string(None, "field")

    def test_non_string_raises(self):
        from app.api.routes.toolsets import _validate_non_empty_string
        with pytest.raises(HTTPException):
            _validate_non_empty_string(123, "field")

    def test_false_value_raises(self):
        from app.api.routes.toolsets import _validate_non_empty_string
        with pytest.raises(HTTPException):
            _validate_non_empty_string(0, "field")


class TestValidateList:
    def test_valid_list(self):
        from app.api.routes.toolsets import _validate_list
        result = _validate_list([1, 2, 3], "items")
        assert result == [1, 2, 3]

    def test_empty_list_allowed(self):
        from app.api.routes.toolsets import _validate_list
        result = _validate_list([], "items")
        assert result == []

    def test_none_returns_empty_when_allow_empty(self):
        from app.api.routes.toolsets import _validate_list
        result = _validate_list(None, "items", allow_empty=True)
        assert result == []

    def test_none_raises_when_not_allow_empty(self):
        from app.api.routes.toolsets import _validate_list
        with pytest.raises(HTTPException) as exc:
            _validate_list(None, "items", allow_empty=False)
        assert exc.value.status_code == 400
        assert "required" in exc.value.detail

    def test_non_list_raises(self):
        from app.api.routes.toolsets import _validate_list
        with pytest.raises(HTTPException) as exc:
            _validate_list("not a list", "items")
        assert "must be a list" in exc.value.detail

    def test_dict_raises(self):
        from app.api.routes.toolsets import _validate_list
        with pytest.raises(HTTPException):
            _validate_list({"a": 1}, "items")

    def test_int_raises(self):
        from app.api.routes.toolsets import _validate_list
        with pytest.raises(HTTPException):
            _validate_list(42, "items")


class TestValidateDict:
    def test_valid_dict(self):
        from app.api.routes.toolsets import _validate_dict
        result = _validate_dict({"key": "val"}, "config")
        assert result == {"key": "val"}

    def test_empty_dict_allowed(self):
        from app.api.routes.toolsets import _validate_dict
        result = _validate_dict({}, "config")
        assert result == {}

    def test_none_returns_empty_when_allow_empty(self):
        from app.api.routes.toolsets import _validate_dict
        result = _validate_dict(None, "config", allow_empty=True)
        assert result == {}

    def test_none_raises_when_not_allow_empty(self):
        from app.api.routes.toolsets import _validate_dict
        with pytest.raises(HTTPException) as exc:
            _validate_dict(None, "config", allow_empty=False)
        assert exc.value.status_code == 400
        assert "required" in exc.value.detail

    def test_non_dict_raises(self):
        from app.api.routes.toolsets import _validate_dict
        with pytest.raises(HTTPException) as exc:
            _validate_dict([1, 2], "config")
        assert "must be an object" in exc.value.detail

    def test_string_raises(self):
        from app.api.routes.toolsets import _validate_dict
        with pytest.raises(HTTPException):
            _validate_dict("not a dict", "config")


# ---------------------------------------------------------------------------
# _has_oauth_credentials
# ---------------------------------------------------------------------------

class TestHasOauthCredentials:
    def test_empty_dict(self):
        from app.api.routes.toolsets import _has_oauth_credentials
        assert _has_oauth_credentials({}) is False

    def test_none(self):
        from app.api.routes.toolsets import _has_oauth_credentials
        assert _has_oauth_credentials(None) is False

    def test_not_a_dict(self):
        from app.api.routes.toolsets import _has_oauth_credentials
        assert _has_oauth_credentials("string") is False

    def test_only_infrastructure_fields(self):
        from app.api.routes.toolsets import _has_oauth_credentials
        config = {
            "type": "OAUTH",
            "redirectUri": "http://localhost",
            "scopes": ["read"],
            "authorizeUrl": "https://example.com/auth",
            "tokenUrl": "https://example.com/token",
        }
        assert _has_oauth_credentials(config) is False

    def test_has_client_id(self):
        from app.api.routes.toolsets import _has_oauth_credentials
        config = {"clientId": "abc123"}
        assert _has_oauth_credentials(config) is True

    def test_has_client_secret(self):
        from app.api.routes.toolsets import _has_oauth_credentials
        config = {"clientSecret": "secret"}
        assert _has_oauth_credentials(config) is True

    def test_has_tenant_id(self):
        from app.api.routes.toolsets import _has_oauth_credentials
        config = {"tenantId": "tenant-abc"}
        assert _has_oauth_credentials(config) is True

    def test_empty_string_values_ignored(self):
        from app.api.routes.toolsets import _has_oauth_credentials
        # Only truly empty strings are ignored by the string branch,
        # but whitespace-only strings fall through to the elif and are
        # considered truthy (not in (None, "", [], {}))
        config = {"clientId": "", "clientSecret": ""}
        assert _has_oauth_credentials(config) is False

    def test_whitespace_only_string_is_truthy(self):
        from app.api.routes.toolsets import _has_oauth_credentials
        # "  " is not in (None, "", [], {}), so the elif branch returns True
        config = {"clientId": "  "}
        assert _has_oauth_credentials(config) is True

    def test_none_values_ignored(self):
        from app.api.routes.toolsets import _has_oauth_credentials
        config = {"clientId": None, "extra": None}
        assert _has_oauth_credentials(config) is False

    def test_empty_list_and_dict_ignored(self):
        from app.api.routes.toolsets import _has_oauth_credentials
        config = {"field1": [], "field2": {}}
        assert _has_oauth_credentials(config) is False

    def test_non_string_truthy_value(self):
        from app.api.routes.toolsets import _has_oauth_credentials
        config = {"customField": 42}
        assert _has_oauth_credentials(config) is True

    def test_boolean_true_value(self):
        from app.api.routes.toolsets import _has_oauth_credentials
        config = {"isEnabled": True}
        assert _has_oauth_credentials(config) is True

    def test_mixed_infra_and_credentials(self):
        from app.api.routes.toolsets import _has_oauth_credentials
        config = {
            "type": "OAUTH",
            "redirectUri": "http://localhost",
            "clientId": "my-client",
        }
        assert _has_oauth_credentials(config) is True


# ---------------------------------------------------------------------------
# get_oauth_credentials_for_toolset
# ---------------------------------------------------------------------------

class TestGetOauthCredentialsForToolset:
    @pytest.mark.asyncio
    async def test_empty_config_raises(self):
        from app.api.routes.toolsets import get_oauth_credentials_for_toolset
        with pytest.raises(ValueError, match="required"):
            await get_oauth_credentials_for_toolset({}, AsyncMock())

    @pytest.mark.asyncio
    async def test_none_config_raises(self):
        from app.api.routes.toolsets import get_oauth_credentials_for_toolset
        with pytest.raises(ValueError, match="required"):
            await get_oauth_credentials_for_toolset(None, AsyncMock())

    @pytest.mark.asyncio
    async def test_legacy_auth_credentials_returned(self):
        """When auth config already has clientId/clientSecret, return as-is."""
        from app.api.routes.toolsets import get_oauth_credentials_for_toolset
        config = {
            "auth": {
                "clientId": "legacy-id",
                "clientSecret": "legacy-secret",
                "tenantId": "tenant-1",
            },
            "toolsetType": "jira",
        }
        result = await get_oauth_credentials_for_toolset(config, AsyncMock())
        assert result["clientId"] == "legacy-id"
        assert result["clientSecret"] == "legacy-secret"
        assert result["tenantId"] == "tenant-1"

    @pytest.mark.asyncio
    async def test_legacy_with_client_id_alt_name(self):
        """Handles client_id (underscore style) in auth config."""
        from app.api.routes.toolsets import get_oauth_credentials_for_toolset
        config = {
            "auth": {
                "client_id": "alt-id",
                "client_secret": "alt-secret",
            },
            "toolsetType": "slack",
        }
        result = await get_oauth_credentials_for_toolset(config, AsyncMock())
        assert result["client_id"] == "alt-id"

    @pytest.mark.asyncio
    async def test_missing_toolset_type_raises(self):
        from app.api.routes.toolsets import get_oauth_credentials_for_toolset
        config = {"auth": {}, "oauthConfigId": "cfg-1"}
        with pytest.raises(ValueError, match="Toolset type not found"):
            await get_oauth_credentials_for_toolset(config, AsyncMock())

    @pytest.mark.asyncio
    async def test_missing_oauth_config_id_fetches_from_instance(self):
        """When oauthConfigId missing but instanceId present, fetches from instance."""
        from app.api.routes.toolsets import get_oauth_credentials_for_toolset

        config_service = AsyncMock()

        async def mock_get_config(path, default=None, use_cache=True):
            if path == "/services/toolset-instances":
                return [
                    {"_id": "inst-1", "oauthConfigId": "oauth-cfg-1"},
                ]
            elif "oauths/toolsets" in path:
                return [
                    {
                        "_id": "oauth-cfg-1",
                        "config": {"clientId": "fetched-id", "clientSecret": "fetched-secret"},
                    },
                ]
            return default

        config_service.get_config = mock_get_config

        toolset_config = {
            "toolsetType": "google",
            "instanceId": "inst-1",
            "auth": {},
        }
        result = await get_oauth_credentials_for_toolset(toolset_config, config_service)
        assert result["clientId"] == "fetched-id"

    @pytest.mark.asyncio
    async def test_no_oauth_config_id_and_no_instance_raises(self):
        from app.api.routes.toolsets import get_oauth_credentials_for_toolset

        config_service = AsyncMock()
        config_service.get_config = AsyncMock(return_value=[])

        toolset_config = {
            "toolsetType": "jira",
            "auth": {},
        }
        with pytest.raises(ValueError, match="No oauthConfigId"):
            await get_oauth_credentials_for_toolset(toolset_config, config_service)

    @pytest.mark.asyncio
    async def test_oauth_config_found_by_id(self):
        from app.api.routes.toolsets import get_oauth_credentials_for_toolset

        config_service = AsyncMock()

        async def mock_get_config(path, default=None, use_cache=True):
            if "oauths/toolsets" in path:
                return [
                    {
                        "_id": "cfg-1",
                        "config": {"clientId": "cid", "clientSecret": "cs", "tenantId": "t1"},
                    },
                    {
                        "_id": "cfg-2",
                        "config": {"clientId": "other", "clientSecret": "other"},
                    },
                ]
            return default

        config_service.get_config = mock_get_config

        toolset_config = {
            "toolsetType": "microsoft",
            "oauthConfigId": "cfg-1",
            "auth": {},
        }
        result = await get_oauth_credentials_for_toolset(toolset_config, config_service)
        assert result["clientId"] == "cid"
        assert result["tenantId"] == "t1"

    @pytest.mark.asyncio
    async def test_oauth_config_not_found_raises(self):
        from app.api.routes.toolsets import get_oauth_credentials_for_toolset

        config_service = AsyncMock()

        async def mock_get_config(path, default=None, use_cache=True):
            if "oauths/toolsets" in path:
                return [{"_id": "other-cfg", "config": {"clientId": "x", "clientSecret": "y"}}]
            return default

        config_service.get_config = mock_get_config

        toolset_config = {
            "toolsetType": "jira",
            "oauthConfigId": "missing-cfg",
            "auth": {},
        }
        with pytest.raises(ValueError, match="not found"):
            await get_oauth_credentials_for_toolset(toolset_config, config_service)

    @pytest.mark.asyncio
    async def test_invalid_format_raises(self):
        from app.api.routes.toolsets import get_oauth_credentials_for_toolset

        config_service = AsyncMock()
        config_service.get_config = AsyncMock(return_value="not a list")

        toolset_config = {
            "toolsetType": "jira",
            "oauthConfigId": "cfg-1",
            "auth": {},
        }
        with pytest.raises(ValueError, match="Invalid OAuth config format"):
            await get_oauth_credentials_for_toolset(toolset_config, config_service)

    @pytest.mark.asyncio
    async def test_empty_config_data_raises(self):
        from app.api.routes.toolsets import get_oauth_credentials_for_toolset

        config_service = AsyncMock()

        async def mock_get_config(path, default=None, use_cache=True):
            if "oauths/toolsets" in path:
                return [{"_id": "cfg-1", "config": {}}]
            return default

        config_service.get_config = mock_get_config

        toolset_config = {
            "toolsetType": "jira",
            "oauthConfigId": "cfg-1",
            "auth": {},
        }
        with pytest.raises(ValueError, match="invalid or empty config"):
            await get_oauth_credentials_for_toolset(toolset_config, config_service)

    @pytest.mark.asyncio
    async def test_missing_client_secret_raises(self):
        from app.api.routes.toolsets import get_oauth_credentials_for_toolset

        config_service = AsyncMock()

        async def mock_get_config(path, default=None, use_cache=True):
            if "oauths/toolsets" in path:
                return [{"_id": "cfg-1", "config": {"clientId": "cid"}}]
            return default

        config_service.get_config = mock_get_config

        toolset_config = {
            "toolsetType": "jira",
            "oauthConfigId": "cfg-1",
            "auth": {},
        }
        with pytest.raises(ValueError, match="missing clientId or clientSecret"):
            await get_oauth_credentials_for_toolset(toolset_config, config_service)

    @pytest.mark.asyncio
    async def test_general_exception_wraps(self):
        from app.api.routes.toolsets import get_oauth_credentials_for_toolset

        config_service = AsyncMock()
        config_service.get_config = AsyncMock(side_effect=RuntimeError("connection failed"))

        toolset_config = {
            "toolsetType": "jira",
            "oauthConfigId": "cfg-1",
            "auth": {},
        }
        with pytest.raises(ValueError, match="Failed to retrieve OAuth credentials"):
            await get_oauth_credentials_for_toolset(toolset_config, config_service)


# ---------------------------------------------------------------------------
# get_toolset_by_id
# ---------------------------------------------------------------------------

class TestGetToolsetById:
    @pytest.mark.asyncio
    async def test_found(self):
        from app.api.routes.toolsets import get_toolset_by_id
        cs = AsyncMock()
        cs.get_config = AsyncMock(return_value=[
            {"_id": "inst-1", "name": "Jira"},
            {"_id": "inst-2", "name": "Slack"},
        ])
        result = await get_toolset_by_id("inst-1", cs)
        assert result["name"] == "Jira"

    @pytest.mark.asyncio
    async def test_not_found(self):
        from app.api.routes.toolsets import get_toolset_by_id
        cs = AsyncMock()
        cs.get_config = AsyncMock(return_value=[
            {"_id": "inst-1", "name": "Jira"},
        ])
        result = await get_toolset_by_id("nonexistent", cs)
        assert result is None

    @pytest.mark.asyncio
    async def test_empty_instances(self):
        from app.api.routes.toolsets import get_toolset_by_id
        cs = AsyncMock()
        cs.get_config = AsyncMock(return_value=[])
        result = await get_toolset_by_id("inst-1", cs)
        assert result is None

    @pytest.mark.asyncio
    async def test_non_list_returns_none(self):
        from app.api.routes.toolsets import get_toolset_by_id
        cs = AsyncMock()
        cs.get_config = AsyncMock(return_value="not a list")
        result = await get_toolset_by_id("inst-1", cs)
        assert result is None

    @pytest.mark.asyncio
    async def test_exception_returns_none(self):
        from app.api.routes.toolsets import get_toolset_by_id
        cs = AsyncMock()
        cs.get_config = AsyncMock(side_effect=Exception("fail"))
        result = await get_toolset_by_id("inst-1", cs)
        assert result is None


# ---------------------------------------------------------------------------
# _check_user_is_admin
# ---------------------------------------------------------------------------

class TestCheckUserIsAdmin:
    @pytest.mark.asyncio
    @patch("app.api.routes.toolsets.httpx.AsyncClient")
    async def test_admin_returns_true(self, mock_client_cls):
        from app.api.routes.toolsets import _check_user_is_admin

        mock_response = MagicMock()
        mock_response.status_code = 200

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client_cls.return_value = mock_client

        request = MagicMock()
        request.headers = {"authorization": "Bearer token123", "cookie": "session=abc"}

        config_service = AsyncMock()
        config_service.get_config = AsyncMock(return_value={
            "nodejs": {"endpoint": "http://nodejs:3001"},
        })

        result = await _check_user_is_admin("user-1", request, config_service)
        assert result is True

    @pytest.mark.asyncio
    @patch("app.api.routes.toolsets.httpx.AsyncClient")
    async def test_non_admin_returns_false(self, mock_client_cls):
        from app.api.routes.toolsets import _check_user_is_admin

        mock_response = MagicMock()
        mock_response.status_code = 403

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client_cls.return_value = mock_client

        request = MagicMock()
        request.headers = {"authorization": "Bearer token123"}

        config_service = AsyncMock()
        config_service.get_config = AsyncMock(return_value={
            "nodejs": {"endpoint": "http://nodejs:3001"},
        })

        result = await _check_user_is_admin("user-1", request, config_service)
        assert result is False

    @pytest.mark.asyncio
    @patch("app.api.routes.toolsets.httpx.AsyncClient")
    async def test_fallback_endpoint_on_config_error(self, mock_client_cls):
        from app.api.routes.toolsets import _check_user_is_admin

        mock_response = MagicMock()
        mock_response.status_code = 200

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client_cls.return_value = mock_client

        request = MagicMock()
        request.headers = {}

        config_service = AsyncMock()
        config_service.get_config = AsyncMock(side_effect=Exception("etcd down"))

        result = await _check_user_is_admin("user-1", request, config_service)
        assert result is True
        # Verify the fallback URL is used
        call_args = mock_client.get.call_args
        assert "user-1/adminCheck" in call_args[0][0]

    @pytest.mark.asyncio
    @patch("app.api.routes.toolsets.httpx.AsyncClient")
    async def test_exception_returns_false(self, mock_client_cls):
        from app.api.routes.toolsets import _check_user_is_admin

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(side_effect=Exception("network error"))
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client_cls.return_value = mock_client

        request = MagicMock()
        request.headers = {}

        config_service = AsyncMock()
        config_service.get_config = AsyncMock(return_value={})

        result = await _check_user_is_admin("user-1", request, config_service)
        assert result is False

    @pytest.mark.asyncio
    @patch("app.api.routes.toolsets.httpx.AsyncClient")
    async def test_forwards_auth_headers(self, mock_client_cls):
        from app.api.routes.toolsets import _check_user_is_admin

        mock_response = MagicMock()
        mock_response.status_code = 200

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client_cls.return_value = mock_client

        request = MagicMock()
        request.headers = {
            "authorization": "Bearer abc",
            "x-organization-id": "org-1",
            "cookie": "session=xyz",
            "content-type": "application/json",  # should not be forwarded
        }

        config_service = AsyncMock()
        config_service.get_config = AsyncMock(return_value={"nodejs": {"endpoint": "http://test:3001"}})

        await _check_user_is_admin("user-1", request, config_service)

        call_kwargs = mock_client.get.call_args
        forwarded = call_kwargs[1]["headers"]
        assert forwarded["authorization"] == "Bearer abc"
        assert forwarded["x-organization-id"] == "org-1"
        assert forwarded["cookie"] == "session=xyz"
        assert "content-type" not in forwarded


# ---------------------------------------------------------------------------
# _get_user_context
# ---------------------------------------------------------------------------

class TestGetUserContext:
    def test_valid_from_state(self):
        from app.api.routes.toolsets import _get_user_context
        request = MagicMock()
        request.state.user = {"userId": "u1", "orgId": "o1"}
        request.headers = {}
        ctx = _get_user_context(request)
        assert ctx["user_id"] == "u1"
        assert ctx["org_id"] == "o1"

    def test_fallback_to_headers(self):
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


# ---------------------------------------------------------------------------
# _get_registry
# ---------------------------------------------------------------------------

class TestGetRegistry:
    def test_returns_registry(self):
        from app.api.routes.toolsets import _get_registry
        mock_registry = MagicMock()
        request = MagicMock()
        request.app.state.toolset_registry = mock_registry
        result = _get_registry(request)
        assert result is mock_registry

    def test_missing_raises(self):
        from app.api.routes.toolsets import _get_registry
        request = MagicMock()
        request.app.state.toolset_registry = None
        with pytest.raises(HTTPException) as exc:
            _get_registry(request)
        assert exc.value.status_code == 500


# ---------------------------------------------------------------------------
# _get_graph_provider
# ---------------------------------------------------------------------------

class TestGetGraphProvider:
    def test_returns_provider(self):
        from app.api.routes.toolsets import _get_graph_provider
        mock_gp = MagicMock()
        request = MagicMock()
        request.app.state.graph_provider = mock_gp
        result = _get_graph_provider(request)
        assert result is mock_gp

    def test_missing_raises(self):
        from app.api.routes.toolsets import _get_graph_provider
        request = MagicMock()
        request.app.state.graph_provider = None
        with pytest.raises(HTTPException) as exc:
            _get_graph_provider(request)
        assert exc.value.status_code == 500


# ---------------------------------------------------------------------------
# _get_toolset_metadata
# ---------------------------------------------------------------------------

class TestGetToolsetMetadata:
    def test_valid_toolset(self):
        from app.api.routes.toolsets import _get_toolset_metadata
        registry = MagicMock()
        registry.get_toolset_metadata.return_value = {
            "display_name": "Jira",
            "isInternal": False,
        }
        result = _get_toolset_metadata(registry, "jira")
        assert result["display_name"] == "Jira"

    def test_empty_type_raises(self):
        from app.api.routes.toolsets import _get_toolset_metadata
        registry = MagicMock()
        with pytest.raises(HTTPException) as exc:
            _get_toolset_metadata(registry, "")
        assert exc.value.status_code == 400

    def test_whitespace_type_raises(self):
        from app.api.routes.toolsets import _get_toolset_metadata
        registry = MagicMock()
        with pytest.raises(HTTPException):
            _get_toolset_metadata(registry, "   ")

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


# ---------------------------------------------------------------------------
# Storage path helpers
# ---------------------------------------------------------------------------

class TestStoragePathHelpers:
    def test_get_instances_path(self):
        from app.api.routes.toolsets import _get_instances_path
        path = _get_instances_path("org-1")
        assert path == "/services/toolset-instances"

    def test_get_user_auth_path(self):
        from app.api.routes.toolsets import _get_user_auth_path
        path = _get_user_auth_path("inst-1", "user-1")
        assert path == "/services/toolsets/inst-1/user-1"

    def test_get_instance_users_prefix(self):
        from app.api.routes.toolsets import _get_instance_users_prefix
        prefix = _get_instance_users_prefix("inst-1")
        assert prefix == "/services/toolsets/inst-1/"

    def test_get_toolset_oauth_config_path(self):
        from app.api.routes.toolsets import _get_toolset_oauth_config_path
        path = _get_toolset_oauth_config_path("JIRA")
        assert path == "/services/oauths/toolsets/jira"

    def test_generate_instance_id(self):
        from app.api.routes.toolsets import _generate_instance_id
        result = _generate_instance_id()
        # Should be a valid UUID string
        uuid.UUID(result)

    def test_generate_oauth_config_id(self):
        from app.api.routes.toolsets import _generate_oauth_config_id
        result = _generate_oauth_config_id()
        uuid.UUID(result)


# ---------------------------------------------------------------------------
# _apply_tenant_to_microsoft_oauth_url
# ---------------------------------------------------------------------------

class TestApplyTenantToMicrosoftOAuthUrl:
    def test_replaces_tenant(self):
        from app.api.routes.toolsets import _apply_tenant_to_microsoft_oauth_url
        url = "https://login.microsoftonline.com/common/oauth2/v2.0/authorize"
        result = _apply_tenant_to_microsoft_oauth_url(url, "my-tenant")
        assert "my-tenant" in result
        assert "common" not in result

    def test_no_op_for_empty_tenant(self):
        from app.api.routes.toolsets import _apply_tenant_to_microsoft_oauth_url
        url = "https://login.microsoftonline.com/common/oauth2/v2.0/authorize"
        result = _apply_tenant_to_microsoft_oauth_url(url, "")
        assert result == url

    def test_no_op_for_none_tenant(self):
        from app.api.routes.toolsets import _apply_tenant_to_microsoft_oauth_url
        url = "https://login.microsoftonline.com/common/oauth2/v2.0/authorize"
        result = _apply_tenant_to_microsoft_oauth_url(url, None)
        assert result == url

    def test_no_op_for_common_tenant(self):
        from app.api.routes.toolsets import _apply_tenant_to_microsoft_oauth_url
        url = "https://login.microsoftonline.com/organizations/oauth2/v2.0/token"
        result = _apply_tenant_to_microsoft_oauth_url(url, "common")
        assert result == url

    def test_no_op_for_non_microsoft_url(self):
        from app.api.routes.toolsets import _apply_tenant_to_microsoft_oauth_url
        url = "https://accounts.google.com/o/oauth2/v2/auth"
        result = _apply_tenant_to_microsoft_oauth_url(url, "my-tenant")
        assert result == url

    def test_no_op_for_empty_url(self):
        from app.api.routes.toolsets import _apply_tenant_to_microsoft_oauth_url
        result = _apply_tenant_to_microsoft_oauth_url("", "my-tenant")
        assert result == ""

    def test_no_op_for_none_url(self):
        from app.api.routes.toolsets import _apply_tenant_to_microsoft_oauth_url
        result = _apply_tenant_to_microsoft_oauth_url(None, "my-tenant")
        assert result is None

    def test_token_url(self):
        from app.api.routes.toolsets import _apply_tenant_to_microsoft_oauth_url
        url = "https://login.microsoftonline.com/common/oauth2/v2.0/token"
        result = _apply_tenant_to_microsoft_oauth_url(url, "abc-123")
        assert "/abc-123/" in result


# ---------------------------------------------------------------------------
# _format_toolset_data
# ---------------------------------------------------------------------------

class TestFormatToolsetData:
    def test_without_tools(self):
        from app.api.routes.toolsets import _format_toolset_data
        metadata = {
            "display_name": "Jira",
            "description": "Jira integration",
            "category": "project_management",
            "group": "atlassian",
            "icon_path": "/icons/jira.svg",
            "supported_auth_types": ["OAUTH"],
            "tools": [{"name": "search"}, {"name": "create_issue"}],
        }
        result = _format_toolset_data("jira", metadata)
        assert result["name"] == "jira"
        assert result["displayName"] == "Jira"
        assert result["toolCount"] == 2
        assert "tools" not in result

    def test_with_tools(self):
        from app.api.routes.toolsets import _format_toolset_data
        metadata = {
            "tools": [
                {"name": "search", "description": "Search issues", "parameters": [], "returns": "list", "tags": ["query"]},
            ],
        }
        result = _format_toolset_data("jira", metadata, include_tools=True)
        assert len(result["tools"]) == 1
        assert result["tools"][0]["fullName"] == "jira.search"
        assert result["tools"][0]["displayName"] == "Search"

    def test_empty_metadata(self):
        from app.api.routes.toolsets import _format_toolset_data
        result = _format_toolset_data("test", {})
        assert result["name"] == "test"
        assert result["toolCount"] == 0


# ---------------------------------------------------------------------------
# _parse_request_json
# ---------------------------------------------------------------------------

class TestParseRequestJson:
    def test_valid_json(self):
        from app.api.routes.toolsets import _parse_request_json
        data = json.dumps({"key": "value"}).encode()
        result = _parse_request_json(MagicMock(), data)
        assert result["key"] == "value"

    def test_empty_body_raises(self):
        from app.api.routes.toolsets import _parse_request_json
        with pytest.raises(HTTPException) as exc:
            _parse_request_json(MagicMock(), b"")
        assert exc.value.status_code == 400

    def test_none_body_raises(self):
        from app.api.routes.toolsets import _parse_request_json
        with pytest.raises(HTTPException):
            _parse_request_json(MagicMock(), None)

    def test_invalid_json_raises(self):
        from app.api.routes.toolsets import _parse_request_json
        with pytest.raises(HTTPException) as exc:
            _parse_request_json(MagicMock(), b"not json{")
        assert exc.value.status_code == 400
        assert "Invalid JSON" in exc.value.detail


# ---------------------------------------------------------------------------
# _load_toolset_instances
# ---------------------------------------------------------------------------

class TestLoadToolsetInstances:
    @pytest.mark.asyncio
    async def test_loads_instances(self):
        from app.api.routes.toolsets import _load_toolset_instances
        cs = AsyncMock()
        cs.get_config = AsyncMock(return_value=[{"_id": "1"}, {"_id": "2"}])
        result = await _load_toolset_instances("org-1", cs)
        assert len(result) == 2

    @pytest.mark.asyncio
    async def test_empty_returns_empty(self):
        from app.api.routes.toolsets import _load_toolset_instances
        cs = AsyncMock()
        cs.get_config = AsyncMock(return_value=[])
        result = await _load_toolset_instances("org-1", cs)
        assert result == []

    @pytest.mark.asyncio
    async def test_exception_raises_http_error(self):
        from app.api.routes.toolsets import _load_toolset_instances
        cs = AsyncMock()
        cs.get_config = AsyncMock(side_effect=RuntimeError("etcd down"))
        with pytest.raises(HTTPException) as exc:
            await _load_toolset_instances("org-1", cs)
        assert exc.value.status_code == 500


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

class TestConstants:
    def test_oauth_infrastructure_fields_is_frozenset(self):
        from app.api.routes.toolsets import OAUTH_INFRASTRUCTURE_FIELDS
        assert isinstance(OAUTH_INFRASTRUCTURE_FIELDS, frozenset)
        assert "redirectUri" in OAUTH_INFRASTRUCTURE_FIELDS
        assert "clientId" not in OAUTH_INFRASTRUCTURE_FIELDS

    def test_default_constants(self):
        from app.api.routes.toolsets import (
            DEFAULT_BASE_URL,
            DEFAULT_ENDPOINTS_PATH,
            DEFAULT_TOOLSET_INSTANCES_PATH,
            MAX_AGENT_NAMES_DISPLAY,
            SPLIT_PATH_EXPECTED_PARTS,
            ENCRYPTED_KEY_PARTS_COUNT,
        )
        assert DEFAULT_BASE_URL == "http://localhost:3001"
        assert DEFAULT_ENDPOINTS_PATH == "/services/endpoints"
        assert DEFAULT_TOOLSET_INSTANCES_PATH == "/services/toolset-instances"
        assert MAX_AGENT_NAMES_DISPLAY == 3
        assert SPLIT_PATH_EXPECTED_PARTS == 2
        assert ENCRYPTED_KEY_PARTS_COUNT == 2


# ---------------------------------------------------------------------------
# _format_toolset_data — deeper coverage
# ---------------------------------------------------------------------------

class TestFormatToolsetDataDeep:
    """Deeper tests for _format_toolset_data with full metadata, OAuth URLs, tool schemas."""

    def test_all_metadata_fields_present(self):
        from app.api.routes.toolsets import _format_toolset_data
        metadata = {
            "display_name": "Microsoft Graph",
            "description": "MS Graph integration",
            "category": "productivity",
            "group": "microsoft",
            "icon_path": "/icons/ms-graph.svg",
            "supported_auth_types": ["OAUTH", "API_KEY"],
            "tools": [
                {"name": "get_files", "description": "Get files", "parameters": [{"name": "path", "type": "string"}], "returns": "list", "tags": ["read", "files"]},
                {"name": "send_email", "description": "Send email", "parameters": [{"name": "to"}, {"name": "body"}], "returns": "dict", "tags": ["write", "email"]},
            ],
        }
        result = _format_toolset_data("msgraph", metadata)
        assert result["name"] == "msgraph"
        assert result["displayName"] == "Microsoft Graph"
        assert result["description"] == "MS Graph integration"
        assert result["category"] == "productivity"
        assert result["group"] == "microsoft"
        assert result["iconPath"] == "/icons/ms-graph.svg"
        assert result["supportedAuthTypes"] == ["OAUTH", "API_KEY"]
        assert result["toolCount"] == 2
        assert "tools" not in result

    def test_include_tools_with_full_schema(self):
        from app.api.routes.toolsets import _format_toolset_data
        metadata = {
            "tools": [
                {
                    "name": "create_issue",
                    "description": "Create a Jira issue",
                    "parameters": [
                        {"name": "project", "type": "string", "required": True},
                        {"name": "summary", "type": "string", "required": True},
                    ],
                    "returns": "dict",
                    "tags": ["write", "issues"],
                },
            ],
        }
        result = _format_toolset_data("jira", metadata, include_tools=True)
        tools = result["tools"]
        assert len(tools) == 1
        tool = tools[0]
        assert tool["name"] == "create_issue"
        assert tool["fullName"] == "jira.create_issue"
        assert tool["displayName"] == "Create Issue"
        assert tool["description"] == "Create a Jira issue"
        assert len(tool["parameters"]) == 2
        assert tool["returns"] == "dict"
        assert tool["tags"] == ["write", "issues"]

    def test_tool_display_name_from_underscored_name(self):
        from app.api.routes.toolsets import _format_toolset_data
        metadata = {
            "tools": [
                {"name": "get_all_user_files"},
            ],
        }
        result = _format_toolset_data("drive", metadata, include_tools=True)
        assert result["tools"][0]["displayName"] == "Get All User Files"

    def test_tool_missing_optional_fields_defaults(self):
        from app.api.routes.toolsets import _format_toolset_data
        metadata = {
            "tools": [
                {"name": "list_items"},
            ],
        }
        result = _format_toolset_data("app", metadata, include_tools=True)
        tool = result["tools"][0]
        assert tool["description"] == ""
        assert tool["parameters"] == []
        assert tool["returns"] is None
        assert tool["tags"] == []

    def test_metadata_defaults_for_missing_keys(self):
        from app.api.routes.toolsets import _format_toolset_data
        result = _format_toolset_data("unknown", {})
        assert result["displayName"] == "unknown"
        assert result["description"] == ""
        assert result["category"] == "app"
        assert result["group"] == ""
        assert result["iconPath"] == ""
        assert result["supportedAuthTypes"] == []
        assert result["toolCount"] == 0

    def test_many_tools_count(self):
        from app.api.routes.toolsets import _format_toolset_data
        metadata = {
            "tools": [{"name": f"tool_{i}"} for i in range(50)],
        }
        result = _format_toolset_data("big_toolset", metadata)
        assert result["toolCount"] == 50

    def test_include_tools_false_no_tools_key(self):
        from app.api.routes.toolsets import _format_toolset_data
        metadata = {"tools": [{"name": "a"}]}
        result = _format_toolset_data("x", metadata, include_tools=False)
        assert "tools" not in result
        assert result["toolCount"] == 1


# ---------------------------------------------------------------------------
# _load_toolset_instances — deeper coverage
# ---------------------------------------------------------------------------

class TestLoadToolsetInstancesDeep:
    @pytest.mark.asyncio
    async def test_returns_validated_list(self):
        from app.api.routes.toolsets import _load_toolset_instances
        cs = AsyncMock()
        items = [
            {"_id": "1", "toolsetType": "jira", "orgId": "org-1"},
            {"_id": "2", "toolsetType": "slack", "orgId": "org-1"},
            {"_id": "3", "toolsetType": "jira", "orgId": "org-1"},
        ]
        cs.get_config = AsyncMock(return_value=items)
        result = await _load_toolset_instances("org-1", cs)
        assert len(result) == 3
        assert result[0]["_id"] == "1"

    @pytest.mark.asyncio
    async def test_default_empty_list(self):
        from app.api.routes.toolsets import _load_toolset_instances
        cs = AsyncMock()
        cs.get_config = AsyncMock(return_value=None)
        # None with allow_empty=True returns []
        result = await _load_toolset_instances("org-1", cs)
        assert result == []

    @pytest.mark.asyncio
    async def test_non_list_raises_validation_error(self):
        from app.api.routes.toolsets import _load_toolset_instances
        cs = AsyncMock()
        cs.get_config = AsyncMock(return_value="not a list")
        with pytest.raises(HTTPException) as exc:
            await _load_toolset_instances("org-1", cs)
        assert exc.value.status_code == 400
        assert "must be a list" in exc.value.detail

    @pytest.mark.asyncio
    async def test_http_exception_propagated(self):
        """HTTPException from _validate_list should propagate, not be wrapped."""
        from app.api.routes.toolsets import _load_toolset_instances
        cs = AsyncMock()
        cs.get_config = AsyncMock(return_value={"key": "val"})
        with pytest.raises(HTTPException) as exc:
            await _load_toolset_instances("org-1", cs)
        # Should be the 400 from _validate_list, not wrapped 500
        assert exc.value.status_code == 400


# ---------------------------------------------------------------------------
# _apply_tenant_to_microsoft_oauth_url — deeper edge cases
# ---------------------------------------------------------------------------

class TestApplyTenantToMicrosoftOAuthUrlDeep:
    def test_whitespace_only_tenant(self):
        from app.api.routes.toolsets import _apply_tenant_to_microsoft_oauth_url
        url = "https://login.microsoftonline.com/common/oauth2/v2.0/authorize"
        result = _apply_tenant_to_microsoft_oauth_url(url, "   ")
        assert result == url

    def test_common_case_insensitive(self):
        from app.api.routes.toolsets import _apply_tenant_to_microsoft_oauth_url
        url = "https://login.microsoftonline.com/organizations/oauth2/v2.0/authorize"
        result = _apply_tenant_to_microsoft_oauth_url(url, "COMMON")
        assert result == url

    def test_tenant_with_guid_format(self):
        from app.api.routes.toolsets import _apply_tenant_to_microsoft_oauth_url
        url = "https://login.microsoftonline.com/common/oauth2/v2.0/token"
        tenant = "12345678-1234-1234-1234-123456789abc"
        result = _apply_tenant_to_microsoft_oauth_url(url, tenant)
        assert tenant in result
        assert "common" not in result

    def test_already_has_custom_tenant_replaced(self):
        from app.api.routes.toolsets import _apply_tenant_to_microsoft_oauth_url
        url = "https://login.microsoftonline.com/old-tenant/oauth2/v2.0/authorize"
        result = _apply_tenant_to_microsoft_oauth_url(url, "new-tenant")
        assert "new-tenant" in result
        assert "old-tenant" not in result

    def test_preserves_path_after_tenant(self):
        from app.api.routes.toolsets import _apply_tenant_to_microsoft_oauth_url
        url = "https://login.microsoftonline.com/common/oauth2/v2.0/authorize"
        result = _apply_tenant_to_microsoft_oauth_url(url, "my-tenant")
        assert result == "https://login.microsoftonline.com/my-tenant/oauth2/v2.0/authorize"

    def test_token_url_preserves_path(self):
        from app.api.routes.toolsets import _apply_tenant_to_microsoft_oauth_url
        url = "https://login.microsoftonline.com/common/oauth2/v2.0/token"
        result = _apply_tenant_to_microsoft_oauth_url(url, "my-tenant")
        assert result == "https://login.microsoftonline.com/my-tenant/oauth2/v2.0/token"

    def test_url_with_query_params(self):
        from app.api.routes.toolsets import _apply_tenant_to_microsoft_oauth_url
        url = "https://login.microsoftonline.com/common/oauth2/v2.0/authorize?response_type=code"
        result = _apply_tenant_to_microsoft_oauth_url(url, "my-tenant")
        assert "my-tenant" in result
        assert "response_type=code" in result

    def test_organizations_tenant_no_op(self):
        """The regex just replaces the tenant segment; 'organizations' is not treated specially."""
        from app.api.routes.toolsets import _apply_tenant_to_microsoft_oauth_url
        url = "https://login.microsoftonline.com/organizations/oauth2/v2.0/token"
        # "organizations" is not == "common" so it WILL be replaced
        result = _apply_tenant_to_microsoft_oauth_url(url, "my-tenant")
        assert "my-tenant" in result


# ---------------------------------------------------------------------------
# _check_instance_name_conflict
# ---------------------------------------------------------------------------

class TestCheckInstanceNameConflict:
    def test_no_conflict_empty_list(self):
        from app.api.routes.toolsets import _check_instance_name_conflict
        assert _check_instance_name_conflict([], "My Jira", "org-1", "jira") is False

    def test_conflict_same_name_same_org_same_type(self):
        from app.api.routes.toolsets import _check_instance_name_conflict
        instances = [
            {"_id": "1", "orgId": "org-1", "toolsetType": "jira", "instanceName": "My Jira"},
        ]
        assert _check_instance_name_conflict(instances, "My Jira", "org-1", "jira") is True

    def test_case_insensitive_conflict(self):
        from app.api.routes.toolsets import _check_instance_name_conflict
        instances = [
            {"_id": "1", "orgId": "org-1", "toolsetType": "jira", "instanceName": "MY JIRA"},
        ]
        assert _check_instance_name_conflict(instances, "my jira", "org-1", "jira") is True

    def test_no_conflict_different_org(self):
        from app.api.routes.toolsets import _check_instance_name_conflict
        instances = [
            {"_id": "1", "orgId": "org-2", "toolsetType": "jira", "instanceName": "My Jira"},
        ]
        assert _check_instance_name_conflict(instances, "My Jira", "org-1", "jira") is False

    def test_no_conflict_different_toolset_type(self):
        from app.api.routes.toolsets import _check_instance_name_conflict
        instances = [
            {"_id": "1", "orgId": "org-1", "toolsetType": "slack", "instanceName": "My Jira"},
        ]
        assert _check_instance_name_conflict(instances, "My Jira", "org-1", "jira") is False

    def test_exclude_id_skips_self(self):
        from app.api.routes.toolsets import _check_instance_name_conflict
        instances = [
            {"_id": "1", "orgId": "org-1", "toolsetType": "jira", "instanceName": "My Jira"},
        ]
        assert _check_instance_name_conflict(instances, "My Jira", "org-1", "jira", exclude_id="1") is False

    def test_exclude_id_does_not_skip_others(self):
        from app.api.routes.toolsets import _check_instance_name_conflict
        instances = [
            {"_id": "1", "orgId": "org-1", "toolsetType": "jira", "instanceName": "My Jira"},
            {"_id": "2", "orgId": "org-1", "toolsetType": "jira", "instanceName": "My Jira"},
        ]
        # exclude_id="1" skips first but second still conflicts
        assert _check_instance_name_conflict(instances, "My Jira", "org-1", "jira", exclude_id="1") is True

    def test_missing_instance_name_no_conflict(self):
        from app.api.routes.toolsets import _check_instance_name_conflict
        instances = [
            {"_id": "1", "orgId": "org-1", "toolsetType": "jira"},
        ]
        assert _check_instance_name_conflict(instances, "My Jira", "org-1", "jira") is False


# ---------------------------------------------------------------------------
# _check_oauth_name_conflict
# ---------------------------------------------------------------------------

class TestCheckOAuthNameConflict:
    def test_no_conflict_empty(self):
        from app.api.routes.toolsets import _check_oauth_name_conflict
        assert _check_oauth_name_conflict([], "Config A", "org-1") is False

    def test_conflict_same_name_same_org(self):
        from app.api.routes.toolsets import _check_oauth_name_conflict
        configs = [
            {"_id": "1", "orgId": "org-1", "oauthInstanceName": "Config A"},
        ]
        assert _check_oauth_name_conflict(configs, "Config A", "org-1") is True

    def test_case_insensitive(self):
        from app.api.routes.toolsets import _check_oauth_name_conflict
        configs = [
            {"_id": "1", "orgId": "org-1", "oauthInstanceName": "CONFIG A"},
        ]
        assert _check_oauth_name_conflict(configs, "config a", "org-1") is True

    def test_no_conflict_different_org(self):
        from app.api.routes.toolsets import _check_oauth_name_conflict
        configs = [
            {"_id": "1", "orgId": "org-2", "oauthInstanceName": "Config A"},
        ]
        assert _check_oauth_name_conflict(configs, "Config A", "org-1") is False

    def test_exclude_id_skips_self(self):
        from app.api.routes.toolsets import _check_oauth_name_conflict
        configs = [
            {"_id": "1", "orgId": "org-1", "oauthInstanceName": "Config A"},
        ]
        assert _check_oauth_name_conflict(configs, "Config A", "org-1", exclude_id="1") is False


# ---------------------------------------------------------------------------
# _encode_state_with_instance / _decode_state_with_instance
# ---------------------------------------------------------------------------

class TestEncodeDecodeStateWithInstance:
    def test_roundtrip(self):
        from app.api.routes.toolsets import (
            _decode_state_with_instance,
            _encode_state_with_instance,
        )
        encoded = _encode_state_with_instance("csrf_token_abc", "inst-42", "user-99")
        decoded = _decode_state_with_instance(encoded)
        assert decoded["state"] == "csrf_token_abc"
        assert decoded["instance_id"] == "inst-42"
        assert decoded["user_id"] == "user-99"

    def test_encode_is_base64_url_safe(self):
        from app.api.routes.toolsets import _encode_state_with_instance
        import base64
        encoded = _encode_state_with_instance("state", "inst", "user")
        # Should be decodable as urlsafe base64
        decoded_bytes = base64.urlsafe_b64decode(encoded.encode())
        assert b"state" in decoded_bytes

    def test_decode_invalid_base64_raises(self):
        from app.api.routes.toolsets import OAuthConfigError, _decode_state_with_instance
        with pytest.raises(OAuthConfigError):
            _decode_state_with_instance("not!valid!base64!!!")

    def test_decode_valid_base64_but_not_json_raises(self):
        import base64
        from app.api.routes.toolsets import OAuthConfigError, _decode_state_with_instance
        encoded = base64.urlsafe_b64encode(b"not json").decode()
        with pytest.raises(OAuthConfigError, match="not valid JSON"):
            _decode_state_with_instance(encoded)

    def test_decode_json_missing_fields_raises(self):
        import base64
        from app.api.routes.toolsets import OAuthConfigError, _decode_state_with_instance
        # Missing user_id
        encoded = base64.urlsafe_b64encode(json.dumps({"state": "s", "instance_id": "i"}).encode()).decode()
        with pytest.raises(OAuthConfigError, match="Missing required fields"):
            _decode_state_with_instance(encoded)

    def test_decode_json_missing_state_raises(self):
        import base64
        from app.api.routes.toolsets import OAuthConfigError, _decode_state_with_instance
        encoded = base64.urlsafe_b64encode(json.dumps({"instance_id": "i", "user_id": "u"}).encode()).decode()
        with pytest.raises(OAuthConfigError, match="Missing required fields"):
            _decode_state_with_instance(encoded)

    def test_decode_json_missing_instance_id_raises(self):
        import base64
        from app.api.routes.toolsets import OAuthConfigError, _decode_state_with_instance
        encoded = base64.urlsafe_b64encode(json.dumps({"state": "s", "user_id": "u"}).encode()).decode()
        with pytest.raises(OAuthConfigError, match="Missing required fields"):
            _decode_state_with_instance(encoded)


# ---------------------------------------------------------------------------
# _get_oauth_configs_for_type
# ---------------------------------------------------------------------------

class TestGetOAuthConfigsForType:
    @pytest.mark.asyncio
    async def test_returns_list(self):
        from app.api.routes.toolsets import _get_oauth_configs_for_type
        cs = AsyncMock()
        cs.get_config = AsyncMock(return_value=[{"_id": "1"}, {"_id": "2"}])
        result = await _get_oauth_configs_for_type("jira", cs)
        assert len(result) == 2

    @pytest.mark.asyncio
    async def test_non_list_returns_empty(self):
        from app.api.routes.toolsets import _get_oauth_configs_for_type
        cs = AsyncMock()
        cs.get_config = AsyncMock(return_value="bad data")
        result = await _get_oauth_configs_for_type("jira", cs)
        assert result == []

    @pytest.mark.asyncio
    async def test_exception_returns_empty(self):
        from app.api.routes.toolsets import _get_oauth_configs_for_type
        cs = AsyncMock()
        cs.get_config = AsyncMock(side_effect=RuntimeError("etcd down"))
        result = await _get_oauth_configs_for_type("jira", cs)
        assert result == []

    @pytest.mark.asyncio
    async def test_default_empty_list(self):
        from app.api.routes.toolsets import _get_oauth_configs_for_type
        cs = AsyncMock()
        cs.get_config = AsyncMock(return_value=[])
        result = await _get_oauth_configs_for_type("jira", cs)
        assert result == []


# ---------------------------------------------------------------------------
# _get_oauth_config_by_id
# ---------------------------------------------------------------------------

class TestGetOAuthConfigById:
    @pytest.mark.asyncio
    async def test_found(self):
        from app.api.routes.toolsets import _get_oauth_config_by_id
        cs = AsyncMock()
        cs.get_config = AsyncMock(return_value=[
            {"_id": "cfg-1", "orgId": "org-1", "config": {"clientId": "x"}},
            {"_id": "cfg-2", "orgId": "org-1", "config": {"clientId": "y"}},
        ])
        result = await _get_oauth_config_by_id("jira", "cfg-2", "org-1", cs)
        assert result["config"]["clientId"] == "y"

    @pytest.mark.asyncio
    async def test_not_found_wrong_id(self):
        from app.api.routes.toolsets import _get_oauth_config_by_id
        cs = AsyncMock()
        cs.get_config = AsyncMock(return_value=[
            {"_id": "cfg-1", "orgId": "org-1"},
        ])
        result = await _get_oauth_config_by_id("jira", "cfg-999", "org-1", cs)
        assert result is None

    @pytest.mark.asyncio
    async def test_not_found_wrong_org(self):
        from app.api.routes.toolsets import _get_oauth_config_by_id
        cs = AsyncMock()
        cs.get_config = AsyncMock(return_value=[
            {"_id": "cfg-1", "orgId": "org-2"},
        ])
        result = await _get_oauth_config_by_id("jira", "cfg-1", "org-1", cs)
        assert result is None

    @pytest.mark.asyncio
    async def test_empty_list_returns_none(self):
        from app.api.routes.toolsets import _get_oauth_config_by_id
        cs = AsyncMock()
        cs.get_config = AsyncMock(return_value=[])
        result = await _get_oauth_config_by_id("jira", "cfg-1", "org-1", cs)
        assert result is None


# ---------------------------------------------------------------------------
# _get_oauth_config_from_registry
# ---------------------------------------------------------------------------

class TestGetOAuthConfigFromRegistry:
    def test_not_found_raises(self):
        from app.api.routes.toolsets import ToolsetNotFoundError, _get_oauth_config_from_registry
        registry = MagicMock()
        registry.get_toolset_metadata.return_value = None
        with pytest.raises(ToolsetNotFoundError):
            _get_oauth_config_from_registry("missing", registry)

    def test_no_oauth_config_raises(self):
        from app.api.routes.toolsets import OAuthConfigError, _get_oauth_config_from_registry
        registry = MagicMock()
        registry.get_toolset_metadata.return_value = {
            "config": {},
            "supported_auth_types": ["API_KEY"],
        }
        with pytest.raises(OAuthConfigError, match="does not support OAuth"):
            _get_oauth_config_from_registry("apikey_only", registry)

    def test_incomplete_oauth_config_raises(self):
        from app.api.routes.toolsets import OAuthConfigError, _get_oauth_config_from_registry
        registry = MagicMock()
        # Has _oauth_configs but the config object is missing attributes
        mock_oauth = MagicMock(spec=[])  # Empty spec = no attributes
        registry.get_toolset_metadata.return_value = {
            "config": {"_oauth_configs": {"OAUTH": mock_oauth}},
        }
        with pytest.raises(OAuthConfigError, match="incomplete OAuth configuration"):
            _get_oauth_config_from_registry("broken", registry)

    def test_valid_returns_oauth_config(self):
        from app.api.routes.toolsets import _get_oauth_config_from_registry
        registry = MagicMock()
        mock_oauth = MagicMock()
        mock_oauth.authorize_url = "https://auth.example.com/authorize"
        mock_oauth.token_url = "https://auth.example.com/token"
        registry.get_toolset_metadata.return_value = {
            "config": {"_oauth_configs": {"OAUTH": mock_oauth}},
        }
        result = _get_oauth_config_from_registry("toolset", registry)
        assert result is mock_oauth


# ---------------------------------------------------------------------------
# _build_oauth_config
# ---------------------------------------------------------------------------

class TestBuildOAuthConfig:
    @pytest.mark.asyncio
    async def test_missing_client_id_raises(self):
        from app.api.routes.toolsets import InvalidAuthConfigError, _build_oauth_config
        with pytest.raises(InvalidAuthConfigError, match="Client ID and Client Secret"):
            await _build_oauth_config(
                {"clientId": "", "clientSecret": "secret"}, "jira", MagicMock()
            )

    @pytest.mark.asyncio
    async def test_missing_client_secret_raises(self):
        from app.api.routes.toolsets import InvalidAuthConfigError, _build_oauth_config
        with pytest.raises(InvalidAuthConfigError, match="Client ID and Client Secret"):
            await _build_oauth_config(
                {"clientId": "id", "clientSecret": ""}, "jira", MagicMock()
            )

    @pytest.mark.asyncio
    async def test_valid_config_returned(self):
        from app.api.routes.toolsets import _build_oauth_config
        registry = MagicMock()
        mock_scopes = MagicMock()
        mock_scopes.get_scopes_for_type.return_value = ["read", "write"]
        mock_oauth = MagicMock()
        mock_oauth.authorize_url = "https://auth.example.com/authorize"
        mock_oauth.token_url = "https://auth.example.com/token"
        mock_oauth.redirect_uri = "/callback"
        mock_oauth.scopes = mock_scopes
        mock_oauth.additional_params = None
        mock_oauth.token_access_type = None
        mock_oauth.scope_parameter_name = "scope"
        mock_oauth.token_response_path = None
        registry.get_toolset_metadata.return_value = {
            "config": {"_oauth_configs": {"OAUTH": mock_oauth}},
        }
        result = await _build_oauth_config(
            {"clientId": "my-id", "clientSecret": "my-secret"},
            "test_toolset",
            registry,
            base_url="https://app.example.com",
        )
        assert result["clientId"] == "my-id"
        assert result["clientSecret"] == "my-secret"
        assert result["redirectUri"] == "https://app.example.com//callback"
        assert result["scopes"] == ["read", "write"]
        assert result["name"] == "test_toolset"

    @pytest.mark.asyncio
    async def test_tenant_id_included_in_config(self):
        from app.api.routes.toolsets import _build_oauth_config
        registry = MagicMock()
        mock_scopes = MagicMock()
        mock_scopes.get_scopes_for_type.return_value = []
        mock_oauth = MagicMock()
        mock_oauth.authorize_url = "https://login.microsoftonline.com/common/oauth2/v2.0/authorize"
        mock_oauth.token_url = "https://login.microsoftonline.com/common/oauth2/v2.0/token"
        mock_oauth.redirect_uri = "/callback"
        mock_oauth.scopes = mock_scopes
        mock_oauth.additional_params = None
        mock_oauth.token_access_type = None
        mock_oauth.scope_parameter_name = "scope"
        mock_oauth.token_response_path = None
        registry.get_toolset_metadata.return_value = {
            "config": {"_oauth_configs": {"OAUTH": mock_oauth}},
        }
        result = await _build_oauth_config(
            {"clientId": "id", "clientSecret": "secret", "tenantId": "my-tenant"},
            "msgraph",
            registry,
        )
        assert result["tenantId"] == "my-tenant"
        assert "my-tenant" in result["authorizeUrl"]
        assert "my-tenant" in result["tokenUrl"]


# ---------------------------------------------------------------------------
# _create_or_update_toolset_oauth_config
# ---------------------------------------------------------------------------

class TestCreateOrUpdateToolsetOAuthConfig:
    """Tests for _create_or_update_toolset_oauth_config()."""

    @pytest.mark.asyncio
    async def test_create_new_config(self):
        from app.api.routes.toolsets import _create_or_update_toolset_oauth_config

        config_service = AsyncMock()
        config_service.get_config = AsyncMock(return_value=[])
        config_service.set_config = AsyncMock()

        registry = MagicMock()
        mock_scopes = MagicMock()
        mock_scopes.get_scopes_for_type.return_value = ["read"]
        mock_oauth = MagicMock()
        mock_oauth.authorize_url = "https://auth.example.com/authorize"
        mock_oauth.token_url = "https://auth.example.com/token"
        mock_oauth.redirect_uri = "/cb"
        mock_oauth.scopes = mock_scopes
        mock_oauth.additional_params = None
        mock_oauth.token_access_type = None
        mock_oauth.scope_parameter_name = "scope"
        mock_oauth.token_response_path = None
        registry.get_toolset_metadata.return_value = {
            "config": {"_oauth_configs": {"OAUTH": mock_oauth}},
        }

        result_id = await _create_or_update_toolset_oauth_config(
            toolset_type="jira",
            auth_config={"type": "OAUTH", "clientId": "cid", "clientSecret": "cs"},
            instance_name="My Jira",
            user_id="user-1",
            org_id="org-1",
            config_service=config_service,
            registry=registry,
            base_url="https://app.example.com",
        )
        assert result_id is not None
        uuid.UUID(result_id)
        config_service.set_config.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_update_existing_config(self):
        from app.api.routes.toolsets import _create_or_update_toolset_oauth_config

        config_service = AsyncMock()
        existing_cfg = {
            "_id": "existing-cfg",
            "orgId": "org-1",
            "config": {"clientId": "old-id"},
            "updatedAtTimestamp": 100,
        }
        config_service.get_config = AsyncMock(return_value=[existing_cfg])
        config_service.set_config = AsyncMock()

        registry = MagicMock()
        mock_scopes = MagicMock()
        mock_scopes.get_scopes_for_type.return_value = []
        mock_oauth = MagicMock()
        mock_oauth.authorize_url = "https://auth.example.com/authorize"
        mock_oauth.token_url = "https://auth.example.com/token"
        mock_oauth.redirect_uri = "/cb"
        mock_oauth.scopes = mock_scopes
        mock_oauth.additional_params = None
        mock_oauth.token_access_type = None
        mock_oauth.scope_parameter_name = "scope"
        mock_oauth.token_response_path = None
        registry.get_toolset_metadata.return_value = {
            "config": {"_oauth_configs": {"OAUTH": mock_oauth}},
        }

        result_id = await _create_or_update_toolset_oauth_config(
            toolset_type="jira",
            auth_config={"type": "OAUTH", "clientId": "new-id", "clientSecret": "new-secret"},
            instance_name="Jira",
            user_id="user-1",
            org_id="org-1",
            config_service=config_service,
            registry=registry,
            base_url="https://app.example.com",
            oauth_config_id="existing-cfg",
        )
        assert result_id == "existing-cfg"
        config_service.set_config.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_update_not_found_falls_through_to_create(self):
        from app.api.routes.toolsets import _create_or_update_toolset_oauth_config

        config_service = AsyncMock()
        config_service.get_config = AsyncMock(return_value=[])
        config_service.set_config = AsyncMock()

        registry = MagicMock()
        mock_scopes = MagicMock()
        mock_scopes.get_scopes_for_type.return_value = []
        mock_oauth = MagicMock()
        mock_oauth.authorize_url = "https://auth.example.com/authorize"
        mock_oauth.token_url = "https://auth.example.com/token"
        mock_oauth.redirect_uri = "/cb"
        mock_oauth.scopes = mock_scopes
        mock_oauth.additional_params = None
        mock_oauth.token_access_type = None
        mock_oauth.scope_parameter_name = "scope"
        mock_oauth.token_response_path = None
        registry.get_toolset_metadata.return_value = {
            "config": {"_oauth_configs": {"OAUTH": mock_oauth}},
        }

        result_id = await _create_or_update_toolset_oauth_config(
            toolset_type="jira",
            auth_config={"type": "OAUTH", "clientId": "cid", "clientSecret": "cs"},
            instance_name="Jira",
            user_id="user-1",
            org_id="org-1",
            config_service=config_service,
            registry=registry,
            base_url="https://app.example.com",
            oauth_config_id="nonexistent-cfg",
        )
        # Falls through to create, so ID should be new
        assert result_id is not None
        assert result_id != "nonexistent-cfg"

    @pytest.mark.asyncio
    async def test_exception_returns_none(self):
        """When set_config fails during creation, returns None."""
        from app.api.routes.toolsets import _create_or_update_toolset_oauth_config

        config_service = AsyncMock()
        # get_config succeeds (returns empty list), but set_config fails
        config_service.get_config = AsyncMock(return_value=[])
        config_service.set_config = AsyncMock(side_effect=RuntimeError("etcd write failed"))

        registry = MagicMock()
        mock_scopes = MagicMock()
        mock_scopes.get_scopes_for_type.return_value = []
        mock_oauth = MagicMock()
        mock_oauth.authorize_url = "https://auth.example.com/authorize"
        mock_oauth.token_url = "https://auth.example.com/token"
        mock_oauth.redirect_uri = "/cb"
        mock_oauth.scopes = mock_scopes
        mock_oauth.additional_params = None
        mock_oauth.token_access_type = None
        mock_oauth.scope_parameter_name = "scope"
        mock_oauth.token_response_path = None
        registry.get_toolset_metadata.return_value = {
            "config": {"_oauth_configs": {"OAUTH": mock_oauth}},
        }

        result_id = await _create_or_update_toolset_oauth_config(
            toolset_type="jira",
            auth_config={"type": "OAUTH", "clientId": "cid", "clientSecret": "cs"},
            instance_name="Jira",
            user_id="user-1",
            org_id="org-1",
            config_service=config_service,
            registry=registry,
            base_url="",
        )
        assert result_id is None


# ---------------------------------------------------------------------------
# _prepare_toolset_auth_config
# ---------------------------------------------------------------------------

class TestPrepareToolsetAuthConfig:
    """Tests for _prepare_toolset_auth_config()."""

    @pytest.mark.asyncio
    async def test_non_oauth_type_returned_as_is(self):
        from app.api.routes.toolsets import _prepare_toolset_auth_config

        auth_config = {"type": "API_KEY", "apiKey": "my-key"}
        result = await _prepare_toolset_auth_config(
            auth_config, "jira", MagicMock(), AsyncMock()
        )
        assert result == auth_config

    @pytest.mark.asyncio
    async def test_oauth_type_enriched(self):
        from app.api.routes.toolsets import _prepare_toolset_auth_config

        registry = MagicMock()
        mock_scopes = MagicMock()
        mock_scopes.get_scopes_for_type.return_value = ["read", "write"]
        mock_oauth = MagicMock()
        mock_oauth.authorize_url = "https://auth.example.com/authorize"
        mock_oauth.token_url = "https://auth.example.com/token"
        mock_oauth.redirect_uri = "/callback"
        mock_oauth.scopes = mock_scopes
        mock_oauth.additional_params = None
        mock_oauth.token_access_type = None
        mock_oauth.scope_parameter_name = "scope"
        mock_oauth.token_response_path = None
        registry.get_toolset_metadata.return_value = {
            "config": {"_oauth_configs": {"OAUTH": mock_oauth}},
        }

        config_service = AsyncMock()
        auth_config = {"type": "OAUTH", "clientId": "cid"}
        result = await _prepare_toolset_auth_config(
            auth_config, "jira", registry, config_service, "https://app.example.com"
        )
        assert result["authorizeUrl"] == "https://auth.example.com/authorize"
        assert result["tokenUrl"] == "https://auth.example.com/token"
        assert result["scopes"] == ["read", "write"]
        assert "callback" in result["redirectUri"]

    @pytest.mark.asyncio
    async def test_oauth_with_tenant_substitution(self):
        from app.api.routes.toolsets import _prepare_toolset_auth_config

        registry = MagicMock()
        mock_scopes = MagicMock()
        mock_scopes.get_scopes_for_type.return_value = []
        mock_oauth = MagicMock()
        mock_oauth.authorize_url = "https://login.microsoftonline.com/common/oauth2/v2.0/authorize"
        mock_oauth.token_url = "https://login.microsoftonline.com/common/oauth2/v2.0/token"
        mock_oauth.redirect_uri = "/cb"
        mock_oauth.scopes = mock_scopes
        mock_oauth.additional_params = None
        mock_oauth.token_access_type = None
        mock_oauth.scope_parameter_name = "scope"
        mock_oauth.token_response_path = None
        registry.get_toolset_metadata.return_value = {
            "config": {"_oauth_configs": {"OAUTH": mock_oauth}},
        }

        config_service = AsyncMock()
        auth_config = {"type": "OAUTH", "tenantId": "my-tenant"}
        result = await _prepare_toolset_auth_config(
            auth_config, "msgraph", registry, config_service, "https://app.example.com"
        )
        assert "my-tenant" in result["authorizeUrl"]
        assert "my-tenant" in result["tokenUrl"]

    @pytest.mark.asyncio
    async def test_oauth_fallback_base_url_from_endpoints(self):
        from app.api.routes.toolsets import _prepare_toolset_auth_config

        registry = MagicMock()
        mock_scopes = MagicMock()
        mock_scopes.get_scopes_for_type.return_value = []
        mock_oauth = MagicMock()
        mock_oauth.authorize_url = "https://auth.example.com/authorize"
        mock_oauth.token_url = "https://auth.example.com/token"
        mock_oauth.redirect_uri = "/callback"
        mock_oauth.scopes = mock_scopes
        mock_oauth.additional_params = None
        mock_oauth.token_access_type = None
        mock_oauth.scope_parameter_name = "scope"
        mock_oauth.token_response_path = None
        registry.get_toolset_metadata.return_value = {
            "config": {"_oauth_configs": {"OAUTH": mock_oauth}},
        }

        config_service = AsyncMock()
        config_service.get_config = AsyncMock(return_value={
            "frontend": {"publicEndpoint": "https://frontend.example.com"}
        })

        auth_config = {"type": "OAUTH"}
        result = await _prepare_toolset_auth_config(
            auth_config, "jira", registry, config_service, base_url=None
        )
        assert "frontend.example.com" in result["redirectUri"]


# ---------------------------------------------------------------------------
# _deauth_all_instance_users
# ---------------------------------------------------------------------------

class TestDeauthAllInstanceUsers:
    """Tests for _deauth_all_instance_users()."""

    @pytest.mark.asyncio
    async def test_no_users_returns_zero(self):
        from app.api.routes.toolsets import _deauth_all_instance_users

        config_service = AsyncMock()
        config_service.list_keys_in_directory = AsyncMock(return_value=[])
        count = await _deauth_all_instance_users("inst-1", config_service)
        assert count == 0

    @pytest.mark.asyncio
    async def test_deauths_users(self):
        from app.api.routes.toolsets import _deauth_all_instance_users

        config_service = AsyncMock()
        config_service.list_keys_in_directory = AsyncMock(
            return_value=["/services/toolsets/inst-1/user-1", "/services/toolsets/inst-1/user-2"]
        )
        config_service.get_config = AsyncMock(return_value={"isAuthenticated": True})
        config_service.set_config = AsyncMock()

        count = await _deauth_all_instance_users("inst-1", config_service)
        assert count == 2
        # Verify set_config was called with isAuthenticated=False
        for call_args in config_service.set_config.call_args_list:
            auth_data = call_args[0][1]
            assert auth_data["isAuthenticated"] is False
            assert auth_data["deauthReason"] == "admin_oauth_config_updated"

    @pytest.mark.asyncio
    async def test_list_keys_error_returns_zero(self):
        from app.api.routes.toolsets import _deauth_all_instance_users

        config_service = AsyncMock()
        config_service.list_keys_in_directory = AsyncMock(side_effect=RuntimeError("etcd down"))
        count = await _deauth_all_instance_users("inst-1", config_service)
        assert count == 0

    @pytest.mark.asyncio
    async def test_individual_deauth_failure_still_counts(self):
        from app.api.routes.toolsets import _deauth_all_instance_users

        config_service = AsyncMock()
        config_service.list_keys_in_directory = AsyncMock(
            return_value=["/services/toolsets/inst-1/user-1"]
        )
        # get_config returns None for this user
        config_service.get_config = AsyncMock(return_value=None)

        count = await _deauth_all_instance_users("inst-1", config_service)
        # Still counts since list_keys returned 1
        assert count == 1


# ---------------------------------------------------------------------------
# _deauth_all_instance_users — deeper edge cases
# ---------------------------------------------------------------------------

class TestDeauthAllInstanceUsersDeep:
    @pytest.mark.asyncio
    async def test_deauth_skips_non_dict_records(self):
        """Records that are not dicts are skipped gracefully."""
        from app.api.routes.toolsets import _deauth_all_instance_users

        config_service = AsyncMock()
        config_service.list_keys_in_directory = AsyncMock(
            return_value=["/services/toolsets/inst-1/user-1"]
        )
        # get_config returns a string, not a dict
        config_service.get_config = AsyncMock(return_value="not a dict")

        count = await _deauth_all_instance_users("inst-1", config_service)
        assert count == 1
        # set_config should not be called since record is not a dict
        config_service.set_config.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_deauth_handles_set_config_failure(self):
        """Individual set_config failures don't prevent counting."""
        from app.api.routes.toolsets import _deauth_all_instance_users

        config_service = AsyncMock()
        config_service.list_keys_in_directory = AsyncMock(
            return_value=[
                "/services/toolsets/inst-1/user-1",
                "/services/toolsets/inst-1/user-2",
            ]
        )
        config_service.get_config = AsyncMock(return_value={"isAuthenticated": True})
        config_service.set_config = AsyncMock(side_effect=RuntimeError("write fail"))

        count = await _deauth_all_instance_users("inst-1", config_service)
        assert count == 2

    @pytest.mark.asyncio
    async def test_deauth_sets_deauth_timestamp(self):
        """Deauth sets deauthAt timestamp field."""
        from app.api.routes.toolsets import _deauth_all_instance_users

        config_service = AsyncMock()
        config_service.list_keys_in_directory = AsyncMock(
            return_value=["/services/toolsets/inst-1/user-1"]
        )
        config_service.get_config = AsyncMock(return_value={"isAuthenticated": True})
        config_service.set_config = AsyncMock()

        await _deauth_all_instance_users("inst-1", config_service)
        call_args = config_service.set_config.call_args[0][1]
        assert "deauthAt" in call_args
        assert isinstance(call_args["deauthAt"], int)


# ---------------------------------------------------------------------------
# _prepare_toolset_auth_config — additional params and token access type
# ---------------------------------------------------------------------------

class TestPrepareToolsetAuthConfigDeep:
    @pytest.mark.asyncio
    async def test_additional_params_included(self):
        from app.api.routes.toolsets import _prepare_toolset_auth_config

        registry = MagicMock()
        mock_scopes = MagicMock()
        mock_scopes.get_scopes_for_type.return_value = []
        mock_oauth = MagicMock()
        mock_oauth.authorize_url = "https://auth.example.com/authorize"
        mock_oauth.token_url = "https://auth.example.com/token"
        mock_oauth.redirect_uri = "/callback"
        mock_oauth.scopes = mock_scopes
        mock_oauth.additional_params = {"prompt": "consent"}
        mock_oauth.token_access_type = "offline"
        mock_oauth.scope_parameter_name = "custom_scope"
        mock_oauth.token_response_path = "data.access_token"
        registry.get_toolset_metadata.return_value = {
            "config": {"_oauth_configs": {"OAUTH": mock_oauth}},
        }

        config_service = AsyncMock()
        auth_config = {"type": "OAUTH", "clientId": "cid"}
        result = await _prepare_toolset_auth_config(
            auth_config, "jira", registry, config_service, "https://app.example.com"
        )
        assert result["additionalParams"] == {"prompt": "consent"}
        assert result["tokenAccessType"] == "offline"
        assert result["scopeParameterName"] == "custom_scope"
        assert result["tokenResponsePath"] == "data.access_token"

    @pytest.mark.asyncio
    async def test_no_additional_params_when_absent(self):
        from app.api.routes.toolsets import _prepare_toolset_auth_config

        registry = MagicMock()
        mock_scopes = MagicMock()
        mock_scopes.get_scopes_for_type.return_value = []
        mock_oauth = MagicMock()
        mock_oauth.authorize_url = "https://auth.example.com/authorize"
        mock_oauth.token_url = "https://auth.example.com/token"
        mock_oauth.redirect_uri = "/callback"
        mock_oauth.scopes = mock_scopes
        mock_oauth.additional_params = None
        mock_oauth.token_access_type = None
        mock_oauth.scope_parameter_name = "scope"
        mock_oauth.token_response_path = None
        registry.get_toolset_metadata.return_value = {
            "config": {"_oauth_configs": {"OAUTH": mock_oauth}},
        }

        config_service = AsyncMock()
        auth_config = {"type": "OAUTH"}
        result = await _prepare_toolset_auth_config(
            auth_config, "jira", registry, config_service, "https://app.example.com"
        )
        assert "additionalParams" not in result
        assert "tokenAccessType" not in result
        assert "scopeParameterName" not in result
        assert "tokenResponsePath" not in result

    @pytest.mark.asyncio
    async def test_fallback_redirect_uri_when_config_endpoint_fails(self):
        from app.api.routes.toolsets import _prepare_toolset_auth_config

        registry = MagicMock()
        mock_scopes = MagicMock()
        mock_scopes.get_scopes_for_type.return_value = []
        mock_oauth = MagicMock()
        mock_oauth.authorize_url = "https://auth.example.com/authorize"
        mock_oauth.token_url = "https://auth.example.com/token"
        mock_oauth.redirect_uri = "/callback"
        mock_oauth.scopes = mock_scopes
        mock_oauth.additional_params = None
        mock_oauth.token_access_type = None
        mock_oauth.scope_parameter_name = "scope"
        mock_oauth.token_response_path = None
        registry.get_toolset_metadata.return_value = {
            "config": {"_oauth_configs": {"OAUTH": mock_oauth}},
        }

        config_service = AsyncMock()
        config_service.get_config = AsyncMock(side_effect=RuntimeError("etcd down"))

        auth_config = {"type": "OAUTH"}
        result = await _prepare_toolset_auth_config(
            auth_config, "jira", registry, config_service, base_url=None
        )
        # Should fall back to localhost
        assert "localhost" in result["redirectUri"]
        assert "callback" in result["redirectUri"]


# ---------------------------------------------------------------------------
# _build_oauth_config — deeper coverage
# ---------------------------------------------------------------------------

class TestBuildOAuthConfigDeep:
    @pytest.mark.asyncio
    async def test_custom_redirect_uri_preserved(self):
        from app.api.routes.toolsets import _build_oauth_config

        registry = MagicMock()
        mock_scopes = MagicMock()
        mock_scopes.get_scopes_for_type.return_value = []
        mock_oauth = MagicMock()
        mock_oauth.authorize_url = "https://auth.example.com/authorize"
        mock_oauth.token_url = "https://auth.example.com/token"
        mock_oauth.redirect_uri = "/callback"
        mock_oauth.scopes = mock_scopes
        mock_oauth.additional_params = None
        mock_oauth.token_access_type = None
        mock_oauth.scope_parameter_name = "scope"
        mock_oauth.token_response_path = None
        registry.get_toolset_metadata.return_value = {
            "config": {"_oauth_configs": {"OAUTH": mock_oauth}},
        }

        result = await _build_oauth_config(
            {
                "clientId": "id",
                "clientSecret": "secret",
                "redirectUri": "https://custom.example.com/callback",
            },
            "toolset",
            registry,
        )
        assert result["redirectUri"] == "https://custom.example.com/callback"

    @pytest.mark.asyncio
    async def test_custom_scopes_preserved(self):
        from app.api.routes.toolsets import _build_oauth_config

        registry = MagicMock()
        mock_scopes = MagicMock()
        mock_scopes.get_scopes_for_type.return_value = ["default"]
        mock_oauth = MagicMock()
        mock_oauth.authorize_url = "https://auth.example.com/authorize"
        mock_oauth.token_url = "https://auth.example.com/token"
        mock_oauth.redirect_uri = "/callback"
        mock_oauth.scopes = mock_scopes
        mock_oauth.additional_params = None
        mock_oauth.token_access_type = None
        mock_oauth.scope_parameter_name = "scope"
        mock_oauth.token_response_path = None
        registry.get_toolset_metadata.return_value = {
            "config": {"_oauth_configs": {"OAUTH": mock_oauth}},
        }

        result = await _build_oauth_config(
            {
                "clientId": "id",
                "clientSecret": "secret",
                "scopes": ["custom_read", "custom_write"],
            },
            "toolset",
            registry,
        )
        assert result["scopes"] == ["custom_read", "custom_write"]

    @pytest.mark.asyncio
    async def test_additional_params_from_auth_config(self):
        from app.api.routes.toolsets import _build_oauth_config

        registry = MagicMock()
        mock_scopes = MagicMock()
        mock_scopes.get_scopes_for_type.return_value = []
        mock_oauth = MagicMock()
        mock_oauth.authorize_url = "https://auth.example.com/authorize"
        mock_oauth.token_url = "https://auth.example.com/token"
        mock_oauth.redirect_uri = "/callback"
        mock_oauth.scopes = mock_scopes
        mock_oauth.additional_params = {"from_registry": True}
        mock_oauth.token_access_type = None
        mock_oauth.scope_parameter_name = "scope"
        mock_oauth.token_response_path = None
        registry.get_toolset_metadata.return_value = {
            "config": {"_oauth_configs": {"OAUTH": mock_oauth}},
        }

        result = await _build_oauth_config(
            {
                "clientId": "id",
                "clientSecret": "secret",
                "additionalParams": {"from_user": True},
            },
            "toolset",
            registry,
        )
        # User-supplied additionalParams should take precedence
        assert result["additionalParams"] == {"from_user": True}

    @pytest.mark.asyncio
    async def test_token_access_type_from_registry(self):
        from app.api.routes.toolsets import _build_oauth_config

        registry = MagicMock()
        mock_scopes = MagicMock()
        mock_scopes.get_scopes_for_type.return_value = []
        mock_oauth = MagicMock()
        mock_oauth.authorize_url = "https://auth.example.com/authorize"
        mock_oauth.token_url = "https://auth.example.com/token"
        mock_oauth.redirect_uri = "/callback"
        mock_oauth.scopes = mock_scopes
        mock_oauth.additional_params = None
        mock_oauth.token_access_type = "offline"
        mock_oauth.scope_parameter_name = "scope"
        mock_oauth.token_response_path = None
        registry.get_toolset_metadata.return_value = {
            "config": {"_oauth_configs": {"OAUTH": mock_oauth}},
        }

        result = await _build_oauth_config(
            {"clientId": "id", "clientSecret": "secret"},
            "toolset",
            registry,
        )
        assert result["tokenAccessType"] == "offline"

    @pytest.mark.asyncio
    async def test_scope_parameter_name_from_registry(self):
        from app.api.routes.toolsets import _build_oauth_config

        registry = MagicMock()
        mock_scopes = MagicMock()
        mock_scopes.get_scopes_for_type.return_value = []
        mock_oauth = MagicMock()
        mock_oauth.authorize_url = "https://auth.example.com/authorize"
        mock_oauth.token_url = "https://auth.example.com/token"
        mock_oauth.redirect_uri = "/callback"
        mock_oauth.scopes = mock_scopes
        mock_oauth.additional_params = None
        mock_oauth.token_access_type = None
        mock_oauth.scope_parameter_name = "scp"
        mock_oauth.token_response_path = "nested.token"
        registry.get_toolset_metadata.return_value = {
            "config": {"_oauth_configs": {"OAUTH": mock_oauth}},
        }

        result = await _build_oauth_config(
            {"clientId": "id", "clientSecret": "secret"},
            "toolset",
            registry,
        )
        assert result["scopeParameterName"] == "scp"
        assert result["tokenResponsePath"] == "nested.token"

    @pytest.mark.asyncio
    async def test_no_base_url_falls_back_to_localhost(self):
        from app.api.routes.toolsets import _build_oauth_config

        registry = MagicMock()
        mock_scopes = MagicMock()
        mock_scopes.get_scopes_for_type.return_value = []
        mock_oauth = MagicMock()
        mock_oauth.authorize_url = "https://auth.example.com/authorize"
        mock_oauth.token_url = "https://auth.example.com/token"
        mock_oauth.redirect_uri = "/callback"
        mock_oauth.scopes = mock_scopes
        mock_oauth.additional_params = None
        mock_oauth.token_access_type = None
        mock_oauth.scope_parameter_name = "scope"
        mock_oauth.token_response_path = None
        registry.get_toolset_metadata.return_value = {
            "config": {"_oauth_configs": {"OAUTH": mock_oauth}},
        }

        result = await _build_oauth_config(
            {"clientId": "id", "clientSecret": "secret"},
            "toolset",
            registry,
            base_url=None,
        )
        assert "localhost:3001" in result["redirectUri"]


# ---------------------------------------------------------------------------
# _create_or_update_toolset_oauth_config — deep update scenarios
# ---------------------------------------------------------------------------

class TestCreateOrUpdateToolsetOAuthConfigDeep:
    @pytest.mark.asyncio
    async def test_update_preserves_existing_client_secret_when_empty(self):
        from app.api.routes.toolsets import _create_or_update_toolset_oauth_config

        config_service = AsyncMock()
        existing_cfg = {
            "_id": "cfg-1",
            "orgId": "org-1",
            "config": {"clientId": "old-id", "clientSecret": "old-secret"},
            "updatedAtTimestamp": 100,
        }
        config_service.get_config = AsyncMock(return_value=[existing_cfg])
        config_service.set_config = AsyncMock()

        registry = MagicMock()
        mock_scopes = MagicMock()
        mock_scopes.get_scopes_for_type.return_value = []
        mock_oauth = MagicMock()
        mock_oauth.authorize_url = "https://auth.example.com/authorize"
        mock_oauth.token_url = "https://auth.example.com/token"
        mock_oauth.redirect_uri = "/cb"
        mock_oauth.scopes = mock_scopes
        mock_oauth.additional_params = None
        mock_oauth.token_access_type = None
        mock_oauth.scope_parameter_name = "scope"
        mock_oauth.token_response_path = None
        registry.get_toolset_metadata.return_value = {
            "config": {"_oauth_configs": {"OAUTH": mock_oauth}},
        }

        result_id = await _create_or_update_toolset_oauth_config(
            toolset_type="jira",
            auth_config={"type": "OAUTH", "clientId": "new-id", "clientSecret": ""},
            instance_name="Jira",
            user_id="user-1",
            org_id="org-1",
            config_service=config_service,
            registry=registry,
            base_url="https://app.example.com",
            oauth_config_id="cfg-1",
        )
        assert result_id == "cfg-1"
        # The saved config should have the new clientId but preserved clientSecret
        saved_data = config_service.set_config.call_args[0][1]
        assert saved_data[0]["config"]["clientId"] == "new-id"
        assert saved_data[0]["config"]["clientSecret"] == "old-secret"

    @pytest.mark.asyncio
    async def test_update_wrong_org_falls_through_to_create(self):
        from app.api.routes.toolsets import _create_or_update_toolset_oauth_config

        config_service = AsyncMock()
        existing_cfg = {
            "_id": "cfg-1",
            "orgId": "org-2",  # Different org
            "config": {"clientId": "old-id"},
        }
        config_service.get_config = AsyncMock(return_value=[existing_cfg])
        config_service.set_config = AsyncMock()

        registry = MagicMock()
        mock_scopes = MagicMock()
        mock_scopes.get_scopes_for_type.return_value = []
        mock_oauth = MagicMock()
        mock_oauth.authorize_url = "https://auth.example.com/authorize"
        mock_oauth.token_url = "https://auth.example.com/token"
        mock_oauth.redirect_uri = "/cb"
        mock_oauth.scopes = mock_scopes
        mock_oauth.additional_params = None
        mock_oauth.token_access_type = None
        mock_oauth.scope_parameter_name = "scope"
        mock_oauth.token_response_path = None
        registry.get_toolset_metadata.return_value = {
            "config": {"_oauth_configs": {"OAUTH": mock_oauth}},
        }

        result_id = await _create_or_update_toolset_oauth_config(
            toolset_type="jira",
            auth_config={"type": "OAUTH", "clientId": "cid", "clientSecret": "cs"},
            instance_name="Jira",
            user_id="user-1",
            org_id="org-1",
            config_service=config_service,
            registry=registry,
            base_url="https://app.example.com",
            oauth_config_id="cfg-1",
        )
        # Should fall through to create since org doesn't match
        assert result_id is not None
        assert result_id != "cfg-1"


# ---------------------------------------------------------------------------
# _validate_non_empty_string — deeper edge cases
# ---------------------------------------------------------------------------

class TestValidateNonEmptyStringDeep:
    def test_unicode_string_accepted(self):
        from app.api.routes.toolsets import _validate_non_empty_string
        result = _validate_non_empty_string("  unicode  ", "field")
        assert result == "unicode"

    def test_tab_whitespace_raises(self):
        from app.api.routes.toolsets import _validate_non_empty_string
        with pytest.raises(HTTPException):
            _validate_non_empty_string("\t\n", "field")

    def test_error_detail_contains_field_name(self):
        from app.api.routes.toolsets import _validate_non_empty_string
        with pytest.raises(HTTPException) as exc:
            _validate_non_empty_string("", "myField")
        assert "myField" in exc.value.detail

    def test_list_raises(self):
        from app.api.routes.toolsets import _validate_non_empty_string
        with pytest.raises(HTTPException):
            _validate_non_empty_string(["a"], "field")

    def test_dict_raises(self):
        from app.api.routes.toolsets import _validate_non_empty_string
        with pytest.raises(HTTPException):
            _validate_non_empty_string({"a": 1}, "field")

    def test_bool_raises(self):
        from app.api.routes.toolsets import _validate_non_empty_string
        with pytest.raises(HTTPException):
            _validate_non_empty_string(True, "field")


# ---------------------------------------------------------------------------
# _validate_list — deeper edge cases
# ---------------------------------------------------------------------------

class TestValidateListDeep:
    def test_tuple_raises(self):
        from app.api.routes.toolsets import _validate_list
        with pytest.raises(HTTPException) as exc:
            _validate_list((1, 2, 3), "items")
        assert "must be a list" in exc.value.detail

    def test_nested_list_allowed(self):
        from app.api.routes.toolsets import _validate_list
        result = _validate_list([[1], [2]], "items")
        assert result == [[1], [2]]

    def test_error_detail_contains_field_name(self):
        from app.api.routes.toolsets import _validate_list
        with pytest.raises(HTTPException) as exc:
            _validate_list(None, "myItems", allow_empty=False)
        assert "myItems" in exc.value.detail


# ---------------------------------------------------------------------------
# _validate_dict — deeper edge cases
# ---------------------------------------------------------------------------

class TestValidateDictDeep:
    def test_nested_dict_allowed(self):
        from app.api.routes.toolsets import _validate_dict
        result = _validate_dict({"a": {"b": 1}}, "config")
        assert result == {"a": {"b": 1}}

    def test_error_detail_contains_field_name(self):
        from app.api.routes.toolsets import _validate_dict
        with pytest.raises(HTTPException) as exc:
            _validate_dict(None, "myConfig", allow_empty=False)
        assert "myConfig" in exc.value.detail

    def test_bool_raises(self):
        from app.api.routes.toolsets import _validate_dict
        with pytest.raises(HTTPException):
            _validate_dict(True, "config")

    def test_int_raises(self):
        from app.api.routes.toolsets import _validate_dict
        with pytest.raises(HTTPException):
            _validate_dict(42, "config")


# ---------------------------------------------------------------------------
# _parse_request_json — deeper edge cases
# ---------------------------------------------------------------------------

class TestParseRequestJsonDeep:
    def test_valid_list_json(self):
        from app.api.routes.toolsets import _parse_request_json
        data = json.dumps([1, 2, 3]).encode()
        result = _parse_request_json(MagicMock(), data)
        assert result == [1, 2, 3]

    def test_nested_json(self):
        from app.api.routes.toolsets import _parse_request_json
        data = json.dumps({"key": {"nested": True}}).encode()
        result = _parse_request_json(MagicMock(), data)
        assert result["key"]["nested"] is True

    def test_whitespace_only_body_raises(self):
        from app.api.routes.toolsets import _parse_request_json
        with pytest.raises(HTTPException) as exc:
            _parse_request_json(MagicMock(), b"   ")
        assert exc.value.status_code == 400

    def test_truncated_json_raises(self):
        from app.api.routes.toolsets import _parse_request_json
        with pytest.raises(HTTPException) as exc:
            _parse_request_json(MagicMock(), b'{"key": ')
        assert "Invalid JSON" in exc.value.detail


# ---------------------------------------------------------------------------
# _get_user_context — deeper coverage
# ---------------------------------------------------------------------------

class TestGetUserContextDeep:
    def test_state_user_takes_precedence_over_headers(self):
        from app.api.routes.toolsets import _get_user_context
        request = MagicMock()
        request.state.user = {"userId": "state-user", "orgId": "state-org"}
        request.headers = {"X-User-Id": "header-user", "X-Organization-Id": "header-org"}
        ctx = _get_user_context(request)
        assert ctx["user_id"] == "state-user"
        assert ctx["org_id"] == "state-org"

    def test_missing_org_id_does_not_raise(self):
        from app.api.routes.toolsets import _get_user_context
        request = MagicMock()
        request.state.user = {"userId": "u1"}
        request.headers = {}
        ctx = _get_user_context(request)
        assert ctx["user_id"] == "u1"
        assert ctx["org_id"] is None or ctx["org_id"] == ""

    def test_state_attribute_error_falls_back_to_headers(self):
        from app.api.routes.toolsets import _get_user_context
        request = MagicMock()
        request.state.user = {}
        request.headers = {"X-User-Id": "header-user", "X-Organization-Id": "header-org"}
        ctx = _get_user_context(request)
        assert ctx["user_id"] == "header-user"


# ===========================================================================
# Route handler tests — Registry endpoints
# ===========================================================================


class TestGetToolsetRegistryEndpoint:
    @pytest.mark.asyncio
    async def test_basic_listing(self):
        from app.api.routes.toolsets import get_toolset_registry_endpoint

        registry = MagicMock()
        registry.list_toolsets.return_value = ["jira", "slack"]
        registry.get_toolset_metadata.side_effect = lambda name: {
            "name": name,
            "display_name": name.title(),
            "description": f"{name} integration",
            "category": "app",
            "group": "",
            "icon_path": "",
            "supported_auth_types": ["OAUTH"],
            "tools": [{"name": "search"}],
        }

        request = MagicMock()
        request.app.state.toolset_registry = registry

        result = await get_toolset_registry_endpoint(request, page=1, limit=20, search=None, include_tools=True, include_tool_count=True, group_by_category=True)
        assert result["status"] == "success"
        assert len(result["toolsets"]) == 2

    @pytest.mark.asyncio
    async def test_search_filter(self):
        from app.api.routes.toolsets import get_toolset_registry_endpoint

        registry = MagicMock()
        registry.list_toolsets.return_value = ["jira", "slack"]
        registry.get_toolset_metadata.side_effect = lambda name: {
            "name": name,
            "display_name": name.title(),
            "description": f"{name} integration",
            "category": "app",
            "group": "",
            "icon_path": "",
            "supported_auth_types": [],
            "tools": [],
        }

        request = MagicMock()
        request.app.state.toolset_registry = registry

        result = await get_toolset_registry_endpoint(request, page=1, limit=20, search="jira", include_tools=True, include_tool_count=True, group_by_category=False)
        toolset_names = [t["name"] for t in result["toolsets"]]
        assert "jira" in toolset_names
        assert "slack" not in toolset_names

    @pytest.mark.asyncio
    async def test_internal_toolsets_excluded(self):
        from app.api.routes.toolsets import get_toolset_registry_endpoint

        registry = MagicMock()
        registry.list_toolsets.return_value = ["jira", "internal_tool"]

        def mock_meta(name):
            if name == "internal_tool":
                return {"isInternal": True, "name": name, "tools": []}
            return {
                "name": name, "display_name": name.title(), "description": "",
                "category": "app", "tools": [],
            }

        registry.get_toolset_metadata.side_effect = mock_meta

        request = MagicMock()
        request.app.state.toolset_registry = registry

        result = await get_toolset_registry_endpoint(request, page=1, limit=20, search=None, include_tools=True, include_tool_count=True, group_by_category=False)
        names = [t["name"] for t in result["toolsets"]]
        assert "internal_tool" not in names

    @pytest.mark.asyncio
    async def test_group_by_category(self):
        from app.api.routes.toolsets import get_toolset_registry_endpoint

        registry = MagicMock()
        registry.list_toolsets.return_value = ["jira"]
        registry.get_toolset_metadata.return_value = {
            "name": "jira", "display_name": "Jira", "description": "",
            "category": "project_management", "tools": [],
        }

        request = MagicMock()
        request.app.state.toolset_registry = registry

        result = await get_toolset_registry_endpoint(request, page=1, limit=20, search=None, include_tools=True, include_tool_count=True, group_by_category=True)
        assert "project_management" in result["categorizedToolsets"]


class TestGetToolsetSchema:
    @pytest.mark.asyncio
    async def test_success(self):
        from app.api.routes.toolsets import get_toolset_schema

        registry = MagicMock()
        registry.get_toolset_metadata.return_value = {
            "name": "jira", "display_name": "Jira", "description": "Jira integration",
            "category": "pm", "supported_auth_types": ["OAUTH"], "tools": [],
        }

        request = MagicMock()
        request.app.state.toolset_registry = registry
        request.app.state.oauth_config_registry = None

        result = await get_toolset_schema("jira", request)
        assert result["status"] == "success"
        assert result["toolset"]["name"] == "jira"

    @pytest.mark.asyncio
    async def test_with_oauth_registry(self):
        from app.api.routes.toolsets import get_toolset_schema

        registry = MagicMock()
        registry.get_toolset_metadata.return_value = {
            "name": "jira", "display_name": "Jira", "description": "",
            "category": "pm", "supported_auth_types": ["OAUTH"], "tools": [],
        }

        oauth_registry = MagicMock()
        oauth_registry.has_config.return_value = True
        oauth_registry.get_metadata.return_value = {"type": "OAUTH"}

        request = MagicMock()
        request.app.state.toolset_registry = registry
        request.app.state.oauth_config_registry = oauth_registry

        result = await get_toolset_schema("jira", request)
        assert result["toolset"]["oauthConfig"] == {"type": "OAUTH"}


class TestGetAllTools:
    @pytest.mark.asyncio
    async def test_returns_flat_list(self):
        from app.api.routes.toolsets import get_all_tools

        registry = MagicMock()
        registry.list_toolsets.return_value = ["jira"]
        registry.get_toolset_metadata.return_value = {
            "name": "jira", "tools": [
                {"name": "search", "description": "Search issues", "parameters": [], "tags": ["query"]},
            ],
        }

        request = MagicMock()
        request.app.state.toolset_registry = registry

        result = await get_all_tools(request, app_name=None, tag=None, search=None)
        assert len(result) == 1
        assert result[0]["full_name"] == "jira.search"

    @pytest.mark.asyncio
    async def test_filter_by_app_name(self):
        from app.api.routes.toolsets import get_all_tools

        registry = MagicMock()
        registry.list_toolsets.return_value = ["jira", "slack"]

        def mock_meta(name):
            return {"name": name, "tools": [{"name": "action", "description": ""}]}

        registry.get_toolset_metadata.side_effect = mock_meta

        request = MagicMock()
        request.app.state.toolset_registry = registry

        result = await get_all_tools(request, app_name="jira", tag=None, search=None)
        assert all(t["app_name"] == "jira" for t in result)

    @pytest.mark.asyncio
    async def test_filter_by_search(self):
        from app.api.routes.toolsets import get_all_tools

        registry = MagicMock()
        registry.list_toolsets.return_value = ["jira"]
        registry.get_toolset_metadata.return_value = {
            "name": "jira", "tools": [
                {"name": "search", "description": "Search issues"},
                {"name": "create", "description": "Create issue"},
            ],
        }

        request = MagicMock()
        request.app.state.toolset_registry = registry

        result = await get_all_tools(request, app_name=None, tag=None, search="search")
        assert len(result) == 1
        assert result[0]["tool_name"] == "search"


class TestGetToolsetTools:
    @pytest.mark.asyncio
    async def test_success(self):
        from app.api.routes.toolsets import get_toolset_tools

        registry = MagicMock()
        registry.get_toolset_metadata.return_value = {
            "name": "jira", "display_name": "Jira",
            "tools": [{"name": "search", "description": "Search"}],
        }

        request = MagicMock()
        request.app.state.toolset_registry = registry

        result = await get_toolset_tools("jira", request)
        assert result["status"] == "success"
        assert len(result["tools"]) == 1
        assert result["tools"][0]["fullName"] == "jira.search"


# ===========================================================================
# Route handler tests — Instance CRUD
# ===========================================================================


class TestCreateToolsetInstance:
    @pytest.mark.asyncio
    async def test_non_admin_rejected(self):
        from app.api.routes.toolsets import create_toolset_instance

        request = MagicMock()
        request.state.user = {"userId": "u1", "orgId": "o1"}
        request.headers = {}

        config_service = AsyncMock()

        with patch("app.api.routes.toolsets._get_user_context", return_value={"user_id": "u1", "org_id": "o1"}), \
             patch("app.api.routes.toolsets._check_user_is_admin", new_callable=AsyncMock, return_value=False):

            with pytest.raises(HTTPException) as exc:
                await create_toolset_instance(request, config_service=config_service)
            assert exc.value.status_code == 403


class TestGetToolsetInstances:
    @pytest.mark.asyncio
    async def test_success(self):
        from app.api.routes.toolsets import get_toolset_instances

        request = MagicMock()
        registry = MagicMock()
        registry.get_toolset_metadata.return_value = {
            "display_name": "Jira", "description": "Jira", "icon_path": "",
            "tools": [{"name": "search"}],
        }
        request.app.state.toolset_registry = registry

        config_service = AsyncMock()
        config_service.get_config = AsyncMock(return_value=[
            {"_id": "i1", "orgId": "o1", "toolsetType": "jira", "instanceName": "My Jira"},
        ])

        with patch("app.api.routes.toolsets._get_user_context", return_value={"user_id": "u1", "org_id": "o1"}):
            result = await get_toolset_instances(request, page=1, limit=50, search=None, config_service=config_service)
            assert result["status"] == "success"
            assert len(result["instances"]) == 1

    @pytest.mark.asyncio
    async def test_with_search(self):
        from app.api.routes.toolsets import get_toolset_instances

        request = MagicMock()
        registry = MagicMock()
        registry.get_toolset_metadata.return_value = {
            "display_name": "Jira", "description": "", "icon_path": "", "tools": [],
        }
        request.app.state.toolset_registry = registry

        config_service = AsyncMock()
        config_service.get_config = AsyncMock(return_value=[
            {"_id": "i1", "orgId": "o1", "toolsetType": "jira", "instanceName": "My Jira"},
            {"_id": "i2", "orgId": "o1", "toolsetType": "slack", "instanceName": "My Slack"},
        ])

        with patch("app.api.routes.toolsets._get_user_context", return_value={"user_id": "u1", "org_id": "o1"}):
            result = await get_toolset_instances(request, page=1, limit=50, search="jira", config_service=config_service)
            assert len(result["instances"]) == 1


class TestGetToolsetInstance:
    @pytest.mark.asyncio
    async def test_found(self):
        from app.api.routes.toolsets import get_toolset_instance

        request = MagicMock()
        registry = MagicMock()
        registry.get_toolset_metadata.return_value = {
            "display_name": "Jira", "description": "", "icon_path": "",
            "supported_auth_types": ["OAUTH"], "tools": [],
        }
        request.app.state.toolset_registry = registry

        config_service = AsyncMock()
        config_service.get_config = AsyncMock(return_value=[
            {"_id": "i1", "orgId": "o1", "toolsetType": "jira", "authType": "API_TOKEN"},
        ])

        with patch("app.api.routes.toolsets._get_user_context", return_value={"user_id": "u1", "org_id": "o1"}), \
             patch("app.api.routes.toolsets._check_user_is_admin", new_callable=AsyncMock, return_value=False):

            result = await get_toolset_instance("i1", request, config_service=config_service)
            assert result["status"] == "success"

    @pytest.mark.asyncio
    async def test_not_found(self):
        from app.api.routes.toolsets import get_toolset_instance

        request = MagicMock()

        config_service = AsyncMock()
        config_service.get_config = AsyncMock(return_value=[])

        with patch("app.api.routes.toolsets._get_user_context", return_value={"user_id": "u1", "org_id": "o1"}), \
             patch("app.api.routes.toolsets._check_user_is_admin", new_callable=AsyncMock, return_value=False):

            with pytest.raises(HTTPException) as exc:
                await get_toolset_instance("missing", request, config_service=config_service)
            assert exc.value.status_code == 404

    @pytest.mark.asyncio
    async def test_admin_gets_oauth_config(self):
        from app.api.routes.toolsets import get_toolset_instance

        request = MagicMock()
        registry = MagicMock()
        registry.get_toolset_metadata.return_value = {
            "display_name": "Jira", "description": "", "icon_path": "",
            "supported_auth_types": ["OAUTH"], "tools": [],
        }
        request.app.state.toolset_registry = registry

        config_service = AsyncMock()
        config_service.get_config = AsyncMock(return_value=[
            {"_id": "i1", "orgId": "o1", "toolsetType": "jira", "authType": "OAUTH", "oauthConfigId": "cfg-1"},
        ])
        config_service.list_keys_in_directory = AsyncMock(return_value=["key1", "key2"])

        with patch("app.api.routes.toolsets._get_user_context", return_value={"user_id": "u1", "org_id": "o1"}), \
             patch("app.api.routes.toolsets._check_user_is_admin", new_callable=AsyncMock, return_value=True), \
             patch("app.api.routes.toolsets._get_oauth_config_by_id", new_callable=AsyncMock, return_value={
                 "_id": "cfg-1", "oauthInstanceName": "My OAuth",
                 "config": {"clientId": "cid", "clientSecret": "cs"},
             }):

            result = await get_toolset_instance("i1", request, config_service=config_service)
            assert result["instance"].get("oauthConfig") is not None
            assert result["instance"]["authenticatedUserCount"] == 2


class TestDeleteToolsetInstance:
    @pytest.mark.asyncio
    async def test_non_admin_rejected(self):
        from app.api.routes.toolsets import delete_toolset_instance

        request = MagicMock()
        config_service = AsyncMock()

        with patch("app.api.routes.toolsets._get_user_context", return_value={"user_id": "u1", "org_id": "o1"}), \
             patch("app.api.routes.toolsets._check_user_is_admin", new_callable=AsyncMock, return_value=False):

            with pytest.raises(HTTPException) as exc:
                await delete_toolset_instance("i1", request, config_service=config_service)
            assert exc.value.status_code == 403

    @pytest.mark.asyncio
    async def test_not_found(self):
        from app.api.routes.toolsets import delete_toolset_instance

        request = MagicMock()
        config_service = AsyncMock()
        config_service.get_config = AsyncMock(return_value=[])

        with patch("app.api.routes.toolsets._get_user_context", return_value={"user_id": "u1", "org_id": "o1"}), \
             patch("app.api.routes.toolsets._check_user_is_admin", new_callable=AsyncMock, return_value=True):

            with pytest.raises(HTTPException) as exc:
                await delete_toolset_instance("missing", request, config_service=config_service)
            assert exc.value.status_code == 404

    @pytest.mark.asyncio
    async def test_in_use_blocked(self):
        from app.api.routes.toolsets import delete_toolset_instance, ToolsetInUseError

        request = MagicMock()
        graph_provider = MagicMock()
        graph_provider.check_toolset_instance_in_use = AsyncMock(return_value=["Agent 1"])
        request.app.state.graph_provider = graph_provider

        config_service = AsyncMock()
        config_service.get_config = AsyncMock(return_value=[
            {"_id": "i1", "orgId": "o1", "instanceName": "My Jira"},
        ])

        with patch("app.api.routes.toolsets._get_user_context", return_value={"user_id": "u1", "org_id": "o1"}), \
             patch("app.api.routes.toolsets._check_user_is_admin", new_callable=AsyncMock, return_value=True):

            with pytest.raises(ToolsetInUseError):
                await delete_toolset_instance("i1", request, config_service=config_service)

    @pytest.mark.asyncio
    async def test_success(self):
        from app.api.routes.toolsets import delete_toolset_instance

        request = MagicMock()
        graph_provider = MagicMock()
        graph_provider.check_toolset_instance_in_use = AsyncMock(return_value=[])
        request.app.state.graph_provider = graph_provider

        config_service = AsyncMock()
        config_service.get_config = AsyncMock(return_value=[
            {"_id": "i1", "orgId": "o1", "instanceName": "My Jira"},
        ])
        config_service.set_config = AsyncMock()
        config_service.list_keys_in_directory = AsyncMock(return_value=[])

        with patch("app.api.routes.toolsets._get_user_context", return_value={"user_id": "u1", "org_id": "o1"}), \
             patch("app.api.routes.toolsets._check_user_is_admin", new_callable=AsyncMock, return_value=True):

            result = await delete_toolset_instance("i1", request, config_service=config_service)
            assert result["status"] == "success"


# ===========================================================================
# Route handler tests — My Toolsets
# ===========================================================================


class TestGetMyToolsets:
    @pytest.mark.asyncio
    async def test_empty_instances(self):
        from app.api.routes.toolsets import get_my_toolsets

        request = MagicMock()
        config_service = AsyncMock()
        config_service.get_config = AsyncMock(return_value=[])

        with patch("app.api.routes.toolsets._get_user_context", return_value={"user_id": "u1", "org_id": "o1"}):
            result = await get_my_toolsets(request, search=None, page=1, limit=20, include_registry=False, auth_status=None, config_service=config_service)
            assert result["status"] == "success"
            assert result["toolsets"] == []

    @pytest.mark.asyncio
    async def test_with_instances(self):
        from app.api.routes.toolsets import get_my_toolsets

        request = MagicMock()
        registry = MagicMock()
        registry.get_toolset_metadata.return_value = {
            "display_name": "Jira", "description": "", "icon_path": "",
            "category": "app", "supported_auth_types": ["OAUTH"], "tools": [],
        }
        registry.list_toolsets.return_value = []
        request.app.state.toolset_registry = registry

        config_service = AsyncMock()

        async def mock_get_config(path, default=None, use_cache=True):
            if "toolset-instances" in path:
                return [{"_id": "i1", "orgId": "o1", "toolsetType": "jira", "instanceName": "My Jira", "authType": "OAUTH"}]
            return default

        config_service.get_config = mock_get_config

        with patch("app.api.routes.toolsets._get_user_context", return_value={"user_id": "u1", "org_id": "o1"}):
            result = await get_my_toolsets(request, search=None, page=1, limit=20, include_registry=False, auth_status=None, config_service=config_service)
            assert result["status"] == "success"
            assert len(result["toolsets"]) == 1
            assert result["filterCounts"]["all"] == 1

    @pytest.mark.asyncio
    async def test_auth_status_filter(self):
        from app.api.routes.toolsets import get_my_toolsets

        request = MagicMock()
        registry = MagicMock()
        registry.get_toolset_metadata.return_value = {
            "display_name": "Jira", "description": "", "icon_path": "",
            "category": "app", "supported_auth_types": [], "tools": [],
        }
        registry.list_toolsets.return_value = []
        request.app.state.toolset_registry = registry

        config_service = AsyncMock()

        async def mock_get_config(path, default=None, use_cache=True):
            if "toolset-instances" in path:
                return [
                    {"_id": "i1", "orgId": "o1", "toolsetType": "jira", "instanceName": "Jira 1", "authType": "OAUTH"},
                    {"_id": "i2", "orgId": "o1", "toolsetType": "slack", "instanceName": "Slack 1", "authType": "OAUTH"},
                ]
            if "i1" in str(path):
                return {"isAuthenticated": True}
            return default

        config_service.get_config = mock_get_config

        with patch("app.api.routes.toolsets._get_user_context", return_value={"user_id": "u1", "org_id": "o1"}):
            result = await get_my_toolsets(request, search=None, page=1, limit=20, include_registry=False, auth_status="authenticated", config_service=config_service)
            assert result["filterCounts"]["all"] == 2
            assert all(t["isAuthenticated"] for t in result["toolsets"])


# ===========================================================================
# Route handler tests — User Authentication
# ===========================================================================


class TestAuthenticateToolsetInstance:
    @pytest.mark.asyncio
    async def test_success_api_token(self):
        from app.api.routes.toolsets import authenticate_toolset_instance

        request = MagicMock()
        request.body = AsyncMock(return_value=json.dumps({"auth": {"apiToken": "my-token"}}).encode())

        config_service = AsyncMock()
        config_service.get_config = AsyncMock(return_value=[
            {"_id": "i1", "orgId": "o1", "toolsetType": "jira", "authType": "API_TOKEN"},
        ])
        config_service.set_config = AsyncMock()

        with patch("app.api.routes.toolsets._get_user_context", return_value={"user_id": "u1", "org_id": "o1"}), \
             patch("app.api.routes.toolsets._parse_request_json", return_value={"auth": {"apiToken": "my-token"}}):

            result = await authenticate_toolset_instance("i1", request, config_service=config_service)
            assert result["status"] == "success"
            assert result["isAuthenticated"] is True

    @pytest.mark.asyncio
    async def test_not_found(self):
        from app.api.routes.toolsets import authenticate_toolset_instance

        request = MagicMock()
        config_service = AsyncMock()
        config_service.get_config = AsyncMock(return_value=[])

        with patch("app.api.routes.toolsets._get_user_context", return_value={"user_id": "u1", "org_id": "o1"}):
            with pytest.raises(HTTPException) as exc:
                await authenticate_toolset_instance("missing", request, config_service=config_service)
            assert exc.value.status_code == 404

    @pytest.mark.asyncio
    async def test_oauth_type_rejected(self):
        from app.api.routes.toolsets import authenticate_toolset_instance

        request = MagicMock()
        config_service = AsyncMock()
        config_service.get_config = AsyncMock(return_value=[
            {"_id": "i1", "orgId": "o1", "authType": "OAUTH"},
        ])

        with patch("app.api.routes.toolsets._get_user_context", return_value={"user_id": "u1", "org_id": "o1"}):
            with pytest.raises(HTTPException) as exc:
                await authenticate_toolset_instance("i1", request, config_service=config_service)
            assert exc.value.status_code == 400


class TestUpdateToolsetCredentials:
    @pytest.mark.asyncio
    async def test_success(self):
        from app.api.routes.toolsets import update_toolset_credentials

        request = MagicMock()
        request.body = AsyncMock(return_value=json.dumps({"auth": {"apiToken": "new-token"}}).encode())

        config_service = AsyncMock()
        config_service.get_config = AsyncMock(return_value={"isAuthenticated": True, "auth": {"apiToken": "old-token"}})
        config_service.set_config = AsyncMock()

        with patch("app.api.routes.toolsets._get_user_context", return_value={"user_id": "u1", "org_id": "o1"}), \
             patch("app.api.routes.toolsets._parse_request_json", return_value={"auth": {"apiToken": "new-token"}}):

            result = await update_toolset_credentials("i1", request, config_service=config_service)
            assert result["status"] == "success"

    @pytest.mark.asyncio
    async def test_no_existing_raises(self):
        from app.api.routes.toolsets import update_toolset_credentials

        request = MagicMock()
        request.body = AsyncMock(return_value=json.dumps({"auth": {"apiToken": "t"}}).encode())

        config_service = AsyncMock()
        config_service.get_config = AsyncMock(return_value=None)

        with patch("app.api.routes.toolsets._get_user_context", return_value={"user_id": "u1", "org_id": "o1"}), \
             patch("app.api.routes.toolsets._parse_request_json", return_value={"auth": {"apiToken": "t"}}):

            with pytest.raises(HTTPException) as exc:
                await update_toolset_credentials("i1", request, config_service=config_service)
            assert exc.value.status_code == 404

    @pytest.mark.asyncio
    async def test_empty_auth_raises(self):
        from app.api.routes.toolsets import update_toolset_credentials

        request = MagicMock()
        request.body = AsyncMock(return_value=b'{"auth":{}}')

        config_service = AsyncMock()

        with patch("app.api.routes.toolsets._get_user_context", return_value={"user_id": "u1", "org_id": "o1"}), \
             patch("app.api.routes.toolsets._parse_request_json", return_value={"auth": {}}):

            with pytest.raises(HTTPException) as exc:
                await update_toolset_credentials("i1", request, config_service=config_service)
            assert exc.value.status_code == 400


class TestRemoveToolsetCredentials:
    @pytest.mark.asyncio
    async def test_success(self):
        from app.api.routes.toolsets import remove_toolset_credentials

        request = MagicMock()
        config_service = AsyncMock()
        config_service.delete_config = AsyncMock()

        mock_startup = MagicMock()
        mock_refresh = MagicMock()
        mock_startup.get_toolset_token_refresh_service.return_value = mock_refresh

        with patch("app.api.routes.toolsets._get_user_context", return_value={"user_id": "u1", "org_id": "o1"}), \
             patch("app.connectors.core.base.token_service.startup_service.startup_service", mock_startup):

            result = await remove_toolset_credentials("i1", request, config_service=config_service)
            assert result["status"] == "success"


class TestReauthenticateToolsetInstance:
    @pytest.mark.asyncio
    async def test_success(self):
        from app.api.routes.toolsets import reauthenticate_toolset_instance

        request = MagicMock()
        config_service = AsyncMock()
        config_service.get_config = AsyncMock(return_value=[
            {"_id": "i1", "orgId": "o1"},
        ])
        config_service.delete_config = AsyncMock()

        mock_startup = MagicMock()
        mock_refresh = MagicMock()
        mock_startup.get_toolset_token_refresh_service.return_value = mock_refresh

        with patch("app.api.routes.toolsets._get_user_context", return_value={"user_id": "u1", "org_id": "o1"}), \
             patch("app.connectors.core.base.token_service.startup_service.startup_service", mock_startup):

            result = await reauthenticate_toolset_instance("i1", request, config_service=config_service)
            assert result["status"] == "success"

    @pytest.mark.asyncio
    async def test_not_found(self):
        from app.api.routes.toolsets import reauthenticate_toolset_instance

        request = MagicMock()
        config_service = AsyncMock()
        config_service.get_config = AsyncMock(return_value=[])

        with patch("app.api.routes.toolsets._get_user_context", return_value={"user_id": "u1", "org_id": "o1"}):
            with pytest.raises(HTTPException) as exc:
                await reauthenticate_toolset_instance("missing", request, config_service=config_service)
            assert exc.value.status_code == 404


# ===========================================================================
# Route handler tests — OAuth Configs Admin
# ===========================================================================


class TestListToolsetOAuthConfigs:
    @pytest.mark.asyncio
    async def test_admin_sees_secrets(self):
        from app.api.routes.toolsets import list_toolset_oauth_configs

        request = MagicMock()
        config_service = AsyncMock()
        config_service.get_config = AsyncMock(return_value=[
            {"_id": "cfg-1", "orgId": "o1", "oauthInstanceName": "My Config",
             "config": {"clientId": "cid", "clientSecret": "cs"}},
        ])

        with patch("app.api.routes.toolsets._get_user_context", return_value={"user_id": "u1", "org_id": "o1"}), \
             patch("app.api.routes.toolsets._check_user_is_admin", new_callable=AsyncMock, return_value=True):

            result = await list_toolset_oauth_configs("jira", request, config_service=config_service)
            assert result["status"] == "success"
            assert len(result["oauthConfigs"]) == 1
            assert result["oauthConfigs"][0]["clientId"] == "cid"

    @pytest.mark.asyncio
    async def test_non_admin_no_secrets(self):
        from app.api.routes.toolsets import list_toolset_oauth_configs

        request = MagicMock()
        config_service = AsyncMock()
        config_service.get_config = AsyncMock(return_value=[
            {"_id": "cfg-1", "orgId": "o1", "oauthInstanceName": "My Config",
             "config": {"clientId": "cid", "clientSecret": "cs"}},
        ])

        with patch("app.api.routes.toolsets._get_user_context", return_value={"user_id": "u1", "org_id": "o1"}), \
             patch("app.api.routes.toolsets._check_user_is_admin", new_callable=AsyncMock, return_value=False):

            result = await list_toolset_oauth_configs("jira", request, config_service=config_service)
            assert result["status"] == "success"
            assert "clientId" not in result["oauthConfigs"][0]


class TestDeleteToolsetOAuthConfig:
    @pytest.mark.asyncio
    async def test_non_admin_rejected(self):
        from app.api.routes.toolsets import delete_toolset_oauth_config

        request = MagicMock()
        config_service = AsyncMock()

        with patch("app.api.routes.toolsets._get_user_context", return_value={"user_id": "u1", "org_id": "o1"}), \
             patch("app.api.routes.toolsets._check_user_is_admin", new_callable=AsyncMock, return_value=False):

            with pytest.raises(HTTPException) as exc:
                await delete_toolset_oauth_config("jira", "cfg-1", request, config_service=config_service)
            assert exc.value.status_code == 403

    @pytest.mark.asyncio
    async def test_not_found(self):
        from app.api.routes.toolsets import delete_toolset_oauth_config

        request = MagicMock()
        config_service = AsyncMock()
        config_service.get_config = AsyncMock(return_value=[])

        with patch("app.api.routes.toolsets._get_user_context", return_value={"user_id": "u1", "org_id": "o1"}), \
             patch("app.api.routes.toolsets._check_user_is_admin", new_callable=AsyncMock, return_value=True):

            with pytest.raises(HTTPException) as exc:
                await delete_toolset_oauth_config("jira", "missing", request, config_service=config_service)
            assert exc.value.status_code == 404

    @pytest.mark.asyncio
    async def test_in_use_blocked(self):
        from app.api.routes.toolsets import delete_toolset_oauth_config

        request = MagicMock()
        config_service = AsyncMock()

        async def mock_get_config(path, default=None, use_cache=True):
            if "oauths" in path:
                return [{"_id": "cfg-1", "orgId": "o1"}]
            if "instances" in path:
                return [{"_id": "i1", "orgId": "o1", "oauthConfigId": "cfg-1", "instanceName": "My Jira"}]
            return default

        config_service.get_config = mock_get_config

        with patch("app.api.routes.toolsets._get_user_context", return_value={"user_id": "u1", "org_id": "o1"}), \
             patch("app.api.routes.toolsets._check_user_is_admin", new_callable=AsyncMock, return_value=True):

            with pytest.raises(HTTPException) as exc:
                await delete_toolset_oauth_config("jira", "cfg-1", request, config_service=config_service)
            assert exc.value.status_code == 409

    @pytest.mark.asyncio
    async def test_success(self):
        from app.api.routes.toolsets import delete_toolset_oauth_config

        request = MagicMock()
        config_service = AsyncMock()

        async def mock_get_config(path, default=None, use_cache=True):
            if "oauths" in path:
                return [{"_id": "cfg-1", "orgId": "o1"}]
            if "instances" in path:
                return []
            return default

        config_service.get_config = mock_get_config
        config_service.set_config = AsyncMock()

        with patch("app.api.routes.toolsets._get_user_context", return_value={"user_id": "u1", "org_id": "o1"}), \
             patch("app.api.routes.toolsets._check_user_is_admin", new_callable=AsyncMock, return_value=True):

            result = await delete_toolset_oauth_config("jira", "cfg-1", request, config_service=config_service)
            assert result["status"] == "success"


# ===========================================================================
# Route handler tests — Instance Status
# ===========================================================================


class TestGetInstanceStatus:
    @pytest.mark.asyncio
    async def test_success_authenticated(self):
        from app.api.routes.toolsets import get_instance_status

        request = MagicMock()

        config_service = AsyncMock()

        async def mock_get_config(path, default=None, use_cache=True):
            if "toolset-instances" in path:
                return [{"_id": "i1", "orgId": "o1", "instanceName": "My Jira", "toolsetType": "jira", "authType": "OAUTH"}]
            if "toolsets/i1/u1" in path:
                return {"isAuthenticated": True}
            return default

        config_service.get_config = mock_get_config

        with patch("app.api.routes.toolsets._get_user_context", return_value={"user_id": "u1", "org_id": "o1"}):
            result = await get_instance_status("i1", request, config_service=config_service)
            assert result["status"] == "success"
            assert result["isAuthenticated"] is True

    @pytest.mark.asyncio
    async def test_not_found(self):
        from app.api.routes.toolsets import get_instance_status

        request = MagicMock()
        config_service = AsyncMock()
        config_service.get_config = AsyncMock(return_value=[])

        with patch("app.api.routes.toolsets._get_user_context", return_value={"user_id": "u1", "org_id": "o1"}):
            with pytest.raises(HTTPException) as exc:
                await get_instance_status("missing", request, config_service=config_service)
            assert exc.value.status_code == 404


# ===========================================================================
# Route handler tests — OAuth Callback
# ===========================================================================


class TestHandleToolsetOAuthCallback:
    @pytest.mark.asyncio
    async def test_error_param(self):
        from app.api.routes.toolsets import handle_toolset_oauth_callback

        request = MagicMock()
        config_service = AsyncMock()

        result = await handle_toolset_oauth_callback(
            request, code=None, state=None, error="access_denied",
            config_service=config_service,
        )
        assert result["success"] is False
        assert "access_denied" in result["error"]

    @pytest.mark.asyncio
    async def test_missing_code(self):
        from app.api.routes.toolsets import handle_toolset_oauth_callback

        request = MagicMock()
        config_service = AsyncMock()

        result = await handle_toolset_oauth_callback(
            request, code=None, state="some-state", error=None,
            config_service=config_service,
        )
        assert result["success"] is False
        assert "missing_parameters" in result["error"]

    @pytest.mark.asyncio
    async def test_missing_state(self):
        from app.api.routes.toolsets import handle_toolset_oauth_callback

        request = MagicMock()
        config_service = AsyncMock()

        result = await handle_toolset_oauth_callback(
            request, code="auth-code", state=None, error=None,
            config_service=config_service,
        )
        assert result["success"] is False


# ===========================================================================
# Route handler tests — Update Toolset Instance
# ===========================================================================


class TestUpdateToolsetInstance:
    @pytest.mark.asyncio
    async def test_non_admin_rejected(self):
        from app.api.routes.toolsets import update_toolset_instance

        request = MagicMock()
        config_service = AsyncMock()

        with patch("app.api.routes.toolsets._get_user_context", return_value={"user_id": "u1", "org_id": "o1"}), \
             patch("app.api.routes.toolsets._check_user_is_admin", new_callable=AsyncMock, return_value=False):

            with pytest.raises(HTTPException) as exc:
                await update_toolset_instance("i1", request, config_service=config_service)
            assert exc.value.status_code == 403

    @pytest.mark.asyncio
    async def test_not_found(self):
        from app.api.routes.toolsets import update_toolset_instance

        request = MagicMock()
        request.body = AsyncMock(return_value=b'{"instanceName":"New Name"}')

        config_service = AsyncMock()
        config_service.get_config = AsyncMock(return_value=[])

        with patch("app.api.routes.toolsets._get_user_context", return_value={"user_id": "u1", "org_id": "o1"}), \
             patch("app.api.routes.toolsets._check_user_is_admin", new_callable=AsyncMock, return_value=True), \
             patch("app.api.routes.toolsets._parse_request_json", return_value={"instanceName": "New Name"}):

            with pytest.raises(HTTPException) as exc:
                await update_toolset_instance("missing", request, config_service=config_service)
            assert exc.value.status_code == 404

    @pytest.mark.asyncio
    async def test_rename_success(self):
        from app.api.routes.toolsets import update_toolset_instance

        request = MagicMock()
        request.body = AsyncMock(return_value=b'{"instanceName":"New Name"}')

        config_service = AsyncMock()
        config_service.get_config = AsyncMock(return_value=[
            {"_id": "i1", "orgId": "o1", "toolsetType": "jira", "instanceName": "Old Name", "authType": "API_TOKEN"},
        ])
        config_service.set_config = AsyncMock()

        with patch("app.api.routes.toolsets._get_user_context", return_value={"user_id": "u1", "org_id": "o1"}), \
             patch("app.api.routes.toolsets._check_user_is_admin", new_callable=AsyncMock, return_value=True), \
             patch("app.api.routes.toolsets._parse_request_json", return_value={"instanceName": "New Name"}):

            result = await update_toolset_instance("i1", request, config_service=config_service)
            assert result["status"] == "success"
            assert result["instance"]["instanceName"] == "New Name"


# ===========================================================================
# Route handler tests — My Toolsets with registry
# ===========================================================================


class TestGetMyToolsetsWithRegistry:
    @pytest.mark.asyncio
    async def test_include_registry(self):
        from app.api.routes.toolsets import get_my_toolsets

        request = MagicMock()
        registry = MagicMock()
        registry.list_toolsets.return_value = ["jira", "slack"]

        def mock_meta(name):
            return {
                "name": name, "display_name": name.title(), "description": "",
                "icon_path": "", "category": "app",
                "supported_auth_types": ["OAUTH"], "tools": [{"name": "search"}],
            }

        registry.get_toolset_metadata.side_effect = mock_meta
        request.app.state.toolset_registry = registry

        config_service = AsyncMock()

        async def mock_get_config(path, default=None, use_cache=True):
            if "toolset-instances" in path:
                return [{"_id": "i1", "orgId": "o1", "toolsetType": "jira", "instanceName": "My Jira", "authType": "OAUTH"}]
            return default

        config_service.get_config = mock_get_config

        with patch("app.api.routes.toolsets._get_user_context", return_value={"user_id": "u1", "org_id": "o1"}):
            result = await get_my_toolsets(request, search=None, page=1, limit=20, include_registry=True, auth_status=None, config_service=config_service)
            assert result["status"] == "success"
            # Should have jira from instances + slack from registry
            types = [t["toolsetType"] for t in result["toolsets"]]
            assert "jira" in types
            assert "slack" in types


# ===========================================================================
# Route handler tests — Delete Toolset Instance with credentials
# ===========================================================================


class TestDeleteToolsetInstanceWithCredentials:
    @pytest.mark.asyncio
    async def test_delete_with_user_credentials(self):
        from app.api.routes.toolsets import delete_toolset_instance

        request = MagicMock()
        graph_provider = MagicMock()
        graph_provider.check_toolset_instance_in_use = AsyncMock(return_value=[])
        request.app.state.graph_provider = graph_provider

        config_service = AsyncMock()
        config_service.get_config = AsyncMock(return_value=[
            {"_id": "i1", "orgId": "o1", "instanceName": "My Jira"},
        ])
        config_service.set_config = AsyncMock()
        config_service.list_keys_in_directory = AsyncMock(return_value=[
            "/services/toolsets/i1/user-1",
            "/services/toolsets/i1/user-2",
        ])
        config_service.delete_config = AsyncMock(return_value=True)

        with patch("app.api.routes.toolsets._get_user_context", return_value={"user_id": "u1", "org_id": "o1"}), \
             patch("app.api.routes.toolsets._check_user_is_admin", new_callable=AsyncMock, return_value=True):

            result = await delete_toolset_instance("i1", request, config_service=config_service)
            assert result["status"] == "success"
            assert result["deletedCredentialsCount"] == 2

    @pytest.mark.asyncio
    async def test_graph_check_non_list_returns_error(self):
        from app.api.routes.toolsets import delete_toolset_instance

        request = MagicMock()
        graph_provider = MagicMock()
        graph_provider.check_toolset_instance_in_use = AsyncMock(return_value="not a list")
        request.app.state.graph_provider = graph_provider

        config_service = AsyncMock()
        config_service.get_config = AsyncMock(return_value=[
            {"_id": "i1", "orgId": "o1", "instanceName": "My Jira"},
        ])

        with patch("app.api.routes.toolsets._get_user_context", return_value={"user_id": "u1", "org_id": "o1"}), \
             patch("app.api.routes.toolsets._check_user_is_admin", new_callable=AsyncMock, return_value=True):

            with pytest.raises(HTTPException) as exc:
                await delete_toolset_instance("i1", request, config_service=config_service)
            assert exc.value.status_code == 500

    @pytest.mark.asyncio
    async def test_graph_check_exception_blocks_delete(self):
        from app.api.routes.toolsets import delete_toolset_instance

        request = MagicMock()
        graph_provider = MagicMock()
        graph_provider.check_toolset_instance_in_use = AsyncMock(side_effect=RuntimeError("db error"))
        request.app.state.graph_provider = graph_provider

        config_service = AsyncMock()
        config_service.get_config = AsyncMock(return_value=[
            {"_id": "i1", "orgId": "o1", "instanceName": "My Jira"},
        ])

        with patch("app.api.routes.toolsets._get_user_context", return_value={"user_id": "u1", "org_id": "o1"}), \
             patch("app.api.routes.toolsets._check_user_is_admin", new_callable=AsyncMock, return_value=True):

            with pytest.raises(HTTPException) as exc:
                await delete_toolset_instance("i1", request, config_service=config_service)
            assert exc.value.status_code == 500


# ===========================================================================
# Route handler tests — Update OAuth Config
# ===========================================================================


class TestUpdateToolsetOAuthConfig:
    @pytest.mark.asyncio
    async def test_non_admin_rejected(self):
        from app.api.routes.toolsets import update_toolset_oauth_config

        request = MagicMock()
        config_service = AsyncMock()

        with patch("app.api.routes.toolsets._get_user_context", return_value={"user_id": "u1", "org_id": "o1"}), \
             patch("app.api.routes.toolsets._check_user_is_admin", new_callable=AsyncMock, return_value=False):

            with pytest.raises(HTTPException) as exc:
                await update_toolset_oauth_config("jira", "cfg-1", request, config_service=config_service)
            assert exc.value.status_code == 403

    @pytest.mark.asyncio
    async def test_not_found(self):
        from app.api.routes.toolsets import update_toolset_oauth_config

        request = MagicMock()
        request.body = AsyncMock(return_value=b'{"authConfig":{"clientId":"cid"}}')

        config_service = AsyncMock()
        config_service.get_config = AsyncMock(return_value=[])

        with patch("app.api.routes.toolsets._get_user_context", return_value={"user_id": "u1", "org_id": "o1"}), \
             patch("app.api.routes.toolsets._check_user_is_admin", new_callable=AsyncMock, return_value=True), \
             patch("app.api.routes.toolsets._parse_request_json", return_value={"authConfig": {"clientId": "cid"}}):

            with pytest.raises(HTTPException) as exc:
                await update_toolset_oauth_config("jira", "missing", request, config_service=config_service)
            assert exc.value.status_code == 404

    @pytest.mark.asyncio
    async def test_success_with_deauth(self):
        from app.api.routes.toolsets import update_toolset_oauth_config

        request = MagicMock()
        request.body = AsyncMock(return_value=b'{"authConfig":{"clientId":"cid","clientSecret":"cs"}}')
        registry = MagicMock()
        mock_scopes = MagicMock()
        mock_scopes.get_scopes_for_type.return_value = []
        mock_oauth = MagicMock()
        mock_oauth.authorize_url = "https://auth.example.com/authorize"
        mock_oauth.token_url = "https://auth.example.com/token"
        mock_oauth.redirect_uri = "/cb"
        mock_oauth.scopes = mock_scopes
        mock_oauth.additional_params = None
        mock_oauth.token_access_type = None
        mock_oauth.scope_parameter_name = "scope"
        mock_oauth.token_response_path = None
        registry.get_toolset_metadata.return_value = {
            "config": {"_oauth_configs": {"OAUTH": mock_oauth}},
        }
        request.app.state.toolset_registry = registry

        config_service = AsyncMock()

        call_count = [0]

        async def mock_get_config(path, default=None, use_cache=True):
            if "oauths" in path:
                call_count[0] += 1
                if call_count[0] == 1:
                    # First call: _get_oauth_config_by_id
                    return [{"_id": "cfg-1", "orgId": "o1", "oauthInstanceName": "My Config", "config": {"clientId": "old"}}]
                else:
                    # Subsequent calls: _get_oauth_configs_for_type
                    return [{"_id": "cfg-1", "orgId": "o1", "oauthInstanceName": "My Config", "config": {"clientId": "old"}}]
            if "instances" in path:
                return [{"_id": "i1", "orgId": "o1", "oauthConfigId": "cfg-1"}]
            if "endpoints" in path:
                return {"frontend": {"publicEndpoint": "http://localhost:3001"}}
            return default

        config_service.get_config = mock_get_config
        config_service.set_config = AsyncMock()
        config_service.list_keys_in_directory = AsyncMock(return_value=[])

        with patch("app.api.routes.toolsets._get_user_context", return_value={"user_id": "u1", "org_id": "o1"}), \
             patch("app.api.routes.toolsets._check_user_is_admin", new_callable=AsyncMock, return_value=True), \
             patch("app.api.routes.toolsets._parse_request_json", return_value={"authConfig": {"clientId": "cid", "clientSecret": "cs"}}):

            result = await update_toolset_oauth_config("jira", "cfg-1", request, config_service=config_service)
            assert result["status"] == "success"


# ===========================================================================
# Route handler tests — Configured Toolsets (backward compat)
# ===========================================================================


class TestGetConfiguredToolsets:
    @pytest.mark.asyncio
    async def test_delegates_to_my_toolsets(self):
        from app.api.routes.toolsets import get_configured_toolsets

        request = MagicMock()
        config_service = AsyncMock()
        config_service.get_config = AsyncMock(return_value=[])

        with patch("app.api.routes.toolsets._get_user_context", return_value={"user_id": "u1", "org_id": "o1"}), \
             patch("app.api.routes.toolsets.get_my_toolsets", new_callable=AsyncMock, return_value={"status": "success", "toolsets": []}) as mock_my:

            result = await get_configured_toolsets(request, config_service=config_service)
            assert result["status"] == "success"
            mock_my.assert_awaited_once()


# ===========================================================================
# Route handler tests — Authenticate with BASIC_AUTH
# ===========================================================================


class TestAuthenticateBasicAuth:
    @pytest.mark.asyncio
    async def test_basic_auth_success(self):
        from app.api.routes.toolsets import authenticate_toolset_instance

        request = MagicMock()
        request.body = AsyncMock(return_value=json.dumps({"auth": {"username": "admin", "password": "pass123"}}).encode())

        config_service = AsyncMock()
        config_service.get_config = AsyncMock(return_value=[
            {"_id": "i1", "orgId": "o1", "toolsetType": "jira", "authType": "BASIC_AUTH"},
        ])
        config_service.set_config = AsyncMock()

        with patch("app.api.routes.toolsets._get_user_context", return_value={"user_id": "u1", "org_id": "o1"}), \
             patch("app.api.routes.toolsets._parse_request_json", return_value={"auth": {"username": "admin", "password": "pass123"}}):

            result = await authenticate_toolset_instance("i1", request, config_service=config_service)
            assert result["status"] == "success"

    @pytest.mark.asyncio
    async def test_empty_credentials_rejected(self):
        from app.api.routes.toolsets import authenticate_toolset_instance

        request = MagicMock()
        request.body = AsyncMock(return_value=b'{}')

        config_service = AsyncMock()
        config_service.get_config = AsyncMock(return_value=[
            {"_id": "i1", "orgId": "o1", "authType": "API_TOKEN"},
        ])

        with patch("app.api.routes.toolsets._get_user_context", return_value={"user_id": "u1", "org_id": "o1"}), \
             patch("app.api.routes.toolsets._parse_request_json", return_value={}):

            with pytest.raises(HTTPException) as exc:
                await authenticate_toolset_instance("i1", request, config_service=config_service)
            assert exc.value.status_code == 400


# ===========================================================================
# Route handler tests — Create Toolset Instance (full body)
# ===========================================================================


class TestCreateToolsetInstanceFull:
    @pytest.mark.asyncio
    async def test_success_with_api_token(self):
        from app.api.routes.toolsets import create_toolset_instance

        request = MagicMock()
        request.body = AsyncMock(return_value=json.dumps({
            "instanceName": "My Jira",
            "toolsetType": "jira",
            "authType": "API_TOKEN",
        }).encode())
        registry = MagicMock()
        registry.get_toolset_metadata.return_value = {
            "name": "jira", "display_name": "Jira",
            "supported_auth_types": ["API_TOKEN", "OAUTH"],
            "tools": [],
        }
        request.app.state.toolset_registry = registry

        config_service = AsyncMock()
        config_service.get_config = AsyncMock(return_value=[])
        config_service.set_config = AsyncMock()

        with patch("app.api.routes.toolsets._get_user_context", return_value={"user_id": "u1", "org_id": "o1"}), \
             patch("app.api.routes.toolsets._check_user_is_admin", new_callable=AsyncMock, return_value=True), \
             patch("app.api.routes.toolsets._parse_request_json", return_value={
                 "instanceName": "My Jira", "toolsetType": "jira", "authType": "API_TOKEN",
             }):

            result = await create_toolset_instance(request, config_service=config_service)
            assert result["status"] == "success"
            assert result["instance"]["instanceName"] == "My Jira"

    @pytest.mark.asyncio
    async def test_duplicate_name_rejected(self):
        from app.api.routes.toolsets import create_toolset_instance

        request = MagicMock()
        request.body = AsyncMock(return_value=b'{}')
        registry = MagicMock()
        registry.get_toolset_metadata.return_value = {
            "name": "jira", "supported_auth_types": ["API_TOKEN"], "tools": [],
        }
        request.app.state.toolset_registry = registry

        config_service = AsyncMock()
        config_service.get_config = AsyncMock(return_value=[
            {"_id": "existing", "orgId": "o1", "toolsetType": "jira", "instanceName": "My Jira"},
        ])

        with patch("app.api.routes.toolsets._get_user_context", return_value={"user_id": "u1", "org_id": "o1"}), \
             patch("app.api.routes.toolsets._check_user_is_admin", new_callable=AsyncMock, return_value=True), \
             patch("app.api.routes.toolsets._parse_request_json", return_value={
                 "instanceName": "My Jira", "toolsetType": "jira", "authType": "API_TOKEN",
             }):

            with pytest.raises(HTTPException) as exc:
                await create_toolset_instance(request, config_service=config_service)
            assert exc.value.status_code == 409

    @pytest.mark.asyncio
    async def test_unsupported_auth_type_rejected(self):
        from app.api.routes.toolsets import create_toolset_instance

        request = MagicMock()
        request.body = AsyncMock(return_value=b'{}')
        registry = MagicMock()
        registry.get_toolset_metadata.return_value = {
            "name": "jira", "supported_auth_types": ["OAUTH"],
            "tools": [],
        }
        request.app.state.toolset_registry = registry

        config_service = AsyncMock()
        config_service.get_config = AsyncMock(return_value=[])

        with patch("app.api.routes.toolsets._get_user_context", return_value={"user_id": "u1", "org_id": "o1"}), \
             patch("app.api.routes.toolsets._check_user_is_admin", new_callable=AsyncMock, return_value=True), \
             patch("app.api.routes.toolsets._parse_request_json", return_value={
                 "instanceName": "My Jira", "toolsetType": "jira", "authType": "BASIC_AUTH",
             }):

            with pytest.raises(HTTPException) as exc:
                await create_toolset_instance(request, config_service=config_service)
            assert exc.value.status_code == 400

    @pytest.mark.asyncio
    async def test_oauth_with_existing_config_id(self):
        from app.api.routes.toolsets import create_toolset_instance

        request = MagicMock()
        request.body = AsyncMock(return_value=b'{}')
        registry = MagicMock()
        registry.get_toolset_metadata.return_value = {
            "name": "jira", "supported_auth_types": ["OAUTH"], "tools": [],
        }
        request.app.state.toolset_registry = registry

        config_service = AsyncMock()

        async def mock_get_config(path, default=None, use_cache=True):
            if "oauths" in path:
                return [{"_id": "cfg-1", "orgId": "o1"}]
            if "instances" in path:
                return []
            if "endpoints" in path:
                return {"frontend": {"publicEndpoint": "http://localhost:3001"}}
            return default

        config_service.get_config = mock_get_config
        config_service.set_config = AsyncMock()

        with patch("app.api.routes.toolsets._get_user_context", return_value={"user_id": "u1", "org_id": "o1"}), \
             patch("app.api.routes.toolsets._check_user_is_admin", new_callable=AsyncMock, return_value=True), \
             patch("app.api.routes.toolsets._parse_request_json", return_value={
                 "instanceName": "My Jira", "toolsetType": "jira", "authType": "OAUTH",
                 "oauthConfigId": "cfg-1",
             }):

            result = await create_toolset_instance(request, config_service=config_service)
            assert result["status"] == "success"
            assert result["instance"].get("oauthConfigId") == "cfg-1"

    @pytest.mark.asyncio
    async def test_oauth_with_new_credentials(self):
        from app.api.routes.toolsets import create_toolset_instance

        request = MagicMock()
        request.body = AsyncMock(return_value=b'{}')
        registry = MagicMock()
        mock_scopes = MagicMock()
        mock_scopes.get_scopes_for_type.return_value = []
        mock_oauth = MagicMock()
        mock_oauth.authorize_url = "https://auth.example.com/authorize"
        mock_oauth.token_url = "https://auth.example.com/token"
        mock_oauth.redirect_uri = "/cb"
        mock_oauth.scopes = mock_scopes
        mock_oauth.additional_params = None
        mock_oauth.token_access_type = None
        mock_oauth.scope_parameter_name = "scope"
        mock_oauth.token_response_path = None
        registry.get_toolset_metadata.side_effect = lambda name, serialize=True: {
            "name": "jira", "display_name": "Jira",
            "supported_auth_types": ["OAUTH"], "tools": [],
            "config": {"_oauth_configs": {"OAUTH": mock_oauth}},
        }
        request.app.state.toolset_registry = registry

        config_service = AsyncMock()

        async def mock_get_config(path, default=None, use_cache=True):
            if "oauths" in path:
                return []
            if "instances" in path:
                return []
            if "endpoints" in path:
                return {"frontend": {"publicEndpoint": "http://localhost:3001"}}
            return default

        config_service.get_config = mock_get_config
        config_service.set_config = AsyncMock()

        with patch("app.api.routes.toolsets._get_user_context", return_value={"user_id": "u1", "org_id": "o1"}), \
             patch("app.api.routes.toolsets._check_user_is_admin", new_callable=AsyncMock, return_value=True), \
             patch("app.api.routes.toolsets._parse_request_json", return_value={
                 "instanceName": "My Jira", "toolsetType": "jira", "authType": "OAUTH",
                 "authConfig": {"clientId": "cid", "clientSecret": "cs"},
             }):

            result = await create_toolset_instance(request, config_service=config_service)
            assert result["status"] == "success"


# ===========================================================================
# Route handler tests — Update Toolset Instance with OAuth
# ===========================================================================


class TestUpdateToolsetInstanceOAuth:
    @pytest.mark.asyncio
    async def test_oauth_config_switch(self):
        from app.api.routes.toolsets import update_toolset_instance

        request = MagicMock()
        request.body = AsyncMock(return_value=b'{"oauthConfigId":"cfg-2"}')

        config_service = AsyncMock()

        async def mock_get_config(path, default=None, use_cache=True):
            if "instances" in path:
                return [{"_id": "i1", "orgId": "o1", "toolsetType": "jira", "instanceName": "My Jira", "authType": "OAUTH", "oauthConfigId": "cfg-1"}]
            if "oauths" in path:
                return [{"_id": "cfg-2", "orgId": "o1"}]
            if "endpoints" in path:
                return {"frontend": {"publicEndpoint": "http://localhost:3001"}}
            return default

        config_service.get_config = mock_get_config
        config_service.set_config = AsyncMock()
        config_service.list_keys_in_directory = AsyncMock(return_value=[])

        with patch("app.api.routes.toolsets._get_user_context", return_value={"user_id": "u1", "org_id": "o1"}), \
             patch("app.api.routes.toolsets._check_user_is_admin", new_callable=AsyncMock, return_value=True), \
             patch("app.api.routes.toolsets._parse_request_json", return_value={"oauthConfigId": "cfg-2"}):

            result = await update_toolset_instance("i1", request, config_service=config_service)
            assert result["status"] == "success"
            assert result["instance"]["oauthConfigId"] == "cfg-2"

    @pytest.mark.asyncio
    async def test_name_conflict_on_rename(self):
        from app.api.routes.toolsets import update_toolset_instance

        request = MagicMock()
        request.body = AsyncMock(return_value=b'{"instanceName":"Existing Jira"}')

        config_service = AsyncMock()
        config_service.get_config = AsyncMock(return_value=[
            {"_id": "i1", "orgId": "o1", "toolsetType": "jira", "instanceName": "My Jira", "authType": "API_TOKEN"},
            {"_id": "i2", "orgId": "o1", "toolsetType": "jira", "instanceName": "Existing Jira", "authType": "API_TOKEN"},
        ])

        with patch("app.api.routes.toolsets._get_user_context", return_value={"user_id": "u1", "org_id": "o1"}), \
             patch("app.api.routes.toolsets._check_user_is_admin", new_callable=AsyncMock, return_value=True), \
             patch("app.api.routes.toolsets._parse_request_json", return_value={"instanceName": "Existing Jira"}):

            with pytest.raises(HTTPException) as exc:
                await update_toolset_instance("i1", request, config_service=config_service)
            assert exc.value.status_code == 409
