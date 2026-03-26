"""Tests for app.connectors.core.base.token_service.oauth_service"""

import base64
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch
from urllib.parse import parse_qs, urlparse

import pytest

from app.connectors.core.base.token_service.oauth_service import (
    OAuthConfig,
    OAuthProvider,
    OAuthToken,
)

# ---------------------------------------------------------------------------
# OAuthToken.is_expired
# ---------------------------------------------------------------------------


class TestOAuthTokenIsExpired:
    """Tests for OAuthToken.is_expired property."""

    def test_expired_token(self):
        """Token created in the past with short expires_in should be expired."""
        token = OAuthToken(
            access_token="expired-tok",
            expires_in=10,
            created_at=datetime.now() - timedelta(seconds=60),
        )
        assert token.is_expired is True

    def test_valid_token(self):
        """Token created just now with long expires_in should not be expired."""
        token = OAuthToken(
            access_token="valid-tok",
            expires_in=3600,
            created_at=datetime.now(),
        )
        assert token.is_expired is False

    def test_no_expires_in_never_expired(self):
        """Token without expires_in is treated as never expiring."""
        token = OAuthToken(access_token="forever-tok", expires_in=None)
        assert token.is_expired is False

    def test_already_past_expiry(self):
        """Token whose expiry is clearly in the past should be expired."""
        token = OAuthToken(
            access_token="boundary-tok",
            expires_in=1,
            created_at=datetime.now() - timedelta(seconds=10),
        )
        assert token.is_expired is True


# ---------------------------------------------------------------------------
# OAuthToken.expires_at_epoch
# ---------------------------------------------------------------------------


class TestExpiresAtEpoch:
    """Tests for OAuthToken.expires_at_epoch property."""

    def test_with_expires_in(self):
        created = datetime(2025, 1, 1, 0, 0, 0)
        token = OAuthToken(
            access_token="tok", expires_in=3600, created_at=created
        )
        expected = int((created + timedelta(seconds=3600)).timestamp())
        assert token.expires_at_epoch == expected

    def test_without_expires_in(self):
        token = OAuthToken(access_token="tok", expires_in=None)
        assert token.expires_at_epoch is None


# ---------------------------------------------------------------------------
# OAuthToken.to_dict / from_dict roundtrip
# ---------------------------------------------------------------------------


class TestOAuthTokenSerialization:
    """Tests for to_dict() and from_dict() serialization roundtrip."""

    def test_roundtrip(self):
        """to_dict() then from_dict() should recreate an equivalent token."""
        original = OAuthToken(
            access_token="access-123",
            token_type="Bearer",
            expires_in=3600,
            refresh_token="refresh-456",
            refresh_token_expires_in=86400,
            scope="read write",
            id_token="id-tok-789",
            created_at=datetime(2025, 6, 15, 12, 0, 0),
            uid="user-1",
            account_id="acc-1",
            team_id="team-1",
        )
        d = original.to_dict()
        restored = OAuthToken.from_dict(d)

        assert restored.access_token == original.access_token
        assert restored.token_type == original.token_type
        assert restored.expires_in == original.expires_in
        assert restored.refresh_token == original.refresh_token
        assert restored.refresh_token_expires_in == original.refresh_token_expires_in
        assert restored.scope == original.scope
        assert restored.id_token == original.id_token
        assert restored.created_at == original.created_at
        assert restored.uid == original.uid
        assert restored.account_id == original.account_id
        assert restored.team_id == original.team_id

    def test_to_dict_contains_all_fields(self):
        token = OAuthToken(access_token="tok")
        d = token.to_dict()
        expected_keys = {
            "access_token",
            "token_type",
            "expires_in",
            "refresh_token",
            "refresh_token_expires_in",
            "scope",
            "id_token",
            "created_at",
            "uid",
            "account_id",
            "team_id",
        }
        assert set(d.keys()) == expected_keys

    def test_to_dict_created_at_is_iso_string(self):
        token = OAuthToken(
            access_token="tok",
            created_at=datetime(2025, 1, 1, 0, 0, 0),
        )
        d = token.to_dict()
        assert d["created_at"] == "2025-01-01T00:00:00"

    def test_from_dict_parses_created_at_string(self):
        d = {
            "access_token": "tok",
            "created_at": "2025-06-15T12:30:00",
        }
        token = OAuthToken.from_dict(d)
        assert token.created_at == datetime(2025, 6, 15, 12, 30, 0)


# ---------------------------------------------------------------------------
# OAuthToken.from_dict filters unknown fields
# ---------------------------------------------------------------------------


class TestFromDictFiltering:
    """from_dict() should silently ignore unknown fields."""

    def test_unknown_fields_filtered(self):
        d = {
            "access_token": "tok",
            "token_type": "Bearer",
            "unknown_field": "should be ignored",
            "another_extra": 123,
        }
        token = OAuthToken.from_dict(d)
        assert token.access_token == "tok"
        assert not hasattr(token, "unknown_field")
        assert not hasattr(token, "another_extra")

    def test_from_dict_does_not_mutate_input(self):
        """from_dict makes a copy; the original dict should be unmodified."""
        d = {
            "access_token": "tok",
            "created_at": "2025-01-01T00:00:00",
            "extra": "stuff",
        }
        original_keys = set(d.keys())
        OAuthToken.from_dict(d)
        assert set(d.keys()) == original_keys
        # created_at should still be the original string, not converted
        assert isinstance(d["created_at"], str)

    def test_minimal_dict(self):
        """Only access_token is required."""
        token = OAuthToken.from_dict({"access_token": "min-tok"})
        assert token.access_token == "min-tok"
        assert token.token_type == "Bearer"  # default
        assert token.expires_in is None
        assert token.refresh_token is None


# ---------------------------------------------------------------------------
# OAuthConfig.generate_state
# ---------------------------------------------------------------------------


