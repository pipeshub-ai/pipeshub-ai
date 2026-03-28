"""Tests for the second half of app/connectors/api/router.py (lines 3500-6675).

Covers:
  - Helper functions: _get_user_context, _validate_admin_only,
    _validate_connector_permissions, _get_and_validate_connector_instance,
    _find_oauth_config_in_list, _check_oauth_name_conflict,
    _update_oauth_infrastructure_fields, _build_oauth_flow_config
  - Route handlers: get_oauth_authorization_url, handle_oauth_callback,
    filter endpoints, toggle, delete, schema, agents, OAuth CRUD
"""

import copy
import logging
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from app.connectors.api.router import (  # noqa: E402
    _check_oauth_name_conflict,
    _clean_schema_for_response,
    _extract_essential_oauth_fields,
    _find_filter_field_config,
    _find_oauth_config_by_id,
    _find_oauth_config_in_list,
    _generate_oauth_config_id,
    _get_and_validate_connector_instance,
    _get_connector_filter_options_from_config,
    _get_connector_from_container,
    _get_fallback_filter_options,
    _get_oauth_config_path,
    _get_oauth_field_names_from_registry,
    _get_static_filter_options,
    _get_user_context,
    _parse_filter_response,
    _validate_admin_only,
    _validate_connector_permissions,
)

_OAUTH_REGISTRY_PATCH = (
    "app.connectors.core.registry.oauth_config_registry.get_oauth_config_registry"
)

# ---------------------------------------------------------------------------
# Shared helpers / fixtures
# ---------------------------------------------------------------------------

def _make_request(
    user_id: str = "u1",
    org_id: str = "o1",
    is_admin: bool = False,
    body: dict | None = None,
):
    """Build a minimal mock Request object used by most handler tests."""
    req = MagicMock()
    req.state.user = {"userId": user_id, "orgId": org_id}
    req.headers = {"X-Is-Admin": "true" if is_admin else "false"}
    if body is not None:
        req.json = AsyncMock(return_value=body)
    else:
        req.json = AsyncMock(return_value={})

    # Container setup
    container = MagicMock()
    container.logger.return_value = logging.getLogger("test")
    container.config_service.return_value = AsyncMock()
    container.messaging_producer = AsyncMock()
    container.messaging_producer.send_message = AsyncMock()

    req.app.container = container
    req.app.state.connector_registry = AsyncMock()
    req.app.state.graph_provider = AsyncMock()
    return req


def _make_instance(
    connector_id: str = "c1",
    connector_type: str = "GMAIL",
    scope: str = "team",
    created_by: str = "u1",
    auth_type: str = "OAUTH",
    is_active: bool = False,
    is_configured: bool = True,
    is_authenticated: bool = True,
    extra: dict | None = None,
) -> dict[str, Any]:
    """Helper to build a connector instance dict."""
    inst = {
        "_key": connector_id,
        "type": connector_type,
        "scope": scope,
        "createdBy": created_by,
        "authType": auth_type,
        "isActive": is_active,
        "isConfigured": is_configured,
        "isAuthenticated": is_authenticated,
        "name": "My connector",
        "appGroup": "google",
        "appGroupId": "ag1",
    }
    if extra:
        inst.update(extra)
    return inst


# ===========================================================================
# _get_user_context
# ===========================================================================

class TestGetUserContext:
    """Tests for _get_user_context helper."""

    def test_returns_context_for_regular_user(self):
        req = _make_request(user_id="u1", org_id="o1", is_admin=False)
        ctx = _get_user_context(req)
        assert ctx == {"user_id": "u1", "org_id": "o1", "is_admin": False}

    def test_returns_context_for_admin(self):
        req = _make_request(user_id="a1", org_id="o2", is_admin=True)
        ctx = _get_user_context(req)
        assert ctx == {"user_id": "a1", "org_id": "o2", "is_admin": True}

    def test_missing_user_id_raises_401(self):
        req = _make_request()
        req.state.user = {"userId": None, "orgId": "o1"}
        with pytest.raises(HTTPException) as exc_info:
            _get_user_context(req)
        assert exc_info.value.status_code == 401

    def test_missing_org_id_raises_401(self):
        req = _make_request()
        req.state.user = {"userId": "u1", "orgId": ""}
        with pytest.raises(HTTPException) as exc_info:
            _get_user_context(req)
        assert exc_info.value.status_code == 401

    def test_both_missing_raises_401(self):
        req = _make_request()
        req.state.user = {"userId": "", "orgId": ""}
        with pytest.raises(HTTPException) as exc_info:
            _get_user_context(req)
        assert exc_info.value.status_code == 401

    def test_is_admin_header_case_insensitive(self):
        req = _make_request()
        req.headers = {"X-Is-Admin": "TRUE"}
        ctx = _get_user_context(req)
        assert ctx["is_admin"] is True

    def test_is_admin_default_false(self):
        req = _make_request()
        req.headers = {"X-Is-Admin": "false"}
        ctx = _get_user_context(req)
        assert ctx["is_admin"] is False


# ===========================================================================
# _validate_admin_only
# ===========================================================================

class TestValidateAdminOnly:
    """Tests for _validate_admin_only helper."""

    def test_admin_passes(self):
        # Should not raise
        _validate_admin_only(is_admin=True, action="delete stuff")

    def test_non_admin_raises_403(self):
        with pytest.raises(HTTPException) as exc_info:
            _validate_admin_only(is_admin=False, action="delete stuff")
        assert exc_info.value.status_code == 403
        assert "delete stuff" in exc_info.value.detail

    def test_default_action_text(self):
        with pytest.raises(HTTPException) as exc_info:
            _validate_admin_only(is_admin=False)
        assert "perform this action" in exc_info.value.detail


# ===========================================================================
# _validate_connector_permissions
# ===========================================================================

class TestValidateConnectorPermissions:
    """Tests for _validate_connector_permissions helper."""

    def test_team_connector_admin_passes(self):
        inst = _make_instance(scope="team", created_by="other")
        _validate_connector_permissions(inst, "u1", is_admin=True, action="access")

    def test_team_connector_non_admin_raises(self):
        inst = _make_instance(scope="team", created_by="u1")
        with pytest.raises(HTTPException) as exc_info:
            _validate_connector_permissions(inst, "u1", is_admin=False, action="access")
        assert exc_info.value.status_code == 403
        assert "team connectors" in exc_info.value.detail

    def test_personal_connector_creator_passes(self):
        inst = _make_instance(scope="personal", created_by="u1")
        _validate_connector_permissions(inst, "u1", is_admin=False, action="access")

    def test_personal_connector_admin_passes(self):
        inst = _make_instance(scope="personal", created_by="other")
        _validate_connector_permissions(inst, "admin1", is_admin=True, action="access")

    def test_personal_connector_non_creator_non_admin_raises(self):
        inst = _make_instance(scope="personal", created_by="other_user")
        with pytest.raises(HTTPException) as exc_info:
            _validate_connector_permissions(inst, "u1", is_admin=False, action="delete")
        assert exc_info.value.status_code == 403

    def test_unknown_scope_creator_passes(self):
        inst = _make_instance(scope="custom")
        inst["createdBy"] = "u1"
        _validate_connector_permissions(inst, "u1", is_admin=False, action="access")

    def test_unknown_scope_non_creator_non_admin_raises(self):
        inst = _make_instance(scope="custom")
        inst["createdBy"] = "other"
        with pytest.raises(HTTPException) as exc_info:
            _validate_connector_permissions(inst, "u1", is_admin=False, action="access")
        assert exc_info.value.status_code == 403

    def test_unknown_scope_admin_passes(self):
        inst = _make_instance(scope="custom")
        inst["createdBy"] = "other"
        _validate_connector_permissions(inst, "u1", is_admin=True, action="access")


# ===========================================================================
# _get_and_validate_connector_instance
# ===========================================================================

class TestGetAndValidateConnectorInstance:
    """Tests for _get_and_validate_connector_instance helper."""

    async def test_returns_instance(self):
        registry = AsyncMock()
        registry.get_connector_instance.return_value = {"_key": "c1", "type": "GMAIL"}
        ctx = {"user_id": "u1", "org_id": "o1", "is_admin": False}
        result = await _get_and_validate_connector_instance(
            "c1", ctx, registry, logging.getLogger("test")
        )
        assert result["_key"] == "c1"

    async def test_not_found_raises_404(self):
        from app.connectors.api.router import _get_and_validate_connector_instance

        registry = AsyncMock()
        registry.get_connector_instance.return_value = None
        ctx = {"user_id": "u1", "org_id": "o1", "is_admin": False}
        with pytest.raises(HTTPException) as exc_info:
            await _get_and_validate_connector_instance(
                "c1", ctx, registry, logging.getLogger("test")
            )
        assert exc_info.value.status_code == 404


# ===========================================================================
# _find_oauth_config_in_list
# ===========================================================================

class TestFindOAuthConfigInList:
    """Tests for _find_oauth_config_in_list helper."""

    async def test_finds_matching_config(self):
        configs = [
            {"_id": "cfg1", "orgId": "o1"},
            {"_id": "cfg2", "orgId": "o1"},
        ]
        cfg, idx = await _find_oauth_config_in_list(
            configs, "cfg2", "o1", logging.getLogger("test")
        )
        assert cfg == configs[1]
        assert idx == 1

    async def test_returns_none_when_not_found(self):
        configs = [{"_id": "cfg1", "orgId": "o1"}]
        cfg, idx = await _find_oauth_config_in_list(
            configs, "missing", "o1", logging.getLogger("test")
        )
        assert cfg is None
        assert idx is None

    async def test_returns_none_for_wrong_org(self):
        configs = [{"_id": "cfg1", "orgId": "o2"}]
        cfg, idx = await _find_oauth_config_in_list(
            configs, "cfg1", "o1", logging.getLogger("test")
        )
        assert cfg is None
        assert idx is None

    async def test_empty_list(self):
        cfg, idx = await _find_oauth_config_in_list(
            [], "cfg1", "o1", logging.getLogger("test")
        )
        assert cfg is None
        assert idx is None


# ===========================================================================
# _check_oauth_name_conflict
# ===========================================================================

class TestCheckOAuthNameConflict:
    """Tests for _check_oauth_name_conflict helper."""

    def test_no_conflict(self):
        configs = [
            {"oauthInstanceName": "config-A", "orgId": "o1"},
            {"oauthInstanceName": "config-B", "orgId": "o1"},
        ]
        # Should not raise
        _check_oauth_name_conflict(configs, "config-C", "o1")

    def test_conflict_raises_409(self):
        configs = [
            {"oauthInstanceName": "dup-name", "orgId": "o1"},
        ]
        with pytest.raises(HTTPException) as exc_info:
            _check_oauth_name_conflict(configs, "dup-name", "o1")
        assert exc_info.value.status_code == 409

    def test_same_name_different_org_no_conflict(self):
        configs = [{"oauthInstanceName": "shared-name", "orgId": "o2"}]
        _check_oauth_name_conflict(configs, "shared-name", "o1")

    def test_exclude_index_skips_self(self):
        configs = [
            {"oauthInstanceName": "my-name", "orgId": "o1"},
            {"oauthInstanceName": "other", "orgId": "o1"},
        ]
        # Exclude index 0 — shouldn't conflict with itself
        _check_oauth_name_conflict(configs, "my-name", "o1", exclude_index=0)

    def test_conflict_with_same_name_at_other_index(self):
        configs = [
            {"oauthInstanceName": "name-A", "orgId": "o1"},
            {"oauthInstanceName": "name-A", "orgId": "o1"},
        ]
        with pytest.raises(HTTPException) as exc_info:
            _check_oauth_name_conflict(configs, "name-A", "o1", exclude_index=0)
        assert exc_info.value.status_code == 409

    def test_empty_configs(self):
        _check_oauth_name_conflict([], "anything", "o1")


# ===========================================================================
# _update_oauth_infrastructure_fields
# ===========================================================================

