"""Strict OpenAPI audit of POST /api/v1/agents/:agentKey/conversations/:conversationId/cancel."""

from __future__ import annotations

import pytest
from agents_audit_support import (
    MISSING_CONVERSATION_ID,
    SEED_AGENT_KEY,
    UNSAFE_PATH_SEGMENT,
    AgentsAuditClient,
    SeedAgentConversation,
)
from strict_openapi import assert_strict_openapi_response

pytestmark = pytest.mark.spec_audit

ROUTE = "/api/v1/agents/:agentKey/conversations/:conversationId/cancel"


def test_cancel_unknown_run_reports_not_cancelled(
    agents_audit_client: AgentsAuditClient,
    seed_agent_conversation: SeedAgentConversation,
) -> None:
    conversation_id = seed_agent_conversation()

    # A runId no stream registered: the only success path that starts no LLM run.
    resp = agents_audit_client.cancel(SEED_AGENT_KEY, conversation_id)
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)
    assert resp.json() == {"cancelled": False}


def test_cancel_unknown_conversation_is_not_found(
    agents_audit_client: AgentsAuditClient,
) -> None:
    resp = agents_audit_client.cancel(SEED_AGENT_KEY, MISSING_CONVERSATION_ID)
    assert resp.status_code == 404, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)


def test_cancel_without_run_id_is_rejected(
    agents_audit_client: AgentsAuditClient,
) -> None:
    resp = agents_audit_client.cancel(SEED_AGENT_KEY, MISSING_CONVERSATION_ID, body={})
    assert resp.status_code == 400, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)


def test_cancel_unsafe_agent_key_is_rejected(
    agents_audit_client: AgentsAuditClient,
) -> None:
    resp = agents_audit_client.cancel(UNSAFE_PATH_SEGMENT, MISSING_CONVERSATION_ID)
    assert resp.status_code == 400, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)


def test_cancel_without_token_is_unauthorized(
    agents_audit_client: AgentsAuditClient,
) -> None:
    resp = agents_audit_client.cancel(
        SEED_AGENT_KEY, MISSING_CONVERSATION_ID, auth=False
    )
    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)
