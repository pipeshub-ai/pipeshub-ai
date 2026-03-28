"""Tests for update_connector_instance_auth_config (lines 2700-3018).

Covers deep OAuth paths including:
- Admin + OAUTH with/without credentials
- OAuth config creation, update, fallback
- Non-OAUTH auth types (field filtering)
- OAuth metadata with redirect URI
- Connector scope fallback
- Cleanup of existing connector instances
- Error paths (update returns None, general exception)
"""

import logging
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from app.config.constants.arangodb import CollectionNames
from app.config.constants.http_status_code import HttpStatusCode
from app.connectors.api.router import update_connector_instance_auth_config

_ROUTER = "app.connectors.api.router"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_container(config_service, *, connectors_map=None):
    """Build a mock container with a real logger and the given config_service."""
    container = MagicMock()
    container.logger = MagicMock(return_value=logging.getLogger("test"))
    container.config_service = MagicMock(return_value=config_service)
    if connectors_map is not None:
        container.connectors_map = connectors_map
    return container


def _make_request(
    *,
    user: dict | None = None,
    headers: dict | None = None,
    body: dict | None = None,
    container: Any | None = None,
    connector_registry: Any | None = None,
):
    """Build a minimal mock FastAPI Request."""
    req = MagicMock()

    user_data = user or {"userId": "u1", "orgId": "o1"}
    req.state = MagicMock()
    req.state.user = MagicMock()
    req.state.user.get = lambda k, default=None: user_data.get(k, default)

    _headers = headers or {}
    req.headers = MagicMock()
    req.headers.get = lambda k, default=None: _headers.get(k, default)

    req.json = AsyncMock(return_value=body if body is not None else {})

    _container = container or MagicMock()
    req.app = MagicMock()
    req.app.container = _container
    req.app.state = MagicMock()
    req.app.state.connector_registry = connector_registry or AsyncMock()

    return req


def _make_config_service(
    instance_config: dict | None = None,
    oauth_configs: list | None = None,
    endpoints: dict | None = None,
):
    """Build an AsyncMock config_service whose get_config returns real dicts.

    The function under test calls ``existing_config.copy()`` and
    ``existing_oauth_configs`` as a list, so we must return genuine
    Python objects — never bare AsyncMock instances.
    """
    _instance_config = instance_config or {
        "auth": {"connectorScope": "personal"},
        "credentials": None,
    }
    _oauth_configs = oauth_configs if oauth_configs is not None else []
    _endpoints = endpoints or {
        "frontend": {"publicEndpoint": "https://app.example.com"},
    }

    async def _get_config(path, **kwargs):
        if "/services/connectors/" in path and path.endswith("/config"):
            return _instance_config
        if "/services/oauth/" in path:
            return _oauth_configs
        if path == "/services/endpoints":
            return _endpoints
        return kwargs.get("default", {})

    svc = AsyncMock()
    svc.get_config = AsyncMock(side_effect=_get_config)
    svc.set_config = AsyncMock(return_value=True)
    return svc


def _make_connector_registry(*, update_return=None):
    """Build an AsyncMock connector registry."""
    reg = AsyncMock()
    reg.get_connector_metadata = AsyncMock(return_value={
        "config": {
            "auth": {
                "schemas": {
                    "OAUTH": {"redirectUri": "oauth/callback"},
                },
                "oauthConfigs": {
                    "OAUTH": {
                        "authorizeUrl": "https://auth.example.com/authorize",
                        "tokenUrl": "https://auth.example.com/token",
                        "scopes": ["read", "write"],
                    },
                },
            },
        },
    })
    reg.update_connector_instance = AsyncMock(
        return_value=update_return if update_return is not None else {"_key": "conn1"}
    )
    return reg


def _base_instance(**overrides):
    """Return a default instance dict for get_validated_connector_instance."""
    inst = {
        "_key": "conn1",
        "type": "GOOGLE_DRIVE",
        "name": "Google Drive Connector",
        "isActive": False,
        "authType": "OAUTH",
        "scope": "personal",
        "createdBy": "u1",
    }
    inst.update(overrides)
    return inst


# Common patch targets
_PATCH_VALIDATE = f"{_ROUTER}.get_validated_connector_instance"
_PATCH_OAUTH_FIELDS = f"{_ROUTER}._get_oauth_field_names_from_registry"
_PATCH_NAME_CONFLICT = f"{_ROUTER}._check_oauth_name_conflict"
_PATCH_CREATE_OAUTH = f"{_ROUTER}._create_or_update_oauth_config"
_PATCH_OAUTH_PATH = f"{_ROUTER}._get_oauth_config_path"
_PATCH_TIMESTAMP = f"{_ROUTER}.get_epoch_timestamp_in_ms"


