"""Tests for update_connector_instance_config (lines 3166-3428) and remaining
small gaps in stream_record_internal (533-597) and download_file (664-683).

Focuses on the uncovered statements starting at line 3284 (OAuth metadata
merge, redirect URI construction, cleanup flow, error paths) plus a few
supplemental edge-case tests for streaming/download error paths.
"""

import logging
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from app.config.constants.arangodb import Connectors
from app.config.constants.http_status_code import HttpStatusCode
from app.connectors.api.router import (
    download_file,
    stream_record_internal,
    update_connector_instance_config,
)

# ---------------------------------------------------------------------------
# Module-level patch target
# ---------------------------------------------------------------------------
_ROUTER = "app.connectors.api.router"
_SENTINEL = object()  # Used to distinguish "not provided" from explicit None

# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _mock_request(
    *,
    user: dict | None = None,
    headers: dict | None = None,
    body: dict | None = None,
    container: Any | None = None,
    connector_registry: Any | None = None,
):
    """Build a minimal mock FastAPI Request for update_connector_instance_config."""
    req = MagicMock()

    user_data = user or {"userId": "user-1", "orgId": "org-1"}
    req.state = MagicMock()
    req.state.user = MagicMock()
    req.state.user.get = lambda k, default=None: user_data.get(k, default)

    _headers = headers or {}
    req.headers = MagicMock()
    req.headers.get = lambda k, default=None: _headers.get(k, default)

    if body is not None:
        req.json = AsyncMock(return_value=body)
    else:
        req.json = AsyncMock(return_value={})

    _container = container or MagicMock()
    if not hasattr(_container, "logger") or not callable(getattr(_container, "logger", None)):
        _container.logger = MagicMock(return_value=logging.getLogger("test"))

    # Use a SimpleNamespace for app.state so attribute lookups are deterministic
    # (MagicMock auto-creates children, which can hide bugs).
    _registry = connector_registry or AsyncMock()
    app_state = SimpleNamespace(connector_registry=_registry)

    req.app = MagicMock()
    req.app.container = _container
    req.app.state = app_state

    return req


def _build_config_service(
    *,
    existing_config: dict | None = None,
    oauth_configs: list | None = None,
    endpoints: dict | None = None,
):
    """Build a config_service mock that returns real dicts for get_config calls.

    The function under test calls .copy() and .get() on values returned by
    get_config, so returning AsyncMock objects would blow up.  We use
    side_effect to return the right dict/list for each etcd path.
    """
    _existing = existing_config if existing_config is not None else {
        "auth": {"connectorScope": "personal"},
        "credentials": None,
        "oauth": None,
    }
    _oauth = oauth_configs if oauth_configs is not None else []
    _endpoints = endpoints if endpoints is not None else {
        "frontend": {"publicEndpoint": "https://app.example.com"},
    }

    def _get_config(path, **kwargs):
        lookup = {
            # Matches _get_config_path_for_instance("conn1")
            "/services/connectors/conn1/config": _existing,
            # Matches _get_oauth_config_path("googledrive")
            "/services/oauth/googledrive": _oauth,
            # Matches endpoints fetch
            "/services/endpoints": _endpoints,
        }
        result = lookup.get(path)
        if result is not None:
            return result
        return kwargs.get("default", {})

    svc = AsyncMock()
    svc.get_config = AsyncMock(side_effect=_get_config)
    svc.set_config = AsyncMock(return_value=True)
    return svc


def _build_connector_registry(
    *,
    metadata: dict | None = None,
    update_return: Any = _SENTINEL,
):
    """Build a connector_registry mock with sensible defaults.

    Use ``update_return=None`` to simulate a failed update (the code checks
    ``if not updated_instance``).  Omit the parameter to get the default
    success dict ``{"_key": "conn1"}``.
    """
    _metadata = metadata if metadata is not None else {
        "config": {
            "auth": {
                "schemas": {
                    "OAUTH": {"redirectUri": "oauth/callback"},
                    "OAUTH_ADMIN_CONSENT": {"redirectUri": "oauth/admin/callback"},
                },
                "oauthConfigs": {
                    "OAUTH": {
                        "authorizeUrl": "https://accounts.google.com/o/oauth2/auth",
                        "tokenUrl": "https://oauth2.googleapis.com/token",
                        "scopes": ["https://www.googleapis.com/auth/drive"],
                    },
                    "OAUTH_ADMIN_CONSENT": {
                        "authorizeUrl": "https://login.microsoftonline.com/common/oauth2/v2.0/authorize",
                        "tokenUrl": "https://login.microsoftonline.com/common/oauth2/v2.0/token",
                        "scopes": ["https://graph.microsoft.com/.default"],
                    },
                },
            }
        }
    }
    _update_ret = {"_key": "conn1"} if update_return is _SENTINEL else update_return

    reg = AsyncMock()
    reg.get_connector_metadata = AsyncMock(return_value=_metadata)
    reg.update_connector_instance = AsyncMock(return_value=_update_ret)
    return reg


class _ContainerStub:
    """A lightweight container stub that does NOT auto-create attributes
    (unlike MagicMock).  This ensures that ``hasattr(container, 'connectors_map')``
    returns ``False`` unless the attribute was explicitly set.
    """

    def __init__(self):
        self.logger = MagicMock(return_value=logging.getLogger("test"))
        self.config_service = MagicMock(return_value=AsyncMock())


def _build_container(config_service=None, *, has_connectors_map=False, connectors_map=None):
    """Build a minimal container mock."""
    container = _ContainerStub()
    if config_service:
        container.config_service = MagicMock(return_value=config_service)

    if has_connectors_map:
        if connectors_map is not None:
            container.connectors_map = connectors_map
        else:
            container.connectors_map = {}

    return container


def _build_graph_provider(scope="personal", created_by="user-1"):
    """Build a mock graph_provider for update_connector_instance_config tests."""
    gp = AsyncMock()
    gp.get_document = AsyncMock(return_value={
        "scope": scope,
        "createdBy": created_by,
    })
    return gp


def _instance(
    *,
    auth_type: str = "OAUTH",
    is_active: bool = False,
    connector_type: str = "googledrive",
    extra: dict | None = None,
):
    inst = {
        "_key": "conn1",
        "type": connector_type,
        "authType": auth_type,
        "isActive": is_active,
        "name": "My GDrive",
    }
    if extra:
        inst.update(extra)
    return inst


# ============================================================================
# update_connector_instance_config -- OAuth metadata merge (lines 3284-3363)
# ============================================================================


