"""Strict OpenAPI audit of POST /api/v1/agents/:agentKey/conversations/:conversationId/cancel."""

from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any

import pytest
from agents_audit_support import (
    ANSWER_TIMEOUT,
    MALFORMED_CONVERSATION_ID,
    MISSING_CONVERSATION_ID,
    OTHER_AGENT_KEY,
    SEED_AGENT_KEY,
    UNSAFE_PATH_SEGMENT,
    AgentsAuditClient,
    SeedAgentConversation,
    new_run_id,
    sse_events,
)
from helper.agui_sse import is_root_error, is_root_finished, run_finished_result
from strict_openapi import assert_strict_openapi_exchange

pytestmark = pytest.mark.spec_audit

ROUTE = "/api/v1/agents/:agentKey/conversations/:conversationId/cancel"


def test_cancel_unknown_run_is_acknowledged(
    agents_audit_client: AgentsAuditClient,
    seed_agent_conversation: SeedAgentConversation,
) -> None:
    conversation_id = seed_agent_conversation()

    # A runId no stream registered: the only success path that starts no LLM run.
    resp = agents_audit_client.cancel(SEED_AGENT_KEY, conversation_id)
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    # The KV-backed registry publishes the stop request for a runId it does not
    # hold and answers true; only the in-process registry answers false.
    body = resp.json()
    assert set(body) == {"cancelled"}, body
    assert isinstance(body["cancelled"], bool), body


def test_cancel_unknown_conversation_is_not_found(
    agents_audit_client: AgentsAuditClient,
) -> None:
    resp = agents_audit_client.cancel(SEED_AGENT_KEY, MISSING_CONVERSATION_ID)
    assert resp.status_code == 404, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


def test_cancel_without_run_id_is_rejected(
    agents_audit_client: AgentsAuditClient,
) -> None:
    resp = agents_audit_client.cancel(SEED_AGENT_KEY, MISSING_CONVERSATION_ID, body={})
    assert resp.status_code == 400, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


def test_cancel_unsafe_agent_key_is_rejected(
    agents_audit_client: AgentsAuditClient,
) -> None:
    resp = agents_audit_client.cancel(UNSAFE_PATH_SEGMENT, MISSING_CONVERSATION_ID)
    assert resp.status_code == 400, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


def test_cancel_without_token_is_unauthorized(
    agents_audit_client: AgentsAuditClient,
) -> None:
    resp = agents_audit_client.cancel(
        SEED_AGENT_KEY, MISSING_CONVERSATION_ID, auth=False
    )
    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


@pytest.mark.parametrize(
    ("conversation_id", "body"),
    [
        pytest.param(MISSING_CONVERSATION_ID, {"runId": "not-a-uuid"}, id="run-id-not-uuid"),
        pytest.param(MISSING_CONVERSATION_ID, {"runId": 42}, id="run-id-not-string"),
        pytest.param(MALFORMED_CONVERSATION_ID, None, id="malformed-conversation-id"),
    ],
)
def test_cancel_refused_request_is_a_validation_error(
    agents_audit_client: AgentsAuditClient,
    conversation_id: str,
    body: dict[str, Any] | None,
) -> None:
    resp = agents_audit_client.cancel(SEED_AGENT_KEY, conversation_id, body=body)
    assert resp.status_code == 400, resp.text[:500]
    assert resp.json()["error"]["code"] == "VALIDATION_ERROR", resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


def test_cancel_through_another_agent_is_not_found(
    agents_audit_client: AgentsAuditClient,
    seed_agent_conversation: SeedAgentConversation,
) -> None:
    conversation_id = seed_agent_conversation()

    resp = agents_audit_client.cancel(OTHER_AGENT_KEY, conversation_id)
    assert resp.status_code == 404, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


def test_cancel_without_agent_execute_scope_is_forbidden(
    agents_audit_client: AgentsAuditClient,
    narrow_scope_headers: dict[str, str],
) -> None:
    resp = agents_audit_client.cancel(
        SEED_AGENT_KEY, MISSING_CONVERSATION_ID, auth=False, headers=narrow_scope_headers
    )
    assert resp.status_code == 403, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)



LONG_TURN = {"query": "Count from 1 to 800, one number per line, nothing else.", "chatMode": "quick"}


def test_cancel_through_its_own_conversation_stops_the_run(
    agents_audit_client: AgentsAuditClient,
    audit_agent: str,
    seed_agent_conversation: SeedAgentConversation,
) -> None:
    conversation_id = seed_agent_conversation(agent_key=audit_agent)
    run_id = new_run_id()
    with ThreadPoolExecutor(max_workers=1) as pool:
        streamed = pool.submit(
            agents_audit_client.stream_message,
            audit_agent,
            conversation_id,
            json={**LONG_TURN, "runId": run_id},
            stream=False,
            timeout=ANSWER_TIMEOUT,
        )
        time.sleep(3)
        assert not streamed.done(), "the run ended before it could be cancelled"
        resp = agents_audit_client.cancel(audit_agent, conversation_id, body={"runId": run_id})
        stream_resp = streamed.result()

    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert resp.json() == {"cancelled": True}
    assert stream_resp.status_code == 200, stream_resp.text[:500]
    events = sse_events(stream_resp)
    assert not any(is_root_error(e, p) for e, p in events), [e for e, _ in events][-10:]
    finished = [run_finished_result(p) for e, p in events if is_root_finished(e, p)]
    assert len(finished) == 1, [e for e, _ in events][-10:]
    assert finished[0]["conversation"]["status"] == "Stopped"


def test_cancel_through_another_conversation_of_the_caller_is_forbidden(
    agents_audit_client: AgentsAuditClient,
    audit_agent: str,
    seed_agent_conversation: SeedAgentConversation,
) -> None:
    running_id = seed_agent_conversation(agent_key=audit_agent)
    other_id = seed_agent_conversation(agent_key=audit_agent)
    run_id = new_run_id()
    with ThreadPoolExecutor(max_workers=1) as pool:
        streamed = pool.submit(
            agents_audit_client.stream_message,
            audit_agent,
            running_id,
            json={**LONG_TURN, "runId": run_id},
            stream=False,
            timeout=ANSWER_TIMEOUT,
        )
        # Until the run registers, the runId is unknown here and the cancel is
        # only published (200); once it is registered the mismatch answers 403.
        resp = agents_audit_client.cancel(audit_agent, other_id, body={"runId": run_id})
        while resp.status_code == 200 and not streamed.done():
            time.sleep(0.5)
            resp = agents_audit_client.cancel(audit_agent, other_id, body={"runId": run_id})
        agents_audit_client.cancel(audit_agent, running_id, body={"runId": run_id})
        streamed.result()

    assert resp.status_code == 403, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
