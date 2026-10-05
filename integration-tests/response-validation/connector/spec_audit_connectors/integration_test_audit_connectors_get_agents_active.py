"""Strict OpenAPI audit of GET /api/v1/connectors/agents/active."""

from __future__ import annotations

from typing import Any

import pytest
from connectors_audit_support import ConnectorsAuditClient, request_as
from helper.second_user import SecondUser
from strict_openapi import assert_strict_openapi_response

pytestmark = pytest.mark.spec_audit

ROUTE = "/api/v1/connectors/agents/active"
PATH = "/agents/active"


def test_admin_lists_active_agent_instances(
    connectors_client: ConnectorsAuditClient,
) -> None:
    resp = connectors_client.get(PATH, params={"page": 1, "limit": 5})
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)

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
    assert pagination["totalCount"] >= len(body["connectors"])
    assert pagination["hasPrev"] is False


def test_member_lists_personal_scope_with_search(second_user: SecondUser) -> None:
    # No admin gate: connector:read is enough, and the search term is echoed in pagination.
    resp = request_as(
        second_user,
        "GET",
        PATH,
        params={"scope": "personal", "search": "spec-audit-no-such-agent"},
    )
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)

    body: dict[str, Any] = resp.json()
    assert body["success"] is True
    assert body["connectors"] == []
    assert body["pagination"]["totalCount"] == 0


def test_without_token_is_unauthorized(
    connectors_client: ConnectorsAuditClient,
) -> None:
    resp = connectors_client.get(PATH, auth=False)
    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)


@pytest.mark.parametrize(
    "params",
    [
        pytest.param({"scope": "org"}, id="scope-not-in-enum"),
        pytest.param({"limit": 201}, id="limit-above-max"),
    ],
)
def test_invalid_query_is_rejected_by_validator(
    connectors_client: ConnectorsAuditClient, params: dict[str, Any]
) -> None:
    resp = connectors_client.get(PATH, params=params)
    assert resp.status_code == 400, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)
