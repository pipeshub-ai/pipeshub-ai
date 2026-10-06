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
from strict_openapi import assert_strict_openapi_exchange

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
