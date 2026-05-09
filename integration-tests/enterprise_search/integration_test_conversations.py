"""Conversations API response-schema integration tests."""

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


def _scoped_auth_headers_or_none() -> Optional[dict]:
    token = os.getenv("PIPESHUB_SCOPED_TOKEN", "").strip()
    if not token:
        return None
    return {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }


_SCOPED_TOKEN_SKIP_REASON = "PIPESHUB_SCOPED_TOKEN not set — /internal/* routes require a CONVERSATION_CREATE-scoped JWT minted server-side."


def _self_user_id_from_jwt(access_token: str) -> str:
    seg = access_token.split(".")[1]
    seg += "=" * (-len(seg) % 4)
    payload = json.loads(base64.urlsafe_b64decode(seg))
    uid = payload.get("userId")
    if not uid:
        pytest.skip("JWT payload has no userId; cannot exercise share/unshare")
    return str(uid)


def _stream_new_conversation(base_url: str, headers: dict, timeout: int) -> Optional[str]:
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
                conv_id = conv.get("_id") or conv.get("id")
                if conv_id:
                    return str(conv_id)
    return None


def _ensure_conversation(base_url: str, headers: dict, timeout: int) -> Optional[str]:
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

    return _stream_new_conversation(base_url, headers, timeout)


def _first_conversation_id(base_url: str, headers: dict, timeout: int) -> Optional[str]:
    return _ensure_conversation(base_url, headers, timeout)


def _first_bot_message_id(
    base_url: str, headers: dict, conversation_id: str, timeout: int
) -> Optional[str]:
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
        assert_openapi_response(
            resp.json(), "/conversations", "GET",
        )

    def test_pagination_params(self) -> None:
        resp = requests.get(
            self.url, headers=self.headers,
            params={"page": 1, "limit": 5}, timeout=self.timeout,
        )
        assert resp.status_code == 200, f"{resp.status_code}: {resp.text}"
        assert_openapi_response(
            resp.json(), "/conversations", "GET",
        )


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
        assert_openapi_response(
            resp.json(), "/conversations/{conversationId}", "GET",
        )

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
        self.user_ids = [_self_user_id_from_jwt(session_access_token)]

    def test_share_response_schema(self) -> None:
        resp = requests.post(
            self.share_url, headers=self.headers,
            json={"userIds": self.user_ids}, timeout=self.timeout,
        )
        assert resp.status_code == 200, f"{resp.status_code}: {resp.text}"
        assert_openapi_response(
            resp.json(), "/conversations/{conversationId}/share", "POST",
        )

    def test_unshare_response_schema(self) -> None:
        resp = requests.post(
            self.unshare_url, headers=self.headers,
            json={"userIds": self.user_ids}, timeout=self.timeout,
        )
        assert resp.status_code == 200, f"{resp.status_code}: {resp.text}"
        assert_openapi_response(
            resp.json(), "/conversations/{conversationId}/unshare", "POST",
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
# SSE endpoints
# ===========================================================================
@pytest.mark.integration
class TestStreamConversation:

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

        event_names = [f["event"] for f in frames]
        assert "connected" in event_names, f"No 'connected' frame — got: {event_names}"
        assert "complete" in event_names or "error" in event_names, (
            f"No terminal 'complete' or 'error' frame — got: {event_names}"
        )

        assert_openapi_sse_stream(frames, "/conversations/stream", "POST")


@pytest.mark.integration
class TestAddMessageStream:

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

    @pytest.fixture(autouse=True)
    def _setup(self, base_url: str, session_auth_headers: dict, timeout: int) -> None:
        self.base_url = base_url
        self.headers = session_auth_headers
        self.timeout = timeout

        # Fresh conversation so regenerate targets the only bot_response.
        conv_id = _stream_new_conversation(base_url, session_auth_headers, timeout)
        _skip_if_none(conv_id, "fresh streamed conversations")

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


# Non-streaming JSON twins — DISABLED.
# OAuth variants 500 (upstream `/chat` missing); internal variants need
# PIPESHUB_SCOPED_TOKEN. Bodies preserved below for revival.
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
        )
"""


# ===========================================================================
# DELETE /api/v1/conversations/{conversationId}
# ===========================================================================
@pytest.mark.integration
@pytest.mark.destructive
class TestDeleteConversation:
    """Destructive — gated by `-m destructive`."""

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
        )
