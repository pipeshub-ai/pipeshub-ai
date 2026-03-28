"""Deep-coverage tests for app/connectors/api/router.py — second half (lines 3500-6675).

Targets the specific uncovered blocks identified by coverage analysis:
  - handle_oauth_callback deep paths (token exchange, config update, error handling)
  - _get_connector_filter_options_from_config / _fetch_filter_options_from_api error paths
  - get_connector_instance_filters error paths
  - get_filter_field_options deep paths (API fetch, fallback options)
  - save_connector_instance_filters error path
  - _ensure_connector_initialized deep paths
  - toggle_connector_instance enable/disable deep flows
  - delete_connector_instance deep paths
  - get_connector_schema / get_oauth_config_registry_by_type error paths
  - get_all_oauth_configs admin vs non-admin, error paths
  - _create_or_update_oauth_config update vs create paths
  - CRUD route error paths (create/list/get/update/delete oauth_config)
"""

import base64
import json
import logging
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

_OAUTH_REGISTRY_PATCH = (
    "app.connectors.core.registry.oauth_config_registry.get_oauth_config_registry"
)
_BETA_PATCH = "app.connectors.api.router.check_beta_connector_access"
_TIMESTAMP_PATCH = "app.connectors.api.router.get_epoch_timestamp_in_ms"
_SETTINGS_PATH_PATCH = "app.connectors.api.router._get_settings_base_path"
_BUILD_FLOW_PATCH = "app.connectors.api.router._build_oauth_flow_config"
_OAUTH_CONFIG_PATCH = "app.connectors.api.router.get_oauth_config"
_OAUTH_PROVIDER_PATCH = "app.connectors.api.router.OAuthProvider"
_CONNECTOR_FACTORY_PATCH = "app.connectors.api.router.ConnectorFactory.create_connector"
_GRAPH_DATA_STORE_PATCH = "app.connectors.core.base.data_store.graph_data_store.GraphDataStore"


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


def _encode_state(state: str = "orig", connector_id: str = "c1") -> str:
    """Produce a base64-encoded state that _decode_state_with_instance can parse."""
    return base64.urlsafe_b64encode(
        json.dumps({"state": state, "connector_id": connector_id}).encode()
    ).decode()


def _make_token(access_token="access_tok", refresh_token="refresh_tok"):
    """Build a mock OAuthToken with all required attributes."""
    tok = MagicMock()
    tok.access_token = access_token
    tok.refresh_token = refresh_token
    tok.token_type = "Bearer"
    tok.expires_in = 3600
    tok.refresh_token_expires_in = None
    tok.scope = "scope1"
    tok.id_token = None
    tok.uid = None
    tok.account_id = None
    tok.team_id = None
    tok.created_at = None
    return tok


# ===========================================================================
# handle_oauth_callback — deep paths
# ===========================================================================


