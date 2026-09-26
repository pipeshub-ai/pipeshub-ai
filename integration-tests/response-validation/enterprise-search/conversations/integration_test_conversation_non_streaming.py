"""Non-streaming conversation integration tests (live stack).

``POST /api/v1/conversations/create``
``POST /api/v1/conversations/{conversationId}/messages``
``POST /api/v1/agents/{agentKey}/conversations``
``POST /api/v1/agents/{agentKey}/conversations/{conversationId}/messages``

Each route runs the same agent-loop pipeline as its ``/stream`` twin and answers
with one JSON body, so every positive test checks three things off a single run:
the body matches the OpenAPI operation, the conversation was persisted
(re-read through ``GET``), and the answer is grounded in the session KB.

Requires ``session_kb``, ``agent_session``, ``reasoning_multimodal_llm_model`` and
``readonly_agent_conversation`` from ``response-validation/enterprise-search/conftest.py``.

``PIPESHUB_TEST_STREAM_TIMEOUT`` (optional): seconds to wait for a full answer;
otherwise ``max(PIPESHUB_TEST_TIMEOUT, 180)``.
"""

from __future__ import annotations

import os
import sys
import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
import requests

_ROOT = Path(__file__).resolve().parents[3]
_RV_HELPER = _ROOT / "response-validation" / "helper"
for _p in (_ROOT, _RV_HELPER):
    s = str(_p)
    if s not in sys.path:
        sys.path.insert(0, s)

from ai_models_setup import SeededAIModel
from helper.clients.conversations_client import AgentConversationsClient, ConversationsClient
from helper.conversation_seeds import seed_query
from openapi_schema_validator import (
    assert_request_body_matches_openapi_operation,
    assert_response_matches_openapi_operation,
    assert_response_matches_openapi_ref,
)

SEARCH_QUERY = "every year asana undertakes which exercise?"
KB_ANSWER_KEYWORDS = ("disaster recovery",)
CONVERSATION_ID_HEADER = "X-Conversation-Id"
_MISSING_CONVERSATION_ID = "507f1f77bcf86cd799439011"


def _json(resp: requests.Response) -> dict[str, Any]:
    try:
        body = resp.json()
    except ValueError as exc:
        raise AssertionError(f"expected JSON, got {resp.status_code}: {resp.text[:500]}") from exc
    assert isinstance(body, dict), f"expected a JSON object, got {body!r}"
    return body


def _messages(body: dict[str, Any]) -> list[dict[str, Any]]:
    conversation = body.get("conversation")
    assert isinstance(conversation, dict), f"missing conversation: {body!r}"
    return [m for m in conversation.get("messages") or [] if isinstance(m, dict)]


def _last_answer(body: dict[str, Any]) -> str:
    messages = _messages(body)
    assert messages, f"conversation has no messages: {body!r}"
    last = messages[-1]
    assert last.get("messageType") == "bot_response", f"last message is not an answer: {last!r}"
    content = last.get("content")
    assert isinstance(content, str) and content.strip(), f"empty answer: {last!r}"
    return content


def _assert_grounded(answer: str) -> None:
    assert any(k in answer.lower() for k in KB_ANSWER_KEYWORDS), (
        f"answer did not contain any of {KB_ANSWER_KEYWORDS!r}: {answer[:500]!r}"
    )


def _assert_validation_error(resp: requests.Response) -> None:
    assert resp.status_code == 400, f"{resp.status_code}: {resp.text}"
    assert_response_matches_openapi_ref(_json(resp), "#/components/schemas/ErrorResponse")


@pytest.mark.integration
class TestNonStreamingOpenApiRequestContract:
    """Offline: the documented request bodies accept what the gateway accepts."""

    @pytest.mark.parametrize(
        ("operation_id", "body"),
        [
            ("createConversation", {"query": "hello"}),
            ("createConversation", {"query": "hello", "chatMode": "agent", "tools": ["jira.search"]}),
            ("addMessage", {"query": "and then?"}),
            ("createAgentConversation", {"query": "hello"}),
            ("createAgentConversation", {"query": "hello", "chatMode": "quick"}),
            ("addAgentConversationMessage", {"query": "and then?"}),
        ],
    )
    def test_documented_request_bodies_are_accepted(self, operation_id: str, body: dict) -> None:
        assert_request_body_matches_openapi_operation(body, operation_id)

    @pytest.mark.parametrize(
        ("operation_id", "body"),
        [
            ("createConversation", {}),
            ("createAgentConversation", {"query": "hello", "chatMode": "agent"}),
            ("addAgentConversationMessage", {"query": ""}),
        ],
    )
    def test_invalid_request_bodies_are_rejected(self, operation_id: str, body: dict) -> None:
        with pytest.raises(AssertionError):
            assert_request_body_matches_openapi_operation(body, operation_id)


