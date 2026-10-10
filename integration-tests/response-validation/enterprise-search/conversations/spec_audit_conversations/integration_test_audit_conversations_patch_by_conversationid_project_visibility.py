"""Strict OpenAPI audit of PATCH /api/v1/conversations/:conversationId/project-visibility."""

from __future__ import annotations

from typing import Any

import pytest
import requests
from bson import ObjectId
from conversations_audit_support import (
    MALFORMED_CONVERSATION_ID,
    MISSING_CONVERSATION_ID,
    PROJECT_VISIBILITIES,
    PROJECT_VISIBILITY_ROUTE,
    ConversationsAuditClient,
    SeedConversation,
    error_of,
    request_as,
    validation_fields,
)
from helper.pipeshub_client import PipeshubClient
from helper.second_user import SecondUser
from pymongo.collection import Collection
from strict_openapi import assert_strict_openapi_exchange

pytestmark = pytest.mark.spec_audit

ROUTE = PROJECT_VISIBILITY_ROUTE


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
        assert_strict_openapi_exchange(resp, ROUTE)
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
    assert_strict_openapi_exchange(resp, ROUTE)
    assert _stored_visibility(chat_sessions_collection, conversation_id) == "private"


@pytest.mark.parametrize(
    ("body", "field"),
    [
        pytest.param({"visibility": "public"}, "body.visibility", id="unknown-visibility"),
        pytest.param({}, "body.visibility", id="visibility-missing"),
    ],
)
def test_invalid_body_fails_validation(
    conversations_audit_client: ConversationsAuditClient,
    seed_conversation: SeedConversation,
    chat_sessions_collection: Collection,
    body: dict[str, Any],
    field: str,
) -> None:
    conversation_id = seed_conversation(project_id=str(ObjectId()))

    resp = conversations_audit_client.patch(f"/{conversation_id}/project-visibility", json=body)
    assert validation_fields(resp) == [field]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert _stored_visibility(chat_sessions_collection, conversation_id) == "private"


def test_malformed_conversation_id_fails_validation(
    conversations_audit_client: ConversationsAuditClient,
) -> None:
    resp = conversations_audit_client.set_project_visibility(MALFORMED_CONVERSATION_ID, "project")
    assert validation_fields(resp) == ["params.conversationId"]
    assert_strict_openapi_exchange(resp, ROUTE)


def test_conversation_without_project_is_a_bad_request(
    conversations_audit_client: ConversationsAuditClient,
    seed_conversation: SeedConversation,
    chat_sessions_collection: Collection,
) -> None:
    conversation_id = seed_conversation()

    resp = conversations_audit_client.set_project_visibility(conversation_id, "project")
    error = error_of(resp, 400)
    assert (error["code"], error["message"]) == ("HTTP_BAD_REQUEST", "Conversation is not linked to a project")
    assert_strict_openapi_exchange(resp, ROUTE)
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
    assert_strict_openapi_exchange(resp, ROUTE)
    assert _stored_visibility(chat_sessions_collection, conversation_id) == "private"


@pytest.mark.parametrize(
    "fields",
    [
        pytest.param(None, id="no-such-conversation"),
        pytest.param({"isDeleted": True}, id="deleted-conversation"),
        pytest.param({"sessionType": "agent", "agentKey": "spec-audit-agent"}, id="agent-conversation"),
    ],
)
def test_unreachable_conversation_is_not_found(
    conversations_audit_client: ConversationsAuditClient,
    seed_conversation: SeedConversation,
    fields: dict[str, Any] | None,
) -> None:
    conversation_id = (
        MISSING_CONVERSATION_ID if fields is None else seed_conversation(project_id=str(ObjectId()), **fields)
    )

    resp = conversations_audit_client.set_project_visibility(conversation_id, "project")
    error = error_of(resp, 404)
    assert (error["code"], error["message"]) == ("HTTP_NOT_FOUND", "Conversation not found or unauthorized")
    assert_strict_openapi_exchange(resp, ROUTE)


def test_token_lacking_write_scope_is_forbidden(
    pipeshub_client: PipeshubClient, narrow_scope_headers: dict[str, str]
) -> None:
    resp = requests.patch(
        f"{pipeshub_client.base_url}/api/v1/conversations/{MISSING_CONVERSATION_ID}/project-visibility",
        headers=narrow_scope_headers,
        json={"visibility": "project"},
        timeout=pipeshub_client.timeout_seconds,
    )
    assert error_of(resp, 403)["code"] == "HTTP_FORBIDDEN"
    assert_strict_openapi_exchange(resp, ROUTE)
