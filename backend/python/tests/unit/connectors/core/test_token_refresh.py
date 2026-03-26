"""Unit tests for app.connectors.core.base.token_service.token_refresh_service."""

import asyncio
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.connectors.core.base.token_service.oauth_service import OAuthToken
from app.connectors.core.base.token_service.token_refresh_service import TokenRefreshService


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_service(config_service=None, graph_provider=None):
    """Create a TokenRefreshService with mocked deps."""
    cs = config_service or MagicMock()
    gp = graph_provider or MagicMock()
    return TokenRefreshService(cs, gp)


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


# ---------------------------------------------------------------------------
# Init
# ---------------------------------------------------------------------------

class TestTokenRefreshServiceInit:
    def test_init(self):
        svc = _make_service()
        assert svc._running is False
        assert svc._refresh_tasks == {}
        assert len(svc._processing_connectors) == 0


# ---------------------------------------------------------------------------
# Start / Stop
# ---------------------------------------------------------------------------

class TestStartStop:
    @pytest.mark.asyncio
    async def test_start_sets_running(self):
        svc = _make_service()
        svc._refresh_all_tokens = AsyncMock()
        # Patch create_task to avoid actual periodic task
        with patch("asyncio.create_task"):
            await svc.start()
        assert svc._running is True
        svc._refresh_all_tokens.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_start_twice_is_noop(self):
        svc = _make_service()
        svc._running = True
        svc._refresh_all_tokens = AsyncMock()
        await svc.start()
        svc._refresh_all_tokens.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_stop_cancels_tasks(self):
        svc = _make_service()
        svc._running = True
        mock_task = MagicMock()
        svc._refresh_tasks = {"conn1": mock_task}
        await svc.stop()
        assert svc._running is False
        mock_task.cancel.assert_called_once()
        assert svc._refresh_tasks == {}


# ---------------------------------------------------------------------------
# _is_oauth_connector
# ---------------------------------------------------------------------------

class TestIsOAuthConnector:
    def test_oauth_type(self):
        svc = _make_service()
        assert svc._is_oauth_connector({"authType": "OAUTH"}) is True

    def test_oauth_admin_consent_type(self):
        svc = _make_service()
        assert svc._is_oauth_connector({"authType": "OAUTH_ADMIN_CONSENT"}) is True

    def test_api_token_type(self):
        svc = _make_service()
        assert svc._is_oauth_connector({"authType": "API_TOKEN"}) is False

    def test_missing_auth_type(self):
        svc = _make_service()
        assert svc._is_oauth_connector({}) is False


# ---------------------------------------------------------------------------
# _is_connector_authenticated
# ---------------------------------------------------------------------------

class TestIsConnectorAuthenticated:
    @pytest.mark.asyncio
    async def test_authenticated(self):
        cs = MagicMock()
        cs.get_config = AsyncMock(return_value={
            "credentials": {"refresh_token": "rt_123"}
        })
        svc = _make_service(config_service=cs)
        result = await svc._is_connector_authenticated("conn1")
        assert result is True

    @pytest.mark.asyncio
    async def test_no_config(self):
        cs = MagicMock()
        cs.get_config = AsyncMock(return_value=None)
        svc = _make_service(config_service=cs)
        assert await svc._is_connector_authenticated("conn1") is False

    @pytest.mark.asyncio
    async def test_no_credentials(self):
        cs = MagicMock()
        cs.get_config = AsyncMock(return_value={"credentials": None})
        svc = _make_service(config_service=cs)
        assert await svc._is_connector_authenticated("conn1") is False

    @pytest.mark.asyncio
    async def test_no_refresh_token(self):
        cs = MagicMock()
        cs.get_config = AsyncMock(return_value={
            "credentials": {"access_token": "at"}
        })
        svc = _make_service(config_service=cs)
        assert await svc._is_connector_authenticated("conn1") is False

    @pytest.mark.asyncio
    async def test_exception_returns_false(self):
        cs = MagicMock()
        cs.get_config = AsyncMock(side_effect=RuntimeError("etcd down"))
        svc = _make_service(config_service=cs)
        assert await svc._is_connector_authenticated("conn1") is False


