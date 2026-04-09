"""Tests for create_connector_instance deep paths (lines 2329-2490).

Covers:
  - Auth type auto-selection when selected_auth_type is None
  - Auth type compatibility validation
  - Pre-validate OAuth config (admin, OAUTH auth type, credentials present)
  - OAuth name conflict: updating existing, creating new, config not found
  - Create instance in DB: success, ValueError, None return
  - Store initial config: admin with auth, non-admin with auth, no auth
  - _handle_oauth_config_creation returning ID vs None
  - Full success response
"""

import contextlib
import logging
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from app.config.constants.http_status_code import HttpStatusCode
from app.connectors.api.router import create_connector_instance

_ROUTER = "app.connectors.api.router"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _mock_request(
    *,
    user: dict = None,
    headers: dict = None,
    body: dict = None,
    config_service: Any = None,
    connector_registry: Any = None,
):
    """Build a minimal mock FastAPI request object for create_connector_instance."""
    req = MagicMock()
    user_data = user or {"userId": "user-1", "orgId": "org-1"}
    req.state = MagicMock()
    req.state.user = MagicMock()
    req.state.user.get = lambda k, default=None: user_data.get(k, default)

    _headers = headers or {}
    req.headers = MagicMock()
    req.headers.get = lambda k, default=None: _headers.get(k, default)

    req.json = AsyncMock(return_value=body or {})

    _config_service = config_service or AsyncMock()
    _container = MagicMock()
    _container.logger = MagicMock(return_value=logging.getLogger("test"))
    _container.config_service = MagicMock(return_value=_config_service)
    req.app = MagicMock()
    req.app.container = _container

    if connector_registry:
        req.app.state.connector_registry = connector_registry
    else:
        req.app.state.connector_registry = MagicMock()

    return req


def _default_metadata(**overrides):
    """Return connector metadata dict with sensible defaults."""
    m = {
        "scope": ["personal", "team"],
        "supportedAuthTypes": ["OAUTH", "NONE"],
        "config": {"auth": {"schemas": {}, "oauthConfigs": {}}},
    }
    m.update(overrides)
    return m


def _default_registry(metadata=None, instance=None):
    """Build a mock connector_registry with defaults."""
    reg = AsyncMock()
    reg.get_connector_metadata = AsyncMock(
        return_value=metadata or _default_metadata()
    )
    reg.create_connector_instance_on_configuration = AsyncMock(
        return_value=instance or {"_key": "conn-1", "type": "googledrive"},
    )
    reg._normalize_connector_name = MagicMock(return_value="googledrive")
    reg._get_beta_connector_names = MagicMock(return_value=[])
    return reg


def _default_graph_provider():
    """Build a mock graph provider."""
    gp = AsyncMock()
    gp.get_account_type = AsyncMock(return_value="individual")
    return gp


def _base_body(**overrides):
    """Build a request body dict with required fields."""
    b = {
        "connectorType": "GOOGLE_DRIVE",
        "instanceName": "My Drive",
        "scope": "personal",
    }
    b.update(overrides)
    return b


