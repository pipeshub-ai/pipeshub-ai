"""Strict OpenAPI audit of GET /api/v1/agents/:agentKey/conversations/show/archives."""

from __future__ import annotations

import uuid

import pytest
from agents_audit_support import (
    UNSAFE_PATH_SEGMENT,
    AgentsAuditClient,
    SeedAgentConversation,
    error_of,
    request_as,
)
from bson import ObjectId
from helper.second_user import SecondUser
from strict_openapi import (
    assert_spec_forbids_request,
    assert_strict_openapi_exchange,
    outside_request_contract,
)

pytestmark = pytest.mark.spec_audit

ROUTE = "/api/v1/agents/:agentKey/conversations/show/archives"


def _agent_key() -> str:
    return f"spec-audit-archives-{uuid.uuid4().hex[:8]}"


def _ids(resp) -> list[str]:  # noqa: ANN001
    return [c["_id"] for c in resp.json()["conversations"]]


def test_lists_only_the_callers_archived_conversations_of_the_agent(
    agents_audit_client: AgentsAuditClient,
    seed_agent_conversation: SeedAgentConversation,
    second_user: SecondUser,
    admin_user_id: str,
) -> None:
    agent_key = _agent_key()
    archived = seed_agent_conversation(
        agent_key=agent_key, isArchived=True, archivedBy=ObjectId(admin_user_id), title="audit archived"
    )
    seed_agent_conversation(agent_key=agent_key)
    seed_agent_conversation(agent_key=_agent_key(), isArchived=True, archivedBy=ObjectId(admin_user_id))
    seed_agent_conversation(
        agent_key=agent_key,
        owner=second_user.user_id,
        isArchived=True,
        archivedBy=ObjectId(second_user.user_id),
    )

    resp = agents_audit_client.list_archived_conversations(
        agent_key, page=1, limit=10, sortBy="title", sortOrder="asc"
    )

    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    body = resp.json()
    assert _ids(resp) == [archived]
    assert body["conversations"][0]["archivedBy"] == admin_user_id
    assert body["pagination"]["totalCount"] == 1
    assert body["summary"]["totalArchived"] == 1


def test_member_lists_own_archives(
    second_user: SecondUser, seed_agent_conversation: SeedAgentConversation
) -> None:
    agent_key = _agent_key()
    archived = seed_agent_conversation(
        agent_key=agent_key,
        owner=second_user.user_id,
        isArchived=True,
        archivedBy=ObjectId(second_user.user_id),
    )

    resp = request_as(second_user, "GET", f"/{agent_key}/conversations/show/archives")

    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert _ids(resp) == [archived]


def test_filters_by_search_and_dates(
    agents_audit_client: AgentsAuditClient,
    seed_agent_conversation: SeedAgentConversation,
    admin_user_id: str,
) -> None:
    agent_key = _agent_key()
    marker = uuid.uuid4().hex[:10]
    archived = seed_agent_conversation(
        agent_key=agent_key, isArchived=True, archivedBy=ObjectId(admin_user_id), title=f"audit {marker}"
    )

    resp = agents_audit_client.list_archived_conversations(
        agent_key, search=marker, startDate="2000-01-01", endDate="2999-01-01T00:00:00Z"
    )

    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert _ids(resp) == [archived]


@pytest.mark.parametrize(
    "params",
    [
        pytest.param({"startDate": ""}, id="empty-start-date"),
        pytest.param({"endDate": ""}, id="empty-end-date"),
    ],
)
def test_empty_date_is_read_as_not_sent(
    agents_audit_client: AgentsAuditClient, params: dict[str, str]
) -> None:
    resp = agents_audit_client.list_archived_conversations(_agent_key(), **params)

    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


@pytest.mark.parametrize(
    "params",
    [
        pytest.param({"startDate": "May 1 2026"}, id="start-date-words"),
        pytest.param({"endDate": "2026/05/01"}, id="end-date-slashes"),
    ],
)
def test_non_iso_date_that_javascript_parses_is_accepted(
    agents_audit_client: AgentsAuditClient, params: dict[str, str]
) -> None:
    with outside_request_contract("the validator accepts any string JavaScript Date can parse"):
        resp = agents_audit_client.list_archived_conversations(_agent_key(), **params)
        assert_strict_openapi_exchange(resp, ROUTE)

    assert resp.status_code == 200, resp.text[:500]


