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
import sys
import uuid
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

from helper.agui_sse import (
    AGUI,
    StreamOutcome,
    answer_from_completion,
    conversation_created_value,
    is_conversation_created,
    is_root_error,
    is_root_finished,
    iter_sse_envelopes,
    run_error_message,
    run_finished_result,
    state_delta_answer,
)
from helper.clients.agents_client import AgentsClient
from helper.clients.conversations_client import AgentConversationsClient
from helper.conversation_seeds import seed_query
from openapi_schema_validator import (
    assert_request_body_matches_openapi_operation,
    assert_response_matches_openapi_ref,
)

SEARCH_QUERY = "every year asana undertakes which exercise?"

# Lowercase keywords a KB-grounded answer to SEARCH_QUERY must contain.
KB_ANSWER_KEYWORDS = ("disaster recovery",)

_AGENT_STREAM_SSE_EVENT_REF = "#/components/schemas/AgentStreamSSEEvent"
_AGENT_MESSAGE_STREAM_SSE_EVENT_REF = "#/components/schemas/AgentMessageStreamSSEEvent"


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
        agent_conversations_client: AgentConversationsClient,
        agents_client: AgentsClient,
        session_kb: dict,
        agent_session: dict[str, Any],
    ) -> None:
        self.conversations = agent_conversations_client
        self.agents = agents_client
        self.kb_id = session_kb["kb_id"]
        self.agent_session = agent_session
        self.timeout = int(os.getenv("PIPESHUB_TEST_TIMEOUT", "60"))
        stream_override = os.getenv("PIPESHUB_TEST_STREAM_TIMEOUT", "").strip()
        self.stream_timeout = (
            int(stream_override)
            if stream_override
            else max(self.timeout, 120)
        )

    def _stream_agent_conversation(
        self,
        agent_key: str,
        *,
        query: str,
        allow_error: bool = False,
    ) -> StreamOutcome:
        """Stream a new agent conversation.

        When ``allow_error`` is True, ``RUN_ERROR`` does not raise (for negative tests)
        and is reported on the returned outcome instead.
        """
        created_conv_id: str | None = None
        accumulated_answer = ""
        saw_finished = False
        error_message: str | None = None

        with self.conversations.stream_conversation(
            agent_key,
            query=query,
            timeout=self.stream_timeout,
        ) as resp:
            assert resp.status_code == 200, f"{resp.status_code}: {resp.text}"

            content_type = (resp.headers.get("Content-Type") or "").lower()
            assert "text/event-stream" in content_type, (
                f"expected text/event-stream, got Content-Type={resp.headers.get('Content-Type')!r}"
            )

            for envelope in iter_sse_envelopes(resp):
                assert_response_matches_openapi_ref(envelope, _AGENT_STREAM_SSE_EVENT_REF)
                event = envelope["event"]
                payload = json.loads(envelope["data"])

                if event == AGUI.CUSTOM and is_conversation_created(payload):
                    conv_id = conversation_created_value(payload).get("conversationId")
                    if isinstance(conv_id, str) and conv_id:
                        created_conv_id = conv_id
                    continue

                if event == AGUI.STATE_DELTA:
                    acc = state_delta_answer(payload)
                    if acc is not None:
                        accumulated_answer = acc
                    continue

                if is_root_error(event, payload):
                    if not allow_error:
                        raise AssertionError(f"stream emitted RUN_ERROR: {payload!r}")
                    error_message = run_error_message(payload)
                    continue

                if not is_root_finished(event, payload):
                    continue

                saw_finished = True
                result = run_finished_result(payload)
                if not accumulated_answer.strip():
                    accumulated_answer = answer_from_completion(result)

                finished_conv = result.get("conversation") or {}
                finished_conv_id = finished_conv.get("_id")
                if isinstance(finished_conv_id, str) and finished_conv_id:
                    if created_conv_id and created_conv_id != finished_conv_id:
                        raise AssertionError(
                            f"conversation_created conversationId {created_conv_id!r} != "
                            f"RUN_FINISHED conversation._id {finished_conv_id!r}"
                        )
                    created_conv_id = finished_conv_id
                break

        return StreamOutcome(created_conv_id, accumulated_answer, saw_finished, error_message)

    def _create_agent_conversation_id(self, agent_key: str, *, query: str) -> str:
        outcome = self._stream_agent_conversation(agent_key, query=query)
        assert outcome.finished, "stream ended without a RUN_FINISHED event"
        assert outcome.conversation_id, "stream did not yield a conversation id"
        return outcome.conversation_id

    def _stream_add_message(
        self,
        agent_key: str,
        conversation_id: str,
        *,
        json_body: dict[str, Any] | None = None,
        query: str | None = None,
        params: dict[str, str] | None = None,
        allow_error: bool = False,
    ) -> StreamOutcome:
        """Stream a follow-up message on an existing agent conversation.

        ``conversation_id`` on the returned outcome is always the one passed in —
        the add-message routes stream onto a conversation the caller already named,
        so their ``conversation_created`` frame carries no id.
        """
        options: dict[str, Any] = {"timeout": self.stream_timeout}
        if params is not None:
            options["params"] = params

        accumulated_answer = ""
        saw_finished = False
        error_message: str | None = None
        finished_result: dict[str, Any] | None = None

        with self.conversations.stream_message(
            agent_key,
            conversation_id,
            query=query,
            json=json_body,
            **options,
        ) as resp:
            assert resp.status_code == 200, f"{resp.status_code}: {resp.text}"

            content_type = (resp.headers.get("Content-Type") or "").lower()
            assert "text/event-stream" in content_type, (
                f"expected text/event-stream, got Content-Type={resp.headers.get('Content-Type')!r}"
            )

            for envelope in iter_sse_envelopes(resp):
                assert_response_matches_openapi_ref(
                    envelope, _AGENT_MESSAGE_STREAM_SSE_EVENT_REF
                )
                event = envelope["event"]
                payload = json.loads(envelope["data"])

                if event == AGUI.STATE_DELTA:
                    acc = state_delta_answer(payload)
                    if acc is not None:
                        accumulated_answer = acc
                    continue

                if is_root_error(event, payload):
                    if not allow_error:
                        raise AssertionError(f"stream emitted RUN_ERROR: {payload!r}")
                    error_message = run_error_message(payload)
                    continue

                if not is_root_finished(event, payload):
                    continue

                saw_finished = True
                finished_result = run_finished_result(payload)
                if not accumulated_answer.strip():
                    accumulated_answer = answer_from_completion(finished_result)
                break

        return StreamOutcome(
            conversation_id, accumulated_answer, saw_finished, error_message, finished_result
        )