@contextlib.contextmanager
def _common_patches(**extra):
    """Apply the three common patches that every test needs plus optional extras.

    The common patches bypass early validation helpers that are not under test:
    - _trim_connector_config: pass-through
    - check_beta_connector_access: no-op
    - _get_config_path_for_instance: return a fixed path

    Extra patches are passed as keyword arguments where the key is the
    unqualified function name and the value is a dict of ``patch()`` kwargs.
    Alternatively, callers can just nest additional ``patch()`` calls around
    the block returned here.
    """
    with patch(
        f"{_ROUTER}._trim_connector_config", side_effect=lambda c: c
    ), patch(
        f"{_ROUTER}.check_beta_connector_access",
        new_callable=AsyncMock,
        return_value=None,
    ), patch(
        f"{_ROUTER}._get_config_path_for_instance",
        return_value="/services/connectors/conn-1/config",
    ):
        yield


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestAuthTypeAutoSelection:
    """Lines 2329-2334: auto-select auth type when not provided."""

    @pytest.mark.asyncio
    async def test_auto_selects_first_supported_auth_type(self) -> None:
        """When authType is not in body, use supportedAuthTypes[0]."""
        body = _base_body()  # no authType key
        registry = _default_registry()
        req = _mock_request(
            body=body,
            headers={"X-Is-Admin": "true"},
            connector_registry=registry,
        )
        gp = _default_graph_provider()

        with _common_patches():
            result = await create_connector_instance(req, gp)

        assert result["success"] is True
        call_kwargs = registry.create_connector_instance_on_configuration.call_args.kwargs
        assert call_kwargs["selected_auth_type"] == "OAUTH"

    @pytest.mark.asyncio
    async def test_auto_selects_none_when_no_supported_types(self) -> None:
        """When supportedAuthTypes is empty, default to 'NONE'."""
        body = _base_body()
        metadata = _default_metadata(supportedAuthTypes=[])
        registry = _default_registry(metadata=metadata)
        req = _mock_request(
            body=body,
            headers={"X-Is-Admin": "true"},
            connector_registry=registry,
        )
        gp = _default_graph_provider()

        with _common_patches():
            result = await create_connector_instance(req, gp)

        assert result["success"] is True
        call_kwargs = registry.create_connector_instance_on_configuration.call_args.kwargs
        assert call_kwargs["selected_auth_type"] == "NONE"


class TestAuthTypeValidation:
    """Lines 2336-2344: validate auth type compatibility."""

    @pytest.mark.asyncio
    async def test_incompatible_auth_type_raises_400(self) -> None:
        """When selected auth type is not in supportedAuthTypes, raise 400."""
        body = _base_body(authType="API_KEY")
        metadata = _default_metadata(supportedAuthTypes=["OAUTH", "NONE"])
        registry = _default_registry(metadata=metadata)
        req = _mock_request(
            body=body,
            headers={"X-Is-Admin": "true"},
            connector_registry=registry,
        )
        gp = _default_graph_provider()

        with _common_patches():
            with pytest.raises(HTTPException) as exc:
                await create_connector_instance(req, gp)
            assert exc.value.status_code == HttpStatusCode.BAD_REQUEST.value
            assert "not supported" in exc.value.detail

    @pytest.mark.asyncio
    async def test_compatible_auth_type_passes(self) -> None:
        """When selected auth type IS in supportedAuthTypes, no error."""
        body = _base_body(authType="NONE")
        registry = _default_registry()
        req = _mock_request(
            body=body,
            headers={"X-Is-Admin": "true"},
            connector_registry=registry,
        )
        gp = _default_graph_provider()

        with _common_patches():
            result = await create_connector_instance(req, gp)

        assert result["success"] is True


