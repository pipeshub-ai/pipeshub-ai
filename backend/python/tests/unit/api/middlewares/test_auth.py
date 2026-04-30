"""Tests for app.api.middlewares.auth"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException
from jose import JWTError

from app.api.middlewares.auth import (
    authMiddleware,
    extract_bearer_token,
    get_config_service,
    isJwtTokenValid,
    require_scopes,
)


# ---------------------------------------------------------------------------
# extract_bearer_token
# ---------------------------------------------------------------------------


class TestExtractBearerToken:
    """Tests for extract_bearer_token()."""

    def test_valid_bearer_token(self):
        """Valid 'Bearer xxx' returns 'xxx'."""
        assert extract_bearer_token("Bearer xxx") == "xxx"

    def test_valid_bearer_token_long(self):
        """A realistic JWT-length token is extracted correctly."""
        token = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.payload.signature"
        assert extract_bearer_token(f"Bearer {token}") == token

    def test_none_header_raises_401(self):
        """None header raises HTTPException 401."""
        with pytest.raises(HTTPException) as exc_info:
            extract_bearer_token(None)
        assert exc_info.value.status_code == 401
        assert "missing" in exc_info.value.detail.lower()

    def test_empty_string_header_raises_401(self):
        """Empty string header raises HTTPException 401."""
        with pytest.raises(HTTPException) as exc_info:
            extract_bearer_token("")
        assert exc_info.value.status_code == 401

    def test_basic_auth_raises_401(self):
        """'Basic xxx' header raises HTTPException 401."""
        with pytest.raises(HTTPException) as exc_info:
            extract_bearer_token("Basic xxx")
        assert exc_info.value.status_code == 401
        assert "Bearer" in exc_info.value.detail

    def test_bearer_empty_token_raises_401(self):
        """'Bearer ' with empty token raises HTTPException 401."""
        with pytest.raises(HTTPException) as exc_info:
            extract_bearer_token("Bearer ")
        assert exc_info.value.status_code == 401
        assert "missing" in exc_info.value.detail.lower()

    def test_bearer_only_spaces_raises_401(self):
        """'Bearer    ' (only whitespace after Bearer) raises HTTPException 401."""
        with pytest.raises(HTTPException) as exc_info:
            extract_bearer_token("Bearer    ")
        assert exc_info.value.status_code == 401

    def test_bearer_whitespace_around_token_stripped(self):
        """'Bearer  token  ' returns 'token' (whitespace stripped)."""
        assert extract_bearer_token("Bearer  token  ") == "token"

    def test_bearer_case_sensitive(self):
        """'bearer xxx' (lowercase) raises HTTPException 401."""
        with pytest.raises(HTTPException) as exc_info:
            extract_bearer_token("bearer xxx")
        assert exc_info.value.status_code == 401

    def test_www_authenticate_header_present(self):
        """All 401 errors include WWW-Authenticate: Bearer header."""
        with pytest.raises(HTTPException) as exc_info:
            extract_bearer_token(None)
        assert exc_info.value.headers == {"WWW-Authenticate": "Bearer"}


# ---------------------------------------------------------------------------
# require_scopes
# ---------------------------------------------------------------------------


class TestRequireScopes:
    """Tests for require_scopes() dependency factory."""

    def test_factory_returns_callable(self):
        """require_scopes() returns a callable (coroutine function)."""
        checker = require_scopes("read", "write")
        assert callable(checker)

    @pytest.mark.asyncio
    async def test_no_user_raises_401(self):
        """Request without state.user raises HTTPException 401."""

        class FakeRequest:
            class state:
                pass  # no 'user' attribute

        checker = require_scopes("read")
        with pytest.raises(HTTPException) as exc_info:
            await checker(FakeRequest())
        assert exc_info.value.status_code == 401

    @pytest.mark.asyncio
    async def test_non_oauth_request_passes(self):
        """Non-OAuth request (isOAuth absent or False) passes without scope check."""

        class FakeRequest:
            class state:
                user = {"userId": "123", "token_type": "regular"}

        checker = require_scopes("read")
        # Should not raise
        result = await checker(FakeRequest())
        assert result is None

    @pytest.mark.asyncio
    async def test_non_oauth_explicit_false_passes(self):
        """Request with isOAuth=False passes without scope check."""

        class FakeRequest:
            class state:
                user = {"userId": "123", "isOAuth": False}

        checker = require_scopes("admin")
        result = await checker(FakeRequest())
        assert result is None

    @pytest.mark.asyncio
    async def test_oauth_request_with_matching_scope_passes(self):
        """OAuth request with a matching scope passes."""

        class FakeRequest:
            class state:
                user = {
                    "userId": "123",
                    "isOAuth": True,
                    "oauthScopes": ["read", "write"],
                }

        checker = require_scopes("read")
        result = await checker(FakeRequest())
        assert result is None

    @pytest.mark.asyncio
    async def test_oauth_request_with_one_of_multiple_scopes_passes(self):
        """OAuth request matching any one of required scopes passes (OR logic)."""

        class FakeRequest:
            class state:
                user = {
                    "userId": "123",
                    "isOAuth": True,
                    "oauthScopes": ["write"],
                }

        checker = require_scopes("read", "write")
        result = await checker(FakeRequest())
        assert result is None

    @pytest.mark.asyncio
    async def test_oauth_request_without_matching_scope_raises_403(self):
        """OAuth request without any matching scope raises HTTPException 403."""

        class FakeRequest:
            class state:
                user = {
                    "userId": "123",
                    "isOAuth": True,
                    "oauthScopes": ["read"],
                }

        checker = require_scopes("admin", "write")
        with pytest.raises(HTTPException) as exc_info:
            await checker(FakeRequest())
        assert exc_info.value.status_code == 403
        assert "scope" in exc_info.value.detail.lower()

    @pytest.mark.asyncio
    async def test_oauth_request_with_empty_scopes_raises_403(self):
        """OAuth request with empty oauthScopes list raises HTTPException 403."""

        class FakeRequest:
            class state:
                user = {
                    "userId": "123",
                    "isOAuth": True,
                    "oauthScopes": [],
                }

        checker = require_scopes("read")
        with pytest.raises(HTTPException) as exc_info:
            await checker(FakeRequest())
        assert exc_info.value.status_code == 403

    @pytest.mark.asyncio
    async def test_oauth_missing_scopes_key_raises_403(self):
        """OAuth request without oauthScopes key raises HTTPException 403."""

        class FakeRequest:
            class state:
                user = {
                    "userId": "123",
                    "isOAuth": True,
                    # oauthScopes missing
                }

        checker = require_scopes("read")
        with pytest.raises(HTTPException) as exc_info:
            await checker(FakeRequest())
        assert exc_info.value.status_code == 403


# ---------------------------------------------------------------------------
# Helper to build a fake request for isJwtTokenValid / authMiddleware
# ---------------------------------------------------------------------------


def _make_fake_request(authorization=None, extra_headers=None):
    """Build a fake FastAPI Request with mocked container/logger."""
    headers = {}
    if authorization is not None:
        headers["Authorization"] = authorization
    if extra_headers:
        headers.update(extra_headers)

    request = MagicMock()
    request.headers = headers
    request.app.container.logger.return_value = MagicMock()
    request.state = MagicMock()
    return request


# ---------------------------------------------------------------------------
# isJwtTokenValid
# ---------------------------------------------------------------------------


class TestIsJwtTokenValid:
    """Tests for isJwtTokenValid()."""

    @pytest.mark.asyncio
    @patch("app.api.middlewares.auth.get_config_service")
    @patch("app.api.middlewares.auth.jwt.decode")
    async def test_regular_jwt_valid(self, mock_jwt_decode, mock_get_config):
        """Regular JWT token decoded successfully returns payload with token_type=regular."""
        mock_config_service = AsyncMock()
        mock_config_service.get_config.return_value = {
            "jwtSecret": "regular-secret",
            "scopedJwtSecret": "scoped-secret",
        }
        mock_get_config.return_value = mock_config_service

        mock_jwt_decode.return_value = {"userId": "user-1", "orgId": "org-1"}

        request = _make_fake_request(authorization="Bearer valid.jwt.token")
        result = await isJwtTokenValid(request)

        assert result["userId"] == "user-1"
        assert result["token_type"] == "regular"
        assert result["user"] == "valid.jwt.token"
        mock_jwt_decode.assert_called_once()

    @pytest.mark.asyncio
    @patch("app.api.middlewares.auth.get_config_service")
    @patch("app.api.middlewares.auth.jwt.decode")
    async def test_scoped_jwt_fallback(self, mock_jwt_decode, mock_get_config):
        """When regular JWT fails but scoped JWT succeeds, returns scoped payload."""
        mock_config_service = AsyncMock()
        mock_config_service.get_config.return_value = {
            "jwtSecret": "regular-secret",
            "scopedJwtSecret": "scoped-secret",
        }
        mock_get_config.return_value = mock_config_service

        # First call (regular) fails, second call (scoped) succeeds
        mock_jwt_decode.side_effect = [
            JWTError("invalid"),
            {"userId": "svc-user", "scope": "internal"},
        ]

        request = _make_fake_request(authorization="Bearer scoped.jwt.token")
        result = await isJwtTokenValid(request)

        assert result["token_type"] == "scoped"
        assert result["userId"] == "svc-user"
        assert result["user"] == "scoped.jwt.token"

    @pytest.mark.asyncio
    @patch("app.api.middlewares.auth.get_config_service")
    @patch("app.api.middlewares.auth.jwt.decode")
    async def test_both_jwt_secrets_fail_raises_401(self, mock_jwt_decode, mock_get_config):
        """When both regular and scoped JWT fail, raises 401."""
        mock_config_service = AsyncMock()
        mock_config_service.get_config.return_value = {
            "jwtSecret": "regular-secret",
            "scopedJwtSecret": "scoped-secret",
        }
        mock_get_config.return_value = mock_config_service

        mock_jwt_decode.side_effect = JWTError("invalid")

        request = _make_fake_request(authorization="Bearer bad.jwt.token")
        with pytest.raises(HTTPException) as exc_info:
            await isJwtTokenValid(request)
        assert exc_info.value.status_code == 401

    @pytest.mark.asyncio
    @patch("app.api.middlewares.auth.get_config_service")
    @patch("app.api.middlewares.auth.jwt.decode")
    async def test_regular_jwt_fails_no_scoped_secret_raises_401(
        self, mock_jwt_decode, mock_get_config
    ):
        """When regular JWT fails and no scoped secret is configured, raises 401."""
        mock_config_service = AsyncMock()
        mock_config_service.get_config.return_value = {
            "jwtSecret": "regular-secret",
            # no scopedJwtSecret
        }
        mock_get_config.return_value = mock_config_service

        mock_jwt_decode.side_effect = JWTError("invalid")

        request = _make_fake_request(authorization="Bearer bad.token")
        with pytest.raises(HTTPException) as exc_info:
            await isJwtTokenValid(request)
        assert exc_info.value.status_code == 401

    @pytest.mark.asyncio
    @patch("app.api.middlewares.auth.get_config_service")
    @patch("app.api.middlewares.auth.jwt.decode")
    async def test_oauth_token_detection(self, mock_jwt_decode, mock_get_config):
        """OAuth tokens are detected and normalized with isOAuth, oauthScopes, oauthClientId."""
        mock_config_service = AsyncMock()
        mock_config_service.get_config.return_value = {
            "jwtSecret": "regular-secret",
            "scopedJwtSecret": "scoped-secret",
        }
        mock_get_config.return_value = mock_config_service

        mock_jwt_decode.return_value = {
            "userId": "user-1",
            "tokenType": "oauth",
            "scope": "read write",
            "client_id": "client-abc",
        }

        request = _make_fake_request(authorization="Bearer oauth.jwt.token")
        result = await isJwtTokenValid(request)

        assert result["isOAuth"] is True
        assert result["oauthScopes"] == ["read", "write"]
        assert result["oauthClientId"] == "client-abc"
        assert result["token_type"] == "regular"

    @pytest.mark.asyncio
    @patch("app.api.middlewares.auth.get_config_service")
    @patch("app.api.middlewares.auth.jwt.decode")
    async def test_oauth_deprecated_user_id_equals_client_id_raises_401(
        self, mock_jwt_decode, mock_get_config
    ):
        """Deprecated OAuth JWTs used client_id as userId; reject (re-issue after revocation)."""
        mock_config_service = AsyncMock()
        mock_config_service.get_config.return_value = {
            "jwtSecret": "regular-secret",
            "scopedJwtSecret": "scoped-secret",
        }
        mock_get_config.return_value = mock_config_service

        mock_jwt_decode.return_value = {
            "userId": "client-xyz",
            "tokenType": "oauth",
            "scope": "admin",
            "client_id": "client-xyz",
            "createdBy": "owner-from-jwt",
        }

        request = _make_fake_request(authorization="Bearer oauth.jwt.token")
        with pytest.raises(HTTPException) as exc_info:
            await isJwtTokenValid(request)
        assert exc_info.value.status_code == 401

    @pytest.mark.asyncio
    @patch("app.api.middlewares.auth.get_config_service")
    async def test_missing_secret_keys_raises_500(self, mock_get_config):
        """When secret_keys config is None, raises 500."""
        mock_config_service = AsyncMock()
        mock_config_service.get_config.return_value = None
        mock_get_config.return_value = mock_config_service

        request = _make_fake_request(authorization="Bearer some.token")
        with pytest.raises(HTTPException) as exc_info:
            await isJwtTokenValid(request)
        assert exc_info.value.status_code == 500
        assert "configuration" in exc_info.value.detail.lower()

    @pytest.mark.asyncio
    @patch("app.api.middlewares.auth.get_config_service")
    async def test_missing_jwt_secret_raises_500(self, mock_get_config):
        """When jwtSecret is missing from config, raises 500."""
        mock_config_service = AsyncMock()
        mock_config_service.get_config.return_value = {
            # no jwtSecret key
            "scopedJwtSecret": "scoped-secret",
        }
        mock_get_config.return_value = mock_config_service

        request = _make_fake_request(authorization="Bearer some.token")
        with pytest.raises(HTTPException) as exc_info:
            await isJwtTokenValid(request)
        assert exc_info.value.status_code == 500

    @pytest.mark.asyncio
    @patch("app.api.middlewares.auth.get_config_service")
    async def test_missing_authorization_header_raises_401(self, mock_get_config):
        """When Authorization header is missing, raises 401."""
        mock_config_service = AsyncMock()
        mock_config_service.get_config.return_value = {
            "jwtSecret": "regular-secret",
            "scopedJwtSecret": "scoped-secret",
        }
        mock_get_config.return_value = mock_config_service

        request = _make_fake_request(authorization=None)
        with pytest.raises(HTTPException) as exc_info:
            await isJwtTokenValid(request)
        assert exc_info.value.status_code == 401

    @pytest.mark.asyncio
    @patch("app.api.middlewares.auth.get_config_service")
    async def test_unexpected_exception_raises_401(self, mock_get_config):
        """Unexpected exception in isJwtTokenValid raises 401."""
        mock_get_config.side_effect = RuntimeError("something broke")

        request = _make_fake_request(authorization="Bearer some.token")
        with pytest.raises(HTTPException) as exc_info:
            await isJwtTokenValid(request)
        assert exc_info.value.status_code == 401

    @pytest.mark.asyncio
    @patch("app.api.middlewares.auth.get_config_service")
    @patch("app.api.middlewares.auth.jwt.decode")
    async def test_oauth_token_without_x_oauth_user_id_header(
        self, mock_jwt_decode, mock_get_config
    ):
        """OAuth token keeps JWT userId when subject is not legacy client_id shape."""
        mock_config_service = AsyncMock()
        mock_config_service.get_config.return_value = {
            "jwtSecret": "regular-secret",
            "scopedJwtSecret": "scoped-secret",
        }
        mock_get_config.return_value = mock_config_service

        mock_jwt_decode.return_value = {
            "userId": "original-user",
            "tokenType": "oauth",
            "scope": "read",
            "client_id": "client-1",
        }

        request = _make_fake_request(authorization="Bearer oauth.jwt.token")
        result = await isJwtTokenValid(request)

        assert result["isOAuth"] is True
        assert result["userId"] == "original-user"

    @pytest.mark.asyncio
    @patch("app.api.middlewares.auth.get_config_service")
    @patch("app.api.middlewares.auth.jwt.decode")
    async def test_non_oauth_token_no_isOAuth_flag(self, mock_jwt_decode, mock_get_config):
        """Non-OAuth token does not get isOAuth flag set."""
        mock_config_service = AsyncMock()
        mock_config_service.get_config.return_value = {
            "jwtSecret": "regular-secret",
            "scopedJwtSecret": "scoped-secret",
        }
        mock_get_config.return_value = mock_config_service

        mock_jwt_decode.return_value = {"userId": "user-1"}

        request = _make_fake_request(authorization="Bearer normal.jwt.token")
        result = await isJwtTokenValid(request)

        assert "isOAuth" not in result

    @pytest.mark.asyncio
    @patch("app.api.middlewares.auth.get_config_service")
    @patch("app.api.middlewares.auth.jwt.decode")
    async def test_scoped_jwt_oauth_token(self, mock_jwt_decode, mock_get_config):
        """OAuth token validated via scoped JWT still gets normalized."""
        mock_config_service = AsyncMock()
        mock_config_service.get_config.return_value = {
            "jwtSecret": "regular-secret",
            "scopedJwtSecret": "scoped-secret",
        }
        mock_get_config.return_value = mock_config_service

        # Regular fails, scoped succeeds with OAuth payload
        mock_jwt_decode.side_effect = [
            JWTError("invalid"),
            {
                "userId": "svc-user",
                "tokenType": "oauth",
                "scope": "admin",
                "client_id": "svc-client",
            },
        ]

        request = _make_fake_request(authorization="Bearer scoped.oauth.token")
        result = await isJwtTokenValid(request)

        assert result["token_type"] == "scoped"
        assert result["isOAuth"] is True
        assert result["oauthScopes"] == ["admin"]
        assert result["oauthClientId"] == "svc-client"


# ---------------------------------------------------------------------------
# authMiddleware
# ---------------------------------------------------------------------------


class TestAuthMiddleware:
    """Tests for authMiddleware()."""

    @pytest.mark.asyncio
    @patch("app.api.middlewares.auth.isJwtTokenValid")
    async def test_success_attaches_user(self, mock_validate):
        """Successful validation attaches payload to request.state.user."""
        payload = {"userId": "user-1", "token_type": "regular", "user": "tok"}
        mock_validate.return_value = payload

        request = _make_fake_request(authorization="Bearer valid.token")
        result = await authMiddleware(request)

        assert result is request
        assert request.state.user == payload

    @pytest.mark.asyncio
    @patch("app.api.middlewares.auth.isJwtTokenValid")
    async def test_http_exception_reraised(self, mock_validate):
        """HTTPException from isJwtTokenValid is re-raised as-is."""
        mock_validate.side_effect = HTTPException(
            status_code=401, detail="Could not validate credentials"
        )

        request = _make_fake_request(authorization="Bearer bad.token")
        with pytest.raises(HTTPException) as exc_info:
            await authMiddleware(request)
        assert exc_info.value.status_code == 401
        assert "validate" in exc_info.value.detail.lower()

    @pytest.mark.asyncio
    @patch("app.api.middlewares.auth.isJwtTokenValid")
    async def test_500_exception_reraised(self, mock_validate):
        """HTTPException 500 from config errors is re-raised."""
        mock_validate.side_effect = HTTPException(
            status_code=500, detail="Authentication configuration error"
        )

        request = _make_fake_request(authorization="Bearer bad.token")
        with pytest.raises(HTTPException) as exc_info:
            await authMiddleware(request)
        assert exc_info.value.status_code == 500

    @pytest.mark.asyncio
    @patch("app.api.middlewares.auth.isJwtTokenValid")
    async def test_unexpected_exception_raises_401(self, mock_validate):
        """Unexpected exception in validation is caught and raises 401."""
        mock_validate.side_effect = RuntimeError("unexpected")

        request = _make_fake_request(authorization="Bearer some.token")
        with pytest.raises(HTTPException) as exc_info:
            await authMiddleware(request)
        assert exc_info.value.status_code == 401
        assert "not authenticated" in exc_info.value.detail.lower()


# ---------------------------------------------------------------------------
# get_config_service
# ---------------------------------------------------------------------------


class TestGetConfigService:
    """Tests for get_config_service()."""

    @pytest.mark.asyncio
    async def test_returns_config_service_from_container(self):
        """get_config_service extracts config_service from request.app.container."""
        fake_config_service = MagicMock()
        request = MagicMock()
        request.app.container.config_service.return_value = fake_config_service

        result = await get_config_service(request)

        assert result is fake_config_service
        request.app.container.config_service.assert_called_once()
