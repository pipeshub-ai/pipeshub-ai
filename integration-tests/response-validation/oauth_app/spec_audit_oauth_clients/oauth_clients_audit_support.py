"""Client, constants and helpers for the strict OpenAPI audit of /api/v1/oauth-clients."""

from __future__ import annotations

import uuid
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
MALFORMED_JSON_BODY = "{not json"
JSON_HEADERS = {"Content-Type": "application/json"}
TOKEN_ENDPOINT = "/api/v1/oauth2/token"

LIST_ROUTE = OAUTH_CLIENTS_BASE
SCOPES_ROUTE = f"{OAUTH_CLIENTS_BASE}/scopes"
APP_ROUTE = f"{OAUTH_CLIENTS_BASE}/:appId"
REGENERATE_SECRET_ROUTE = f"{APP_ROUTE}/regenerate-secret"
SUSPEND_ROUTE = f"{APP_ROUTE}/suspend"
ACTIVATE_ROUTE = f"{APP_ROUTE}/activate"
TOKENS_ROUTE = f"{APP_ROUTE}/tokens"
REVOKE_ALL_TOKENS_ROUTE = f"{APP_ROUTE}/revoke-all-tokens"
TOKEN_IDENTITY_ROUTE = f"{APP_ROUTE}/token-identity"

# Scopes only an org admin may put on an app (getAllowedScopeNamesForRole).
ADMIN_ONLY_SCOPES = (
    "org:write",
    "org:admin",
    "user:invite",
    "user:delete",
    "usergroup:write",
    "team:write",
    "config:write",
    "crawl:write",
    "crawl:delete",
)

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


def app_name() -> str:
    return f"spec-audit {uuid.uuid4().hex[:8]}"


def mint_client_credentials_token(base_url: str, app: dict[str, Any], scope: str) -> str:
    """Issue one access token from a seeded client_credentials app, so its token list is not empty."""
    resp = requests.post(
        f"{base_url}{TOKEN_ENDPOINT}",
        json={
            "grant_type": "client_credentials",
            "client_id": app["clientId"],
            "client_secret": app["clientSecret"],
            "scope": scope,
        },
        timeout=60,
    )
    assert resp.status_code == 200, f"minting a client_credentials token failed: {resp.status_code} {resp.text[:300]}"
    token: str = resp.json()["access_token"]
    return token


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