def test_unsupported_sort_values_are_ignored(agents_audit_client: AgentsAuditClient) -> None:
    resp = agents_audit_client.list_archived_conversations(_agent_key(), sortBy="colour", sortOrder="sideways")

    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert resp.json()["conversations"] == []


@pytest.mark.parametrize(
    ("params", "page", "limit"),
    [
        pytest.param({"page": "0", "limit": "101"}, 1, 20, id="out-of-range-reset"),
        pytest.param({"page": "abc", "limit": "xyz"}, 1, 20, id="non-numeric-defaulted"),
    ],
)
def test_out_of_contract_paging_is_normalised_not_refused(
    agents_audit_client: AgentsAuditClient, params: dict[str, str], page: int, limit: int
) -> None:
    with outside_request_contract("the validator resets paging values the spec bounds"):
        resp = agents_audit_client.list_archived_conversations(_agent_key(), **params)
        assert_strict_openapi_exchange(resp, ROUTE)

    assert resp.status_code == 200, resp.text[:500]
    pagination = resp.json()["pagination"]
    assert (pagination["page"], pagination["limit"]) == (page, limit)


def test_unknown_query_parameter_is_refused(agents_audit_client: AgentsAuditClient) -> None:
    # OpenAPI cannot forbid undeclared query parameters; the description says they are refused.
    with outside_request_contract("the query object is strict but the spec cannot express that"):
        resp = agents_audit_client.list_archived_conversations(_agent_key(), shared="true")
        assert_strict_openapi_exchange(resp, ROUTE)

    assert resp.status_code == 400, resp.text[:500]
    error = error_of(resp)
    assert error["code"] == "VALIDATION_ERROR"
    assert [e["field"] for e in error["metadata"]["errors"]] == ["query"]


@pytest.mark.parametrize(
    ("params", "field"),
    [
        pytest.param({"startDate": "not-a-date"}, "query.startDate", id="bad-start-date"),
        pytest.param({"endDate": "not-a-date"}, "query.endDate", id="bad-end-date"),
        pytest.param({"search": "%s%s"}, "query.search", id="format-specifier-search"),
        pytest.param({"search": "x" * 1001}, "query.search", id="overlong-search"),
    ],
)
def test_invalid_query_is_refused_by_the_validator(
    agents_audit_client: AgentsAuditClient, params: dict[str, str], field: str
) -> None:
    resp = agents_audit_client.list_archived_conversations(_agent_key(), **params)

    assert resp.status_code == 400, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    error = error_of(resp)
    assert error["code"] == "VALIDATION_ERROR"
    assert [e["field"] for e in error["metadata"]["errors"]] == [field]


def test_markup_in_search_is_refused_by_the_sanitizer(agents_audit_client: AgentsAuditClient) -> None:
    resp = agents_audit_client.list_archived_conversations(_agent_key(), search="<i>x</i>")

    assert resp.status_code == 400, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert error_of(resp)["code"] == "HTTP_BAD_REQUEST"
    assert_spec_forbids_request(resp, ROUTE)


def test_unsafe_agent_key_is_rejected(agents_audit_client: AgentsAuditClient) -> None:
    resp = agents_audit_client.list_archived_conversations(UNSAFE_PATH_SEGMENT)

    assert resp.status_code == 400, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert error_of(resp)["code"] == "HTTP_BAD_REQUEST"


def test_without_token_is_unauthorized(agents_audit_client: AgentsAuditClient) -> None:
    resp = agents_audit_client.list_archived_conversations(_agent_key(), auth=False)

    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


def test_token_without_agent_read_scope_is_forbidden(
    agents_audit_client: AgentsAuditClient, narrow_scope_headers: dict[str, str]
) -> None:
    resp = agents_audit_client.list_archived_conversations(
        _agent_key(), auth=False, headers=narrow_scope_headers
    )

    assert resp.status_code == 403, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
