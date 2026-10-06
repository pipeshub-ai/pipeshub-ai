"""Strict OpenAPI audit of DELETE /api/v1/agents/:agentKey/conversations/:conversationId."""

from __future__ import annotations

import pytest
from agents_audit_support import (
    MALFORMED_CONVERSATION_ID,
    MISSING_CONVERSATION_ID,
    OTHER_AGENT_KEY,
    SEED_AGENT_KEY,
    UNSAFE_PATH_SEGMENT,
    AgentsAuditClient,
    SeedAgentConversation,
)
from pymongo.collection import Collection
from bson import ObjectId
from strict_openapi import assert_strict_openapi_exchange

pytestmark = pytest.mark.spec_audit

ROUTE = "/api/v1/agents/:agentKey/conversations/:conversationId"


def test_delete_soft_deletes_and_returns_the_conversation(
    agents_audit_client: AgentsAuditClient,
    seed_agent_conversation: SeedAgentConversation,
    chat_sessions_collection: Collection,
) -> None:
    conversation_id = seed_agent_conversation()

    resp = agents_audit_client.delete_conversation(SEED_AGENT_KEY, conversation_id)

    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert resp.json()["conversation"]["_id"] == conversation_id
    stored = chat_sessions_collection.find_one({"_id": ObjectId(conversation_id)})
    assert stored is not None and stored["isDeleted"] is True


@pytest.mark.parametrize("target", ["missing", "other-agent", "already-deleted"])
def test_delete_out_of_reach_is_a_no_op(
    agents_audit_client: AgentsAuditClient,
    seed_agent_conversation: SeedAgentConversation,
    target: str,
) -> None:
    conversation_id = {
        "missing": lambda: MISSING_CONVERSATION_ID,
        "other-agent": lambda: seed_agent_conversation(agent_key=OTHER_AGENT_KEY),
        "already-deleted": lambda: seed_agent_conversation(isDeleted=True),
    }[target]()

    resp = agents_audit_client.delete_conversation(SEED_AGENT_KEY, conversation_id)

    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert resp.json()["conversation"] is None


@pytest.mark.parametrize(
    ("agent_key", "conversation_id"),
    [
        pytest.param(SEED_AGENT_KEY, MALFORMED_CONVERSATION_ID, id="malformed-conversation-id"),
        pytest.param(UNSAFE_PATH_SEGMENT, MISSING_CONVERSATION_ID, id="unsafe-agent-key"),
    ],
)
def test_delete_with_bad_path_is_rejected(
    agents_audit_client: AgentsAuditClient, agent_key: str, conversation_id: str
) -> None:
    resp = agents_audit_client.delete_conversation(agent_key, conversation_id)

    assert resp.status_code == 400, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


def test_delete_without_token_is_unauthorized(agents_audit_client: AgentsAuditClient) -> None:
    resp = agents_audit_client.delete_conversation(SEED_AGENT_KEY, MISSING_CONVERSATION_ID, auth=False)

    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


def test_delete_without_agent_write_scope_is_forbidden(
    agents_audit_client: AgentsAuditClient,
    narrow_scope_headers: dict[str, str],
) -> None:
    resp = agents_audit_client.delete_conversation(
        SEED_AGENT_KEY, MISSING_CONVERSATION_ID, auth=False, headers=narrow_scope_headers
    )

    assert resp.status_code == 403, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
