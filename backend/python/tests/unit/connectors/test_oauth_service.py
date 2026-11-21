"""Tests for app.connectors.core.base.token_service.oauth_service"""

import base64
import logging
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch
from urllib.parse import parse_qs, urlparse

import pytest

from app.connectors.core.base.token_service import oauth_service
from app.connectors.core.base.token_service.oauth_service import (
    OAuthConfig,
    OAuthProvider,
    OAuthStateError,
    OAuthStoreUnavailableError,
    OAuthToken,
    OAuthTokenRequestError,
    RefreshTokenInvalidError,
)
from app.utils.encryption.encryption_service import DecryptionError
from app.utils.error_class import classify_error
from app.utils.logger import JsonFormatter, TextFormatter, log_record_fields
from app.utils.oauth_config import extract_oauth_error_message, get_oauth_config
from app.utils.oauth_errors import MAX_TOKEN_LIFETIME_S
from tests.support.refusing_token_endpoint import (
    ENTRA_INTERACTION_REQUIRED,
    ENTRA_INVALID_CLIENT,
    RefusingTokenEndpoint,
)
from tests.support.slack_oauth import TOKEN_URL as SLACK_TOKEN_URL
from tests.support.slack_oauth import FakeSlackOAuth

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
            "id",
        }
        assert set(d.keys()) == expected_keys

    def test_salesforce_identity_url_survives_storage(self) -> None:
        """Salesforce names the authenticated user only through the token's identity URL."""
        url = "https://login.salesforce.com/id/00Dx0000000BV7zEAG/005x00000012Q9PAAU"
        token = OAuthToken.from_dict({"access_token": "tok", "id": url})
        assert OAuthToken.from_dict(token.to_dict()).id == url

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
# OAuthToken.from_dict: field types (a provider's answer or a saved record)
# ---------------------------------------------------------------------------

MARKER = "FIELD-MARKER-31"
_NOW_TOLERANCE = timedelta(seconds=5)


def _malformed(data: dict) -> oauth_service.MalformedTokenError:
    with pytest.raises(oauth_service.MalformedTokenError) as raised:
        OAuthToken.from_dict({"access_token": "tok", **data})
    return raised.value


