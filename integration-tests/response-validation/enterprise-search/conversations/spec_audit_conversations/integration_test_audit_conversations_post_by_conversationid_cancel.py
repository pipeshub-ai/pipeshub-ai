"""Strict OpenAPI audit of POST /api/v1/conversations/:conversationId/cancel."""

from __future__ import annotations

import threading
import time
import uuid
from typing import Any

import pytest
import requests
from bson import ObjectId
from conversations_audit_support import (
    CANCEL_ROUTE,
    INVALID_AUTH_HEADERS,
    LLM_TIMEOUT_SECONDS,
    MALFORMED_CONVERSATION_ID,
    MALFORMED_RUN_ID,
    MISSING_CONVERSATION_ID,
    ConversationsAuditClient,
    SeedConversation,
    StreamRun,
    error_of,
    request_as,
    validation_fields,
)
from helper.pipeshub_client import PipeshubClient
from helper.second_user import SecondUser
from pymongo.collection import Collection
from strict_openapi import assert_strict_openapi_exchange

pytestmark = pytest.mark.spec_audit

ROUTE = CANCEL_ROUTE
# Long enough an answer that the run is still generating when it is cancelled.
LONG_QUERY = "Write every whole number from 1 to 2000 in words, separated by commas. Do not stop early."
REGISTRATION_TIMEOUT_SECONDS = 60


def test_cancel_unknown_run_reports_cancelled(
    conversations_audit_client: ConversationsAuditClient,
    seed_conversation: SeedConversation,
) -> None:
    # API bug: the KV-backed registry publishes the cancel and acks it even though no run exists.
    resp = conversations_audit_client.cancel_stream(seed_conversation(), str(uuid.uuid4()))

    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert resp.json() == {"cancelled": True}


def test_cancel_stops_a_running_stream_and_refuses_another_conversation(
    conversations_audit_client: ConversationsAuditClient,
    seed_conversation: SeedConversation,
    chat_sessions_collection: Collection,
) -> None:
    conversation_id = seed_conversation()
    other_conversation_id = seed_conversation()
    run_id = str(uuid.uuid4())
    outcome: dict[str, requests.Response] = {}

    def run_stream() -> None:
        outcome["stream"] = conversations_audit_client.stream_message(
            conversation_id,
            json={"query": LONG_QUERY, "chatMode": "internal_search", "runId": run_id},
            stream=False,
            timeout=LLM_TIMEOUT_SECONDS,
        )

    worker = threading.Thread(target=run_stream)
    worker.start()
    try:
        # Until the run registers, any cancel is published and acked; once it does, a
        # cancel naming another conversation of the same owner is refused.
        deadline = time.monotonic() + REGISTRATION_TIMEOUT_SECONDS
        refused = conversations_audit_client.cancel_stream(other_conversation_id, run_id)
        while refused.status_code == 200 and time.monotonic() < deadline:
            time.sleep(0.5)
            refused = conversations_audit_client.cancel_stream(other_conversation_id, run_id)
        error = error_of(refused, 403)
        assert (error["code"], error["message"]) == ("HTTP_FORBIDDEN", "You do not own this run")
        assert_strict_openapi_exchange(refused, ROUTE)

        resp = conversations_audit_client.cancel_stream(conversation_id, run_id)
        assert resp.status_code == 200, resp.text[:500]
        assert_strict_openapi_exchange(resp, ROUTE)
        assert resp.json() == {"cancelled": True}
    finally:
        worker.join(LLM_TIMEOUT_SECONDS)
    run = StreamRun(outcome["stream"], "ConversationMessageStreamSSEEvent")
    assert run.result is not None, run.names[-5:]
    assert run.result["conversation"]["status"] == "Stopped"
    stored = chat_sessions_collection.find_one({"_id": ObjectId(conversation_id)})
    assert stored is not None and stored["status"] == "Stopped"


@pytest.mark.parametrize(
    "headers",
    [pytest.param(None, id="no_token"), pytest.param(INVALID_AUTH_HEADERS, id="invalid_token")],
)
def test_cancel_without_valid_token_is_unauthorized(
    conversations_audit_client: ConversationsAuditClient, headers: dict[str, str] | None
) -> None:
    kwargs: dict[str, Any] = {"headers": headers} if headers else {}
    resp = conversations_audit_client.cancel_stream(MISSING_CONVERSATION_ID, auth=False, **kwargs)

    assert error_of(resp, 401)["code"] == "HTTP_UNAUTHORIZED"
    assert_strict_openapi_exchange(resp, ROUTE)


def test_cancel_with_token_lacking_chat_scope_is_forbidden(
    pipeshub_client: PipeshubClient, narrow_scope_headers: dict[str, str]
) -> None:
    resp = requests.post(
        f"{pipeshub_client.base_url}/api/v1/conversations/{MISSING_CONVERSATION_ID}/cancel",
        headers=narrow_scope_headers,
        json={"runId": str(uuid.uuid4())},
        timeout=pipeshub_client.timeout_seconds,
    )

    assert error_of(resp, 403)["code"] == "HTTP_FORBIDDEN"
    assert_strict_openapi_exchange(resp, ROUTE)


@pytest.mark.parametrize(
    ("conversation_id", "body", "field"),
    [
        pytest.param(MISSING_CONVERSATION_ID, {"runId": MALFORMED_RUN_ID}, "body.runId", id="malformed-run-id"),
        pytest.param(MISSING_CONVERSATION_ID, {}, "body.runId", id="run-id-missing"),
        pytest.param(
            MALFORMED_CONVERSATION_ID, {"runId": str(uuid.uuid4())}, "params.conversationId", id="malformed-conversation-id"
        ),
    ],
)
def test_cancel_rejects_invalid_input(
    conversations_audit_client: ConversationsAuditClient,
    conversation_id: str,
    body: dict[str, Any],
    field: str,
) -> None:
    resp = conversations_audit_client.cancel_stream(conversation_id, json=body)

    assert validation_fields(resp) == [field]
    assert_strict_openapi_exchange(resp, ROUTE)


@pytest.mark.parametrize("case", ["missing", "another-users", "agent-conversation", "deleted"])
def test_cancel_on_a_conversation_the_caller_does_not_own_is_not_found(
    conversations_audit_client: ConversationsAuditClient,
    second_user: SecondUser,
    seed_conversation: SeedConversation,
    case: str,
) -> None:
    body = {"runId": str(uuid.uuid4())}
    if case == "missing":
        resp = conversations_audit_client.cancel_stream(MISSING_CONVERSATION_ID, json=body)
    elif case == "another-users":
        resp = request_as(second_user, "POST", f"/{seed_conversation()}/cancel", json=body)
    else:
        fields: dict[str, Any] = (
            {"isDeleted": True} if case == "deleted" else {"sessionType": "agent", "agentKey": "spec-audit-agent"}
        )
        resp = conversations_audit_client.cancel_stream(seed_conversation(**fields), json=body)

    error = error_of(resp, 404)
    assert (error["code"], error["message"]) == ("HTTP_NOT_FOUND", "Conversation not found or unauthorized")
    assert_strict_openapi_exchange(resp, ROUTE)
