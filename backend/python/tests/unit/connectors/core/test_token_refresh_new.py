"""Tests targeting uncovered lines in token_refresh_service.py:
- _send_app_disabled_event (lines 591-634)
- _mark_connector_unauthenticated (lines 557-589)
- _handle_refresh_token_invalid consecutive failure counter (lines 536-555)
- refresh_now public entry point (lines 521-534)
- _refresh_token_immediately with RefreshTokenInvalidError (lines 860-862)
- set_messaging_producer (lines 56-58)
- start with wait_for_initial_refresh=False (line 80)
- _enrich_from_registry with registry hit (lines 256-307)
- _build_oauth_flow_from_auth_config with instanceUrl (lines 421-440)
"""

import asyncio
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.connectors.core.base.token_service.oauth_service import (
    OAuthToken,
    RefreshTokenInvalidError,
)
from app.connectors.core.base.token_service.token_refresh_service import (
    MAX_REFRESH_TOKEN_INVALID_FAILURES,
    TokenRefreshService,
)
from app.connectors.core.constants import (
    AuthFieldKeys,
    OAuthConfigKeys,
)


def _make_service(config_service=None, graph_provider=None, messaging_producer=None):
    cs = config_service or MagicMock()
    gp = graph_provider or MagicMock()
    return TokenRefreshService(cs, gp, messaging_producer)


def _make_token(access_token="at", refresh_token="rt", expires_in=3600, created_at=None):
    return OAuthToken(
        access_token=access_token,
        refresh_token=refresh_token,
        expires_in=expires_in,
        created_at=created_at or datetime.now(),
    )


# ---------------------------------------------------------------------------
# set_messaging_producer
# ---------------------------------------------------------------------------


class TestSetMessagingProducer:
    def test_sets_producer(self):
        svc = _make_service()
        producer = MagicMock()
        svc.set_messaging_producer(producer)
        assert svc._messaging_producer is producer


# ---------------------------------------------------------------------------
# start with wait_for_initial_refresh=False
# ---------------------------------------------------------------------------


class TestStartNoWait:
    @pytest.mark.asyncio
    async def test_background_initial_refresh(self):
        svc = _make_service()
        svc._refresh_all_tokens = AsyncMock()
        svc._periodic_refresh_check = AsyncMock()
        with patch("asyncio.create_task") as mock_create:
            await svc.start(wait_for_initial_refresh=False)
        assert svc._running is True
        svc._refresh_all_tokens.assert_not_awaited()
        assert mock_create.call_count == 2


# ---------------------------------------------------------------------------
# _send_app_disabled_event
# ---------------------------------------------------------------------------


class TestSendAppDisabledEvent:
    @pytest.mark.asyncio
    async def test_no_producer_returns_false(self):
        svc = _make_service(messaging_producer=None)
        result = await svc._send_app_disabled_event("conn1")
        assert result is False

    @pytest.mark.asyncio
    async def test_no_app_doc_returns_false(self):
        gp = AsyncMock()
        gp.get_document = AsyncMock(return_value=None)
        producer = AsyncMock()
        svc = _make_service(graph_provider=gp, messaging_producer=producer)
        result = await svc._send_app_disabled_event("conn1")
        assert result is False

    @pytest.mark.asyncio
    async def test_org_id_from_app_doc_sends_message(self):
        gp = AsyncMock()
        gp.get_document = AsyncMock(return_value={
            "orgId": "org1",
            "appGroup": "google",
            "type": "Google Drive",
            "scope": "team",
        })
        producer = AsyncMock()
        producer.send_message = AsyncMock()
        svc = _make_service(graph_provider=gp, messaging_producer=producer)

        result = await svc._send_app_disabled_event("conn1")
        assert result is True
        producer.send_message.assert_awaited_once()
        call_kwargs = producer.send_message.call_args
        assert call_kwargs[1]["topic"] == "entity-events"
        msg = call_kwargs[1]["message"]
        assert msg["eventType"] == "appDisabled"
        assert msg["payload"]["orgId"] == "org1"
        assert msg["payload"]["apps"] == ["googledrive"]

    @pytest.mark.asyncio
    async def test_org_id_from_edges_fallback(self):
        gp = AsyncMock()
        gp.get_document = AsyncMock(return_value={
            "appGroup": "jira",
            "type": "Jira",
            "scope": "personal",
        })
        gp.get_edges_to_node = AsyncMock(return_value=[
            {"_from": "organizations/org-from-edge"}
        ])
        producer = AsyncMock()
        producer.send_message = AsyncMock()
        svc = _make_service(graph_provider=gp, messaging_producer=producer)

        result = await svc._send_app_disabled_event("conn1")
        assert result is True
        msg = producer.send_message.call_args[1]["message"]
        assert msg["payload"]["orgId"] == "org-from-edge"

    @pytest.mark.asyncio
    async def test_org_id_edges_empty_still_sends(self):
        gp = AsyncMock()
        gp.get_document = AsyncMock(return_value={
            "appGroup": "slack",
            "type": "Slack",
            "scope": "team",
        })
        gp.get_edges_to_node = AsyncMock(return_value=[])
        producer = AsyncMock()
        producer.send_message = AsyncMock()
        svc = _make_service(graph_provider=gp, messaging_producer=producer)

        result = await svc._send_app_disabled_event("conn1")
        assert result is True
        msg = producer.send_message.call_args[1]["message"]
        assert msg["payload"]["orgId"] is None

    @pytest.mark.asyncio
    async def test_send_message_exception_returns_false(self):
        gp = AsyncMock()
        gp.get_document = AsyncMock(return_value={
            "orgId": "org1",
            "appGroup": "google",
            "type": "Google Drive",
            "scope": "team",
        })
        producer = AsyncMock()
        producer.send_message = AsyncMock(side_effect=RuntimeError("kafka down"))
        svc = _make_service(graph_provider=gp, messaging_producer=producer)

        result = await svc._send_app_disabled_event("conn1")
        assert result is False


