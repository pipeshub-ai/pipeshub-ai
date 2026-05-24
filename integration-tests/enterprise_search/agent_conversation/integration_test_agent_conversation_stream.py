"""Agent conversation stream integration tests.

``POST /api/v1/agents/{agentKey}/conversations/stream``

Requires ``session_kb`` (indexed Asana DR PDF) and ``agent_session`` fixtures from
the parent ``enterprise_search/conftest.py``.

``PIPESHUB_TEST_STREAM_TIMEOUT`` (optional): override seconds for SSE reads;
otherwise ``max(PIPESHUB_TEST_TIMEOUT, 120)``.
"""

from __future__ import annotations

import json
import os
import random
import uuid
from typing import Any

import pytest
import requests

from pipeshub_client import PipeshubClient

SEARCH_QUERY = "every year asana undertakes which exercise?"

# Question -> keywords that should appear in a KB-grounded answer.
_KB_QA_POOL: list[tuple[str, list[str]]] = [
    (SEARCH_QUERY, ["disaster recovery"]),
    (
        "What disaster recovery exercise does Asana perform annually?",
        ["disaster recovery"],
    ),
]

_SSE_MAX_EVENTS = 10_000


def _iter_sse_envelopes(resp: requests.Response, *, max_events: int = _SSE_MAX_EVENTS):
    """
    Minimal SSE parser for frames like:

      event: <name>
      data: <payload>

    Frames are separated by a blank line. Returns envelopes:
    { "event": <name>, "data": <string> }.
    """
    event_name: str | None = None
    data_lines: list[str] = []

    def flush():
        nonlocal event_name, data_lines
        if event_name is None:
            return None
        env = {"event": event_name, "data": "\n".join(data_lines)}
        event_name = None
        data_lines = []
        return env

    emitted = 0
    for raw in resp.iter_lines(decode_unicode=True):
        if raw is None:
            continue
        line = raw.rstrip("\r")
        if line == "":
            env = flush()
            if env is not None:
                yield env
                emitted += 1
                if emitted >= max_events:
                    raise AssertionError(f"SSE exceeded max_events={max_events}")
            continue

        if line.startswith(":"):
            continue
        if line.startswith("event:"):
            event_name = line[len("event:") :].strip()
            continue
        if line.startswith("data:"):
            data_lines.append(line[len("data:") :].lstrip())
            continue

    env = flush()
    if env is not None:
        yield env


def _answer_from_complete_payload(payload: dict) -> str:
    conv = payload.get("conversation") or {}
    msgs = conv.get("messages") or []
    for m in reversed(msgs if isinstance(msgs, list) else []):
        if not isinstance(m, dict):
            continue
        if m.get("messageType") == "bot_response":
            content = m.get("content")
            if isinstance(content, str) and content.strip():
                return content
        if m.get("role") == "assistant":
            content = m.get("content")
            if isinstance(content, str) and content.strip():
                return content
    answer = payload.get("answer")
    if isinstance(answer, str) and answer.strip():
        return answer
    return ""


