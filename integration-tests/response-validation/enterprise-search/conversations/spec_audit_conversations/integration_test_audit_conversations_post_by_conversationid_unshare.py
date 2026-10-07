"""Strict OpenAPI audit of POST /api/v1/conversations/:conversationId/unshare."""

from __future__ import annotations

from typing import Any

import pytest
import requests
from bson import ObjectId
from conversations_audit_support import (
    INVALID_AUTH_HEADERS,
    MALFORMED_CONVERSATION_ID,
    MISSING_CONVERSATION_ID,
    UNSHARE_ROUTE,
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

ROUTE = UNSHARE_ROUTE
OTHER_USER_ID = "0123456789abcdef0123abcd"


def _stored(collection: Collection, conversation_id: str) -> dict[str, Any]:
    stored = collection.find_one({"_id": ObjectId(conversation_id)})
    assert stored is not None, "seeded conversation disappeared"
    return stored


def test_removing_the_last_user_makes_the_conversation_private(
    conversations_audit_client: ConversationsAuditClient,
    seed_conversation: SeedConversation,
    second_user: SecondUser,
    chat_sessions_collection: Collection,
) -> None:
    conversation_id = seed_conversation(isShared=True, sharedWith=[share_entry(second_user.user_id)])

    resp = conversations_audit_client.unshare_conversation(conversation_id, userIds=[second_user.user_id])

    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    body = resp.json()
    assert (body["id"], body["isShared"], body["sharedWith"]) == (conversation_id, False, [])
    assert body["unsharedUsers"] == [second_user.user_id]
    stored = _stored(chat_sessions_collection, conversation_id)
    assert (stored["isShared"], stored["sharedWith"]) == (False, [])
    assert request_as(second_user, "GET", f"/{conversation_id}").status_code == 404


def test_other_users_keep_their_access(
    conversations_audit_client: ConversationsAuditClient,
    seed_conversation: SeedConversation,
    second_user: SecondUser,
) -> None:
    conversation_id = seed_conversation(
        isShared=True, sharedWith=[share_entry(second_user.user_id), share_entry(OTHER_USER_ID, "write")]
    )

    resp = conversations_audit_client.unshare_conversation(conversation_id, userIds=[second_user.user_id])

    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    body = resp.json()
    assert body["isShared"] is True
    assert [(s["userId"], s["accessLevel"]) for s in body["sharedWith"]] == [(OTHER_USER_ID, "write")]


def test_a_user_who_was_never_listed_is_echoed_without_error(
    conversations_audit_client: ConversationsAuditClient, seed_conversation: SeedConversation
) -> None:
    # No user lookup on this route: any well-formed id is accepted and echoed back.
    conversation_id = seed_conversation()

    resp = conversations_audit_client.unshare_conversation(conversation_id, userIds=[OTHER_USER_ID])

    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    body = resp.json()
    assert (body["isShared"], body["sharedWith"], body["unsharedUsers"]) == (False, [], [OTHER_USER_ID])


@pytest.mark.parametrize(
    ("body", "field"),
    [
        pytest.param({}, "body.userIds", id="user-ids-missing"),
        pytest.param({"userIds": []}, "body.userIds", id="user-ids-empty"),
        pytest.param({"userIds": "x"}, "body.userIds", id="user-ids-not-a-list"),
        pytest.param({"userIds": ["x"]}, "body.userIds.0", id="user-id-malformed"),
    ],
)
def test_invalid_body_is_rejected_by_the_validator(
    conversations_audit_client: ConversationsAuditClient, body: dict[str, Any], field: str
) -> None:
    resp = conversations_audit_client.post(f"/{MISSING_CONVERSATION_ID}/unshare", json=body)

    assert validation_fields(resp) == [field]
    assert_strict_openapi_exchange(resp, ROUTE)


def test_malformed_conversation_id_is_rejected(conversations_audit_client: ConversationsAuditClient) -> None:
    resp = conversations_audit_client.unshare_conversation(MALFORMED_CONVERSATION_ID, userIds=[OTHER_USER_ID])

    assert validation_fields(resp) == ["params.conversationId"]
    assert_strict_openapi_exchange(resp, ROUTE)


@pytest.mark.parametrize("case", ["missing", "shared-with-write-access", "agent-conversation"])
def test_a_conversation_the_caller_did_not_start_is_not_found(
    conversations_audit_client: ConversationsAuditClient,
    seed_conversation: SeedConversation,
    second_user: SecondUser,
    case: str,
) -> None:
    if case == "missing":
        resp = conversations_audit_client.unshare_conversation(MISSING_CONVERSATION_ID, userIds=[OTHER_USER_ID])
    elif case == "shared-with-write-access":
        conversation_id = seed_conversation(isShared=True, sharedWith=[share_entry(second_user.user_id, "write")])
        resp = request_as(second_user, "POST", f"/{conversation_id}/unshare", json={"userIds": [second_user.user_id]})
    else:
        conversation_id = seed_conversation(sessionType="agent", agentKey="spec-audit-agent")
        resp = conversations_audit_client.unshare_conversation(conversation_id, userIds=[OTHER_USER_ID])

    error = error_of(resp, 404)
    assert (error["code"], error["message"]) == ("HTTP_NOT_FOUND", "Conversation not found or unauthorized")
    assert_strict_openapi_exchange(resp, ROUTE)


@pytest.mark.parametrize(
    "headers",
    [pytest.param(None, id="no_token"), pytest.param(INVALID_AUTH_HEADERS, id="invalid_token")],
)
def test_unshare_without_valid_token_is_unauthorized(
    conversations_audit_client: ConversationsAuditClient, headers: dict[str, str] | None
) -> None:
    kwargs: dict[str, Any] = {"headers": headers} if headers else {}
    resp = conversations_audit_client.unshare_conversation(
        MISSING_CONVERSATION_ID, auth=False, userIds=[OTHER_USER_ID], **kwargs
    )

    assert error_of(resp, 401)["code"] == "HTTP_UNAUTHORIZED"
    assert_strict_openapi_exchange(resp, ROUTE)


def test_unshare_with_token_lacking_write_scope_is_forbidden(
    pipeshub_client: PipeshubClient, narrow_scope_headers: dict[str, str]
) -> None:
    resp = requests.post(
        f"{pipeshub_client.base_url}/api/v1/conversations/{MISSING_CONVERSATION_ID}/unshare",
        headers=narrow_scope_headers,
        json={"userIds": [OTHER_USER_ID]},
        timeout=pipeshub_client.timeout_seconds,
    )

    assert error_of(resp, 403)["code"] == "HTTP_FORBIDDEN"
    assert_strict_openapi_exchange(resp, ROUTE)
