"""Strict OpenAPI audit of POST /api/v1/conversations/stream.

The gate does not read ``text/event-stream`` bodies, so every frame of the one
real stream is checked here against ``ConversationStreamSSEEvent``.
"""

from __future__ import annotations

from typing import Any, Callable

import pytest
import requests
from conversations_audit_support import (
    CHEAP_QUERY,
    INVALID_AUTH_HEADERS,
    INVALID_TURN_FIELDS,
    LEGACY_CHAT_MODES,
    LLM_TIMEOUT_SECONDS,
    STREAM_ROUTE,
    ConversationsAuditClient,
    StreamRun,
    turn_body,
    validation_fields,
)
from helper.agui_sse import AGUI
from helper.pipeshub_client import PipeshubClient
from strict_openapi import assert_strict_openapi_exchange

pytestmark = pytest.mark.spec_audit

ROUTE = STREAM_ROUTE

VALID_BODY: dict[str, Any] = {"query": CHEAP_QUERY, "chatMode": "internal_search"}


def test_stream_creates_a_conversation_and_finishes(
    conversations_audit_client: ConversationsAuditClient,
    delete_conversation_later: Callable[[str], None],
) -> None:
    resp = conversations_audit_client.stream_conversation(
        json=VALID_BODY, stream=False, timeout=LLM_TIMEOUT_SECONDS
    )
    assert_strict_openapi_exchange(resp, ROUTE)
    run = StreamRun(resp, "ConversationStreamSSEEvent")
    conversation_id = run.created.get("conversationId")
    assert conversation_id, run.names[:10]
    delete_conversation_later(conversation_id)

    assert run.events[0][0] == AGUI.CUSTOM, run.names[:5]
    assert run.created["title"] == CHEAP_QUERY[:100]
    assert run.error is None, run.error
    assert run.result is not None, f"no root RUN_FINISHED: {run.names[-5:]}"
    assert run.names[-1] == AGUI.RUN_FINISHED, run.names[-5:]
    conversation = run.result["conversation"]
    assert conversation["_id"] == conversation_id
    assert conversation["status"] == "Complete"
    assert [m["messageType"] for m in conversation["messages"]][:1] == ["user_query"]


@pytest.mark.parametrize(
    ("fields", "named"),
    [pytest.param(fields, named, id=case) for case, fields, named in INVALID_TURN_FIELDS]
    + [
        pytest.param({"chatMode": None}, "body.chatMode", id="missing-chat-mode"),
        *[
            pytest.param({"chatMode": mode}, "body.chatMode", id=f"legacy-chat-mode-{mode}")
            for mode in LEGACY_CHAT_MODES
        ],
        pytest.param({"recordIds": ["not-an-object-id"]}, "body.recordIds.0", id="record-id-not-an-object-id"),
        pytest.param({"projectId": "not-an-object-id"}, "body.projectId", id="project-id-not-an-object-id"),
        pytest.param({"projectVisibility": "public"}, "body.projectVisibility", id="unknown-project-visibility"),
    ],
)
def test_stream_rejects_invalid_body_before_streaming(
    conversations_audit_client: ConversationsAuditClient, fields: dict[str, Any], named: str
) -> None:
    resp = conversations_audit_client.stream_conversation(
        json=turn_body(VALID_BODY, fields), stream=False
    )
    assert resp.status_code == 400, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert named in validation_fields(resp), resp.text[:500]


@pytest.mark.parametrize(
    "headers",
    [pytest.param(None, id="no_token"), pytest.param(INVALID_AUTH_HEADERS, id="invalid_token")],
)
def test_stream_without_valid_token_is_unauthorized(
    conversations_audit_client: ConversationsAuditClient, headers: dict[str, str] | None
) -> None:
    kwargs: dict[str, Any] = {"headers": headers} if headers else {}
    resp = conversations_audit_client.stream_conversation(
        json=VALID_BODY, stream=False, auth=False, **kwargs
    )
    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


def test_stream_with_token_lacking_chat_scope_is_forbidden(
    pipeshub_client: PipeshubClient, narrow_scope_headers: dict[str, str]
) -> None:
    resp = requests.post(
        f"{pipeshub_client.base_url}{ROUTE}",
        headers=narrow_scope_headers,
        json=VALID_BODY,
        timeout=pipeshub_client.timeout_seconds,
    )
    assert resp.status_code == 403, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert resp.json()["error"]["code"] == "HTTP_FORBIDDEN", resp.text[:500]
