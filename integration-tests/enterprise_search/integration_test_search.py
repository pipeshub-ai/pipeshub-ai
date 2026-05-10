"""POST /api/v1/search response-schema integration test.

Edit `SEARCH_QUERY` to a query that matches data in the local server before
running. The schema check passes regardless of whether the search returns
hits — the spec's `SemanticSearchExecuteResponse` only requires `searchId`
and a `searchResponse` envelope.
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
