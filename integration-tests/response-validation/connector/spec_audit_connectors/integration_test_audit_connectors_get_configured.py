"""Strict OpenAPI audit of GET /api/v1/connectors/configured."""

from __future__ import annotations

import pytest
from connectors_audit_support import ConnectorsAuditClient, request_as
from helper.second_user import SecondUser
from strict_openapi import assert_strict_openapi_response

pytestmark = pytest.mark.spec_audit

ROUTE = "/api/v1/connectors/configured"


def test_admin_lists_configured_connectors(
    connectors_client: ConnectorsAuditClient,
) -> None:
    resp = connectors_client.get("/configured", params={"page": 1, "limit": 5})
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)

    body = resp.json()
    assert body["success"] is True
    # Python nests the registry result, so the list and pagination sit one level down.
    listing = body["connectors"]
    assert isinstance(listing["connectors"], list)
    assert all(item["isConfigured"] for item in listing["connectors"])
    assert listing["pagination"]["page"] == 1
    assert listing["pagination"]["limit"] == 5


def test_member_lists_own_personal_configured_connectors(
    second_user: SecondUser,
) -> None:
    resp = request_as(
        second_user, "GET", "/configured", params={"scope": "personal"}
    )
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)
    assert resp.json()["success"] is True


def test_requires_authentication(connectors_client: ConnectorsAuditClient) -> None:
    resp = connectors_client.get("/configured", auth=False)
    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)


@pytest.mark.parametrize(
    "params",
    [{"scope": "org"}, {"limit": 201}],
    ids=["unknown-scope", "limit-above-max"],
)
def test_invalid_query_is_rejected(
    connectors_client: ConnectorsAuditClient, params: dict[str, str | int]
) -> None:
    resp = connectors_client.get("/configured", params=params)
    assert resp.status_code == 400, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)
