"""Agent conversation archive integration tests."""

from __future__ import annotations

import json
import math
import os
import time
import uuid
from typing import Any

import pytest
import requests

from openapi_search_validator import assert_response_matches_spec
from pipeshub_client import PipeshubClient


_PRIMARY_AGENT_ARCHIVE_COUNT = 6
_EXTRA_AGENT_GROUP_COUNT = 1
_GROUPED_AGENT_ARCHIVE_PATH = "/agents/conversations/show/archives"
_PRIMARY_AGENT_KEY = "81d9ec44-4f65-43b5-af35-7736fb87dabf"
_EXTRA_AGENT_KEYS = [
    "211b2897-fa92-40b4-9e55-4b30b7c5694f",
    # "REPLACE_WITH_EXISTING_EXTRA_AGENT_KEY_3",
    # "REPLACE_WITH_EXISTING_EXTRA_AGENT_KEY_4",
    # "REPLACE_WITH_EXISTING_EXTRA_AGENT_KEY_5",
]
_SSE_MAX_EVENTS = 10_000


def _configured_agent_keys() -> tuple[str, list[str]]:
    primary = _PRIMARY_AGENT_KEY.strip()
    extra = [key.strip() for key in _EXTRA_AGENT_KEYS]

    if (
        not primary
        or primary.startswith("REPLACE_WITH_")
        or any(not key or key.startswith("REPLACE_WITH_") for key in extra)
    ):
        pytest.skip(
            "Replace the placeholder agent keys in "
            "integration_test_agent_conversation.py before running these tests."
        )

    return primary, extra


def _extract_conversation_id(payload: dict[str, Any]) -> str:
    conversation = (
        payload.get("conversation")
        if isinstance(payload.get("conversation"), dict)
        else payload
    )
    for key in ("_id", "id"):
        value = conversation.get(key) if isinstance(conversation, dict) else None
        if isinstance(value, str) and value.strip():
            return value
    raise AssertionError(f"Could not extract conversation id from payload: {payload!r}")


def _iter_sse_envelopes(resp: requests.Response, *, max_events: int = _SSE_MAX_EVENTS):
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


# Sets up the test data: starts chats with two agents, then archives them.
@pytest.fixture(scope="class")
def archived_agent_groups_dataset(
    pipeshub_client: PipeshubClient,
) -> dict[str, Any]:
    primary_agent_key, extra_agents = _configured_agent_keys()
    base_url = pipeshub_client.base_url
    headers = pipeshub_client.auth_headers
    timeout = int(os.getenv("PIPESHUB_TEST_TIMEOUT", "60"))
    stream_timeout = max(timeout, 120)
    agents_base_url = f"{base_url}/api/v1/agents"
    created_conversations: list[tuple[str, str]] = []

    def create_archived_conversation(agent_key: str, query: str) -> str:
        stream_headers = {**headers, "Accept": "text/event-stream"}
        with requests.post(
            f"{agents_base_url}/{agent_key}/conversations/stream",
            headers=stream_headers,
            json={"query": query},
            stream=True,
            timeout=stream_timeout,
        ) as create_resp:
            assert create_resp.status_code == 200, (
                f"{create_resp.status_code}: {create_resp.text}"
            )
            conversation_id: str | None = None
            for envelope in _iter_sse_envelopes(create_resp):
                if envelope["event"] == "error":
                    payload = json.loads(envelope["data"])
                    raise AssertionError(
                        f"agent conversation stream emitted error event: {payload!r}"
                    )
                if envelope["event"] != "complete":
                    continue
                payload = json.loads(envelope["data"])
                conversation_id = _extract_conversation_id(payload)
                break

        assert conversation_id, (
            f"agent conversation stream ended without a complete event for agent {agent_key!r}"
        )

        archive_resp = requests.post(
            f"{agents_base_url}/{agent_key}/conversations/{conversation_id}/archive",
            headers=headers,
            timeout=timeout,
        )
        assert archive_resp.status_code == 200, (
            f"{archive_resp.status_code}: {archive_resp.text}"
        )
        archive_body = archive_resp.json()
        assert archive_body.get("id") == conversation_id, (
            f"archive id mismatch: {archive_body!r}"
        )
        assert archive_body.get("status") == "archived", (
            f"expected archived status: {archive_body!r}"
        )
        created_conversations.append((agent_key, conversation_id))
        time.sleep(0.05)
        return conversation_id

    dataset_token = uuid.uuid4().hex
    for idx, agent_key in enumerate(extra_agents):
        create_archived_conversation(
            agent_key,
            query=f"integration extra archived conversation {dataset_token} {idx}",
        )

    primary_conversation_ids = [
        create_archived_conversation(
            primary_agent_key,
            query=f"integration primary archived conversation {dataset_token} {idx}",
        )
        for idx in range(_PRIMARY_AGENT_ARCHIVE_COUNT)
    ]

    try:
        yield {
            "base_url": base_url,
            "headers": headers,
            "timeout": timeout,
            "total_groups": _EXTRA_AGENT_GROUP_COUNT + 1,
            "primary_agent_key": primary_agent_key,
            "primary_conversation_ids": primary_conversation_ids,
            "all_agent_keys": [*extra_agents, primary_agent_key],
        }
    finally:
        for agent_key, conversation_id in reversed(created_conversations):
            resp = requests.delete(
                f"{agents_base_url}/{agent_key}/conversations/{conversation_id}",
                headers=headers,
                timeout=timeout,
            )
            assert resp.status_code == 200, f"{resp.status_code}: {resp.text}"


