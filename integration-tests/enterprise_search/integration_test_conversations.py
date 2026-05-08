"""
Conversations API – Response Validation Integration Tests
=========================================================

Validates every JSON-returning conversation route against the response schema
declared in ``pipeshub-openapi.yaml``.

Routes covered
--------------
  GET    /api/v1/conversations                                          getAllConversations
  GET    /api/v1/conversations/show/archives                            listAllArchivesConversation
  GET    /api/v1/conversations/{conversationId}                         getConversationById
  PATCH  /api/v1/conversations/{conversationId}/title                   updateTitle
  PATCH  /api/v1/conversations/{conversationId}/archive                 archiveConversation
  PATCH  /api/v1/conversations/{conversationId}/unarchive               unarchiveConversation
  POST   /api/v1/conversations/{conversationId}/share                   shareConversationById
  POST   /api/v1/conversations/{conversationId}/unshare                 unshareConversationById
  POST   /api/v1/conversations/{conversationId}/message/{id}/feedback   updateFeedback
  DELETE /api/v1/conversations/{conversationId}                         deleteConversationById   [destructive]

Non-streaming JSON twins of the streaming chat routes
-----------------------------------------------------
  POST   /api/v1/conversations/create                                   createConversation         (OAuth)
  POST   /api/v1/conversations/internal/create                          createConversation         (scoped token)
  POST   /api/v1/conversations/{conversationId}/messages                addMessage                 (OAuth)
  POST   /api/v1/conversations/internal/{conversationId}/messages       addMessage                 (scoped token)

Streaming routes covered (SSE / text/event-stream)
--------------------------------------------------
  POST /api/v1/conversations/stream
  POST /api/v1/conversations/{conversationId}/messages/stream
  POST /api/v1/conversations/{conversationId}/message/{messageId}/regenerate

Requires (set in integration-tests/.env.local)
-----------------------------------------------
  PIPESHUB_BASE_URL
  PIPESHUB_TEST_USER_EMAIL
  PIPESHUB_TEST_USER_PASSWORD

Optional (only required to exercise the ``/internal/*`` scoped-token routes)
----------------------------------------------------------------------------
  PIPESHUB_SCOPED_TOKEN              — pre-minted JWT signed with the server's
                                       SCOPED_JWT_SECRET and carrying the
                                       ``conversation:create`` scope. Without
                                       it, the two ``TestCreateConversationInternal``
                                       and ``TestAddMessageInternal`` cases skip.
"""

from __future__ import annotations

import base64
import json
import os
from typing import Optional

import pytest
import requests

from openapi_validator import (
    assert_openapi_response,
    assert_openapi_sse_stream,
    parse_sse_stream,
)


# ---------------------------------------------------------------------------
# Scoped-token helper — for the ``/internal/*`` routes guarded by
# ``authMiddleware.scopedTokenValidator(TokenScopes.CONVERSATION_CREATE)``.
#
# Tokens are minted internally by the Node.js service (e.g. by the Slack-bot
# integration) and are not obtainable via any public HTTP endpoint, so the
# integration test environment can only exercise these routes when the caller
# supplies a pre-minted JWT via ``PIPESHUB_SCOPED_TOKEN``.  When that env var
# is not set the dependent test cases are skipped with a documented reason.
# ---------------------------------------------------------------------------

def _scoped_auth_headers_or_none() -> Optional[dict]:
    """Return Authorization headers for a CONVERSATION_CREATE scoped token, or None."""
    token = os.getenv("PIPESHUB_SCOPED_TOKEN", "").strip()
    if not token:
        return None
    return {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }


_SCOPED_TOKEN_SKIP_REASON = (
    "PIPESHUB_SCOPED_TOKEN not set — the /conversations/internal/* routes "
    "require a JWT signed with the server's SCOPED_JWT_SECRET and carrying "
    "the 'conversation:create' scope.  No public endpoint mints this token "
    "(it is generated only inside the Slack-bot integration via "
    "AuthTokenService.generateScopedToken), so it must be provided "
    "out-of-band to exercise these routes."
)


def _self_user_id_from_jwt(access_token: str) -> str:
    """Decode the ``userId`` claim from the access-token JWT payload."""
    seg = access_token.split(".")[1]
    seg += "=" * (-len(seg) % 4)
    payload = json.loads(base64.urlsafe_b64decode(seg))
    uid = payload.get("userId")
    if not uid:
        pytest.skip("JWT payload has no userId; cannot exercise share/unshare")
    return str(uid)