class TestUpdateOAuthInfrastructureFields:
    """Tests for _update_oauth_infrastructure_fields helper."""

    async def test_adds_missing_fields_from_registry(self):
        from app.connectors.api.router import _update_oauth_infrastructure_fields

        mock_scopes = MagicMock()
        mock_scopes.to_dict.return_value = {"team_sync": ["scope1"]}

        mock_registry_config = MagicMock()
        mock_registry_config.authorize_url = "https://auth.example.com"
        mock_registry_config.token_url = "https://token.example.com"
        mock_registry_config.redirect_uri = "oauth/callback"
        mock_registry_config.scopes = mock_scopes
        mock_registry_config.token_access_type = "offline"
        mock_registry_config.additional_params = {"prompt": "consent"}
        mock_registry_config.icon_path = "/icon.svg"
        mock_registry_config.app_group = "Google"
        mock_registry_config.app_description = "Google Drive"
        mock_registry_config.app_categories = ["storage"]

        mock_registry = MagicMock()
        mock_registry.get_config.return_value = mock_registry_config

        config_service = AsyncMock()
        oauth_config: dict[str, Any] = {}

        with patch(
            _OAUTH_REGISTRY_PATCH,
            return_value=mock_registry,
        ):
            await _update_oauth_infrastructure_fields(
                oauth_config, "DRIVE", config_service, "https://example.com"
            )

        assert oauth_config["authorizeUrl"] == "https://auth.example.com"
        assert oauth_config["tokenUrl"] == "https://token.example.com"
        assert oauth_config["redirectUri"] == "https://example.com/oauth/callback"
        assert oauth_config["scopes"] == {"team_sync": ["scope1"]}
        assert oauth_config["tokenAccessType"] == "offline"
        assert oauth_config["additionalParams"] == {"prompt": "consent"}
        assert oauth_config["iconPath"] == "/icon.svg"
        assert oauth_config["appGroup"] == "Google"
        assert oauth_config["appDescription"] == "Google Drive"
        assert oauth_config["appCategories"] == ["storage"]

    async def test_preserves_existing_fields(self):
        from app.connectors.api.router import _update_oauth_infrastructure_fields

        mock_registry_config = MagicMock()
        mock_registry_config.authorize_url = "https://new-auth.example.com"
        mock_registry_config.token_url = "https://new-token.example.com"
        mock_registry = MagicMock()
        mock_registry.get_config.return_value = mock_registry_config

        config_service = AsyncMock()
        oauth_config: dict[str, Any] = {
            "authorizeUrl": "https://existing-auth.example.com",
            "tokenUrl": "https://existing-token.example.com",
            "redirectUri": "https://existing-redirect",
            "scopes": {"team_sync": ["existing"]},
            "iconPath": "/existing.svg",
            "appGroup": "Existing",
            "appDescription": "Existing desc",
            "appCategories": ["existing"],
        }

        with patch(
            _OAUTH_REGISTRY_PATCH,
            return_value=mock_registry,
        ):
            await _update_oauth_infrastructure_fields(
                oauth_config, "DRIVE", config_service, "https://example.com"
            )

        assert oauth_config["authorizeUrl"] == "https://existing-auth.example.com"
        assert oauth_config["tokenUrl"] == "https://existing-token.example.com"
        assert oauth_config["redirectUri"] == "https://existing-redirect"
        assert oauth_config["scopes"] == {"team_sync": ["existing"]}

    async def test_no_registry_config_returns_early(self):
        from app.connectors.api.router import _update_oauth_infrastructure_fields

        mock_registry = MagicMock()
        mock_registry.get_config.return_value = None

        config_service = AsyncMock()
        oauth_config: dict[str, Any] = {}

        with patch(
            _OAUTH_REGISTRY_PATCH,
            return_value=mock_registry,
        ):
            await _update_oauth_infrastructure_fields(
                oauth_config, "UNKNOWN", config_service, ""
            )

        # Config should remain empty
        assert oauth_config == {}

    async def test_fallback_redirect_uri_without_base_url(self):
        from app.connectors.api.router import _update_oauth_infrastructure_fields

        mock_registry_config = MagicMock()
        mock_registry_config.authorize_url = "https://auth.example.com"
        mock_registry_config.token_url = "https://token.example.com"
        mock_registry_config.redirect_uri = "oauth/callback"
        mock_registry_config.scopes = MagicMock()
        mock_registry_config.scopes.to_dict.return_value = {}
        mock_registry_config.token_access_type = None
        mock_registry_config.additional_params = None
        mock_registry_config.icon_path = "/icon.svg"
        mock_registry_config.app_group = "Test"
        mock_registry_config.app_description = "Test"
        mock_registry_config.app_categories = []

        mock_registry = MagicMock()
        mock_registry.get_config.return_value = mock_registry_config

        config_service = AsyncMock()
        config_service.get_config = AsyncMock(
            return_value={"frontend": {"publicEndpoint": "https://fallback.com"}}
        )
        oauth_config: dict[str, Any] = {}

        with patch(
            _OAUTH_REGISTRY_PATCH,
            return_value=mock_registry,
        ):
            await _update_oauth_infrastructure_fields(
                oauth_config, "DRIVE", config_service, ""
            )

        assert oauth_config["redirectUri"] == "https://fallback.com/oauth/callback"

    async def test_empty_redirect_uri_path(self):
        from app.connectors.api.router import _update_oauth_infrastructure_fields

        mock_registry_config = MagicMock()
        mock_registry_config.authorize_url = "https://auth.example.com"
        mock_registry_config.token_url = "https://token.example.com"
        mock_registry_config.redirect_uri = ""
        mock_registry_config.scopes = MagicMock()
        mock_registry_config.scopes.to_dict.return_value = {}
        mock_registry_config.token_access_type = None
        mock_registry_config.additional_params = None
        mock_registry_config.icon_path = "/icon.svg"
        mock_registry_config.app_group = "Test"
        mock_registry_config.app_description = "Test"
        mock_registry_config.app_categories = []

        mock_registry = MagicMock()
        mock_registry.get_config.return_value = mock_registry_config

        config_service = AsyncMock()
        oauth_config: dict[str, Any] = {}

        with patch(
            _OAUTH_REGISTRY_PATCH,
            return_value=mock_registry,
        ):
            await _update_oauth_infrastructure_fields(
                oauth_config, "DRIVE", config_service, "https://example.com"
            )

        assert oauth_config["redirectUri"] == ""


# ===========================================================================
# _build_oauth_flow_config
# ===========================================================================

class TestBuildOAuthFlowConfig:
    """Tests for _build_oauth_flow_config helper."""

    async def test_uses_shared_oauth_config(self):
        from app.connectors.api.router import _build_oauth_flow_config

        config_service = AsyncMock()
        config_service.get_config = AsyncMock(return_value=[
            {
                "_id": "oa1",
                "orgId": "o1",
                "authorizeUrl": "https://auth.test",
                "tokenUrl": "https://token.test",
                "redirectUri": "https://redirect.test",
                "scopes": {"team_sync": ["s1", "s2"]},
                "tokenAccessType": "offline",
                "additionalParams": {"extra": "val"},
                "config": {"clientId": "cid", "clientSecret": "csecret"},
            }
        ])

        auth_config = {
            "oauthConfigId": "oa1",
            "connectorScope": "team",
            "authType": "OAUTH",
        }

        result = await _build_oauth_flow_config(
            auth_config=auth_config,
            connector_type="GMAIL",
            org_id="o1",
            config_service=config_service,
            logger=logging.getLogger("test"),
        )

        assert result["authorizeUrl"] == "https://auth.test"
        assert result["tokenUrl"] == "https://token.test"
        assert result["redirectUri"] == "https://redirect.test"
        assert result["scopes"] == ["s1", "s2"]
        assert result["tokenAccessType"] == "offline"
        assert result["clientId"] == "cid"
        assert result["clientSecret"] == "csecret"
        assert result["authType"] == "OAUTH"
        assert result["connectorScope"] == "team"

    async def test_shared_config_not_found_raises_404(self):
        from app.connectors.api.router import _build_oauth_flow_config

        config_service = AsyncMock()
        config_service.get_config = AsyncMock(return_value=[])

        auth_config = {"oauthConfigId": "missing"}

        with pytest.raises(HTTPException) as exc_info:
            await _build_oauth_flow_config(
                auth_config=auth_config,
                connector_type="GMAIL",
                org_id="o1",
                config_service=config_service,
                logger=logging.getLogger("test"),
            )
        assert exc_info.value.status_code == 404

    async def test_shared_config_wrong_org_raises_404(self):
        from app.connectors.api.router import _build_oauth_flow_config

        config_service = AsyncMock()
        config_service.get_config = AsyncMock(return_value=[
            {"_id": "oa1", "orgId": "other_org"}
        ])

        auth_config = {"oauthConfigId": "oa1"}

        with pytest.raises(HTTPException) as exc_info:
            await _build_oauth_flow_config(
                auth_config=auth_config,
                connector_type="GMAIL",
                org_id="o1",
                config_service=config_service,
                logger=logging.getLogger("test"),
            )
        assert exc_info.value.status_code == 404

    async def test_direct_auth_config_without_shared(self):
        from app.connectors.api.router import _build_oauth_flow_config

        config_service = AsyncMock()
        auth_config = {
            "authorizeUrl": "https://direct.auth",
            "tokenUrl": "https://direct.token",
            "clientId": "direct_id",
        }

        result = await _build_oauth_flow_config(
            auth_config=auth_config,
            connector_type="GMAIL",
            org_id="o1",
            config_service=config_service,
            logger=logging.getLogger("test"),
        )

        assert result["authorizeUrl"] == "https://direct.auth"
        assert result["clientId"] == "direct_id"

    async def test_scopes_from_auth_config_take_priority(self):
        from app.connectors.api.router import _build_oauth_flow_config

        config_service = AsyncMock()
        config_service.get_config = AsyncMock(return_value=[
            {
                "_id": "oa1",
                "orgId": "o1",
                "scopes": {"team_sync": ["shared_scope"]},
                "config": {},
            }
        ])

        auth_config = {
            "oauthConfigId": "oa1",
            "scopes": ["custom_scope_1", "custom_scope_2"],
        }

        result = await _build_oauth_flow_config(
            auth_config=auth_config,
            connector_type="GMAIL",
            org_id="o1",
            config_service=config_service,
            logger=logging.getLogger("test"),
        )

        assert result["scopes"] == ["custom_scope_1", "custom_scope_2"]

    async def test_scopes_fallback_personal_scope(self):
        from app.connectors.api.router import _build_oauth_flow_config

        config_service = AsyncMock()
        config_service.get_config = AsyncMock(return_value=[
            {
                "_id": "oa1",
                "orgId": "o1",
                "scopes": {"personal_sync": ["ps1"], "team_sync": ["ts1"]},
                "config": {},
            }
        ])

        auth_config = {
            "oauthConfigId": "oa1",
            "connectorScope": "personal",
        }

        result = await _build_oauth_flow_config(
            auth_config=auth_config,
            connector_type="GMAIL",
            org_id="o1",
            config_service=config_service,
            logger=logging.getLogger("test"),
        )

        assert result["scopes"] == ["ps1"]

    async def test_scopes_fallback_agent_scope(self):
        from app.connectors.api.router import _build_oauth_flow_config

        config_service = AsyncMock()
        config_service.get_config = AsyncMock(return_value=[
            {
                "_id": "oa1",
                "orgId": "o1",
                "scopes": {"agent": ["agent_s1"]},
                "config": {},
            }
        ])

        auth_config = {
            "oauthConfigId": "oa1",
            "connectorScope": "agent",
        }

        result = await _build_oauth_flow_config(
            auth_config=auth_config,
            connector_type="GMAIL",
            org_id="o1",
            config_service=config_service,
            logger=logging.getLogger("test"),
        )

        assert result["scopes"] == ["agent_s1"]

    async def test_scopes_as_list_directly(self):
        from app.connectors.api.router import _build_oauth_flow_config

        config_service = AsyncMock()
        config_service.get_config = AsyncMock(return_value=[
            {
                "_id": "oa1",
                "orgId": "o1",
                "scopes": ["scope_a", "scope_b"],
                "config": {},
            }
        ])

        auth_config = {"oauthConfigId": "oa1", "connectorScope": "team"}

        result = await _build_oauth_flow_config(
            auth_config=auth_config,
            connector_type="GMAIL",
            org_id="o1",
            config_service=config_service,
            logger=logging.getLogger("test"),
        )

        assert result["scopes"] == ["scope_a", "scope_b"]

    async def test_config_normalizes_client_fields(self):
        from app.connectors.api.router import _build_oauth_flow_config

        config_service = AsyncMock()
        config_service.get_config = AsyncMock(return_value=[
            {
                "_id": "oa1",
                "orgId": "o1",
                "config": {"client_id": "cid", "client_secret": "cs"},
            }
        ])

        auth_config = {"oauthConfigId": "oa1", "connectorScope": "team"}

        result = await _build_oauth_flow_config(
            auth_config=auth_config,
            connector_type="GMAIL",
            org_id="o1",
            config_service=config_service,
            logger=logging.getLogger("test"),
        )

        assert result["clientId"] == "cid"
        assert result["clientSecret"] == "cs"

    async def test_non_list_oauth_configs_treated_as_empty(self):
        from app.connectors.api.router import _build_oauth_flow_config

        config_service = AsyncMock()
        config_service.get_config = AsyncMock(return_value="not_a_list")

        auth_config = {"oauthConfigId": "oa1"}

        with pytest.raises(HTTPException) as exc_info:
            await _build_oauth_flow_config(
                auth_config=auth_config,
                connector_type="GMAIL",
                org_id="o1",
                config_service=config_service,
                logger=logging.getLogger("test"),
            )
        assert exc_info.value.status_code == 404


# ===========================================================================
# _parse_filter_response
# ===========================================================================

class TestParseFilterResponse:
    """Tests for _parse_filter_response helper."""

    def test_gmail_labels(self):
        data = {
            "labels": [
                {"id": "INBOX", "name": "Inbox", "type": "system"},
                {"id": "custom1", "name": "Custom", "type": "user"},
            ]
        }
        result = _parse_filter_response(data, "labels", "GMAIL")
        assert len(result) == 1
        assert result[0] == {"value": "custom1", "label": "Custom"}

    def test_drive_folders(self):
        data = {"files": [{"id": "f1", "name": "Folder A"}]}
        result = _parse_filter_response(data, "folders", "Drive")
        assert result == [{"value": "f1", "label": "Folder A"}]

    def test_onedrive_folders(self):
        data = {
            "value": [
                {"id": "od1", "name": "FolderA", "folder": True},
                {"id": "od2", "name": "FileB"},
            ]
        }
        result = _parse_filter_response(data, "folders", "OneDrive")
        assert len(result) == 1
        assert result[0] == {"value": "od1", "label": "FolderA"}

    def test_slack_channels(self):
        data = {
            "channels": [
                {"id": "ch1", "name": "general", "is_archived": False},
                {"id": "ch2", "name": "archived", "is_archived": True},
            ]
        }
        result = _parse_filter_response(data, "channels", "Slack")
        assert len(result) == 1
        assert result[0] == {"value": "ch1", "label": "#general"}

    def test_confluence_spaces(self):
        data = {"results": [{"key": "DOCS", "name": "Documentation"}]}
        result = _parse_filter_response(data, "spaces", "Confluence")
        assert result == [{"value": "DOCS", "label": "Documentation"}]

    def test_unknown_connector_returns_empty(self):
        result = _parse_filter_response({"data": []}, "filters", "UNKNOWN")
        assert result == []

    def test_parsing_error_returns_empty(self):
        # Labels missing the "id" key should be handled gracefully
        data = {"labels": [{"name": "no_id", "type": "user"}]}
        result = _parse_filter_response(data, "labels", "GMAIL")
        # KeyError is caught by the except block
        assert result == []


# ===========================================================================
# _get_static_filter_options
# ===========================================================================

class TestGetStaticFilterOptions:
    """Tests for _get_static_filter_options helper."""

    async def test_file_types(self):
        result = await _get_static_filter_options("DRIVE", "fileTypes")
        assert len(result) == 6
        assert any(o["value"] == "document" for o in result)

    async def test_content_types(self):
        result = await _get_static_filter_options("CONFLUENCE", "contentTypes")
        assert len(result) == 4
        assert any(o["value"] == "page" for o in result)

    async def test_unknown_filter_type(self):
        result = await _get_static_filter_options("GMAIL", "unknownType")
        assert result == []


# ===========================================================================
# _get_fallback_filter_options
# ===========================================================================

class TestGetFallbackFilterOptions:
    """Tests for _get_fallback_filter_options helper."""

    async def test_gmail_fallback(self):
        result = await _get_fallback_filter_options("GMAIL")
        assert "labels" in result
        assert len(result["labels"]) > 0

    async def test_drive_fallback(self):
        result = await _get_fallback_filter_options("Drive")
        assert "fileTypes" in result

    async def test_onedrive_fallback(self):
        result = await _get_fallback_filter_options("OneDrive")
        assert "fileTypes" in result

    async def test_slack_fallback(self):
        result = await _get_fallback_filter_options("Slack")
        assert "channels" in result

    async def test_confluence_fallback(self):
        result = await _get_fallback_filter_options("Confluence")
        assert "spaces" in result

    async def test_unknown_connector_fallback(self):
        result = await _get_fallback_filter_options("UNKNOWN_TYPE")
        assert result == {}


