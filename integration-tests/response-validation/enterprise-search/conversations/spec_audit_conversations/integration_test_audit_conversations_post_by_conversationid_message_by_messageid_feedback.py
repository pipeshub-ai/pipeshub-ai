"""Strict OpenAPI audit of POST /api/v1/conversations/:conversationId/message/:messageId/feedback."""

from __future__ import annotations

from typing import Any

import pytest
import requests
from bson import ObjectId
from conversations_audit_support import (
    FEEDBACK_ROUTE,
    INVALID_AUTH_HEADERS,
    MALFORMED_CONVERSATION_ID,
    MESSAGES_COLLECTION,
    MISSING_CONVERSATION_ID,
    ConversationsAuditClient,
    SeedTurn,
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

ROUTE = FEEDBACK_ROUTE
MISSING_MESSAGE_ID = "0123456789abcdef0123abcd"

FULL_FEEDBACK = {
    "isHelpful": True,
    "categories": ["excellent_answer", "helpful_citations"],
    "comments": {"positive": "clear", "negative": "short"},
}


def _stored_feedback(collection: Collection, message_id: str) -> list[dict[str, Any]]:
    stored = collection.database[MESSAGES_COLLECTION].find_one({"_id": ObjectId(message_id)})
    assert stored is not None, "seeded message disappeared"
    return list(stored["feedback"])


@pytest.mark.parametrize("body", [FULL_FEEDBACK, {}], ids=["every-field", "empty-body"])
def test_owner_appends_feedback_to_an_answer(
    conversations_audit_client: ConversationsAuditClient,
    seed_turn: SeedTurn,
    chat_sessions_collection: Collection,
    admin_user_id: str,
    body: dict[str, Any],
) -> None:
    conversation_id, _, bot_id = seed_turn()

    resp = conversations_audit_client.submit_message_feedback(conversation_id, bot_id, **body)

    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    reply = resp.json()
    assert (reply["conversationId"], reply["messageId"]) == (conversation_id, bot_id)
    entry = reply["feedback"]
    assert {k: entry[k] for k in body} == body
    assert entry["feedbackProvider"] == admin_user_id
    assert set(entry["metrics"]) == {"timeToFeedback", "userAgent"}
    [stored] = _stored_feedback(chat_sessions_collection, bot_id)
    assert stored["source"] == "user"


def test_each_call_appends_another_entry(
    conversations_audit_client: ConversationsAuditClient,
    seed_turn: SeedTurn,
    chat_sessions_collection: Collection,
) -> None:
    conversation_id, _, bot_id = seed_turn()

    for helpful in (True, False):
        resp = conversations_audit_client.submit_message_feedback(conversation_id, bot_id, isHelpful=helpful)
        assert resp.status_code == 200, resp.text[:500]
        assert_strict_openapi_exchange(resp, ROUTE)
    assert [f["isHelpful"] for f in _stored_feedback(chat_sessions_collection, bot_id)] == [True, False]


def test_a_user_the_conversation_is_shared_with_can_give_feedback(
    seed_turn: SeedTurn, second_user: SecondUser, chat_sessions_collection: Collection
) -> None:
    conversation_id, _, bot_id = seed_turn(isShared=True, sharedWith=[share_entry(second_user.user_id)])

    resp = request_as(second_user, "POST", f"/{conversation_id}/message/{bot_id}/feedback", json={"isHelpful": False})

    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    [stored] = _stored_feedback(chat_sessions_collection, bot_id)
    assert str(stored["feedbackProvider"]) == second_user.user_id


def test_unknown_fields_are_dropped(
    conversations_audit_client: ConversationsAuditClient,
    seed_turn: SeedTurn,
    chat_sessions_collection: Collection,
) -> None:
    conversation_id, _, bot_id = seed_turn()

    # The stored feedback model has ratings, metrics and more, but the validator keeps only
    # isHelpful, categories and comments; metrics is always computed by the server.
    with outside_request_contract("fields of the stored feedback model the validator strips"):
        resp = conversations_audit_client.submit_message_feedback(
            conversation_id,
            bot_id,
            ratings={"accuracy": 5},
            metrics={"userInteractionTime": 7},
            source="admin",
        )
        assert resp.status_code == 200, resp.text[:500]
        assert_strict_openapi_exchange(resp, ROUTE)
    entry = resp.json()["feedback"]
    assert "ratings" not in entry and "userInteractionTime" not in entry["metrics"]
    [stored] = _stored_feedback(chat_sessions_collection, bot_id)
    assert stored["source"] == "user" and "ratings" not in stored


def test_feedback_on_a_question_is_a_bad_request(
    conversations_audit_client: ConversationsAuditClient, seed_turn: SeedTurn
) -> None:
    conversation_id, user_query_id, _ = seed_turn()

    resp = conversations_audit_client.submit_message_feedback(conversation_id, user_query_id, isHelpful=True)

    error = error_of(resp, 400)
    assert (error["code"], error["message"]) == ("HTTP_BAD_REQUEST", "Feedback is only allowed for bot responses")
    assert_strict_openapi_exchange(resp, ROUTE)


@pytest.mark.parametrize(
    ("body", "field"),
    [
        pytest.param({"isHelpful": "yes"}, "body.isHelpful", id="is-helpful-not-boolean"),
        pytest.param({"categories": ["meh"]}, "body.categories.0", id="unknown-category"),
        pytest.param({"categories": "other"}, "body.categories", id="categories-not-a-list"),
        pytest.param({"comments": {"positive": 1}}, "body.comments.positive", id="comment-not-a-string"),
    ],
)
def test_invalid_body_is_rejected_by_the_validator(
    conversations_audit_client: ConversationsAuditClient, body: dict[str, Any], field: str
) -> None:
    resp = conversations_audit_client.submit_message_feedback(MISSING_CONVERSATION_ID, MISSING_MESSAGE_ID, **body)

    assert validation_fields(resp) == [field]
    assert_strict_openapi_exchange(resp, ROUTE)


@pytest.mark.parametrize(
    ("conversation_id", "message_id", "field"),
    [
        pytest.param(MALFORMED_CONVERSATION_ID, MISSING_MESSAGE_ID, "params.conversationId", id="malformed-conversation-id"),
        pytest.param(MISSING_CONVERSATION_ID, "not-a-message-id", "params.messageId", id="malformed-message-id"),
    ],
)
def test_malformed_path_ids_are_rejected(
    conversations_audit_client: ConversationsAuditClient, conversation_id: str, message_id: str, field: str
) -> None:
    resp = conversations_audit_client.submit_message_feedback(conversation_id, message_id, isHelpful=True)

    assert validation_fields(resp) == [field]
    assert_strict_openapi_exchange(resp, ROUTE)


@pytest.mark.parametrize(
    ("case", "message"),
    [
        pytest.param("missing-conversation", "Conversation not found", id="missing-conversation"),
        pytest.param("another-users", "Conversation not found", id="another-users-conversation"),
        pytest.param("agent-conversation", "Conversation not found", id="agent-conversation"),
        pytest.param("missing-message", "Message not found", id="missing-message"),
        pytest.param("message-of-another-conversation", "Message not found", id="message-of-another-conversation"),
    ],
)
def test_unreachable_conversation_or_message_is_not_found(
    conversations_audit_client: ConversationsAuditClient,
    seed_turn: SeedTurn,
    second_user: SecondUser,
    case: str,
    message: str,
) -> None:
    if case == "missing-conversation":
        target = (MISSING_CONVERSATION_ID, MISSING_MESSAGE_ID)
    elif case == "another-users":
        target = seed_turn(owner=second_user.user_id)[::2]
    elif case == "agent-conversation":
        target = seed_turn(sessionType="agent", agentKey="spec-audit-agent")[::2]
    elif case == "missing-message":
        target = (seed_turn()[0], MISSING_MESSAGE_ID)
    else:
        target = (seed_turn()[0], seed_turn()[2])

    resp = conversations_audit_client.submit_message_feedback(*target, isHelpful=True)

    error = error_of(resp, 404)
    assert (error["code"], error["message"]) == ("HTTP_NOT_FOUND", message)
    assert_strict_openapi_exchange(resp, ROUTE)


@pytest.mark.parametrize(
    "headers",
    [pytest.param(None, id="no_token"), pytest.param(INVALID_AUTH_HEADERS, id="invalid_token")],
)
def test_feedback_without_valid_token_is_unauthorized(
    conversations_audit_client: ConversationsAuditClient, headers: dict[str, str] | None
) -> None:
    kwargs: dict[str, Any] = {"headers": headers} if headers else {}
    resp = conversations_audit_client.submit_message_feedback(
        MISSING_CONVERSATION_ID, MISSING_MESSAGE_ID, auth=False, **kwargs
    )

    assert error_of(resp, 401)["code"] == "HTTP_UNAUTHORIZED"
    assert_strict_openapi_exchange(resp, ROUTE)


def test_feedback_with_token_lacking_write_scope_is_forbidden(
    pipeshub_client: PipeshubClient, narrow_scope_headers: dict[str, str]
) -> None:
    resp = requests.post(
        f"{pipeshub_client.base_url}/api/v1/conversations/{MISSING_CONVERSATION_ID}"
        f"/message/{MISSING_MESSAGE_ID}/feedback",
        headers=narrow_scope_headers,
        json={},
        timeout=pipeshub_client.timeout_seconds,
    )

    assert error_of(resp, 403)["code"] == "HTTP_FORBIDDEN"
    assert_strict_openapi_exchange(resp, ROUTE)
