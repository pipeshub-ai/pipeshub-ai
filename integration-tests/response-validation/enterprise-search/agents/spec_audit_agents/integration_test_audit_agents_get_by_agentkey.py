"""Strict OpenAPI audit of GET /api/v1/agents/:agentKey."""

from __future__ import annotations

import pytest
from agents_audit_support import (
    MISSING_AGENT_KEY,
    UNSAFE_PATH_SEGMENT,
    AgentsAuditClient,
    MakeAgent,
    error_of,
    reasoning_model_entry,
    request_as,
)
from ai_models_setup import SeededAIModel
from helper.second_user import SecondUser
from strict_openapi import assert_strict_openapi_exchange, outside_request_contract

pytestmark = pytest.mark.spec_audit

ROUTE = "/api/v1/agents/:agentKey"


def test_owner_reads_the_agent_with_enriched_models(
    agents_audit_client: AgentsAuditClient,
    make_agent: MakeAgent,
    reasoning_multimodal_llm_model: SeededAIModel,
) -> None:
    key = make_agent(models=[reasoning_model_entry(reasoning_multimodal_llm_model)])

    resp = agents_audit_client.get_agent(key)

    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    agent = resp.json()["agent"]
    assert agent["_key"] == key
    assert "id" not in agent
    assert [m["modelKey"] for m in agent["models"]] == [reasoning_multimodal_llm_model.model_key]
    assert agent["can_edit"] is True


def test_unknown_query_parameters_are_ignored(
    agents_audit_client: AgentsAuditClient, make_agent: MakeAgent
) -> None:
    key = make_agent()

    with outside_request_contract("the route reads no query parameter and ignores any it gets"):
        resp = agents_audit_client.get_agent(key, params={"include": "all", "page": "2"})
        assert_strict_openapi_exchange(resp, ROUTE)

    assert resp.status_code == 200, resp.text[:500]
    assert resp.json()["agent"]["_key"] == key


def test_member_reads_an_org_shared_agent(
    second_user: SecondUser, make_agent: MakeAgent
) -> None:
    key = make_agent(shareWithOrg=True)

    resp = request_as(second_user, "GET", f"/{key}")

    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    agent = resp.json()["agent"]
    assert agent["can_edit"] is False and agent["can_delete"] is False


def test_member_gets_not_found_for_a_private_agent(
    second_user: SecondUser, make_agent: MakeAgent
) -> None:
    key = make_agent()

    resp = request_as(second_user, "GET", f"/{key}")

    assert resp.status_code == 404, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


@pytest.mark.parametrize("deleted", [False, True], ids=["unknown-key", "deleted-agent"])
def test_missing_agent_is_not_found(
    agents_audit_client: AgentsAuditClient, make_agent: MakeAgent, deleted: bool
) -> None:
    key = MISSING_AGENT_KEY
    if deleted:
        key = make_agent()
        assert agents_audit_client.delete_agent(key).status_code == 200

    resp = agents_audit_client.get_agent(key)

    assert resp.status_code == 404, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert error_of(resp)["code"] == "HTTP_NOT_FOUND"


def test_unsafe_agent_key_is_rejected(agents_audit_client: AgentsAuditClient) -> None:
    resp = agents_audit_client.get_agent(UNSAFE_PATH_SEGMENT)

    assert resp.status_code == 400, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert error_of(resp)["code"] == "HTTP_BAD_REQUEST"


def test_without_token_is_unauthorized(agents_audit_client: AgentsAuditClient) -> None:
    resp = agents_audit_client.get_agent(MISSING_AGENT_KEY, auth=False)

    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


def test_token_without_agent_read_scope_is_forbidden(
    agents_audit_client: AgentsAuditClient, narrow_scope_headers: dict[str, str]
) -> None:
    resp = agents_audit_client.get_agent(MISSING_AGENT_KEY, auth=False, headers=narrow_scope_headers)

    assert resp.status_code == 403, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