class TestHandleOAuthCallbackDeep:
    """Cover lines 4034-4087, 4208, 4294-4320, 4358."""

    async def test_non_creator_non_admin_returns_server_error(self):
        """Line 4208: non-creator, non-admin gets caught by outer except."""
        from app.connectors.api.router import handle_oauth_callback

        state = _encode_state()
        req = _make_request(user_id="u1", is_admin=False)
        instance = _make_instance(scope="personal", created_by="other_user")
        req.app.state.connector_registry.get_connector_instance = AsyncMock(
            return_value=instance
        )
        req.app.state.connector_registry.update_connector_instance = AsyncMock()

        with patch(_SETTINGS_PATH_PATCH, new_callable=AsyncMock, return_value="/settings"), \
             patch(_BETA_PATCH, new_callable=AsyncMock), \
             patch(_TIMESTAMP_PATCH, return_value=1000):
            result = await handle_oauth_callback(
                req, code="code", state=state, error=None,
                base_url="https://example.com", graph_provider=AsyncMock(),
            )

        assert result["success"] is False

    async def test_config_cache_refresh_succeeds(self):
        """Lines 4294-4296: successful config cache refresh after token exchange."""
        from app.connectors.api.router import handle_oauth_callback

        state = _encode_state()
        req = _make_request(is_admin=True)
        instance = _make_instance(scope="team", created_by="u1")
        req.app.state.connector_registry.get_connector_instance = AsyncMock(
            return_value=instance
        )
        req.app.state.connector_registry.update_connector_instance = AsyncMock(
            return_value=True
        )

        config_service = req.app.container.config_service()
        # First call returns auth config, second call (cache refresh) returns updated dict
        config_service.get_config = AsyncMock(
            side_effect=[
                {"auth": {"clientId": "cid"}},  # initial config
                {"auth": {"clientId": "cid"}, "credentials": {"access_token": "new"}},  # cache refresh
            ]
        )
        config_service.set_config = AsyncMock(return_value=True)

        mock_token = _make_token()
        mock_oauth_provider = AsyncMock()
        mock_oauth_provider.handle_callback = AsyncMock(return_value=mock_token)
        mock_oauth_provider.close = AsyncMock()

        mock_refresh_service = AsyncMock()
        mock_refresh_service.schedule_token_refresh = AsyncMock()
        mock_startup = MagicMock()
        mock_startup.get_token_refresh_service.return_value = mock_refresh_service

        with patch(_SETTINGS_PATH_PATCH, new_callable=AsyncMock, return_value="/settings/connectors"), \
             patch(_BETA_PATCH, new_callable=AsyncMock), \
             patch(_BUILD_FLOW_PATCH, new_callable=AsyncMock, return_value={"clientId": "cid"}), \
             patch(_OAUTH_CONFIG_PATCH, return_value=MagicMock()), \
             patch(_OAUTH_PROVIDER_PATCH, return_value=mock_oauth_provider), \
             patch(_TIMESTAMP_PATCH, return_value=1000), \
             patch("app.connectors.core.base.token_service.startup_service.startup_service", mock_startup):
            result = await handle_oauth_callback(
                req, code="auth_code", state=state, error=None,
                base_url="https://example.com", graph_provider=AsyncMock(),
            )

        assert result["success"] is True
        # set_config should have been called to refresh cache
        config_service.set_config.assert_called()

    async def test_config_cache_refresh_failure_continues(self):
        """Lines 4297-4298: cache refresh failure is logged but doesn't fail the callback."""
        from app.connectors.api.router import handle_oauth_callback

        state = _encode_state()
        req = _make_request(is_admin=True)
        instance = _make_instance(scope="team", created_by="u1")
        req.app.state.connector_registry.get_connector_instance = AsyncMock(
            return_value=instance
        )
        req.app.state.connector_registry.update_connector_instance = AsyncMock(
            return_value=True
        )

        config_service = req.app.container.config_service()
        call_count = 0

        async def config_side_effect(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return {"auth": {"clientId": "cid"}}
            # second call (cache refresh) fails
            raise RuntimeError("cache refresh error")

        config_service.get_config = AsyncMock(side_effect=config_side_effect)
        config_service.set_config = AsyncMock(return_value=True)

        mock_token = _make_token()
        mock_oauth_provider = AsyncMock()
        mock_oauth_provider.handle_callback = AsyncMock(return_value=mock_token)
        mock_oauth_provider.close = AsyncMock()

        mock_refresh_service = AsyncMock()
        mock_refresh_service.schedule_token_refresh = AsyncMock()
        mock_startup = MagicMock()
        mock_startup.get_token_refresh_service.return_value = mock_refresh_service

        with patch(_SETTINGS_PATH_PATCH, new_callable=AsyncMock, return_value="/settings/connectors"), \
             patch(_BETA_PATCH, new_callable=AsyncMock), \
             patch(_BUILD_FLOW_PATCH, new_callable=AsyncMock, return_value={"clientId": "cid"}), \
             patch(_OAUTH_CONFIG_PATCH, return_value=MagicMock()), \
             patch(_OAUTH_PROVIDER_PATCH, return_value=mock_oauth_provider), \
             patch(_TIMESTAMP_PATCH, return_value=1000), \
             patch("app.connectors.core.base.token_service.startup_service.startup_service", mock_startup):
            result = await handle_oauth_callback(
                req, code="auth_code", state=state, error=None,
                base_url="https://example.com", graph_provider=AsyncMock(),
            )

        assert result["success"] is True

    async def test_token_refresh_service_not_initialized_fallback(self):
        """Lines 4310-4318: fallback to temporary TokenRefreshService."""
        from app.connectors.api.router import handle_oauth_callback

        state = _encode_state()
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
            return_value={"auth": {"clientId": "cid"}}
        )
        config_service.set_config = AsyncMock(return_value=True)

        mock_token = _make_token()
        mock_oauth_provider = AsyncMock()
        mock_oauth_provider.handle_callback = AsyncMock(return_value=mock_token)
        mock_oauth_provider.close = AsyncMock()

        # startup_service returns None for refresh service
        mock_startup = MagicMock()
        mock_startup.get_token_refresh_service.return_value = None

        mock_temp_service = AsyncMock()
        mock_temp_service.schedule_token_refresh = AsyncMock()

        with patch(_SETTINGS_PATH_PATCH, new_callable=AsyncMock, return_value="/settings/connectors"), \
             patch(_BETA_PATCH, new_callable=AsyncMock), \
             patch(_BUILD_FLOW_PATCH, new_callable=AsyncMock, return_value={"clientId": "cid"}), \
             patch(_OAUTH_CONFIG_PATCH, return_value=MagicMock()), \
             patch(_OAUTH_PROVIDER_PATCH, return_value=mock_oauth_provider), \
             patch(_TIMESTAMP_PATCH, return_value=1000), \
             patch("app.connectors.core.base.token_service.startup_service.startup_service", mock_startup), \
             patch("app.connectors.core.base.token_service.token_refresh_service.TokenRefreshService", return_value=mock_temp_service):
            result = await handle_oauth_callback(
                req, code="auth_code", state=state, error=None,
                base_url="https://example.com", graph_provider=AsyncMock(),
            )

        assert result["success"] is True
        mock_temp_service.schedule_token_refresh.assert_called_once()

    async def test_token_refresh_schedule_failure_continues(self):
        """Line 4320: scheduling token refresh fails but callback still succeeds."""
        from app.connectors.api.router import handle_oauth_callback

        state = _encode_state()
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
            return_value={"auth": {"clientId": "cid"}}
        )
        config_service.set_config = AsyncMock(return_value=True)

        mock_token = _make_token()
        mock_oauth_provider = AsyncMock()
        mock_oauth_provider.handle_callback = AsyncMock(return_value=mock_token)
        mock_oauth_provider.close = AsyncMock()

        # startup_service import itself fails
        with patch(_SETTINGS_PATH_PATCH, new_callable=AsyncMock, return_value="/settings/connectors"), \
             patch(_BETA_PATCH, new_callable=AsyncMock), \
             patch(_BUILD_FLOW_PATCH, new_callable=AsyncMock, return_value={"clientId": "cid"}), \
             patch(_OAUTH_CONFIG_PATCH, return_value=MagicMock()), \
             patch(_OAUTH_PROVIDER_PATCH, return_value=mock_oauth_provider), \
             patch(_TIMESTAMP_PATCH, return_value=1000), \
             patch.dict("sys.modules", {"app.connectors.core.base.token_service.startup_service": MagicMock(startup_service=MagicMock(get_token_refresh_service=MagicMock(side_effect=RuntimeError("startup error"))))}):
            result = await handle_oauth_callback(
                req, code="auth_code", state=state, error=None,
                base_url="https://example.com", graph_provider=AsyncMock(),
            )

        assert result["success"] is True

    async def test_exception_updates_instance_and_returns_server_error(self):
        """Lines 4341-4365: general exception marks instance as unauthenticated."""
        from app.connectors.api.router import handle_oauth_callback

        state = _encode_state()
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
            return_value={"auth": {"clientId": "cid"}}
        )

        mock_oauth_provider = AsyncMock()
        mock_oauth_provider.handle_callback = AsyncMock(
            side_effect=RuntimeError("token exchange failed")
        )
        mock_oauth_provider.close = AsyncMock()

        with patch(_SETTINGS_PATH_PATCH, new_callable=AsyncMock, return_value="/settings"), \
             patch(_BETA_PATCH, new_callable=AsyncMock), \
             patch(_BUILD_FLOW_PATCH, new_callable=AsyncMock, return_value={"clientId": "cid"}), \
             patch(_OAUTH_CONFIG_PATCH, return_value=MagicMock()), \
             patch(_OAUTH_PROVIDER_PATCH, return_value=mock_oauth_provider), \
             patch(_TIMESTAMP_PATCH, return_value=1000):
            result = await handle_oauth_callback(
                req, code="code", state=state, error=None,
                base_url="https://example.com", graph_provider=AsyncMock(),
            )

        assert result["success"] is False
        assert result["error"] == "server_error"
        # Verify instance was marked as unauthenticated
        req.app.state.connector_registry.update_connector_instance.assert_called()

    async def test_exception_with_update_failure_still_returns_error(self):
        """Line 4358: update_connector_instance fails during error handling (pass)."""
        from app.connectors.api.router import handle_oauth_callback

        state = _encode_state()
        req = _make_request(is_admin=True)
        instance = _make_instance(scope="team", created_by="u1")
        req.app.state.connector_registry.get_connector_instance = AsyncMock(
            return_value=instance
        )
        req.app.state.connector_registry.update_connector_instance = AsyncMock(
            side_effect=RuntimeError("db error during cleanup")
        )

        config_service = req.app.container.config_service()
        config_service.get_config = AsyncMock(
            return_value={"auth": {"clientId": "cid"}}
        )

        mock_oauth_provider = AsyncMock()
        mock_oauth_provider.handle_callback = AsyncMock(
            side_effect=RuntimeError("token exchange failed")
        )
        mock_oauth_provider.close = AsyncMock()

        with patch(_SETTINGS_PATH_PATCH, new_callable=AsyncMock, return_value="/settings"), \
             patch(_BETA_PATCH, new_callable=AsyncMock), \
             patch(_BUILD_FLOW_PATCH, new_callable=AsyncMock, return_value={"clientId": "cid"}), \
             patch(_OAUTH_CONFIG_PATCH, return_value=MagicMock()), \
             patch(_OAUTH_PROVIDER_PATCH, return_value=mock_oauth_provider), \
             patch(_TIMESTAMP_PATCH, return_value=1000):
            result = await handle_oauth_callback(
                req, code="code", state=state, error=None,
                base_url="https://example.com", graph_provider=AsyncMock(),
            )

        assert result["success"] is False
        assert result["error"] == "server_error"