# ---------------------------------------------------------------------------
# _mark_connector_unauthenticated
# ---------------------------------------------------------------------------


class TestMarkConnectorUnauthenticated:
    @pytest.mark.asyncio
    async def test_cancels_existing_task_and_updates_node(self):
        gp = AsyncMock()
        gp.update_node = AsyncMock(return_value=True)
        svc = _make_service(graph_provider=gp)
        svc._send_app_disabled_event = AsyncMock(return_value=True)

        mock_task = MagicMock()
        mock_task.done.return_value = False
        svc._refresh_tasks["conn1"] = mock_task

        await svc._mark_connector_unauthenticated("conn1")

        mock_task.cancel.assert_called_once()
        gp.update_node.assert_awaited_once()
        call_args = gp.update_node.call_args
        updates = call_args[0][2]
        assert updates["isAuthenticated"] is False
        assert "isActive" not in updates

    @pytest.mark.asyncio
    async def test_app_disabled_fails_sets_is_active_false(self):
        gp = AsyncMock()
        gp.update_node = AsyncMock(return_value=True)
        svc = _make_service(graph_provider=gp)
        svc._send_app_disabled_event = AsyncMock(return_value=False)

        await svc._mark_connector_unauthenticated("conn1")

        call_args = gp.update_node.call_args
        updates = call_args[0][2]
        assert updates["isActive"] is False

    @pytest.mark.asyncio
    async def test_update_node_returns_falsy_does_not_raise(self):
        gp = AsyncMock()
        gp.update_node = AsyncMock(return_value=None)
        svc = _make_service(graph_provider=gp)
        svc._send_app_disabled_event = AsyncMock(return_value=True)

        await svc._mark_connector_unauthenticated("conn1")
        gp.update_node.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_update_node_exception_does_not_raise(self):
        gp = AsyncMock()
        gp.update_node = AsyncMock(side_effect=RuntimeError("db fail"))
        svc = _make_service(graph_provider=gp)
        svc._send_app_disabled_event = AsyncMock(return_value=True)

        await svc._mark_connector_unauthenticated("conn1")
        gp.update_node.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_no_existing_task_does_not_cancel(self):
        gp = AsyncMock()
        gp.update_node = AsyncMock(return_value=True)
        svc = _make_service(graph_provider=gp)
        svc._send_app_disabled_event = AsyncMock(return_value=True)
        svc._cancel_existing_refresh_task = MagicMock()

        await svc._mark_connector_unauthenticated("conn1")
        svc._cancel_existing_refresh_task.assert_not_called()


# ---------------------------------------------------------------------------
# _handle_refresh_token_invalid
# ---------------------------------------------------------------------------


