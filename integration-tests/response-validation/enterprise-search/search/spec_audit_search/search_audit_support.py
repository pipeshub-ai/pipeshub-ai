"""Client, constants and helpers for the strict OpenAPI audit of /api/v1/search."""

from __future__ import annotations

import datetime
import os
from typing import Any, Callable

import jwt
import requests

from helper.clients.search_client import SearchClient
from helper.second_user import SecondUser

SEARCH_BASE = "/api/v1/search"
UPDATE_APP_CONFIG_ROUTE = f"{SEARCH_BASE}/updateAppConfig"
UPDATE_APP_CONFIG_MESSAGE = "User configuration updated successfully"

MISSING_SEARCH_ID = "0123456789abcdef01234567"
MALFORMED_SEARCH_ID = "not-an-object-id"

FETCH_CONFIG_SCOPE = "fetch:config"
# A real scope that updateAppConfig does not accept.
OTHER_SCOPE = "storage:token"
WRONG_SECRET = "spec-audit-not-the-deployment-secret"
MALFORMED_TOKEN = "not.a.jwt"

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


class SearchAuditClient(SearchClient):
    """SearchClient plus the routes the shared helper client does not cover."""

    def update_app_config(
        self,
        *,
        token: str | None = None,
        auth: bool = True,
        **kwargs: Any,
    ) -> requests.Response:
        """POST /updateAppConfig.

        The route takes a scoped service token, never a session token: pass
        ``token=`` to send one. Without it, ``auth=True`` sends the shared
        admin's session token (which the route rejects) and ``auth=False``
        sends no Authorization header.
        """
        if token is not None:
            # auth=False so the helper neither overwrites the header nor
            # retries a 401 with a refreshed admin token.
            return self.post(
                "/updateAppConfig", auth=False, headers=bearer(token), **kwargs
            )
        return self.post("/updateAppConfig", auth=auth, **kwargs)


def request_as(
    user: SecondUser, method: str, path: str = "", **kwargs: Any
) -> requests.Response:
    """Call a search route with the non-admin member's session token; path is relative to the router."""
    kwargs.setdefault("timeout", user.timeout)
    return requests.request(
        method, f"{user.base_url}{SEARCH_BASE}{path}", headers=user.headers, **kwargs
    )
