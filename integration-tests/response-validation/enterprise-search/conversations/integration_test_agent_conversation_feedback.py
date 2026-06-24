"""Agent conversation message feedback integration tests.

``POST /api/v1/agents/{agentKey}/conversations/{conversationId}/message/{messageId}/feedback``

Uses ``agent_session`` from ``response-validation/enterprise-search/conftest.py``.
"""

from __future__ import annotations

import json
import os
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any
from uuid import uuid4

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
    assert_response_matches_openapi_operation,
    assert_response_matches_openapi_ref,
)
from pipeshub_client import PipeshubClient

_SSE_MAX_EVENTS = 10_000
_SSEEnvelope = dict[str, str]

_MINIMAL_FEEDBACK_PAYLOAD: dict[str, Any] = {
    "isHelpful": True,
    "categories": ["excellent_answer"],
}


def _iter_sse_envelopes(
    resp: requests.Response,
    *,
    max_events: int = _SSE_MAX_EVENTS,
) -> Iterator[_SSEEnvelope]:
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
            event_name = line[len("event:") :].strip()
            continue
        if line.startswith("data:"):
            data_lines.append(line[len("data:") :].lstrip())
            continue

    env = flush()
    if env is not None:
        yield env


def _response_json(resp: requests.Response) -> dict[str, Any]:
    try:
        data = resp.json()
    except ValueError as exc:
        raise AssertionError(
            f"Expected JSON response, got status={resp.status_code}: {resp.text[:500]}"
        ) from exc
    assert isinstance(data, dict), f"Expected dict JSON body, got: {data!r}"
    return data


@pytest.mark.integration
class TestAgentConversationMessageFeedbackOpenApiRequestContract:
    """Offline OpenAPI request-body checks for updateAgentConversationMessageFeedback."""

    def test_minimal_request_body_matches_openapi_spec(self) -> None:
        assert_request_body_matches_openapi_operation(
            _MINIMAL_FEEDBACK_PAYLOAD,
            "updateAgentConversationMessageFeedback",
        )

    def test_rich_request_body_matches_openapi_spec(self) -> None:
        assert_request_body_matches_openapi_operation(
            {
                "isHelpful": True,
                "categories": ["excellent_answer"],
                "comments": {"positive": "Clear and useful."},
            },
            "updateAgentConversationMessageFeedback",
        )

    def test_rejects_extra_top_level_property(self) -> None:
        payload = {**_MINIMAL_FEEDBACK_PAYLOAD, "unexpectedTopLevelField": "x"}
        with pytest.raises(AssertionError):
            assert_request_body_matches_openapi_operation(
                payload,
                "updateAgentConversationMessageFeedback",
            )

    def test_rejects_invalid_category_offline(self) -> None:
        payload = {"isHelpful": False, "categories": ["citation_issues"]}
        with pytest.raises(AssertionError):
            assert_request_body_matches_openapi_operation(
                payload,
                "updateAgentConversationMessageFeedback",
            )

    def test_rejects_removed_ratings_field_offline(self) -> None:
        payload = {**_MINIMAL_FEEDBACK_PAYLOAD, "ratings": {"accuracy": 5}}
        with pytest.raises(AssertionError):
            assert_request_body_matches_openapi_operation(
                payload,
                "updateAgentConversationMessageFeedback",
            )