def _standard_patches(
    instance=None,
    oauth_fields=None,
    create_oauth_return="oauth-123",
):
    """Return a dict of common patch kwargs for stacking decorators."""
    return {
        "instance": instance or _base_instance(),
        "oauth_fields": oauth_fields or ["clientId", "clientSecret"],
        "create_oauth_return": create_oauth_return,
    }


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestAdminOAuthCreatesNewConfig:
    """1. Admin + OAUTH + has credentials + no existing oauth_app_id
       -> creates new OAuth config."""

    async def test_creates_new_oauth_config(self):
        config_service = _make_config_service()
        registry = _make_connector_registry()
        container = _make_container(config_service)

        body = {
            "auth": {"clientId": "cid", "clientSecret": "csec"},
            "baseUrl": "https://app.example.com",
        }
        request = _make_request(
            headers={"X-Is-Admin": "true"},
            body=body,
            container=container,
            connector_registry=registry,
        )
        graph_provider = AsyncMock()
        graph_provider.get_document = AsyncMock(return_value={"scope": "personal"})

        with (
            patch(_PATCH_VALIDATE, new_callable=AsyncMock, return_value=_base_instance()),
            patch(_PATCH_OAUTH_FIELDS, return_value=["clientId", "clientSecret"]),
            patch(_PATCH_NAME_CONFLICT) as mock_conflict,
            patch(_PATCH_CREATE_OAUTH, new_callable=AsyncMock, return_value="oauth-new-1"),
            patch(_PATCH_OAUTH_PATH, return_value="/services/oauth/googledrive"),
            patch(_PATCH_TIMESTAMP, return_value=1234567890),
        ):
            result = await update_connector_instance_auth_config(
                "conn1", request, graph_provider
            )

        assert result["success"] is True
        assert result["config"]["auth"]["oauthConfigId"] == "oauth-new-1"
        # Credentials should be cleared
        assert result["config"]["credentials"] is None
        assert result["config"]["oauth"] is None
        # clientId/clientSecret should be filtered out for OAUTH type
        assert "clientId" not in result["config"]["auth"]
        assert "clientSecret" not in result["config"]["auth"]
        # Name conflict was checked (no oauth_app_id means new config)
        mock_conflict.assert_called_once()


class TestAdminOAuthUpdatesExistingConfigFound:
    """2. Admin + OAUTH + has credentials + has oauth_app_id + config found
       -> updates existing OAuth config."""

    async def test_updates_existing_oauth_config(self):
        existing_oauth_cfgs = [
            {
                "_id": "oauth-existing",
                "orgId": "o1",
                "oauthInstanceName": "My Drive",
                "clientId": "old-cid",
            },
        ]
        config_service = _make_config_service(oauth_configs=existing_oauth_cfgs)
        registry = _make_connector_registry()
        container = _make_container(config_service)

        body = {
            "auth": {
                "clientId": "new-cid",
                "clientSecret": "new-csec",
                "oauthConfigId": "oauth-existing",
            },
            "baseUrl": "https://app.example.com",
        }
        request = _make_request(
            headers={"X-Is-Admin": "true"},
            body=body,
            container=container,
            connector_registry=registry,
        )
        graph_provider = AsyncMock()
        graph_provider.get_document = AsyncMock(return_value={"scope": "personal"})

        with (
            patch(_PATCH_VALIDATE, new_callable=AsyncMock, return_value=_base_instance()),
            patch(_PATCH_OAUTH_FIELDS, return_value=["clientId", "clientSecret"]),
            patch(_PATCH_NAME_CONFLICT) as mock_conflict,
            patch(
                _PATCH_CREATE_OAUTH,
                new_callable=AsyncMock,
                return_value="oauth-existing",
            ) as mock_create,
            patch(_PATCH_OAUTH_PATH, return_value="/services/oauth/googledrive"),
            patch(_PATCH_TIMESTAMP, return_value=1234567890),
        ):
            result = await update_connector_instance_auth_config(
                "conn1", request, graph_provider
            )

        assert result["success"] is True
        assert result["config"]["auth"]["oauthConfigId"] == "oauth-existing"
        # _create_or_update_oauth_config called with the existing oauth_app_id
        mock_create.assert_called_once()
        call_kwargs = mock_create.call_args
        assert call_kwargs.kwargs.get("oauth_app_id") == "oauth-existing"
        # No name conflict check when name is unchanged (existing name kept)
        mock_conflict.assert_not_called()


