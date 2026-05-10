"""Enterprise search response-schema integration tests.

Edit `SEARCH_QUERY` to a query that matches data in the local server before
running. Schema checks pass regardless of whether searches return hits — the
spec only requires envelope-level fields to be shaped correctly.
"""

from __future__ import annotations

import pytest
import requests

from openapi_search_validator import assert_response_matches_spec

SEARCH_QUERY = "placeholder query"


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
        assert_response_matches_spec(resp.json(), "/search", "POST", 200)

    def test_get_search_by_id_response_matches_spec(self) -> None:
        # Create a search so we have a stable id to fetch back.
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
        # The handler uses Model.find() rather than findOne(), so the wire
        # format is an array. Spec encodes that as PersistedSemanticSearchEnvelope.
        assert isinstance(body, list), f"Expected array response, got {type(body).__name__}"
        assert len(body) == 1, f"Expected exactly one document for round-tripped id, got {len(body)}"

        # `records` is a map of source-record id to JSON.stringify(record).
        # Verify the stringification contract: every value parses back to a dict.
        import json as _json
        records = body[0].get("records", {})
        assert isinstance(records, dict), f"Expected records to be an object, got {type(records).__name__}"
        for key, value in records.items():
            assert isinstance(value, str), (
                f"records[{key!r}] should be a JSON-stringified record, "
                f"got {type(value).__name__}"
            )
            parsed = _json.loads(value)
            assert isinstance(parsed, dict), (
                f"records[{key!r}] string did not decode to an object: {parsed!r}"
            )

        assert_response_matches_spec(body, "/search/{searchId}", "GET", 200)
