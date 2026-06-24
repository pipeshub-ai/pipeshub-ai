"""Agent conversation stream integration tests.

``POST /api/v1/agents/{agentKey}/conversations/stream``
``POST /api/v1/agents/{agentKey}/conversations/{conversationId}/messages/stream``

Requires ``session_kb`` (indexed Asana DR PDF) and ``agent_session`` fixtures from
the local ``response-validation/enterprise-search/conftest.py``.

``PIPESHUB_TEST_STREAM_TIMEOUT`` (optional): override seconds for SSE reads;
otherwise ``max(PIPESHUB_TEST_TIMEOUT, 120)``.
"""

from __future__ import annotations

import json
import os
import random
import sys
import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
import requests

_ROOT = Path(__file__).resolve().parents[3]
_HELPER = _ROOT / "helper"
_RV_HELPER = _ROOT / "response-validation" / "helper"
for _p in (_ROOT, _HELPER, _RV_HELPER):
    s = str(_p)
    if s not in sys.path:
        sys.path.insert(0, s)

from openapi_schema_validator import (
    assert_request_body_matches_openapi_operation,
    assert_response_matches_openapi_ref,
)
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
_SSEEnvelope = dict[str, str]
_AGENT_STREAM_SSE_EVENT_REF = "#/components/schemas/AgentStreamSSEEvent"
_AGENT_MESSAGE_STREAM_SSE_EVENT_REF = "#/components/schemas/AgentMessageStreamSSEEvent"

def _iter_sse_envelopes(
    resp: requests.Response,
    *,
    max_events: int = _SSE_MAX_EVENTS,
) -> Iterator[_SSEEnvelope]:
    """
    Minimal SSE parser for frames like:

      event: <name>
      data: <payload>

    Frames are separated by a blank line. Returns envelopes:
    { "event": <name>, "data": <string> }.
    """
    event_name: str | None = None
    data_lines: list[str] = []

    def flush() -> _SSEEnvelope | None:
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
            event_name = line[len("event:") :].removeprefix(" ")
            continue
        if line.startswith("data:"):
            data_lines.append(line[len("data:") :].removeprefix(" "))
            continue

    env = flush()
    if env is not None:
        yield env


def _answer_from_complete_payload(payload: dict[str, Any]) -> str:
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


def _response_json(resp: requests.Response) -> dict[str, Any]:
    try:
        data = resp.json()
    except ValueError as exc:
        raise AssertionError(
            f"Expected JSON response, got status={resp.status_code}: {resp.text[:500]}"
        ) from exc
    assert isinstance(data, dict), f"Expected dict JSON body, got: {data!r}"
    return data


def _assert_validation_error(
    resp: requests.Response,
    *,
    label: str | None = None,
) -> dict[str, Any]:
    prefix = f"[{label}] " if label else ""
    assert resp.status_code == 400, f"{prefix}{resp.status_code}: {resp.text}"
    body = _response_json(resp)
    assert_response_matches_openapi_ref(body, "#/components/schemas/ErrorResponse")
    error = body.get("error")
    assert isinstance(error, dict), f"{prefix}Expected error object, got: {body!r}"
    assert error.get("code") == "VALIDATION_ERROR", (
        f"{prefix}Expected VALIDATION_ERROR, got {error.get('code')!r}"
    )
    metadata = error.get("metadata")
    assert isinstance(metadata, dict), (
        f"{prefix}Expected error.metadata object, got: {body!r}"
    )
    details = metadata.get("errors")
    assert isinstance(details, list) and details, (
        f"{prefix}Expected non-empty error.metadata.errors list, got: {body!r}"
    )
    return body