class TestOAuthConfigGenerateState:
    """Tests for OAuthConfig.generate_state()."""

    def test_returns_non_empty_string(self):
        config = OAuthConfig(
            client_id="cid",
            client_secret="csec",
            redirect_uri="http://localhost/callback",
            authorize_url="https://auth.example.com/authorize",
            token_url="https://auth.example.com/token",
        )
        state = config.generate_state()
        assert isinstance(state, str)
        assert len(state) > 0

    def test_sets_state_attribute(self):
        config = OAuthConfig(
            client_id="cid",
            client_secret="csec",
            redirect_uri="http://localhost/callback",
            authorize_url="https://auth.example.com/authorize",
            token_url="https://auth.example.com/token",
        )
        state = config.generate_state()
        assert config.state == state

    def test_different_each_call(self):
        config = OAuthConfig(
            client_id="cid",
            client_secret="csec",
            redirect_uri="http://localhost/callback",
            authorize_url="https://auth.example.com/authorize",
            token_url="https://auth.example.com/token",
        )
        state1 = config.generate_state()
        state2 = config.generate_state()
        assert state1 != state2


# ---------------------------------------------------------------------------
# OAuthConfig.normalize_token_response
# ---------------------------------------------------------------------------


class TestNormalizeTokenResponse:
    """Tests for OAuthConfig.normalize_token_response()."""

    def _make_config(self, token_response_path=None):
        return OAuthConfig(
            client_id="cid",
            client_secret="csec",
            redirect_uri="http://localhost/callback",
            authorize_url="https://auth.example.com/authorize",
            token_url="https://auth.example.com/token",
            token_response_path=token_response_path,
        )

    def test_no_path_returns_response_unchanged(self):
        """Without token_response_path, the response is returned as-is."""
        config = self._make_config()
        response = {"access_token": "abc", "token_type": "Bearer"}
        assert config.normalize_token_response(response) is response

    def test_nested_path_extraction(self):
        """Extracts token data from a nested path (e.g. Slack's authed_user)."""
        config = self._make_config(token_response_path="authed_user")
        response = {
            "ok": True,
            "authed_user": {
                "access_token": "xoxp-user-token",
                "scope": "chat:write",
                "id": "U12345",
            },
            "scope": "incoming-webhook",
            "token_type": "bearer",
        }
        result = config.normalize_token_response(response)
        assert result["access_token"] == "xoxp-user-token"
        assert result["scope"] == "chat:write"  # from nested, not top-level
        assert result["token_type"] == "bearer"  # merged from top-level

    def test_nested_path_access_token_fallback_to_top_level(self):
        """If nested data has no access_token, falls back to top-level."""
        config = self._make_config(token_response_path="authed_user")
        response = {
            "access_token": "top-level-token",
            "authed_user": {
                "id": "U12345",
                "scope": "read",
            },
        }
        result = config.normalize_token_response(response)
        assert result["access_token"] == "top-level-token"

    def test_nested_path_no_access_token_anywhere_returns_original(self):
        """If no access_token in nested or top-level, returns original response."""
        config = self._make_config(token_response_path="authed_user")
        response = {
            "authed_user": {
                "id": "U12345",
            },
        }
        result = config.normalize_token_response(response)
        assert result is response

    def test_nested_path_not_a_dict_falls_back(self):
        """If the path value is not a dict, returns original response."""
        config = self._make_config(token_response_path="authed_user")
        response = {
            "authed_user": "not-a-dict",
            "access_token": "tok",
        }
        result = config.normalize_token_response(response)
        assert result is response

    def test_nested_path_missing_falls_back(self):
        """If the path doesn't exist in response, returns original."""
        config = self._make_config(token_response_path="authed_user")
        response = {"access_token": "tok"}
        result = config.normalize_token_response(response)
        assert result is response

    def test_merges_top_level_fields(self):
        """Top-level scope, token_type, expires_in etc. are merged if missing in nested."""
        config = self._make_config(token_response_path="authed_user")
        response = {
            "authed_user": {
                "access_token": "nested-tok",
            },
            "scope": "admin",
            "token_type": "bearer",
            "expires_in": 3600,
            "refresh_token": "ref-tok",
            "refresh_token_expires_in": 86400,
        }
        result = config.normalize_token_response(response)
        assert result["access_token"] == "nested-tok"
        assert result["scope"] == "admin"
        assert result["token_type"] == "bearer"
        assert result["expires_in"] == 3600
        assert result["refresh_token"] == "ref-tok"
        assert result["refresh_token_expires_in"] == 86400

    def test_nested_fields_take_precedence_over_top_level(self):
        """When both nested and top-level have the same field, nested wins."""
        config = self._make_config(token_response_path="authed_user")
        response = {
            "authed_user": {
                "access_token": "nested-tok",
                "scope": "nested-scope",
            },
            "scope": "top-scope",
        }
        result = config.normalize_token_response(response)
        assert result["scope"] == "nested-scope"

    def test_slack_team_id_extraction(self):
        """Extracts team_id from team.id (Slack-specific)."""
        config = self._make_config(token_response_path="authed_user")
        response = {
            "authed_user": {
                "access_token": "tok",
            },
            "team": {"id": "T12345", "name": "MyTeam"},
        }
        result = config.normalize_token_response(response)
        assert result["team_id"] == "T12345"

    def test_team_not_dict_no_team_id(self):
        """If team is not a dict, team_id is not added."""
        config = self._make_config(token_response_path="authed_user")
        response = {
            "authed_user": {
                "access_token": "tok",
            },
            "team": "not-a-dict",
        }
        result = config.normalize_token_response(response)
        assert "team_id" not in result


# ---------------------------------------------------------------------------
# Fixtures for OAuthProvider tests
# ---------------------------------------------------------------------------


def _make_oauth_config(**overrides):
    """Build a minimal OAuthConfig for testing."""
    defaults = {
        "client_id": "test-client-id",
        "client_secret": "test-client-secret",
        "redirect_uri": "http://localhost/callback",
        "authorize_url": "https://auth.example.com/authorize",
        "token_url": "https://auth.example.com/token",
    }
    defaults.update(overrides)
    return OAuthConfig(**defaults)


@pytest.fixture
def mock_config_service():
    """Mock ConfigurationService with async get_config/set_config."""
    svc = MagicMock()
    svc.get_config = AsyncMock(return_value={})
    svc.set_config = AsyncMock()
    return svc


