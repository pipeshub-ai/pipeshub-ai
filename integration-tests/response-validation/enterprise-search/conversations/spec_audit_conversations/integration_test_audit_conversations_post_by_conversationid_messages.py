"""Strict OpenAPI audit of POST /api/v1/conversations/:conversationId/messages (non-streaming)."""

from __future__ import annotations

from typing import Any

import pytest
import requests
from conversations_audit_support import (
    APPLIED_NODE,
    CHEAP_FOLLOW_UP,
    INVALID_AUTH_HEADERS,
    INVALID_TURN_FIELDS,
    LEGACY_CHAT_MODES,
    LLM_TIMEOUT_SECONDS,
    MALFORMED_CONVERSATION_ID,
    MESSAGES_ROUTE,
    MISSING_CONVERSATION_ID,
    ConversationsAuditClient,
    SeedConversation,
    turn_body,
    validation_fields,
)
from helper.pipeshub_client import PipeshubClient
from helper.second_user import SecondUser
from strict_openapi import (
    assert_spec_forbids_request,
    assert_strict_openapi_exchange,
    outside_request_contract,
)

pytestmark = pytest.mark.spec_audit

ROUTE = MESSAGES_ROUTE

VALID_BODY: dict[str, Any] = {"query": CHEAP_FOLLOW_UP}


def test_add_message_answers_with_the_updated_conversation(
    conversations_audit_client: ConversationsAuditClient,
    live_conversation: requests.Response,
) -> None:
    assert live_conversation.status_code == 201, live_conversation.text[:500]
    conversation_id = live_conversation.json()["conversation"]["_id"]
    before = len(live_conversation.json()["conversation"]["messages"])

    resp = conversations_audit_client.add_message(
        conversation_id,
        json={
            **VALID_BODY,
            "chatMode": "internal_search",
            "appliedFilters": {"apps": [APPLIED_NODE]},
            "timezone": "UTC",
            "currentTime": "2026-10-06T07:30:00Z",
            "reasoningEffort": "none",
        },
        timeout=LLM_TIMEOUT_SECONDS,
    )
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    body = resp.json()
    assert resp.headers["X-Conversation-Id"] == conversation_id
    messages = body["conversation"]["messages"]
    assert len(messages) >= before + 2, [m["messageType"] for m in messages]
    assert messages[-2]["content"] == CHEAP_FOLLOW_UP
    assert messages[-1]["messageType"] == "bot_response"
    assert body["recordsUsed"] == body["meta"]["recordsUsed"]


@pytest.mark.parametrize(
    ("owner", "fields"),
    [
        pytest.param("missing", {}, id="no-such-conversation"),
        pytest.param("member", {}, id="another-users-conversation"),
        pytest.param("admin", {"isDeleted": True}, id="deleted-conversation"),
        pytest.param("admin", {"sessionType": "agent", "agentKey": "spec-audit-agent"}, id="agent-conversation"),
    ],
)
def test_add_message_to_an_unreachable_conversation_is_not_found(
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
    resp = conversations_audit_client.add_message(conversation_id, json=VALID_BODY)
    assert resp.status_code == 404, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert resp.json()["error"]["code"] == "HTTP_NOT_FOUND", resp.text[:500]


def test_add_message_validator_strips_unknown_fields(
    conversations_audit_client: ConversationsAuditClient,
) -> None:
    # The 404 comes from the lookup that runs after validation, so the field got through.
    with outside_request_contract("an undocumented body field is sent on purpose"):
        resp = conversations_audit_client.add_message(
            MISSING_CONVERSATION_ID, json={**VALID_BODY, "specAuditExtra": 1}
        )
    assert resp.status_code == 404, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert resp.json()["error"]["code"] == "HTTP_NOT_FOUND", resp.text[:500]


@pytest.mark.parametrize("chat_mode", LEGACY_CHAT_MODES)
def test_validator_accepts_legacy_chat_modes(
    conversations_audit_client: ConversationsAuditClient, chat_mode: str
) -> None:
    resp = conversations_audit_client.add_message(
        MISSING_CONVERSATION_ID, json={"query": "", "chatMode": chat_mode}
    )
    assert resp.status_code == 400, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert validation_fields(resp) == ["body.query"]


@pytest.mark.parametrize(
    ("fields", "named"),
    [pytest.param(fields, named, id=case) for case, fields, named in INVALID_TURN_FIELDS],
)
def test_add_message_rejects_invalid_body(
    conversations_audit_client: ConversationsAuditClient, fields: dict[str, Any], named: str
) -> None:
    resp = conversations_audit_client.add_message(
        MISSING_CONVERSATION_ID, json=turn_body(VALID_BODY, fields)
    )
    assert resp.status_code == 400, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert named in validation_fields(resp), resp.text[:500]


@pytest.mark.parametrize("query", ["   ", "\n\t "], ids=["spaces", "mixed-whitespace"])
def test_add_message_refuses_a_blank_query_in_the_handler(
    conversations_audit_client: ConversationsAuditClient, seed_conversation: SeedConversation, query: str
) -> None:
    resp = conversations_audit_client.add_message(seed_conversation(), json={"query": query})
    error = resp.json()["error"]
    assert resp.status_code == 400, resp.text[:500]
    assert (error["code"], error["message"]) == ("HTTP_BAD_REQUEST", "Query is required"), resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert_spec_forbids_request(resp, ROUTE)


def test_add_message_with_malformed_conversation_id_is_rejected(
    conversations_audit_client: ConversationsAuditClient,
) -> None:
    resp = conversations_audit_client.add_message(MALFORMED_CONVERSATION_ID, json=VALID_BODY)
    assert resp.status_code == 400, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert validation_fields(resp) == ["params.conversationId"]


@pytest.mark.parametrize(
    "query",
    [
        pytest.param("<script>alert(1)</script>", id="markup"),
        pytest.param("show %s of the files", id="format-specifier"),
    ],
)
def test_add_message_rejects_markup_or_format_specifiers_in_the_query(
    conversations_audit_client: ConversationsAuditClient, seed_conversation: SeedConversation, query: str
) -> None:
    resp = conversations_audit_client.add_message(seed_conversation(), json={"query": query})
    assert resp.status_code == 400, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


@pytest.mark.parametrize(
    "headers",
    [pytest.param(None, id="no_token"), pytest.param(INVALID_AUTH_HEADERS, id="invalid_token")],
)
def test_add_message_without_valid_token_is_unauthorized(
    conversations_audit_client: ConversationsAuditClient, headers: dict[str, str] | None
) -> None:
    kwargs: dict[str, Any] = {"headers": headers} if headers else {}
    resp = conversations_audit_client.add_message(
        MISSING_CONVERSATION_ID, json=VALID_BODY, auth=False, **kwargs
    )
    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


def test_add_message_with_token_lacking_chat_scope_is_forbidden(
    pipeshub_client: PipeshubClient, narrow_scope_headers: dict[str, str]
) -> None:
    resp = requests.post(
        f"{pipeshub_client.base_url}/api/v1/conversations/{MISSING_CONVERSATION_ID}/messages",
        headers=narrow_scope_headers,
        json=VALID_BODY,
        timeout=pipeshub_client.timeout_seconds,
    )
    assert resp.status_code == 403, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert resp.json()["error"]["code"] == "HTTP_FORBIDDEN", resp.text[:500]
