"""Enterprise search response-schema integration tests.

Set SEARCH_QUERY to a question that has answers in your data.
Set SHARE_TARGET_USER_ID to a real user id in your organisation.
"""

from __future__ import annotations

import pytest
import requests

from openapi_search_validator import (
    assert_matches_component_schema,
    assert_response_matches_spec,
)

SEARCH_QUERY = "every year asana undertakes which exercise?"
SHARE_TARGET_USER_ID = "69fb98146a623870d20860bb"


@pytest.mark.integration
class TestSemanticSearch:

    @pytest.fixture(autouse=True)
    def _setup(self, base_url: str, session_auth_headers: dict, timeout: int) -> None:
        self.url = f"{base_url}/api/v1/search"
        self.headers = session_auth_headers
        self.timeout = timeout

    def test_post_search_response_matches_spec(self) -> None:
        resp = requests.post(
            self.url,
            headers=self.headers,
            json={"query": SEARCH_QUERY, "limit": 5},
            timeout=self.timeout,
        )
        assert resp.status_code == 200, f"{resp.status_code}: {resp.text}"

        body = resp.json()
        search_response = body.get("searchResponse") or {}
        search_results = search_response.get("searchResults")
        records = search_response.get("records")

        # Search must actually return results.
        assert isinstance(search_results, list), (
            f"searchResults should be a list, got "
            f"{type(search_results).__name__}: {search_results!r}"
        )
        assert len(search_results) > 0, (
            f"No results came back for query {SEARCH_QUERY!r}."
        )

        # Each result should look the way the spec says.
        for idx, hit in enumerate(search_results):
            try:
                assert_matches_component_schema(hit, "SemanticSearchHit")
            except AssertionError as exc:
                raise AssertionError(
                    f"searchResults[{idx}] does not match SemanticSearchHit:\n{exc}"
                ) from exc

        # At least one result should have actual text in it.
        hits_with_content = [
            r for r in search_results
            if r.get("content") not in (None, "", [])
        ]
        assert hits_with_content, (
            f"None of the {len(search_results)} hits had any content."
        )

        # Records list should not be empty when we have hits.
        assert isinstance(records, list), (
            f"records should be a list, got {type(records).__name__}: {records!r}"
        )
        assert len(records) > 0, (
            f"records is empty even though we got {len(search_results)} hit(s)."
        )

        # Each record should look the way the spec says.
        for idx, record in enumerate(records):
            try:
                assert_matches_component_schema(record, "SemanticSearchGraphRecord")
            except AssertionError as exc:
                raise AssertionError(
                    f"records[{idx}] does not match SemanticSearchGraphRecord:\n{exc}"
                ) from exc

        assert_response_matches_spec(body, "/search", "POST", 200)

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
        applied = body.get("filters", {}).get("applied", {})
        assert "page" in applied.get("values", {}), (
            f"filters.applied.values missing 'page': {applied!r}"
        )
        assert "limit" in applied.get("values", {}), (
            f"filters.applied.values missing 'limit': {applied!r}"
        )

        # Citation ids should be plain id strings, not full objects.
        for row in body.get("searchHistory", []):
            for cid in row.get("citationIds", []):
                assert isinstance(cid, str), (
                    f"citationIds entry should be a string, got "
                    f"{type(cid).__name__}: {cid!r}"
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

        # Response is a list, not a single object.
        assert isinstance(body, list), f"Expected a list, got {type(body).__name__}"
        assert len(body) == 1, f"Expected exactly one row, got {len(body)}"

        # Each record value is a JSON string that should parse to an object.
        import json as _json
        records = body[0].get("records", {})
        assert isinstance(records, dict), (
            f"Expected records to be an object, got {type(records).__name__}"
        )
        for key, value in records.items():
            assert isinstance(value, str), (
                f"records[{key!r}] should be a string, got {type(value).__name__}"
            )
            parsed = _json.loads(value)
            assert isinstance(parsed, dict), (
                f"records[{key!r}] did not decode to an object: {parsed!r}"
            )

        assert_response_matches_spec(body, "/search/{searchId}", "GET", 200)

    def test_patch_search_share_response_matches_spec(self) -> None:
        if SHARE_TARGET_USER_ID == "000000000000000000000000":
            pytest.skip("Set SHARE_TARGET_USER_ID at the top of this file.")

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

        # Citation ids should be plain id strings, not full objects.
        for cid in body.get("citationIds", []):
            assert isinstance(cid, str), (
                f"citationIds entry should be a string, got "
                f"{type(cid).__name__}: {cid!r}"
            )

        # Share returns `_id`, not `id`.
        assert "_id" in body, f"share response missing `_id`: {body!r}"
        assert "id" not in body, (
            f"share response should not have `id`: {body!r}"
        )

        # Each sharedWith entry should look the way the spec says.
        for idx, entry in enumerate(body.get("sharedWith", [])):
            try:
                assert_matches_component_schema(
                    entry, "PersistedSemanticSearchSharedWithEntry"
                )
            except AssertionError as exc:
                raise AssertionError(
                    f"sharedWith[{idx}] does not match "
                    f"PersistedSemanticSearchSharedWithEntry:\n{exc}"
                ) from exc

        assert_response_matches_spec(body, "/search/{searchId}/share", "PATCH", 200)

    def test_archive_unarchive_lifecycle_matches_spec_and_history(self) -> None:
        from datetime import datetime

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
        archived_by = archive_body.get("archivedBy")
        assert isinstance(archived_by, str) and archived_by, (
            f"archivedBy should be a non-empty string, got {archived_by!r}"
        )
        archived_at = archive_body.get("archivedAt")
        assert isinstance(archived_at, str) and archived_at, (
            f"archivedAt should be a non-empty string, got {archived_at!r}"
        )
        datetime.fromisoformat(archived_at.replace("Z", "+00:00"))
        archive_meta = archive_body.get("meta") or {}
        assert isinstance(archive_meta, dict), (
            f"meta should be an object, got "
            f"{type(archive_meta).__name__}: {archive_meta!r}"
        )
        assert (
            isinstance(archive_meta.get("timestamp"), str)
            and archive_meta.get("timestamp")
        ), f"meta.timestamp should be a non-empty string, got {archive_meta.get('timestamp')!r}"
        datetime.fromisoformat(archive_meta["timestamp"].replace("Z", "+00:00"))
        assert (
            isinstance(archive_meta.get("duration"), int)
            and archive_meta.get("duration") >= 0
        ), f"meta.duration should be a non-negative integer, got {archive_meta.get('duration')!r}"
        assert "query" not in archive_body, (
            f"archive response should not include `query`: {archive_body!r}"
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
        unarchived_by = unarchive_body.get("unarchivedBy")
        assert isinstance(unarchived_by, str) and unarchived_by, (
            f"unarchivedBy should be a non-empty string, got {unarchived_by!r}"
        )
        unarchived_at = unarchive_body.get("unarchivedAt")
        assert isinstance(unarchived_at, str) and unarchived_at, (
            f"unarchivedAt should be a non-empty string, got {unarchived_at!r}"
        )
        datetime.fromisoformat(unarchived_at.replace("Z", "+00:00"))
        unarchive_meta = unarchive_body.get("meta") or {}
        assert isinstance(unarchive_meta, dict), (
            f"meta should be an object, got "
            f"{type(unarchive_meta).__name__}: {unarchive_meta!r}"
        )
        assert (
            isinstance(unarchive_meta.get("timestamp"), str)
            and unarchive_meta.get("timestamp")
        ), f"meta.timestamp should be a non-empty string, got {unarchive_meta.get('timestamp')!r}"
        datetime.fromisoformat(unarchive_meta["timestamp"].replace("Z", "+00:00"))
        assert (
            isinstance(unarchive_meta.get("duration"), int)
            and unarchive_meta.get("duration") >= 0
        ), f"meta.duration should be a non-negative integer, got {unarchive_meta.get('duration')!r}"
        assert "query" not in unarchive_body, (
            f"unarchive response should not include `query`: {unarchive_body!r}"
        )
        assert "archivedBy" not in unarchive_body, (
            f"unarchive response should not include `archivedBy`: {unarchive_body!r}"
        )
        assert "archivedAt" not in unarchive_body, (
            f"unarchive response should not include `archivedAt`: {unarchive_body!r}"
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
        if SHARE_TARGET_USER_ID == "000000000000000000000000":
            pytest.skip("Set SHARE_TARGET_USER_ID at the top of this file.")

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

        # Unshare returns `id`, not `_id`.
        assert "id" in body, f"unshare response missing `id`: {body!r}"
        assert "_id" not in body, (
            f"unshare response should not have `_id`: {body!r}"
        )
        # Unshare response should not include the full search document.
        assert "query" not in body, (
            f"unshare response should not include `query`: {body!r}"
        )

        # `unsharedUsers` should echo back the user ids we sent.
        assert body.get("unsharedUsers") == [SHARE_TARGET_USER_ID], (
            f"unsharedUsers should echo request userIds, got "
            f"{body.get('unsharedUsers')!r}"
        )

        # Each sharedWith entry should look the way the spec says.
        for idx, entry in enumerate(body.get("sharedWith", [])):
            try:
                assert_matches_component_schema(
                    entry, "PersistedSemanticSearchSharedWithEntry"
                )
            except AssertionError as exc:
                raise AssertionError(
                    f"sharedWith[{idx}] does not match "
                    f"PersistedSemanticSearchSharedWithEntry:\n{exc}"
                ) from exc

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
        assert isinstance(body, dict), f"Expected an object, got {type(body).__name__}"
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
        assert isinstance(body, dict), f"Expected an object, got {type(body).__name__}"
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
