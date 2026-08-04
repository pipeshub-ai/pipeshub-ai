"""Agent conversation update/delete integration tests.

``PATCH /api/v1/agents/{agentKey}/conversations/{conversationId}/title``
``DELETE /api/v1/agents/{agentKey}/conversations/{conversationId}``
``POST /api/v1/agents/{agentKey}/conversations/{conversationId}/archive``
``POST /api/v1/agents/{agentKey}/conversations/{conversationId}/unarchive``
``POST /api/v1/agents/{agentKey}/conversations/{conversationId}/message/{messageId}/regenerate``

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
_RV_HELPER = _ROOT / "response-validation" / "helper"
for _p in (_ROOT, _RV_HELPER):
    s = str(_p)
    if s not in sys.path:
        sys.path.insert(0, s)

from helper.agui_sse import (
    is_root_error,
    is_root_finished,
    iter_sse_envelopes,
    run_error_message,
    run_finished_result,
)
from helper.clients.conversations_client import AgentConversationsClient
from openapi_search_validator import assert_matches_component_schema
from openapi_schema_validator import (
    assert_request_body_matches_openapi_operation,
    assert_response_matches_openapi_operation,
    assert_response_matches_openapi_ref,
)


# Rich optional-field body for offline OpenAPI request validation. Omits `filters`
# because `Filters.apps` / `Filters.kb` use a oneOf that jsonschema rejects for
# typical test ids (documented OpenAPI/schema quirk; live gateway still accepts them).
_RICH_REGENERATE_REQUEST_OPENAPI_PAYLOAD: dict[str, Any] = {
    "chatMode": "quick",
    "modelKey": "model-key",
    "modelName": "model-name",
    "modelFriendlyName": "Model Friendly Name",
    "timezone": "Asia/Kolkata",
    "currentTime": "2026-05-27T10:30:00+05:30",
    "tools": ["web_search", "calculator"],
}


def _response_json(resp: requests.Response) -> dict[str, Any]:
    try:
        data = resp.json()
    except ValueError as exc:
        raise AssertionError(
            f"Expected JSON response, got status={resp.status_code}: {resp.text[:500]}"
        ) from exc
    assert isinstance(data, dict), f"Expected dict JSON body, got: {data!r}"
    return data


class AgentConversationsTestBase:
    """Shared agent_conversations_client fixture and stream helpers."""

    @pytest.fixture(autouse=True)
    def _setup(
        self,
        agent_conversations_client: AgentConversationsClient,
        agent_session: dict[str, Any],
    ) -> None:
        self.conversations = agent_conversations_client
        self.timeout = int(os.getenv("PIPESHUB_TEST_TIMEOUT", "60"))
        stream_override = os.getenv("PIPESHUB_TEST_STREAM_TIMEOUT", "").strip()
        self.stream_timeout = (
            int(stream_override)
            if stream_override
            else max(self.timeout, 120)
        )
        self.primary_agent = agent_session["primary_agent"]
        self.secondary_agents = list(agent_session["secondary_agents"])

    @pytest.fixture
    def created_conversations(self) -> Iterator[list[tuple[str, str]]]:
        created: list[tuple[str, str]] = []
        yield created
        for agent_key, conversation_id in reversed(created):
            try:
                resp = self.conversations.delete_conversation(
                    agent_key,
                    conversation_id,
                    timeout=self.timeout,
                )
                if resp.status_code >= 300:
                    pass
            except Exception:
                pass

    def _stream_create_agent_conversation_id(
        self,
        agent_key: str,
        *,
        query: str,
        created_conversations: list[tuple[str, str]],
    ) -> str:
        with self.conversations.stream_conversation(
            agent_key,
            query=query,
            timeout=self.stream_timeout,
        ) as resp:
            assert resp.status_code == 200, f"{resp.status_code}: {resp.text}"

            for envelope in iter_sse_envelopes(resp):
                payload = json.loads(envelope["data"])
                if is_root_error(envelope["event"], payload):
                    raise AssertionError(f"stream emitted RUN_ERROR: {payload!r}")
                if not is_root_finished(envelope["event"], payload):
                    continue

                result = run_finished_result(payload)
                conversation = result.get("conversation") or {}
                conversation_id = conversation.get("_id")
                assert isinstance(conversation_id, str) and conversation_id, (
                    f"RUN_FINISHED result missing conversation._id: {result!r}"
                )
                created_conversations.append((agent_key, conversation_id))
                return conversation_id

        raise AssertionError("agent conversation stream ended without a RUN_FINISHED event")

    def _get_conversation_messages(
        self,
        agent_key: str,
        conversation_id: str,
    ) -> list[dict[str, Any]]:
        resp = self.conversations.get_conversation(
            agent_key,
            conversation_id,
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


@pytest.mark.integration
class TestAgentConversationTitleUpdate(AgentConversationsTestBase):
    @pytest.mark.parametrize(
        "payload",
        [
            {"title": "x"},
            {"title": "rename-" + ("a" * 193)},
        ],
    )
    def test_patch_agent_conversation_title_request_body_matches_openapi_spec(
        self,
        payload: dict[str, Any],
    ) -> None:
        assert_request_body_matches_openapi_operation(
            payload,
            "updateAgentConversationTitle",
        )

    @pytest.mark.parametrize(
        "payload",
        [
            {},
            {"title": ""},
            {"title": "a" * 201},
            {"title": 123},
        ],
    )
    def test_patch_agent_conversation_title_request_body_rejects_invalid_openapi_payloads(
        self,
        payload: dict[str, Any],
    ) -> None:
        with pytest.raises(AssertionError):
            assert_request_body_matches_openapi_operation(
                payload,
                "updateAgentConversationTitle",
            )

    def test_patch_agent_conversation_title_updates_and_persists(
        self,
        created_conversations: list[tuple[str, str]],
    ) -> None:
        conversation_id = self._stream_create_agent_conversation_id(
            self.primary_agent,
            query=f"agent-title-happy-{uuid4().hex}",
            created_conversations=created_conversations,
        )
        new_title = f"renamed-{uuid4().hex}"
        payload = {"title": new_title}

        assert_request_body_matches_openapi_operation(
            payload,
            "updateAgentConversationTitle",
        )

        resp = self.conversations.update_title(
            self.primary_agent,
            conversation_id,
            json=payload,
            timeout=self.timeout,
        )
        assert resp.status_code == 200, f"{resp.status_code}: {resp.text}"

        body = _response_json(resp)
        conversation = body.get("conversation")
        assert isinstance(conversation, dict), f"Expected conversation object, got: {body!r}"
        assert str(conversation.get("_id")) == conversation_id, (
            f"conversation._id mismatch: expected {conversation_id!r}, got {conversation!r}"
        )
        assert conversation.get("title") == new_title, (
            f"conversation.title mismatch: expected {new_title!r}, got {conversation!r}"
        )
        assert_response_matches_openapi_operation(body, "updateAgentConversationTitle", status_code="200")

        get_resp = self.conversations.get_conversation(
            self.primary_agent,
            conversation_id,
            timeout=self.timeout,
        )
        assert get_resp.status_code == 200, f"{get_resp.status_code}: {get_resp.text}"
        get_body = _response_json(get_resp)
        get_conversation = get_body.get("conversation")
        assert isinstance(get_conversation, dict), (
            f"Expected GET conversation object, got: {get_body!r}"
        )
        assert get_conversation.get("id") == conversation_id, (
            f"GET conversation.id mismatch: {get_conversation!r}"
        )
        assert get_conversation.get("title") == new_title, (
            f"GET conversation.title did not persist: expected {new_title!r}, "
            f"got {get_conversation.get('title')!r}"
        )

    @pytest.mark.parametrize(
        ("label", "params"),
        [
            (
                "unknown scalar query params",
                {"foo": "bar", "search": "ignored", "unused": "1"},
            ),
            (
                "pagination-like query params",
                {"page": "1", "limit": "20", "sortBy": "createdAt"},
            ),
            (
                "array-like query params",
                {"foo": ["a", "b"], "page": ["1", "2"]},
            ),
        ],
    )
    def test_patch_agent_conversation_title_accepts_ignored_query_params(
        self,
        label: str,
        params: dict[str, Any],
        created_conversations: list[tuple[str, str]],
    ) -> None:
        conversation_id = self._stream_create_agent_conversation_id(
            self.primary_agent,
            query=f"agent-title-query-{label}-{uuid4().hex}",
            created_conversations=created_conversations,
        )
        new_title = f"query-rename-{uuid4().hex}"

        resp = self.conversations.update_title(
            self.primary_agent,
            conversation_id,
            title=new_title,
            params=params,
            timeout=self.timeout,
        )
        assert resp.status_code == 200, f"[{label}] {resp.status_code}: {resp.text}"

        body = _response_json(resp)
        conversation = body.get("conversation")
        assert isinstance(conversation, dict), f"[{label}] Unexpected body: {body!r}"
        assert str(conversation.get("_id")) == conversation_id, (
            f"[{label}] conversation._id mismatch: {conversation!r}"
        )
        assert conversation.get("title") == new_title, (
            f"[{label}] conversation.title mismatch: {conversation!r}"
        )
        assert_response_matches_openapi_operation(
            body,
            "updateAgentConversationTitle",
            status_code="200",
        )

    @pytest.mark.parametrize(
        ("label", "conversation_id"),
        [
            ("non-hex", "not-an-objectid"),
            ("too short", "abc123"),
            ("too long", "a" * 25),
        ],
    )
    def test_patch_agent_conversation_title_rejects_invalid_conversation_id_shapes(
        self,
        label: str,
        conversation_id: str,
    ) -> None:
        resp = self.conversations.update_title(
            self.primary_agent,
            conversation_id,
            title="x",
            timeout=self.timeout,
        )
        body = self._assert_validation_error(resp)
        assert body["error"]["metadata"]["errors"], (
            f"[{label}] Expected validation details"
        )
        assert_response_matches_openapi_operation(
            body,
            "updateAgentConversationTitle",
            status_code="400",
        )

    def test_patch_agent_conversation_title_nonexistent_conversation_returns_404(self) -> None:
        resp = self.conversations.update_title(
            self.primary_agent,
            "0" * 24,
            title="x",
            timeout=self.timeout,
        )
        assert resp.status_code == 404, f"{resp.status_code}: {resp.text}"
        body = _response_json(resp)
        assert_response_matches_openapi_operation(
            body,
            "updateAgentConversationTitle",
            status_code="404",
        )

    def test_patch_agent_conversation_title_for_other_agent_key_returns_404(
        self,
        created_conversations: list[tuple[str, str]],
    ) -> None:
        conversation_id = self._stream_create_agent_conversation_id(
            self.primary_agent,
            query=f"agent-title-wrong-agent-{uuid4().hex}",
            created_conversations=created_conversations,
        )
        other_agent_key = self.secondary_agents[0]

        resp = self.conversations.update_title(
            other_agent_key,
            conversation_id,
            title="wrong-agent-title",
            timeout=self.timeout,
        )
        assert resp.status_code == 404, f"{resp.status_code}: {resp.text}"
        body = _response_json(resp)
        assert_response_matches_openapi_operation(
            body,
            "updateAgentConversationTitle",
            status_code="404",
        )

    @pytest.mark.parametrize(
        ("label", "payload"),
        [
            ("missing title", {}),
            ("empty title", {"title": ""}),
            ("too long title", {"title": "a" * 201}),
            ("non-string title", {"title": 123}),
        ],
    )
    def test_patch_agent_conversation_title_rejects_invalid_body(
        self,
        label: str,
        payload: dict[str, Any],
        created_conversations: list[tuple[str, str]],
    ) -> None:
        conversation_id = self._stream_create_agent_conversation_id(
            self.primary_agent,
            query=f"agent-title-invalid-body-{label}-{uuid4().hex}",
            created_conversations=created_conversations,
        )

        resp = self.conversations.update_title(
            self.primary_agent,
            conversation_id,
            json=payload,
            timeout=self.timeout,
        )
        body = self._assert_validation_error(resp)
        assert body["error"]["metadata"]["errors"], (
            f"[{label}] Expected validation details"
        )
        assert_response_matches_openapi_operation(
            body,
            "updateAgentConversationTitle",
            status_code="400",
        )


@pytest.mark.integration
class TestAgentConversationDelete(AgentConversationsTestBase):
    @staticmethod
    def _assert_delete_success(
        body: dict[str, Any],
        *,
        expected_conversation_id: str | None = None,
        expect_conversation: bool,
    ) -> None:
        assert body.get("message") == "Conversation deleted successfully", (
            f"Unexpected delete success message: {body!r}"
        )
        assert "conversation" in body, f"Delete response missing conversation key: {body!r}"
        conversation = body.get("conversation")
        if expect_conversation:
            assert isinstance(conversation, dict), (
                f"Expected deleted conversation object, got: {body!r}"
            )
            if expected_conversation_id is not None:
                assert str(conversation.get("_id")) == expected_conversation_id, (
                    f"Deleted conversation id mismatch: {conversation!r}"
                )
            assert conversation.get("isDeleted") is True, (
                f"Expected soft-deleted conversation payload: {conversation!r}"
            )
        else:
            assert conversation is None, (
                f"Expected null conversation for no-op delete, got: {body!r}"
            )

    def test_delete_agent_conversation_updates_and_hides_conversation(
        self,
        created_conversations: list[tuple[str, str]],
    ) -> None:
        conversation_id = self._stream_create_agent_conversation_id(
            self.primary_agent,
            query=f"agent-delete-happy-{uuid4().hex}",
            created_conversations=created_conversations,
        )

        resp = self.conversations.delete_conversation(
            self.primary_agent,
            conversation_id,
            timeout=self.timeout,
        )
        assert resp.status_code == 200, f"{resp.status_code}: {resp.text}"

        body = _response_json(resp)
        self._assert_delete_success(
            body,
            expected_conversation_id=conversation_id,
            expect_conversation=True,
        )
        assert_response_matches_openapi_operation(body, "deleteAgentConversationById", status_code="200")

        get_resp = self.conversations.get_conversation(
            self.primary_agent,
            conversation_id,
            timeout=self.timeout,
        )
        assert get_resp.status_code == 404, f"{get_resp.status_code}: {get_resp.text}"

    @pytest.mark.parametrize(
        ("label", "params"),
        [
            (
                "unknown scalar query params",
                {"foo": "bar", "search": "ignored", "unused": "1"},
            ),
            (
                "pagination-like query params",
                {"page": "1", "limit": "20", "sortBy": "createdAt"},
            ),
            (
                "array-like query params",
                [("foo", "a"), ("foo", "b"), ("page", "1"), ("page", "2")],
            ),
        ],
    )
    def test_delete_agent_conversation_accepts_ignored_query_params(
        self,
        label: str,
        params: Any,
        created_conversations: list[tuple[str, str]],
    ) -> None:
        conversation_id = self._stream_create_agent_conversation_id(
            self.primary_agent,
            query=f"agent-delete-query-{label}-{uuid4().hex}",
            created_conversations=created_conversations,
        )

        resp = self.conversations.delete_conversation(
            self.primary_agent,
            conversation_id,
            params=params,
            timeout=self.timeout,
        )
        assert resp.status_code == 200, f"[{label}] {resp.status_code}: {resp.text}"

        body = _response_json(resp)
        self._assert_delete_success(
            body,
            expected_conversation_id=conversation_id,
            expect_conversation=True,
        )

    @pytest.mark.parametrize(
        ("label", "conversation_id"),
        [
            ("non-hex", "not-an-objectid"),
            ("too short", "abc123"),
            ("too long", "a" * 25),
        ],
    )
    def test_delete_agent_conversation_rejects_invalid_conversation_id_shapes(
        self,
        label: str,
        conversation_id: str,
    ) -> None:
        resp = self.conversations.delete_conversation(
            self.primary_agent,
            conversation_id,
            timeout=self.timeout,
        )
        body = self._assert_validation_error(resp)
        assert body["error"]["metadata"]["errors"], (
            f"[{label}] Expected validation details"
        )

    def test_delete_agent_conversation_nonexistent_conversation_returns_success_with_null(
        self,
    ) -> None:
        resp = self.conversations.delete_conversation(
            self.primary_agent,
            "0" * 24,
            timeout=self.timeout,
        )
        assert resp.status_code == 200, f"{resp.status_code}: {resp.text}"

        body = _response_json(resp)
        self._assert_delete_success(body, expect_conversation=False)

    def test_delete_agent_conversation_for_other_agent_key_is_a_no_op(
        self,
        created_conversations: list[tuple[str, str]],
    ) -> None:
        conversation_id = self._stream_create_agent_conversation_id(
            self.primary_agent,
            query=f"agent-delete-wrong-agent-{uuid4().hex}",
            created_conversations=created_conversations,
        )
        other_agent_key = self.secondary_agents[0]

        delete_resp = self.conversations.delete_conversation(
            other_agent_key,
            conversation_id,
            timeout=self.timeout,
        )
        assert delete_resp.status_code == 200, f"{delete_resp.status_code}: {delete_resp.text}"

        delete_body = _response_json(delete_resp)
        self._assert_delete_success(delete_body, expect_conversation=False)

        get_resp = self.conversations.get_conversation(
            self.primary_agent,
            conversation_id,
            timeout=self.timeout,
        )
        assert get_resp.status_code == 200, f"{get_resp.status_code}: {get_resp.text}"

    def test_delete_agent_conversation_second_delete_is_a_no_op(
        self,
        created_conversations: list[tuple[str, str]],
    ) -> None:
        conversation_id = self._stream_create_agent_conversation_id(
            self.primary_agent,
            query=f"agent-delete-twice-{uuid4().hex}",
            created_conversations=created_conversations,
        )

        first_delete = self.conversations.delete_conversation(
            self.primary_agent,
            conversation_id,
            timeout=self.timeout,
        )
        assert first_delete.status_code == 200, f"{first_delete.status_code}: {first_delete.text}"
        first_body = _response_json(first_delete)
        self._assert_delete_success(
            first_body,
            expected_conversation_id=conversation_id,
            expect_conversation=True,
        )

        second_delete = self.conversations.delete_conversation(
            self.primary_agent,
            conversation_id,
            timeout=self.timeout,
        )
        assert second_delete.status_code == 200, (
            f"{second_delete.status_code}: {second_delete.text}"
        )
        second_body = _response_json(second_delete)
        self._assert_delete_success(second_body, expect_conversation=False)

    def test_delete_agent_conversation_without_auth_returns_401(self) -> None:
        resp = self.conversations.delete_conversation(
            self.primary_agent,
            "0" * 24,
            auth=False,
            timeout=self.timeout,
        )
        assert resp.status_code == 401, f"{resp.status_code}: {resp.text}"


@pytest.mark.integration
class TestAgentConversationArchive(AgentConversationsTestBase):
    @staticmethod
    def _assert_archive_success(
        body: dict[str, Any],
        *,
        expected_conversation_id: str,
    ) -> None:
        assert body.get("id") == expected_conversation_id, (
            f"Archive id mismatch: expected {expected_conversation_id!r}, got {body!r}"
        )
        assert body.get("status") == "archived", (
            f"Expected archived status, got: {body!r}"
        )
        assert body.get("archivedBy"), f"Expected archivedBy in response: {body!r}"
        assert body.get("archivedAt"), f"Expected archivedAt in response: {body!r}"
        meta = body.get("meta")
        assert isinstance(meta, dict), f"Expected meta object, got: {body!r}"
        assert meta.get("requestId"), f"Expected meta.requestId, got: {body!r}"
        assert meta.get("timestamp"), f"Expected meta.timestamp, got: {body!r}"

    def test_post_archive_agent_conversation_archives_successfully(
        self,
        created_conversations: list[tuple[str, str]],
    ) -> None:
        conversation_id = self._stream_create_agent_conversation_id(
            self.primary_agent,
            query=f"agent-archive-happy-{uuid4().hex}",
            created_conversations=created_conversations,
        )

        resp = self.conversations.archive_conversation(
            self.primary_agent,
            conversation_id,
            timeout=self.timeout,
        )
        assert resp.status_code == 200, f"{resp.status_code}: {resp.text}"

        body = _response_json(resp)
        self._assert_archive_success(body, expected_conversation_id=conversation_id)
        assert_response_matches_openapi_operation(body, "archiveAgentConversation", status_code="200")

    @pytest.mark.parametrize(
        ("label", "params"),
        [
            (
                "unknown scalar query params",
                {"foo": "bar", "search": "ignored", "unused": "1"},
            ),
            (
                "pagination-like query params",
                {"page": "1", "limit": "20", "sortBy": "createdAt"},
            ),
            (
                "array-like query params",
                [("foo", "a"), ("foo", "b"), ("page", "1"), ("page", "2")],
            ),
        ],
    )
    def test_post_archive_agent_conversation_accepts_ignored_query_params(
        self,
        label: str,
        params: Any,
        created_conversations: list[tuple[str, str]],
    ) -> None:
        conversation_id = self._stream_create_agent_conversation_id(
            self.primary_agent,
            query=f"agent-archive-query-{label}-{uuid4().hex}",
            created_conversations=created_conversations,
        )

        resp = self.conversations.archive_conversation(
            self.primary_agent,
            conversation_id,
            params=params,
            timeout=self.timeout,
        )
        assert resp.status_code == 200, f"[{label}] {resp.status_code}: {resp.text}"

        body = _response_json(resp)
        self._assert_archive_success(body, expected_conversation_id=conversation_id)

    @pytest.mark.parametrize(
        ("label", "conversation_id"),
        [
            ("non-hex", "not-an-objectid"),
            ("too short", "abc123"),
            ("too long", "a" * 25),
        ],
    )
    def test_post_archive_agent_conversation_rejects_invalid_conversation_id_shapes(
        self,
        label: str,
        conversation_id: str,
    ) -> None:
        resp = self.conversations.archive_conversation(
            self.primary_agent,
            conversation_id,
            timeout=self.timeout,
        )
        body = self._assert_validation_error(resp)
        assert body["error"]["metadata"]["errors"], (
            f"[{label}] Expected validation details"
        )

    def test_post_archive_agent_conversation_nonexistent_conversation_returns_404(
        self,
    ) -> None:
        resp = self.conversations.archive_conversation(
            self.primary_agent,
            "0" * 24,
            timeout=self.timeout,
        )
        assert resp.status_code == 404, f"{resp.status_code}: {resp.text}"

    def test_post_archive_agent_conversation_for_other_agent_key_returns_404(
        self,
        created_conversations: list[tuple[str, str]],
    ) -> None:
        conversation_id = self._stream_create_agent_conversation_id(
            self.primary_agent,
            query=f"agent-archive-wrong-agent-{uuid4().hex}",
            created_conversations=created_conversations,
        )
        other_agent_key = self.secondary_agents[0]

        resp = self.conversations.archive_conversation(
            other_agent_key,
            conversation_id,
            timeout=self.timeout,
        )
        assert resp.status_code == 404, f"{resp.status_code}: {resp.text}"

    def test_post_archive_agent_conversation_already_archived_returns_400(
        self,
        created_conversations: list[tuple[str, str]],
    ) -> None:
        conversation_id = self._stream_create_agent_conversation_id(
            self.primary_agent,
            query=f"agent-archive-twice-{uuid4().hex}",
            created_conversations=created_conversations,
        )

        first_resp = self.conversations.archive_conversation(
            self.primary_agent,
            conversation_id,
            timeout=self.timeout,
        )
        assert first_resp.status_code == 200, f"{first_resp.status_code}: {first_resp.text}"

        second_resp = self.conversations.archive_conversation(
            self.primary_agent,
            conversation_id,
            timeout=self.timeout,
        )
        assert second_resp.status_code == 400, (
            f"{second_resp.status_code}: {second_resp.text}"
        )

    def test_post_archive_agent_conversation_without_auth_returns_401(self) -> None:
        resp = self.conversations.archive_conversation(
            self.primary_agent,
            "0" * 24,
            auth=False,
            timeout=self.timeout,
        )
        assert resp.status_code == 401, f"{resp.status_code}: {resp.text}"


@pytest.mark.integration
class TestAgentConversationUnarchive(AgentConversationsTestBase):
    @staticmethod
    def _assert_unarchive_success(
        body: dict[str, Any],
        *,
        expected_conversation_id: str,
    ) -> None:
        assert body.get("id") == expected_conversation_id, (
            f"Unarchive id mismatch: expected {expected_conversation_id!r}, got {body!r}"
        )
        assert body.get("status") == "unarchived", (
            f"Expected unarchived status, got: {body!r}"
        )
        assert body.get("unarchivedBy"), f"Expected unarchivedBy in response: {body!r}"
        assert body.get("unarchivedAt"), f"Expected unarchivedAt in response: {body!r}"
        meta = body.get("meta")
        assert isinstance(meta, dict), f"Expected meta object, got: {body!r}"
        assert meta.get("requestId"), f"Expected meta.requestId, got: {body!r}"
        assert meta.get("timestamp"), f"Expected meta.timestamp, got: {body!r}"

    def test_post_unarchive_agent_conversation_unarchives_successfully(
        self,
        created_conversations: list[tuple[str, str]],
    ) -> None:
        conversation_id = self._stream_create_agent_conversation_id(
            self.primary_agent,
            query=f"agent-unarchive-happy-{uuid4().hex}",
            created_conversations=created_conversations,
        )
        archive_resp = self.conversations.archive_conversation(
            self.primary_agent,
            conversation_id,
            timeout=self.timeout,
        )
        assert archive_resp.status_code == 200, f"{archive_resp.status_code}: {archive_resp.text}"

        resp = self.conversations.unarchive_conversation(
            self.primary_agent,
            conversation_id,
            timeout=self.timeout,
        )
        assert resp.status_code == 200, f"{resp.status_code}: {resp.text}"

        body = _response_json(resp)
        self._assert_unarchive_success(body, expected_conversation_id=conversation_id)
        assert_response_matches_openapi_operation(body, "unarchiveAgentConversation", status_code="200")

    @pytest.mark.parametrize(
        ("label", "params"),
        [
            (
                "unknown scalar query params",
                {"foo": "bar", "search": "ignored", "unused": "1"},
            ),
            (
                "pagination-like query params",
                {"page": "1", "limit": "20", "sortBy": "createdAt"},
            ),
            (
                "array-like query params",
                [("foo", "a"), ("foo", "b"), ("page", "1"), ("page", "2")],
            ),
        ],
    )
    def test_post_unarchive_agent_conversation_accepts_ignored_query_params(
        self,
        label: str,
        params: Any,
        created_conversations: list[tuple[str, str]],
    ) -> None:
        conversation_id = self._stream_create_agent_conversation_id(
            self.primary_agent,
            query=f"agent-unarchive-query-{label}-{uuid4().hex}",
            created_conversations=created_conversations,
        )
        archive_resp = self.conversations.archive_conversation(
            self.primary_agent,
            conversation_id,
            timeout=self.timeout,
        )
        assert archive_resp.status_code == 200, f"{archive_resp.status_code}: {archive_resp.text}"

        resp = self.conversations.unarchive_conversation(
            self.primary_agent,
            conversation_id,
            params=params,
            timeout=self.timeout,
        )
        assert resp.status_code == 200, f"[{label}] {resp.status_code}: {resp.text}"

        body = _response_json(resp)
        self._assert_unarchive_success(body, expected_conversation_id=conversation_id)

    @pytest.mark.parametrize(
        ("label", "conversation_id"),
        [
            ("non-hex", "not-an-objectid"),
            ("too short", "abc123"),
            ("too long", "a" * 25),
        ],
    )
    def test_post_unarchive_agent_conversation_rejects_invalid_conversation_id_shapes(
        self,
        label: str,
        conversation_id: str,
    ) -> None:
        resp = self.conversations.unarchive_conversation(
            self.primary_agent,
            conversation_id,
            timeout=self.timeout,
        )
        body = self._assert_validation_error(resp)
        assert body["error"]["metadata"]["errors"], (
            f"[{label}] Expected validation details"
        )

    def test_post_unarchive_agent_conversation_nonexistent_conversation_returns_404(
        self,
    ) -> None:
        resp = self.conversations.unarchive_conversation(
            self.primary_agent,
            "0" * 24,
            timeout=self.timeout,
        )
        assert resp.status_code == 404, f"{resp.status_code}: {resp.text}"

    def test_post_unarchive_agent_conversation_for_other_agent_key_returns_404(
        self,
        created_conversations: list[tuple[str, str]],
    ) -> None:
        conversation_id = self._stream_create_agent_conversation_id(
            self.primary_agent,
            query=f"agent-unarchive-wrong-agent-{uuid4().hex}",
            created_conversations=created_conversations,
        )
        archive_resp = self.conversations.archive_conversation(
            self.primary_agent,
            conversation_id,
            timeout=self.timeout,
        )
        assert archive_resp.status_code == 200, f"{archive_resp.status_code}: {archive_resp.text}"
        other_agent_key = self.secondary_agents[0]

        resp = self.conversations.unarchive_conversation(
            other_agent_key,
            conversation_id,
            timeout=self.timeout,
        )
        assert resp.status_code == 404, f"{resp.status_code}: {resp.text}"

    def test_post_unarchive_agent_conversation_not_archived_returns_400(
        self,
        created_conversations: list[tuple[str, str]],
    ) -> None:
        conversation_id = self._stream_create_agent_conversation_id(
            self.primary_agent,
            query=f"agent-unarchive-active-{uuid4().hex}",
            created_conversations=created_conversations,
        )

        resp = self.conversations.unarchive_conversation(
            self.primary_agent,
            conversation_id,
            timeout=self.timeout,
        )
        assert resp.status_code == 400, f"{resp.status_code}: {resp.text}"

    def test_post_unarchive_agent_conversation_without_auth_returns_401(self) -> None:
        resp = self.conversations.unarchive_conversation(
            self.primary_agent,
            "0" * 24,
            auth=False,
            timeout=self.timeout,
        )
        assert resp.status_code == 401, f"{resp.status_code}: {resp.text}"


@pytest.mark.integration
class TestAgentConversationRegenerate(AgentConversationsTestBase):
    @pytest.fixture(autouse=True)
    def _setup_regenerate(
        self,
        reasoning_multimodal_llm_model: Any,
        session_kb: dict[str, str],
    ) -> None:
        self.org_id = self.conversations._client.org_id
        self.reasoning_model_key = reasoning_multimodal_llm_model.model_key
        self.reasoning_model_name = reasoning_multimodal_llm_model.model_name
        self.kb_id = session_kb["kb_id"]  # Add KB ID for filter tests

    def _post_regenerate_stream(
        self,
        agent_key: str,
        conversation_id: str,
        message_id: str,
        *,
        payload: dict[str, Any],
    ) -> tuple[int, str, list[dict[str, Any]]]:
        envelopes: list[dict[str, Any]] = []

        with self.conversations.regenerate_message(
            agent_key,
            conversation_id,
            message_id,
            json=payload,
            stream=True,
            timeout=self.stream_timeout,
        ) as resp:
            status_code = resp.status_code
            content_type = (resp.headers.get("Content-Type") or "").lower()
            for envelope in iter_sse_envelopes(resp):
                assert_matches_component_schema(
                    envelope,
                    "AgentRegenerateSSEEvent",
                )
                parsed_data: Any
                try:
                    parsed_data = json.loads(envelope["data"])
                except ValueError:
                    parsed_data = envelope["data"]
                envelopes.append(
                    {"event": envelope["event"], "data": parsed_data}
                )
                if is_root_finished(envelope["event"], parsed_data) or is_root_error(
                    envelope["event"], parsed_data
                ):
                    break

        return status_code, content_type, envelopes

    def _assert_sse_complete(
        self,
        status_code: int,
        content_type: str,
        envelopes: list[dict[str, Any]],
        *,
        expected_conversation_id: str,
    ) -> dict[str, Any]:
        assert status_code == 200, f"{status_code}: {envelopes!r}"
        assert "text/event-stream" in content_type, (
            f"expected text/event-stream, got Content-Type={content_type!r}"
        )
        assert envelopes, "expected at least one SSE event"
        finished_event = next(
            (e for e in envelopes if is_root_finished(e["event"], e["data"])),
            None,
        )
        assert finished_event is not None, (
            f"expected RUN_FINISHED event, got: {envelopes!r}"
        )
        result = run_finished_result(finished_event["data"])
        assert result, f"expected dict RUN_FINISHED result, got: {finished_event['data']!r}"
        conversation = result.get("conversation") or {}
        assert conversation.get("_id") == expected_conversation_id, (
            f"conversation id mismatch: expected {expected_conversation_id!r}, got {result!r}"
        )
        messages = conversation.get("messages") or []
        assert messages, f"expected messages in RUN_FINISHED result: {result!r}"
        last_message = messages[-1]
        assert isinstance(last_message, dict), f"expected dict last message, got: {result!r}"
        assert last_message.get("messageType") == "bot_response", (
            f"expected last message bot_response, got: {last_message!r}"
        )
        content = last_message.get("content") or ""
        assert isinstance(content, str) and content.strip(), (
            f"expected non-empty bot content, got: {last_message!r}"
        )
        return result

    def _assert_sse_error(
        self,
        status_code: int,
        content_type: str,
        envelopes: list[dict[str, Any]],
        *,
        expected_substring: str,
    ) -> dict[str, Any]:
        assert status_code == 200, f"{status_code}: {envelopes!r}"
        assert "text/event-stream" in content_type, (
            f"expected text/event-stream, got Content-Type={content_type!r}"
        )
        assert envelopes, "expected at least one SSE event"
        error_event = next(
            (e for e in envelopes if is_root_error(e["event"], e["data"])),
            None,
        )
        assert error_event is not None, f"expected RUN_ERROR event, got: {envelopes!r}"
        payload = error_event["data"]
        assert isinstance(payload, dict), f"expected dict RUN_ERROR payload, got: {payload!r}"
        message = run_error_message(payload)
        assert expected_substring.lower() in message.lower(), (
            f"expected {expected_substring!r} in RUN_ERROR payload, got: {payload!r}"
        )
        return payload

    def test_post_agent_conversation_regenerate_streams_to_complete(
        self,
        created_conversations: list[tuple[str, str]],
    ) -> None:
        conversation_id = self._stream_create_agent_conversation_id(
            self.primary_agent,
            query=f"agent-regenerate-happy-{uuid4().hex}",
            created_conversations=created_conversations,
        )
        message_id, _ = self._conversation_last_bot_and_user_message_ids(
            self.primary_agent,
            conversation_id,
        )

        assert_request_body_matches_openapi_operation(
            {"chatMode": "quick"},
            "regenerateAgentConversationMessage",
        )
        assert_request_body_matches_openapi_operation(
            _RICH_REGENERATE_REQUEST_OPENAPI_PAYLOAD,
            "regenerateAgentConversationMessage",
        )

        status_code, content_type, envelopes = self._post_regenerate_stream(
            self.primary_agent,
            conversation_id,
            message_id,
            payload={"chatMode": "quick"},
        )

        self._assert_sse_complete(
            status_code,
            content_type,
            envelopes,
            expected_conversation_id=conversation_id,
        )

    @pytest.mark.parametrize(
        ("label", "payload"),
        [
            (
                "all optional fields",
                {
                    "filters": {
                        "apps": ["123e4567-e89b-12d3-a456-426614174000"],
                        "kb": ["knowledgeBase_placeholder"],
                    },
                    "chatMode": "quick",
                    "modelKey": "model-key",
                    "modelName": "model-name",
                    "modelFriendlyName": "Model Friendly Name",
                    "timezone": "Asia/Kolkata",
                    "currentTime": "2026-05-27T10:30:00+05:30",
                    "tools": ["web_search", "calculator"],
                },
            ),
            ("chatMode only", {"chatMode": "quick"}),
            (
                "currentTime only",
                {
                    "chatMode": "quick",
                    "currentTime": "2026-05-27T10:30:00+05:30",
                },
            ),
            ("tools only", {"chatMode": "quick", "tools": ["web_search"]}),
            (
                "filters only",
                {
                    "chatMode": "quick",
                    "filters": {
                        "apps": ["123e4567-e89b-12d3-a456-426614174001"],
                        "kb": ["knowledgeBase_placeholder"],
                    }
                },
            ),
            (
                "unknown extra keys",
                {"chatMode": "quick", "ignoredField": "ignored-value"},
            ),
        ],
    )
    def test_post_agent_conversation_regenerate_accepts_valid_optional_body_fields(
        self,
        label: str,
        payload: dict[str, Any],
        created_conversations: list[tuple[str, str]],
    ) -> None:
        conversation_id = self._stream_create_agent_conversation_id(
            self.primary_agent,
            query=f"agent-regenerate-body-{label}-{uuid4().hex}",
            created_conversations=created_conversations,
        )
        message_id, _ = self._conversation_last_bot_and_user_message_ids(
            self.primary_agent,
            conversation_id,
        )
        request_payload = json.loads(json.dumps(payload).replace(
            "knowledgeBase_placeholder",
            self.kb_id,  # Use actual KB UUID instead of knowledgeBase_orgId
        ))
        if request_payload.get("modelKey") == "model-key":
            request_payload["modelKey"] = self.reasoning_model_key
        if request_payload.get("modelName") == "model-name":
            request_payload["modelName"] = self.reasoning_model_name
        if label not in {"unknown extra keys", "all optional fields", "filters only"}:
            assert_request_body_matches_openapi_operation(
                request_payload,
                "regenerateAgentConversationMessage",
            )

        status_code, content_type, envelopes = self._post_regenerate_stream(
            self.primary_agent,
            conversation_id,
            message_id,
            payload=request_payload,
        )

        self._assert_sse_complete(
            status_code,
            content_type,
            envelopes,
            expected_conversation_id=conversation_id,
        )

    @pytest.mark.parametrize(
        ("label", "conversation_id"),
        [
            ("non-hex", "not-an-objectid"),
            ("too short", "abc123"),
            ("too long", "a" * 25),
        ],
    )
    def test_post_agent_conversation_regenerate_rejects_invalid_conversation_id_shapes(
        self,
        label: str,
        conversation_id: str,
    ) -> None:
        resp = self.conversations.regenerate_message(
            self.primary_agent,
            conversation_id,
            "0" * 24,
            json={"chatMode": "quick"},
            stream=False,
            timeout=self.timeout,
        )
        body = self._assert_validation_error(resp)
        assert body["error"]["metadata"]["errors"], (
            f"[{label}] Expected validation details"
        )

    @pytest.mark.parametrize(
        ("label", "message_id"),
        [
            ("non-hex", "not-an-objectid"),
            ("too short", "abc123"),
            ("too long", "a" * 25),
        ],
    )
    def test_post_agent_conversation_regenerate_rejects_invalid_message_id_shapes(
        self,
        label: str,
        message_id: str,
    ) -> None:
        resp = self.conversations.regenerate_message(
            self.primary_agent,
            "0" * 24,
            message_id,
            json={"chatMode": "quick"},
            stream=False,
            timeout=self.timeout,
        )
        body = self._assert_validation_error(resp)
        assert body["error"]["metadata"]["errors"], (
            f"[{label}] Expected validation details"
        )

    @pytest.mark.parametrize(
        ("label", "payload"),
        [
            ("missing chatMode", {}),
            ("empty chatMode", {"chatMode": ""}),
            ("auto chatMode", {"chatMode": "auto"}),
            ("deep chatMode", {"chatMode": "deep"}),
            ("planExecute chatMode", {"chatMode": "planExecute"}),
            ("verification chatMode", {"chatMode": "verification"}),
            ("empty modelKey", {"chatMode": "quick", "modelKey": ""}),
            ("empty modelName", {"chatMode": "quick", "modelName": ""}),
            (
                "empty modelFriendlyName",
                {"chatMode": "quick", "modelFriendlyName": ""},
            ),
            ("empty timezone", {"chatMode": "quick", "timezone": ""}),
            (
                "invalid currentTime",
                {"chatMode": "quick", "currentTime": "not-an-iso-datetime"},
            ),
            (
                "currentTime without offset",
                {
                    "chatMode": "quick",
                    "currentTime": "2026-05-27T10:30:00",
                },
            ),
            ("tools not array", {"chatMode": "quick", "tools": "web_search"}),
            (
                "tools has empty string",
                {"chatMode": "quick", "tools": ["web_search", ""]},
            ),
            ("filters not object", {"chatMode": "quick", "filters": "invalid"}),
            (
                "filters.apps not array",
                {"chatMode": "quick", "filters": {"apps": "invalid"}},
            ),
            (
                "filters.kb not array",
                {"chatMode": "quick", "filters": {"kb": "invalid"}},
            ),
            (
                "filters.apps invalid entry",
                {
                    "chatMode": "quick",
                    "filters": {"apps": ["not-a-valid-app-id"]},
                },
            ),
            (
                "filters.kb invalid entry",
                {
                    "chatMode": "quick",
                    "filters": {"kb": ["not-a-valid-kb-id"]},
                },
            ),
        ],
    )
    def test_post_agent_conversation_regenerate_rejects_invalid_body(
        self,
        label: str,
        payload: dict[str, Any],
        created_conversations: list[tuple[str, str]],
    ) -> None:
        conversation_id = self._stream_create_agent_conversation_id(
            self.primary_agent,
            query=f"agent-regenerate-invalid-body-{label}-{uuid4().hex}",
            created_conversations=created_conversations,
        )
        message_id, _ = self._conversation_last_bot_and_user_message_ids(
            self.primary_agent,
            conversation_id,
        )

        resp = self.conversations.regenerate_message(
            self.primary_agent,
            conversation_id,
            message_id,
            json=payload,
            stream=False,
            timeout=self.timeout,
        )
        body = self._assert_validation_error(resp)
        assert body["error"]["metadata"]["errors"], (
            f"[{label}] Expected validation details"
        )

    def test_post_agent_conversation_regenerate_nonexistent_conversation_emits_sse_error(
        self,
    ) -> None:
        status_code, content_type, envelopes = self._post_regenerate_stream(
            self.primary_agent,
            "0" * 24,
            "0" * 24,
            payload={"chatMode": "quick"},
        )

        self._assert_sse_error(
            status_code,
            content_type,
            envelopes,
            expected_substring="not found",
        )

    def test_post_agent_conversation_regenerate_for_other_agent_key_emits_sse_error(
        self,
        created_conversations: list[tuple[str, str]],
    ) -> None:
        conversation_id = self._stream_create_agent_conversation_id(
            self.primary_agent,
            query=f"agent-regenerate-wrong-agent-{uuid4().hex}",
            created_conversations=created_conversations,
        )
        message_id, _ = self._conversation_last_bot_and_user_message_ids(
            self.primary_agent,
            conversation_id,
        )
        other_agent_key = self.secondary_agents[0]

        status_code, content_type, envelopes = self._post_regenerate_stream(
            other_agent_key,
            conversation_id,
            message_id,
            payload={"chatMode": "quick"},
        )

        self._assert_sse_error(
            status_code,
            content_type,
            envelopes,
            expected_substring="not found",
        )

    def test_post_agent_conversation_regenerate_non_last_message_id_emits_sse_error(
        self,
        created_conversations: list[tuple[str, str]],
    ) -> None:
        conversation_id = self._stream_create_agent_conversation_id(
            self.primary_agent,
            query=f"agent-regenerate-non-last-{uuid4().hex}",
            created_conversations=created_conversations,
        )
        _last_bot_id, user_query_id = self._conversation_last_bot_and_user_message_ids(
            self.primary_agent,
            conversation_id,
        )

        status_code, content_type, envelopes = self._post_regenerate_stream(
            self.primary_agent,
            conversation_id,
            user_query_id,
            payload={"chatMode": "quick"},
        )

        self._assert_sse_error(
            status_code,
            content_type,
            envelopes,
            expected_substring="last message",
        )


@pytest.mark.integration
class TestAgentConversationRegenerateOpenApiRequestContract:
    """Offline OpenAPI request-body checks not covered by live gateway negatives."""

    def test_requires_quick_chat_mode(self) -> None:
        assert_request_body_matches_openapi_operation(
            {"chatMode": "quick"},
            "regenerateAgentConversationMessage",
        )

        for payload in ({}, {"chatMode": "auto"}):
            with pytest.raises(AssertionError):
                assert_request_body_matches_openapi_operation(
                    payload,
                    "regenerateAgentConversationMessage",
                )

    def test_rejects_extra_top_level_property(self) -> None:
        payload = {"chatMode": "answer", "unexpectedTopLevelField": "x"}
        with pytest.raises(AssertionError):
            assert_request_body_matches_openapi_operation(
                payload,
                "regenerateAgentConversationMessage",
            )

    def test_rejects_model_provider_top_level(self) -> None:
        with pytest.raises(AssertionError):
            assert_request_body_matches_openapi_operation(
                {"modelProvider": "openai"},
                "regenerateAgentConversationMessage",
            )
