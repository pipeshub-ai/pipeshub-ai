"""Enterprise search response-schema integration tests.

Set SEARCH_QUERY to a question that has answers in your data.
Set PIPESHUB_TEST_SHARE_TARGET_USER_ID to a real user id in your organisation
(share/unshare tests skip when it is unset).

``PIPESHUB_TEST_STREAM_TIMEOUT`` (optional): override seconds for SSE reads only
in this module; otherwise ``max(PIPESHUB_TEST_TIMEOUT, 120)``.
"""

from __future__ import annotations

import json
import os
import uuid

import pytest
import requests

from openapi_search_validator import (
    assert_matches_component_schema,
    assert_response_matches_spec,
)
from pipeshub_client import PipeshubClient

SEARCH_QUERY = "every year asana undertakes which exercise?"
# TODO: add connector filter support to tests
# CONNECTOR_SEARCH_QUERY = "What are some new news?"
# CONNECTOR_APP_ID = "ed6d6cc4-70bd-4838-9aeb-488e910c833a"
SHARE_TARGET_USER_ID = os.getenv("PIPESHUB_TEST_SHARE_TARGET_USER_ID", "").strip()

# Cap for runaway SSE; high enough for verbose dev streams before `complete`.
_SSE_MAX_EVENTS = 10_000
class _BaseEnterpriseSearchIntegration:

    @pytest.fixture(autouse=True)
    def _setup(self, pipeshub_client: PipeshubClient, session_kb: dict) -> None:
        base_url = pipeshub_client.base_url
        self.base_url = base_url
        self.url = f"{base_url}/api/v1/search"
        self.kb_id = session_kb["kb_id"]
        self.conversation_stream_url = f"{base_url}/api/v1/conversations/stream"
        self.message_stream_url_tpl = (
            f"{base_url}/api/v1/conversations/{{conversationId}}/messages/stream"
        )
        self.conversations_base_url = f"{base_url}/api/v1/conversations"
        self.archived_conversations_search_url = (
            f"{base_url}/api/v1/conversations/show/archives/search"
        )
        self.conversations_url = self.conversations_base_url
        self.conversations_list_url = self.conversations_base_url
        self.regenerate_url_tpl = (
            f"{base_url}/api/v1/conversations/{{conversationId}}/message/{{messageId}}/regenerate"
        )
        self.feedback_url_tpl = (
            f"{base_url}/api/v1/conversations/{{conversationId}}/message/{{messageId}}/feedback"
        )
        self.headers = pipeshub_client.auth_headers
        self.timeout = int(os.getenv("PIPESHUB_TEST_TIMEOUT", "60"))
        stream_override = os.getenv("PIPESHUB_TEST_STREAM_TIMEOUT", "").strip()
        self.stream_timeout = (
            int(stream_override)
            if stream_override
            else max(self.timeout, 120)
        )


