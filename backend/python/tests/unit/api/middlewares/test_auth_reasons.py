"""Every auth refusal is an ``AuthRejected`` with a machine-readable ``reason`` and the same status, detail and headers
as before (docs/logging.md, "HTTP requests (Python)"). Real signed tokens; only Node's caller-role lookup is faked."""

import logging
import time
from collections.abc import Iterator
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import jwt
import pytest
from starlette.requests import Request

from app.api.middlewares import auth as auth_module
from app.api.middlewares.admin_gate import require_admin_caller
from app.api.middlewares.auth import (
    AuthRejected,
    authMiddleware,
    deny_service_tokens,
    require_scopes,
    require_service_token,
    service_token_org,
)
from app.api.middlewares.caller_role import CallerRole, CallerRoleStatus
from app.config.constants.service import TokenScopes
from app.utils import logging_fields

JWT_SECRET = "session-secret-for-reason-tests"
SCOPED_SECRET = "scoped-secret-for-reason-tests"
LOGGER = "test-auth-reasons"
BEARER = {"WWW-Authenticate": "Bearer"}
NOW = int(time.time())
USER_CLAIMS = {"userId": "u1", "orgId": "o1", "role": "member", "iat": NOW, "exp": NOW + 600}


def _token(claims: dict, secret: str = JWT_SECRET) -> str:
    return jwt.encode(claims, secret, "HS256")


class _Config:
    def __init__(self, keys: object) -> None:
        self.keys = keys

    async def get_config(self, key: str, **kwargs: object) -> object:
        if isinstance(self.keys, Exception):
            raise self.keys
        return self.keys


def _request(authorization: str | None, *, keys: object = None, path: str = "/api/v1/x") -> Request:
    config = _Config({"jwtSecret": JWT_SECRET, "scopedJwtSecret": SCOPED_SECRET} if keys is None else keys)
    app = SimpleNamespace(container=SimpleNamespace(logger=lambda: logging.getLogger(LOGGER), config_service=lambda: config))
    headers = [(b"authorization", authorization.encode())] if authorization is not None else []
    return Request({"type": "http", "method": "GET", "path": path, "headers": headers, "query_string": b"", "app": app})


@pytest.fixture(autouse=True)
def _node(request: pytest.FixtureRequest) -> Iterator[None]:
    auth_module._session_checks.clear()
    status = getattr(request, "param", CallerRoleStatus.VALID)
    with patch("app.api.middlewares.auth.fetch_caller_role", new_callable=AsyncMock,
               return_value=CallerRole(status, "member")):
        yield
    auth_module._session_checks.clear()


async def _refusal(authorization: str | None, **kwargs: object) -> AuthRejected:
    with pytest.raises(AuthRejected) as caught:
        await authMiddleware(_request(authorization, **kwargs))
    return caught.value


def _same_as_main(exc: AuthRejected, status: int, detail: str, headers: dict | None, reason: str) -> None:
    assert (exc.status_code, exc.detail, exc.headers, exc.reason) == (status, detail, headers, reason)


@pytest.mark.parametrize(
    ("authorization", "status", "detail", "headers", "reason"),
    [
        (None, 401, "Authorization header is missing", BEARER, "missing_header"),
        ("Basic dXNlcjpwYXNz", 401, "Authorization header must start with 'Bearer '", BEARER, "bad_scheme"),
        ("Bearer    ", 401, "Token is missing in Authorization header", BEARER, "missing_token"),
        (f"Bearer {_token({**USER_CLAIMS, 'exp': NOW - 60})}", 401, "Could not validate credentials", BEARER, "expired"),
        (f"Bearer {_token(USER_CLAIMS, 'not-the-secret-0123456789')}", 401, "Could not validate credentials", BEARER,
         "invalid_token"),
        (f"Bearer {_token({**USER_CLAIMS, 'tokenType': 'oauth', 'scope': ''})}", 401, "Could not validate credentials",
         BEARER, "no_scopes"),
        (f"Bearer {_token({'orgId': 'o1', 'scopes': ['not:accepted'], 'exp': NOW + 600}, SCOPED_SECRET)}", 401,
         "Could not validate credentials", BEARER, "scope_not_accepted"),
        (f"Bearer {_token({'scopes': [TokenScopes.FETCH_CONFIG.value], 'exp': NOW + 600}, SCOPED_SECRET)}", 401,
         "Token missing orgId", None, "missing_org"),
    ],
)
async def test_token_refusals_carry_a_reason_and_answer_as_before(authorization, status, detail, headers, reason) -> None:
    _same_as_main(await _refusal(authorization), status, detail, headers, reason)


async def test_expired_with_only_the_session_secret_is_still_expired() -> None:
    expired = f"Bearer {_token({**USER_CLAIMS, 'exp': NOW - 60})}"

    exc = await _refusal(expired, keys={"jwtSecret": JWT_SECRET})

    _same_as_main(exc, 401, "Could not validate credentials", BEARER, "expired")


@pytest.mark.parametrize(
    ("_node", "claims", "status", "detail", "headers", "reason"),
    [
        (CallerRoleStatus.REJECTED, USER_CLAIMS, 401, "Your session has ended. Please sign in again.", BEARER,
         "session_ended"),
        (CallerRoleStatus.REJECTED, {**USER_CLAIMS, "tokenType": "oauth", "scope": "kb:read"}, 401,
         "Token is no longer valid", BEARER, "token_revoked"),
        (CallerRoleStatus.UNKNOWN, USER_CLAIMS, 503,
         "We couldn't confirm your sign-in just now. Please try again in a few seconds.", {"Retry-After": "5"},
         "unconfirmed"),
    ],
    indirect=["_node"],
)
async def test_node_refusals_carry_a_reason(_node, claims, status, detail, headers, reason) -> None:
    _same_as_main(await _refusal(f"Bearer {_token(claims)}"), status, detail, headers, reason)