# ---------------------------------------------------------------------------
# _filter_authenticated_oauth_connectors
# ---------------------------------------------------------------------------

class TestFilterAuthenticatedOAuthConnectors:
    @pytest.mark.asyncio
    async def test_filters_correctly(self):
        cs = MagicMock()
        cs.get_config = AsyncMock(return_value={
            "credentials": {"refresh_token": "rt"}
        })
        svc = _make_service(config_service=cs)

        connectors = [
            {"_key": "c1", "authType": "OAUTH"},
            {"_key": "c2", "authType": "API_TOKEN"},
            {"_key": "c3", "authType": "OAUTH_ADMIN_CONSENT"},
        ]
        result = await svc._filter_authenticated_oauth_connectors(connectors)
        keys = [c["_key"] for c in result]
        assert "c1" in keys
        assert "c3" in keys
        assert "c2" not in keys

    @pytest.mark.asyncio
    async def test_skips_no_key(self):
        cs = MagicMock()
        cs.get_config = AsyncMock(return_value={
            "credentials": {"refresh_token": "rt"}
        })
        svc = _make_service(config_service=cs)

        connectors = [{"authType": "OAUTH"}]
        result = await svc._filter_authenticated_oauth_connectors(connectors)
        assert len(result) == 0


# ---------------------------------------------------------------------------
# Processing connector tracking
# ---------------------------------------------------------------------------

class TestProcessingTracking:
    def test_mark_and_check(self):
        svc = _make_service()
        assert svc._is_connector_being_processed("c1") is False
        svc._mark_connector_processing("c1")
        assert svc._is_connector_being_processed("c1") is True

    def test_unmark(self):
        svc = _make_service()
        svc._mark_connector_processing("c1")
        svc._unmark_connector_processing("c1")
        assert svc._is_connector_being_processed("c1") is False

    def test_unmark_nonexistent(self):
        svc = _make_service()
        # Should not raise
        svc._unmark_connector_processing("c1")


# ---------------------------------------------------------------------------
# _load_token_from_config
# ---------------------------------------------------------------------------

class TestLoadTokenFromConfig:
    @pytest.mark.asyncio
    async def test_success(self):
        cs = MagicMock()
        cs.get_config = AsyncMock(return_value={
            "credentials": {
                "access_token": "at",
                "refresh_token": "rt",
                "expires_in": 3600,
                "created_at": datetime.now().isoformat(),
            }
        })
        svc = _make_service(config_service=cs)
        token, has_creds = await svc._load_token_from_config("c1")
        assert has_creds is True
        assert token is not None
        assert token.access_token == "at"

    @pytest.mark.asyncio
    async def test_no_config(self):
        cs = MagicMock()
        cs.get_config = AsyncMock(return_value=None)
        svc = _make_service(config_service=cs)
        token, has_creds = await svc._load_token_from_config("c1")
        assert has_creds is False
        assert token is None

    @pytest.mark.asyncio
    async def test_no_credentials(self):
        cs = MagicMock()
        cs.get_config = AsyncMock(return_value={"credentials": None})
        svc = _make_service(config_service=cs)
        token, has_creds = await svc._load_token_from_config("c1")
        assert has_creds is False

    @pytest.mark.asyncio
    async def test_no_refresh_token(self):
        cs = MagicMock()
        cs.get_config = AsyncMock(return_value={
            "credentials": {"access_token": "at"}
        })
        svc = _make_service(config_service=cs)
        token, has_creds = await svc._load_token_from_config("c1")
        assert has_creds is False


# ---------------------------------------------------------------------------
# _calculate_refresh_delay
# ---------------------------------------------------------------------------

class TestCalculateRefreshDelay:
    def test_future_expiry(self):
        svc = _make_service()
        token = _make_token(expires_in=7200, created_at=datetime.now())
        delay, refresh_time = svc._calculate_refresh_delay(token)
        # Should be ~6600 seconds (7200 - 600)
        assert delay > 6000
        assert delay < 7000

    def test_near_expiry(self):
        svc = _make_service()
        token = _make_token(expires_in=300, created_at=datetime.now())
        delay, _ = svc._calculate_refresh_delay(token)
        # expires_in=300 < 600, so delay should be negative
        assert delay < 0

    def test_zero_expires_in(self):
        svc = _make_service()
        token = _make_token(expires_in=0, created_at=datetime.now())
        delay, _ = svc._calculate_refresh_delay(token)
        assert delay <= 0


