"""Strict OpenAPI audit of POST /api/v1/agents/:agentKey/conversations/:conversationId/message/:messageId/regenerate.

The gate does not read event streams, so each SSE test asserts the frames itself.
"""

from __future__ import annotations

from typing import Any

import pytest
from agents_audit_support import (
    ANSWER_TIMEOUT,
    MALFORMED_CONVERSATION_ID,
    MALFORMED_MESSAGE_ID,
    MISSING_CONVERSATION_ID,
    MISSING_MESSAGE_ID,
    SEED_AGENT_KEY,
    AgentsAuditClient,
    SeedAgentConversation,
    SeedMessage,
    error_of,
    refused_bodies,
    sse_events,
)
from helper.agui_sse import is_root_error, is_root_finished, run_finished_result
from strict_openapi import assert_strict_openapi_exchange

pytestmark = pytest.mark.spec_audit

ROUTE = "/api/v1/agents/:agentKey/conversations/:conversationId/message/:messageId/regenerate"

QUICK = {"chatMode": "quick"}


def _regenerate(
    client: AgentsAuditClient,
    agent_key: str,
    conversation_id: str,
    message_id: str,
    body: dict[str, Any],
    **kwargs: Any,
):
    # Read whole (stream=False): the gate still sees the request and the status.
    return client.regenerate_message(
        agent_key, conversation_id, message_id, json=body, stream=False,
        timeout=ANSWER_TIMEOUT, **kwargs,
    )


def test_regenerating_the_last_answer_streams_a_finished_run(
    agents_audit_client: AgentsAuditClient,
    audit_agent: str,
    seed_agent_conversation: SeedAgentConversation,
    seed_message: SeedMessage,
) -> None:
    conversation_id = seed_agent_conversation(agent_key=audit_agent)
    seed_message(conversation_id, "user_query", "Reply with the single word OK.")
    bot_id = seed_message(conversation_id, "bot_response", "OK")

    resp = _regenerate(
        agents_audit_client, audit_agent, conversation_id, bot_id,
        {**QUICK, "reasoningEffort": "low", "timezone": "UTC"},
    )

    assert resp.status_code == 200, resp.text[:500]
    assert resp.headers["Content-Type"].startswith("text/event-stream")
    assert_strict_openapi_exchange(resp, ROUTE)
    events = sse_events(resp)
    errors = [p for e, p in events if is_root_error(e, p)]
    assert not errors, errors
    finished = [run_finished_result(p) for e, p in events if is_root_finished(e, p)]
    assert len(finished) == 1, [e for e, _ in events][-10:]
    assert finished[0]["conversation"]["_id"] == conversation_id


@pytest.mark.parametrize("target", ["user-query", "missing-message", "missing-conversation"])
def test_lookup_and_rule_failures_stream_a_run_error(
    agents_audit_client: AgentsAuditClient,
    seed_agent_conversation: SeedAgentConversation,
    seed_message: SeedMessage,
    target: str,
) -> None:
    conversation_id = seed_agent_conversation()
    user_id = seed_message(conversation_id, "user_query", "Hello?")
    seed_message(conversation_id, "bot_response", "Hello.")
    if target == "missing-conversation":
        conversation_id = MISSING_CONVERSATION_ID
    message_id = user_id if target == "user-query" else MISSING_MESSAGE_ID

    resp = _regenerate(agents_audit_client, SEED_AGENT_KEY, conversation_id, message_id, QUICK)

    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    events = sse_events(resp)
    assert any(is_root_error(e, p) for e, p in events), resp.text[:500]
    assert not any(is_root_finished(e, p) for e, p in events)


@pytest.mark.parametrize(
    "body",
    [pytest.param({}, id="missing-chat-mode"), *refused_bodies("regenerate", QUICK)],
)
def test_refused_body_is_a_validation_error(
    agents_audit_client: AgentsAuditClient, body: dict[str, Any]
) -> None:
    resp = _regenerate(
        agents_audit_client, SEED_AGENT_KEY, MISSING_CONVERSATION_ID, MISSING_MESSAGE_ID, body
    )

    assert resp.status_code == 400, resp.text[:500]
    assert error_of(resp)["code"] == "VALIDATION_ERROR", resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


@pytest.mark.parametrize(
    ("conversation_id", "message_id"),
    [
        pytest.param(MALFORMED_CONVERSATION_ID, MISSING_MESSAGE_ID, id="malformed-conversation-id"),
        pytest.param(MISSING_CONVERSATION_ID, MALFORMED_MESSAGE_ID, id="malformed-message-id"),
    ],
)
def test_malformed_path_id_is_a_validation_error(
    agents_audit_client: AgentsAuditClient, conversation_id: str, message_id: str
) -> None:
    resp = _regenerate(agents_audit_client, SEED_AGENT_KEY, conversation_id, message_id, QUICK)

    assert resp.status_code == 400, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


def test_without_token_is_unauthorized(agents_audit_client: AgentsAuditClient) -> None:
    resp = _regenerate(
        agents_audit_client, SEED_AGENT_KEY, MISSING_CONVERSATION_ID, MISSING_MESSAGE_ID, QUICK,
        auth=False,
    )

    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


def test_token_without_agent_execute_scope_is_forbidden(
    agents_audit_client: AgentsAuditClient,
    narrow_scope_headers: dict[str, str],
) -> None:
    resp = _regenerate(
        agents_audit_client, SEED_AGENT_KEY, MISSING_CONVERSATION_ID, MISSING_MESSAGE_ID, QUICK,
        auth=False, headers=narrow_scope_headers,
    )

    assert resp.status_code == 403, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