async def test_missing_secrets_are_a_500_raised_from_the_configuration_error() -> None:
    exc = await _refusal(f"Bearer {_token(USER_CLAIMS)}", keys={})

    _same_as_main(exc, 500, "Authentication configuration error", None, "auth_config")
    assert isinstance(exc.__context__, ValueError)


async def test_an_unexpected_validation_error_is_auth_error() -> None:
    exc = await _refusal(f"Bearer {_token(USER_CLAIMS)}", keys=RuntimeError("store down"))

    _same_as_main(exc, 401, "Could not validate credentials", BEARER, "auth_error")


async def test_an_unexpected_middleware_error_is_auth_error() -> None:
    with patch("app.api.middlewares.auth.resolve_request_role", new_callable=AsyncMock, side_effect=RuntimeError("bug")):
        exc = await _refusal(f"Bearer {_token(USER_CLAIMS)}")

    _same_as_main(exc, 401, "Not authenticated", BEARER, "auth_error")


def _with_user(user: object) -> Request:
    request = _request(None)
    if user is not None:
        request.state.user = user
    return request


SERVICE_USER = {"token_type": "scoped", "scopes": [TokenScopes.FETCH_CONFIG.value], "orgId": "o1"}
OAUTH_USER = {"token_type": "regular", "isOAuth": True, "oauthScopes": ["other:scope"]}
SESSION_USER = {"token_type": "regular", "userId": "u1", "orgId": "o1"}


@pytest.mark.parametrize(
    ("dependency", "user", "status", "detail", "headers", "reason"),
    [
        (require_scopes("kb:read"), None, 401, "Authentication required", BEARER, "unauthenticated"),
        (require_scopes("kb:read"), SERVICE_USER, 403, "This route does not accept service tokens", None,
         "service_token_not_allowed"),
        (require_scopes("kb:read"), OAUTH_USER, 403, "Insufficient scope. Required: kb:read", None, "insufficient_scope"),
        (require_service_token(TokenScopes.FETCH_CONFIG), SESSION_USER, 403, "This route requires a service token", None,
         "service_token_required"),
        (deny_service_tokens, SERVICE_USER, 403, "This route does not accept service tokens", None,
         "service_token_not_allowed"),
    ],
)
async def test_route_dependency_refusals_carry_a_reason(dependency, user, status, detail, headers, reason) -> None:
    with pytest.raises(AuthRejected) as caught:
        await dependency(_with_user(user))

    _same_as_main(caught.value, status, detail, headers, reason)


def test_service_token_org_refusals_carry_a_reason() -> None:
    with pytest.raises(AuthRejected) as missing:
        service_token_org({})
    with pytest.raises(AuthRejected) as mismatch:
        service_token_org({"orgId": "o1"}, "o2")

    _same_as_main(missing.value, 401, "Token missing orgId", None, "missing_org")
    _same_as_main(mismatch.value, 403, "org_id does not match the service token", None, "org_mismatch")


async def test_a_non_admin_is_refused_with_a_reason() -> None:
    with patch("app.edition_config.check_user_is_admin", new_callable=AsyncMock, return_value=False):
        with pytest.raises(AuthRejected) as caught:
            await require_admin_caller(_with_user(SESSION_USER))

    _same_as_main(caught.value, 403, "Only administrators can do this.", None, "not_admin")


@pytest.mark.parametrize(
    "authorization",
    [
        None,
        f"Bearer {_token({**USER_CLAIMS, 'exp': NOW - 60})}",
        f"Bearer {_token(USER_CLAIMS, 'not-the-secret-0123456789')}",
    ],
)
async def test_routine_refusals_log_no_warning_here(authorization, caplog: pytest.LogCaptureFixture) -> None:
    """The request's ``http.request.end`` line says it, once."""
    caplog.set_level(logging.DEBUG, logger=LOGGER)

    await _refusal(authorization)

    assert [r.getMessage() for r in caplog.records if r.name == LOGGER and r.levelno >= logging.WARNING] == []


async def test_control_a_refused_service_scope_still_names_the_scopes(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG, logger=LOGGER)

    await _refusal(f"Bearer {_token({'orgId': 'o1', 'scopes': ['not:accepted'], 'exp': NOW + 600}, SCOPED_SECRET)}")

    warnings = [r.getMessage() for r in caplog.records if r.name == LOGGER and r.levelno == logging.WARNING]
    assert len(warnings) == 1 and "not:accepted" in warnings[0]


async def test_a_missing_scoped_secret_is_warned_about_once_not_per_request(
    caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(logging_fields, "_log_once_last", {})
    caplog.set_level(logging.DEBUG, logger=LOGGER)
    request_token = f"Bearer {_token(USER_CLAIMS)}"

    for _ in range(3):
        await authMiddleware(_request(request_token, keys={"jwtSecret": JWT_SECRET}))

    assert [r.getMessage() for r in caplog.records if "scopedJwtSecret" in r.getMessage()] == [
        "scopedJwtSecret not found in configuration - service tokens are disabled"]