@pytest.fixture
def oauth_provider(mock_config_service):
    """Build an OAuthProvider with a mock configuration service."""
    config = _make_oauth_config()
    return OAuthProvider(config, mock_config_service, "/test/creds/path")


# ---------------------------------------------------------------------------
# OAuthProvider.session property
# ---------------------------------------------------------------------------


class TestOAuthProviderSession:
    """Tests for the session property (lazy ClientSession creation)."""

    @pytest.mark.asyncio
    async def test_session_creates_new_session(self, oauth_provider):
        """First access creates a new ClientSession."""
        assert oauth_provider._session is None
        session = await oauth_provider.session
        assert session is not None
        # Clean up
        await session.close()

    @pytest.mark.asyncio
    async def test_session_reuses_existing_open_session(self, oauth_provider):
        """Second access reuses the same session."""
        session1 = await oauth_provider.session
        session2 = await oauth_provider.session
        assert session1 is session2
        await session1.close()

    @pytest.mark.asyncio
    async def test_session_recreates_if_closed(self, oauth_provider):
        """If session was closed, a new one is created."""
        session1 = await oauth_provider.session
        await session1.close()
        session2 = await oauth_provider.session
        assert session2 is not session1
        assert not session2.closed
        await session2.close()


# ---------------------------------------------------------------------------
# OAuthProvider.close()
# ---------------------------------------------------------------------------


class TestOAuthProviderClose:
    """Tests for close()."""

    @pytest.mark.asyncio
    async def test_close_closes_open_session(self, oauth_provider):
        """close() should close an open session."""
        session = await oauth_provider.session
        assert not session.closed
        await oauth_provider.close()
        assert session.closed

    @pytest.mark.asyncio
    async def test_close_noop_when_no_session(self, oauth_provider):
        """close() when no session was created should not raise."""
        assert oauth_provider._session is None
        await oauth_provider.close()

    @pytest.mark.asyncio
    async def test_close_noop_when_already_closed(self, oauth_provider):
        """close() when session already closed should not raise."""
        session = await oauth_provider.session
        await session.close()
        await oauth_provider.close()  # Should not raise

    @pytest.mark.asyncio
    async def test_context_manager(self, mock_config_service):
        """Async context manager calls close on exit."""
        config = _make_oauth_config()
        async with OAuthProvider(config, mock_config_service, "/path") as provider:
            session = await provider.session
            assert not session.closed
        assert session.closed


# ---------------------------------------------------------------------------
# OAuthProvider._get_authorization_url()
# ---------------------------------------------------------------------------


class TestGetAuthorizationUrl:
    """Tests for _get_authorization_url()."""

    def test_basic_url_construction(self, oauth_provider):
        """URL contains required params: client_id, redirect_uri, response_type, state."""
        url = oauth_provider._get_authorization_url(state="test-state")
        parsed = urlparse(url)
        params = parse_qs(parsed.query)

        assert parsed.scheme == "https"
        assert parsed.netloc == "auth.example.com"
        assert parsed.path == "/authorize"
        assert params["client_id"] == ["test-client-id"]
        assert params["redirect_uri"] == ["http://localhost/callback"]
        assert params["response_type"] == ["code"]
        assert params["state"] == ["test-state"]

    def test_scope_included_when_set(self, mock_config_service):
        """Scope is included when configured."""
        config = _make_oauth_config(scope="read write")
        provider = OAuthProvider(config, mock_config_service, "/path")
        url = provider._get_authorization_url(state="s")
        params = parse_qs(urlparse(url).query)
        assert params["scope"] == ["read write"]

    def test_scope_omitted_when_none(self, oauth_provider):
        """Scope param is not included when scope is None."""
        url = oauth_provider._get_authorization_url(state="s")
        params = parse_qs(urlparse(url).query)
        assert "scope" not in params

    def test_custom_scope_parameter_name(self, mock_config_service):
        """Configurable scope_parameter_name (e.g., 'user_scope')."""
        config = _make_oauth_config(scope="chat:write", scope_parameter_name="user_scope")
        provider = OAuthProvider(config, mock_config_service, "/path")
        url = provider._get_authorization_url(state="s")
        params = parse_qs(urlparse(url).query)
        assert "user_scope" in params
        assert params["user_scope"] == ["chat:write"]
        assert "scope" not in params

    def test_additional_params_included(self, mock_config_service):
        """additional_params from config are included."""
        config = _make_oauth_config(additional_params={"prompt": "consent", "access_type": "offline"})
        provider = OAuthProvider(config, mock_config_service, "/path")
        url = provider._get_authorization_url(state="s")
        params = parse_qs(urlparse(url).query)
        assert params["prompt"] == ["consent"]
        assert params["access_type"] == ["offline"]

    def test_kwargs_override(self, oauth_provider):
        """Extra kwargs are appended to the URL."""
        url = oauth_provider._get_authorization_url(state="s", custom_param="custom_value")
        params = parse_qs(urlparse(url).query)
        assert params["custom_param"] == ["custom_value"]

    def test_token_access_type_included(self, mock_config_service):
        """token_access_type is included when set."""
        config = _make_oauth_config(token_access_type="offline")
        provider = OAuthProvider(config, mock_config_service, "/path")
        url = provider._get_authorization_url(state="s")
        params = parse_qs(urlparse(url).query)
        assert params["token_access_type"] == ["offline"]


# ---------------------------------------------------------------------------
# OAuthProvider._make_token_request()
# ---------------------------------------------------------------------------


