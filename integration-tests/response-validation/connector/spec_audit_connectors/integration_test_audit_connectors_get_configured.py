"""Strict OpenAPI audit of GET /api/v1/connectors/configured."""

from __future__ import annotations

from typing import Any

import pytest
from connectors_audit_support import (
    INVALID_LIST_IDS,
    INVALID_LIST_QUERIES,
    ConnectorsAuditClient,
    SeedConnector,
    bearer,
    request_as,
    unique_name,
)
from helper.second_user import SecondUser
from strict_openapi import assert_strict_openapi_exchange, outside_request_contract

pytestmark = pytest.mark.spec_audit

ROUTE = "/api/v1/connectors/configured"


def _page(body: dict[str, Any]) -> dict[str, Any]:
    # API bug: the Python handler returns {"success", "connectors": <registry result>} where
    # the registry result is itself {"connectors": [...], "pagination": {...}}. GET / and
    # GET /agents/active spread the same result, so only this list is nested.
    assert set(body) == {"success", "connectors"}, body
    page = body["connectors"]
    assert set(page) == {"connectors", "pagination"}, page
    return page


def _names(page: dict[str, Any]) -> list[str]:
    return [instance["name"] for instance in page["connectors"]]


def test_admin_lists_configured_connectors(
    connectors_client: ConnectorsAuditClient, seed_connector: SeedConnector
) -> None:
    name = unique_name("spec-audit-configured")
    connector_id = seed_connector(instanceName=name)

    resp = connectors_client.get("/configured", params={"search": name, "page": 1, "limit": 5})
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)

    body = resp.json()
    assert body["success"] is True
    page = _page(body)
    # A Demo instance needs no credentials, so it is stored as configured from creation.
    assert [instance["_key"] for instance in page["connectors"]] == [connector_id]
    assert page["connectors"][0]["isConfigured"] is True
    assert "config" not in page["connectors"][0]
    assert page["pagination"] == {
        "page": 1,
        "limit": 5,
        "search": name,
        "totalCount": 1,
        "totalPages": 1,
        "hasPrev": False,
        "hasNext": False,
        "prevPage": None,
        "nextPage": None,
    }


def test_pages_walk_every_matching_instance_newest_first(
    connectors_client: ConnectorsAuditClient, seed_connector: SeedConnector
) -> None:
    token = unique_name("spec-audit-window")
    names = [f"{token} {index}" for index in range(3)]
    for name in names:
        seed_connector(instanceName=name)

    everything = connectors_client.get("/configured", params={"search": token})
    assert everything.status_code == 200, everything.text[:500]
    assert_strict_openapi_exchange(everything, ROUTE)
    assert _names(_page(everything.json())) == names[::-1]
    assert _page(everything.json())["pagination"]["totalCount"] == 3

    walked: list[str] = []
    for page_number in (1, 2, 3):
        resp = connectors_client.get(
            "/configured", params={"search": token, "limit": 1, "page": page_number}
        )
        assert resp.status_code == 200, resp.text[:500]
        assert_strict_openapi_exchange(resp, ROUTE)
        page = _page(resp.json())
        assert len(page["connectors"]) == 1, page
        pagination = page["pagination"]
        assert pagination["totalCount"] == 3
        assert pagination["totalPages"] == 3
        assert pagination["hasPrev"] is (page_number > 1)
        assert pagination["hasNext"] is (page_number < 3)
        assert pagination["nextPage"] == (page_number + 1 if page_number < 3 else None)
        walked.extend(_names(page))
    assert walked == names[::-1]

    beyond = connectors_client.get("/configured", params={"search": token, "limit": 1, "page": 4})
    assert beyond.status_code == 200, beyond.text[:500]
    assert_strict_openapi_exchange(beyond, ROUTE)
    assert _page(beyond.json())["connectors"] == []
    assert _page(beyond.json())["pagination"]["totalCount"] == 3


def test_member_lists_own_personal_configured_connectors(
    second_user: SecondUser, member_connector: SeedConnector
) -> None:
    connector_id = member_connector()

    resp = request_as(second_user, "GET", "/configured", params={"scope": "personal", "limit": 200})
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)

    page = _page(resp.json())
    listed = {instance["_key"]: instance for instance in page["connectors"]}
    assert connector_id in listed
    assert listed[connector_id]["scope"] == "personal"
    assert page["pagination"]["search"] is None

    # Without scope the validator defaults it to team, which leaves the personal one out.
    default_scope = request_as(second_user, "GET", "/configured", params={"limit": 200})
    assert default_scope.status_code == 200, default_scope.text[:500]
    assert_strict_openapi_exchange(default_scope, ROUTE)
    scopes = {instance["scope"] for instance in _page(default_scope.json())["connectors"]}
    assert scopes <= {"team"}, scopes


def test_configured_accepts_and_ignores_the_instance_filters(
    connectors_client: ConnectorsAuditClient, seed_connector: SeedConnector
) -> None:
    # Shared validator with GET /connectors; the controller forwards only scope/page/limit/search.
    name = unique_name("spec-audit-ignored")
    connector_id = seed_connector(instanceName=name)

    resp = connectors_client.get(
        "/configured",
        params={
            "search": name,
            "isActive": "true",
            "isAuthenticated": "true",
            "connectorType": "spec-audit-no-such-type",
        },
    )
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    # An inactive, unauthenticated Demo instance is still listed: none of the three filtered.
    assert [c["_key"] for c in _page(resp.json())["connectors"]] == [connector_id]


def test_configured_ignores_an_unknown_query_parameter(
    connectors_client: ConnectorsAuditClient,
) -> None:
    with outside_request_contract("the validator strips query parameters it does not know"):
        resp = connectors_client.get("/configured", params={"specAuditUnknown": "1"})
        assert resp.status_code == 200, resp.text[:500]
        assert_strict_openapi_exchange(resp, ROUTE)


def test_requires_authentication(connectors_client: ConnectorsAuditClient) -> None:
    resp = connectors_client.get("/configured", auth=False)
    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


def test_configured_without_the_connector_read_scope_is_forbidden(
    connectors_client: ConnectorsAuditClient, token_without_connector_scopes: str
) -> None:
    resp = connectors_client.get(
        "/configured", auth=False, headers=bearer(token_without_connector_scopes)
    )
    assert resp.status_code == 403, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


@pytest.mark.parametrize("params", INVALID_LIST_QUERIES, ids=INVALID_LIST_IDS)
def test_invalid_query_is_rejected(
    connectors_client: ConnectorsAuditClient, params: Any
) -> None:
    resp = connectors_client.get("/configured", params=params)
    assert resp.status_code == 400, resp.text[:500]
    assert resp.json()["error"]["code"] == "VALIDATION_ERROR", resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
