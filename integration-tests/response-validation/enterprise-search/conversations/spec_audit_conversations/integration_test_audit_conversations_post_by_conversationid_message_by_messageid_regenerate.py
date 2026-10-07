"""Strict OpenAPI audit of POST /api/v1/conversations/:conversationId/message/:messageId/regenerate.

The answer is a ``text/event-stream`` the gate does not read, so every frame is
checked here against ``SSEEvent``. Only the validator, the token check and the
scope check answer before the stream opens; every other refusal is a
``RUN_ERROR`` frame inside a 200 stream.
"""

from __future__ import annotations

from typing import Any

import pytest
import requests
from bson import ObjectId
from conversations_audit_support import (
    INVALID_AUTH_HEADERS,
    LLM_TIMEOUT_SECONDS,
    MALFORMED_CONVERSATION_ID,
    MESSAGES_COLLECTION,
    MISSING_CONVERSATION_ID,
    REGENERATE_ROUTE,
    ConversationsAuditClient,
    SeedConversation,
    SeedTurn,
    StreamRun,
    error_of,
    validation_fields,
)
from helper.agui_sse import AGUI
from helper.pipeshub_client import PipeshubClient
from helper.second_user import SecondUser
from pymongo.collection import Collection
from strict_openapi import assert_strict_openapi_exchange

pytestmark = pytest.mark.spec_audit

ROUTE = REGENERATE_ROUTE
EVENT_SCHEMA = "SSEEvent"
MISSING_MESSAGE_ID = "0123456789abcdef0123abcd"


def test_regenerate_replaces_the_last_answer(
    conversations_audit_client: ConversationsAuditClient,
    seed_turn: SeedTurn,
    chat_sessions_collection: Collection,
) -> None:
    conversation_id, _, bot_id = seed_turn()

    resp = conversations_audit_client.regenerate_message(
        conversation_id, bot_id, json={"chatMode": "internal_search"}, timeout=LLM_TIMEOUT_SECONDS
    )

    assert_strict_openapi_exchange(resp, ROUTE)
    run = StreamRun(resp, EVENT_SCHEMA)
    assert run.error is None, run.error
    assert run.names[0] == AGUI.CUSTOM and run.names[-1] == AGUI.RUN_FINISHED, run.names[-5:]
    assert run.result is not None
    messages = run.result["conversation"]["messages"]
    assert [m["messageType"] for m in messages] == ["user_query", "bot_response"]
    stored = chat_sessions_collection.database[MESSAGES_COLLECTION].find_one({"_id": ObjectId(bot_id)})
    assert stored is not None and stored["modelInfo"]["chatMode"] == "internal_search", stored


@pytest.mark.parametrize(
    ("case", "message"),
    [
        pytest.param("not-the-last-answer", "Can only regenerate the last message in the conversation", id="not-the-last-answer"),
        pytest.param("no-messages", "No messages found in conversation", id="no-messages"),
        pytest.param("missing-conversation", "Conversation not found or unauthorized", id="missing-conversation"),
        pytest.param("another-users", "Conversation not found or unauthorized", id="another-users-conversation"),
    ],
)
def test_refusals_after_validation_are_error_frames_in_a_200_stream(
    conversations_audit_client: ConversationsAuditClient,
    seed_turn: SeedTurn,
    seed_conversation: SeedConversation,
    second_user: SecondUser,
    case: str,
    message: str,
) -> None:
    if case == "not-the-last-answer":
        conversation_id, user_query_id, _ = seed_turn()
        target = (conversation_id, user_query_id)
    elif case == "no-messages":
        target = (seed_conversation(), MISSING_MESSAGE_ID)
    elif case == "missing-conversation":
        target = (MISSING_CONVERSATION_ID, MISSING_MESSAGE_ID)
    else:
        conversation_id, _, bot_id = seed_turn(owner=second_user.user_id)
        target = (conversation_id, bot_id)

    resp = conversations_audit_client.regenerate_message(*target, json={})

    assert_strict_openapi_exchange(resp, ROUTE)
    run = StreamRun(resp, EVENT_SCHEMA)
    assert run.names == [AGUI.CUSTOM, AGUI.RUN_ERROR], run.names
    assert (run.error, run.error_code) == (message, "streaming_error")


@pytest.mark.parametrize(
    ("body", "field"),
    [
        pytest.param({"chatMode": ""}, "body.chatMode", id="empty-chat-mode"),
        pytest.param({"modelKey": ""}, "body.modelKey", id="empty-model-key"),
        pytest.param({"reasoningEffort": "extreme"}, "body.reasoningEffort", id="unknown-reasoning-effort"),
        pytest.param({"currentTime": "yesterday"}, "body.currentTime", id="current-time-not-iso"),
        pytest.param({"filters": {"kb": ["not-a-uuid"]}}, "body.filters.kb.0", id="filter-kb-not-a-uuid"),
        pytest.param({"runId": "not-a-uuid"}, "body.runId", id="run-id-not-a-uuid"),
        pytest.param({"protocol": "sse"}, "body.protocol", id="protocol-not-agui"),
    ],
)
def test_invalid_body_is_rejected_before_the_stream(
    conversations_audit_client: ConversationsAuditClient, body: dict[str, Any], field: str
) -> None:
    resp = conversations_audit_client.regenerate_message(MISSING_CONVERSATION_ID, MISSING_MESSAGE_ID, json=body)

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
    resp = conversations_audit_client.regenerate_message(conversation_id, message_id, json={})

    assert validation_fields(resp) == [field]
    assert_strict_openapi_exchange(resp, ROUTE)


@pytest.mark.parametrize(
    "headers",
    [pytest.param(None, id="no_token"), pytest.param(INVALID_AUTH_HEADERS, id="invalid_token")],
)
def test_regenerate_without_valid_token_is_unauthorized(
    conversations_audit_client: ConversationsAuditClient, headers: dict[str, str] | None
) -> None:
    kwargs: dict[str, Any] = {"headers": headers} if headers else {}
    resp = conversations_audit_client.regenerate_message(
        MISSING_CONVERSATION_ID, MISSING_MESSAGE_ID, json={}, auth=False, **kwargs
    )

    assert error_of(resp, 401)["code"] == "HTTP_UNAUTHORIZED"
    assert_strict_openapi_exchange(resp, ROUTE)


def test_regenerate_with_token_lacking_chat_scope_is_forbidden(
    pipeshub_client: PipeshubClient, narrow_scope_headers: dict[str, str]
) -> None:
    resp = requests.post(
        f"{pipeshub_client.base_url}/api/v1/conversations/{MISSING_CONVERSATION_ID}"
        f"/message/{MISSING_MESSAGE_ID}/regenerate",
        headers=narrow_scope_headers,
        json={},
        timeout=pipeshub_client.timeout_seconds,
    )

    assert error_of(resp, 403)["code"] == "HTTP_FORBIDDEN"
    assert_strict_openapi_exchange(resp, ROUTE)