class TestFromDictFieldTypes:
    def test_a_bad_created_at_names_the_field_never_the_value(self) -> None:
        error = _malformed({"created_at": MARKER})

        assert error.field_name == "created_at"
        assert MARKER not in str(error) and str(error) == "OAuth token field 'created_at' is malformed"
        assert error.__context__ is None and error.__cause__ is None  # the stdlib's text, which quotes the value, is not chained
        assert isinstance(error, ValueError)  # callers that catch ValueError are unchanged
        assert error.refresh_reason == "malformed_token"

    # The offsets at datetime's ends overflow on the way through UTC, whatever the host's zone.
    @pytest.mark.parametrize("value", [
        10**15, -10**15, True, [1], {"a": 1}, float("nan"), "0001-01-01T00:00:00+01:00", "9999-12-31T23:59:59-01:00",
    ], ids=["year_too_big", "year_too_small", "bool", "list", "dict", "nan", "offset_before_the_start", "offset_past_the_end"])
    def test_a_created_at_that_is_no_time_is_malformed(self, value: object) -> None:
        error = _malformed({"created_at": value})

        assert error.field_name == "created_at" and "year" not in str(error)
        assert error.__context__ is None  # the stdlib's text is not chained

    # Two days in from each end: inside the range in every zone from UTC-14 to UTC+14.
    @pytest.mark.parametrize("value", ["0001-01-02T12:00:00+01:00", "9999-12-30T12:00:00-01:00"], ids=["near_the_start", "near_the_end"])
    def test_an_offset_near_datetimes_ends_is_kept(self, value: str) -> None:
        expected = datetime.fromisoformat(value).astimezone().replace(tzinfo=None)

        assert OAuthToken.from_dict({"access_token": "tok", "created_at": value}).created_at == expected

    # OAuthToken's convention is naive local time, hence the naive datetimes below.
    @pytest.mark.parametrize(("value", "expected"), [
        ("2025-06-15T12:30:00", datetime(2025, 6, 15, 12, 30)),  # noqa: DTZ001
        (1_750_000_000, datetime.fromtimestamp(1_750_000_000)),  # noqa: DTZ006
        (1_750_000_000.5, datetime.fromtimestamp(1_750_000_000.5)),  # noqa: DTZ006
        (datetime(2025, 1, 1), datetime(2025, 1, 1)),  # noqa: DTZ001
    ], ids=["iso", "epoch_int", "epoch_float", "datetime"])
    def test_a_created_at_that_is_a_time_is_kept(self, value: object, expected: datetime) -> None:
        assert OAuthToken.from_dict({"access_token": "tok", "created_at": value}).created_at == expected

    def test_an_iso_created_at_with_an_offset_becomes_naive_local_time(self) -> None:
        # A non-zero offset: on a UTC host, dropping the zone without converting would still pass with +00:00.
        offset = timezone(timedelta(hours=5, minutes=30))
        token = OAuthToken.from_dict({"access_token": "tok", "expires_in": 3600, "created_at": "2026-01-01T00:00:00+05:30"})

        assert token.created_at == datetime(2026, 1, 1, tzinfo=offset).astimezone().replace(tzinfo=None)
        assert token.created_at.tzinfo is None
        assert token.is_expired is True  # an aware value would raise TypeError against the naive datetime.now()
        assert token.expires_at_epoch == int(datetime(2026, 1, 1, 1, tzinfo=offset).timestamp())

    @pytest.mark.parametrize("data", [{}, {"created_at": None}, {"created_at": ""}, {"created_at": "  "}],
                             ids=["absent", "null", "empty", "blank"])
    def test_an_empty_created_at_is_absent_so_now(self, data: dict) -> None:
        before = datetime.now()  # noqa: DTZ005 - OAuthToken is naive
        token = OAuthToken.from_dict({"access_token": "tok", **data})

        assert before <= token.created_at <= datetime.now() + _NOW_TOLERANCE  # noqa: DTZ005

    @pytest.mark.parametrize("name", ["expires_in", "refresh_token_expires_in"])
    @pytest.mark.parametrize(("value", "expected"), [
        ("3600", 3600), (" 3600 ", 3600), (3600, 3600), (3599.5, 3599.5),
        (None, None), ("", None), ("  ", None),
        (MAX_TOKEN_LIFETIME_S, MAX_TOKEN_LIFETIME_S),
    ], ids=["numeric_text", "padded_text", "int", "float", "null", "empty", "blank", "the_bound"])
    def test_a_lifetime_that_is_a_number_of_seconds_is_kept(self, name: str, value: object, expected: object) -> None:
        token = OAuthToken.from_dict({"access_token": "tok", name: value})

        assert getattr(token, name) == expected
        assert token.is_expired is False  # usable: created_at + the lifetime is inside datetime's range

    @pytest.mark.parametrize("value", [
        f"soon-{MARKER}", True, [3600], "3600.5", float("nan"), float("inf"), float("-inf"), 10**20,
        MAX_TOKEN_LIFETIME_S + 1,
    ], ids=["text", "bool", "list", "decimal_text", "nan", "inf", "-inf", "huge", "past_the_bound"])
    def test_an_expires_in_that_is_no_number_of_seconds_is_malformed(self, value: object) -> None:
        error = _malformed({"expires_in": value})

        assert error.field_name == "expires_in"
        assert MARKER not in str(error) and error.__context__ is None

    @pytest.mark.parametrize("value", [f"soon-{MARKER}", float("nan"), 10**20], ids=["text", "nan", "huge"])
    def test_a_bad_refresh_token_lifetime_is_dropped_with_one_warning_without_its_value(
        self, value: object, caplog: pytest.LogCaptureFixture,
    ) -> None:
        caplog.set_level(logging.DEBUG, logger=oauth_service.__name__)

        tokens = [OAuthToken.from_dict({"access_token": "tok", "refresh_token_expires_in": value}) for _ in range(2)]

        assert [t.refresh_token_expires_in for t in tokens] == [None, None]  # nothing reads it: no lockout
        [line] = [r for r in caplog.records if r.name == oauth_service.__name__]  # the second parse is in the same hour
        assert (line.getMessage(), line.levelno, log_record_fields(line)) == (
            "oauth_token.field_ignored", logging.WARNING, {"field": "refresh_token_expires_in"})
        assert MARKER not in TextFormatter().format(line) + JsonFormatter().format(line)

    @pytest.mark.parametrize(("created_at", "expires_in"), [
        (datetime.max, 3600), (datetime.min, -3600),
    ], ids=["past_the_end", "before_the_start"])
    def test_a_created_at_whose_expiry_leaves_datetimes_range_is_malformed(self, created_at: datetime, expires_in: int) -> None:
        error = _malformed({"created_at": created_at, "expires_in": expires_in})

        assert error.field_name == "created_at"
        assert error.__cause__ is None and error.__suppress_context__  # a traceback shows no chained OverflowError

    @pytest.mark.parametrize("expires_in", [3600, -3600, 0])
    def test_a_created_at_at_datetimes_edge_with_room_for_the_lifetime_is_kept(self, expires_in: int) -> None:
        created_at = (datetime.max - timedelta(hours=1)) if expires_in >= 0 else (datetime.min + timedelta(hours=1))
        token = OAuthToken.from_dict({"access_token": "tok", "created_at": created_at, "expires_in": expires_in})

        assert token.is_expired is (expires_in < 0)

    def test_from_dict_does_not_mutate_input_it_corrects(self) -> None:
        data = {"access_token": "tok", "created_at": "", "expires_in": "3600", "refresh_token_expires_in": "x"}
        original = dict(data)

        OAuthToken.from_dict(data)

        assert data == original


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
    svc.create_config_if_absent = AsyncMock(return_value=True)  # the callback's claim on its code
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

    async def test_session_reads_a_header_longer_than_aiohttps_default(self, oauth_provider, monkeypatch) -> None:
        """GitHub's token endpoint sends a ~9 KB Content-Security-Policy header."""
        from aiohttp import web
        from aiohttp.test_utils import TestServer

        # The test server is on loopback, which the address guard refuses.
        monkeypatch.setattr(oauth_service, "guarded_session_kwargs", lambda **_: {})

        async def token(_request: web.Request) -> web.Response:
            return web.json_response({"access_token": "t"}, headers={"Content-Security-Policy": "a" * 9243})

        app = web.Application()
        app.router.add_post("/token", token)
        async with TestServer(app) as server:
            session = await oauth_provider.session
            async with session.post(server.make_url("/token")) as response:
                assert (await response.json())["access_token"] == "t"
            await session.close()


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

    def test_token_access_type_left_out_when_unset(self, oauth_provider) -> None:
        """Only Dropbox-style providers take token_access_type; the rest must not get "None"."""
        url = oauth_provider._get_authorization_url(state="s")
        params = parse_qs(urlparse(url).query)
        assert "token_access_type" not in params, params

    def test_unset_extra_params_are_left_out(self, mock_config_service) -> None:
        config = _make_oauth_config(additional_params={"prompt": None, "access_type": "offline"})
        provider = OAuthProvider(config, mock_config_service, "/path")
        url = provider._get_authorization_url(state="s", login_hint=None)
        params = parse_qs(urlparse(url).query)
        assert "prompt" not in params, params
        assert "login_hint" not in params, params
        assert params["access_type"] == ["offline"]

    @pytest.mark.asyncio
    async def test_jira_authorize_url_carries_no_none_values(self, mock_config_service) -> None:
        """The connector authorize route builds Jira's URL this way; the nightly saw token_access_type=None."""
        config = get_oauth_config({
            "clientId": "jira-client",
            "clientSecret": "jira-secret",
            "redirectUri": "http://localhost:3000/connectors/oauth/callback/Jira",
            "authorizeUrl": "https://auth.atlassian.com/authorize",
            "tokenUrl": "https://auth.atlassian.com/oauth/token",
            "scopes": ["read:jira-work", "offline_access"],
            "additionalParams": {"audience": "api.atlassian.com", "prompt": "consent"},
        })
        provider = OAuthProvider(config, mock_config_service, "/path")
        url = await provider.start_authorization()
        params = parse_qs(urlparse(url).query)
        assert "token_access_type" not in params, params
        assert [key for key, values in params.items() if "None" in values] == [], params
        assert params["audience"] == ["api.atlassian.com"]


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
        assert result["expires_in"] == "3600"  # the text as sent: OAuthToken.from_dict makes it a number
        assert OAuthToken.from_dict(result).expires_in == 3600

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
        """The raw answer keeps an expires_in that is not a number; OAuthToken.from_dict is what rejects it."""
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
        with pytest.raises(oauth_service.MalformedTokenError):
            OAuthToken.from_dict(result)


