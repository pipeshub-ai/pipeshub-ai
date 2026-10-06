"""Strict OpenAPI audit of GET /api/v1/agents/:agentKey/conversations."""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from agents_audit_support import (
    MISSING_PROJECT_ID,
    UNSAFE_PATH_SEGMENT,
    AgentsAuditClient,
    SeedAgentConversation,
    error_of,
)
from strict_openapi import assert_spec_forbids_request, assert_strict_openapi_exchange

pytestmark = pytest.mark.spec_audit

ROUTE = "/api/v1/agents/:agentKey/conversations"


def _ids(body: dict[str, Any]) -> list[str]:
    return [c["_id"] for c in body["conversations"]]


def test_lists_own_unarchived_conversations_of_the_agent(
    agents_audit_client: AgentsAuditClient,
    seed_agent_conversation: SeedAgentConversation,
) -> None:
    agent_key = f"spec-audit-list-{uuid.uuid4().hex[:8]}"
    listed = seed_agent_conversation(agent_key=agent_key)
    seed_agent_conversation(agent_key=agent_key, isArchived=True)
    seed_agent_conversation(agent_key=f"{agent_key}-other")

    resp = agents_audit_client.list_conversations(agent_key)

    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    body = resp.json()
    assert _ids(body) == [listed]
    assert body["pagination"]["totalCount"] == 1


@pytest.mark.parametrize(
    "params",
    [
        pytest.param(
            {"page": "1", "limit": "100", "sortBy": "title", "sortOrder": "asc"}, id="paging-and-sort"
        ),
        pytest.param({"search": "spec-audit"}, id="search"),
        pytest.param(
            {"startDate": "2020-01-01T00:00:00.000Z", "endDate": "2999-01-01T00:00:00.000Z"},
            id="date-range",
        ),
        pytest.param({"startDate": "2020-01-01", "endDate": "2999-01-01"}, id="date-only"),
        pytest.param({"status": "Complete", "isArchived": "false"}, id="shared-branch-filters"),
        pytest.param({"projectId": "unassigned"}, id="unassigned-project"),
        pytest.param({"projectId": MISSING_PROJECT_ID}, id="unknown-project"),
        pytest.param({"sortBy": "colour", "sortOrder": "sideways"}, id="unsupported-sort-ignored"),
    ],
)
def test_documented_query_is_accepted(
    agents_audit_client: AgentsAuditClient,
    seed_agent_conversation: SeedAgentConversation,
    params: dict[str, str],
) -> None:
    agent_key = f"spec-audit-list-{uuid.uuid4().hex[:8]}"
    seed_agent_conversation(agent_key=agent_key)

    resp = agents_audit_client.list_conversations(agent_key, **params)

    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


@pytest.mark.parametrize(
    "params",
    [
        pytest.param({"page": "0"}, id="page-zero"),
        pytest.param({"limit": "101"}, id="limit-over-max"),
        pytest.param({"limit": "abc"}, id="limit-not-numeric"),
        pytest.param({"startDate": "not-a-date"}, id="invalid-start-date"),
        pytest.param({"endDate": "not-a-date"}, id="invalid-end-date"),
        pytest.param({"search": "%s%s%s"}, id="search-format-specifier"),
        pytest.param({"search": "x" * 1001}, id="search-too-long"),
        pytest.param({"search": ["a", "b"]}, id="search-repeated"),
        pytest.param({"isArchived": "yes"}, id="is-archived-not-boolean"),
        pytest.param({"projectId": "not-a-project"}, id="project-id-malformed"),
    ],
)
def test_refused_query_is_a_validation_error(
    agents_audit_client: AgentsAuditClient, params: dict[str, Any]
) -> None:
    resp = agents_audit_client.list_conversations("spec-audit-agent", **params)

    assert resp.status_code == 400, resp.text[:500]
    assert error_of(resp)["code"] == "VALIDATION_ERROR", resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


def test_markup_in_search_is_refused_by_the_sanitizer(agents_audit_client: AgentsAuditClient) -> None:
    resp = agents_audit_client.list_conversations("spec-audit-agent", search="<b>x</b>")

    assert resp.status_code == 400, resp.text[:500]
    assert error_of(resp)["code"] == "HTTP_BAD_REQUEST", resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert_spec_forbids_request(resp, ROUTE)


def test_unsafe_agent_key_is_rejected(agents_audit_client: AgentsAuditClient) -> None:
    resp = agents_audit_client.list_conversations(UNSAFE_PATH_SEGMENT)

    assert resp.status_code == 400, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


def test_without_token_is_unauthorized(agents_audit_client: AgentsAuditClient) -> None:
    resp = agents_audit_client.list_conversations("spec-audit-agent", auth=False)

    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


def test_token_without_agent_read_scope_is_forbidden(
    agents_audit_client: AgentsAuditClient,
    narrow_scope_headers: dict[str, str],
) -> None:
    resp = agents_audit_client.list_conversations(
        "spec-audit-agent", auth=False, headers=narrow_scope_headers
    )

    assert resp.status_code == 403, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
