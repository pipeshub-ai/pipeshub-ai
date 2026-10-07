"""Client, constants and helpers for the strict OpenAPI audit of /api/v1/service-accounts."""

from __future__ import annotations

import uuid
from typing import Any, Callable

import requests

from helper.http.api_client import APIClient
from helper.second_user import SecondUser

SERVICE_ACCOUNTS_BASE = "/api/v1/service-accounts"
ROOT_TEMPLATE = SERVICE_ACCOUNTS_BASE
BY_ID_TEMPLATE = f"{SERVICE_ACCOUNTS_BASE}/:id"

MISSING_SERVICE_ACCOUNT_ID = "0123456789abcdef01234567"
MALFORMED_SERVICE_ACCOUNT_ID = "not-an-object-id"

SERVICE_ACCOUNT_EMAIL_DOMAIN = "service.pipeshub.internal"
SLUG_MIN_LENGTH = 3
SLUG_MAX_LENGTH = 48
FULL_NAME_MAX_LENGTH = 100
DESCRIPTION_MAX_LENGTH = 500

# Returns the service account view (id, slug, fullName, email, isDisabled, ...).
SeedServiceAccount = Callable[..., dict[str, Any]]
# Registers the id of an account a test created itself, for removal on teardown.
TrackServiceAccount = Callable[[str], None]


def unique_slug() -> str:
    """A slug nothing has used: a slug stays bound to its (soft-deleted) record for good."""
    return f"spec-audit-{uuid.uuid4().hex[:10]}"


def create_body(slug: str | None = None, **overrides: Any) -> dict[str, Any]:
    """A valid POST body; overrides replace or add keys."""
    slug = slug or unique_slug()
    return {"slug": slug, "fullName": f"Spec audit {slug}", **overrides}


class ServiceAccountsClient(APIClient):
    """Client for /api/v1/service-accounts, acting as the shared org admin."""

    BASE = SERVICE_ACCOUNTS_BASE

    def list(self, *, auth: bool = True) -> requests.Response:
        return self.get("", auth=auth)

    def create(self, *, auth: bool = True, **kwargs: Any) -> requests.Response:
        """POST /; pass ``json=`` yourself so any body can be sent."""
        return self.post("", auth=auth, **kwargs)

    def fetch(self, account_id: str, *, auth: bool = True) -> requests.Response:
        return self.get(f"/{account_id}", auth=auth)

    def update(
        self, account_id: str, *, auth: bool = True, **kwargs: Any
    ) -> requests.Response:
        """PATCH /:id; pass ``json=`` yourself so any body can be sent."""
        return self.patch(f"/{account_id}", auth=auth, **kwargs)

    def remove(self, account_id: str, *, auth: bool = True) -> requests.Response:
        return self.delete(f"/{account_id}", auth=auth)


def request_as(
    user: SecondUser, method: str, path: str = "", **kwargs: Any
) -> requests.Response:
    """Call a service-accounts route as the non-admin member; path is relative to the router."""
    kwargs.setdefault("timeout", user.timeout)
    return requests.request(
        method,
        f"{user.base_url}{SERVICE_ACCOUNTS_BASE}{path}",
        headers=user.headers,
        **kwargs,
    )


def request_with_token(
    base_url: str, token: str, method: str, path: str = "", **kwargs: Any
) -> requests.Response:
    """Call a service-accounts route with an arbitrary bearer token, e.g. a narrowly scoped PAT."""
    kwargs.setdefault("timeout", 60)
    return requests.request(
        method,
        f"{base_url}{SERVICE_ACCOUNTS_BASE}{path}",
        headers={"Authorization": f"Bearer {token}"},
        **kwargs,
    )