class TestMakeTokenRequest:
    """Tests for _make_token_request()."""

    @pytest.mark.asyncio
    async def test_standard_form_encoded_request(self, mock_config_service):
        """Standard flow: client_id/secret in body, form-encoded, JSON response."""
        config = _make_oauth_config()
        provider = OAuthProvider(config, mock_config_service, "/path")

        mock_response = AsyncMock()
        mock_response.status = 200
        mock_response.headers = {"Content-Type": "application/json"}
        mock_response.json = AsyncMock(return_value={"access_token": "tok123"})
        mock_response.raise_for_status = MagicMock()
        mock_response.__aenter__ = AsyncMock(return_value=mock_response)
        mock_response.__aexit__ = AsyncMock(return_value=False)

        mock_session = MagicMock()
        mock_session.closed = False
        mock_session.post = MagicMock(return_value=mock_response)

        provider._session = mock_session

        result = await provider._make_token_request({"grant_type": "authorization_code"})
        assert result == {"access_token": "tok123"}

        # Verify client_id and client_secret were added to data
        call_kwargs = mock_session.post.call_args
        post_kwargs = call_kwargs.kwargs if call_kwargs.kwargs else {}
        # The data should contain client credentials
        data = post_kwargs.get("data", {})
        assert data["client_id"] == "test-client-id"
        assert data["client_secret"] == "test-client-secret"

    @pytest.mark.asyncio
    async def test_basic_auth_for_notion(self, mock_config_service):
        """Notion-style: use_basic_auth=True sends Basic auth header."""
        config = _make_oauth_config(additional_params={
            "use_basic_auth": True,
            "notion_version": "2022-06-28",
        })
        provider = OAuthProvider(config, mock_config_service, "/path")

        mock_response = AsyncMock()
        mock_response.status = 200
        mock_response.headers = {"Content-Type": "application/json"}
        mock_response.json = AsyncMock(return_value={"access_token": "notion-tok"})
        mock_response.raise_for_status = MagicMock()
        mock_response.__aenter__ = AsyncMock(return_value=mock_response)
        mock_response.__aexit__ = AsyncMock(return_value=False)

        mock_session = MagicMock()
        mock_session.closed = False
        mock_session.post = MagicMock(return_value=mock_response)

        provider._session = mock_session

        data = {"grant_type": "authorization_code"}
        result = await provider._make_token_request(data)
        assert result == {"access_token": "notion-tok"}

        call_kwargs = mock_session.post.call_args.kwargs
        headers = call_kwargs["headers"]

        # Verify Basic Auth header
        expected_creds = base64.b64encode(b"test-client-id:test-client-secret").decode()
        assert headers["Authorization"] == f"Basic {expected_creds}"
        assert headers["Notion-Version"] == "2022-06-28"

        # client_id/secret should NOT be in body when using basic auth
        assert "client_id" not in data
        assert "client_secret" not in data

    @pytest.mark.asyncio
    async def test_basic_auth_without_notion_version(self, mock_config_service):
        """use_basic_auth=True without notion_version skips the Notion header."""
        config = _make_oauth_config(additional_params={"use_basic_auth": True})
        provider = OAuthProvider(config, mock_config_service, "/path")

        mock_response = AsyncMock()
        mock_response.status = 200
        mock_response.headers = {"Content-Type": "application/json"}
        mock_response.json = AsyncMock(return_value={"access_token": "basic-tok"})
        mock_response.raise_for_status = MagicMock()
        mock_response.__aenter__ = AsyncMock(return_value=mock_response)
        mock_response.__aexit__ = AsyncMock(return_value=False)

        mock_session = MagicMock()
        mock_session.closed = False
        mock_session.post = MagicMock(return_value=mock_response)
        provider._session = mock_session

        result = await provider._make_token_request({"grant_type": "authorization_code"})
        assert result == {"access_token": "basic-tok"}

        call_kwargs = mock_session.post.call_args.kwargs
        headers = call_kwargs["headers"]
        assert "Authorization" in headers
        assert "Notion-Version" not in headers

    @pytest.mark.asyncio
    async def test_json_body_request(self, mock_config_service):
        """use_json_body=True sends JSON body instead of form-encoded."""
        config = _make_oauth_config(additional_params={"use_json_body": True})
        provider = OAuthProvider(config, mock_config_service, "/path")

        mock_response = AsyncMock()
        mock_response.status = 200
        mock_response.headers = {"Content-Type": "application/json"}
        mock_response.json = AsyncMock(return_value={"access_token": "json-tok"})
        mock_response.raise_for_status = MagicMock()
        mock_response.__aenter__ = AsyncMock(return_value=mock_response)
        mock_response.__aexit__ = AsyncMock(return_value=False)

        mock_session = MagicMock()
        mock_session.closed = False
        mock_session.post = MagicMock(return_value=mock_response)

        provider._session = mock_session

        result = await provider._make_token_request({"grant_type": "authorization_code"})
        assert result == {"access_token": "json-tok"}

        call_kwargs = mock_session.post.call_args.kwargs
        assert "json" in call_kwargs
        assert call_kwargs["headers"]["Content-Type"] == "application/json"

    @pytest.mark.asyncio
    async def test_form_urlencoded_response(self, mock_config_service):
        """Handles form-urlencoded response (e.g., GitHub)."""
        config = _make_oauth_config()
        provider = OAuthProvider(config, mock_config_service, "/path")

        mock_response = AsyncMock()
        mock_response.status = 200
        mock_response.headers = {"Content-Type": "application/x-www-form-urlencoded"}
        mock_response.text = AsyncMock(return_value="access_token=tok&token_type=bearer&expires_in=3600")
        mock_response.raise_for_status = MagicMock()
        mock_response.__aenter__ = AsyncMock(return_value=mock_response)
        mock_response.__aexit__ = AsyncMock(return_value=False)

        mock_session = MagicMock()
        mock_session.closed = False
        mock_session.post = MagicMock(return_value=mock_response)
        provider._session = mock_session

        result = await provider._make_token_request({"grant_type": "authorization_code"})
        assert result["access_token"] == "tok"
        assert result["token_type"] == "bearer"
        assert result["expires_in"] == 3600  # Converted to int

    @pytest.mark.asyncio
    async def test_text_plain_response(self, mock_config_service):
        """Handles text/plain response (same parsing as form-urlencoded)."""
        config = _make_oauth_config()
        provider = OAuthProvider(config, mock_config_service, "/path")

        mock_response = AsyncMock()
        mock_response.status = 200
        mock_response.headers = {"Content-Type": "text/plain"}
        mock_response.text = AsyncMock(return_value="access_token=tok123")
        mock_response.raise_for_status = MagicMock()
        mock_response.__aenter__ = AsyncMock(return_value=mock_response)
        mock_response.__aexit__ = AsyncMock(return_value=False)

        mock_session = MagicMock()
        mock_session.closed = False
        mock_session.post = MagicMock(return_value=mock_response)
        provider._session = mock_session

        result = await provider._make_token_request({"grant_type": "authorization_code"})
        assert result["access_token"] == "tok123"

    @pytest.mark.asyncio
    async def test_unknown_content_type_falls_back_to_json(self, mock_config_service):
        """Unknown content type falls back to response.json()."""
        config = _make_oauth_config()
        provider = OAuthProvider(config, mock_config_service, "/path")

        mock_response = AsyncMock()
        mock_response.status = 200
        mock_response.headers = {"Content-Type": "text/xml"}
        mock_response.json = AsyncMock(return_value={"access_token": "xml-tok"})
        mock_response.raise_for_status = MagicMock()
        mock_response.__aenter__ = AsyncMock(return_value=mock_response)
        mock_response.__aexit__ = AsyncMock(return_value=False)

        mock_session = MagicMock()
        mock_session.closed = False
        mock_session.post = MagicMock(return_value=mock_response)
        provider._session = mock_session

        result = await provider._make_token_request({"grant_type": "authorization_code"})
        assert result == {"access_token": "xml-tok"}

    @pytest.mark.asyncio
    async def test_error_status_raises(self, mock_config_service):
        """HTTP 400+ raises an exception with masked client_id."""
        config = _make_oauth_config()
        provider = OAuthProvider(config, mock_config_service, "/path")

        mock_response = AsyncMock()
        mock_response.status = 400
        mock_response.text = AsyncMock(return_value='{"error": "invalid_grant"}')
        mock_response.__aenter__ = AsyncMock(return_value=mock_response)
        mock_response.__aexit__ = AsyncMock(return_value=False)

        mock_session = MagicMock()
        mock_session.closed = False
        mock_session.post = MagicMock(return_value=mock_response)
        provider._session = mock_session

        with pytest.raises(Exception, match="OAuth token request failed with status 400"):
            await provider._make_token_request({"grant_type": "authorization_code"})

    @pytest.mark.asyncio
    async def test_error_status_short_client_id_masked(self, mock_config_service):
        """Short client_id is masked as '***'."""
        config = _make_oauth_config(client_id="short")
        provider = OAuthProvider(config, mock_config_service, "/path")

        mock_response = AsyncMock()
        mock_response.status = 401
        mock_response.text = AsyncMock(return_value="unauthorized")
        mock_response.__aenter__ = AsyncMock(return_value=mock_response)
        mock_response.__aexit__ = AsyncMock(return_value=False)

        mock_session = MagicMock()
        mock_session.closed = False
        mock_session.post = MagicMock(return_value=mock_response)
        provider._session = mock_session

        with pytest.raises(Exception, match=r"\*\*\*"):
            await provider._make_token_request({"grant_type": "authorization_code"})

    @pytest.mark.asyncio
    async def test_form_urlencoded_invalid_expires_in(self, mock_config_service):
        """expires_in that can't be converted to int is left as-is."""
        config = _make_oauth_config()
        provider = OAuthProvider(config, mock_config_service, "/path")

        mock_response = AsyncMock()
        mock_response.status = 200
        mock_response.headers = {"Content-Type": "application/x-www-form-urlencoded"}
        mock_response.text = AsyncMock(return_value="access_token=tok&expires_in=not_a_number")
        mock_response.raise_for_status = MagicMock()
        mock_response.__aenter__ = AsyncMock(return_value=mock_response)
        mock_response.__aexit__ = AsyncMock(return_value=False)

        mock_session = MagicMock()
        mock_session.closed = False
        mock_session.post = MagicMock(return_value=mock_response)
        provider._session = mock_session

        result = await provider._make_token_request({"grant_type": "authorization_code"})
        assert result["access_token"] == "tok"
        assert result["expires_in"] == "not_a_number"  # Left as string


