"""Strict OpenAPI audit of PUT /api/v1/conversations/:conversationId/project."""

from __future__ import annotations

from typing import Any

import pytest
import requests
from bson import ObjectId
from conversations_audit_support import (
    INVALID_AUTH_HEADERS,
    MALFORMED_CONVERSATION_ID,
    MISSING_CONVERSATION_ID,
    PROJECT_ROUTE,
    ConversationsAuditClient,
    SeedConversation,
    SeedProject,
    error_of,
    request_as,
    share_entry,
    validation_fields,
)
from helper.pipeshub_client import PipeshubClient
from helper.second_user import SecondUser
from pymongo.collection import Collection
from strict_openapi import assert_strict_openapi_exchange, outside_request_contract

pytestmark = pytest.mark.spec_audit

ROUTE = PROJECT_ROUTE
MISSING_PROJECT_ID = "0123456789abcdef0123abcd"


def _stored(collection: Collection, conversation_id: str) -> dict[str, Any]:
    stored = collection.find_one({"_id": ObjectId(conversation_id)})
    assert stored is not None, "seeded conversation disappeared"
    return stored


def test_link_then_unlink_a_conversation(
    conversations_audit_client: ConversationsAuditClient,
    seed_conversation: SeedConversation,
    seed_project: SeedProject,
    chat_sessions_collection: Collection,
) -> None:
    conversation_id = seed_conversation()
    project_id = seed_project()

    linked = conversations_audit_client.set_project(conversation_id, project_id)
    assert linked.status_code == 200, linked.text[:500]
    assert_strict_openapi_exchange(linked, ROUTE)
    # A new project's chatSharing is private, so the link defaults to private visibility.
    assert linked.json() == {
        "conversationId": conversation_id,
        "projectId": project_id,
        "projectVisibility": "private",
    }
    stored = _stored(chat_sessions_collection, conversation_id)
    assert (str(stored["projectId"]), stored["projectVisibility"]) == (project_id, "private")

    unlinked = conversations_audit_client.set_project(conversation_id, None)
    assert unlinked.status_code == 200, unlinked.text[:500]
    assert_strict_openapi_exchange(unlinked, ROUTE)
    assert unlinked.json() == {"conversationId": conversation_id, "projectId": None, "projectVisibility": None}
    stored = _stored(chat_sessions_collection, conversation_id)
    assert "projectId" not in stored and "projectVisibility" not in stored


def test_relinking_keeps_project_visibility(
    conversations_audit_client: ConversationsAuditClient,
    seed_conversation: SeedConversation,
    seed_project: SeedProject,
) -> None:
    conversation_id = seed_conversation(project_id=str(ObjectId()), projectVisibility="project")
    project_id = seed_project()

    resp = conversations_audit_client.set_project(conversation_id, project_id)

    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert resp.json()["projectVisibility"] == "project"


def test_unknown_body_fields_are_dropped(
    conversations_audit_client: ConversationsAuditClient,
    seed_conversation: SeedConversation,
    chat_sessions_collection: Collection,
) -> None:
    conversation_id = seed_conversation(project_id=str(ObjectId()))

    with outside_request_contract("unknown body fields are stripped by the validator, not refused"):
        resp = conversations_audit_client.put(
            f"/{conversation_id}/project", json={"projectId": None, "projectVisibility": "project"}
        )
        assert resp.status_code == 200, resp.text[:500]
        assert_strict_openapi_exchange(resp, ROUTE)
    assert "projectVisibility" not in _stored(chat_sessions_collection, conversation_id)


@pytest.mark.parametrize(
    "body",
    [
        pytest.param({}, id="project-id-missing"),
        pytest.param({"projectId": "not-a-project"}, id="project-id-malformed"),
        pytest.param({"projectId": 5}, id="project-id-not-a-string"),
    ],
)
def test_invalid_body_is_rejected_by_the_validator(
    conversations_audit_client: ConversationsAuditClient, body: dict[str, Any]
) -> None:
    resp = conversations_audit_client.put(f"/{MISSING_CONVERSATION_ID}/project", json=body)

    assert validation_fields(resp) == ["body.projectId"]
    assert_strict_openapi_exchange(resp, ROUTE)