# ===========================================================================
# _get_connector_filter_options_from_config
# ===========================================================================

class TestGetConnectorFilterOptionsFromConfig:
    """Tests for _get_connector_filter_options_from_config helper."""

    async def test_no_endpoints_returns_empty(self):
        result = await _get_connector_filter_options_from_config(
            "GMAIL", {"config": {}}, {}, {}
        )
        assert result == {}

    async def test_static_endpoint(self):
        connector_config = {
            "config": {"filters": {"endpoints": {"fileTypes": "static"}}}
        }
        result = await _get_connector_filter_options_from_config(
            "DRIVE", connector_config, {}, {}
        )
        assert "fileTypes" in result
        assert len(result["fileTypes"]) == 6

    async def test_api_endpoint_success(self):
        connector_config = {
            "config": {
                "filters": {"endpoints": {"labels": "https://api.test/labels"}}
            }
        }
        token = {"access_token": "tok123"}

        # Mock aiohttp response
        mock_response = MagicMock()
        mock_response.status = 200
        mock_response.json = AsyncMock(return_value={
            "labels": [{"id": "L1", "name": "MyLabel", "type": "user"}]
        })

        resp_cm = MagicMock()
        resp_cm.__aenter__ = AsyncMock(return_value=mock_response)
        resp_cm.__aexit__ = AsyncMock(return_value=False)

        mock_session = MagicMock()
        mock_session.get.return_value = resp_cm

        session_cm = MagicMock()
        session_cm.__aenter__ = AsyncMock(return_value=mock_session)
        session_cm.__aexit__ = AsyncMock(return_value=False)

        with patch("aiohttp.ClientSession", return_value=session_cm):
            result = await _get_connector_filter_options_from_config(
                "GMAIL", connector_config, token, {}
            )

        assert "labels" in result

    async def test_outer_exception_returns_fallback(self):
        # Trigger the outer except by making config.get raise
        connector_config = MagicMock()
        connector_config.get.side_effect = RuntimeError("boom")

        result = await _get_connector_filter_options_from_config(
            "GMAIL", connector_config, {}, {}
        )
        # Should return fallback options for GMAIL
        assert "labels" in result


# ===========================================================================
# _get_connector_from_container
# ===========================================================================

class TestGetConnectorFromContainer:
    """Tests for _get_connector_from_container helper."""

    def test_found_via_attribute(self):
        container = MagicMock()
        mock_connector = MagicMock()
        container.c1_connector = MagicMock(return_value=mock_connector)
        result = _get_connector_from_container(container, "c1")
        assert result == mock_connector

    def test_found_via_connectors_map(self):
        container = MagicMock(spec=[])
        container.connectors_map = {"c1": "mock_connector"}
        result = _get_connector_from_container(container, "c1")
        assert result == "mock_connector"

    def test_not_found_returns_none(self):
        container = MagicMock(spec=[])
        result = _get_connector_from_container(container, "missing")
        assert result is None


# ===========================================================================
# _find_filter_field_config
# ===========================================================================

class TestFindFilterFieldConfig:
    """Tests for _find_filter_field_config helper."""

    def test_finds_in_sync_category(self):
        metadata = {
            "config": {
                "filters": {
                    "sync": {
                        "schema": {
                            "fields": [
                                {"name": "space_keys", "type": "multi_select"},
                            ]
                        }
                    }
                }
            }
        }
        result = _find_filter_field_config(metadata, "space_keys")
        assert result is not None
        assert result["name"] == "space_keys"

    def test_finds_in_indexing_category(self):
        metadata = {
            "config": {
                "filters": {
                    "indexing": {
                        "schema": {
                            "fields": [
                                {"name": "content_type", "type": "select"},
                            ]
                        }
                    }
                }
            }
        }
        result = _find_filter_field_config(metadata, "content_type")
        assert result is not None

    def test_not_found_returns_none(self):
        metadata = {
            "config": {
                "filters": {
                    "sync": {"schema": {"fields": [{"name": "other"}]}},
                }
            }
        }
        assert _find_filter_field_config(metadata, "missing") is None

    def test_empty_metadata(self):
        assert _find_filter_field_config({}, "anything") is None


# ===========================================================================
# _clean_schema_for_response
# ===========================================================================

class TestCleanSchemaForResponse:
    """Tests for _clean_schema_for_response helper."""

    def test_removes_internal_fields(self):
        schema = {
            "_oauth_configs": [{"secret": "data"}],
            "auth": {
                "authorizeUrl": "https://auth.test",
                "tokenUrl": "https://token.test",
                "scopes": ["s1"],
                "oauthConfigs": [{"id": "oa1"}],
                "redirectUri": "https://redirect.test",
                "displayRedirectUri": True,
            },
            "filters": {"sync": {"schema": {}}},
        }
        cleaned = _clean_schema_for_response(schema)

        assert "_oauth_configs" not in cleaned
        assert "authorizeUrl" not in cleaned["auth"]
        assert "tokenUrl" not in cleaned["auth"]
        assert "scopes" not in cleaned["auth"]
        assert "oauthConfigs" not in cleaned["auth"]
        # These should be preserved
        assert "redirectUri" in cleaned["auth"]
        assert "displayRedirectUri" in cleaned["auth"]
        assert "filters" in cleaned

    def test_no_auth_section(self):
        schema = {"filters": {"data": "val"}}
        cleaned = _clean_schema_for_response(schema)
        assert cleaned == schema

    def test_empty_schema(self):
        cleaned = _clean_schema_for_response({})
        assert cleaned == {}

    def test_deep_copy_does_not_mutate_original(self):
        schema = {
            "_oauth_configs": [{"secret": "data"}],
            "auth": {"authorizeUrl": "https://auth.test"},
        }
        original_copy = copy.deepcopy(schema)
        _clean_schema_for_response(schema)
        assert schema == original_copy


# ===========================================================================
# _get_oauth_config_path
# ===========================================================================

class TestGetOAuthConfigPath:
    """Tests for _get_oauth_config_path helper."""

    def test_simple_type(self):
        assert _get_oauth_config_path("GMAIL") == "/services/oauth/gmail"

    def test_type_with_spaces(self):
        assert _get_oauth_config_path("Google Drive") == "/services/oauth/googledrive"

    def test_mixed_case(self):
        assert _get_oauth_config_path("OneDrive") == "/services/oauth/onedrive"


# ===========================================================================
# _generate_oauth_config_id
# ===========================================================================

class TestGenerateOAuthConfigId:
    """Tests for _generate_oauth_config_id helper."""

    def test_returns_string(self):
        result = _generate_oauth_config_id()
        assert isinstance(result, str)

    def test_unique_ids(self):
        ids = {_generate_oauth_config_id() for _ in range(100)}
        assert len(ids) == 100


# ===========================================================================
# _get_oauth_field_names_from_registry
# ===========================================================================

class TestGetOAuthFieldNamesFromRegistry:
    """Tests for _get_oauth_field_names_from_registry helper."""

    def test_returns_field_names(self):
        field1 = MagicMock()
        field1.name = "clientId"
        field2 = MagicMock()
        field2.name = "clientSecret"
        field3 = MagicMock()
        field3.name = "domain"

        mock_config = MagicMock()
        mock_config.auth_fields = [field1, field2, field3]

        mock_registry = MagicMock()
        mock_registry.get_config.return_value = mock_config

        with patch(
            _OAUTH_REGISTRY_PATCH,
            return_value=mock_registry,
        ):
            result = _get_oauth_field_names_from_registry("GMAIL")

        assert result == ["clientId", "clientSecret", "domain"]

    def test_no_config_returns_defaults(self):
        mock_registry = MagicMock()
        mock_registry.get_config.return_value = None

        with patch(
            _OAUTH_REGISTRY_PATCH,
            return_value=mock_registry,
        ):
            result = _get_oauth_field_names_from_registry("UNKNOWN")

        assert result == ["clientId", "clientSecret"]

    def test_no_auth_fields_returns_defaults(self):
        mock_config = MagicMock()
        mock_config.auth_fields = None

        mock_registry = MagicMock()
        mock_registry.get_config.return_value = mock_config

        with patch(
            _OAUTH_REGISTRY_PATCH,
            return_value=mock_registry,
        ):
            result = _get_oauth_field_names_from_registry("GMAIL")

        assert result == ["clientId", "clientSecret"]

    def test_exception_returns_defaults(self):
        with patch(
            _OAUTH_REGISTRY_PATCH,
            side_effect=RuntimeError("import error"),
        ):
            result = _get_oauth_field_names_from_registry("GMAIL")

        assert result == ["clientId", "clientSecret"]


# ===========================================================================
# _extract_essential_oauth_fields
# ===========================================================================

class TestExtractEssentialOAuthFields:
    """Tests for _extract_essential_oauth_fields helper."""

    def test_extracts_all_fields(self):
        config = {
            "_id": "oa1",
            "oauthInstanceName": "My OAuth",
            "iconPath": "/icon.svg",
            "appGroup": "Google",
            "appDescription": "Google Drive OAuth",
            "appCategories": ["storage"],
            "connectorType": "DRIVE",
            "createdAtTimestamp": 1000,
            "updatedAtTimestamp": 2000,
            "config": {"clientId": "secret_should_not_appear"},
        }
        result = _extract_essential_oauth_fields(config, "DRIVE")
        assert result["_id"] == "oa1"
        assert result["oauthInstanceName"] == "My OAuth"
        assert result["connectorType"] == "DRIVE"
        assert "config" not in result
        assert "clientId" not in result

    def test_defaults_for_missing_fields(self):
        config = {"_id": "oa2"}
        result = _extract_essential_oauth_fields(config, "SLACK")
        assert result["iconPath"] == "/assets/icons/connectors/default.svg"
        assert result["appGroup"] == ""
        assert result["appDescription"] == ""
        assert result["appCategories"] == []
        assert result["connectorType"] == "SLACK"

    def test_connector_type_from_config_takes_priority(self):
        config = {"_id": "oa3", "connectorType": "FROM_CONFIG"}
        result = _extract_essential_oauth_fields(config, "FALLBACK")
        assert result["connectorType"] == "FROM_CONFIG"


# ===========================================================================
# _find_oauth_config_by_id
# ===========================================================================

class TestFindOAuthConfigById:
    """Tests for _find_oauth_config_by_id helper."""

    def test_finds_config(self):
        configs = [
            {"_id": "c1", "orgId": "o1"},
            {"_id": "c2", "orgId": "o1"},
        ]
        result = _find_oauth_config_by_id(configs, "c2", "o1")
        assert result == configs[1]

    def test_not_found(self):
        configs = [{"_id": "c1", "orgId": "o1"}]
        assert _find_oauth_config_by_id(configs, "missing", "o1") is None

    def test_wrong_org(self):
        configs = [{"_id": "c1", "orgId": "o2"}]
        assert _find_oauth_config_by_id(configs, "c1", "o1") is None

    def test_empty_list(self):
        assert _find_oauth_config_by_id([], "c1", "o1") is None


# ===========================================================================
# _get_oauth_configs_from_etcd
# ===========================================================================

class TestGetOAuthConfigsFromEtcd:
    """Tests for _get_oauth_configs_from_etcd helper."""

    async def test_returns_list(self):
        from app.connectors.api.router import _get_oauth_configs_from_etcd

        config_service = AsyncMock()
        config_service.get_config = AsyncMock(
            return_value=[{"_id": "cfg1"}, {"_id": "cfg2"}]
        )
        result = await _get_oauth_configs_from_etcd("GMAIL", config_service)
        assert len(result) == 2

    async def test_non_list_returns_empty(self):
        from app.connectors.api.router import _get_oauth_configs_from_etcd

        config_service = AsyncMock()
        config_service.get_config = AsyncMock(return_value="not_a_list")
        result = await _get_oauth_configs_from_etcd("GMAIL", config_service)
        assert result == []

    async def test_default_empty(self):
        from app.connectors.api.router import _get_oauth_configs_from_etcd

        config_service = AsyncMock()
        config_service.get_config = AsyncMock(return_value=[])
        result = await _get_oauth_configs_from_etcd("GMAIL", config_service)
        assert result == []


# ===========================================================================
# _create_or_update_oauth_config
# ===========================================================================

class TestCreateOrUpdateOAuthConfig:
    """Tests for _create_or_update_oauth_config helper."""

    async def test_create_new_config(self):
        from app.connectors.api.router import _create_or_update_oauth_config

        config_service = AsyncMock()
        config_service.get_config = AsyncMock(return_value=[])
        config_service.set_config = AsyncMock(return_value=True)

        with patch(
            "app.connectors.api.router._get_oauth_field_names_from_registry",
            return_value=["clientId", "clientSecret"],
        ), patch(
            "app.connectors.api.router._update_oauth_infrastructure_fields",
            new_callable=AsyncMock,
        ):
            result = await _create_or_update_oauth_config(
                connector_type="GMAIL",
                auth_config={"clientId": "cid", "clientSecret": "cs"},
                instance_name="Test OAuth",
                user_id="u1",
                org_id="o1",
                is_admin=True,
                config_service=config_service,
                base_url="https://example.com",
            )

        assert result is not None
        assert isinstance(result, str)
        config_service.set_config.assert_called_once()

    async def test_update_existing_config(self):
        from app.connectors.api.router import _create_or_update_oauth_config

        existing_config = {
            "_id": "existing_id",
            "orgId": "o1",
            "userId": "u1",
            "config": {"clientId": "old_cid"},
        }

        config_service = AsyncMock()
        config_service.get_config = AsyncMock(return_value=[existing_config])
        config_service.set_config = AsyncMock(return_value=True)

        with patch(
            "app.connectors.api.router._get_oauth_field_names_from_registry",
            return_value=["clientId", "clientSecret"],
        ), patch(
            "app.connectors.api.router._update_oauth_infrastructure_fields",
            new_callable=AsyncMock,
        ):
            result = await _create_or_update_oauth_config(
                connector_type="GMAIL",
                auth_config={"clientId": "new_cid"},
                instance_name="Test OAuth",
                user_id="u1",
                org_id="o1",
                is_admin=True,
                config_service=config_service,
                base_url="https://example.com",
                oauth_app_id="existing_id",
            )

        assert result == "existing_id"

    async def test_update_with_admin_different_user(self):
        from app.connectors.api.router import _create_or_update_oauth_config

        existing_config = {
            "_id": "existing_id",
            "orgId": "o1",
            "userId": "other_user",
            "config": {},
        }

        config_service = AsyncMock()
        config_service.get_config = AsyncMock(return_value=[existing_config])
        config_service.set_config = AsyncMock(return_value=True)

        with patch(
            "app.connectors.api.router._get_oauth_field_names_from_registry",
            return_value=["clientId"],
        ), patch(
            "app.connectors.api.router._update_oauth_infrastructure_fields",
            new_callable=AsyncMock,
        ):
            result = await _create_or_update_oauth_config(
                connector_type="GMAIL",
                auth_config={"clientId": "admin_cid"},
                instance_name="Admin OAuth",
                user_id="admin_user",
                org_id="o1",
                is_admin=True,
                config_service=config_service,
                base_url="",
                oauth_app_id="existing_id",
            )

        assert result == "existing_id"

    async def test_update_not_found_creates_new(self):
        from app.connectors.api.router import _create_or_update_oauth_config

        config_service = AsyncMock()
        config_service.get_config = AsyncMock(return_value=[])
        config_service.set_config = AsyncMock(return_value=True)

        with patch(
            "app.connectors.api.router._get_oauth_field_names_from_registry",
            return_value=["clientId"],
        ), patch(
            "app.connectors.api.router._update_oauth_infrastructure_fields",
            new_callable=AsyncMock,
        ):
            result = await _create_or_update_oauth_config(
                connector_type="GMAIL",
                auth_config={"clientId": "cid"},
                instance_name="Test",
                user_id="u1",
                org_id="o1",
                is_admin=True,
                config_service=config_service,
                base_url="",
                oauth_app_id="nonexistent",
            )

        assert result is not None
        assert result != "nonexistent"

    async def test_exception_returns_none(self):
        from app.connectors.api.router import _create_or_update_oauth_config

        config_service = AsyncMock()
        config_service.get_config = AsyncMock(side_effect=RuntimeError("db error"))

        with patch(
            "app.connectors.api.router._get_oauth_field_names_from_registry",
            return_value=["clientId"],
        ):
            result = await _create_or_update_oauth_config(
                connector_type="GMAIL",
                auth_config={},
                instance_name="Test",
                user_id="u1",
                org_id="o1",
                is_admin=True,
                config_service=config_service,
                base_url="",
            )

        assert result is None