class TestUpdateConfigOAuthWithConfigId:
    """Auth update with oauth_config_id -> fetch OAuth config from etcd -> merge."""

    @pytest.mark.asyncio
    @patch(f"{_ROUTER}.get_epoch_timestamp_in_ms", return_value=1234567890)
    @patch(f"{_ROUTER}.get_validated_connector_instance", new_callable=AsyncMock)
    async def test_oauth_config_id_found_merges_reference(self, mock_get_inst, mock_ts):
        """When oauth_config_id matches a config in etcd, store reference + OAuth URLs."""
        mock_get_inst.return_value = _instance(auth_type="OAUTH")

        config_service = _build_config_service(
            oauth_configs=[
                {"_id": "oa1", "orgId": "org-1", "oauthInstanceName": "My OAuth App"},
            ],
        )
        registry = _build_connector_registry()
        container = _build_container(config_service)

        request = _mock_request(
            container=container,
            connector_registry=registry,
            headers={"X-Is-Admin": "true"},
            body={
                "auth": {"connectorScope": "personal"},
                "oauthConfigId": "oa1",
                "baseUrl": "https://app.example.com",
            },
        )

        graph_provider = _build_graph_provider()
        result = await update_connector_instance_config("conn1", request, graph_provider)

        assert result["success"] is True
        cfg = result["config"]
        # OAuth reference stored
        assert cfg["auth"]["oauthConfigId"] == "oa1"
        assert cfg["auth"]["oauthInstanceName"] == "My OAuth App"
        # OAuth URLs merged from metadata
        assert cfg["auth"]["authorizeUrl"] == "https://accounts.google.com/o/oauth2/auth"
        assert cfg["auth"]["tokenUrl"] == "https://oauth2.googleapis.com/token"
        assert cfg["auth"]["scopes"] == ["https://www.googleapis.com/auth/drive"]
        assert cfg["auth"]["redirectUri"] == "https://app.example.com/oauth/callback"
        assert cfg["auth"]["authType"] == "OAUTH"
        # Credentials cleared on auth update
        assert cfg["credentials"] is None
        assert cfg["oauth"] is None
        # set_config was called
        config_service.set_config.assert_awaited_once()

    @pytest.mark.asyncio
    @patch(f"{_ROUTER}.get_epoch_timestamp_in_ms", return_value=1234567890)
    @patch(f"{_ROUTER}.get_validated_connector_instance", new_callable=AsyncMock)
    async def test_oauth_config_id_not_found_raises_404(self, mock_get_inst, mock_ts):
        """When oauth_config_id does not match any etcd config, 404."""
        mock_get_inst.return_value = _instance(auth_type="OAUTH")

        config_service = _build_config_service(
            oauth_configs=[
                {"_id": "oa-other", "orgId": "org-1", "oauthInstanceName": "Other"},
            ],
        )
        registry = _build_connector_registry()
        container = _build_container(config_service)

        request = _mock_request(
            container=container,
            connector_registry=registry,
            body={
                "auth": {"connectorScope": "personal"},
                "oauthConfigId": "oa-missing",
            },
        )

        with pytest.raises(HTTPException) as exc:
            await update_connector_instance_config("conn1", request)
        assert exc.value.status_code == HttpStatusCode.NOT_FOUND.value
        assert "not found or access denied" in exc.value.detail

    @pytest.mark.asyncio
    @patch(f"{_ROUTER}.get_epoch_timestamp_in_ms", return_value=1234567890)
    @patch(f"{_ROUTER}.get_validated_connector_instance", new_callable=AsyncMock)
    async def test_oauth_config_id_wrong_org_raises_404(self, mock_get_inst, mock_ts):
        """Config exists but belongs to a different org -> 404."""
        mock_get_inst.return_value = _instance(auth_type="OAUTH")

        config_service = _build_config_service(
            oauth_configs=[
                {"_id": "oa1", "orgId": "different-org", "oauthInstanceName": "X"},
            ],
        )
        registry = _build_connector_registry()
        container = _build_container(config_service)

        request = _mock_request(
            container=container,
            connector_registry=registry,
            body={
                "auth": {"connectorScope": "personal"},
                "oauthConfigId": "oa1",
            },
        )

        with pytest.raises(HTTPException) as exc:
            await update_connector_instance_config("conn1", request)
        assert exc.value.status_code == HttpStatusCode.NOT_FOUND.value

    @pytest.mark.asyncio
    @patch(f"{_ROUTER}.get_epoch_timestamp_in_ms", return_value=1234567890)
    @patch(f"{_ROUTER}.get_validated_connector_instance", new_callable=AsyncMock)
    async def test_oauth_config_fetch_exception_raises_500(self, mock_get_inst, mock_ts):
        """If fetching OAuth configs from etcd throws, wrap in 500."""
        mock_get_inst.return_value = _instance(auth_type="OAUTH")

        config_service = _build_config_service()
        # Override the side_effect to raise on the oauth path
        original_side_effect = config_service.get_config.side_effect

        async def _exploding_get(path, **kwargs):
            if "oauth" in path:
                raise RuntimeError("etcd connection lost")
            return original_side_effect(path, **kwargs)

        config_service.get_config = AsyncMock(side_effect=_exploding_get)

        registry = _build_connector_registry()
        container = _build_container(config_service)

        request = _mock_request(
            container=container,
            connector_registry=registry,
            body={
                "auth": {"connectorScope": "personal"},
                "oauthConfigId": "oa1",
            },
        )

        with pytest.raises(HTTPException) as exc:
            await update_connector_instance_config("conn1", request)
        assert exc.value.status_code == HttpStatusCode.INTERNAL_SERVER_ERROR.value
        assert "Failed to fetch OAuth configuration" in exc.value.detail

    @pytest.mark.asyncio
    @patch(f"{_ROUTER}.get_epoch_timestamp_in_ms", return_value=1234567890)
    @patch(f"{_ROUTER}.get_validated_connector_instance", new_callable=AsyncMock)
    async def test_oauth_configs_not_a_list_treated_as_empty(self, mock_get_inst, mock_ts):
        """If etcd returns a non-list for oauth_configs, treat as empty -> 404."""
        mock_get_inst.return_value = _instance(auth_type="OAUTH")

        # Return a string instead of a list for the oauth path
        config_service = _build_config_service(oauth_configs=[])
        original_side_effect = config_service.get_config.side_effect

        async def _bad_type_get(path, **kwargs):
            if "oauth" in path and "connectors" not in path:
                return "not-a-list"
            return original_side_effect(path, **kwargs)

        config_service.get_config = AsyncMock(side_effect=_bad_type_get)

        registry = _build_connector_registry()
        container = _build_container(config_service)

        request = _mock_request(
            container=container,
            connector_registry=registry,
            body={
                "auth": {"connectorScope": "personal"},
                "oauthConfigId": "oa1",
            },
        )

        with pytest.raises(HTTPException) as exc:
            await update_connector_instance_config("conn1", request)
        assert exc.value.status_code == HttpStatusCode.NOT_FOUND.value