@pytest.mark.integration
class TestAgentConversationStreamOpenApiRequestContract:
    """Offline OpenAPI request-body checks for streamAgentConversation."""

    def test_minimal_request_body_matches_openapi_spec(self) -> None:
        assert_request_body_matches_openapi_operation(
            {"query": "hello", "chatMode": "quick"},
            "streamAgentConversation",
        )

    def test_rejects_previous_conversations_offline(self) -> None:
        with pytest.raises(AssertionError):
            assert_request_body_matches_openapi_operation(
                {
                    "query": "hi",
                    "chatMode": "quick",
                    "previousConversations": [],
                },
                "streamAgentConversation",
            )

    def test_rejects_quick_mode_offline(self) -> None:
        with pytest.raises(AssertionError):
            assert_request_body_matches_openapi_operation(
                {"query": "hi", "chatMode": "quick", "quickMode": True},
                "streamAgentConversation",
            )

    def test_follow_up_requires_quick_mode_offline(self) -> None:
        assert_request_body_matches_openapi_operation(
            {"query": "hello", "chatMode": "quick"},
            "streamAgentConversationMessage",
        )

        for payload in (
            {"query": "missing mode"},
            {"query": "unsupported mode", "chatMode": "agent"},
        ):
            with pytest.raises(AssertionError):
                assert_request_body_matches_openapi_operation(
                    payload,
                    "streamAgentConversationMessage",
                )


