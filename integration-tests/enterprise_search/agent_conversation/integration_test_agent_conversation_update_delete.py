"""Agent conversation title update integration tests.

``PATCH /api/v1/agents/{agentKey}/conversations/{conversationId}/title``

Focuses on the path/query variations permitted by the route's Zod schema:
- ``agentKey``: non-empty string path param
- ``conversationId``: Mongo ObjectId path param
- no route query schema, so arbitrary query params are accepted/ignored
"""

from __future__ import annotations

import json
import os
from collections.abc import Iterator
from typing import Any
from uuid import uuid4

import pytest
import requests

from openapi_search_validator import assert_response_matches_spec
from pipeshub_client import PipeshubClient

_DELETE_SPEC_PATH = "/agents/{agentKey}/conversations/{conversationId}"
_ARCHIVE_SPEC_PATH = "/agents/{agentKey}/conversations/{conversationId}/archive"
_UNARCHIVE_SPEC_PATH = "/agents/{agentKey}/conversations/{conversationId}/unarchive"
_TITLE_SPEC_PATH = "/agents/{agentKey}/conversations/{conversationId}/title"
_SSE_MAX_EVENTS = 10_000
_SSEEnvelope = dict[str, str]


def _response_json(resp: requests.Response) -> dict[str, Any]:
    try:
        data = resp.json()
    except ValueError as exc:
        raise AssertionError(
            f"Expected JSON response, got status={resp.status_code}: {resp.text[:500]}"
        ) from exc
    assert isinstance(data, dict), f"Expected dict JSON body, got: {data!r}"
    return data


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