# ---------------------------------------------------------------------------
# OAuthProvider.exchange_code_for_token()
# ---------------------------------------------------------------------------


class TestExchangeCodeForToken:
    """Tests for exchange_code_for_token()."""

    @pytest.mark.asyncio
    async def test_basic_exchange(self, mock_config_service):
        """Basic code exchange returns an OAuthToken."""
        config = _make_oauth_config()
        provider = OAuthProvider(config, mock_config_service, "/path")

        provider._make_token_request = AsyncMock(return_value={
            "access_token": "new-token",
            "token_type": "Bearer",
            "expires_in": 3600,
        })

        token = await provider.exchange_code_for_token("auth-code")
        assert token.access_token == "new-token"
        assert token.token_type == "Bearer"

        # Verify the data sent to _make_token_request
        call_data = provider._make_token_request.call_args[0][0]
        assert call_data["grant_type"] == "authorization_code"
        assert call_data["code"] == "auth-code"
        assert call_data["redirect_uri"] == "http://localhost/callback"

    @pytest.mark.asyncio
    async def test_exchange_with_pkce(self, mock_config_service):
        """code_verifier is included in the request when provided."""
        config = _make_oauth_config()
        provider = OAuthProvider(config, mock_config_service, "/path")

        provider._make_token_request = AsyncMock(return_value={
            "access_token": "pkce-token",
        })

        token = await provider.exchange_code_for_token("code", code_verifier="my-verifier")
        call_data = provider._make_token_request.call_args[0][0]
        assert call_data["code_verifier"] == "my-verifier"

    @pytest.mark.asyncio
    async def test_exchange_with_normalization(self, mock_config_service):
        """Token response normalization (e.g., Slack nested path) is applied."""
        config = _make_oauth_config(token_response_path="authed_user")
        provider = OAuthProvider(config, mock_config_service, "/path")

        provider._make_token_request = AsyncMock(return_value={
            "authed_user": {
                "access_token": "nested-tok",
                "scope": "read",
            },
            "token_type": "bearer",
        })

        token = await provider.exchange_code_for_token("code")
        assert token.access_token == "nested-tok"

    @pytest.mark.asyncio
    async def test_exchange_missing_access_token_raises(self, mock_config_service):
        """Missing access_token after normalization raises ValueError."""
        config = _make_oauth_config()
        provider = OAuthProvider(config, mock_config_service, "/path")

        provider._make_token_request = AsyncMock(return_value={
            "token_type": "Bearer",
            # No access_token!
        })

        with pytest.raises(ValueError, match="missing required 'access_token'"):
            await provider.exchange_code_for_token("code")