# ===========================================================================
# Route handler tests: get_oauth_authorization_url
# ===========================================================================

class TestGetOAuthAuthorizationUrl:
    """Tests for get_oauth_authorization_url route handler."""

    async def test_unauthenticated_user_raises_401(self):
        from app.connectors.api.router import get_oauth_authorization_url

        req = _make_request()
        req.state.user = {"userId": None, "orgId": "o1"}

        with pytest.raises(HTTPException) as exc_info:
            await get_oauth_authorization_url("c1", req, graph_provider=AsyncMock())
        assert exc_info.value.status_code == 401

    async def test_instance_not_found_raises_404(self):
        from app.connectors.api.router import get_oauth_authorization_url

        req = _make_request(is_admin=True)
        req.app.state.connector_registry.get_connector_instance = AsyncMock(
            return_value=None
        )

        with pytest.raises(HTTPException) as exc_info:
            await get_oauth_authorization_url("c1", req, graph_provider=AsyncMock())
        assert exc_info.value.status_code == 404

    async def test_team_connector_non_admin_raises_403(self):
        from app.connectors.api.router import get_oauth_authorization_url

        req = _make_request(is_admin=False)
        instance = _make_instance(scope="team", created_by="u1", auth_type="OAUTH")
        req.app.state.connector_registry.get_connector_instance = AsyncMock(
            return_value=instance
        )

        with patch("app.connectors.api.router.check_beta_connector_access", new_callable=AsyncMock):
            with pytest.raises(HTTPException) as exc_info:
                await get_oauth_authorization_url("c1", req, graph_provider=AsyncMock())
        assert exc_info.value.status_code == 403

    async def test_non_oauth_auth_type_raises_400(self):
        from app.connectors.api.router import get_oauth_authorization_url

        req = _make_request(is_admin=True)
        instance = _make_instance(
            scope="team", created_by="u1", auth_type="API_TOKEN"
        )
        req.app.state.connector_registry.get_connector_instance = AsyncMock(
            return_value=instance
        )

        with patch("app.connectors.api.router.check_beta_connector_access", new_callable=AsyncMock):
            with pytest.raises(HTTPException) as exc_info:
                await get_oauth_authorization_url("c1", req, graph_provider=AsyncMock())
        assert exc_info.value.status_code == 400

    async def test_no_auth_config_raises_400(self):
        from app.connectors.api.router import get_oauth_authorization_url

        req = _make_request(is_admin=True)
        instance = _make_instance(scope="team", created_by="u1", auth_type="OAUTH")
        req.app.state.connector_registry.get_connector_instance = AsyncMock(
            return_value=instance
        )

        config_service = req.app.container.config_service()
        config_service.get_config = AsyncMock(return_value=None)

        with patch("app.connectors.api.router.check_beta_connector_access", new_callable=AsyncMock):
            with pytest.raises(HTTPException) as exc_info:
                await get_oauth_authorization_url("c1", req, graph_provider=AsyncMock())
        assert exc_info.value.status_code == 400

    async def test_non_creator_non_admin_raises_403(self):
        from app.connectors.api.router import get_oauth_authorization_url

        req = _make_request(user_id="u1", is_admin=False)
        instance = _make_instance(
            scope="personal", created_by="other_user", auth_type="OAUTH"
        )
        req.app.state.connector_registry.get_connector_instance = AsyncMock(
            return_value=instance
        )

        with patch("app.connectors.api.router.check_beta_connector_access", new_callable=AsyncMock):
            with pytest.raises(HTTPException) as exc_info:
                await get_oauth_authorization_url("c1", req, graph_provider=AsyncMock())
        assert exc_info.value.status_code == 403

    async def test_success_returns_url(self):
        from app.connectors.api.router import get_oauth_authorization_url

        req = _make_request(is_admin=True)
        instance = _make_instance(
            scope="team", created_by="u1", auth_type="OAUTH", connector_type="DRIVE"
        )
        req.app.state.connector_registry.get_connector_instance = AsyncMock(
            return_value=instance
        )

        config_service = req.app.container.config_service()
        config_service.get_config = AsyncMock(return_value={
            "auth": {
                "authorizeUrl": "https://accounts.google.com/o/oauth2/auth",
                "tokenUrl": "https://oauth2.googleapis.com/token",
                "clientId": "cid",
                "clientSecret": "cs",
                "redirectUri": "https://example.com/callback",
                "scopes": ["scope1"],
            }
        })

        mock_oauth_provider = AsyncMock()
        mock_oauth_provider.start_authorization = AsyncMock(
            return_value="https://accounts.google.com/o/oauth2/auth?state=abc123&client_id=cid&scope=scope1"
        )
        mock_oauth_provider.close = AsyncMock()

        mock_oauth_config = MagicMock()
        mock_oauth_config.scope = "scope1"

        with patch("app.connectors.api.router.check_beta_connector_access", new_callable=AsyncMock), \
             patch("app.connectors.api.router._build_oauth_flow_config", new_callable=AsyncMock, return_value={
                 "authorizeUrl": "https://accounts.google.com/o/oauth2/auth",
                 "tokenUrl": "https://oauth2.googleapis.com/token",
                 "clientId": "cid",
                 "redirectUri": "https://example.com/callback",
                 "scopes": ["scope1"],
             }), \
             patch("app.connectors.api.router.get_oauth_config", return_value=mock_oauth_config), \
             patch("app.connectors.api.router.OAuthProvider", return_value=mock_oauth_provider):
            result = await get_oauth_authorization_url(
                "c1", req, graph_provider=AsyncMock()
            )

        assert result["success"] is True
        assert "authorizationUrl" in result
        assert "state" in result


# ===========================================================================
# Route handler tests: handle_oauth_callback
# ===========================================================================

class TestHandleOAuthCallback:
    """Tests for handle_oauth_callback route handler."""

    async def test_oauth_error_returns_failure(self):
        from app.connectors.api.router import handle_oauth_callback

        req = _make_request()

        with patch("app.connectors.api.router._get_settings_base_path", new_callable=AsyncMock, return_value="/settings"):
            result = await handle_oauth_callback(
                req,
                code=None,
                state=None,
                error="access_denied",
                base_url="https://example.com",
                graph_provider=AsyncMock(),
            )

        assert result["success"] is False
        assert "access_denied" in result["error"]

    async def test_null_error_treated_as_none(self):
        from app.connectors.api.router import handle_oauth_callback

        req = _make_request()

        with patch("app.connectors.api.router._get_settings_base_path", new_callable=AsyncMock, return_value="/settings"):
            result = await handle_oauth_callback(
                req,
                code=None,
                state=None,
                error="null",
                base_url="",
                graph_provider=AsyncMock(),
            )

        # "null" is treated as None, so it falls through to missing parameters
        assert result["success"] is False
        assert result["error"] == "missing_parameters"

    async def test_missing_code_returns_missing_params(self):
        from app.connectors.api.router import handle_oauth_callback

        req = _make_request()

        with patch("app.connectors.api.router._get_settings_base_path", new_callable=AsyncMock, return_value="/settings"):
            result = await handle_oauth_callback(
                req,
                code=None,
                state="some_state",
                error=None,
                base_url="",
                graph_provider=AsyncMock(),
            )

        assert result["success"] is False
        assert result["error"] == "missing_parameters"

    async def test_missing_state_returns_missing_params(self):
        from app.connectors.api.router import handle_oauth_callback

        req = _make_request()

        with patch("app.connectors.api.router._get_settings_base_path", new_callable=AsyncMock, return_value="/settings"):
            result = await handle_oauth_callback(
                req,
                code="auth_code",
                state=None,
                error=None,
                base_url="",
                graph_provider=AsyncMock(),
            )

        assert result["success"] is False
        assert result["error"] == "missing_parameters"

    async def test_unauthenticated_raises_401(self):
        from app.connectors.api.router import handle_oauth_callback

        import base64, json
        state = base64.urlsafe_b64encode(
            json.dumps({"state": "orig", "connector_id": "c1"}).encode()
        ).decode()

        req = _make_request()
        req.state.user = {"userId": None, "orgId": "o1"}

        with patch("app.connectors.api.router._get_settings_base_path", new_callable=AsyncMock, return_value="/settings"):
            # This should raise since user is not authenticated and we get past the state decode
            result = await handle_oauth_callback(
                req,
                code="code",
                state=state,
                error=None,
                base_url="",
                graph_provider=AsyncMock(),
            )

        # It returns a server_error because the HTTPException is caught by the outer except
        assert result["success"] is False

    async def test_invalid_state_returns_failure(self):
        from app.connectors.api.router import handle_oauth_callback

        req = _make_request()

        with patch("app.connectors.api.router._get_settings_base_path", new_callable=AsyncMock, return_value="/settings"):
            result = await handle_oauth_callback(
                req,
                code="code",
                state="invalid_base64!!!",
                error=None,
                base_url="",
                graph_provider=AsyncMock(),
            )

        assert result["success"] is False
        assert result["error"] == "invalid_state"

    async def test_instance_not_found_returns_failure(self):
        from app.connectors.api.router import handle_oauth_callback

        import base64, json
        state = base64.urlsafe_b64encode(
            json.dumps({"state": "orig", "connector_id": "c1"}).encode()
        ).decode()

        req = _make_request()
        req.app.state.connector_registry.get_connector_instance = AsyncMock(
            return_value=None
        )

        with patch("app.connectors.api.router._get_settings_base_path", new_callable=AsyncMock, return_value="/settings"):
            result = await handle_oauth_callback(
                req,
                code="code",
                state=state,
                error=None,
                base_url="",
                graph_provider=AsyncMock(),
            )

        assert result["success"] is False
        assert result["error"] == "instance_not_found"

    async def test_no_config_returns_failure(self):
        from app.connectors.api.router import handle_oauth_callback

        import base64, json
        state = base64.urlsafe_b64encode(
            json.dumps({"state": "orig", "connector_id": "c1"}).encode()
        ).decode()

        req = _make_request(is_admin=True)
        instance = _make_instance(scope="team", created_by="u1")
        req.app.state.connector_registry.get_connector_instance = AsyncMock(
            return_value=instance
        )

        config_service = req.app.container.config_service()
        config_service.get_config = AsyncMock(return_value=None)

        with patch("app.connectors.api.router._get_settings_base_path", new_callable=AsyncMock, return_value="/settings"), \
             patch("app.connectors.api.router.check_beta_connector_access", new_callable=AsyncMock):
            result = await handle_oauth_callback(
                req,
                code="code",
                state=state,
                error=None,
                base_url="",
                graph_provider=AsyncMock(),
            )

        assert result["success"] is False
        assert result["error"] == "config_not_found"

    async def test_oauth_build_config_error_returns_failure(self):
        from app.connectors.api.router import handle_oauth_callback

        import base64, json
        state = base64.urlsafe_b64encode(
            json.dumps({"state": "orig", "connector_id": "c1"}).encode()
        ).decode()

        req = _make_request(is_admin=True)
        instance = _make_instance(scope="team", created_by="u1")
        req.app.state.connector_registry.get_connector_instance = AsyncMock(
            return_value=instance
        )

        config_service = req.app.container.config_service()
        config_service.get_config = AsyncMock(
            return_value={"auth": {"oauthConfigId": "missing_config"}}
        )

        with patch("app.connectors.api.router._get_settings_base_path", new_callable=AsyncMock, return_value="/settings"), \
             patch("app.connectors.api.router.check_beta_connector_access", new_callable=AsyncMock), \
             patch("app.connectors.api.router._build_oauth_flow_config", new_callable=AsyncMock, side_effect=HTTPException(status_code=404, detail="not found")):
            result = await handle_oauth_callback(
                req, code="code", state=state, error=None,
                base_url="https://example.com", graph_provider=AsyncMock(),
            )

        assert result["success"] is False
        assert result["error"] == "oauth_config_fetch_error"

    async def test_successful_callback(self):
        from app.connectors.api.router import handle_oauth_callback

        import base64, json
        state = base64.urlsafe_b64encode(
            json.dumps({"state": "orig", "connector_id": "c1"}).encode()
        ).decode()

        req = _make_request(is_admin=True)
        instance = _make_instance(scope="team", created_by="u1")
        req.app.state.connector_registry.get_connector_instance = AsyncMock(
            return_value=instance
        )
        req.app.state.connector_registry.update_connector_instance = AsyncMock(
            return_value=True
        )

        config_service = req.app.container.config_service()
        config_service.get_config = AsyncMock(
            return_value={"auth": {"clientId": "cid", "redirectUri": "https://cb"}}
        )
        config_service.set_config = AsyncMock(return_value=True)

        mock_token = MagicMock()
        mock_token.access_token = "access_tok"
        mock_token.refresh_token = "refresh_tok"
        mock_token.token_type = "Bearer"
        mock_token.expires_in = 3600
        mock_token.refresh_token_expires_in = None
        mock_token.scope = "scope1"
        mock_token.id_token = None
        mock_token.uid = None
        mock_token.account_id = None
        mock_token.team_id = None
        mock_token.created_at = None

        mock_oauth_provider = AsyncMock()
        mock_oauth_provider.handle_callback = AsyncMock(return_value=mock_token)
        mock_oauth_provider.close = AsyncMock()

        mock_oauth_config = MagicMock()

        mock_refresh_service = AsyncMock()
        mock_refresh_service.schedule_token_refresh = AsyncMock()
        mock_startup = MagicMock()
        mock_startup.get_token_refresh_service.return_value = mock_refresh_service

        with patch("app.connectors.api.router._get_settings_base_path", new_callable=AsyncMock, return_value="/settings/connectors"), \
             patch("app.connectors.api.router.check_beta_connector_access", new_callable=AsyncMock), \
             patch("app.connectors.api.router._build_oauth_flow_config", new_callable=AsyncMock, return_value={"clientId": "cid"}), \
             patch("app.connectors.api.router.get_oauth_config", return_value=mock_oauth_config), \
             patch("app.connectors.api.router.OAuthProvider", return_value=mock_oauth_provider), \
             patch("app.connectors.api.router.get_epoch_timestamp_in_ms", return_value=1000), \
             patch("app.connectors.core.base.token_service.startup_service.startup_service", mock_startup):
            result = await handle_oauth_callback(
                req, code="auth_code", state=state, error=None,
                base_url="https://example.com", graph_provider=AsyncMock(),
            )

        assert result["success"] is True
        assert "redirect_url" in result

    async def test_invalid_token_returns_failure(self):
        from app.connectors.api.router import handle_oauth_callback

        import base64, json
        state = base64.urlsafe_b64encode(
            json.dumps({"state": "orig", "connector_id": "c1"}).encode()
        ).decode()

        req = _make_request(is_admin=True)
        instance = _make_instance(scope="team", created_by="u1")
        req.app.state.connector_registry.get_connector_instance = AsyncMock(
            return_value=instance
        )

        config_service = req.app.container.config_service()
        config_service.get_config = AsyncMock(
            return_value={"auth": {"clientId": "cid"}}
        )

        mock_token = MagicMock()
        mock_token.access_token = None  # Invalid token

        mock_oauth_provider = AsyncMock()
        mock_oauth_provider.handle_callback = AsyncMock(return_value=mock_token)
        mock_oauth_provider.close = AsyncMock()

        with patch("app.connectors.api.router._get_settings_base_path", new_callable=AsyncMock, return_value="/settings"), \
             patch("app.connectors.api.router.check_beta_connector_access", new_callable=AsyncMock), \
             patch("app.connectors.api.router._build_oauth_flow_config", new_callable=AsyncMock, return_value={}), \
             patch("app.connectors.api.router.get_oauth_config", return_value=MagicMock()), \
             patch("app.connectors.api.router.OAuthProvider", return_value=mock_oauth_provider):
            result = await handle_oauth_callback(
                req, code="code", state=state, error=None,
                base_url="https://example.com", graph_provider=AsyncMock(),
            )

        assert result["success"] is False
        assert result["error"] == "invalid_token"

    async def test_team_connector_non_admin_raises_403(self):
        from app.connectors.api.router import handle_oauth_callback

        import base64, json
        state = base64.urlsafe_b64encode(
            json.dumps({"state": "orig", "connector_id": "c1"}).encode()
        ).decode()

        req = _make_request(is_admin=False)
        instance = _make_instance(scope="team", created_by="u1")
        req.app.state.connector_registry.get_connector_instance = AsyncMock(
            return_value=instance
        )
        req.app.state.connector_registry.update_connector_instance = AsyncMock()

        with patch("app.connectors.api.router._get_settings_base_path", new_callable=AsyncMock, return_value="/settings"), \
             patch("app.connectors.api.router.check_beta_connector_access", new_callable=AsyncMock), \
             patch("app.connectors.api.router.get_epoch_timestamp_in_ms", return_value=1000):
            result = await handle_oauth_callback(
                req, code="code", state=state, error=None,
                base_url="", graph_provider=AsyncMock(),
            )

        # HTTPException is caught and results in server_error
        assert result["success"] is False

    async def test_error_values_normalized(self):
        """Test that "undefined", "None", "" error values are treated as no error."""
        from app.connectors.api.router import handle_oauth_callback

        req = _make_request()

        for error_val in ["undefined", "None", ""]:
            with patch("app.connectors.api.router._get_settings_base_path", new_callable=AsyncMock, return_value="/settings"):
                result = await handle_oauth_callback(
                    req, code=None, state=None, error=error_val,
                    base_url="", graph_provider=AsyncMock(),
                )
            assert result["success"] is False
            assert result["error"] == "missing_parameters"


