"""Client, constants and helpers for the strict OpenAPI audit of /api/v1/service-tokens."""

from __future__ import annotations

from typing import Any, Callable

import requests

from helper.http.api_client import APIClient
from helper.second_user import SecondUser

SERVICE_TOKENS_BASE = "/api/v1/service-tokens"
SERVICE_ACCOUNTS_BASE = "/api/v1/service-accounts"
MISSING_SERVICE_ACCOUNT_ID = "0123456789abcdef01234567"
MALFORMED_SERVICE_ACCOUNT_ID = "not-an-object-id"
MISSING_TOKEN_ID = "0123456789abcdef01234568"
MALFORMED_TOKEN_ID = "not-an-object-id"

# In the default MCP_SCOPES of every compose file and not refused for service tokens.
DEFAULT_TOKEN_SCOPES = ("kb:read",)
# In MCP_SCOPES but refused: service tokens are read-only.
DENIED_TOKEN_SCOPE = "agent:execute"
UNKNOWN_TOKEN_SCOPE = "spec-audit:no-such-scope"
MAX_EXPIRY_DAYS = 365
DEFAULT_EXPIRY_DAYS = 90
# SERVICE_TOKEN_MAX_ACTIVE in service-token.service.ts.
MAX_ACTIVE_TOKENS = 50

# Returns the service account view (id, slug, fullName, email, isDisabled, ...).
SeedServiceAccount = Callable[..., dict[str, Any]]
# Returns the `token` object of the create response (id, name, accessToken, ...).
SeedServiceToken = Callable[..., dict[str, Any]]


class ServiceTokensClient(APIClient):
    """Client for /api/v1/service-tokens, acting as the shared org admin.

    ``service_account_id=None`` leaves the parameter out, to reach the validator's 400.
    """

    BASE = SERVICE_TOKENS_BASE

    def list_tokens(
        self, service_account_id: str | None = None, *, auth: bool = True, **params: Any
    ) -> requests.Response:
        return self.get("", auth=auth, params=_account_params(service_account_id, params))

    def create_token(self, *, auth: bool = True, **body: Any) -> requests.Response:
        """POST / with exactly the fields given; see ``create_token_body``."""
        return self.post("", auth=auth, json=body)

    def list_scopes(self, *, auth: bool = True) -> requests.Response:
        return self.get("/scopes", auth=auth)

    def revoke_token(
        self,
        token_id: str,
        service_account_id: str | None = None,
        *,
        auth: bool = True,
        **params: Any,
    ) -> requests.Response:
        return self.delete(
            f"/{token_id}", auth=auth, params=_account_params(service_account_id, params)
        )


def _account_params(service_account_id: str | None, params: dict[str, Any]) -> dict[str, Any]:
    if service_account_id is not None:
        params = {"serviceAccountId": service_account_id, **params}
    return params


def create_token_body(service_account_id: str, **overrides: Any) -> dict[str, Any]:
    """A valid POST body; an override set to None is dropped from the body."""
    body: dict[str, Any] = {
        "serviceAccountId": service_account_id,
        "name": "spec-audit token",
        "scopes": list(DEFAULT_TOKEN_SCOPES),
        **overrides,
    }
    return {key: value for key, value in body.items() if value is not None}


def request_as(
    user: SecondUser, method: str, path: str = "", **kwargs: Any
) -> requests.Response:
    """Call a service-tokens route as the non-admin member; path is relative to the router."""
    kwargs.setdefault("timeout", user.timeout)
    return requests.request(
        method,
        f"{user.base_url}{SERVICE_TOKENS_BASE}{path}",
        headers=user.headers,
        **kwargs,
    )


def request_with_token(
    base_url: str, token: str, method: str, path: str = "", **kwargs: Any
) -> requests.Response:
    """Call a service-tokens route with an arbitrary bearer token, e.g. a narrowly scoped PAT."""
    kwargs.setdefault("timeout", 60)
    return requests.request(
        method,
        f"{base_url}{SERVICE_TOKENS_BASE}{path}",
        headers={"Authorization": f"Bearer {token}"},
        **kwargs,
    )
