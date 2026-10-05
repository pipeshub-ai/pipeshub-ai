"""Client, constants and helpers for the strict OpenAPI audit of /api/v1/connectors."""

from __future__ import annotations

import uuid
from typing import Any, Callable

import requests

from helper.clients.connectors_client import ConnectorsClient
from helper.second_user import SecondUser

CONNECTORS_BASE = "/api/v1/connectors"

MISSING_CONNECTOR_ID = "00000000-0000-4000-8000-000000000000"
# Express decodes this to "bad/id", which guardPathParams refuses on every
# :connectorId / :connectorType route before auth runs.
UNSAFE_CONNECTOR_ID = "bad%2Fid"
# Passes guardPathParams but not the connectorIdParamSchema regex, which only
# some routes use (the others accept any non-empty string and ask Python).
MALFORMED_CONNECTOR_ID = "bad.id"
MISSING_CONNECTOR_TYPE = "NoSuchConnectorType"

# Needs no credentials and contacts nothing external; team scope only.
SEED_CONNECTOR_TYPE = "Demo"
SEED_CONNECTOR_SCOPE = "team"
SEED_CONNECTOR_AUTH_TYPE = "NONE"

SeedConnector = Callable[..., str]


class ConnectorsAuditClient(ConnectorsClient):
    """Client for /api/v1/connectors, acting as the shared org admin."""

    def create_instance(
        self, *, auth: bool = True, **body: Any
    ) -> requests.Response:
        return self.post("/", auth=auth, json=body)

    def delete_instance(self, connector_id: str) -> requests.Response:
        return self.delete(f"/{connector_id}")


def create_seed_connector(client: ConnectorsAuditClient, **overrides: Any) -> str:
    """Create one unconfigured, never-synced Demo instance and return its connectorId."""
    body: dict[str, Any] = {
        "connectorType": SEED_CONNECTOR_TYPE,
        "instanceName": f"spec-audit {uuid.uuid4().hex[:8]}",
        "scope": SEED_CONNECTOR_SCOPE,
        "authType": SEED_CONNECTOR_AUTH_TYPE,
        **overrides,
    }
    resp = client.create_instance(**body)
    assert resp.status_code < 300, (
        f"could not seed a {body['connectorType']} connector: "
        f"{resp.status_code} {resp.text[:300]}"
    )
    return str(resp.json()["connector"]["connectorId"])


def request_as(
    user: SecondUser, method: str, path: str = "", **kwargs: Any
) -> requests.Response:
    """Call a connectors route as the non-admin member; path is relative to the router."""
    kwargs.setdefault("timeout", user.timeout)
    return requests.request(
        method,
        f"{user.base_url}{CONNECTORS_BASE}{path}",
        headers=user.headers,
        **kwargs,
    )