@pytest.mark.integration
class TestAgentConversationStream(_AgentStreamTestBase):

    # ------------------------------------------------------------------------
    # Positive tests
    # ------------------------------------------------------------------------

    def test_stream_agent_conversation_matches_spec_and_answers_from_kb(self) -> None:
        """One KB-grounded stream, asserted three ways.

        ``_stream_agent_conversation`` validates every envelope against
        ``AgentStreamSSEEvent`` and the SSE content type as it reads, so the schema
        contract, the completion contract and the answer quality all come off a single
        agent run rather than three.
        """
        outcome = self._stream_agent_conversation(
            self.agent_session["primary_agent"],
            query=SEARCH_QUERY,
        )

        assert outcome.finished, "stream ended without a RUN_FINISHED event"
        assert outcome.conversation_id, "stream did not yield a conversation id"
        assert outcome.answer.strip(), "stream finished but answer text was empty"
        assert any(kw in outcome.answer.lower() for kw in KB_ANSWER_KEYWORDS), (
            f"answer did not contain any of {KB_ANSWER_KEYWORDS!r} for "
            f"query={SEARCH_QUERY!r}: {outcome.answer[:500]!r}"
        )

    # ------------------------------------------------------------------------
    # Negative tests
    # ------------------------------------------------------------------------

    @pytest.mark.parametrize(
        "payload",
        [
            {},
            {"query": "missing mode"},
            {"query": "", "chatMode": "quick"},
            {"query": "unsupported mode", "chatMode": "auto"},
            {"query": "unsupported mode", "chatMode": "deep"},
            {"query": "unsupported mode", "chatMode": "planExecute"},
            {"query": "unsupported mode", "chatMode": "verification"},
        ],
    )
    def test_stream_agent_conversation_invalid_payload_returns_400(
        self, payload: dict
    ) -> None:
        agent_key = self.agent_session["primary_agent"]

        resp = self.conversations.stream_conversation(
            agent_key,
            json=payload,
            stream=False,
            timeout=self.timeout,
        )
        _assert_validation_error(resp)

    def test_stream_agent_conversation_missing_auth_returns_401_or_403(self) -> None:
        agent_key = self.agent_session["primary_agent"]

        resp = self.conversations.stream_conversation(
            agent_key,
            query=SEARCH_QUERY,
            stream=False,
            auth=False,
            timeout=self.timeout,
        )
        assert resp.status_code in (401, 403), f"{resp.status_code}: {resp.text}"

    def test_stream_agent_conversation_unknown_agent_emits_error(self) -> None:
        unknown_key = str(uuid.uuid4())

        outcome = self._stream_agent_conversation(
            unknown_key,
            query=SEARCH_QUERY,
            allow_error=True,
        )

        # Assert the RUN_ERROR positively: `not finished` alone is also true when
        # the stream hangs or dies silently, which would make this test unfailable.
        assert outcome.errored, (
            f"expected a RUN_ERROR for unknown agent; conv_id={outcome.conversation_id!r}, "
            f"finished={outcome.finished}, answer={outcome.answer[:200]!r}"
        )
        assert not outcome.finished, (
            f"expected no RUN_FINISHED for unknown agent; error={outcome.error!r}"
        )

    def test_stream_agent_conversation_deleted_agent_fails(self) -> None:
        # disposable_agent exists to be destroyed here. Deleting a shared secondary key
        # would leave every later "some other agent" probe pointing at a corpse, and the
        # gateway answers identically for a missing agent and one that merely does not
        # own the conversation — so those probes would pass without proving anything.
        deleted_key = self.agent_session["disposable_agent"]

        delete_resp = self.agents.delete_agent(deleted_key, timeout=self.timeout)
        assert delete_resp.status_code < 300, (
            f"agent delete failed: {delete_resp.status_code}: {delete_resp.text}"
        )

        outcome = self._stream_agent_conversation(
            deleted_key,
            query=SEARCH_QUERY,
            allow_error=True,
        )

        assert outcome.errored, (
            f"expected a RUN_ERROR for deleted agent {deleted_key}; "
            f"conv_id={outcome.conversation_id!r}, finished={outcome.finished}, "
            f"answer={outcome.answer[:200]!r}"
        )
        assert not outcome.finished, (
            f"expected no RUN_FINISHED for deleted agent {deleted_key}; error={outcome.error!r}"
        )