@pytest.mark.integration
class TestAgentConversationMessageFeedback:
    @pytest.fixture(autouse=True)
    def _setup(
        self,
        pipeshub_client: PipeshubClient,
        agent_session: dict[str, Any],
    ) -> None:
        self.client = pipeshub_client
        self.base_url = pipeshub_client.base_url
        self.headers = pipeshub_client.auth_headers
        self.timeout = int(os.getenv("PIPESHUB_TEST_TIMEOUT", "60"))
        stream_override = os.getenv("PIPESHUB_TEST_STREAM_TIMEOUT", "").strip()
        self.stream_timeout = (
            int(stream_override)
            if stream_override
            else max(self.timeout, 120)
        )
        self.primary_agent = agent_session["primary_agent"]

    @pytest.fixture
    def created_conversations(self):
        created: list[tuple[str, str]] = []
        yield created
        for agent_key, conversation_id in reversed(created):
            try:
                resp = requests.delete(
                    self._conversation_url(agent_key, conversation_id),
                    headers=self.headers,
                    timeout=self.timeout,
                )
                assert resp.status_code < 300, (
                    f"Conversation delete failed for {conversation_id}: "
                    f"HTTP {resp.status_code} {resp.text[:300]}"
                )
            except Exception:
                pass

    def _stream_url(self, agent_key: str) -> str:
        return f"{self.base_url}/api/v1/agents/{agent_key}/conversations/stream"

    def _conversation_url(self, agent_key: str, conversation_id: str) -> str:
        return (
            f"{self.base_url}/api/v1/agents/{agent_key}"
            f"/conversations/{conversation_id}"
        )

    def _feedback_url(
        self,
        agent_key: str,
        conversation_id: str,
        message_id: str,
    ) -> str:
        return (
            f"{self._conversation_url(agent_key, conversation_id)}"
            f"/message/{message_id}/feedback"
        )

    def _stream_create_agent_conversation_id(
        self,
        agent_key: str,
        *,
        query: str,
        created_conversations: list[tuple[str, str]],
    ) -> str:
        headers = {**self.headers, "Accept": "text/event-stream"}

        with requests.post(
            self._stream_url(agent_key),
            headers=headers,
            json={"query": query},
            stream=True,
            timeout=self.stream_timeout,
        ) as resp:
            assert resp.status_code == 200, f"{resp.status_code}: {resp.text}"

            for envelope in _iter_sse_envelopes(resp):
                if envelope["event"] == "error":
                    payload = json.loads(envelope["data"]) if envelope["data"] else {}
                    raise AssertionError(f"stream emitted error event: {payload!r}")
                if envelope["event"] != "complete":
                    continue

                payload = json.loads(envelope["data"]) if envelope["data"] else {}
                conversation = payload.get("conversation") or {}
                conversation_id = conversation.get("_id")
                assert isinstance(conversation_id, str) and conversation_id, (
                    f"complete payload missing conversation._id: {payload!r}"
                )
                created_conversations.append((agent_key, conversation_id))
                return conversation_id

        raise AssertionError("agent conversation stream ended without a complete event")

    def _get_conversation_messages(
        self,
        agent_key: str,
        conversation_id: str,
    ) -> list[dict[str, Any]]:
        resp = requests.get(
            self._conversation_url(agent_key, conversation_id),
            headers=self.headers,
            timeout=self.timeout,
        )
        assert resp.status_code == 200, f"{resp.status_code}: {resp.text}"
        body = _response_json(resp)
        conversation = body.get("conversation")
        assert isinstance(conversation, dict), f"Expected conversation object, got: {body!r}"
        messages = conversation.get("messages")
        assert isinstance(messages, list) and messages, (
            f"Expected non-empty messages list, got: {body!r}"
        )
        typed_messages: list[dict[str, Any]] = [
            message for message in messages if isinstance(message, dict)
        ]
        assert typed_messages, f"Expected dict messages, got: {messages!r}"
        return typed_messages

    def _conversation_last_bot_and_user_message_ids(
        self,
        agent_key: str,
        conversation_id: str,
    ) -> tuple[str, str]:
        messages = self._get_conversation_messages(agent_key, conversation_id)
        last_message = messages[-1]
        last_id = last_message.get("_id") or last_message.get("id")
        assert last_message.get("messageType") == "bot_response", (
            f"Expected last message bot_response, got: {last_message!r}"
        )
        assert isinstance(last_id, str) and last_id, (
            f"Expected last message id, got: {last_message!r}"
        )

        user_query_id: str | None = None
        for message in messages:
            if message.get("messageType") != "user_query":
                continue
            candidate = message.get("_id") or message.get("id")
            if isinstance(candidate, str) and candidate:
                user_query_id = candidate
                break

        assert user_query_id, f"No user_query id found in messages: {messages!r}"
        return last_id, user_query_id

    def _stream_create_agent_conversation_bot_and_user_message_ids(
        self,
        agent_key: str,
        *,
        query: str,
        created_conversations: list[tuple[str, str]],
    ) -> tuple[str, str, str]:
        conversation_id = self._stream_create_agent_conversation_id(
            agent_key,
            query=query,
            created_conversations=created_conversations,
        )
        bot_id, user_id = self._conversation_last_bot_and_user_message_ids(
            agent_key,
            conversation_id,
        )
        return conversation_id, bot_id, user_id

    @staticmethod
    def _assert_validation_error(resp: requests.Response) -> dict[str, Any]:
        assert resp.status_code == 400, f"{resp.status_code}: {resp.text}"
        body = _response_json(resp)
        assert_response_matches_openapi_ref(body, "#/components/schemas/ErrorResponse")
        error = body.get("error")
        assert isinstance(error, dict), f"Expected error object, got: {body!r}"
        assert error.get("code") == "VALIDATION_ERROR", (
            f"Expected VALIDATION_ERROR, got {error.get('code')!r}"
        )
        metadata = error.get("metadata")
        assert isinstance(metadata, dict), f"Expected error.metadata object, got: {body!r}"
        details = metadata.get("errors")
        assert isinstance(details, list) and details, (
            f"Expected non-empty error.metadata.errors list, got: {body!r}"
        )
        return body

    def test_post_agent_message_feedback_on_bot_response_matches_spec(
        self,
        created_conversations,
    ) -> None:
        assert_request_body_matches_openapi_operation(
            _MINIMAL_FEEDBACK_PAYLOAD,
            "updateAgentConversationMessageFeedback",
        )

        conversation_id, bot_id, _user_id = (
            self._stream_create_agent_conversation_bot_and_user_message_ids(
                self.primary_agent,
                query=f"integration: agent message feedback positive-{uuid4().hex}",
                created_conversations=created_conversations,
            )
        )
        url = self._feedback_url(self.primary_agent, conversation_id, bot_id)
        resp = requests.post(
            url,
            headers=self.headers,
            json=_MINIMAL_FEEDBACK_PAYLOAD,
            timeout=self.timeout,
        )
        assert resp.status_code == 200, f"{resp.status_code}: {resp.text}"
        body = resp.json()
        assert body.get("conversationId") == conversation_id
        assert body.get("messageId") == bot_id
        assert_response_matches_openapi_operation(
            body,
            "updateAgentConversationMessageFeedback",
            status_code="200",
        )

    def test_post_agent_message_feedback_rich_payload_matches_spec(
        self,
        created_conversations,
    ) -> None:
        payload = {
            "isHelpful": True,
            "categories": ["excellent_answer"],
            "comments": {"positive": "Clear and useful."},
        }
        assert_request_body_matches_openapi_operation(
            payload,
            "updateAgentConversationMessageFeedback",
        )

        conversation_id, bot_id, _user_id = (
            self._stream_create_agent_conversation_bot_and_user_message_ids(
                self.primary_agent,
                query=f"integration: agent message feedback rich-{uuid4().hex}",
                created_conversations=created_conversations,
            )
        )
        url = self._feedback_url(self.primary_agent, conversation_id, bot_id)
        resp = requests.post(
            url,
            headers=self.headers,
            json=payload,
            timeout=self.timeout,
        )
        assert resp.status_code == 200, f"{resp.status_code}: {resp.text}"
        body = resp.json()
        assert body.get("conversationId") == conversation_id
        assert body.get("messageId") == bot_id
        assert_response_matches_openapi_operation(
            body,
            "updateAgentConversationMessageFeedback",
            status_code="200",
        )

    def test_post_agent_message_feedback_strips_unknown_fields_returns_200(
        self,
        created_conversations,
    ) -> None:
        conversation_id, bot_id, _user_id = (
            self._stream_create_agent_conversation_bot_and_user_message_ids(
                self.primary_agent,
                query=f"integration: agent message feedback strip-{uuid4().hex}",
                created_conversations=created_conversations,
            )
        )
        payload = {**_MINIMAL_FEEDBACK_PAYLOAD, "unexpectedTopLevelField": "drop-me"}
        url = self._feedback_url(self.primary_agent, conversation_id, bot_id)
        resp = requests.post(
            url,
            headers=self.headers,
            json=payload,
            timeout=self.timeout,
        )
        assert resp.status_code == 200, f"{resp.status_code}: {resp.text}"
        body = resp.json()
        assert_response_matches_openapi_operation(
            body,
            "updateAgentConversationMessageFeedback",
            status_code="200",
        )

    def test_post_agent_message_feedback_on_user_query_returns_400(
        self,
        created_conversations,
    ) -> None:
        conversation_id, _bot_id, user_id = (
            self._stream_create_agent_conversation_bot_and_user_message_ids(
                self.primary_agent,
                query=f"integration: agent message feedback user_query-{uuid4().hex}",
                created_conversations=created_conversations,
            )
        )
        url = self._feedback_url(self.primary_agent, conversation_id, user_id)
        resp = requests.post(
            url,
            headers=self.headers,
            json={},
            timeout=self.timeout,
        )
        assert resp.status_code == 400, f"{resp.status_code}: {resp.text}"
        lowered = resp.text.lower()
        assert "bot" in lowered or "feedback" in lowered, (
            f"unexpected error body: {resp.text!r}"
        )

    def test_post_agent_message_feedback_invalid_category_returns_400(
        self,
        created_conversations,
    ) -> None:
        conversation_id, bot_id, _user_id = (
            self._stream_create_agent_conversation_bot_and_user_message_ids(
                self.primary_agent,
                query=f"integration: agent message feedback bad category-{uuid4().hex}",
                created_conversations=created_conversations,
            )
        )
        url = self._feedback_url(self.primary_agent, conversation_id, bot_id)
        resp = requests.post(
            url,
            headers=self.headers,
            json={"isHelpful": False, "categories": ["citation_issues"]},
            timeout=self.timeout,
        )
        self._assert_validation_error(resp)

    def test_post_agent_message_feedback_invalid_message_id_returns_400(
        self,
        created_conversations,
    ) -> None:
        conversation_id = self._stream_create_agent_conversation_id(
            self.primary_agent,
            query=f"integration: agent message feedback bad message id-{uuid4().hex}",
            created_conversations=created_conversations,
        )
        url = self._feedback_url(self.primary_agent, conversation_id, "bad-id")
        resp = requests.post(
            url,
            headers=self.headers,
            json=_MINIMAL_FEEDBACK_PAYLOAD,
            timeout=self.timeout,
        )
        self._assert_validation_error(resp)