# ---------------------------------------------------------------------------
# OAuthProvider.refresh_access_token()
# ---------------------------------------------------------------------------


class TestRefreshAccessToken:
    """Tests for refresh_access_token()."""

    @pytest.mark.asyncio
    async def test_basic_refresh(self, mock_config_service):
        """Basic refresh returns new token and updates config."""
        config = _make_oauth_config()
        provider = OAuthProvider(config, mock_config_service, "/path")

        provider._make_token_request = AsyncMock(return_value={
            "access_token": "new-access",
            "refresh_token": "new-refresh",
            "expires_in": 3600,
        })

        token = await provider.refresh_access_token("old-refresh")
        assert token.access_token == "new-access"
        assert token.refresh_token == "new-refresh"

        # Should have updated config
        mock_config_service.set_config.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_refresh_preserves_old_refresh_token(self, mock_config_service):
        """When no new refresh_token is returned (Google), old one is preserved."""
        config = _make_oauth_config()
        provider = OAuthProvider(config, mock_config_service, "/path")

        provider._make_token_request = AsyncMock(return_value={
            "access_token": "new-access",
            # No refresh_token returned
        })

        token = await provider.refresh_access_token("old-refresh-token")
        assert token.access_token == "new-access"
        assert token.refresh_token == "old-refresh-token"  # Preserved

    @pytest.mark.asyncio
    async def test_refresh_403_enhanced_error(self, mock_config_service):
        """403 error enhances error message about expired/invalid refresh token."""
        config = _make_oauth_config()
        provider = OAuthProvider(config, mock_config_service, "/path")

        provider._make_token_request = AsyncMock(
            side_effect=Exception("OAuth token request failed with status 403. Response: forbidden")
        )

        with pytest.raises(Exception, match="Token refresh failed with 403 Forbidden"):
            await provider.refresh_access_token("bad-refresh")

    @pytest.mark.asyncio
    async def test_refresh_non_403_error_reraises(self, mock_config_service):
        """Non-403 errors are re-raised without modification."""
        config = _make_oauth_config()
        provider = OAuthProvider(config, mock_config_service, "/path")

        provider._make_token_request = AsyncMock(
            side_effect=Exception("OAuth token request failed with status 500. Response: server error")
        )

        with pytest.raises(Exception, match="status 500"):
            await provider.refresh_access_token("refresh-tok")

    @pytest.mark.asyncio
    async def test_refresh_updates_credentials_in_config(self, mock_config_service):
        """refresh_access_token stores new token in config service."""
        config = _make_oauth_config()
        mock_config_service.get_config = AsyncMock(return_value={"existing": "data"})
        provider = OAuthProvider(config, mock_config_service, "/path")

        provider._make_token_request = AsyncMock(return_value={
            "access_token": "refreshed",
            "refresh_token": "new-ref",
        })

        token = await provider.refresh_access_token("old-ref")

        # Verify set_config was called with credentials
        set_call = mock_config_service.set_config.call_args
        stored_config = set_call[0][1]
        assert stored_config["credentials"]["access_token"] == "refreshed"

    @pytest.mark.asyncio
    async def test_refresh_config_not_dict_creates_new(self, mock_config_service):
        """When get_config returns non-dict, a new dict is created."""
        config = _make_oauth_config()
        mock_config_service.get_config = AsyncMock(return_value="not a dict")
        provider = OAuthProvider(config, mock_config_service, "/path")

        provider._make_token_request = AsyncMock(return_value={
            "access_token": "tok",
        })

        token = await provider.refresh_access_token("ref-tok")

        set_call = mock_config_service.set_config.call_args
        stored_config = set_call[0][1]
        assert "credentials" in stored_config

    @pytest.mark.asyncio
    async def test_refresh_error_without_status_in_msg(self, mock_config_service):
        """Error without 'status NNN' pattern is re-raised as-is."""
        config = _make_oauth_config()
        provider = OAuthProvider(config, mock_config_service, "/path")

        provider._make_token_request = AsyncMock(
            side_effect=Exception("Connection timeout")
        )

        with pytest.raises(Exception, match="Connection timeout"):
            await provider.refresh_access_token("ref-tok")


# ---------------------------------------------------------------------------
# OAuthProvider.ensure_valid_token()
# ---------------------------------------------------------------------------


class TestEnsureValidToken:
    """Tests for ensure_valid_token()."""

    @pytest.mark.asyncio
    async def test_no_token_raises(self, oauth_provider):
        """No token set raises ValueError."""
        with pytest.raises(ValueError, match="No token found"):
            await oauth_provider.ensure_valid_token()

    @pytest.mark.asyncio
    async def test_valid_token_returned_as_is(self, oauth_provider):
        """Non-expired token is returned without refreshing."""
        token = OAuthToken(
            access_token="valid",
            expires_in=3600,
            created_at=datetime.now(),
            refresh_token="ref",
        )
        oauth_provider.token = token

        result = await oauth_provider.ensure_valid_token()
        assert result is token
        assert result.access_token == "valid"

    @pytest.mark.asyncio
    async def test_expired_token_with_refresh_triggers_refresh(self, oauth_provider):
        """Expired token with refresh_token triggers refresh_access_token."""
        expired_token = OAuthToken(
            access_token="expired",
            expires_in=1,
            created_at=datetime.now() - timedelta(seconds=100),
            refresh_token="my-refresh",
        )
        oauth_provider.token = expired_token

        new_token = OAuthToken(access_token="refreshed", expires_in=3600)
        oauth_provider.refresh_access_token = AsyncMock(return_value=new_token)

        result = await oauth_provider.ensure_valid_token()
        assert result.access_token == "refreshed"
        oauth_provider.refresh_access_token.assert_awaited_once_with("my-refresh")

    @pytest.mark.asyncio
    async def test_expired_token_no_refresh_raises(self, oauth_provider):
        """Expired token without refresh_token raises ValueError."""
        expired_token = OAuthToken(
            access_token="expired",
            expires_in=1,
            created_at=datetime.now() - timedelta(seconds=100),
            refresh_token=None,
        )
        oauth_provider.token = expired_token

        with pytest.raises(ValueError, match="Token expired and no refresh token"):
            await oauth_provider.ensure_valid_token()

    @pytest.mark.asyncio
    async def test_token_without_expires_in_not_expired(self, oauth_provider):
        """Token without expires_in is never considered expired."""
        token = OAuthToken(access_token="forever", expires_in=None)
        oauth_provider.token = token

        result = await oauth_provider.ensure_valid_token()
        assert result.access_token == "forever"


