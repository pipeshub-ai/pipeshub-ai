"""Strict OpenAPI audit of GET /api/v1/agents/model-usage/:model_key."""

from __future__ import annotations

import pytest
from agents_audit_support import (
    UNKNOWN_MODEL_KEY,
    UNSAFE_PATH_SEGMENT,
    AgentsAuditClient,
    request_as,
)
from helper.second_user import SecondUser
from strict_openapi import assert_strict_openapi_response

pytestmark = pytest.mark.spec_audit

ROUTE = "/api/v1/agents/model-usage/:model_key"

NO_AGENTS = {"success": True, "agents": []}


def test_unknown_model_key_lists_no_agents(
    agents_audit_client: AgentsAuditClient,
) -> None:
    resp = agents_audit_client.model_usage(UNKNOWN_MODEL_KEY)
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)
    assert resp.json() == NO_AGENTS


def test_member_can_read_model_usage(second_user: SecondUser) -> None:
    # No admin gate, and session tokens are not scope-restricted.
    resp = request_as(second_user, "GET", f"/model-usage/{UNKNOWN_MODEL_KEY}")
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)
    assert resp.json() == NO_AGENTS


def test_model_usage_without_token_is_unauthorized(
    agents_audit_client: AgentsAuditClient,
) -> None:
    resp = agents_audit_client.model_usage(UNKNOWN_MODEL_KEY, auth=False)
    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)


def test_model_usage_rejects_any_query_param(
    agents_audit_client: AgentsAuditClient,
) -> None:
    # The query schema is an empty strict object.
    resp = agents_audit_client.model_usage(UNKNOWN_MODEL_KEY, params={"page": "1"})
    assert resp.status_code == 400, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)


def test_model_usage_rejects_unsafe_model_key(
    agents_audit_client: AgentsAuditClient,
) -> None:
    resp = agents_audit_client.model_usage(UNSAFE_PATH_SEGMENT)
    assert resp.status_code == 400, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)
