"""Unit tests for app.utils.oauth_config."""

from unittest.mock import AsyncMock, MagicMock

import pytest
import pytest_asyncio

from app.utils.oauth_config import fetch_oauth_config_by_id, get_oauth_config


# ===================================================================
# get_oauth_config
# ===================================================================
class TestGetOAuthConfig:
    def test_full_config(self):
        auth = {
            "clientId": "my-client",
            "clientSecret": "my-secret",
            "redirectUri": "https://example.com/callback",
            "authorizeUrl": "https://provider.com/authorize",
            "tokenUrl": "https://provider.com/token",
            "scopes": ["read", "write"],
            "tokenAccessType": "offline",
            "additionalParams": {"param1": "val1"},
            "scopeParameterName": "user_scope",
            "tokenResponsePath": "authed_user",
        }
        result = get_oauth_config(auth)
        assert result.client_id == "my-client"
        assert result.client_secret == "my-secret"
        assert result.redirect_uri == "https://example.com/callback"
        assert result.authorize_url == "https://provider.com/authorize"
        assert result.token_url == "https://provider.com/token"
        assert result.scope == "read write"
        assert result.token_access_type == "offline"
        assert result.additional_params["param1"] == "val1"
        assert result.scope_parameter_name == "user_scope"
        assert result.token_response_path == "authed_user"

    def test_minimal_config(self):
        auth = {
            "clientId": "c",
            "clientSecret": "s",
        }
        result = get_oauth_config(auth)
        assert result.client_id == "c"
        assert result.client_secret == "s"
        assert result.redirect_uri == ""
        assert result.authorize_url == ""
        assert result.token_url == ""
        assert result.scope == ""
        assert result.additional_params == {}

    def test_scopes_joined_with_space(self):
        auth = {
            "clientId": "c",
            "clientSecret": "s",
            "scopes": ["a", "b", "c"],
        }
        result = get_oauth_config(auth)
        assert result.scope == "a b c"

    def test_empty_scopes(self):
        auth = {
            "clientId": "c",
            "clientSecret": "s",
            "scopes": [],
        }
        result = get_oauth_config(auth)
        assert result.scope == ""

    def test_no_scopes_key(self):
        auth = {
            "clientId": "c",
            "clientSecret": "s",
        }
        result = get_oauth_config(auth)
        assert result.scope == ""

    def test_notion_detection_exact_domain(self):
        auth = {
            "clientId": "c",
            "clientSecret": "s",
            "tokenUrl": "https://api.notion.com/v1/oauth/token",
        }
        result = get_oauth_config(auth)
        assert result.additional_params.get("use_basic_auth") is True
        assert result.additional_params.get("use_json_body") is True
        assert result.additional_params.get("notion_version") == "2025-09-03"

    def test_notion_detection_subdomain(self):
        auth = {
            "clientId": "c",
            "clientSecret": "s",
            "tokenUrl": "https://api.notion.com/v1/oauth/token",
        }
        result = get_oauth_config(auth)
        assert result.additional_params.get("use_basic_auth") is True

    def test_non_notion_url_no_basic_auth(self):
        auth = {
            "clientId": "c",
            "clientSecret": "s",
            "tokenUrl": "https://accounts.google.com/o/oauth2/token",
        }
        result = get_oauth_config(auth)
        assert result.additional_params.get("use_basic_auth") is None

    def test_malicious_domain_not_detected_as_notion(self):
        """Domains like evilnotion.com should NOT trigger Notion detection."""
        auth = {
            "clientId": "c",
            "clientSecret": "s",
            "tokenUrl": "https://evilnotion.com/token",
        }
        result = get_oauth_config(auth)
        assert result.additional_params.get("use_basic_auth") is None

    def test_malicious_prefix_domain_not_detected(self):
        """notion.com.evil.com should NOT trigger Notion detection."""
        auth = {
            "clientId": "c",
            "clientSecret": "s",
            "tokenUrl": "https://notion.com.evil.com/token",
        }
        result = get_oauth_config(auth)
        assert result.additional_params.get("use_basic_auth") is None

    def test_no_token_url(self):
        auth = {
            "clientId": "c",
            "clientSecret": "s",
        }
        result = get_oauth_config(auth)
        # Should not crash, no Notion detection
        assert result.additional_params == {}

    def test_additional_params_default_empty_dict(self):
        auth = {
            "clientId": "c",
            "clientSecret": "s",
        }
        result = get_oauth_config(auth)
        assert result.additional_params == {}

    def test_token_access_type_not_set_when_missing(self):
        auth = {
            "clientId": "c",
            "clientSecret": "s",
        }
        result = get_oauth_config(auth)
        # token_access_type should be None (the default from OAuthConfig)
        assert result.token_access_type is None

    def test_scope_parameter_name_not_set_when_missing(self):
        auth = {
            "clientId": "c",
            "clientSecret": "s",
        }
        result = get_oauth_config(auth)
        # Default is "scope" from the OAuthConfig dataclass
        assert result.scope_parameter_name == "scope"

    def test_token_response_path_not_set_when_missing(self):
        auth = {
            "clientId": "c",
            "clientSecret": "s",
        }
        result = get_oauth_config(auth)
        assert result.token_response_path is None

    def test_invalid_token_url_parsing_handled(self):
        """Even a garbage token URL should not crash."""
        auth = {
            "clientId": "c",
            "clientSecret": "s",
            "tokenUrl": "not-a-valid-url",
        }
        result = get_oauth_config(auth)
        # Should not crash; notion detection should not trigger
        assert result.client_id == "c"


