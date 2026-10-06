"""Strict OpenAPI audit of DELETE /api/v1/conversations/:conversationId."""

from __future__ import annotations

from typing import Any

import pytest
import requests
from conversations_audit_support import (
    BY_ID_ROUTE,
    COLLECTION,
    INVALID_AUTH_HEADERS,
    MALFORMED_CONVERSATION_ID,
    MISSING_CONVERSATION_ID,
    ConversationsAuditClient,
    SeedConversation,
    validation_fields,
)
from bson import ObjectId
from helper.pipeshub_client import PipeshubClient
from helper.second_user import SecondUser
from pymongo.collection import Collection
from strict_openapi import assert_strict_openapi_exchange

pytestmark = pytest.mark.spec_audit

ROUTE = BY_ID_ROUTE
assert COLLECTION == "chatSessions"


def test_delete_own_conversation_soft_deletes_it(
    conversations_audit_client: ConversationsAuditClient,
    seed_conversation: SeedConversation,
    chat_sessions_collection: Collection,
) -> None:
    conversation_id = seed_conversation()
    resp = conversations_audit_client.delete_conversation(conversation_id)
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    stored = chat_sessions_collection.find_one({"_id": ObjectId(conversation_id)})
    assert stored is not None and stored["isDeleted"] is True

    again = conversations_audit_client.delete_conversation(conversation_id)
    assert again.status_code == 404, again.text[:500]
    assert_strict_openapi_exchange(again, ROUTE)


@pytest.mark.parametrize(
    ("owner", "fields"),
    [
        pytest.param("missing", {}, id="no-such-conversation"),
        pytest.param("member", {}, id="another-users-conversation"),
        pytest.param("agent", {"sessionType": "agent", "agentKey": "spec-audit-agent"}, id="agent-conversation"),
    ],
)
def test_delete_unreachable_conversation_is_not_found(
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
    resp = conversations_audit_client.delete_conversation(conversation_id)
    assert resp.status_code == 404, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


def test_member_cannot_delete_a_conversation_shared_with_them(
    seed_conversation: SeedConversation, second_user: SecondUser
) -> None:
    conversation_id = seed_conversation(
        isShared=True,
        sharedWith=[{"userId": ObjectId(second_user.user_id), "accessLevel": "write", "_id": ObjectId()}],
    )
    resp = requests.delete(
        f"{second_user.base_url}/api/v1/conversations/{conversation_id}",
        headers={"Authorization": f"Bearer {second_user.token}"},
        timeout=second_user.timeout,
    )
    # The lookup also requires the caller to own the row, so a write share is not enough.
    assert resp.status_code == 404, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


def test_delete_malformed_conversation_id_is_rejected(
    conversations_audit_client: ConversationsAuditClient,
) -> None:
    resp = conversations_audit_client.delete_conversation(MALFORMED_CONVERSATION_ID)
    assert resp.status_code == 400, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert validation_fields(resp) == ["params.conversationId"]


@pytest.mark.parametrize(
    "headers",
    [pytest.param(None, id="no_token"), pytest.param(INVALID_AUTH_HEADERS, id="invalid_token")],
)
def test_delete_without_valid_token_is_unauthorized(
    conversations_audit_client: ConversationsAuditClient, headers: dict[str, str] | None
) -> None:
    kwargs: dict[str, Any] = {"headers": headers} if headers else {}
    resp = conversations_audit_client.delete_conversation(MISSING_CONVERSATION_ID, auth=False, **kwargs)
    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


def test_delete_with_token_lacking_write_scope_is_forbidden(
    pipeshub_client: PipeshubClient, narrow_scope_headers: dict[str, str]
) -> None:
    resp = requests.delete(
        f"{pipeshub_client.base_url}/api/v1/conversations/{MISSING_CONVERSATION_ID}",
        headers=narrow_scope_headers,
        timeout=pipeshub_client.timeout_seconds,
    )
    assert resp.status_code == 403, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert resp.json()["error"]["code"] == "HTTP_FORBIDDEN", resp.text[:500]
