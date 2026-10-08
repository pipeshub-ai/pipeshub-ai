"""Strict OpenAPI audit of POST /api/v1/agents/:agentKey/conversations (non-streaming)."""

from __future__ import annotations

import uuid
from typing import Any, Callable

import pytest
from agents_audit_support import (
    ANSWER_TIMEOUT,
    MISSING_PROJECT_ID,
    QUICK_TURN,
    SEED_AGENT_KEY,
    UNKNOWN_MODEL_KEY,
    UNSAFE_PATH_SEGMENT,
    AgentsAuditClient,
    UploadAttachment,
    error_of,
    refused_bodies,
    request_as,
)
from helper.second_user import SecondUser
from strict_openapi import (
    assert_spec_forbids_request,
    assert_strict_openapi_exchange,
    outside_request_contract,
)

pytestmark = pytest.mark.spec_audit

ROUTE = "/api/v1/agents/:agentKey/conversations"
CONVERSATION_ID_HEADER = "X-Conversation-Id"


def test_first_turn_returns_the_answered_conversation(
    agents_audit_client: AgentsAuditClient,
    audit_agent: str,
    upload_attachment: UploadAttachment,
    forget_conversation: Callable[[str | None], None],
) -> None:
    uploaded = upload_attachment(agent_key=audit_agent)
    assert uploaded.status_code == 200, uploaded.text[:500]
    attachment = uploaded.json()["attachments"][0]
    body: dict[str, Any] = {
        **QUICK_TURN,
        "reasoningEffort": "low",
        "timezone": "UTC",
        "currentTime": "2026-05-19T12:58:01+05:30",
        "runId": str(uuid.uuid4()),
        "tools": [],
        "filters": {"apps": [], "kb": []},
        "appliedFilters": {"apps": [], "kb": []},
        "agentCapabilities": {"webSearch": False, "internalSearch": False},
        "attachments": [
            {
                "recordId": attachment["recordId"],
                "recordName": attachment["recordName"],
                "mimeType": attachment["mimeType"],
                "source": "upload",
            }
        ],
    }

    resp = agents_audit_client.create_conversation(audit_agent, json=body, timeout=ANSWER_TIMEOUT)
    forget_conversation(resp.headers.get(CONVERSATION_ID_HEADER))

    assert resp.status_code == 201, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    conversation = resp.json()["conversation"]
    assert resp.headers[CONVERSATION_ID_HEADER] == conversation["_id"]
    assert conversation["agentKey"] == audit_agent
    assert [m["messageType"] for m in conversation["messages"]] == ["user_query", "bot_response"]
    assert conversation["messages"][0]["attachments"][0]["recordId"] == attachment["recordId"]


def test_unknown_model_key_falls_back_to_the_default_model(
    agents_audit_client: AgentsAuditClient,
    audit_agent: str,
    forget_conversation: Callable[[str | None], None],
) -> None:
    body = {**QUICK_TURN, "modelKey": UNKNOWN_MODEL_KEY}

    resp = agents_audit_client.create_conversation(audit_agent, json=body, timeout=ANSWER_TIMEOUT)
    forget_conversation(resp.headers.get(CONVERSATION_ID_HEADER))

    assert resp.status_code == 201, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    conversation = resp.json()["conversation"]
    assert conversation["status"] == "Complete", conversation.get("failReason")
    assert conversation["modelInfo"]["modelKey"] == UNKNOWN_MODEL_KEY
    assert conversation["messages"][-1]["messageType"] == "bot_response"

def test_unknown_agent_fails_the_turn_and_names_the_conversation(
    agents_audit_client: AgentsAuditClient,
    forget_conversation: Callable[[str | None], None],
) -> None:
    resp = agents_audit_client.create_conversation(
        f"spec-audit-missing-{uuid.uuid4().hex[:8]}", json=QUICK_TURN, timeout=ANSWER_TIMEOUT
    )
    forget_conversation(resp.headers.get(CONVERSATION_ID_HEADER))

    assert resp.status_code == 404, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert resp.headers.get(CONVERSATION_ID_HEADER), "a failed turn names the conversation it left"


