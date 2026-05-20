"""Agent conversation archive integration tests."""

from __future__ import annotations

import json
import math
import os
import time
import uuid
from datetime import datetime, timezone
from typing import Any

import pytest
import requests

from openapi_search_validator import (
    assert_matches_component_schema,
    assert_response_matches_spec,
)
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
        self.stream_timeout = max(self.timeout, 120)
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


@pytest.mark.integration
class TestAgentConversationMessageRoute(_BaseAgentConversationIntegration):
    _AGENT_MESSAGE_STREAM_PATH = (
        "/agents/{agentKey}/conversations/{conversationId}/messages/stream"
    )

    def _fetch_first_live_model(self) -> dict[str, Any]:
        resp = requests.get(
            f"{self.base_url}/api/v1/configurationManager/ai-models/available/llm",
            headers=self.headers,
            timeout=self.timeout,
        )
        assert resp.status_code == 200, f"{resp.status_code}: {resp.text}"
        body = resp.json()
        models = body.get("models") or []
        assert models, f"no live llm models returned: {body!r}"
        model = models[0]
        assert isinstance(model, dict), f"unexpected model shape: {model!r}"
        assert model.get("modelKey"), f"live model missing modelKey: {model!r}"
        assert model.get("modelName"), f"live model missing modelName: {model!r}"
        return model

    def _fetch_first_live_kb(self) -> dict[str, Any]:
        resp = requests.get(
            f"{self.base_url}/api/v1/knowledgeBase",
            headers=self.headers,
            params={"page": 1, "limit": 1},
            timeout=self.timeout,
        )
        assert resp.status_code == 200, f"{resp.status_code}: {resp.text}"
        body = resp.json()
        knowledge_bases = body.get("knowledgeBases") or []
        assert knowledge_bases, f"no live knowledge bases returned: {body!r}"
        kb = knowledge_bases[0]
        assert isinstance(kb, dict), f"unexpected knowledge base shape: {kb!r}"
        assert kb.get("id"), f"live knowledge base missing id: {kb!r}"
        assert kb.get("name"), f"live knowledge base missing name: {kb!r}"
        return kb

    def _create_agent_conversation(self, agent_key: str) -> str:
        stream_headers = {**self.headers, "Accept": "text/event-stream"}
        with requests.post(
            f"{self.base_url}/api/v1/agents/{agent_key}/conversations/stream",
            headers=stream_headers,
            json={"query": f"integration agent conversation seed {uuid.uuid4().hex}"},
            stream=True,
            timeout=self.stream_timeout,
        ) as create_resp:
            assert create_resp.status_code == 200, (
                f"{create_resp.status_code}: {create_resp.text}"
            )
            for envelope in _iter_sse_envelopes(create_resp):
                if envelope["event"] == "error":
                    payload = json.loads(envelope["data"])
                    raise AssertionError(
                        f"agent conversation stream emitted error event: {payload!r}"
                    )
                if envelope["event"] != "complete":
                    continue
                payload = json.loads(envelope["data"])
                return _extract_conversation_id(payload)

        raise AssertionError(
            f"agent conversation stream ended without a complete event for agent {agent_key!r}"
        )

    def _delete_agent_conversation(self, agent_key: str, conversation_id: str) -> None:
        delete_resp = requests.delete(
            f"{self.base_url}/api/v1/agents/{agent_key}/conversations/{conversation_id}",
            headers=self.headers,
            timeout=self.timeout,
        )
        assert delete_resp.status_code == 200, (
            f"{delete_resp.status_code}: {delete_resp.text}"
        )

    def _runtime_timezone_name(self) -> str:
        tz_name = (os.getenv("TZ") or "").strip()
        if "/" in tz_name:
            return tz_name
        return "UTC"

    def _runtime_current_time(self) -> str:
        return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")

    def _build_kb_filter_payload(self, kb: dict[str, Any]) -> dict[str, Any]:
        kb_id = str(kb["id"])
        kb_name = str(kb["name"])
        return {
            "filters": {"kb": [kb_id]},
            "appliedFilters": {
                "kb": [
                    {
                        "id": kb_id,
                        "name": kb_name,
                        "nodeType": "recordGroup",
                        "connector": "KB",
                    }
                ]
            },
        }

    def _build_follow_up_payload(
        self,
        query: str | None,
        *,
        include_model: bool = False,
        include_kb_filter: bool = False,
        include_time_context: bool = False,
        include_tools: bool = False,
        chat_mode: str | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {}
        if query is not None:
            payload["query"] = query

        if include_model:
            model = self._fetch_first_live_model()
            payload["modelKey"] = model["modelKey"]
            payload["modelName"] = model["modelName"]
            if model.get("modelFriendlyName"):
                payload["modelFriendlyName"] = model["modelFriendlyName"]

        if include_kb_filter:
            payload.update(self._build_kb_filter_payload(self._fetch_first_live_kb()))

        if include_time_context:
            payload["timezone"] = self._runtime_timezone_name()
            payload["currentTime"] = self._runtime_current_time()

        if include_tools:
            payload["tools"] = []

        if chat_mode is not None:
            payload["chatMode"] = chat_mode

        return payload

    @pytest.mark.parametrize(
        ("case_name", "payload_kwargs"),
        [
            ("query_only", {}),
            ("query_with_live_model", {"include_model": True}),
            ("query_with_live_kb_filter", {"include_kb_filter": True}),
            (
                "query_with_model_and_kb_filter",
                {
                    "include_model": True,
                    "include_kb_filter": True,
                    "include_tools": True,
                },
            ),
            (
                "query_with_runtime_time_fields",
                {"include_time_context": True, "chat_mode": "verification"},
            ),
        ],
    )
    def test_add_message_stream_to_agent_conversation_success_matrix(
        self,
        case_name: str,
        payload_kwargs: dict[str, Any],
    ) -> None:
        agent_key, _ = _configured_agent_keys()
        created_conversation_id: str | None = None

        try:
            created_conversation_id = self._create_agent_conversation(agent_key)
            payload = self._build_follow_up_payload(
                f"integration agent varied request {case_name} {uuid.uuid4().hex}",
                **payload_kwargs,
            )

            stream_headers = {**self.headers, "Accept": "text/event-stream"}
            complete_payload: dict[str, Any] | None = None

            with requests.post(
                f"{self.base_url}/api/v1/agents/{agent_key}/conversations/"
                f"{created_conversation_id}/messages/stream",
                headers=stream_headers,
                json=payload,
                stream=True,
                timeout=self.stream_timeout,
            ) as resp:
                assert resp.status_code == 200, f"{resp.status_code}: {resp.text}"
                content_type = (resp.headers.get("content-type") or "").lower()
                assert "text/event-stream" in content_type, (
                    f"unexpected content-type for case={case_name!r}: {content_type!r}"
                )

                for envelope in _iter_sse_envelopes(resp):
                    assert_matches_component_schema(
                        envelope,
                        "AgentMessageStreamSSEEvent",
                    )
                    if envelope["event"] == "error":
                        payload_obj = json.loads(envelope["data"])
                        raise AssertionError(
                            f"agent message stream emitted error event for case={case_name!r}: {payload_obj!r}"
                        )
                    if envelope["event"] != "complete":
                        continue
                    payload_obj = json.loads(envelope["data"])
                    complete_payload = payload_obj
                    break

            assert complete_payload is not None, (
                f"stream ended without complete event for case={case_name!r}"
            )
            assert _extract_conversation_id(complete_payload) == created_conversation_id, (
                f"response conversation id mismatch for case={case_name!r}: {complete_payload!r}"
            )
            assert isinstance(complete_payload.get("conversation"), dict), (
                f"conversation object missing for case={case_name!r}: {complete_payload!r}"
            )
        finally:
            if created_conversation_id:
                self._delete_agent_conversation(agent_key, created_conversation_id)

    @pytest.mark.parametrize(
        ("case_name", "query"),
        [
            ("missing_query", None),
            ("empty_query", ""),
        ],
    )
    def test_add_message_stream_to_agent_conversation_invalid_query_returns_400(
        self,
        case_name: str,
        query: str | None,
    ) -> None:
        agent_key, _ = _configured_agent_keys()
        created_conversation_id: str | None = None

        try:
            created_conversation_id = self._create_agent_conversation(agent_key)
            payload = self._build_follow_up_payload(
                query,
                include_model=True,
                include_kb_filter=True,
                include_time_context=True,
                include_tools=True,
            )

            resp = requests.post(
                f"{self.base_url}/api/v1/agents/{agent_key}/conversations/"
                f"{created_conversation_id}/messages/stream",
                headers=self.headers,
                json=payload,
                timeout=self.timeout,
            )
            assert resp.status_code == 400, (
                f"case={case_name!r} expected 400, got {resp.status_code}: {resp.text}"
            )

            try:
                body = resp.json()
            except ValueError as exc:
                raise AssertionError(
                    f"case={case_name!r} returned non-JSON error body: {resp.text}"
                ) from exc

            assert any(body.get(key) for key in ("error", "message", "msg")), (
                f"case={case_name!r} returned no diagnostic error payload: {body!r}"
            )
        finally:
            if created_conversation_id:
                self._delete_agent_conversation(agent_key, created_conversation_id)
