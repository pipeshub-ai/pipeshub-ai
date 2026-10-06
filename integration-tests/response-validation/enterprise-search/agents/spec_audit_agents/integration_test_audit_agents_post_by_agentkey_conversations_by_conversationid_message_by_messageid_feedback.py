"""Strict OpenAPI audit of POST /api/v1/agents/:agentKey/conversations/:conversationId/message/:messageId/feedback."""

from __future__ import annotations

from typing import Any

import pytest
from agents_audit_support import (
    MALFORMED_CONVERSATION_ID,
    MALFORMED_MESSAGE_ID,
    MISSING_CONVERSATION_ID,
    MISSING_MESSAGE_ID,
    OTHER_AGENT_KEY,
    SEED_AGENT_KEY,
    AgentsAuditClient,
    SeedAgentConversation,
    SeedMessage,
    error_of,
)
from strict_openapi import assert_strict_openapi_exchange, outside_request_contract

pytestmark = pytest.mark.spec_audit

ROUTE = "/api/v1/agents/:agentKey/conversations/:conversationId/message/:messageId/feedback"

FULL_FEEDBACK: dict[str, Any] = {
    "isHelpful": True,
    "categories": ["excellent_answer", "helpful_citations"],
    "comments": {"positive": "Clear.", "negative": ""},
}


@pytest.fixture
def seeded_turn(
    seed_agent_conversation: SeedAgentConversation, seed_message: SeedMessage
) -> tuple[str, str, str]:
    """(conversationId, userQueryId, botResponseId) of one seeded admin conversation."""
    conversation_id = seed_agent_conversation()
    user_id = seed_message(conversation_id, "user_query", "Hello?")
    bot_id = seed_message(conversation_id, "bot_response", "Hello.")
    return conversation_id, user_id, bot_id


@pytest.mark.parametrize(
    "body",
    [pytest.param(FULL_FEEDBACK, id="full"), pytest.param({}, id="empty")],
)
def test_feedback_on_bot_response_is_stored(
    agents_audit_client: AgentsAuditClient,
    seeded_turn: tuple[str, str, str],
    body: dict[str, Any],
) -> None:
    conversation_id, _, bot_id = seeded_turn

    resp = agents_audit_client.feedback(SEED_AGENT_KEY, conversation_id, bot_id, body)

    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    stored = resp.json()
    assert stored["conversationId"] == conversation_id
    assert stored["messageId"] == bot_id
    for key, value in body.items():
        assert stored["feedback"][key] == value


def test_unknown_fields_are_stripped(
    agents_audit_client: AgentsAuditClient,
    seeded_turn: tuple[str, str, str],
) -> None:
    conversation_id, _, bot_id = seeded_turn

    with outside_request_contract("an unknown field is sent to show the validator strips it"):
        resp = agents_audit_client.feedback(
            SEED_AGENT_KEY, conversation_id, bot_id, {"isHelpful": False, "unexpected": "drop-me"}
        )
        assert_strict_openapi_exchange(resp, ROUTE)
    assert resp.status_code == 200, resp.text[:500]
    assert "unexpected" not in resp.json()["feedback"]


def test_feedback_on_user_query_is_refused(
    agents_audit_client: AgentsAuditClient,
    seeded_turn: tuple[str, str, str],
) -> None:
    conversation_id, user_id, _ = seeded_turn

    resp = agents_audit_client.feedback(SEED_AGENT_KEY, conversation_id, user_id, FULL_FEEDBACK)

    assert resp.status_code == 400, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


@pytest.mark.parametrize("target", ["missing-conversation", "missing-message", "other-agent"])
def test_feedback_target_out_of_reach_is_not_found(
    agents_audit_client: AgentsAuditClient,
    seeded_turn: tuple[str, str, str],
    target: str,
) -> None:
    conversation_id, _, bot_id = seeded_turn
    agent_key = OTHER_AGENT_KEY if target == "other-agent" else SEED_AGENT_KEY
    if target == "missing-conversation":
        conversation_id = MISSING_CONVERSATION_ID
    if target == "missing-message":
        bot_id = MISSING_MESSAGE_ID

    resp = agents_audit_client.feedback(agent_key, conversation_id, bot_id, FULL_FEEDBACK)

    assert resp.status_code == 404, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


@pytest.mark.parametrize(
    ("conversation_id", "message_id", "body"),
    [
        pytest.param(MISSING_CONVERSATION_ID, MISSING_MESSAGE_ID, {"categories": ["great"]}, id="unknown-category"),
        pytest.param(MISSING_CONVERSATION_ID, MISSING_MESSAGE_ID, {"isHelpful": "yes"}, id="is-helpful-not-boolean"),
        pytest.param(MISSING_CONVERSATION_ID, MISSING_MESSAGE_ID, {"comments": {"positive": 1}}, id="comment-not-string"),
        pytest.param(MALFORMED_CONVERSATION_ID, MISSING_MESSAGE_ID, {}, id="malformed-conversation-id"),
        pytest.param(MISSING_CONVERSATION_ID, MALFORMED_MESSAGE_ID, {}, id="malformed-message-id"),
    ],
)
def test_refused_request_is_a_validation_error(
    agents_audit_client: AgentsAuditClient,
    conversation_id: str,
    message_id: str,
    body: dict[str, Any],
) -> None:
    resp = agents_audit_client.feedback(SEED_AGENT_KEY, conversation_id, message_id, body)

    assert resp.status_code == 400, resp.text[:500]
    assert error_of(resp)["code"] == "VALIDATION_ERROR", resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


def test_without_token_is_unauthorized(agents_audit_client: AgentsAuditClient) -> None:
    resp = agents_audit_client.feedback(
        SEED_AGENT_KEY, MISSING_CONVERSATION_ID, MISSING_MESSAGE_ID, {}, auth=False
    )

    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


def test_token_without_agent_execute_scope_is_forbidden(
    agents_audit_client: AgentsAuditClient,
    narrow_scope_headers: dict[str, str],
) -> None:
    resp = agents_audit_client.feedback(
        SEED_AGENT_KEY, MISSING_CONVERSATION_ID, MISSING_MESSAGE_ID, {},
        auth=False, headers=narrow_scope_headers,
    )

    assert resp.status_code == 403, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
