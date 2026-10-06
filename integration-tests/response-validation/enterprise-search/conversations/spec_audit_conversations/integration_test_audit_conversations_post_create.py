"""Strict OpenAPI audit of POST /api/v1/conversations/create (non-streaming chat).

Every success runs a real LLM turn, so successes are few and share one conversation
where they can; refusals all happen in the validator, before any model is called.
"""

from __future__ import annotations

import uuid
from typing import Any, Callable

import pytest
import requests
from conversations_audit_support import (
    APPLIED_NODE,
    CHEAP_QUERY,
    CREATE_ROUTE,
    INVALID_AUTH_HEADERS,
    INVALID_TURN_FIELDS,
    LEGACY_CHAT_MODES,
    LLM_TIMEOUT_SECONDS,
    SOME_UUID,
    ConversationsAuditClient,
    UploadAttachment,
    turn_body,
    uploaded_record_ids,
    validation_fields,
)
from helper.pipeshub_client import PipeshubClient
from strict_openapi import assert_spec_forbids_request, assert_strict_openapi_exchange

pytestmark = pytest.mark.spec_audit

ROUTE = CREATE_ROUTE

VALID_BODY: dict[str, Any] = {"query": CHEAP_QUERY}


def _assert_created(resp: requests.Response) -> dict[str, Any]:
    assert resp.status_code == 201, resp.text[:500]
    body = resp.json()
    conversation = body["conversation"]
    assert resp.headers["X-Conversation-Id"] == conversation["_id"], resp.headers
    assert conversation["status"] == "Complete", conversation.get("status")
    types = [m["messageType"] for m in conversation["messages"]]
    assert types[0] == "user_query" and "bot_response" in types, types
    assert body["meta"]["duration"] >= 0
    return conversation


def test_create_with_only_a_query_answers_with_the_conversation(
    live_conversation: requests.Response,
) -> None:
    conversation = _assert_created(live_conversation)
    assert_strict_openapi_exchange(live_conversation, ROUTE)
    assert conversation["messages"][0]["content"] == CHEAP_QUERY


def test_create_with_every_optional_field(
    conversations_audit_client: ConversationsAuditClient,
    upload_attachment: UploadAttachment,
    delete_conversation_later: Callable[[str], None],
) -> None:
    uploaded = upload_attachment()
    assert uploaded.status_code == 200, uploaded.text[:500]
    attachment = uploaded.json()["attachments"][0]
    body = {
        "query": CHEAP_QUERY,
        # A legacy mode: the validator accepts it and the service runs internal search.
        "chatMode": "quick",
        "recordIds": ["0123456789abcdef01234567"],
        "filters": {"apps": [SOME_UUID], "kb": [SOME_UUID]},
        "appliedFilters": {"apps": [APPLIED_NODE], "kb": [APPLIED_NODE]},
        "attachments": [
            {
                "recordId": attachment["recordId"],
                "recordName": attachment["recordName"],
                "mimeType": attachment["mimeType"],
                "extension": attachment["extension"],
                "virtualRecordId": attachment["virtualRecordId"],
                "source": "upload",
            }
        ],
        "projectVisibility": "private",
        "reasoningEffort": "low",
        "timezone": "Europe/Berlin",
        "currentTime": "2026-10-06T09:30:00+02:00",
        "tools": ["spec_audit.no_such_tool"],
        "protocol": "agui",
        "agentCapabilities": {"internalSearch": True, "webSearch": False, "deepSearch": False},
        "runId": str(uuid.uuid4()),
    }
    resp = conversations_audit_client.create_conversation(json=body, timeout=LLM_TIMEOUT_SECONDS)
    if resp.headers.get("X-Conversation-Id"):
        delete_conversation_later(resp.headers["X-Conversation-Id"])

    conversation = _assert_created(resp)
    assert_strict_openapi_exchange(resp, ROUTE)
    question = conversation["messages"][0]
    assert question["appliedFilters"] == {"apps": [APPLIED_NODE], "kb": [APPLIED_NODE]}, question
    assert [a["recordId"] for a in question["attachments"]] == uploaded_record_ids(uploaded), question
    assert "projectId" not in conversation, "projectVisibility alone links no project"


@pytest.mark.parametrize("chat_mode", LEGACY_CHAT_MODES)
def test_validator_accepts_legacy_chat_modes(
    conversations_audit_client: ConversationsAuditClient, chat_mode: str
) -> None:
    # The empty query makes the validator refuse the request, so no model runs;
    # what matters is that it names only the query, not chatMode.
    resp = conversations_audit_client.create_conversation(json={"query": "", "chatMode": chat_mode})
    assert resp.status_code == 400, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert validation_fields(resp) == ["body.query"]


@pytest.mark.parametrize(
    ("fields", "named"),
    [pytest.param(fields, named, id=case) for case, fields, named in INVALID_TURN_FIELDS]
    + [
        pytest.param({"recordIds": ["not-an-object-id"]}, "body.recordIds.0", id="record-id-not-an-object-id"),
        pytest.param({"projectId": "not-an-object-id"}, "body.projectId", id="project-id-not-an-object-id"),
        pytest.param({"projectVisibility": "public"}, "body.projectVisibility", id="unknown-project-visibility"),
    ],
)
def test_create_rejects_invalid_body(
    conversations_audit_client: ConversationsAuditClient, fields: dict[str, Any], named: str
) -> None:
    resp = conversations_audit_client.create_conversation(json=turn_body(VALID_BODY, fields))
    assert resp.status_code == 400, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert named in validation_fields(resp), resp.text[:500]


@pytest.mark.parametrize(
    "query",
    [
        pytest.param("<script>alert(1)</script>", id="markup"),
        pytest.param("show %s of the files", id="format-specifier"),
    ],
)
def test_create_rejects_markup_or_format_specifiers_in_the_query(
    conversations_audit_client: ConversationsAuditClient, query: str
) -> None:
    resp = conversations_audit_client.create_conversation(json={"query": query})
    assert resp.status_code == 400, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert resp.json()["error"]["code"] in ("VALIDATION_ERROR", "HTTP_BAD_REQUEST"), resp.text[:500]


def test_create_without_body_is_rejected(conversations_audit_client: ConversationsAuditClient) -> None:
    resp = conversations_audit_client.post("/create")
    assert resp.status_code == 400, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert_spec_forbids_request(resp, ROUTE)


@pytest.mark.parametrize(
    "headers",
    [pytest.param(None, id="no_token"), pytest.param(INVALID_AUTH_HEADERS, id="invalid_token")],
)
def test_create_without_valid_token_is_unauthorized(
    conversations_audit_client: ConversationsAuditClient, headers: dict[str, str] | None
) -> None:
    kwargs: dict[str, Any] = {"headers": headers} if headers else {}
    resp = conversations_audit_client.create_conversation(json=VALID_BODY, auth=False, **kwargs)
    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


def test_create_with_token_lacking_write_scope_is_forbidden(
    pipeshub_client: PipeshubClient, narrow_scope_headers: dict[str, str]
) -> None:
    resp = requests.post(
        f"{pipeshub_client.base_url}{ROUTE}",
        headers=narrow_scope_headers,
        json=VALID_BODY,
        timeout=pipeshub_client.timeout_seconds,
    )
    assert resp.status_code == 403, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert resp.json()["error"]["code"] == "HTTP_FORBIDDEN", resp.text[:500]
