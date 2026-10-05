"""Client, constants and helpers for the strict OpenAPI audit of /api/v1/document."""

from __future__ import annotations

import os
from typing import Any

import requests

from helper.http.api_client import APIClient
from helper.second_user import SecondUser

DOCUMENT_BASE = "/api/v1/document"
UPDATE_APP_CONFIG_ROUTE = f"{DOCUMENT_BASE}/updateAppConfig"
MISSING_DOCUMENT_ID = "0123456789abcdef01234567"
MALFORMED_DOCUMENT_ID = "not-an-object-id"

FETCH_CONFIG_SCOPE = "fetch:config"
STORAGE_TOKEN_SCOPE = "storage:token"
MALFORMED_TOKEN = "not.a.jwt"


def scoped_jwt_secret() -> str:
    """The deployment's scoped JWT secret, or "" when the run was not given one."""
    return os.getenv("SCOPED_JWT_SECRET", "").strip()


def mint_scoped_token(
    org_id: str, scopes: list[str], user_id: str | None = None, secret: str | None = None
) -> str:
    """Service token shaped like the one Node's scopedTokenValidator verifies.

    Pass ``secret`` to sign with a key the deployment does not hold.
    """
    # Lazy: `app` is on sys.path only once the root conftest has run.
    from app.utils.jwt import mint_service_token  # type: ignore[import-not-found]  # noqa: PLC0415

    key = secret or scoped_jwt_secret()
    if not key:
        raise RuntimeError("SCOPED_JWT_SECRET is required to mint a scoped token")
    claims: dict[str, Any] = {"orgId": org_id, "scopes": scopes}
    if user_id:
        claims["userId"] = user_id
    return mint_service_token(key, claims)


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


class DocumentClient(APIClient):
    """Client for /api/v1/document.

    Every route takes a scoped service token, so ``token`` replaces the admin
    session token; without it ``auth=True`` sends the admin's user JWT, which
    these routes refuse.
    """

    BASE = DOCUMENT_BASE

    def call(
        self,
        method: str,
        path: str = "",
        *,
        token: str | None = None,
        auth: bool = True,
        **kwargs: Any,
    ) -> requests.Response:
        if token is not None:
            headers = {**bearer(token), **(kwargs.pop("headers", None) or {})}
            return self._client.request(
                method, self._path(path), auth=False, headers=headers, **kwargs
            )
        return self._client.request(method, self._path(path), auth=auth, **kwargs)

    def update_app_config(
        self, *, token: str | None = None, auth: bool = True, **kwargs: Any
    ) -> requests.Response:
        return self.call("POST", "/updateAppConfig", token=token, auth=auth, **kwargs)


def request_as(
    user: SecondUser, method: str, path: str = "", **kwargs: Any
) -> requests.Response:
    """Call a document route as the non-admin member; path is relative to the router."""
    kwargs.setdefault("timeout", user.timeout)
    return requests.request(
        method,
        f"{user.base_url}{DOCUMENT_BASE}{path}",
        headers=user.headers,
        **kwargs,
    )