class TestAdminOAuthUpdatesExistingConfigNotFound:
    """3. Admin + OAUTH + has credentials + has oauth_app_id + config NOT found.

    NOTE: The source code has a variable shadowing issue at line 2815 where
    ``existing_config = None`` shadows the outer etcd config variable.  When
    the oauth_app_id is not found in the list, the outer ``existing_config``
    becomes ``None``, causing ``existing_config.get("auth", {})`` at line 2882
    to raise ``AttributeError``.  This results in a 500 error.
    The test documents the actual current behavior.
    """

    async def test_config_not_found_triggers_500_due_to_variable_shadowing(self):
        # oauth_app_id won't match anything in existing configs
        existing_oauth_cfgs = [
            {"_id": "other-id", "orgId": "o1", "oauthInstanceName": "Other"},
        ]
        config_service = _make_config_service(oauth_configs=existing_oauth_cfgs)
        registry = _make_connector_registry()
        container = _make_container(config_service)

        body = {
            "auth": {
                "clientId": "cid",
                "clientSecret": "csec",
                "oauthConfigId": "nonexistent-id",
            },
            "baseUrl": "https://app.example.com",
        }
        request = _make_request(
            headers={"X-Is-Admin": "true"},
            body=body,
            container=container,
            connector_registry=registry,
        )
        graph_provider = AsyncMock()
        graph_provider.get_document = AsyncMock(return_value={"scope": "personal"})

        with (
            patch(_PATCH_VALIDATE, new_callable=AsyncMock, return_value=_base_instance()),
            patch(_PATCH_OAUTH_FIELDS, return_value=["clientId", "clientSecret"]),
            patch(_PATCH_NAME_CONFLICT),
            patch(
                _PATCH_CREATE_OAUTH,
                new_callable=AsyncMock,
                return_value="oauth-brand-new",
            ),
            patch(_PATCH_OAUTH_PATH, return_value="/services/oauth/googledrive"),
            patch(_PATCH_TIMESTAMP, return_value=1234567890),
        ):
            # Due to variable shadowing bug, this raises 500 instead of succeeding
            with pytest.raises(HTTPException) as exc_info:
                await update_connector_instance_auth_config(
                    "conn1", request, graph_provider
                )

        assert exc_info.value.status_code == HttpStatusCode.INTERNAL_SERVER_ERROR.value
        assert "NoneType" in exc_info.value.detail or "attribute" in exc_info.value.detail


class TestAdminOAuthNoCredentialsHasAppId:
    """4. Admin + OAUTH + no credentials + has oauth_app_id
       -> uses existing config without creating/updating."""

    async def test_uses_existing_without_creating(self):
        config_service = _make_config_service()
        registry = _make_connector_registry()
        container = _make_container(config_service)

        body = {
            "auth": {"oauthConfigId": "oauth-preset"},
            "baseUrl": "https://app.example.com",
        }
        request = _make_request(
            headers={"X-Is-Admin": "true"},
            body=body,
            container=container,
            connector_registry=registry,
        )
        graph_provider = AsyncMock()
        graph_provider.get_document = AsyncMock(return_value={"scope": "personal"})

        with (
            patch(_PATCH_VALIDATE, new_callable=AsyncMock, return_value=_base_instance()),
            patch(_PATCH_OAUTH_FIELDS, return_value=["clientId", "clientSecret"]),
            patch(_PATCH_NAME_CONFLICT) as mock_conflict,
            patch(
                _PATCH_CREATE_OAUTH,
                new_callable=AsyncMock,
            ) as mock_create,
            patch(_PATCH_OAUTH_PATH, return_value="/services/oauth/googledrive"),
            patch(_PATCH_TIMESTAMP, return_value=1234567890),
        ):
            result = await update_connector_instance_auth_config(
                "conn1", request, graph_provider
            )

        assert result["success"] is True
        # Should store the existing oauthConfigId
        assert result["config"]["auth"]["oauthConfigId"] == "oauth-preset"
        # _create_or_update_oauth_config should NOT be called
        mock_create.assert_not_called()
        mock_conflict.assert_not_called()