# ===========================================================================
# Route handler tests: get_connector_instance_filters
# ===========================================================================

class TestGetConnectorInstanceFilters:
    """Tests for get_connector_instance_filters route handler."""

    async def test_unauthenticated_raises_401(self):
        from app.connectors.api.router import get_connector_instance_filters

        req = _make_request()
        req.state.user = {"userId": None, "orgId": "o1"}

        with pytest.raises(HTTPException) as exc_info:
            await get_connector_instance_filters("c1", req, graph_provider=AsyncMock())
        assert exc_info.value.status_code == 401

    async def test_instance_not_found_raises_404(self):
        from app.connectors.api.router import get_connector_instance_filters

        req = _make_request(is_admin=True)
        req.app.state.connector_registry.get_connector_instance = AsyncMock(
            return_value=None
        )

        with pytest.raises(HTTPException) as exc_info:
            await get_connector_instance_filters("c1", req, graph_provider=AsyncMock())
        assert exc_info.value.status_code == 404

    async def test_unsupported_auth_type_raises_400(self):
        from app.connectors.api.router import get_connector_instance_filters

        req = _make_request(is_admin=True)
        instance = _make_instance(
            scope="team", created_by="u1", auth_type="UNKNOWN_TYPE"
        )
        req.app.state.connector_registry.get_connector_instance = AsyncMock(
            return_value=instance
        )
        req.app.state.connector_registry.get_connector_metadata = AsyncMock(
            return_value={"config": {}}
        )

        config_service = req.app.container.config_service()
        config_service.get_config = AsyncMock(return_value={"auth": {}, "credentials": {}})

        with patch("app.connectors.api.router.check_beta_connector_access", new_callable=AsyncMock):
            with pytest.raises(HTTPException) as exc_info:
                await get_connector_instance_filters(
                    "c1", req, graph_provider=AsyncMock()
                )
        assert exc_info.value.status_code == 400

    async def test_connector_type_not_found_raises_404(self):
        from app.connectors.api.router import get_connector_instance_filters

        req = _make_request(is_admin=True)
        instance = _make_instance(scope="team", created_by="u1", auth_type="OAUTH")
        req.app.state.connector_registry.get_connector_instance = AsyncMock(
            return_value=instance
        )
        req.app.state.connector_registry.get_connector_metadata = AsyncMock(
            return_value=None
        )

        with patch("app.connectors.api.router.check_beta_connector_access", new_callable=AsyncMock):
            with pytest.raises(HTTPException) as exc_info:
                await get_connector_instance_filters(
                    "c1", req, graph_provider=AsyncMock()
                )
        assert exc_info.value.status_code == 404

    async def test_oauth_no_credentials_raises_400(self):
        from app.connectors.api.router import get_connector_instance_filters

        req = _make_request(is_admin=True)
        instance = _make_instance(scope="team", created_by="u1", auth_type="OAUTH")
        req.app.state.connector_registry.get_connector_instance = AsyncMock(
            return_value=instance
        )
        req.app.state.connector_registry.get_connector_metadata = AsyncMock(
            return_value={"config": {}}
        )

        config_service = req.app.container.config_service()
        config_service.get_config = AsyncMock(return_value={})

        with patch("app.connectors.api.router.check_beta_connector_access", new_callable=AsyncMock):
            with pytest.raises(HTTPException) as exc_info:
                await get_connector_instance_filters(
                    "c1", req, graph_provider=AsyncMock()
                )
        assert exc_info.value.status_code == 400

    async def test_success_with_oauth(self):
        from app.connectors.api.router import get_connector_instance_filters

        req = _make_request(is_admin=True)
        instance = _make_instance(scope="team", created_by="u1", auth_type="OAUTH")
        req.app.state.connector_registry.get_connector_instance = AsyncMock(
            return_value=instance
        )
        req.app.state.connector_registry.get_connector_metadata = AsyncMock(
            return_value={"config": {"filters": {"endpoints": {}}}}
        )

        config_service = req.app.container.config_service()
        config_service.get_config = AsyncMock(return_value={
            "credentials": {"access_token": "tok", "token_type": "Bearer"},
        })

        with patch("app.connectors.api.router.check_beta_connector_access", new_callable=AsyncMock), \
             patch("app.connectors.api.router._get_connector_filter_options_from_config", new_callable=AsyncMock, return_value={"labels": []}):
            result = await get_connector_instance_filters(
                "c1", req, graph_provider=AsyncMock()
            )

        assert result["success"] is True
        assert "filterOptions" in result


# ===========================================================================
# Route handler tests: save_connector_instance_filters
# ===========================================================================

class TestSaveConnectorInstanceFilters:
    """Tests for save_connector_instance_filters route handler."""

    async def test_no_filters_raises_400(self):
        from app.connectors.api.router import save_connector_instance_filters

        req = _make_request(is_admin=True, body={"filters": {}})
        req.app.state.connector_registry.get_connector_instance = AsyncMock(
            return_value=_make_instance()
        )

        with pytest.raises(HTTPException) as exc_info:
            await save_connector_instance_filters("c1", req, graph_provider=AsyncMock())
        assert exc_info.value.status_code == 400

    async def test_success_saves_filters(self):
        from app.connectors.api.router import save_connector_instance_filters

        req = _make_request(
            is_admin=True,
            body={"filters": {"labels": ["INBOX"]}},
        )
        instance = _make_instance(scope="team", created_by="u1")
        req.app.state.connector_registry.get_connector_instance = AsyncMock(
            return_value=instance
        )

        config_service = req.app.container.config_service()
        config_service.get_config = AsyncMock(return_value={"filters": {}})
        config_service.set_config = AsyncMock(return_value=True)

        with patch("app.connectors.api.router.check_beta_connector_access", new_callable=AsyncMock):
            result = await save_connector_instance_filters(
                "c1", req, graph_provider=AsyncMock()
            )

        assert result["success"] is True
        config_service.set_config.assert_called_once()

    async def test_saves_filters_when_config_is_none(self):
        from app.connectors.api.router import save_connector_instance_filters

        req = _make_request(
            is_admin=True,
            body={"filters": {"labels": ["INBOX"]}},
        )
        instance = _make_instance(scope="team", created_by="u1")
        req.app.state.connector_registry.get_connector_instance = AsyncMock(
            return_value=instance
        )

        config_service = req.app.container.config_service()
        config_service.get_config = AsyncMock(return_value=None)
        config_service.set_config = AsyncMock(return_value=True)

        with patch("app.connectors.api.router.check_beta_connector_access", new_callable=AsyncMock):
            result = await save_connector_instance_filters(
                "c1", req, graph_provider=AsyncMock()
            )

        assert result["success"] is True


# ===========================================================================
# Route handler tests: toggle_connector_instance
# ===========================================================================