class _AgentStreamTestBase:
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
                assert_response_matches_openapi_ref(envelope, _AGENT_STREAM_SSE_EVENT_REF)
                event = envelope["event"]
                payload = json.loads(envelope["data"]) if envelope["data"] else {}

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

        return connected_conv_id, accumulated_answer, saw_complete

    def _agent_message_stream_url(self, agent_key: str, conversation_id: str) -> str:
        return (
            f"{self.base_url}/api/v1/agents/{agent_key}"
            f"/conversations/{conversation_id}/messages/stream"
        )

    def _create_agent_conversation_id(self, agent_key: str, *, query: str) -> str:
        conv_id, _, saw_complete = self._stream_agent_conversation(agent_key, query=query)
        assert saw_complete, "stream ended without a complete event"
        assert conv_id, "stream did not yield a conversation id"
        return conv_id

    def _stream_add_message(
        self,
        agent_key: str,
        conversation_id: str,
        *,
        json_body: dict[str, Any] | None = None,
        query: str | None = None,
        headers: dict | None = None,
        params: dict[str, str] | None = None,
        allow_error: bool = False,
    ) -> tuple[str, bool]:
        """Stream a follow-up message on an existing agent conversation.

        Returns ``(accumulated_answer, saw_complete)``.
        """
        body: dict[str, Any] = dict(json_body) if json_body is not None else {}
        if query is not None:
            body["query"] = query
        req_headers = {**(headers or self.headers), "Accept": "text/event-stream"}
        accumulated_answer = ""
        saw_complete = False

        with requests.post(
            self._agent_message_stream_url(agent_key, conversation_id),
            headers=req_headers,
            json=body,
            params=params,
            stream=True,
            timeout=self.stream_timeout,
        ) as resp:
            assert resp.status_code == 200, f"{resp.status_code}: {resp.text}"

            content_type = (resp.headers.get("Content-Type") or "").lower()
            assert "text/event-stream" in content_type, (
                f"expected text/event-stream, got Content-Type={resp.headers.get('Content-Type')!r}"
            )

            for envelope in _iter_sse_envelopes(resp):
                assert_response_matches_openapi_ref(
                    envelope, _AGENT_MESSAGE_STREAM_SSE_EVENT_REF
                )
                event = envelope["event"]
                payload = json.loads(envelope["data"]) if envelope["data"] else {}

                if event == "answer_chunk" and isinstance(payload, dict):
                    acc = payload.get("accumulated")
                    if isinstance(acc, str):
                        accumulated_answer = acc
                    continue

                if event == "error":
                    if not allow_error:
                        raise AssertionError(f"stream emitted error event: {payload!r}")
                    continue

                if event != "complete":
                    continue

                saw_complete = True
                if not accumulated_answer.strip() and isinstance(payload, dict):
                    accumulated_answer = _answer_from_complete_payload(payload)
                break

        return accumulated_answer, saw_complete


@pytest.mark.integration
class TestAgentConversationStreamOpenApiRequestContract:
    """Offline OpenAPI request-body checks for streamAgentConversation."""

    def test_minimal_request_body_matches_openapi_spec(self) -> None:
        assert_request_body_matches_openapi_operation(
            {"query": "hello"},
            "streamAgentConversation",
        )

    def test_rejects_previous_conversations_offline(self) -> None:
        with pytest.raises(AssertionError):
            assert_request_body_matches_openapi_operation(
                {"query": "hi", "previousConversations": []},
                "streamAgentConversation",
            )

    def test_rejects_quick_mode_offline(self) -> None:
        with pytest.raises(AssertionError):
            assert_request_body_matches_openapi_operation(
                {"query": "hi", "quickMode": True},
                "streamAgentConversation",
            )


@pytest.mark.integration
class TestAgentConversationStream(_AgentStreamTestBase):

    # ------------------------------------------------------------------------
    # Positive tests
    # ------------------------------------------------------------------------

    def test_stream_agent_conversation_response_matches_spec(self) -> None:
        agent_key = self.agent_session["primary_agent"]
        headers = {**self.headers, "Accept": "text/event-stream"}

        with requests.post(
            self._agent_stream_url(agent_key),
            headers=headers,
            json={"query": SEARCH_QUERY},
            stream=True,
            timeout=self.stream_timeout,
        ) as resp:
            assert resp.status_code == 200, f"{resp.status_code}: {resp.text}"

            content_type = (resp.headers.get("Content-Type") or "").lower()
            assert "text/event-stream" in content_type, (
                f"expected text/event-stream, got Content-Type={resp.headers.get('Content-Type')!r}"
            )

            accumulated_answer = ""
            saw_complete = False

            for envelope in _iter_sse_envelopes(resp):
                assert_response_matches_openapi_ref(envelope, _AGENT_STREAM_SSE_EVENT_REF)

                payload = json.loads(envelope["data"]) if envelope["data"] else {}
                event = envelope["event"]

                if event == "answer_chunk" and isinstance(payload, dict):
                    acc = payload.get("accumulated")
                    if isinstance(acc, str):
                        accumulated_answer = acc

                if event == "error":
                    raise AssertionError(f"stream emitted error event: {payload!r}")

                if event == "complete":
                    saw_complete = True
                    if not accumulated_answer.strip() and isinstance(payload, dict):
                        accumulated_answer = _answer_from_complete_payload(payload)
                    break

            assert saw_complete, "stream ended without a complete event"
            assert accumulated_answer.strip(), "stream completed but answer text was empty"

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
        _assert_validation_error(resp)

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