class _Base:
    @pytest.fixture(autouse=True)
    def _setup(
        self,
        conversations_client: ConversationsClient,
        agent_conversations_client: AgentConversationsClient,
        agent_session: dict[str, Any],
        reasoning_multimodal_llm_model: SeededAIModel,
        session_kb: dict,
    ) -> Iterator[None]:
        self.conversations = conversations_client
        self.agent_conversations = agent_conversations_client
        self.agent_session = agent_session
        self.reasoning_model = reasoning_multimodal_llm_model
        self.timeout = int(os.getenv("PIPESHUB_TEST_TIMEOUT", "60"))
        override = os.getenv("PIPESHUB_TEST_STREAM_TIMEOUT", "").strip()
        self.answer_timeout = int(override) if override else max(self.timeout, 180)
        self._created: list[tuple[str | None, str]] = []
        yield
        for agent_key, conversation_id in reversed(self._created):
            try:
                if agent_key:
                    self.agent_conversations.delete_conversation(agent_key, conversation_id)
                else:
                    self.conversations.delete_conversation(conversation_id)
            except Exception:  # noqa: BLE001 - best-effort cleanup
                pass

    def _track(self, resp: requests.Response, agent_key: str | None = None) -> str | None:
        conversation_id = resp.headers.get(CONVERSATION_ID_HEADER)
        if conversation_id:
            self._created.append((agent_key, conversation_id))
        return conversation_id


@pytest.mark.integration
class TestConversationNonStreaming(_Base):
    def _create(self, **body: Any) -> requests.Response:
        resp = self.conversations.create_conversation(json=body, timeout=self.answer_timeout)
        self._track(resp)
        return resp

    def test_create_returns_the_persisted_conversation_and_a_grounded_answer(self) -> None:
        resp = self._create(query=SEARCH_QUERY, chatMode="internal_search")

        assert resp.status_code == 201, f"{resp.status_code}: {resp.text}"
        body = _json(resp)
        assert_response_matches_openapi_operation(body, "createConversation", status_code="201")
        conversation_id = body["conversation"]["_id"]
        assert resp.headers.get(CONVERSATION_ID_HEADER) == conversation_id
        _assert_grounded(_last_answer(body))

        stored = self.conversations.get_conversation(conversation_id, timeout=self.timeout)
        assert stored.status_code == 200, f"{stored.status_code}: {stored.text}"
        stored_body = _json(stored)
        assert stored_body["conversation"]["status"] == "Complete"
        assert [m["messageType"] for m in _messages(stored_body)][-2:] == ["user_query", "bot_response"]

    def test_follow_up_appends_one_turn_and_reports_records_used(self) -> None:
        created = self._create(query=SEARCH_QUERY, chatMode="internal_search")
        assert created.status_code == 201, f"{created.status_code}: {created.text}"
        conversation_id = _json(created)["conversation"]["_id"]
        before = len(_messages(_json(created)))

        resp = self.conversations.add_message(
            conversation_id,
            json={"query": "In one sentence, why does that exercise matter?", "chatMode": "internal_search"},
            timeout=self.answer_timeout,
        )

        assert resp.status_code == 200, f"{resp.status_code}: {resp.text}"
        body = _json(resp)
        assert_response_matches_openapi_operation(body, "addMessage", status_code="200")
        assert isinstance(body["recordsUsed"], int) and body["recordsUsed"] >= 0
        assert body["meta"]["recordsUsed"] == body["recordsUsed"]
        _last_answer(body)
        assert len(_messages(body)) == before + 2, "the response must carry the whole conversation"

        stored = _json(self.conversations.get_conversation(conversation_id, timeout=self.timeout))
        assert len(_messages(stored)) == before + 2

    def test_agent_mode_runs_the_universal_agent(self) -> None:
        resp = self._create(
            query="What is two plus two? Answer with just the number.",
            chatMode="agent",
            modelKey=self.reasoning_model.model_key,
            modelName=self.reasoning_model.model_name,
        )

        assert resp.status_code == 201, f"{resp.status_code}: {resp.text}"
        assert "4" in _last_answer(_json(resp))

    @pytest.mark.parametrize(
        "body",
        [
            {},
            {"query": ""},
            {"query": "<script>alert(1)</script>"},
            {"query": "hello", "chatMode": "not-a-mode"},
            {"query": "hello", "recordIds": ["not-an-object-id"]},
        ],
    )
    def test_invalid_payload_returns_400(self, body: dict) -> None:
        resp = self.conversations.create_conversation(json=body, timeout=self.timeout)
        assert resp.status_code == 400, f"{resp.status_code}: {resp.text}"
        assert CONVERSATION_ID_HEADER not in resp.headers, "a rejected request must not create a conversation"

    def test_missing_auth_returns_401_or_403(self) -> None:
        resp = self.conversations.create_conversation(query=SEARCH_QUERY, auth=False, timeout=self.timeout)
        assert resp.status_code in (401, 403), f"{resp.status_code}: {resp.text}"

    def test_follow_up_on_unknown_conversation_returns_404(self) -> None:
        resp = self.conversations.add_message(
            _MISSING_CONVERSATION_ID, query="hello", timeout=self.timeout
        )
        assert resp.status_code == 404, f"{resp.status_code}: {resp.text}"

    def test_assistant_route_cannot_reach_an_agent_conversation(
        self, readonly_agent_conversation: dict[str, str]
    ) -> None:
        resp = self.conversations.add_message(
            readonly_agent_conversation["conversation_id"], query="hello", timeout=self.timeout
        )
        assert resp.status_code == 404, f"{resp.status_code}: {resp.text}"