class TestPreValidateOAuthConfig:
    """Lines 2349-2394: pre-validate OAuth config for name conflicts."""

    @pytest.mark.asyncio
    async def test_new_oauth_config_no_conflict(self) -> None:
        """Admin + OAUTH + credentials + no oauthConfigId -> create new, check conflict."""
        body = _base_body(
            authType="OAUTH",
            config={"auth": {"clientId": "cid", "clientSecret": "csecret"}},
        )
        registry = _default_registry()
        config_service = AsyncMock()
        config_service.get_config = AsyncMock(return_value=[])
        config_service.set_config = AsyncMock(return_value=True)
        req = _mock_request(
            body=body,
            headers={"X-Is-Admin": "true"},
            connector_registry=registry,
            config_service=config_service,
        )
        gp = _default_graph_provider()

        with _common_patches(), patch(
            f"{_ROUTER}._get_oauth_field_names_from_registry",
            return_value=["clientId", "clientSecret"],
        ), patch(
            f"{_ROUTER}._get_oauth_config_path",
            return_value="/services/oauth/googledrive",
        ), patch(
            f"{_ROUTER}._check_oauth_name_conflict"
        ) as mock_conflict, patch(
            f"{_ROUTER}._handle_oauth_config_creation",
            new_callable=AsyncMock,
            return_value="oauth-123",
        ), patch(
            f"{_ROUTER}._prepare_connector_config",
            new_callable=AsyncMock,
            return_value={"auth": {"oauthConfigId": "oauth-123"}},
        ):
            result = await create_connector_instance(req, gp)

        assert result["success"] is True
        mock_conflict.assert_called_once()
        call_args = mock_conflict.call_args
        assert "exclude_index" not in call_args.kwargs

    @pytest.mark.asyncio
    async def test_update_existing_oauth_config_found(self) -> None:
        """Admin + OAUTH + matching oauthConfigId with no name change -> no conflict check needed."""
        body = _base_body(
            authType="OAUTH",
            config={
                "auth": {
                    "clientId": "cid",
                    "clientSecret": "csecret",
                    "oauthConfigId": "existing-id",
                }
            },
        )
        existing_configs = [
            {"_id": "existing-id", "orgId": "org-1", "name": "Old Name"},
            {"_id": "other-id", "orgId": "org-1", "name": "Other"},
        ]
        registry = _default_registry()
        config_service = AsyncMock()
        config_service.get_config = AsyncMock(return_value=existing_configs)
        config_service.set_config = AsyncMock(return_value=True)
        req = _mock_request(
            body=body,
            headers={"X-Is-Admin": "true"},
            connector_registry=registry,
            config_service=config_service,
        )
        gp = _default_graph_provider()

        with _common_patches(), patch(
            f"{_ROUTER}._get_oauth_field_names_from_registry",
            return_value=["clientId", "clientSecret"],
        ), patch(
            f"{_ROUTER}._get_oauth_config_path",
            return_value="/services/oauth/googledrive",
        ), patch(
            f"{_ROUTER}._check_oauth_name_conflict"
        ) as mock_conflict, patch(
            f"{_ROUTER}._handle_oauth_config_creation",
            new_callable=AsyncMock,
            return_value="existing-id",
        ), patch(
            f"{_ROUTER}._prepare_connector_config",
            new_callable=AsyncMock,
            return_value={"auth": {}},
        ):
            result = await create_connector_instance(req, gp)

        assert result["success"] is True
        mock_conflict.assert_not_called()

    @pytest.mark.asyncio
    async def test_update_oauth_config_not_found_falls_to_new(self) -> None:
        """Admin + OAUTH + credentials + provided_oauth_config_id that doesn't match -> treat as new."""
        body = _base_body(
            authType="OAUTH",
            config={
                "auth": {
                    "clientId": "cid",
                    "clientSecret": "csecret",
                    "oauthConfigId": "missing-id",
                }
            },
        )
        existing_configs = [
            {"_id": "different-id", "orgId": "org-1", "name": "Existing"},
        ]
        registry = _default_registry()
        config_service = AsyncMock()
        config_service.get_config = AsyncMock(return_value=existing_configs)
        config_service.set_config = AsyncMock(return_value=True)
        req = _mock_request(
            body=body,
            headers={"X-Is-Admin": "true"},
            connector_registry=registry,
            config_service=config_service,
        )
        gp = _default_graph_provider()

        with _common_patches(), patch(
            f"{_ROUTER}._get_oauth_field_names_from_registry",
            return_value=["clientId", "clientSecret"],
        ), patch(
            f"{_ROUTER}._get_oauth_config_path",
            return_value="/services/oauth/googledrive",
        ), patch(
            f"{_ROUTER}._check_oauth_name_conflict"
        ) as mock_conflict, patch(
            f"{_ROUTER}._handle_oauth_config_creation",
            new_callable=AsyncMock,
            return_value="new-id",
        ), patch(
            f"{_ROUTER}._prepare_connector_config",
            new_callable=AsyncMock,
            return_value={"auth": {}},
        ):
            result = await create_connector_instance(req, gp)

        assert result["success"] is True
        mock_conflict.assert_called_once()
        assert "exclude_index" not in mock_conflict.call_args.kwargs

    @pytest.mark.asyncio
    async def test_non_list_existing_configs_coerced_to_empty_list(self) -> None:
        """When get_config returns non-list, it should be treated as empty list."""
        body = _base_body(
            authType="OAUTH",
            config={"auth": {"clientId": "cid", "clientSecret": "csecret"}},
        )
        registry = _default_registry()
        config_service = AsyncMock()
        config_service.get_config = AsyncMock(return_value="not-a-list")
        config_service.set_config = AsyncMock(return_value=True)
        req = _mock_request(
            body=body,
            headers={"X-Is-Admin": "true"},
            connector_registry=registry,
            config_service=config_service,
        )
        gp = _default_graph_provider()

        with _common_patches(), patch(
            f"{_ROUTER}._get_oauth_field_names_from_registry",
            return_value=["clientId", "clientSecret"],
        ), patch(
            f"{_ROUTER}._get_oauth_config_path",
            return_value="/services/oauth/googledrive",
        ), patch(
            f"{_ROUTER}._check_oauth_name_conflict"
        ) as mock_conflict, patch(
            f"{_ROUTER}._handle_oauth_config_creation",
            new_callable=AsyncMock,
            return_value="oauth-new",
        ), patch(
            f"{_ROUTER}._prepare_connector_config",
            new_callable=AsyncMock,
            return_value={"auth": {}},
        ):
            result = await create_connector_instance(req, gp)

        assert result["success"] is True
        mock_conflict.assert_called_once()
        assert mock_conflict.call_args.args[0] == []

    @pytest.mark.asyncio
    async def test_no_oauth_credentials_skips_pre_validation(self) -> None:
        """Admin + OAUTH + auth config but no actual credential values -> skip pre-validation."""
        body = _base_body(
            authType="OAUTH",
            config={"auth": {"someOtherField": "value"}},
        )
        registry = _default_registry()
        config_service = AsyncMock()
        config_service.set_config = AsyncMock(return_value=True)
        req = _mock_request(
            body=body,
            headers={"X-Is-Admin": "true"},
            connector_registry=registry,
            config_service=config_service,
        )
        gp = _default_graph_provider()

        with _common_patches(), patch(
            f"{_ROUTER}._get_oauth_field_names_from_registry",
            return_value=["clientId", "clientSecret"],
        ), patch(
            f"{_ROUTER}._check_oauth_name_conflict"
        ) as mock_conflict, patch(
            f"{_ROUTER}._handle_oauth_config_creation",
            new_callable=AsyncMock,
            return_value=None,
        ), patch(
            f"{_ROUTER}._prepare_connector_config",
            new_callable=AsyncMock,
            return_value={"auth": {}},
        ):
            result = await create_connector_instance(req, gp)

        assert result["success"] is True
        mock_conflict.assert_not_called()

    @pytest.mark.asyncio
    async def test_oauth_instance_name_from_config(self) -> None:
        """oauthInstanceName in auth config should be used over instance_name."""
        body = _base_body(
            authType="OAUTH",
            config={
                "auth": {
                    "clientId": "cid",
                    "clientSecret": "csecret",
                    "oauthInstanceName": "Custom OAuth Name",
                }
            },
        )
        registry = _default_registry()
        config_service = AsyncMock()
        config_service.get_config = AsyncMock(return_value=[])
        config_service.set_config = AsyncMock(return_value=True)
        req = _mock_request(
            body=body,
            headers={"X-Is-Admin": "true"},
            connector_registry=registry,
            config_service=config_service,
        )
        gp = _default_graph_provider()

        with _common_patches(), patch(
            f"{_ROUTER}._get_oauth_field_names_from_registry",
            return_value=["clientId", "clientSecret"],
        ), patch(
            f"{_ROUTER}._get_oauth_config_path",
            return_value="/services/oauth/googledrive",
        ), patch(
            f"{_ROUTER}._check_oauth_name_conflict"
        ) as mock_conflict, patch(
            f"{_ROUTER}._handle_oauth_config_creation",
            new_callable=AsyncMock,
            return_value="oauth-new",
        ), patch(
            f"{_ROUTER}._prepare_connector_config",
            new_callable=AsyncMock,
            return_value={"auth": {}},
        ):
            result = await create_connector_instance(req, gp)

        assert result["success"] is True
        assert mock_conflict.call_args.args[1] == "Custom OAuth Name"