@pytest.mark.integration
class TestAgentConversationTitleUpdate:
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
        self.secondary_agents = list(agent_session["secondary_agents"])

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

    def _title_url(self, agent_key: str, conversation_id: str) -> str:
        return f"{self._conversation_url(agent_key, conversation_id)}/title"

    def _delete_agent_conversation(
        self,
        agent_key: str,
        conversation_id: str,
        *,
        params: Any | None = None,
        headers: dict[str, str] | None = None,
    ) -> requests.Response:
        return requests.delete(
            self._conversation_url(agent_key, conversation_id),
            headers=headers or self.headers,
            params=params,
            timeout=self.timeout,
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
                    payload = json.loads(envelope["data"])
                    raise AssertionError(f"stream emitted error event: {payload!r}")
                if envelope["event"] != "complete":
                    continue

                payload = json.loads(envelope["data"])
                conversation = payload.get("conversation") or {}
                conversation_id = conversation.get("_id")
                assert isinstance(conversation_id, str) and conversation_id, (
                    f"complete payload missing conversation._id: {payload!r}"
                )
                created_conversations.append((agent_key, conversation_id))
                return conversation_id

        raise AssertionError("agent conversation stream ended without a complete event")

    @staticmethod
    def _assert_validation_error(resp: requests.Response) -> dict[str, Any]:
        assert resp.status_code == 400, f"{resp.status_code}: {resp.text}"
        body = _response_json(resp)
        error = body.get("error")
        assert isinstance(error, dict), f"Expected error object, got: {body!r}"
        assert error.get("message") == "Validation failed", (
            f"Expected 'Validation failed', got {error.get('message')!r}"
        )
        metadata = error.get("metadata")
        assert isinstance(metadata, dict), f"Expected error.metadata object, got: {body!r}"
        details = metadata.get("errors")
        assert isinstance(details, list) and details, (
            f"Expected non-empty error.metadata.errors list, got: {body!r}"
        )
        return body

    def test_patch_agent_conversation_title_updates_and_persists(self, created_conversations) -> None:
        conversation_id = self._stream_create_agent_conversation_id(
            self.primary_agent,
            query=f"agent-title-happy-{uuid4().hex}",
            created_conversations=created_conversations,
        )
        new_title = f"renamed-{uuid4().hex}"

        resp = requests.patch(
            self._title_url(self.primary_agent, conversation_id),
            headers=self.headers,
            json={"title": new_title},
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
        assert_response_matches_spec(body, _TITLE_SPEC_PATH, "PATCH", 200)

        get_resp = requests.get(
            self._conversation_url(self.primary_agent, conversation_id),
            headers=self.headers,
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
        created_conversations,
    ) -> None:
        conversation_id = self._stream_create_agent_conversation_id(
            self.primary_agent,
            query=f"agent-title-query-{label}-{uuid4().hex}",
            created_conversations=created_conversations,
        )
        new_title = f"query-rename-{uuid4().hex}"

        resp = requests.patch(
            self._title_url(self.primary_agent, conversation_id),
            headers=self.headers,
            params=params,
            json={"title": new_title},
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
        resp = requests.patch(
            self._title_url(self.primary_agent, conversation_id),
            headers=self.headers,
            json={"title": "x"},
            timeout=self.timeout,
        )
        body = self._assert_validation_error(resp)
        assert body["error"]["metadata"]["errors"], (
            f"[{label}] Expected validation details"
        )

    def test_patch_agent_conversation_title_nonexistent_conversation_returns_404(self) -> None:
        resp = requests.patch(
            self._title_url(self.primary_agent, "0" * 24),
            headers=self.headers,
            json={"title": "x"},
            timeout=self.timeout,
        )
        assert resp.status_code == 404, f"{resp.status_code}: {resp.text}"

    def test_patch_agent_conversation_title_for_other_agent_key_returns_404(
        self,
        created_conversations,
    ) -> None:
        conversation_id = self._stream_create_agent_conversation_id(
            self.primary_agent,
            query=f"agent-title-wrong-agent-{uuid4().hex}",
            created_conversations=created_conversations,
        )
        other_agent_key = self.secondary_agents[0]

        resp = requests.patch(
            self._title_url(other_agent_key, conversation_id),
            headers=self.headers,
            json={"title": "wrong-agent-title"},
            timeout=self.timeout,
        )
        assert resp.status_code == 404, f"{resp.status_code}: {resp.text}"

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
        created_conversations,
    ) -> None:
        conversation_id = self._stream_create_agent_conversation_id(
            self.primary_agent,
            query=f"agent-title-invalid-body-{label}-{uuid4().hex}",
            created_conversations=created_conversations,
        )

        resp = requests.patch(
            self._title_url(self.primary_agent, conversation_id),
            headers=self.headers,
            json=payload,
            timeout=self.timeout,
        )
        body = self._assert_validation_error(resp)
        assert body["error"]["metadata"]["errors"], (
            f"[{label}] Expected validation details"
        )


@pytest.mark.integration
class TestAgentConversationDelete:
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
        self.secondary_agents = list(agent_session["secondary_agents"])

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

    def _delete_agent_conversation(
        self,
        agent_key: str,
        conversation_id: str,
        *,
        params: Any | None = None,
        headers: dict[str, str] | None = None,
    ) -> requests.Response:
        return requests.delete(
            self._conversation_url(agent_key, conversation_id),
            headers=headers or self.headers,
            params=params,
            timeout=self.timeout,
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
                    payload = json.loads(envelope["data"])
                    raise AssertionError(f"stream emitted error event: {payload!r}")
                if envelope["event"] != "complete":
                    continue

                payload = json.loads(envelope["data"])
                conversation = payload.get("conversation") or {}
                conversation_id = conversation.get("_id")
                assert isinstance(conversation_id, str) and conversation_id, (
                    f"complete payload missing conversation._id: {payload!r}"
                )
                created_conversations.append((agent_key, conversation_id))
                return conversation_id

        raise AssertionError("agent conversation stream ended without a complete event")

    @staticmethod
    def _assert_validation_error(resp: requests.Response) -> dict[str, Any]:
        assert resp.status_code == 400, f"{resp.status_code}: {resp.text}"
        body = _response_json(resp)
        error = body.get("error")
        assert isinstance(error, dict), f"Expected error object, got: {body!r}"
        assert error.get("message") == "Validation failed", (
            f"Expected 'Validation failed', got {error.get('message')!r}"
        )
        metadata = error.get("metadata")
        assert isinstance(metadata, dict), f"Expected error.metadata object, got: {body!r}"
        details = metadata.get("errors")
        assert isinstance(details, list) and details, (
            f"Expected non-empty error.metadata.errors list, got: {body!r}"
        )
        return body

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
        created_conversations,
    ) -> None:
        conversation_id = self._stream_create_agent_conversation_id(
            self.primary_agent,
            query=f"agent-delete-happy-{uuid4().hex}",
            created_conversations=created_conversations,
        )

        resp = self._delete_agent_conversation(self.primary_agent, conversation_id)
        assert resp.status_code == 200, f"{resp.status_code}: {resp.text}"

        body = _response_json(resp)
        self._assert_delete_success(
            body,
            expected_conversation_id=conversation_id,
            expect_conversation=True,
        )
        assert_response_matches_spec(body, _DELETE_SPEC_PATH, "DELETE", 200)

        get_resp = requests.get(
            self._conversation_url(self.primary_agent, conversation_id),
            headers=self.headers,
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
        created_conversations,
    ) -> None:
        conversation_id = self._stream_create_agent_conversation_id(
            self.primary_agent,
            query=f"agent-delete-query-{label}-{uuid4().hex}",
            created_conversations=created_conversations,
        )

        resp = self._delete_agent_conversation(
            self.primary_agent,
            conversation_id,
            params=params,
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
        resp = self._delete_agent_conversation(self.primary_agent, conversation_id)
        body = self._assert_validation_error(resp)
        assert body["error"]["metadata"]["errors"], (
            f"[{label}] Expected validation details"
        )

    def test_delete_agent_conversation_nonexistent_conversation_returns_success_with_null(
        self,
    ) -> None:
        resp = self._delete_agent_conversation(self.primary_agent, "0" * 24)
        assert resp.status_code == 200, f"{resp.status_code}: {resp.text}"

        body = _response_json(resp)
        self._assert_delete_success(body, expect_conversation=False)

    def test_delete_agent_conversation_for_other_agent_key_is_a_no_op(
        self,
        created_conversations,
    ) -> None:
        conversation_id = self._stream_create_agent_conversation_id(
            self.primary_agent,
            query=f"agent-delete-wrong-agent-{uuid4().hex}",
            created_conversations=created_conversations,
        )
        other_agent_key = self.secondary_agents[0]

        delete_resp = self._delete_agent_conversation(other_agent_key, conversation_id)
        assert delete_resp.status_code == 200, f"{delete_resp.status_code}: {delete_resp.text}"

        delete_body = _response_json(delete_resp)
        self._assert_delete_success(delete_body, expect_conversation=False)

        get_resp = requests.get(
            self._conversation_url(self.primary_agent, conversation_id),
            headers=self.headers,
            timeout=self.timeout,
        )
        assert get_resp.status_code == 200, f"{get_resp.status_code}: {get_resp.text}"

    def test_delete_agent_conversation_second_delete_is_a_no_op(
        self,
        created_conversations,
    ) -> None:
        conversation_id = self._stream_create_agent_conversation_id(
            self.primary_agent,
            query=f"agent-delete-twice-{uuid4().hex}",
            created_conversations=created_conversations,
        )

        first_delete = self._delete_agent_conversation(self.primary_agent, conversation_id)
        assert first_delete.status_code == 200, f"{first_delete.status_code}: {first_delete.text}"
        first_body = _response_json(first_delete)
        self._assert_delete_success(
            first_body,
            expected_conversation_id=conversation_id,
            expect_conversation=True,
        )

        second_delete = self._delete_agent_conversation(self.primary_agent, conversation_id)
        assert second_delete.status_code == 200, (
            f"{second_delete.status_code}: {second_delete.text}"
        )
        second_body = _response_json(second_delete)
        self._assert_delete_success(second_body, expect_conversation=False)

    def test_delete_agent_conversation_without_auth_returns_401(self) -> None:
        resp = requests.delete(
            self._conversation_url(self.primary_agent, "0" * 24),
            timeout=self.timeout,
        )
        assert resp.status_code == 401, f"{resp.status_code}: {resp.text}"


@pytest.mark.integration
class TestAgentConversationArchive:
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
        self.secondary_agents = list(agent_session["secondary_agents"])

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

    def _archive_url(self, agent_key: str, conversation_id: str) -> str:
        return f"{self._conversation_url(agent_key, conversation_id)}/archive"

    def _archive_agent_conversation(
        self,
        agent_key: str,
        conversation_id: str,
        *,
        params: Any | None = None,
        headers: dict[str, str] | None = None,
    ) -> requests.Response:
        return requests.post(
            self._archive_url(agent_key, conversation_id),
            headers=headers or self.headers,
            params=params,
            timeout=self.timeout,
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
                    payload = json.loads(envelope["data"])
                    raise AssertionError(f"stream emitted error event: {payload!r}")
                if envelope["event"] != "complete":
                    continue

                payload = json.loads(envelope["data"])
                conversation = payload.get("conversation") or {}
                conversation_id = conversation.get("_id")
                assert isinstance(conversation_id, str) and conversation_id, (
                    f"complete payload missing conversation._id: {payload!r}"
                )
                created_conversations.append((agent_key, conversation_id))
                return conversation_id

        raise AssertionError("agent conversation stream ended without a complete event")

    @staticmethod
    def _assert_validation_error(resp: requests.Response) -> dict[str, Any]:
        assert resp.status_code == 400, f"{resp.status_code}: {resp.text}"
        body = _response_json(resp)
        error = body.get("error")
        assert isinstance(error, dict), f"Expected error object, got: {body!r}"
        assert error.get("message") == "Validation failed", (
            f"Expected 'Validation failed', got {error.get('message')!r}"
        )
        metadata = error.get("metadata")
        assert isinstance(metadata, dict), f"Expected error.metadata object, got: {body!r}"
        details = metadata.get("errors")
        assert isinstance(details, list) and details, (
            f"Expected non-empty error.metadata.errors list, got: {body!r}"
        )
        return body

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
        created_conversations,
    ) -> None:
        conversation_id = self._stream_create_agent_conversation_id(
            self.primary_agent,
            query=f"agent-archive-happy-{uuid4().hex}",
            created_conversations=created_conversations,
        )

        resp = self._archive_agent_conversation(self.primary_agent, conversation_id)
        assert resp.status_code == 200, f"{resp.status_code}: {resp.text}"

        body = _response_json(resp)
        self._assert_archive_success(body, expected_conversation_id=conversation_id)
        assert_response_matches_spec(body, _ARCHIVE_SPEC_PATH, "POST", 200)

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
        created_conversations,
    ) -> None:
        conversation_id = self._stream_create_agent_conversation_id(
            self.primary_agent,
            query=f"agent-archive-query-{label}-{uuid4().hex}",
            created_conversations=created_conversations,
        )

        resp = self._archive_agent_conversation(
            self.primary_agent,
            conversation_id,
            params=params,
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
        resp = self._archive_agent_conversation(self.primary_agent, conversation_id)
        body = self._assert_validation_error(resp)
        assert body["error"]["metadata"]["errors"], (
            f"[{label}] Expected validation details"
        )

    def test_post_archive_agent_conversation_nonexistent_conversation_returns_404(
        self,
    ) -> None:
        resp = self._archive_agent_conversation(self.primary_agent, "0" * 24)
        assert resp.status_code == 404, f"{resp.status_code}: {resp.text}"

    def test_post_archive_agent_conversation_for_other_agent_key_returns_404(
        self,
        created_conversations,
    ) -> None:
        conversation_id = self._stream_create_agent_conversation_id(
            self.primary_agent,
            query=f"agent-archive-wrong-agent-{uuid4().hex}",
            created_conversations=created_conversations,
        )
        other_agent_key = self.secondary_agents[0]

        resp = self._archive_agent_conversation(other_agent_key, conversation_id)
        assert resp.status_code == 404, f"{resp.status_code}: {resp.text}"

    def test_post_archive_agent_conversation_already_archived_returns_400(
        self,
        created_conversations,
    ) -> None:
        conversation_id = self._stream_create_agent_conversation_id(
            self.primary_agent,
            query=f"agent-archive-twice-{uuid4().hex}",
            created_conversations=created_conversations,
        )

        first_resp = self._archive_agent_conversation(self.primary_agent, conversation_id)
        assert first_resp.status_code == 200, f"{first_resp.status_code}: {first_resp.text}"

        second_resp = self._archive_agent_conversation(self.primary_agent, conversation_id)
        assert second_resp.status_code == 400, (
            f"{second_resp.status_code}: {second_resp.text}"
        )

    def test_post_archive_agent_conversation_without_auth_returns_401(self) -> None:
        resp = requests.post(
            self._archive_url(self.primary_agent, "0" * 24),
            timeout=self.timeout,
        )
        assert resp.status_code == 401, f"{resp.status_code}: {resp.text}"


@pytest.mark.integration
class TestAgentConversationUnarchive:
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
        self.secondary_agents = list(agent_session["secondary_agents"])

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

    def _archive_url(self, agent_key: str, conversation_id: str) -> str:
        return f"{self._conversation_url(agent_key, conversation_id)}/archive"

    def _unarchive_url(self, agent_key: str, conversation_id: str) -> str:
        return f"{self._conversation_url(agent_key, conversation_id)}/unarchive"

    def _archive_agent_conversation(
        self,
        agent_key: str,
        conversation_id: str,
        *,
        params: Any | None = None,
        headers: dict[str, str] | None = None,
    ) -> requests.Response:
        return requests.post(
            self._archive_url(agent_key, conversation_id),
            headers=headers or self.headers,
            params=params,
            timeout=self.timeout,
        )

    def _unarchive_agent_conversation(
        self,
        agent_key: str,
        conversation_id: str,
        *,
        params: Any | None = None,
        headers: dict[str, str] | None = None,
    ) -> requests.Response:
        return requests.post(
            self._unarchive_url(agent_key, conversation_id),
            headers=headers or self.headers,
            params=params,
            timeout=self.timeout,
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
                    payload = json.loads(envelope["data"])
                    raise AssertionError(f"stream emitted error event: {payload!r}")
                if envelope["event"] != "complete":
                    continue

                payload = json.loads(envelope["data"])
                conversation = payload.get("conversation") or {}
                conversation_id = conversation.get("_id")
                assert isinstance(conversation_id, str) and conversation_id, (
                    f"complete payload missing conversation._id: {payload!r}"
                )
                created_conversations.append((agent_key, conversation_id))
                return conversation_id

        raise AssertionError("agent conversation stream ended without a complete event")

    @staticmethod
    def _assert_validation_error(resp: requests.Response) -> dict[str, Any]:
        assert resp.status_code == 400, f"{resp.status_code}: {resp.text}"
        body = _response_json(resp)
        error = body.get("error")
        assert isinstance(error, dict), f"Expected error object, got: {body!r}"
        assert error.get("message") == "Validation failed", (
            f"Expected 'Validation failed', got {error.get('message')!r}"
        )
        metadata = error.get("metadata")
        assert isinstance(metadata, dict), f"Expected error.metadata object, got: {body!r}"
        details = metadata.get("errors")
        assert isinstance(details, list) and details, (
            f"Expected non-empty error.metadata.errors list, got: {body!r}"
        )
        return body

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
        created_conversations,
    ) -> None:
        conversation_id = self._stream_create_agent_conversation_id(
            self.primary_agent,
            query=f"agent-unarchive-happy-{uuid4().hex}",
            created_conversations=created_conversations,
        )
        archive_resp = self._archive_agent_conversation(self.primary_agent, conversation_id)
        assert archive_resp.status_code == 200, f"{archive_resp.status_code}: {archive_resp.text}"

        resp = self._unarchive_agent_conversation(self.primary_agent, conversation_id)
        assert resp.status_code == 200, f"{resp.status_code}: {resp.text}"

        body = _response_json(resp)
        self._assert_unarchive_success(body, expected_conversation_id=conversation_id)
        assert_response_matches_spec(body, _UNARCHIVE_SPEC_PATH, "POST", 200)

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
        created_conversations,
    ) -> None:
        conversation_id = self._stream_create_agent_conversation_id(
            self.primary_agent,
            query=f"agent-unarchive-query-{label}-{uuid4().hex}",
            created_conversations=created_conversations,
        )
        archive_resp = self._archive_agent_conversation(self.primary_agent, conversation_id)
        assert archive_resp.status_code == 200, f"{archive_resp.status_code}: {archive_resp.text}"

        resp = self._unarchive_agent_conversation(
            self.primary_agent,
            conversation_id,
            params=params,
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
        resp = self._unarchive_agent_conversation(self.primary_agent, conversation_id)
        body = self._assert_validation_error(resp)
        assert body["error"]["metadata"]["errors"], (
            f"[{label}] Expected validation details"
        )

    def test_post_unarchive_agent_conversation_nonexistent_conversation_returns_404(
        self,
    ) -> None:
        resp = self._unarchive_agent_conversation(self.primary_agent, "0" * 24)
        assert resp.status_code == 404, f"{resp.status_code}: {resp.text}"

    def test_post_unarchive_agent_conversation_for_other_agent_key_returns_404(
        self,
        created_conversations,
    ) -> None:
        conversation_id = self._stream_create_agent_conversation_id(
            self.primary_agent,
            query=f"agent-unarchive-wrong-agent-{uuid4().hex}",
            created_conversations=created_conversations,
        )
        archive_resp = self._archive_agent_conversation(self.primary_agent, conversation_id)
        assert archive_resp.status_code == 200, f"{archive_resp.status_code}: {archive_resp.text}"
        other_agent_key = self.secondary_agents[0]

        resp = self._unarchive_agent_conversation(other_agent_key, conversation_id)
        assert resp.status_code == 404, f"{resp.status_code}: {resp.text}"

    def test_post_unarchive_agent_conversation_not_archived_returns_400(
        self,
        created_conversations,
    ) -> None:
        conversation_id = self._stream_create_agent_conversation_id(
            self.primary_agent,
            query=f"agent-unarchive-active-{uuid4().hex}",
            created_conversations=created_conversations,
        )

        resp = self._unarchive_agent_conversation(self.primary_agent, conversation_id)
        assert resp.status_code == 400, f"{resp.status_code}: {resp.text}"

    def test_post_unarchive_agent_conversation_without_auth_returns_401(self) -> None:
        resp = requests.post(
            self._unarchive_url(self.primary_agent, "0" * 24),
            timeout=self.timeout,
        )
        assert resp.status_code == 401, f"{resp.status_code}: {resp.text}"