# ============================================================================
# Redirect URI construction
# ============================================================================


class TestUpdateConfigRedirectUri:
    """Tests for redirect URI construction with/without base_url."""

    @pytest.mark.asyncio
    @patch(f"{_ROUTER}.get_epoch_timestamp_in_ms", return_value=1234567890)
    @patch(f"{_ROUTER}.get_validated_connector_instance", new_callable=AsyncMock)
    async def test_redirect_uri_with_base_url(self, mock_get_inst, mock_ts):
        """When baseUrl is provided in the request body, use it for redirect URI."""
        mock_get_inst.return_value = _instance(auth_type="OAUTH")

        config_service = _build_config_service()
        registry = _build_connector_registry()
        container = _build_container(config_service)

        request = _mock_request(
            container=container,
            connector_registry=registry,
            body={
                "auth": {"connectorScope": "personal"},
                "baseUrl": "https://custom.example.com/",
            },
        )

        graph_provider = _build_graph_provider()
        result = await update_connector_instance_config("conn1", request, graph_provider)

        assert result["success"] is True
        # Trailing slash should be stripped, then /oauth/callback appended
        assert result["config"]["auth"]["redirectUri"] == "https://custom.example.com/oauth/callback"

    @pytest.mark.asyncio
    @patch(f"{_ROUTER}.get_epoch_timestamp_in_ms", return_value=1234567890)
    @patch(f"{_ROUTER}.get_validated_connector_instance", new_callable=AsyncMock)
    async def test_redirect_uri_without_base_url_fetches_from_endpoints(self, mock_get_inst, mock_ts):
        """When baseUrl is NOT provided, fetch from /services/endpoints."""
        mock_get_inst.return_value = _instance(auth_type="OAUTH")

        config_service = _build_config_service(
            endpoints={"frontend": {"publicEndpoint": "https://from-endpoints.example.com"}},
        )
        registry = _build_connector_registry()
        container = _build_container(config_service)

        request = _mock_request(
            container=container,
            connector_registry=registry,
            body={
                "auth": {"connectorScope": "personal"},
                # No baseUrl here
            },
        )

        graph_provider = _build_graph_provider()
        result = await update_connector_instance_config("conn1", request, graph_provider)

        assert result["success"] is True
        assert result["config"]["auth"]["redirectUri"] == "https://from-endpoints.example.com/oauth/callback"

    @pytest.mark.asyncio
    @patch(f"{_ROUTER}.get_epoch_timestamp_in_ms", return_value=1234567890)
    @patch(f"{_ROUTER}.get_validated_connector_instance", new_callable=AsyncMock)
    async def test_empty_redirect_uri_in_schema_skips_uri(self, mock_get_inst, mock_ts):
        """When the auth schema has no redirectUri, the field is empty string."""
        mock_get_inst.return_value = _instance(auth_type="OAUTH")

        config_service = _build_config_service()
        # Override metadata to have empty redirectUri
        registry = _build_connector_registry(
            metadata={
                "config": {
                    "auth": {
                        "schemas": {"OAUTH": {"redirectUri": ""}},
                        "oauthConfigs": {"OAUTH": {
                            "authorizeUrl": "https://auth.example.com",
                            "tokenUrl": "https://token.example.com",
                            "scopes": ["read"],
                        }},
                    }
                }
            },
        )
        container = _build_container(config_service)

        request = _mock_request(
            container=container,
            connector_registry=registry,
            body={
                "auth": {"connectorScope": "personal"},
                "baseUrl": "https://app.example.com",
            },
        )

        graph_provider = _build_graph_provider()
        result = await update_connector_instance_config("conn1", request, graph_provider)

        assert result["success"] is True
        # Empty redirect URI stays empty (the `if redirect_uri:` guard prevents building it)
        assert result["config"]["auth"]["redirectUri"] == ""


# ============================================================================
# OAUTH_ADMIN_CONSENT type
# ============================================================================


class TestUpdateConfigOAuthAdminConsent:
    """OAUTH_ADMIN_CONSENT auth type follows the same OAuth metadata path."""

    @pytest.mark.asyncio
    @patch(f"{_ROUTER}.get_epoch_timestamp_in_ms", return_value=1234567890)
    @patch(f"{_ROUTER}.get_validated_connector_instance", new_callable=AsyncMock)
    async def test_admin_consent_merges_metadata(self, mock_get_inst, mock_ts):
        mock_get_inst.return_value = _instance(auth_type="OAUTH_ADMIN_CONSENT")

        config_service = _build_config_service()
        registry = _build_connector_registry()
        container = _build_container(config_service)

        request = _mock_request(
            container=container,
            connector_registry=registry,
            body={
                "auth": {"connectorScope": "team"},
                "baseUrl": "https://admin.example.com",
            },
        )

        graph_provider = _build_graph_provider(scope="team")
        result = await update_connector_instance_config("conn1", request, graph_provider)

        assert result["success"] is True
        cfg = result["config"]["auth"]
        assert cfg["authType"] == "OAUTH_ADMIN_CONSENT"
        assert cfg["authorizeUrl"] == "https://login.microsoftonline.com/common/oauth2/v2.0/authorize"
        assert cfg["tokenUrl"] == "https://login.microsoftonline.com/common/oauth2/v2.0/token"
        assert cfg["scopes"] == ["https://graph.microsoft.com/.default"]
        assert cfg["redirectUri"] == "https://admin.example.com/oauth/admin/callback"


# ============================================================================
# Auth type change attempt
# ============================================================================