class TestCreateInstanceInDB:
    """Lines 2399-2421: create instance in database."""

    @pytest.mark.asyncio
    async def test_value_error_from_registry_raises_400(self) -> None:
        """When create_connector_instance_on_configuration raises ValueError -> 400."""
        body = _base_body(authType="NONE")
        registry = _default_registry()
        registry.create_connector_instance_on_configuration = AsyncMock(
            side_effect=ValueError("Duplicate instance name"),
        )
        req = _mock_request(
            body=body,
            headers={"X-Is-Admin": "true"},
            connector_registry=registry,
        )
        gp = _default_graph_provider()

        with _common_patches():
            with pytest.raises(HTTPException) as exc:
                await create_connector_instance(req, gp)
            assert exc.value.status_code == HttpStatusCode.BAD_REQUEST.value
            assert "Duplicate instance name" in exc.value.detail

    @pytest.mark.asyncio
    async def test_none_instance_from_registry_raises_500(self) -> None:
        """When create_connector_instance_on_configuration returns None -> 500."""
        body = _base_body(authType="NONE")
        registry = _default_registry()
        registry.create_connector_instance_on_configuration = AsyncMock(
            return_value=None
        )
        req = _mock_request(
            body=body,
            headers={"X-Is-Admin": "true"},
            connector_registry=registry,
        )
        gp = _default_graph_provider()

        with _common_patches():
            with pytest.raises(HTTPException) as exc:
                await create_connector_instance(req, gp)
            assert exc.value.status_code == HttpStatusCode.INTERNAL_SERVER_ERROR.value
            assert "Failed to create" in exc.value.detail


