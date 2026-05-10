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
