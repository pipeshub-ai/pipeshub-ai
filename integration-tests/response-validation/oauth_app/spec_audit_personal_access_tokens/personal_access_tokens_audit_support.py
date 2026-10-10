"""Client, constants and helpers for the strict OpenAPI audit of /api/v1/personal-access-tokens."""

from __future__ import annotations

import uuid
from typing import Any, Callable

import requests

from helper.http.api_client import APIClient
from helper.second_user import SecondUser

PATS_BASE = "/api/v1/personal-access-tokens"
MISSING_TOKEN_ID = "0123456789abcdef01234567"
MALFORMED_TOKEN_ID = "not-an-object-id"
PAT_PREFIX = "pat_"
EXPIRY_CHOICES = (30, 90, 365, "never")
# Passes the zod schema (any string) but is not a scope the backend knows.
UNKNOWN_SCOPE = "spec-audit:not-a-scope"

# mint_pat(as_user=None, **body) -> the "token" object of the 201 response.
MintPat = Callable[..., dict[str, Any]]


def pat_name() -> str:
    return f"spec-audit-pat-{uuid.uuid4().hex[:12]}"


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}


class PatsClient(APIClient):
    """Client for /api/v1/personal-access-tokens, acting as whoever the wrapped client is."""

    BASE = PATS_BASE

    def list(self, *, auth: bool = True, **kwargs: Any) -> requests.Response:
        return self.get("", auth=auth, **kwargs)

    def create(self, body: Any = None, *, auth: bool = True, **kwargs: Any) -> requests.Response:
        return self.post("", auth=auth, json=body, **kwargs)

    def scopes(self, *, auth: bool = True, **kwargs: Any) -> requests.Response:
        return self.get("/scopes", auth=auth, **kwargs)

    def revoke(self, token_id: str, *, auth: bool = True, **kwargs: Any) -> requests.Response:
        return self.delete(f"/{token_id}", auth=auth, **kwargs)

    def admin_list(self, *, auth: bool = True, **params: Any) -> requests.Response:
        return self.get("/admin", auth=auth, params=params)

    def admin_revoke(self, token_id: str, *, auth: bool = True, **kwargs: Any) -> requests.Response:
        return self.delete(f"/admin/{token_id}", auth=auth, **kwargs)


def request_as(
    user: SecondUser, method: str, path: str = "", **kwargs: Any
) -> requests.Response:
    """Call a PAT route as the non-admin member (a session JWT); path is relative to the router."""
    kwargs.setdefault("timeout", user.timeout)
    return requests.request(
        method, f"{user.base_url}{PATS_BASE}{path}", headers=user.headers, **kwargs
    )


def request_with_token(
    base_url: str, token: str, method: str, path: str = "", **kwargs: Any
) -> requests.Response:
    """Call a PAT route with an arbitrary bearer token, e.g. a PAT itself or a garbage string."""
    kwargs.setdefault("timeout", 60)
    return requests.request(
        method, f"{base_url}{PATS_BASE}{path}", headers=bearer(token), **kwargs
    )
