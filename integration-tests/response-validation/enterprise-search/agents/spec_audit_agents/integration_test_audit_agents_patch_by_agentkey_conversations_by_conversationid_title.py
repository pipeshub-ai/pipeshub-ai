"""Strict OpenAPI audit of PATCH /api/v1/agents/:agentKey/conversations/:conversationId/title."""

from __future__ import annotations

from typing import Any

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

ROUTE = "/api/v1/agents/:agentKey/conversations/:conversationId/title"


def _stored_title(collection: Collection, conversation_id: str) -> str:
    stored = collection.find_one({"_id": ObjectId(conversation_id)})
    assert stored is not None
    return stored["title"]


def test_title_is_trimmed_and_saved(
    agents_audit_client: AgentsAuditClient,
    seed_agent_conversation: SeedAgentConversation,
    chat_sessions_collection: Collection,
) -> None:
    conversation_id = seed_agent_conversation()

    resp = agents_audit_client.update_title(SEED_AGENT_KEY, conversation_id, title="  Renamed by audit  ")

    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    conversation = resp.json()["conversation"]
    assert conversation["_id"] == conversation_id
    assert conversation["title"] == "Renamed by audit"
    assert _stored_title(chat_sessions_collection, conversation_id) == "Renamed by audit"


def test_unknown_fields_and_query_parameters_are_ignored(
    agents_audit_client: AgentsAuditClient,
    seed_agent_conversation: SeedAgentConversation,
    chat_sessions_collection: Collection,
) -> None:
    conversation_id = seed_agent_conversation()

    with outside_request_contract("unknown body fields are stripped and the query is not read"):
        resp = agents_audit_client.update_title(
            SEED_AGENT_KEY,
            conversation_id,
            json={"title": "Kept", "pinned": True},
            params={"page": "2"},
        )
        assert_strict_openapi_exchange(resp, ROUTE)

    assert resp.status_code == 200, resp.text[:500]
    assert _stored_title(chat_sessions_collection, conversation_id) == "Kept"
    assert "pinned" not in resp.json()["conversation"]


@pytest.mark.parametrize(
    "body",
    [
        pytest.param({}, id="missing-title"),
        pytest.param({"title": ""}, id="empty-title"),
        pytest.param({"title": "   "}, id="blank-title"),
        pytest.param({"title": "x" * 201}, id="overlong-title"),
        pytest.param({"title": 7}, id="title-not-string"),
    ],
)
def test_invalid_title_is_refused_by_the_validator(
    agents_audit_client: AgentsAuditClient,
    seed_agent_conversation: SeedAgentConversation,
    chat_sessions_collection: Collection,
    body: dict[str, Any],
) -> None:
    conversation_id = seed_agent_conversation()
    before = _stored_title(chat_sessions_collection, conversation_id)

    resp = agents_audit_client.update_title(SEED_AGENT_KEY, conversation_id, json=body)

    assert resp.status_code == 400, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert error_of(resp)["code"] == "VALIDATION_ERROR"
    assert _stored_title(chat_sessions_collection, conversation_id) == before


@pytest.mark.parametrize("target", ["missing", "other-agent", "other-user", "deleted"])
def test_conversation_out_of_reach_is_not_found(
    agents_audit_client: AgentsAuditClient,
    seed_agent_conversation: SeedAgentConversation,
    second_user: SecondUser,
    target: str,
) -> None:
    conversation_id = {
        "missing": lambda: MISSING_CONVERSATION_ID,
        "other-agent": lambda: seed_agent_conversation(agent_key=OTHER_AGENT_KEY),
        "other-user": lambda: seed_agent_conversation(owner=second_user.user_id),
        "deleted": lambda: seed_agent_conversation(isDeleted=True),
    }[target]()

    resp = agents_audit_client.update_title(SEED_AGENT_KEY, conversation_id, title="Nope")

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
    resp = agents_audit_client.update_title(agent_key, conversation_id, title="Nope")

    assert resp.status_code == 400, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert error_of(resp)["code"] == code


def test_without_token_is_unauthorized(agents_audit_client: AgentsAuditClient) -> None:
    resp = agents_audit_client.update_title(SEED_AGENT_KEY, MISSING_CONVERSATION_ID, title="x", auth=False)

    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


def test_token_without_agent_write_scope_is_forbidden(
    agents_audit_client: AgentsAuditClient, narrow_scope_headers: dict[str, str]
) -> None:
    resp = agents_audit_client.update_title(
        SEED_AGENT_KEY, MISSING_CONVERSATION_ID, title="x", auth=False, headers=narrow_scope_headers
    )

    assert resp.status_code == 403, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
