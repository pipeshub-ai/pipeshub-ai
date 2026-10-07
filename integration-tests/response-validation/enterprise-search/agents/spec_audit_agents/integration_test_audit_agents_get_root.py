"""Strict OpenAPI audit of GET /api/v1/agents."""

from __future__ import annotations

import pytest
from agents_audit_support import (
    AgentsAuditClient,
    MakeAgent,
    error_of,
    request_as,
    unique_agent_name,
)
from helper.second_user import SecondUser
from strict_openapi import (
    assert_spec_forbids_request,
    assert_strict_openapi_exchange,
    outside_request_contract,
)

pytestmark = pytest.mark.spec_audit

ROUTE = "/api/v1/agents"


def _keys(resp) -> list[str]:  # noqa: ANN001
    return [a["_key"] for a in resp.json()["agents"]]


def test_search_finds_the_agent_and_pages(
    agents_audit_client: AgentsAuditClient, make_agent: MakeAgent
) -> None:
    name = unique_agent_name("spec-audit-listed")
    key = make_agent(name=name)

    resp = agents_audit_client.list_agents(
        params={"search": name, "page": "1", "limit": "5", "sort_by": "name", "sort_order": "asc"}
    )

    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    body = resp.json()
    assert _keys(resp) == [key]
    assert "id" not in body["agents"][0]
    assert body["pagination"]["currentPage"] == 1
    assert body["pagination"]["limit"] == 5
    assert body["pagination"]["totalItems"] == 1


def test_defaults_apply_without_query(agents_audit_client: AgentsAuditClient) -> None:
    resp = agents_audit_client.list_agents()

    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    pagination = resp.json()["pagination"]
    assert (pagination["currentPage"], pagination["limit"]) == (1, 20)


def test_unsupported_sort_field_is_accepted(agents_audit_client: AgentsAuditClient) -> None:
    resp = agents_audit_client.list_agents(params={"sort_by": "spec-audit-no-such-field", "limit": "1"})

    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


def test_unknown_query_parameters_are_ignored(agents_audit_client: AgentsAuditClient) -> None:
    with outside_request_contract("parameters the route does not forward are ignored"):
        resp = agents_audit_client.list_agents(params={"isDeleted": "true", "limit": "1"})
        assert_strict_openapi_exchange(resp, ROUTE)

    assert resp.status_code == 200, resp.text[:500]
    assert all(a["isDeleted"] is False for a in resp.json()["agents"])


def test_member_sees_org_shared_agents_but_not_private_ones(
    second_user: SecondUser, make_agent: MakeAgent
) -> None:
    prefix = unique_agent_name("spec-audit-visibility")
    shared = make_agent(name=f"{prefix}-shared", shareWithOrg=True)
    make_agent(name=f"{prefix}-private")

    resp = request_as(second_user, "GET", "", params={"search": prefix})

    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert _keys(resp) == [shared]


@pytest.mark.parametrize(
    ("params", "field"),
    [
        pytest.param({"page": "0"}, "query.page", id="page-zero"),
        pytest.param({"page": "abc"}, "query.page", id="page-not-number"),
        pytest.param({"limit": "0"}, "query.limit", id="limit-zero"),
        pytest.param({"limit": "201"}, "query.limit", id="limit-over-200"),
        pytest.param({"search": "   "}, "query.search", id="blank-search"),
        pytest.param({"search": "x" * 1001}, "query.search", id="overlong-search"),
        pytest.param({"search": "%s%s"}, "query.search", id="format-specifier-search"),
        pytest.param({"sort_by": "  "}, "query.sort_by", id="blank-sort-by"),
        pytest.param({"sort_order": "up"}, "query.sort_order", id="unknown-sort-order"),
    ],
)
def test_invalid_query_is_refused_by_the_validator(
    agents_audit_client: AgentsAuditClient, params: dict[str, str], field: str
) -> None:
    resp = agents_audit_client.list_agents(params=params)

    assert resp.status_code == 400, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    error = error_of(resp)
    assert error["code"] == "VALIDATION_ERROR", resp.text[:500]
    assert [e["field"] for e in error["metadata"]["errors"]] == [field]


def test_markup_in_search_is_refused_by_the_sanitizer(agents_audit_client: AgentsAuditClient) -> None:
    resp = agents_audit_client.list_agents(params={"search": "<b>agent</b>"})

    assert resp.status_code == 400, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert error_of(resp)["code"] == "HTTP_BAD_REQUEST"
    assert_spec_forbids_request(resp, ROUTE)


def test_without_token_is_unauthorized(agents_audit_client: AgentsAuditClient) -> None:
    resp = agents_audit_client.list_agents(auth=False)

    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


def test_token_without_agent_read_scope_is_forbidden(
    agents_audit_client: AgentsAuditClient, narrow_scope_headers: dict[str, str]
) -> None:
    resp = agents_audit_client.list_agents(auth=False, headers=narrow_scope_headers)

    assert resp.status_code == 403, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
