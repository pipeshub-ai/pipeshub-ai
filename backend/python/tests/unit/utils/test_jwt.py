"""Unit tests for app.utils.jwt."""

import base64
import json
import os
import time
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.utils.jwt import generate_jwt, is_jwt_expired


def _make_jwt_token(payload: dict) -> str:
    """Create a minimal JWT-like token (header.payload.signature) for testing."""
    header = base64.urlsafe_b64encode(json.dumps({"alg": "HS256"}).encode()).rstrip(b"=").decode()
    body = base64.urlsafe_b64encode(json.dumps(payload).encode()).rstrip(b"=").decode()
    sig = base64.urlsafe_b64encode(b"fake-signature").rstrip(b"=").decode()
    return f"{header}.{body}.{sig}"


# ---------------------------------------------------------------------------
# is_jwt_expired
# ---------------------------------------------------------------------------
class TestIsJwtExpired:
    """Tests for is_jwt_expired()."""

    def test_expired_token_returns_true(self):
        """Token with an exp timestamp in the past should evaluate as expired."""
        past = int(datetime.now(timezone.utc).timestamp()) - 3600
        token = _make_jwt_token({"exp": past, "sub": "user1"})
        assert is_jwt_expired(token) is True

    def test_valid_token_returns_false(self):
        """Token with an exp timestamp in the future should evaluate as not expired."""
        future = int(datetime.now(timezone.utc).timestamp()) + 3600
        token = _make_jwt_token({"exp": future, "sub": "user1"})
        assert is_jwt_expired(token) is False

    def test_empty_string_returns_true(self):
        """Empty token string should be treated as expired."""
        assert is_jwt_expired("") is True

    def test_none_returns_true(self):
        """None token should be treated as expired."""
        assert is_jwt_expired(None) is True

    def test_malformed_not_three_parts_returns_true(self):
        """Token without three dot-separated segments should be treated as expired."""
        assert is_jwt_expired("only.two") is True

    def test_malformed_four_parts_returns_true(self):
        """Token with four segments should be treated as expired."""
        assert is_jwt_expired("a.b.c.d") is True

    def test_no_exp_claim_returns_true(self):
        """Token missing the exp claim should be treated as expired."""
        token = _make_jwt_token({"sub": "user1"})
        assert is_jwt_expired(token) is True

    def test_token_just_expired(self):
        """Token that expired 1 second ago should be expired."""
        just_past = int(datetime.now(timezone.utc).timestamp()) - 1
        token = _make_jwt_token({"exp": just_past})
        assert is_jwt_expired(token) is True

    def test_token_expires_far_future(self):
        """Token expiring far into the future should evaluate as not expired."""
        far_future = int(datetime.now(timezone.utc).timestamp()) + 365 * 86400
        token = _make_jwt_token({"exp": far_future})
        assert is_jwt_expired(token) is False

    def test_padding_handled_correctly(self):
        """Token payload that needs base64 padding should still decode."""
        # Create payload whose base64 length is not a multiple of 4
        payload = {"exp": int(datetime.now(timezone.utc).timestamp()) + 3600, "x": "a"}
        token = _make_jwt_token(payload)
        # Verify it works without error
        assert is_jwt_expired(token) is False

    def test_exp_exactly_now(self):
        """Token with exp exactly at current time should not be expired (strict < comparison)."""
        now_ts = int(datetime.now(timezone.utc).timestamp())
        token = _make_jwt_token({"exp": now_ts})
        # exp < current_time: if they're equal, exp is NOT less than current_time
        # so it should return False (not expired) in a race-free scenario. Use mock:
        with patch("app.utils.jwt.datetime") as mock_dt:
            mock_dt.now.return_value.timestamp.return_value = float(now_ts)
            result = is_jwt_expired(token)
        # exp == current_time means exp < current_time is False, so not expired
        assert result is False

    def test_timezone_independence(self) -> None:
        """Verify token expiration evaluates correctly under non-UTC process timezones."""
        orig_tz = os.environ.get("TZ")
        for tz in ("America/Los_Angeles", "Asia/Kolkata"):
            try:
                os.environ["TZ"] = tz
                if hasattr(time, "tzset"):
                    time.tzset()
                now_ts = int(datetime.now(timezone.utc).timestamp())
                valid_token = _make_jwt_token({"exp": now_ts + 1800, "sub": "user1"})
                expired_token = _make_jwt_token({"exp": now_ts - 1800, "sub": "user1"})
                assert is_jwt_expired(valid_token) is False
                assert is_jwt_expired(expired_token) is True
            finally:
                if orig_tz is None:
                    os.environ.pop("TZ", None)
                else:
                    os.environ["TZ"] = orig_tz
                if hasattr(time, "tzset"):
                    time.tzset()