class TestStoreInitialConfig:
    """Lines 2426-2480: store initial config after instance creation."""

    @pytest.mark.asyncio
    async def test_admin_with_auth_creates_oauth_and_stores_config(self) -> None:
        """Admin with auth config -> _handle_oauth_config_creation called, ID set in config."""
        body = _base_body(
            authType="OAUTH",
            config={"auth": {"clientId": "cid"}},
        )
        registry = _default_registry()
        config_service = AsyncMock()
        config_service.get_config = AsyncMock(return_value=[])
        config_service.set_config = AsyncMock(return_value=True)
        req = _mock_request(
            body=body,
            headers={"X-Is-Admin": "true"},
            connector_registry=registry,
            config_service=config_service,
        )
        gp = _default_graph_provider()

        with _common_patches(), patch(
            f"{_ROUTER}._get_oauth_field_names_from_registry",
            return_value=["clientId", "clientSecret"],
        ), patch(
            f"{_ROUTER}._get_oauth_config_path",
            return_value="/services/oauth/googledrive",
        ), patch(
            f"{_ROUTER}._check_oauth_name_conflict"
        ), patch(
            f"{_ROUTER}._handle_oauth_config_creation",
            new_callable=AsyncMock,
            return_value="oauth-999",
        ) as mock_handle, patch(
            f"{_ROUTER}._prepare_connector_config",
            new_callable=AsyncMock,
            return_value={"auth": {"oauthConfigId": "oauth-999"}},
        ) as mock_prepare:
            result = await create_connector_instance(req, gp)

        assert result["success"] is True
        mock_handle.assert_awaited_once()
        mock_prepare.assert_awaited_once()
        config_service.set_config.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_admin_with_auth_but_oauth_returns_none(self) -> None:
        """Admin with auth config but _handle_oauth_config_creation returns None."""
        body = _base_body(
            authType="OAUTH",
            config={"auth": {"clientId": "cid"}},
        )
        registry = _default_registry()
        config_service = AsyncMock()
        config_service.get_config = AsyncMock(return_value=[])
        config_service.set_config = AsyncMock(return_value=True)
        req = _mock_request(
            body=body,
            headers={"X-Is-Admin": "true"},
            connector_registry=registry,
            config_service=config_service,
        )
        gp = _default_graph_provider()

        with _common_patches(), patch(
            f"{_ROUTER}._get_oauth_field_names_from_registry",
            return_value=["clientId", "clientSecret"],
        ), patch(
            f"{_ROUTER}._get_oauth_config_path",
            return_value="/services/oauth/googledrive",
        ), patch(
            f"{_ROUTER}._check_oauth_name_conflict"
        ), patch(
            f"{_ROUTER}._handle_oauth_config_creation",
            new_callable=AsyncMock,
            return_value=None,
        ), patch(
            f"{_ROUTER}._prepare_connector_config",
            new_callable=AsyncMock,
            return_value={"auth": {}},
        ):
            result = await create_connector_instance(req, gp)

        assert result["success"] is True
        config_service.set_config.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_non_admin_with_auth_skips_oauth_creation(self) -> None:
        """Non-admin with auth config -> skip OAuth creation, still store config."""
        body = _base_body(
            authType="OAUTH",
            oauthConfigId="test-oauth-123",
            config={"auth": {"token": "abc"}},
        )
        registry = _default_registry()
        config_service = AsyncMock()
        config_service.set_config = AsyncMock(return_value=True)
        # Mock get_config to return existing OAuth config for validation
        config_service.get_config = AsyncMock(return_value=[
            {"_id": "test-oauth-123", "orgId": "org-1", "name": "Test OAuth App"}
        ])
        req = _mock_request(
            body=body,
            headers={"X-Is-Admin": "false"},
            connector_registry=registry,
            config_service=config_service,
        )
        gp = _default_graph_provider()

        with _common_patches(), patch(
            f"{_ROUTER}._handle_oauth_config_creation",
            new_callable=AsyncMock,
        ) as mock_handle, patch(
            f"{_ROUTER}._prepare_connector_config",
            new_callable=AsyncMock,
            return_value={"auth": {"token": "abc"}},
        ):
            result = await create_connector_instance(req, gp)

        assert result["success"] is True
        mock_handle.assert_not_awaited()
        config_service.set_config.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_config_without_auth_skips_oauth(self) -> None:
        """Config provided but no auth section -> skip OAuth, still store config."""
        body = _base_body(
            authType="NONE",
            config={"sync": {"interval": 60}},
        )
        registry = _default_registry()
        config_service = AsyncMock()
        config_service.set_config = AsyncMock(return_value=True)
        req = _mock_request(
            body=body,
            headers={"X-Is-Admin": "true"},
            connector_registry=registry,
            config_service=config_service,
        )
        gp = _default_graph_provider()

        with _common_patches(), patch(
            f"{_ROUTER}._handle_oauth_config_creation",
            new_callable=AsyncMock,
        ) as mock_handle, patch(
            f"{_ROUTER}._prepare_connector_config",
            new_callable=AsyncMock,
            return_value={"sync": {"interval": 60}},
        ):
            result = await create_connector_instance(req, gp)

        assert result["success"] is True
        mock_handle.assert_not_awaited()
        config_service.set_config.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_no_config_and_no_oauth_id_skips_storage(self) -> None:
        """No config and no oauthConfigId -> skip entire config storage block."""
        body = _base_body(authType="NONE")
        registry = _default_registry()
        config_service = AsyncMock()
        req = _mock_request(
            body=body,
            headers={"X-Is-Admin": "true"},
            connector_registry=registry,
            config_service=config_service,
        )
        gp = _default_graph_provider()

        with _common_patches(), patch(
            f"{_ROUTER}._prepare_connector_config",
            new_callable=AsyncMock,
        ) as mock_prepare:
            result = await create_connector_instance(req, gp)

        assert result["success"] is True
        mock_prepare.assert_not_awaited()
        config_service.set_config.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_oauth_config_id_only_triggers_storage(self) -> None:
        """oauthConfigId provided without config -> still enter storage block."""
        body = _base_body(authType="OAUTH", oauthConfigId="pre-existing-oauth")
        registry = _default_registry()
        config_service = AsyncMock()
        config_service.set_config = AsyncMock(return_value=True)
        req = _mock_request(
            body=body,
            headers={"X-Is-Admin": "true"},
            connector_registry=registry,
            config_service=config_service,
        )
        gp = _default_graph_provider()

        with _common_patches(), patch(
            f"{_ROUTER}._prepare_connector_config",
            new_callable=AsyncMock,
            return_value={"auth": {"oauthConfigId": "pre-existing-oauth"}},
        ):
            result = await create_connector_instance(req, gp)

        assert result["success"] is True
        config_service.set_config.assert_awaited_once()