# ---------------------------------------------------------------------------
# _cancel_existing_refresh_task
# ---------------------------------------------------------------------------

class TestCancelExistingRefreshTask:
    def test_no_existing_task(self):
        svc = _make_service()
        svc._cancel_existing_refresh_task("c1")  # Should not raise

    def test_done_task_removed(self):
        svc = _make_service()
        task = MagicMock()
        task.done.return_value = True
        svc._refresh_tasks["c1"] = task
        svc._cancel_existing_refresh_task("c1")
        assert "c1" not in svc._refresh_tasks

    def test_running_task_cancelled(self):
        svc = _make_service()
        task = MagicMock()
        task.done.return_value = False
        svc._refresh_tasks["c1"] = task
        svc._cancel_existing_refresh_task("c1")
        task.cancel.assert_called_once()

    def test_cancel_error_handled(self):
        svc = _make_service()
        task = MagicMock()
        task.done.return_value = False
        task.cancel.side_effect = RuntimeError("cancel fail")
        svc._refresh_tasks["c1"] = task
        svc._cancel_existing_refresh_task("c1")  # Should not raise


# ---------------------------------------------------------------------------
# _refresh_connector_token
# ---------------------------------------------------------------------------

class TestRefreshConnectorToken:
    @pytest.mark.asyncio
    async def test_skips_already_processing(self):
        svc = _make_service()
        svc._mark_connector_processing("c1")
        svc._load_token_from_config = AsyncMock()
        await svc._refresh_connector_token("c1", "Jira")
        svc._load_token_from_config.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_skips_no_credentials(self):
        svc = _make_service()
        svc._load_token_from_config = AsyncMock(return_value=(None, False))
        svc._handle_token_refresh_workflow = AsyncMock()
        await svc._refresh_connector_token("c1", "Jira")
        svc._handle_token_refresh_workflow.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_calls_workflow_on_valid_token(self):
        svc = _make_service()
        token = _make_token()
        svc._load_token_from_config = AsyncMock(return_value=(token, True))
        svc._handle_token_refresh_workflow = AsyncMock()
        await svc._refresh_connector_token("c1", "Jira")
        svc._handle_token_refresh_workflow.assert_awaited_once_with("c1", "Jira", token)

    @pytest.mark.asyncio
    async def test_unmarks_processing_on_error(self):
        svc = _make_service()
        svc._load_token_from_config = AsyncMock(side_effect=RuntimeError("fail"))
        await svc._refresh_connector_token("c1", "Jira")
        assert svc._is_connector_being_processed("c1") is False

    @pytest.mark.asyncio
    async def test_handles_recursion_error(self):
        svc = _make_service()
        svc._load_token_from_config = AsyncMock(side_effect=RecursionError("too deep"))
        await svc._refresh_connector_token("c1", "Jira")
        assert svc._is_connector_being_processed("c1") is False


# ---------------------------------------------------------------------------
# _handle_token_refresh_workflow
# ---------------------------------------------------------------------------

class TestHandleTokenRefreshWorkflow:
    @pytest.mark.asyncio
    async def test_not_expired_schedules_refresh(self):
        svc = _make_service()
        token = _make_token(expires_in=7200)
        svc.schedule_token_refresh = AsyncMock()
        svc._perform_token_refresh = AsyncMock()

        await svc._handle_token_refresh_workflow("c1", "Jira", token)
        svc.schedule_token_refresh.assert_awaited_once()
        svc._perform_token_refresh.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_expired_refreshes_and_schedules(self):
        svc = _make_service()
        token = _make_expired_token()
        new_token = _make_token(expires_in=7200)
        svc._perform_token_refresh = AsyncMock(return_value=new_token)
        svc.schedule_token_refresh = AsyncMock()

        await svc._handle_token_refresh_workflow("c1", "Jira", token)
        svc._perform_token_refresh.assert_awaited_once_with("c1", "Jira", token.refresh_token)
        svc.schedule_token_refresh.assert_awaited_once_with("c1", "Jira", new_token)


