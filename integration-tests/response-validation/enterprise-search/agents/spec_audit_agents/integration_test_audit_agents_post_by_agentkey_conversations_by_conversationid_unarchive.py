"""Strict OpenAPI audit of POST /api/v1/agents/:agentKey/conversations/:conversationId/unarchive."""

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
    error_of,
)
from bson import ObjectId
from helper.second_user import SecondUser
from pymongo.collection import Collection
from strict_openapi import assert_strict_openapi_exchange, outside_request_contract

pytestmark = pytest.mark.spec_audit

ROUTE = "/api/v1/agents/:agentKey/conversations/:conversationId/unarchive"


def _call(client: AgentsAuditClient, agent_key: str, conversation_id: str, **kwargs):  # noqa: ANN202
    return client.unarchive_conversation(agent_key, conversation_id, **kwargs)


def test_unarchive_flips_the_flag(
    agents_audit_client: AgentsAuditClient,
    seed_agent_conversation: SeedAgentConversation,
    chat_sessions_collection: Collection,
    admin_user_id: str,
) -> None:
    conversation_id = seed_agent_conversation(isArchived=True, archivedBy=ObjectId(admin_user_id))

    resp = _call(agents_audit_client, SEED_AGENT_KEY, conversation_id)

    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    body = resp.json()
    assert body["id"] == conversation_id
    assert body["status"] == "unarchived"
    assert body["unarchivedBy"] == admin_user_id
    stored = chat_sessions_collection.find_one({"_id": ObjectId(conversation_id)})
    assert stored is not None
    assert stored["isArchived"] is False
    assert stored["archivedBy"] is None


def test_query_parameters_are_ignored(
    agents_audit_client: AgentsAuditClient,
    seed_agent_conversation: SeedAgentConversation,
    admin_user_id: str,
) -> None:
    conversation_id = seed_agent_conversation(isArchived=True, archivedBy=ObjectId(admin_user_id))

    with outside_request_contract("the route reads no query parameter"):
        resp = _call(agents_audit_client, SEED_AGENT_KEY, conversation_id, params={"page": "1"})
        assert_strict_openapi_exchange(resp, ROUTE)

    assert resp.status_code == 200, resp.text[:500]


def test_unarchive_twice_is_refused(
    agents_audit_client: AgentsAuditClient,
    seed_agent_conversation: SeedAgentConversation,
) -> None:
    conversation_id = seed_agent_conversation()

    resp = _call(agents_audit_client, SEED_AGENT_KEY, conversation_id)

    assert resp.status_code == 400, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    error = error_of(resp)
    assert (error["code"], error["message"]) == ("HTTP_BAD_REQUEST", "Agent conversation is not archived")


@pytest.mark.parametrize("target", ["missing", "other-agent", "other-user", "deleted"])
def test_conversation_out_of_reach_is_not_found(
    agents_audit_client: AgentsAuditClient,
    seed_agent_conversation: SeedAgentConversation,
    second_user: SecondUser,
    target: str,
) -> None:
    conversation_id = {
        "missing": lambda: MISSING_CONVERSATION_ID,
        "other-agent": lambda: seed_agent_conversation(agent_key=OTHER_AGENT_KEY, isArchived=True),
        "other-user": lambda: seed_agent_conversation(owner=second_user.user_id, isArchived=True),
        "deleted": lambda: seed_agent_conversation(isDeleted=True, isArchived=True),
    }[target]()

    resp = _call(agents_audit_client, SEED_AGENT_KEY, conversation_id)

    assert resp.status_code == 404, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


@pytest.mark.parametrize(
    ("agent_key", "conversation_id", "code"),
    [
        pytest.param(SEED_AGENT_KEY, MALFORMED_CONVERSATION_ID, "VALIDATION_ERROR", id="malformed-conversation-id"),
        pytest.param(UNSAFE_PATH_SEGMENT, MISSING_CONVERSATION_ID, "HTTP_BAD_REQUEST", id="unsafe-agent-key"),
    ],
)
def test_bad_path_is_rejected(
    agents_audit_client: AgentsAuditClient, agent_key: str, conversation_id: str, code: str
) -> None:
    resp = _call(agents_audit_client, agent_key, conversation_id)

    assert resp.status_code == 400, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert error_of(resp)["code"] == code


def test_without_token_is_unauthorized(agents_audit_client: AgentsAuditClient) -> None:
    resp = _call(agents_audit_client, SEED_AGENT_KEY, MISSING_CONVERSATION_ID, auth=False)

    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


def test_token_without_agent_write_scope_is_forbidden(
    agents_audit_client: AgentsAuditClient, narrow_scope_headers: dict[str, str]
) -> None:
    resp = _call(
        agents_audit_client, SEED_AGENT_KEY, MISSING_CONVERSATION_ID, auth=False, headers=narrow_scope_headers
    )

    assert resp.status_code == 403, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