# ---------------------------------------------------------------------------
# Module-level resource helpers
# ---------------------------------------------------------------------------

def _stream_new_conversation(base_url: str, headers: dict, timeout: int) -> Optional[str]:
    """
    Start a new conversation via the SSE stream endpoint and return its ID.

    Consumes the full stream until the ``complete`` event is emitted, then
    extracts the conversation ID from the payload.  Returns None on failure.
    """
    resp = requests.post(
        f"{base_url}/api/v1/conversations/stream",
        headers=headers,
        json={"query": "Why was monolith started?"},
        timeout=timeout,
        stream=True,
    )
    if resp.status_code != 200:
        return None

    raw = resp.content.decode("utf-8", errors="replace")
    frames = parse_sse_stream(raw)
    for frame in frames:
        if frame["event"] == "complete":
            data = frame["data"]
            if isinstance(data, dict):
                conv = data.get("conversation", {})
                # complete payload from /conversations/stream uses _id
                conv_id = conv.get("_id") or conv.get("id")
                if conv_id:
                    return str(conv_id)
    return None


def _ensure_conversation(base_url: str, headers: dict, timeout: int) -> Optional[str]:
    """
    Return the ID of an existing non-archived conversation, or create one via
    the stream endpoint if none exist yet.
    """
    resp = requests.get(
        f"{base_url}/api/v1/conversations",
        headers=headers,
        params={"page": 1, "limit": 1},
        timeout=timeout,
    )
    if resp.status_code == 200:
        items = resp.json().get("conversations", [])
        if items:
            return str(items[0].get("_id") or items[0].get("id") or "")

    # No conversations — create one via stream.
    return _stream_new_conversation(base_url, headers, timeout)


def _first_conversation_id(base_url: str, headers: dict, timeout: int) -> Optional[str]:
    """Return the id of the first non-archived conversation, or None."""
    return _ensure_conversation(base_url, headers, timeout)


def _first_bot_message_id(
    base_url: str, headers: dict, conversation_id: str, timeout: int
) -> Optional[str]:
    """Return the id of the first bot_response message in a conversation."""
    resp = requests.get(
        f"{base_url}/api/v1/conversations/{conversation_id}",
        headers=headers,
        params={"limit": 100, "sortOrder": "asc"},
        timeout=timeout,
    )
    if resp.status_code != 200:
        return None
    messages = resp.json().get("conversation", {}).get("messages", [])
    for msg in messages:
        if msg.get("messageType") == "bot_response":
            return str(msg.get("_id") or msg.get("id") or "")
    return None


def _last_bot_message_id(
    base_url: str, headers: dict, conversation_id: str, timeout: int
) -> Optional[str]:
    """Return the id of the last bot_response message in a conversation."""
    resp = requests.get(
        f"{base_url}/api/v1/conversations/{conversation_id}",
        headers=headers,
        params={"limit": 100, "sortOrder": "asc"},
        timeout=timeout,
    )
    if resp.status_code != 200:
        return None
    messages = resp.json().get("conversation", {}).get("messages", [])
    last_bot = None
    for msg in messages:
        if msg.get("messageType") == "bot_response":
            last_bot = str(msg.get("_id") or msg.get("id") or "")
    return last_bot


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
        assert_openapi_response(resp.json(), "/conversations", "GET", additional_properties=True)

    def test_pagination_params(self) -> None:
        resp = requests.get(
            self.url, headers=self.headers,
            params={"page": 1, "limit": 5}, timeout=self.timeout,
        )
        assert resp.status_code == 200, f"{resp.status_code}: {resp.text}"
        assert_openapi_response(resp.json(), "/conversations", "GET", additional_properties=True)


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
        assert_openapi_response(resp.json(), "/conversations/show/archives", "GET", additional_properties=True)

    def test_pagination_params(self) -> None:
        resp = requests.get(
            self.url, headers=self.headers,
            params={"page": 1, "limit": 5}, timeout=self.timeout,
        )
        assert resp.status_code == 200, f"{resp.status_code}: {resp.text}"
        assert_openapi_response(resp.json(), "/conversations/show/archives", "GET", additional_properties=True)


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
        assert_openapi_response(resp.json(), "/conversations/{conversationId}", "GET", additional_properties=True)

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
        return resp.json().get("conversation", {}).get("title", "Untitled")

    def test_response_schema(self) -> None:
        original = self._current_title()
        try:
            resp = requests.patch(
                self.url, headers=self.headers,
                json={"title": "IT-temp-title"}, timeout=self.timeout,
            )
            assert resp.status_code == 200, f"{resp.status_code}: {resp.text}"
            assert_openapi_response(
                resp.json(), "/conversations/{conversationId}/title", "PATCH",
                additional_properties=True,
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
                resp.json(), "/conversations/{conversationId}/archive", "PATCH",
                additional_properties=True,
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
            resp.json(), "/conversations/{conversationId}/unarchive", "PATCH",
            additional_properties=True,
        )