class TestAdminOAuthNoCredentialsNoAppId:
    """5. Admin + OAUTH + no credentials + no oauth_app_id -> skips OAuth ops."""

    async def test_skips_oauth_operations(self):
        config_service = _make_config_service()
        registry = _make_connector_registry()
        container = _make_container(config_service)

        body = {
            "auth": {"someOtherField": "value"},
            "baseUrl": "https://app.example.com",
        }
        request = _make_request(
            headers={"X-Is-Admin": "true"},
            body=body,
            container=container,
            connector_registry=registry,
        )
        graph_provider = AsyncMock()
        graph_provider.get_document = AsyncMock(return_value={"scope": "personal"})

        with (
            patch(_PATCH_VALIDATE, new_callable=AsyncMock, return_value=_base_instance()),
            patch(_PATCH_OAUTH_FIELDS, return_value=["clientId", "clientSecret"]),
            patch(
                _PATCH_CREATE_OAUTH,
                new_callable=AsyncMock,
            ) as mock_create,
            patch(_PATCH_OAUTH_PATH, return_value="/services/oauth/googledrive"),
            patch(_PATCH_TIMESTAMP, return_value=1234567890),
        ):
            result = await update_connector_instance_auth_config(
                "conn1", request, graph_provider
            )

        assert result["success"] is True
        # No OAuth config created or updated
        mock_create.assert_not_called()
        # No oauthConfigId should be set since none was provided
        assert "oauthConfigId" not in result["config"]["auth"] or result["config"]["auth"].get("oauthConfigId") is None


class TestNonOAuthAuthType:
    """6. Non-OAUTH auth type -> keeps all fields (no credential filtering)."""

    async def test_keeps_all_fields_for_api_token(self):
        config_service = _make_config_service()
        registry = _make_connector_registry()
        container = _make_container(config_service)

        body = {
            "auth": {"apiToken": "tok-123", "clientId": "keep-me"},
        }
        instance = _base_instance(authType="API_TOKEN")
        request = _make_request(
            headers={"X-Is-Admin": "true"},
            body=body,
            container=container,
            connector_registry=registry,
        )
        graph_provider = AsyncMock()
        graph_provider.get_document = AsyncMock(return_value={"scope": "personal"})

        with (
            patch(_PATCH_VALIDATE, new_callable=AsyncMock, return_value=instance),
            patch(_PATCH_TIMESTAMP, return_value=1234567890),
        ):
            result = await update_connector_instance_auth_config(
                "conn1", request, graph_provider
            )

        assert result["success"] is True
        # For non-OAUTH, all fields should be preserved (no filtering)
        assert result["config"]["auth"]["apiToken"] == "tok-123"
        assert result["config"]["auth"]["clientId"] == "keep-me"


class TestOAuthMetadataRedirectUriWithBaseUrl:
    """7. OAuth metadata with redirect URI + base_url provided."""

    async def test_redirect_uri_uses_provided_base_url(self):
        config_service = _make_config_service()
        registry = _make_connector_registry()
        container = _make_container(config_service)

        body = {
            "auth": {"oauthConfigId": "oauth-1"},
            "baseUrl": "https://custom.example.com",
        }
        request = _make_request(
            headers={"X-Is-Admin": "true"},
            body=body,
            container=container,
            connector_registry=registry,
        )
        graph_provider = AsyncMock()
        graph_provider.get_document = AsyncMock(return_value={"scope": "personal"})

        with (
            patch(_PATCH_VALIDATE, new_callable=AsyncMock, return_value=_base_instance()),
            patch(_PATCH_OAUTH_FIELDS, return_value=["clientId", "clientSecret"]),
            patch(_PATCH_TIMESTAMP, return_value=1234567890),
        ):
            result = await update_connector_instance_auth_config(
                "conn1", request, graph_provider
            )

        assert result["success"] is True
        auth = result["config"]["auth"]
        assert auth["redirectUri"] == "https://custom.example.com/oauth/callback"
        assert auth["scopes"] == ["read", "write"]
        assert auth["authorizeUrl"] == "https://auth.example.com/authorize"
        assert auth["tokenUrl"] == "https://auth.example.com/token"


class TestOAuthMetadataRedirectUriNoBaseUrl:
    """8. OAuth metadata with redirect URI + no base_url (fetches from endpoints)."""

    async def test_redirect_uri_fetches_from_endpoints(self):
        config_service = _make_config_service(
            endpoints={
                "frontend": {"publicEndpoint": "https://default.example.com"},
            },
        )
        registry = _make_connector_registry()
        container = _make_container(config_service)

        # No baseUrl provided
        body = {
            "auth": {"oauthConfigId": "oauth-1"},
        }
        request = _make_request(
            headers={"X-Is-Admin": "true"},
            body=body,
            container=container,
            connector_registry=registry,
        )
        graph_provider = AsyncMock()
        graph_provider.get_document = AsyncMock(return_value={"scope": "personal"})

        with (
            patch(_PATCH_VALIDATE, new_callable=AsyncMock, return_value=_base_instance()),
            patch(_PATCH_OAUTH_FIELDS, return_value=["clientId", "clientSecret"]),
            patch(_PATCH_TIMESTAMP, return_value=1234567890),
        ):
            result = await update_connector_instance_auth_config(
                "conn1", request, graph_provider
            )

        assert result["success"] is True
        auth = result["config"]["auth"]
        assert auth["redirectUri"] == "https://default.example.com/oauth/callback"


