"""Strict OpenAPI audit of POST /api/v1/conversations/:conversationId/cancel."""

from __future__ import annotations

import uuid

import pytest
from conversations_audit_support import (
    MALFORMED_CONVERSATION_ID,
    MALFORMED_RUN_ID,
    MISSING_CONVERSATION_ID,
    ConversationsAuditClient,
    SeedConversation,
    request_as,
)
from helper.second_user import SecondUser
from strict_openapi import assert_strict_openapi_response

pytestmark = pytest.mark.spec_audit

ROUTE = "/api/v1/conversations/:conversationId/cancel"


@pytest.mark.xfail(
    strict=True,
    reason="API bug: the KV-backed run registry acks a never-registered runId as cancelled: true",
)
def test_cancel_unknown_run_reports_not_cancelled(
    conversations_audit_client: ConversationsAuditClient,
    seed_conversation: SeedConversation,
) -> None:
    conversation_id = seed_conversation()

    # A never-registered runId is the only success path that stops nothing.
    resp = conversations_audit_client.cancel_stream(conversation_id, str(uuid.uuid4()))

    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)
    assert resp.json() == {"cancelled": False}


def test_cancel_without_token_is_unauthorized(
    conversations_audit_client: ConversationsAuditClient,
) -> None:
    resp = conversations_audit_client.cancel_stream(MISSING_CONVERSATION_ID, auth=False)

    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)


@pytest.mark.parametrize(
    ("conversation_id", "run_id"),
    [
        pytest.param(MISSING_CONVERSATION_ID, MALFORMED_RUN_ID, id="malformed-run-id"),
        pytest.param(MALFORMED_CONVERSATION_ID, str(uuid.uuid4()), id="malformed-conversation-id"),
    ],
)
def test_cancel_rejects_invalid_input(
    conversations_audit_client: ConversationsAuditClient,
    conversation_id: str,
    run_id: str,
) -> None:
    resp = conversations_audit_client.cancel_stream(conversation_id, run_id)

    assert resp.status_code == 400, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)


def test_cancel_on_another_users_conversation_is_not_found(
    second_user: SecondUser,
    seed_conversation: SeedConversation,
) -> None:
    # The handler filters on the caller's userId, so an existing id owned by the admin 404s.
    conversation_id = seed_conversation()

    resp = request_as(
        second_user, "POST", f"/{conversation_id}/cancel", json={"runId": str(uuid.uuid4())}
    )

    assert resp.status_code == 404, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)
