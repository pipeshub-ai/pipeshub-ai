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
    error_of,
)
from bson import ObjectId
from helper.second_user import SecondUser
from pymongo.collection import Collection
from strict_openapi import assert_strict_openapi_exchange, outside_request_contract

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
    assert_strict_openapi_exchange(linked, ROUTE)
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
    assert_strict_openapi_exchange(unlinked, ROUTE)
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
    assert_strict_openapi_exchange(resp, ROUTE)


def test_token_without_agent_write_scope_is_forbidden(
    agents_audit_client: AgentsAuditClient, narrow_scope_headers: dict[str, str]
) -> None:
    resp = agents_audit_client.set_project(
        SEED_AGENT_KEY, MISSING_CONVERSATION_ID, None, auth=False, headers=narrow_scope_headers
    )

    assert resp.status_code == 403, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


def test_another_users_conversation_is_not_found(
    agents_audit_client: AgentsAuditClient,
    second_user: SecondUser,
    seed_agent_conversation: SeedAgentConversation,
) -> None:
    conversation_id = seed_agent_conversation(owner=second_user.user_id)

    resp = agents_audit_client.set_project(SEED_AGENT_KEY, conversation_id, None)

    assert resp.status_code == 404, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


def test_unknown_body_fields_and_query_are_ignored(
    agents_audit_client: AgentsAuditClient,
    seed_agent_conversation: SeedAgentConversation,
) -> None:
    conversation_id = seed_agent_conversation()

    with outside_request_contract("unknown body fields are stripped and the query is not read"):
        resp = agents_audit_client.put(
            f"/{SEED_AGENT_KEY}/conversations/{conversation_id}/project",
            json={"projectId": None, "visibility": "project"},
            params={"page": "1"},
        )
        assert_strict_openapi_exchange(resp, ROUTE)

    assert resp.status_code == 200, resp.text[:500]
    assert resp.json()["projectId"] is None


@pytest.mark.parametrize(
    ("agent_key", "body", "expected_status", "code"),
    [
        pytest.param(SEED_AGENT_KEY, {"projectId": MALFORMED_PROJECT_ID}, 400, "VALIDATION_ERROR", id="malformed-project-id"),
        pytest.param(SEED_AGENT_KEY, {}, 400, "VALIDATION_ERROR", id="missing-project-id"),
        pytest.param(SEED_AGENT_KEY, {"projectId": 7}, 400, "VALIDATION_ERROR", id="project-id-not-string"),
        pytest.param(SEED_AGENT_KEY, {"projectId": MISSING_PROJECT_ID}, 404, "HTTP_NOT_FOUND", id="unknown-project"),
        pytest.param(UNSAFE_PATH_SEGMENT, {"projectId": MISSING_PROJECT_ID}, 400, "HTTP_BAD_REQUEST", id="unsafe-agent-key"),
    ],
)
def test_set_project_is_refused(
    agents_audit_client: AgentsAuditClient,
    seed_agent_conversation: SeedAgentConversation,
    chat_sessions_collection: Collection,
    agent_key: str,
    body: dict[str, object],
    expected_status: int,
    code: str,
) -> None:
    # A real, owned conversation, so the refusal comes from the input under test.
    conversation_id = seed_agent_conversation()

    resp = agents_audit_client.put(f"/{agent_key}/conversations/{conversation_id}/project", json=body)

    assert resp.status_code == expected_status, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert error_of(resp)["code"] == code

    stored = chat_sessions_collection.find_one({"_id": ObjectId(conversation_id)})
    assert stored is not None
    assert "projectId" not in stored