class TestConnectorScopeMissing:
    """9. Connector scope missing -> fetches from graph_provider."""

    async def test_fetches_scope_from_graph_when_missing(self):
        # Instance config has no connectorScope
        config_service = _make_config_service(
            instance_config={"auth": {}, "credentials": None},
        )
        registry = _make_connector_registry()
        container = _make_container(config_service)

        body = {
            "auth": {"oauthConfigId": "oauth-1"},
            "baseUrl": "https://app.example.com",
        }
        request = _make_request(
            headers={"X-Is-Admin": "true"},
            body=body,
            container=container,
            connector_registry=registry,
        )
        graph_provider = AsyncMock()
        graph_provider.get_document = AsyncMock(
            return_value={"scope": "organization"}
        )

        with (
            patch(_PATCH_VALIDATE, new_callable=AsyncMock, return_value=_base_instance()),
            patch(_PATCH_OAUTH_FIELDS, return_value=["clientId", "clientSecret"]),
            patch(_PATCH_TIMESTAMP, return_value=1234567890),
        ):
            result = await update_connector_instance_auth_config(
                "conn1", request, graph_provider
            )

        assert result["success"] is True
        assert result["config"]["auth"]["connectorScope"] == "organization"
        assert graph_provider.get_document.call_count == 2
        graph_provider.get_document.assert_called_with(
            "conn1", CollectionNames.APPS.value
        )


class TestCleanupExistingConnector:
    """10. Cleanup existing connector in connectors_map."""

    async def test_cleans_up_existing_connector_instance(self):
        mock_connector = AsyncMock()
        mock_connector.cleanup = AsyncMock()
        connectors_map = {"conn1": mock_connector}

        config_service = _make_config_service()
        registry = _make_connector_registry()
        container = _make_container(config_service, connectors_map=connectors_map)

        body = {
            "auth": {"apiToken": "new-token"},
        }
        instance = _base_instance(authType="API_TOKEN")
        request = _make_request(
            headers={"X-Is-Admin": "false"},
            body=body,
            container=container,
            connector_registry=registry,
        )
        graph_provider = AsyncMock()
        graph_provider.get_document = AsyncMock(return_value={"scope": "personal"})

        with (
            patch(_PATCH_VALIDATE, new_callable=AsyncMock, return_value=instance),
            patch(_PATCH_TIMESTAMP, return_value=1234567890),
        ):
            result = await update_connector_instance_auth_config(
                "conn1", request, graph_provider
            )

        assert result["success"] is True
        mock_connector.cleanup.assert_called_once()
        # Connector should be removed from the map
        assert "conn1" not in connectors_map


class TestCleanupErrorIsLoggedNotRaised:
    """11. Cleanup error is logged, not raised."""

    async def test_cleanup_error_does_not_propagate(self):
        mock_connector = AsyncMock()
        mock_connector.cleanup = AsyncMock(side_effect=RuntimeError("cleanup boom"))
        connectors_map = {"conn1": mock_connector}

        config_service = _make_config_service()
        registry = _make_connector_registry()
        container = _make_container(config_service, connectors_map=connectors_map)

        body = {
            "auth": {"apiToken": "tok"},
        }
        instance = _base_instance(authType="API_TOKEN")
        request = _make_request(
            headers={"X-Is-Admin": "false"},
            body=body,
            container=container,
            connector_registry=registry,
        )
        graph_provider = AsyncMock()
        graph_provider.get_document = AsyncMock(return_value={"scope": "personal"})

        with (
            patch(_PATCH_VALIDATE, new_callable=AsyncMock, return_value=instance),
            patch(_PATCH_TIMESTAMP, return_value=1234567890),
        ):
            # Should NOT raise despite cleanup failure
            result = await update_connector_instance_auth_config(
                "conn1", request, graph_provider
            )

        assert result["success"] is True
        mock_connector.cleanup.assert_called_once()