def test_malformed_conversation_id_is_rejected(conversations_audit_client: ConversationsAuditClient) -> None:
    resp = conversations_audit_client.set_project(MALFORMED_CONVERSATION_ID, None)

    assert validation_fields(resp) == ["params.conversationId"]
    assert_strict_openapi_exchange(resp, ROUTE)


@pytest.mark.parametrize("case", ["missing", "another-users", "shared-with-write-access", "agent-conversation"])
def test_a_conversation_the_caller_did_not_start_is_not_found(
    conversations_audit_client: ConversationsAuditClient,
    seed_conversation: SeedConversation,
    second_user: SecondUser,
    case: str,
) -> None:
    if case == "missing":
        resp = conversations_audit_client.set_project(MISSING_CONVERSATION_ID, None)
    elif case == "another-users":
        resp = request_as(second_user, "PUT", f"/{seed_conversation()}/project", json={"projectId": None})
    elif case == "shared-with-write-access":
        conversation_id = seed_conversation(isShared=True, sharedWith=[share_entry(second_user.user_id, "write")])
        resp = request_as(second_user, "PUT", f"/{conversation_id}/project", json={"projectId": None})
    else:
        conversation_id = seed_conversation(sessionType="agent", agentKey="spec-audit-agent")
        resp = conversations_audit_client.set_project(conversation_id, None)

    error = error_of(resp, 404)
    assert (error["code"], error["message"]) == ("HTTP_NOT_FOUND", "Conversation not found or unauthorized")
    assert_strict_openapi_exchange(resp, ROUTE)


@pytest.mark.parametrize("case", ["missing-project", "project-the-caller-cannot-see"])
def test_a_project_the_caller_cannot_see_is_not_found(
    conversations_audit_client: ConversationsAuditClient,
    seed_conversation: SeedConversation,
    seed_project: SeedProject,
    second_user: SecondUser,
    chat_sessions_collection: Collection,
    case: str,
) -> None:
    if case == "missing-project":
        conversation_id = seed_conversation()
        resp = conversations_audit_client.set_project(conversation_id, MISSING_PROJECT_ID)
    else:
        conversation_id = seed_conversation(owner=second_user.user_id)
        resp = request_as(second_user, "PUT", f"/{conversation_id}/project", json={"projectId": seed_project()})

    error = error_of(resp, 404)
    assert (error["code"], error["message"]) == ("HTTP_NOT_FOUND", "Project not found")
    assert_strict_openapi_exchange(resp, ROUTE)
    assert "projectId" not in _stored(chat_sessions_collection, conversation_id)


@pytest.mark.parametrize(
    "headers",
    [pytest.param(None, id="no_token"), pytest.param(INVALID_AUTH_HEADERS, id="invalid_token")],
)
def test_project_without_valid_token_is_unauthorized(
    conversations_audit_client: ConversationsAuditClient, headers: dict[str, str] | None
) -> None:
    kwargs: dict[str, Any] = {"headers": headers} if headers else {}
    resp = conversations_audit_client.set_project(MISSING_CONVERSATION_ID, None, auth=False, **kwargs)

    assert error_of(resp, 401)["code"] == "HTTP_UNAUTHORIZED"
    assert_strict_openapi_exchange(resp, ROUTE)


def test_project_with_token_lacking_write_scope_is_forbidden(
    pipeshub_client: PipeshubClient, narrow_scope_headers: dict[str, str]
) -> None:
    resp = requests.put(
        f"{pipeshub_client.base_url}/api/v1/conversations/{MISSING_CONVERSATION_ID}/project",
        headers=narrow_scope_headers,
        json={"projectId": None},
        timeout=pipeshub_client.timeout_seconds,
    )

    assert error_of(resp, 403)["code"] == "HTTP_FORBIDDEN"
    assert_strict_openapi_exchange(resp, ROUTE)