# ===================================================================
# fetch_oauth_config_by_id
# ===================================================================
class TestFetchOAuthConfigById:
    @pytest.mark.asyncio
    async def test_found(self):
        config_service = AsyncMock()
        config_service.get_config.return_value = [
            {"_id": "cfg-1", "config": {"clientId": "c1"}},
            {"_id": "cfg-2", "config": {"clientId": "c2"}},
        ]
        result = await fetch_oauth_config_by_id(
            oauth_config_id="cfg-2",
            connector_type="GOOGLE_DRIVE",
            config_service=config_service,
        )
        assert result is not None
        assert result["_id"] == "cfg-2"

    @pytest.mark.asyncio
    async def test_not_found(self):
        config_service = AsyncMock()
        config_service.get_config.return_value = [
            {"_id": "cfg-1", "config": {"clientId": "c1"}},
        ]
        logger = MagicMock()
        result = await fetch_oauth_config_by_id(
            oauth_config_id="nonexistent",
            connector_type="GOOGLE_DRIVE",
            config_service=config_service,
            logger=logger,
        )
        assert result is None
        logger.warning.assert_called()

    @pytest.mark.asyncio
    async def test_empty_oauth_config_id(self):
        config_service = AsyncMock()
        logger = MagicMock()
        result = await fetch_oauth_config_by_id(
            oauth_config_id="",
            connector_type="GOOGLE_DRIVE",
            config_service=config_service,
            logger=logger,
        )
        assert result is None
        logger.warning.assert_called()

    @pytest.mark.asyncio
    async def test_empty_connector_type(self):
        config_service = AsyncMock()
        logger = MagicMock()
        result = await fetch_oauth_config_by_id(
            oauth_config_id="cfg-1",
            connector_type="",
            config_service=config_service,
            logger=logger,
        )
        assert result is None

    @pytest.mark.asyncio
    async def test_none_oauth_config_id(self):
        config_service = AsyncMock()
        result = await fetch_oauth_config_by_id(
            oauth_config_id=None,
            connector_type="GOOGLE_DRIVE",
            config_service=config_service,
        )
        assert result is None

    @pytest.mark.asyncio
    async def test_config_service_returns_non_list(self):
        config_service = AsyncMock()
        config_service.get_config.return_value = "not-a-list"
        logger = MagicMock()
        result = await fetch_oauth_config_by_id(
            oauth_config_id="cfg-1",
            connector_type="GOOGLE_DRIVE",
            config_service=config_service,
            logger=logger,
        )
        assert result is None
        logger.warning.assert_called()

    @pytest.mark.asyncio
    async def test_config_service_raises_exception(self):
        config_service = AsyncMock()
        config_service.get_config.side_effect = RuntimeError("network error")
        logger = MagicMock()
        result = await fetch_oauth_config_by_id(
            oauth_config_id="cfg-1",
            connector_type="GOOGLE_DRIVE",
            config_service=config_service,
            logger=logger,
        )
        assert result is None
        logger.error.assert_called()

    @pytest.mark.asyncio
    async def test_no_logger_no_crash_on_warning(self):
        config_service = AsyncMock()
        config_service.get_config.return_value = []
        result = await fetch_oauth_config_by_id(
            oauth_config_id="cfg-1",
            connector_type="GOOGLE_DRIVE",
            config_service=config_service,
            logger=None,
        )
        assert result is None

    @pytest.mark.asyncio
    async def test_normalized_config_path(self):
        config_service = AsyncMock()
        config_service.get_config.return_value = [
            {"_id": "cfg-1", "config": {"clientId": "c1"}},
        ]
        await fetch_oauth_config_by_id(
            oauth_config_id="cfg-1",
            connector_type="DROPBOX_PERSONAL",
            config_service=config_service,
        )
        # Verify the path used to fetch config
        call_args = config_service.get_config.call_args
        expected_path = "/services/oauth/dropbox_personal"
        assert call_args[0][0] == expected_path

    @pytest.mark.asyncio
    async def test_connector_type_with_spaces_normalized(self):
        config_service = AsyncMock()
        config_service.get_config.return_value = []
        await fetch_oauth_config_by_id(
            oauth_config_id="cfg-1",
            connector_type="GOOGLE DRIVE",
            config_service=config_service,
        )
        call_args = config_service.get_config.call_args
        expected_path = "/services/oauth/googledrive"
        assert call_args[0][0] == expected_path

    @pytest.mark.asyncio
    async def test_no_logger_no_crash_on_error(self):
        config_service = AsyncMock()
        config_service.get_config.side_effect = RuntimeError("boom")
        result = await fetch_oauth_config_by_id(
            oauth_config_id="cfg-1",
            connector_type="SLACK",
            config_service=config_service,
            logger=None,
        )
        assert result is None

    @pytest.mark.asyncio
    async def test_config_service_returns_empty_list(self):
        config_service = AsyncMock()
        config_service.get_config.return_value = []
        logger = MagicMock()
        result = await fetch_oauth_config_by_id(
            oauth_config_id="cfg-1",
            connector_type="SLACK",
            config_service=config_service,
            logger=logger,
        )
        assert result is None
        logger.warning.assert_called()
