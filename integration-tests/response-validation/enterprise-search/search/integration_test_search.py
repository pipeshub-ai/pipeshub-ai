"""Enterprise search response-schema integration tests.

Set SEARCH_QUERY to a question that has answers in your data.
Set PIPESHUB_TEST_SHARE_TARGET_USER_ID to a real user id in your organisation
(share/unshare tests skip when it is unset).
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

import pytest

_ROOT = Path(__file__).resolve().parents[3]
_RV_HELPER = _ROOT / "response-validation" / "helper"
for _p in (_ROOT, _RV_HELPER):
    s = str(_p)
    if s not in sys.path:
        sys.path.insert(0, s)

from helper.clients.search_client import SearchClient
from openapi_schema_validator import (
    assert_response_matches_openapi_operation,
    assert_response_matches_openapi_ref,
)

SEARCH_QUERY = "every year asana undertakes which exercise?"
# TODO: add connector filter support to tests
# CONNECTOR_SEARCH_QUERY = "What are some new news?"
# CONNECTOR_APP_ID = "ed6d6cc4-70bd-4838-9aeb-488e910c833a"
SHARE_TARGET_USER_ID = os.getenv("PIPESHUB_TEST_SHARE_TARGET_USER_ID", "").strip()


class SearchTestBase:
    """Shared search_client fixture and request timeout."""

    @pytest.fixture(autouse=True)
    def _setup(self, search_client: SearchClient, session_kb: dict[str, str]) -> None:
        self.search = search_client
        self.kb_id = session_kb["kb_id"]
        self.timeout = int(os.getenv("PIPESHUB_TEST_TIMEOUT", "60"))

    def _create_search(self, **payload: Any) -> str:
        """Create a search and return its ``searchId``."""
        body = {"query": SEARCH_QUERY, "limit": 5, **payload}
        resp = self.search.create_search(**body, timeout=self.timeout)
        assert resp.status_code == 200, f"{resp.status_code}: {resp.text}"
        search_id = resp.json().get("searchId")
        assert search_id, "POST /search response missing searchId"
        return search_id

    def _list_active_history(self) -> list[dict[str, Any]]:
        """Return active (non-archived) search history rows."""
        resp = self.search.list_history(limit=100, timeout=self.timeout)
        assert resp.status_code == 200, f"{resp.status_code}: {resp.text}"
        return resp.json().get("searchHistory") or []


# ============================================================================
# Router: createSemanticSearchRouter
# Routes mounted at /api/v1/search
# ============================================================================
@pytest.mark.integration
class TestSemanticSearch(SearchTestBase):

    def _assert_search_response_ok(self, body: dict[str, Any], query: str) -> None:
        search_response = body.get("searchResponse") or {}
        search_results = search_response.get("searchResults") or []
        records = search_response.get("records") or []

        assert search_results, f"No results came back for query {query!r}."

        assert any(
            r.get("content") not in (None, "", []) for r in search_results
        ), f"None of the {len(search_results)} hits had any content."

        assert records, (
            f"records is empty even though we got {len(search_results)} hit(s)."
        )

        assert_response_matches_openapi_operation(
            body, "search", status_code="200"
        )

    def test_post_search_with_kb_filter_response_matches_spec(self) -> None:
        resp = self.search.create_search(
            query=SEARCH_QUERY,
            filters={"kb": [self.kb_id]},
            limit=5,
            timeout=self.timeout,
        )
        assert resp.status_code == 200, f"{resp.status_code}: {resp.text}"
        self._assert_search_response_ok(resp.json(), SEARCH_QUERY)

    # TODO: add connector filter support to tests
    # def test_post_search_with_connector_filter_response_matches_spec(self) -> None:
    #     resp = self.search.create_search(
    #         query=CONNECTOR_SEARCH_QUERY,
    #         filters={"apps": [CONNECTOR_APP_ID]},
    #         limit=5,
    #         timeout=self.timeout,
    #     )
    #     ...

    def test_get_search_history_response_matches_spec(self) -> None:
        self._create_search()

        resp = self.search.list_history(timeout=self.timeout)
        assert resp.status_code == 200, f"{resp.status_code}: {resp.text}"

        body = resp.json()
        applied_values = body.get("filters", {}).get("applied", {}).get("values", {})
        assert "page" in applied_values and "limit" in applied_values, (
            f"filters.applied.values missing 'page'/'limit': {applied_values!r}"
        )

        assert_response_matches_openapi_operation(
            body, "searchHistory", status_code="200"
        )

    def test_get_search_by_id_response_matches_spec(self) -> None:
        search_id = self._create_search()

        resp = self.search.get_search(search_id, timeout=self.timeout)
        assert resp.status_code == 200, f"{resp.status_code}: {resp.text}"

        body = resp.json()
        assert len(body) == 1, f"Expected exactly one row, got {len(body)}"

        for key, value in (body[0].get("records") or {}).items():
            parsed = json.loads(value)
            assert isinstance(parsed, dict), (
                f"records[{key!r}] did not decode to an object: {parsed!r}"
            )

        assert_response_matches_openapi_operation(
            body, "getSearchById", status_code="200"
        )

    def test_patch_search_share_response_matches_spec(self) -> None:
        if not SHARE_TARGET_USER_ID:
            pytest.skip("Set PIPESHUB_TEST_SHARE_TARGET_USER_ID to run this test.")

        search_id = self._create_search()

        share_resp = self.search.share_search(
            search_id,
            userIds=[SHARE_TARGET_USER_ID],
            accessLevel="read",
            timeout=self.timeout,
        )
        assert share_resp.status_code == 200, (
            f"{share_resp.status_code}: {share_resp.text}"
        )

        body = share_resp.json()
        assert body.get("_id") == search_id, (
            f"share response _id mismatch: expected {search_id!r}, got {body.get('_id')!r}"
        )

        assert_response_matches_openapi_ref(
            body, "#/components/schemas/ShareSearchResponse"
        )

    def test_archive_unarchive_lifecycle_matches_spec_and_history(self) -> None:
        before_history = self._list_active_history()
        for row in before_history:
            assert row.get("isArchived") is False, (
                f"active history returned an archived row {row.get('_id')!r}: "
                f"isArchived={row.get('isArchived')!r}"
            )

        if not before_history:
            self._create_search()
            before_history = self._list_active_history()
            assert before_history, (
                "active history is still empty after creating a new search"
            )

        search_id = before_history[0].get("_id")
        assert search_id, f"active history row is missing `_id`: {before_history[0]!r}"
        before_count = len(before_history)

        archive_resp = self.search.archive_search(search_id, timeout=self.timeout)
        assert archive_resp.status_code == 200, (
            f"{archive_resp.status_code}: {archive_resp.text}"
        )
        archive_body = archive_resp.json()

        assert archive_body.get("id") == search_id, (
            f"archive response id mismatch: expected {search_id!r}, "
            f"got {archive_body.get('id')!r}"
        )
        assert archive_body.get("status") == "archived", (
            f"status should be 'archived', got {archive_body.get('status')!r}"
        )
        assert_response_matches_openapi_operation(
            archive_body, "archiveSearch", status_code="200"
        )

        after_archive_history = self._list_active_history()
        after_archive_ids = {row.get("_id") for row in after_archive_history}
        assert search_id not in after_archive_ids, (
            f"archived search {search_id!r} should not appear in active history, "
            f"but it did"
        )
        assert len(after_archive_history) == before_count - 1, (
            f"active history count should drop by 1 after archive, "
            f"was {before_count}, now {len(after_archive_history)}"
        )

        unarchive_resp = self.search.unarchive_search(search_id, timeout=self.timeout)
        assert unarchive_resp.status_code == 200, (
            f"{unarchive_resp.status_code}: {unarchive_resp.text}"
        )
        unarchive_body = unarchive_resp.json()

        assert unarchive_body.get("id") == search_id, (
            f"unarchive response id mismatch: expected {search_id!r}, "
            f"got {unarchive_body.get('id')!r}"
        )
        assert unarchive_body.get("status") == "unarchived", (
            f"status should be 'unarchived', got {unarchive_body.get('status')!r}"
        )
        assert_response_matches_openapi_operation(
            unarchive_body, "unarchiveSearch", status_code="200"
        )

        after_unarchive_history = self._list_active_history()
        after_unarchive_ids = {row.get("_id") for row in after_unarchive_history}
        assert search_id in after_unarchive_ids, (
            f"unarchived search {search_id!r} should be back in active history, "
            f"but is missing"
        )
        assert len(after_unarchive_history) == before_count, (
            f"active history count should return to {before_count} after unarchive, "
            f"got {len(after_unarchive_history)}"
        )
        for row in after_unarchive_history:
            if row.get("_id") == search_id:
                assert row.get("isArchived") is False, (
                    f"unarchived row should have isArchived=False, "
                    f"got {row.get('isArchived')!r}"
                )
                break

    def test_patch_search_unshare_response_matches_spec(self) -> None:
        if not SHARE_TARGET_USER_ID:
            pytest.skip("Set PIPESHUB_TEST_SHARE_TARGET_USER_ID to run this test.")

        search_id = self._create_search()

        share_resp = self.search.share_search(
            search_id,
            userIds=[SHARE_TARGET_USER_ID],
            accessLevel="read",
            timeout=self.timeout,
        )
        assert share_resp.status_code == 200, (
            f"{share_resp.status_code}: {share_resp.text}"
        )

        unshare_resp = self.search.unshare_search(
            search_id,
            userIds=[SHARE_TARGET_USER_ID],
            timeout=self.timeout,
        )
        assert unshare_resp.status_code == 200, (
            f"{unshare_resp.status_code}: {unshare_resp.text}"
        )

        body = unshare_resp.json()
        assert body.get("id") == search_id, (
            f"unshare response id mismatch: expected {search_id!r}, got {body.get('id')!r}"
        )
        assert body.get("unsharedUsers") == [SHARE_TARGET_USER_ID], (
            f"unsharedUsers should echo request userIds, got "
            f"{body.get('unsharedUsers')!r}"
        )

        assert_response_matches_openapi_ref(
            body, "#/components/schemas/UnshareSearchResponse"
        )

    def test_delete_search_by_id_response_matches_spec(self) -> None:
        search_id = self._create_search()

        del_resp = self.search.delete_search(search_id, timeout=self.timeout)
        assert del_resp.status_code == 200, f"{del_resp.status_code}: {del_resp.text}"

        body = del_resp.json()
        assert body == {"message": "Search deleted successfully"}, (
            f"unexpected delete body: {body!r}"
        )

        assert_response_matches_openapi_operation(
            body, "deleteSearchById", status_code="200"
        )

        history_resp = self.search.list_history(limit=100, timeout=self.timeout)
        assert history_resp.status_code == 200, (
            f"{history_resp.status_code}: {history_resp.text}"
        )
        history_ids = {
            row.get("_id") for row in history_resp.json().get("searchHistory") or []
        }
        assert search_id not in history_ids, (
            f"deleted search {search_id!r} should not appear in history, but did"
        )

        second_resp = self.search.delete_search(search_id, timeout=self.timeout)
        assert second_resp.status_code == 404, (
            f"second delete should be 404, got "
            f"{second_resp.status_code}: {second_resp.text}"
        )

    def test_delete_search_history_response_matches_spec(self) -> None:
        # WARNING: this test wipes the test user's entire search history.
        self._create_search()

        del_resp = self.search.delete_history(timeout=self.timeout)
        assert del_resp.status_code == 200, f"{del_resp.status_code}: {del_resp.text}"

        body = del_resp.json()
        assert body == {"message": "Search history deleted successfully"}, (
            f"unexpected delete body: {body!r}"
        )

        assert_response_matches_openapi_operation(
            body, "deleteSearchHistory", status_code="200"
        )

        history_resp = self.search.list_history(limit=100, timeout=self.timeout)
        assert history_resp.status_code == 200, (
            f"{history_resp.status_code}: {history_resp.text}"
        )
        history = history_resp.json().get("searchHistory") or []
        assert history == [], f"history should be empty after wipe, got {history!r}"

        second_resp = self.search.delete_history(timeout=self.timeout)
        assert second_resp.status_code == 404, (
            f"second wipe should be 404, got "
            f"{second_resp.status_code}: {second_resp.text}"
        )

    @pytest.mark.parametrize("sort_order", ["asc", "desc"])
    def test_get_search_history_with_sort_order_variants(
        self, sort_order: str,
    ) -> None:
        self._create_search()

        resp = self.search.list_history(
            sortOrder=sort_order,
            limit=20,
            page=1,
            timeout=self.timeout,
        )
        assert resp.status_code == 200, f"{resp.status_code}: {resp.text}"
        assert_response_matches_openapi_operation(
            resp.json(), "searchHistory", status_code="200"
        )

    def test_get_search_history_with_limit_caps_rows(self) -> None:
        for _ in range(2):
            self._create_search()

        resp = self.search.list_history(limit=1, page=1, timeout=self.timeout)
        assert resp.status_code == 200, f"{resp.status_code}: {resp.text}"
        body = resp.json()
        rows = body.get("searchHistory") or []
        assert len(rows) <= 1, f"limit=1 should cap rows at 1, got {len(rows)}"
        assert_response_matches_openapi_operation(
            body, "searchHistory", status_code="200"
        )
