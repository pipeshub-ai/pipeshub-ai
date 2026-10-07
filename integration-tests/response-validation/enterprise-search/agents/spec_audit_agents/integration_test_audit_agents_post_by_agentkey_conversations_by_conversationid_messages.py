"""Strict OpenAPI audit of POST /api/v1/agents/:agentKey/conversations/:conversationId/messages."""

from __future__ import annotations

from typing import Any

import pytest
from agents_audit_support import (
    ANSWER_TIMEOUT,
    MALFORMED_CONVERSATION_ID,
    MISSING_CONVERSATION_ID,
    OTHER_AGENT_KEY,
    QUICK_TURN,
    SEED_AGENT_KEY,
    UNKNOWN_MODEL_KEY,
    AgentsAuditClient,
    SeedAgentConversation,
    SeedMessage,
    error_of,
    refused_bodies,
    request_as,
)
from helper.second_user import SecondUser
from strict_openapi import assert_spec_forbids_request, assert_strict_openapi_exchange

pytestmark = pytest.mark.spec_audit

ROUTE = "/api/v1/agents/:agentKey/conversations/:conversationId/messages"
CONVERSATION_ID_HEADER = "X-Conversation-Id"


def test_follow_up_appends_one_answered_turn(
    agents_audit_client: AgentsAuditClient,
    audit_agent: str,
    seed_agent_conversation: SeedAgentConversation,
    seed_message: SeedMessage,
) -> None:
    conversation_id = seed_agent_conversation(agent_key=audit_agent)
    seed_message(conversation_id, "user_query", "Say hello.")
    seed_message(conversation_id, "bot_response", "Hello.")

    resp = agents_audit_client.add_message(
        audit_agent,
        conversation_id,
        json={"query": "Reply with the single word OK.", "reasoningEffort": "low"},
        timeout=ANSWER_TIMEOUT,
    )

    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert resp.headers[CONVERSATION_ID_HEADER] == conversation_id
    body = resp.json()
    assert [m["messageType"] for m in body["conversation"]["messages"]][-2:] == [
        "user_query",
        "bot_response",
    ]
    assert body["recordsUsed"] == body["meta"]["recordsUsed"]


def test_unknown_model_key_falls_back_to_the_default_model(
    agents_audit_client: AgentsAuditClient,
    audit_agent: str,
    seed_agent_conversation: SeedAgentConversation,
) -> None:
    conversation_id = seed_agent_conversation(agent_key=audit_agent)

    resp = agents_audit_client.add_message(
        audit_agent,
        conversation_id,
        json={**QUICK_TURN, "modelKey": UNKNOWN_MODEL_KEY},
        timeout=ANSWER_TIMEOUT,
    )

    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    conversation = resp.json()["conversation"]
    assert conversation["status"] == "Complete", conversation.get("failReason")
    assert conversation["messages"][-1]["messageType"] == "bot_response"

@pytest.mark.parametrize(
    ("agent_key", "conversation_id"),
    [
        pytest.param(SEED_AGENT_KEY, MISSING_CONVERSATION_ID, id="missing-conversation"),
        pytest.param(OTHER_AGENT_KEY, None, id="other-agents-conversation"),
    ],
)
def test_conversation_out_of_reach_is_not_found(
    agents_audit_client: AgentsAuditClient,
    seed_agent_conversation: SeedAgentConversation,
    agent_key: str,
    conversation_id: str | None,
) -> None:
    target = conversation_id or seed_agent_conversation(agent_key=SEED_AGENT_KEY)

    resp = agents_audit_client.add_message(agent_key, target, json=QUICK_TURN)

    assert resp.status_code == 404, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


def test_member_without_access_to_the_agent_fails_the_turn_as_not_found(
    audit_agent: str,
    second_user: SecondUser,
    seed_agent_conversation: SeedAgentConversation,
) -> None:
    conversation_id = seed_agent_conversation(agent_key=audit_agent, owner=second_user.user_id)

    resp = request_as(
        second_user,
        "POST",
        f"/{audit_agent}/conversations/{conversation_id}/messages",
        json=QUICK_TURN,
        timeout=ANSWER_TIMEOUT,
    )

    assert resp.status_code == 404, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert error_of(resp)["code"] == "HTTP_NOT_FOUND", resp.text[:500]
    assert resp.headers.get(CONVERSATION_ID_HEADER) == conversation_id


@pytest.mark.parametrize("body", refused_bodies("message", QUICK_TURN))
def test_refused_body_is_a_validation_error(
    agents_audit_client: AgentsAuditClient, body: dict[str, Any]
) -> None:
    resp = agents_audit_client.add_message(SEED_AGENT_KEY, MISSING_CONVERSATION_ID, json=body)

    assert resp.status_code == 400, resp.text[:500]
    assert error_of(resp)["code"] == "VALIDATION_ERROR", resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


def test_malformed_conversation_id_is_a_validation_error(
    agents_audit_client: AgentsAuditClient,
) -> None:
    resp = agents_audit_client.add_message(SEED_AGENT_KEY, MALFORMED_CONVERSATION_ID, json=QUICK_TURN)

    assert resp.status_code == 400, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


@pytest.mark.parametrize(
    "query",
    [
        pytest.param("<b>hello</b>", id="markup"),
        pytest.param("show %s please", id="format-specifier"),
    ],
)
def test_unsafe_query_is_refused_before_the_turn_starts(
    agents_audit_client: AgentsAuditClient,
    seed_agent_conversation: SeedAgentConversation,
    query: str,
) -> None:
    conversation_id = seed_agent_conversation()

    resp = agents_audit_client.add_message(
        SEED_AGENT_KEY, conversation_id, json={**QUICK_TURN, "query": query}
    )

    assert resp.status_code == 400, resp.text[:500]
    assert CONVERSATION_ID_HEADER not in resp.headers
    assert_strict_openapi_exchange(resp, ROUTE)
    assert_spec_forbids_request(resp, ROUTE)


def test_blank_query_is_refused_by_the_controller(
    agents_audit_client: AgentsAuditClient,
    seed_agent_conversation: SeedAgentConversation,
) -> None:
    conversation_id = seed_agent_conversation()

    resp = agents_audit_client.add_message(
        SEED_AGENT_KEY, conversation_id, json={**QUICK_TURN, "query": "   "}
    )

    assert resp.status_code == 400, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    error = error_of(resp)
    assert (error["code"], error["message"]) == ("HTTP_BAD_REQUEST", "Query is required"), resp.text[:500]
    assert_spec_forbids_request(resp, ROUTE)


def test_without_token_is_unauthorized(agents_audit_client: AgentsAuditClient) -> None:
    resp = agents_audit_client.add_message(
        SEED_AGENT_KEY, MISSING_CONVERSATION_ID, json=QUICK_TURN, auth=False
    )

    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


def test_token_without_agent_execute_scope_is_forbidden(
    agents_audit_client: AgentsAuditClient,
    narrow_scope_headers: dict[str, str],
) -> None:
    resp = agents_audit_client.add_message(
        SEED_AGENT_KEY, MISSING_CONVERSATION_ID, json=QUICK_TURN, auth=False,
        headers=narrow_scope_headers,
    )

    assert resp.status_code == 403, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