@pytest.mark.integration
class TestAgentConversationMessageStream(_AgentStreamTestBase):

    # ------------------------------------------------------------------------
    # Positive tests
    # ------------------------------------------------------------------------

    def test_add_message_stream_matches_spec_answers_from_kb_and_appends(self) -> None:
        """One cheap seed plus one KB-grounded follow-up, asserted four ways.

        ``_stream_add_message`` validates every envelope against
        ``AgentMessageStreamSSEEvent`` and the SSE content type as it reads, and returns
        the RUN_FINISHED result, so the schema contract, completion, answer quality and
        the appended-message count all come off a single follow-up run.
        """
        agent_key = self.agent_session["primary_agent"]
        conversation_id = self._create_agent_conversation_id(
            agent_key,
            query=seed_query(uuid.uuid4().hex),
        )

        outcome = self._stream_add_message(
            agent_key,
            conversation_id,
            query=SEARCH_QUERY,
        )

        assert outcome.finished, "stream ended without a RUN_FINISHED event"
        assert outcome.answer.strip(), "stream finished but answer text was empty"
        assert any(kw in outcome.answer.lower() for kw in KB_ANSWER_KEYWORDS), (
            f"answer did not contain any of {KB_ANSWER_KEYWORDS!r} for "
            f"query={SEARCH_QUERY!r}: {outcome.answer[:500]!r}"
        )

        result = outcome.result or {}
        conv = result.get("conversation")
        assert isinstance(conv, dict), (
            f"RUN_FINISHED result carried no conversation object: {result!r}"
        )
        assert conv.get("_id") == conversation_id, (
            f"RUN_FINISHED conversation id mismatch: {conv.get('_id')!r}"
        )
        messages = conv.get("messages")
        assert isinstance(messages, list), (
            f"RUN_FINISHED conversation.messages was not a list: {messages!r}"
        )
        non_empty_contents = [
            m.get("content") for m in messages
            if isinstance(m, dict) and (m.get("content") or "").strip()
        ]
        assert len(non_empty_contents) >= 2, (
            f"expected at least 2 non-empty message contents, got {len(non_empty_contents)}"
        )

    def test_add_message_stream_quick_mode_completes(self) -> None:
        agent_key = self.agent_session["primary_agent"]
        conversation_id = self._create_agent_conversation_id(
            agent_key,
            query=seed_query(uuid.uuid4().hex),
        )

        outcome = self._stream_add_message(
            agent_key,
            conversation_id,
            json_body={
                "query": "follow-up question with quick chatMode",
                "chatMode": "quick",
            },
        )

        assert outcome.finished, "stream ended without a RUN_FINISHED event"
        assert outcome.answer.strip(), "stream finished but answer text was empty"

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
            query=seed_query(uuid.uuid4().hex),
        )
        body = dict(json_body)
        body["chatMode"] = "quick"
        if label == "filters kb":
            body["filters"] = {"kb": [self.kb_id]}

        outcome = self._stream_add_message(
            agent_key,
            conversation_id,
            json_body=body,
        )
        assert outcome.finished, f"[{label}] stream ended without a RUN_FINISHED event"
        assert outcome.answer.strip(), f"[{label}] stream finished but answer text was empty"

    def test_add_message_stream_ignores_benign_query_param(self) -> None:
        agent_key = self.agent_session["primary_agent"]
        conversation_id = self._create_agent_conversation_id(
            agent_key,
            query="stream-create conversation for benign query param test",
        )

        outcome = self._stream_add_message(
            agent_key,
            conversation_id,
            query="follow-up with debug query param",
            params={"debug": "1"},
        )

        assert outcome.finished, "stream ended without a RUN_FINISHED event"
        assert outcome.answer.strip(), "stream finished but answer text was empty"

    # ------------------------------------------------------------------------
    # Negative tests — path params and auth
    # ------------------------------------------------------------------------

    def test_add_message_invalid_conversation_id_returns_400(self) -> None:
        agent_key = self.agent_session["primary_agent"]

        resp = self.conversations.stream_message(
            agent_key,
            "not-an-objectid",
            query="hi",
            stream=False,
            timeout=self.timeout,
        )
        _assert_validation_error(resp)

    def test_add_message_missing_auth_returns_401_or_403(self) -> None:
        agent_key = self.agent_session["primary_agent"]
        conversation_id = "0" * 24

        resp = self.conversations.stream_message(
            agent_key,
            conversation_id,
            query="hi",
            stream=False,
            auth=False,
            timeout=self.timeout,
        )
        assert resp.status_code in (401, 403), f"{resp.status_code}: {resp.text}"

    def test_add_message_nonexistent_conversation_emits_error(self) -> None:
        agent_key = self.agent_session["primary_agent"]

        with self.conversations.stream_message(
            agent_key,
            "0" * 24,
            query="hi",
            timeout=self.stream_timeout,
        ) as resp:
            if resp.status_code != 200:
                assert resp.status_code == 404, f"{resp.status_code}: {resp.text}"
                return

            for envelope in iter_sse_envelopes(resp):
                payload = json.loads(envelope["data"])
                if not is_root_error(envelope["event"], payload):
                    continue
                msg = run_error_message(payload)
                assert "not found" in msg.lower(), (
                    f"unexpected RUN_ERROR payload: {payload!r}"
                )
                return

        raise AssertionError("stream ended without a RUN_ERROR event")

    def test_add_message_wrong_agent_key_emits_error(
        self,
        readonly_agent_conversation: dict[str, str],
    ) -> None:
        # secondary_agents[0] is live but does not own this conversation — that is the
        # property under test, and it only holds because the delete-agent test consumes
        # disposable_agent rather than a shared secondary key.
        wrong_key = self.agent_session["secondary_agents"][0]

        outcome = self._stream_add_message(
            wrong_key,
            readonly_agent_conversation["conversation_id"],
            query=SEARCH_QUERY,
            allow_error=True,
        )

        # Assert the RUN_ERROR positively: `not finished` alone is also true when
        # the stream hangs or dies silently, which would make this test unfailable.
        assert outcome.errored, (
            f"expected a RUN_ERROR for wrong agent key; finished={outcome.finished}, "
            f"answer={outcome.answer[:200]!r}"
        )
        assert not outcome.finished, (
            f"expected no RUN_FINISHED for wrong agent key; error={outcome.error!r}"
        )

    def test_add_message_unknown_agent_emits_error(self) -> None:
        unknown_key = str(uuid.uuid4())
        conversation_id = "0" * 24

        saw_finished = False
        saw_error = False
        with self.conversations.stream_message(
            unknown_key,
            conversation_id,
            query=SEARCH_QUERY,
            timeout=self.stream_timeout,
        ) as resp:
            assert resp.status_code == 200, f"{resp.status_code}: {resp.text}"

            for envelope in iter_sse_envelopes(resp):
                assert_response_matches_openapi_ref(
                    envelope, _AGENT_MESSAGE_STREAM_SSE_EVENT_REF
                )
                payload = json.loads(envelope["data"])
                if is_root_error(envelope["event"], payload):
                    saw_error = True
                    break
                if is_root_finished(envelope["event"], payload):
                    saw_finished = True
                    break

        assert saw_error, (
            f"expected a RUN_ERROR for unknown agent; saw_finished={saw_finished}"
        )
        assert not saw_finished, "expected no RUN_FINISHED for unknown agent"

    # ------------------------------------------------------------------------
    # Negative tests — body (Zod)
    # ------------------------------------------------------------------------

    @pytest.mark.parametrize(
        ("label", "payload"),
        [
            ("missing query", {}),
            ("missing chatMode", {"query": "x"}),
            ("empty query", {"query": "", "chatMode": "quick"}),
            (
                "invalid filters kb",
                {
                    "query": "x",
                    "chatMode": "quick",
                    "filters": {"kb": ["bad-id"]},
                },
            ),
            (
                "invalid currentTime",
                {
                    "query": "x",
                    "chatMode": "quick",
                    "currentTime": "not-an-iso-datetime",
                },
            ),
            ("empty chatMode", {"query": "x", "chatMode": ""}),
            ("auto chatMode", {"query": "x", "chatMode": "auto"}),
            ("deep chatMode", {"query": "x", "chatMode": "deep"}),
            (
                "planExecute chatMode",
                {"query": "x", "chatMode": "planExecute"},
            ),
            (
                "verification chatMode",
                {"query": "x", "chatMode": "verification"},
            ),
            (
                "internal_search chatMode",
                {"query": "x", "chatMode": "internal_search"},
            ),
            (
                "attachment missing recordId",
                {
                    "query": "x",
                    "chatMode": "quick",
                    "attachments": [{"recordName": "only-name"}],
                },
            ),
            (
                "empty modelKey",
                {"query": "x", "chatMode": "quick", "modelKey": ""},
            ),
        ],
    )
    def test_add_message_invalid_body_returns_400(
        self,
        label: str,
        payload: dict[str, Any],
    ) -> None:
        # A placeholder id is fine since validation runs before the lookup.
        resp = self.conversations.stream_message(
            self.agent_session["primary_agent"],
            "0" * 24,
            json=payload,
            stream=False,
            timeout=self.timeout,
        )
        _assert_validation_error(resp, label=label)