class TestFormEncodedRefreshRefusal:
    """GitHub answers in form encoding unless asked for JSON, and reports errors with HTTP 200."""

    @staticmethod
    def _provider(mock_config_service, status: int, body: str, content_type: str) -> OAuthProvider:
        provider = OAuthProvider(_make_oauth_config(), mock_config_service, "/path")
        mock_response = AsyncMock()
        mock_response.status = status
        mock_response.headers = {"Content-Type": content_type}
        mock_response.text = AsyncMock(return_value=body)
        mock_response.raise_for_status = MagicMock()
        mock_response.__aenter__ = AsyncMock(return_value=mock_response)
        mock_response.__aexit__ = AsyncMock(return_value=False)
        provider._session = MagicMock(closed=False, post=MagicMock(return_value=mock_response))
        return provider

    @pytest.mark.asyncio
    @pytest.mark.parametrize("status", [200, 401])
    @pytest.mark.parametrize("code", ["incorrect_client_credentials", "bad_refresh_token"])
    async def test_a_permanent_form_encoded_code_is_permanent(self, mock_config_service, status, code) -> None:
        provider = self._provider(
            mock_config_service, status, f"error={code}&error_description=nope", "application/x-www-form-urlencoded",
        )
        with pytest.raises(RefreshTokenInvalidError, match=code):
            await provider.refresh_access_token("old-refresh")

    @pytest.mark.asyncio
    @pytest.mark.parametrize("status", [200, 400])
    async def test_a_transient_form_encoded_code_stays_retryable(self, mock_config_service, status) -> None:
        provider = self._provider(
            mock_config_service, status, "error=temporarily_unavailable", "application/x-www-form-urlencoded",
        )
        with pytest.raises(OAuthTokenRequestError) as exc_info:
            await provider.refresh_access_token("old-refresh")
        assert not isinstance(exc_info.value, RefreshTokenInvalidError)
        assert exc_info.value.error_code == "temporarily_unavailable"

    @pytest.mark.parametrize(("body", "code"), [
        ('{"error": "incorrect_client_credentials"}', "incorrect_client_credentials"),
        ("error=incorrect_client_credentials&error_uri=x", "incorrect_client_credentials"),
        ("<html>502 Bad Gateway</html>", None),
        ("", None),
        ("error=", None),
        ('{"error": 5}', None),
    ])
    def test_body_error_code_reads_json_and_form_bodies(self, body: str, code) -> None:
        assert oauth_service._body_error_code(body) == code


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

    @pytest.mark.asyncio
    async def test_exchange_missing_access_token_is_tagged_for_the_refresh_lines(self, mock_config_service) -> None:
        provider = OAuthProvider(_make_oauth_config(), mock_config_service, "/path")
        provider._make_token_request = AsyncMock(return_value={"token_type": "Bearer"})

        with pytest.raises(oauth_service.TokenResponseError) as raised:
            await provider.exchange_code_for_token("code")
        assert isinstance(raised.value, ValueError)  # callers that catch ValueError are unchanged