class TestUpdateConfigAuthTypeChange:
    """Attempting to change auth type after creation -> 400."""

    @pytest.mark.asyncio
    @patch(f"{_ROUTER}.get_epoch_timestamp_in_ms", return_value=1234567890)
    @patch(f"{_ROUTER}.get_validated_connector_instance", new_callable=AsyncMock)
    async def test_auth_type_change_in_body_auth(self, mock_get_inst, mock_ts):
        """body.auth.authType differs from instance.authType -> 400."""
        mock_get_inst.return_value = _instance(auth_type="API_TOKEN")

        config_service = _build_config_service()
        container = _build_container(config_service)

        request = _mock_request(
            container=container,
            body={"auth": {"authType": "OAUTH", "apiToken": "tok"}},
        )

        with pytest.raises(HTTPException) as exc:
            await update_connector_instance_config("conn1", request)
        assert exc.value.status_code == HttpStatusCode.BAD_REQUEST.value
        assert "Cannot change auth type" in exc.value.detail

    @pytest.mark.asyncio
    @patch(f"{_ROUTER}.get_epoch_timestamp_in_ms", return_value=1234567890)
    @patch(f"{_ROUTER}.get_validated_connector_instance", new_callable=AsyncMock)
    async def test_auth_type_change_in_body_root(self, mock_get_inst, mock_ts):
        """body.authType (backward compat) differs -> 400."""
        mock_get_inst.return_value = _instance(auth_type="API_TOKEN")

        config_service = _build_config_service()
        container = _build_container(config_service)

        request = _mock_request(
            container=container,
            body={"auth": {"apiToken": "tok"}, "authType": "OAUTH"},
        )

        with pytest.raises(HTTPException) as exc:
            await update_connector_instance_config("conn1", request)
        assert exc.value.status_code == HttpStatusCode.BAD_REQUEST.value


# ============================================================================
# Filters/sync update only (no auth) -> connector stays as-is
# ============================================================================


class TestUpdateConfigFiltersOnly:
    """Filters/sync update does not reset auth status."""

    @pytest.mark.asyncio
    @patch(f"{_ROUTER}.get_epoch_timestamp_in_ms", return_value=9999)
    @patch(f"{_ROUTER}.get_validated_connector_instance", new_callable=AsyncMock)
    async def test_filters_only_preserves_state(self, mock_get_inst, mock_ts):
        mock_get_inst.return_value = _instance(auth_type="OAUTH", is_active=False)

        config_service = _build_config_service(
            existing_config={"auth": {"old": "data"}, "filters": {"sync": {"types": ["FILE"]}}},
        )
        registry = _build_connector_registry()
        container = _build_container(config_service)

        request = _mock_request(
            container=container,
            connector_registry=registry,
            body={"filters": {"sync": {"types": ["FILE", "FOLDER"]}}},
        )

        graph_provider = _build_graph_provider()
        result = await update_connector_instance_config("conn1", request, graph_provider)

        assert result["success"] is True
        # Auth data preserved (not wiped)
        assert result["config"]["auth"] == {"old": "data"}
        # Filters updated
        assert result["config"]["filters"]["sync"]["types"] == ["FILE", "FOLDER"]
        # The update call should NOT include isAuthenticated/isActive resets
        call_kwargs = registry.update_connector_instance.call_args
        updates = call_kwargs.kwargs.get("updates") or call_kwargs[1].get("updates") or call_kwargs[0][1] if len(call_kwargs[0]) > 1 else call_kwargs.kwargs["updates"]
        assert "isAuthenticated" not in updates
        assert "isActive" not in updates
        assert updates["isConfigured"] is True
        assert updates["updatedAtTimestamp"] == 9999

    @pytest.mark.asyncio
    @patch(f"{_ROUTER}.get_epoch_timestamp_in_ms", return_value=9999)
    @patch(f"{_ROUTER}.get_validated_connector_instance", new_callable=AsyncMock)
    async def test_sync_only_update(self, mock_get_inst, mock_ts):
        """Updating only the sync section does not touch auth."""
        mock_get_inst.return_value = _instance(auth_type="OAUTH", is_active=False)

        config_service = _build_config_service(
            existing_config={"auth": {"key": "val"}, "sync": {"interval": 60}},
        )
        registry = _build_connector_registry()
        container = _build_container(config_service)

        request = _mock_request(
            container=container,
            connector_registry=registry,
            body={"sync": {"interval": 120}},
        )

        graph_provider = _build_graph_provider()
        result = await update_connector_instance_config("conn1", request, graph_provider)

        assert result["success"] is True
        assert result["config"]["sync"]["interval"] == 120
        assert result["config"]["auth"] == {"key": "val"}
        # Credentials should NOT be cleared
        assert result["config"].get("credentials") is None  # was already None in existing
        # No isAuthenticated reset
        updates = registry.update_connector_instance.call_args.kwargs["updates"]
        assert "isAuthenticated" not in updates


# ============================================================================
# Auth update -> cleanup connector in connectors_map
# ============================================================================


