"""Strict OpenAPI audit of GET /api/v1/connectors/configured."""

from __future__ import annotations

import pytest
from connectors_audit_support import ConnectorsAuditClient, request_as
from helper.second_user import SecondUser
from strict_openapi import assert_strict_openapi_response

pytestmark = pytest.mark.spec_audit

ROUTE = "/api/v1/connectors/configured"

# The Python handler returns {"success", "connectors": <registry result>} where the
# registry result is itself {"connectors": [...], "pagination": {...}}; GET / and
# GET /agents/active spread the same result instead.
NESTED_LISTING_BUG = (
    "API bug: GET /connectors/configured nests the page under connectors "
    "(connectors.connectors, connectors.pagination) instead of returning the list "
    "and pagination at the top level like the other connector lists"
)


@pytest.mark.xfail(strict=True, reason=NESTED_LISTING_BUG)
def test_admin_lists_configured_connectors(
    connectors_client: ConnectorsAuditClient,
) -> None:
    resp = connectors_client.get("/configured", params={"page": 1, "limit": 5})
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)

    body = resp.json()
    assert body["success"] is True
    assert isinstance(body["connectors"], list)
    assert all(item["isConfigured"] for item in body["connectors"])
    assert body["pagination"]["page"] == 1
    assert body["pagination"]["limit"] == 5


@pytest.mark.xfail(strict=True, reason=NESTED_LISTING_BUG)
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
