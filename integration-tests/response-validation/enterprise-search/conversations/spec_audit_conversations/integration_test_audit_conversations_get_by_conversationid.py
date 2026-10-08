"""Strict OpenAPI audit of GET /api/v1/conversations/:conversationId."""

from __future__ import annotations

from typing import Any

import pytest
import requests
from bson import ObjectId
from conversations_audit_support import (
    BY_ID_ROUTE,
    INVALID_AUTH_HEADERS,
    MALFORMED_CONVERSATION_ID,
    MISSING_CONVERSATION_ID,
    ConversationsAuditClient,
    SeedConversation,
    validation_fields,
)
from helper.pipeshub_client import PipeshubClient
from helper.second_user import SecondUser
from strict_openapi import (
    assert_spec_forbids_request,
    assert_strict_openapi_exchange,
    outside_request_contract,
)

pytestmark = pytest.mark.spec_audit

ROUTE = BY_ID_ROUTE


def test_get_live_conversation_with_its_messages(
    conversations_audit_client: ConversationsAuditClient, live_conversation: requests.Response
) -> None:
    assert live_conversation.status_code == 201, live_conversation.text[:500]
    conversation_id = live_conversation.json()["conversation"]["_id"]
    resp = conversations_audit_client.get_conversation(conversation_id)
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    conversation = resp.json()["conversation"]
    types = {m["messageType"] for m in conversation["messages"]}
    assert {"user_query", "bot_response"} <= types, types


def test_get_own_seeded_conversation(
    conversations_audit_client: ConversationsAuditClient, seed_conversation: SeedConversation
) -> None:
    conversation_id = seed_conversation()
    resp = conversations_audit_client.get_conversation(conversation_id)
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert resp.json()["conversation"]["messages"] == []


@pytest.mark.parametrize(
    "params",
    [
        pytest.param({"page": 1, "limit": 100}, id="page-and-limit"),
        pytest.param({"sortBy": "createdAt", "sortOrder": "asc"}, id="sort"),
        pytest.param({"search": "pong"}, id="search"),
        pytest.param({"messageType": "bot_response"}, id="message-type"),
        pytest.param({"startDate": "2020-01-01T00:00:00Z", "endDate": "2099-01-01T00:00:00Z"}, id="date-range"),
    ],
)
def test_get_accepts_documented_message_filters(
    conversations_audit_client: ConversationsAuditClient,
    live_conversation: requests.Response,
    params: dict[str, Any],
) -> None:
    conversation_id = live_conversation.json()["conversation"]["_id"]
    resp = conversations_audit_client.get_conversation(conversation_id, **params)
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


@pytest.mark.parametrize(
    "params",
    [
        pytest.param({"messageType": "tool_call"}, id="message-type-tool-call"),
        pytest.param({"shared": "false"}, id="shared-false"),
        pytest.param({"shared": "0"}, id="shared-zero"),
        pytest.param({"projectId": "unassigned"}, id="project-unassigned"),
        pytest.param({"startDate": "2020-01-01"}, id="start-date-without-time"),
    ],
)
def test_get_accepts_more_filters(
    conversations_audit_client: ConversationsAuditClient,
    seed_conversation: SeedConversation,
    params: dict[str, Any],
) -> None:
    conversation_id = seed_conversation()
    resp = conversations_audit_client.get_conversation(conversation_id, **params)
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


@pytest.mark.parametrize(
    "params",
    [
        pytest.param({"messageType": "bot_response"}, id="message-type"),
        pytest.param({"search": "pong"}, id="search-matching-one-message"),
    ],
)
def test_get_message_filters_do_not_narrow_the_messages(
    conversations_audit_client: ConversationsAuditClient,
    live_conversation: requests.Response,
    params: dict[str, Any],
) -> None:
    conversation_id = live_conversation.json()["conversation"]["_id"]
    resp = conversations_audit_client.get_conversation(conversation_id, **params)
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    types = {m["messageType"] for m in resp.json()["conversation"]["messages"]}
    assert {"user_query", "bot_response"} <= types, types


