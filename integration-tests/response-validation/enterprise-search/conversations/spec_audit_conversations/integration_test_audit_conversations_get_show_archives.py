"""Strict OpenAPI audit of GET /api/v1/conversations/show/archives."""

from __future__ import annotations

from typing import Any

import pytest
import requests
from bson import ObjectId
from conversations_audit_support import (
    ARCHIVES_ROUTE,
    INVALID_AUTH_HEADERS,
    MALFORMED_CONVERSATION_ID,
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

ROUTE = ARCHIVES_ROUTE


def _archived(user_id: str, **fields: Any) -> dict[str, Any]:
    return {"isArchived": True, "archivedBy": ObjectId(user_id), **fields}


def _list(client: ConversationsAuditClient, **params: Any) -> requests.Response:
    return client.list_archived_conversations(**params)


def _ids(resp: requests.Response) -> list[str]:
    return [c["_id"] for c in resp.json()["conversations"]]


def test_list_archived_conversations_with_their_messages(
    conversations_audit_client: ConversationsAuditClient,
    seed_turn: SeedTurn,
    admin_user_id: str,
) -> None:
    conversation_id, user_query_id, bot_id = seed_turn(**_archived(admin_user_id))

    resp = _list(conversations_audit_client, conversationId=conversation_id)

    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    body = resp.json()
    [item] = body["conversations"]
    assert [m["_id"] for m in item["messages"]] == [user_query_id, bot_id]
    assert (item["isOwner"], item["accessLevel"], item["archivedBy"]) == (True, "read", admin_user_id)
    assert item["archivedAt"] == item["updatedAt"]
    assert body["pagination"] == {
        "page": 1, "limit": 20, "totalCount": 1, "totalPages": 1, "hasNextPage": False, "hasPrevPage": False,
    }
    assert body["summary"] == {
        "totalArchived": 1, "oldestArchive": item["archivedAt"], "newestArchive": item["archivedAt"],
    }


def test_shared_archived_conversations_are_listed_for_the_recipient(
    conversations_audit_client: ConversationsAuditClient,
    seed_conversation: SeedConversation,
    second_user: SecondUser,
    admin_user_id: str,
) -> None:
    conversation_id = seed_conversation(
        owner=second_user.user_id,
        **_archived(second_user.user_id, isShared=True, sharedWith=[share_entry(admin_user_id, "write")]),
    )

    resp = _list(conversations_audit_client, conversationId=conversation_id)

    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    [item] = resp.json()["conversations"]
    assert (item["isOwner"], item["accessLevel"]) == (False, "write")
    assert [s["userId"] for s in item["sharedWith"]] == [admin_user_id]


@pytest.mark.parametrize(
    ("fields", "archived_by"),
    [
        pytest.param({"isArchived": True}, False, id="archived-without-archived-by"),
        pytest.param({}, False, id="active"),
        pytest.param({"isDeleted": True, "isArchived": True}, True, id="deleted"),
        pytest.param({"sessionType": "agent", "agentKey": "spec-audit-agent", "isArchived": True}, True, id="agent"),
    ],
)
def test_conversations_that_are_not_listed(
    conversations_audit_client: ConversationsAuditClient,
    seed_conversation: SeedConversation,
    admin_user_id: str,
    fields: dict[str, Any],
    archived_by: bool,
) -> None:
    if archived_by:
        fields = {**fields, "archivedBy": ObjectId(admin_user_id)}
    conversation_id = seed_conversation(**fields)

    resp = _list(conversations_audit_client, conversationId=conversation_id)

    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert _ids(resp) == []


def test_search_matches_message_content(
    conversations_audit_client: ConversationsAuditClient, seed_turn: SeedTurn, admin_user_id: str
) -> None:
    marker = f"spec-audit-archive-{ObjectId()}"
    conversation_id, _, _ = seed_turn(answer=f"the answer is {marker}", **_archived(admin_user_id))

    resp = _list(conversations_audit_client, search=marker)

    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert _ids(resp) == [conversation_id]
    assert resp.json()["filters"]["applied"]["values"]["search"] == marker


@pytest.mark.parametrize(
    "params",
    [
        pytest.param({"page": 1000, "limit": 100}, id="max-page-and-limit"),
        pytest.param({"sortBy": "createdAt", "sortOrder": "asc"}, id="sort-created-asc"),
        pytest.param({"sortBy": "title", "sortOrder": "desc"}, id="sort-title-desc"),
        pytest.param({"search": "spec-audit"}, id="search"),
        pytest.param({"startDate": "2020-01-01T00:00:00Z", "endDate": "2099-01-01T00:00:00.000+05:30"}, id="date-range"),
        *[pytest.param({"shared": value}, id=f"shared-{value}") for value in ("true", "false", "1", "0")],
    ],
)
def test_list_accepts_every_documented_filter(
    conversations_audit_client: ConversationsAuditClient,
    seed_conversation: SeedConversation,
    admin_user_id: str,
    params: dict[str, Any],
) -> None:
    seed_conversation(**_archived(admin_user_id))

    resp = _list(conversations_audit_client, **params)

    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert resp.json()["pagination"]["limit"] == params.get("limit", 20)


@pytest.mark.parametrize(
    ("params", "page", "limit"),
    [
        pytest.param({"page": "1.5"}, 1, 20, id="fractional-page"),
        pytest.param({"page": "2.9", "limit": "5.5"}, 2, 5, id="fractional-page-and-limit"),
    ],
)
def test_list_truncates_fractional_page_and_limit(
    conversations_audit_client: ConversationsAuditClient,
    params: dict[str, Any],
    page: int,
    limit: int,
) -> None:
    resp = _list(conversations_audit_client, **params)
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    pagination = resp.json()["pagination"]
    assert (pagination["page"], pagination["limit"]) == (page, limit), pagination


def test_date_range_excludes_conversations_created_outside_it(
    conversations_audit_client: ConversationsAuditClient,
    seed_conversation: SeedConversation,
    admin_user_id: str,
) -> None:
    conversation_id = seed_conversation(**_archived(admin_user_id))

    resp = _list(conversations_audit_client, conversationId=conversation_id, startDate="2099-01-01T00:00:00Z")

    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert _ids(resp) == []
    assert resp.json()["filters"]["applied"]["values"]["dateRange"] == {"start": "2099-01-01T00:00:00.000Z"}


@pytest.mark.parametrize(
    "params",
    [
        pytest.param({"projectId": "not-a-project"}, id="project-id"),
        pytest.param({"specAuditExtra": "1"}, id="unknown-parameter"),
    ],
)
def test_query_parameters_outside_the_validator_are_ignored(
    conversations_audit_client: ConversationsAuditClient,
    seed_conversation: SeedConversation,
    admin_user_id: str,
    params: dict[str, Any],
) -> None:
    conversation_id = seed_conversation(**_archived(admin_user_id))

    with outside_request_contract("query parameters the validator strips are sent on purpose"):
        resp = _list(conversations_audit_client, conversationId=conversation_id, **params)
        assert resp.status_code == 200, resp.text[:500]
        assert_strict_openapi_exchange(resp, ROUTE)
    assert _ids(resp) == [conversation_id]


@pytest.mark.parametrize(
    ("params", "named"),
    [
        pytest.param({"page": 0}, "query.page", id="page-zero"),
        pytest.param({"page": 1001}, "query.page", id="page-over-1000"),
        pytest.param({"page": "first"}, "query.page", id="page-not-a-number"),
        pytest.param({"limit": 0}, "query.limit", id="limit-zero"),
        pytest.param({"limit": 101}, "query.limit", id="limit-over-100"),
        pytest.param({"sortBy": "owner"}, "query.sortBy", id="unknown-sort-field"),
        pytest.param({"sortOrder": "up"}, "query.sortOrder", id="unknown-sort-order"),
        pytest.param({"search": "a" * 1001}, "query.search", id="search-over-1000-chars"),
        pytest.param({"shared": "yes"}, "query.shared", id="unknown-shared-value"),
        pytest.param({"startDate": "2026-01-01"}, "query.startDate", id="start-date-without-time"),
        pytest.param({"startDate": "2026-01-01T00:00:00"}, "query.startDate", id="start-date-without-zone"),
        pytest.param({"endDate": "yesterday"}, "query.endDate", id="end-date-not-iso"),
        pytest.param({"conversationId": MALFORMED_CONVERSATION_ID}, "query.conversationId", id="malformed-conversation-id"),
    ],
)
def test_list_rejects_invalid_query(
    conversations_audit_client: ConversationsAuditClient, params: dict[str, Any], named: str
) -> None:
    resp = _list(conversations_audit_client, **params)

    assert validation_fields(resp) == [named]
    assert_strict_openapi_exchange(resp, ROUTE)


def test_markup_in_search_is_refused_by_the_controller(conversations_audit_client: ConversationsAuditClient) -> None:
    resp = _list(conversations_audit_client, search="<script>alert(1)</script>")

    assert error_of(resp, 400)["code"] == "HTTP_BAD_REQUEST"
    assert_strict_openapi_exchange(resp, ROUTE)


@pytest.mark.parametrize(
    "headers",
    [pytest.param(None, id="no_token"), pytest.param(INVALID_AUTH_HEADERS, id="invalid_token")],
)
def test_list_without_valid_token_is_unauthorized(
    conversations_audit_client: ConversationsAuditClient, headers: dict[str, str] | None
) -> None:
    kwargs: dict[str, Any] = {"headers": headers} if headers else {}
    resp = conversations_audit_client.list_archived_conversations(auth=False, **kwargs)

    assert error_of(resp, 401)["code"] == "HTTP_UNAUTHORIZED"
    assert_strict_openapi_exchange(resp, ROUTE)


def test_list_with_token_lacking_read_scope_is_forbidden(
    pipeshub_client: PipeshubClient, narrow_scope_headers: dict[str, str]
) -> None:
    resp = requests.get(
        f"{pipeshub_client.base_url}{ROUTE}", headers=narrow_scope_headers, timeout=pipeshub_client.timeout_seconds
    )

    assert error_of(resp, 403)["code"] == "HTTP_FORBIDDEN"
    assert_strict_openapi_exchange(resp, ROUTE)
