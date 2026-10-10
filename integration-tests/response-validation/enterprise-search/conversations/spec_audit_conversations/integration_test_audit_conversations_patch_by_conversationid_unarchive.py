"""Strict OpenAPI audit of PATCH /api/v1/conversations/:conversationId/unarchive."""

from __future__ import annotations

from typing import Any

import pytest
import requests
from bson import ObjectId
from conversations_audit_support import (
    INVALID_AUTH_HEADERS,
    MALFORMED_CONVERSATION_ID,
    MISSING_CONVERSATION_ID,
    UNARCHIVE_ROUTE,
    ConversationsAuditClient,
    SeedConversation,
    error_of,
    request_as,
    share_entry,
    validation_fields,
)
from helper.pipeshub_client import PipeshubClient
from helper.second_user import SecondUser
from pymongo.collection import Collection
from strict_openapi import assert_strict_openapi_exchange

pytestmark = pytest.mark.spec_audit

ROUTE = UNARCHIVE_ROUTE


def _stored(collection: Collection, conversation_id: str) -> dict[str, Any]:
    stored = collection.find_one({"_id": ObjectId(conversation_id)})
    assert stored is not None, "seeded conversation disappeared"
    return stored


def _archived(admin_user_id: str, **fields: Any) -> dict[str, Any]:
    return {"isArchived": True, "archivedBy": ObjectId(admin_user_id), **fields}


def test_owner_unarchives_a_conversation(
    conversations_audit_client: ConversationsAuditClient,
    seed_conversation: SeedConversation,
    chat_sessions_collection: Collection,
    admin_user_id: str,
) -> None:
    conversation_id = seed_conversation(**_archived(admin_user_id))

    resp = conversations_audit_client.unarchive_conversation(conversation_id)

    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    body = resp.json()
    assert (body["id"], body["status"], body["unarchivedBy"]) == (conversation_id, "unarchived", admin_user_id)
    stored = _stored(chat_sessions_collection, conversation_id)
    assert (stored["isArchived"], stored["archivedBy"]) == (False, None)
    assert body["unarchivedAt"] == stored["updatedAt"].isoformat(timespec="milliseconds") + "Z"


def test_unarchiving_an_active_conversation_is_a_bad_request(
    conversations_audit_client: ConversationsAuditClient, seed_conversation: SeedConversation
) -> None:
    resp = conversations_audit_client.unarchive_conversation(seed_conversation())

    error = error_of(resp, 400)
    assert (error["code"], error["message"]) == ("HTTP_BAD_REQUEST", "Conversation is not archived")
    assert_strict_openapi_exchange(resp, ROUTE)


@pytest.mark.parametrize(
    "case", ["missing", "another-users", "shared-with-write-access", "deleted", "agent-conversation"]
)
def test_a_conversation_the_caller_does_not_own_is_not_found(
    conversations_audit_client: ConversationsAuditClient,
    seed_conversation: SeedConversation,
    second_user: SecondUser,
    chat_sessions_collection: Collection,
    admin_user_id: str,
    case: str,
) -> None:
    conversation_id = MISSING_CONVERSATION_ID
    if case == "missing":
        resp = conversations_audit_client.unarchive_conversation(conversation_id)
    elif case == "another-users":
        conversation_id = seed_conversation(**_archived(admin_user_id))
        resp = request_as(second_user, "PATCH", f"/{conversation_id}/unarchive")
    elif case == "shared-with-write-access":
        # API bug: the lookup also filters on the caller's userId, so the write-share branch never matches.
        conversation_id = seed_conversation(
            **_archived(admin_user_id, isShared=True, sharedWith=[share_entry(second_user.user_id, "write")])
        )
        resp = request_as(second_user, "PATCH", f"/{conversation_id}/unarchive")
    else:
        fields: dict[str, Any] = (
            {"isDeleted": True} if case == "deleted" else {"sessionType": "agent", "agentKey": "spec-audit-agent"}
        )
        conversation_id = seed_conversation(**_archived(admin_user_id, **fields))
        resp = conversations_audit_client.unarchive_conversation(conversation_id)

    error = error_of(resp, 404)
    assert (error["code"], error["message"]) == ("HTTP_NOT_FOUND", "Conversation not found or no unarchive permission")
    assert_strict_openapi_exchange(resp, ROUTE)
    if case != "missing":
        assert _stored(chat_sessions_collection, conversation_id)["isArchived"] is True


def test_malformed_conversation_id_is_rejected(conversations_audit_client: ConversationsAuditClient) -> None:
    resp = conversations_audit_client.unarchive_conversation(MALFORMED_CONVERSATION_ID)

    assert validation_fields(resp) == ["params.conversationId"]
    assert_strict_openapi_exchange(resp, ROUTE)


@pytest.mark.parametrize(
    "headers",
    [pytest.param(None, id="no_token"), pytest.param(INVALID_AUTH_HEADERS, id="invalid_token")],
)
def test_unarchive_without_valid_token_is_unauthorized(
    conversations_audit_client: ConversationsAuditClient, headers: dict[str, str] | None
) -> None:
    kwargs: dict[str, Any] = {"headers": headers} if headers else {}
    resp = conversations_audit_client.unarchive_conversation(MISSING_CONVERSATION_ID, auth=False, **kwargs)

    assert error_of(resp, 401)["code"] == "HTTP_UNAUTHORIZED"
    assert_strict_openapi_exchange(resp, ROUTE)


def test_unarchive_with_token_lacking_write_scope_is_forbidden(
    pipeshub_client: PipeshubClient, narrow_scope_headers: dict[str, str]
) -> None:
    resp = requests.patch(
        f"{pipeshub_client.base_url}/api/v1/conversations/{MISSING_CONVERSATION_ID}/unarchive",
        headers=narrow_scope_headers,
        timeout=pipeshub_client.timeout_seconds,
    )

    assert error_of(resp, 403)["code"] == "HTTP_FORBIDDEN"
    assert_strict_openapi_exchange(resp, ROUTE)