def test_get_rejects_markup_in_search(
    conversations_audit_client: ConversationsAuditClient, seed_conversation: SeedConversation
) -> None:
    # The spec cannot express the controller's markup check, so only the response is compared.
    resp = conversations_audit_client.get_conversation(seed_conversation(), search="<b>x</b>")
    assert resp.status_code == 400, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert resp.json()["error"]["code"] == "HTTP_BAD_REQUEST", resp.text[:500]


def test_get_default_message_page_size_is_20(
    conversations_audit_client: ConversationsAuditClient, seed_conversation: SeedConversation
) -> None:
    resp = conversations_audit_client.get_conversation(seed_conversation())
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert resp.json()["conversation"]["pagination"]["limit"] == 20, resp.text[:800]


@pytest.mark.parametrize(
    ("params", "seed_fields"),
    [
        pytest.param({"search": "spec-audit-no-such-text-zq"}, {}, id="search-matches-nothing"),
        pytest.param({"startDate": "2099-01-01T00:00:00Z"}, {}, id="created-before-start-date"),
        pytest.param({"endDate": "2000-01-01T00:00:00Z"}, {}, id="created-after-end-date"),
        pytest.param({"shared": "true"}, {}, id="not-shared"),
        pytest.param({"projectId": MISSING_CONVERSATION_ID}, {}, id="other-project"),
    ],
)
def test_get_query_filters_apply_to_the_conversation_itself(
    conversations_audit_client: ConversationsAuditClient,
    seed_conversation: SeedConversation,
    params: dict[str, Any],
    seed_fields: dict[str, Any],
) -> None:
    # The list filters are reused for the single lookup: a conversation they exclude is a 404.
    conversation_id = seed_conversation(**seed_fields)
    resp = conversations_audit_client.get_conversation(conversation_id, **params)
    assert resp.status_code == 404, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


@pytest.mark.parametrize(
    "params",
    [
        pytest.param({"messageType": "bogus"}, id="unknown-message-type"),
        pytest.param({"sortBy": "title"}, id="unknown-sort-field"),
        pytest.param({"startDate": "yesterday"}, id="start-date-not-a-date"),
        pytest.param({"endDate": "never"}, id="end-date-not-a-date"),
        pytest.param({"shared": "yes"}, id="shared-not-a-boolean"),
        pytest.param({"search": "a" * 1001}, id="search-over-1000-chars"),
        pytest.param({"page": 0}, id="page-zero"),
        pytest.param({"page": "first"}, id="page-not-a-number"),
        pytest.param({"limit": 0}, id="limit-zero"),
        pytest.param({"limit": 101}, id="limit-over-100"),
    ],
)
def test_get_rejects_invalid_query_in_the_controller(
    conversations_audit_client: ConversationsAuditClient,
    seed_conversation: SeedConversation,
    params: dict[str, Any],
) -> None:
    resp = conversations_audit_client.get_conversation(seed_conversation(), **params)
    assert resp.status_code == 400, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert resp.json()["error"]["code"] == "HTTP_BAD_REQUEST", resp.text[:500]
    assert_spec_forbids_request(resp, ROUTE)


@pytest.mark.parametrize(
    ("reason", "params"),
    [
        pytest.param("an unknown sortOrder sorts descending", {"sortOrder": "sideways"}, id="unknown-sort-order"),
        pytest.param("shared is read case-insensitively", {"shared": "FALSE"}, id="shared-upper-case"),
        pytest.param("query parameters the controller does not read are ignored", {"specAuditExtra": "1"}, id="unknown-parameter"),
    ],
)
def test_get_tolerates_query_the_spec_does_not_allow(
    conversations_audit_client: ConversationsAuditClient,
    seed_conversation: SeedConversation,
    reason: str,
    params: dict[str, Any],
) -> None:
    with outside_request_contract(reason):
        resp = conversations_audit_client.get_conversation(seed_conversation(), **params)
        assert resp.status_code == 200, resp.text[:500]
        assert_strict_openapi_exchange(resp, ROUTE)


