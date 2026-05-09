"""Search API response-schema integration tests."""

from __future__ import annotations

import base64
import json
from typing import Optional

import pytest
import requests

from openapi_validator import assert_openapi_response

# Match seeded KB doc (see conftest _seed_kb_doc_from_url). Update if seed URL changes.
IT_SEARCH_QUERY = "Every year Asana undertakes which exercise?"


def _self_user_id_from_jwt(access_token: str) -> str:
    seg = access_token.split(".")[1]
    seg += "=" * (-len(seg) % 4)
    payload = json.loads(base64.urlsafe_b64decode(seg))
    uid = payload.get("userId")
    if not uid:
        pytest.skip("JWT payload has no userId; cannot exercise share/unshare")
    return str(uid)


def _run_search(base_url: str, headers: dict, timeout: int) -> Optional[str]:
    resp = requests.post(
        f"{base_url}/api/v1/search",
        headers=headers,
        json={"query": IT_SEARCH_QUERY, "limit": 1},
        timeout=timeout,
    )
    if resp.status_code == 200:
        sid = resp.json().get("searchId")
        if sid:
            return str(sid)

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
            json={"query": IT_SEARCH_QUERY, "limit": 5},
            timeout=self.timeout,
        )
        assert resp.status_code == 200, f"{resp.status_code}: {resp.text}"
        assert_openapi_response(
            resp.json(), "/search", "POST",
        )

    def test_empty_results_schema(self) -> None:
        resp = requests.post(
            self.url, headers=self.headers,
            json={"query": "xyzzy_no_match_8f3k2p", "limit": 1},
            timeout=self.timeout,
        )
        assert resp.status_code == 200, f"{resp.status_code}: {resp.text}"
        assert_openapi_response(
            resp.json(), "/search", "POST",
        )


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
        assert_openapi_response(
            resp.json(), "/search", "GET",
        )

    def test_pagination_params(self) -> None:
        resp = requests.get(
            self.url, headers=self.headers,
            params={"page": 1, "limit": 5}, timeout=self.timeout,
        )
        assert resp.status_code == 200, f"{resp.status_code}: {resp.text}"
        assert_openapi_response(
            resp.json(), "/search", "GET",
        )


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
        assert_openapi_response(
            resp.json(), "/search/{searchId}", "GET",
        )

    def test_unknown_id_returns_empty_list(self) -> None:
        # Server returns 200 + [] for unknown searchId (not 404).
        url = f"{self.base_url}/api/v1/search/000000000000000000000000"
        resp = requests.get(url, headers=self.headers, timeout=self.timeout)
        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
        assert resp.json() == [], f"Expected empty list, got {resp.text!r}"


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

    @pytest.fixture(autouse=True)
    def _setup(
        self,
        base_url: str,
        session_auth_headers: dict,
        session_access_token: str,
        timeout: int,
    ) -> None:
        search_id = _run_search(base_url, session_auth_headers, timeout)
        _skip_if_none(search_id, "search history")
        self.share_url = f"{base_url}/api/v1/search/{search_id}/share"
        self.unshare_url = f"{base_url}/api/v1/search/{search_id}/unshare"
        self.headers = session_auth_headers
        self.timeout = timeout
        self.user_ids = [_self_user_id_from_jwt(session_access_token)]

    def test_share_response_schema(self) -> None:
        resp = requests.patch(
            self.share_url, headers=self.headers,
            json={"userIds": self.user_ids}, timeout=self.timeout,
        )
        assert resp.status_code == 200, f"{resp.status_code}: {resp.text}"
        assert_openapi_response(
            resp.json(), "/search/{searchId}/share", "PATCH",
        )

    def test_unshare_response_schema(self) -> None:
        resp = requests.patch(
            self.unshare_url, headers=self.headers,
            json={"userIds": self.user_ids}, timeout=self.timeout,
        )
        assert resp.status_code == 200, f"{resp.status_code}: {resp.text}"
        assert_openapi_response(
            resp.json(), "/search/{searchId}/unshare", "PATCH",
        )


# ===========================================================================
# DELETE /api/v1/search/{searchId}   [destructive]
# ===========================================================================
@pytest.mark.integration
@pytest.mark.destructive
class TestDeleteSearchById:
    """Destructive — gated by `-m destructive`."""

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
    """Destructive — purges all search history. Gated by `-m destructive`."""

    @pytest.fixture(autouse=True)
    def _setup(self, base_url: str, session_auth_headers: dict, timeout: int) -> None:
        self.url = f"{base_url}/api/v1/search"
        self.headers = session_auth_headers
        self.timeout = timeout

    def test_response_schema(self) -> None:
        resp = requests.delete(self.url, headers=self.headers, timeout=self.timeout)
        assert resp.status_code == 200, f"{resp.status_code}: {resp.text}"
        assert_openapi_response(resp.json(), "/search", "DELETE")