class TestToggleConnectorInstance:
    """Tests for toggle_connector_instance route handler."""

    async def test_invalid_toggle_type_raises_400(self):
        from app.connectors.api.router import toggle_connector_instance

        req = _make_request(is_admin=True, body={"type": "invalid"})

        with pytest.raises(HTTPException) as exc_info:
            await toggle_connector_instance("c1", req, graph_provider=AsyncMock())
        assert exc_info.value.status_code == 400

    async def test_missing_toggle_type_raises_400(self):
        from app.connectors.api.router import toggle_connector_instance

        req = _make_request(is_admin=True, body={})

        with pytest.raises(HTTPException) as exc_info:
            await toggle_connector_instance("c1", req, graph_provider=AsyncMock())
        assert exc_info.value.status_code == 400

    async def test_org_not_found_raises_404(self):
        from app.connectors.api.router import toggle_connector_instance

        req = _make_request(is_admin=True, body={"type": "sync"})
        graph_provider = AsyncMock()
        graph_provider.get_document = AsyncMock(return_value=None)

        with pytest.raises(HTTPException) as exc_info:
            await toggle_connector_instance("c1", req, graph_provider=graph_provider)
        assert exc_info.value.status_code == 404

    async def test_unauthenticated_raises_401(self):
        from app.connectors.api.router import toggle_connector_instance

        req = _make_request(body={"type": "sync"})
        req.state.user = {"userId": None, "orgId": "o1"}
        graph_provider = AsyncMock()
        graph_provider.get_document = AsyncMock(return_value={"_key": "o1"})

        with pytest.raises(HTTPException) as exc_info:
            await toggle_connector_instance("c1", req, graph_provider=graph_provider)
        assert exc_info.value.status_code == 401

    async def test_instance_not_found_raises_404(self):
        from app.connectors.api.router import toggle_connector_instance

        req = _make_request(is_admin=True, body={"type": "sync"})
        graph_provider = AsyncMock()
        graph_provider.get_document = AsyncMock(return_value={"_key": "o1"})
        req.app.state.connector_registry.get_connector_instance = AsyncMock(
            return_value=None
        )

        with pytest.raises(HTTPException) as exc_info:
            await toggle_connector_instance("c1", req, graph_provider=graph_provider)
        assert exc_info.value.status_code == 404

    async def test_team_connector_non_admin_raises_403(self):
        from app.connectors.api.router import toggle_connector_instance

        req = _make_request(is_admin=False, body={"type": "sync"})
        graph_provider = AsyncMock()
        graph_provider.get_document = AsyncMock(return_value={"_key": "o1"})
        instance = _make_instance(scope="team", created_by="u1")
        req.app.state.connector_registry.get_connector_instance = AsyncMock(
            return_value=instance
        )

        with patch("app.connectors.api.router.check_beta_connector_access", new_callable=AsyncMock):
            with pytest.raises(HTTPException) as exc_info:
                await toggle_connector_instance(
                    "c1", req, graph_provider=graph_provider
                )
        assert exc_info.value.status_code == 403

    async def test_personal_connector_non_creator_raises_403(self):
        from app.connectors.api.router import toggle_connector_instance

        req = _make_request(user_id="u1", is_admin=False, body={"type": "sync"})
        graph_provider = AsyncMock()
        graph_provider.get_document = AsyncMock(return_value={"_key": "o1"})
        instance = _make_instance(scope="personal", created_by="other_user")
        req.app.state.connector_registry.get_connector_instance = AsyncMock(
            return_value=instance
        )

        with patch("app.connectors.api.router.check_beta_connector_access", new_callable=AsyncMock):
            with pytest.raises(HTTPException) as exc_info:
                await toggle_connector_instance(
                    "c1", req, graph_provider=graph_provider
                )
        assert exc_info.value.status_code == 403

    async def test_agent_toggle_not_supported_raises_400(self):
        from app.connectors.api.router import toggle_connector_instance

        req = _make_request(is_admin=True, body={"type": "agent"})
        graph_provider = AsyncMock()
        graph_provider.get_document = AsyncMock(return_value={"_key": "o1"})
        instance = _make_instance(
            scope="team",
            created_by="u1",
            extra={"supportsAgent": False, "isAgentActive": False},
        )
        req.app.state.connector_registry.get_connector_instance = AsyncMock(
            return_value=instance
        )

        with patch("app.connectors.api.router.check_beta_connector_access", new_callable=AsyncMock):
            with pytest.raises(HTTPException) as exc_info:
                await toggle_connector_instance(
                    "c1", req, graph_provider=graph_provider
                )
        assert exc_info.value.status_code == 400

    async def test_sync_toggle_success(self):
        from app.connectors.api.router import toggle_connector_instance

        req = _make_request(is_admin=True, body={"type": "sync", "fullSync": False})
        graph_provider = AsyncMock()
        graph_provider.get_document = AsyncMock(
            return_value={"_key": "o1", "accountType": "free"}
        )
        instance = _make_instance(
            scope="team",
            created_by="u1",
            auth_type="API_TOKEN",
            is_active=True,
            is_configured=True,
        )
        req.app.state.connector_registry.get_connector_instance = AsyncMock(
            return_value=instance
        )
        req.app.state.connector_registry.update_connector_instance = AsyncMock(
            return_value=True
        )

        with patch("app.connectors.api.router.check_beta_connector_access", new_callable=AsyncMock), \
             patch("app.connectors.api.router.get_epoch_timestamp_in_ms", return_value=1000):
            result = await toggle_connector_instance(
                "c1", req, graph_provider=graph_provider
            )

        assert result["success"] is True

    async def test_update_failure_raises_404(self):
        from app.connectors.api.router import toggle_connector_instance

        req = _make_request(is_admin=True, body={"type": "sync"})
        graph_provider = AsyncMock()
        graph_provider.get_document = AsyncMock(
            return_value={"_key": "o1", "accountType": "free"}
        )
        instance = _make_instance(
            scope="team", created_by="u1", auth_type="API_TOKEN",
            is_active=True, is_configured=True,
        )
        req.app.state.connector_registry.get_connector_instance = AsyncMock(
            return_value=instance
        )
        req.app.state.connector_registry.update_connector_instance = AsyncMock(
            return_value=False
        )

        with patch("app.connectors.api.router.check_beta_connector_access", new_callable=AsyncMock):
            with pytest.raises(HTTPException) as exc_info:
                await toggle_connector_instance(
                    "c1", req, graph_provider=graph_provider
                )
        assert exc_info.value.status_code == 404

    async def test_enable_oauth_without_credentials_raises_400(self):
        from app.connectors.api.router import toggle_connector_instance

        req = _make_request(is_admin=True, body={"type": "sync"})
        graph_provider = AsyncMock()
        graph_provider.get_document = AsyncMock(
            return_value={"_key": "o1", "accountType": "free"}
        )
        instance = _make_instance(
            scope="team", created_by="u1", auth_type="OAUTH",
            is_active=False, is_configured=True,
        )
        req.app.state.connector_registry.get_connector_instance = AsyncMock(
            return_value=instance
        )

        config_service = req.app.container.config_service()
        config_service.get_config = AsyncMock(return_value=None)

        with patch("app.connectors.api.router.check_beta_connector_access", new_callable=AsyncMock):
            with pytest.raises(HTTPException) as exc_info:
                await toggle_connector_instance(
                    "c1", req, graph_provider=graph_provider
                )
        assert exc_info.value.status_code == 400

    async def test_enable_non_oauth_not_configured_raises_400(self):
        from app.connectors.api.router import toggle_connector_instance

        req = _make_request(is_admin=True, body={"type": "sync"})
        graph_provider = AsyncMock()
        graph_provider.get_document = AsyncMock(
            return_value={"_key": "o1", "accountType": "free"}
        )
        instance = _make_instance(
            scope="team", created_by="u1", auth_type="API_TOKEN",
            is_active=False, is_configured=False,
        )
        req.app.state.connector_registry.get_connector_instance = AsyncMock(
            return_value=instance
        )

        with patch("app.connectors.api.router.check_beta_connector_access", new_callable=AsyncMock):
            with pytest.raises(HTTPException) as exc_info:
                await toggle_connector_instance(
                    "c1", req, graph_provider=graph_provider
                )
        assert exc_info.value.status_code == 400

    async def test_agent_not_configured_raises_400(self):
        from app.connectors.api.router import toggle_connector_instance

        req = _make_request(is_admin=True, body={"type": "agent"})
        graph_provider = AsyncMock()
        graph_provider.get_document = AsyncMock(
            return_value={"_key": "o1", "accountType": "free"}
        )
        instance = _make_instance(
            scope="team", created_by="u1",
            is_active=True, is_configured=False,
            extra={"supportsAgent": True, "isAgentActive": False},
        )
        req.app.state.connector_registry.get_connector_instance = AsyncMock(
            return_value=instance
        )

        with patch("app.connectors.api.router.check_beta_connector_access", new_callable=AsyncMock):
            with pytest.raises(HTTPException) as exc_info:
                await toggle_connector_instance(
                    "c1", req, graph_provider=graph_provider
                )
        assert exc_info.value.status_code == 400

    async def test_disable_sync_sends_event_and_cleans_up(self):
        from app.connectors.api.router import toggle_connector_instance

        req = _make_request(is_admin=True, body={"type": "sync", "fullSync": False})
        graph_provider = AsyncMock()
        graph_provider.get_document = AsyncMock(
            return_value={"_key": "o1", "accountType": "free"}
        )
        instance = _make_instance(
            scope="team", created_by="u1", auth_type="API_TOKEN",
            is_active=True, is_configured=True,
        )
        req.app.state.connector_registry.get_connector_instance = AsyncMock(
            return_value=instance
        )
        req.app.state.connector_registry.update_connector_instance = AsyncMock(
            return_value=True
        )

        # Set up a connector in the connectors_map to test cleanup
        mock_existing_connector = AsyncMock()
        mock_existing_connector.cleanup = AsyncMock()
        req.app.container.connectors_map = {"c1": mock_existing_connector}

        producer = req.app.container.messaging_producer
        producer.send_message = AsyncMock()

        with patch("app.connectors.api.router.check_beta_connector_access", new_callable=AsyncMock), \
             patch("app.connectors.api.router.get_epoch_timestamp_in_ms", return_value=1000):
            result = await toggle_connector_instance(
                "c1", req, graph_provider=graph_provider
            )

        assert result["success"] is True
        producer.send_message.assert_called_once()
        mock_existing_connector.cleanup.assert_called_once()


# ===========================================================================
# Route handler tests: delete_connector_instance
# ===========================================================================

class TestDeleteConnectorInstance:
    """Tests for delete_connector_instance route handler."""

    async def test_unauthenticated_raises_401(self):
        from app.connectors.api.router import delete_connector_instance

        req = _make_request()
        req.state.user = {"userId": None, "orgId": "o1"}

        with pytest.raises(HTTPException) as exc_info:
            await delete_connector_instance("c1", req, graph_provider=AsyncMock())
        assert exc_info.value.status_code == 401

    async def test_instance_not_found_raises_404(self):
        from app.connectors.api.router import delete_connector_instance

        req = _make_request(is_admin=True)
        req.app.state.connector_registry.get_connector_instance = AsyncMock(
            return_value=None
        )

        with pytest.raises(HTTPException) as exc_info:
            await delete_connector_instance("c1", req, graph_provider=AsyncMock())
        assert exc_info.value.status_code == 404

    async def test_already_deleting_raises_409(self):
        from app.connectors.api.router import delete_connector_instance

        req = _make_request(is_admin=True)
        instance = _make_instance(scope="team", created_by="u1", extra={"status": "DELETING"})
        req.app.state.connector_registry.get_connector_instance = AsyncMock(
            return_value=instance
        )

        with patch("app.connectors.api.router.check_beta_connector_access", new_callable=AsyncMock), \
             patch("app.connectors.api.router._validate_connector_deletion_permissions"):
            with pytest.raises(HTTPException) as exc_info:
                await delete_connector_instance("c1", req, graph_provider=AsyncMock())
        assert exc_info.value.status_code == 409

    async def test_success_returns_202(self):
        from app.connectors.api.router import delete_connector_instance

        req = _make_request(is_admin=True)
        instance = _make_instance(scope="team", created_by="u1")
        req.app.state.connector_registry.get_connector_instance = AsyncMock(
            return_value=instance
        )

        graph_provider = AsyncMock()
        graph_provider.batch_upsert_nodes = AsyncMock()
        producer = req.app.container.messaging_producer
        producer.send_message = AsyncMock()

        with patch("app.connectors.api.router.check_beta_connector_access", new_callable=AsyncMock), \
             patch("app.connectors.api.router._validate_connector_deletion_permissions"), \
             patch("app.connectors.api.router.get_epoch_timestamp_in_ms", return_value=1000):
            result = await delete_connector_instance(
                "c1", req, graph_provider=graph_provider
            )

        assert result.status_code == 202

    async def test_team_connector_non_admin_raises_403(self):
        from app.connectors.api.router import delete_connector_instance

        req = _make_request(is_admin=False)
        instance = _make_instance(scope="team", created_by="u1")
        req.app.state.connector_registry.get_connector_instance = AsyncMock(
            return_value=instance
        )

        with patch("app.connectors.api.router.check_beta_connector_access", new_callable=AsyncMock):
            with pytest.raises(HTTPException) as exc_info:
                await delete_connector_instance("c1", req, graph_provider=AsyncMock())
        assert exc_info.value.status_code == 403


# ===========================================================================
# Route handler tests: get_connector_schema
# ===========================================================================

class TestGetConnectorSchema:
    """Tests for get_connector_schema route handler."""

    async def test_connector_not_found_raises_404(self):
        from app.connectors.api.router import get_connector_schema

        req = _make_request()
        req.app.state.connector_registry.get_connector_metadata = AsyncMock(
            return_value=None
        )

        with patch("app.connectors.api.router.check_beta_connector_access", new_callable=AsyncMock):
            with pytest.raises(HTTPException) as exc_info:
                await get_connector_schema("UNKNOWN", req)
        assert exc_info.value.status_code == 404

    async def test_success_returns_schema(self):
        from app.connectors.api.router import get_connector_schema

        req = _make_request()
        req.app.state.connector_registry.get_connector_metadata = AsyncMock(
            return_value={
                "config": {
                    "auth": {"field1": "val1"},
                    "filters": {"sync": {}},
                }
            }
        )

        with patch("app.connectors.api.router.check_beta_connector_access", new_callable=AsyncMock):
            result = await get_connector_schema("GMAIL", req)

        assert result["success"] is True
        assert "schema" in result


# ===========================================================================
# Route handler tests: get_active_agent_instances
# ===========================================================================

class TestGetActiveAgentInstances:
    """Tests for get_active_agent_instances route handler."""

    async def test_unauthenticated_raises(self):
        from app.connectors.api.router import get_active_agent_instances

        req = _make_request()
        req.state.user = {"userId": None, "orgId": "o1"}

        with pytest.raises(HTTPException):
            await get_active_agent_instances(
                req, scope=None, page=1, limit=20, search=None
            )

    async def test_invalid_scope_raises(self):
        from app.connectors.api.router import get_active_agent_instances

        req = _make_request()

        with pytest.raises(HTTPException):
            await get_active_agent_instances(
                req, scope="invalid", page=1, limit=20, search=None
            )

    async def test_success(self):
        from app.connectors.api.router import get_active_agent_instances

        req = _make_request()
        req.app.state.connector_registry.get_active_agent_connector_instances = AsyncMock(
            return_value={
                "connectors": [{"_key": "c1", "type": "GMAIL"}],
                "total": 1,
            }
        )

        result = await get_active_agent_instances(
            req, scope=None, page=1, limit=20, search=None
        )
        assert result["success"] is True
        assert "connectors" in result


# ===========================================================================
# Route handler tests: get_oauth_config_registry
# ===========================================================================

class TestGetOAuthConfigRegistryRoute:
    """Tests for get_oauth_config_registry route handler."""

    async def test_success(self):
        from app.connectors.api.router import get_oauth_config_registry

        req = _make_request()
        mock_registry = MagicMock()
        mock_registry.get_oauth_config_registry_connectors = AsyncMock(
            return_value={"connectors": [], "total": 0}
        )

        with patch(
            _OAUTH_REGISTRY_PATCH,
            return_value=mock_registry,
        ):
            result = await get_oauth_config_registry(req, page=1, limit=20, search=None)

        assert result["success"] is True

    async def test_error_raises_500(self):
        from app.connectors.api.router import get_oauth_config_registry

        req = _make_request()

        with patch(
            _OAUTH_REGISTRY_PATCH,
            side_effect=RuntimeError("registry error"),
        ):
            with pytest.raises(HTTPException) as exc_info:
                await get_oauth_config_registry(req, page=1, limit=20, search=None)
        assert exc_info.value.status_code == 500


# ===========================================================================
# Route handler tests: get_oauth_config_registry_by_type
# ===========================================================================

