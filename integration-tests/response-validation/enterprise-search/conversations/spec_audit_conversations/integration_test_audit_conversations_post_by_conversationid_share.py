"""Strict OpenAPI audit of POST /api/v1/conversations/:conversationId/share."""

from __future__ import annotations

from typing import Any

import pytest
import requests
from bson import ObjectId
from conversations_audit_support import (
    INVALID_AUTH_HEADERS,
    MALFORMED_CONVERSATION_ID,
    MISSING_CONVERSATION_ID,
    SHARE_ROUTE,
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
from strict_openapi import assert_strict_openapi_exchange, outside_request_contract

pytestmark = pytest.mark.spec_audit

ROUTE = SHARE_ROUTE
UNKNOWN_USER_ID = "0123456789abcdef0123abcd"


def _stored_shares(collection: Collection, conversation_id: str) -> list[tuple[str, str]]:
    stored = collection.find_one({"_id": ObjectId(conversation_id)})
    assert stored is not None, "seeded conversation disappeared"
    return [(str(s["userId"]), s["accessLevel"]) for s in stored["sharedWith"]]


def test_owner_shares_a_conversation_read_only(
    conversations_audit_client: ConversationsAuditClient,
    seed_conversation: SeedConversation,
    second_user: SecondUser,
    chat_sessions_collection: Collection,
) -> None:
    conversation_id = seed_conversation()

    resp = conversations_audit_client.share_conversation(conversation_id, userIds=[second_user.user_id])

    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    body = resp.json()
    assert (body["id"], body["isShared"]) == (conversation_id, True)
    assert [(s["userId"], s["accessLevel"]) for s in body["sharedWith"]] == [(second_user.user_id, "read")]
    assert "shareLink" not in body
    assert _stored_shares(chat_sessions_collection, conversation_id) == [(second_user.user_id, "read")]
    seen = request_as(second_user, "GET", f"/{conversation_id}")
    assert seen.status_code == 200, seen.text[:500]


@pytest.mark.parametrize("access_level", ["write", "admin"])
def test_access_level_is_dropped_and_every_share_is_read(
    conversations_audit_client: ConversationsAuditClient,
    seed_conversation: SeedConversation,
    second_user: SecondUser,
    chat_sessions_collection: Collection,
    access_level: str,
) -> None:
    # A write share that already exists is downgraded, because the dropped field defaults to read.
    conversation_id = seed_conversation(isShared=True, sharedWith=[share_entry(second_user.user_id, "write")])

    with outside_request_contract("accessLevel is stripped by the validator, so the API ignores it"):
        resp = conversations_audit_client.share_conversation(
            conversation_id, userIds=[second_user.user_id], accessLevel=access_level
        )
        assert resp.status_code == 200, resp.text[:500]
        assert_strict_openapi_exchange(resp, ROUTE)
    assert [(s["userId"], s["accessLevel"]) for s in resp.json()["sharedWith"]] == [(second_user.user_id, "read")]
    assert _stored_shares(chat_sessions_collection, conversation_id) == [(second_user.user_id, "read")]


def test_owner_can_list_themselves(
    conversations_audit_client: ConversationsAuditClient,
    seed_conversation: SeedConversation,
    admin_user_id: str,
) -> None:
    conversation_id = seed_conversation()

    resp = conversations_audit_client.share_conversation(conversation_id, userIds=[admin_user_id])

    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert [s["userId"] for s in resp.json()["sharedWith"]] == [admin_user_id]


def test_unknown_user_fails_the_whole_request(
    conversations_audit_client: ConversationsAuditClient,
    seed_conversation: SeedConversation,
    second_user: SecondUser,
    chat_sessions_collection: Collection,
) -> None:
    conversation_id = seed_conversation()

    resp = conversations_audit_client.share_conversation(
        conversation_id, userIds=[second_user.user_id, UNKNOWN_USER_ID]
    )

    error = error_of(resp, 400)
    assert (error["code"], error["message"]) == ("HTTP_BAD_REQUEST", f"User not found: {UNKNOWN_USER_ID}")
    assert_strict_openapi_exchange(resp, ROUTE)
    assert _stored_shares(chat_sessions_collection, conversation_id) == []


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
    resp = conversations_audit_client.post(f"/{MISSING_CONVERSATION_ID}/share", json=body)

    assert validation_fields(resp) == [field]
    assert_strict_openapi_exchange(resp, ROUTE)


def test_malformed_conversation_id_is_rejected(conversations_audit_client: ConversationsAuditClient) -> None:
    resp = conversations_audit_client.share_conversation(MALFORMED_CONVERSATION_ID, userIds=[UNKNOWN_USER_ID])

    assert validation_fields(resp) == ["params.conversationId"]
    assert_strict_openapi_exchange(resp, ROUTE)


@pytest.mark.parametrize(
    "case", ["missing", "another-users", "shared-with-write-access", "deleted", "agent-conversation"]
)
def test_a_conversation_the_caller_did_not_start_is_not_found(
    conversations_audit_client: ConversationsAuditClient,
    seed_conversation: SeedConversation,
    second_user: SecondUser,
    admin_user_id: str,
    case: str,
) -> None:
    if case == "missing":
        resp = conversations_audit_client.share_conversation(MISSING_CONVERSATION_ID, userIds=[second_user.user_id])
    elif case == "another-users":
        resp = request_as(second_user, "POST", f"/{seed_conversation()}/share", json={"userIds": [admin_user_id]})
    elif case == "shared-with-write-access":
        conversation_id = seed_conversation(isShared=True, sharedWith=[share_entry(second_user.user_id, "write")])
        resp = request_as(second_user, "POST", f"/{conversation_id}/share", json={"userIds": [admin_user_id]})
    else:
        fields: dict[str, Any] = (
            {"isDeleted": True} if case == "deleted" else {"sessionType": "agent", "agentKey": "spec-audit-agent"}
        )
        resp = conversations_audit_client.share_conversation(
            seed_conversation(**fields), userIds=[second_user.user_id]
        )

    error = error_of(resp, 404)
    assert (error["code"], error["message"]) == ("HTTP_NOT_FOUND", "Conversation not found or unauthorized")
    assert_strict_openapi_exchange(resp, ROUTE)


@pytest.mark.parametrize(
    "headers",
    [pytest.param(None, id="no_token"), pytest.param(INVALID_AUTH_HEADERS, id="invalid_token")],
)
def test_share_without_valid_token_is_unauthorized(
    conversations_audit_client: ConversationsAuditClient, headers: dict[str, str] | None
) -> None:
    kwargs: dict[str, Any] = {"headers": headers} if headers else {}
    resp = conversations_audit_client.share_conversation(
        MISSING_CONVERSATION_ID, auth=False, userIds=[UNKNOWN_USER_ID], **kwargs
    )

    assert error_of(resp, 401)["code"] == "HTTP_UNAUTHORIZED"
    assert_strict_openapi_exchange(resp, ROUTE)


def test_share_with_token_lacking_write_scope_is_forbidden(
    pipeshub_client: PipeshubClient, narrow_scope_headers: dict[str, str]
) -> None:
    resp = requests.post(
        f"{pipeshub_client.base_url}/api/v1/conversations/{MISSING_CONVERSATION_ID}/share",
        headers=narrow_scope_headers,
        json={"userIds": [UNKNOWN_USER_ID]},
        timeout=pipeshub_client.timeout_seconds,
    )

    assert error_of(resp, 403)["code"] == "HTTP_FORBIDDEN"
    assert_strict_openapi_exchange(resp, ROUTE)