# ---------------------------------------------------------------------------
# schedule_token_refresh
# ---------------------------------------------------------------------------

class TestScheduleTokenRefresh:
    @pytest.mark.asyncio
    async def test_no_expires_in_returns(self):
        svc = _make_service()
        token = _make_token(expires_in=None)
        svc._cancel_existing_refresh_task = MagicMock()
        await svc.schedule_token_refresh("c1", "Jira", token)
        svc._cancel_existing_refresh_task.assert_not_called()

    @pytest.mark.asyncio
    async def test_future_token_schedules_task(self):
        svc = _make_service()
        svc._running = True
        token = _make_token(expires_in=7200)
        svc._cancel_existing_refresh_task = MagicMock()
        svc._create_refresh_task = MagicMock(return_value=True)

        await svc.schedule_token_refresh("c1", "Jira", token)
        svc._cancel_existing_refresh_task.assert_called_once_with("c1")
        svc._create_refresh_task.assert_called_once()

    @pytest.mark.asyncio
    async def test_immediate_refresh_needed(self):
        svc = _make_service()
        svc._running = True
        expired_token = _make_expired_token()
        new_token = _make_token(expires_in=7200)
        svc._refresh_token_immediately = AsyncMock(return_value=(new_token, True))
        svc._cancel_existing_refresh_task = MagicMock()
        svc._create_refresh_task = MagicMock(return_value=True)

        await svc.schedule_token_refresh("c1", "Jira", expired_token)
        svc._refresh_token_immediately.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_immediate_refresh_fails_returns(self):
        svc = _make_service()
        svc._running = True
        expired_token = _make_expired_token()
        svc._refresh_token_immediately = AsyncMock(return_value=(None, False))
        svc._create_refresh_task = MagicMock()

        await svc.schedule_token_refresh("c1", "Jira", expired_token)
        svc._create_refresh_task.assert_not_called()

    @pytest.mark.asyncio
    async def test_new_token_also_expired_returns(self):
        svc = _make_service()
        svc._running = True
        expired_token = _make_expired_token()
        also_expired = _make_expired_token()
        svc._refresh_token_immediately = AsyncMock(return_value=(also_expired, True))
        svc._create_refresh_task = MagicMock()

        await svc.schedule_token_refresh("c1", "Jira", expired_token)
        svc._create_refresh_task.assert_not_called()


# ---------------------------------------------------------------------------
# _refresh_token_immediately
# ---------------------------------------------------------------------------

class TestRefreshTokenImmediately:
    @pytest.mark.asyncio
    async def test_success(self):
        svc = _make_service()
        new_token = _make_token()
        svc._perform_token_refresh = AsyncMock(return_value=new_token)
        token = _make_token()
        result_token, success = await svc._refresh_token_immediately("c1", "Jira", token)
        assert success is True
        assert result_token is new_token

    @pytest.mark.asyncio
    async def test_failure(self):
        svc = _make_service()
        svc._perform_token_refresh = AsyncMock(side_effect=RuntimeError("refresh failed"))
        token = _make_token()
        result_token, success = await svc._refresh_token_immediately("c1", "Jira", token)
        assert success is False
        assert result_token is None


# ---------------------------------------------------------------------------
# Helper methods: _extract_scopes, _extract_credentials, etc.
# ---------------------------------------------------------------------------

