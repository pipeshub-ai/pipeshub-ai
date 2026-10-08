"""Strict OpenAPI audit of PATCH /api/v1/conversations/:conversationId/title."""

from __future__ import annotations

from typing import Any

import pytest
import requests
from bson import ObjectId
from conversations_audit_support import (
    INVALID_AUTH_HEADERS,
    MALFORMED_CONVERSATION_ID,
    MISSING_CONVERSATION_ID,
    TITLE_ROUTE,
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

ROUTE = TITLE_ROUTE


def _stored_title(collection: Collection, conversation_id: str) -> str:
    stored = collection.find_one({"_id": ObjectId(conversation_id)})
    assert stored is not None, "seeded conversation disappeared"
    return str(stored["title"])


@pytest.mark.parametrize("title", ["Q4 review", "x" * 200], ids=["short", "200-chars"])
def test_owner_renames_a_shared_conversation(
    conversations_audit_client: ConversationsAuditClient,
    seed_conversation: SeedConversation,
    second_user: SecondUser,
    chat_sessions_collection: Collection,
    title: str,
) -> None:
    conversation_id = seed_conversation(isShared=True, sharedWith=[share_entry(second_user.user_id)])

    resp = conversations_audit_client.update_title(conversation_id, title=title)

    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    conversation = resp.json()["conversation"]
    assert (conversation["_id"], conversation["title"]) == (conversation_id, title)
    assert "messages" not in conversation
    assert [s["userId"] for s in conversation["sharedWith"]] == [second_user.user_id]
    assert _stored_title(chat_sessions_collection, conversation_id) == title


def test_unknown_body_fields_are_dropped(
    conversations_audit_client: ConversationsAuditClient,
    seed_conversation: SeedConversation,
    chat_sessions_collection: Collection,
) -> None:
    conversation_id = seed_conversation()

    with outside_request_contract("unknown body fields are stripped by the validator, not refused"):
        resp = conversations_audit_client.update_title(conversation_id, title="renamed", isArchived=True)
        assert resp.status_code == 200, resp.text[:500]
        assert_strict_openapi_exchange(resp, ROUTE)
    assert resp.json()["conversation"]["isArchived"] is False
    stored = chat_sessions_collection.find_one({"_id": ObjectId(conversation_id)})
    assert stored is not None and stored["isArchived"] is False


@pytest.mark.parametrize(
    "body",
    [
        pytest.param({}, id="title-missing"),
        pytest.param({"title": ""}, id="title-empty"),
        pytest.param({"title": "   "}, id="title-only-spaces"),
        pytest.param({"title": "x" * 201}, id="title-over-200-chars"),
        pytest.param({"title": 7}, id="title-not-a-string"),
    ],
)
def test_invalid_body_is_rejected_by_the_validator(
    conversations_audit_client: ConversationsAuditClient, body: dict[str, Any]
) -> None:
    resp = conversations_audit_client.update_title(MISSING_CONVERSATION_ID, json=body)

    assert validation_fields(resp) == ["body.title"]
    assert_strict_openapi_exchange(resp, ROUTE)


def test_malformed_conversation_id_is_rejected(conversations_audit_client: ConversationsAuditClient) -> None:
    resp = conversations_audit_client.update_title(MALFORMED_CONVERSATION_ID, title="x")

    assert validation_fields(resp) == ["params.conversationId"]
    assert_strict_openapi_exchange(resp, ROUTE)


@pytest.mark.parametrize("case", ["missing", "deleted", "agent-conversation", "shared-with-write-access"])
def test_a_conversation_the_caller_does_not_own_is_not_found(
    conversations_audit_client: ConversationsAuditClient,
    seed_conversation: SeedConversation,
    second_user: SecondUser,
    case: str,
) -> None:
    if case == "missing":
        resp = conversations_audit_client.update_title(MISSING_CONVERSATION_ID, title="x")
    elif case == "shared-with-write-access":
        conversation_id = seed_conversation(isShared=True, sharedWith=[share_entry(second_user.user_id, "write")])
        resp = request_as(second_user, "PATCH", f"/{conversation_id}/title", json={"title": "x"})
    else:
        fields: dict[str, Any] = (
            {"isDeleted": True} if case == "deleted" else {"sessionType": "agent", "agentKey": "spec-audit-agent"}
        )
        resp = conversations_audit_client.update_title(seed_conversation(**fields), title="x")

    error = error_of(resp, 404)
    assert (error["code"], error["message"]) == ("HTTP_NOT_FOUND", "Conversation not found")
    assert_strict_openapi_exchange(resp, ROUTE)


@pytest.mark.parametrize(
    "headers",
    [pytest.param(None, id="no_token"), pytest.param(INVALID_AUTH_HEADERS, id="invalid_token")],
)
def test_title_without_valid_token_is_unauthorized(
    conversations_audit_client: ConversationsAuditClient, headers: dict[str, str] | None
) -> None:
    kwargs: dict[str, Any] = {"headers": headers} if headers else {}
    resp = conversations_audit_client.update_title(MISSING_CONVERSATION_ID, title="x", auth=False, **kwargs)

    assert error_of(resp, 401)["code"] == "HTTP_UNAUTHORIZED"
    assert_strict_openapi_exchange(resp, ROUTE)


def test_title_with_token_lacking_write_scope_is_forbidden(
    pipeshub_client: PipeshubClient, narrow_scope_headers: dict[str, str]
) -> None:
    resp = requests.patch(
        f"{pipeshub_client.base_url}/api/v1/conversations/{MISSING_CONVERSATION_ID}/title",
        headers=narrow_scope_headers,
        json={"title": "x"},
        timeout=pipeshub_client.timeout_seconds,
    )

    assert error_of(resp, 403)["code"] == "HTTP_FORBIDDEN"
    assert_strict_openapi_exchange(resp, ROUTE)
