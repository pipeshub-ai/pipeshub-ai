"""Strict OpenAPI audit of POST /api/v1/agents/:agentKey/conversations/stream.

The gate does not read event streams, so each SSE test asserts the frames itself.
"""

from __future__ import annotations

import uuid
from typing import Any, Callable

import pytest
from agents_audit_support import (
    ANSWER_TIMEOUT,
    QUICK_TURN,
    SEED_AGENT_KEY,
    UNSAFE_PATH_SEGMENT,
    AgentsAuditClient,
    error_of,
    refused_bodies,
    sse_events,
)
from helper.agui_sse import (
    conversation_created_value,
    is_conversation_created,
    is_root_error,
    is_root_finished,
    run_finished_result,
)
from strict_openapi import assert_strict_openapi_exchange

pytestmark = pytest.mark.spec_audit

ROUTE = "/api/v1/agents/:agentKey/conversations/stream"


def _stream(client: AgentsAuditClient, agent_key: str, body: dict[str, Any], **kwargs: Any):
    # Read whole (stream=False): the gate still sees the request and the status.
    return client.stream_conversation(
        agent_key, json=body, stream=False, timeout=ANSWER_TIMEOUT, **kwargs
    )


def _created_id(events: list[tuple[str, dict[str, Any]]]) -> str | None:
    created = [conversation_created_value(p) for _, p in events if is_conversation_created(p)]
    return created[0].get("conversationId") if created else None


def test_stream_creates_the_conversation_and_finishes(
    agents_audit_client: AgentsAuditClient,
    audit_agent: str,
    forget_conversation: Callable[[str | None], None],
) -> None:
    resp = _stream(agents_audit_client, audit_agent, {**QUICK_TURN, "runId": str(uuid.uuid4())})
    events = sse_events(resp) if resp.status_code == 200 else []
    conversation_id = _created_id(events)
    forget_conversation(conversation_id)

    assert resp.status_code == 200, resp.text[:500]
    assert resp.headers["Content-Type"].startswith("text/event-stream")
    assert_strict_openapi_exchange(resp, ROUTE)
    assert conversation_id, [e for e, _ in events][:10]
    errors = [p for e, p in events if is_root_error(e, p)]
    assert not errors, errors
    finished = [run_finished_result(p) for e, p in events if is_root_finished(e, p)]
    assert len(finished) == 1, [e for e, _ in events][-10:]
    conversation = finished[0]["conversation"]
    assert conversation["_id"] == conversation_id
    assert conversation["messages"][-1]["messageType"] == "bot_response"


def test_blank_query_is_accepted_and_creates_the_conversation(
    agents_audit_client: AgentsAuditClient,
    audit_agent: str,
    forget_conversation: Callable[[str | None], None],
) -> None:
    resp = _stream(agents_audit_client, audit_agent, {**QUICK_TURN, "query": "   "})
    events = sse_events(resp) if resp.status_code == 200 else []
    conversation_id = _created_id(events)
    forget_conversation(conversation_id)

    assert resp.status_code == 200, resp.text[:500]
    assert resp.headers["Content-Type"].startswith("text/event-stream")
    assert_strict_openapi_exchange(resp, ROUTE)
    assert conversation_id, [e for e, _ in events][:10]


def test_unknown_agent_streams_a_run_error(
    agents_audit_client: AgentsAuditClient,
    forget_conversation: Callable[[str | None], None],
) -> None:
    resp = _stream(agents_audit_client, f"spec-audit-missing-{uuid.uuid4().hex[:8]}", QUICK_TURN)
    events = sse_events(resp) if resp.status_code == 200 else []
    forget_conversation(_created_id(events))

    # The stream opens before the agent is looked up, so the failure is a frame.
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert any(is_root_error(e, p) for e, p in events), [e for e, _ in events]
    assert not any(is_root_finished(e, p) for e, p in events)


@pytest.mark.parametrize(
    "body",
    [
        pytest.param({"query": QUICK_TURN["query"]}, id="missing-chat-mode"),
        *refused_bodies("create", QUICK_TURN),
    ],
)
def test_refused_body_is_a_validation_error(
    agents_audit_client: AgentsAuditClient, body: dict[str, Any]
) -> None:
    resp = _stream(agents_audit_client, SEED_AGENT_KEY, body)

    assert resp.status_code == 400, resp.text[:500]
    assert error_of(resp)["code"] == "VALIDATION_ERROR", resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


def test_unsafe_agent_key_is_rejected(agents_audit_client: AgentsAuditClient) -> None:
    resp = _stream(agents_audit_client, UNSAFE_PATH_SEGMENT, QUICK_TURN)

    assert resp.status_code == 400, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


def test_without_token_is_unauthorized(agents_audit_client: AgentsAuditClient) -> None:
    resp = _stream(agents_audit_client, SEED_AGENT_KEY, QUICK_TURN, auth=False)

    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


def test_token_without_agent_execute_scope_is_forbidden(
    agents_audit_client: AgentsAuditClient,
    narrow_scope_headers: dict[str, str],
) -> None:
    resp = _stream(
        agents_audit_client, SEED_AGENT_KEY, QUICK_TURN, auth=False, headers=narrow_scope_headers
    )

    assert resp.status_code == 403, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