class TestHandleRefreshTokenInvalid:
    @pytest.mark.asyncio
    async def test_below_threshold_does_not_deactivate(self):
        svc = _make_service()
        svc._mark_connector_unauthenticated = AsyncMock()

        error = RefreshTokenInvalidError("token revoked")
        await svc._handle_refresh_token_invalid("conn1", error)

        assert svc._invalid_refresh_failures["conn1"] == 1
        svc._mark_connector_unauthenticated.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_increments_counter_each_call(self):
        svc = _make_service()
        svc._mark_connector_unauthenticated = AsyncMock()
        error = RefreshTokenInvalidError("rejected")

        for i in range(MAX_REFRESH_TOKEN_INVALID_FAILURES - 1):
            await svc._handle_refresh_token_invalid("conn1", error)

        assert svc._invalid_refresh_failures["conn1"] == MAX_REFRESH_TOKEN_INVALID_FAILURES - 1
        svc._mark_connector_unauthenticated.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_at_threshold_deactivates(self):
        svc = _make_service()
        svc._mark_connector_unauthenticated = AsyncMock()
        error = RefreshTokenInvalidError("rejected")

        for _ in range(MAX_REFRESH_TOKEN_INVALID_FAILURES):
            await svc._handle_refresh_token_invalid("conn1", error)

        svc._mark_connector_unauthenticated.assert_awaited_once_with("conn1")
        assert "conn1" not in svc._invalid_refresh_failures


# ---------------------------------------------------------------------------
# refresh_now
# ---------------------------------------------------------------------------


class TestRefreshNow:
    @pytest.mark.asyncio
    async def test_success_returns_token(self):
        svc = _make_service()
        token = _make_token()
        svc._perform_token_refresh = AsyncMock(return_value=token)

        result = await svc.refresh_now("conn1", "Drive", "rt")
        assert result is token
        svc._perform_token_refresh.assert_awaited_once_with("conn1", "Drive", "rt")

    @pytest.mark.asyncio
    async def test_refresh_token_invalid_calls_handler_and_reraises(self):
        svc = _make_service()
        error = RefreshTokenInvalidError("permanently revoked")
        svc._perform_token_refresh = AsyncMock(side_effect=error)
        svc._handle_refresh_token_invalid = AsyncMock()

        with pytest.raises(RefreshTokenInvalidError, match="permanently revoked"):
            await svc.refresh_now("conn1", "Drive", "rt")

        svc._handle_refresh_token_invalid.assert_awaited_once_with("conn1", error, "rt")


# ---------------------------------------------------------------------------
# _refresh_token_immediately with RefreshTokenInvalidError
# ---------------------------------------------------------------------------


class TestRefreshTokenImmediatelyInvalid:
    @pytest.mark.asyncio
    async def test_refresh_token_invalid_handled(self):
        svc = _make_service()
        token = _make_token()
        error = RefreshTokenInvalidError("expired refresh")
        svc._perform_token_refresh = AsyncMock(side_effect=error)
        svc._handle_refresh_token_invalid = AsyncMock()

        result_token, success = await svc._refresh_token_immediately("conn1", "Jira", token)
        assert success is False
        assert result_token is None
        svc._handle_refresh_token_invalid.assert_awaited_once_with("conn1", error, "rt")


# ---------------------------------------------------------------------------
# _enrich_from_registry
# ---------------------------------------------------------------------------