class TestUpdateConfigCleanup:
    """Auth update triggers cleanup of existing connector in connectors_map."""

    @pytest.mark.asyncio
    @patch(f"{_ROUTER}.get_epoch_timestamp_in_ms", return_value=1234567890)
    @patch(f"{_ROUTER}.get_validated_connector_instance", new_callable=AsyncMock)
    async def test_auth_update_cleans_up_existing_connector(self, mock_get_inst, mock_ts):
        """If connector exists in connectors_map, pop + cleanup on auth update."""
        mock_get_inst.return_value = _instance(auth_type="OAUTH")

        config_service = _build_config_service()
        registry = _build_connector_registry()

        # Build a mock connector object with a cleanup method
        mock_connector = AsyncMock()
        mock_connector.cleanup = AsyncMock()

        container = _build_container(
            config_service,
            has_connectors_map=True,
            connectors_map={"conn1": mock_connector},
        )

        request = _mock_request(
            container=container,
            connector_registry=registry,
            body={
                "auth": {"connectorScope": "personal"},
                "baseUrl": "https://app.example.com",
            },
        )

        graph_provider = _build_graph_provider()
        result = await update_connector_instance_config("conn1", request, graph_provider)

        assert result["success"] is True
        # Connector was popped from map
        assert "conn1" not in container.connectors_map
        # cleanup was called
        mock_connector.cleanup.assert_awaited_once()
        # Instance updates include auth reset fields
        updates = registry.update_connector_instance.call_args.kwargs["updates"]
        assert updates["isAuthenticated"] is False
        assert updates["isActive"] is False
        assert updates["isConfigured"] is True

    @pytest.mark.asyncio
    @patch(f"{_ROUTER}.get_epoch_timestamp_in_ms", return_value=1234567890)
    @patch(f"{_ROUTER}.get_validated_connector_instance", new_callable=AsyncMock)
    async def test_cleanup_error_logged_not_raised(self, mock_get_inst, mock_ts):
        """If cleanup() raises, the error is logged but does not fail the update."""
        mock_get_inst.return_value = _instance(auth_type="OAUTH")

        config_service = _build_config_service()
        registry = _build_connector_registry()

        mock_connector = AsyncMock()
        mock_connector.cleanup = AsyncMock(side_effect=RuntimeError("cleanup boom"))

        container = _build_container(
            config_service,
            has_connectors_map=True,
            connectors_map={"conn1": mock_connector},
        )

        request = _mock_request(
            container=container,
            connector_registry=registry,
            body={
                "auth": {"connectorScope": "personal"},
                "baseUrl": "https://app.example.com",
            },
        )

        # Should NOT raise despite cleanup failure
        graph_provider = _build_graph_provider()
        result = await update_connector_instance_config("conn1", request, graph_provider)
        assert result["success"] is True

    @pytest.mark.asyncio
    @patch(f"{_ROUTER}.get_epoch_timestamp_in_ms", return_value=1234567890)
    @patch(f"{_ROUTER}.get_validated_connector_instance", new_callable=AsyncMock)
    async def test_auth_update_no_connectors_map_attr(self, mock_get_inst, mock_ts):
        """If container has no connectors_map, skip cleanup gracefully."""
        mock_get_inst.return_value = _instance(auth_type="OAUTH")

        config_service = _build_config_service()
        registry = _build_connector_registry()
        container = _build_container(config_service, has_connectors_map=False)

        request = _mock_request(
            container=container,
            connector_registry=registry,
            body={
                "auth": {"connectorScope": "personal"},
                "baseUrl": "https://app.example.com",
            },
        )

        graph_provider = _build_graph_provider()
        result = await update_connector_instance_config("conn1", request, graph_provider)
        assert result["success"] is True

    @pytest.mark.asyncio
    @patch(f"{_ROUTER}.get_epoch_timestamp_in_ms", return_value=1234567890)
    @patch(f"{_ROUTER}.get_validated_connector_instance", new_callable=AsyncMock)
    async def test_auth_update_connector_not_in_map(self, mock_get_inst, mock_ts):
        """connectors_map exists but connector not in it -> no cleanup, still succeeds."""
        mock_get_inst.return_value = _instance(auth_type="OAUTH")

        config_service = _build_config_service()
        registry = _build_connector_registry()
        container = _build_container(
            config_service,
            has_connectors_map=True,
            connectors_map={"other-conn": AsyncMock()},
        )

        request = _mock_request(
            container=container,
            connector_registry=registry,
            body={
                "auth": {"connectorScope": "personal"},
                "baseUrl": "https://app.example.com",
            },
        )

        graph_provider = _build_graph_provider()
        result = await update_connector_instance_config("conn1", request, graph_provider)
        assert result["success"] is True

    @pytest.mark.asyncio
    @patch(f"{_ROUTER}.get_epoch_timestamp_in_ms", return_value=1234567890)
    @patch(f"{_ROUTER}.get_validated_connector_instance", new_callable=AsyncMock)
    async def test_connector_without_cleanup_method(self, mock_get_inst, mock_ts):
        """Connector in map but has no cleanup attr -> skip cleanup, still succeeds."""
        mock_get_inst.return_value = _instance(auth_type="OAUTH")

        config_service = _build_config_service()
        registry = _build_connector_registry()

        mock_connector = MagicMock(spec=[])  # spec=[] means no attributes at all

        container = _build_container(
            config_service,
            has_connectors_map=True,
            connectors_map={"conn1": mock_connector},
        )

        request = _mock_request(
            container=container,
            connector_registry=registry,
            body={
                "auth": {"connectorScope": "personal"},
                "baseUrl": "https://app.example.com",
            },
        )

        graph_provider = _build_graph_provider()
        result = await update_connector_instance_config("conn1", request, graph_provider)
        assert result["success"] is True


# ============================================================================
# update_connector_instance returns None -> 500
# ============================================================================


class TestUpdateConfigInstanceUpdateFailure:
    """When update_connector_instance returns None, raise 500."""

    @pytest.mark.asyncio
    @patch(f"{_ROUTER}.get_epoch_timestamp_in_ms", return_value=1234567890)
    @patch(f"{_ROUTER}.get_validated_connector_instance", new_callable=AsyncMock)
    async def test_update_returns_none_raises_500(self, mock_get_inst, mock_ts):
        mock_get_inst.return_value = _instance(auth_type="OAUTH")

        config_service = _build_config_service()
        registry = _build_connector_registry(update_return=None)
        # Use has_connectors_map=True with empty dict so that
        # hasattr(container, 'connectors_map') is True but
        # connector_id not in the map -> cleanup skipped -> reaches update call
        container = _build_container(
            config_service, has_connectors_map=True, connectors_map={},
        )

        request = _mock_request(
            container=container,
            connector_registry=registry,
            body={
                "auth": {"connectorScope": "personal"},
                "baseUrl": "https://app.example.com",
            },
        )

        with pytest.raises(HTTPException) as exc:
            await update_connector_instance_config("conn1", request)
        assert exc.value.status_code == HttpStatusCode.INTERNAL_SERVER_ERROR.value
        assert "Failed to update" in exc.value.detail

    @pytest.mark.asyncio
    @patch(f"{_ROUTER}.get_epoch_timestamp_in_ms", return_value=9999)
    @patch(f"{_ROUTER}.get_validated_connector_instance", new_callable=AsyncMock)
    async def test_update_returns_none_on_filters_only(self, mock_get_inst, mock_ts):
        """Even for filters-only updates, None return -> 500."""
        mock_get_inst.return_value = _instance(auth_type="API_TOKEN")

        config_service = _build_config_service()
        registry = _build_connector_registry(update_return=None)
        container = _build_container(
            config_service, has_connectors_map=True, connectors_map={},
        )

        request = _mock_request(
            container=container,
            connector_registry=registry,
            body={"filters": {"sync": {"types": ["FILE"]}}},
        )

        with pytest.raises(HTTPException) as exc:
            await update_connector_instance_config("conn1", request)
        assert exc.value.status_code == HttpStatusCode.INTERNAL_SERVER_ERROR.value


# ============================================================================
# General exception -> 500
# ============================================================================


class TestUpdateConfigGeneralException:
    """Unexpected exceptions are caught and wrapped in 500."""

    @pytest.mark.asyncio
    @patch(f"{_ROUTER}.get_validated_connector_instance", new_callable=AsyncMock)
    async def test_unexpected_exception_raises_500(self, mock_get_inst):
        """An unexpected error in the body triggers generic 500."""
        mock_get_inst.side_effect = RuntimeError("something unexpected")

        container = _build_container()
        request = _mock_request(
            container=container,
            body={"auth": {"key": "val"}},
        )

        with pytest.raises(HTTPException) as exc:
            await update_connector_instance_config("conn1", request)
        assert exc.value.status_code == HttpStatusCode.INTERNAL_SERVER_ERROR.value
        assert "Failed to update connector configuration" in exc.value.detail