class TestSuccessResponse:
    """Lines 2490-2503: verify the shape of the success response."""

    @pytest.mark.asyncio
    async def test_response_shape_with_config(self) -> None:
        """Response includes correct connector details when config is provided."""
        body = _base_body(
            authType="NONE",
            config={"sync": {"interval": 30}},
        )
        registry = _default_registry()
        config_service = AsyncMock()
        config_service.set_config = AsyncMock(return_value=True)
        req = _mock_request(
            body=body,
            headers={"X-Is-Admin": "true"},
            connector_registry=registry,
            config_service=config_service,
        )
        gp = _default_graph_provider()

        with _common_patches(), patch(
            f"{_ROUTER}._prepare_connector_config",
            new_callable=AsyncMock,
            return_value={"sync": {"interval": 30}},
        ):
            result = await create_connector_instance(req, gp)

        assert result["success"] is True
        assert result["message"] == "Connector instance created successfully."
        connector = result["connector"]
        assert connector["connectorId"] == "conn-1"
        assert connector["connectorType"] == "GOOGLE_DRIVE"
        assert connector["instanceName"] == "My Drive"
        assert connector["created"] is True
        assert connector["scope"] == "personal"
        assert connector["createdBy"] == "user-1"
        assert connector["isAuthenticated"] is False
        assert connector["isConfigured"] is True

    @pytest.mark.asyncio
    async def test_response_is_configured_false_when_no_config(self) -> None:
        """isConfigured is False when no config is provided."""
        body = _base_body(authType="NONE")
        registry = _default_registry()
        req = _mock_request(
            body=body,
            headers={"X-Is-Admin": "true"},
            connector_registry=registry,
        )
        gp = _default_graph_provider()

        with _common_patches():
            result = await create_connector_instance(req, gp)

        assert result["connector"]["isConfigured"] is False