class TestUpdateInstanceReturnsNone:
    """12. update_connector_instance returns None -> raises 500."""

    async def test_raises_500_when_update_returns_none(self):
        config_service = _make_config_service()
        registry = _make_connector_registry(update_return=None)
        # Explicitly set update to return None
        registry.update_connector_instance = AsyncMock(return_value=None)
        container = _make_container(config_service)

        body = {
            "auth": {"apiToken": "tok"},
        }
        instance = _base_instance(authType="API_TOKEN", name="My Connector")
        request = _make_request(
            headers={"X-Is-Admin": "false"},
            body=body,
            container=container,
            connector_registry=registry,
        )
        graph_provider = AsyncMock()
        graph_provider.get_document = AsyncMock(return_value={"scope": "personal"})

        with (
            patch(_PATCH_VALIDATE, new_callable=AsyncMock, return_value=instance),
            patch(_PATCH_TIMESTAMP, return_value=1234567890),
        ):
            with pytest.raises(HTTPException) as exc_info:
                await update_connector_instance_auth_config(
                    "conn1", request, graph_provider
                )

        assert exc_info.value.status_code == HttpStatusCode.INTERNAL_SERVER_ERROR.value
        assert "My Connector" in exc_info.value.detail


class TestGeneralExceptionRaises500:
    """13. General exception -> raises 500."""

    async def test_unexpected_error_raises_500(self):
        config_service = _make_config_service()
        registry = _make_connector_registry()
        container = _make_container(config_service)

        body = {
            "auth": {"apiToken": "tok"},
        }
        request = _make_request(
            headers={"X-Is-Admin": "false"},
            body=body,
            container=container,
            connector_registry=registry,
        )
        graph_provider = AsyncMock()

        with (
            patch(
                _PATCH_VALIDATE,
                new_callable=AsyncMock,
                side_effect=ValueError("something broke"),
            ),
        ):
            with pytest.raises(HTTPException) as exc_info:
                await update_connector_instance_auth_config(
                    "conn1", request, graph_provider
                )

        assert exc_info.value.status_code == HttpStatusCode.INTERNAL_SERVER_ERROR.value
        assert "something broke" in exc_info.value.detail


class TestOAuthCreateFailsRaises500:
    """_create_or_update_oauth_config returns None -> raises 500."""

    async def test_oauth_create_failure_raises_500(self):
        config_service = _make_config_service()
        registry = _make_connector_registry()
        container = _make_container(config_service)

        body = {
            "auth": {"clientId": "cid", "clientSecret": "csec"},
            "baseUrl": "https://app.example.com",
        }
        request = _make_request(
            headers={"X-Is-Admin": "true"},
            body=body,
            container=container,
            connector_registry=registry,
        )
        graph_provider = AsyncMock()
        graph_provider.get_document = AsyncMock(return_value={"scope": "personal"})

        with (
            patch(_PATCH_VALIDATE, new_callable=AsyncMock, return_value=_base_instance()),
            patch(_PATCH_OAUTH_FIELDS, return_value=["clientId", "clientSecret"]),
            patch(_PATCH_NAME_CONFLICT),
            patch(
                _PATCH_CREATE_OAUTH,
                new_callable=AsyncMock,
                return_value=None,  # Failure
            ),
            patch(_PATCH_OAUTH_PATH, return_value="/services/oauth/googledrive"),
            patch(_PATCH_TIMESTAMP, return_value=1234567890),
        ):
            with pytest.raises(HTTPException) as exc_info:
                await update_connector_instance_auth_config(
                    "conn1", request, graph_provider
                )

        assert exc_info.value.status_code == HttpStatusCode.INTERNAL_SERVER_ERROR.value
        assert "OAuth" in exc_info.value.detail


class TestAdminOAuthUpdateExistingChangeName:
    """Admin + OAUTH + credentials + oauth_app_id found + new name provided
    that differs from existing -> checks name conflict."""

    async def test_name_change_triggers_conflict_check(self):
        existing_oauth_cfgs = [
            {
                "_id": "oauth-existing",
                "orgId": "o1",
                "oauthInstanceName": "Old Name",
                "clientId": "old-cid",
            },
        ]
        config_service = _make_config_service(oauth_configs=existing_oauth_cfgs)
        registry = _make_connector_registry()
        container = _make_container(config_service)

        body = {
            "auth": {
                "clientId": "new-cid",
                "clientSecret": "new-csec",
                "oauthConfigId": "oauth-existing",
                "oauthInstanceName": "New Name",
            },
            "baseUrl": "https://app.example.com",
        }
        request = _make_request(
            headers={"X-Is-Admin": "true"},
            body=body,
            container=container,
            connector_registry=registry,
        )
        graph_provider = AsyncMock()
        graph_provider.get_document = AsyncMock(return_value={"scope": "personal"})

        with (
            patch(_PATCH_VALIDATE, new_callable=AsyncMock, return_value=_base_instance()),
            patch(_PATCH_OAUTH_FIELDS, return_value=["clientId", "clientSecret"]),
            patch(_PATCH_NAME_CONFLICT) as mock_conflict,
            patch(
                _PATCH_CREATE_OAUTH,
                new_callable=AsyncMock,
                return_value="oauth-existing",
            ),
            patch(_PATCH_OAUTH_PATH, return_value="/services/oauth/googledrive"),
            patch(_PATCH_TIMESTAMP, return_value=1234567890),
        ):
            result = await update_connector_instance_auth_config(
                "conn1", request, graph_provider
            )

        assert result["success"] is True
        # Name changed from "Old Name" to "New Name" -> conflict check
        mock_conflict.assert_called_once()
        args = mock_conflict.call_args
        assert args[0][1] == "New Name"  # name arg
        assert args[0][2] == "o1"  # org_id
        assert args[1]["exclude_index"] == 0  # exclude_index kwarg


