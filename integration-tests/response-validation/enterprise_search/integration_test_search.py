"""
Semantic Search API – Response Validation Integration Tests
===========================================================

Validates every JSON-returning search route against the response schema
declared in ``pipeshub-openapi.yaml``.

Routes covered
--------------
  POST  /api/v1/search                       search
  GET   /api/v1/search                       searchHistory
  GET   /api/v1/search/{searchId}            getSearchById
  PATCH /api/v1/search/{searchId}/share      shareSearch
  PATCH /api/v1/search/{searchId}/unshare    unshareSearch
  PATCH /api/v1/search/{searchId}/archive    archiveSearch
  PATCH /api/v1/search/{searchId}/unarchive  unarchiveSearch
  DELETE /api/v1/search/{searchId}           deleteSearchById   [destructive]
  DELETE /api/v1/search                      deleteSearchHistory [destructive]

Requires (set in integration-tests/.env.local)
-----------------------------------------------
  PIPESHUB_BASE_URL
  PIPESHUB_TEST_USER_EMAIL
  PIPESHUB_TEST_USER_PASSWORD
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Optional

import pytest
import requests

# ---------------------------------------------------------------------------
# Path setup
# ---------------------------------------------------------------------------
_IT_ROOT = Path(__file__).resolve().parents[2]
_RV_HELPER = _IT_ROOT / "response-validation" / "helper"
for _p in (_IT_ROOT, _RV_HELPER):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from response_validator import assert_openapi_response  # noqa: E402


# ---------------------------------------------------------------------------
# Module-level resource helpers
# ---------------------------------------------------------------------------

def _run_search(base_url: str, headers: dict, timeout: int) -> Optional[str]:
    """
    POST /search with a probe query.  Returns the searchId if the server saved
    the result, falls back to the most recent history entry, or returns None.
    """
    resp = requests.post(
        f"{base_url}/api/v1/search",
        headers=headers,
        json={"query": "integration test probe", "limit": 1},
        timeout=timeout,
    )
    if resp.status_code == 200:
        sid = resp.json().get("searchId")
        if sid:
            return str(sid)

    # Fallback: pick from history
    hist = requests.get(
        f"{base_url}/api/v1/search",
        headers=headers,
        params={"page": 1, "limit": 1},
        timeout=timeout,
    )
    if hist.status_code == 200:
        items = hist.json().get("searches", [])
        if items:
            return str(items[0].get("_id") or items[0].get("id") or "")
    return None


def _skip_if_none(value: Optional[str], label: str) -> None:
    if not value:
        pytest.skip(f"No {label} available — run a search via the UI first")


# ===========================================================================
# POST /api/v1/search
# ===========================================================================
@pytest.mark.integration
class TestPerformSearch:

    @pytest.fixture(autouse=True)
    def _setup(self, base_url: str, session_auth_headers: dict, timeout: int) -> None:
        self.url = f"{base_url}/api/v1/search"
        self.headers = session_auth_headers
        self.timeout = timeout

    def test_response_schema(self) -> None:
        resp = requests.post(
            self.url, headers=self.headers,
            json={"query": "company policy documents", "limit": 5},
            timeout=self.timeout,
        )
        assert resp.status_code == 200, f"{resp.status_code}: {resp.text}"
        assert_openapi_response(resp.json(), "/search", "POST")

    def test_empty_results_schema(self) -> None:
        resp = requests.post(
            self.url, headers=self.headers,
            json={"query": "xyzzy_no_match_8f3k2p", "limit": 1},
            timeout=self.timeout,
        )
        assert resp.status_code == 200, f"{resp.status_code}: {resp.text}"
        assert_openapi_response(resp.json(), "/search", "POST")


# ===========================================================================
# GET /api/v1/search  (history)
# ===========================================================================
@pytest.mark.integration
class TestGetSearchHistory:

    @pytest.fixture(autouse=True)
    def _setup(self, base_url: str, session_auth_headers: dict, timeout: int) -> None:
        self.url = f"{base_url}/api/v1/search"
        self.headers = session_auth_headers
        self.timeout = timeout

    def test_response_schema(self) -> None:
        resp = requests.get(self.url, headers=self.headers, timeout=self.timeout)
        assert resp.status_code == 200, f"{resp.status_code}: {resp.text}"
        assert_openapi_response(resp.json(), "/search", "GET")

    def test_pagination_params(self) -> None:
        resp = requests.get(
            self.url, headers=self.headers,
            params={"page": 1, "limit": 5}, timeout=self.timeout,
        )
        assert resp.status_code == 200, f"{resp.status_code}: {resp.text}"
        assert_openapi_response(resp.json(), "/search", "GET")


# ===========================================================================
# GET /api/v1/search/{searchId}
# ===========================================================================
@pytest.mark.integration
class TestGetSearchById:

    @pytest.fixture(autouse=True)
    def _setup(self, base_url: str, session_auth_headers: dict, timeout: int) -> None:
        self.base_url = base_url
        self.headers = session_auth_headers
        self.timeout = timeout
        search_id = _run_search(base_url, session_auth_headers, timeout)
        _skip_if_none(search_id, "search history")
        self.search_id = search_id
        self.url = f"{base_url}/api/v1/search/{search_id}"

    def test_response_schema(self) -> None:
        resp = requests.get(self.url, headers=self.headers, timeout=self.timeout)
        assert resp.status_code == 200, f"{resp.status_code}: {resp.text}"
        assert_openapi_response(resp.json(), "/search/{searchId}", "GET")

    def test_unknown_id_returns_404(self) -> None:
        url = f"{self.base_url}/api/v1/search/000000000000000000000000"
        resp = requests.get(url, headers=self.headers, timeout=self.timeout)
        assert resp.status_code == 404, f"Expected 404, got {resp.status_code}"


# ===========================================================================
# PATCH archive + PATCH unarchive  (reversible pair)
# ===========================================================================
@pytest.mark.integration
class TestArchiveUnarchiveSearch:

    @pytest.fixture(autouse=True)
    def _setup(self, base_url: str, session_auth_headers: dict, timeout: int) -> None:
        search_id = _run_search(base_url, session_auth_headers, timeout)
        _skip_if_none(search_id, "search history")
        self.archive_url = f"{base_url}/api/v1/search/{search_id}/archive"
        self.unarchive_url = f"{base_url}/api/v1/search/{search_id}/unarchive"
        self.headers = session_auth_headers
        self.timeout = timeout

    def test_archive_response_schema(self) -> None:
        try:
            resp = requests.patch(
                self.archive_url, headers=self.headers, timeout=self.timeout
            )
            assert resp.status_code == 200, f"{resp.status_code}: {resp.text}"
            assert_openapi_response(resp.json(), "/search/{searchId}/archive", "PATCH")
        finally:
            requests.patch(self.unarchive_url, headers=self.headers, timeout=self.timeout)

    def test_unarchive_response_schema(self) -> None:
        requests.patch(self.archive_url, headers=self.headers, timeout=self.timeout)
        resp = requests.patch(
            self.unarchive_url, headers=self.headers, timeout=self.timeout
        )
        assert resp.status_code == 200, f"{resp.status_code}: {resp.text}"
        assert_openapi_response(resp.json(), "/search/{searchId}/unarchive", "PATCH")


# ===========================================================================
# PATCH share + PATCH unshare  (reversible pair)
# ===========================================================================
@pytest.mark.integration
class TestShareUnshareSearch:

    # Set to real user IDs in your test org to exercise sharing fully.
    _USER_IDS: list[str] = []

    @pytest.fixture(autouse=True)
    def _setup(self, base_url: str, session_auth_headers: dict, timeout: int) -> None:
        search_id = _run_search(base_url, session_auth_headers, timeout)
        _skip_if_none(search_id, "search history")
        self.share_url = f"{base_url}/api/v1/search/{search_id}/share"
        self.unshare_url = f"{base_url}/api/v1/search/{search_id}/unshare"
        self.headers = session_auth_headers
        self.timeout = timeout

    def test_share_response_schema(self) -> None:
        resp = requests.patch(
            self.share_url, headers=self.headers,
            json={"userIds": self._USER_IDS}, timeout=self.timeout,
        )
        assert resp.status_code == 200, f"{resp.status_code}: {resp.text}"
        assert_openapi_response(resp.json(), "/search/{searchId}/share", "PATCH")

    def test_unshare_response_schema(self) -> None:
        resp = requests.patch(
            self.unshare_url, headers=self.headers,
            json={"userIds": self._USER_IDS}, timeout=self.timeout,
        )
        assert resp.status_code == 200, f"{resp.status_code}: {resp.text}"
        assert_openapi_response(resp.json(), "/search/{searchId}/unshare", "PATCH")


# ===========================================================================
# DELETE /api/v1/search/{searchId}   [destructive]
# ===========================================================================
@pytest.mark.integration
@pytest.mark.destructive
class TestDeleteSearchById:
    """Only runs with ``pytest -m destructive``. Do NOT run against production."""

    @pytest.fixture(autouse=True)
    def _setup(self, base_url: str, session_auth_headers: dict, timeout: int) -> None:
        search_id = _run_search(base_url, session_auth_headers, timeout)
        _skip_if_none(search_id, "search history")
        self.url = f"{base_url}/api/v1/search/{search_id}"
        self.headers = session_auth_headers
        self.timeout = timeout

    def test_response_schema(self) -> None:
        resp = requests.delete(self.url, headers=self.headers, timeout=self.timeout)
        assert resp.status_code == 200, f"{resp.status_code}: {resp.text}"
        assert_openapi_response(resp.json(), "/search/{searchId}", "DELETE")


# ===========================================================================
# DELETE /api/v1/search   [destructive]
# ===========================================================================
@pytest.mark.integration
@pytest.mark.destructive
class TestDeleteAllSearchHistory:
    """
    Purges the entire search history.  Irreversible.
    Only runs with ``pytest -m destructive`` against a throwaway account.
    """

    @pytest.fixture(autouse=True)
    def _setup(self, base_url: str, session_auth_headers: dict, timeout: int) -> None:
        self.url = f"{base_url}/api/v1/search"
        self.headers = session_auth_headers
        self.timeout = timeout

    def test_response_schema(self) -> None:
        resp = requests.delete(self.url, headers=self.headers, timeout=self.timeout)
        assert resp.status_code == 200, f"{resp.status_code}: {resp.text}"
        assert_openapi_response(resp.json(), "/search", "DELETE")
