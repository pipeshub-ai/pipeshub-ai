"""Agent conversation integration tests (archives, messages, feedback).

Run archive/unarchive route tests:

    pytest integration-tests/enterprise_search/integration_test_agent_conversation.py::TestAgentConversationArchiveRoutes -v

Run per-agent archived list (GET .../agents/{agentKey}/conversations/show/archives):

    pytest integration-tests/enterprise_search/integration_test_agent_conversation.py::TestAgentPerAgentArchivedConversationList -v
"""

from __future__ import annotations

import json
import os
import uuid
from typing import Any

import pytest
import requests

from openapi_search_validator import (
    assert_matches_component_schema,
    assert_response_matches_spec,
)
from pipeshub_client import PipeshubClient
from enterprise_search.conversation_test_utils import (
    AGENT_CONVERSATION_DETAIL_PATH,
    AGENT_CONVERSATION_TITLE_PATH,
    AGENT_FEEDBACK_PATH,
    AGENT_MESSAGE_STREAM_PATH,
    AGENT_REGENERATE_PATH,
    GROUPED_AGENT_ARCHIVE_PATH,
    PER_AGENT_ARCHIVES_SPEC_PATH,
    build_connector_filter_payload,
    build_follow_up_payload,
    build_kb_filter_payload,
    create_agent_conversation,
    create_agent_conversations,
    delete_agent_conversation,
    extract_conversation_id,
    get_agent_conversation_messages,
    iter_sse_envelopes,
    list_agent_conversation_ids,
    runtime_current_time,
    runtime_timezone_name,
    stream_create_agent_conversation_and_last_bot_message_id,
    stream_create_agent_conversation_bot_and_user_message_ids,
    stream_json_post_to_complete,
)


class _BaseAgentConversationIntegration:

    @pytest.fixture(autouse=True)
    def _setup(
        self,
        pipeshub_client: PipeshubClient,
        provisioned_agent_keys: dict[str, Any],
    ) -> None:
        self.base_url = pipeshub_client.base_url
        self.headers = pipeshub_client.auth_headers
        self.timeout = int(os.getenv("PIPESHUB_TEST_TIMEOUT", "60"))
        self.stream_timeout = max(self.timeout, 120)
        self.primary_agent_key = provisioned_agent_keys["primary_agent_key"]
        self.extra_agent_key = provisioned_agent_keys["extra_agent_key"]
        self.all_agent_keys = provisioned_agent_keys["all_agent_keys"]
        self.grouped_archives_url = (
            f"{self.base_url}/api/v1{GROUPED_AGENT_ARCHIVE_PATH}"
        )
        self.agent_regenerate_url_tpl = (
            f"{self.base_url}/api/v1/agents/{{agentKey}}/conversations/"
            f"{{conversationId}}/message/{{messageId}}/regenerate"
        )
        self.agent_feedback_url_tpl = (
            f"{self.base_url}/api/v1/agents/{{agentKey}}/conversations/"
            f"{{conversationId}}/message/{{messageId}}/feedback"
        )
        self.agent_conversations_url_tpl = (
            f"{self.base_url}/api/v1/agents/{{agentKey}}/conversations"
        )
        self.agent_conversation_detail_url_tpl = (
            f"{self.base_url}/api/v1/agents/{{agentKey}}/conversations/"
            f"{{conversationId}}"
        )
        self.agent_conversation_title_url_tpl = (
            f"{self.base_url}/api/v1/agents/{{agentKey}}/conversations/"
            f"{{conversationId}}/title"
        )
        self.agent_archive_url_tpl = (
            f"{self.base_url}/api/v1/agents/{{agentKey}}/conversations/"
            f"{{conversationId}}/archive"
        )
        self.agent_unarchive_url_tpl = (
            f"{self.base_url}/api/v1/agents/{{agentKey}}/conversations/"
            f"{{conversationId}}/unarchive"
        )
        self.agent_archives_list_url_tpl = (
            f"{self.base_url}/api/v1/agents/{{agentKey}}/conversations/show/archives"
        )

    def _create_agent_conversation(
        self,
        agent_key: str,
        *,
        query: str | None = None,
    ) -> str:
        return create_agent_conversation(
            self.base_url,
            self.headers,
            self.stream_timeout,
            agent_key,
            query=query,
        )

    def _delete_agent_conversation(self, agent_key: str, conversation_id: str) -> None:
        delete_agent_conversation(
            self.base_url,
            self.headers,
            self.timeout,
            agent_key,
            conversation_id,
        )

    def _assert_agent_archive_response(
        self,
        body: dict[str, Any],
        *,
        conversation_id: str,
    ) -> None:
        assert_matches_component_schema(body, "AgentConversationArchiveResponse")
        assert_response_matches_spec(
            body,
            "/agents/{agentKey}/conversations/{conversationId}/archive",
            "POST",
            200,
        )
        assert body.get("id") == conversation_id, (
            f"archive response id mismatch: expected {conversation_id!r}, "
            f"got {body.get('id')!r}"
        )
        assert body.get("status") == "archived", (
            f"status should be 'archived', got {body.get('status')!r}"
        )
        assert body.get("archivedBy"), (
            f"archivedBy should be populated: {body!r}"
        )
        assert body.get("archivedAt"), (
            f"archivedAt should be populated: {body!r}"
        )

    def _assert_agent_unarchive_response(
        self,
        body: dict[str, Any],
        *,
        conversation_id: str,
    ) -> None:
        assert_matches_component_schema(body, "AgentConversationUnarchiveResponse")
        assert_response_matches_spec(
            body,
            "/agents/{agentKey}/conversations/{conversationId}/unarchive",
            "POST",
            200,
        )
        assert body.get("id") == conversation_id, (
            f"unarchive response id mismatch: expected {conversation_id!r}, "
            f"got {body.get('id')!r}"
        )
        assert body.get("status") == "unarchived", (
            f"status should be 'unarchived', got {body.get('status')!r}"
        )
        assert body.get("unarchivedBy"), (
            f"unarchivedBy should be populated: {body!r}"
        )
        assert body.get("unarchivedAt"), (
            f"unarchivedAt should be populated: {body!r}"
        )

    def _assert_agent_conversation_title_patch_response(
        self,
        body: dict[str, Any],
    ) -> None:
        assert_response_matches_spec(
            body,
            AGENT_CONVERSATION_TITLE_PATH,
            "PATCH",
            200,
        )

    def _active_agent_conversation_ids(
        self,
        agent_key: str,
        *,
        limit: int = 100,
    ) -> set[str]:
        return list_agent_conversation_ids(
            self.agent_conversations_url_tpl.format(agentKey=agent_key),
            self.headers,
            self.timeout,
            limit=limit,
        )

    def _archived_agent_conversation_ids(
        self,
        agent_key: str,
        *,
        limit: int = 100,
    ) -> set[str]:
        return list_agent_conversation_ids(
            self.agent_archives_list_url_tpl.format(agentKey=agent_key),
            self.headers,
            self.timeout,
            limit=limit,
        )


