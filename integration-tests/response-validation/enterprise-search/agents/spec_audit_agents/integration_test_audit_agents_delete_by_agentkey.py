"""Strict OpenAPI audit of DELETE /api/v1/agents/:agentKey."""

from __future__ import annotations

import pytest
from agents_audit_support import (
    MISSING_AGENT_KEY,
    UNSAFE_PATH_SEGMENT,
    AgentsAuditClient,
    MakeAgent,
    error_of,
    request_as,
)
from helper.second_user import SecondUser
from strict_openapi import assert_strict_openapi_exchange, outside_request_contract

pytestmark = pytest.mark.spec_audit

ROUTE = "/api/v1/agents/:agentKey"


def test_owner_soft_deletes_the_agent(
    agents_audit_client: AgentsAuditClient, make_agent: MakeAgent
) -> None:
    key = make_agent()

    resp = agents_audit_client.delete_agent(key)

    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    body = resp.json()
    assert body["status"] == "success"
    assert body["deleted"]["agents"] == 1
    assert agents_audit_client.get_agent(key).status_code == 404

    again = agents_audit_client.delete_agent(key)
    assert again.status_code == 404, again.text[:500]
    assert_strict_openapi_exchange(again, ROUTE)


def test_query_and_body_are_ignored(
    agents_audit_client: AgentsAuditClient, make_agent: MakeAgent
) -> None:
    key = make_agent()

    with outside_request_contract("the route reads neither a query nor a body"):
        resp = agents_audit_client.delete_agent(key, params={"force": "true"}, json={"cascade": True})
        assert_strict_openapi_exchange(resp, ROUTE)

    assert resp.status_code == 200, resp.text[:500]
    assert resp.json()["deleted"]["agents"] == 1


def test_member_cannot_delete_an_org_shared_agent(
    agents_audit_client: AgentsAuditClient, second_user: SecondUser, make_agent: MakeAgent
) -> None:
    key = make_agent(shareWithOrg=True)

    resp = request_as(second_user, "DELETE", f"/{key}")

    assert resp.status_code == 403, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert error_of(resp)["code"] == "HTTP_FORBIDDEN"
    assert agents_audit_client.get_agent(key).status_code == 200


def test_member_gets_not_found_for_a_private_agent(
    second_user: SecondUser, make_agent: MakeAgent
) -> None:
    key = make_agent()

    resp = request_as(second_user, "DELETE", f"/{key}")

    assert resp.status_code == 404, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


def test_unknown_agent_is_not_found(agents_audit_client: AgentsAuditClient) -> None:
    resp = agents_audit_client.delete_agent(MISSING_AGENT_KEY)

    assert resp.status_code == 404, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


def test_unsafe_agent_key_is_rejected(agents_audit_client: AgentsAuditClient) -> None:
    resp = agents_audit_client.delete_agent(UNSAFE_PATH_SEGMENT)

    assert resp.status_code == 400, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert error_of(resp)["code"] == "HTTP_BAD_REQUEST"


def test_without_token_is_unauthorized(agents_audit_client: AgentsAuditClient) -> None:
    resp = agents_audit_client.delete_agent(MISSING_AGENT_KEY, auth=False)

    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


def test_token_without_agent_write_scope_is_forbidden(
    agents_audit_client: AgentsAuditClient, narrow_scope_headers: dict[str, str]
) -> None:
    resp = agents_audit_client.delete_agent(MISSING_AGENT_KEY, auth=False, headers=narrow_scope_headers)

    assert resp.status_code == 403, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
