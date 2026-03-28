"""Unit tests for app.connectors.core.base.token_service.toolset_token_refresh_service."""

import asyncio
import time
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.connectors.core.base.token_service.oauth_service import OAuthToken
from app.connectors.core.base.token_service.toolset_token_refresh_service import (
    INITIAL_RETRY_DELAY,
    LOCK_TIMEOUT,
    MIN_IMMEDIATE_RECHECK_DELAY,
    MIN_PATH_PARTS_COUNT,
    MIN_SHORT_LIVED_REFRESH_WINDOW_SECONDS,
    PROACTIVE_REFRESH_WINDOW_SECONDS,
    REFRESH_COOLDOWN,
    SHORT_LIVED_TOKEN_BUFFER_RATIO,
    TOKEN_REFRESH_MAX_RETRIES,
    ToolsetTokenRefreshService,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_service(config_service=None):
    """Create a ToolsetTokenRefreshService with mocked deps."""
    cs = config_service or AsyncMock()
    return ToolsetTokenRefreshService(cs)


def _make_token(
    access_token="at_123",
    refresh_token="rt_123",
    expires_in=3600,
    created_at=None,
):
    return OAuthToken(
        access_token=access_token,
        refresh_token=refresh_token,
        expires_in=expires_in,
        created_at=created_at or datetime.now(),
    )


def _make_expired_token():
    return OAuthToken(
        access_token="at_exp",
        refresh_token="rt_exp",
        expires_in=100,
        created_at=datetime.now() - timedelta(seconds=200),
    )


def _make_short_lived_token(expires_in=120, age_seconds=0):
    """Token with short TTL."""
    return OAuthToken(
        access_token="at_short",
        refresh_token="rt_short",
        expires_in=expires_in,
        created_at=datetime.now() - timedelta(seconds=age_seconds),
    )


# ---------------------------------------------------------------------------
# Init
# ---------------------------------------------------------------------------


class TestToolsetTokenRefreshServiceInit:
    def test_init(self):
        svc = _make_service()
        assert svc._running is False
        assert svc._refresh_tasks == {}
        assert len(svc._processing_toolsets) == 0
        assert svc._toolset_locks == {}
        assert svc._schedule_locks == {}
        assert svc._last_refresh_time == {}

    def test_init_stores_configuration_service(self):
        cs = AsyncMock()
        svc = ToolsetTokenRefreshService(cs)
        assert svc.configuration_service is cs


# ---------------------------------------------------------------------------
# _get_toolset_lock / _get_schedule_lock
# ---------------------------------------------------------------------------


class TestToolsetLocks:
    def test_get_toolset_lock_creates_new(self):
        svc = _make_service()
        lock = svc._get_toolset_lock("/services/toolsets/inst1/user1")
        assert isinstance(lock, asyncio.Lock)

    def test_get_toolset_lock_returns_same(self):
        svc = _make_service()
        path = "/services/toolsets/inst1/user1"
        lock1 = svc._get_toolset_lock(path)
        lock2 = svc._get_toolset_lock(path)
        assert lock1 is lock2

    def test_get_toolset_lock_different_paths(self):
        svc = _make_service()
        lock1 = svc._get_toolset_lock("/services/toolsets/inst1/user1")
        lock2 = svc._get_toolset_lock("/services/toolsets/inst2/user2")
        assert lock1 is not lock2

    def test_get_schedule_lock_creates_new(self):
        svc = _make_service()
        lock = svc._get_schedule_lock("/services/toolsets/inst1/user1")
        assert isinstance(lock, asyncio.Lock)

    def test_get_schedule_lock_returns_same(self):
        svc = _make_service()
        path = "/services/toolsets/inst1/user1"
        lock1 = svc._get_schedule_lock(path)
        lock2 = svc._get_schedule_lock(path)
        assert lock1 is lock2


# ---------------------------------------------------------------------------
# Start / Stop
# ---------------------------------------------------------------------------


class TestStartStop:
    async def test_start_sets_running(self):
        svc = _make_service()
        svc._refresh_all_tokens = AsyncMock()
        with patch("asyncio.create_task"):
            await svc.start()
        assert svc._running is True
        svc._refresh_all_tokens.assert_awaited_once()

    async def test_start_twice_is_noop(self):
        svc = _make_service()
        svc._running = True
        svc._refresh_all_tokens = AsyncMock()
        await svc.start()
        svc._refresh_all_tokens.assert_not_awaited()

    async def test_start_creates_periodic_and_cleanup_tasks(self):
        svc = _make_service()
        svc._refresh_all_tokens = AsyncMock()
        created_tasks = []
        with patch("asyncio.create_task", side_effect=lambda c: created_tasks.append(c)):
            await svc.start()
        # Should have created 2 tasks: periodic_refresh_check and cleanup_old_locks
        assert len(created_tasks) == 2

    async def test_stop(self):
        svc = _make_service()
        svc._running = True
        # Add a mock task
        mock_task = MagicMock()
        svc._refresh_tasks["path1"] = mock_task
        await svc.stop()
        assert svc._running is False
        assert svc._refresh_tasks == {}
        mock_task.cancel.assert_called_once()

    async def test_stop_cancels_multiple_tasks(self):
        svc = _make_service()
        svc._running = True
        task1 = MagicMock()
        task2 = MagicMock()
        svc._refresh_tasks = {"path1": task1, "path2": task2}
        await svc.stop()
        task1.cancel.assert_called_once()
        task2.cancel.assert_called_once()
        assert svc._refresh_tasks == {}


# ---------------------------------------------------------------------------
# _is_toolset_authenticated
# ---------------------------------------------------------------------------


class TestIsToolsetAuthenticated:
    async def test_no_config(self):
        cs = AsyncMock()
        cs.get_config = AsyncMock(return_value=None)
        svc = _make_service(cs)
        result = await svc._is_toolset_authenticated("/services/toolsets/inst/user")
        assert result is False

    async def test_not_authenticated_flag(self):
        cs = AsyncMock()
        cs.get_config = AsyncMock(return_value={"isAuthenticated": False})
        svc = _make_service(cs)
        result = await svc._is_toolset_authenticated("/services/toolsets/inst/user")
        assert result is False

    async def test_api_token_type(self):
        cs = AsyncMock()
        cs.get_config = AsyncMock(return_value={
            "isAuthenticated": True,
            "auth": {"type": "API_TOKEN"},
            "credentials": {"refresh_token": "rt_123"}
        })
        svc = _make_service(cs)
        result = await svc._is_toolset_authenticated("/services/toolsets/inst/user")
        assert result is False

    async def test_unknown_auth_type(self):
        cs = AsyncMock()
        cs.get_config = AsyncMock(return_value={
            "isAuthenticated": True,
            "auth": {"type": "SAML"},
            "credentials": {"refresh_token": "rt_123"}
        })
        svc = _make_service(cs)
        result = await svc._is_toolset_authenticated("/services/toolsets/inst/user")
        assert result is False

    async def test_no_credentials(self):
        cs = AsyncMock()
        cs.get_config = AsyncMock(return_value={
            "isAuthenticated": True,
            "auth": {"type": "OAUTH"},
            "credentials": None
        })
        svc = _make_service(cs)
        result = await svc._is_toolset_authenticated("/services/toolsets/inst/user")
        assert result is False

    async def test_no_refresh_token(self):
        cs = AsyncMock()
        cs.get_config = AsyncMock(return_value={
            "isAuthenticated": True,
            "auth": {"type": "OAUTH"},
            "credentials": {"access_token": "at_123"}
        })
        svc = _make_service(cs)
        result = await svc._is_toolset_authenticated("/services/toolsets/inst/user")
        assert result is False

    async def test_authenticated_oauth(self):
        cs = AsyncMock()
        cs.get_config = AsyncMock(return_value={
            "isAuthenticated": True,
            "auth": {"type": "OAUTH"},
            "credentials": {"refresh_token": "rt_123"}
        })
        svc = _make_service(cs)
        result = await svc._is_toolset_authenticated("/services/toolsets/inst/user")
        assert result is True

    async def test_authenticated_no_auth_type_with_refresh_token(self):
        """OAuth without explicit auth.type set (legacy)."""
        cs = AsyncMock()
        cs.get_config = AsyncMock(return_value={
            "isAuthenticated": True,
            "auth": {},
            "credentials": {"refresh_token": "rt_123"}
        })
        svc = _make_service(cs)
        result = await svc._is_toolset_authenticated("/services/toolsets/inst/user")
        assert result is True

    async def test_exception_returns_false(self):
        cs = AsyncMock()
        cs.get_config = AsyncMock(side_effect=Exception("etcd error"))
        svc = _make_service(cs)
        result = await svc._is_toolset_authenticated("/services/toolsets/inst/user")
        assert result is False

    async def test_no_auth_key_in_config(self):
        """Config without 'auth' key at all."""
        cs = AsyncMock()
        cs.get_config = AsyncMock(return_value={
            "isAuthenticated": True,
            "credentials": {"refresh_token": "rt_123"}
        })
        svc = _make_service(cs)
        result = await svc._is_toolset_authenticated("/services/toolsets/inst/user")
        assert result is True


# ---------------------------------------------------------------------------
# _refresh_all_tokens / _refresh_all_tokens_internal
# ---------------------------------------------------------------------------


class TestRefreshAllTokens:
    async def test_refresh_all_tokens_calls_internal(self):
        svc = _make_service()
        svc._refresh_all_tokens_internal = AsyncMock()
        await svc._refresh_all_tokens()
        svc._refresh_all_tokens_internal.assert_awaited_once()

    async def test_no_keys_found(self):
        cs = AsyncMock()
        cs.list_keys_in_directory = AsyncMock(return_value=[])
        svc = _make_service(cs)
        svc._refresh_toolset_token = AsyncMock()
        await svc._refresh_all_tokens_internal()
        svc._refresh_toolset_token.assert_not_awaited()

    async def test_list_keys_exception(self):
        cs = AsyncMock()
        cs.list_keys_in_directory = AsyncMock(side_effect=Exception("etcd down"))
        svc = _make_service(cs)
        # Should not raise
        await svc._refresh_all_tokens_internal()

    async def test_invalid_path_too_short(self):
        cs = AsyncMock()
        cs.list_keys_in_directory = AsyncMock(return_value=[
            "/services/toolsets/short"
        ])
        svc = _make_service(cs)
        svc._refresh_toolset_token = AsyncMock()
        await svc._refresh_all_tokens_internal()
        svc._refresh_toolset_token.assert_not_awaited()

    async def test_legacy_format_skipped(self):
        """Non-UUID at path_parts[2] treated as legacy."""
        cs = AsyncMock()
        cs.list_keys_in_directory = AsyncMock(return_value=[
            "/services/toolsets/slack/user123"
        ])
        svc = _make_service(cs)
        svc._refresh_toolset_token = AsyncMock()
        await svc._refresh_all_tokens_internal()
        svc._refresh_toolset_token.assert_not_awaited()

    async def test_no_config_found(self):
        cs = AsyncMock()
        cs.list_keys_in_directory = AsyncMock(return_value=[
            "/services/toolsets/107344f6-66cb-46f9-89f1-22d0bdae99cb/user1"
        ])
        cs.get_config = AsyncMock(return_value=None)
        svc = _make_service(cs)
        svc._refresh_toolset_token = AsyncMock()
        await svc._refresh_all_tokens_internal()
        svc._refresh_toolset_token.assert_not_awaited()

    async def test_no_toolset_type_in_config(self):
        cs = AsyncMock()
        cs.list_keys_in_directory = AsyncMock(return_value=[
            "/services/toolsets/107344f6-66cb-46f9-89f1-22d0bdae99cb/user1"
        ])
        cs.get_config = AsyncMock(return_value={"isAuthenticated": True})
        svc = _make_service(cs)
        svc._refresh_toolset_token = AsyncMock()
        svc._is_toolset_authenticated = AsyncMock(return_value=True)
        await svc._refresh_all_tokens_internal()
        svc._refresh_toolset_token.assert_not_awaited()

    async def test_not_authenticated_skipped(self):
        cs = AsyncMock()
        cs.list_keys_in_directory = AsyncMock(return_value=[
            "/services/toolsets/107344f6-66cb-46f9-89f1-22d0bdae99cb/user1"
        ])
        cs.get_config = AsyncMock(return_value={
            "isAuthenticated": False,
            "toolsetType": "googledrive"
        })
        svc = _make_service(cs)
        svc._is_toolset_authenticated = AsyncMock(return_value=False)
        svc._refresh_toolset_token = AsyncMock()
        await svc._refresh_all_tokens_internal()
        svc._refresh_toolset_token.assert_not_awaited()

    async def test_authenticated_toolset_processed(self):
        cs = AsyncMock()
        path = "/services/toolsets/107344f6-66cb-46f9-89f1-22d0bdae99cb/user1"
        cs.list_keys_in_directory = AsyncMock(return_value=[path])
        cs.get_config = AsyncMock(return_value={
            "isAuthenticated": True,
            "toolsetType": "googledrive",
            "auth": {"type": "OAUTH"},
            "credentials": {"refresh_token": "rt_123"}
        })
        svc = _make_service(cs)
        svc._is_toolset_authenticated = AsyncMock(return_value=True)
        svc._refresh_toolset_token = AsyncMock()
        await svc._refresh_all_tokens_internal()
        svc._refresh_toolset_token.assert_awaited_once_with(path, "googledrive")

    async def test_duplicate_keys_skipped(self):
        cs = AsyncMock()
        path = "/services/toolsets/107344f6-66cb-46f9-89f1-22d0bdae99cb/user1"
        cs.list_keys_in_directory = AsyncMock(return_value=[path, path])
        cs.get_config = AsyncMock(return_value={
            "isAuthenticated": True,
            "toolsetType": "googledrive",
            "auth": {"type": "OAUTH"},
            "credentials": {"refresh_token": "rt_123"}
        })
        svc = _make_service(cs)
        svc._is_toolset_authenticated = AsyncMock(return_value=True)
        svc._refresh_toolset_token = AsyncMock()
        await svc._refresh_all_tokens_internal()
        svc._refresh_toolset_token.assert_awaited_once()

    async def test_refresh_toolset_exception_does_not_abort(self):
        """Exception during _refresh_toolset_token doesn't stop other toolsets."""
        cs = AsyncMock()
        path1 = "/services/toolsets/107344f6-66cb-46f9-89f1-22d0bdae99cb/user1"
        path2 = "/services/toolsets/207344f6-66cb-46f9-89f1-22d0bdae99cb/user2"
        cs.list_keys_in_directory = AsyncMock(return_value=[path1, path2])

        def config_side_effect(path, **kwargs):
            return {
                "isAuthenticated": True,
                "toolsetType": "googledrive",
                "auth": {"type": "OAUTH"},
                "credentials": {"refresh_token": "rt_123"}
            }

        cs.get_config = AsyncMock(side_effect=config_side_effect)
        svc = _make_service(cs)
        svc._is_toolset_authenticated = AsyncMock(return_value=True)
        call_count = 0

        async def refresh_side_effect(path, toolset_type):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise Exception("Refresh failed")

        svc._refresh_toolset_token = AsyncMock(side_effect=refresh_side_effect)
        await svc._refresh_all_tokens_internal()
        assert svc._refresh_toolset_token.await_count == 2

    async def test_per_key_exception_continues(self):
        """Exception during processing one key doesn't stop iteration."""
        cs = AsyncMock()
        path1 = "/services/toolsets/107344f6-66cb-46f9-89f1-22d0bdae99cb/user1"
        path2 = "/services/toolsets/207344f6-66cb-46f9-89f1-22d0bdae99cb/user2"
        cs.list_keys_in_directory = AsyncMock(return_value=[path1, path2])

        call_count = 0

        async def get_config_side_effect(path, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise Exception("etcd read error")
            return {
                "isAuthenticated": True,
                "toolsetType": "googledrive",
                "auth": {"type": "OAUTH"},
                "credentials": {"refresh_token": "rt_123"}
            }

        cs.get_config = AsyncMock(side_effect=get_config_side_effect)
        svc = _make_service(cs)
        svc._is_toolset_authenticated = AsyncMock(return_value=True)
        svc._refresh_toolset_token = AsyncMock()
        await svc._refresh_all_tokens_internal()
        # Second path should still be processed
        svc._refresh_toolset_token.assert_awaited_once()


# ---------------------------------------------------------------------------
# _load_admin_oauth_config
# ---------------------------------------------------------------------------


class TestLoadAdminOauthConfig:
    async def test_no_user_config(self):
        cs = AsyncMock()
        cs.get_config = AsyncMock(return_value=None)
        svc = _make_service(cs)
        result = await svc._load_admin_oauth_config("/services/toolsets/inst/user", "googledrive")
        assert result is None

    async def test_no_oauth_config_id(self):
        cs = AsyncMock()
        cs.get_config = AsyncMock(return_value={"orgId": "org1"})
        svc = _make_service(cs)
        result = await svc._load_admin_oauth_config("/services/toolsets/inst/user", "googledrive")
        assert result is None

    async def test_no_org_id(self):
        cs = AsyncMock()
        cs.get_config = AsyncMock(return_value={"oauthConfigId": "cfg1"})
        svc = _make_service(cs)
        result = await svc._load_admin_oauth_config("/services/toolsets/inst/user", "googledrive")
        assert result is None

    async def test_configs_not_list(self):
        cs = AsyncMock()
        call_count = 0

        async def get_config_side_effect(path, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return {"oauthConfigId": "cfg1", "orgId": "org1"}
            return "not a list"

        cs.get_config = AsyncMock(side_effect=get_config_side_effect)
        svc = _make_service(cs)
        result = await svc._load_admin_oauth_config("/services/toolsets/inst/user", "googledrive")
        assert result is None

    async def test_matching_config_found(self):
        cs = AsyncMock()
        admin_cfg = {"_id": "cfg1", "orgId": "org1", "clientId": "cid"}
        call_count = 0

        async def get_config_side_effect(path, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return {"oauthConfigId": "cfg1", "orgId": "org1"}
            return [admin_cfg, {"_id": "cfg2", "orgId": "org2"}]

        cs.get_config = AsyncMock(side_effect=get_config_side_effect)
        svc = _make_service(cs)
        result = await svc._load_admin_oauth_config("/services/toolsets/inst/user", "googledrive")
        assert result == admin_cfg

    async def test_no_matching_config(self):
        cs = AsyncMock()
        call_count = 0

        async def get_config_side_effect(path, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return {"oauthConfigId": "cfg999", "orgId": "org1"}
            return [{"_id": "cfg1", "orgId": "org1"}]

        cs.get_config = AsyncMock(side_effect=get_config_side_effect)
        svc = _make_service(cs)
        result = await svc._load_admin_oauth_config("/services/toolsets/inst/user", "googledrive")
        assert result is None

    async def test_exception_returns_none(self):
        cs = AsyncMock()
        cs.get_config = AsyncMock(side_effect=Exception("etcd error"))
        svc = _make_service(cs)
        result = await svc._load_admin_oauth_config("/services/toolsets/inst/user", "googledrive")
        assert result is None


# ---------------------------------------------------------------------------
# _get_toolset_oauth_config_from_registry
# ---------------------------------------------------------------------------


class TestGetToolsetOauthConfigFromRegistry:
    def test_no_registry(self):
        svc = _make_service()
        with patch(
            "app.connectors.core.base.token_service.toolset_token_refresh_service.ToolsetTokenRefreshService._get_toolset_oauth_config_from_registry"
        ) as m:
            # Call the real method, but mock internal imports
            m.side_effect = lambda ts: None
        # Actually test the method with mocked import
        with patch(
            "app.agents.registry.toolset_registry.get_toolset_registry",
            return_value=None
        ):
            result = svc._get_toolset_oauth_config_from_registry("googledrive")
            assert result is None

    def test_no_metadata(self):
        svc = _make_service()
        mock_registry = MagicMock()
        mock_registry.get_toolset_metadata.return_value = None
        with patch(
            "app.agents.registry.toolset_registry.get_toolset_registry",
            return_value=mock_registry
        ):
            result = svc._get_toolset_oauth_config_from_registry("googledrive")
            assert result is None

    def test_no_oauth_in_config(self):
        svc = _make_service()
        mock_registry = MagicMock()
        mock_registry.get_toolset_metadata.return_value = {
            "config": {"_oauth_configs": {}}
        }
        with patch(
            "app.agents.registry.toolset_registry.get_toolset_registry",
            return_value=mock_registry
        ):
            result = svc._get_toolset_oauth_config_from_registry("googledrive")
            assert result is None

    def test_oauth_config_without_required_attrs(self):
        svc = _make_service()
        mock_registry = MagicMock()
        oauth_cfg = MagicMock(spec=[])  # No attributes at all
        mock_registry.get_toolset_metadata.return_value = {
            "config": {"_oauth_configs": {"OAUTH": oauth_cfg}}
        }
        with patch(
            "app.agents.registry.toolset_registry.get_toolset_registry",
            return_value=mock_registry
        ):
            result = svc._get_toolset_oauth_config_from_registry("googledrive")
            assert result is None

    def test_valid_oauth_config(self):
        svc = _make_service()
        mock_registry = MagicMock()
        oauth_cfg = MagicMock()
        oauth_cfg.authorize_url = "https://auth.example.com"
        oauth_cfg.redirect_uri = "/callback"
        mock_registry.get_toolset_metadata.return_value = {
            "config": {"_oauth_configs": {"OAUTH": oauth_cfg}}
        }
        with patch(
            "app.agents.registry.toolset_registry.get_toolset_registry",
            return_value=mock_registry
        ):
            result = svc._get_toolset_oauth_config_from_registry("googledrive")
            assert result is oauth_cfg

    def test_exception_returns_none(self):
        svc = _make_service()
        with patch(
            "app.agents.registry.toolset_registry.get_toolset_registry",
            side_effect=Exception("registry error")
        ):
            result = svc._get_toolset_oauth_config_from_registry("googledrive")
            assert result is None

    def test_no_config_key_in_metadata(self):
        svc = _make_service()
        mock_registry = MagicMock()
        mock_registry.get_toolset_metadata.return_value = {"name": "test"}
        with patch(
            "app.agents.registry.toolset_registry.get_toolset_registry",
            return_value=mock_registry
        ):
            result = svc._get_toolset_oauth_config_from_registry("googledrive")
            assert result is None


# ---------------------------------------------------------------------------
# _enrich_from_toolset_registry
# ---------------------------------------------------------------------------


class TestEnrichFromToolsetRegistry:
    def test_already_has_fields_skips(self):
        svc = _make_service()
        config = {"tokenAccessType": "offline", "additionalParams": {}}
        svc._get_toolset_oauth_config_from_registry = MagicMock(return_value=None)
        svc._enrich_from_toolset_registry(config, "googledrive")
        svc._get_toolset_oauth_config_from_registry.assert_not_called()

    def test_enriches_token_access_type(self):
        svc = _make_service()
        config = {}
        oauth_cfg = MagicMock()
        oauth_cfg.token_access_type = "offline"
        oauth_cfg.additional_params = {"prompt": "consent"}
        oauth_cfg.scope_parameter_name = "user_scope"
        oauth_cfg.token_response_path = "authed_user"
        svc._get_toolset_oauth_config_from_registry = MagicMock(return_value=oauth_cfg)
        svc._enrich_from_toolset_registry(config, "googledrive")
        assert config["tokenAccessType"] == "offline"
        assert config["additionalParams"] == {"prompt": "consent"}
        assert config["scopeParameterName"] == "user_scope"
        assert config["tokenResponsePath"] == "authed_user"

    def test_does_not_enrich_scope_parameter_name_if_default(self):
        svc = _make_service()
        config = {}
        oauth_cfg = MagicMock()
        oauth_cfg.token_access_type = None
        oauth_cfg.additional_params = None
        oauth_cfg.scope_parameter_name = "scope"  # Default, should not be added
        oauth_cfg.token_response_path = None
        svc._get_toolset_oauth_config_from_registry = MagicMock(return_value=oauth_cfg)
        svc._enrich_from_toolset_registry(config, "googledrive")
        assert "scopeParameterName" not in config
        assert "tokenAccessType" not in config
        assert "additionalParams" not in config
        assert "tokenResponsePath" not in config

    def test_no_registry_config(self):
        svc = _make_service()
        config = {}
        svc._get_toolset_oauth_config_from_registry = MagicMock(return_value=None)
        svc._enrich_from_toolset_registry(config, "googledrive")
        assert config == {}

    def test_exception_does_not_raise(self):
        svc = _make_service()
        config = {}
        svc._get_toolset_oauth_config_from_registry = MagicMock(side_effect=Exception("err"))
        # Should not raise
        svc._enrich_from_toolset_registry(config, "googledrive")

    def test_does_not_overwrite_existing_fields(self):
        """If only one of the two required fields is present, enrichment still runs."""
        svc = _make_service()
        config = {"tokenAccessType": "existing_value"}
        oauth_cfg = MagicMock()
        oauth_cfg.token_access_type = "new_value"
        oauth_cfg.additional_params = {"new": True}
        oauth_cfg.scope_parameter_name = "scope"
        oauth_cfg.token_response_path = None
        svc._get_toolset_oauth_config_from_registry = MagicMock(return_value=oauth_cfg)
        svc._enrich_from_toolset_registry(config, "googledrive")
        # tokenAccessType already existed, should not be overwritten since
        # the early return checks BOTH fields. Since additionalParams is missing,
        # enrichment runs and adds it, but tokenAccessType check passes (already present)
        assert config["tokenAccessType"] == "existing_value"
        assert config["additionalParams"] == {"new": True}


# ---------------------------------------------------------------------------
# _build_complete_oauth_config
# ---------------------------------------------------------------------------


class TestBuildCompleteOauthConfig:
    async def test_success_with_centralized_creds(self):
        cs = AsyncMock()
        cs.get_config = AsyncMock(return_value={
            "oauthConfigId": "cfg1",
            "orgId": "org1",
            "auth": {"type": "OAUTH"}
        })
        svc = _make_service(cs)
        svc._get_toolset_oauth_config_from_registry = MagicMock(return_value=None)
        svc._enrich_from_toolset_registry = MagicMock()

        mock_creds = {
            "clientId": "cid",
            "clientSecret": "csecret",
            "authorizeUrl": "https://auth.example.com",
            "tokenUrl": "https://token.example.com",
            "redirectUri": "https://redirect.example.com",
            "scopes": ["scope1"],
        }

        with patch(
            "app.api.routes.toolsets.get_oauth_credentials_for_toolset",
            new_callable=AsyncMock,
            return_value=mock_creds
        ):
            result = await svc._build_complete_oauth_config(
                "/services/toolsets/inst/user",
                "googledrive",
                {"type": "OAUTH"}
            )

        assert result["clientId"] == "cid"
        assert result["clientSecret"] == "csecret"
        assert result["authorizeUrl"] == "https://auth.example.com"
        assert result["tokenUrl"] == "https://token.example.com"

    async def test_fallback_to_legacy_creds(self):
        cs = AsyncMock()
        cs.get_config = AsyncMock(side_effect=Exception("etcd error"))
        svc = _make_service(cs)
        svc._get_toolset_oauth_config_from_registry = MagicMock(return_value=None)
        svc._enrich_from_toolset_registry = MagicMock()

        auth_config = {
            "type": "OAUTH",
            "clientId": "legacy_cid",
            "clientSecret": "legacy_csecret",
            "authorizeUrl": "https://auth.legacy.com",
            "tokenUrl": "https://token.legacy.com",
        }
        result = await svc._build_complete_oauth_config(
            "/services/toolsets/inst/user",
            "googledrive",
            auth_config
        )
        assert result["clientId"] == "legacy_cid"
        assert result["clientSecret"] == "legacy_csecret"

    async def test_fallback_no_legacy_creds_raises(self):
        cs = AsyncMock()
        cs.get_config = AsyncMock(side_effect=Exception("etcd error"))
        svc = _make_service(cs)

        with pytest.raises(ValueError, match="No OAuth credentials found"):
            await svc._build_complete_oauth_config(
                "/services/toolsets/inst/user",
                "googledrive",
                {"type": "OAUTH"}
            )

    async def test_incomplete_centralized_creds_raises(self):
        """Incomplete centralized creds fall back to legacy, which also fails."""
        cs = AsyncMock()
        cs.get_config = AsyncMock(return_value={
            "oauthConfigId": "cfg1",
            "orgId": "org1",
        })
        svc = _make_service(cs)
        svc._get_toolset_oauth_config_from_registry = MagicMock(return_value=None)
        svc._enrich_from_toolset_registry = MagicMock()

        mock_creds = {"clientId": "cid"}  # Missing clientSecret

        with patch(
            "app.api.routes.toolsets.get_oauth_credentials_for_toolset",
            new_callable=AsyncMock,
            return_value=mock_creds
        ):
            with pytest.raises(ValueError, match="No OAuth credentials found"):
                await svc._build_complete_oauth_config(
                    "/services/toolsets/inst/user",
                    "googledrive",
                    {"type": "OAUTH"}
                )

    async def test_null_config_from_etcd_raises(self):
        cs = AsyncMock()
        cs.get_config = AsyncMock(return_value=None)
        svc = _make_service(cs)

        # When full_user_config is None, ValueError is raised
        # which falls through to legacy fallback
        with pytest.raises(ValueError, match="No OAuth credentials found"):
            await svc._build_complete_oauth_config(
                "/services/toolsets/inst/user",
                "googledrive",
                {"type": "OAUTH"}
            )

    async def test_urls_from_registry_fallback(self):
        """When auth_config has no URLs, registry provides them."""
        cs = AsyncMock()
        cs.get_config = AsyncMock(return_value={
            "oauthConfigId": "cfg1",
            "orgId": "org1",
        })
        svc = _make_service(cs)

        oauth_cfg_obj = MagicMock()
        oauth_cfg_obj.authorize_url = "https://registry-auth.example.com"
        oauth_cfg_obj.token_url = "https://registry-token.example.com"
        oauth_cfg_obj.redirect_uri = "callback/path"
        oauth_cfg_obj.token_access_type = None
        oauth_cfg_obj.additional_params = None
        oauth_cfg_obj.scope_parameter_name = "scope"
        oauth_cfg_obj.token_response_path = None
        oauth_cfg_obj.scopes = MagicMock()
        oauth_cfg_obj.scopes.get_scopes_for_type = MagicMock(return_value=["scope1"])

        svc._get_toolset_oauth_config_from_registry = MagicMock(return_value=oauth_cfg_obj)
        svc._enrich_from_toolset_registry = MagicMock()

        mock_creds = {
            "clientId": "cid",
            "clientSecret": "csecret",
        }
        with patch(
            "app.api.routes.toolsets.get_oauth_credentials_for_toolset",
            new_callable=AsyncMock,
            return_value=mock_creds
        ):
            result = await svc._build_complete_oauth_config(
                "/services/toolsets/inst/user",
                "googledrive",
                {}
            )

        assert result["authorizeUrl"] == "https://registry-auth.example.com"
        assert result["tokenUrl"] == "https://registry-token.example.com"

    async def test_redirect_uri_from_registry_with_endpoints(self):
        """When redirectUri comes from registry and endpoints are available."""
        cs = AsyncMock()
        call_count = 0

        async def get_config_side_effect(path, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                # First call: full_user_config
                return {"oauthConfigId": "cfg1", "orgId": "org1"}
            elif "/services/endpoints" in str(path):
                return {
                    "frontend": {"publicEndpoint": "https://app.example.com"}
                }
            return None

        cs.get_config = AsyncMock(side_effect=get_config_side_effect)
        svc = _make_service(cs)

        oauth_cfg_obj = MagicMock()
        oauth_cfg_obj.authorize_url = "https://auth.example.com"
        oauth_cfg_obj.token_url = "https://token.example.com"
        oauth_cfg_obj.redirect_uri = "oauth/callback"
        oauth_cfg_obj.token_access_type = None
        oauth_cfg_obj.additional_params = None
        oauth_cfg_obj.scope_parameter_name = "scope"
        oauth_cfg_obj.token_response_path = None
        oauth_cfg_obj.scopes = MagicMock()
        oauth_cfg_obj.scopes.get_scopes_for_type = MagicMock(return_value=[])

        svc._get_toolset_oauth_config_from_registry = MagicMock(return_value=oauth_cfg_obj)
        svc._enrich_from_toolset_registry = MagicMock()

        mock_creds = {"clientId": "cid", "clientSecret": "csecret"}
        with patch(
            "app.api.routes.toolsets.get_oauth_credentials_for_toolset",
            new_callable=AsyncMock,
            return_value=mock_creds
        ):
            result = await svc._build_complete_oauth_config(
                "/services/toolsets/inst/user",
                "googledrive",
                {}
            )

        assert "oauth/callback" in result["redirectUri"]

    async def test_empty_urls_when_no_source(self):
        """When neither auth_config nor registry has URLs, empty strings."""
        cs = AsyncMock()
        cs.get_config = AsyncMock(return_value={
            "oauthConfigId": "cfg1",
            "orgId": "org1",
        })
        svc = _make_service(cs)
        svc._get_toolset_oauth_config_from_registry = MagicMock(return_value=None)
        svc._enrich_from_toolset_registry = MagicMock()

        mock_creds = {"clientId": "cid", "clientSecret": "csecret"}
        with patch(
            "app.api.routes.toolsets.get_oauth_credentials_for_toolset",
            new_callable=AsyncMock,
            return_value=mock_creds
        ):
            result = await svc._build_complete_oauth_config(
                "/services/toolsets/inst/user",
                "googledrive",
                {}
            )

        assert result["authorizeUrl"] == ""
        assert result["tokenUrl"] == ""
        assert result["redirectUri"] == ""
        assert result["scopes"] == []

    async def test_provider_specific_fields(self):
        """tenantId, domain, etc. from auth_config are forwarded."""
        cs = AsyncMock()
        cs.get_config = AsyncMock(return_value={
            "oauthConfigId": "cfg1",
            "orgId": "org1",
        })
        svc = _make_service(cs)
        svc._get_toolset_oauth_config_from_registry = MagicMock(return_value=None)
        svc._enrich_from_toolset_registry = MagicMock()

        mock_creds = {
            "clientId": "cid",
            "clientSecret": "csecret",
            "tenantId": "tenant1",
            "domain": "example.slack.com",
        }
        with patch(
            "app.api.routes.toolsets.get_oauth_credentials_for_toolset",
            new_callable=AsyncMock,
            return_value=mock_creds
        ):
            result = await svc._build_complete_oauth_config(
                "/services/toolsets/inst/user",
                "slack",
                {}
            )

        assert result["tenantId"] == "tenant1"
        assert result["domain"] == "example.slack.com"

    async def test_optional_fields_from_auth_config(self):
        """tokenAccessType, additionalParams, etc. from auth_config."""
        cs = AsyncMock()
        cs.get_config = AsyncMock(return_value={
            "oauthConfigId": "cfg1",
            "orgId": "org1",
        })
        svc = _make_service(cs)
        svc._get_toolset_oauth_config_from_registry = MagicMock(return_value=None)
        svc._enrich_from_toolset_registry = MagicMock()

        mock_creds = {"clientId": "cid", "clientSecret": "csecret"}
        auth_config = {
            "tokenAccessType": "offline",
            "additionalParams": {"prompt": "consent"},
            "scopeParameterName": "user_scope",
            "tokenResponsePath": "authed_user",
            "scopes": ["read", "write"],
        }
        with patch(
            "app.api.routes.toolsets.get_oauth_credentials_for_toolset",
            new_callable=AsyncMock,
            return_value=mock_creds
        ):
            result = await svc._build_complete_oauth_config(
                "/services/toolsets/inst/user",
                "googledrive",
                auth_config
            )

        assert result["tokenAccessType"] == "offline"
        assert result["additionalParams"] == {"prompt": "consent"}
        assert result["scopeParameterName"] == "user_scope"
        assert result["tokenResponsePath"] == "authed_user"
        assert result["scopes"] == ["read", "write"]

    async def test_optional_fields_from_registry_fallback(self):
        """When auth_config lacks optional fields, registry provides them."""
        cs = AsyncMock()
        cs.get_config = AsyncMock(return_value={
            "oauthConfigId": "cfg1",
            "orgId": "org1",
        })
        svc = _make_service(cs)

        oauth_cfg_obj = MagicMock()
        oauth_cfg_obj.authorize_url = "https://auth.example.com"
        oauth_cfg_obj.token_url = "https://token.example.com"
        oauth_cfg_obj.redirect_uri = "/callback"
        oauth_cfg_obj.token_access_type = "offline"
        oauth_cfg_obj.additional_params = {"prompt": "consent"}
        oauth_cfg_obj.scope_parameter_name = "user_scope"
        oauth_cfg_obj.token_response_path = "authed_user"
        oauth_cfg_obj.scopes = MagicMock()
        oauth_cfg_obj.scopes.get_scopes_for_type = MagicMock(return_value=["scope1"])

        svc._get_toolset_oauth_config_from_registry = MagicMock(return_value=oauth_cfg_obj)
        svc._enrich_from_toolset_registry = MagicMock()

        mock_creds = {"clientId": "cid", "clientSecret": "csecret"}
        with patch(
            "app.api.routes.toolsets.get_oauth_credentials_for_toolset",
            new_callable=AsyncMock,
            return_value=mock_creds
        ):
            result = await svc._build_complete_oauth_config(
                "/services/toolsets/inst/user",
                "googledrive",
                {}
            )

        assert result["tokenAccessType"] == "offline"
        assert result["additionalParams"] == {"prompt": "consent"}
        assert result["scopeParameterName"] == "user_scope"
        assert result["tokenResponsePath"] == "authed_user"

    async def test_scopes_from_registry(self):
        """When auth_config has no scopes, registry provides them."""
        cs = AsyncMock()
        cs.get_config = AsyncMock(return_value={
            "oauthConfigId": "cfg1",
            "orgId": "org1",
        })
        svc = _make_service(cs)

        oauth_cfg_obj = MagicMock()
        oauth_cfg_obj.authorize_url = ""
        oauth_cfg_obj.token_url = ""
        oauth_cfg_obj.token_access_type = None
        oauth_cfg_obj.additional_params = None
        oauth_cfg_obj.scope_parameter_name = "scope"
        oauth_cfg_obj.token_response_path = None
        oauth_cfg_obj.scopes = MagicMock()
        oauth_cfg_obj.scopes.get_scopes_for_type = MagicMock(return_value=["drive.read", "drive.write"])

        svc._get_toolset_oauth_config_from_registry = MagicMock(return_value=oauth_cfg_obj)
        svc._enrich_from_toolset_registry = MagicMock()

        mock_creds = {"clientId": "cid", "clientSecret": "csecret"}
        with patch(
            "app.api.routes.toolsets.get_oauth_credentials_for_toolset",
            new_callable=AsyncMock,
            return_value=mock_creds
        ):
            result = await svc._build_complete_oauth_config(
                "/services/toolsets/inst/user",
                "googledrive",
                {}
            )

        assert result["scopes"] == ["drive.read", "drive.write"]


# ---------------------------------------------------------------------------
# _perform_token_refresh
# ---------------------------------------------------------------------------


class TestPerformTokenRefresh:
    async def test_no_config_raises(self):
        cs = AsyncMock()
        cs.get_config = AsyncMock(return_value=None)
        svc = _make_service(cs)

        with pytest.raises(ValueError, match="No config found"):
            await svc._perform_token_refresh("/services/toolsets/inst/user", "googledrive", "rt_123")

    async def test_non_oauth_type_raises(self):
        cs = AsyncMock()
        cs.get_config = AsyncMock(return_value={
            "auth": {"type": "API_TOKEN"}
        })
        svc = _make_service(cs)

        with pytest.raises(ValueError, match="not OAuth"):
            await svc._perform_token_refresh("/services/toolsets/inst/user", "googledrive", "rt_123")

    async def test_missing_token_url_raises(self):
        cs = AsyncMock()
        cs.get_config = AsyncMock(return_value={
            "auth": {"type": "OAUTH"}
        })
        svc = _make_service(cs)
        svc._build_complete_oauth_config = AsyncMock(return_value={
            "clientId": "cid",
            "clientSecret": "csecret",
            "tokenUrl": "",
        })

        with pytest.raises(ValueError, match="Missing tokenUrl"):
            await svc._perform_token_refresh("/services/toolsets/inst/user", "googledrive", "rt_123")

    async def test_missing_client_id_raises(self):
        cs = AsyncMock()
        cs.get_config = AsyncMock(return_value={
            "auth": {"type": "OAUTH"}
        })
        svc = _make_service(cs)
        svc._build_complete_oauth_config = AsyncMock(return_value={
            "clientId": "",
            "clientSecret": "csecret",
            "tokenUrl": "https://token.example.com",
        })

        with pytest.raises(ValueError, match="Missing clientId"):
            await svc._perform_token_refresh("/services/toolsets/inst/user", "googledrive", "rt_123")

    async def test_missing_client_secret_raises(self):
        cs = AsyncMock()
        cs.get_config = AsyncMock(return_value={
            "auth": {"type": "OAUTH"}
        })
        svc = _make_service(cs)
        svc._build_complete_oauth_config = AsyncMock(return_value={
            "clientId": "cid",
            "clientSecret": "",
            "tokenUrl": "https://token.example.com",
        })

        with pytest.raises(ValueError, match="Missing clientSecret"):
            await svc._perform_token_refresh("/services/toolsets/inst/user", "googledrive", "rt_123")

    async def test_successful_refresh(self):
        cs = AsyncMock()
        config = {
            "auth": {"type": "OAUTH"},
            "credentials": {"refresh_token": "rt_old"},
        }
        cs.get_config = AsyncMock(return_value=config)
        cs.set_config = AsyncMock(return_value=True)
        svc = _make_service(cs)

        new_token = _make_token(access_token="at_new", refresh_token="rt_new")
        svc._build_complete_oauth_config = AsyncMock(return_value={
            "clientId": "cid",
            "clientSecret": "csecret",
            "tokenUrl": "https://token.example.com",
            "authorizeUrl": "https://auth.example.com",
            "redirectUri": "https://redirect.example.com",
            "scopes": [],
        })

        mock_provider = AsyncMock()
        mock_provider.refresh_access_token = AsyncMock(return_value=new_token)
        mock_provider.close = AsyncMock()

        with patch(
            "app.connectors.core.base.token_service.toolset_token_refresh_service.get_oauth_config"
        ), patch(
            "app.connectors.core.base.token_service.oauth_service.OAuthProvider",
            return_value=mock_provider
        ):
            result = await svc._perform_token_refresh("/services/toolsets/inst/user", "googledrive", "rt_123")

        assert result is new_token
        cs.set_config.assert_awaited()
        mock_provider.close.assert_awaited_once()

    async def test_refresh_retries_on_verification_failure(self):
        cs = AsyncMock()
        config = {
            "auth": {"type": "OAUTH"},
            "credentials": {"refresh_token": "rt_old"},
        }
        cs.get_config = AsyncMock(return_value=config)
        # First set_config returns False, second returns True
        cs.set_config = AsyncMock(side_effect=[False, True])
        svc = _make_service(cs)

        new_token = _make_token()
        svc._build_complete_oauth_config = AsyncMock(return_value={
            "clientId": "cid",
            "clientSecret": "csecret",
            "tokenUrl": "https://token.example.com",
            "authorizeUrl": "",
            "redirectUri": "",
            "scopes": [],
        })

        mock_provider = AsyncMock()
        mock_provider.refresh_access_token = AsyncMock(return_value=new_token)
        mock_provider.close = AsyncMock()

        with patch(
            "app.connectors.core.base.token_service.toolset_token_refresh_service.get_oauth_config"
        ), patch(
            "app.connectors.core.base.token_service.oauth_service.OAuthProvider",
            return_value=mock_provider
        ), patch("asyncio.sleep", new_callable=AsyncMock):
            result = await svc._perform_token_refresh("/services/toolsets/inst/user", "googledrive", "rt_123")

        assert result is new_token
        assert cs.set_config.await_count == 2

    async def test_refresh_all_retries_exhausted_raises(self):
        cs = AsyncMock()
        config = {
            "auth": {"type": "OAUTH"},
            "credentials": {"refresh_token": "rt_old"},
        }
        cs.get_config = AsyncMock(return_value=config)
        cs.set_config = AsyncMock(return_value=False)  # Always fails
        svc = _make_service(cs)

        new_token = _make_token()
        svc._build_complete_oauth_config = AsyncMock(return_value={
            "clientId": "cid",
            "clientSecret": "csecret",
            "tokenUrl": "https://token.example.com",
            "authorizeUrl": "",
            "redirectUri": "",
            "scopes": [],
        })

        mock_provider = AsyncMock()
        mock_provider.refresh_access_token = AsyncMock(return_value=new_token)
        mock_provider.close = AsyncMock()

        with patch(
            "app.connectors.core.base.token_service.toolset_token_refresh_service.get_oauth_config"
        ), patch(
            "app.connectors.core.base.token_service.oauth_service.OAuthProvider",
            return_value=mock_provider
        ), patch("asyncio.sleep", new_callable=AsyncMock):
            with pytest.raises(Exception, match="Failed to save refreshed credentials"):
                await svc._perform_token_refresh("/services/toolsets/inst/user", "googledrive", "rt_123")

        mock_provider.close.assert_awaited_once()

    async def test_refresh_set_config_exception_retries(self):
        cs = AsyncMock()
        config = {
            "auth": {"type": "OAUTH"},
            "credentials": {"refresh_token": "rt_old"},
        }
        cs.get_config = AsyncMock(return_value=config)
        cs.set_config = AsyncMock(side_effect=[Exception("etcd write error"), True])
        svc = _make_service(cs)

        new_token = _make_token()
        svc._build_complete_oauth_config = AsyncMock(return_value={
            "clientId": "cid",
            "clientSecret": "csecret",
            "tokenUrl": "https://token.example.com",
            "authorizeUrl": "",
            "redirectUri": "",
            "scopes": [],
        })

        mock_provider = AsyncMock()
        mock_provider.refresh_access_token = AsyncMock(return_value=new_token)
        mock_provider.close = AsyncMock()

        with patch(
            "app.connectors.core.base.token_service.toolset_token_refresh_service.get_oauth_config"
        ), patch(
            "app.connectors.core.base.token_service.oauth_service.OAuthProvider",
            return_value=mock_provider
        ), patch("asyncio.sleep", new_callable=AsyncMock):
            result = await svc._perform_token_refresh("/services/toolsets/inst/user", "googledrive", "rt_123")

        assert result is new_token

    async def test_no_auth_type_assumes_oauth(self):
        """Toolset with empty auth.type but has refresh_token is treated as OAuth."""
        cs = AsyncMock()
        config = {
            "auth": {},
            "credentials": {"refresh_token": "rt_old"},
        }
        cs.get_config = AsyncMock(return_value=config)
        cs.set_config = AsyncMock(return_value=True)
        svc = _make_service(cs)

        new_token = _make_token()
        svc._build_complete_oauth_config = AsyncMock(return_value={
            "clientId": "cid",
            "clientSecret": "csecret",
            "tokenUrl": "https://token.example.com",
            "authorizeUrl": "",
            "redirectUri": "",
            "scopes": [],
        })

        mock_provider = AsyncMock()
        mock_provider.refresh_access_token = AsyncMock(return_value=new_token)
        mock_provider.close = AsyncMock()

        with patch(
            "app.connectors.core.base.token_service.toolset_token_refresh_service.get_oauth_config"
        ), patch(
            "app.connectors.core.base.token_service.oauth_service.OAuthProvider",
            return_value=mock_provider
        ), patch("asyncio.sleep", new_callable=AsyncMock):
            result = await svc._perform_token_refresh("/services/toolsets/inst/user", "googledrive", "rt_123")

        assert result is new_token

    async def test_updates_last_refresh_time(self):
        cs = AsyncMock()
        config = {
            "auth": {"type": "OAUTH"},
            "credentials": {"refresh_token": "rt_old"},
        }
        cs.get_config = AsyncMock(return_value=config)
        cs.set_config = AsyncMock(return_value=True)
        svc = _make_service(cs)

        new_token = _make_token()
        svc._build_complete_oauth_config = AsyncMock(return_value={
            "clientId": "cid",
            "clientSecret": "csecret",
            "tokenUrl": "https://token.example.com",
            "authorizeUrl": "",
            "redirectUri": "",
            "scopes": [],
        })

        mock_provider = AsyncMock()
        mock_provider.refresh_access_token = AsyncMock(return_value=new_token)
        mock_provider.close = AsyncMock()
        config_path = "/services/toolsets/inst/user"

        with patch(
            "app.connectors.core.base.token_service.toolset_token_refresh_service.get_oauth_config"
        ), patch(
            "app.connectors.core.base.token_service.oauth_service.OAuthProvider",
            return_value=mock_provider
        ), patch("asyncio.sleep", new_callable=AsyncMock):
            await svc._perform_token_refresh(config_path, "googledrive", "rt_123")

        assert config_path in svc._last_refresh_time
        assert svc._last_refresh_time[config_path] > 0


# ---------------------------------------------------------------------------
# Processing tracking helpers
# ---------------------------------------------------------------------------


class TestProcessingTracking:
    def test_is_toolset_being_processed_false(self):
        svc = _make_service()
        assert svc._is_toolset_being_processed("/path") is False

    def test_mark_and_check(self):
        svc = _make_service()
        svc._mark_toolset_processing("/path")
        assert svc._is_toolset_being_processed("/path") is True

    def test_unmark(self):
        svc = _make_service()
        svc._mark_toolset_processing("/path")
        svc._unmark_toolset_processing("/path")
        assert svc._is_toolset_being_processed("/path") is False

    def test_unmark_nonexistent_safe(self):
        svc = _make_service()
        # discard on set doesn't raise for missing items
        svc._unmark_toolset_processing("/nonexistent")


# ---------------------------------------------------------------------------
# _load_token_from_config
# ---------------------------------------------------------------------------


class TestLoadTokenFromConfig:
    async def test_no_config(self):
        cs = AsyncMock()
        cs.get_config = AsyncMock(return_value=None)
        svc = _make_service(cs)
        token, has_creds = await svc._load_token_from_config("/path")
        assert token is None
        assert has_creds is False

    async def test_not_authenticated(self):
        cs = AsyncMock()
        cs.get_config = AsyncMock(return_value={"isAuthenticated": False})
        svc = _make_service(cs)
        token, has_creds = await svc._load_token_from_config("/path")
        assert token is None
        assert has_creds is False

    async def test_no_credentials(self):
        cs = AsyncMock()
        cs.get_config = AsyncMock(return_value={
            "isAuthenticated": True,
            "credentials": None,
        })
        svc = _make_service(cs)
        token, has_creds = await svc._load_token_from_config("/path")
        assert token is None
        assert has_creds is False

    async def test_no_refresh_token(self):
        cs = AsyncMock()
        cs.get_config = AsyncMock(return_value={
            "isAuthenticated": True,
            "credentials": {"access_token": "at_123"},
        })
        svc = _make_service(cs)
        token, has_creds = await svc._load_token_from_config("/path")
        assert token is None
        assert has_creds is False

    async def test_valid_credentials(self):
        cs = AsyncMock()
        cs.get_config = AsyncMock(return_value={
            "isAuthenticated": True,
            "credentials": {
                "access_token": "at_123",
                "refresh_token": "rt_123",
                "expires_in": 3600,
                "created_at": datetime.now().isoformat(),
            },
        })
        svc = _make_service(cs)
        token, has_creds = await svc._load_token_from_config("/path")
        assert token is not None
        assert has_creds is True
        assert token.access_token == "at_123"
        assert token.refresh_token == "rt_123"


# ---------------------------------------------------------------------------
# _handle_token_refresh_workflow
# ---------------------------------------------------------------------------


class TestHandleTokenRefreshWorkflow:
    async def test_expired_token_refreshes_immediately(self):
        svc = _make_service()
        token = _make_expired_token()
        new_token = _make_token()
        svc._perform_token_refresh = AsyncMock(return_value=new_token)
        svc.schedule_token_refresh = AsyncMock()

        config_path = "/services/toolsets/inst/user"
        await svc._handle_token_refresh_workflow(config_path, "googledrive", token)

        svc._perform_token_refresh.assert_awaited_once_with(config_path, "googledrive", token.refresh_token)
        svc.schedule_token_refresh.assert_awaited_once_with(config_path, "googledrive", new_token)

    async def test_valid_token_schedules_refresh(self):
        svc = _make_service()
        token = _make_token(expires_in=7200)  # 2 hours, well outside refresh window
        svc._perform_token_refresh = AsyncMock()
        svc.schedule_token_refresh = AsyncMock()

        config_path = "/services/toolsets/inst/user"
        await svc._handle_token_refresh_workflow(config_path, "googledrive", token)

        svc._perform_token_refresh.assert_not_awaited()
        svc.schedule_token_refresh.assert_awaited_once_with(config_path, "googledrive", token)

    async def test_token_no_expires_in(self):
        """Token with expires_in=0 triggers immediate refresh."""
        svc = _make_service()
        token = _make_token(expires_in=0)
        new_token = _make_token()
        svc._perform_token_refresh = AsyncMock(return_value=new_token)
        svc.schedule_token_refresh = AsyncMock()

        await svc._handle_token_refresh_workflow("/path", "googledrive", token)
        svc._perform_token_refresh.assert_awaited_once()


# ---------------------------------------------------------------------------
# _refresh_toolset_token
# ---------------------------------------------------------------------------


class TestRefreshToolsetToken:
    async def test_timeout_on_lock(self):
        svc = _make_service()
        config_path = "/services/toolsets/inst/user"
        lock = svc._get_toolset_lock(config_path)
        # Hold the lock so the method times out
        await lock.acquire()

        with patch("asyncio.wait_for", side_effect=asyncio.TimeoutError()):
            await svc._refresh_toolset_token(config_path, "googledrive")

        lock.release()

    async def test_already_processing_returns(self):
        svc = _make_service()
        config_path = "/services/toolsets/inst/user"
        svc._mark_toolset_processing(config_path)
        svc._load_token_from_config = AsyncMock()

        await svc._refresh_toolset_token(config_path, "googledrive")

        svc._load_token_from_config.assert_not_awaited()
        # Clean up
        svc._unmark_toolset_processing(config_path)

    async def test_no_credentials(self):
        svc = _make_service()
        svc._load_token_from_config = AsyncMock(return_value=(None, False))
        svc._handle_token_refresh_workflow = AsyncMock()

        await svc._refresh_toolset_token("/services/toolsets/inst/user", "googledrive")

        svc._handle_token_refresh_workflow.assert_not_awaited()

    async def test_successful_refresh(self):
        svc = _make_service()
        token = _make_token()
        svc._load_token_from_config = AsyncMock(return_value=(token, True))
        svc._handle_token_refresh_workflow = AsyncMock()

        await svc._refresh_toolset_token("/services/toolsets/inst/user", "googledrive")

        svc._handle_token_refresh_workflow.assert_awaited_once()

    async def test_exception_in_workflow(self):
        svc = _make_service()
        token = _make_token()
        svc._load_token_from_config = AsyncMock(return_value=(token, True))
        svc._handle_token_refresh_workflow = AsyncMock(side_effect=Exception("workflow error"))

        config_path = "/services/toolsets/inst/user"
        # Should not raise
        await svc._refresh_toolset_token(config_path, "googledrive")

        # Processing should be unmarked after exception
        assert not svc._is_toolset_being_processed(config_path)

    async def test_recursion_error_caught(self):
        svc = _make_service()
        token = _make_token()
        svc._load_token_from_config = AsyncMock(return_value=(token, True))
        svc._handle_token_refresh_workflow = AsyncMock(side_effect=RecursionError("recursion"))

        # Should not raise
        await svc._refresh_toolset_token("/services/toolsets/inst/user", "googledrive")

    async def test_lock_acquire_exception(self):
        svc = _make_service()
        config_path = "/services/toolsets/inst/user"

        with patch("asyncio.wait_for", side_effect=RuntimeError("lock error")):
            # Should not raise
            await svc._refresh_toolset_token(config_path, "googledrive")


# ---------------------------------------------------------------------------
# _periodic_refresh_check
# ---------------------------------------------------------------------------


class TestPeriodicRefreshCheck:
    async def test_runs_and_stops(self):
        svc = _make_service()
        svc._running = True
        svc._refresh_all_tokens = AsyncMock()

        call_count = 0

        async def mock_sleep(seconds):
            nonlocal call_count
            call_count += 1
            if call_count >= 2:
                svc._running = False

        with patch("asyncio.sleep", side_effect=mock_sleep):
            await svc._periodic_refresh_check()

        assert svc._refresh_all_tokens.await_count >= 1

    async def test_cancelled_error(self):
        svc = _make_service()
        svc._running = True

        with patch("asyncio.sleep", side_effect=asyncio.CancelledError()):
            await svc._periodic_refresh_check()

    async def test_exception_continues(self):
        svc = _make_service()
        svc._running = True
        call_count = 0

        svc._refresh_all_tokens = AsyncMock(side_effect=Exception("refresh error"))

        async def mock_sleep(seconds):
            nonlocal call_count
            call_count += 1
            if call_count >= 2:
                svc._running = False

        with patch("asyncio.sleep", side_effect=mock_sleep):
            await svc._periodic_refresh_check()


# ---------------------------------------------------------------------------
# refresh_toolset_token (public)
# ---------------------------------------------------------------------------


class TestRefreshToolsetTokenPublic:
    async def test_delegates_to_private(self):
        svc = _make_service()
        svc._refresh_toolset_token = AsyncMock()
        await svc.refresh_toolset_token("/path", "googledrive")
        svc._refresh_toolset_token.assert_awaited_once_with("/path", "googledrive")


# ---------------------------------------------------------------------------
# _calculate_refresh_delay
# ---------------------------------------------------------------------------


class TestCalculateRefreshDelay:
    def test_no_expires_in(self):
        svc = _make_service()
        token = _make_token(expires_in=None)
        delay, refresh_time = svc._calculate_refresh_delay(token)
        assert delay == 0.0

    def test_zero_expires_in(self):
        svc = _make_service()
        token = _make_token(expires_in=0)
        delay, refresh_time = svc._calculate_refresh_delay(token)
        assert delay == 0.0

    def test_negative_expires_in(self):
        svc = _make_service()
        token = OAuthToken(
            access_token="at",
            refresh_token="rt",
            expires_in=-100,
        )
        delay, refresh_time = svc._calculate_refresh_delay(token)
        assert delay == 0.0

    def test_normal_token_refresh_window(self):
        """Token with 3600s TTL uses 600s proactive window."""
        svc = _make_service()
        token = _make_token(expires_in=3600)
        delay, refresh_time = svc._calculate_refresh_delay(token)
        # delay should be roughly 3600 - 600 = 3000 seconds from now
        assert delay > 2900
        assert delay < 3100

    def test_short_lived_token(self):
        """Token with 120s TTL uses 20% buffer (24s, min 60s)."""
        svc = _make_service()
        token = _make_short_lived_token(expires_in=120)
        delay, refresh_time = svc._calculate_refresh_delay(token)
        # Window = max(60, 120*0.2) = max(60, 24) = 60
        # Window clamped to min(60, max(1, 119)) = 60
        # delay ~ 120 - 60 = 60 seconds
        assert delay > 50
        assert delay < 70

    def test_very_short_lived_token(self):
        """Token with 10s TTL."""
        svc = _make_service()
        token = _make_short_lived_token(expires_in=10)
        delay, refresh_time = svc._calculate_refresh_delay(token)
        # Window = max(60, 10*0.2) = 60, clamped to min(60, max(1, 9)) = 9
        # delay ~ 10 - 9 = 1 second
        assert delay >= 0
        assert delay < 5

    def test_expired_token(self):
        svc = _make_service()
        token = _make_expired_token()
        delay, refresh_time = svc._calculate_refresh_delay(token)
        assert delay < 0

    def test_token_in_refresh_window(self):
        """Token created a while ago, now inside the refresh window."""
        svc = _make_service()
        # Token with 3600s TTL, created 3100s ago (within 600s window)
        token = _make_token(
            expires_in=3600,
            created_at=datetime.now() - timedelta(seconds=3100)
        )
        delay, refresh_time = svc._calculate_refresh_delay(token)
        assert delay < 0  # Already past refresh time


# ---------------------------------------------------------------------------
# _refresh_token_immediately
# ---------------------------------------------------------------------------


class TestRefreshTokenImmediately:
    async def test_success(self):
        svc = _make_service()
        token = _make_token()
        new_token = _make_token(access_token="at_new")
        svc._perform_token_refresh = AsyncMock(return_value=new_token)

        result_token, success = await svc._refresh_token_immediately("/path", "googledrive", token)
        assert success is True
        assert result_token is new_token

    async def test_failure(self):
        svc = _make_service()
        token = _make_token()
        svc._perform_token_refresh = AsyncMock(side_effect=Exception("refresh failed"))

        result_token, success = await svc._refresh_token_immediately("/path", "googledrive", token)
        assert success is False
        assert result_token is None


# ---------------------------------------------------------------------------
# _cancel_existing_refresh_task / cancel_refresh_task
# ---------------------------------------------------------------------------


class TestCancelRefreshTask:
    def test_no_existing_task(self):
        svc = _make_service()
        # Should not raise
        svc._cancel_existing_refresh_task("/nonexistent")

    def test_done_task_removed(self):
        svc = _make_service()
        task = MagicMock()
        task.done.return_value = True
        svc._refresh_tasks["/path"] = task
        svc._cancel_existing_refresh_task("/path")
        assert "/path" not in svc._refresh_tasks
        task.cancel.assert_not_called()

    def test_running_task_cancelled(self):
        svc = _make_service()
        task = MagicMock()
        task.done.return_value = False
        svc._refresh_tasks["/path"] = task
        svc._cancel_existing_refresh_task("/path")
        task.cancel.assert_called_once()
        assert "/path" not in svc._refresh_tasks

    def test_public_cancel_delegates(self):
        svc = _make_service()
        svc._cancel_existing_refresh_task = MagicMock()
        svc.cancel_refresh_task("/path")
        svc._cancel_existing_refresh_task.assert_called_once_with("/path")


# ---------------------------------------------------------------------------
# cancel_refresh_tasks_for_instance
# ---------------------------------------------------------------------------


class TestCancelRefreshTasksForInstance:
    def test_no_matching_tasks(self):
        svc = _make_service()
        task = MagicMock()
        task.done.return_value = False
        svc._refresh_tasks["/services/toolsets/other-inst/user1"] = task
        count = svc.cancel_refresh_tasks_for_instance("target-inst")
        assert count == 0
        task.cancel.assert_not_called()

    def test_cancels_matching_tasks(self):
        svc = _make_service()
        inst_id = "107344f6-66cb-46f9-89f1-22d0bdae99cb"
        task1 = MagicMock()
        task1.done.return_value = False
        task2 = MagicMock()
        task2.done.return_value = False
        task3 = MagicMock()
        task3.done.return_value = False

        svc._refresh_tasks[f"/services/toolsets/{inst_id}/user1"] = task1
        svc._refresh_tasks[f"/services/toolsets/{inst_id}/user2"] = task2
        svc._refresh_tasks["/services/toolsets/other-inst/user3"] = task3

        count = svc.cancel_refresh_tasks_for_instance(inst_id)
        assert count == 2
        task1.cancel.assert_called_once()
        task2.cancel.assert_called_once()
        task3.cancel.assert_not_called()


# ---------------------------------------------------------------------------
# _create_refresh_task
# ---------------------------------------------------------------------------


class TestCreateRefreshTask:
    def test_creates_task(self):
        svc = _make_service()
        config_path = "/services/toolsets/inst/user"
        with patch("asyncio.create_task", return_value=MagicMock()) as mock_ct:
            result = svc._create_refresh_task(config_path, "googledrive", 300.0, datetime.now())
        assert result is True
        assert config_path in svc._refresh_tasks

    def test_refuses_duplicate_running_task(self):
        svc = _make_service()
        config_path = "/services/toolsets/inst/user"
        existing = MagicMock()
        existing.done.return_value = False
        existing.cancelled.return_value = False
        svc._refresh_tasks[config_path] = existing

        result = svc._create_refresh_task(config_path, "googledrive", 300.0, datetime.now())
        assert result is False

    def test_replaces_done_task(self):
        svc = _make_service()
        config_path = "/services/toolsets/inst/user"
        existing = MagicMock()
        existing.done.return_value = True
        existing.cancelled.return_value = False
        svc._refresh_tasks[config_path] = existing

        with patch("asyncio.create_task", return_value=MagicMock()):
            result = svc._create_refresh_task(config_path, "googledrive", 300.0, datetime.now())
        assert result is True

    def test_exception_returns_false(self):
        svc = _make_service()
        with patch("asyncio.create_task", side_effect=RuntimeError("event loop closed")):
            result = svc._create_refresh_task("/path", "googledrive", 300.0, datetime.now())
        assert result is False


# ---------------------------------------------------------------------------
# schedule_token_refresh
# ---------------------------------------------------------------------------


class TestScheduleTokenRefresh:
    async def test_no_expires_in(self):
        svc = _make_service()
        token = _make_token(expires_in=None)
        svc._create_refresh_task = MagicMock()
        await svc.schedule_token_refresh("/path", "googledrive", token)
        svc._create_refresh_task.assert_not_called()

    async def test_schedules_refresh(self):
        svc = _make_service()
        svc._running = True
        token = _make_token(expires_in=3600)
        svc._create_refresh_task = MagicMock(return_value=True)

        await svc.schedule_token_refresh("/path", "googledrive", token)
        svc._create_refresh_task.assert_called_once()

    async def test_skips_if_existing_running_task(self):
        svc = _make_service()
        svc._running = True
        token = _make_token(expires_in=3600)

        existing_task = MagicMock()
        existing_task.done.return_value = False
        existing_task.cancelled.return_value = False
        svc._refresh_tasks["/path"] = existing_task
        svc._create_refresh_task = MagicMock()

        # Mock current_task to return something different from existing
        with patch("asyncio.current_task", return_value=MagicMock()):
            await svc.schedule_token_refresh("/path", "googledrive", token)

        svc._create_refresh_task.assert_not_called()

    async def test_replaces_dead_task(self):
        svc = _make_service()
        svc._running = True
        token = _make_token(expires_in=3600)

        existing_task = MagicMock()
        existing_task.done.return_value = True
        existing_task.cancelled.return_value = False
        svc._refresh_tasks["/path"] = existing_task
        svc._create_refresh_task = MagicMock(return_value=True)

        with patch("asyncio.current_task", return_value=MagicMock()):
            await svc.schedule_token_refresh("/path", "googledrive", token)

        svc._create_refresh_task.assert_called_once()

    async def test_cooldown_skips(self):
        svc = _make_service()
        svc._running = True
        token = _make_token(expires_in=3600)
        svc._last_refresh_time["/path"] = time.time()  # Just refreshed
        svc._create_refresh_task = MagicMock()

        await svc.schedule_token_refresh("/path", "googledrive", token)
        svc._create_refresh_task.assert_not_called()

    async def test_expired_token_schedules_immediate(self):
        svc = _make_service()
        svc._running = True
        token = _make_expired_token()
        svc._create_refresh_task = MagicMock(return_value=True)

        await svc.schedule_token_refresh("/path", "googledrive", token)

        # Should be called with a small delay (MIN_IMMEDIATE_RECHECK_DELAY)
        call_args = svc._create_refresh_task.call_args
        delay_arg = call_args[0][2]
        assert delay_arg == MIN_IMMEDIATE_RECHECK_DELAY

    async def test_self_rescheduling(self):
        """When existing task IS the current task, allow self-rescheduling."""
        svc = _make_service()
        svc._running = True
        token = _make_token(expires_in=3600)

        sentinel_task = MagicMock()
        svc._refresh_tasks["/path"] = sentinel_task
        svc._create_refresh_task = MagicMock(return_value=True)

        # current_task returns the SAME task object
        with patch("asyncio.current_task", return_value=sentinel_task):
            await svc.schedule_token_refresh("/path", "googledrive", token)

        # Should proceed to create (after removing old reference)
        svc._create_refresh_task.assert_called_once()

    async def test_not_running_still_schedules(self):
        svc = _make_service()
        svc._running = False  # Not running
        token = _make_token(expires_in=3600)
        svc._create_refresh_task = MagicMock(return_value=True)

        await svc.schedule_token_refresh("/path", "googledrive", token)
        svc._create_refresh_task.assert_called_once()


# ---------------------------------------------------------------------------
# _delayed_refresh
# ---------------------------------------------------------------------------


class TestDelayedRefresh:
    async def test_normal_flow(self):
        svc = _make_service()
        svc._refresh_toolset_token = AsyncMock()
        config_path = "/services/toolsets/inst/user"
        # Put a task reference so cleanup works
        current_task_mock = MagicMock()

        with patch("asyncio.sleep", new_callable=AsyncMock), \
             patch("asyncio.current_task", return_value=current_task_mock):
            svc._refresh_tasks[config_path] = current_task_mock
            await svc._delayed_refresh(config_path, "googledrive", 0.0)

        svc._refresh_toolset_token.assert_awaited_once()

    async def test_cancelled_error_reraises(self):
        svc = _make_service()
        config_path = "/services/toolsets/inst/user"

        with patch("asyncio.sleep", side_effect=asyncio.CancelledError()):
            with pytest.raises(asyncio.CancelledError):
                await svc._delayed_refresh(config_path, "googledrive", 10.0)

    async def test_exception_in_refresh(self):
        svc = _make_service()
        svc._refresh_toolset_token = AsyncMock(side_effect=Exception("refresh error"))
        config_path = "/services/toolsets/inst/user"
        current_task_mock = MagicMock()

        with patch("asyncio.sleep", new_callable=AsyncMock), \
             patch("asyncio.current_task", return_value=current_task_mock):
            svc._refresh_tasks[config_path] = current_task_mock
            # Should not raise
            await svc._delayed_refresh(config_path, "googledrive", 0.0)

    async def test_cleanup_only_removes_own_task(self):
        """Finally block only removes task if it's the current task."""
        svc = _make_service()
        svc._refresh_toolset_token = AsyncMock()
        config_path = "/services/toolsets/inst/user"

        current_task_mock = MagicMock()
        different_task = MagicMock()
        svc._refresh_tasks[config_path] = different_task

        with patch("asyncio.sleep", new_callable=AsyncMock), \
             patch("asyncio.current_task", return_value=current_task_mock):
            await svc._delayed_refresh(config_path, "googledrive", 0.0)

        # different_task should still be there since current_task != tracked task
        assert config_path in svc._refresh_tasks
        assert svc._refresh_tasks[config_path] is different_task


# ---------------------------------------------------------------------------
# _cleanup_old_locks
# ---------------------------------------------------------------------------


class TestCleanupOldLocks:
    async def test_cleanup_stale_locks(self):
        svc = _make_service()
        svc._running = True

        # Add locks for paths that won't be in current_paths
        stale_path = "/services/toolsets/stale/user"
        svc._toolset_locks[stale_path] = asyncio.Lock()
        svc._schedule_locks[stale_path] = asyncio.Lock()
        svc._last_refresh_time[stale_path] = time.time()

        current_path = "/services/toolsets/active/user"
        svc._toolset_locks[current_path] = asyncio.Lock()

        cs = AsyncMock()
        cs.list_keys_in_directory = AsyncMock(return_value=[current_path])
        svc.configuration_service = cs

        call_count = 0

        async def mock_sleep(seconds):
            nonlocal call_count
            call_count += 1
            if call_count >= 1:
                svc._running = False

        with patch("asyncio.sleep", side_effect=mock_sleep):
            await svc._cleanup_old_locks()

        assert stale_path not in svc._toolset_locks
        assert stale_path not in svc._schedule_locks
        assert stale_path not in svc._last_refresh_time
        assert current_path in svc._toolset_locks

    async def test_cleanup_skips_locked(self):
        svc = _make_service()
        svc._running = True

        stale_path = "/services/toolsets/stale/user"
        lock = asyncio.Lock()
        await lock.acquire()  # Lock is held
        svc._toolset_locks[stale_path] = lock

        cs = AsyncMock()
        cs.list_keys_in_directory = AsyncMock(return_value=[])
        svc.configuration_service = cs

        call_count = 0

        async def mock_sleep(seconds):
            nonlocal call_count
            call_count += 1
            if call_count >= 1:
                svc._running = False

        with patch("asyncio.sleep", side_effect=mock_sleep):
            await svc._cleanup_old_locks()

        # Should NOT remove locked lock
        assert stale_path in svc._toolset_locks
        lock.release()

    async def test_cleanup_list_keys_exception(self):
        svc = _make_service()
        svc._running = True

        cs = AsyncMock()
        cs.list_keys_in_directory = AsyncMock(side_effect=Exception("etcd error"))
        svc.configuration_service = cs

        call_count = 0

        async def mock_sleep(seconds):
            nonlocal call_count
            call_count += 1
            if call_count >= 1:
                svc._running = False

        with patch("asyncio.sleep", side_effect=mock_sleep):
            await svc._cleanup_old_locks()

    async def test_cleanup_cancelled(self):
        svc = _make_service()
        svc._running = True

        with patch("asyncio.sleep", side_effect=asyncio.CancelledError()):
            await svc._cleanup_old_locks()

    async def test_cleanup_general_exception(self):
        svc = _make_service()
        svc._running = True

        cs = AsyncMock()
        cs.list_keys_in_directory = AsyncMock(side_effect=RuntimeError("unexpected"))
        svc.configuration_service = cs

        call_count = 0

        async def mock_sleep(seconds):
            nonlocal call_count
            call_count += 1
            if call_count >= 1:
                svc._running = False

        with patch("asyncio.sleep", side_effect=mock_sleep):
            await svc._cleanup_old_locks()

    async def test_no_stale_locks(self):
        svc = _make_service()
        svc._running = True

        active_path = "/services/toolsets/active/user"
        svc._toolset_locks[active_path] = asyncio.Lock()

        cs = AsyncMock()
        cs.list_keys_in_directory = AsyncMock(return_value=[active_path])
        svc.configuration_service = cs

        call_count = 0

        async def mock_sleep(seconds):
            nonlocal call_count
            call_count += 1
            if call_count >= 1:
                svc._running = False

        with patch("asyncio.sleep", side_effect=mock_sleep):
            await svc._cleanup_old_locks()

        assert active_path in svc._toolset_locks


# ---------------------------------------------------------------------------
# Constants verification
# ---------------------------------------------------------------------------


class TestConstants:
    def test_constants_values(self):
        assert MIN_PATH_PARTS_COUNT == 4
        assert LOCK_TIMEOUT == 30
        assert TOKEN_REFRESH_MAX_RETRIES == 3
        assert INITIAL_RETRY_DELAY == 0.3
        assert REFRESH_COOLDOWN == 10
        assert MIN_IMMEDIATE_RECHECK_DELAY == 1
        assert PROACTIVE_REFRESH_WINDOW_SECONDS == 600
        assert MIN_SHORT_LIVED_REFRESH_WINDOW_SECONDS == 60
        assert SHORT_LIVED_TOKEN_BUFFER_RATIO == 0.2