class TestGetOAuthConfigRegistryByType:
    """Tests for get_oauth_config_registry_by_type route handler."""

    async def test_not_found_raises_404(self):
        from app.connectors.api.router import get_oauth_config_registry_by_type

        req = _make_request()
        mock_registry = MagicMock()
        mock_registry.get_connector_registry_info.return_value = None

        with patch(
            _OAUTH_REGISTRY_PATCH,
            return_value=mock_registry,
        ):
            with pytest.raises(HTTPException) as exc_info:
                await get_oauth_config_registry_by_type("UNKNOWN", req)
        assert exc_info.value.status_code == 404

    async def test_success(self):
        from app.connectors.api.router import get_oauth_config_registry_by_type

        req = _make_request()
        mock_registry = MagicMock()
        mock_registry.get_connector_registry_info.return_value = {
            "type": "GMAIL",
            "name": "Gmail",
        }

        with patch(
            _OAUTH_REGISTRY_PATCH,
            return_value=mock_registry,
        ):
            result = await get_oauth_config_registry_by_type("GMAIL", req)

        assert result["success"] is True
        assert result["connector"]["type"] == "GMAIL"


# ===========================================================================
# Route handler tests: get_all_oauth_configs
# ===========================================================================

class TestGetAllOAuthConfigs:
    """Tests for get_all_oauth_configs route handler."""

    async def test_unauthenticated_raises_401(self):
        from app.connectors.api.router import get_all_oauth_configs

        req = _make_request()
        req.state.user = {"userId": None, "orgId": "o1"}

        with pytest.raises(HTTPException) as exc_info:
            await get_all_oauth_configs(
                req, page=1, limit=20, search=None, config_service=AsyncMock()
            )
        assert exc_info.value.status_code == 401

    async def test_no_oauth_connectors_returns_empty(self):
        from app.connectors.api.router import get_all_oauth_configs

        req = _make_request()
        mock_registry = MagicMock()
        mock_registry.get_oauth_connectors.return_value = []

        with patch(
            _OAUTH_REGISTRY_PATCH,
            return_value=mock_registry,
        ):
            result = await get_all_oauth_configs(
                req, page=1, limit=20, search=None, config_service=AsyncMock()
            )

        assert result["success"] is True
        assert result["oauthConfigs"] == []
        assert result["pagination"]["totalItems"] == 0

    async def test_returns_filtered_configs(self):
        from app.connectors.api.router import get_all_oauth_configs

        req = _make_request()
        mock_registry = MagicMock()
        mock_registry.get_oauth_connectors.return_value = ["GMAIL"]

        config_service = AsyncMock()
        config_service.get_config = AsyncMock(return_value=[
            {
                "_id": "c1",
                "orgId": "o1",
                "oauthInstanceName": "My Gmail",
                "connectorType": "GMAIL",
                "createdAtTimestamp": 1000,
                "updatedAtTimestamp": 2000,
            }
        ])

        with patch(
            _OAUTH_REGISTRY_PATCH,
            return_value=mock_registry,
        ):
            result = await get_all_oauth_configs(
                req, page=1, limit=20, search=None, config_service=config_service
            )

        assert result["success"] is True
        assert len(result["oauthConfigs"]) == 1

    async def test_search_filters_results(self):
        from app.connectors.api.router import get_all_oauth_configs

        req = _make_request()
        mock_registry = MagicMock()
        mock_registry.get_oauth_connectors.return_value = ["GMAIL"]

        config_service = AsyncMock()
        config_service.get_config = AsyncMock(return_value=[
            {"_id": "c1", "orgId": "o1", "oauthInstanceName": "Gmail Config", "connectorType": "GMAIL"},
            {"_id": "c2", "orgId": "o1", "oauthInstanceName": "Other", "connectorType": "GMAIL"},
        ])

        with patch(
            _OAUTH_REGISTRY_PATCH,
            return_value=mock_registry,
        ):
            result = await get_all_oauth_configs(
                req, page=1, limit=20, search="Gmail Config", config_service=config_service
            )

        assert result["success"] is True
        assert len(result["oauthConfigs"]) == 1

    async def test_pagination(self):
        from app.connectors.api.router import get_all_oauth_configs

        req = _make_request()
        mock_registry = MagicMock()
        mock_registry.get_oauth_connectors.return_value = ["GMAIL"]

        configs = [
            {"_id": f"c{i}", "orgId": "o1", "oauthInstanceName": f"Config {i}", "connectorType": "GMAIL"}
            for i in range(5)
        ]
        config_service = AsyncMock()
        config_service.get_config = AsyncMock(return_value=configs)

        with patch(
            _OAUTH_REGISTRY_PATCH,
            return_value=mock_registry,
        ):
            result = await get_all_oauth_configs(
                req, page=1, limit=2, search=None, config_service=config_service
            )

        assert result["pagination"]["totalItems"] == 5
        assert result["pagination"]["totalPages"] == 3
        assert result["pagination"]["hasNext"] is True
        assert result["pagination"]["hasPrev"] is False
        assert len(result["oauthConfigs"]) == 2


# ===========================================================================
# Route handler tests: create_oauth_config
# ===========================================================================

class TestCreateOAuthConfig:
    """Tests for create_oauth_config route handler."""

    async def test_non_admin_raises_403(self):
        from app.connectors.api.router import create_oauth_config

        req = _make_request(is_admin=False)
        with pytest.raises(HTTPException) as exc_info:
            await create_oauth_config("GMAIL", req, config_service=AsyncMock())
        assert exc_info.value.status_code == 403

    async def test_missing_name_raises_400(self):
        from app.connectors.api.router import create_oauth_config

        req = _make_request(is_admin=True, body={"oauthInstanceName": "", "config": {"key": "val"}})
        with pytest.raises(HTTPException) as exc_info:
            await create_oauth_config("GMAIL", req, config_service=AsyncMock())
        assert exc_info.value.status_code == 400

    async def test_missing_config_raises_400(self):
        from app.connectors.api.router import create_oauth_config

        req = _make_request(
            is_admin=True,
            body={"oauthInstanceName": "My Config", "config": {}},
        )
        with pytest.raises(HTTPException) as exc_info:
            await create_oauth_config("GMAIL", req, config_service=AsyncMock())
        assert exc_info.value.status_code == 400

    async def test_name_conflict_raises_409(self):
        from app.connectors.api.router import create_oauth_config

        req = _make_request(
            is_admin=True,
            body={"oauthInstanceName": "Existing", "config": {"clientId": "cid"}},
        )

        config_service = AsyncMock()
        config_service.get_config = AsyncMock(return_value=[
            {"_id": "c1", "oauthInstanceName": "Existing", "orgId": "o1"}
        ])

        mock_registry = MagicMock()
        mock_registry.get_metadata.return_value = {}

        with patch(
            _OAUTH_REGISTRY_PATCH,
            return_value=mock_registry,
        ):
            with pytest.raises(HTTPException) as exc_info:
                await create_oauth_config("GMAIL", req, config_service=config_service)
        assert exc_info.value.status_code == 409

    async def test_success(self):
        from app.connectors.api.router import create_oauth_config

        req = _make_request(
            is_admin=True,
            body={
                "oauthInstanceName": "New Config",
                "config": {"clientId": "cid", "clientSecret": "cs"},
                "baseUrl": "https://example.com",
            },
        )

        config_service = AsyncMock()
        config_service.get_config = AsyncMock(return_value=[])
        config_service.set_config = AsyncMock(return_value=True)

        mock_registry = MagicMock()
        mock_registry.get_metadata.return_value = {
            "iconPath": "/icon.svg",
            "appGroup": "Google",
            "appDescription": "Gmail",
            "appCategories": ["email"],
        }

        with patch(
            _OAUTH_REGISTRY_PATCH,
            return_value=mock_registry,
        ), patch(
            "app.connectors.api.router._update_oauth_infrastructure_fields",
            new_callable=AsyncMock,
        ):
            result = await create_oauth_config("GMAIL", req, config_service=config_service)

        assert result["success"] is True
        assert "oauthConfig" in result

    async def test_set_config_failure_raises_500(self):
        from app.connectors.api.router import create_oauth_config

        req = _make_request(
            is_admin=True,
            body={
                "oauthInstanceName": "Config",
                "config": {"clientId": "cid"},
            },
        )

        config_service = AsyncMock()
        config_service.get_config = AsyncMock(return_value=[])
        config_service.set_config = AsyncMock(return_value=False)

        mock_registry = MagicMock()
        mock_registry.get_metadata.return_value = {}

        with patch(
            _OAUTH_REGISTRY_PATCH,
            return_value=mock_registry,
        ), patch(
            "app.connectors.api.router._update_oauth_infrastructure_fields",
            new_callable=AsyncMock,
        ):
            with pytest.raises(HTTPException) as exc_info:
                await create_oauth_config("GMAIL", req, config_service=config_service)
        assert exc_info.value.status_code == 500


# ===========================================================================
# Route handler tests: list_oauth_configs
# ===========================================================================

class TestListOAuthConfigs:
    """Tests for list_oauth_configs route handler."""

    async def test_success(self):
        from app.connectors.api.router import list_oauth_configs

        req = _make_request()

        config_service = AsyncMock()
        config_service.get_config = AsyncMock(return_value=[])

        mock_registry = MagicMock()
        mock_registry.get_oauth_configs_for_connector = AsyncMock(
            return_value={"oauthConfigs": [], "pagination": {"page": 1}}
        )

        with patch(
            _OAUTH_REGISTRY_PATCH,
            return_value=mock_registry,
        ):
            result = await list_oauth_configs(
                "GMAIL", req, page=1, limit=20, search=None,
                config_service=config_service
            )

        assert result["success"] is True

    async def test_unauthenticated_raises_401(self):
        from app.connectors.api.router import list_oauth_configs

        req = _make_request()
        req.state.user = {"userId": None, "orgId": "o1"}

        with pytest.raises(HTTPException) as exc_info:
            await list_oauth_configs(
                "GMAIL", req, page=1, limit=20, search=None,
                config_service=AsyncMock()
            )
        assert exc_info.value.status_code == 401


# ===========================================================================
# Route handler tests: get_oauth_config_by_id
# ===========================================================================

class TestGetOAuthConfigById:
    """Tests for get_oauth_config_by_id route handler."""

    async def test_not_found_raises_404(self):
        from app.connectors.api.router import get_oauth_config_by_id

        req = _make_request()

        config_service = AsyncMock()
        config_service.get_config = AsyncMock(return_value=[])

        with pytest.raises(HTTPException) as exc_info:
            await get_oauth_config_by_id(
                "GMAIL", "missing", req, config_service=config_service
            )
        assert exc_info.value.status_code == 404

    async def test_admin_gets_full_config(self):
        from app.connectors.api.router import get_oauth_config_by_id

        req = _make_request(is_admin=True)

        config_service = AsyncMock()
        config_service.get_config = AsyncMock(return_value=[
            {
                "_id": "cfg1",
                "orgId": "o1",
                "oauthInstanceName": "Test",
                "config": {"clientId": "secret"},
            }
        ])

        result = await get_oauth_config_by_id(
            "GMAIL", "cfg1", req, config_service=config_service
        )

        assert result["success"] is True
        assert "config" in result["oauthConfig"]
        assert result["oauthConfig"]["config"]["clientId"] == "secret"

    async def test_non_admin_gets_essential_fields(self):
        from app.connectors.api.router import get_oauth_config_by_id

        req = _make_request(is_admin=False)

        config_service = AsyncMock()
        config_service.get_config = AsyncMock(return_value=[
            {
                "_id": "cfg1",
                "orgId": "o1",
                "oauthInstanceName": "Test",
                "config": {"clientId": "secret"},
            }
        ])

        result = await get_oauth_config_by_id(
            "GMAIL", "cfg1", req, config_service=config_service
        )

        assert result["success"] is True
        assert "config" not in result["oauthConfig"]


# ===========================================================================
# Route handler tests: update_oauth_config
# ===========================================================================

class TestUpdateOAuthConfig:
    """Tests for update_oauth_config route handler."""

    async def test_non_admin_raises_403(self):
        from app.connectors.api.router import update_oauth_config

        req = _make_request(is_admin=False, body={})

        with pytest.raises(HTTPException) as exc_info:
            await update_oauth_config("GMAIL", "cfg1", req, config_service=AsyncMock())
        assert exc_info.value.status_code == 403

    async def test_not_found_raises_404(self):
        from app.connectors.api.router import update_oauth_config

        req = _make_request(is_admin=True, body={})

        config_service = AsyncMock()
        config_service.get_config = AsyncMock(return_value=[])

        with pytest.raises(HTTPException) as exc_info:
            await update_oauth_config(
                "GMAIL", "missing", req, config_service=config_service
            )
        assert exc_info.value.status_code == 404

    async def test_name_conflict_raises_409(self):
        from app.connectors.api.router import update_oauth_config

        req = _make_request(
            is_admin=True,
            body={"oauthInstanceName": "Existing Name"},
        )

        config_service = AsyncMock()
        config_service.get_config = AsyncMock(return_value=[
            {"_id": "cfg1", "orgId": "o1", "oauthInstanceName": "Old Name"},
            {"_id": "cfg2", "orgId": "o1", "oauthInstanceName": "Existing Name"},
        ])

        with pytest.raises(HTTPException) as exc_info:
            await update_oauth_config(
                "GMAIL", "cfg1", req, config_service=config_service
            )
        assert exc_info.value.status_code == 409

    async def test_success(self):
        from app.connectors.api.router import update_oauth_config

        req = _make_request(
            is_admin=True,
            body={
                "oauthInstanceName": "Updated Name",
                "config": {"clientId": "new_cid"},
            },
        )

        config_service = AsyncMock()
        config_service.get_config = AsyncMock(return_value=[
            {
                "_id": "cfg1",
                "orgId": "o1",
                "oauthInstanceName": "Old Name",
                "config": {"clientId": "old_cid"},
            }
        ])
        config_service.set_config = AsyncMock(return_value=True)

        with patch(
            "app.connectors.api.router._update_oauth_infrastructure_fields",
            new_callable=AsyncMock,
        ):
            result = await update_oauth_config(
                "GMAIL", "cfg1", req, config_service=config_service
            )

        assert result["success"] is True
        assert "Updated Name" in result["oauthConfig"]["oauthInstanceName"]

    async def test_set_config_failure_raises_500(self):
        from app.connectors.api.router import update_oauth_config

        req = _make_request(
            is_admin=True,
            body={"oauthInstanceName": "Updated"},
        )

        config_service = AsyncMock()
        config_service.get_config = AsyncMock(return_value=[
            {"_id": "cfg1", "orgId": "o1", "oauthInstanceName": "Old"},
        ])
        config_service.set_config = AsyncMock(return_value=False)

        with patch(
            "app.connectors.api.router._update_oauth_infrastructure_fields",
            new_callable=AsyncMock,
        ):
            with pytest.raises(HTTPException) as exc_info:
                await update_oauth_config(
                    "GMAIL", "cfg1", req, config_service=config_service
                )
        assert exc_info.value.status_code == 500


# ===========================================================================
# Route handler tests: delete_oauth_config
# ===========================================================================

