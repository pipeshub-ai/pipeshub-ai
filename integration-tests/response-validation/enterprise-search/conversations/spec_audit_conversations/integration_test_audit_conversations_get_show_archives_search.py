"""Strict OpenAPI audit of GET /api/v1/conversations/show/archives/search."""

from __future__ import annotations

from typing import Any

import pytest
import requests
from bson import ObjectId
from conversations_audit_support import (
    ARCHIVES_SEARCH_ROUTE,
    INVALID_AUTH_HEADERS,
    ConversationsAuditClient,
    SeedConversation,
    SeedTurn,
    error_of,
    share_entry,
    validation_fields,
)
from helper.pipeshub_client import PipeshubClient
from helper.second_user import SecondUser
from strict_openapi import assert_strict_openapi_exchange, outside_request_contract

pytestmark = pytest.mark.spec_audit

ROUTE = ARCHIVES_SEARCH_ROUTE


def _archived(user_id: str, **fields: Any) -> dict[str, Any]:
    return {"isArchived": True, "archivedBy": ObjectId(user_id), **fields}


def _marker() -> str:
    return f"spec-audit-archive-search-{ObjectId()}"


def _search(client: ConversationsAuditClient, **params: Any) -> requests.Response:
    return client.search_archived_conversations(**params)


def test_search_finds_assistant_and_agent_archives_by_title(
    conversations_audit_client: ConversationsAuditClient,
    seed_conversation: SeedConversation,
    admin_user_id: str,
) -> None:
    marker = _marker()
    assistant_id = seed_conversation(title=f"{marker} assistant", lastActivityAt=2, **_archived(admin_user_id))
    agent_id = seed_conversation(
        title=f"{marker} agent",
        lastActivityAt=1,
        **_archived(admin_user_id, sessionType="agent", agentKey="spec-audit-agent"),
    )

    resp = _search(conversations_audit_client, search=f"  {marker}  ")

    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    body = resp.json()
    assert [(c["_id"], c["source"]) for c in body["conversations"]] == [
        (assistant_id, "assistant"),
        (agent_id, "agent"),
    ]
    assert body["conversations"][1]["agentKey"] == "spec-audit-agent"
    assert "agentKey" not in body["conversations"][0]
    assert all("messages" not in c for c in body["conversations"])
    assert body["summary"] == {"totalMatches": 2, "assistantMatches": 1, "agentMatches": 1, "searchQuery": marker}
    assert body["pagination"] == {
        "page": 1, "limit": 20, "totalCount": 2, "totalPages": 1, "hasNextPage": False, "hasPrevPage": False,
    }


def test_search_matches_message_content_and_shared_archives(
    conversations_audit_client: ConversationsAuditClient,
    seed_turn: SeedTurn,
    second_user: SecondUser,
    admin_user_id: str,
) -> None:
    marker = _marker()
    conversation_id, _, _ = seed_turn(
        answer=f"the answer is {marker}",
        owner=second_user.user_id,
        **_archived(second_user.user_id, isShared=True, sharedWith=[share_entry(admin_user_id)]),
    )

    resp = _search(conversations_audit_client, search=marker)

    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    [item] = resp.json()["conversations"]
    assert (item["_id"], item["source"], item["isOwner"], item["accessLevel"]) == (
        conversation_id, "assistant", False, "read",
    )


def test_pages_past_the_end_are_empty(
    conversations_audit_client: ConversationsAuditClient,
    seed_conversation: SeedConversation,
    admin_user_id: str,
) -> None:
    marker = _marker()
    seed_conversation(title=marker, **_archived(admin_user_id))

    resp = _search(conversations_audit_client, search=marker, page=1000, limit=100)

    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    body = resp.json()
    assert body["conversations"] == []
    assert body["pagination"]["totalCount"] == 1 and body["pagination"]["hasPrevPage"] is True