class TestHelperMethods:
    def test_extract_scopes_dict_personal(self):
        svc = _make_service()
        config = {"scopes": {"personal_sync": ["read"], "team_sync": ["admin"]}}
        result = svc._extract_scopes(config, "personal")
        assert result == ["read"]

    def test_extract_scopes_dict_team(self):
        svc = _make_service()
        config = {"scopes": {"personal_sync": ["read"], "team_sync": ["admin"]}}
        result = svc._extract_scopes(config, "team")
        assert result == ["admin"]

    def test_extract_scopes_dict_agent(self):
        svc = _make_service()
        config = {"scopes": {"agent": ["exec"]}}
        result = svc._extract_scopes(config, "agent")
        assert result == ["exec"]

    def test_extract_scopes_not_dict(self):
        svc = _make_service()
        config = {"scopes": ["scope1", "scope2"]}
        result = svc._extract_scopes(config, "personal")
        assert result == ["scope1", "scope2"]

    def test_extract_scopes_not_dict_not_list(self):
        svc = _make_service()
        config = {"scopes": "not_a_valid_type"}
        result = svc._extract_scopes(config, "personal")
        assert result == []

    def test_extract_scopes_missing(self):
        svc = _make_service()
        result = svc._extract_scopes({}, "personal")
        assert result == []

    def test_extract_credentials_success(self):
        svc = _make_service()
        config = {"config": {"clientId": "cid", "clientSecret": "csec"}}
        cid, csec = svc._extract_credentials_from_oauth_config(config)
        assert cid == "cid"
        assert csec == "csec"

    def test_extract_credentials_alternate_keys(self):
        svc = _make_service()
        config = {"config": {"client_id": "cid2", "client_secret": "csec2"}}
        cid, csec = svc._extract_credentials_from_oauth_config(config)
        assert cid == "cid2"
        assert csec == "csec2"

    def test_extract_credentials_no_config(self):
        svc = _make_service()
        cid, csec = svc._extract_credentials_from_oauth_config({})
        assert cid is None
        assert csec is None

    def test_extract_credentials_empty_config_key(self):
        svc = _make_service()
        cid, csec = svc._extract_credentials_from_oauth_config({"config": {}})
        assert cid is None
        assert csec is None

    def test_build_oauth_flow_from_auth_config_fills_missing(self):
        svc = _make_service()
        auth_config = {
            "authorizeUrl": "https://auth.example.com",
            "tokenUrl": "https://token.example.com",
            "redirectUri": "https://redirect.example.com",
            "scopes": ["read"],
        }
        base = {}
        result = svc._build_oauth_flow_from_auth_config(auth_config, base)
        assert result["authorizeUrl"] == "https://auth.example.com"
        assert result["tokenUrl"] == "https://token.example.com"
        assert result["redirectUri"] == "https://redirect.example.com"
        assert result["scopes"] == ["read"]

    def test_build_oauth_flow_from_auth_config_preserves_existing(self):
        svc = _make_service()
        auth_config = {
            "authorizeUrl": "https://new.auth",
        }
        base = {"authorizeUrl": "https://existing.auth"}
        result = svc._build_oauth_flow_from_auth_config(auth_config, base)
        assert result["authorizeUrl"] == "https://existing.auth"

    def test_build_oauth_flow_from_shared_config(self):
        svc = _make_service()
        svc._enrich_from_registry = MagicMock()
        shared = {
            "authorizeUrl": "https://auth",
            "tokenUrl": "https://token",
            "redirectUri": "https://redirect",
            "tokenAccessType": "offline",
            "additionalParams": {"prompt": "consent"},
            "scopes": {"personal_sync": ["read"], "team_sync": ["admin"]},
        }
        result = svc._build_oauth_flow_from_shared_config(shared, "team", "Jira")
        assert result["authorizeUrl"] == "https://auth"
        assert result["tokenAccessType"] == "offline"
        assert result["scopes"] == ["admin"]

    def test_enrich_from_registry_already_has_fields(self):
        svc = _make_service()
        config = {"tokenAccessType": "offline", "additionalParams": {"p": "v"}}
        svc._enrich_from_registry(config, "Jira")
        # Should not modify since both fields exist
        assert config["tokenAccessType"] == "offline"

    @patch("app.connectors.core.registry.oauth_config_registry.get_oauth_config_registry")
    def test_enrich_from_registry_adds_missing(self, mock_get_registry):
        svc = _make_service()
        mock_registry = MagicMock()
        mock_config = MagicMock()
        mock_config.token_access_type = "offline"
        mock_config.additional_params = {"prompt": "consent"}
        mock_registry.get_config.return_value = mock_config
        mock_get_registry.return_value = mock_registry

        config = {}
        svc._enrich_from_registry(config, "Jira")
        assert config["tokenAccessType"] == "offline"
        assert config["additionalParams"] == {"prompt": "consent"}

    @patch("app.connectors.core.registry.oauth_config_registry.get_oauth_config_registry")
    def test_enrich_from_registry_no_config_found(self, mock_get_registry):
        """When registry has no config for the connector type, nothing happens."""
        svc = _make_service()
        mock_registry = MagicMock()
        mock_registry.get_config.return_value = None
        mock_get_registry.return_value = mock_registry

        config = {}
        svc._enrich_from_registry(config, "Unknown")
        assert "tokenAccessType" not in config
        assert "additionalParams" not in config

    @patch("app.connectors.core.registry.oauth_config_registry.get_oauth_config_registry")
    def test_enrich_from_registry_only_adds_missing_fields(self, mock_get_registry):
        """Enrich only adds fields not already present."""
        svc = _make_service()
        mock_registry = MagicMock()
        mock_config = MagicMock()
        mock_config.token_access_type = "offline"
        mock_config.additional_params = {"prompt": "consent"}
        mock_registry.get_config.return_value = mock_config
        mock_get_registry.return_value = mock_registry

        # tokenAccessType already present, additionalParams missing
        config = {"tokenAccessType": "existing"}
        svc._enrich_from_registry(config, "Jira")
        # Should add additionalParams but not overwrite tokenAccessType
        assert config["tokenAccessType"] == "existing"
        assert config["additionalParams"] == {"prompt": "consent"}

    @patch("app.connectors.core.registry.oauth_config_registry.get_oauth_config_registry")
    def test_enrich_from_registry_exception_handled(self, mock_get_registry):
        """Exceptions during enrichment are handled gracefully."""
        svc = _make_service()
        mock_get_registry.side_effect = RuntimeError("import error")

        config = {}
        # Should not raise
        svc._enrich_from_registry(config, "Jira")
        assert config == {}


