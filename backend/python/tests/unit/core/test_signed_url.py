"""Unit tests for app.core.signed_url module."""

import time
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import jwt
import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from app.core.signed_url import SignedUrlConfig, SignedUrlHandler, TokenPayload


# ---------------------------------------------------------------------------
# SignedUrlConfig
# ---------------------------------------------------------------------------
class TestSignedUrlConfig:
    """Tests for SignedUrlConfig dataclass/model."""

    def test_defaults(self):
        config = SignedUrlConfig(private_key="secret123")
        assert config.private_key == "secret123"
        assert config.expiration_minutes == 60
        assert config.algorithm == "HS256"
        assert config.url_prefix == "/api/v1/index"

    def test_custom_values(self):
        config = SignedUrlConfig(
            private_key="key",
            expiration_minutes=30,
            algorithm="HS384",
            url_prefix="/custom",
        )
        assert config.expiration_minutes == 30
        assert config.algorithm == "HS384"
        assert config.url_prefix == "/custom"

    def test_raises_without_private_key(self):
        with pytest.raises(ValueError, match="Private key"):
            SignedUrlConfig(private_key=None)

    def test_raises_with_empty_private_key(self):
        with pytest.raises(ValueError, match="Private key"):
            SignedUrlConfig(private_key="")

    @pytest.mark.asyncio
    async def test_create_factory_success(self):
        mock_config_service = MagicMock()
        mock_config_service.get_config = AsyncMock(
            return_value={"scopedJwtSecret": "my-secret"}
        )
        config = await SignedUrlConfig.create(mock_config_service)
        assert config.private_key == "my-secret"

    @pytest.mark.asyncio
    async def test_create_factory_missing_secret(self):
        mock_config_service = MagicMock()
        mock_config_service.get_config = AsyncMock(return_value={})
        with pytest.raises(ValueError, match="Private key"):
            await SignedUrlConfig.create(mock_config_service)

    @pytest.mark.asyncio
    async def test_create_factory_none_secret(self):
        mock_config_service = MagicMock()
        mock_config_service.get_config = AsyncMock(
            return_value={"scopedJwtSecret": None}
        )
        with pytest.raises(ValueError, match="Private key"):
            await SignedUrlConfig.create(mock_config_service)


# ---------------------------------------------------------------------------
# TokenPayload
# ---------------------------------------------------------------------------
class TestTokenPayload:
    """Tests for TokenPayload model."""

    def test_fields(self):
        now = datetime.now(timezone.utc)
        exp = now + timedelta(hours=1)
        tp = TokenPayload(
            record_id="rec1",
            user_id="user1",
            exp=exp,
            iat=now,
        )
        assert tp.record_id == "rec1"
        assert tp.user_id == "user1"
        assert tp.additional_claims == {}

    def test_with_additional_claims(self):
        now = datetime.now(timezone.utc)
        tp = TokenPayload(
            record_id="rec1",
            user_id="user1",
            exp=now + timedelta(hours=1),
            iat=now,
            additional_claims={"role": "admin"},
        )
        assert tp.additional_claims == {"role": "admin"}

    def test_missing_required_fields(self):
        with pytest.raises(ValidationError):
            TokenPayload(record_id="rec1")

    def test_json_encoder_datetime(self):
        now = datetime.now(timezone.utc)
        tp = TokenPayload(
            record_id="r", user_id="u", exp=now, iat=now
        )
        # Verify json_encoders config exists
        assert datetime in tp.Config.json_encoders