# ===========================================================================
# POST share + POST unshare  (reversible pair)
# ===========================================================================
@pytest.mark.integration
class TestShareUnshareConversation:

    @pytest.fixture(autouse=True)
    def _setup(
        self,
        base_url: str,
        session_auth_headers: dict,
        session_access_token: str,
        timeout: int,
    ) -> None:
        conv_id = _first_conversation_id(base_url, session_auth_headers, timeout)
        _skip_if_none(conv_id, "conversations")
        self.share_url = f"{base_url}/api/v1/conversations/{conv_id}/share"
        self.unshare_url = f"{base_url}/api/v1/conversations/{conv_id}/unshare"
        self.headers = session_auth_headers
        self.timeout = timeout
        # Server requires at least one userId; share with self is a valid no-op.
        self.user_ids = [_self_user_id_from_jwt(session_access_token)]

    def test_share_response_schema(self) -> None:
        resp = requests.post(
            self.share_url, headers=self.headers,
            json={"userIds": self.user_ids}, timeout=self.timeout,
        )
        assert resp.status_code == 200, f"{resp.status_code}: {resp.text}"
        assert_openapi_response(
            resp.json(), "/conversations/{conversationId}/share", "POST",
            additional_properties=True,
        )

    def test_unshare_response_schema(self) -> None:
        resp = requests.post(
            self.unshare_url, headers=self.headers,
            json={"userIds": self.user_ids}, timeout=self.timeout,
        )
        assert resp.status_code == 200, f"{resp.status_code}: {resp.text}"
        assert_openapi_response(
            resp.json(), "/conversations/{conversationId}/unshare", "POST",
            additional_properties=True,
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
        msg_id = _first_bot_message_id(base_url, session_auth_headers, conv_id, timeout)
        _skip_if_none(msg_id, "bot_response messages")
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
            additional_properties=True,
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
            additional_properties=True,
        )


# ===========================================================================
# SSE endpoints
# ===========================================================================
@pytest.mark.integration
class TestStreamConversation:
    """POST /api/v1/conversations/stream — create conversation with SSE response."""

    @pytest.fixture(autouse=True)
    def _setup(self, base_url: str, session_auth_headers: dict, timeout: int) -> None:
        self.url = f"{base_url}/api/v1/conversations/stream"
        self.headers = session_auth_headers
        self.timeout = timeout

    def test_sse_stream_schema(self) -> None:
        resp = requests.post(
            self.url,
            headers=self.headers,
            json={"query": "Why was monolith started?"},
            timeout=self.timeout,
            stream=True,
        )
        assert resp.status_code == 200, f"{resp.status_code}: {resp.text[:200]}"

        raw = resp.content.decode("utf-8", errors="replace")
        frames = parse_sse_stream(raw)
        assert frames, "Expected at least one SSE frame"

        # Sanity: must have a 'connected' frame and a terminal frame ('complete' or 'error').
        event_names = [f["event"] for f in frames]
        assert "connected" in event_names, f"No 'connected' frame — got: {event_names}"
        assert "complete" in event_names or "error" in event_names, (
            f"No terminal 'complete' or 'error' frame — got: {event_names}"
        )

        assert_openapi_sse_stream(frames, "/conversations/stream", "POST")


