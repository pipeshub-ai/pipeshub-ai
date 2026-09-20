"""Tests for app.api.middlewares.auth"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException
from jose import JWTError

from app.api.middlewares.auth import (
    AUTH_POLICY_ATTR,
    authMiddleware,
    deny_service_tokens,
    extract_bearer_token,
    get_config_service,
    isJwtTokenValid,
    is_request_admin,
    normalize_auth_role,
    require_scopes,
    require_service_token,
    resolve_request_role,
)
from app.api.middlewares.caller_role import CallerRole, CallerRoleStatus
from app.config.constants.service import TokenScopes


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

    @pytest.mark.asyncio
    async def test_service_token_rejected_by_default(self):
        """A route that does not opt in rejects every service token."""
        checker = require_scopes("read")
        with pytest.raises(HTTPException) as exc_info:
            await checker(_request_with_user(_service_user(["record:content"])))
        assert exc_info.value.status_code == 403

    @pytest.mark.asyncio
    async def test_service_token_with_opted_in_scope_passes(self):
        checker = require_scopes("read", service_scopes=(TokenScopes.CONVERSATION_CREATE,))
        result = await checker(_request_with_user(_service_user(["conversation:create"])))
        assert result is None

    @pytest.mark.asyncio
    async def test_service_token_with_other_scope_rejected(self):
        checker = require_scopes("read", service_scopes=(TokenScopes.CONVERSATION_CREATE,))
        with pytest.raises(HTTPException) as exc_info:
            await checker(_request_with_user(_service_user(["fetch:config"])))
        assert exc_info.value.status_code == 403

    @pytest.mark.asyncio
    async def test_service_token_oauth_scopes_do_not_count(self):
        """oauthScopes on a service token must not satisfy the OAuth check."""
        user = _service_user(["fetch:config"])
        user.update({"isOAuth": True, "oauthScopes": ["read"]})
        checker = require_scopes("read")
        with pytest.raises(HTTPException) as exc_info:
            await checker(_request_with_user(user))
        assert exc_info.value.status_code == 403

    @pytest.mark.asyncio
    async def test_session_token_unaffected_by_service_scopes(self):
        checker = require_scopes("read", service_scopes=(TokenScopes.CONVERSATION_CREATE,))
        result = await checker(_request_with_user({"userId": "u1", "token_type": "regular"}))
        assert result is None

    def test_dependency_is_tagged_for_route_inventory(self):
        assert getattr(require_scopes("read"), AUTH_POLICY_ATTR).kind == "scopes"


def _service_user(scopes):
    return {"userId": "svc", "orgId": "org-1", "token_type": "scoped", "scopes": scopes}


def _request_with_user(user):
    request = MagicMock()
    request.state.user = user
    return request


class TestRequireServiceToken:
    """Tests for require_service_token() dependency factory."""

    def test_requires_at_least_one_scope(self):
        with pytest.raises(ValueError):
            require_service_token()

    def test_dependency_is_tagged_for_route_inventory(self):
        checker = require_service_token(TokenScopes.FETCH_CONFIG)
        assert getattr(checker, AUTH_POLICY_ATTR).kind == "service"

    @pytest.mark.asyncio
    async def test_no_user_raises_401(self):
        request = MagicMock()
        request.state.user = None
        with pytest.raises(HTTPException) as exc_info:
            await require_service_token(TokenScopes.FETCH_CONFIG)(request)
        assert exc_info.value.status_code == 401

    @pytest.mark.asyncio
    async def test_matching_service_token_returns_claims(self):
        user = _service_user(["fetch:config"])
        claims = await require_service_token(TokenScopes.FETCH_CONFIG)(_request_with_user(user))
        assert claims is user

    @pytest.mark.asyncio
    async def test_wrong_scope_raises_403(self):
        with pytest.raises(HTTPException) as exc_info:
            await require_service_token(TokenScopes.FETCH_CONFIG)(
                _request_with_user(_service_user(["record:content"]))
            )
        assert exc_info.value.status_code == 403

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "user",
        [
            {"userId": "u1", "token_type": "regular", "scopes": ["fetch:config"]},
            {"userId": "u1", "token_type": "regular", "isOAuth": True, "oauthScopes": ["fetch:config"]},
        ],
        ids=["session-with-scopes-claim", "oauth-with-matching-oauth-scope"],
    )
    async def test_non_service_tokens_raise_403(self, user):
        with pytest.raises(HTTPException) as exc_info:
            await require_service_token(TokenScopes.FETCH_CONFIG)(_request_with_user(user))
        assert exc_info.value.status_code == 403


class TestDenyServiceTokens:
    """Tests for deny_service_tokens dependency."""

    def test_dependency_is_tagged_for_route_inventory(self):
        assert getattr(deny_service_tokens, AUTH_POLICY_ATTR).kind == "deny_service"

    @pytest.mark.asyncio
    async def test_service_token_raises_403(self):
        with pytest.raises(HTTPException) as exc_info:
            await deny_service_tokens(_request_with_user(_service_user(["fetch:config"])))
        assert exc_info.value.status_code == 403

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "user",
        [
            {"userId": "u1", "token_type": "regular"},
            {"userId": "u1", "token_type": "regular", "isOAuth": True, "oauthScopes": []},
        ],
        ids=["session", "oauth"],
    )
    async def test_user_tokens_pass(self, user):
        assert await deny_service_tokens(_request_with_user(user)) is None

    @pytest.mark.asyncio
    async def test_no_user_raises_401(self):
        request = MagicMock()
        request.state.user = None
        with pytest.raises(HTTPException) as exc_info:
            await deny_service_tokens(request)
        assert exc_info.value.status_code == 401


# ---------------------------------------------------------------------------
# Helper to build a fake request for isJwtTokenValid / authMiddleware
# ---------------------------------------------------------------------------


def _make_fake_request(authorization=None):
    """Build a fake FastAPI Request with mocked container/logger."""
    headers = {}
    if authorization is not None:
        headers["Authorization"] = authorization

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
    @pytest.mark.parametrize(
        "scope",
        ["connector:signedUrl", "record:content", "conversation:create", "fetch:config"],
    )
    async def test_scoped_jwt_with_accepted_scope(self, mock_jwt_decode, mock_get_config, scope):
        """A scoped-secret token carrying an accepted service scope is a service token."""
        mock_config_service = AsyncMock()
        mock_config_service.get_config.return_value = {
            "jwtSecret": "regular-secret",
            "scopedJwtSecret": "scoped-secret",
        }
        mock_get_config.return_value = mock_config_service

        mock_jwt_decode.side_effect = [
            JWTError("invalid"),
            {"userId": "svc-user", "orgId": "org-1", "scopes": [scope]},
        ]

        request = _make_fake_request(authorization="Bearer scoped.jwt.token")
        result = await isJwtTokenValid(request)

        assert result["token_type"] == "scoped"
        assert result["userId"] == "svc-user"
        assert result["user"] == "scoped.jwt.token"
        assert result["role"] == "member"

    @pytest.mark.asyncio
    @patch("app.api.middlewares.auth.get_config_service")
    @patch("app.api.middlewares.auth.jwt.decode")
    @pytest.mark.parametrize(
        "claims",
        [
            {"scopes": ["token:refresh"]},
            {"scopes": ["password:reset"]},
            {"scopes": ["email:validate"]},
            {"scopes": ["org:email:verify"]},
            {"scopes": ["email:verified"]},
            {"scopes": ["mail:send"]},
            {"scopes": ["user:lookup"]},
            {"scopes": ["storage:token"]},
            {"scopes": []},
            {},
            {"scopes": "fetch:config"},
            {"scope": "fetch:config"},
        ],
        ids=[
            "refresh", "password-reset", "validate-email", "org-email-verify",
            "email-verified", "mail", "user-lookup", "storage", "empty-list",
            "no-scopes", "scopes-as-string", "oauth-style-scope",
        ],
    )
    async def test_scoped_jwt_without_accepted_scope_raises_401(
        self, mock_jwt_decode, mock_get_config, claims
    ):
        """Node-only and user-held scoped tokens never authenticate against Python services."""
        mock_config_service = AsyncMock()
        mock_config_service.get_config.return_value = {
            "jwtSecret": "regular-secret",
            "scopedJwtSecret": "scoped-secret",
        }
        mock_get_config.return_value = mock_config_service

        mock_jwt_decode.side_effect = [
            JWTError("invalid"),
            {"userId": "user-1", "orgId": "org-1", **claims},
        ]

        request = _make_fake_request(authorization="Bearer user.held.token")
        with pytest.raises(HTTPException) as exc_info:
            await isJwtTokenValid(request)
        assert exc_info.value.status_code == 401

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
    async def test_oauth_client_credentials_resolves_identity_from_created_by(
        self, mock_jwt_decode, mock_get_config
    ):
        """client_credentials tokens act as the identity in the createdBy claim.

        The claim keeps its original name for tokens already in circulation,
        but it carries whoever the application acts as, which Node resolves
        when minting: the application's service account where one has been
        set, its creator otherwise.
        """
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
            "createdBy": "app-owner-id",
        }

        request = _make_fake_request(authorization="Bearer oauth.jwt.token")
        result = await isJwtTokenValid(request)
        assert result["isOAuth"] is True
        assert result["userId"] == "app-owner-id"

    @pytest.mark.asyncio
    @patch("app.api.middlewares.auth.get_config_service")
    @patch("app.api.middlewares.auth.jwt.decode")
    async def test_oauth_client_credentials_acts_as_the_apps_service_account(
        self, mock_jwt_decode, mock_get_config
    ):
        """An application pointed at a service account acts as it here too.

        This is the case the whole feature exists for. Retrieval keys on the
        userId this returns, so if it came back as the person who created the
        application, search and the connectors would go on reading as them
        while the Node routes read as the service account.
        """
        mock_config_service = AsyncMock()
        mock_config_service.get_config.return_value = {
            "jwtSecret": "regular-secret",
            "scopedJwtSecret": "scoped-secret",
        }
        mock_get_config.return_value = mock_config_service

        mock_jwt_decode.return_value = {
            "userId": "client-xyz",
            "tokenType": "oauth",
            "scope": "kb:read",
            "client_id": "client-xyz",
            # Minted after the application was pointed at a service account,
            # so the claim carries the service account rather than the person.
            "createdBy": "service-account-id",
        }

        request = _make_fake_request(authorization="Bearer oauth.jwt.token")
        result = await isJwtTokenValid(request)
        assert result["userId"] == "service-account-id"

    @pytest.mark.asyncio
    @patch("app.api.middlewares.auth.get_config_service")
    @patch("app.api.middlewares.auth.jwt.decode")
    async def test_oauth_client_credentials_keeps_jwt_user_id_without_created_by(
        self, mock_jwt_decode, mock_get_config
    ):
        """client_credentials tokens without a createdBy claim keep the client_id subject."""
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
        }

        request = _make_fake_request(authorization="Bearer oauth.jwt.token")
        result = await isJwtTokenValid(request)
        assert result["isOAuth"] is True
        assert result["userId"] == "client-xyz"

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
    async def test_oauth_token_keeps_jwt_user_id(
        self, mock_jwt_decode, mock_get_config
    ):
        """authorization_code tokens keep the real user subject; createdBy must not override it."""
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
            "createdBy": "app-owner-id",
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
    async def test_scoped_token_claiming_oauth_gets_no_oauth_fields(
        self, mock_jwt_decode, mock_get_config
    ):
        """A service token cannot pose as an OAuth client or as an org admin."""
        mock_config_service = AsyncMock()
        mock_config_service.get_config.return_value = {
            "jwtSecret": "regular-secret",
            "scopedJwtSecret": "scoped-secret",
        }
        mock_get_config.return_value = mock_config_service

        mock_jwt_decode.side_effect = [
            JWTError("invalid"),
            {
                "userId": "svc-user",
                "orgId": "org-1",
                "scopes": ["fetch:config"],
                "tokenType": "oauth",
                "scope": "admin",
                "client_id": "svc-client",
                "isOAuth": True,
                "oauthScopes": ["connector:read"],
                "oauthClientId": "svc-client",
                "role": "admin",
            },
        ]

        request = _make_fake_request(authorization="Bearer scoped.oauth.token")
        result = await isJwtTokenValid(request)

        assert result["token_type"] == "scoped"
        assert "isOAuth" not in result
        assert "oauthScopes" not in result
        assert "oauthClientId" not in result
        assert result["role"] == "member"

    @pytest.mark.asyncio
    @patch("app.api.middlewares.auth.get_config_service")
    @patch("app.api.middlewares.auth.jwt.decode")
    async def test_scoped_token_keeps_service_account_identity(
        self, mock_jwt_decode, mock_get_config
    ):
        """The Slack service-account token's identity claims survive normalization."""
        mock_config_service = AsyncMock()
        mock_config_service.get_config.return_value = {
            "jwtSecret": "regular-secret",
            "scopedJwtSecret": "scoped-secret",
        }
        mock_get_config.return_value = mock_config_service

        mock_jwt_decode.side_effect = [
            JWTError("invalid"),
            {
                "userId": "sa-user",
                "orgId": "org-1",
                "email": "bot@example.com",
                "scopes": ["conversation:create"],
                "isServiceAccount": True,
            },
        ]

        request = _make_fake_request(authorization="Bearer scoped.sa.token")
        result = await isJwtTokenValid(request)

        assert result["userId"] == "sa-user"
        assert result["orgId"] == "org-1"
        assert result["email"] == "bot@example.com"
        assert result["isServiceAccount"] is True


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

    @pytest.mark.asyncio
    @patch("app.api.middlewares.auth.isJwtTokenValid")
    async def test_session_jwt_admin_role_attached(self, mock_validate):
        """Session JWT with role=admin is stored on request.state.user."""
        payload = {"userId": "user-1", "role": "admin", "token_type": "regular"}
        mock_validate.return_value = payload

        request = _make_fake_request(authorization="Bearer valid.token")
        await authMiddleware(request)

        assert request.state.user["role"] == "admin"
        assert is_request_admin(request) is True

    @pytest.mark.asyncio
    @patch("app.api.middlewares.auth.fetch_caller_role", new_callable=AsyncMock)
    @patch("app.api.middlewares.auth.isJwtTokenValid")
    async def test_oauth_role_comes_from_node(self, mock_validate, mock_role):
        """OAuth/PAT tokens carry no role claim; Node's live role is used, whatever the scopes."""
        payload = {
            "userId": "user-1",
            "isOAuth": True,
            "oauthScopes": ["agent:read"],
            "token_type": "regular",
        }
        mock_validate.return_value = payload
        mock_role.return_value = CallerRole(CallerRoleStatus.VALID, "admin")

        request = _make_fake_request(authorization="Bearer oauth.token")
        await authMiddleware(request)

        assert request.state.user["role"] == "admin"
        mock_role.assert_awaited_once()

    @pytest.mark.asyncio
    @patch("app.api.middlewares.auth.fetch_caller_role", new_callable=AsyncMock)
    @patch("app.api.middlewares.auth.isJwtTokenValid")
    async def test_oauth_token_refused_when_node_cannot_confirm_it(
        self, mock_validate, mock_role
    ):
        """A revoked token must not slip through while Node is unreachable or throttled."""
        from types import SimpleNamespace

        mock_validate.return_value = {"userId": "user-1", "isOAuth": True, "token_type": "regular"}
        mock_role.return_value = CallerRole(CallerRoleStatus.UNKNOWN)

        request = _make_fake_request(authorization="Bearer oauth.token")
        request.state = SimpleNamespace()
        with pytest.raises(HTTPException) as exc_info:
            await authMiddleware(request)

        assert exc_info.value.status_code == 503
        assert exc_info.value.headers == {"Retry-After": "5"}
        # Shown to the person as-is: plain words and when to retry, no "access token".
        assert exc_info.value.detail == (
            "We couldn't confirm your sign-in just now. Please try again in a few seconds."
        )
        assert not hasattr(request.state, "user")

    @pytest.mark.asyncio
    @patch("app.api.middlewares.auth.fetch_caller_role", new_callable=AsyncMock)
    @patch("app.api.middlewares.auth.isJwtTokenValid")
    async def test_oauth_token_node_rejects_is_refused(self, mock_validate, mock_role):
        """A revoked PAT or a deleted user's OAuth token must not authenticate here either."""
        mock_validate.return_value = {"userId": "user-1", "isOAuth": True, "token_type": "regular"}
        mock_role.return_value = CallerRole(CallerRoleStatus.REJECTED)

        request = _make_fake_request(authorization="Bearer revoked.pat")
        with pytest.raises(HTTPException) as exc_info:
            await authMiddleware(request)
        assert exc_info.value.status_code == 401

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "payload",
        [
            {"userId": "user-1", "role": "admin", "token_type": "regular"},
            {"userId": "svc", "token_type": "scoped", "role": "member"},
        ],
        ids=["session", "service"],
    )
    @patch("app.api.middlewares.auth.fetch_caller_role", new_callable=AsyncMock)
    @patch("app.api.middlewares.auth.isJwtTokenValid")
    async def test_non_oauth_tokens_never_call_node(self, mock_validate, mock_role, payload):
        mock_validate.return_value = dict(payload)

        await authMiddleware(_make_fake_request(authorization="Bearer tok"))

        mock_role.assert_not_awaited()

    @pytest.mark.asyncio
    @patch("app.api.middlewares.auth.isJwtTokenValid")
    async def test_forged_x_is_admin_header_ignored(self, mock_validate):
        """X-Is-Admin on the request does not grant admin."""
        payload = {"userId": "user-1", "role": "member", "token_type": "regular"}
        mock_validate.return_value = payload

        request = _make_fake_request(authorization="Bearer valid.token")
        request.headers["X-Is-Admin"] = "true"
        request.headers["x-is-admin"] = "true"
        await authMiddleware(request)

        assert request.state.user["role"] == "member"
        assert is_request_admin(request) is False


# ---------------------------------------------------------------------------
# role helpers
# ---------------------------------------------------------------------------


class TestAuthRoleHelpers:
    def test_normalize_auth_role_admin_variants(self):
        assert normalize_auth_role("admin") == "admin"
        assert normalize_auth_role("ADMIN") == "admin"
        assert normalize_auth_role(" Admin ") == "admin"

    def test_normalize_auth_role_fail_closed(self):
        assert normalize_auth_role(None) == "member"
        assert normalize_auth_role("") == "member"
        assert normalize_auth_role("member") == "member"
        assert normalize_auth_role("superadmin") == "member"
        assert normalize_auth_role(True) == "member"

    def test_is_request_admin_reads_jwt_role_only(self):
        request = MagicMock()
        request.state.user = {"role": "admin"}
        request.headers = {"X-Is-Admin": "false"}
        assert is_request_admin(request) is True

        request.state.user = {"role": "member"}
        request.headers = {"X-Is-Admin": "true"}
        assert is_request_admin(request) is False

    @pytest.mark.asyncio
    async def test_resolve_skips_lookup_when_session_admin(self):
        request = _make_fake_request()
        role = await resolve_request_role(request, {"role": "admin"})
        assert role == "admin"


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
