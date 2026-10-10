"""Client, constants and helpers for the strict OpenAPI audit of /api/v1/search."""

from __future__ import annotations

import datetime
import os
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any, Callable

import jwt
import requests

from helper.clients.search_client import SearchClient
from helper.local_auth import obtain_user_session_token
from helper.second_user import SecondUser

SEARCH_BASE = "/api/v1/search"
ROOT_TEMPLATE = SEARCH_BASE
SEARCH_TEMPLATE = f"{SEARCH_BASE}/:searchId"
SHARE_TEMPLATE = f"{SEARCH_BASE}/:searchId/share"
UNSHARE_TEMPLATE = f"{SEARCH_BASE}/:searchId/unshare"
ARCHIVE_TEMPLATE = f"{SEARCH_BASE}/:searchId/archive"
UNARCHIVE_TEMPLATE = f"{SEARCH_BASE}/:searchId/unarchive"
UPDATE_APP_CONFIG_ROUTE = f"{SEARCH_BASE}/updateAppConfig"

# Answered by the PDF that the session_kb fixture indexes.
SEARCH_QUERY = "every year asana undertakes which exercise?"
# A well-formed UUID that names no knowledge base.
UNKNOWN_KB_ID = "11111111-1111-4111-8111-111111111111"
# A well-formed id that names no user in the org.
UNKNOWN_USER_ID = "ffffffffffffffffffffffff"

JSON_HEADERS = {"Content-Type": "application/json"}
MALFORMED_JSON_BODY = "{not json"

OAUTH_CLIENTS_PATH = "/api/v1/oauth-clients"
OAUTH_TOKEN_PATH = "/api/v1/oauth2/token"
UPDATE_APP_CONFIG_MESSAGE = "User configuration updated successfully"

MISSING_SEARCH_ID = "0123456789abcdef01234567"
MALFORMED_SEARCH_ID = "not-an-object-id"

FETCH_CONFIG_SCOPE = "fetch:config"
# A real scope that updateAppConfig does not accept.
OTHER_SCOPE = "storage:token"
WRONG_SECRET = "spec-audit-not-the-deployment-secret"
MALFORMED_TOKEN = "not.a.jwt"

MintScopedToken = Callable[..., str]
# seed_search(**body) -> the new searchId; body defaults to a KB-scoped query with limit 1.
SeedSearch = Callable[..., str]


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


def validation_fields(resp: requests.Response) -> set[str]:
    """Fields named by a Node validator 400; fails the test for any other response."""
    assert resp.status_code == 400, resp.text[:500]
    error = resp.json()["error"]
    assert error["code"] == "VALIDATION_ERROR", resp.text[:500]
    return {str(e["field"]) for e in error["metadata"]["errors"]}


def error_of(resp: requests.Response, status: int) -> dict[str, Any]:
    assert resp.status_code == status, resp.text[:500]
    error: dict[str, Any] = resp.json()["error"]
    return error


@contextmanager
def oauth_token_with_scopes(base_url: str, scopes: list[str], timeout: int = 60) -> Iterator[str]:
    """A client-credentials token limited to ``scopes``, from an OAuth app that is deleted on exit.

    The suite's own token carries every scope and a session JWT is never scope-checked, so this is
    the only caller that can be refused for a missing scope.
    """
    admin = bearer(obtain_user_session_token(base_url, timeout))
    created = requests.post(
        f"{base_url}{OAUTH_CLIENTS_PATH}",
        headers=admin,
        json={
            "name": f"spec-audit-search-scope-{uuid.uuid4().hex[:8]}",
            "allowedGrantTypes": ["client_credentials"],
            "allowedScopes": scopes,
        },
        timeout=timeout,
    )
    assert created.status_code == 201, f"creating an OAuth app failed: {created.status_code} {created.text[:300]}"
    app = created.json()["app"]
    try:
        issued = requests.post(
            f"{base_url}{OAUTH_TOKEN_PATH}",
            json={
                "grant_type": "client_credentials",
                "client_id": app["clientId"],
                "client_secret": app["clientSecret"],
            },
            timeout=timeout,
        )
        assert issued.status_code == 200, f"token request failed: {issued.status_code}"
        yield issued.json()["access_token"]
    finally:
        requests.delete(f"{base_url}{OAUTH_CLIENTS_PATH}/{app['id']}", headers=admin, timeout=timeout)


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