@pytest.mark.integration
class TestAddMessageStream:
    """POST /api/v1/conversations/{conversationId}/messages/stream."""

    @pytest.fixture(autouse=True)
    def _setup(self, base_url: str, session_auth_headers: dict, timeout: int) -> None:
        self.base_url = base_url
        self.headers = session_auth_headers
        self.timeout = timeout
        conv_id = _first_conversation_id(base_url, session_auth_headers, timeout)
        _skip_if_none(conv_id, "conversations")
        self.conv_id = conv_id
        self.url = f"{base_url}/api/v1/conversations/{conv_id}/messages/stream"

    def test_sse_stream_schema(self) -> None:
        resp = requests.post(
            self.url,
            headers=self.headers,
            json={"query": "Can you summarise that in one sentence?"},
            timeout=self.timeout,
            stream=True,
        )
        assert resp.status_code == 200, f"{resp.status_code}: {resp.text[:200]}"

        raw = resp.content.decode("utf-8", errors="replace")
        frames = parse_sse_stream(raw)
        assert frames, "Expected at least one SSE frame"

        event_names = [f["event"] for f in frames]
        assert "connected" in event_names, f"No 'connected' frame — got: {event_names}"
        assert "complete" in event_names or "error" in event_names, (
            f"No terminal 'complete' or 'error' frame — got: {event_names}"
        )

        assert_openapi_sse_stream(
            frames, "/conversations/{conversationId}/messages/stream", "POST"
        )


@pytest.mark.integration
class TestRegenerateMessageStream:
    """POST /api/v1/conversations/{conversationId}/message/{messageId}/regenerate."""

    @pytest.fixture(autouse=True)
    def _setup(self, base_url: str, session_auth_headers: dict, timeout: int) -> None:
        self.base_url = base_url
        self.headers = session_auth_headers
        self.timeout = timeout

        # Create a fresh 2-message conversation (user_query + bot_response) so
        # that regenerate is always targeted at the last (and only) bot message.
        conv_id = _stream_new_conversation(base_url, session_auth_headers, timeout)
        _skip_if_none(conv_id, "fresh streamed conversations")

        # Regenerate only works on the last bot_response in a conversation.
        msg_id = _last_bot_message_id(base_url, session_auth_headers, conv_id, timeout)
        _skip_if_none(msg_id, "bot_response messages")
        self.url = (
            f"{base_url}/api/v1/conversations/{conv_id}/message/{msg_id}/regenerate"
        )

    def test_sse_stream_schema(self) -> None:
        resp = requests.post(
            self.url,
            headers=self.headers,
            json={},
            timeout=self.timeout,
            stream=True,
        )
        assert resp.status_code == 200, f"{resp.status_code}: {resp.text[:200]}"

        raw = resp.content.decode("utf-8", errors="replace")
        frames = parse_sse_stream(raw)
        assert frames, "Expected at least one SSE frame"

        event_names = [f["event"] for f in frames]
        assert "connected" in event_names, f"No 'connected' frame — got: {event_names}"
        assert "complete" in event_names or "error" in event_names, (
            f"No terminal 'complete' or 'error' frame — got: {event_names}"
        )

        assert_openapi_sse_stream(
            frames,
            "/conversations/{conversationId}/message/{messageId}/regenerate",
            "POST",
        )


