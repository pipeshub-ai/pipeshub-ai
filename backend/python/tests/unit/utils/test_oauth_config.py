"""Unit tests for app.utils.oauth_config."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.utils.oauth_config import (
    _override_oauth_host_with_instance,
    extract_oauth_error_message,
    fetch_oauth_config_by_id,
    fetch_toolset_oauth_config_by_id,
    get_oauth_config,
    resolve_instance_url,
)


# ===================================================================
# extract_oauth_error_message
# ===================================================================
class TestExtractOAuthErrorMessage:
    """Covers lines 52-138 — the entire extract_oauth_error_message function."""

    # --- Path 1: JSON body with error_description > 5 chars (lines 77-79, 84-95) ---

    def test_json_body_with_error_description(self):
        exc = Exception(
            'Token request failed. Response: {"error": "invalid_grant", '
            '"error_description": "The authorization code has expired"}'
        )
        result = extract_oauth_error_message(exc)
        assert result.startswith("OAuth provider error:")
        assert "authorization code has expired" in result
        assert result.endswith("Please verify your OAuth credentials in the admin settings.")

    def test_json_body_error_description_strips_trailing_dot(self):
        exc = Exception(
            'Token request failed. Response: {"error_description": "Token was revoked."}'
        )
        result = extract_oauth_error_message(exc)
        assert "OAuth provider error: Token was revoked." in result
        assert not result.startswith("OAuth provider error: Token was revoked..")
        assert "Token was revoked" in result

    def test_json_body_with_notion_message_field(self):
        """Notion returns {"error": "unauthorized", "message": "..."} — line 88-89."""
        exc = Exception(
            'Token request failed. Response: {"error": "unauthorized", '
            '"message": "API token is invalid or has been revoked"}'
        )
        result = extract_oauth_error_message(exc)
        assert result.startswith("OAuth provider error:")
        assert "API token is invalid" in result

    def test_json_body_short_description_falls_through_to_error_code(self):
        """Description <= 5 chars should be ignored; fall through to error code map."""
        exc = Exception(
            'Token request failed. Response: {"error": "invalid_client", '
            '"error_description": "bad"}'
        )
        result = extract_oauth_error_message(exc)
        assert "Invalid OAuth client credentials" in result
        assert "Please update it in the admin settings." in result

    # --- Path 2: error code in _OAUTH_ERROR_CODE_MAP (lines 99-106) ---

    def test_known_error_code_no_description(self):
        exc = Exception(
            'Token request failed. Response: {"error": "invalid_scope"}'
        )
        result = extract_oauth_error_message(exc)
        assert "One or more requested scopes" in result
        assert "Please update it in the admin settings." in result

    def test_slack_ok_false_without_error_code(self):
        """Slack returns {"ok": false} with no "error" key — line 103."""
        exc = Exception(
            'Token request failed. Response: {"ok": false}'
        )
        result = extract_oauth_error_message(exc)
        assert "malformed" in result or "invalid_request" in result.lower() or "Please update" in result

    def test_slack_ok_false_with_error_code(self):
        exc = Exception(
            'Token request failed. Response: {"ok": false, "error": "bad_client_secret"}'
        )
        result = extract_oauth_error_message(exc)
        assert "Slack client secret is invalid" in result

    # --- Path 3: error_body is not a dict (branch 84->93, 99->105) ---

    def test_json_body_is_list_not_dict(self):
        """json.loads returns a list — isinstance(error_body, dict) is False."""
        exc = Exception(
            'Token request failed. Response: ["error1", "error2"]'
        )
        result = extract_oauth_error_message(exc)
        assert "unexpected error" in result.lower()

    def test_json_body_is_string(self):
        exc = Exception(
            'Token request failed. Response: "some string"'
        )
        result = extract_oauth_error_message(exc)
        assert "unexpected error" in result.lower()

    # --- Path 4: JSON parse fails, fallback to raw regex scan (line 113) ---

    def test_json_parse_fails_but_known_code_in_raw_message(self):
        """No valid JSON but a known code appears in the text — line 113."""
        exc = Exception("OAuth error: invalid_client returned by provider")
        result = extract_oauth_error_message(exc)
        assert "Invalid OAuth client credentials" in result
        assert "Please update it in the admin settings." in result

    def test_raw_message_contains_access_denied(self):
        exc = Exception("The request resulted in access_denied from the server")
        result = extract_oauth_error_message(exc)
        assert "Access was denied" in result

    # --- Path 5: HTTP status code fallback (lines 118-130) ---

    def test_status_400(self):
        exc = Exception("Request failed with status 400")
        result = extract_oauth_error_message(exc)
        assert "400 Bad Request" in result

    def test_status_401(self):
        exc = Exception("Request failed with status 401")
        result = extract_oauth_error_message(exc)
        assert "401 Unauthorized" in result

    def test_status_403(self):
        exc = Exception("Request failed with status 403")
        result = extract_oauth_error_message(exc)
        assert "403 Forbidden" in result

    def test_status_404(self):
        exc = Exception("Request failed with status 404")
        result = extract_oauth_error_message(exc)
        assert "404" in result
        assert "not found" in result.lower()

    def test_status_429(self):
        exc = Exception("Request failed with status 429")
        result = extract_oauth_error_message(exc)
        assert "rate-limited" in result.lower() or "rate" in result.lower()

    def test_status_500(self):
        exc = Exception("Request failed with status 500")
        result = extract_oauth_error_message(exc)
        assert "experiencing issues" in result

    def test_status_502(self):
        """Any status >= 500 should give the server-issues message."""
        exc = Exception("Request failed with status 502")
        result = extract_oauth_error_message(exc)
        assert "experiencing issues" in result

    def test_status_422_unhandled_falls_through(self):
        """Status codes not explicitly handled (e.g. 422) fall through to generic."""
        exc = Exception("Request failed with status 422")
        result = extract_oauth_error_message(exc)
        assert "unexpected error" in result.lower()

    # --- Path 6: ValueError messages from handle_callback (lines 134, 136) ---

    def test_invalid_or_expired_state(self):
        exc = ValueError("Invalid or expired state token received")
        result = extract_oauth_error_message(exc)
        assert "session has expired" in result

    def test_already_been_used(self):
        exc = ValueError("This code has already been used")
        result = extract_oauth_error_message(exc)
        assert "already been used" in result

    # --- Path 7: Generic fallback (line 138) ---

    def test_generic_fallback(self):
        exc = Exception("Something completely unexpected happened")
        result = extract_oauth_error_message(exc)
        assert "unexpected error" in result.lower()
        assert "administrator" in result

    # --- Edge cases ---

    def test_no_response_prefix_no_known_code(self):
        exc = Exception("Network timeout")
        result = extract_oauth_error_message(exc)
        assert "unexpected error" in result.lower()

    def test_malformed_json_in_response(self):
        """Response: followed by invalid JSON — lines 77-79 except branch."""
        exc = Exception("Token request failed. Response: {not valid json}")
        result = extract_oauth_error_message(exc)
        assert isinstance(result, str)

    def test_empty_exception_message(self):
        exc = Exception("")
        result = extract_oauth_error_message(exc)
        assert "unexpected error" in result.lower()

    def test_json_body_with_empty_error_description_falls_to_code_map(self):
        exc = Exception(
            'Token request failed. Response: {"error": "server_error", "error_description": ""}'
        )
        result = extract_oauth_error_message(exc)
        assert "internal error" in result.lower()

    def test_json_body_with_whitespace_only_description(self):
        exc = Exception(
            'Token request failed. Response: {"error": "forbidden", "error_description": "   "}'
        )
        result = extract_oauth_error_message(exc)
        assert "403 Forbidden" in result or "Please update" in result

    def test_all_known_error_codes_return_mapped_message(self):
        """Smoke test: every code in the map should produce its mapped message."""
        from app.utils.oauth_config import _OAUTH_ERROR_CODE_MAP

        for code in _OAUTH_ERROR_CODE_MAP:
            exc = Exception(f'Token request failed. Response: {{"error": "{code}"}}')
            result = extract_oauth_error_message(exc)
            assert "Please update it in the admin settings." in result, f"Code {code} not mapped"


# ===================================================================
# _override_oauth_host_with_instance
# ===================================================================
class TestOverrideOAuthHostWithInstance:
    """Covers edge cases in _override_oauth_host_with_instance (lines 155, 159-160)."""

    def test_empty_url_returns_url(self):
        """Line 155 — not url branch."""
        assert _override_oauth_host_with_instance("", "https://example.com") == ""

    def test_empty_instance_url_returns_url(self):
        """Line 155 — not instance_url branch."""
        assert (
            _override_oauth_host_with_instance("https://gitlab.com/oauth/authorize", "")
            == "https://gitlab.com/oauth/authorize"
        )

    def test_both_empty_returns_empty(self):
        assert _override_oauth_host_with_instance("", "") == ""

    def test_instance_url_no_scheme_or_netloc_returns_url(self):
        """instance_url without scheme/netloc — line 161-162 guard."""
        result = _override_oauth_host_with_instance(
            "https://gitlab.com/oauth/authorize", "not-a-url"
        )
        assert result == "https://gitlab.com/oauth/authorize"

    def test_non_standard_path_left_untouched(self):
        result = _override_oauth_host_with_instance(
            "https://host.com/custom/auth", "https://instance.com"
        )
        assert result == "https://host.com/custom/auth"

    def test_same_netloc_noop(self):
        result = _override_oauth_host_with_instance(
            "https://gitlab.myco.com/oauth/authorize", "https://gitlab.myco.com"
        )
        assert result == "https://gitlab.myco.com/oauth/authorize"

    def test_standard_path_swaps_host(self):
        result = _override_oauth_host_with_instance(
            "https://gitlab.com/oauth/token", "https://my.gitlab.com"
        )
        assert result == "https://my.gitlab.com/oauth/token"

    @patch("app.utils.oauth_config.urlparse", side_effect=ValueError("bad url"))
    def test_urlparse_exception_returns_url_unchanged(self, _mock):
        """Lines 159-160 — exception during URL parsing."""
        result = _override_oauth_host_with_instance(
            "https://gitlab.com/oauth/authorize", "https://instance.com"
        )
        assert result == "https://gitlab.com/oauth/authorize"


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
        assert result.client_id == "c"

    @patch("app.utils.oauth_config.urlparse")
    def test_notion_detection_urlparse_exception(self, mock_urlparse):
        """Line 227 — urlparse raises inside the Notion detection try block.

        When there is no instanceUrl, _override_oauth_host_with_instance is
        never called, so the FIRST urlparse call is the Notion detection one.
        """
        mock_urlparse.side_effect = ValueError("intentional parse failure")
        auth = {
            "clientId": "c",
            "clientSecret": "s",
            "tokenUrl": "https://api.notion.com/v1/oauth/token",
        }
        result = get_oauth_config(auth)
        assert result.client_id == "c"
        assert result.additional_params.get("use_basic_auth") is None


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

    @pytest.mark.asyncio
    @patch("app.utils.oauth_config.fetch_oauth_config_by_id", new_callable=AsyncMock)
    async def test_fetch_raises_uncaught_exception_returns_default(self, mock_fetch):
        """Lines 277-278 — the outer except around fetch_oauth_config_by_id."""
        mock_fetch.side_effect = RuntimeError("unexpected crash")
        config_service = AsyncMock()
        result = await resolve_instance_url(
            {"oauthConfigId": "oauth-1", "connectorType": "GITLAB"},
            config_service,
            default="https://gitlab.com",
        )
        assert result == "https://gitlab.com"

    @pytest.mark.asyncio
    @patch("app.utils.oauth_config.fetch_oauth_config_by_id", new_callable=AsyncMock)
    async def test_fetch_raises_with_empty_default(self, mock_fetch):
        """Lines 277-278 — default is empty string."""
        mock_fetch.side_effect = RuntimeError("crash")
        config_service = AsyncMock()
        result = await resolve_instance_url(
            {"oauthConfigId": "oauth-1", "connectorType": "GITLAB"},
            config_service,
            default="",
        )
        assert result == ""

    @pytest.mark.asyncio
    @patch("app.utils.oauth_config.fetch_oauth_config_by_id", new_callable=AsyncMock)
    async def test_fetch_raises_with_trailing_slash_default(self, mock_fetch):
        """Lines 277-278 — default has trailing slash, should be stripped."""
        mock_fetch.side_effect = RuntimeError("crash")
        config_service = AsyncMock()
        result = await resolve_instance_url(
            {"oauthConfigId": "oauth-1", "connectorType": "GITLAB"},
            config_service,
            default="https://gitlab.com/",
        )
        assert result == "https://gitlab.com"

    @pytest.mark.asyncio
    async def test_shared_config_returns_none(self):
        """Line 280-281 — fetch_oauth_config_by_id returns None."""
        config_service = AsyncMock()
        config_service.get_config.return_value = []
        result = await resolve_instance_url(
            {"oauthConfigId": "oauth-1", "connectorType": "GITLAB"},
            config_service,
            default="https://fallback.com/",
        )
        assert result == "https://fallback.com"

    @pytest.mark.asyncio
    async def test_shared_config_has_no_config_key(self):
        """Line 283 — shared.get("config") returns None."""
        config_service = AsyncMock()
        config_service.get_config.return_value = [
            {"_id": "oauth-1"}
        ]
        result = await resolve_instance_url(
            {"oauthConfigId": "oauth-1", "connectorType": "GITLAB"},
            config_service,
            default="https://fallback.com",
        )
        assert result == "https://fallback.com"

    @pytest.mark.asyncio
    async def test_whitespace_instance_url_treated_as_empty(self):
        config_service = AsyncMock()
        config_service.get_config.return_value = [
            {"_id": "oauth-1", "config": {"instanceUrl": "   "}}
        ]
        result = await resolve_instance_url(
            {
                "instanceUrl": "   ",
                "oauthConfigId": "oauth-1",
                "connectorType": "GITLAB",
            },
            config_service,
            default="https://fallback.com",
        )
        assert result == "https://fallback.com"

    @pytest.mark.asyncio
    async def test_returns_default_when_no_connector_type(self):
        config_service = AsyncMock()
        result = await resolve_instance_url(
            {"oauthConfigId": "oauth-1"},
            config_service,
            default="https://fallback.com/",
        )
        assert result == "https://fallback.com"
        config_service.get_config.assert_not_called()


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

    @pytest.mark.asyncio
    async def test_non_list_response_no_logger(self):
        """Branch 389->391 — non-list config without logger should not crash."""
        config_service = AsyncMock()
        config_service.get_config.return_value = "not-a-list"
        result = await fetch_oauth_config_by_id(
            oauth_config_id="cfg-1",
            connector_type="GOOGLE_DRIVE",
            config_service=config_service,
            logger=None,
        )
        assert result is None


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

    @pytest.mark.asyncio
    async def test_non_list_response_no_logger(self):
        """Branch 315->317 — non-list config without logger should not crash."""
        config_service = AsyncMock()
        config_service.get_config.return_value = "not-a-list"
        result = await fetch_toolset_oauth_config_by_id(
            oauth_config_id="cfg-1",
            toolset_type="jira",
            config_service=config_service,
            logger=None,
        )
        assert result is None