class TestNonAdminOAuthSkipsOAuthOps:
    """Non-admin user with OAUTH type -> skips admin OAuth operations
    but still processes OAuth metadata."""

    async def test_non_admin_skips_admin_oauth_path(self):
        config_service = _make_config_service()
        registry = _make_connector_registry()
        container = _make_container(config_service)

        body = {
            "auth": {"oauthConfigId": "oauth-1"},
            "baseUrl": "https://app.example.com",
        }
        request = _make_request(
            headers={"X-Is-Admin": "false"},
            body=body,
            container=container,
            connector_registry=registry,
        )
        graph_provider = AsyncMock()
        graph_provider.get_document = AsyncMock(return_value={"scope": "personal"})

        with (
            patch(_PATCH_VALIDATE, new_callable=AsyncMock, return_value=_base_instance()),
            patch(
                _PATCH_CREATE_OAUTH,
                new_callable=AsyncMock,
            ) as mock_create,
            patch(_PATCH_TIMESTAMP, return_value=1234567890),
        ):
            result = await update_connector_instance_auth_config(
                "conn1", request, graph_provider
            )

        assert result["success"] is True
        # Admin OAuth creation path not entered
        mock_create.assert_not_called()
        # OAuth metadata still applied (redirect URI, scopes, etc.)
        auth = result["config"]["auth"]
        assert "redirectUri" in auth
        assert auth["scopes"] == ["read", "write"]


class TestExistingOAuthConfigsNotAList:
    """When get_config for oauth path returns a non-list (e.g., dict), it should
    be normalized to an empty list."""

    async def test_non_list_oauth_configs_normalized(self):
        # Return a dict instead of a list for oauth configs
        async def _get_config(path, **kwargs):
            if "/services/connectors/" in path and path.endswith("/config"):
                return {"auth": {"connectorScope": "personal"}, "credentials": None}
            if "/services/oauth/" in path:
                return {"not": "a list"}  # Not a list!
            if path == "/services/endpoints":
                return {"frontend": {"publicEndpoint": "https://app.example.com"}}
            return kwargs.get("default", {})

        config_service = AsyncMock()
        config_service.get_config = AsyncMock(side_effect=_get_config)
        config_service.set_config = AsyncMock(return_value=True)

        registry = _make_connector_registry()
        container = _make_container(config_service)

        body = {
            "auth": {"clientId": "cid", "clientSecret": "csec"},
            "baseUrl": "https://app.example.com",
        }
        request = _make_request(
            headers={"X-Is-Admin": "true"},
            body=body,
            container=container,
            connector_registry=registry,
        )
        graph_provider = AsyncMock()
        graph_provider.get_document = AsyncMock(return_value={"scope": "personal"})

        with (
            patch(_PATCH_VALIDATE, new_callable=AsyncMock, return_value=_base_instance()),
            patch(_PATCH_OAUTH_FIELDS, return_value=["clientId", "clientSecret"]),
            patch(_PATCH_NAME_CONFLICT),
            patch(
                _PATCH_CREATE_OAUTH,
                new_callable=AsyncMock,
                return_value="oauth-new",
            ),
            patch(_PATCH_OAUTH_PATH, return_value="/services/oauth/googledrive"),
            patch(_PATCH_TIMESTAMP, return_value=1234567890),
        ):
            # Should not crash - non-list is handled gracefully
            result = await update_connector_instance_auth_config(
                "conn1", request, graph_provider
            )

        assert result["success"] is True


