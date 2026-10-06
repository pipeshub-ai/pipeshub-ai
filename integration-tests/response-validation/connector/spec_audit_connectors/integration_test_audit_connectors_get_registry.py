"""Strict OpenAPI audit of GET /api/v1/connectors/registry."""

from __future__ import annotations

from typing import Any

import pytest
from connectors_audit_support import (
    INVALID_LIST_IDS,
    INVALID_LIST_QUERIES,
    ConnectorsAuditClient,
    bearer,
    request_as,
)
from helper.second_user import SecondUser
from strict_openapi import assert_strict_openapi_exchange, outside_request_contract

pytestmark = pytest.mark.spec_audit

ROUTE = "/api/v1/connectors/registry"


def test_admin_lists_every_registered_connector_type(
    connectors_client: ConnectorsAuditClient,
) -> None:
    # The largest page, so the strict check sees as many connector shapes as possible.
    resp = connectors_client.get("/registry", params={"limit": 200})
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)

    body = resp.json()
    assert body["success"] is True
    assert body["connectors"], "the registry lists no connector type"
    assert body["pagination"]["page"] == 1
    assert body["pagination"]["limit"] == 200
    assert body["pagination"]["totalCount"] >= len(body["connectors"])
    assert set(body["registryCountsByScope"]) == {"personal", "team"}
    # scope defaults to team, and every entry echoes the scope it was listed for.
    assert {entry["scope"] for entry in body["connectors"]} == {"team"}
    assert body["pagination"]["totalCount"] == body["registryCountsByScope"]["team"]


def test_member_pages_and_searches_the_personal_registry(
    second_user: SecondUser,
) -> None:
    # No admin gate on this route; a one-item page also exercises nextPage / search echo.
    resp = request_as(
        second_user,
        "GET",
        "/registry",
        params={"scope": "personal", "page": 1, "limit": 1, "search": "a"},
    )
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)

    body = resp.json()
    assert len(body["connectors"]) == 1
    assert body["connectors"][0]["scope"] == "personal"
    assert body["pagination"]["limit"] == 1
    assert body["pagination"]["search"] == "a"
    assert body["pagination"]["hasPrev"] is False
    assert body["pagination"]["prevPage"] is None
    assert body["pagination"]["hasNext"] is True
    assert body["pagination"]["nextPage"] == 2


def test_registry_accepts_and_ignores_the_instance_filters(
    connectors_client: ConnectorsAuditClient,
) -> None:
    # The validator is shared with GET /connectors, so the three instance filters are
    # checked here too, but the controller never forwards them.
    unfiltered = connectors_client.get("/registry", params={"limit": 200})
    assert unfiltered.status_code == 200, unfiltered.text[:500]

    resp = connectors_client.get(
        "/registry",
        params={
            "limit": 200,
            "isActive": "true",
            "isAuthenticated": "false",
            "connectorType": "spec-audit-no-such-type",
        },
    )
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert resp.json()["connectors"] == unfiltered.json()["connectors"]


def test_registry_search_with_no_match_is_an_empty_page(
    connectors_client: ConnectorsAuditClient,
) -> None:
    resp = connectors_client.get("/registry", params={"search": "spec-audit-no-such-connector"})
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)

    body = resp.json()
    assert body["connectors"] == []
    assert body["pagination"]["totalCount"] == 0
    assert body["pagination"]["totalPages"] == 0
    # The counts are taken before the search is applied.
    assert body["registryCountsByScope"]["team"] > 0


def test_registry_ignores_an_unknown_query_parameter(
    connectors_client: ConnectorsAuditClient,
) -> None:
    with outside_request_contract("the validator strips query parameters it does not know"):
        resp = connectors_client.get("/registry", params={"specAuditUnknown": "1"})
        assert resp.status_code == 200, resp.text[:500]
        assert_strict_openapi_exchange(resp, ROUTE)


@pytest.mark.parametrize(
    "headers",
    [
        pytest.param(None, id="no-token"),
        pytest.param({"Authorization": "Bearer not.a.jwt"}, id="invalid-token"),
    ],
)
def test_registry_without_a_valid_token_is_unauthorized(
    connectors_client: ConnectorsAuditClient, headers: dict[str, str] | None
) -> None:
    resp = connectors_client.get("/registry", auth=False, headers=headers)
    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


def test_registry_without_the_connector_read_scope_is_forbidden(
    connectors_client: ConnectorsAuditClient, token_without_connector_scopes: str
) -> None:
    resp = connectors_client.get(
        "/registry", auth=False, headers=bearer(token_without_connector_scopes)
    )
    assert resp.status_code == 403, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


@pytest.mark.parametrize("params", INVALID_LIST_QUERIES, ids=INVALID_LIST_IDS)
def test_registry_rejects_invalid_query(
    connectors_client: ConnectorsAuditClient, params: Any
) -> None:
    resp = connectors_client.get("/registry", params=params)
    assert resp.status_code == 400, resp.text[:500]
    assert resp.json()["error"]["code"] == "VALIDATION_ERROR", resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