@pytest.mark.integration
class TestAgentConversationMessageStream(_AgentStreamTestBase):

    # ------------------------------------------------------------------------
    # Positive tests
    # ------------------------------------------------------------------------

    def test_add_message_stream_response_matches_spec(self) -> None:
        agent_key = self.agent_session["primary_agent"]
        conversation_id = self._create_agent_conversation_id(
            agent_key,
            query="stream-create conversation for add-message spec test",
        )
        headers = {**self.headers, "Accept": "text/event-stream"}
        url = self._agent_message_stream_url(agent_key, conversation_id)

        with requests.post(
            url,
            headers=headers,
            json={"query": SEARCH_QUERY},
            stream=True,
            timeout=self.stream_timeout,
        ) as resp:
            assert resp.status_code == 200, f"{resp.status_code}: {resp.text}"

            content_type = (resp.headers.get("Content-Type") or "").lower()
            assert "text/event-stream" in content_type, (
                f"expected text/event-stream, got Content-Type={resp.headers.get('Content-Type')!r}"
            )

            accumulated_answer = ""
            saw_complete = False

            for envelope in _iter_sse_envelopes(resp):
                assert_response_matches_openapi_ref(
                    envelope, _AGENT_MESSAGE_STREAM_SSE_EVENT_REF
                )

                payload = json.loads(envelope["data"]) if envelope["data"] else {}
                event = envelope["event"]

                if event == "answer_chunk" and isinstance(payload, dict):
                    acc = payload.get("accumulated")
                    if isinstance(acc, str):
                        accumulated_answer = acc

                if event == "error":
                    raise AssertionError(f"stream emitted error event: {payload!r}")

                if event == "complete":
                    saw_complete = True
                    if not accumulated_answer.strip() and isinstance(payload, dict):
                        accumulated_answer = _answer_from_complete_payload(payload)
                    break

            assert saw_complete, "stream ended without a complete event"
            assert accumulated_answer.strip(), "stream completed but answer text was empty"

    def test_add_message_stream_completes_with_answer(self) -> None:
        agent_key = self.agent_session["primary_agent"]
        conversation_id = self._create_agent_conversation_id(
            agent_key,
            query="stream-create conversation for add-message positive test",
        )

        answer, saw_complete = self._stream_add_message(
            agent_key,
            conversation_id,
            query=SEARCH_QUERY,
        )

        assert saw_complete, "stream ended without a complete event"
        assert answer.strip(), "stream completed but answer text was empty"

    def test_add_message_stream_random_kb_question_plausible(self) -> None:
        agent_key = self.agent_session["primary_agent"]
        query, expected_keywords = random.choice(_KB_QA_POOL)
        conversation_id = self._create_agent_conversation_id(
            agent_key,
            query="stream-create conversation for add-message KB test",
        )

        answer, saw_complete = self._stream_add_message(
            agent_key,
            conversation_id,
            query=query,
        )

        assert saw_complete, f"stream ended without complete for query={query!r}"
        answer_lower = answer.lower()
        assert any(kw.lower() in answer_lower for kw in expected_keywords), (
            f"answer did not contain any of {expected_keywords!r} for query={query!r}: "
            f"{answer[:500]!r}"
        )

    def test_add_message_stream_updates_conversation_message_count(self) -> None:
        agent_key = self.agent_session["primary_agent"]
        conversation_id = self._create_agent_conversation_id(
            agent_key,
            query="stream-create conversation for message-count test",
        )
        headers = {**self.headers, "Accept": "text/event-stream"}
        url = self._agent_message_stream_url(agent_key, conversation_id)

        with requests.post(
            url,
            headers=headers,
            json={"query": "follow-up question"},
            stream=True,
            timeout=self.stream_timeout,
        ) as resp:
            assert resp.status_code == 200, f"{resp.status_code}: {resp.text}"

            saw_complete = False
            for envelope in _iter_sse_envelopes(resp):
                assert_response_matches_openapi_ref(
                    envelope, _AGENT_MESSAGE_STREAM_SSE_EVENT_REF
                )
                if envelope["event"] == "error":
                    payload = json.loads(envelope["data"]) if envelope["data"] else {}
                    raise AssertionError(f"stream emitted error event: {payload!r}")
                if envelope["event"] != "complete":
                    continue

                saw_complete = True
                payload = json.loads(envelope["data"]) if envelope["data"] else {}
                conv = payload.get("conversation") or {}
                assert conv.get("_id") == conversation_id, (
                    f"complete conversation id mismatch: {conv.get('_id')!r}"
                )
                msgs = conv.get("messages") or []
                non_empty_contents = [
                    m.get("content") for m in msgs
                    if isinstance(m, dict) and (m.get("content") or "").strip()
                ]
                assert len(non_empty_contents) >= 2, (
                    f"expected at least 2 non-empty message contents, got {len(non_empty_contents)}"
                )
                break

            assert saw_complete, "stream ended without a complete event"

    @pytest.mark.parametrize("chat_mode", ["auto", "quick", "verification", "deep"])
    def test_add_message_stream_all_chat_modes_complete(self, chat_mode: str) -> None:
        agent_key = self.agent_session["primary_agent"]
        conversation_id = self._create_agent_conversation_id(
            agent_key,
            query=f"stream-create for chatMode={chat_mode}",
        )

        answer, saw_complete = self._stream_add_message(
            agent_key,
            conversation_id,
            json_body={
                "query": f"follow-up question with chatMode {chat_mode}",
                "chatMode": chat_mode,
            },
        )

        assert saw_complete, f"[{chat_mode}] stream ended without a complete event"
        assert answer.strip(), f"[{chat_mode}] stream completed but answer text was empty"

    @pytest.mark.parametrize(
        ("label", "json_body"),
        [
            ("filters kb", {"query": "follow-up with kb filter"}),
            (
                "timezone and currentTime",
                {
                    "query": "follow-up with time context",
                    "timezone": "America/New_York",
                    "currentTime": "2026-05-19T12:58:01-04:00",
                },
            ),
            (
                "empty filters and appliedFilters",
                {
                    "query": "follow-up empty filters",
                    "filters": {"kb": []},
                    "appliedFilters": {"kb": []},
                },
            ),
        ],
    )
    def test_add_message_stream_optional_body_fields_accepted(
        self,
        label: str,
        json_body: dict[str, Any],
    ) -> None:
        agent_key = self.agent_session["primary_agent"]
        conversation_id = self._create_agent_conversation_id(
            agent_key,
            query=f"stream-create for optional body: {label}",
        )
        body = dict(json_body)
        if label == "filters kb":
            body["filters"] = {"kb": [self.kb_id]}

        answer, saw_complete = self._stream_add_message(
            agent_key,
            conversation_id,
            json_body=body,
        )
        assert saw_complete, f"[{label}] stream ended without a complete event"
        assert answer.strip(), f"[{label}] stream completed but answer text was empty"

    def test_add_message_stream_ignores_benign_query_param(self) -> None:
        agent_key = self.agent_session["primary_agent"]
        conversation_id = self._create_agent_conversation_id(
            agent_key,
            query="stream-create conversation for benign query param test",
        )

        answer, saw_complete = self._stream_add_message(
            agent_key,
            conversation_id,
            query="follow-up with debug query param",
            params={"debug": "1"},
        )

        assert saw_complete, "stream ended without a complete event"
        assert answer.strip(), "stream completed but answer text was empty"

    # ------------------------------------------------------------------------
    # Negative tests — path params and auth
    # ------------------------------------------------------------------------

    def test_add_message_invalid_conversation_id_returns_400(self) -> None:
        agent_key = self.agent_session["primary_agent"]
        headers = {**self.headers, "Accept": "text/event-stream"}
        url = self._agent_message_stream_url(agent_key, "not-an-objectid")

        resp = requests.post(
            url,
            headers=headers,
            json={"query": "hi"},
            timeout=self.timeout,
        )
        _assert_validation_error(resp)

    def test_add_message_missing_auth_returns_401_or_403(self) -> None:
        agent_key = self.agent_session["primary_agent"]
        conversation_id = "0" * 24
        headers = {"Accept": "text/event-stream"}
        url = self._agent_message_stream_url(agent_key, conversation_id)

        resp = requests.post(
            url,
            headers=headers,
            json={"query": "hi"},
            timeout=self.timeout,
        )
        assert resp.status_code in (401, 403), f"{resp.status_code}: {resp.text}"

    def test_add_message_nonexistent_conversation_emits_error(self) -> None:
        agent_key = self.agent_session["primary_agent"]
        headers = {**self.headers, "Accept": "text/event-stream"}
        url = self._agent_message_stream_url(agent_key, "0" * 24)

        with requests.post(
            url,
            headers=headers,
            json={"query": "hi"},
            stream=True,
            timeout=self.stream_timeout,
        ) as resp:
            if resp.status_code != 200:
                assert resp.status_code == 404, f"{resp.status_code}: {resp.text}"
                return

            for envelope in _iter_sse_envelopes(resp):
                if envelope["event"] != "error":
                    continue
                payload = json.loads(envelope["data"]) if envelope["data"] else {}
                msg = payload.get("message") or payload.get("error") or ""
                assert "not found" in str(msg).lower(), (
                    f"unexpected error payload: {payload!r}"
                )
                return

        raise AssertionError("stream ended without an error event")

    def test_add_message_wrong_agent_key_emits_error(self) -> None:
        primary_key = self.agent_session["primary_agent"]
        wrong_key = self.agent_session["secondary_agents"][0]
        conversation_id = self._create_agent_conversation_id(
            primary_key,
            query="stream-create conversation for wrong-agent-key test",
        )

        answer, saw_complete = self._stream_add_message(
            wrong_key,
            conversation_id,
            query=SEARCH_QUERY,
            allow_error=True,
        )

        assert not saw_complete, (
            f"expected no complete for wrong agent key; answer={answer[:200]!r}"
        )

    def test_add_message_unknown_agent_emits_error(self) -> None:
        unknown_key = str(uuid.uuid4())
        conversation_id = "0" * 24
        headers = {**self.headers, "Accept": "text/event-stream"}
        url = self._agent_message_stream_url(unknown_key, conversation_id)

        saw_complete = False
        saw_error = False
        with requests.post(
            url,
            headers=headers,
            json={"query": SEARCH_QUERY},
            stream=True,
            timeout=self.stream_timeout,
        ) as resp:
            assert resp.status_code == 200, f"{resp.status_code}: {resp.text}"

            for envelope in _iter_sse_envelopes(resp):
                assert_response_matches_openapi_ref(
                    envelope, _AGENT_MESSAGE_STREAM_SSE_EVENT_REF
                )
                if envelope["event"] == "error":
                    saw_error = True
                    break
                if envelope["event"] == "complete":
                    saw_complete = True
                    break

        assert saw_error or not saw_complete, (
            "expected error event or no complete for unknown agent"
        )

    # ------------------------------------------------------------------------
    # Negative tests — body (Zod)
    # ------------------------------------------------------------------------

    @pytest.mark.parametrize(
        ("label", "payload"),
        [
            ("missing query", {}),
            ("empty query", {"query": ""}),
            ("invalid filters kb", {"query": "x", "filters": {"kb": ["bad-id"]}}),
            ("invalid currentTime", {"query": "x", "currentTime": "not-an-iso-datetime"}),
            ("empty chatMode", {"query": "x", "chatMode": ""}),
            ("invalid chatMode", {"query": "x", "chatMode": "internal_search"}),
            (
                "attachment missing recordId",
                {"query": "x", "attachments": [{"recordName": "only-name"}]},
            ),
            ("empty modelKey", {"query": "x", "modelKey": ""}),
        ],
    )
    def test_add_message_invalid_body_returns_400(
        self,
        label: str,
        payload: dict[str, Any],
    ) -> None:
        agent_key = self.agent_session["primary_agent"]
        conversation_id = self._create_agent_conversation_id(
            agent_key,
            query=f"stream-create for invalid body: {label}",
        )
        headers = {**self.headers, "Accept": "text/event-stream"}
        url = self._agent_message_stream_url(agent_key, conversation_id)

        resp = requests.post(
            url,
            headers=headers,
            json=payload,
            timeout=self.timeout,
        )
        _assert_validation_error(resp, label=label)