@pytest.mark.parametrize(
    ("params", "page", "limit"),
    [
        pytest.param({"page": "1.5"}, 1, 20, id="fractional-page"),
        pytest.param({"page": "2.9", "limit": "5.5"}, 2, 5, id="fractional-page-and-limit"),
    ],
)
def test_search_truncates_fractional_page_and_limit(
    conversations_audit_client: ConversationsAuditClient,
    params: dict[str, Any],
    page: int,
    limit: int,
) -> None:
    resp = _search(conversations_audit_client, search=_marker(), **params)
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    pagination = resp.json()["pagination"]
    assert (pagination["page"], pagination["limit"]) == (page, limit), pagination


def test_unknown_query_parameters_are_ignored(
    conversations_audit_client: ConversationsAuditClient,
    seed_conversation: SeedConversation,
    admin_user_id: str,
) -> None:
    marker = _marker()
    conversation_id = seed_conversation(title=marker, **_archived(admin_user_id))

    with outside_request_contract("query parameters the validator strips are sent on purpose"):
        resp = _search(conversations_audit_client, search=marker, sortOrder="asc", specAuditExtra="1")
        assert resp.status_code == 200, resp.text[:500]
        assert_strict_openapi_exchange(resp, ROUTE)
    assert [c["_id"] for c in resp.json()["conversations"]] == [conversation_id]


@pytest.mark.parametrize(
    ("params", "named"),
    [
        pytest.param({}, "query.search", id="search-missing"),
        pytest.param({"search": ""}, "query.search", id="search-empty"),
        pytest.param({"search": "   "}, "query.search", id="search-only-spaces"),
        pytest.param({"search": "a" * 1001}, "query.search", id="search-over-1000-chars"),
        pytest.param({"search": "a", "page": 0}, "query.page", id="page-zero"),
        pytest.param({"search": "a", "page": 1001}, "query.page", id="page-over-1000"),
        pytest.param({"search": "a", "limit": 0}, "query.limit", id="limit-zero"),
        pytest.param({"search": "a", "limit": 101}, "query.limit", id="limit-over-100"),
        pytest.param({"search": "a", "limit": "many"}, "query.limit", id="limit-not-a-number"),
    ],
)
def test_search_rejects_invalid_query(
    conversations_audit_client: ConversationsAuditClient, params: dict[str, Any], named: str
) -> None:
    resp = _search(conversations_audit_client, **params)

    assert validation_fields(resp) == [named]
    assert_strict_openapi_exchange(resp, ROUTE)


@pytest.mark.parametrize(
    "search",
    [pytest.param("<script>alert(1)</script>", id="markup"), pytest.param("rate %s", id="format-specifier")],
)
def test_search_refuses_markup_and_format_specifiers_in_the_controller(
    conversations_audit_client: ConversationsAuditClient, search: str
) -> None:
    resp = _search(conversations_audit_client, search=search)

    assert error_of(resp, 400)["code"] == "HTTP_BAD_REQUEST"
    assert_strict_openapi_exchange(resp, ROUTE)


@pytest.mark.parametrize(
    "headers",
    [pytest.param(None, id="no_token"), pytest.param(INVALID_AUTH_HEADERS, id="invalid_token")],
)
def test_search_without_valid_token_is_unauthorized(
    conversations_audit_client: ConversationsAuditClient, headers: dict[str, str] | None
) -> None:
    kwargs: dict[str, Any] = {"headers": headers} if headers else {}
    resp = conversations_audit_client.search_archived_conversations(search="a", auth=False, **kwargs)

    assert error_of(resp, 401)["code"] == "HTTP_UNAUTHORIZED"
    assert_strict_openapi_exchange(resp, ROUTE)


def test_search_with_token_lacking_read_scope_is_forbidden(
    pipeshub_client: PipeshubClient, narrow_scope_headers: dict[str, str]
) -> None:
    resp = requests.get(
        f"{pipeshub_client.base_url}{ROUTE}",
        params={"search": "a"},
        headers=narrow_scope_headers,
        timeout=pipeshub_client.timeout_seconds,
    )

    assert error_of(resp, 403)["code"] == "HTTP_FORBIDDEN"
    assert_strict_openapi_exchange(resp, ROUTE)
