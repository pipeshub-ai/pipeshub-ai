"""Unit tests for app.utils.oauth_config."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.utils.oauth_config import (
    fetch_oauth_config_by_id,
    fetch_toolset_oauth_config_by_id,
    get_oauth_config,
    resolve_instance_url,
)


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

    def test_instance_url_derives_oauth_endpoints_when_urls_missing(self):
        auth = {
            "clientId": "c",
            "clientSecret": "s",
            "instanceUrl": "https://gitlab.enterprise.example",
        }
        result = get_oauth_config(auth)
        assert result.authorize_url == "https://gitlab.enterprise.example/oauth/authorize"
        assert result.token_url == "https://gitlab.enterprise.example/oauth/token"

    def test_instance_url_trailing_slash_stripped_for_derived_urls(self):
        auth = {
            "clientId": "c",
            "clientSecret": "s",
            "instanceUrl": "https://gitlab.enterprise.example/",
        }
        result = get_oauth_config(auth)
        assert result.authorize_url == "https://gitlab.enterprise.example/oauth/authorize"
        assert result.token_url == "https://gitlab.enterprise.example/oauth/token"

    def test_explicit_authorize_and_token_urls_preempt_instance_url(self):
        auth = {
            "clientId": "c",
            "clientSecret": "s",
            "instanceUrl": "https://gitlab.enterprise.example",
            "authorizeUrl": "https://idp.example.com/auth",
            "tokenUrl": "https://idp.example.com/token",
        }
        result = get_oauth_config(auth)
        assert result.authorize_url == "https://idp.example.com/auth"
        assert result.token_url == "https://idp.example.com/token"

    def test_instance_url_overrides_saas_host_for_standard_oauth_paths(self):
        """GitLab EE: stored URLs point at gitlab.com but user supplied a self-managed
        instanceUrl. Host of the standard /oauth/authorize and /oauth/token endpoints
        must be swapped to the user's instance so the login flow hits the EE host."""
        auth = {
            "clientId": "c",
            "clientSecret": "s",
            "instanceUrl": "https://gitlab.mycompany.com",
            "authorizeUrl": "https://gitlab.com/oauth/authorize",
            "tokenUrl": "https://gitlab.com/oauth/token",
        }
        result = get_oauth_config(auth)
        assert result.authorize_url == "https://gitlab.mycompany.com/oauth/authorize"
        assert result.token_url == "https://gitlab.mycompany.com/oauth/token"

    def test_instance_url_override_handles_trailing_slash(self):
        auth = {
            "clientId": "c",
            "clientSecret": "s",
            "instanceUrl": "https://gitlab.mycompany.com/",
            "authorizeUrl": "https://gitlab.com/oauth/authorize",
            "tokenUrl": "https://gitlab.com/oauth/token",
        }
        result = get_oauth_config(auth)
        assert result.authorize_url == "https://gitlab.mycompany.com/oauth/authorize"
        assert result.token_url == "https://gitlab.mycompany.com/oauth/token"

    def test_instance_url_does_not_override_non_standard_oauth_path(self):
        """ServiceNow-style: user supplies instanceUrl AND explicit authorize/token URLs
        with a non-standard path (/oauth_auth.do, /oauth_token.do). Must be left alone."""
        auth = {
            "clientId": "c",
            "clientSecret": "s",
            "instanceUrl": "https://dev12345.service-now.com",
            "authorizeUrl": "https://other.service-now.com/oauth_auth.do",
            "tokenUrl": "https://other.service-now.com/oauth_token.do",
        }
        result = get_oauth_config(auth)
        assert result.authorize_url == "https://other.service-now.com/oauth_auth.do"
        assert result.token_url == "https://other.service-now.com/oauth_token.do"

    def test_instance_url_override_noop_when_hosts_match(self):
        auth = {
            "clientId": "c",
            "clientSecret": "s",
            "instanceUrl": "https://gitlab.mycompany.com",
            "authorizeUrl": "https://gitlab.mycompany.com/oauth/authorize",
            "tokenUrl": "https://gitlab.mycompany.com/oauth/token",
        }
        result = get_oauth_config(auth)
        assert result.authorize_url == "https://gitlab.mycompany.com/oauth/authorize"
        assert result.token_url == "https://gitlab.mycompany.com/oauth/token"

    def test_invalid_instance_url_does_not_break_existing_urls(self):
        auth = {
            "clientId": "c",
            "clientSecret": "s",
            "instanceUrl": "not-a-url",
            "authorizeUrl": "https://gitlab.com/oauth/authorize",
            "tokenUrl": "https://gitlab.com/oauth/token",
        }
        result = get_oauth_config(auth)
        assert result.authorize_url == "https://gitlab.com/oauth/authorize"
        assert result.token_url == "https://gitlab.com/oauth/token"

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
# resolve_instance_url
# ===================================================================
class TestResolveInstanceUrl:
    @pytest.mark.asyncio
    async def test_returns_instance_url_from_auth_config(self):
        config_service = AsyncMock()
        result = await resolve_instance_url(
            {"instanceUrl": "https://git.example.com"},
            config_service,
            default="https://gitlab.com",
        )
        assert result == "https://git.example.com"
        config_service.get_config.assert_not_called()

    @pytest.mark.asyncio
    async def test_strips_trailing_slash_from_auth_config(self):
        config_service = AsyncMock()
        result = await resolve_instance_url(
            {"instanceUrl": "https://git.example.com/"},
            config_service,
            default="https://gitlab.com",
        )
        assert result == "https://git.example.com"

    @pytest.mark.asyncio
    async def test_falls_back_to_shared_config_when_instance_url_missing(self):
        config_service = AsyncMock()
        config_service.get_config.return_value = [
            {
                "_id": "oauth-1",
                "config": {
                    "clientId": "cid",
                    "instanceUrl": "https://git.example.com",
                },
            }
        ]
        result = await resolve_instance_url(
            {"oauthConfigId": "oauth-1", "connectorType": "GITLAB"},
            config_service,
            default="https://gitlab.com",
        )
        assert result == "https://git.example.com"

    @pytest.mark.asyncio
    async def test_falls_back_to_shared_config_when_instance_url_empty_string(self):
        config_service = AsyncMock()
        config_service.get_config.return_value = [
            {
                "_id": "oauth-1",
                "config": {"instanceUrl": "https://git.example.com"},
            }
        ]
        result = await resolve_instance_url(
            {
                "instanceUrl": "",
                "oauthConfigId": "oauth-1",
                "connectorType": "GITLAB",
            },
            config_service,
            default="https://gitlab.com",
        )
        assert result == "https://git.example.com"

    @pytest.mark.asyncio
    async def test_returns_default_when_no_auth_config(self):
        config_service = AsyncMock()
        result = await resolve_instance_url(
            None, config_service, default="https://gitlab.com"
        )
        assert result == "https://gitlab.com"
        config_service.get_config.assert_not_called()

    @pytest.mark.asyncio
    async def test_returns_default_when_no_oauth_config_id(self):
        config_service = AsyncMock()
        result = await resolve_instance_url(
            {"connectorType": "GITLAB"},
            config_service,
            default="https://gitlab.com",
        )
        assert result == "https://gitlab.com"
        config_service.get_config.assert_not_called()

    @pytest.mark.asyncio
    async def test_returns_default_when_shared_config_not_found(self):
        config_service = AsyncMock()
        config_service.get_config.return_value = []
        result = await resolve_instance_url(
            {"oauthConfigId": "oauth-1", "connectorType": "GITLAB"},
            config_service,
            default="https://gitlab.com",
        )
        assert result == "https://gitlab.com"

    @pytest.mark.asyncio
    async def test_returns_default_when_shared_config_has_no_instance_url(self):
        config_service = AsyncMock()
        config_service.get_config.return_value = [
            {"_id": "oauth-1", "config": {"clientId": "cid"}}
        ]
        result = await resolve_instance_url(
            {"oauthConfigId": "oauth-1", "connectorType": "GITLAB"},
            config_service,
            default="https://gitlab.com",
        )
        assert result == "https://gitlab.com"

    @pytest.mark.asyncio
    async def test_returns_default_unchanged_when_default_is_empty(self):
        config_service = AsyncMock()
        result = await resolve_instance_url(None, config_service)
        assert result == ""

    @pytest.mark.asyncio
    async def test_swallows_fetch_exception_and_returns_default(self):
        config_service = AsyncMock()
        config_service.get_config.side_effect = RuntimeError("network down")
        result = await resolve_instance_url(
            {"oauthConfigId": "oauth-1", "connectorType": "GITLAB"},
            config_service,
            default="https://gitlab.com",
        )
        assert result == "https://gitlab.com"


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


