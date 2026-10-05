"""Strict OpenAPI audit of GET /api/v1/connectors/navigate."""

from __future__ import annotations

from typing import Any

import pytest
from connectors_audit_support import ConnectorsAuditClient
from strict_openapi import assert_strict_openapi_response

pytestmark = pytest.mark.spec_audit

ROUTE = "/api/v1/connectors/navigate"


def test_navigate_root_lists_connected_apps(
    connectors_client: ConnectorsAuditClient,
) -> None:
    resp = connectors_client.navigate()
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)

    body = resp.json()
    assert body["current"] is None
    assert isinstance(body["rows"], list)
    assert isinstance(body["text"], str)


def test_navigate_into_connector_node(
    connectors_client: ConnectorsAuditClient, connector_id: str
) -> None:
    # An app node the caller cannot open is answered as an empty view, not a 404,
    # so this is 200 whether or not the unsynced instance is reachable.
    resp = connectors_client.navigate(
        connector_id,
        page=1,
        limit=50,
        depth=2,
        node_types=["recordGroup", "record"],
        created_after="2020-01-01",
    )
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)
    assert isinstance(resp.json()["rows"], list)


@pytest.mark.parametrize(
    "params",
    [
        # Refused by the Node zod schema: the limit floor is 50.
        pytest.param({"limit": 10}, id="limit-below-floor"),
        # Passes zod (any non-empty string) and is refused by the Python time parser.
        pytest.param({"createdAfter": "2024-01-15T08:00:00"}, id="created-after-without-timezone"),
    ],
)
def test_navigate_invalid_query_is_rejected(
    connectors_client: ConnectorsAuditClient, params: dict[str, Any]
) -> None:
    resp = connectors_client.get("/navigate", params=params)
    assert resp.status_code == 400, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)


def test_navigate_without_token_is_unauthorized(
    connectors_client: ConnectorsAuditClient,
) -> None:
    resp = connectors_client.navigate(auth=False)
    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)