# ---------------------------------------------------------------------------
# _build_complete_oauth_config
# ---------------------------------------------------------------------------

class TestBuildCompleteOAuthConfig:
    @pytest.mark.asyncio
    async def test_with_shared_config(self):
        svc = _make_service()
        svc._fetch_shared_oauth_config = AsyncMock(return_value={
            "authorizeUrl": "https://auth",
            "tokenUrl": "https://token",
            "redirectUri": "https://redir",
            "config": {"clientId": "cid", "clientSecret": "csec"},
            "scopes": {"team_sync": ["admin"]},
        })
        svc._enrich_from_registry = MagicMock()

        auth_config = {"oauthConfigId": "oid1", "connectorScope": "team"}
        result = await svc._build_complete_oauth_config("c1", "Jira", auth_config)
        assert result["clientId"] == "cid"
        assert result["clientSecret"] == "csec"

    @pytest.mark.asyncio
    async def test_fallback_to_auth_config(self):
        svc = _make_service()
        svc._fetch_shared_oauth_config = AsyncMock(return_value={})

        auth_config = {
            "oauthConfigId": "oid1",
            "connectorScope": "team",
            "clientId": "fallback_cid",
            "clientSecret": "fallback_csec",
            "authorizeUrl": "https://fb_auth",
            "tokenUrl": "https://fb_token",
        }
        result = await svc._build_complete_oauth_config("c1", "Jira", auth_config)
        assert result["clientId"] == "fallback_cid"
        assert result["clientSecret"] == "fallback_csec"

    @pytest.mark.asyncio
    async def test_no_credentials_raises(self):
        svc = _make_service()
        svc._fetch_shared_oauth_config = AsyncMock(return_value={})

        auth_config = {"oauthConfigId": "oid1"}
        with pytest.raises(ValueError, match="No OAuth credentials found"):
            await svc._build_complete_oauth_config("c1", "Jira", auth_config)

    @pytest.mark.asyncio
    async def test_shared_config_missing_credentials_falls_back(self):
        svc = _make_service()
        svc._fetch_shared_oauth_config = AsyncMock(return_value={
            "authorizeUrl": "https://auth",
            "config": {},  # No client ID/secret
        })

        auth_config = {
            "oauthConfigId": "oid1",
            "clientId": "fb_cid",
            "clientSecret": "fb_csec",
        }
        result = await svc._build_complete_oauth_config("c1", "Jira", auth_config)
        assert result["clientId"] == "fb_cid"