# ===========================================================================
# _get_connector_filter_options_from_config — deep paths
# ===========================================================================


class TestGetConnectorFilterOptionsFromConfigDeep:
    """Cover lines 4413-4418: API endpoint returns options, exception fallback."""

    async def test_api_endpoint_returns_options(self):
        """Lines 4413-4414: API call returns non-empty options, they are stored."""
        from app.connectors.api.router import _get_connector_filter_options_from_config

        connector_config = {
            "config": {
                "filters": {
                    "endpoints": {"labels": "https://api.test/labels"}
                }
            }
        }
        token = MagicMock()
        token.access_token = "tok123"

        mock_response = MagicMock()
        mock_response.status = 200
        mock_response.json = AsyncMock(return_value={
            "labels": [{"id": "L1", "name": "Label1", "type": "user"}]
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
        assert len(result["labels"]) == 1
        assert result["labels"][0]["value"] == "L1"

    async def test_api_endpoint_returns_empty_not_stored(self):
        """Line 4413: options is empty (falsy), filter_type not added to result."""
        from app.connectors.api.router import _get_connector_filter_options_from_config

        connector_config = {
            "config": {
                "filters": {
                    "endpoints": {"labels": "https://api.test/labels"}
                }
            }
        }

        mock_response = MagicMock()
        mock_response.status = 200
        mock_response.json = AsyncMock(return_value={"labels": []})

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
                "GMAIL", connector_config, {}, {}
            )

        # Empty list from API => not stored
        assert "labels" not in result

    async def test_per_filter_exception_falls_back_to_static(self):
        """Lines 4416-4421: exception in single filter endpoint falls back to static."""
        from app.connectors.api.router import _get_connector_filter_options_from_config

        connector_config = {
            "config": {
                "filters": {
                    "endpoints": {"fileTypes": "https://api.test/bad"}
                }
            }
        }

        with patch(
            "app.connectors.api.router._fetch_filter_options_from_api",
            new_callable=AsyncMock,
            side_effect=RuntimeError("network error"),
        ):
            result = await _get_connector_filter_options_from_config(
                "DRIVE", connector_config, {}, {}
            )

        # Should fall back to static filter options
        assert "fileTypes" in result
        assert len(result["fileTypes"]) > 0


# ===========================================================================
# get_connector_instance_filters — error paths
# ===========================================================================


class TestGetConnectorInstanceFiltersDeep:
    """Cover lines 4695-4727."""

    async def test_oauth_admin_consent_no_auth_raises_400(self):
        """Lines 4695-4700: OAUTH_ADMIN_CONSENT without auth config raises 400."""
        from app.connectors.api.router import get_connector_instance_filters

        req = _make_request(is_admin=True)
        instance = _make_instance(
            scope="team", created_by="u1", auth_type="OAUTH_ADMIN_CONSENT"
        )
        req.app.state.connector_registry.get_connector_instance = AsyncMock(
            return_value=instance
        )
        req.app.state.connector_registry.get_connector_metadata = AsyncMock(
            return_value={"config": {}}
        )

        config_service = req.app.container.config_service()
        config_service.get_config = AsyncMock(return_value={})  # no "auth" key

        with patch(_BETA_PATCH, new_callable=AsyncMock):
            with pytest.raises(HTTPException) as exc_info:
                await get_connector_instance_filters("c1", req, graph_provider=AsyncMock())
        assert exc_info.value.status_code == 400
        assert "Configuration not found" in exc_info.value.detail

    async def test_api_token_with_auth_config_succeeds(self):
        """Lines 4694-4701: API_TOKEN auth type with valid auth config succeeds."""
        from app.connectors.api.router import get_connector_instance_filters

        req = _make_request(is_admin=True)
        instance = _make_instance(
            scope="team", created_by="u1", auth_type="API_TOKEN"
        )
        req.app.state.connector_registry.get_connector_instance = AsyncMock(
            return_value=instance
        )
        req.app.state.connector_registry.get_connector_metadata = AsyncMock(
            return_value={"config": {"filters": {"endpoints": {}}}}
        )

        config_service = req.app.container.config_service()
        config_service.get_config = AsyncMock(
            return_value={"auth": {"api_token": "tok123"}}
        )

        with patch(_BETA_PATCH, new_callable=AsyncMock), \
             patch(
                 "app.connectors.api.router._get_connector_filter_options_from_config",
                 new_callable=AsyncMock,
                 return_value={"channels": []},
             ):
            result = await get_connector_instance_filters(
                "c1", req, graph_provider=AsyncMock()
            )

        assert result["success"] is True
        assert result["filterOptions"] == {"channels": []}

    async def test_generic_exception_raises_500(self):
        """Lines 4725-4727: unexpected exception raises 500."""
        from app.connectors.api.router import get_connector_instance_filters

        req = _make_request(is_admin=True)
        instance = _make_instance(
            scope="team", created_by="u1", auth_type="OAUTH"
        )
        req.app.state.connector_registry.get_connector_instance = AsyncMock(
            return_value=instance
        )
        req.app.state.connector_registry.get_connector_metadata = AsyncMock(
            return_value={"config": {}}
        )

        config_service = req.app.container.config_service()
        config_service.get_config = AsyncMock(
            return_value={"credentials": {"access_token": "tok"}}
        )

        with patch(_BETA_PATCH, new_callable=AsyncMock), \
             patch(
                 "app.connectors.api.router._get_connector_filter_options_from_config",
                 new_callable=AsyncMock,
                 side_effect=RuntimeError("unexpected"),
             ):
            with pytest.raises(HTTPException) as exc_info:
                await get_connector_instance_filters(
                    "c1", req, graph_provider=AsyncMock()
                )
        assert exc_info.value.status_code == 500


# ===========================================================================
# get_filter_field_options — deep paths
# ===========================================================================


class TestGetFilterFieldOptionsDeep:
    """Cover lines 4829-4869."""

    def _metadata_with_dynamic_filter(self, filter_key="space_keys"):
        return {
            "config": {
                "filters": {
                    "sync": {
                        "schema": {
                            "fields": [
                                {"name": filter_key, "optionSourceType": "dynamic"}
                            ]
                        }
                    }
                }
            }
        }

    async def test_connector_not_in_container_initializes_and_fetches(self):
        """Lines 4829-4861: connector not found -> initialize -> call get_filter_options."""
        from app.connectors.api.router import get_filter_field_options

        req = _make_request(is_admin=True)
        instance = _make_instance(
            scope="team", created_by="u1", auth_type="API_TOKEN",
            connector_type="CONFLUENCE",
        )
        req.app.state.connector_registry.get_connector_instance = AsyncMock(
            return_value=instance
        )
        req.app.state.connector_registry.get_connector_metadata = AsyncMock(
            return_value=self._metadata_with_dynamic_filter()
        )

        mock_connector = AsyncMock()
        mock_filter_response = MagicMock()
        mock_filter_response.to_dict.return_value = {
            "success": True,
            "options": [{"id": "s1", "value": "s1", "label": "Space 1"}],
            "pagination": {"page": 1, "limit": 20, "hasMore": False},
        }
        mock_connector.get_filter_options = AsyncMock(
            return_value=mock_filter_response
        )

        # Container has no connector
        container = req.app.container
        if hasattr(container, "connectors_map"):
            del container.connectors_map

        with patch(_BETA_PATCH, new_callable=AsyncMock), \
             patch(
                 "app.connectors.api.router._get_connector_from_container",
                 return_value=None,
             ), \
             patch(
                 "app.connectors.api.router._ensure_connector_initialized",
                 new_callable=AsyncMock,
                 return_value=mock_connector,
             ):
            result = await get_filter_field_options(
                "c1", "space_keys", req, graph_provider=AsyncMock()
            )

        assert result["success"] is True
        assert len(result["options"]) == 1

    async def test_connector_none_after_init_raises_400(self):
        """Lines 4845-4849: connector is None after initialization (e.g., Gmail)."""
        from app.connectors.api.router import get_filter_field_options

        req = _make_request(is_admin=True)
        instance = _make_instance(
            scope="team", created_by="u1", auth_type="OAUTH",
            connector_type="GMAIL",
        )
        req.app.state.connector_registry.get_connector_instance = AsyncMock(
            return_value=instance
        )
        req.app.state.connector_registry.get_connector_metadata = AsyncMock(
            return_value=self._metadata_with_dynamic_filter("labels")
        )

        with patch(_BETA_PATCH, new_callable=AsyncMock), \
             patch(
                 "app.connectors.api.router._get_connector_from_container",
                 return_value=None,
             ), \
             patch(
                 "app.connectors.api.router._ensure_connector_initialized",
                 new_callable=AsyncMock,
                 return_value=None,
             ):
            with pytest.raises(HTTPException) as exc_info:
                await get_filter_field_options(
                    "c1", "labels", req, graph_provider=AsyncMock()
                )
        assert exc_info.value.status_code == 400
        assert "does not support filter options" in exc_info.value.detail

    async def test_generic_exception_raises_500(self):
        """Lines 4865-4869: unexpected exception in get_filter_field_options raises 500."""
        from app.connectors.api.router import get_filter_field_options

        req = _make_request(is_admin=True)
        instance = _make_instance(
            scope="team", created_by="u1", auth_type="API_TOKEN",
            connector_type="CONFLUENCE",
        )
        req.app.state.connector_registry.get_connector_instance = AsyncMock(
            return_value=instance
        )
        req.app.state.connector_registry.get_connector_metadata = AsyncMock(
            return_value=self._metadata_with_dynamic_filter()
        )

        mock_connector = AsyncMock()
        mock_connector.get_filter_options = AsyncMock(
            side_effect=RuntimeError("connector crashed")
        )

        with patch(_BETA_PATCH, new_callable=AsyncMock), \
             patch(
                 "app.connectors.api.router._get_connector_from_container",
                 return_value=mock_connector,
             ):
            with pytest.raises(HTTPException) as exc_info:
                await get_filter_field_options(
                    "c1", "space_keys", req, graph_provider=AsyncMock()
                )
        assert exc_info.value.status_code == 500
        assert "Failed to get filter options" in exc_info.value.detail

    async def test_connector_found_in_container_fetches_directly(self):
        """Lines 4829, 4851-4861: connector already in container."""
        from app.connectors.api.router import get_filter_field_options

        req = _make_request(is_admin=True)
        instance = _make_instance(
            scope="team", created_by="u1", auth_type="API_TOKEN",
            connector_type="CONFLUENCE",
        )
        req.app.state.connector_registry.get_connector_instance = AsyncMock(
            return_value=instance
        )
        req.app.state.connector_registry.get_connector_metadata = AsyncMock(
            return_value=self._metadata_with_dynamic_filter()
        )

        mock_filter_response = MagicMock()
        mock_filter_response.to_dict.return_value = {
            "success": True,
            "options": [],
            "pagination": {"page": 1, "limit": 20, "hasMore": False},
        }
        mock_connector = AsyncMock()
        mock_connector.get_filter_options = AsyncMock(
            return_value=mock_filter_response
        )

        with patch(_BETA_PATCH, new_callable=AsyncMock), \
             patch(
                 "app.connectors.api.router._get_connector_from_container",
                 return_value=mock_connector,
             ):
            result = await get_filter_field_options(
                "c1", "space_keys", req, graph_provider=AsyncMock()
            )

        assert result["success"] is True


# ===========================================================================
# save_connector_instance_filters — error path
# ===========================================================================


class TestSaveConnectorInstanceFiltersDeep:
    """Cover lines 4986-4988."""

    async def test_generic_exception_raises_500(self):
        """Lines 4986-4988: unexpected exception in save_connector_instance_filters."""
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
        config_service.get_config = AsyncMock(
            return_value={"filters": {}}
        )
        config_service.set_config = AsyncMock(
            side_effect=RuntimeError("etcd write error")
        )

        with patch(_BETA_PATCH, new_callable=AsyncMock):
            with pytest.raises(HTTPException) as exc_info:
                await save_connector_instance_filters(
                    "c1", req, graph_provider=AsyncMock()
                )
        assert exc_info.value.status_code == 500
        assert "Failed to save filter selections" in exc_info.value.detail


# ===========================================================================
# _ensure_connector_initialized — deep paths
# ===========================================================================


class TestEnsureConnectorInitializedDeep:
    """Cover lines 5123-5126: generic exception wrapping."""

    async def test_generic_non_http_exception_raises_500(self):
        """Lines 5123-5126: non-HTTPException at top level gets wrapped in 500."""
        from app.connectors.api.router import _ensure_connector_initialized

        container = MagicMock()
        # config_service() itself raises
        container.config_service.side_effect = RuntimeError("DI failure")
        if hasattr(container, "connectors_map"):
            del container.connectors_map

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
        assert "Failed to initialize connector" in exc_info.value.detail


# ===========================================================================
# toggle_connector_instance — enable/disable deep flows
# ===========================================================================


class TestToggleConnectorInstanceDeep:
    """Cover lines 5234-5291, 5312-5387."""

    async def test_personal_scope_other_creator_raises_403(self):
        """Lines 5234-5238: personal connector toggled by someone other than creator."""
        from app.connectors.api.router import toggle_connector_instance

        req = _make_request(user_id="u1", is_admin=True, body={"type": "sync"})
        graph_provider = AsyncMock()
        graph_provider.get_document = AsyncMock(
            return_value={"_key": "o1", "accountType": "free"}
        )
        instance = _make_instance(
            scope="personal", created_by="other_user", is_active=False,
        )
        req.app.state.connector_registry.get_connector_instance = AsyncMock(
            return_value=instance
        )

        with patch(_BETA_PATCH, new_callable=AsyncMock):
            with pytest.raises(HTTPException) as exc_info:
                await toggle_connector_instance("c1", req, graph_provider=graph_provider)
        assert exc_info.value.status_code == 403
        assert "Only the creator can toggle" in exc_info.value.detail

    async def test_enable_sync_oauth_with_custom_google_business_logic_success(self):
        """Lines 5256-5273: enterprise + GMAIL WORKSPACE + team -> custom google business logic."""
        from app.connectors.api.router import toggle_connector_instance

        req = _make_request(is_admin=True, body={"type": "sync", "fullSync": False})
        graph_provider = AsyncMock()
        graph_provider.get_document = AsyncMock(
            return_value={"_key": "o1", "accountType": "enterprise"}
        )
        instance = _make_instance(
            scope="team", created_by="u1", auth_type="OAUTH",
            is_active=False, connector_type="GMAIL WORKSPACE",
        )
        req.app.state.connector_registry.get_connector_instance = AsyncMock(
            return_value=instance
        )
        req.app.state.connector_registry.update_connector_instance = AsyncMock(
            return_value=True
        )

        config_service = req.app.container.config_service()
        config_service.get_config = AsyncMock(
            return_value={"auth": {"client_id": "cid", "adminEmail": "admin@test.com"}}
        )

        with patch(_BETA_PATCH, new_callable=AsyncMock), \
             patch(_TIMESTAMP_PATCH, return_value=1000), \
             patch(
                 "app.connectors.api.router._ensure_connector_initialized",
                 new_callable=AsyncMock,
                 return_value=MagicMock(),
             ):
            result = await toggle_connector_instance(
                "c1", req, graph_provider=graph_provider
            )

        assert result["success"] is True

    async def test_enable_sync_oauth_google_business_no_creds_raises_400(self):
        """Lines 5265-5273: enterprise Google connector without proper auth creds."""
        from app.connectors.api.router import toggle_connector_instance

        req = _make_request(is_admin=True, body={"type": "sync"})
        graph_provider = AsyncMock()
        graph_provider.get_document = AsyncMock(
            return_value={"_key": "o1", "accountType": "enterprise"}
        )
        instance = _make_instance(
            scope="team", created_by="u1", auth_type="OAUTH",
            is_active=False, connector_type="DRIVE WORKSPACE",
        )
        req.app.state.connector_registry.get_connector_instance = AsyncMock(
            return_value=instance
        )

        config_service = req.app.container.config_service()
        config_service.get_config = AsyncMock(
            return_value={"auth": {}}  # missing client_id and adminEmail
        )

        with patch(_BETA_PATCH, new_callable=AsyncMock):
            with pytest.raises(HTTPException) as exc_info:
                await toggle_connector_instance(
                    "c1", req, graph_provider=graph_provider
                )
        assert exc_info.value.status_code == 400
        assert "OAuth authentication" in exc_info.value.detail

    async def test_enable_sync_oauth_non_google_with_credentials(self):
        """Lines 5274-5281: non-google OAuth with valid credentials passes."""
        from app.connectors.api.router import toggle_connector_instance

        req = _make_request(is_admin=True, body={"type": "sync", "fullSync": False})
        graph_provider = AsyncMock()
        graph_provider.get_document = AsyncMock(
            return_value={"_key": "o1", "accountType": "free"}
        )
        instance = _make_instance(
            scope="team", created_by="u1", auth_type="OAUTH",
            is_active=False, connector_type="SLACK",
        )
        req.app.state.connector_registry.get_connector_instance = AsyncMock(
            return_value=instance
        )
        req.app.state.connector_registry.update_connector_instance = AsyncMock(
            return_value=True
        )

        config_service = req.app.container.config_service()
        config_service.get_config = AsyncMock(
            return_value={"credentials": {"access_token": "tok123"}}
        )

        with patch(_BETA_PATCH, new_callable=AsyncMock), \
             patch(_TIMESTAMP_PATCH, return_value=1000), \
             patch(
                 "app.connectors.api.router._ensure_connector_initialized",
                 new_callable=AsyncMock,
                 return_value=MagicMock(),
             ):
            result = await toggle_connector_instance(
                "c1", req, graph_provider=graph_provider
            )

        assert result["success"] is True

    async def test_enable_sync_oauth_non_google_no_credentials_raises_400(self):
        """Lines 5275-5281: non-google OAuth without access_token raises 400."""
        from app.connectors.api.router import toggle_connector_instance

        req = _make_request(is_admin=True, body={"type": "sync"})
        graph_provider = AsyncMock()
        graph_provider.get_document = AsyncMock(
            return_value={"_key": "o1", "accountType": "free"}
        )
        instance = _make_instance(
            scope="team", created_by="u1", auth_type="OAUTH",
            is_active=False, connector_type="SLACK",
        )
        req.app.state.connector_registry.get_connector_instance = AsyncMock(
            return_value=instance
        )

        config_service = req.app.container.config_service()
        config_service.get_config = AsyncMock(return_value={"credentials": {}})

        with patch(_BETA_PATCH, new_callable=AsyncMock):
            with pytest.raises(HTTPException) as exc_info:
                await toggle_connector_instance(
                    "c1", req, graph_provider=graph_provider
                )
        assert exc_info.value.status_code == 400

    async def test_enable_sync_non_oauth_not_configured_raises_400(self):
        """Lines 5283-5288: non-OAuth connector not configured raises 400."""
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

        with patch(_BETA_PATCH, new_callable=AsyncMock):
            with pytest.raises(HTTPException) as exc_info:
                await toggle_connector_instance(
                    "c1", req, graph_provider=graph_provider
                )
        assert exc_info.value.status_code == 400
        assert "configured before enabling" in exc_info.value.detail

    async def test_enable_sync_calls_ensure_connector_initialized(self):
        """Lines 5291-5301: enabling sync calls _ensure_connector_initialized."""
        from app.connectors.api.router import toggle_connector_instance

        req = _make_request(is_admin=True, body={"type": "sync", "fullSync": True})
        graph_provider = AsyncMock()
        graph_provider.get_document = AsyncMock(
            return_value={"_key": "o1", "accountType": "free"}
        )
        instance = _make_instance(
            scope="team", created_by="u1", auth_type="API_TOKEN",
            is_active=False, is_configured=True,
        )
        req.app.state.connector_registry.get_connector_instance = AsyncMock(
            return_value=instance
        )
        req.app.state.connector_registry.update_connector_instance = AsyncMock(
            return_value=True
        )

        ensure_mock = AsyncMock(return_value=MagicMock())

        with patch(_BETA_PATCH, new_callable=AsyncMock), \
             patch(_TIMESTAMP_PATCH, return_value=1000), \
             patch(
                 "app.connectors.api.router._ensure_connector_initialized",
                 ensure_mock,
             ):
            result = await toggle_connector_instance(
                "c1", req, graph_provider=graph_provider
            )

        assert result["success"] is True
        ensure_mock.assert_called_once()

    async def test_enable_agent_not_configured_raises_400(self):
        """Lines 5312-5317: agent toggle on unconfigured connector."""
        from app.connectors.api.router import toggle_connector_instance

        req = _make_request(is_admin=True, body={"type": "agent"})
        graph_provider = AsyncMock()
        graph_provider.get_document = AsyncMock(
            return_value={"_key": "o1", "accountType": "free"}
        )
        instance = _make_instance(
            scope="team", created_by="u1", is_active=True,
            is_configured=False,
            extra={"supportsAgent": True, "isAgentActive": False},
        )
        req.app.state.connector_registry.get_connector_instance = AsyncMock(
            return_value=instance
        )

        with patch(_BETA_PATCH, new_callable=AsyncMock):
            with pytest.raises(HTTPException) as exc_info:
                await toggle_connector_instance(
                    "c1", req, graph_provider=graph_provider
                )
        assert exc_info.value.status_code == 400
        assert "configured before enabling" in exc_info.value.detail

    async def test_sync_enable_sends_app_enabled_event(self):
        """Lines 5342-5365: enabling sync sends appEnabled event via Kafka."""
        from app.connectors.api.router import toggle_connector_instance

        req = _make_request(is_admin=True, body={"type": "sync", "fullSync": True})
        graph_provider = AsyncMock()
        graph_provider.get_document = AsyncMock(
            return_value={"_key": "o1", "accountType": "free"}
        )
        instance = _make_instance(
            scope="team", created_by="u1", auth_type="API_TOKEN",
            is_active=False, is_configured=True,
        )
        req.app.state.connector_registry.get_connector_instance = AsyncMock(
            return_value=instance
        )
        req.app.state.connector_registry.update_connector_instance = AsyncMock(
            return_value=True
        )
        producer = req.app.container.messaging_producer
        producer.send_message = AsyncMock()

        with patch(_BETA_PATCH, new_callable=AsyncMock), \
             patch(_TIMESTAMP_PATCH, return_value=1000), \
             patch(
                 "app.connectors.api.router._ensure_connector_initialized",
                 new_callable=AsyncMock,
                 return_value=MagicMock(),
             ):
            result = await toggle_connector_instance(
                "c1", req, graph_provider=graph_provider
            )

        assert result["success"] is True
        producer.send_message.assert_called_once()
        call_kwargs = producer.send_message.call_args
        assert call_kwargs[1]["topic"] == "entity-events"
        assert call_kwargs[1]["message"]["eventType"] == "appEnabled"
        assert call_kwargs[1]["message"]["payload"]["fullSync"] is True

    async def test_sync_disable_sends_event_and_cleans_up_connector(self):
        """Lines 5367-5376: disabling sync removes connector from map and cleans up."""
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

        mock_connector = AsyncMock()
        mock_connector.cleanup = AsyncMock()
        req.app.container.connectors_map = {"c1": mock_connector}

        producer = req.app.container.messaging_producer
        producer.send_message = AsyncMock()

        with patch(_BETA_PATCH, new_callable=AsyncMock), \
             patch(_TIMESTAMP_PATCH, return_value=1000):
            result = await toggle_connector_instance(
                "c1", req, graph_provider=graph_provider
            )

        assert result["success"] is True
        producer.send_message.assert_called_once()
        mock_connector.cleanup.assert_called_once()
        assert "c1" not in req.app.container.connectors_map

    async def test_sync_disable_cleanup_error_continues(self):
        """Lines 5375-5376: cleanup error during disable is logged but doesn't fail."""
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
            return_value=True
        )

        mock_connector = AsyncMock()
        mock_connector.cleanup = AsyncMock(side_effect=RuntimeError("cleanup failed"))
        req.app.container.connectors_map = {"c1": mock_connector}

        producer = req.app.container.messaging_producer
        producer.send_message = AsyncMock()

        with patch(_BETA_PATCH, new_callable=AsyncMock), \
             patch(_TIMESTAMP_PATCH, return_value=1000):
            result = await toggle_connector_instance(
                "c1", req, graph_provider=graph_provider
            )

        assert result["success"] is True

    async def test_generic_exception_raises_500(self):
        """Lines 5385-5387: generic exception in toggle raises 500."""
        from app.connectors.api.router import toggle_connector_instance

        req = _make_request(is_admin=True, body={"type": "sync"})
        graph_provider = AsyncMock()
        graph_provider.get_document = AsyncMock(
            side_effect=RuntimeError("unexpected DB error")
        )

        with pytest.raises(HTTPException) as exc_info:
            await toggle_connector_instance("c1", req, graph_provider=graph_provider)
        assert exc_info.value.status_code == 500