class _BaseAgentConversationIntegration:

    @pytest.fixture(autouse=True)
    def _setup(self, pipeshub_client: PipeshubClient) -> None:
        self.base_url = pipeshub_client.base_url
        self.headers = pipeshub_client.auth_headers
        self.timeout = int(os.getenv("PIPESHUB_TEST_TIMEOUT", "60"))
        self.grouped_archives_url = (
            f"{self.base_url}/api/v1{_GROUPED_AGENT_ARCHIVE_PATH}"
        )


@pytest.mark.integration
class TestAgentArchivedConversationGroups(_BaseAgentConversationIntegration):
    @pytest.mark.parametrize(
        (
            "params",
            "expected_page",
            "expected_limit",
            "expected_group_count",
            "expected_has_next",
            "expected_has_prev",
        ),
        [
            ({}, 1, 5, 2, False, False),
            ({"agentPage": "2"}, 2, 5, 0, False, True),
            ({"agentPage": "2", "agentLimit": "2"}, 2, 2, 0, False, True),
            ({"agentPage": "0", "agentLimit": "0"}, 1, 5, 2, False, False),
            ({"agentPage": "-4", "agentLimit": "-7"}, 1, 1, 1, True, False),
            ({"agentPage": "abc", "agentLimit": "999"}, 1, 100, 2, False, False),
            ({"agentPage": "2.9", "agentLimit": "2.2"}, 2, 2, 0, False, True),
        ],
    )
    # Checks the archived chats page handles different page sizes and odd inputs sanely.
    def test_get_grouped_archived_agent_conversations_query_variations(
        self,
        archived_agent_groups_dataset: dict[str, Any],
        params: dict[str, str],
        expected_page: int,
        expected_limit: int,
        expected_group_count: int,
        expected_has_next: bool,
        expected_has_prev: bool,
    ) -> None:
        resp = requests.get(
            self.grouped_archives_url,
            headers=self.headers,
            params=params,
            timeout=self.timeout,
        )
        assert resp.status_code == 200, f"{resp.status_code}: {resp.text}"

        body = resp.json()
        assert_response_matches_spec(body, _GROUPED_AGENT_ARCHIVE_PATH, "GET", 200)

        groups = body.get("groups") or []
        pagination = body.get("agentPagination") or {}
        total_groups = archived_agent_groups_dataset["total_groups"]

        assert len(groups) == expected_group_count, (
            f"group count mismatch for params={params!r}: {body!r}"
        )
        assert pagination.get("page") == expected_page, (
            f"page mismatch for params={params!r}: {pagination!r}"
        )
        assert pagination.get("limit") == expected_limit, (
            f"limit mismatch for params={params!r}: {pagination!r}"
        )
        assert pagination.get("totalCount") == total_groups, (
            f"totalCount mismatch for params={params!r}: {pagination!r}"
        )
        assert pagination.get("totalPages") == math.ceil(total_groups / expected_limit), (
            f"totalPages mismatch for params={params!r}: {pagination!r}"
        )
        assert pagination.get("hasNextPage") is expected_has_next, (
            f"hasNextPage mismatch for params={params!r}: {pagination!r}"
        )
        assert pagination.get("hasPrevPage") is expected_has_prev, (
            f"hasPrevPage mismatch for params={params!r}: {pagination!r}"
        )

        returned_keys = [g.get("agentKey") for g in groups if isinstance(g, dict)]
        assert len(returned_keys) == len(set(returned_keys)), (
            f"duplicate agent groups returned for params={params!r}: {groups!r}"
        )
        assert set(returned_keys).issubset(
            set(archived_agent_groups_dataset["all_agent_keys"])
        ), f"unexpected agent keys returned: {returned_keys!r}"

        for group in groups:
            conversations = group.get("conversations") or []
            group_pagination = group.get("pagination") or {}
            assert conversations, f"empty conversation group returned: {group!r}"
            assert group_pagination.get("page") == 1, (
                f"group pagination page mismatch: {group!r}"
            )
            assert group_pagination.get("limit") == 5, (
                f"group pagination limit mismatch: {group!r}"
            )
            assert (group_pagination.get("totalCount") or 0) >= len(conversations), (
                f"group totalCount smaller than returned rows: {group!r}"
            )

    # Checks each agent shows only its 5 most recent archived chats as a preview.
    def test_get_grouped_archived_agent_conversations_slices_to_five_chats_per_agent(
        self,
        archived_agent_groups_dataset: dict[str, Any],
    ) -> None:
        resp = requests.get(
            self.grouped_archives_url,
            headers=self.headers,
            params={"agentLimit": "5"},
            timeout=self.timeout,
        )
        assert resp.status_code == 200, f"{resp.status_code}: {resp.text}"
        body = resp.json()
        assert_response_matches_spec(body, _GROUPED_AGENT_ARCHIVE_PATH, "GET", 200)

        primary_agent_key = archived_agent_groups_dataset["primary_agent_key"]
        primary_group = next(
            (
                group
                for group in (body.get("groups") or [])
                if isinstance(group, dict) and group.get("agentKey") == primary_agent_key
            ),
            None,
        )
        assert primary_group is not None, (
            f"primary agent group {primary_agent_key!r} missing: {body!r}"
        )

        grouped_conversations = primary_group.get("conversations") or []
        assert len(grouped_conversations) == 5, (
            f"grouped route should only return first 5 chats: {primary_group!r}"
        )

        per_agent_resp = requests.get(
            f"{self.base_url}/api/v1/agents/{primary_agent_key}/conversations/show/archives",
            headers=self.headers,
            params={"page": 1, "limit": 100},
            timeout=self.timeout,
        )
        assert per_agent_resp.status_code == 200, (
            f"{per_agent_resp.status_code}: {per_agent_resp.text}"
        )
        per_agent_body = per_agent_resp.json()
        per_agent_ids = [
            row.get("_id")
            for row in (per_agent_body.get("conversations") or [])
            if isinstance(row, dict)
        ]
        grouped_ids = [
            row.get("_id")
            for row in grouped_conversations
            if isinstance(row, dict)
        ]
        assert grouped_ids == per_agent_ids[:5], (
            "grouped archive route should align with page 1 ordering from the "
            f"per-agent archive route: grouped={grouped_ids!r} "
            f"per_agent={per_agent_ids!r}"
        )

    # Checks that someone not signed in cannot view archived chats.
    def test_get_grouped_archived_agent_conversations_missing_auth_returns_401_or_403(
        self,
    ) -> None:
        resp = requests.get(
            self.grouped_archives_url,
            headers={"Content-Type": "application/json"},
            timeout=self.timeout,
        )
        assert resp.status_code in (401, 403), f"{resp.status_code}: {resp.text}"
