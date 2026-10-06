"""Strict OpenAPI audit of GET /api/v1/agents/:agentKey/conversations/:conversationId."""

from __future__ import annotations

from typing import Any

import pytest
from agents_audit_support import (
    MALFORMED_CONVERSATION_ID,
    MISSING_CONVERSATION_ID,
    OTHER_AGENT_KEY,
    SEED_AGENT_KEY,
    AgentsAuditClient,
    SeedAgentConversation,
    SeedMessage,
    error_of,
)
from strict_openapi import assert_strict_openapi_exchange

pytestmark = pytest.mark.spec_audit

ROUTE = "/api/v1/agents/:agentKey/conversations/:conversationId"


@pytest.fixture
def seeded_turn(
    seed_agent_conversation: SeedAgentConversation, seed_message: SeedMessage
) -> tuple[str, list[str]]:
    conversation_id = seed_agent_conversation()
    ids = [
        seed_message(conversation_id, "user_query", "Hello?"),
        seed_message(conversation_id, "bot_response", "Hello."),
    ]
    return conversation_id, ids


def test_returns_the_conversation_with_its_messages(
    agents_audit_client: AgentsAuditClient,
    seeded_turn: tuple[str, list[str]],
) -> None:
    conversation_id, message_ids = seeded_turn

    resp = agents_audit_client.get_conversation(SEED_AGENT_KEY, conversation_id)

    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    body = resp.json()
    assert body["conversation"]["id"] == conversation_id
    assert sorted(m["_id"] for m in body["conversation"]["messages"]) == sorted(message_ids)
    assert body["meta"]["messageCount"] == 2


@pytest.mark.parametrize(
    "params",
    [
        pytest.param({"page": "1", "limit": "100", "sortBy": "content", "sortOrder": "asc"}, id="paging-and-sort"),
        pytest.param({"messageType": "bot_response"}, id="message-type"),
        pytest.param(
            {"startDate": "2020-01-01T00:00:00.000Z", "endDate": "2999-01-01T00:00:00.000Z"},
            id="date-range",
        ),
        pytest.param({"startDate": "2020-01-01"}, id="date-only"),
        pytest.param({"sortOrder": "sideways"}, id="non-asc-order-read-as-desc"),
    ],
)
def test_documented_query_is_accepted(
    agents_audit_client: AgentsAuditClient,
    seeded_turn: tuple[str, list[str]],
    params: dict[str, str],
) -> None:
    conversation_id, _ = seeded_turn

    resp = agents_audit_client.get_conversation(SEED_AGENT_KEY, conversation_id, **params)

    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


@pytest.mark.parametrize(
    "params",
    [
        pytest.param({"page": "0"}, id="page-zero"),
        pytest.param({"limit": "101"}, id="limit-over-max"),
        pytest.param({"sortBy": "title"}, id="sort-by-unknown"),
        pytest.param({"messageType": "tool_call"}, id="message-type-unknown"),
        pytest.param({"startDate": "not-a-date"}, id="invalid-start-date"),
        pytest.param({"endDate": "not-a-date"}, id="invalid-end-date"),
        pytest.param({"sortBy": ["createdAt", "content"]}, id="sort-by-repeated"),
        pytest.param({"messageType": ["user_query", "bot_response"]}, id="message-type-repeated"),
    ],
)
def test_refused_query_is_a_validation_error(
    agents_audit_client: AgentsAuditClient, params: dict[str, Any]
) -> None:
    resp = agents_audit_client.get_conversation(SEED_AGENT_KEY, MISSING_CONVERSATION_ID, **params)

    assert resp.status_code == 400, resp.text[:500]
    assert error_of(resp)["code"] == "VALIDATION_ERROR", resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


def test_malformed_conversation_id_is_a_validation_error(
    agents_audit_client: AgentsAuditClient,
) -> None:
    resp = agents_audit_client.get_conversation(SEED_AGENT_KEY, MALFORMED_CONVERSATION_ID)

    assert resp.status_code == 400, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


@pytest.mark.parametrize("target", ["missing-conversation", "other-agent", "deleted"])
def test_conversation_out_of_reach_is_not_found(
    agents_audit_client: AgentsAuditClient,
    seed_agent_conversation: SeedAgentConversation,
    target: str,
) -> None:
    conversation_id = {
        "missing-conversation": lambda: MISSING_CONVERSATION_ID,
        "other-agent": lambda: seed_agent_conversation(agent_key=OTHER_AGENT_KEY),
        "deleted": lambda: seed_agent_conversation(isDeleted=True),
    }[target]()

    resp = agents_audit_client.get_conversation(SEED_AGENT_KEY, conversation_id)

    assert resp.status_code == 404, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


def test_without_token_is_unauthorized(agents_audit_client: AgentsAuditClient) -> None:
    resp = agents_audit_client.get_conversation(SEED_AGENT_KEY, MISSING_CONVERSATION_ID, auth=False)

    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


def test_token_without_agent_read_scope_is_forbidden(
    agents_audit_client: AgentsAuditClient,
    narrow_scope_headers: dict[str, str],
) -> None:
    resp = agents_audit_client.get_conversation(
        SEED_AGENT_KEY, MISSING_CONVERSATION_ID, auth=False, headers=narrow_scope_headers
    )

    assert resp.status_code == 403, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