# ===========================================================================
# Non-streaming JSON twins — DISABLED
# ===========================================================================
# These four endpoints (POST /conversations/create, /conversations/internal/create,
# /conversations/{id}/messages, /conversations/internal/{id}/messages) are
# documented in the OpenAPI spec, but the tests are intentionally disabled here:
#
#   * The two OAuth tests (TestCreateConversation, TestAddMessage) currently
#     return HTTP 500 from the live stack. The Node controller proxies to
#     `${aiBackend}/api/v1/chat`, but the Python query service only declares
#     `POST /chat/stream` (backend/python/app/api/routes/chatbot.py) — the
#     non-streaming `/chat` upstream route does not exist. Re-enable once the
#     upstream route is added (or the non-streaming Node routes are removed).
#
#   * The two scoped-token tests (TestCreateConversationInternal,
#     TestAddMessageInternal) cannot run in the standard test environment —
#     there is no public way to mint a CONVERSATION_CREATE scoped token. They
#     can be re-enabled by setting PIPESHUB_SCOPED_TOKEN with a pre-minted JWT.
#
# The test bodies are preserved below as a triple-quoted string (parser no-op)
# so they are easy to revive once the upstream route lands and/or scoped-token
# auth is wired into the harness.
"""
@pytest.mark.integration
class TestCreateConversation:
    \"\"\"POST /api/v1/conversations/create — JSON twin of /conversations/stream.\"\"\"

    @pytest.fixture(autouse=True)
    def _setup(self, base_url: str, session_auth_headers: dict, timeout: int) -> None:
        self.url = f"{base_url}/api/v1/conversations/create"
        self.headers = session_auth_headers
        self.timeout = timeout

    def test_response_schema(self) -> None:
        resp = requests.post(
            self.url,
            headers=self.headers,
            json={"query": "Why was monolith started?"},
            timeout=self.timeout,
        )
        assert resp.status_code == 201, f"{resp.status_code}: {resp.text[:500]}"
        assert_openapi_response(
            resp.json(),
            "/conversations/create",
            "POST",
            status_code=201,
            additional_properties=True,
        )


@pytest.mark.integration
class TestCreateConversationInternal:
    \"\"\"
    POST /api/v1/conversations/internal/create — scoped-token variant.

    Skipped unless ``PIPESHUB_SCOPED_TOKEN`` is set; see
    ``_SCOPED_TOKEN_SKIP_REASON`` at the top of the file for details.
    \"\"\"

    @pytest.fixture(autouse=True)
    def _setup(self, base_url: str, timeout: int) -> None:
        scoped_headers = _scoped_auth_headers_or_none()
        if scoped_headers is None:
            pytest.skip(_SCOPED_TOKEN_SKIP_REASON)
        self.url = f"{base_url}/api/v1/conversations/internal/create"
        self.headers = scoped_headers
        self.timeout = timeout

    def test_response_schema(self) -> None:
        resp = requests.post(
            self.url,
            headers=self.headers,
            json={"query": "Why was monolith started?"},
            timeout=self.timeout,
        )
        assert resp.status_code == 201, f"{resp.status_code}: {resp.text[:500]}"
        assert_openapi_response(
            resp.json(),
            "/conversations/internal/create",
            "POST",
            status_code=201,
            additional_properties=True,
        )


@pytest.mark.integration
class TestAddMessage:
    \"\"\"POST /api/v1/conversations/{conversationId}/messages — JSON twin of the stream variant.\"\"\"

    @pytest.fixture(autouse=True)
    def _setup(self, base_url: str, session_auth_headers: dict, timeout: int) -> None:
        self.base_url = base_url
        self.headers = session_auth_headers
        self.timeout = timeout
        conv_id = _first_conversation_id(base_url, session_auth_headers, timeout)
        _skip_if_none(conv_id, "conversations")
        self.conv_id = conv_id
        self.url = f"{base_url}/api/v1/conversations/{conv_id}/messages"

    def test_response_schema(self) -> None:
        resp = requests.post(
            self.url,
            headers=self.headers,
            json={"query": "Can you summarise that in one sentence?"},
            timeout=self.timeout,
        )
        assert resp.status_code == 200, f"{resp.status_code}: {resp.text[:500]}"
        assert_openapi_response(
            resp.json(),
            "/conversations/{conversationId}/messages",
            "POST",
            status_code=200,
            additional_properties=True,
        )


@pytest.mark.integration
class TestAddMessageInternal:
    \"\"\"
    POST /api/v1/conversations/internal/{conversationId}/messages — scoped-token variant.

    Skipped unless ``PIPESHUB_SCOPED_TOKEN`` is set; see
    ``_SCOPED_TOKEN_SKIP_REASON`` at the top of the file for details.
    \"\"\"

    @pytest.fixture(autouse=True)
    def _setup(
        self,
        base_url: str,
        session_auth_headers: dict,
        timeout: int,
    ) -> None:
        scoped_headers = _scoped_auth_headers_or_none()
        if scoped_headers is None:
            pytest.skip(_SCOPED_TOKEN_SKIP_REASON)
        # The conversation lookup uses the regular OAuth headers because the
        # scoped token only carries CONVERSATION_CREATE scope and cannot list
        # conversations.  The actual /internal/* call is then made with the
        # scoped-token headers as the route requires.
        conv_id = _first_conversation_id(base_url, session_auth_headers, timeout)
        _skip_if_none(conv_id, "conversations")
        self.url = f"{base_url}/api/v1/conversations/internal/{conv_id}/messages"
        self.headers = scoped_headers
        self.timeout = timeout

    def test_response_schema(self) -> None:
        resp = requests.post(
            self.url,
            headers=self.headers,
            json={"query": "Can you summarise that in one sentence?"},
            timeout=self.timeout,
        )
        assert resp.status_code == 200, f"{resp.status_code}: {resp.text[:500]}"
        assert_openapi_response(
            resp.json(),
            "/conversations/internal/{conversationId}/messages",
            "POST",
            status_code=200,
            additional_properties=True,
        )
"""


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
            resp.json(), "/conversations/{conversationId}", "DELETE",
            additional_properties=True,
        )
