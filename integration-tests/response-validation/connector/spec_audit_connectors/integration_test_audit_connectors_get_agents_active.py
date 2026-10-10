"""Strict OpenAPI audit of GET /api/v1/connectors/agents/active."""

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

ROUTE = "/api/v1/connectors/agents/active"
PATH = "/agents/active"


def test_admin_lists_active_agent_instances(
    connectors_client: ConnectorsAuditClient,
) -> None:
    resp = connectors_client.get(PATH, params={"page": 1, "limit": 5})
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)

    body: dict[str, Any] = resp.json()
    assert body["success"] is True
    assert isinstance(body["connectors"], list)
    assert len(body["connectors"]) <= 5
    for instance in body["connectors"]:
        assert instance["isAgentActive"] is True
        assert instance["isConfigured"] is True
    pagination = body["pagination"]
    assert pagination["page"] == 1
    assert pagination["limit"] == 5
    assert pagination["search"] is None
    assert pagination["totalCount"] >= len(body["connectors"])
    assert pagination["hasPrev"] is False


def test_instance_not_enabled_for_agents_is_not_listed(
    connectors_client: ConnectorsAuditClient, seed_connector: SeedConnector
) -> None:
    name = unique_name("spec-audit-agents")
    seed_connector(instanceName=name)

    resp = connectors_client.get(PATH, params={"search": name, "scope": "team"})
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)

    body = resp.json()
    assert body["connectors"] == []
    assert body["pagination"]["totalCount"] == 0
    assert body["pagination"]["totalPages"] == 0
    assert body["pagination"]["search"] == name


def test_member_lists_personal_scope_with_search(second_user: SecondUser) -> None:
    # No admin gate: connector:read is enough, and the search term is echoed in pagination.
    resp = request_as(
        second_user,
        "GET",
        PATH,
        params={"scope": "personal", "search": "spec-audit-no-such-agent"},
    )
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)

    body: dict[str, Any] = resp.json()
    assert body["success"] is True
    assert body["connectors"] == []
    assert body["pagination"]["totalCount"] == 0


def test_agents_active_accepts_and_ignores_the_instance_filters(
    connectors_client: ConnectorsAuditClient,
) -> None:
    # Shared validator with GET /connectors; the controller forwards only scope/page/limit/search.
    unfiltered = connectors_client.get(PATH, params={"limit": 200})
    assert unfiltered.status_code == 200, unfiltered.text[:500]

    resp = connectors_client.get(
        PATH,
        params={
            "limit": 200,
            "isActive": "false",
            "isAuthenticated": "false",
            "connectorType": "spec-audit-no-such-type",
        },
    )
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert resp.json()["pagination"]["totalCount"] == unfiltered.json()["pagination"]["totalCount"]


def test_agents_active_ignores_an_unknown_query_parameter(
    connectors_client: ConnectorsAuditClient,
) -> None:
    with outside_request_contract("the validator strips query parameters it does not know"):
        resp = connectors_client.get(PATH, params={"specAuditUnknown": "1"})
        assert resp.status_code == 200, resp.text[:500]
        assert_strict_openapi_exchange(resp, ROUTE)


def test_without_token_is_unauthorized(
    connectors_client: ConnectorsAuditClient,
) -> None:
    resp = connectors_client.get(PATH, auth=False)
    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


def test_without_the_connector_read_scope_is_forbidden(
    connectors_client: ConnectorsAuditClient, token_without_connector_scopes: str
) -> None:
    resp = connectors_client.get(PATH, auth=False, headers=bearer(token_without_connector_scopes))
    assert resp.status_code == 403, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


@pytest.mark.parametrize("params", INVALID_LIST_QUERIES, ids=INVALID_LIST_IDS)
def test_invalid_query_is_rejected_by_validator(
    connectors_client: ConnectorsAuditClient, params: Any
) -> None:
    resp = connectors_client.get(PATH, params=params)
    assert resp.status_code == 400, resp.text[:500]
    assert resp.json()["error"]["code"] == "VALIDATION_ERROR", resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