# ===========================================================================
# delete_connector_instance — deep paths
# ===========================================================================


class TestDeleteConnectorInstanceDeep:
    """Cover lines 5476-5525."""

    async def test_app_disabled_event_failure_continues(self):
        """Lines 5476-5480: appDisabled event send failure doesn't stop deletion."""
        from app.connectors.api.router import delete_connector_instance

        req = _make_request(is_admin=True)
        instance = _make_instance(
            scope="team", created_by="u1",
            extra={"isActive": True},
        )
        req.app.state.connector_registry.get_connector_instance = AsyncMock(
            return_value=instance
        )

        graph_provider = AsyncMock()
        graph_provider.batch_upsert_nodes = AsyncMock()

        producer = req.app.container.messaging_producer
        call_count = 0

        async def send_side_effect(**kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise RuntimeError("kafka error")
            # second call (deletion event) succeeds

        producer.send_message = AsyncMock(side_effect=send_side_effect)

        with patch(_BETA_PATCH, new_callable=AsyncMock), \
             patch("app.connectors.api.router._validate_connector_deletion_permissions"), \
             patch(_TIMESTAMP_PATCH, return_value=1000):
            result = await delete_connector_instance(
                "c1", req, graph_provider=graph_provider
            )

        assert result.status_code == 202

    async def test_generic_exception_raises_500(self):
        """Lines 5523-5525: generic exception during deletion raises 500."""
        from app.connectors.api.router import delete_connector_instance

        req = _make_request(is_admin=True)
        instance = _make_instance(
            scope="team", created_by="u1",
        )
        req.app.state.connector_registry.get_connector_instance = AsyncMock(
            return_value=instance
        )

        graph_provider = AsyncMock()
        graph_provider.batch_upsert_nodes = AsyncMock(
            side_effect=RuntimeError("graph DB error")
        )

        producer = req.app.container.messaging_producer
        producer.send_message = AsyncMock()

        with patch(_BETA_PATCH, new_callable=AsyncMock), \
             patch("app.connectors.api.router._validate_connector_deletion_permissions"), \
             patch(_TIMESTAMP_PATCH, return_value=1000):
            with pytest.raises(HTTPException) as exc_info:
                await delete_connector_instance(
                    "c1", req, graph_provider=graph_provider
                )
        assert exc_info.value.status_code == 500
        assert "Failed to initiate connector deletion" in exc_info.value.detail

    async def test_successful_deletion_flow(self):
        """Lines 5460-5519: full success path with event publishing and DELETING status."""
        from app.connectors.api.router import delete_connector_instance

        req = _make_request(is_admin=True)
        instance = _make_instance(
            scope="team", created_by="u1",
            extra={"isActive": True},
        )
        req.app.state.connector_registry.get_connector_instance = AsyncMock(
            return_value=instance
        )

        graph_provider = AsyncMock()
        graph_provider.batch_upsert_nodes = AsyncMock()

        producer = req.app.container.messaging_producer
        producer.send_message = AsyncMock()

        with patch(_BETA_PATCH, new_callable=AsyncMock), \
             patch("app.connectors.api.router._validate_connector_deletion_permissions"), \
             patch(_TIMESTAMP_PATCH, return_value=1000):
            result = await delete_connector_instance(
                "c1", req, graph_provider=graph_provider
            )

        assert result.status_code == 202
        # Verify two messages sent: appDisabled + delete
        assert producer.send_message.call_count == 2
        # Verify batch_upsert_nodes called to set DELETING status
        graph_provider.batch_upsert_nodes.assert_called_once()


# ===========================================================================
# get_connector_schema / get_oauth_config_registry_by_type — error paths
# ===========================================================================


class TestGetConnectorSchemaDeep:
    """Cover lines 5623-5625."""

    async def test_generic_exception_raises_500(self):
        """Lines 5623-5625: unexpected exception in get_connector_schema."""
        from app.connectors.api.router import get_connector_schema

        req = _make_request()
        req.app.state.connector_registry.get_connector_metadata = AsyncMock(
            side_effect=RuntimeError("registry crashed")
        )

        with patch(_BETA_PATCH, new_callable=AsyncMock):
            with pytest.raises(HTTPException) as exc_info:
                await get_connector_schema("GMAIL", req)
        assert exc_info.value.status_code == 500
        assert "Failed to get connector schema" in exc_info.value.detail


class TestGetOAuthConfigRegistryByTypeDeep:
    """Cover lines 5805-5807."""

    async def test_generic_exception_raises_500(self):
        """Lines 5805-5807: unexpected exception in get_oauth_config_registry_by_type."""
        from app.connectors.api.router import get_oauth_config_registry_by_type

        req = _make_request()

        with patch(
            _OAUTH_REGISTRY_PATCH,
            side_effect=RuntimeError("registry error"),
        ):
            with pytest.raises(HTTPException) as exc_info:
                await get_oauth_config_registry_by_type("GMAIL", req)
        assert exc_info.value.status_code == 500


# ===========================================================================
# get_all_oauth_configs — admin vs non-admin, error paths
# ===========================================================================


class TestGetAllOAuthConfigsDeep:
    """Cover lines 5885-5901, 5948-5950."""

    async def test_fetch_configs_exception_returns_empty_list(self):
        """Lines 5885-5887: exception in fetch_configs_for_type returns empty."""
        from app.connectors.api.router import get_all_oauth_configs

        req = _make_request()
        mock_registry = MagicMock()
        mock_registry.get_oauth_connectors.return_value = ["GMAIL"]

        config_service = AsyncMock()
        config_service.get_config = AsyncMock(
            side_effect=RuntimeError("etcd timeout")
        )

        with patch(_OAUTH_REGISTRY_PATCH, return_value=mock_registry):
            result = await get_all_oauth_configs(
                req, page=1, limit=20, search=None, config_service=config_service
            )

        assert result["success"] is True
        assert result["oauthConfigs"] == []

    async def test_parallel_fetch_exception_results_logged(self):
        """Lines 5898-5901: asyncio.gather result is an Exception, gets logged."""
        from app.connectors.api.router import get_all_oauth_configs
        import asyncio

        req = _make_request()
        mock_registry = MagicMock()
        mock_registry.get_oauth_connectors.return_value = ["GMAIL", "DRIVE"]

        config_service = AsyncMock()

        async def config_side_effect(path, *args, **kwargs):
            if "gmail" in path:
                return [{"_id": "c1", "orgId": "o1", "oauthInstanceName": "Gmail Config"}]
            raise RuntimeError("DRIVE etcd error")

        config_service.get_config = AsyncMock(side_effect=config_side_effect)

        with patch(_OAUTH_REGISTRY_PATCH, return_value=mock_registry):
            result = await get_all_oauth_configs(
                req, page=1, limit=20, search=None, config_service=config_service
            )

        assert result["success"] is True
        # GMAIL configs should be present, DRIVE failed
        assert len(result["oauthConfigs"]) >= 1

    async def test_generic_exception_raises_500(self):
        """Lines 5948-5950: unexpected exception in get_all_oauth_configs."""
        from app.connectors.api.router import get_all_oauth_configs

        req = _make_request()

        # Make _get_user_context fail with a non-HTTP exception
        req.state.user = MagicMock()
        req.state.user.get = MagicMock(side_effect=RuntimeError("user state corrupted"))

        with pytest.raises(HTTPException) as exc_info:
            await get_all_oauth_configs(
                req, page=1, limit=20, search=None, config_service=AsyncMock()
            )
        assert exc_info.value.status_code == 500

    async def test_empty_oauth_connectors_returns_empty_pagination(self):
        """Lines 5857-5870: no OAuth connectors returns empty with pagination."""
        from app.connectors.api.router import get_all_oauth_configs

        req = _make_request()
        mock_registry = MagicMock()
        mock_registry.get_oauth_connectors.return_value = []

        with patch(_OAUTH_REGISTRY_PATCH, return_value=mock_registry):
            result = await get_all_oauth_configs(
                req, page=1, limit=20, search=None, config_service=AsyncMock()
            )

        assert result["success"] is True
        assert result["oauthConfigs"] == []
        assert result["pagination"]["totalItems"] == 0
        assert result["pagination"]["totalPages"] == 0
        assert result["pagination"]["hasNext"] is False
        assert result["pagination"]["hasPrev"] is False


# ===========================================================================
# _create_or_update_oauth_config — update vs create paths
# ===========================================================================


class TestCreateOrUpdateOAuthConfigDeep:
    """Cover lines 6051-6065."""

    async def test_update_existing_config_creates_config_key_if_missing(self):
        """Lines 6064-6065: oauth_cfg without 'config' key gets it created."""
        from app.connectors.api.router import _create_or_update_oauth_config

        existing_config = {
            "_id": "existing_id",
            "orgId": "o1",
            "userId": "u1",
            # no "config" key
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
        # Verify the saved config has the "config" key with the clientId
        saved_configs = config_service.set_config.call_args[0][1]
        found = [c for c in saved_configs if c["_id"] == "existing_id"]
        assert found[0]["config"]["clientId"] == "new_cid"

    async def test_update_non_list_oauth_configs_resets_to_empty(self):
        """Line 6051: oauth_configs is not a list -> reset to []."""
        from app.connectors.api.router import _create_or_update_oauth_config

        config_service = AsyncMock()
        config_service.get_config = AsyncMock(return_value="not_a_list")
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
            )

        assert result is not None
        # It should create a new config since list was empty

    async def test_update_with_wrong_org_falls_through_to_create(self):
        """Update path: config found but wrong org, falls through to create new."""
        from app.connectors.api.router import _create_or_update_oauth_config

        existing_config = {
            "_id": "existing_id",
            "orgId": "other_org",
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
                auth_config={"clientId": "cid"},
                instance_name="Test",
                user_id="u1",
                org_id="o1",
                is_admin=False,
                config_service=config_service,
                base_url="",
                oauth_app_id="existing_id",
            )

        # Should create a new config since existing one belongs to different org
        assert result is not None
        assert result != "existing_id"

    async def test_create_with_no_logger_uses_default(self):
        """Line 6038-6040: logger=None creates a default logger."""
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
                logger=None,  # triggers default logger creation
            )

        assert result is not None


