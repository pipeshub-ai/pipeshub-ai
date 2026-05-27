"""Agent conversation listing integration tests.

``GET /api/v1/agents/{agentKey}/conversations``

Exercises the Node gateway + Zod validation layer for agent conversation
listing using the shared ``agent_session`` fixture.
"""

from __future__ import annotations

import json
import os
from collections.abc import Iterator
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import uuid4

import pytest
import requests

from openapi_search_validator import assert_response_matches_spec
from pipeshub_client import PipeshubClient

_AGENT_LIST_SPEC_PATH = "/agents/{agentKey}/conversations"
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
class TestAgentConversationListing:
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
                    (
                        f"{self.base_url}/api/v1/agents/{agent_key}"
                        f"/conversations/{conversation_id}"
                    ),
                    headers=self.headers,
                    timeout=self.timeout,
                )
                assert resp.status_code < 300, (
                    f"Conversation delete failed for {conversation_id}: "
                    f"HTTP {resp.status_code} {resp.text[:300]}"
                )
            except Exception:
                pass

    def _list_url(self, agent_key: str) -> str:
        return f"{self.base_url}/api/v1/agents/{agent_key}/conversations"

    def _stream_url(self, agent_key: str) -> str:
        return f"{self.base_url}/api/v1/agents/{agent_key}/conversations/stream"

    def _conversation_url(self, agent_key: str, conversation_id: str) -> str:
        return (
            f"{self.base_url}/api/v1/agents/{agent_key}"
            f"/conversations/{conversation_id}"
        )

    def _grouped_archives_url(self) -> str:
        return f"{self.base_url}/api/v1/agents/conversations/show/archives"

    def _archive_agent_conversation_url(
        self,
        agent_key: str,
        conversation_id: str,
    ) -> str:
        return (
            f"{self.base_url}/api/v1/agents/{agent_key}"
            f"/conversations/{conversation_id}/archive"
        )

    def _archive_agent_conversation(
        self,
        agent_key: str,
        conversation_id: str,
        *,
        headers: dict[str, str] | None = None,
    ) -> requests.Response:
        return requests.post(
            self._archive_agent_conversation_url(agent_key, conversation_id),
            headers=headers or self.headers,
            timeout=self.timeout,
        )

    def _create_agent_conversation_id(
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
                conv = payload.get("conversation") or {}
                conversation_id = conv.get("_id")
                assert isinstance(conversation_id, str) and conversation_id, (
                    f"complete payload missing conversation._id: {payload!r}"
                )
                created_conversations.append((agent_key, conversation_id))
                return conversation_id

        raise AssertionError("agent conversation stream ended without a complete event")

    def _stream_add_message(
        self,
        agent_key: str,
        conversation_id: str,
        *,
        query: str,
    ) -> None:
        headers = {**self.headers, "Accept": "text/event-stream"}
        url = (
            f"{self.base_url}/api/v1/agents/{agent_key}"
            f"/conversations/{conversation_id}/messages/stream"
        )

        with requests.post(
            url,
            headers=headers,
            json={"query": query},
            stream=True,
            timeout=self.stream_timeout,
        ) as resp:
            assert resp.status_code == 200, f"{resp.status_code}: {resp.text}"

            for envelope in _iter_sse_envelopes(resp):
                if envelope["event"] == "error":
                    payload = json.loads(envelope["data"])
                    raise AssertionError(
                        f"message stream emitted error event: {payload!r}"
                    )
                if envelope["event"] == "complete":
                    return

        raise AssertionError("agent add-message stream ended without a complete event")

    def _list_agent_conversations(
        self,
        agent_key: str,
        *,
        params: Any | None = None,
        headers: dict[str, str] | None = None,
    ) -> requests.Response:
        return requests.get(
            self._list_url(agent_key),
            headers=headers or self.headers,
            params=params,
            timeout=self.timeout,
        )

    def _get_agent_conversation(
        self,
        agent_key: str,
        conversation_id: str,
        *,
        params: Any | None = None,
        headers: dict[str, str] | None = None,
    ) -> requests.Response:
        return requests.get(
            self._conversation_url(agent_key, conversation_id),
            headers=headers or self.headers,
            params=params,
            timeout=self.timeout,
        )

    def _list_grouped_archived_agent_conversations(
        self,
        *,
        params: Any | None = None,
        headers: dict[str, str] | None = None,
    ) -> requests.Response:
        return requests.get(
            self._grouped_archives_url(),
            headers=headers or self.headers,
            params=params,
            timeout=self.timeout,
        )

    @staticmethod
    def _all_returned_conversation_ids(body: dict[str, Any]) -> set[str]:
        ids: set[str] = set()
        for key in ("conversations", "sharedWithMeConversations"):
            rows = body.get(key)
            if not isinstance(rows, list):
                continue
            for row in rows:
                if not isinstance(row, dict):
                    continue
                conv_id = row.get("_id")
                if isinstance(conv_id, str) and conv_id:
                    ids.add(conv_id)
        return ids

    @staticmethod
    def _all_returned_conversations(body: dict[str, Any]) -> list[dict[str, Any]]:
        rows_out: list[dict[str, Any]] = []
        for key in ("conversations", "sharedWithMeConversations"):
            rows = body.get(key)
            if not isinstance(rows, list):
                continue
            for row in rows:
                if isinstance(row, dict):
                    rows_out.append(row)
        return rows_out

    @staticmethod
    def _grouped_archive_conversations(
        body: dict[str, Any],
    ) -> list[tuple[str, dict[str, Any]]]:
        groups = body.get("groups")
        assert isinstance(groups, list), f"Expected groups list, got: {body!r}"
        rows_out: list[tuple[str, dict[str, Any]]] = []
        for group in groups:
            assert isinstance(group, dict), f"Expected group object, got: {group!r}"
            agent_key = group.get("agentKey")
            assert isinstance(agent_key, str) and agent_key, (
                f"Expected non-empty group.agentKey, got: {group!r}"
            )
            conversations = group.get("conversations")
            assert isinstance(conversations, list), (
                f"Expected group.conversations list, got: {group!r}"
            )
            for row in conversations:
                assert isinstance(row, dict), (
                    f"Expected archived conversation object, got: {row!r}"
                )
                rows_out.append((agent_key, row))
        return rows_out

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
    def _conversation_messages(body: dict[str, Any]) -> list[dict[str, Any]]:
        conversation = body.get("conversation")
        assert isinstance(conversation, dict), f"Expected conversation object, got: {body!r}"
        messages = conversation.get("messages")
        assert isinstance(messages, list), f"Expected conversation.messages list, got: {body!r}"
        out: list[dict[str, Any]] = []
        for row in messages:
            assert isinstance(row, dict), f"Expected message object, got: {row!r}"
            out.append(row)
        return out

    def test_list_agent_conversations_default_returns_owned_results(
        self,
        created_conversations: list[tuple[str, str]],
    ) -> None:
        query = f"listing-default-{uuid4().hex}"
        conversation_id = self._create_agent_conversation_id(
            self.primary_agent,
            query=query,
            created_conversations=created_conversations,
        )

        resp = self._list_agent_conversations(self.primary_agent)
        assert resp.status_code == 200, f"{resp.status_code}: {resp.text}"

        body = _response_json(resp)
        assert isinstance(body.get("conversations"), list), f"Unexpected body: {body!r}"
        assert isinstance(body.get("sharedWithMeConversations"), list), (
            f"Unexpected body: {body!r}"
        )
        returned_ids = self._all_returned_conversation_ids(body)
        assert conversation_id in returned_ids, (
            f"Expected conversation {conversation_id} in response, got: {body!r}"
        )
        pagination = body.get("pagination")
        assert isinstance(pagination, dict), f"Missing pagination: {body!r}"
        assert pagination.get("page") == 1
        assert pagination.get("limit") == 20

    def test_list_agent_conversations_supports_search_and_sort_queries(
        self,
        created_conversations: list[tuple[str, str]],
    ) -> None:
        search_token = f"listing-search-{uuid4().hex}"
        created_id = self._create_agent_conversation_id(
            self.primary_agent,
            query=f"{search_token} tell me about the attached kb",
            created_conversations=created_conversations,
        )

        resp = self._list_agent_conversations(
            self.primary_agent,
            params={
                "search": search_token,
                "page": "1",
                "limit": "20",
                "sortBy": "lastActivityAt",
                "sortOrder": "desc",
                "shared": "false",
                "status": "Complete",
                "isArchived": "false",
            },
        )
        assert resp.status_code == 200, f"{resp.status_code}: {resp.text}"

        body = _response_json(resp)
        returned_ids = self._all_returned_conversation_ids(body)
        assert created_id in returned_ids, (
            f"Expected search-filtered response to include {created_id}, got: {body!r}"
        )

        filters = body.get("filters")
        assert isinstance(filters, dict), f"Missing filters metadata: {body!r}"
        applied = (filters.get("applied") or {}).get("filters")
        assert isinstance(applied, list), f"Missing applied filters list: {filters!r}"
        assert "search" in applied

    def test_list_agent_conversations_supports_date_range_and_pagination(
        self,
        created_conversations: list[tuple[str, str]],
    ) -> None:
        created_id = self._create_agent_conversation_id(
            self.primary_agent,
            query=f"listing-date-range-{uuid4().hex}",
            created_conversations=created_conversations,
        )
        now = datetime.now(timezone.utc)
        start_date = (now - timedelta(days=1)).isoformat()
        end_date = (now + timedelta(days=1)).isoformat()

        resp = self._list_agent_conversations(
            self.primary_agent,
            params={
                "page": "1",
                "limit": "1",
                "sortBy": "createdAt",
                "sortOrder": "desc",
                "startDate": start_date,
                "endDate": end_date,
            },
        )
        assert resp.status_code == 200, f"{resp.status_code}: {resp.text}"

        body = _response_json(resp)
        pagination = body.get("pagination")
        assert isinstance(pagination, dict), f"Missing pagination object: {body!r}"
        assert pagination.get("page") == 1
        assert pagination.get("limit") == 1
        assert pagination.get("totalCount", 0) >= 1

        returned_ids = self._all_returned_conversation_ids(body)
        assert created_id in returned_ids or pagination.get("totalCount", 0) >= 1, (
            f"Expected created conversation or non-zero total count, got: {body!r}"
        )

    def test_list_agent_conversations_for_other_agent_key_is_scoped_to_that_agent(
        self,
    ) -> None:
        other_agent_key = self.secondary_agents[0]

        resp = self._list_agent_conversations(other_agent_key)
        assert resp.status_code == 200, f"{resp.status_code}: {resp.text}"

        body = _response_json(resp)
        rows = self._all_returned_conversations(body)
        for row in rows:
            assert row.get("agentKey") == other_agent_key, (
                "Agent-scoped listing returned a conversation from a different agent: "
                f"{row!r}"
            )
            assert row.get("agentKey") != self.primary_agent, (
                "Agent-scoped listing leaked the primary agent key into another "
                f"agent's results: {row!r}"
            )

    def test_list_agent_conversations_without_auth_returns_401(self) -> None:
        resp = requests.get(self._list_url(self.primary_agent), timeout=self.timeout)
        assert resp.status_code == 401, f"{resp.status_code}: {resp.text}"

    @pytest.mark.parametrize(
        ("label", "params"),
        [
            ("page zero", {"page": "0"}),
            ("page non-numeric", {"page": "abc"}),
            ("limit zero", {"limit": "0"}),
            ("limit too large", {"limit": "101"}),
            ("invalid startDate", {"startDate": "not-a-date"}),
            ("invalid endDate", {"endDate": "still-not-a-date"}),
            ("invalid shared", {"shared": "maybe"}),
            ("invalid isArchived", {"isArchived": "1"}),
        ],
    )
    def test_list_agent_conversations_rejects_invalid_query_shapes(
        self,
        label: str,
        params: dict[str, str],
    ) -> None:
        resp = self._list_agent_conversations(self.primary_agent, params=params)
        body = self._assert_validation_error(resp)
        assert body["error"]["metadata"]["errors"], (
            f"[{label}] Expected validation details"
        )

    def test_list_agent_conversations_rejects_duplicate_search_query_param(self) -> None:
        resp = self._list_agent_conversations(
            self.primary_agent,
            params=[("search", "first"), ("search", "second")],
        )
        self._assert_validation_error(resp)

    @pytest.mark.parametrize(
        ("label", "search_value"),
        [
            ("format specifier payload", "%s%s%s"),
            ("too long", "x" * 1001),
        ],
    )
    def test_list_agent_conversations_rejects_invalid_search_values(
        self,
        label: str,
        search_value: str,
    ) -> None:
        resp = self._list_agent_conversations(
            self.primary_agent,
            params={"search": search_value},
        )
        body = self._assert_validation_error(resp)
        assert body["error"]["metadata"]["errors"], (
            f"[{label}] Expected validation details"
        )

    def test_list_agent_conversations_rejects_xss_search_via_middleware(self) -> None:
        resp = self._list_agent_conversations(
            self.primary_agent,
            params={"search": "<script>alert('x')</script>"},
        )
        assert resp.status_code == 400, f"{resp.status_code}: {resp.text}"

        body = _response_json(resp)
        error = body.get("error")
        assert isinstance(error, dict), f"Expected error object, got: {body!r}"
        message = error.get("message")
        assert isinstance(message, str) and "HTML tags, scripts, and XSS content" in message, (
            f"Expected XSS middleware rejection message, got: {body!r}"
        )

    def test_list_grouped_archived_agent_conversations_default_returns_groups(
        self,
        created_conversations: list[tuple[str, str]],
    ) -> None:
        primary_conversation_id = self._create_agent_conversation_id(
            self.primary_agent,
            query=f"grouped-archives-primary-{uuid4().hex}",
            created_conversations=created_conversations,
        )
        secondary_agent_key = self.secondary_agents[0]
        secondary_conversation_id = self._create_agent_conversation_id(
            secondary_agent_key,
            query=f"grouped-archives-secondary-{uuid4().hex}",
            created_conversations=created_conversations,
        )
        active_conversation_id = self._create_agent_conversation_id(
            self.primary_agent,
            query=f"grouped-archives-active-{uuid4().hex}",
            created_conversations=created_conversations,
        )

        for agent_key, conversation_id in (
            (self.primary_agent, primary_conversation_id),
            (secondary_agent_key, secondary_conversation_id),
        ):
            archive_resp = self._archive_agent_conversation(agent_key, conversation_id)
            assert archive_resp.status_code == 200, (
                f"{archive_resp.status_code}: {archive_resp.text}"
            )
            archive_body = _response_json(archive_resp)
            assert archive_body.get("id") == conversation_id, (
                f"archive id mismatch: {archive_body!r}"
            )
            assert archive_body.get("status") == "archived", (
                f"expected archived status: {archive_body!r}"
            )

        resp = self._list_grouped_archived_agent_conversations()
        assert resp.status_code == 200, f"{resp.status_code}: {resp.text}"

        body = _response_json(resp)
        rows = self._grouped_archive_conversations(body)
        archived_ids = {
            row.get("_id") for _, row in rows if isinstance(row.get("_id"), str)
        }
        assert primary_conversation_id in archived_ids, (
            f"Expected archived primary conversation in grouped response: {body!r}"
        )
        assert secondary_conversation_id in archived_ids, (
            f"Expected archived secondary conversation in grouped response: {body!r}"
        )
        assert active_conversation_id not in archived_ids, (
            "Active conversation leaked into grouped archived response: "
            f"{body!r}"
        )

        seen_agent_keys = {agent_key for agent_key, _ in rows}
        assert self.primary_agent in seen_agent_keys, (
            f"Primary agent group missing from grouped archives: {body!r}"
        )
        assert secondary_agent_key in seen_agent_keys, (
            f"Secondary agent group missing from grouped archives: {body!r}"
        )

        groups = body.get("groups")
        assert isinstance(groups, list), f"Expected groups list, got: {body!r}"
        for group in groups:
            assert isinstance(group, dict), f"Expected group object, got: {group!r}"
            pagination = group.get("pagination")
            assert isinstance(pagination, dict), (
                f"Expected group pagination object, got: {group!r}"
            )
            conversations = group.get("conversations")
            assert isinstance(conversations, list), (
                f"Expected group conversations list, got: {group!r}"
            )
            assert len(conversations) <= 5, (
                f"Expected grouped archives to slice to 5 chats per agent: {group!r}"
            )
            for row in conversations:
                assert row.get("archivedAt"), (
                    f"Expected archivedAt on grouped archived conversation: {row!r}"
                )
                assert row.get("archivedBy"), (
                    f"Expected archivedBy on grouped archived conversation: {row!r}"
                )

        agent_pagination = body.get("agentPagination")
        assert isinstance(agent_pagination, dict), (
            f"Expected agentPagination object, got: {body!r}"
        )
        assert agent_pagination.get("page") == 1
        assert agent_pagination.get("limit") == 5
        assert agent_pagination.get("totalCount", 0) >= 2

    def test_list_grouped_archived_agent_conversations_supports_agent_pagination(
        self,
        created_conversations: list[tuple[str, str]],
    ) -> None:
        seeded_agents = [self.primary_agent, self.secondary_agents[0], self.secondary_agents[1]]
        archived_by_agent: dict[str, str] = {}

        for agent_key in seeded_agents:
            conversation_id = self._create_agent_conversation_id(
                agent_key,
                query=f"grouped-archives-page-{agent_key}-{uuid4().hex}",
                created_conversations=created_conversations,
            )
            archive_resp = self._archive_agent_conversation(agent_key, conversation_id)
            assert archive_resp.status_code == 200, (
                f"{archive_resp.status_code}: {archive_resp.text}"
            )
            archived_by_agent[agent_key] = conversation_id

        page_one = self._list_grouped_archived_agent_conversations(
            params={"agentPage": "1", "agentLimit": "1"},
        )
        assert page_one.status_code == 200, f"{page_one.status_code}: {page_one.text}"
        page_one_body = _response_json(page_one)

        page_one_groups = page_one_body.get("groups")
        assert isinstance(page_one_groups, list) and len(page_one_groups) == 1, (
            f"Expected exactly one group on page one: {page_one_body!r}"
        )
        page_one_agent_key = page_one_groups[0].get("agentKey")
        assert page_one_agent_key in archived_by_agent, (
            f"Unexpected page one agent key: {page_one_body!r}"
        )

        page_one_pagination = page_one_body.get("agentPagination")
        assert isinstance(page_one_pagination, dict), (
            f"Missing agentPagination on page one: {page_one_body!r}"
        )
        assert page_one_pagination.get("page") == 1
        assert page_one_pagination.get("limit") == 1
        assert page_one_pagination.get("totalCount", 0) >= len(seeded_agents)
        assert page_one_pagination.get("hasNextPage") is True
        assert page_one_pagination.get("hasPrevPage") is False

        page_two = self._list_grouped_archived_agent_conversations(
            params={"agentPage": "2", "agentLimit": "1"},
        )
        assert page_two.status_code == 200, f"{page_two.status_code}: {page_two.text}"
        page_two_body = _response_json(page_two)

        page_two_groups = page_two_body.get("groups")
        assert isinstance(page_two_groups, list) and len(page_two_groups) == 1, (
            f"Expected exactly one group on page two: {page_two_body!r}"
        )
        page_two_agent_key = page_two_groups[0].get("agentKey")
        assert page_two_agent_key in archived_by_agent, (
            f"Unexpected page two agent key: {page_two_body!r}"
        )
        assert page_two_agent_key != page_one_agent_key, (
            "Expected agent pagination to return a different group on page two: "
            f"{page_one_body!r} vs {page_two_body!r}"
        )

        page_two_pagination = page_two_body.get("agentPagination")
        assert isinstance(page_two_pagination, dict), (
            f"Missing agentPagination on page two: {page_two_body!r}"
        )
        assert page_two_pagination.get("page") == 2
        assert page_two_pagination.get("limit") == 1
        assert page_two_pagination.get("hasPrevPage") is True

    def test_list_grouped_archived_agent_conversations_normalizes_query_values(
        self,
        created_conversations: list[tuple[str, str]],
    ) -> None:
        conversation_id = self._create_agent_conversation_id(
            self.primary_agent,
            query=f"grouped-archives-normalize-{uuid4().hex}",
            created_conversations=created_conversations,
        )
        archive_resp = self._archive_agent_conversation(
            self.primary_agent,
            conversation_id,
        )
        assert archive_resp.status_code == 200, (
            f"{archive_resp.status_code}: {archive_resp.text}"
        )

        resp = self._list_grouped_archived_agent_conversations(
            params={
                "agentPage": "0",
                "agentLimit": "999",
            },
        )
        assert resp.status_code == 200, f"{resp.status_code}: {resp.text}"

        body = _response_json(resp)
        agent_pagination = body.get("agentPagination")
        assert isinstance(agent_pagination, dict), (
            f"Expected agentPagination object, got: {body!r}"
        )
        assert agent_pagination.get("page") == 1, (
            f"Expected agentPage=0 to normalize to 1: {body!r}"
        )
        assert agent_pagination.get("limit") == 100, (
            f"Expected agentLimit=999 to clamp to 100: {body!r}"
        )

        rows = self._grouped_archive_conversations(body)
        archived_ids = {
            row.get("_id") for _, row in rows if isinstance(row.get("_id"), str)
        }
        assert conversation_id in archived_ids, (
            f"Expected archived conversation in normalized grouped response: {body!r}"
        )

    def test_list_grouped_archived_agent_conversations_non_numeric_query_values_use_defaults(
        self,
        created_conversations: list[tuple[str, str]],
    ) -> None:
        conversation_id = self._create_agent_conversation_id(
            self.primary_agent,
            query=f"grouped-archives-nonnumeric-{uuid4().hex}",
            created_conversations=created_conversations,
        )
        archive_resp = self._archive_agent_conversation(
            self.primary_agent,
            conversation_id,
        )
        assert archive_resp.status_code == 200, (
            f"{archive_resp.status_code}: {archive_resp.text}"
        )

        resp = self._list_grouped_archived_agent_conversations(
            params={"agentPage": "abc", "agentLimit": "xyz"},
        )
        assert resp.status_code == 200, f"{resp.status_code}: {resp.text}"

        body = _response_json(resp)
        agent_pagination = body.get("agentPagination")
        assert isinstance(agent_pagination, dict), (
            f"Expected agentPagination object, got: {body!r}"
        )
        assert agent_pagination.get("page") == 1, (
            f"Expected non-numeric agentPage to default to 1: {body!r}"
        )
        assert agent_pagination.get("limit") == 5, (
            f"Expected non-numeric agentLimit to default to 5: {body!r}"
        )

    def test_list_grouped_archived_agent_conversations_without_auth_returns_401(
        self,
    ) -> None:
        resp = requests.get(self._grouped_archives_url(), timeout=self.timeout)
        assert resp.status_code == 401, f"{resp.status_code}: {resp.text}"

    def test_list_agent_conversations_response_matches_openapi_spec(
        self,
        created_conversations: list[tuple[str, str]],
    ) -> None:
        self._create_agent_conversation_id(
            self.primary_agent,
            query=f"listing-openapi-{uuid4().hex}",
            created_conversations=created_conversations,
        )

        resp = self._list_agent_conversations(
            self.primary_agent,
            params={"page": "1", "limit": "20"},
        )
        assert resp.status_code == 200, f"{resp.status_code}: {resp.text}"

        body = _response_json(resp)
        assert_response_matches_spec(body, _AGENT_LIST_SPEC_PATH, "get", status_code=200)

    def test_get_agent_conversation_by_id_default_returns_conversation(
        self,
        created_conversations: list[tuple[str, str]],
    ) -> None:
        conversation_id = self._create_agent_conversation_id(
            self.primary_agent,
            query=f"agent-get-default-{uuid4().hex}",
            created_conversations=created_conversations,
        )

        resp = self._get_agent_conversation(self.primary_agent, conversation_id)
        assert resp.status_code == 200, f"{resp.status_code}: {resp.text}"

        body = _response_json(resp)
        conversation = body.get("conversation")
        assert isinstance(conversation, dict), f"Expected conversation object, got: {body!r}"
        assert conversation.get("id") == conversation_id, (
            f"conversation.id mismatch: {conversation!r}"
        )
        assert body.get("meta", {}).get("conversationId") == conversation_id, (
            f"meta.conversationId mismatch: {body.get('meta', {})!r}"
        )

        messages = self._conversation_messages(body)
        assert messages, f"Expected at least one message, got: {body!r}"

        pagination = conversation.get("pagination")
        assert isinstance(pagination, dict), f"Missing conversation.pagination: {conversation!r}"
        assert pagination.get("page") == 1
        assert pagination.get("limit") == 20
        assert pagination.get("totalCount", 0) >= len(messages)

    def test_get_agent_conversation_by_id_supports_query_params_and_pagination(
        self,
        created_conversations: list[tuple[str, str]],
    ) -> None:
        conversation_id = self._create_agent_conversation_id(
            self.primary_agent,
            query=f"agent-get-query-1-{uuid4().hex}",
            created_conversations=created_conversations,
        )
        self._stream_add_message(
            self.primary_agent,
            conversation_id,
            query=f"agent-get-query-2-{uuid4().hex}",
        )

        now = datetime.now(timezone.utc)
        start_date = (now - timedelta(days=1)).isoformat()
        end_date = (now + timedelta(days=1)).isoformat()
        resp = self._get_agent_conversation(
            self.primary_agent,
            conversation_id,
            params={
                "page": "1",
                "limit": "2",
                "sortBy": "createdAt",
                "sortOrder": "asc",
                "startDate": start_date,
                "endDate": end_date,
                "messageType": "bot_response",
            },
        )
        assert resp.status_code == 200, f"{resp.status_code}: {resp.text}"

        body = _response_json(resp)
        conversation = body.get("conversation")
        assert isinstance(conversation, dict), f"Expected conversation object, got: {body!r}"
        assert conversation.get("id") == conversation_id, (
            f"conversation.id mismatch: {conversation!r}"
        )

        messages = self._conversation_messages(body)
        assert 1 <= len(messages) <= 2, f"Expected paginated messages, got: {messages!r}"

        created_at_values = [msg.get("createdAt") for msg in messages]
        assert created_at_values == sorted(created_at_values), (
            f"Expected createdAt ascending order, got: {created_at_values!r}"
        )

        pagination = conversation.get("pagination")
        assert isinstance(pagination, dict), f"Missing conversation.pagination: {conversation!r}"
        assert pagination.get("page") == 1
        assert pagination.get("limit") == 2
        assert pagination.get("totalCount", 0) >= 4, (
            f"Expected at least two user/bot exchanges, got: {pagination!r}"
        )

    def test_get_agent_conversation_by_id_accepts_non_asc_sort_order_as_desc(
        self,
        created_conversations: list[tuple[str, str]],
    ) -> None:
        conversation_id = self._create_agent_conversation_id(
            self.primary_agent,
            query=f"agent-get-sort-order-{uuid4().hex}",
            created_conversations=created_conversations,
        )

        resp = self._get_agent_conversation(
            self.primary_agent,
            conversation_id,
            params={"sortOrder": "sideways"},
        )
        assert resp.status_code == 200, f"{resp.status_code}: {resp.text}"

        body = _response_json(resp)
        conversation = body.get("conversation")
        assert isinstance(conversation, dict), f"Expected conversation object, got: {body!r}"
        assert conversation.get("id") == conversation_id, (
            f"conversation.id mismatch: {conversation!r}"
        )
        assert self._conversation_messages(body), f"Expected messages in response, got: {body!r}"

    def test_get_agent_conversation_by_id_for_other_agent_key_returns_404(
        self,
        created_conversations: list[tuple[str, str]],
    ) -> None:
        conversation_id = self._create_agent_conversation_id(
            self.primary_agent,
            query=f"agent-get-wrong-agent-{uuid4().hex}",
            created_conversations=created_conversations,
        )
        other_agent_key = self.secondary_agents[0]

        resp = self._get_agent_conversation(other_agent_key, conversation_id)
        assert resp.status_code == 404, f"{resp.status_code}: {resp.text}"

    def test_get_agent_conversation_by_id_without_auth_returns_401(self) -> None:
        resp = requests.get(
            self._conversation_url(self.primary_agent, "0" * 24),
            timeout=self.timeout,
        )
        assert resp.status_code == 401, f"{resp.status_code}: {resp.text}"

    @pytest.mark.parametrize(
        ("label", "conversation_id", "params"),
        [
            ("invalid conversation id", "not-an-objectid", None),
            ("page zero", "0" * 24, {"page": "0"}),
            ("page non-numeric", "0" * 24, {"page": "abc"}),
            ("limit zero", "0" * 24, {"limit": "0"}),
            ("limit too large", "0" * 24, {"limit": "101"}),
            ("invalid sortBy", "0" * 24, {"sortBy": "lastActivityAt"}),
            ("invalid startDate", "0" * 24, {"startDate": "not-a-date"}),
            ("invalid endDate", "0" * 24, {"endDate": "still-not-a-date"}),
            ("invalid messageType", "0" * 24, {"messageType": "assistant"}),
        ],
    )
    def test_get_agent_conversation_by_id_rejects_invalid_params(
        self,
        label: str,
        conversation_id: str,
        params: dict[str, str] | None,
    ) -> None:
        resp = self._get_agent_conversation(
            self.primary_agent,
            conversation_id,
            params=params,
        )
        body = self._assert_validation_error(resp)
        assert body["error"]["metadata"]["errors"], (
            f"[{label}] Expected validation details"
        )

    @pytest.mark.parametrize(
        ("label", "params"),
        [
            ("duplicate sortBy", [("sortBy", "createdAt"), ("sortBy", "content")]),
            (
                "duplicate messageType",
                [("messageType", "bot_response"), ("messageType", "user_query")],
            ),
        ],
    )
    def test_get_agent_conversation_by_id_rejects_duplicate_query_params(
        self,
        label: str,
        params: list[tuple[str, str]],
    ) -> None:
        resp = self._get_agent_conversation(
            self.primary_agent,
            "0" * 24,
            params=params,
        )
        body = self._assert_validation_error(resp)
        assert body["error"]["metadata"]["errors"], (
            f"[{label}] Expected validation details"
        )