# ============================================================================
# Router: createSemanticSearchRouter
# Routes mounted at /api/v1/search
# ============================================================================
@pytest.mark.integration
class TestSemanticSearch(_BaseEnterpriseSearchIntegration):

    def _assert_search_response_ok(self, body: dict, query: str) -> None:
        search_response = body.get("searchResponse") or {}
        search_results = search_response.get("searchResults") or []
        records = search_response.get("records") or []

        # Search must actually return results.
        assert search_results, f"No results came back for query {query!r}."

        # At least one result should have actual text in it.
        assert any(
            r.get("content") not in (None, "", []) for r in search_results
        ), f"None of the {len(search_results)} hits had any content."

        # Records list should not be empty when we have hits.
        assert records, (
            f"records is empty even though we got {len(search_results)} hit(s)."
        )

        assert_response_matches_spec(body, "/search", "POST", 200)

    def test_post_search_with_kb_filter_response_matches_spec(self) -> None:
        resp = requests.post(
            self.url,
            headers=self.headers,
            json={
                "query": SEARCH_QUERY,
                "filters": {"kb": [self.kb_id]},
                "limit": 5,
            },
            timeout=self.timeout,
        )
        assert resp.status_code == 200, f"{resp.status_code}: {resp.text}"
        self._assert_search_response_ok(resp.json(), SEARCH_QUERY)

    # TODO: add connector filter support to tests
    # def test_post_search_with_connector_filter_response_matches_spec(self) -> None:
    #     resp = requests.post(
    #         self.url,
    #         headers=self.headers,
    #         json={
    #             "query": CONNECTOR_SEARCH_QUERY,
    #             "filters": {"apps": [CONNECTOR_APP_ID]},
    #             "limit": 5,
    #         },
    #         timeout=self.timeout,
    #     )
    #     assert resp.status_code == 200, f"{resp.status_code}: {resp.text}"
    #     self._assert_search_response_ok(resp.json(), CONNECTOR_SEARCH_QUERY)

    def test_get_search_history_response_matches_spec(self) -> None:
        # Create a search so history has something to return.
        post_resp = requests.post(
            self.url,
            headers=self.headers,
            json={"query": SEARCH_QUERY, "limit": 5},
            timeout=self.timeout,
        )
        assert post_resp.status_code == 200, f"{post_resp.status_code}: {post_resp.text}"

        get_resp = requests.get(
            self.url,
            headers=self.headers,
            timeout=self.timeout,
        )
        assert get_resp.status_code == 200, f"{get_resp.status_code}: {get_resp.text}"

        body = get_resp.json()

        # Page and limit should always come back in applied filters.
        applied_values = body.get("filters", {}).get("applied", {}).get("values", {})
        assert "page" in applied_values and "limit" in applied_values, (
            f"filters.applied.values missing 'page'/'limit': {applied_values!r}"
        )

        assert_response_matches_spec(body, "/search", "GET", 200)

    def test_get_search_by_id_response_matches_spec(self) -> None:
        # Create a search so we have an id to fetch.
        post_resp = requests.post(
            self.url,
            headers=self.headers,
            json={"query": SEARCH_QUERY, "limit": 5},
            timeout=self.timeout,
        )
        assert post_resp.status_code == 200, f"{post_resp.status_code}: {post_resp.text}"
        search_id = post_resp.json().get("searchId")
        assert search_id, "POST /search response missing searchId"

        get_resp = requests.get(
            f"{self.url}/{search_id}",
            headers=self.headers,
            timeout=self.timeout,
        )
        assert get_resp.status_code == 200, f"{get_resp.status_code}: {get_resp.text}"

        body = get_resp.json()

        # Response is a list with exactly one row.
        assert len(body) == 1, f"Expected exactly one row, got {len(body)}"

        # Each record value is a JSON string that should parse to an object.
        for key, value in (body[0].get("records") or {}).items():
            parsed = json.loads(value)
            assert isinstance(parsed, dict), (
                f"records[{key!r}] did not decode to an object: {parsed!r}"
            )

        assert_response_matches_spec(body, "/search/{searchId}", "GET", 200)

    def test_patch_search_share_response_matches_spec(self) -> None:
        if not SHARE_TARGET_USER_ID:
            pytest.skip("Set PIPESHUB_TEST_SHARE_TARGET_USER_ID to run this test.")

        # Create a search to share.
        post_resp = requests.post(
            self.url,
            headers=self.headers,
            json={"query": SEARCH_QUERY, "limit": 5},
            timeout=self.timeout,
        )
        assert post_resp.status_code == 200, f"{post_resp.status_code}: {post_resp.text}"
        search_id = post_resp.json().get("searchId")
        assert search_id, "POST /search response missing searchId"

        share_resp = requests.patch(
            f"{self.url}/{search_id}/share",
            headers=self.headers,
            json={"userIds": [SHARE_TARGET_USER_ID], "accessLevel": "read"},
            timeout=self.timeout,
        )
        assert share_resp.status_code == 200, (
            f"{share_resp.status_code}: {share_resp.text}"
        )

        body = share_resp.json()

        # Share echoes back the search id under `_id`.
        assert body.get("_id") == search_id, (
            f"share response _id mismatch: expected {search_id!r}, got {body.get('_id')!r}"
        )

        assert_response_matches_spec(body, "/search/{searchId}/share", "PATCH", 200)

    def test_archive_unarchive_lifecycle_matches_spec_and_history(self) -> None:
        def list_active_history() -> list:
            # Ask for a big page so a recently used search is on it.
            resp = requests.get(
                self.url,
                headers=self.headers,
                params={"limit": 100},
                timeout=self.timeout,
            )
            assert resp.status_code == 200, f"{resp.status_code}: {resp.text}"
            return resp.json().get("searchHistory") or []

        # Step 1: list history and pick a search to archive.
        before_history = list_active_history()
        # The active history should not include any archived rows.
        for row in before_history:
            assert row.get("isArchived") is False, (
                f"active history returned an archived row {row.get('_id')!r}: "
                f"isArchived={row.get('isArchived')!r}"
            )

        if not before_history:
            # No prior searches available, so create one to operate on.
            post_resp = requests.post(
                self.url,
                headers=self.headers,
                json={"query": SEARCH_QUERY, "limit": 5},
                timeout=self.timeout,
            )
            assert post_resp.status_code == 200, (
                f"{post_resp.status_code}: {post_resp.text}"
            )
            assert post_resp.json().get("searchId"), (
                "POST /search response missing searchId"
            )
            before_history = list_active_history()
            assert before_history, (
                "active history is still empty after creating a new search"
            )

        # Pick the most recent active search for the lifecycle.
        search_id = before_history[0].get("_id")
        assert search_id, f"active history row is missing `_id`: {before_history[0]!r}"
        before_count = len(before_history)

        # Step 2: archive the search and check the response shape.
        archive_resp = requests.patch(
            f"{self.url}/{search_id}/archive",
            headers=self.headers,
            timeout=self.timeout,
        )
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
        assert_response_matches_spec(
            archive_body, "/search/{searchId}/archive", "PATCH", 200
        )

        # Step 3: list history again — the archived search must be gone.
        after_archive_history = list_active_history()
        after_archive_ids = {row.get("_id") for row in after_archive_history}
        assert search_id not in after_archive_ids, (
            f"archived search {search_id!r} should not appear in active history, "
            f"but it did"
        )
        assert len(after_archive_history) == before_count - 1, (
            f"active history count should drop by 1 after archive, "
            f"was {before_count}, now {len(after_archive_history)}"
        )

        # Step 4: unarchive the search and check the response shape.
        unarchive_resp = requests.patch(
            f"{self.url}/{search_id}/unarchive",
            headers=self.headers,
            timeout=self.timeout,
        )
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
        assert_response_matches_spec(
            unarchive_body, "/search/{searchId}/unarchive", "PATCH", 200
        )

        # Step 5: list history one more time — the search is back and active.
        after_unarchive_history = list_active_history()
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

        # Create and share a search so we have someone to unshare.
        post_resp = requests.post(
            self.url,
            headers=self.headers,
            json={"query": SEARCH_QUERY, "limit": 5},
            timeout=self.timeout,
        )
        assert post_resp.status_code == 200, f"{post_resp.status_code}: {post_resp.text}"
        search_id = post_resp.json().get("searchId")
        assert search_id, "POST /search response missing searchId"

        share_resp = requests.patch(
            f"{self.url}/{search_id}/share",
            headers=self.headers,
            json={"userIds": [SHARE_TARGET_USER_ID], "accessLevel": "read"},
            timeout=self.timeout,
        )
        assert share_resp.status_code == 200, (
            f"{share_resp.status_code}: {share_resp.text}"
        )

        unshare_resp = requests.patch(
            f"{self.url}/{search_id}/unshare",
            headers=self.headers,
            json={"userIds": [SHARE_TARGET_USER_ID]},
            timeout=self.timeout,
        )
        assert unshare_resp.status_code == 200, (
            f"{unshare_resp.status_code}: {unshare_resp.text}"
        )

        body = unshare_resp.json()

        # Unshare echoes back the search id and the user ids we sent.
        assert body.get("id") == search_id, (
            f"unshare response id mismatch: expected {search_id!r}, got {body.get('id')!r}"
        )
        assert body.get("unsharedUsers") == [SHARE_TARGET_USER_ID], (
            f"unsharedUsers should echo request userIds, got "
            f"{body.get('unsharedUsers')!r}"
        )

        assert_response_matches_spec(
            body, "/search/{searchId}/unshare", "PATCH", 200
        )

    def test_delete_search_by_id_response_matches_spec(self) -> None:
        # Create a search so we have one to delete.
        post_resp = requests.post(
            self.url,
            headers=self.headers,
            json={"query": SEARCH_QUERY, "limit": 5},
            timeout=self.timeout,
        )
        assert post_resp.status_code == 200, f"{post_resp.status_code}: {post_resp.text}"
        search_id = post_resp.json().get("searchId")
        assert search_id, "POST /search response missing searchId"

        # Delete that one search.
        del_resp = requests.delete(
            f"{self.url}/{search_id}",
            headers=self.headers,
            timeout=self.timeout,
        )
        assert del_resp.status_code == 200, f"{del_resp.status_code}: {del_resp.text}"

        body = del_resp.json()

        # Body should just be the success message and nothing else.
        assert body == {"message": "Search deleted successfully"}, (
            f"unexpected delete body: {body!r}"
        )

        assert_response_matches_spec(body, "/search/{searchId}", "DELETE", 200)

        # The search should be gone from the history list.
        history_resp = requests.get(
            self.url,
            headers=self.headers,
            params={"limit": 100},
            timeout=self.timeout,
        )
        assert history_resp.status_code == 200, (
            f"{history_resp.status_code}: {history_resp.text}"
        )
        history_ids = {
            row.get("_id") for row in history_resp.json().get("searchHistory") or []
        }
        assert search_id not in history_ids, (
            f"deleted search {search_id!r} should not appear in history, but did"
        )

        # Deleting the same search a second time should now return a 404.
        second_resp = requests.delete(
            f"{self.url}/{search_id}",
            headers=self.headers,
            timeout=self.timeout,
        )
        assert second_resp.status_code == 404, (
            f"second delete should be 404, got "
            f"{second_resp.status_code}: {second_resp.text}"
        )

    def test_delete_search_history_response_matches_spec(self) -> None:
        # WARNING: this test wipes the test user's entire search history.
        # Make sure at least one search exists before we wipe.
        post_resp = requests.post(
            self.url,
            headers=self.headers,
            json={"query": SEARCH_QUERY, "limit": 5},
            timeout=self.timeout,
        )
        assert post_resp.status_code == 200, f"{post_resp.status_code}: {post_resp.text}"

        # Wipe everything owned by, or shared with, the test user.
        del_resp = requests.delete(
            self.url,
            headers=self.headers,
            timeout=self.timeout,
        )
        assert del_resp.status_code == 200, f"{del_resp.status_code}: {del_resp.text}"

        body = del_resp.json()

        # Body should just be the success message and nothing else.
        assert body == {"message": "Search history deleted successfully"}, (
            f"unexpected delete body: {body!r}"
        )

        assert_response_matches_spec(body, "/search", "DELETE", 200)

        # History should now be empty.
        history_resp = requests.get(
            self.url,
            headers=self.headers,
            params={"limit": 100},
            timeout=self.timeout,
        )
        assert history_resp.status_code == 200, (
            f"{history_resp.status_code}: {history_resp.text}"
        )
        history = history_resp.json().get("searchHistory") or []
        assert history == [], f"history should be empty after wipe, got {history!r}"

        # Wiping again when nothing matches should return a 404.
        second_resp = requests.delete(
            self.url,
            headers=self.headers,
            timeout=self.timeout,
        )
        assert second_resp.status_code == 404, (
            f"second wipe should be 404, got "
            f"{second_resp.status_code}: {second_resp.text}"
        )

    # Asking for history sorted oldest first or newest first should both work.
    @pytest.mark.parametrize("sort_order", ["asc", "desc"])
    def test_get_search_history_with_sort_order_variants(
        self, sort_order: str,
    ) -> None:
        post_resp = requests.post(
            self.url,
            headers=self.headers,
            json={"query": SEARCH_QUERY, "limit": 5},
            timeout=self.timeout,
        )
        assert post_resp.status_code == 200, (
            f"{post_resp.status_code}: {post_resp.text}"
        )

        resp = requests.get(
            self.url,
            headers=self.headers,
            params={"sortOrder": sort_order, "limit": 20, "page": 1},
            timeout=self.timeout,
        )
        assert resp.status_code == 200, f"{resp.status_code}: {resp.text}"
        assert_response_matches_spec(resp.json(), "/search", "GET", 200)

    # Asking for one row of history caps how many come back.
    def test_get_search_history_with_limit_caps_rows(self) -> None:
        for _ in range(2):
            post_resp = requests.post(
                self.url,
                headers=self.headers,
                json={"query": SEARCH_QUERY, "limit": 5},
                timeout=self.timeout,
            )
            assert post_resp.status_code == 200, (
                f"{post_resp.status_code}: {post_resp.text}"
            )

        resp = requests.get(
            self.url,
            headers=self.headers,
            params={"limit": 1, "page": 1},
            timeout=self.timeout,
        )
        assert resp.status_code == 200, f"{resp.status_code}: {resp.text}"
        body = resp.json()
        rows = body.get("searchHistory") or []
        assert len(rows) <= 1, f"limit=1 should cap rows at 1, got {len(rows)}"
        assert_response_matches_spec(body, "/search", "GET", 200)