# ============================================================================
# Active connector raises 400
# ============================================================================


class TestUpdateConfigActiveConnector:
    """Active connector cannot be updated."""

    @pytest.mark.asyncio
    @patch(f"{_ROUTER}.get_validated_connector_instance", new_callable=AsyncMock)
    async def test_active_connector_raises_400(self, mock_get_inst):
        mock_get_inst.return_value = _instance(is_active=True)

        container = _build_container()
        request = _mock_request(
            container=container,
            body={"auth": {"key": "val"}},
        )

        with pytest.raises(HTTPException) as exc:
            await update_connector_instance_config("conn1", request)
        assert exc.value.status_code == HttpStatusCode.BAD_REQUEST.value
        assert "Cannot update configuration while connector is active" in exc.value.detail


# ============================================================================
# Section merge edge cases
# ============================================================================


class TestUpdateConfigSectionMerge:
    """Edge cases for section merging logic."""

    @pytest.mark.asyncio
    @patch(f"{_ROUTER}.get_epoch_timestamp_in_ms", return_value=9999)
    @patch(f"{_ROUTER}.get_validated_connector_instance", new_callable=AsyncMock)
    async def test_new_auth_section_when_none_exists(self, mock_get_inst, mock_ts):
        """When existing_config has no auth section, it gets created."""
        mock_get_inst.return_value = _instance(auth_type="API_TOKEN")

        config_service = _build_config_service(
            existing_config={"filters": {}},
        )
        registry = _build_connector_registry()
        container = _build_container(config_service)

        request = _mock_request(
            container=container,
            connector_registry=registry,
            body={"auth": {"apiToken": "my-token"}},
        )

        graph_provider = _build_graph_provider()
        result = await update_connector_instance_config("conn1", request, graph_provider)

        assert result["success"] is True
        assert result["config"]["auth"]["apiToken"] == "my-token"

    @pytest.mark.asyncio
    @patch(f"{_ROUTER}.get_epoch_timestamp_in_ms", return_value=9999)
    @patch(f"{_ROUTER}.get_validated_connector_instance", new_callable=AsyncMock)
    async def test_filters_indexing_preserved_when_sync_updated(self, mock_get_inst, mock_ts):
        """Updating filters.sync preserves filters.indexing."""
        mock_get_inst.return_value = _instance(auth_type="API_TOKEN")

        config_service = _build_config_service(
            existing_config={
                "auth": {"tok": "abc"},
                "filters": {
                    "sync": {"types": ["FILE"]},
                    "indexing": {"extensions": [".pdf"]},
                },
            },
        )
        registry = _build_connector_registry()
        container = _build_container(config_service)

        request = _mock_request(
            container=container,
            connector_registry=registry,
            body={"filters": {"sync": {"types": ["FILE", "FOLDER"]}}},
        )

        graph_provider = _build_graph_provider()
        result = await update_connector_instance_config("conn1", request, graph_provider)

        assert result["success"] is True
        filters = result["config"]["filters"]
        assert filters["sync"]["types"] == ["FILE", "FOLDER"]
        assert filters["indexing"]["extensions"] == [".pdf"]

    @pytest.mark.asyncio
    @patch(f"{_ROUTER}.get_epoch_timestamp_in_ms", return_value=9999)
    @patch(f"{_ROUTER}.get_validated_connector_instance", new_callable=AsyncMock)
    async def test_empty_existing_config_returns_empty_dict(self, mock_get_inst, mock_ts):
        """When get_config returns None, use empty dict."""
        mock_get_inst.return_value = _instance(auth_type="API_TOKEN")

        config_service = _build_config_service(existing_config={})
        # Override to return None for the config path
        original_side_effect = config_service.get_config.side_effect

        async def _null_config(path, **kwargs):
            if "connectors" in path and "config" in path:
                return None
            return original_side_effect(path, **kwargs)

        config_service.get_config = AsyncMock(side_effect=_null_config)

        registry = _build_connector_registry()
        container = _build_container(config_service)

        request = _mock_request(
            container=container,
            connector_registry=registry,
            body={"filters": {"sync": {"types": ["FILE"]}}},
        )

        graph_provider = _build_graph_provider()
        result = await update_connector_instance_config("conn1", request, graph_provider)

        assert result["success"] is True
        assert result["config"]["filters"]["sync"]["types"] == ["FILE"]


# ============================================================================
# Non-OAUTH auth types skip OAuth metadata (line 3285 guard)
# ============================================================================


class TestUpdateConfigNonOAuthAuthType:
    """API_TOKEN or SERVICE_ACCOUNT auth types skip the OAuth metadata block."""

    @pytest.mark.asyncio
    @patch(f"{_ROUTER}.get_epoch_timestamp_in_ms", return_value=1234567890)
    @patch(f"{_ROUTER}.get_validated_connector_instance", new_callable=AsyncMock)
    async def test_api_token_auth_skips_oauth_metadata(self, mock_get_inst, mock_ts):
        mock_get_inst.return_value = _instance(auth_type="API_TOKEN")

        config_service = _build_config_service()
        registry = _build_connector_registry()
        container = _build_container(config_service)

        request = _mock_request(
            container=container,
            connector_registry=registry,
            body={"auth": {"apiToken": "secret-token"}},
        )

        graph_provider = _build_graph_provider()
        result = await update_connector_instance_config("conn1", request, graph_provider)

        assert result["success"] is True
        # No OAuth URLs should be present
        assert "authorizeUrl" not in result["config"].get("auth", {})
        assert "tokenUrl" not in result["config"].get("auth", {})
        # get_connector_metadata should NOT have been called
        registry.get_connector_metadata.assert_not_awaited()


# ============================================================================
# stream_record_internal -- additional edge cases (lines 533-597)
# ============================================================================


def _mock_record(**overrides):
    """Build a fake Record-like object."""
    defaults = {
        "id": "rec-1",
        "org_id": "org-1",
        "record_name": "doc.pdf",
        "mime_type": "application/pdf",
        "connector_name": Connectors.GOOGLE_DRIVE,
        "connector_id": "conn-1",
        "external_record_id": "ext-1",
    }
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