class TestEnrichFromRegistry:
    def test_all_fields_present_skips_enrichment(self):
        svc = _make_service()
        config = {
            OAuthConfigKeys.TOKEN_ACCESS_TYPE: "offline",
            OAuthConfigKeys.ADDITIONAL_PARAMS: {},
            OAuthConfigKeys.SCOPE_PARAMETER_NAME: "scope",
            OAuthConfigKeys.TOKEN_RESPONSE_PATH: ".",
        }
        svc._enrich_from_registry(config, "GoogleDrive")
        assert config[OAuthConfigKeys.TOKEN_ACCESS_TYPE] == "offline"

    def test_enriches_missing_fields_from_registry(self):
        svc = _make_service()
        config = {}

        mock_registry_config = MagicMock()
        mock_registry_config.token_access_type = "offline"
        mock_registry_config.additional_params = {"prompt": "consent"}
        mock_registry_config.scope_parameter_name = "scp"
        mock_registry_config.token_response_path = "data.token"

        mock_registry = MagicMock()
        mock_registry.get_config.return_value = mock_registry_config

        with patch(
            "app.connectors.core.registry.oauth_config_registry.get_oauth_config_registry",
            return_value=mock_registry,
        ):
            svc._enrich_from_registry(config, "GoogleDrive")

        assert config[OAuthConfigKeys.TOKEN_ACCESS_TYPE] == "offline"
        assert config[OAuthConfigKeys.ADDITIONAL_PARAMS] == {"prompt": "consent"}
        assert config[OAuthConfigKeys.SCOPE_PARAMETER_NAME] == "scp"
        assert config[OAuthConfigKeys.TOKEN_RESPONSE_PATH] == "data.token"

    def test_no_registry_config_returns_without_changes(self):
        svc = _make_service()
        config = {}

        mock_registry = MagicMock()
        mock_registry.get_config.return_value = None

        with patch(
            "app.connectors.core.registry.oauth_config_registry.get_oauth_config_registry",
            return_value=mock_registry,
        ):
            svc._enrich_from_registry(config, "UnknownType")

        assert OAuthConfigKeys.TOKEN_ACCESS_TYPE not in config

    def test_registry_import_exception_handled(self):
        svc = _make_service()
        config = {}

        with patch(
            "app.connectors.core.registry.oauth_config_registry.get_oauth_config_registry",
            side_effect=ImportError("no module"),
        ):
            svc._enrich_from_registry(config, "GoogleDrive")

        assert OAuthConfigKeys.TOKEN_ACCESS_TYPE not in config

    def test_scope_parameter_name_default_not_added(self):
        svc = _make_service()
        config = {}

        mock_registry_config = MagicMock()
        mock_registry_config.token_access_type = None
        mock_registry_config.additional_params = None
        mock_registry_config.scope_parameter_name = "scope"
        mock_registry_config.token_response_path = None

        mock_registry = MagicMock()
        mock_registry.get_config.return_value = mock_registry_config

        with patch(
            "app.connectors.core.registry.oauth_config_registry.get_oauth_config_registry",
            return_value=mock_registry,
        ):
            svc._enrich_from_registry(config, "GoogleDrive")

        assert OAuthConfigKeys.SCOPE_PARAMETER_NAME not in config


# ---------------------------------------------------------------------------
# _build_oauth_flow_from_auth_config with instanceUrl
# ---------------------------------------------------------------------------


class TestBuildOAuthFlowFromAuthConfig:
    def test_fills_from_auth_config(self):
        svc = _make_service()
        auth_config = {
            AuthFieldKeys.AUTHORIZE_URL: "https://example.com/authorize",
            AuthFieldKeys.TOKEN_URL: "https://example.com/token",
            AuthFieldKeys.REDIRECT_URI: "https://app.com/callback",
            OAuthConfigKeys.SCOPES: ["read", "write"],
        }
        result = svc._build_oauth_flow_from_auth_config(auth_config, {})
        assert result[AuthFieldKeys.AUTHORIZE_URL] == "https://example.com/authorize"
        assert result[AuthFieldKeys.TOKEN_URL] == "https://example.com/token"
        assert result[AuthFieldKeys.REDIRECT_URI] == "https://app.com/callback"
        assert result[OAuthConfigKeys.SCOPES] == ["read", "write"]

    def test_instance_url_derived_endpoints(self):
        svc = _make_service()
        auth_config = {
            "instanceUrl": "https://gitlab.mycompany.com/",
        }
        result = svc._build_oauth_flow_from_auth_config(auth_config, {})
        assert result[AuthFieldKeys.AUTHORIZE_URL] == "https://gitlab.mycompany.com/oauth/authorize"
        assert result[AuthFieldKeys.TOKEN_URL] == "https://gitlab.mycompany.com/oauth/token"

    def test_does_not_override_existing_values(self):
        svc = _make_service()
        auth_config = {
            AuthFieldKeys.AUTHORIZE_URL: "https://override.com/auth",
            AuthFieldKeys.TOKEN_URL: "https://override.com/token",
        }
        base_config = {
            AuthFieldKeys.AUTHORIZE_URL: "https://existing.com/auth",
            AuthFieldKeys.TOKEN_URL: "https://existing.com/token",
        }
        result = svc._build_oauth_flow_from_auth_config(auth_config, base_config)
        assert result[AuthFieldKeys.AUTHORIZE_URL] == "https://existing.com/auth"
        assert result[AuthFieldKeys.TOKEN_URL] == "https://existing.com/token"

    def test_no_instance_url_empty_strings(self):
        svc = _make_service()
        auth_config = {}
        result = svc._build_oauth_flow_from_auth_config(auth_config, {})
        assert result[AuthFieldKeys.AUTHORIZE_URL] == ""
        assert result[AuthFieldKeys.TOKEN_URL] == ""
        assert result[AuthFieldKeys.REDIRECT_URI] == ""
        assert result[OAuthConfigKeys.SCOPES] == []