# ---------------------------------------------------------------------------
# _fetch_shared_oauth_config
# ---------------------------------------------------------------------------

class TestFetchSharedOAuthConfig:
    @pytest.mark.asyncio
    async def test_found(self):
        cs = MagicMock()
        cs.get_config = AsyncMock(return_value=[
            {"_id": "oid1", "data": "config1"},
            {"_id": "oid2", "data": "config2"},
        ])
        svc = _make_service(config_service=cs)
        result = await svc._fetch_shared_oauth_config("oid1", "Jira")
        assert result["_id"] == "oid1"

    @pytest.mark.asyncio
    async def test_not_found_in_list(self):
        cs = MagicMock()
        cs.get_config = AsyncMock(return_value=[{"_id": "oid2"}])
        svc = _make_service(config_service=cs)
        result = await svc._fetch_shared_oauth_config("oid1", "Jira")
        assert result == {}

    @pytest.mark.asyncio
    async def test_no_configs(self):
        cs = MagicMock()
        cs.get_config = AsyncMock(return_value=None)
        svc = _make_service(config_service=cs)
        result = await svc._fetch_shared_oauth_config("oid1", "Jira")
        assert result == {}

    @pytest.mark.asyncio
    async def test_not_list(self):
        cs = MagicMock()
        cs.get_config = AsyncMock(return_value="not a list")
        svc = _make_service(config_service=cs)
        result = await svc._fetch_shared_oauth_config("oid1", "Jira")
        assert result == {}

    @pytest.mark.asyncio
    async def test_exception(self):
        cs = MagicMock()
        cs.get_config = AsyncMock(side_effect=RuntimeError("fail"))
        svc = _make_service(config_service=cs)
        result = await svc._fetch_shared_oauth_config("oid1", "Jira")
        assert result == {}


# ---------------------------------------------------------------------------
# _process_connectors_for_refresh
# ---------------------------------------------------------------------------

class TestProcessConnectorsForRefresh:
    @pytest.mark.asyncio
    async def test_deduplicates(self):
        svc = _make_service()
        svc._refresh_connector_token = AsyncMock()

        connectors = [
            {"_key": "c1", "type": "Jira"},
            {"_key": "c1", "type": "Jira"},  # duplicate
        ]
        await svc._process_connectors_for_refresh(connectors)
        svc._refresh_connector_token.assert_awaited_once_with("c1", "Jira")

    @pytest.mark.asyncio
    async def test_skips_no_key(self):
        svc = _make_service()
        svc._refresh_connector_token = AsyncMock()

        connectors = [{"type": "Jira"}]
        await svc._process_connectors_for_refresh(connectors)
        svc._refresh_connector_token.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_handles_error_gracefully(self):
        svc = _make_service()
        svc._refresh_connector_token = AsyncMock(side_effect=RuntimeError("fail"))

        connectors = [
            {"_key": "c1", "type": "Jira"},
            {"_key": "c2", "type": "Slack"},
        ]
        # Should not raise even when first connector fails
        await svc._process_connectors_for_refresh(connectors)
        assert svc._refresh_connector_token.await_count == 2


# ---------------------------------------------------------------------------
# refresh_connector_token (public)
# ---------------------------------------------------------------------------

class TestRefreshConnectorTokenPublic:
    @pytest.mark.asyncio
    async def test_delegates_to_private(self):
        svc = _make_service()
        svc._refresh_connector_token = AsyncMock()
        await svc.refresh_connector_token("c1", "Jira")
        svc._refresh_connector_token.assert_awaited_once_with("c1", "Jira")


# ---------------------------------------------------------------------------
# _create_refresh_task
# ---------------------------------------------------------------------------