class TestStreamRecordInternalGoogleDrivePaths:
    """Cover the Google Drive / Google Mail branching in stream_record_internal
    (lines 582-585) and the org retry path (lines 532-535)."""

    def _setup_stream(
        self,
        *,
        connector_name=Connectors.GOOGLE_DRIVE,
        app_name=Connectors.GOOGLE_DRIVE_WORKSPACE,
        has_connector_obj=True,
        org_found=True,
        record_org_id="org-1",
    ):
        """Build all mocks needed for stream_record_internal."""
        import jwt as pyjwt

        record = _mock_record(connector_name=connector_name, org_id=record_org_id)

        graph_provider = AsyncMock()
        graph_provider.get_record_by_id = AsyncMock(return_value=record)

        org_doc = {"_key": "org-1", "name": "Test Org"} if org_found else None
        graph_provider.get_document = AsyncMock(return_value=org_doc)

        connector_instance = {"name": "My Drive", "type": "googledrive"}

        # Second call to get_document returns the connector instance
        async def _get_doc(doc_id, collection):
            if collection == "orgs":
                return org_doc
            return connector_instance

        graph_provider.get_document = AsyncMock(side_effect=_get_doc)

        connector_obj = AsyncMock()
        connector_obj.get_app_name = MagicMock(return_value=app_name)
        connector_obj.stream_record = AsyncMock(return_value=b"file-bytes")

        container = MagicMock()
        if has_connector_obj:
            container.connectors_map = {"conn-1": connector_obj}
        else:
            container.connectors_map = {}

        config_service = AsyncMock()
        config_service.get_config = AsyncMock(return_value={"scopedJwtSecret": "test-secret"})

        jwt_secret = "test-secret"
        token = pyjwt.encode({"orgId": "org-1", "userId": "user-1"}, jwt_secret, algorithm="HS256")

        req = MagicMock()
        req.headers = MagicMock()
        req.headers.get = lambda k, default=None: {
            "Authorization": f"Bearer {token}",
        }.get(k, default)
        req.app = MagicMock()
        req.app.container = container

        return req, graph_provider, config_service, connector_obj

    @pytest.mark.asyncio
    async def test_google_drive_workspace_passes_user_id(self):
        """Google Drive Workspace calls stream_record(record, userId)."""
        req, gp, cs, conn_obj = self._setup_stream(
            app_name=Connectors.GOOGLE_DRIVE_WORKSPACE,
        )

        result = await stream_record_internal(req, "rec-1", gp, cs)

        conn_obj.stream_record.assert_awaited_once()
        call_args = conn_obj.stream_record.call_args[0]
        assert len(call_args) == 2  # record, userId

    @pytest.mark.asyncio
    async def test_google_mail_workspace_passes_user_id(self):
        """Google Mail Workspace also calls stream_record(record, userId)."""
        req, gp, cs, conn_obj = self._setup_stream(
            app_name=Connectors.GOOGLE_MAIL_WORKSPACE,
        )

        result = await stream_record_internal(req, "rec-1", gp, cs)

        conn_obj.stream_record.assert_awaited_once()
        call_args = conn_obj.stream_record.call_args[0]
        assert len(call_args) == 2

    @pytest.mark.asyncio
    async def test_non_google_connector_no_user_id(self):
        """Non-Google connector calls stream_record(record) only."""
        req, gp, cs, conn_obj = self._setup_stream(
            app_name=Connectors.SLACK,
        )

        result = await stream_record_internal(req, "rec-1", gp, cs)

        conn_obj.stream_record.assert_awaited_once()
        call_args = conn_obj.stream_record.call_args[0]
        assert len(call_args) == 1  # only record

    @pytest.mark.asyncio
    async def test_org_retry_with_different_record_org_id(self):
        """When org not found initially but record has different org_id, retry succeeds."""
        import jwt as pyjwt

        record = _mock_record(org_id="record-org-id")

        graph_provider = AsyncMock()
        graph_provider.get_record_by_id = AsyncMock(return_value=record)

        call_count = 0

        async def _get_doc(doc_id, collection):
            nonlocal call_count
            if collection == "orgs":
                call_count += 1
                if call_count == 1:
                    # First call (with jwt org_id) returns None
                    return None
                else:
                    # Retry with record org_id returns the org
                    return {"_key": "record-org-id", "name": "Record Org"}
            # connector instance
            return {"name": "My Conn", "type": "slack"}

        graph_provider.get_document = AsyncMock(side_effect=_get_doc)

        connector_obj = AsyncMock()
        connector_obj.get_app_name = MagicMock(return_value=Connectors.SLACK)
        connector_obj.stream_record = AsyncMock(return_value=b"data")

        container = MagicMock()
        container.connectors_map = {"conn-1": connector_obj}

        config_service = AsyncMock()
        config_service.get_config = AsyncMock(return_value={"scopedJwtSecret": "test-secret"})

        token = pyjwt.encode({"orgId": "org-1", "userId": "user-1"}, "test-secret", algorithm="HS256")

        req = MagicMock()
        req.headers = MagicMock()
        req.headers.get = lambda k, default=None: {
            "Authorization": f"Bearer {token}",
        }.get(k, default)
        req.app = MagicMock()
        req.app.container = container

        result = await stream_record_internal(req, "rec-1", graph_provider, config_service)

        # Should have succeeded despite first org lookup failing
        connector_obj.stream_record.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_validation_error_raises_400(self):
        """ValidationError in token payload -> 400."""
        from pydantic import ValidationError as PydanticValidationError

        req = MagicMock()
        req.headers = MagicMock()
        req.headers.get = lambda k, default=None: {
            "Authorization": "Bearer bad-token",
        }.get(k, default)

        config_service = AsyncMock()
        config_service.get_config = AsyncMock(return_value={"scopedJwtSecret": "secret"})

        # jwt.decode will raise a different error, but we can test ValidationError path
        # by patching jwt.decode to raise ValidationError
        with patch("app.connectors.api.router.jwt.decode") as mock_decode:
            # Create a real ValidationError
            from pydantic import BaseModel

            class DummyModel(BaseModel):
                x: int

            try:
                DummyModel(x="not-an-int")  # This actually succeeds with coercion
            except Exception:
                pass

            # Use a simpler approach - mock it to raise a ValidationError-like exception
            mock_decode.side_effect = PydanticValidationError.from_exception_data(
                title="test",
                line_errors=[],
            )

            with pytest.raises(HTTPException) as exc:
                await stream_record_internal(req, "rec-1", AsyncMock(), config_service)
            assert exc.value.status_code == HttpStatusCode.BAD_REQUEST.value

    @pytest.mark.asyncio
    async def test_generic_exception_raises_500(self):
        """Unexpected exception -> 500."""
        req = MagicMock()
        req.headers = MagicMock()
        req.headers.get = lambda k, default=None: {
            "Authorization": "Bearer some-token",
        }.get(k, default)

        config_service = AsyncMock()
        config_service.get_config = AsyncMock(side_effect=RuntimeError("boom"))

        with pytest.raises(HTTPException) as exc:
            await stream_record_internal(req, "rec-1", AsyncMock(), config_service)
        assert exc.value.status_code == HttpStatusCode.INTERNAL_SERVER_ERROR.value
        assert "Error streaming record" in exc.value.detail


