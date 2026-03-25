"""Unit tests for app.utils.jwt."""

import base64
import json
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
        # is_jwt_expired uses datetime.utcnow().timestamp() which interprets
        # the naive datetime as local time. We must use the same approach for
        # the test to be consistent regardless of system timezone.
        past = int(datetime.utcnow().timestamp()) - 3600
        token = _make_jwt_token({"exp": past, "sub": "user1"})
        assert is_jwt_expired(token) is True

    def test_valid_token_returns_false(self):
        future = int(datetime.utcnow().timestamp()) + 3600
        token = _make_jwt_token({"exp": future, "sub": "user1"})
        assert is_jwt_expired(token) is False

    def test_empty_string_returns_true(self):
        assert is_jwt_expired("") is True

    def test_none_returns_true(self):
        assert is_jwt_expired(None) is True

    def test_malformed_not_three_parts_returns_true(self):
        assert is_jwt_expired("only.two") is True

    def test_malformed_four_parts_returns_true(self):
        assert is_jwt_expired("a.b.c.d") is True

    def test_no_exp_claim_returns_true(self):
        token = _make_jwt_token({"sub": "user1"})
        assert is_jwt_expired(token) is True

    def test_token_just_expired(self):
        """Token that expired 1 second ago should be expired."""
        just_past = int(datetime.utcnow().timestamp()) - 1
        token = _make_jwt_token({"exp": just_past})
        assert is_jwt_expired(token) is True

    def test_token_expires_far_future(self):
        far_future = int(datetime.utcnow().timestamp()) + 365 * 86400
        token = _make_jwt_token({"exp": far_future})
        assert is_jwt_expired(token) is False

    def test_padding_handled_correctly(self):
        """Token payload that needs base64 padding should still decode."""
        # Create payload whose base64 length is not a multiple of 4
        payload = {"exp": int(datetime.utcnow().timestamp()) + 3600, "x": "a"}
        token = _make_jwt_token(payload)
        # Verify it works without error
        assert is_jwt_expired(token) is False

    def test_exp_exactly_now(self):
        """Token with exp exactly at current time should be expired (strict < comparison)."""
        now_ts = int(datetime.utcnow().timestamp())
        token = _make_jwt_token({"exp": now_ts})
        # exp < current_time: if they're equal, exp is NOT less than current_time
        # so it should return False (not expired) in a race-free scenario, but
        # by the time we check, current_time may have moved past. Use mock:
        with patch("app.utils.jwt.datetime") as mock_dt:
            mock_dt.utcnow.return_value.timestamp.return_value = float(now_ts)
            result = is_jwt_expired(token)
        # exp == current_time means exp < current_time is False, so not expired
        assert result is False


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
    async def test_adds_exp_if_missing(self):
        mock_config = AsyncMock()
        mock_config.get_config.return_value = {"scopedJwtSecret": "test-secret-key-12345"}

        payload = {"sub": "user1"}
        assert "exp" not in payload
        token = await generate_jwt(mock_config, payload)
        assert isinstance(token, str)
        # After call, payload should have exp added
        assert "exp" in payload

    @pytest.mark.asyncio
    async def test_adds_iat_if_missing(self):
        mock_config = AsyncMock()
        mock_config.get_config.return_value = {"scopedJwtSecret": "test-secret-key-12345"}

        payload = {"sub": "user1"}
        assert "iat" not in payload
        await generate_jwt(mock_config, payload)
        assert "iat" in payload

    @pytest.mark.asyncio
    async def test_preserves_existing_exp(self):
        """When exp is already set, generate_jwt should not overwrite it."""
        from jose import jwt as jose_jwt

        mock_config = AsyncMock()
        secret = "test-secret-key-12345"
        mock_config.get_config.return_value = {"scopedJwtSecret": secret}

        custom_exp = datetime.now(timezone.utc) + timedelta(hours=5)
        expected_ts = int(custom_exp.timestamp())
        payload = {"sub": "user1", "exp": custom_exp}
        token = await generate_jwt(mock_config, payload)
        decoded = jose_jwt.decode(token, secret, algorithms=["HS256"])
        assert decoded["exp"] == expected_ts

    @pytest.mark.asyncio
    async def test_preserves_existing_iat(self):
        """When iat is already set, generate_jwt should not overwrite it."""
        from jose import jwt as jose_jwt

        mock_config = AsyncMock()
        secret = "test-secret-key-12345"
        mock_config.get_config.return_value = {"scopedJwtSecret": secret}

        custom_iat = datetime.now(timezone.utc) - timedelta(minutes=10)
        expected_ts = int(custom_iat.timestamp())
        payload = {"sub": "user1", "iat": custom_iat}
        token = await generate_jwt(mock_config, payload)
        decoded = jose_jwt.decode(token, secret, algorithms=["HS256"])
        assert decoded["iat"] == expected_ts

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
    async def test_calls_config_service_with_correct_key(self):
        from app.config.constants.service import config_node_constants

        mock_config = AsyncMock()
        mock_config.get_config.return_value = {"scopedJwtSecret": "secret"}

        await generate_jwt(mock_config, {"sub": "user1"})
        mock_config.get_config.assert_called_once_with(config_node_constants.SECRET_KEYS.value)

    @pytest.mark.asyncio
    async def test_generated_token_is_decodable(self):
        """The generated token should be decodable with the same secret."""
        from jose import jwt as jose_jwt

        mock_config = AsyncMock()
        secret = "my-test-secret-for-jwt"
        mock_config.get_config.return_value = {"scopedJwtSecret": secret}

        payload = {"sub": "testuser", "scope": "admin"}
        token = await generate_jwt(mock_config, payload)

        decoded = jose_jwt.decode(token, secret, algorithms=["HS256"])
        assert decoded["sub"] == "testuser"
        assert decoded["scope"] == "admin"
        assert "exp" in decoded
        assert "iat" in decoded