class TestOAuthBodyLevelConfigId:
    """Test the oauthConfigId from top-level body vs nested in auth config."""

    @pytest.mark.asyncio
    async def test_body_level_oauth_config_id_used_for_update(self) -> None:
        """Body-level oauthConfigId is used; without name override conflict check is skipped."""
        body = _base_body(
            authType="OAUTH",
            oauthConfigId="body-level-id",
            config={"auth": {"clientId": "cid", "clientSecret": "csecret"}},
        )
        existing_configs = [
            {"_id": "body-level-id", "orgId": "org-1", "name": "Body OAuth"},
        ]
        registry = _default_registry()
        config_service = AsyncMock()
        config_service.get_config = AsyncMock(return_value=existing_configs)
        config_service.set_config = AsyncMock(return_value=True)
        req = _mock_request(
            body=body,
            headers={"X-Is-Admin": "true"},
            connector_registry=registry,
            config_service=config_service,
        )
        gp = _default_graph_provider()

        with _common_patches(), patch(
            f"{_ROUTER}._get_oauth_field_names_from_registry",
            return_value=["clientId", "clientSecret"],
        ), patch(
            f"{_ROUTER}._get_oauth_config_path",
            return_value="/services/oauth/googledrive",
        ), patch(
            f"{_ROUTER}._check_oauth_name_conflict"
        ) as mock_conflict, patch(
            f"{_ROUTER}._handle_oauth_config_creation",
            new_callable=AsyncMock,
            return_value="body-level-id",
        ), patch(
            f"{_ROUTER}._prepare_connector_config",
            new_callable=AsyncMock,
            return_value={"auth": {}},
        ):
            result = await create_connector_instance(req, gp)

        assert result["success"] is True
        mock_conflict.assert_not_called()


class TestGenericExceptionHandling:
    """Lines 2505-2512: generic exception -> 500."""

    @pytest.mark.asyncio
    async def test_unexpected_exception_raises_500(self) -> None:
        """An unexpected error during processing becomes a 500 response."""
        body = _base_body(authType="NONE")
        registry = _default_registry()
        registry.create_connector_instance_on_configuration = AsyncMock(
            side_effect=RuntimeError("Unexpected DB failure"),
        )
        req = _mock_request(
            body=body,
            headers={"X-Is-Admin": "true"},
            connector_registry=registry,
        )
        gp = _default_graph_provider()

        with _common_patches():
            with pytest.raises(HTTPException) as exc:
                await create_connector_instance(req, gp)
            assert exc.value.status_code == HttpStatusCode.INTERNAL_SERVER_ERROR.value
            assert "Unexpected DB failure" in exc.value.detail