@pytest.mark.parametrize(
    ("params", "page", "limit"),
    [
        pytest.param({"page": "1.5"}, 1, 20, id="fractional-page"),
        pytest.param({"page": "2.9", "limit": "5.5"}, 2, 5, id="fractional-page-and-limit"),
    ],
)
def test_get_truncates_fractional_page_and_limit(
    conversations_audit_client: ConversationsAuditClient,
    seed_conversation: SeedConversation,
    params: dict[str, Any],
    page: int,
    limit: int,
) -> None:
    with outside_request_contract("page and limit are read with parseInt, so a fraction is truncated"):
        resp = conversations_audit_client.get_conversation(seed_conversation(), **params)
        assert resp.status_code == 200, resp.text[:500]
        assert_strict_openapi_exchange(resp, ROUTE)
    pagination = resp.json()["conversation"]["pagination"]
    assert (pagination["page"], pagination["limit"]) == (page, limit), pagination


def test_member_reads_a_conversation_shared_with_them(
    seed_conversation: SeedConversation, second_user: SecondUser
) -> None:
    conversation_id = seed_conversation(
        isShared=True,
        sharedWith=[{"userId": ObjectId(second_user.user_id), "accessLevel": "read", "_id": ObjectId()}],
    )
    resp = requests.get(
        f"{second_user.base_url}/api/v1/conversations/{conversation_id}",
        headers={"Authorization": f"Bearer {second_user.token}"},
        timeout=second_user.timeout,
    )
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


@pytest.mark.parametrize(
    ("owner", "fields"),
    [
        pytest.param("missing", {}, id="no-such-conversation"),
        pytest.param("member", {}, id="another-users-private-conversation"),
        pytest.param("admin", {"isDeleted": True}, id="deleted-conversation"),
        pytest.param("admin", {"sessionType": "agent", "agentKey": "spec-audit-agent"}, id="agent-conversation"),
    ],
)
def test_get_unreachable_conversation_is_not_found(
    conversations_audit_client: ConversationsAuditClient,
    seed_conversation: SeedConversation,
    second_user: SecondUser,
    owner: str,
    fields: dict[str, Any],
) -> None:
    if owner == "missing":
        conversation_id = MISSING_CONVERSATION_ID
    else:
        conversation_id = seed_conversation(
            owner=second_user.user_id if owner == "member" else None, **fields
        )
    resp = conversations_audit_client.get_conversation(conversation_id)
    assert resp.status_code == 404, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


def test_get_malformed_conversation_id_is_rejected(
    conversations_audit_client: ConversationsAuditClient,
) -> None:
    resp = conversations_audit_client.get_conversation(MALFORMED_CONVERSATION_ID)
    assert resp.status_code == 400, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert validation_fields(resp) == ["params.conversationId"]


@pytest.mark.parametrize(
    "headers",
    [pytest.param(None, id="no_token"), pytest.param(INVALID_AUTH_HEADERS, id="invalid_token")],
)
def test_get_without_valid_token_is_unauthorized(
    conversations_audit_client: ConversationsAuditClient, headers: dict[str, str] | None
) -> None:
    kwargs: dict[str, Any] = {"headers": headers} if headers else {}
    resp = conversations_audit_client.get(f"/{MISSING_CONVERSATION_ID}", auth=False, **kwargs)
    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


def test_get_with_token_lacking_read_scope_is_forbidden(
    pipeshub_client: PipeshubClient, narrow_scope_headers: dict[str, str]
) -> None:
    resp = requests.get(
        f"{pipeshub_client.base_url}/api/v1/conversations/{MISSING_CONVERSATION_ID}",
        headers=narrow_scope_headers,
        timeout=pipeshub_client.timeout_seconds,
    )
    assert resp.status_code == 403, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert resp.json()["error"]["code"] == "HTTP_FORBIDDEN", resp.text[:500]