# ---------------------------------------------------------------------------
# generate_jwt
# ---------------------------------------------------------------------------
class TestGenerateJwt:
    """Tests for generate_jwt()."""

    @pytest.mark.asyncio
    async def test_returns_string_token(self):
        mock_config = AsyncMock()
        mock_config.get_config.return_value = {"scopedJwtSecret": "test-secret-key-12345"}

        payload = {"sub": "user1", "scope": "read"}
        token = await generate_jwt(mock_config, payload)
        assert isinstance(token, str)
        # JWT has 3 parts
        assert len(token.split(".")) == 3

    @pytest.mark.asyncio
    async def test_stamps_one_hour_expiry(self):
        import jwt as pyjwt

        mock_config = AsyncMock()
        secret = "test-secret-key-12345"
        mock_config.get_config.return_value = {"scopedJwtSecret": secret}

        token = await generate_jwt(mock_config, {"sub": "user1"})
        decoded = pyjwt.decode(token, secret, algorithms=["HS256"])
        assert decoded["exp"] - decoded["iat"] == 3600
        assert abs(decoded["iat"] - int(time.time())) <= 5

    @pytest.mark.asyncio
    async def test_does_not_mutate_caller_payload(self):
        mock_config = AsyncMock()
        mock_config.get_config.return_value = {"scopedJwtSecret": "test-secret-key-12345"}

        payload = {"sub": "user1"}
        await generate_jwt(mock_config, payload)
        assert payload == {"sub": "user1"}

    @pytest.mark.asyncio
    async def test_caller_cannot_extend_expiry(self):
        """A service token's lifetime is fixed by the minter, not the caller."""
        import jwt as pyjwt

        mock_config = AsyncMock()
        secret = "test-secret-key-12345"
        mock_config.get_config.return_value = {"scopedJwtSecret": secret}

        far_future = datetime.now(timezone.utc) + timedelta(days=365)
        token = await generate_jwt(mock_config, {"sub": "user1", "exp": far_future})
        decoded = pyjwt.decode(token, secret, algorithms=["HS256"])
        assert decoded["exp"] <= int(time.time()) + 3600 + 5

    @pytest.mark.asyncio
    async def test_caller_cannot_backdate_issued_at(self):
        import jwt as pyjwt

        mock_config = AsyncMock()
        secret = "test-secret-key-12345"
        mock_config.get_config.return_value = {"scopedJwtSecret": secret}

        backdated = datetime.now(timezone.utc) - timedelta(days=1)
        token = await generate_jwt(mock_config, {"sub": "user1", "iat": backdated})
        decoded = pyjwt.decode(token, secret, algorithms=["HS256"])
        assert abs(decoded["iat"] - int(time.time())) <= 5

    @pytest.mark.asyncio
    async def test_raises_when_secret_keys_none(self):
        mock_config = AsyncMock()
        mock_config.get_config.return_value = None

        with pytest.raises(ValueError, match="SECRET_KEYS"):
            await generate_jwt(mock_config, {"sub": "user1"})

    @pytest.mark.asyncio
    async def test_raises_when_scoped_jwt_secret_none(self):
        mock_config = AsyncMock()
        mock_config.get_config.return_value = {"scopedJwtSecret": None}

        with pytest.raises(ValueError, match="SCOPED_JWT_SECRET"):
            await generate_jwt(mock_config, {"sub": "user1"})

    @pytest.mark.asyncio
    async def test_raises_when_scoped_jwt_secret_missing(self):
        mock_config = AsyncMock()
        mock_config.get_config.return_value = {"otherKey": "value"}

        with pytest.raises(ValueError, match="SCOPED_JWT_SECRET"):
            await generate_jwt(mock_config, {"sub": "user1"})

    @pytest.mark.asyncio
    async def test_reads_the_secret_keys_through_the_config_cache(self):
        from app.config.constants.service import config_node_constants

        mock_config = AsyncMock()
        mock_config.get_config.return_value = {"scopedJwtSecret": "secret"}

        await generate_jwt(mock_config, {"sub": "user1"})
        mock_config.get_config.assert_called_once_with(
            config_node_constants.SECRET_KEYS.value, use_cache=True
        )

    @pytest.mark.asyncio
    async def test_generated_token_is_decodable(self):
        """The generated token should be decodable with the same secret."""
        import jwt as pyjwt

        mock_config = AsyncMock()
        secret = "my-test-secret-for-jwt"
        mock_config.get_config.return_value = {"scopedJwtSecret": secret}

        payload = {"sub": "testuser", "scope": "admin"}
        token = await generate_jwt(mock_config, payload)

        decoded = pyjwt.decode(token, secret, algorithms=["HS256"])
        assert decoded["sub"] == "testuser"
        assert decoded["scope"] == "admin"
        assert "exp" in decoded
        assert "iat" in decoded
