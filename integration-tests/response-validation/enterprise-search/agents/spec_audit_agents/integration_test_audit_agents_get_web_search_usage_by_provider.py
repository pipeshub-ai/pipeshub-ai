"""Strict OpenAPI audit of GET /api/v1/agents/web-search-usage/:provider."""

from __future__ import annotations

import pytest
from agents_audit_support import (
    UNKNOWN_WEB_SEARCH_PROVIDER,
    UNSAFE_PATH_SEGMENT,
    WEB_SEARCH_PROVIDERS,
    AgentsAuditClient,
    MakeAgent,
    error_of,
    request_as,
)
from helper.second_user import SecondUser
from strict_openapi import assert_strict_openapi_exchange, outside_request_contract

pytestmark = pytest.mark.spec_audit

ROUTE = "/api/v1/agents/web-search-usage/:provider"
PROVIDER = WEB_SEARCH_PROVIDERS[0]


def test_usage_lists_an_agent_using_the_provider(
    agents_audit_client: AgentsAuditClient, make_agent: MakeAgent
) -> None:
    key = make_agent(webSearch={"provider": PROVIDER})

    # Node and Python both lower-case the provider before matching it.
    resp = agents_audit_client.web_search_usage(PROVIDER.upper())

    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    body = resp.json()
    assert body["success"] is True
    assert key in [a["_key"] for a in body["agents"]]


def test_unknown_provider_lists_no_agents(agents_audit_client: AgentsAuditClient) -> None:
    resp = agents_audit_client.web_search_usage(UNKNOWN_WEB_SEARCH_PROVIDER)

    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert resp.json() == {"success": True, "agents": []}


def test_member_can_read_usage(second_user: SecondUser) -> None:
    resp = request_as(second_user, "GET", f"/web-search-usage/{UNKNOWN_WEB_SEARCH_PROVIDER}")

    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


def test_usage_rejects_any_query_param(agents_audit_client: AgentsAuditClient) -> None:
    # OpenAPI cannot forbid undeclared query parameters; the description says they are refused.
    with outside_request_contract("the query object is strict but the spec cannot express that"):
        resp = agents_audit_client.web_search_usage(PROVIDER, params={"limit": "1"})
        assert_strict_openapi_exchange(resp, ROUTE)

    assert resp.status_code == 400, resp.text[:500]
    error = error_of(resp)
    assert error["code"] == "VALIDATION_ERROR"
    assert [e["field"] for e in error["metadata"]["errors"]] == ["query"]


def test_usage_rejects_a_provider_unsafe_as_a_path_segment(
    agents_audit_client: AgentsAuditClient,
) -> None:
    resp = agents_audit_client.web_search_usage(UNSAFE_PATH_SEGMENT)

    assert resp.status_code == 400, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert error_of(resp)["code"] == "HTTP_BAD_REQUEST"


def test_usage_rejects_a_call_without_a_token(agents_audit_client: AgentsAuditClient) -> None:
    resp = agents_audit_client.web_search_usage(PROVIDER, auth=False)

    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


def test_token_without_agent_read_scope_is_forbidden(
    agents_audit_client: AgentsAuditClient, narrow_scope_headers: dict[str, str]
) -> None:
    resp = agents_audit_client.web_search_usage(PROVIDER, auth=False, headers=narrow_scope_headers)

    assert resp.status_code == 403, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
