"""Strict OpenAPI audit of PATCH /api/v1/conversations/:conversationId/project-visibility."""

from __future__ import annotations

import pytest
from bson import ObjectId
from pymongo.collection import Collection

from conversations_audit_support import (
    PROJECT_VISIBILITIES,
    ConversationsAuditClient,
    SeedConversation,
    request_as,
)
from helper.second_user import SecondUser
from strict_openapi import assert_strict_openapi_response

pytestmark = pytest.mark.spec_audit

ROUTE = "/api/v1/conversations/:conversationId/project-visibility"


def _stored_visibility(collection: Collection, conversation_id: str) -> str | None:
    stored = collection.find_one({"_id": ObjectId(conversation_id)})
    assert stored is not None, "seeded conversation disappeared"
    return stored.get("projectVisibility")


def test_owner_toggles_visibility_of_project_linked_conversation(
    conversations_audit_client: ConversationsAuditClient,
    seed_conversation: SeedConversation,
    chat_sessions_collection: Collection,
) -> None:
    conversation_id = seed_conversation(project_id=str(ObjectId()))

    # Seeded as 'private', so 'project' first proves the write, then it is put back.
    for visibility in reversed(PROJECT_VISIBILITIES):
        resp = conversations_audit_client.set_project_visibility(conversation_id, visibility)
        assert resp.status_code == 200, resp.text[:500]
        assert_strict_openapi_response(resp, ROUTE)
        assert resp.json() == {
            "conversationId": conversation_id,
            "projectVisibility": visibility,
        }
        assert _stored_visibility(chat_sessions_collection, conversation_id) == visibility


def test_unauthenticated_request_is_rejected(
    conversations_audit_client: ConversationsAuditClient,
    seed_conversation: SeedConversation,
    chat_sessions_collection: Collection,
) -> None:
    conversation_id = seed_conversation(project_id=str(ObjectId()))

    resp = conversations_audit_client.set_project_visibility(
        conversation_id, "project", auth=False
    )
    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)
    assert _stored_visibility(chat_sessions_collection, conversation_id) == "private"


def test_unknown_visibility_value_fails_validation(
    conversations_audit_client: ConversationsAuditClient,
    seed_conversation: SeedConversation,
    chat_sessions_collection: Collection,
) -> None:
    conversation_id = seed_conversation(project_id=str(ObjectId()))

    resp = conversations_audit_client.set_project_visibility(conversation_id, "public")
    assert resp.status_code == 400, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)
    assert _stored_visibility(chat_sessions_collection, conversation_id) == "private"


def test_conversation_without_project_is_a_bad_request(
    conversations_audit_client: ConversationsAuditClient,
    seed_conversation: SeedConversation,
    chat_sessions_collection: Collection,
) -> None:
    conversation_id = seed_conversation()

    resp = conversations_audit_client.set_project_visibility(conversation_id, "project")
    assert resp.status_code == 400, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)
    assert _stored_visibility(chat_sessions_collection, conversation_id) is None


def test_member_cannot_change_another_users_conversation(
    second_user: SecondUser,
    seed_conversation: SeedConversation,
    chat_sessions_collection: Collection,
) -> None:
    conversation_id = seed_conversation(project_id=str(ObjectId()))

    # No admin gate on this route: the owner filter answers 404, not 403.
    resp = request_as(
        second_user,
        "PATCH",
        f"/{conversation_id}/project-visibility",
        json={"visibility": "project"},
    )
    assert resp.status_code == 404, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)
    assert _stored_visibility(chat_sessions_collection, conversation_id) == "private"