@pytest.mark.integration
class TestAgentArchivedConversationGroups(_BaseAgentConversationIntegration):
    @staticmethod
    def _group_by_agent_key(
        body: dict[str, Any],
        agent_key: str,
    ) -> dict[str, Any] | None:
        return next(
            (
                group
                for group in (body.get("groups") or [])
                if isinstance(group, dict) and group.get("agentKey") == agent_key
            ),
            None,
        )

    @pytest.mark.parametrize(
        (
            "params",
            "expected_page",
            "expected_limit",
            "expected_has_prev",
        ),
        [
            ({}, 1, 5, False),
            ({"agentPage": "2"}, 2, 5, True),
            ({"agentPage": "2", "agentLimit": "2"}, 2, 2, True),
            ({"agentPage": "0", "agentLimit": "0"}, 1, 5, False),
            ({"agentPage": "-4", "agentLimit": "-7"}, 1, 1, False),
            ({"agentPage": "abc", "agentLimit": "999"}, 1, 100, False),
            ({"agentPage": "2.9", "agentLimit": "2.2"}, 2, 2, True),
        ],
    )
    # Checks the archived chats page handles different page sizes and odd inputs sanely.
    def test_get_grouped_archived_agent_conversations_query_variations(
        self,
        archived_agent_groups_dataset: dict[str, Any],
        params: dict[str, str],
        expected_page: int,
        expected_limit: int,
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
        assert_response_matches_spec(body, GROUPED_AGENT_ARCHIVE_PATH, "GET", 200)

        groups = body.get("groups") or []
        pagination = body.get("agentPagination") or {}
        assert pagination.get("page") == expected_page, (
            f"page mismatch for params={params!r}: {pagination!r}"
        )
        assert pagination.get("limit") == expected_limit, (
            f"limit mismatch for params={params!r}: {pagination!r}"
        )
        assert pagination.get("hasPrevPage") is expected_has_prev, (
            f"hasPrevPage mismatch for params={params!r}: {pagination!r}"
        )

        returned_keys = [g.get("agentKey") for g in groups if isinstance(g, dict)]
        assert len(returned_keys) == len(set(returned_keys)), (
            f"duplicate agent groups returned for params={params!r}: {groups!r}"
        )

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

        expected_primary_ids = set(
            archived_agent_groups_dataset["primary_conversation_ids"]
        )
        expected_extra_ids = set(
            archived_agent_groups_dataset["extra_conversation_ids"]
        )
        primary_group = self._group_by_agent_key(
            body,
            archived_agent_groups_dataset["primary_agent_key"],
        )
        extra_group = self._group_by_agent_key(
            body,
            archived_agent_groups_dataset["extra_agent_key"],
        )
        if primary_group is not None:
            primary_ids = {
                row.get("_id")
                for row in (primary_group.get("conversations") or [])
                if isinstance(row, dict) and row.get("_id")
            }
            assert primary_ids <= expected_primary_ids, (
                f"unexpected primary grouped ids for params={params!r}: {primary_ids!r}"
            )
        if extra_group is not None:
            extra_ids = {
                row.get("_id")
                for row in (extra_group.get("conversations") or [])
                if isinstance(row, dict) and row.get("_id")
            }
            assert extra_ids <= expected_extra_ids, (
                f"unexpected extra grouped ids for params={params!r}: {extra_ids!r}"
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
        assert_response_matches_spec(body, GROUPED_AGENT_ARCHIVE_PATH, "GET", 200)

        primary_agent_key = archived_agent_groups_dataset["primary_agent_key"]
        primary_group = self._group_by_agent_key(
            body,
            primary_agent_key,
        )
        assert primary_group is not None, (
            f"primary agent group {primary_agent_key!r} missing: {body!r}"
        )

        grouped_conversations = primary_group.get("conversations") or []

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
        expected_primary_ids = archived_agent_groups_dataset["primary_conversation_ids"]
        assert set(grouped_ids) <= set(expected_primary_ids), (
            f"grouped route returned unexpected primary ids: {grouped_ids!r}"
        )
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
class TestAgentPerAgentArchivedConversationList(_BaseAgentConversationIntegration):
    """GET /api/v1/agents/{agentKey}/conversations/show/archives — OpenAPI + behavioral checks."""

    @staticmethod
    def _assert_per_agent_archives_envelope(body: Any) -> None:
        assert isinstance(body, dict), f"expected JSON object, got {type(body)!r}"
        for key in ("conversations", "pagination", "filters", "summary", "meta"):
            assert key in body, f"missing top-level key {key!r}: {body!r}"
        pagination = body["pagination"]
        assert isinstance(pagination, dict), f"pagination should be object: {pagination!r}"
        summary = body["summary"]
        assert isinstance(summary, dict), f"summary should be object: {summary!r}"
        assert "totalArchived" in summary, f"summary missing totalArchived: {summary!r}"
        assert isinstance(summary["totalArchived"], int), (
            f"summary.totalArchived should be int: {summary!r}"
        )

    def _assert_per_agent_archives_200(self, body: Any) -> None:
        assert_response_matches_spec(body, PER_AGENT_ARCHIVES_SPEC_PATH, "GET", 200)
        self._assert_per_agent_archives_envelope(body)

    def test_get_per_agent_archived_conversations_full_list_and_ids(
        self,
        archived_agent_groups_dataset: dict[str, Any],
    ) -> None:
        primary_key = archived_agent_groups_dataset["primary_agent_key"]
        expected_ids = set(archived_agent_groups_dataset["primary_conversation_ids"])
        url = self.agent_archives_list_url_tpl.format(agentKey=primary_key)
        resp = requests.get(
            url,
            headers=self.headers,
            params={"page": 1, "limit": 100},
            timeout=self.timeout,
        )
        assert resp.status_code == 200, f"{resp.status_code}: {resp.text}"
        body = resp.json()
        self._assert_per_agent_archives_200(body)

        rows = body.get("conversations") or []
        got_ids = {row["_id"] for row in rows if isinstance(row, dict) and row.get("_id")}
        assert got_ids == expected_ids, (
            f"conversation id set mismatch: got={got_ids!r} expected={expected_ids!r}"
        )
        for row in rows:
            assert isinstance(row, dict), f"non-object row: {row!r}"
            assert row.get("archivedAt"), f"missing archivedAt: {row!r}"
            assert row.get("archivedBy"), f"missing archivedBy: {row!r}"
            assert "messages" not in row, f"messages should be stripped from list: {row!r}"

    def test_get_per_agent_archived_conversations_pagination(
        self,
        archived_agent_groups_dataset: dict[str, Any],
    ) -> None:
        primary_key = archived_agent_groups_dataset["primary_agent_key"]
        expected_ids = set(archived_agent_groups_dataset["primary_conversation_ids"])
        url = self.agent_archives_list_url_tpl.format(agentKey=primary_key)
        collected: set[str] = set()
        page = 1
        per_page = 2
        last_pagination: dict[str, Any] = {}

        while True:
            resp = requests.get(
                url,
                headers=self.headers,
                params={"page": page, "limit": per_page},
                timeout=self.timeout,
            )
            assert resp.status_code == 200, f"{resp.status_code}: {resp.text}"
            body = resp.json()
            self._assert_per_agent_archives_200(body)
            last_pagination = body["pagination"] or {}

            for row in body.get("conversations") or []:
                if isinstance(row, dict) and isinstance(row.get("_id"), str):
                    collected.add(row["_id"])

            if not last_pagination.get("hasNextPage"):
                break
            page += 1
            assert page <= 20, "pagination loop exceeded expected max pages"

        assert collected == expected_ids, (
            f"expected archived ids across pages to match dataset: "
            f"got={collected!r} expected={expected_ids!r}"
        )
        assert last_pagination.get("hasNextPage") is False, (
            f"last page should not have next: {last_pagination!r}"
        )

    @pytest.mark.parametrize(
        (
            "query_params",
            "expected_page",
            "expected_limit",
            "expected_ids_slice",
        ),
        [
            ({}, 1, 20, slice(0, 20)),
            ({"page": "1", "limit": "3"}, 1, 3, slice(0, 3)),
            ({"page": "2", "limit": "3"}, 2, 3, slice(3, 6)),
            ({"page": "3", "limit": "3"}, 3, 3, slice(6, 9)),
            ({"page": "2.9", "limit": "2.2"}, 2, 2, slice(2, 4)),
            ({"page": "1", "limit": "100"}, 1, 100, slice(0, 100)),
        ],
    )
    def test_get_per_agent_archived_conversations_pagination_query_variations(
        self,
        archived_agent_groups_dataset: dict[str, Any],
        query_params: dict[str, str],
        expected_page: int,
        expected_limit: int,
        expected_ids_slice: slice,
    ) -> None:
        primary_key = archived_agent_groups_dataset["primary_agent_key"]
        expected_ids = list(
            reversed(archived_agent_groups_dataset["primary_conversation_ids"])
        )
        url = self.agent_archives_list_url_tpl.format(agentKey=primary_key)
        resp = requests.get(
            url,
            headers=self.headers,
            params=query_params,
            timeout=self.timeout,
        )
        assert resp.status_code == 200, f"{resp.status_code}: {resp.text}"
        body = resp.json()
        self._assert_per_agent_archives_200(body)
        pagination = body["pagination"] or {}
        assert pagination.get("page") == expected_page, (
            f"page mismatch for params={query_params!r}: {pagination!r}"
        )
        assert pagination.get("limit") == expected_limit, (
            f"limit mismatch for params={query_params!r}: {pagination!r}"
        )
        rows = body.get("conversations") or []
        row_ids = [
            row.get("_id")
            for row in rows
            if isinstance(row, dict) and row.get("_id")
        ]
        assert row_ids == expected_ids[expected_ids_slice], (
            f"row ids mismatch for params={query_params!r}: "
            f"got={row_ids!r} expected={expected_ids[expected_ids_slice]!r}"
        )

    @pytest.mark.parametrize(
        "query_params",
        [
            {"page": "abc", "limit": "10"},
            {"page": "0", "limit": "5"},
            {"page": "-4", "limit": "-7"},
        ],
    )
    def test_get_per_agent_archived_conversations_invalid_pagination_returns_400(
        self,
        archived_agent_groups_dataset: dict[str, Any],
        query_params: dict[str, str],
    ) -> None:
        primary_key = archived_agent_groups_dataset["primary_agent_key"]
        url = self.agent_archives_list_url_tpl.format(agentKey=primary_key)
        resp = requests.get(
            url,
            headers=self.headers,
            params=query_params,
            timeout=self.timeout,
        )
        assert resp.status_code == 400, (
            f"expected 400 for params={query_params!r}, "
            f"got {resp.status_code}: {resp.text}"
        )

    @pytest.mark.parametrize(
        ("sort_by", "sort_order"),
        [
            ("lastActivityAt", "desc"),
            ("lastActivityAt", "asc"),
            ("createdAt", "desc"),
            ("createdAt", "asc"),
            ("title", "desc"),
            ("title", "asc"),
        ],
    )
    def test_get_per_agent_archived_conversations_sort_query_variations(
        self,
        archived_agent_groups_dataset: dict[str, Any],
        sort_by: str,
        sort_order: str,
    ) -> None:
        primary_key = archived_agent_groups_dataset["primary_agent_key"]
        url = self.agent_archives_list_url_tpl.format(agentKey=primary_key)
        resp = requests.get(
            url,
            headers=self.headers,
            params={
                "page": 1,
                "limit": 100,
                "sortBy": sort_by,
                "sortOrder": sort_order,
            },
            timeout=self.timeout,
        )
        assert resp.status_code == 200, f"{resp.status_code}: {resp.text}"
        body = resp.json()
        self._assert_per_agent_archives_200(body)
        expected_ids = set(archived_agent_groups_dataset["primary_conversation_ids"])
        row_ids = {
            row.get("_id")
            for row in (body.get("conversations") or [])
            if isinstance(row, dict) and row.get("_id")
        }
        assert row_ids == expected_ids, (
            f"sorted archived ids mismatch: got={row_ids!r} expected={expected_ids!r}"
        )

    def test_get_per_agent_archived_conversations_benign_extra_query_params(
        self,
        archived_agent_groups_dataset: dict[str, Any],
    ) -> None:
        primary_key = archived_agent_groups_dataset["primary_agent_key"]
        url = self.agent_archives_list_url_tpl.format(agentKey=primary_key)
        resp = requests.get(
            url,
            headers=self.headers,
            params={
                "page": 1,
                "limit": 100,
                "debug": "1",
                "source": "integration",
            },
            timeout=self.timeout,
        )
        assert resp.status_code == 200, f"{resp.status_code}: {resp.text}"
        body = resp.json()
        self._assert_per_agent_archives_200(body)
        expected_ids = set(archived_agent_groups_dataset["primary_conversation_ids"])
        row_ids = {
            row.get("_id")
            for row in (body.get("conversations") or [])
            if isinstance(row, dict) and row.get("_id")
        }
        assert row_ids == expected_ids, (
            f"extra query params changed returned ids: got={row_ids!r} expected={expected_ids!r}"
        )

    def test_get_per_agent_archived_conversations_search_matches_fixture_token(
        self,
        archived_agent_groups_dataset: dict[str, Any],
    ) -> None:
        primary_key = archived_agent_groups_dataset["primary_agent_key"]
        token = archived_agent_groups_dataset["dataset_token"]
        url = self.agent_archives_list_url_tpl.format(agentKey=primary_key)
        resp = requests.get(
            url,
            headers=self.headers,
            params={"page": 1, "limit": 100, "search": token},
            timeout=self.timeout,
        )
        assert resp.status_code == 200, f"{resp.status_code}: {resp.text}"
        body = resp.json()
        self._assert_per_agent_archives_200(body)
        expected_ids = set(archived_agent_groups_dataset["primary_conversation_ids"])
        row_ids = {
            row.get("_id")
            for row in (body.get("conversations") or [])
            if isinstance(row, dict) and row.get("_id")
        }
        assert row_ids == expected_ids, (
            f"search should match all primary archived ids: "
            f"got={row_ids!r} expected={expected_ids!r}"
        )

    def test_get_per_agent_archived_conversations_date_range_includes_archives(
        self,
        archived_agent_groups_dataset: dict[str, Any],
    ) -> None:
        primary_key = archived_agent_groups_dataset["primary_agent_key"]
        url = self.agent_archives_list_url_tpl.format(agentKey=primary_key)
        resp = requests.get(
            url,
            headers=self.headers,
            params={
                "page": 1,
                "limit": 100,
                "startDate": "2000-01-01T00:00:00.000Z",
                "endDate": "2035-12-31T23:59:59.999Z",
            },
            timeout=self.timeout,
        )
        assert resp.status_code == 200, f"{resp.status_code}: {resp.text}"
        body = resp.json()
        self._assert_per_agent_archives_200(body)
        expected_ids = set(archived_agent_groups_dataset["primary_conversation_ids"])
        row_ids = {
            row.get("_id")
            for row in (body.get("conversations") or [])
            if isinstance(row, dict) and row.get("_id")
        }
        assert row_ids == expected_ids, (
            f"date range should include all primary archived ids: "
            f"got={row_ids!r} expected={expected_ids!r}"
        )

    @pytest.mark.parametrize(
        "path_kind",
        ["primary", "extra", "unknown"],
    )
    def test_get_per_agent_archived_conversations_path_param_variations(
        self,
        archived_agent_groups_dataset: dict[str, Any],
        path_kind: str,
    ) -> None:
        primary_key = archived_agent_groups_dataset["primary_agent_key"]
        if path_kind == "primary":
            agent_key = primary_key
            expected_ids = set(archived_agent_groups_dataset["primary_conversation_ids"])
        elif path_kind == "extra":
            agent_key = archived_agent_groups_dataset["extra_agent_key"]
            expected_ids = set(archived_agent_groups_dataset["extra_conversation_ids"])
        else:
            agent_key = f"missing-agent-{uuid.uuid4().hex}"
            expected_ids = set()

        url = self.agent_archives_list_url_tpl.format(agentKey=agent_key)
        resp = requests.get(
            url,
            headers=self.headers,
            params={"page": 1, "limit": 50},
            timeout=self.timeout,
        )
        assert resp.status_code == 200, f"{resp.status_code}: {resp.text}"
        body = resp.json()
        self._assert_per_agent_archives_200(body)
        row_ids = {
            row.get("_id")
            for row in (body.get("conversations") or [])
            if isinstance(row, dict) and row.get("_id")
        }
        assert row_ids == expected_ids, (
            f"path kind {path_kind!r} returned unexpected ids: "
            f"got={row_ids!r} expected={expected_ids!r}"
        )

    def test_get_per_agent_archived_conversations_missing_auth_returns_401_or_403(
        self,
        archived_agent_groups_dataset: dict[str, Any],
    ) -> None:
        primary_key = archived_agent_groups_dataset["primary_agent_key"]
        url = self.agent_archives_list_url_tpl.format(agentKey=primary_key)
        resp = requests.get(
            url,
            headers={"Content-Type": "application/json"},
            timeout=self.timeout,
        )
        assert resp.status_code in (401, 403), f"{resp.status_code}: {resp.text}"

    def test_get_per_agent_archived_conversations_invalid_start_date_returns_400(
        self,
        archived_agent_groups_dataset: dict[str, Any],
    ) -> None:
        primary_key = archived_agent_groups_dataset["primary_agent_key"]
        url = self.agent_archives_list_url_tpl.format(agentKey=primary_key)
        resp = requests.get(
            url,
            headers=self.headers,
            params={"page": 1, "limit": 20, "startDate": "not-a-date"},
            timeout=self.timeout,
        )
        assert resp.status_code == 400, (
            f"invalid startDate should be 400, got {resp.status_code}: {resp.text}"
        )

    def test_get_per_agent_archived_conversations_invalid_end_date_returns_400(
        self,
        archived_agent_groups_dataset: dict[str, Any],
    ) -> None:
        primary_key = archived_agent_groups_dataset["primary_agent_key"]
        url = self.agent_archives_list_url_tpl.format(agentKey=primary_key)
        resp = requests.get(
            url,
            headers=self.headers,
            params={"page": 1, "limit": 20, "endDate": "invalid"},
            timeout=self.timeout,
        )
        assert resp.status_code == 400, (
            f"invalid endDate should be 400, got {resp.status_code}: {resp.text}"
        )

    def test_get_per_agent_archived_conversations_unknown_agent_key_returns_empty(
        self,
    ) -> None:
        fake_key = f"missing-agent-{uuid.uuid4().hex}"
        url = self.agent_archives_list_url_tpl.format(agentKey=fake_key)
        resp = requests.get(
            url,
            headers=self.headers,
            params={"page": 1, "limit": 20},
            timeout=self.timeout,
        )
        assert resp.status_code == 200, f"{resp.status_code}: {resp.text}"
        body = resp.json()
        self._assert_per_agent_archives_200(body)
        assert body.get("conversations") == [], f"expected empty list: {body!r}"

    def test_get_per_agent_archived_conversations_extra_agent_single_row(
        self,
        archived_agent_groups_dataset: dict[str, Any],
    ) -> None:
        extra_key = archived_agent_groups_dataset["extra_agent_key"]
        expected_ids = set(archived_agent_groups_dataset["extra_conversation_ids"])
        url = self.agent_archives_list_url_tpl.format(agentKey=extra_key)
        resp = requests.get(
            url,
            headers=self.headers,
            params={"page": 1, "limit": 20},
            timeout=self.timeout,
        )
        assert resp.status_code == 200, f"{resp.status_code}: {resp.text}"
        body = resp.json()
        self._assert_per_agent_archives_200(body)
        rows = body.get("conversations") or []
        row_ids = {
            row.get("_id")
            for row in rows
            if isinstance(row, dict) and row.get("_id")
        }
        assert row_ids == expected_ids, (
            f"extra agent archived ids mismatch: got={row_ids!r} expected={expected_ids!r}"
        )
        for row in rows:
            assert isinstance(row, dict), row
            assert row.get("_id"), f"missing _id: {row!r}"
            assert row.get("archivedAt") and row.get("archivedBy"), row
            assert "messages" not in row

    def test_get_per_agent_archived_conversations_ordering_matches_grouped_route(
        self,
        archived_agent_groups_dataset: dict[str, Any],
    ) -> None:
        """First five archived ids on grouped route match page-1 slice from per-agent list."""
        primary_key = archived_agent_groups_dataset["primary_agent_key"]
        grouped_resp = requests.get(
            self.grouped_archives_url,
            headers=self.headers,
            params={"agentLimit": "5"},
            timeout=self.timeout,
        )
        assert grouped_resp.status_code == 200, (
            f"{grouped_resp.status_code}: {grouped_resp.text}"
        )
        grouped_body = grouped_resp.json()
        assert_response_matches_spec(
            grouped_body, GROUPED_AGENT_ARCHIVE_PATH, "GET", 200
        )
        primary_group = next(
            (
                g
                for g in (grouped_body.get("groups") or [])
                if isinstance(g, dict) and g.get("agentKey") == primary_key
            ),
            None,
        )
        assert primary_group is not None, (
            f"missing primary agent group in grouped response: {grouped_body!r}"
        )
        grouped_ids = [
            row["_id"]
            for row in (primary_group.get("conversations") or [])
            if isinstance(row, dict) and row.get("_id")
        ]

        per_agent_url = self.agent_archives_list_url_tpl.format(agentKey=primary_key)
        per_agent_resp = requests.get(
            per_agent_url,
            headers=self.headers,
            params={"page": 1, "limit": 100},
            timeout=self.timeout,
        )
        assert per_agent_resp.status_code == 200, (
            f"{per_agent_resp.status_code}: {per_agent_resp.text}"
        )
        per_agent_body = per_agent_resp.json()
        self._assert_per_agent_archives_200(per_agent_body)
        per_agent_ids = [
            row["_id"]
            for row in (per_agent_body.get("conversations") or [])
            if isinstance(row, dict) and row.get("_id")
        ]
        assert grouped_ids == per_agent_ids[:5], (
            "grouped archive route should align with page 1 ordering from the "
            f"per-agent archive route: grouped={grouped_ids!r} "
            f"per_agent={per_agent_ids!r}"
        )


@pytest.mark.integration
class TestAgentConversationArchiveRoutes(_BaseAgentConversationIntegration):
    def test_post_archive_unarchive_agent_conversation_lifecycle(self) -> None:
        agent_key = self.primary_agent_key
        conversation_id: str | None = None

        try:
            conversation_id = self._create_agent_conversation(
                agent_key,
                query=(
                    "integration: archive unarchive agent conversation lifecycle "
                    f"{uuid.uuid4().hex}"
                ),
            )
            archive_url = self.agent_archive_url_tpl.format(
                agentKey=agent_key,
                conversationId=conversation_id,
            )
            unarchive_url = self.agent_unarchive_url_tpl.format(
                agentKey=agent_key,
                conversationId=conversation_id,
            )

            assert conversation_id in self._active_agent_conversation_ids(agent_key), (
                f"conversation {conversation_id!r} should appear in active list before archive"
            )

            archive_resp = requests.post(
                archive_url,
                headers=self.headers,
                timeout=self.timeout,
            )
            assert archive_resp.status_code == 200, (
                f"{archive_resp.status_code}: {archive_resp.text}"
            )
            self._assert_agent_archive_response(
                archive_resp.json(),
                conversation_id=conversation_id,
            )

            assert conversation_id not in self._active_agent_conversation_ids(agent_key), (
                f"archived conversation {conversation_id!r} should not appear in active list"
            )
            assert conversation_id in self._archived_agent_conversation_ids(agent_key), (
                f"archived conversation {conversation_id!r} should appear in archives list"
            )

            second_archive = requests.post(
                archive_url,
                headers=self.headers,
                timeout=self.timeout,
            )
            assert second_archive.status_code == 400, (
                f"second archive should be 400, got "
                f"{second_archive.status_code}: {second_archive.text}"
            )

            unarchive_resp = requests.post(
                unarchive_url,
                headers=self.headers,
                timeout=self.timeout,
            )
            assert unarchive_resp.status_code == 200, (
                f"{unarchive_resp.status_code}: {unarchive_resp.text}"
            )
            self._assert_agent_unarchive_response(
                unarchive_resp.json(),
                conversation_id=conversation_id,
            )

            assert conversation_id in self._active_agent_conversation_ids(agent_key), (
                f"unarchived conversation {conversation_id!r} should reappear in active list"
            )
            assert conversation_id not in self._archived_agent_conversation_ids(
                agent_key
            ), (
                f"unarchived conversation {conversation_id!r} should not appear in archives"
            )

            second_unarchive = requests.post(
                unarchive_url,
                headers=self.headers,
                timeout=self.timeout,
            )
            assert second_unarchive.status_code == 400, (
                f"second unarchive should be 400, got "
                f"{second_unarchive.status_code}: {second_unarchive.text}"
            )
        finally:
            if conversation_id:
                self._delete_agent_conversation(agent_key, conversation_id)

    def test_post_archive_agent_conversation_ignores_extra_query_params(self) -> None:
        agent_key = self.primary_agent_key
        conversation_id: str | None = None

        try:
            conversation_id = self._create_agent_conversation(
                agent_key,
                query=(
                    "integration: archive agent conversation extra query params "
                    f"{uuid.uuid4().hex}"
                ),
            )
            resp = requests.post(
                self.agent_archive_url_tpl.format(
                    agentKey=agent_key,
                    conversationId=conversation_id,
                ),
                headers=self.headers,
                params={"debug": "1", "source": "integration"},
                json={},
                timeout=self.timeout,
            )
            assert resp.status_code == 200, f"{resp.status_code}: {resp.text}"
            self._assert_agent_archive_response(
                resp.json(),
                conversation_id=conversation_id,
            )
        finally:
            if conversation_id:
                self._delete_agent_conversation(agent_key, conversation_id)

    def test_post_unarchive_agent_conversation_ignores_extra_query_params(self) -> None:
        agent_key = self.primary_agent_key
        conversation_id: str | None = None

        try:
            conversation_id = self._create_agent_conversation(
                agent_key,
                query=(
                    "integration: unarchive agent conversation extra query params "
                    f"{uuid.uuid4().hex}"
                ),
            )
            archive_url = self.agent_archive_url_tpl.format(
                agentKey=agent_key,
                conversationId=conversation_id,
            )
            archive_resp = requests.post(
                archive_url,
                headers=self.headers,
                timeout=self.timeout,
            )
            assert archive_resp.status_code == 200, (
                f"{archive_resp.status_code}: {archive_resp.text}"
            )

            resp = requests.post(
                self.agent_unarchive_url_tpl.format(
                    agentKey=agent_key,
                    conversationId=conversation_id,
                ),
                headers=self.headers,
                params={"debug": "1", "source": "integration"},
                json={},
                timeout=self.timeout,
            )
            assert resp.status_code == 200, f"{resp.status_code}: {resp.text}"
            self._assert_agent_unarchive_response(
                resp.json(),
                conversation_id=conversation_id,
            )
        finally:
            if conversation_id:
                self._delete_agent_conversation(agent_key, conversation_id)

    def test_post_archive_agent_conversation_missing_auth_returns_401_or_403(
        self,
    ) -> None:
        resp = requests.post(
            self.agent_archive_url_tpl.format(
                agentKey=self.primary_agent_key,
                conversationId="0" * 24,
            ),
            headers={"Content-Type": "application/json"},
            timeout=self.timeout,
        )
        assert resp.status_code in (401, 403), f"{resp.status_code}: {resp.text}"

    def test_post_unarchive_agent_conversation_missing_auth_returns_401_or_403(
        self,
    ) -> None:
        resp = requests.post(
            self.agent_unarchive_url_tpl.format(
                agentKey=self.primary_agent_key,
                conversationId="0" * 24,
            ),
            headers={"Content-Type": "application/json"},
            timeout=self.timeout,
        )
        assert resp.status_code in (401, 403), f"{resp.status_code}: {resp.text}"

    @pytest.mark.parametrize(
        ("case_name", "conversation_id", "expected_status"),
        [
            ("invalid_agent_key", "0" * 24, (400, 404)),
            ("invalid_conversation_id", "not-an-objectid", (400,)),
            ("nonexistent_conversation_id", "0" * 24, (404,)),
            ("unknown_agent_key", "0" * 24, (404,)),
        ],
    )
    def test_post_archive_agent_conversation_invalid_or_missing_path_params(
        self,
        case_name: str,
        conversation_id: str,
        expected_status: tuple[int, ...],
    ) -> None:
        if case_name == "invalid_agent_key":
            agent_key = ""
        elif case_name == "unknown_agent_key":
            agent_key = f"missing-agent-{uuid.uuid4().hex}"
        else:
            agent_key = self.primary_agent_key
        resp = requests.post(
            self.agent_archive_url_tpl.format(
                agentKey=agent_key,
                conversationId=conversation_id,
            ),
            headers=self.headers,
            timeout=self.timeout,
        )
        assert resp.status_code in expected_status, (
            f"case={case_name!r} expected {expected_status}, got "
            f"{resp.status_code}: {resp.text}"
        )

    @pytest.mark.parametrize(
        ("case_name", "conversation_id", "expected_status"),
        [
            ("invalid_agent_key", "0" * 24, (400, 404)),
            ("invalid_conversation_id", "not-an-objectid", (400,)),
            ("nonexistent_conversation_id", "0" * 24, (404,)),
            ("unknown_agent_key", "0" * 24, (404,)),
        ],
    )
    def test_post_unarchive_agent_conversation_invalid_or_missing_path_params(
        self,
        case_name: str,
        conversation_id: str,
        expected_status: tuple[int, ...],
    ) -> None:
        if case_name == "invalid_agent_key":
            agent_key = ""
        elif case_name == "unknown_agent_key":
            agent_key = f"missing-agent-{uuid.uuid4().hex}"
        else:
            agent_key = self.primary_agent_key
        resp = requests.post(
            self.agent_unarchive_url_tpl.format(
                agentKey=agent_key,
                conversationId=conversation_id,
            ),
            headers=self.headers,
            timeout=self.timeout,
        )
        assert resp.status_code in expected_status, (
            f"case={case_name!r} expected {expected_status}, got "
            f"{resp.status_code}: {resp.text}"
        )

    def test_post_archive_agent_conversation_wrong_agent_key_returns_404(self) -> None:
        agent_key = self.primary_agent_key
        conversation_id: str | None = None

        try:
            conversation_id = self._create_agent_conversation(
                agent_key,
                query=(
                    "integration: archive agent conversation wrong agent key "
                    f"{uuid.uuid4().hex}"
                ),
            )
            resp = requests.post(
                self.agent_archive_url_tpl.format(
                    agentKey=f"missing-agent-{uuid.uuid4().hex}",
                    conversationId=conversation_id,
                ),
                headers=self.headers,
                timeout=self.timeout,
            )
            assert resp.status_code == 404, f"{resp.status_code}: {resp.text}"
        finally:
            if conversation_id:
                self._delete_agent_conversation(agent_key, conversation_id)

    def test_post_unarchive_agent_conversation_wrong_agent_key_returns_404(
        self,
    ) -> None:
        agent_key = self.primary_agent_key
        conversation_id: str | None = None

        try:
            conversation_id = self._create_agent_conversation(
                agent_key,
                query=(
                    "integration: unarchive agent conversation wrong agent key "
                    f"{uuid.uuid4().hex}"
                ),
            )
            archive_resp = requests.post(
                self.agent_archive_url_tpl.format(
                    agentKey=agent_key,
                    conversationId=conversation_id,
                ),
                headers=self.headers,
                timeout=self.timeout,
            )
            assert archive_resp.status_code == 200, (
                f"{archive_resp.status_code}: {archive_resp.text}"
            )

            resp = requests.post(
                self.agent_unarchive_url_tpl.format(
                    agentKey=f"missing-agent-{uuid.uuid4().hex}",
                    conversationId=conversation_id,
                ),
                headers=self.headers,
                timeout=self.timeout,
            )
            assert resp.status_code == 404, f"{resp.status_code}: {resp.text}"

            cleanup = requests.post(
                self.agent_unarchive_url_tpl.format(
                    agentKey=agent_key,
                    conversationId=conversation_id,
                ),
                headers=self.headers,
                timeout=self.timeout,
            )
            assert cleanup.status_code == 200, (
                f"cleanup unarchive failed: {cleanup.status_code}: {cleanup.text}"
            )
        finally:
            if conversation_id:
                self._delete_agent_conversation(agent_key, conversation_id)

    def test_post_archive_agent_conversation_already_archived_returns_400(
        self,
    ) -> None:
        agent_key = self.primary_agent_key
        conversation_id: str | None = None

        try:
            conversation_id = self._create_agent_conversation(
                agent_key,
                query=(
                    "integration: archive agent conversation already archived "
                    f"{uuid.uuid4().hex}"
                ),
            )
            archive_url = self.agent_archive_url_tpl.format(
                agentKey=agent_key,
                conversationId=conversation_id,
            )
            first = requests.post(
                archive_url,
                headers=self.headers,
                timeout=self.timeout,
            )
            assert first.status_code == 200, f"{first.status_code}: {first.text}"

            second = requests.post(
                archive_url,
                headers=self.headers,
                timeout=self.timeout,
            )
            assert second.status_code == 400, (
                f"second archive should be 400, got "
                f"{second.status_code}: {second.text}"
            )
        finally:
            if conversation_id:
                self._delete_agent_conversation(agent_key, conversation_id)

    def test_post_unarchive_agent_conversation_when_not_archived_returns_400(
        self,
    ) -> None:
        agent_key = self.primary_agent_key
        conversation_id: str | None = None

        try:
            conversation_id = self._create_agent_conversation(
                agent_key,
                query=(
                    "integration: unarchive agent conversation not archived "
                    f"{uuid.uuid4().hex}"
                ),
            )
            resp = requests.post(
                self.agent_unarchive_url_tpl.format(
                    agentKey=agent_key,
                    conversationId=conversation_id,
                ),
                headers=self.headers,
                timeout=self.timeout,
            )
            assert resp.status_code == 400, f"{resp.status_code}: {resp.text}"
        finally:
            if conversation_id:
                self._delete_agent_conversation(agent_key, conversation_id)


@pytest.mark.integration
class TestAgentConversationCreateStreamRoute(_BaseAgentConversationIntegration):
    def test_stream_create_agent_conversation_matches_spec(self) -> None:
        agent_key = self.primary_agent_key
        conversation_id: str | None = None

        try:
            content_type, complete_payload = stream_json_post_to_complete(
                f"{self.base_url}/api/v1/agents/{agent_key}/conversations/stream",
                self.headers,
                {"query": f"integration agent stream create {uuid.uuid4().hex}"},
                timeout=self.stream_timeout,
                context="agent conversation create stream",
                envelope_schema="AgentStreamSSEEvent",
            )
            assert "text/event-stream" in content_type, (
                f"unexpected content-type: {content_type!r}"
            )
            conversation_id = extract_conversation_id(complete_payload)
            assert conversation_id
        finally:
            if conversation_id:
                self._delete_agent_conversation(agent_key, conversation_id)


@pytest.mark.integration
class TestAgentConversationMessageRoute(_BaseAgentConversationIntegration):
    _AGENT_MESSAGE_STREAM_PATH = AGENT_MESSAGE_STREAM_PATH
    _AGENT_CONVERSATION_DETAIL_PATH = AGENT_CONVERSATION_DETAIL_PATH
    _AGENT_CONVERSATION_TITLE_PATH = AGENT_CONVERSATION_TITLE_PATH
    _AGENT_REGENERATE_PATH = AGENT_REGENERATE_PATH
    _AGENT_FEEDBACK_PATH = AGENT_FEEDBACK_PATH

    def _get_agent_conversation_messages(
        self,
        agent_key: str,
        conversation_id: str,
    ) -> list[dict[str, Any]]:
        return get_agent_conversation_messages(
            self.base_url,
            self.headers,
            self.timeout,
            agent_key,
            conversation_id,
        )

    def _runtime_timezone_name(self) -> str:
        return runtime_timezone_name()

    def _runtime_current_time(self) -> str:
        return runtime_current_time()

    def _build_kb_filter_payload(self, kb: dict[str, Any]) -> dict[str, Any]:
        return build_kb_filter_payload(kb)

    def _build_connector_filter_payload(self, connector: dict[str, Any]) -> dict[str, Any]:
        return build_connector_filter_payload(connector)

    def _build_follow_up_payload(
        self,
        query: str | None,
        *,
        live_model: dict[str, Any] | None = None,
        include_model: bool = False,
        include_kb_filter: bool = False,
        include_time_context: bool = False,
        include_tools: bool = False,
        chat_mode: str | None = None,
    ) -> dict[str, Any]:
        return build_follow_up_payload(
            query,
            live_model=live_model,
            include_model=include_model,
            include_kb_filter=include_kb_filter,
            include_time_context=include_time_context,
            include_tools=include_tools,
            chat_mode=chat_mode,
        )

    def _stream_create_agent_conversation_and_last_bot_message_id(
        self,
        agent_key: str,
        *,
        query: str,
    ) -> tuple[str, str]:
        return stream_create_agent_conversation_and_last_bot_message_id(
            self.base_url,
            self.headers,
            self.timeout,
            self.stream_timeout,
            agent_key,
            query=query,
        )

    def _stream_create_agent_conversation_bot_and_user_message_ids(
        self,
        agent_key: str,
        *,
        query: str,
    ) -> tuple[str, str, str]:
        return stream_create_agent_conversation_bot_and_user_message_ids(
            self.base_url,
            self.headers,
            self.timeout,
            self.stream_timeout,
            agent_key,
            query=query,
        )

    def _assert_agent_feedback_response(
        self,
        body: dict[str, Any],
        *,
        conversation_id: str,
        message_id: str,
        request_payload: dict[str, Any],
    ) -> None:
        assert body.get("conversationId") == conversation_id, (
            f"conversationId mismatch: {body!r}"
        )
        assert body.get("messageId") == message_id, f"messageId mismatch: {body!r}"
        assert_response_matches_spec(body, self._AGENT_FEEDBACK_PATH, "POST", 200)

        feedback = body.get("feedback") or {}
        assert isinstance(feedback, dict), f"feedback missing or invalid: {body!r}"
        assert feedback.get("feedbackProvider"), (
            f"feedbackProvider missing: {feedback!r}"
        )
        assert isinstance(feedback.get("timestamp"), int), (
            f"timestamp missing or invalid: {feedback!r}"
        )

        metrics = feedback.get("metrics") or {}
        assert isinstance(metrics, dict), f"metrics missing or invalid: {feedback!r}"
        assert isinstance(metrics.get("timeToFeedback"), (int, float)), (
            f"timeToFeedback missing or invalid: {metrics!r}"
        )
        assert metrics.get("userAgent"), f"userAgent missing from metrics: {metrics!r}"

        for key in ("isHelpful", "ratings", "categories", "comments"):
            if key in request_payload:
                assert feedback.get(key) == request_payload[key], (
                    f"feedback.{key} mismatch: feedback={feedback!r} payload={request_payload!r}"
                )

        req_metrics = request_payload.get("metrics")
        if isinstance(req_metrics, dict):
            for key in ("userInteractionTime", "feedbackSessionId"):
                if key in req_metrics:
                    assert metrics.get(key) == req_metrics[key], (
                        f"feedback.metrics.{key} mismatch: metrics={metrics!r} payload={request_payload!r}"
                    )

        meta = body.get("meta") or {}
        assert meta.get("requestId"), f"meta.requestId missing: {body!r}"
        assert meta.get("timestamp"), f"meta.timestamp missing: {body!r}"
        assert isinstance(meta.get("duration"), int), (
            f"meta.duration missing or invalid: {body!r}"
        )

    def _create_agent_conversations(
        self,
        agent_key: str,
        queries: list[str],
    ) -> list[str]:
        return create_agent_conversations(
            self.base_url,
            self.headers,
            self.stream_timeout,
            agent_key,
            queries,
        )

    def _assert_agent_conversation_detail_response(
        self,
        body: dict[str, Any],
        *,
        conversation_id: str,
    ) -> None:
        assert_response_matches_spec(
            body,
            self._AGENT_CONVERSATION_DETAIL_PATH,
            "GET",
            200,
        )
        conversation = body.get("conversation") or {}
        assert conversation.get("id") == conversation_id, (
            f"conversation.id mismatch: {conversation!r}"
        )
        assert body.get("meta", {}).get("conversationId") == conversation_id, (
            f"meta.conversationId mismatch: {body.get('meta', {})!r}"
        )
        assert isinstance(conversation.get("messages") or [], list), (
            f"conversation.messages missing or invalid: {conversation!r}"
        )
        assert isinstance(conversation.get("pagination") or {}, dict), (
            f"conversation.pagination missing or invalid: {conversation!r}"
        )

    @pytest.mark.parametrize(
        ("case_name", "payload_kwargs"),
        [
            ("query_only", {}),
            ("query_with_live_model", {"include_model": True}),
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
        live_llm_model: dict[str, Any],
    ) -> None:
        agent_key = self.primary_agent_key
        created_conversation_id: str | None = None

        try:
            created_conversation_id = self._create_agent_conversation(agent_key)
            payload = self._build_follow_up_payload(
                f"integration agent varied request {case_name} {uuid.uuid4().hex}",
                live_model=live_llm_model,
                **payload_kwargs,
            )
            content_type, complete_payload = stream_json_post_to_complete(
                f"{self.base_url}/api/v1/agents/{agent_key}/conversations/"
                f"{created_conversation_id}/messages/stream",
                self.headers,
                payload,
                timeout=self.stream_timeout,
                context=f"agent message stream for case={case_name!r}",
                envelope_schema="AgentMessageStreamSSEEvent",
            )
            assert "text/event-stream" in content_type, (
                f"unexpected content-type for case={case_name!r}: {content_type!r}"
            )
            assert extract_conversation_id(complete_payload) == created_conversation_id, (
                f"response conversation id mismatch for case={case_name!r}: {complete_payload!r}"
            )
            assert isinstance(complete_payload.get("conversation"), dict), (
                f"conversation object missing for case={case_name!r}: {complete_payload!r}"
            )
        finally:
            if created_conversation_id:
                self._delete_agent_conversation(agent_key, created_conversation_id)

    def test_add_message_stream_to_agent_conversation_with_live_kb_filter(
        self,
        live_llm_model: dict[str, Any],
        live_knowledge_base: dict[str, Any],
    ) -> None:
        agent_key = self.primary_agent_key
        created_conversation_id: str | None = None

        try:
            created_conversation_id = self._create_agent_conversation(agent_key)
            payload = {
                **self._build_follow_up_payload(
                    f"integration agent varied request live_kb_filter {uuid.uuid4().hex}",
                    live_model=live_llm_model,
                    include_model=True,
                ),
                **self._build_kb_filter_payload(live_knowledge_base),
            }
            stream_json_post_to_complete(
                f"{self.base_url}/api/v1/agents/{agent_key}/conversations/"
                f"{created_conversation_id}/messages/stream",
                self.headers,
                payload,
                timeout=self.stream_timeout,
                context="agent message stream for live KB filter case",
                envelope_schema="AgentMessageStreamSSEEvent",
            )
        finally:
            if created_conversation_id:
                self._delete_agent_conversation(agent_key, created_conversation_id)

    def test_add_message_stream_to_agent_conversation_with_live_connector_and_kb_filters(
        self,
        live_llm_model: dict[str, Any],
        live_knowledge_base: dict[str, Any],
        live_connector: dict[str, Any],
    ) -> None:
        agent_key = self.primary_agent_key
        created_conversation_id: str | None = None

        try:
            created_conversation_id = self._create_agent_conversation(agent_key)
            payload = self._build_follow_up_payload(
                f"integration agent varied request live_connector_kb_filter {uuid.uuid4().hex}",
                live_model=live_llm_model,
                include_model=True,
                include_tools=True,
            )
            payload.update(self._build_kb_filter_payload(live_knowledge_base))
            connector_filter = self._build_connector_filter_payload(live_connector)
            payload["filters"] = {
                **(payload.get("filters") or {}),
                **connector_filter["filters"],
            }
            payload["appliedFilters"] = {
                **(payload.get("appliedFilters") or {}),
                **connector_filter["appliedFilters"],
            }
            stream_json_post_to_complete(
                f"{self.base_url}/api/v1/agents/{agent_key}/conversations/"
                f"{created_conversation_id}/messages/stream",
                self.headers,
                payload,
                timeout=self.stream_timeout,
                context="agent message stream for live connector + KB filter case",
                envelope_schema="AgentMessageStreamSSEEvent",
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
        live_llm_model: dict[str, Any],
        live_knowledge_base: dict[str, Any],
    ) -> None:
        agent_key = self.primary_agent_key
        created_conversation_id: str | None = None

        try:
            created_conversation_id = self._create_agent_conversation(agent_key)
            payload = {
                **self._build_follow_up_payload(
                    query,
                    live_model=live_llm_model,
                    include_model=True,
                    include_time_context=True,
                    include_tools=True,
                ),
                **self._build_kb_filter_payload(live_knowledge_base),
            }

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

    def test_get_agent_conversations_includes_two_stream_created(self) -> None:
        agent_key = self.primary_agent_key
        created_ids: list[str] = []

        try:
            created_ids = self._create_agent_conversations(
                agent_key,
                [
                    f"get-agent-conversations positive test conversation A {uuid.uuid4().hex}",
                    f"get-agent-conversations positive test conversation B {uuid.uuid4().hex}",
                ],
            )
            needed = set(created_ids)
            found: set[str] = set()
            first_list_body: dict[str, Any] | None = None
            page = 1

            while True:
                resp = requests.get(
                    self.agent_conversations_url_tpl.format(agentKey=agent_key),
                    headers=self.headers,
                    params={"limit": 100, "page": page},
                    timeout=self.timeout,
                )
                assert resp.status_code == 200, f"{resp.status_code}: {resp.text}"
                body = resp.json()
                if first_list_body is None:
                    first_list_body = body

                for row in body.get("conversations") or []:
                    if not isinstance(row, dict):
                        continue
                    cid = row.get("_id")
                    if isinstance(cid, str) and cid in needed:
                        found.add(cid)

                if needed <= found:
                    break

                pagination = body.get("pagination") or {}
                if not pagination.get("hasNextPage"):
                    pytest.fail(
                        f"Expected both new conversation ids in agent list; "
                        f"needed={needed}, found={found}, last_page={page}"
                    )
                page += 1

            assert first_list_body is not None
            assert_response_matches_spec(
                first_list_body,
                "/agents/{agentKey}/conversations",
                "GET",
                200,
            )
        finally:
            for conversation_id in reversed(created_ids):
                self._delete_agent_conversation(agent_key, conversation_id)

    @pytest.mark.parametrize(
        (
            "params",
            "expected_page",
            "expected_limit",
        ),
        [
            ({}, 1, 20),
            ({"page": "2", "limit": "1"}, 2, 1),
            ({"page": "2.9", "limit": "2.2"}, 2, 2),
            ({"sortBy": "createdAt", "sortOrder": "asc"}, 1, 20),
            ({"shared": "true"}, 1, 20),
            ({"search": "integration"}, 1, 20),
        ],
    )
    def test_get_agent_conversations_query_variations(
        self,
        params: dict[str, str],
        expected_page: int,
        expected_limit: int,
    ) -> None:
        agent_key = self.primary_agent_key
        resp = requests.get(
            self.agent_conversations_url_tpl.format(agentKey=agent_key),
            headers=self.headers,
            params=params,
            timeout=self.timeout,
        )
        assert resp.status_code == 200, f"{resp.status_code}: {resp.text}"
        body = resp.json()
        assert_response_matches_spec(
            body,
            "/agents/{agentKey}/conversations",
            "GET",
            200,
        )
        pagination = body.get("pagination") or {}
        assert pagination.get("page") == expected_page, (
            f"page mismatch for params={params!r}: {pagination!r}"
        )
        assert pagination.get("limit") == expected_limit, (
            f"limit mismatch for params={params!r}: {pagination!r}"
        )
        assert isinstance(body.get("conversations") or [], list), (
            f"conversations missing or invalid for params={params!r}: {body!r}"
        )
        assert isinstance(body.get("sharedWithMeConversations") or [], list), (
            f"sharedWithMeConversations missing or invalid for params={params!r}: {body!r}"
        )

    @pytest.mark.parametrize(
        ("case_name", "params"),
        [
            ("invalid_start_date", {"startDate": "not-a-date"}),
            ("invalid_end_date", {"endDate": "not-a-date"}),
            ("invalid_shared_boolean", {"shared": "not-a-bool"}),
            ("search_too_long", {"search": "a" * 1001}),
            ("search_xss", {"search": "<script>alert(1)</script>"}),
            ("page_zero", {"page": "0", "limit": "0"}),
            ("negative_page_limit", {"page": "-4", "limit": "-7"}),
            ("invalid_page", {"page": "abc", "limit": "999"}),
        ],
    )
    def test_get_agent_conversations_invalid_query_returns_400(
        self,
        case_name: str,
        params: dict[str, str],
    ) -> None:
        agent_key = self.primary_agent_key
        resp = requests.get(
            self.agent_conversations_url_tpl.format(agentKey=agent_key),
            headers=self.headers,
            params=params,
            timeout=self.timeout,
        )
        assert resp.status_code == 400, (
            f"case={case_name!r} expected 400, got {resp.status_code}: {resp.text}"
        )

    def test_get_agent_conversations_unknown_agent_key_returns_empty_lists(self) -> None:
        resp = requests.get(
            self.agent_conversations_url_tpl.format(agentKey=f"missing-agent-{uuid.uuid4().hex}"),
            headers=self.headers,
            timeout=self.timeout,
        )
        assert resp.status_code == 200, f"{resp.status_code}: {resp.text}"
        body = resp.json()
        assert_response_matches_spec(
            body,
            "/agents/{agentKey}/conversations",
            "GET",
            200,
        )
        assert body.get("conversations") == [], f"unexpected body: {body!r}"
        assert body.get("sharedWithMeConversations") == [], (
            f"unexpected body: {body!r}"
        )

    def test_get_agent_conversations_missing_auth_returns_401_or_403(self) -> None:
        agent_key = self.primary_agent_key
        resp = requests.get(
            self.agent_conversations_url_tpl.format(agentKey=agent_key),
            headers={"Content-Type": "application/json"},
            timeout=self.timeout,
        )
        assert resp.status_code in (401, 403), f"{resp.status_code}: {resp.text}"

    def test_get_agent_conversation_by_id_response_matches_spec(self) -> None:
        agent_key = self.primary_agent_key
        conversation_id: str | None = None

        try:
            conversation_id = self._create_agent_conversation(
                agent_key,
                query=f"integration: get agent conversation by id {uuid.uuid4().hex}",
            )
            resp = requests.get(
                self.agent_conversation_detail_url_tpl.format(
                    agentKey=agent_key,
                    conversationId=conversation_id,
                ),
                headers=self.headers,
                timeout=self.timeout,
            )
            assert resp.status_code == 200, f"{resp.status_code}: {resp.text}"
            body = resp.json()
            self._assert_agent_conversation_detail_response(
                body,
                conversation_id=conversation_id,
            )
        finally:
            if conversation_id:
                self._delete_agent_conversation(agent_key, conversation_id)

    @pytest.mark.parametrize("sort_order", ["asc", "desc"])
    def test_get_agent_conversation_by_id_with_sort_order_variants(
        self,
        sort_order: str,
    ) -> None:
        agent_key = self.primary_agent_key
        conversation_id: str | None = None

        try:
            conversation_id = self._create_agent_conversation(
                agent_key,
                query=f"integration: get agent conversation sort {sort_order} {uuid.uuid4().hex}",
            )
            resp = requests.get(
                self.agent_conversation_detail_url_tpl.format(
                    agentKey=agent_key,
                    conversationId=conversation_id,
                ),
                headers=self.headers,
                params={"sortOrder": sort_order},
                timeout=self.timeout,
            )
            assert resp.status_code == 200, f"{resp.status_code}: {resp.text}"
            body = resp.json()
            self._assert_agent_conversation_detail_response(
                body,
                conversation_id=conversation_id,
            )
        finally:
            if conversation_id:
                self._delete_agent_conversation(agent_key, conversation_id)

    def test_get_agent_conversation_by_id_with_limit_caps_messages(self) -> None:
        agent_key = self.primary_agent_key
        conversation_id: str | None = None

        try:
            conversation_id = self._create_agent_conversation(
                agent_key,
                query=f"integration: get agent conversation paginated messages {uuid.uuid4().hex}",
            )
            resp = requests.get(
                self.agent_conversation_detail_url_tpl.format(
                    agentKey=agent_key,
                    conversationId=conversation_id,
                ),
                headers=self.headers,
                params={"limit": 1, "page": 1},
                timeout=self.timeout,
            )
            assert resp.status_code == 200, f"{resp.status_code}: {resp.text}"
            body = resp.json()
            self._assert_agent_conversation_detail_response(
                body,
                conversation_id=conversation_id,
            )
            conversation = body.get("conversation") or {}
            messages = conversation.get("messages") or []
            assert len(messages) <= 1, (
                f"limit=1 should cap messages at 1, got {len(messages)}"
            )
        finally:
            if conversation_id:
                self._delete_agent_conversation(agent_key, conversation_id)

    @pytest.mark.parametrize(
        ("params", "expected_page", "expected_limit"),
        [
            ({}, 1, 20),
            ({"page": "1", "limit": "1"}, 1, 1),
            ({"page": "2", "limit": "1"}, 2, 1),
            ({"sortBy": "createdAt", "sortOrder": "asc"}, 1, 20),
            ({"sortBy": "content", "sortOrder": "desc"}, 1, 20),
            ({"page": "2.9", "limit": "2.2"}, 2, 2),
            ({"messageType": "user_query"}, 1, 20),
            (
                {
                    "startDate": "2026-01-01T00:00:00.000Z",
                    "endDate": "2026-12-31T23:59:59.999Z",
                },
                1,
                20,
            ),
        ],
    )
    def test_get_agent_conversation_by_id_query_variations(
        self,
        params: dict[str, str],
        expected_page: int,
        expected_limit: int,
    ) -> None:
        agent_key = self.primary_agent_key
        conversation_id: str | None = None

        try:
            conversation_id = self._create_agent_conversation(
                agent_key,
                query=f"integration: get agent conversation query variations {uuid.uuid4().hex}",
            )
            resp = requests.get(
                self.agent_conversation_detail_url_tpl.format(
                    agentKey=agent_key,
                    conversationId=conversation_id,
                ),
                headers=self.headers,
                params=params,
                timeout=self.timeout,
            )
            assert resp.status_code == 200, (
                f"params={params!r} {resp.status_code}: {resp.text}"
            )
            body = resp.json()
            self._assert_agent_conversation_detail_response(
                body,
                conversation_id=conversation_id,
            )
            pagination = (body.get("conversation") or {}).get("pagination") or {}
            assert pagination.get("page") == expected_page, (
                f"page mismatch for params={params!r}: {pagination!r}"
            )
            assert pagination.get("limit") == expected_limit, (
                f"limit mismatch for params={params!r}: {pagination!r}"
            )
        finally:
            if conversation_id:
                self._delete_agent_conversation(agent_key, conversation_id)

    @pytest.mark.parametrize(
        ("case_name", "params"),
        [
            ("invalid_message_type", {"messageType": "bad"}),
            ("invalid_start_date", {"startDate": "not-a-date"}),
            ("invalid_end_date", {"endDate": "not-a-date"}),
            ("invalid_sort_by", {"sortBy": "bad-field"}),
            ("page_zero", {"page": "0", "limit": "0"}),
            ("invalid_page", {"page": "abc", "limit": "999"}),
        ],
    )
    def test_get_agent_conversation_by_id_invalid_query_returns_400(
        self,
        case_name: str,
        params: dict[str, str],
    ) -> None:
        agent_key = self.primary_agent_key
        conversation_id: str | None = None

        try:
            conversation_id = self._create_agent_conversation(
                agent_key,
                query=f"integration: get agent conversation invalid query {case_name} {uuid.uuid4().hex}",
            )
            resp = requests.get(
                self.agent_conversation_detail_url_tpl.format(
                    agentKey=agent_key,
                    conversationId=conversation_id,
                ),
                headers=self.headers,
                params=params,
                timeout=self.timeout,
            )
            assert resp.status_code == 400, (
                f"case={case_name!r} expected 400, got {resp.status_code}: {resp.text}"
            )
        finally:
            if conversation_id:
                self._delete_agent_conversation(agent_key, conversation_id)

    def test_get_agent_conversation_by_id_missing_auth_returns_401_or_403(self) -> None:
        url = self.agent_conversation_detail_url_tpl.format(
            agentKey=self.primary_agent_key,
            conversationId="0" * 24,
        )
        resp = requests.get(
            url,
            headers={"Content-Type": "application/json"},
            timeout=self.timeout,
        )
        assert resp.status_code in (401, 403), f"{resp.status_code}: {resp.text}"

    def test_get_agent_conversation_by_id_invalid_conversation_id_returns_500(self) -> None:
        agent_key = self.primary_agent_key
        resp = requests.get(
            self.agent_conversation_detail_url_tpl.format(
                agentKey=agent_key,
                conversationId="not-an-objectid",
            ),
            headers=self.headers,
            timeout=self.timeout,
        )
        assert resp.status_code == 500, f"{resp.status_code}: {resp.text}"

    def test_get_agent_conversation_by_id_nonexistent_returns_404(self) -> None:
        agent_key = self.primary_agent_key
        resp = requests.get(
            self.agent_conversation_detail_url_tpl.format(
                agentKey=agent_key,
                conversationId="0" * 24,
            ),
            headers=self.headers,
            timeout=self.timeout,
        )
        assert resp.status_code == 404, f"{resp.status_code}: {resp.text}"

    def test_get_agent_conversation_by_id_wrong_agent_key_returns_404(self) -> None:
        agent_key = self.primary_agent_key
        conversation_id: str | None = None

        try:
            conversation_id = self._create_agent_conversation(
                agent_key,
                query=f"integration: get agent conversation wrong agent key {uuid.uuid4().hex}",
            )
            resp = requests.get(
                self.agent_conversation_detail_url_tpl.format(
                    agentKey=f"missing-agent-{uuid.uuid4().hex}",
                    conversationId=conversation_id,
                ),
                headers=self.headers,
                timeout=self.timeout,
            )
            assert resp.status_code == 404, f"{resp.status_code}: {resp.text}"
        finally:
            if conversation_id:
                self._delete_agent_conversation(agent_key, conversation_id)

    def test_delete_agent_conversation_lifecycle(self) -> None:
        agent_key = self.primary_agent_key
        conversation_id: str | None = None

        try:
            conversation_id = self._create_agent_conversation(
                agent_key,
                query=f"integration: delete agent conversation lifecycle {uuid.uuid4().hex}",
            )
            url = self.agent_conversation_detail_url_tpl.format(
                agentKey=agent_key,
                conversationId=conversation_id,
            )

            get_before = requests.get(
                url,
                headers=self.headers,
                timeout=self.timeout,
            )
            assert get_before.status_code == 200, (
                f"{get_before.status_code}: {get_before.text}"
            )
            before_body = get_before.json()
            self._assert_agent_conversation_detail_response(
                before_body,
                conversation_id=conversation_id,
            )

            delete_resp = requests.delete(
                url,
                headers=self.headers,
                timeout=self.timeout,
            )
            assert delete_resp.status_code == 200, (
                f"{delete_resp.status_code}: {delete_resp.text}"
            )
            delete_body = delete_resp.json()
            assert_response_matches_spec(
                delete_body,
                "/agents/{agentKey}/conversations/{conversationId}",
                "DELETE",
                200,
            )
            assert delete_body.get("message") == "Conversation deleted successfully", (
                f"unexpected delete body: {delete_body!r}"
            )
            conversation = delete_body.get("conversation") or {}
            assert conversation.get("_id") == conversation_id, (
                f"delete response conversation id mismatch: {conversation!r}"
            )
            assert conversation.get("agentKey") == agent_key, (
                f"delete response agent key mismatch: {conversation!r}"
            )
            assert conversation.get("isDeleted") is True, (
                f"conversation should be soft deleted: {conversation!r}"
            )
            assert conversation.get("deletedBy"), (
                f"deletedBy should be populated after delete: {conversation!r}"
            )

            get_after = requests.get(
                url,
                headers=self.headers,
                timeout=self.timeout,
            )
            assert get_after.status_code == 404, (
                f"GET after delete should be 404, got "
                f"{get_after.status_code}: {get_after.text}"
            )
            conversation_id = None
        finally:
            if conversation_id:
                self._delete_agent_conversation(agent_key, conversation_id)

    def test_delete_agent_conversation_missing_auth_returns_401_or_403(self) -> None:
        agent_key = self.primary_agent_key
        resp = requests.delete(
            self.agent_conversation_detail_url_tpl.format(
                agentKey=agent_key,
                conversationId="0" * 24,
            ),
            headers={"Content-Type": "application/json"},
            timeout=self.timeout,
        )
        assert resp.status_code in (401, 403), f"{resp.status_code}: {resp.text}"

    def test_delete_agent_conversation_invalid_conversation_id_returns_200_with_null_conversation(
        self,
    ) -> None:
        agent_key = self.primary_agent_key
        resp = requests.delete(
            self.agent_conversation_detail_url_tpl.format(
                agentKey=agent_key,
                conversationId="not-an-objectid",
            ),
            headers=self.headers,
            timeout=self.timeout,
        )
        assert resp.status_code == 200, f"{resp.status_code}: {resp.text}"
        body = resp.json()
        assert_response_matches_spec(
            body,
            "/agents/{agentKey}/conversations/{conversationId}",
            "DELETE",
            200,
        )
        assert body.get("message") == "Conversation deleted successfully", (
            f"unexpected delete body: {body!r}"
        )
        assert body.get("conversation") is None, f"unexpected body: {body!r}"

    def test_delete_agent_conversation_nonexistent_returns_200_with_null_conversation(
        self,
    ) -> None:
        agent_key = self.primary_agent_key
        resp = requests.delete(
            self.agent_conversation_detail_url_tpl.format(
                agentKey=agent_key,
                conversationId="0" * 24,
            ),
            headers=self.headers,
            timeout=self.timeout,
        )
        assert resp.status_code == 200, f"{resp.status_code}: {resp.text}"
        body = resp.json()
        assert_response_matches_spec(
            body,
            "/agents/{agentKey}/conversations/{conversationId}",
            "DELETE",
            200,
        )
        assert body.get("message") == "Conversation deleted successfully", (
            f"unexpected delete body: {body!r}"
        )
        assert body.get("conversation") is None, f"unexpected body: {body!r}"

    def test_delete_agent_conversation_wrong_agent_key_returns_200_with_null_conversation(
        self,
    ) -> None:
        agent_key = self.primary_agent_key
        conversation_id: str | None = None

        try:
            conversation_id = self._create_agent_conversation(
                agent_key,
                query=(
                    "integration: delete agent conversation wrong agent key "
                    f"{uuid.uuid4().hex}"
                ),
            )
            resp = requests.delete(
                self.agent_conversation_detail_url_tpl.format(
                    agentKey=f"missing-agent-{uuid.uuid4().hex}",
                    conversationId=conversation_id,
                ),
                headers=self.headers,
                timeout=self.timeout,
            )
            assert resp.status_code == 200, f"{resp.status_code}: {resp.text}"
            body = resp.json()
            assert_response_matches_spec(
                body,
                "/agents/{agentKey}/conversations/{conversationId}",
                "DELETE",
                200,
            )
            assert body.get("message") == "Conversation deleted successfully", (
                f"unexpected delete body: {body!r}"
            )
            assert body.get("conversation") is None, f"unexpected body: {body!r}"
        finally:
            if conversation_id:
                self._delete_agent_conversation(agent_key, conversation_id)

    # ------------------------------------------------------------------
    # PATCH /:agentKey/conversations/:conversationId/title
    # ------------------------------------------------------------------

    def test_patch_agent_conversation_title_updates_and_persists(self) -> None:
        agent_key = self.primary_agent_key
        conversation_id: str | None = None
        new_title = "Renamed via integration test"

        try:
            conversation_id = self._create_agent_conversation(
                agent_key,
                query=f"integration: rename agent conversation title {uuid.uuid4().hex}",
            )
            title_url = self.agent_conversation_title_url_tpl.format(
                agentKey=agent_key,
                conversationId=conversation_id,
            )

            patch_resp = requests.patch(
                title_url,
                headers=self.headers,
                json={"title": new_title},
                timeout=self.timeout,
            )
            assert patch_resp.status_code == 200, (
                f"{patch_resp.status_code}: {patch_resp.text}"
            )

            body = patch_resp.json()
            conv = body.get("conversation") or {}
            assert conv.get("_id") == conversation_id, (
                f"conversation._id mismatch: expected {conversation_id!r}, "
                f"got {conv.get('_id')!r}"
            )
            assert conv.get("title") == new_title, (
                f"conversation.title mismatch: expected {new_title!r}, "
                f"got {conv.get('title')!r}"
            )
            self._assert_agent_conversation_title_patch_response(body)

            meta = body.get("meta") or {}
            assert meta.get("requestId"), f"meta.requestId missing: {meta!r}"
            assert meta.get("timestamp"), f"meta.timestamp missing: {meta!r}"
            assert "duration" in meta, f"meta.duration missing: {meta!r}"

            get_resp = requests.get(
                self.agent_conversation_detail_url_tpl.format(
                    agentKey=agent_key,
                    conversationId=conversation_id,
                ),
                headers=self.headers,
                timeout=self.timeout,
            )
            assert get_resp.status_code == 200, (
                f"{get_resp.status_code}: {get_resp.text}"
            )
            get_conv = get_resp.json().get("conversation") or {}
            assert get_conv.get("id") == conversation_id, (
                f"GET conversation.id mismatch: {get_conv!r}"
            )
            assert get_conv.get("title") == new_title, (
                f"GET conversation.title did not persist: expected {new_title!r}, "
                f"got {get_conv.get('title')!r}"
            )
        finally:
            if conversation_id:
                self._delete_agent_conversation(agent_key, conversation_id)

    @pytest.mark.parametrize("new_title", ["a", "x" * 200])
    def test_patch_agent_conversation_title_boundary_lengths(
        self,
        new_title: str,
    ) -> None:
        agent_key = self.primary_agent_key
        conversation_id: str | None = None

        try:
            conversation_id = self._create_agent_conversation(
                agent_key,
                query=(
                    f"integration: agent title boundary length {len(new_title)} "
                    f"{uuid.uuid4().hex}"
                ),
            )
            title_url = self.agent_conversation_title_url_tpl.format(
                agentKey=agent_key,
                conversationId=conversation_id,
            )

            patch_resp = requests.patch(
                title_url,
                headers=self.headers,
                json={"title": new_title},
                timeout=self.timeout,
            )
            assert patch_resp.status_code == 200, (
                f"{patch_resp.status_code}: {patch_resp.text}"
            )
            body = patch_resp.json()
            conv = body.get("conversation") or {}
            assert conv.get("title") == new_title, (
                f"PATCH title mismatch: expected {new_title!r}, got {conv.get('title')!r}"
            )
            self._assert_agent_conversation_title_patch_response(body)

            get_resp = requests.get(
                self.agent_conversation_detail_url_tpl.format(
                    agentKey=agent_key,
                    conversationId=conversation_id,
                ),
                headers=self.headers,
                timeout=self.timeout,
            )
            assert get_resp.status_code == 200, (
                f"{get_resp.status_code}: {get_resp.text}"
            )
            get_conv = get_resp.json().get("conversation") or {}
            assert get_conv.get("title") == new_title, (
                f"GET title did not persist: expected {new_title!r}, "
                f"got {get_conv.get('title')!r}"
            )
        finally:
            if conversation_id:
                self._delete_agent_conversation(agent_key, conversation_id)

    def test_patch_agent_conversation_title_with_extraneous_query_params(self) -> None:
        agent_key = self.primary_agent_key
        conversation_id: str | None = None
        new_title = f"Title with query params {uuid.uuid4().hex}"

        try:
            conversation_id = self._create_agent_conversation(
                agent_key,
                query=f"integration: agent title with query params {uuid.uuid4().hex}",
            )
            patch_resp = requests.patch(
                self.agent_conversation_title_url_tpl.format(
                    agentKey=agent_key,
                    conversationId=conversation_id,
                ),
                headers=self.headers,
                params={"unused": "value", "page": "1"},
                json={"title": new_title},
                timeout=self.timeout,
            )
            assert patch_resp.status_code == 200, (
                f"{patch_resp.status_code}: {patch_resp.text}"
            )
            body = patch_resp.json()
            conv = body.get("conversation") or {}
            assert conv.get("title") == new_title, (
                f"title mismatch: expected {new_title!r}, got {conv.get('title')!r}"
            )
            self._assert_agent_conversation_title_patch_response(body)
        finally:
            if conversation_id:
                self._delete_agent_conversation(agent_key, conversation_id)

    def test_patch_agent_conversation_title_trims_whitespace(self) -> None:
        agent_key = self.primary_agent_key
        conversation_id: str | None = None
        expected_title = "Trimmed title"

        try:
            conversation_id = self._create_agent_conversation(
                agent_key,
                query=f"integration: agent title trim whitespace {uuid.uuid4().hex}",
            )
            title_url = self.agent_conversation_title_url_tpl.format(
                agentKey=agent_key,
                conversationId=conversation_id,
            )

            patch_resp = requests.patch(
                title_url,
                headers=self.headers,
                json={"title": f"  {expected_title}  "},
                timeout=self.timeout,
            )
            assert patch_resp.status_code == 200, (
                f"{patch_resp.status_code}: {patch_resp.text}"
            )
            body = patch_resp.json()
            conv = body.get("conversation") or {}
            assert conv.get("title") == expected_title, (
                f"PATCH title should be trimmed: expected {expected_title!r}, "
                f"got {conv.get('title')!r}"
            )
            self._assert_agent_conversation_title_patch_response(body)

            get_resp = requests.get(
                self.agent_conversation_detail_url_tpl.format(
                    agentKey=agent_key,
                    conversationId=conversation_id,
                ),
                headers=self.headers,
                timeout=self.timeout,
            )
            assert get_resp.status_code == 200, (
                f"{get_resp.status_code}: {get_resp.text}"
            )
            get_conv = get_resp.json().get("conversation") or {}
            assert get_conv.get("title") == expected_title, (
                f"GET title should be trimmed: expected {expected_title!r}, "
                f"got {get_conv.get('title')!r}"
            )
        finally:
            if conversation_id:
                self._delete_agent_conversation(agent_key, conversation_id)

    @pytest.mark.parametrize(
        ("case_name", "conversation_id", "expected_status"),
        [
            ("invalid_agent_key", "0" * 24, (400, 404)),
            ("invalid_conversation_id", "not-an-objectid", (400,)),
            ("nonexistent_conversation_id", "0" * 24, (404,)),
        ],
    )
    def test_patch_agent_conversation_title_invalid_path_params(
        self,
        case_name: str,
        conversation_id: str,
        expected_status: tuple[int, ...],
    ) -> None:
        agent_key = "" if case_name == "invalid_agent_key" else self.primary_agent_key
        resp = requests.patch(
            self.agent_conversation_title_url_tpl.format(
                agentKey=agent_key,
                conversationId=conversation_id,
            ),
            headers=self.headers,
            json={"title": "x"},
            timeout=self.timeout,
        )
        assert resp.status_code in expected_status, (
            f"case={case_name!r} expected {expected_status}, "
            f"got {resp.status_code}: {resp.text}"
        )

    def test_patch_agent_conversation_title_wrong_agent_key_returns_404(self) -> None:
        agent_key = self.primary_agent_key
        conversation_id: str | None = None

        try:
            conversation_id = self._create_agent_conversation(
                agent_key,
                query=(
                    "integration: patch agent conversation title wrong agent key "
                    f"{uuid.uuid4().hex}"
                ),
            )
            resp = requests.patch(
                self.agent_conversation_title_url_tpl.format(
                    agentKey=f"missing-agent-{uuid.uuid4().hex}",
                    conversationId=conversation_id,
                ),
                headers=self.headers,
                json={"title": "Should not apply"},
                timeout=self.timeout,
            )
            assert resp.status_code == 404, f"{resp.status_code}: {resp.text}"
        finally:
            if conversation_id:
                self._delete_agent_conversation(agent_key, conversation_id)

    def test_patch_agent_conversation_title_missing_auth_returns_401_or_403(
        self,
    ) -> None:
        resp = requests.patch(
            self.agent_conversation_title_url_tpl.format(
                agentKey=self.primary_agent_key,
                conversationId="0" * 24,
            ),
            headers={"Content-Type": "application/json"},
            json={"title": "x"},
            timeout=self.timeout,
        )
        assert resp.status_code in (401, 403), f"{resp.status_code}: {resp.text}"

    @pytest.mark.parametrize(
        "payload",
        [
            {},
            {"title": ""},
            {"title": "a" * 201},
            {"title": 123},
            {"title": "   "},
        ],
    )
    def test_patch_agent_conversation_title_invalid_body_returns_400(
        self,
        payload: dict[str, Any],
    ) -> None:
        agent_key = self.primary_agent_key
        conversation_id: str | None = None

        try:
            conversation_id = self._create_agent_conversation(
                agent_key,
                query=(
                    "integration: patch agent conversation invalid title payload "
                    f"{uuid.uuid4().hex}"
                ),
            )
            resp = requests.patch(
                self.agent_conversation_title_url_tpl.format(
                    agentKey=agent_key,
                    conversationId=conversation_id,
                ),
                headers=self.headers,
                json=payload,
                timeout=self.timeout,
            )
            assert resp.status_code == 400, f"{resp.status_code}: {resp.text}"
        finally:
            if conversation_id:
                self._delete_agent_conversation(agent_key, conversation_id)

    def test_patch_agent_conversation_title_on_deleted_conversation_returns_404(
        self,
    ) -> None:
        agent_key = self.primary_agent_key
        conversation_id: str | None = None

        try:
            conversation_id = self._create_agent_conversation(
                agent_key,
                query=(
                    "integration: patch agent conversation title after delete "
                    f"{uuid.uuid4().hex}"
                ),
            )
            detail_url = self.agent_conversation_detail_url_tpl.format(
                agentKey=agent_key,
                conversationId=conversation_id,
            )
            delete_resp = requests.delete(
                detail_url,
                headers=self.headers,
                timeout=self.timeout,
            )
            assert delete_resp.status_code == 200, (
                f"{delete_resp.status_code}: {delete_resp.text}"
            )

            patch_resp = requests.patch(
                self.agent_conversation_title_url_tpl.format(
                    agentKey=agent_key,
                    conversationId=conversation_id,
                ),
                headers=self.headers,
                json={"title": "Should not apply"},
                timeout=self.timeout,
            )
            assert patch_resp.status_code == 404, (
                f"{patch_resp.status_code}: {patch_resp.text}"
            )
            conversation_id = None
        finally:
            if conversation_id:
                self._delete_agent_conversation(agent_key, conversation_id)

    @pytest.mark.parametrize(
        ("case_name", "payload"),
        [
            ("empty_body", {}),
            ("real_payload_shape_with_live_filters", {"chatMode": "auto", "tools": []}),
            (
                "live_model_with_runtime_context",
                {
                    "chatMode": "verification",
                    "tools": [],
                },
            ),
        ],
    )
    def test_regenerate_agent_last_bot_message_streams_to_complete(
        self,
        case_name: str,
        payload: dict[str, Any],
        live_llm_model: dict[str, Any],
        live_knowledge_base: dict[str, Any],
        live_connectors: list[dict[str, Any]],
    ) -> None:
        agent_key = self.primary_agent_key
        conversation_id: str | None = None

        try:
            conversation_id, message_id = (
                self._stream_create_agent_conversation_and_last_bot_message_id(
                    agent_key,
                    query=f"integration agent regenerate positive {case_name} {uuid.uuid4().hex}",
                )
            )
            if case_name == "real_payload_shape_with_live_filters":
                payload = {
                    "modelKey": live_llm_model["modelKey"],
                    "modelName": live_llm_model["modelName"],
                    "timezone": "Asia/Kolkata",
                    "currentTime": "2026-05-20T08:32:29+05:30",
                    **payload,
                }
                if live_llm_model.get("modelFriendlyName"):
                    payload["modelFriendlyName"] = live_llm_model["modelFriendlyName"]
                payload.update(self._build_kb_filter_payload(live_knowledge_base))
                if live_connectors:
                    connector_ids = [
                        str(
                            connector.get("_id")
                            or connector.get("id")
                            or connector.get("_key")
                        )
                        for connector in live_connectors[:3]
                        if connector.get("_id")
                        or connector.get("id")
                        or connector.get("_key")
                    ]
                    if connector_ids:
                        filters = payload.setdefault("filters", {})
                        filters["apps"] = connector_ids
            if case_name == "live_model_with_runtime_context":
                model = live_llm_model
                payload = {
                    **payload,
                    "modelKey": model["modelKey"],
                    "modelName": model["modelName"],
                    "timezone": self._runtime_timezone_name(),
                    "currentTime": self._runtime_current_time(),
                    "filters": {},
                }

            url = self.agent_regenerate_url_tpl.format(
                agentKey=agent_key,
                conversationId=conversation_id,
                messageId=message_id,
            )
            headers = {**self.headers, "Accept": "text/event-stream"}

            with requests.post(
                url,
                headers=headers,
                json=payload,
                stream=True,
                timeout=self.stream_timeout,
            ) as resp:
                assert resp.status_code == 200, (
                    f"case={case_name!r} {resp.status_code}: {resp.text}"
                )
                content_type = (resp.headers.get("Content-Type") or "").lower()
                assert "text/event-stream" in content_type, (
                    f"case={case_name!r} expected text/event-stream, got {resp.headers.get('Content-Type')!r}"
                )

                saw_complete = False
                for envelope in iter_sse_envelopes(resp):
                    assert_matches_component_schema(
                        envelope,
                        "AgentRegenerateSSEEvent",
                    )
                    if envelope["event"] == "error":
                        payload_obj = json.loads(envelope["data"])
                        raise AssertionError(
                            f"case={case_name!r} regenerate stream emitted error: {payload_obj!r}"
                        )
                    if envelope["event"] != "complete":
                        continue

                    saw_complete = True
                    payload_obj = json.loads(envelope["data"])
                    conv = payload_obj.get("conversation") or {}
                    assert conv.get("_id") == conversation_id, (
                        f"case={case_name!r} conversation id mismatch: {payload_obj!r}"
                    )
                    msgs = conv.get("messages") or []
                    assert msgs, (
                        f"case={case_name!r} complete payload missing messages: {payload_obj!r}"
                    )
                    last = msgs[-1]
                    assert last.get("messageType") == "bot_response", (
                        f"case={case_name!r} expected last message bot_response, got {last.get('messageType')!r}"
                    )
                    content = last.get("content") or ""
                    assert content.strip(), (
                        f"case={case_name!r} expected non-empty bot content, got {content!r}"
                    )
                    break

                assert saw_complete, (
                    f"case={case_name!r} regenerate stream ended without a complete event"
                )
        finally:
            if conversation_id:
                self._delete_agent_conversation(agent_key, conversation_id)

    def test_regenerate_agent_missing_auth_returns_401_or_403(self) -> None:
        url = self.agent_regenerate_url_tpl.format(
            agentKey=self.primary_agent_key,
            conversationId="0" * 24,
            messageId="0" * 24,
        )
        resp = requests.post(
            url,
            headers={"Content-Type": "application/json"},
            json={},
            timeout=self.timeout,
        )
        assert resp.status_code in (401, 403), f"{resp.status_code}: {resp.text}"

    @pytest.mark.parametrize(
        ("case_name", "conversation_id", "message_id"),
        [
            ("invalid_agent_key", "0" * 24, "0" * 24),
            ("invalid_conversation_id", "not-an-objectid", "0" * 24),
            ("invalid_message_id", "0" * 24, "not-an-objectid"),
        ],
    )
    def test_regenerate_agent_invalid_path_params_return_400_or_404(
        self,
        case_name: str,
        conversation_id: str,
        message_id: str,
    ) -> None:
        agent_key = "" if case_name == "invalid_agent_key" else self.primary_agent_key
        url = self.agent_regenerate_url_tpl.format(
            agentKey=agent_key,
            conversationId=conversation_id,
            messageId=message_id,
        )
        resp = requests.post(
            url,
            headers=self.headers,
            json={},
            timeout=self.timeout,
        )
        assert resp.status_code in (400, 404), (
            f"case={case_name!r} expected 400/404, got {resp.status_code}: {resp.text}"
        )

    @pytest.mark.parametrize(
        ("case_name", "payload"),
        [
            ("invalid_current_time", {"currentTime": "not-an-iso-datetime"}),
            ("invalid_timezone_type", {"timezone": 123}),
            ("invalid_model_name_type", {"modelName": 123}),
            ("invalid_filters_shape", {"filters": {"apps": "not-a-list"}}),
            ("invalid_tools_shape", {"tools": {}}),
            ("empty_chat_mode", {"chatMode": ""}),
        ],
    )
    def test_regenerate_agent_invalid_body_returns_400(
        self,
        case_name: str,
        payload: dict[str, Any],
    ) -> None:
        agent_key = self.primary_agent_key
        conversation_id: str | None = None

        try:
            conversation_id, message_id = (
                self._stream_create_agent_conversation_and_last_bot_message_id(
                    agent_key,
                    query=f"integration agent regenerate invalid body {case_name} {uuid.uuid4().hex}",
                )
            )
            url = self.agent_regenerate_url_tpl.format(
                agentKey=agent_key,
                conversationId=conversation_id,
                messageId=message_id,
            )
            resp = requests.post(
                url,
                headers=self.headers,
                json=payload,
                timeout=self.timeout,
            )
            assert resp.status_code == 400, (
                f"case={case_name!r} expected 400, got {resp.status_code}: {resp.text}"
            )
        finally:
            if conversation_id:
                self._delete_agent_conversation(agent_key, conversation_id)

    def test_regenerate_agent_non_last_message_id_emits_sse_error(self) -> None:
        agent_key = self.primary_agent_key
        conversation_id: str | None = None

        try:
            conversation_id, _bot_id, user_query_id = (
                self._stream_create_agent_conversation_bot_and_user_message_ids(
                    agent_key,
                    query=f"integration agent regenerate wrong message id {uuid.uuid4().hex}",
                )
            )
            url = self.agent_regenerate_url_tpl.format(
                agentKey=agent_key,
                conversationId=conversation_id,
                messageId=user_query_id,
            )
            headers = {**self.headers, "Accept": "text/event-stream"}

            with requests.post(
                url,
                headers=headers,
                json={},
                stream=True,
                timeout=self.stream_timeout,
            ) as resp:
                assert resp.status_code == 200, f"{resp.status_code}: {resp.text}"

                for envelope in iter_sse_envelopes(resp):
                    if envelope["event"] != "error":
                        continue
                    payload = json.loads(envelope["data"])
                    err = payload.get("message") or payload.get("error") or ""
                    assert "last message" in str(err).lower(), (
                        f"unexpected error payload: {payload!r}"
                    )
                    return

            raise AssertionError("regenerate stream ended without an error event")
        finally:
            if conversation_id:
                self._delete_agent_conversation(agent_key, conversation_id)

    @pytest.mark.parametrize(
        ("case_name", "payload"),
        [
            (
                "minimal_real_body",
                {"isHelpful": True, "categories": ["helpful_citations"]},
            ),
            (
                "rich_structured_body",
                {
                    "isHelpful": True,
                    "ratings": {"accuracy": 5, "relevance": 4},
                    "categories": ["excellent_answer", "well_explained"],
                    "comments": {
                        "positive": "Clear and useful.",
                        "negative": "",
                        "suggestions": "Add one more example.",
                    },
                    "metrics": {
                        "userInteractionTime": 1200,
                        "feedbackSessionId": "integration-agent-feedback-session",
                    },
                },
            ),
            ("empty_body", {}),
        ],
    )
    def test_post_agent_message_feedback_on_bot_response_matches_spec(
        self,
        case_name: str,
        payload: dict[str, Any],
    ) -> None:
        agent_key = self.primary_agent_key
        conversation_id: str | None = None

        try:
            conversation_id, bot_id, _user_id = (
                self._stream_create_agent_conversation_bot_and_user_message_ids(
                    agent_key,
                    query=f"integration: agent message feedback positive {case_name} {uuid.uuid4().hex}",
                )
            )
            url = self.agent_feedback_url_tpl.format(
                agentKey=agent_key,
                conversationId=conversation_id,
                messageId=bot_id,
            )
            resp = requests.post(
                url, headers=self.headers, json=payload, timeout=self.timeout
            )
            assert resp.status_code == 200, (
                f"case={case_name!r} {resp.status_code}: {resp.text}"
            )
            body = resp.json()
            self._assert_agent_feedback_response(
                body,
                conversation_id=conversation_id,
                message_id=bot_id,
                request_payload=payload,
            )
        finally:
            if conversation_id:
                self._delete_agent_conversation(agent_key, conversation_id)

    def test_post_agent_message_feedback_ignores_extra_query_params(self) -> None:
        agent_key = self.primary_agent_key
        conversation_id: str | None = None

        try:
            conversation_id, bot_id, _user_id = (
                self._stream_create_agent_conversation_bot_and_user_message_ids(
                    agent_key,
                    query=f"integration: agent message feedback query params {uuid.uuid4().hex}",
                )
            )
            payload = {"isHelpful": True, "categories": ["helpful_citations"]}
            url = self.agent_feedback_url_tpl.format(
                agentKey=agent_key,
                conversationId=conversation_id,
                messageId=bot_id,
            )
            resp = requests.post(
                url,
                headers=self.headers,
                params={"debug": "1", "source": "integration"},
                json=payload,
                timeout=self.timeout,
            )
            assert resp.status_code == 200, f"{resp.status_code}: {resp.text}"
            body = resp.json()
            self._assert_agent_feedback_response(
                body,
                conversation_id=conversation_id,
                message_id=bot_id,
                request_payload=payload,
            )
        finally:
            if conversation_id:
                self._delete_agent_conversation(agent_key, conversation_id)

    def test_post_agent_message_feedback_on_user_query_returns_400(self) -> None:
        agent_key = self.primary_agent_key
        conversation_id: str | None = None

        try:
            conversation_id, _bot_id, user_id = (
                self._stream_create_agent_conversation_bot_and_user_message_ids(
                    agent_key,
                    query=f"integration: agent message feedback negative user_query {uuid.uuid4().hex}",
                )
            )
            url = self.agent_feedback_url_tpl.format(
                agentKey=agent_key,
                conversationId=conversation_id,
                messageId=user_id,
            )
            resp = requests.post(
                url, headers=self.headers, json={}, timeout=self.timeout
            )
            assert resp.status_code == 400, f"{resp.status_code}: {resp.text}"
            lowered = resp.text.lower()
            assert "bot" in lowered or "feedback" in lowered, (
                f"unexpected error body: {resp.text!r}"
            )
        finally:
            if conversation_id:
                self._delete_agent_conversation(agent_key, conversation_id)

    def test_post_agent_message_feedback_missing_auth_returns_401_or_403(self) -> None:
        url = self.agent_feedback_url_tpl.format(
            agentKey=self.primary_agent_key,
            conversationId="0" * 24,
            messageId="0" * 24,
        )
        resp = requests.post(
            url,
            headers={"Content-Type": "application/json"},
            json={},
            timeout=self.timeout,
        )
        assert resp.status_code in (401, 403), f"{resp.status_code}: {resp.text}"

    @pytest.mark.parametrize(
        ("case_name", "conversation_id", "message_id", "expected_status"),
        [
            ("invalid_agent_key", "0" * 24, "0" * 24, (400, 404)),
            ("invalid_conversation_id", "not-an-objectid", "0" * 24, (400,)),
            ("invalid_message_id", "0" * 24, "not-an-objectid", (400,)),
            ("nonexistent_conversation_id", "0" * 24, "0" * 24, (404,)),
        ],
    )
    def test_post_agent_message_feedback_invalid_or_missing_path_params(
        self,
        case_name: str,
        conversation_id: str,
        message_id: str,
        expected_status: tuple[int, ...],
    ) -> None:
        agent_key = "" if case_name == "invalid_agent_key" else self.primary_agent_key
        url = self.agent_feedback_url_tpl.format(
            agentKey=agent_key,
            conversationId=conversation_id,
            messageId=message_id,
        )
        resp = requests.post(
            url,
            headers=self.headers,
            json={"isHelpful": True},
            timeout=self.timeout,
        )
        assert resp.status_code in expected_status, (
            f"case={case_name!r} expected {expected_status}, got {resp.status_code}: {resp.text}"
        )

    def test_post_agent_message_feedback_nonexistent_message_id_returns_404(self) -> None:
        agent_key = self.primary_agent_key
        conversation_id: str | None = None

        try:
            conversation_id = self._create_agent_conversation(agent_key)
            url = self.agent_feedback_url_tpl.format(
                agentKey=agent_key,
                conversationId=conversation_id,
                messageId="0" * 24,
            )
            resp = requests.post(
                url,
                headers=self.headers,
                json={"isHelpful": True},
                timeout=self.timeout,
            )
            assert resp.status_code == 404, f"{resp.status_code}: {resp.text}"
        finally:
            if conversation_id:
                self._delete_agent_conversation(agent_key, conversation_id)

    @pytest.mark.parametrize(
        ("case_name", "payload"),
        [
            ("invalid_category_enum", {"categories": ["not-a-valid-category"]}),
            ("ratings_below_min", {"ratings": {"accuracy": 0}}),
            ("ratings_above_max", {"ratings": {"accuracy": 6}}),
            ("invalid_is_helpful_type", {"isHelpful": "true"}),
            ("invalid_ratings_type", {"ratings": "bad"}),
            ("invalid_categories_type", {"categories": "helpful_citations"}),
            ("invalid_comments_type", {"comments": "bad"}),
            ("invalid_comments_positive_type", {"comments": {"positive": 7}}),
            ("invalid_metrics_type", {"metrics": "bad"}),
            ("invalid_user_interaction_time_type", {"metrics": {"userInteractionTime": "fast"}}),
            ("invalid_feedback_session_id_type", {"metrics": {"feedbackSessionId": 123}}),
        ],
    )
    def test_post_agent_message_feedback_invalid_body_returns_400(
        self,
        case_name: str,
        payload: dict[str, Any],
    ) -> None:
        agent_key = self.primary_agent_key
        conversation_id: str | None = None

        try:
            conversation_id, bot_id, _user_id = (
                self._stream_create_agent_conversation_bot_and_user_message_ids(
                    agent_key,
                    query=f"integration: agent message feedback invalid body {case_name} {uuid.uuid4().hex}",
                )
            )
            url = self.agent_feedback_url_tpl.format(
                agentKey=agent_key,
                conversationId=conversation_id,
                messageId=bot_id,
            )
            resp = requests.post(
                url,
                headers=self.headers,
                json=payload,
                timeout=self.timeout,
            )
            assert resp.status_code == 400, (
                f"case={case_name!r} expected 400, got {resp.status_code}: {resp.text}"
            )
        finally:
            if conversation_id:
                self._delete_agent_conversation(agent_key, conversation_id)