def test_member_without_access_to_the_agent_fails_the_turn_as_not_found(
    audit_agent: str,
    second_user: SecondUser,
    forget_conversation: Callable[[str | None], None],
) -> None:
    resp = request_as(
        second_user, "POST", f"/{audit_agent}/conversations", json=QUICK_TURN, timeout=ANSWER_TIMEOUT
    )
    forget_conversation(resp.headers.get(CONVERSATION_ID_HEADER))

    assert resp.status_code == 404, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert error_of(resp)["code"] == "HTTP_NOT_FOUND", resp.text[:500]
    assert resp.headers.get(CONVERSATION_ID_HEADER), "a failed turn names the conversation it left"


def test_unknown_project_is_not_found(
    agents_audit_client: AgentsAuditClient,
    forget_conversation: Callable[[str | None], None],
) -> None:
    resp = agents_audit_client.create_conversation(
        SEED_AGENT_KEY, json={**QUICK_TURN, "projectId": MISSING_PROJECT_ID}
    )
    forget_conversation(resp.headers.get(CONVERSATION_ID_HEADER))

    assert resp.status_code == 404, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


@pytest.mark.parametrize("body", refused_bodies("create", QUICK_TURN))
def test_refused_body_is_a_validation_error(
    agents_audit_client: AgentsAuditClient, body: dict[str, Any]
) -> None:
    resp = agents_audit_client.create_conversation(SEED_AGENT_KEY, json=body)

    assert resp.status_code == 400, resp.text[:500]
    assert error_of(resp)["code"] == "VALIDATION_ERROR", resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


@pytest.mark.parametrize(
    "query",
    [
        pytest.param("<b>hello</b>", id="markup"),
        pytest.param("show %s please", id="format-specifier"),
    ],
)
def test_unsafe_query_is_refused_before_the_turn_starts(
    agents_audit_client: AgentsAuditClient,
    forget_conversation: Callable[[str | None], None],
    query: str,
) -> None:
    resp = agents_audit_client.create_conversation(SEED_AGENT_KEY, json={**QUICK_TURN, "query": query})
    forget_conversation(resp.headers.get(CONVERSATION_ID_HEADER))

    assert resp.status_code == 400, resp.text[:500]
    assert CONVERSATION_ID_HEADER not in resp.headers
    assert_strict_openapi_exchange(resp, ROUTE)
    assert_spec_forbids_request(resp, ROUTE)


def test_blank_query_is_refused_by_the_controller(
    agents_audit_client: AgentsAuditClient,
    forget_conversation: Callable[[str | None], None],
) -> None:
    resp = agents_audit_client.create_conversation(SEED_AGENT_KEY, json={**QUICK_TURN, "query": "   "})
    forget_conversation(resp.headers.get(CONVERSATION_ID_HEADER))

    assert resp.status_code == 400, resp.text[:500]
    assert CONVERSATION_ID_HEADER not in resp.headers
    assert_strict_openapi_exchange(resp, ROUTE)
    error = error_of(resp)
    assert (error["code"], error["message"]) == ("HTTP_BAD_REQUEST", "Query is required"), resp.text[:500]
    assert_spec_forbids_request(resp, ROUTE)


def test_unsafe_agent_key_is_rejected(agents_audit_client: AgentsAuditClient) -> None:
    resp = agents_audit_client.create_conversation(UNSAFE_PATH_SEGMENT, json=QUICK_TURN)

    assert resp.status_code == 400, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


def test_without_token_is_unauthorized(agents_audit_client: AgentsAuditClient) -> None:
    resp = agents_audit_client.create_conversation(SEED_AGENT_KEY, json=QUICK_TURN, auth=False)

    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


def test_token_without_agent_execute_scope_is_forbidden(
    agents_audit_client: AgentsAuditClient,
    narrow_scope_headers: dict[str, str],
) -> None:
    resp = agents_audit_client.create_conversation(
        SEED_AGENT_KEY, json=QUICK_TURN, auth=False, headers=narrow_scope_headers
    )

    assert resp.status_code == 403, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


def test_unknown_fields_are_stripped_not_refused(
    agents_audit_client: AgentsAuditClient,
) -> None:
    # Refused for a missing project after validation, so no LLM run starts; a 404
    # (not a 400) shows the extra field passed the validator.
    with outside_request_contract("an unknown field is sent to prove the validator strips it"):
        resp = agents_audit_client.create_conversation(
            SEED_AGENT_KEY,
            json={**QUICK_TURN, "projectId": MISSING_PROJECT_ID, "previousConversations": []},
        )
        assert_strict_openapi_exchange(resp, ROUTE)
    assert resp.status_code == 404, resp.text[:500]