# ===========================================================================
# CRUD route error paths
# ===========================================================================


class TestCreateOAuthConfigErrorPath:
    """Cover line 6323-6325."""

    async def test_generic_exception_raises_500(self):
        """Lines 6323-6325: unexpected exception in create_oauth_config."""
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

        mock_registry = MagicMock()
        mock_registry.get_metadata.side_effect = RuntimeError("registry crash")

        with patch(_OAUTH_REGISTRY_PATCH, return_value=mock_registry):
            with pytest.raises(HTTPException) as exc_info:
                await create_oauth_config("GMAIL", req, config_service=config_service)
        assert exc_info.value.status_code == 500
        assert "Failed to create OAuth configuration" in exc_info.value.detail


class TestListOAuthConfigsErrorPath:
    """Cover lines 6400-6402."""

    async def test_generic_exception_raises_500(self):
        """Lines 6400-6402: unexpected exception in list_oauth_configs."""
        from app.connectors.api.router import list_oauth_configs

        req = _make_request()

        config_service = AsyncMock()
        config_service.get_config = AsyncMock(
            side_effect=RuntimeError("etcd error")
        )

        with pytest.raises(HTTPException) as exc_info:
            await list_oauth_configs(
                "GMAIL", req, page=1, limit=20, search=None,
                config_service=config_service,
            )
        assert exc_info.value.status_code == 500
        assert "Failed to list OAuth configurations" in exc_info.value.detail