class TestExistingConfigEmpty:
    """When get_config for instance returns None/falsy, code should default to {}."""

    async def test_empty_existing_config_handled(self):
        async def _get_config(path, **kwargs):
            if "/services/connectors/" in path and path.endswith("/config"):
                return None  # Empty/None config
            if path == "/services/endpoints":
                return {"frontend": {"publicEndpoint": "https://app.example.com"}}
            return kwargs.get("default", {})

        config_service = AsyncMock()
        config_service.get_config = AsyncMock(side_effect=_get_config)
        config_service.set_config = AsyncMock(return_value=True)

        registry = _make_connector_registry()
        container = _make_container(config_service)

        body = {
            "auth": {"apiToken": "tok"},
            "baseUrl": "https://app.example.com",
        }
        instance = _base_instance(authType="API_TOKEN")
        request = _make_request(
            headers={"X-Is-Admin": "false"},
            body=body,
            container=container,
            connector_registry=registry,
        )
        graph_provider = AsyncMock()
        graph_provider.get_document = AsyncMock(return_value={"scope": "personal"})

        with (
            patch(_PATCH_VALIDATE, new_callable=AsyncMock, return_value=instance),
            patch(_PATCH_TIMESTAMP, return_value=1234567890),
        ):
            result = await update_connector_instance_auth_config(
                "conn1", request, graph_provider
            )

        assert result["success"] is True
        assert result["config"]["auth"]["apiToken"] == "tok"
        assert result["config"]["auth"]["connectorScope"] == "personal"


class TestOAuthFieldFilteringKeepsMetadataFields:
    """For OAUTH type, metadata fields (oauthConfigId, oauthInstanceName,
    authType, connectorScope) are preserved while credential fields are removed.

    Uses a non-admin request to bypass the admin OAuth creation path and
    focus purely on the field filtering logic at lines 2889-2908.
    """

    async def test_metadata_fields_preserved_credentials_removed(self):
        config_service = _make_config_service()
        registry = _make_connector_registry()
        container = _make_container(config_service)

        body = {
            "auth": {
                "clientId": "should-be-removed",
                "clientSecret": "should-be-removed",
                "oauthConfigId": "oauth-1",
                "oauthInstanceName": "My App",
                "authType": "OAUTH",
                "connectorScope": "personal",
                "someExtraField": "should-be-kept",
            },
            "baseUrl": "https://app.example.com",
        }
        # Use non-admin to skip admin OAuth creation path and test only filtering
        request = _make_request(
            headers={"X-Is-Admin": "false"},
            body=body,
            container=container,
            connector_registry=registry,
        )
        graph_provider = AsyncMock()
        graph_provider.get_document = AsyncMock(return_value={"scope": "personal"})

        with (
            patch(_PATCH_VALIDATE, new_callable=AsyncMock, return_value=_base_instance()),
            patch(_PATCH_OAUTH_FIELDS, return_value=["clientId", "clientSecret"]),
            patch(_PATCH_TIMESTAMP, return_value=1234567890),
        ):
            result = await update_connector_instance_auth_config(
                "conn1", request, graph_provider
            )

        assert result["success"] is True
        auth = result["config"]["auth"]
        # Credential fields filtered out for OAUTH type
        assert "clientId" not in auth
        assert "clientSecret" not in auth
        # Metadata fields preserved
        assert auth["oauthConfigId"] == "oauth-1"
        assert auth["oauthInstanceName"] == "My App"
        assert auth["connectorScope"] == "personal"
        # Extra non-oauth fields preserved
        assert auth["someExtraField"] == "should-be-kept"


class TestMergePreservesExistingAuthFields:
    """Merging should preserve existing auth fields not in the update body."""

    async def test_existing_fields_preserved_on_merge(self):
        config_service = _make_config_service(
            instance_config={
                "auth": {
                    "connectorScope": "personal",
                    "existingField": "should-survive",
                },
                "credentials": {"old": "cred"},
            },
        )
        registry = _make_connector_registry()
        container = _make_container(config_service)

        body = {
            "auth": {"apiToken": "new-tok"},
        }
        instance = _base_instance(authType="API_TOKEN")
        request = _make_request(
            headers={"X-Is-Admin": "false"},
            body=body,
            container=container,
            connector_registry=registry,
        )
        graph_provider = AsyncMock()
        graph_provider.get_document = AsyncMock(return_value={"scope": "personal"})

        with (
            patch(_PATCH_VALIDATE, new_callable=AsyncMock, return_value=instance),
            patch(_PATCH_TIMESTAMP, return_value=1234567890),
        ):
            result = await update_connector_instance_auth_config(
                "conn1", request, graph_provider
            )

        assert result["success"] is True
        auth = result["config"]["auth"]
        assert auth["existingField"] == "should-survive"
        assert auth["apiToken"] == "new-tok"
        assert auth["connectorScope"] == "personal"
        # credentials and oauth should be cleared
        assert result["config"]["credentials"] is None
        assert result["config"]["oauth"] is None