class TestCreateRefreshTask:
    def test_success(self):
        svc = _make_service()
        with patch("asyncio.create_task") as mock_create:
            mock_task = MagicMock()
            mock_create.return_value = mock_task
            result = svc._create_refresh_task("c1", "Jira", 100.0, datetime.now())
            assert result is True
            assert svc._refresh_tasks["c1"] is mock_task

    def test_failure(self):
        svc = _make_service()
        with patch("asyncio.create_task", side_effect=RuntimeError("no loop")):
            result = svc._create_refresh_task("c1", "Jira", 100.0, datetime.now())
            assert result is False
            assert "c1" not in svc._refresh_tasks


# ---------------------------------------------------------------------------
# _refresh_all_tokens_internal
# ---------------------------------------------------------------------------

class TestRefreshAllTokensInternal:
    @pytest.mark.asyncio
    async def test_success(self):
        gp = MagicMock()
        gp.get_all_documents = AsyncMock(return_value=[
            {"_key": "c1", "authType": "OAUTH"},
        ])
        cs = MagicMock()
        cs.get_config = AsyncMock(return_value={
            "credentials": {"refresh_token": "rt"}
        })
        svc = _make_service(config_service=cs, graph_provider=gp)
        svc._process_connectors_for_refresh = AsyncMock()
        await svc._refresh_all_tokens_internal()
        svc._process_connectors_for_refresh.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_exception_handled(self):
        gp = MagicMock()
        gp.get_all_documents = AsyncMock(side_effect=RuntimeError("db down"))
        svc = _make_service(graph_provider=gp)
        # Should not raise
        await svc._refresh_all_tokens_internal()


# ---------------------------------------------------------------------------
# _delayed_refresh
# ---------------------------------------------------------------------------

class TestDelayedRefresh:
    @pytest.mark.asyncio
    async def test_delayed_refresh_calls_refresh(self):
        svc = _make_service()
        svc._refresh_connector_token = AsyncMock()
        # Use delay=0 so it runs immediately
        await svc._delayed_refresh("c1", "Jira", 0)
        svc._refresh_connector_token.assert_awaited_once_with("c1", "Jira")

    @pytest.mark.asyncio
    async def test_delayed_refresh_error_handled(self):
        svc = _make_service()
        svc._refresh_connector_token = AsyncMock(side_effect=RuntimeError("fail"))
        # Should not raise
        await svc._delayed_refresh("c1", "Jira", 0)

    @pytest.mark.asyncio
    async def test_delayed_refresh_cleanup_on_done(self):
        svc = _make_service()
        svc._refresh_connector_token = AsyncMock()
        mock_task = MagicMock()
        mock_task.done.return_value = True
        svc._refresh_tasks["c1"] = mock_task
        await svc._delayed_refresh("c1", "Jira", 0)
        assert "c1" not in svc._refresh_tasks


# ---------------------------------------------------------------------------
# _build_complete_oauth_config - no oauthConfigId
# ---------------------------------------------------------------------------

class TestBuildCompleteOAuthConfigNoShared:
    @pytest.mark.asyncio
    async def test_no_oauth_config_id_uses_auth_config(self):
        svc = _make_service()
        auth_config = {
            "clientId": "cid",
            "clientSecret": "csec",
            "authorizeUrl": "https://auth",
            "tokenUrl": "https://token",
        }
        result = await svc._build_complete_oauth_config("c1", "Jira", auth_config)
        assert result["clientId"] == "cid"
        assert result["clientSecret"] == "csec"


# ---------------------------------------------------------------------------
# _extract_scopes edge cases
# ---------------------------------------------------------------------------

class TestExtractScopesEdgeCases:
    def test_scope_returns_non_list_value(self):
        """When scope value is not a list, it's returned as-is."""
        svc = _make_service()
        config = {"scopes": {"personal_sync": "single_scope"}}
        result = svc._extract_scopes(config, "personal")
        # The code does: `return scope_list if isinstance(scope_list, list) else []`
        # So a non-list value returns empty list
        assert result == []

    def test_default_scope_key_map(self):
        """Unknown connector scope defaults to 'team_sync'."""
        svc = _make_service()
        config = {"scopes": {"team_sync": ["default_scope"]}}
        result = svc._extract_scopes(config, "unknown_scope")
        assert result == ["default_scope"]
