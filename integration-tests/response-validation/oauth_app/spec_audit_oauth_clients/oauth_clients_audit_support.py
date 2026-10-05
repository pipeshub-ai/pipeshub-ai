"""Client, constants and helpers for the strict OpenAPI audit of /api/v1/oauth-clients."""

from __future__ import annotations

from typing import Any, Callable

import requests

from helper.clients.oauth_client import OAuthAppsClient
from helper.second_user import SecondUser

OAUTH_CLIENTS_BASE = "/api/v1/oauth-clients"
SERVICE_ACCOUNTS_BASE = "/api/v1/service-accounts"
MISSING_APP_ID = "0123456789abcdef01234567"
MALFORMED_APP_ID = "not-an-object-id"
MISSING_SERVICE_ACCOUNT_ID = "0123456789abcdef01234568"
MALFORMED_SERVICE_ACCOUNT_ID = "not-an-object-id"

# Returns the `app` object of the create response (id, clientId, name, ...).
SeedOAuthApp = Callable[..., dict[str, Any]]
# Returns the service account view (id, slug, fullName, email, isDisabled, ...).
SeedServiceAccount = Callable[..., dict[str, Any]]


class OAuthClientsAuditClient(OAuthAppsClient):
    """OAuthAppsClient plus the routes it has no method for."""

    def set_token_identity(
        self, app_id: str, *, auth: bool = True, **kwargs: Any
    ) -> requests.Response:
        """PUT /:appId/token-identity; pass ``json=`` yourself so any body can be sent."""
        return self.put(f"/{app_id}/token-identity", auth=auth, **kwargs)


def token_identity_body(service_account_id: str | None) -> dict[str, str | None]:
    return {"serviceAccountId": service_account_id}


def request_as(
    user: SecondUser, method: str, path: str = "", **kwargs: Any
) -> requests.Response:
    """Call an oauth-clients route as the non-admin member; path is relative to the router."""
    kwargs.setdefault("timeout", user.timeout)
    return requests.request(
        method,
        f"{user.base_url}{OAUTH_CLIENTS_BASE}{path}",
        headers=user.headers,
        **kwargs,
    )
