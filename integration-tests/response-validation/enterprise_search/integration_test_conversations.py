"""
Conversations API – Response Validation Integration Tests
==========================================================

Validates every JSON-returning conversation route against the response schema
declared in ``pipeshub-openapi.yaml``.  The YAML is the single source of
truth: if the server's actual response shape diverges from the spec the test
fails, which would mean the generated SDK types are wrong.

Routes covered (JSON responses only)
--------------------------------------
  GET  /api/v1/conversations                                              getAllConversations
  GET  /api/v1/conversations/show/archives                                listAllArchivesConversation
  GET  /api/v1/conversations/{conversationId}                             getConversationById
  PATCH /api/v1/conversations/{conversationId}/title                      updateTitle
  PATCH /api/v1/conversations/{conversationId}/archive                    archiveConversation
  PATCH /api/v1/conversations/{conversationId}/unarchive                  unarchiveConversation
  POST  /api/v1/conversations/{conversationId}/share                      shareConversationById
  POST  /api/v1/conversations/{conversationId}/unshare                    unshareConversationById
  POST  /api/v1/conversations/{conversationId}/message/{id}/feedback      updateFeedback
  DELETE /api/v1/conversations/{conversationId}                           deleteConversationById  [destructive]

Routes skipped — SSE (text/event-stream, no JSON schema)
---------------------------------------------------------
  POST /api/v1/conversations/stream
  POST /api/v1/conversations/internal/stream
  POST /api/v1/conversations/{conversationId}/messages/stream
  POST /api/v1/conversations/internal/{conversationId}/messages/stream
  POST /api/v1/conversations/{conversationId}/message/{messageId}/regenerate

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
# Path setup — mirror the pattern used by PR #1869
#
#   parents[0] = enterprise_search/
#   parents[1] = response-validation/
#   parents[2] = integration-tests/
# ---------------------------------------------------------------------------
_IT_ROOT = Path(__file__).resolve().parents[2]
_RV_HELPER = _IT_ROOT / "response-validation" / "helper"
for _p in (_IT_ROOT, _RV_HELPER):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from openapi_validator import assert_openapi_response  # noqa: E402


# ---------------------------------------------------------------------------
# Module-level resource helpers
# ---------------------------------------------------------------------------

def _first_conversation_id(base_url: str, headers: dict, timeout: int) -> Optional[str]:
    """Return the _id of the first non-archived conversation, or None."""
    resp = requests.get(
        f"{base_url}/api/v1/conversations",
        headers=headers,
        params={"page": 1, "limit": 1},
        timeout=timeout,
    )
    if resp.status_code != 200:
        return None
    items = resp.json().get("conversations", [])
    if not items:
        return None
    return str(items[0].get("_id") or items[0].get("id") or "")


def _first_message_id(
    base_url: str, headers: dict, conversation_id: str, timeout: int
) -> Optional[str]:
    """Return the _id of the first message in the given conversation, or None."""
    resp = requests.get(
        f"{base_url}/api/v1/conversations/{conversation_id}",
        headers=headers,
        timeout=timeout,
    )
    if resp.status_code != 200:
        return None
    messages = resp.json().get("messages", [])
    if not messages:
        return None
    return str(messages[0].get("_id") or messages[0].get("id") or "")


def _skip_if_none(value: Optional[str], label: str) -> None:
    if not value:
        pytest.skip(f"No {label} available — create one via the UI first")


# ===========================================================================
# GET /api/v1/conversations
# ===========================================================================
@pytest.mark.integration
class TestListConversations:

    @pytest.fixture(autouse=True)
    def _setup(self, base_url: str, session_auth_headers: dict, timeout: int) -> None:
        self.url = f"{base_url}/api/v1/conversations"
        self.headers = session_auth_headers
        self.timeout = timeout

    def test_response_schema(self) -> None:
        resp = requests.get(self.url, headers=self.headers, timeout=self.timeout)
        assert resp.status_code == 200, f"{resp.status_code}: {resp.text}"
        assert_openapi_response(resp.json(), "/conversations", "GET")

    def test_pagination_params(self) -> None:
        resp = requests.get(
            self.url, headers=self.headers,
            params={"page": 1, "limit": 5}, timeout=self.timeout,
        )
        assert resp.status_code == 200, f"{resp.status_code}: {resp.text}"
        assert_openapi_response(resp.json(), "/conversations", "GET")


# ===========================================================================
# GET /api/v1/conversations/show/archives
# ===========================================================================
@pytest.mark.integration
class TestListArchivedConversations:

    @pytest.fixture(autouse=True)
    def _setup(self, base_url: str, session_auth_headers: dict, timeout: int) -> None:
        self.url = f"{base_url}/api/v1/conversations/show/archives"
        self.headers = session_auth_headers
        self.timeout = timeout

    def test_response_schema(self) -> None:
        resp = requests.get(self.url, headers=self.headers, timeout=self.timeout)
        assert resp.status_code == 200, f"{resp.status_code}: {resp.text}"
        assert_openapi_response(resp.json(), "/conversations/show/archives", "GET")

    def test_pagination_params(self) -> None:
        resp = requests.get(
            self.url, headers=self.headers,
            params={"page": 1, "limit": 5}, timeout=self.timeout,
        )
        assert resp.status_code == 200, f"{resp.status_code}: {resp.text}"
        assert_openapi_response(resp.json(), "/conversations/show/archives", "GET")


# ===========================================================================
# GET /api/v1/conversations/{conversationId}
# ===========================================================================
@pytest.mark.integration
class TestGetConversationById:

    @pytest.fixture(autouse=True)
    def _setup(self, base_url: str, session_auth_headers: dict, timeout: int) -> None:
        self.base_url = base_url
        self.headers = session_auth_headers
        self.timeout = timeout
        conv_id = _first_conversation_id(base_url, session_auth_headers, timeout)
        _skip_if_none(conv_id, "conversations")
        self.conv_id = conv_id
        self.url = f"{base_url}/api/v1/conversations/{conv_id}"

    def test_response_schema(self) -> None:
        resp = requests.get(self.url, headers=self.headers, timeout=self.timeout)
        assert resp.status_code == 200, f"{resp.status_code}: {resp.text}"
        assert_openapi_response(resp.json(), "/conversations/{conversationId}", "GET")

    def test_unknown_id_returns_404(self) -> None:
        url = f"{self.base_url}/api/v1/conversations/000000000000000000000000"
        resp = requests.get(url, headers=self.headers, timeout=self.timeout)
        assert resp.status_code == 404, f"Expected 404, got {resp.status_code}"


# ===========================================================================
# PATCH /api/v1/conversations/{conversationId}/title
# ===========================================================================
@pytest.mark.integration
class TestUpdateConversationTitle:

    @pytest.fixture(autouse=True)
    def _setup(self, base_url: str, session_auth_headers: dict, timeout: int) -> None:
        conv_id = _first_conversation_id(base_url, session_auth_headers, timeout)
        _skip_if_none(conv_id, "conversations")
        self.conv_id = conv_id
        self.base_url = base_url
        self.url = f"{base_url}/api/v1/conversations/{conv_id}/title"
        self.headers = session_auth_headers
        self.timeout = timeout

    def _current_title(self) -> str:
        resp = requests.get(
            f"{self.base_url}/api/v1/conversations/{self.conv_id}",
            headers=self.headers, timeout=self.timeout,
        )
        assert resp.status_code == 200
        return resp.json().get("title", "Untitled")

    def test_response_schema(self) -> None:
        original = self._current_title()
        try:
            resp = requests.patch(
                self.url, headers=self.headers,
                json={"title": "IT-temp-title"}, timeout=self.timeout,
            )
            assert resp.status_code == 200, f"{resp.status_code}: {resp.text}"
            assert_openapi_response(
                resp.json(), "/conversations/{conversationId}/title", "PATCH"
            )
        finally:
            requests.patch(
                self.url, headers=self.headers,
                json={"title": original}, timeout=self.timeout,
            )


# ===========================================================================
# PATCH archive + PATCH unarchive  (reversible pair)
# ===========================================================================
@pytest.mark.integration
class TestArchiveUnarchiveConversation:

    @pytest.fixture(autouse=True)
    def _setup(self, base_url: str, session_auth_headers: dict, timeout: int) -> None:
        conv_id = _first_conversation_id(base_url, session_auth_headers, timeout)
        _skip_if_none(conv_id, "conversations")
        self.archive_url = f"{base_url}/api/v1/conversations/{conv_id}/archive"
        self.unarchive_url = f"{base_url}/api/v1/conversations/{conv_id}/unarchive"
        self.headers = session_auth_headers
        self.timeout = timeout

    def test_archive_response_schema(self) -> None:
        try:
            resp = requests.patch(
                self.archive_url, headers=self.headers, timeout=self.timeout
            )
            assert resp.status_code == 200, f"{resp.status_code}: {resp.text}"
            assert_openapi_response(
                resp.json(), "/conversations/{conversationId}/archive", "PATCH"
            )
        finally:
            requests.patch(self.unarchive_url, headers=self.headers, timeout=self.timeout)

    def test_unarchive_response_schema(self) -> None:
        requests.patch(self.archive_url, headers=self.headers, timeout=self.timeout)
        resp = requests.patch(
            self.unarchive_url, headers=self.headers, timeout=self.timeout
        )
        assert resp.status_code == 200, f"{resp.status_code}: {resp.text}"
        assert_openapi_response(
            resp.json(), "/conversations/{conversationId}/unarchive", "PATCH"
        )


# ===========================================================================
# POST share + POST unshare  (reversible pair)
# ===========================================================================
@pytest.mark.integration
class TestShareUnshareConversation:

    # Set to real user IDs in your test org to exercise sharing fully.
    # With an empty list the server will accept the call (no-op share).
    _USER_IDS: list[str] = []

    @pytest.fixture(autouse=True)
    def _setup(self, base_url: str, session_auth_headers: dict, timeout: int) -> None:
        conv_id = _first_conversation_id(base_url, session_auth_headers, timeout)
        _skip_if_none(conv_id, "conversations")
        self.share_url = f"{base_url}/api/v1/conversations/{conv_id}/share"
        self.unshare_url = f"{base_url}/api/v1/conversations/{conv_id}/unshare"
        self.headers = session_auth_headers
        self.timeout = timeout

    def test_share_response_schema(self) -> None:
        resp = requests.post(
            self.share_url, headers=self.headers,
            json={"userIds": self._USER_IDS}, timeout=self.timeout,
        )
        assert resp.status_code == 200, f"{resp.status_code}: {resp.text}"
        assert_openapi_response(
            resp.json(), "/conversations/{conversationId}/share", "POST"
        )

    def test_unshare_response_schema(self) -> None:
        resp = requests.post(
            self.unshare_url, headers=self.headers,
            json={"userIds": self._USER_IDS}, timeout=self.timeout,
        )
        assert resp.status_code == 200, f"{resp.status_code}: {resp.text}"
        assert_openapi_response(
            resp.json(), "/conversations/{conversationId}/unshare", "POST"
        )


# ===========================================================================
# POST /api/v1/conversations/{conversationId}/message/{messageId}/feedback
# ===========================================================================
@pytest.mark.integration
class TestMessageFeedback:

    @pytest.fixture(autouse=True)
    def _setup(self, base_url: str, session_auth_headers: dict, timeout: int) -> None:
        conv_id = _first_conversation_id(base_url, session_auth_headers, timeout)
        _skip_if_none(conv_id, "conversations")
        msg_id = _first_message_id(base_url, session_auth_headers, conv_id, timeout)
        _skip_if_none(msg_id, "messages")
        self.url = (
            f"{base_url}/api/v1/conversations/{conv_id}/message/{msg_id}/feedback"
        )
        self.headers = session_auth_headers
        self.timeout = timeout

    def test_positive_feedback_schema(self) -> None:
        resp = requests.post(
            self.url, headers=self.headers,
            json={"feedback": "positive"}, timeout=self.timeout,
        )
        assert resp.status_code == 200, f"{resp.status_code}: {resp.text}"
        assert_openapi_response(
            resp.json(),
            "/conversations/{conversationId}/message/{messageId}/feedback",
            "POST",
        )

    def test_negative_feedback_schema(self) -> None:
        resp = requests.post(
            self.url, headers=self.headers,
            json={"feedback": "negative", "comment": "Response was off-topic."},
            timeout=self.timeout,
        )
        assert resp.status_code == 200, f"{resp.status_code}: {resp.text}"
        assert_openapi_response(
            resp.json(),
            "/conversations/{conversationId}/message/{messageId}/feedback",
            "POST",
        )


# ===========================================================================
# DELETE /api/v1/conversations/{conversationId}
# ===========================================================================
@pytest.mark.integration
@pytest.mark.destructive
class TestDeleteConversation:
    """
    Only runs with ``pytest -m destructive``.
    Do NOT run against a production instance.
    """

    @pytest.fixture(autouse=True)
    def _setup(self, base_url: str, session_auth_headers: dict, timeout: int) -> None:
        conv_id = _first_conversation_id(base_url, session_auth_headers, timeout)
        _skip_if_none(conv_id, "conversations")
        self.url = f"{base_url}/api/v1/conversations/{conv_id}"
        self.headers = session_auth_headers
        self.timeout = timeout

    def test_response_schema(self) -> None:
        resp = requests.delete(self.url, headers=self.headers, timeout=self.timeout)
        assert resp.status_code == 200, f"{resp.status_code}: {resp.text}"
        assert_openapi_response(
            resp.json(), "/conversations/{conversationId}", "DELETE"
        )