@pytest.mark.integration
class TestAgentConversationStream:

    @pytest.fixture(autouse=True)
    def _setup(
        self,
        pipeshub_client: PipeshubClient,
        session_kb: dict,
        agent_session: dict[str, Any],
    ) -> None:
        self.base_url = pipeshub_client.base_url
        self.kb_id = session_kb["kb_id"]
        self.agent_session = agent_session
        self.headers = pipeshub_client.auth_headers
        self.client = pipeshub_client
        self.timeout = int(os.getenv("PIPESHUB_TEST_TIMEOUT", "60"))
        stream_override = os.getenv("PIPESHUB_TEST_STREAM_TIMEOUT", "").strip()
        self.stream_timeout = (
            int(stream_override)
            if stream_override
            else max(self.timeout, 120)
        )

    def _agent_stream_url(self, agent_key: str) -> str:
        return f"{self.base_url}/api/v1/agents/{agent_key}/conversations/stream"

    def _stream_agent_conversation(
        self,
        agent_key: str,
        *,
        query: str,
        headers: dict | None = None,
        allow_error: bool = False,
    ) -> tuple[str | None, str, bool]:
        """Stream a new agent conversation.

        Returns ``(conversation_id, accumulated_answer, saw_complete)``.
        When ``allow_error`` is True, error SSE events do not raise (for negative tests).
        """
        req_headers = {**(headers or self.headers), "Accept": "text/event-stream"}
        connected_conv_id: str | None = None
        accumulated_answer = ""
        saw_complete = False
        saw_error = False

        with requests.post(
            self._agent_stream_url(agent_key),
            headers=req_headers,
            json={"query": query},
            stream=True,
            timeout=self.stream_timeout,
        ) as resp:
            assert resp.status_code == 200, f"{resp.status_code}: {resp.text}"

            content_type = (resp.headers.get("Content-Type") or "").lower()
            assert "text/event-stream" in content_type, (
                f"expected text/event-stream, got Content-Type={resp.headers.get('Content-Type')!r}"
            )

            for envelope in _iter_sse_envelopes(resp):
                event = envelope["event"]
                payload = json.loads(envelope["data"])

                if event == "connected":
                    conv_id = payload.get("conversationId")
                    if isinstance(conv_id, str) and conv_id:
                        connected_conv_id = conv_id
                    continue

                if event == "answer_chunk" and isinstance(payload, dict):
                    acc = payload.get("accumulated")
                    if isinstance(acc, str):
                        accumulated_answer = acc
                    continue

                if event == "error":
                    saw_error = True
                    if not allow_error:
                        raise AssertionError(f"stream emitted error event: {payload!r}")
                    continue

                if event != "complete":
                    continue

                saw_complete = True
                if not accumulated_answer.strip() and isinstance(payload, dict):
                    accumulated_answer = _answer_from_complete_payload(payload)

                complete_conv = (
                    (payload.get("conversation") or {}) if isinstance(payload, dict) else {}
                )
                complete_conv_id = complete_conv.get("_id")
                if isinstance(complete_conv_id, str) and complete_conv_id:
                    if connected_conv_id and connected_conv_id != complete_conv_id:
                        raise AssertionError(
                            f"connected conversationId {connected_conv_id!r} != "
                            f"complete conversation._id {complete_conv_id!r}"
                        )
                    connected_conv_id = complete_conv_id
                break

        if saw_error and not allow_error:
            raise AssertionError("stream ended after error event without raising earlier")

        return connected_conv_id, accumulated_answer, saw_complete

    # ------------------------------------------------------------------------
    # Positive tests
    # ------------------------------------------------------------------------

    def test_stream_agent_conversation_completes_with_answer(self) -> None:
        agent_key = self.agent_session["primary_agent"]

        conv_id, answer, saw_complete = self._stream_agent_conversation(
            agent_key,
            query=SEARCH_QUERY,
        )

        assert saw_complete, "stream ended without a complete event"
        assert conv_id, "stream did not yield a conversation id"
        assert answer.strip(), "stream completed but answer text was empty"

    def test_stream_agent_conversation_random_question_answer_is_plausible(self) -> None:
        agent_key = self.agent_session["primary_agent"]
        query, expected_keywords = random.choice(_KB_QA_POOL)

        conv_id, answer, saw_complete = self._stream_agent_conversation(
            agent_key,
            query=query,
        )

        assert saw_complete, f"stream ended without complete for query={query!r}"
        assert conv_id, f"no conversation id for query={query!r}"
        answer_lower = answer.lower()
        assert any(kw.lower() in answer_lower for kw in expected_keywords), (
            f"answer did not contain any of {expected_keywords!r} for query={query!r}: "
            f"{answer[:500]!r}"
        )

    # ------------------------------------------------------------------------
    # Negative tests
    # ------------------------------------------------------------------------

    @pytest.mark.parametrize(
        "payload",
        [
            {},
            {"query": ""},
        ],
    )
    def test_stream_agent_conversation_invalid_payload_returns_400(
        self, payload: dict
    ) -> None:
        headers = {**self.headers, "Accept": "text/event-stream"}
        agent_key = self.agent_session["primary_agent"]

        resp = requests.post(
            self._agent_stream_url(agent_key),
            headers=headers,
            json=payload,
            timeout=self.timeout,
        )
        assert resp.status_code == 400, f"{resp.status_code}: {resp.text}"

    def test_stream_agent_conversation_missing_auth_returns_401_or_403(self) -> None:
        agent_key = self.agent_session["primary_agent"]
        headers = {"Accept": "text/event-stream"}

        resp = requests.post(
            self._agent_stream_url(agent_key),
            headers=headers,
            json={"query": SEARCH_QUERY},
            timeout=self.timeout,
        )
        assert resp.status_code in (401, 403), f"{resp.status_code}: {resp.text}"

    def test_stream_agent_conversation_unknown_agent_emits_error(self) -> None:
        unknown_key = str(uuid.uuid4())

        conv_id, answer, saw_complete = self._stream_agent_conversation(
            unknown_key,
            query=SEARCH_QUERY,
            allow_error=True,
        )

        assert not saw_complete, (
            f"expected no complete event for unknown agent; conv_id={conv_id!r}, "
            f"answer={answer[:200]!r}"
        )

    def test_stream_agent_conversation_deleted_agent_fails(self) -> None:
        deleted_key = self.agent_session["secondary_agents"][0]

        delete_url = f"{self.base_url}/api/v1/agents/{deleted_key}"
        delete_resp = requests.delete(
            delete_url,
            headers=self.headers,
            timeout=self.timeout,
        )
        assert delete_resp.status_code < 300, (
            f"agent delete failed: {delete_resp.status_code}: {delete_resp.text}"
        )

        conv_id, answer, saw_complete = self._stream_agent_conversation(
            deleted_key,
            query=SEARCH_QUERY,
            allow_error=True,
        )

        assert not saw_complete, (
            f"expected stream to fail for deleted agent {deleted_key}; "
            f"conv_id={conv_id!r}, answer={answer[:200]!r}"
        )