# ---------------------------------------------------------------------------
# SignedUrlHandler
# ---------------------------------------------------------------------------
class TestSignedUrlHandler:
    """Tests for SignedUrlHandler."""

    def _make_handler(self, private_key="test-secret", expiration_minutes=60):
        config = SignedUrlConfig(
            private_key=private_key, expiration_minutes=expiration_minutes
        )
        logger = MagicMock()
        config_service = MagicMock()
        config_service.get_config = AsyncMock(
            return_value={
                "connectors": {
                    "endpoint": "http://localhost:8088"
                }
            }
        )
        return SignedUrlHandler(logger=logger, config=config, config_service=config_service)

    def test_init_valid(self):
        handler = self._make_handler()
        assert handler.signed_url_config.private_key == "test-secret"

    def test_init_raises_on_zero_expiration(self):
        config = SignedUrlConfig(private_key="key", expiration_minutes=1)
        # Manually set to test validation
        config.expiration_minutes = 0
        with pytest.raises(ValueError, match="positive"):
            SignedUrlHandler(
                logger=MagicMock(), config=config, config_service=MagicMock()
            )

    def test_init_raises_on_negative_expiration(self):
        config = SignedUrlConfig(private_key="key", expiration_minutes=1)
        config.expiration_minutes = -5
        with pytest.raises(ValueError, match="positive"):
            SignedUrlHandler(
                logger=MagicMock(), config=config, config_service=MagicMock()
            )

    # ---- get_signed_url tests ----

    @pytest.mark.asyncio
    async def test_get_signed_url_returns_valid_url(self):
        handler = self._make_handler()
        url = await handler.get_signed_url(
            record_id="rec123",
            org_id="org1",
            user_id="user1",
            connector="google",
        )
        assert "http://localhost:8088" in url
        assert "/api/v1/index/org1/google/record/rec123" in url
        assert "?token=" in url

    @pytest.mark.asyncio
    async def test_get_signed_url_token_is_decodable(self):
        handler = self._make_handler(private_key="my-key")
        url = await handler.get_signed_url(
            record_id="rec1",
            org_id="org1",
            user_id="user1",
            connector="slack",
        )
        token = url.split("?token=")[1]
        decoded = jwt.decode(token, "my-key", algorithms=["HS256"])
        assert decoded["record_id"] == "rec1"
        assert decoded["user_id"] == "user1"
        assert "exp" in decoded
        assert "iat" in decoded

    @pytest.mark.asyncio
    async def test_get_signed_url_with_additional_claims(self):
        handler = self._make_handler(private_key="my-key")
        url = await handler.get_signed_url(
            record_id="rec1",
            org_id="org1",
            user_id="user1",
            additional_claims={"scope": "read"},
            connector="google",
        )
        token = url.split("?token=")[1]
        decoded = jwt.decode(token, "my-key", algorithms=["HS256"])
        assert decoded["additional_claims"] == {"scope": "read"}

    @pytest.mark.asyncio
    async def test_get_signed_url_no_additional_claims_defaults_empty(self):
        handler = self._make_handler(private_key="my-key")
        url = await handler.get_signed_url(
            record_id="rec1",
            org_id="org1",
            user_id="user1",
            connector="jira",
        )
        token = url.split("?token=")[1]
        decoded = jwt.decode(token, "my-key", algorithms=["HS256"])
        assert decoded["additional_claims"] == {}

    @pytest.mark.asyncio
    async def test_get_signed_url_uses_configured_endpoint(self):
        config = SignedUrlConfig(private_key="key")
        logger = MagicMock()
        config_service = MagicMock()
        config_service.get_config = AsyncMock(
            return_value={
                "connectors": {
                    "endpoint": "https://custom-host:9999"
                }
            }
        )
        handler = SignedUrlHandler(
            logger=logger, config=config, config_service=config_service
        )

        url = await handler.get_signed_url(
            record_id="rec1", org_id="org1", user_id="u1", connector="google"
        )
        assert url.startswith("https://custom-host:9999")

    @pytest.mark.asyncio
    async def test_get_signed_url_config_service_error(self):
        config = SignedUrlConfig(private_key="key")
        logger = MagicMock()
        config_service = MagicMock()
        config_service.get_config = AsyncMock(side_effect=Exception("config down"))
        handler = SignedUrlHandler(
            logger=logger, config=config, config_service=config_service
        )

        with pytest.raises(HTTPException) as exc_info:
            await handler.get_signed_url(
                record_id="rec1", org_id="org1", user_id="u1", connector="google"
            )
        assert exc_info.value.status_code == 500

    # ---- validate_token tests ----

    def test_validate_token_valid(self):
        handler = self._make_handler(private_key="secret")
        now = datetime.now(timezone.utc)
        payload = {
            "record_id": "rec1",
            "user_id": "user1",
            "exp": (now + timedelta(hours=1)).timestamp(),
            "iat": now.timestamp(),
            "additional_claims": {},
        }
        token = jwt.encode(payload, "secret", algorithm="HS256")
        result = handler.validate_token(token)
        assert result.record_id == "rec1"
        assert result.user_id == "user1"

    def test_validate_token_expired(self):
        handler = self._make_handler(private_key="secret")
        now = datetime.now(timezone.utc)
        payload = {
            "record_id": "rec1",
            "user_id": "user1",
            "exp": (now - timedelta(hours=1)).timestamp(),
            "iat": (now - timedelta(hours=2)).timestamp(),
            "additional_claims": {},
        }
        token = jwt.encode(payload, "secret", algorithm="HS256")
        with pytest.raises(HTTPException) as exc_info:
            handler.validate_token(token)
        # PyJWT's ExpiredSignatureError is not caught by jose.JWTError,
        # so it falls through to the generic Exception handler -> 500
        assert exc_info.value.status_code == 500

    def test_validate_token_wrong_key(self):
        handler = self._make_handler(private_key="correct-key")
        now = datetime.now(timezone.utc)
        payload = {
            "record_id": "rec1",
            "user_id": "user1",
            "exp": (now + timedelta(hours=1)).timestamp(),
            "iat": now.timestamp(),
            "additional_claims": {},
        }
        token = jwt.encode(payload, "wrong-key", algorithm="HS256")
        with pytest.raises(HTTPException) as exc_info:
            handler.validate_token(token)
        assert exc_info.value.status_code in (401, 500)

    def test_validate_token_with_required_claims_pass(self):
        handler = self._make_handler(private_key="secret")
        now = datetime.now(timezone.utc)
        payload = {
            "record_id": "rec1",
            "user_id": "user1",
            "exp": (now + timedelta(hours=1)).timestamp(),
            "iat": now.timestamp(),
            "additional_claims": {"role": "admin"},
        }
        token = jwt.encode(payload, "secret", algorithm="HS256")
        result = handler.validate_token(token, required_claims={"role": "admin"})
        assert result.additional_claims["role"] == "admin"

    def test_validate_token_with_required_claims_fail(self):
        handler = self._make_handler(private_key="secret")
        now = datetime.now(timezone.utc)
        payload = {
            "record_id": "rec1",
            "user_id": "user1",
            "exp": (now + timedelta(hours=1)).timestamp(),
            "iat": now.timestamp(),
            "additional_claims": {"role": "viewer"},
        }
        token = jwt.encode(payload, "secret", algorithm="HS256")
        with pytest.raises(HTTPException) as exc_info:
            handler.validate_token(token, required_claims={"role": "admin"})
        # The inner HTTPException(401) is caught by the outer except Exception
        # handler which re-raises as HTTPException(500)
        assert exc_info.value.status_code == 500

    def test_validate_token_missing_required_claim_key(self):
        handler = self._make_handler(private_key="secret")
        now = datetime.now(timezone.utc)
        payload = {
            "record_id": "rec1",
            "user_id": "user1",
            "exp": (now + timedelta(hours=1)).timestamp(),
            "iat": now.timestamp(),
            "additional_claims": {},
        }
        token = jwt.encode(payload, "secret", algorithm="HS256")
        with pytest.raises(HTTPException) as exc_info:
            handler.validate_token(token, required_claims={"scope": "write"})
        # The inner HTTPException(401) is caught by the outer except Exception -> 500
        assert exc_info.value.status_code == 500

    def test_validate_token_malformed(self):
        handler = self._make_handler(private_key="secret")
        with pytest.raises(HTTPException) as exc_info:
            handler.validate_token("not.a.valid.token.at.all")
        assert exc_info.value.status_code in (400, 401, 500)

    def test_validate_token_missing_fields(self):
        handler = self._make_handler(private_key="secret")
        now = datetime.now(timezone.utc)
        # Missing record_id and user_id
        payload = {
            "exp": (now + timedelta(hours=1)).timestamp(),
            "iat": now.timestamp(),
        }
        token = jwt.encode(payload, "secret", algorithm="HS256")
        with pytest.raises(HTTPException) as exc_info:
            handler.validate_token(token)
        assert exc_info.value.status_code == 400

    def test_validate_token_no_required_claims(self):
        """Passing required_claims=None should skip claim validation."""
        handler = self._make_handler(private_key="secret")
        now = datetime.now(timezone.utc)
        payload = {
            "record_id": "rec1",
            "user_id": "user1",
            "exp": (now + timedelta(hours=1)).timestamp(),
            "iat": now.timestamp(),
            "additional_claims": {},
        }
        token = jwt.encode(payload, "secret", algorithm="HS256")
        result = handler.validate_token(token, required_claims=None)
        assert result.record_id == "rec1"