# ---------------------------------------------------------------------------
# OAuthProvider.revoke_token()
# ---------------------------------------------------------------------------


class TestRevokeToken:
    """Tests for revoke_token()."""

    @pytest.mark.asyncio
    async def test_revoke_clears_credentials(self, oauth_provider, mock_config_service):
        """revoke_token clears credentials in config."""
        mock_config_service.get_config = AsyncMock(return_value={"credentials": {"access_token": "tok"}})

        result = await oauth_provider.revoke_token()
        assert result is True

        set_call = mock_config_service.set_config.call_args
        stored = set_call[0][1]
        assert stored["credentials"] is None

    @pytest.mark.asyncio
    async def test_revoke_with_non_dict_config(self, oauth_provider, mock_config_service):
        """revoke_token handles non-dict config gracefully."""
        mock_config_service.get_config = AsyncMock(return_value=None)

        result = await oauth_provider.revoke_token()
        assert result is True


# ---------------------------------------------------------------------------
# OAuthProvider._gen_code_verifier / _gen_code_challenge
# ---------------------------------------------------------------------------


class TestPKCEHelpers:
    """Tests for PKCE code verifier and challenge generation."""

    def test_gen_code_verifier_returns_string(self, oauth_provider):
        verifier = oauth_provider._gen_code_verifier()
        assert isinstance(verifier, str)
        assert len(verifier) > 0
        # Should be URL-safe base64 without padding
        assert "=" not in verifier

    def test_gen_code_challenge_returns_s256_hash(self, oauth_provider):
        verifier = "test-verifier-string"
        challenge = oauth_provider._gen_code_challenge(verifier)
        assert isinstance(challenge, str)
        assert len(challenge) > 0
        assert "=" not in challenge

    def test_gen_code_challenge_deterministic(self, oauth_provider):
        """Same verifier always produces same challenge."""
        v = "deterministic-verifier"
        c1 = oauth_provider._gen_code_challenge(v)
        c2 = oauth_provider._gen_code_challenge(v)
        assert c1 == c2


# ---------------------------------------------------------------------------
# OAuthProvider.start_authorization()
# ---------------------------------------------------------------------------


class TestStartAuthorization:
    """Tests for start_authorization()."""

    @pytest.mark.asyncio
    async def test_start_authorization_with_pkce(self, oauth_provider, mock_config_service):
        """start_authorization with PKCE stores verifier and returns URL with challenge."""
        mock_config_service.get_config = AsyncMock(return_value={})

        url = await oauth_provider.start_authorization(return_to="/dashboard")

        # URL should contain code_challenge params
        params = parse_qs(urlparse(url).query)
        assert "code_challenge" in params
        assert params["code_challenge_method"] == ["S256"]
        assert "state" in params

        # Config should have been updated with oauth session data
        set_call = mock_config_service.set_config.call_args
        stored = set_call[0][1]
        assert "oauth" in stored
        assert "state" in stored["oauth"]
        assert "code_verifier" in stored["oauth"]
        assert stored["oauth"]["pkce"] is True
        assert stored["oauth"]["return_to"] == "/dashboard"
        assert stored["oauth"]["used_codes"] == []

    @pytest.mark.asyncio
    async def test_start_authorization_without_pkce(self, oauth_provider, mock_config_service):
        """start_authorization without PKCE doesn't include challenge params."""
        mock_config_service.get_config = AsyncMock(return_value={})

        url = await oauth_provider.start_authorization(use_pkce=False)

        params = parse_qs(urlparse(url).query)
        assert "code_challenge" not in params
        assert "code_challenge_method" not in params

        set_call = mock_config_service.set_config.call_args
        stored = set_call[0][1]
        assert "code_verifier" not in stored["oauth"]

    @pytest.mark.asyncio
    async def test_start_authorization_non_dict_config(self, oauth_provider, mock_config_service):
        """start_authorization handles non-dict config from get_config."""
        mock_config_service.get_config = AsyncMock(return_value="not a dict")

        url = await oauth_provider.start_authorization()
        assert "state" in url

    @pytest.mark.asyncio
    async def test_start_authorization_extra_params(self, oauth_provider, mock_config_service):
        """Extra kwargs are passed through to URL."""
        mock_config_service.get_config = AsyncMock(return_value={})

        url = await oauth_provider.start_authorization(login_hint="user@example.com")
        params = parse_qs(urlparse(url).query)
        assert params["login_hint"] == ["user@example.com"]


# ---------------------------------------------------------------------------
# OAuthProvider.handle_callback()
# ---------------------------------------------------------------------------


