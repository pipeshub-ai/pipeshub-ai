"""Strict OpenAPI audit of PATCH /api/v1/agents/:agentKey/conversations/:conversationId/project-visibility."""

from __future__ import annotations

import pytest
from agents_audit_support import (
    MISSING_CONVERSATION_ID,
    PROJECT_VISIBILITIES,
    SEED_AGENT_KEY,
    AgentsAuditClient,
    SeedAgentConversation,
    SeedProject,
)
from bson import ObjectId
from pymongo.collection import Collection

from helper.second_user import SecondUser
from strict_openapi import assert_strict_openapi_response

pytestmark = pytest.mark.spec_audit

ROUTE = "/api/v1/agents/:agentKey/conversations/:conversationId/project-visibility"


def test_owner_switches_visibility_of_linked_conversation(
    agents_audit_client: AgentsAuditClient,
    seed_agent_conversation: SeedAgentConversation,
    seed_project: SeedProject,
    chat_sessions_collection: Collection,
) -> None:
    project_id = seed_project()
    conversation_id = seed_agent_conversation(
        projectId=ObjectId(project_id), projectVisibility="private"
    )

    for visibility in reversed(PROJECT_VISIBILITIES):
        resp = agents_audit_client.set_project_visibility(
            SEED_AGENT_KEY, conversation_id, visibility
        )
        assert resp.status_code == 200, resp.text[:500]
        assert_strict_openapi_response(resp, ROUTE)
        assert resp.json() == {
            "conversationId": conversation_id,
            "projectVisibility": visibility,
        }

        stored = chat_sessions_collection.find_one({"_id": ObjectId(conversation_id)})
        assert stored is not None
        assert stored["projectVisibility"] == visibility
        assert str(stored["projectId"]) == project_id


def test_unlinked_conversation_is_rejected(
    agents_audit_client: AgentsAuditClient,
    seed_agent_conversation: SeedAgentConversation,
    chat_sessions_collection: Collection,
) -> None:
    conversation_id = seed_agent_conversation()

    resp = agents_audit_client.set_project_visibility(
        SEED_AGENT_KEY, conversation_id, "project"
    )
    assert resp.status_code == 400, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)

    stored = chat_sessions_collection.find_one({"_id": ObjectId(conversation_id)})
    assert stored is not None
    assert "projectVisibility" not in stored


def test_unknown_visibility_value_is_rejected(
    agents_audit_client: AgentsAuditClient,
) -> None:
    # 'org' is a valid *project* visibility but not a conversation one.
    resp = agents_audit_client.set_project_visibility(
        SEED_AGENT_KEY, MISSING_CONVERSATION_ID, "org"
    )
    assert resp.status_code == 400, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)


def test_another_users_conversation_is_not_found(
    agents_audit_client: AgentsAuditClient,
    second_user: SecondUser,
    seed_agent_conversation: SeedAgentConversation,
    chat_sessions_collection: Collection,
) -> None:
    # The controller never loads the project, so the link needs no real one.
    conversation_id = seed_agent_conversation(
        owner=second_user.user_id, projectId=ObjectId(), projectVisibility="private"
    )

    resp = agents_audit_client.set_project_visibility(
        SEED_AGENT_KEY, conversation_id, "project"
    )
    assert resp.status_code == 404, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)

    stored = chat_sessions_collection.find_one({"_id": ObjectId(conversation_id)})
    assert stored is not None
    assert stored["projectVisibility"] == "private"


def test_without_token_is_unauthorized(
    agents_audit_client: AgentsAuditClient,
) -> None:
    resp = agents_audit_client.set_project_visibility(
        SEED_AGENT_KEY, MISSING_CONVERSATION_ID, "project", auth=False
    )
    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)
