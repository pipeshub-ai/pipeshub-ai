"""Strict OpenAPI audit of POST /api/v1/agents/:agentKey/conversations/:conversationId/messages/stream.

The gate does not read event streams, so each SSE test asserts the frames itself.
"""

from __future__ import annotations

from typing import Any

import pytest
from agents_audit_support import (
    ANSWER_TIMEOUT,
    MALFORMED_CONVERSATION_ID,
    MISSING_CONVERSATION_ID,
    OTHER_AGENT_KEY,
    QUICK_TURN,
    SEED_AGENT_KEY,
    AgentsAuditClient,
    SeedAgentConversation,
    SeedMessage,
    error_of,
    refused_bodies,
    sse_events,
)
from helper.agui_sse import is_root_error, is_root_finished, run_error_message, run_finished_result
from strict_openapi import assert_strict_openapi_exchange

pytestmark = pytest.mark.spec_audit

ROUTE = "/api/v1/agents/:agentKey/conversations/:conversationId/messages/stream"


def _stream(
    client: AgentsAuditClient, agent_key: str, conversation_id: str, body: dict[str, Any], **kwargs: Any
):
    # Read whole (stream=False): the gate still sees the request and the status.
    return client.stream_message(
        agent_key, conversation_id, json=body, stream=False, timeout=ANSWER_TIMEOUT, **kwargs
    )


def test_follow_up_streams_to_a_finished_run(
    agents_audit_client: AgentsAuditClient,
    audit_agent: str,
    seed_agent_conversation: SeedAgentConversation,
    seed_message: SeedMessage,
) -> None:
    conversation_id = seed_agent_conversation(agent_key=audit_agent)
    seed_message(conversation_id, "user_query", "Say hello.")
    seed_message(conversation_id, "bot_response", "Hello.")

    resp = _stream(agents_audit_client, audit_agent, conversation_id, QUICK_TURN)

    assert resp.status_code == 200, resp.text[:500]
    assert resp.headers["Content-Type"].startswith("text/event-stream")
    assert_strict_openapi_exchange(resp, ROUTE)
    events = sse_events(resp)
    errors = [p for e, p in events if is_root_error(e, p)]
    assert not errors, errors
    finished = [run_finished_result(p) for e, p in events if is_root_finished(e, p)]
    assert len(finished) == 1, [e for e, _ in events][-10:]
    conversation = finished[0]["conversation"]
    assert conversation["_id"] == conversation_id
    assert [m["messageType"] for m in conversation["messages"]][-2:] == ["user_query", "bot_response"]


@pytest.mark.parametrize(
    ("agent_key", "conversation_id"),
    [
        pytest.param(SEED_AGENT_KEY, MISSING_CONVERSATION_ID, id="missing-conversation"),
        pytest.param(OTHER_AGENT_KEY, None, id="other-agents-conversation"),
    ],
)
def test_conversation_out_of_reach_streams_a_run_error(
    agents_audit_client: AgentsAuditClient,
    seed_agent_conversation: SeedAgentConversation,
    agent_key: str,
    conversation_id: str | None,
) -> None:
    target = conversation_id or seed_agent_conversation(agent_key=SEED_AGENT_KEY)

    resp = _stream(agents_audit_client, agent_key, target, QUICK_TURN)

    # The SSE headers go out before the lookup, so "not found" arrives as a frame.
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    errors = [p for e, p in sse_events(resp) if is_root_error(e, p)]
    assert len(errors) == 1, resp.text[:500]
    assert errors[0].get("code") == "internal_error", errors
    assert run_error_message(errors[0]), errors


@pytest.mark.parametrize(
    "body",
    [
        pytest.param({"query": QUICK_TURN["query"]}, id="missing-chat-mode"),
        *refused_bodies("message", QUICK_TURN),
    ],
)
def test_refused_body_is_a_validation_error(
    agents_audit_client: AgentsAuditClient, body: dict[str, Any]
) -> None:
    resp = _stream(agents_audit_client, SEED_AGENT_KEY, MISSING_CONVERSATION_ID, body)

    assert resp.status_code == 400, resp.text[:500]
    assert error_of(resp)["code"] == "VALIDATION_ERROR", resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


def test_malformed_conversation_id_is_a_validation_error(
    agents_audit_client: AgentsAuditClient,
) -> None:
    resp = _stream(agents_audit_client, SEED_AGENT_KEY, MALFORMED_CONVERSATION_ID, QUICK_TURN)

    assert resp.status_code == 400, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


def test_without_token_is_unauthorized(agents_audit_client: AgentsAuditClient) -> None:
    resp = _stream(agents_audit_client, SEED_AGENT_KEY, MISSING_CONVERSATION_ID, QUICK_TURN, auth=False)

    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


def test_token_without_agent_execute_scope_is_forbidden(
    agents_audit_client: AgentsAuditClient,
    narrow_scope_headers: dict[str, str],
) -> None:
    resp = _stream(
        agents_audit_client, SEED_AGENT_KEY, MISSING_CONVERSATION_ID, QUICK_TURN,
        auth=False, headers=narrow_scope_headers,
    )

    assert resp.status_code == 403, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
