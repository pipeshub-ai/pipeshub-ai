"""Agent conversation listing integration tests.

``GET /api/v1/agents/{agentKey}/conversations``

Exercises the Node gateway + Zod validation layer for agent conversation
listing using the shared ``agent_session`` fixture.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import uuid4

import pytest
import requests

from openapi_search_validator import assert_response_matches_spec
from pipeshub_client import PipeshubClient

_AGENT_LIST_SPEC_PATH = "/agents/{agentKey}/conversations"
_SSE_MAX_EVENTS = 10_000


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
):
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