# ============================================================================
# download_file -- error paths (lines 664-683)
# ============================================================================


class TestDownloadFileErrorPaths:
    """Cover download_file error paths in the inner and outer try/except blocks."""

    def _setup_download(
        self,
        *,
        app_name=Connectors.GOOGLE_DRIVE_WORKSPACE,
        has_connector_obj=True,
        stream_raises=None,
        connector_type="googledrive",
    ):
        """Build all mocks for download_file."""
        payload = SimpleNamespace(record_id="rec-1", user_id="user-1")
        handler = MagicMock()
        handler.validate_token = MagicMock(return_value=payload)

        record = _mock_record(connector_name=Connectors.GOOGLE_DRIVE)

        graph_provider = AsyncMock()
        graph_provider.get_document = AsyncMock(
            return_value={"_key": "org-1", "name": "Test Org", "type": connector_type}
        )
        graph_provider.get_record_by_id = AsyncMock(return_value=record)

        connector_obj = AsyncMock()
        connector_obj.get_app_name = MagicMock(return_value=app_name)
        if stream_raises:
            connector_obj.stream_record = AsyncMock(side_effect=stream_raises)
        else:
            connector_obj.stream_record = AsyncMock(return_value=b"file-data")

        container = MagicMock()
        if has_connector_obj:
            container.connectors_map = MagicMock()
            container.connectors_map.get = MagicMock(return_value=connector_obj)
        else:
            container.connectors_map = MagicMock()
            container.connectors_map.get = MagicMock(return_value=None)

        req = MagicMock()
        req.app = MagicMock()
        req.app.container = container

        return req, handler, graph_provider, connector_obj

    @pytest.mark.asyncio
    async def test_google_drive_passes_user_id(self):
        """Google Drive passes user_id to stream_record."""
        req, handler, gp, conn_obj = self._setup_download(
            app_name=Connectors.GOOGLE_DRIVE_WORKSPACE,
        )

        result = await download_file(req, "org-1", "rec-1", "googledrive", "tok", handler, gp)

        assert result == b"file-data"
        call_args = conn_obj.stream_record.call_args[0]
        assert len(call_args) == 2  # record, user_id

    @pytest.mark.asyncio
    async def test_google_mail_passes_user_id(self):
        """Google Mail Workspace passes user_id to stream_record."""
        req, handler, gp, conn_obj = self._setup_download(
            app_name=Connectors.GOOGLE_MAIL_WORKSPACE,
        )

        result = await download_file(req, "org-1", "rec-1", "googledrive", "tok", handler, gp)

        call_args = conn_obj.stream_record.call_args[0]
        assert len(call_args) == 2

    @pytest.mark.asyncio
    async def test_non_google_no_user_id(self):
        """Non-Google connector passes only record."""
        req, handler, gp, conn_obj = self._setup_download(
            app_name=Connectors.SLACK,
        )

        result = await download_file(req, "org-1", "rec-1", "slack", "tok", handler, gp)

        call_args = conn_obj.stream_record.call_args[0]
        assert len(call_args) == 1

    @pytest.mark.asyncio
    async def test_stream_record_inner_exception_raises_500(self):
        """When stream_record raises a non-HTTP exception -> inner except -> 500."""
        req, handler, gp, _ = self._setup_download(
            stream_raises=RuntimeError("connection lost"),
        )

        with pytest.raises(HTTPException) as exc:
            await download_file(req, "org-1", "rec-1", "googledrive", "tok", handler, gp)
        assert exc.value.status_code == HttpStatusCode.INTERNAL_SERVER_ERROR.value
        assert "Error downloading file" in exc.value.detail
        assert "connection lost" in exc.value.detail

    @pytest.mark.asyncio
    async def test_inner_http_exception_re_raised(self):
        """An HTTPException from stream_record is re-raised through inner except."""
        inner_exc = HTTPException(status_code=403, detail="Forbidden")
        req, handler, gp, _ = self._setup_download(stream_raises=inner_exc)

        with pytest.raises(HTTPException) as exc:
            await download_file(req, "org-1", "rec-1", "googledrive", "tok", handler, gp)
        assert exc.value.status_code == 403

    @pytest.mark.asyncio
    async def test_outer_exception_from_validate_token(self):
        """Outer except catches non-HTTP errors (e.g., from validate_token)."""
        payload = SimpleNamespace(record_id="rec-1", user_id="user-1")
        handler = MagicMock()
        handler.validate_token = MagicMock(side_effect=ValueError("invalid token format"))

        req = MagicMock()
        req.app = MagicMock()

        with pytest.raises(HTTPException) as exc:
            await download_file(req, "org-1", "rec-1", "googledrive", "tok", handler, AsyncMock())
        assert exc.value.status_code == HttpStatusCode.INTERNAL_SERVER_ERROR.value
        assert "Error downloading file" in exc.value.detail

    @pytest.mark.asyncio
    async def test_connector_instance_no_type_raises_404(self):
        """Connector instance exists but has no type -> 404."""
        payload = SimpleNamespace(record_id="rec-1", user_id="user-1")
        handler = MagicMock()
        handler.validate_token = MagicMock(return_value=payload)

        record = _mock_record()
        graph_provider = AsyncMock()
        graph_provider.get_record_by_id = AsyncMock(return_value=record)

        call_count = 0

        async def _get_doc(doc_id, collection):
            nonlocal call_count
            call_count += 1
            if collection == "orgs":
                return {"_key": "org-1"}
            # connector instance with no type
            return {"name": "My Conn", "type": None}

        graph_provider.get_document = AsyncMock(side_effect=_get_doc)

        req = MagicMock()
        req.app = MagicMock()

        with pytest.raises(HTTPException) as exc:
            await download_file(req, "org-1", "rec-1", "googledrive", "tok", handler, graph_provider)
        assert exc.value.status_code == HttpStatusCode.NOT_FOUND.value
        assert "no longer exists" in exc.value.detail
