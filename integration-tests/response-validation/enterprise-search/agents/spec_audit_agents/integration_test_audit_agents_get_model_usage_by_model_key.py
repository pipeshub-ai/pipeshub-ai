"""Strict OpenAPI audit of GET /api/v1/agents/model-usage/:model_key."""

from __future__ import annotations

import pytest
from agents_audit_support import (
    UNKNOWN_MODEL_KEY,
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

ROUTE = "/api/v1/agents/model-usage/:model_key"

NO_AGENTS = {"success": True, "agents": []}


def test_usage_lists_an_agent_using_the_model(
    agents_audit_client: AgentsAuditClient,
    make_agent: MakeAgent,
    reasoning_multimodal_llm_model: SeededAIModel,
) -> None:
    key = make_agent(models=[reasoning_model_entry(reasoning_multimodal_llm_model)])

    resp = agents_audit_client.model_usage(reasoning_multimodal_llm_model.model_key)

    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert key in [a["_key"] for a in resp.json()["agents"]]


def test_unknown_model_key_lists_no_agents(
    agents_audit_client: AgentsAuditClient,
) -> None:
    resp = agents_audit_client.model_usage(UNKNOWN_MODEL_KEY)

    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert resp.json() == NO_AGENTS


def test_member_can_read_model_usage(second_user: SecondUser) -> None:
    # No admin gate, and session tokens are not scope-restricted.
    resp = request_as(second_user, "GET", f"/model-usage/{UNKNOWN_MODEL_KEY}")

    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert resp.json() == NO_AGENTS


def test_model_usage_without_token_is_unauthorized(
    agents_audit_client: AgentsAuditClient,
) -> None:
    resp = agents_audit_client.model_usage(UNKNOWN_MODEL_KEY, auth=False)

    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


def test_token_without_agent_read_scope_is_forbidden(
    agents_audit_client: AgentsAuditClient, narrow_scope_headers: dict[str, str]
) -> None:
    resp = agents_audit_client.model_usage(UNKNOWN_MODEL_KEY, auth=False, headers=narrow_scope_headers)

    assert resp.status_code == 403, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


def test_model_usage_rejects_any_query_param(
    agents_audit_client: AgentsAuditClient,
) -> None:
    # OpenAPI cannot forbid undeclared query parameters; the description says they are refused.
    with outside_request_contract("the query object is strict but the spec cannot express that"):
        resp = agents_audit_client.model_usage(UNKNOWN_MODEL_KEY, params={"page": "1"})
        assert_strict_openapi_exchange(resp, ROUTE)

    assert resp.status_code == 400, resp.text[:500]
    error = error_of(resp)
    assert error["code"] == "VALIDATION_ERROR"
    assert [e["field"] for e in error["metadata"]["errors"]] == ["query"]


def test_model_usage_rejects_unsafe_model_key(
    agents_audit_client: AgentsAuditClient,
) -> None:
    resp = agents_audit_client.model_usage(UNSAFE_PATH_SEGMENT)

    assert resp.status_code == 400, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert error_of(resp)["code"] == "HTTP_BAD_REQUEST"