class TestGetOAuthConfigByIdErrorPath:
    """Cover lines 6486-6488."""

    async def test_generic_exception_raises_500(self):
        """Lines 6486-6488: unexpected exception in get_oauth_config_by_id."""
        from app.connectors.api.router import get_oauth_config_by_id

        req = _make_request()

        config_service = AsyncMock()
        config_service.get_config = AsyncMock(
            side_effect=RuntimeError("etcd error")
        )

        with pytest.raises(HTTPException) as exc_info:
            await get_oauth_config_by_id(
                "GMAIL", "cfg1", req, config_service=config_service,
            )
        assert exc_info.value.status_code == 500
        assert "Failed to get OAuth configuration" in exc_info.value.detail


class TestUpdateOAuthConfigErrorPath:
    """Cover lines 6593-6595."""

    async def test_generic_exception_raises_500(self):
        """Lines 6593-6595: unexpected exception in update_oauth_config."""
        from app.connectors.api.router import update_oauth_config

        req = _make_request(is_admin=True, body={"oauthInstanceName": "Updated"})

        config_service = AsyncMock()
        config_service.get_config = AsyncMock(
            side_effect=RuntimeError("etcd read error")
        )

        with pytest.raises(HTTPException) as exc_info:
            await update_oauth_config(
                "GMAIL", "cfg1", req, config_service=config_service,
            )
        assert exc_info.value.status_code == 500
        assert "Failed to update OAuth configuration" in exc_info.value.detail


class TestDeleteOAuthConfigErrorPath:
    """Cover lines 6670-6672."""

    async def test_generic_exception_raises_500(self):
        """Lines 6670-6672: unexpected exception in delete_oauth_config."""
        from app.connectors.api.router import delete_oauth_config

        req = _make_request(is_admin=True)

        config_service = AsyncMock()
        config_service.get_config = AsyncMock(
            side_effect=RuntimeError("etcd error")
        )

        with pytest.raises(HTTPException) as exc_info:
            await delete_oauth_config(
                "GMAIL", "cfg1", req, config_service=config_service,
            )
        assert exc_info.value.status_code == 500
        assert "Failed to delete OAuth configuration" in exc_info.value.detail
