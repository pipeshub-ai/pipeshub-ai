"""Client, constants and helpers for the strict OpenAPI audit of /api/v1/oauth."""

from __future__ import annotations

import uuid
from typing import Any, Callable, TypedDict

import requests

from helper.http.api_client import APIClient
from helper.second_user import SecondUser

OAUTH_BASE = "/api/v1/oauth"

# Registered in the Python OAuth config registry; the lookup is case-sensitive.
SEED_CONNECTOR_TYPE = "Confluence"
UNKNOWN_CONNECTOR_TYPE = "SpecAuditNoSuchConnector"
# Config ids are uuid4 strings; nothing validates their format, so an unknown one is a 404.
MISSING_CONFIG_ID = "00000000-0000-4000-8000-000000000000"
# Decodes to "bad%id", which guardPathParams refuses for connectorType and configId alike.
UNSAFE_PATH_SEGMENT = "bad%25id"

NAME_PREFIX = "spec-audit-oauth-"


class SeededOAuthConfig(TypedDict):
    id: str
    connector_type: str
    name: str
    body: dict[str, Any]


SeedOAuthConfig = Callable[..., SeededOAuthConfig]


class ConnectorOAuthClient(APIClient):
    """Client for /api/v1/oauth (connector OAuth app configs), acting as the shared org admin."""

    BASE = OAUTH_BASE

    def registry(self, *, auth: bool = True, **params: Any) -> requests.Response:
        return self.get("/registry", auth=auth, params=params)

    def registry_entry(self, connector_type: str, *, auth: bool = True) -> requests.Response:
        return self.get(f"/registry/{connector_type}", auth=auth)

    def list_all(self, *, auth: bool = True, **params: Any) -> requests.Response:
        return self.get("", auth=auth, params=params)

    def list_for_type(
        self, connector_type: str, *, auth: bool = True, **params: Any
    ) -> requests.Response:
        return self.get(f"/{connector_type}", auth=auth, params=params)

    def create(
        self, connector_type: str, body: Any = None, *, auth: bool = True
    ) -> requests.Response:
        return self.post(f"/{connector_type}", auth=auth, json=body)

    def fetch(self, connector_type: str, config_id: str, *, auth: bool = True) -> requests.Response:
        return self.get(f"/{connector_type}/{config_id}", auth=auth)

    def update(
        self, connector_type: str, config_id: str, body: Any = None, *, auth: bool = True
    ) -> requests.Response:
        return self.put(f"/{connector_type}/{config_id}", auth=auth, json=body)

    def remove(self, connector_type: str, config_id: str, *, auth: bool = True) -> requests.Response:
        return self.delete(f"/{connector_type}/{config_id}", auth=auth)


def unique_name() -> str:
    return f"{NAME_PREFIX}{uuid.uuid4().hex[:10]}"


def oauth_config_body(name: str | None = None, **config: Any) -> dict[str, Any]:
    """A create body the Node validator and the Python handler both accept."""
    return {
        "oauthInstanceName": name or unique_name(),
        "config": {
            "clientId": "spec-audit-client-id",
            "clientSecret": "spec-audit-client-secret",
            **config,
        },
        # Without it Python reads the frontend endpoint from the KV store to build redirectUri.
        "baseUrl": "http://localhost:3001",
    }


def created_config_id(resp: requests.Response) -> str | None:
    """The id in a create/update/get reply, or None when the reply carries none."""
    try:
        body = resp.json()
    except ValueError:
        return None
    if not isinstance(body, dict) or not isinstance(body.get("oauthConfig"), dict):
        return None
    config_id = body["oauthConfig"].get("_id")
    return str(config_id) if config_id else None


def request_as(
    user: SecondUser, method: str, path: str = "", **kwargs: Any
) -> requests.Response:
    """Call an OAuth config route as the non-admin member; path is relative to the router."""
    kwargs.setdefault("timeout", user.timeout)
    return requests.request(
        method,
        f"{user.base_url}{OAUTH_BASE}{path}",
        headers=user.headers,
        **kwargs,
    )


def bearer(token: str) -> dict[str, str]:
    """Headers for a raw token; pass with auth=False, e.g. the inherited scope-less token."""
    return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