@pytest.mark.integration
class TestAgentConversationNonStreaming(_Base):
    def _create(self, agent_key: str, **body: Any) -> requests.Response:
        resp = self.agent_conversations.create_conversation(
            agent_key, json=body, timeout=self.answer_timeout
        )
        self._track(resp, agent_key)
        return resp

    def test_create_returns_the_persisted_conversation_and_a_grounded_answer(self) -> None:
        agent_key = self.agent_session["primary_agent"]

        resp = self._create(agent_key, query=SEARCH_QUERY)

        assert resp.status_code == 201, f"{resp.status_code}: {resp.text}"
        body = _json(resp)
        assert_response_matches_openapi_operation(body, "createAgentConversation", status_code="201")
        conversation_id = body["conversation"]["_id"]
        assert resp.headers.get(CONVERSATION_ID_HEADER) == conversation_id
        assert body["conversation"].get("agentKey") == agent_key
        _assert_grounded(_last_answer(body))

        stored = self.agent_conversations.get_conversation(agent_key, conversation_id, timeout=self.timeout)
        assert stored.status_code == 200, f"{stored.status_code}: {stored.text}"
        assert _json(stored)["conversation"]["status"] == "Complete"

    def test_follow_up_appends_one_turn(self) -> None:
        agent_key = self.agent_session["workhorse_agent"]
        created = self._create(agent_key, query=seed_query(uuid.uuid4().hex), chatMode="quick")
        assert created.status_code == 201, f"{created.status_code}: {created.text}"
        conversation_id = _json(created)["conversation"]["_id"]

        resp = self.agent_conversations.add_message(
            agent_key, conversation_id, query="Reply with OK again.", timeout=self.answer_timeout
        )

        assert resp.status_code == 200, f"{resp.status_code}: {resp.text}"
        body = _json(resp)
        assert_response_matches_openapi_operation(body, "addAgentConversationMessage", status_code="200")
        _last_answer(body)
        stored = _json(self.agent_conversations.get_conversation(agent_key, conversation_id, timeout=self.timeout))
        assert [m["messageType"] for m in _messages(stored)][-4:] == [
            "user_query", "bot_response", "user_query", "bot_response",
        ]

    @pytest.mark.parametrize(
        "body",
        [
            {},
            {"query": ""},
            {"query": "hello", "chatMode": "agent"},
            {"query": "hello", "runId": "not-a-uuid"},
        ],
    )
    def test_invalid_payload_returns_400(self, body: dict) -> None:
        resp = self.agent_conversations.create_conversation(
            self.agent_session["workhorse_agent"], json=body, timeout=self.timeout
        )
        _assert_validation_error(resp)

    def test_other_agents_conversation_returns_404(
        self, readonly_agent_conversation: dict[str, str]
    ) -> None:
        other_agent = self.agent_session["secondary_agents"][0]
        resp = self.agent_conversations.add_message(
            other_agent, readonly_agent_conversation["conversation_id"], query="hello", timeout=self.timeout
        )
        assert resp.status_code == 404, f"{resp.status_code}: {resp.text}"

    def test_unknown_agent_fails_the_turn_and_names_the_conversation(self) -> None:
        missing_agent = f"missing-agent-{uuid.uuid4().hex[:8]}"

        resp = self._create(missing_agent, query="hello")

        assert 400 <= resp.status_code < 500, f"{resp.status_code}: {resp.text}"
        conversation_id = resp.headers.get(CONVERSATION_ID_HEADER)
        assert conversation_id, "a failed turn must name the conversation it left behind"
        # The GET is a Mongo lookup scoped by agentKey, so it finds the turn saved under the
        # unknown key; anything but 200 means the failed turn was not persisted.
        stored = self.agent_conversations.get_conversation(missing_agent, conversation_id, timeout=self.timeout)
        assert stored.status_code == 200, f"{stored.status_code}: {stored.text}"
        stored_body = _json(stored)
        assert stored_body["conversation"]["status"] == "Failed"
        assert _messages(stored_body)[-1].get("messageType") == "error"
