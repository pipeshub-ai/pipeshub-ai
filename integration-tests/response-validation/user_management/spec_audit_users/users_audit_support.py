"""Constants and helpers for the strict OpenAPI audit of /api/v1/users."""

from __future__ import annotations

import datetime
import os
from typing import Any, Callable

import jwt
import requests

from helper.pipeshub_client import PipeshubClient
from helper.second_user import SecondUser

USERS_BASE = "/api/v1/users"
MISSING_USER_ID = "0123456789abcdef01234567"
MALFORMED_USER_ID = "not-an-object-id"

INVALID_BEARER = {"Authorization": "Bearer not-a-jwt"}

USER_LOOKUP_SCOPE = "user:lookup"
FETCH_CONFIG_SCOPE = "fetch:config"
# A real scope no route of this router accepts.
OTHER_SCOPE = "storage:token"
WRONG_SECRET = "spec-audit-not-the-deployment-secret"

SeededUser = dict[str, Any]
SeedUser = Callable[..., SeededUser]
MintScopedToken = Callable[..., str]


def scoped_jwt_secret() -> str:
    return os.getenv("SCOPED_JWT_SECRET", "").strip()


def mint_scoped_token(
    secret: str,
    scopes: list[str] | None,
    *,
    ttl_seconds: int = 3600,
    **claims: Any,
) -> str:
    """Service token shaped like Node's createJwt ones; a negative ttl gives an expired one."""
    now = datetime.datetime.now(datetime.timezone.utc)
    payload: dict[str, Any] = {
        **claims,
        "iat": now,
        "exp": now + datetime.timedelta(seconds=ttl_seconds),
    }
    if scopes is not None:
        payload["scopes"] = scopes
    return jwt.encode(payload, secret, algorithm="HS256")


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}


def request_with_token(
    client: PipeshubClient, token: str, method: str, path: str = "", **kwargs: Any
) -> requests.Response:
    """Call a users route with exactly this bearer token; path is relative to the router."""
    # auth=False so the helper neither overwrites the header nor retries a 401
    # with a refreshed admin token.
    return client.request(
        method, f"{USERS_BASE}{path}", auth=False, headers=bearer(token), **kwargs
    )


def request_as(
    user: SecondUser, method: str, path: str = "", **kwargs: Any
) -> requests.Response:
    """Call a users route as the non-admin member; path is relative to the router."""
    kwargs.setdefault("timeout", user.timeout)
    return requests.request(
        method,
        f"{user.base_url}{USERS_BASE}{path}",
        headers=user.headers,
        **kwargs,
    )