# ===================================================================
# fetch_toolset_oauth_config_by_id
# ===================================================================
class TestFetchToolsetOAuthConfigById:
    @pytest.mark.asyncio
    async def test_found(self):
        config_service = AsyncMock()
        config_service.get_config.return_value = [
            {"_id": "cfg-1", "config": {"clientId": "c1"}},
            {"_id": "cfg-2", "config": {"clientId": "c2"}},
        ]
        result = await fetch_toolset_oauth_config_by_id(
            oauth_config_id="cfg-2",
            toolset_type="jira",
            config_service=config_service,
        )
        assert result is not None
        assert result["_id"] == "cfg-2"
        # Verify the toolset-specific path was queried
        call_args = config_service.get_config.call_args
        assert "/services/oauths/toolsets/" in call_args[0][0]

    @pytest.mark.asyncio
    async def test_not_found(self):
        config_service = AsyncMock()
        config_service.get_config.return_value = [
            {"_id": "cfg-1", "config": {"clientId": "c1"}},
        ]
        logger = MagicMock()
        result = await fetch_toolset_oauth_config_by_id(
            oauth_config_id="missing",
            toolset_type="slack",
            config_service=config_service,
            logger=logger,
        )
        assert result is None
        logger.warning.assert_called()

    @pytest.mark.asyncio
    async def test_empty_oauth_config_id(self):
        config_service = AsyncMock()
        logger = MagicMock()
        result = await fetch_toolset_oauth_config_by_id(
            oauth_config_id="",
            toolset_type="jira",
            config_service=config_service,
            logger=logger,
        )
        assert result is None
        logger.warning.assert_called()

    @pytest.mark.asyncio
    async def test_empty_toolset_type(self):
        config_service = AsyncMock()
        logger = MagicMock()
        result = await fetch_toolset_oauth_config_by_id(
            oauth_config_id="cfg-1",
            toolset_type="",
            config_service=config_service,
            logger=logger,
        )
        assert result is None
        logger.warning.assert_called()

    @pytest.mark.asyncio
    async def test_non_list_response(self):
        config_service = AsyncMock()
        config_service.get_config.return_value = {"not": "a list"}
        logger = MagicMock()
        result = await fetch_toolset_oauth_config_by_id(
            oauth_config_id="cfg-1",
            toolset_type="jira",
            config_service=config_service,
            logger=logger,
        )
        assert result is None
        logger.warning.assert_called()

    @pytest.mark.asyncio
    async def test_config_service_raises(self):
        config_service = AsyncMock()
        config_service.get_config.side_effect = RuntimeError("network error")
        logger = MagicMock()
        result = await fetch_toolset_oauth_config_by_id(
            oauth_config_id="cfg-1",
            toolset_type="jira",
            config_service=config_service,
            logger=logger,
        )
        assert result is None
        logger.error.assert_called()

    @pytest.mark.asyncio
    async def test_no_logger_empty_inputs(self):
        config_service = AsyncMock()
        result = await fetch_toolset_oauth_config_by_id(
            oauth_config_id="",
            toolset_type="",
            config_service=config_service,
            logger=None,
        )
        assert result is None

    @pytest.mark.asyncio
    async def test_no_logger_exception_branch(self):
        config_service = AsyncMock()
        config_service.get_config.side_effect = RuntimeError("boom")
        result = await fetch_toolset_oauth_config_by_id(
            oauth_config_id="cfg-1",
            toolset_type="slack",
            config_service=config_service,
            logger=None,
        )
        assert result is None

    @pytest.mark.asyncio
    async def test_no_logger_not_found(self):
        config_service = AsyncMock()
        config_service.get_config.return_value = []
        result = await fetch_toolset_oauth_config_by_id(
            oauth_config_id="cfg-1",
            toolset_type="slack",
            config_service=config_service,
            logger=None,
        )
        assert result is None
