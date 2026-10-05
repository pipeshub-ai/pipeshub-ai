"""Strict OpenAPI audit of PUT /api/v1/agents/:agentKey/conversations/:conversationId/project."""

from __future__ import annotations

import pytest
from agents_audit_support import (
    MALFORMED_PROJECT_ID,
    MISSING_CONVERSATION_ID,
    MISSING_PROJECT_ID,
    PROJECT_VISIBILITIES,
    SEED_AGENT_KEY,
    UNSAFE_PATH_SEGMENT,
    AgentsAuditClient,
    SeedAgentConversation,
    SeedProject,
)
from bson import ObjectId
from pymongo.collection import Collection
from strict_openapi import assert_strict_openapi_response

pytestmark = pytest.mark.spec_audit

ROUTE = "/api/v1/agents/:agentKey/conversations/:conversationId/project"


def test_link_then_unlink_project(
    agents_audit_client: AgentsAuditClient,
    seed_agent_conversation: SeedAgentConversation,
    seed_project: SeedProject,
    chat_sessions_collection: Collection,
) -> None:
    conversation_id = seed_agent_conversation()
    project_id = seed_project()

    linked = agents_audit_client.set_project(SEED_AGENT_KEY, conversation_id, project_id)
    assert linked.status_code == 200, linked.text[:500]
    assert_strict_openapi_response(linked, ROUTE)
    body = linked.json()
    assert body["conversationId"] == conversation_id
    assert body["projectId"] == project_id
    assert body["projectVisibility"] in PROJECT_VISIBILITIES

    stored = chat_sessions_collection.find_one({"_id": ObjectId(conversation_id)})
    assert stored is not None
    assert str(stored["projectId"]) == project_id
    assert stored["projectVisibility"] == body["projectVisibility"]

    unlinked = agents_audit_client.set_project(SEED_AGENT_KEY, conversation_id, None)
    assert unlinked.status_code == 200, unlinked.text[:500]
    assert_strict_openapi_response(unlinked, ROUTE)
    # The controller sends explicit nulls instead of dropping the keys.
    assert unlinked.json() == {
        "conversationId": conversation_id,
        "projectId": None,
        "projectVisibility": None,
    }

    stored = chat_sessions_collection.find_one({"_id": ObjectId(conversation_id)})
    assert stored is not None
    assert "projectId" not in stored
    assert "projectVisibility" not in stored


def test_set_project_without_token_is_unauthorized(
    agents_audit_client: AgentsAuditClient,
) -> None:
    resp = agents_audit_client.set_project(
        SEED_AGENT_KEY, MISSING_CONVERSATION_ID, None, auth=False
    )

    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)


@pytest.mark.parametrize(
    ("agent_key", "project_id", "expected_status"),
    [
        pytest.param(SEED_AGENT_KEY, MALFORMED_PROJECT_ID, 400, id="malformed-project-id"),
        pytest.param(SEED_AGENT_KEY, MISSING_PROJECT_ID, 404, id="unknown-project"),
        pytest.param(UNSAFE_PATH_SEGMENT, MISSING_PROJECT_ID, 400, id="unsafe-agent-key"),
    ],
)
def test_set_project_is_refused(
    agents_audit_client: AgentsAuditClient,
    seed_agent_conversation: SeedAgentConversation,
    chat_sessions_collection: Collection,
    agent_key: str,
    project_id: str,
    expected_status: int,
) -> None:
    # A real, owned conversation, so the refusal comes from the input under test.
    conversation_id = seed_agent_conversation()

    resp = agents_audit_client.set_project(agent_key, conversation_id, project_id)

    assert resp.status_code == expected_status, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)

    stored = chat_sessions_collection.find_one({"_id": ObjectId(conversation_id)})
    assert stored is not None
    assert "projectId" not in stored