class TestHandleCallback:
    """Tests for handle_callback()."""

    @pytest.mark.asyncio
    async def test_successful_callback(self, oauth_provider, mock_config_service):
        """Successful callback exchanges code for token and stores credentials."""
        mock_config_service.get_config = AsyncMock(return_value={
            "oauth": {
                "state": "valid-state",
                "code_verifier": "my-verifier",
                "used_codes": [],
            }
        })

        new_token = OAuthToken(access_token="new-tok", expires_in=3600)
        oauth_provider.exchange_code_for_token = AsyncMock(return_value=new_token)

        token = await oauth_provider.handle_callback(code="auth-code", state="valid-state")

        assert token.access_token == "new-tok"
        assert oauth_provider.token is token

        # Verify exchange was called with correct params
        oauth_provider.exchange_code_for_token.assert_awaited_once_with(
            code="auth-code", state="valid-state", code_verifier="my-verifier"
        )

        # Verify config was updated
        set_call = mock_config_service.set_config.call_args
        stored = set_call[0][1]
        assert stored["credentials"]["access_token"] == "new-tok"
        assert "auth-code" in stored["oauth"]["used_codes"]

    @pytest.mark.asyncio
    async def test_callback_state_mismatch_raises(self, oauth_provider, mock_config_service):
        """State mismatch raises ValueError."""
        mock_config_service.get_config = AsyncMock(return_value={
            "oauth": {
                "state": "stored-state",
                "used_codes": [],
            }
        })

        with pytest.raises(ValueError, match="Invalid or expired state"):
            await oauth_provider.handle_callback(code="code", state="wrong-state")

    @pytest.mark.asyncio
    async def test_callback_no_stored_state_raises(self, oauth_provider, mock_config_service):
        """No stored state raises ValueError."""
        mock_config_service.get_config = AsyncMock(return_value={
            "oauth": {"used_codes": []},
        })

        with pytest.raises(ValueError, match="Invalid or expired state"):
            await oauth_provider.handle_callback(code="code", state="any-state")

    @pytest.mark.asyncio
    async def test_callback_state_mismatch_duplicate_with_valid_creds(self, oauth_provider, mock_config_service):
        """State mismatch but code already used with valid creds returns existing token."""
        mock_config_service.get_config = AsyncMock(return_value={
            "oauth": {
                "state": "old-state",
                "used_codes": ["auth-code"],
            },
            "credentials": {
                "access_token": "existing-tok",
                "token_type": "Bearer",
            }
        })

        token = await oauth_provider.handle_callback(code="auth-code", state="different-state")
        assert token.access_token == "existing-tok"

    @pytest.mark.asyncio
    async def test_callback_state_mismatch_duplicate_malformed_creds(self, oauth_provider, mock_config_service):
        """State mismatch, code used, but creds are malformed raises ValueError."""
        mock_config_service.get_config = AsyncMock(return_value={
            "oauth": {
                "state": "old-state",
                "used_codes": ["auth-code"],
            },
            "credentials": {
                "access_token": "tok",
                # Will cause OAuthToken.from_dict to fail with TypeError by passing bad created_at
            }
        })

        # We need to make from_dict raise. Patch it.
        with patch.object(OAuthToken, "from_dict", side_effect=TypeError("bad data")):
            with pytest.raises(ValueError, match="Invalid or expired state"):
                await oauth_provider.handle_callback(code="auth-code", state="different-state")

    @pytest.mark.asyncio
    async def test_callback_code_already_used_with_valid_creds(self, oauth_provider, mock_config_service):
        """State matches, but code was already used with valid creds returns existing token."""
        mock_config_service.get_config = AsyncMock(return_value={
            "oauth": {
                "state": "valid-state",
                "used_codes": ["auth-code"],
            },
            "credentials": {
                "access_token": "existing-tok",
                "token_type": "Bearer",
            }
        })

        token = await oauth_provider.handle_callback(code="auth-code", state="valid-state")
        assert token.access_token == "existing-tok"

    @pytest.mark.asyncio
    async def test_callback_code_already_used_no_valid_creds(self, oauth_provider, mock_config_service):
        """State matches, code used, but no valid credentials raises error."""
        mock_config_service.get_config = AsyncMock(return_value={
            "oauth": {
                "state": "valid-state",
                "used_codes": ["auth-code"],
            },
            "credentials": None,
        })

        with pytest.raises(ValueError, match="Authorization code has already been used"):
            await oauth_provider.handle_callback(code="auth-code", state="valid-state")

    @pytest.mark.asyncio
    async def test_callback_code_already_used_malformed_creds(self, oauth_provider, mock_config_service):
        """State matches, code used, creds exist but from_dict fails."""
        mock_config_service.get_config = AsyncMock(return_value={
            "oauth": {
                "state": "valid-state",
                "used_codes": ["auth-code"],
            },
            "credentials": {
                "access_token": "tok",
            }
        })

        with patch.object(OAuthToken, "from_dict", side_effect=TypeError("bad")):
            with pytest.raises(ValueError, match="Authorization code has already been used"):
                await oauth_provider.handle_callback(code="auth-code", state="valid-state")

    @pytest.mark.asyncio
    async def test_callback_exchange_failure_marks_code_used(self, oauth_provider, mock_config_service):
        """When exchange_code_for_token fails, code is still marked as used."""
        mock_config_service.get_config = AsyncMock(return_value={
            "oauth": {
                "state": "valid-state",
                "used_codes": [],
            }
        })

        oauth_provider.exchange_code_for_token = AsyncMock(
            side_effect=Exception("exchange failed")
        )

        with pytest.raises(Exception, match="exchange failed"):
            await oauth_provider.handle_callback(code="auth-code", state="valid-state")

        # Config should have been updated with used_codes
        set_call = mock_config_service.set_config.call_args
        stored = set_call[0][1]
        assert "auth-code" in stored["oauth"]["used_codes"]

    @pytest.mark.asyncio
    async def test_callback_non_dict_config(self, oauth_provider, mock_config_service):
        """handle_callback with non-dict config from get_config raises due to no state."""
        mock_config_service.get_config = AsyncMock(return_value="not a dict")

        with pytest.raises(ValueError, match="Invalid or expired state"):
            await oauth_provider.handle_callback(code="code", state="state")

    @pytest.mark.asyncio
    async def test_callback_none_oauth_data(self, oauth_provider, mock_config_service):
        """handle_callback with None oauth data uses empty dict fallback."""
        mock_config_service.get_config = AsyncMock(return_value={
            "oauth": None,
        })

        with pytest.raises(ValueError, match="Invalid or expired state"):
            await oauth_provider.handle_callback(code="code", state="state")
