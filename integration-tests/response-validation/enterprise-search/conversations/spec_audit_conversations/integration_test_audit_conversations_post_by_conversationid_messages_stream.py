"""Strict OpenAPI audit of POST /api/v1/conversations/:conversationId/messages/stream.

The gate does not read ``text/event-stream`` bodies, so every frame of the
streams is checked here against ``ConversationMessageStreamSSEEvent``.
"""

from __future__ import annotations

from typing import Any

import pytest
import requests
from conversations_audit_support import (
    CHEAP_FOLLOW_UP,
    INVALID_AUTH_HEADERS,
    INVALID_TURN_FIELDS,
    LEGACY_CHAT_MODES,
    LLM_TIMEOUT_SECONDS,
    MALFORMED_CONVERSATION_ID,
    MESSAGES_STREAM_ROUTE,
    MISSING_CONVERSATION_ID,
    ConversationsAuditClient,
    SeedConversation,
    StreamRun,
    turn_body,
    validation_fields,
)
from helper.agui_sse import AGUI
from helper.pipeshub_client import PipeshubClient
from helper.second_user import SecondUser
from strict_openapi import assert_strict_openapi_exchange

pytestmark = pytest.mark.spec_audit

ROUTE = MESSAGES_STREAM_ROUTE
EVENT_SCHEMA = "ConversationMessageStreamSSEEvent"

VALID_BODY: dict[str, Any] = {"query": CHEAP_FOLLOW_UP, "chatMode": "internal_search"}


def test_stream_message_appends_a_turn_and_finishes(
    conversations_audit_client: ConversationsAuditClient,
    live_conversation: requests.Response,
) -> None:
    assert live_conversation.status_code == 201, live_conversation.text[:500]
    conversation_id = live_conversation.json()["conversation"]["_id"]

    resp = conversations_audit_client.stream_message(
        conversation_id, json=VALID_BODY, stream=False, timeout=LLM_TIMEOUT_SECONDS
    )
    assert_strict_openapi_exchange(resp, ROUTE)
    run = StreamRun(resp, EVENT_SCHEMA)
    assert run.error is None, run.error
    assert run.result is not None, f"no root RUN_FINISHED: {run.names[-5:]}"
    assert run.names[-1] == AGUI.RUN_FINISHED, run.names[-5:]
    conversation = run.result["conversation"]
    assert conversation["_id"] == conversation_id
    contents = [m["content"] for m in conversation["messages"] if m["messageType"] == "user_query"]
    assert contents[-1] == CHEAP_FOLLOW_UP, contents


@pytest.mark.parametrize(
    ("owner", "fields"),
    [
        pytest.param("missing", {}, id="no-such-conversation"),
        pytest.param("member", {}, id="another-users-conversation"),
        pytest.param("admin", {"isDeleted": True}, id="deleted-conversation"),
        pytest.param("admin", {"sessionType": "agent", "agentKey": "spec-audit-agent"}, id="agent-conversation"),
    ],
)
def test_stream_message_to_an_unreachable_conversation_is_a_failed_stream(
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
    resp = conversations_audit_client.stream_message(
        conversation_id, json=VALID_BODY, stream=False, timeout=LLM_TIMEOUT_SECONDS
    )
    # API bug: the conversation is looked up after the stream is open, so a
    # missing one is a failed 200 stream rather than a 404.
    assert_strict_openapi_exchange(resp, ROUTE)
    run = StreamRun(resp, EVENT_SCHEMA)
    assert run.names == [AGUI.CUSTOM, AGUI.RUN_ERROR], run.names
    assert run.created == {"conversationId": conversation_id}
    assert run.error == "Conversation not found"
    assert run.error_code == "internal_error"
    assert run.result is None


@pytest.mark.parametrize(
    ("fields", "named"),
    [pytest.param(fields, named, id=case) for case, fields, named in INVALID_TURN_FIELDS]
    + [
        pytest.param({"chatMode": None}, "body.chatMode", id="missing-chat-mode"),
        *[
            pytest.param({"chatMode": mode}, "body.chatMode", id=f"legacy-chat-mode-{mode}")
            for mode in LEGACY_CHAT_MODES
        ],
    ],
)
def test_stream_message_rejects_invalid_body_before_streaming(
    conversations_audit_client: ConversationsAuditClient, fields: dict[str, Any], named: str
) -> None:
    resp = conversations_audit_client.stream_message(
        MISSING_CONVERSATION_ID, json=turn_body(VALID_BODY, fields), stream=False
    )
    assert resp.status_code == 400, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert named in validation_fields(resp), resp.text[:500]


def test_stream_message_with_malformed_conversation_id_is_rejected(
    conversations_audit_client: ConversationsAuditClient,
) -> None:
    resp = conversations_audit_client.stream_message(
        MALFORMED_CONVERSATION_ID, json=VALID_BODY, stream=False
    )
    assert resp.status_code == 400, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert validation_fields(resp) == ["params.conversationId"]


@pytest.mark.parametrize(
    "headers",
    [pytest.param(None, id="no_token"), pytest.param(INVALID_AUTH_HEADERS, id="invalid_token")],
)
def test_stream_message_without_valid_token_is_unauthorized(
    conversations_audit_client: ConversationsAuditClient, headers: dict[str, str] | None
) -> None:
    kwargs: dict[str, Any] = {"headers": headers} if headers else {}
    resp = conversations_audit_client.stream_message(
        MISSING_CONVERSATION_ID, json=VALID_BODY, stream=False, auth=False, **kwargs
    )
    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


def test_stream_message_with_token_lacking_chat_scope_is_forbidden(
    pipeshub_client: PipeshubClient, narrow_scope_headers: dict[str, str]
) -> None:
    resp = requests.post(
        f"{pipeshub_client.base_url}/api/v1/conversations/{MISSING_CONVERSATION_ID}/messages/stream",
        headers=narrow_scope_headers,
        json=VALID_BODY,
        timeout=pipeshub_client.timeout_seconds,
    )
    assert resp.status_code == 403, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert resp.json()["error"]["code"] == "HTTP_FORBIDDEN", resp.text[:500]