# ---------------------------------------------------------------------------
# OAuthProvider.refresh_access_token()
# ---------------------------------------------------------------------------


class TestRefreshAccessToken:
    """Tests for refresh_access_token()."""

    @pytest.mark.parametrize("token_response_path", [None, "authed_user"], ids=["top_level", "nested_path"])
    @pytest.mark.parametrize("body", [{"token_type": "bearer"}, [{"access_token": "in-a-list"}]], ids=["no_access_token", "json_list"])
    @pytest.mark.asyncio
    async def test_a_success_answer_without_an_access_token_is_a_tagged_token_response_error(
        self, mock_config_service, body: object, token_response_path: str | None,
    ) -> None:
        provider = OAuthProvider(
            _make_oauth_config(token_response_path=token_response_path), mock_config_service, "/path",
        )
        provider._make_token_request = AsyncMock(return_value=body)

        with pytest.raises(oauth_service.TokenResponseError, match="missing required 'access_token'") as raised:
            await provider.refresh_access_token("old-refresh")
        assert raised.value.refresh_reason == "no_access_token"
        mock_config_service.set_config.assert_not_awaited()

    @pytest.mark.parametrize("field", ["created_at", "expires_in"])
    @pytest.mark.asyncio
    async def test_a_success_answer_with_a_malformed_field_saves_nothing(self, mock_config_service, field: str) -> None:
        provider = OAuthProvider(_make_oauth_config(), mock_config_service, "/path")
        provider._make_token_request = AsyncMock(return_value={"access_token": "new", "expires_in": 3600, field: MARKER})

        with pytest.raises(oauth_service.MalformedTokenError) as raised:
            await provider.refresh_access_token("old-refresh")
        assert (raised.value.field_name, raised.value.refresh_reason) == (field, "malformed_token")
        assert MARKER not in str(raised.value)
        mock_config_service.set_config.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_a_form_encoded_answer_saves_expires_in_as_a_number(self, mock_config_service) -> None:
        provider = OAuthProvider(_make_oauth_config(), mock_config_service, "/path")
        response = AsyncMock(status=200, headers={"Content-Type": "application/x-www-form-urlencoded"})
        response.text = AsyncMock(return_value="access_token=new&expires_in=3600")
        response.raise_for_status = MagicMock()
        response.__aenter__ = AsyncMock(return_value=response)
        response.__aexit__ = AsyncMock(return_value=False)
        provider._session = MagicMock(closed=False, post=MagicMock(return_value=response))

        token = await provider.refresh_access_token("old-refresh")

        assert token.expires_in == 3600
        assert mock_config_service.set_config.call_args[0][1]["credentials"]["expires_in"] == 3600

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
            side_effect=OAuthTokenRequestError("OAuth token request failed with status 403. Response: forbidden", status=403)
        )

        with pytest.raises(Exception, match="Token refresh failed with 403 Forbidden"):
            await provider.refresh_access_token("bad-refresh")

    @pytest.mark.asyncio
    async def test_refresh_atlassian_403_raises_refresh_token_invalid_error(self, mock_config_service):
        """Atlassian-style 403 with refresh_token marker raises the typed error."""
        config = _make_oauth_config()
        provider = OAuthProvider(config, mock_config_service, "/path")

        provider._make_token_request = AsyncMock(
            side_effect=OAuthTokenRequestError(
                'OAuth token request failed with status 403. '
                'Response: {"error":"unauthorized_client","error_description":"refresh_token is invalid"}',
                status=403,
                error_code="unauthorized_client",
            )
        )

        with pytest.raises(RefreshTokenInvalidError):
            await provider.refresh_access_token("bad-refresh")

    @pytest.mark.asyncio
    async def test_refresh_bare_403_is_not_permanent(self, mock_config_service):
        """403 without a token-specific marker (WAF block, misconfig) must not deactivate."""
        config = _make_oauth_config()
        provider = OAuthProvider(config, mock_config_service, "/path")

        provider._make_token_request = AsyncMock(
            side_effect=OAuthTokenRequestError("OAuth token request failed with status 403. Response: forbidden", status=403)
        )

        with pytest.raises(Exception, match="403 Forbidden") as exc_info:
            await provider.refresh_access_token("bad-refresh")
        assert not isinstance(exc_info.value, RefreshTokenInvalidError)

    @pytest.mark.asyncio
    async def test_refresh_invalid_grant_raises_refresh_token_invalid_error(self, mock_config_service):
        """invalid_grant (Google/Microsoft style, status 400) raises the typed error."""
        config = _make_oauth_config()
        provider = OAuthProvider(config, mock_config_service, "/path")

        provider._make_token_request = AsyncMock(
            side_effect=OAuthTokenRequestError(
                'OAuth token request failed with status 400. Response: {"error":"invalid_grant"}',
                status=400,
                error_code="invalid_grant",
            )
        )

        with pytest.raises(RefreshTokenInvalidError, match="invalid_grant"):
            await provider.refresh_access_token("expired-refresh")

    @pytest.mark.asyncio
    async def test_refresh_servicenow_401_raises_refresh_token_invalid_error(self, mock_config_service):
        """ServiceNow signals a dead refresh token as 401 server_error/access_denied."""
        config = _make_oauth_config(token_url="https://dev293310.service-now.com/oauth_token.do")
        provider = OAuthProvider(config, mock_config_service, "/path")

        provider._make_token_request = AsyncMock(
            side_effect=OAuthTokenRequestError(
                'OAuth token request failed with status 401. '
                'Token URL: https://dev293310.service-now.com/oauth_token.do, '
                'Response: {"error_description":"access_denied","error":"server_error"}',
                status=401,
                error_code="server_error",
            )
        )

        with pytest.raises(RefreshTokenInvalidError):
            await provider.refresh_access_token("expired-refresh")

    @pytest.mark.asyncio
    async def test_refresh_servicenow_body_on_other_provider_is_not_permanent(self, mock_config_service):
        """server_error/access_denied is ambiguous outside ServiceNow and must stay transient."""
        config = _make_oauth_config()
        provider = OAuthProvider(config, mock_config_service, "/path")

        provider._make_token_request = AsyncMock(
            side_effect=OAuthTokenRequestError(
                'OAuth token request failed with status 401. '
                'Response: {"error_description":"access_denied","error":"server_error"}',
                status=401,
                error_code="server_error",
            )
        )

        with pytest.raises(Exception) as exc_info:
            await provider.refresh_access_token("expired-refresh")
        assert not isinstance(exc_info.value, RefreshTokenInvalidError)

    @pytest.mark.asyncio
    async def test_refresh_non_403_error_reraises(self, mock_config_service):
        """Non-403 errors are re-raised without modification."""
        config = _make_oauth_config()
        provider = OAuthProvider(config, mock_config_service, "/path")

        provider._make_token_request = AsyncMock(
            side_effect=OAuthTokenRequestError("OAuth token request failed with status 500. Response: server error", status=500)
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
    async def test_a_sign_in_answer_with_a_malformed_created_at_saves_no_credentials(
        self, oauth_provider, mock_config_service,
    ) -> None:
        mock_config_service.get_config = AsyncMock(return_value={"oauth": {"state": "valid-state", "used_codes": []}})
        oauth_provider._make_token_request = AsyncMock(return_value={"access_token": "new", "created_at": MARKER})

        with pytest.raises(oauth_service.MalformedTokenError) as raised:
            await oauth_provider.handle_callback(code="auth-code", state="valid-state")

        assert raised.value.field_name == "created_at"
        chain = [raised.value, raised.value.__cause__, raised.value.__context__]
        assert not any(MARKER in str(link) for link in chain if link is not None)
        assert not any("credentials" in call.args[1] for call in mock_config_service.set_config.call_args_list)

    @pytest.mark.asyncio
    async def test_callback_state_mismatch_raises(self, oauth_provider, mock_config_service):
        """State mismatch raises ValueError."""
        mock_config_service.get_config = AsyncMock(return_value={
            "oauth": {
                "state": "stored-state",
                "used_codes": [],
            }
        })

        with pytest.raises(OAuthStateError, match="Invalid or expired state"):
            await oauth_provider.handle_callback(code="code", state="wrong-state")
        mock_config_service.set_config.assert_not_called()  # a refused callback saves nothing

    def test_state_error_is_still_a_value_error(self) -> None:
        # Callers that catch ValueError, and the user message read from the text, keep working.
        assert issubclass(OAuthStateError, ValueError)

    @pytest.mark.asyncio
    async def test_callback_no_stored_state_raises(self, oauth_provider, mock_config_service):
        """No stored state raises ValueError."""
        mock_config_service.get_config = AsyncMock(return_value={
            "oauth": {"used_codes": []},
        })

        with pytest.raises(OAuthStateError, match="Invalid or expired state"):
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
            with pytest.raises(ValueError, match="Invalid or expired state") as raised:
                await oauth_provider.handle_callback(code="auth-code", state="different-state")
            # The earlier sign-in succeeded and its credentials no longer load: the callback signs the connector out.
            assert not isinstance(raised.value, OAuthStateError)

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

        with pytest.raises(OAuthStateError, match="Authorization code has already been used"):
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
            with pytest.raises(ValueError, match="Authorization code has already been used") as raised:
                await oauth_provider.handle_callback(code="auth-code", state="valid-state")
        # Saved credentials that no longer load sign the connector out; only a replay with nothing saved is a refusal.
        assert not isinstance(raised.value, OAuthStateError)

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

        with pytest.raises(OAuthStateError, match="Invalid or expired state"):
            await oauth_provider.handle_callback(code="code", state="state")

    @pytest.mark.asyncio
    async def test_callback_none_oauth_data(self, oauth_provider, mock_config_service):
        """handle_callback with None oauth data uses empty dict fallback."""
        mock_config_service.get_config = AsyncMock(return_value={
            "oauth": None,
        })

        with pytest.raises(OAuthStateError, match="Invalid or expired state"):
            await oauth_provider.handle_callback(code="code", state="state")


def _matching() -> dict:
    """A sign-in in progress (a fresh copy: the callback appends to its used_codes)."""
    return {"oauth": {"state": "valid-state", "used_codes": []}, "credentials": {"access_token": "old-tok"}}


class TestHandleCallbackStore:
    """The callback's own store: an outage is told apart from "nothing stored", and only one callback exchanges a code."""

    @pytest.mark.asyncio
    async def test_the_config_is_read_asking_the_store_to_raise(self, oauth_provider, mock_config_service) -> None:
        mock_config_service.get_config = AsyncMock(side_effect=ConnectionError("kv down"))
        oauth_provider.exchange_code_for_token = AsyncMock()

        with pytest.raises(OAuthStoreUnavailableError) as raised:
            await oauth_provider.handle_callback(code="auth-code", state="valid-state")

        assert mock_config_service.get_config.await_args.kwargs == {"raise_on_error": True}
        assert isinstance(raised.value.__cause__, ConnectionError)
        oauth_provider.exchange_code_for_token.assert_not_awaited()
        mock_config_service.create_config_if_absent.assert_not_awaited()
        mock_config_service.set_config.assert_not_awaited()

    @pytest.mark.asyncio
    @pytest.mark.parametrize("unreadable", [ValueError("Stored value could not be decoded"),
                                            DecryptionError("Decryption failed")])
    async def test_a_value_that_does_not_decode_reads_as_unset_not_as_an_outage(
        self, oauth_provider, mock_config_service, unreadable: Exception,
    ) -> None:
        mock_config_service.get_config = AsyncMock(side_effect=unreadable)

        with pytest.raises(OAuthStateError, match="Invalid or expired state"):
            await oauth_provider.handle_callback(code="auth-code", state="valid-state")

    @pytest.mark.asyncio
    async def test_the_code_is_claimed_once_hashed_and_expiring(self, oauth_provider, mock_config_service) -> None:
        mock_config_service.get_config = AsyncMock(return_value=_matching())
        oauth_provider.exchange_code_for_token = AsyncMock(return_value=OAuthToken(access_token="new-tok"))

        await oauth_provider.handle_callback(code="CODE-MARKER", state="valid-state")

        (key, value), kwargs = mock_config_service.create_config_if_absent.await_args
        assert key.startswith("/services/oauth-code-claims/") and "CODE-MARKER" not in key
        assert (value, kwargs) == ({"claimed": True}, {"ttl_seconds": 600})
        assert key == oauth_service._code_claim_path("/test/creds/path", "CODE-MARKER")
        assert key != oauth_service._code_claim_path("/other/creds/path", "CODE-MARKER")
        assert key != oauth_service._code_claim_path("/test/creds/path", "other-code")

    @pytest.mark.asyncio
    async def test_a_code_another_callback_claimed_is_refused_without_an_exchange_or_a_write(
        self, oauth_provider, mock_config_service,
    ) -> None:
        mock_config_service.get_config = AsyncMock(return_value=_matching())
        mock_config_service.create_config_if_absent = AsyncMock(return_value=False)
        oauth_provider.exchange_code_for_token = AsyncMock()

        with pytest.raises(OAuthStateError, match="already submitted") as raised:
            await oauth_provider.handle_callback(code="auth-code", state="valid-state")

        assert "start a new sign-in" in extract_oauth_error_message(raised.value)
        oauth_provider.exchange_code_for_token.assert_not_awaited()
        mock_config_service.set_config.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_a_claim_the_store_cannot_make_is_unavailable(self, oauth_provider, mock_config_service) -> None:
        mock_config_service.get_config = AsyncMock(return_value=_matching())
        mock_config_service.create_config_if_absent = AsyncMock(side_effect=ConnectionError("kv down"))
        oauth_provider.exchange_code_for_token = AsyncMock()

        with pytest.raises(OAuthStoreUnavailableError):
            await oauth_provider.handle_callback(code="auth-code", state="valid-state")

        oauth_provider.exchange_code_for_token.assert_not_awaited()
        mock_config_service.set_config.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_credentials_the_store_did_not_save_are_unavailable_not_a_token(
        self, oauth_provider, mock_config_service,
    ) -> None:
        mock_config_service.get_config = AsyncMock(return_value=_matching())
        mock_config_service.set_config = AsyncMock(return_value=False)
        oauth_provider.exchange_code_for_token = AsyncMock(return_value=OAuthToken(access_token="new-tok"))

        with pytest.raises(OAuthStoreUnavailableError, match="could not be saved"):
            await oauth_provider.handle_callback(code="auth-code", state="valid-state")

        # Only the save of the new credentials: the exchange's failure path, which would re-save, does not run.
        (call,) = mock_config_service.set_config.await_args_list
        assert call.args[1]["credentials"]["access_token"] == "new-tok"

    @pytest.mark.asyncio
    async def test_a_refresh_after_a_finished_sign_in_reuses_the_token_without_a_claim(
        self, oauth_provider, mock_config_service,
    ) -> None:
        mock_config_service.get_config = AsyncMock(return_value={
            "oauth": {"used_codes": ["auth-code"]}, "credentials": {"access_token": "saved-tok"}})

        token = await oauth_provider.handle_callback(code="auth-code", state="valid-state")

        assert token.access_token == "saved-tok"
        mock_config_service.create_config_if_absent.assert_not_awaited()

    def test_an_outage_is_classed_unavailable_and_answered_with_a_retry_message(self) -> None:
        error = OAuthStoreUnavailableError("The OAuth config could not be read")
        assert classify_error(error) == "unavailable"
        assert "try again" in extract_oauth_error_message(error)


class TestSlackTokenEndpoint:
    """Slack reports token-endpoint failures as HTTP 200 with ok=false."""

    @staticmethod
    def _provider(mock_config_service) -> OAuthProvider:
        config = _make_oauth_config(token_url=SLACK_TOKEN_URL, token_response_path="authed_user")
        return OAuthProvider(config, mock_config_service, "/services/connectors/c1/config")

    @pytest.mark.asyncio
    async def test_a_revoked_refresh_token_is_reported_as_permanent(self, mock_config_service, monkeypatch) -> None:
        slack = FakeSlackOAuth("xoxe-1-live")
        monkeypatch.setattr(oauth_service, "ClientSession", slack.client_session)

        with pytest.raises(RefreshTokenInvalidError) as err:
            await self._provider(mock_config_service).refresh_access_token("xoxe-1-revoked")

        assert "invalid_refresh_token" in str(err.value)
        assert "xoxe-1-revoked" not in str(err.value)
        mock_config_service.set_config.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_a_refresh_saves_the_new_access_and_refresh_token_in_one_write(
        self, mock_config_service, monkeypatch,
    ) -> None:
        slack = FakeSlackOAuth("xoxe-1-live")
        monkeypatch.setattr(oauth_service, "ClientSession", slack.client_session)

        token = await self._provider(mock_config_service).refresh_access_token("xoxe-1-live")

        new_access, new_refresh = slack.issued[0]
        assert (token.access_token, token.refresh_token) == (new_access, new_refresh)
        mock_config_service.set_config.assert_awaited_once()
        saved = mock_config_service.set_config.await_args.args[1]["credentials"]
        assert (saved["access_token"], saved["refresh_token"], saved["expires_in"]) == (new_access, new_refresh, 43200)

    @pytest.mark.asyncio
    async def test_ok_false_carries_slacks_error_code(self, mock_config_service, monkeypatch) -> None:
        slack = FakeSlackOAuth("xoxe-1-live")
        monkeypatch.setattr(oauth_service, "ClientSession", slack.client_session)

        with pytest.raises(OAuthTokenRequestError) as err:
            await self._provider(mock_config_service)._make_token_request(
                {"grant_type": "refresh_token", "refresh_token": "xoxe-1-revoked"},
            )

        assert (err.value.status, err.value.error_code) == (200, "invalid_refresh_token")
        assert str(err.value) == f"OAuth token request was rejected by {SLACK_TOKEN_URL}: invalid_refresh_token"


class TestTokenEndpointRefusals:
    """The real OAuthProvider against a token endpoint that refuses every request."""

    @staticmethod
    def _provider(endpoint: RefusingTokenEndpoint, mock_config_service, monkeypatch) -> OAuthProvider:
        monkeypatch.setattr(oauth_service, "ClientSession", endpoint.client_session)
        config = _make_oauth_config(token_url="https://login.microsoftonline.com/consumers/oauth2/v2.0/token")
        return OAuthProvider(config, mock_config_service, "/services/connectors/c1/config")

    @pytest.mark.asyncio
    async def test_an_error_body_is_kept_in_the_message_and_its_code_parsed(
        self, mock_config_service, monkeypatch,
    ) -> None:
        provider = self._provider(RefusingTokenEndpoint(400, ENTRA_INVALID_CLIENT), mock_config_service, monkeypatch)

        with pytest.raises(OAuthTokenRequestError) as err:
            await provider._make_token_request({"grant_type": "refresh_token"})

        assert (err.value.status, err.value.error_code) == (400, "invalid_client")
        assert str(err.value) == (
            "OAuth token request failed with status 400. "
            "Token URL: https://login.microsoftonline.com/consumers/oauth2/v2.0/token, "
            "Redirect URI: http://localhost/callback, "
            "Client ID (masked): test-cli..., "
            f"Response: {ENTRA_INVALID_CLIENT}"
        )

    @pytest.mark.asyncio
    @pytest.mark.parametrize("body", ["<html><body>Bad Request</body></html>", '{"error": 42}', '["invalid_client"]'])
    async def test_a_body_without_an_error_string_has_no_code(self, body, mock_config_service, monkeypatch) -> None:
        provider = self._provider(RefusingTokenEndpoint(400, body, "text/html"), mock_config_service, monkeypatch)

        with pytest.raises(OAuthTokenRequestError) as err:
            await provider._make_token_request({"grant_type": "refresh_token"})

        assert (err.value.status, err.value.error_code) == (400, None)

    @pytest.mark.asyncio
    @pytest.mark.parametrize("body", [ENTRA_INVALID_CLIENT, ENTRA_INTERACTION_REQUIRED])
    async def test_microsoft_refusing_the_client_or_demanding_sign_in_is_permanent(
        self, body, mock_config_service, monkeypatch,
    ) -> None:
        provider = self._provider(RefusingTokenEndpoint(400, body), mock_config_service, monkeypatch)

        with pytest.raises(RefreshTokenInvalidError, match=r"re-authentication is required \((invalid_client|interaction_required)\)"):
            await provider.refresh_access_token("refresh-tok")

        mock_config_service.set_config.assert_not_awaited()

    @pytest.mark.asyncio
    @pytest.mark.parametrize("status", [429, 500, 503])
    async def test_a_permanent_code_from_an_overloaded_endpoint_stays_transient(
        self, status, mock_config_service, monkeypatch,
    ) -> None:
        provider = self._provider(RefusingTokenEndpoint(status, ENTRA_INVALID_CLIENT), mock_config_service, monkeypatch)

        with pytest.raises(OAuthTokenRequestError) as err:
            await provider.refresh_access_token("refresh-tok")

        assert not isinstance(err.value, RefreshTokenInvalidError)

    @pytest.mark.asyncio
    @pytest.mark.parametrize("body", [
        '{"error":"temporarily_unavailable","error_description":"AADSTS90033: retry later"}',
        '{"error":"invalid_request","error_description":"AADSTS900144: missing parameter"}',
        '{"error":"invalid_scope","error_description":"AADSTS70011: scope is not valid"}',
    ])
    async def test_an_outage_or_a_refused_request_shape_stays_transient(
        self, body, mock_config_service, monkeypatch,
    ) -> None:
        provider = self._provider(RefusingTokenEndpoint(400, body), mock_config_service, monkeypatch)

        with pytest.raises(OAuthTokenRequestError) as err:
            await provider.refresh_access_token("refresh-tok")

        assert not isinstance(err.value, RefreshTokenInvalidError)

    def test_the_friendly_message_still_reads_the_response_body(self) -> None:
        error = OAuthTokenRequestError(
            f"OAuth token request failed with status 400. Response: {ENTRA_INVALID_CLIENT}",
            status=400,
            error_code="invalid_client",
        )

        assert "Invalid OAuth client credentials" in extract_oauth_error_message(error)