class TestDeleteOAuthConfig:
    """Tests for delete_oauth_config route handler."""

    async def test_non_admin_raises_403(self):
        from app.connectors.api.router import delete_oauth_config

        req = _make_request(is_admin=False)

        with pytest.raises(HTTPException) as exc_info:
            await delete_oauth_config("GMAIL", "cfg1", req, config_service=AsyncMock())
        assert exc_info.value.status_code == 403

    async def test_not_found_raises_404(self):
        from app.connectors.api.router import delete_oauth_config

        req = _make_request(is_admin=True)

        config_service = AsyncMock()
        config_service.get_config = AsyncMock(return_value=[])

        with pytest.raises(HTTPException) as exc_info:
            await delete_oauth_config(
                "GMAIL", "missing", req, config_service=config_service
            )
        assert exc_info.value.status_code == 404

    async def test_success(self):
        from app.connectors.api.router import delete_oauth_config

        req = _make_request(is_admin=True)

        config_service = AsyncMock()
        config_service.get_config = AsyncMock(return_value=[
            {"_id": "cfg1", "orgId": "o1"},
        ])
        config_service.set_config = AsyncMock(return_value=True)

        result = await delete_oauth_config(
            "GMAIL", "cfg1", req, config_service=config_service
        )

        assert result["success"] is True
        # Verify config was removed
        config_service.set_config.assert_called_once()

    async def test_set_config_failure_raises_500(self):
        from app.connectors.api.router import delete_oauth_config

        req = _make_request(is_admin=True)

        config_service = AsyncMock()
        config_service.get_config = AsyncMock(return_value=[
            {"_id": "cfg1", "orgId": "o1"},
        ])
        config_service.set_config = AsyncMock(return_value=False)

        with pytest.raises(HTTPException) as exc_info:
            await delete_oauth_config(
                "GMAIL", "cfg1", req, config_service=config_service
            )
        assert exc_info.value.status_code == 500


# ===========================================================================
# Route handler tests: get_filter_field_options
# ===========================================================================

class TestGetFilterFieldOptions:
    """Tests for get_filter_field_options route handler."""

    async def test_unauthenticated_raises_401(self):
        from app.connectors.api.router import get_filter_field_options

        req = _make_request()
        req.state.user = {"userId": None, "orgId": "o1"}

        with pytest.raises(HTTPException) as exc_info:
            await get_filter_field_options(
                "c1", "space_keys", req, graph_provider=AsyncMock()
            )
        assert exc_info.value.status_code == 401

    async def test_non_authenticated_connector_raises_400(self):
        from app.connectors.api.router import get_filter_field_options

        req = _make_request(is_admin=True)
        instance = _make_instance(
            auth_type="OAUTH",
            is_authenticated=False,
            scope="team",
            created_by="u1",
        )
        req.app.state.connector_registry.get_connector_instance = AsyncMock(
            return_value=instance
        )

        with pytest.raises(HTTPException) as exc_info:
            await get_filter_field_options(
                "c1", "space_keys", req, graph_provider=AsyncMock()
            )
        assert exc_info.value.status_code == 400

    async def test_metadata_not_found_raises_404(self):
        from app.connectors.api.router import get_filter_field_options

        req = _make_request(is_admin=True)
        instance = _make_instance(scope="team", created_by="u1", auth_type="API_TOKEN")
        req.app.state.connector_registry.get_connector_instance = AsyncMock(
            return_value=instance
        )
        req.app.state.connector_registry.get_connector_metadata = AsyncMock(
            return_value=None
        )

        with patch("app.connectors.api.router.check_beta_connector_access", new_callable=AsyncMock):
            with pytest.raises(HTTPException) as exc_info:
                await get_filter_field_options(
                    "c1", "space_keys", req, graph_provider=AsyncMock()
                )
        assert exc_info.value.status_code == 404

    async def test_filter_field_not_found_raises_404(self):
        from app.connectors.api.router import get_filter_field_options

        req = _make_request(is_admin=True)
        instance = _make_instance(scope="team", created_by="u1", auth_type="API_TOKEN")
        req.app.state.connector_registry.get_connector_instance = AsyncMock(
            return_value=instance
        )
        req.app.state.connector_registry.get_connector_metadata = AsyncMock(
            return_value={"config": {"filters": {}}}
        )

        with patch("app.connectors.api.router.check_beta_connector_access", new_callable=AsyncMock):
            with pytest.raises(HTTPException) as exc_info:
                await get_filter_field_options(
                    "c1", "nonexistent", req, graph_provider=AsyncMock()
                )
        assert exc_info.value.status_code == 404

    async def test_non_dynamic_filter_raises_400(self):
        from app.connectors.api.router import get_filter_field_options

        req = _make_request(is_admin=True)
        instance = _make_instance(scope="team", created_by="u1", auth_type="API_TOKEN")
        req.app.state.connector_registry.get_connector_instance = AsyncMock(
            return_value=instance
        )
        req.app.state.connector_registry.get_connector_metadata = AsyncMock(
            return_value={
                "config": {
                    "filters": {
                        "sync": {
                            "schema": {
                                "fields": [
                                    {"name": "space_keys", "optionSourceType": "manual"}
                                ]
                            }
                        }
                    }
                }
            }
        )

        with patch("app.connectors.api.router.check_beta_connector_access", new_callable=AsyncMock):
            with pytest.raises(HTTPException) as exc_info:
                await get_filter_field_options(
                    "c1", "space_keys", req, graph_provider=AsyncMock()
                )
        assert exc_info.value.status_code == 400


# ===========================================================================
# Route handler tests: _ensure_connector_initialized
# ===========================================================================

class TestEnsureConnectorInitialized:
    """Tests for _ensure_connector_initialized helper."""

    async def test_already_initialized(self):
        from app.connectors.api.router import _ensure_connector_initialized

        container = MagicMock()
        container.connectors_map = {"c1": MagicMock()}

        result = await _ensure_connector_initialized(
            container=container,
            connector_id="c1",
            connector_type="CONFLUENCE",
            connector_registry=AsyncMock(),
            graph_provider=AsyncMock(),
            user_id="u1",
            org_id="o1",
            is_admin=True,
            logger=logging.getLogger("test"),
        )

        assert result is not None

    async def test_initialization_failure_raises_500(self):
        from app.connectors.api.router import _ensure_connector_initialized

        container = MagicMock()
        container.config_service.return_value = AsyncMock()
        # Ensure no connectors_map so it doesn't short-circuit
        del container.connectors_map

        with patch(
            "app.connectors.api.router.ConnectorFactory.create_connector",
            new_callable=AsyncMock,
            return_value=None,
        ), patch(
            "app.connectors.core.base.data_store.graph_data_store.GraphDataStore",
        ):
            with pytest.raises(HTTPException) as exc_info:
                await _ensure_connector_initialized(
                    container=container,
                    connector_id="c1",
                    connector_type="CONFLUENCE",
                    connector_registry=AsyncMock(),
                    graph_provider=AsyncMock(),
                    user_id="u1",
                    org_id="o1",
                    is_admin=True,
                    logger=logging.getLogger("test"),
                )
        assert exc_info.value.status_code == 500

    async def test_init_returns_false_raises_400(self):
        from app.connectors.api.router import _ensure_connector_initialized

        container = MagicMock()
        container.config_service.return_value = AsyncMock()
        del container.connectors_map

        mock_connector = AsyncMock()
        mock_connector.init.return_value = False
        mock_connector.cleanup = AsyncMock()

        with patch(
            "app.connectors.api.router.ConnectorFactory.create_connector",
            new_callable=AsyncMock,
            return_value=mock_connector,
        ), patch(
            "app.connectors.core.base.data_store.graph_data_store.GraphDataStore",
        ):
            with pytest.raises(HTTPException) as exc_info:
                await _ensure_connector_initialized(
                    container=container,
                    connector_id="c1",
                    connector_type="CONFLUENCE",
                    connector_registry=AsyncMock(),
                    graph_provider=AsyncMock(),
                    user_id="u1",
                    org_id="o1",
                    is_admin=True,
                    logger=logging.getLogger("test"),
                )
        assert exc_info.value.status_code == 400

    async def test_connection_test_failure_raises_400(self):
        from app.connectors.api.router import _ensure_connector_initialized

        container = MagicMock()
        container.config_service.return_value = AsyncMock()
        del container.connectors_map

        mock_connector = AsyncMock()
        mock_connector.init.return_value = True
        mock_connector.test_connection_and_access.return_value = False
        mock_connector.cleanup = AsyncMock()

        with patch(
            "app.connectors.api.router.ConnectorFactory.create_connector",
            new_callable=AsyncMock,
            return_value=mock_connector,
        ), patch(
            "app.connectors.core.base.data_store.graph_data_store.GraphDataStore",
        ):
            with pytest.raises(HTTPException) as exc_info:
                await _ensure_connector_initialized(
                    container=container,
                    connector_id="c1",
                    connector_type="CONFLUENCE",
                    connector_registry=AsyncMock(),
                    graph_provider=AsyncMock(),
                    user_id="u1",
                    org_id="o1",
                    is_admin=True,
                    logger=logging.getLogger("test"),
                )
        assert exc_info.value.status_code == 400

    async def test_connection_test_exception_raises_500(self):
        from app.connectors.api.router import _ensure_connector_initialized

        container = MagicMock()
        container.config_service.return_value = AsyncMock()
        del container.connectors_map

        mock_connector = AsyncMock()
        mock_connector.init.return_value = True
        mock_connector.test_connection_and_access.side_effect = RuntimeError("network err")
        mock_connector.cleanup = AsyncMock()

        with patch(
            "app.connectors.api.router.ConnectorFactory.create_connector",
            new_callable=AsyncMock,
            return_value=mock_connector,
        ), patch(
            "app.connectors.core.base.data_store.graph_data_store.GraphDataStore",
        ):
            with pytest.raises(HTTPException) as exc_info:
                await _ensure_connector_initialized(
                    container=container,
                    connector_id="c1",
                    connector_type="CONFLUENCE",
                    connector_registry=AsyncMock(),
                    graph_provider=AsyncMock(),
                    user_id="u1",
                    org_id="o1",
                    is_admin=True,
                    logger=logging.getLogger("test"),
                )
        assert exc_info.value.status_code == 500

    async def test_successful_initialization(self):
        from app.connectors.api.router import _ensure_connector_initialized

        container = MagicMock()
        container.config_service.return_value = AsyncMock()
        del container.connectors_map

        mock_connector = AsyncMock()
        mock_connector.init.return_value = True
        mock_connector.test_connection_and_access.return_value = True

        registry = AsyncMock()
        registry.update_connector_instance = AsyncMock(return_value=True)

        with patch(
            "app.connectors.api.router.ConnectorFactory.create_connector",
            new_callable=AsyncMock,
            return_value=mock_connector,
        ), patch(
            "app.connectors.core.base.data_store.graph_data_store.GraphDataStore",
        ):
            result = await _ensure_connector_initialized(
                container=container,
                connector_id="c1",
                connector_type="CONFLUENCE",
                connector_registry=registry,
                graph_provider=AsyncMock(),
                user_id="u1",
                org_id="o1",
                is_admin=True,
                logger=logging.getLogger("test"),
            )

        assert result == mock_connector
        assert container.connectors_map["c1"] == mock_connector


# ===========================================================================
# _fetch_filter_options_from_api
# ===========================================================================

class TestFetchFilterOptionsFromApi:
    """Tests for _fetch_filter_options_from_api helper."""

    def _mock_aiohttp_session(self, status=200, json_data=None):
        """Create a properly mocked aiohttp ClientSession context manager."""
        mock_response = MagicMock()
        mock_response.status = status
        mock_response.json = AsyncMock(return_value=json_data or {})

        # response context manager (session.get(...) returns this)
        resp_cm = MagicMock()
        resp_cm.__aenter__ = AsyncMock(return_value=mock_response)
        resp_cm.__aexit__ = AsyncMock(return_value=False)

        mock_session = MagicMock()
        mock_session.get.return_value = resp_cm

        # session context manager (ClientSession() returns this)
        session_cm = MagicMock()
        session_cm.__aenter__ = AsyncMock(return_value=mock_session)
        session_cm.__aexit__ = AsyncMock(return_value=False)

        return session_cm

    async def test_with_access_token_object(self):
        from app.connectors.api.router import _fetch_filter_options_from_api

        token = MagicMock()
        token.access_token = "tok123"
        session_cm = self._mock_aiohttp_session(200, {"labels": []})

        with patch("aiohttp.ClientSession", return_value=session_cm):
            result = await _fetch_filter_options_from_api(
                "https://api.test/labels", "labels", token, "GMAIL"
            )
        assert isinstance(result, list)

    async def test_with_dict_access_token(self):
        from app.connectors.api.router import _fetch_filter_options_from_api

        token = {"access_token": "tok123"}
        session_cm = self._mock_aiohttp_session(200, {"labels": []})

        with patch("aiohttp.ClientSession", return_value=session_cm):
            result = await _fetch_filter_options_from_api(
                "https://api.test/labels", "labels", token, "GMAIL"
            )
        assert isinstance(result, list)

    async def test_non_200_returns_empty(self):
        from app.connectors.api.router import _fetch_filter_options_from_api

        session_cm = self._mock_aiohttp_session(403)

        with patch("aiohttp.ClientSession", return_value=session_cm):
            result = await _fetch_filter_options_from_api(
                "https://api.test/labels", "labels", {}, "GMAIL"
            )
        assert result == []

    async def test_exception_returns_empty(self):
        from app.connectors.api.router import _fetch_filter_options_from_api

        with patch("aiohttp.ClientSession", side_effect=RuntimeError("conn error")):
            result = await _fetch_filter_options_from_api(
                "https://api.test/labels", "labels", {}, "GMAIL"
            )
        assert result == []

    async def test_dict_with_api_token(self):
        from app.connectors.api.router import _fetch_filter_options_from_api

        token = {"api_token": "api_tok"}
        session_cm = self._mock_aiohttp_session(200, {})

        with patch("aiohttp.ClientSession", return_value=session_cm):
            result = await _fetch_filter_options_from_api(
                "https://api.test", "filters", token, "JIRA"
            )
        assert isinstance(result, list)

    async def test_dict_with_generic_token(self):
        from app.connectors.api.router import _fetch_filter_options_from_api

        token = {"token": "generic_tok"}
        session_cm = self._mock_aiohttp_session(200, {})

        with patch("aiohttp.ClientSession", return_value=session_cm):
            result = await _fetch_filter_options_from_api(
                "https://api.test", "filters", token, "CUSTOM"
            )
        assert isinstance(result, list)
