"""Enterprise conversation integration tests.

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
SHARE_TARGET_USER_ID = os.getenv("PIPESHUB_TEST_SHARE_TARGET_USER_ID", "").strip()

# Cap for runaway SSE; high enough for verbose dev streams before `complete`.
_SSE_MAX_EVENTS = 10_000


def _iter_sse_envelopes(resp: requests.Response, *, max_events: int = _SSE_MAX_EVENTS):
    """
    Minimal SSE parser for frames like:

      event: <name>
      data: <payload>

    Frames are separated by a blank line. We return OpenAPI-style envelopes:
    { "event": <name>, "data": <string> }.
    """
    event_name: str | None = None
    data_lines: list[str] = []

    def flush():
        nonlocal event_name, data_lines
        if event_name is None:
            return None
        env = {"event": event_name, "data": "\n".join(data_lines)}
        event_name = None
        data_lines = []
        return env

    emitted = 0
    for raw in resp.iter_lines(decode_unicode=True):
        if raw is None:
            continue
        line = raw.rstrip("\r")
        if line == "":
            env = flush()
            if env is not None:
                yield env
                emitted += 1
                if emitted >= max_events:
                    raise AssertionError(f"SSE exceeded max_events={max_events}")
            continue

        if line.startswith(":"):
            continue
        if line.startswith("event:"):
            event_name = line[len("event:") :].strip()
            continue
        if line.startswith("data:"):
            data_lines.append(line[len("data:") :].lstrip())
            continue
        # Ignore optional SSE fields (id:, retry:, etc.)

    env = flush()
    if env is not None:
        yield env


class _BaseEnterpriseConversationIntegration:

    @pytest.fixture(autouse=True)
    def _setup(self, pipeshub_client: PipeshubClient, session_kb: dict) -> None:
        base_url = pipeshub_client.base_url
        self.base_url = base_url
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
# Router: createConversationalRouter
# Routes mounted at /api/v1/conversations
# ============================================================================
@pytest.mark.integration
class TestConversations(_BaseEnterpriseConversationIntegration):

    # ------------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------------

    def _stream_create_conversation_id(self, *, query: str = SEARCH_QUERY) -> str:
        headers = {**self.headers, "Accept": "text/event-stream"}

        with requests.post(
            self.conversation_stream_url,
            headers=headers,
            json={"query": query},
            stream=True,
            timeout=self.stream_timeout,
        ) as resp:
            assert resp.status_code == 200, f"{resp.status_code}: {resp.text}"

            for envelope in _iter_sse_envelopes(resp):
                if envelope["event"] == "error":
                    payload = json.loads(envelope["data"])
                    raise AssertionError(f"stream emitted error event: {payload!r}")
                if envelope["event"] != "complete":
                    continue

                payload = json.loads(envelope["data"])
                conv = payload.get("conversation") or {}
                conv_id = conv.get("_id")
                assert isinstance(conv_id, str) and conv_id, (
                    f"complete payload missing conversation._id: {payload!r}"
                )
                return conv_id

        raise AssertionError("conversation stream ended without a complete event")

    def _stream_create_conversation_and_last_bot_message_id(
        self, *, query: str = SEARCH_QUERY
    ) -> tuple[str, str]:
        headers = {**self.headers, "Accept": "text/event-stream"}

        with requests.post(
            self.conversation_stream_url,
            headers=headers,
            json={"query": query},
            stream=True,
            timeout=self.stream_timeout,
        ) as resp:
            assert resp.status_code == 200, f"{resp.status_code}: {resp.text}"

            for envelope in _iter_sse_envelopes(resp):
                assert_matches_component_schema(envelope, "AssistantStreamSSEEvent")
                if envelope["event"] == "error":
                    payload = json.loads(envelope["data"])
                    raise AssertionError(f"stream emitted error event: {payload!r}")
                if envelope["event"] != "complete":
                    continue

                payload = json.loads(envelope["data"])
                conv = payload.get("conversation") or {}
                conv_id = conv.get("_id")
                assert isinstance(conv_id, str) and conv_id, (
                    f"complete payload missing conversation._id: {payload!r}"
                )
                msgs = conv.get("messages") or []
                bot_id: str | None = None
                for m in reversed(msgs if isinstance(msgs, list) else []):
                    if not isinstance(m, dict):
                        continue
                    if m.get("messageType") != "bot_response":
                        continue
                    mid = m.get("_id") or m.get("id")
                    if isinstance(mid, str) and mid:
                        bot_id = mid
                        break
                assert bot_id, f"no bot_response with _id in messages: {msgs!r}"
                return conv_id, bot_id

        raise AssertionError("conversation stream ended without a complete event")

    def _stream_create_conversation_bot_and_user_message_ids(
        self, *, query: str = SEARCH_QUERY
    ) -> tuple[str, str, str]:
        headers = {**self.headers, "Accept": "text/event-stream"}

        with requests.post(
            self.conversation_stream_url,
            headers=headers,
            json={"query": query},
            stream=True,
            timeout=self.stream_timeout,
        ) as resp:
            assert resp.status_code == 200, f"{resp.status_code}: {resp.text}"

            for envelope in _iter_sse_envelopes(resp):
                assert_matches_component_schema(envelope, "AssistantStreamSSEEvent")
                if envelope["event"] == "error":
                    payload = json.loads(envelope["data"])
                    raise AssertionError(f"stream emitted error event: {payload!r}")
                if envelope["event"] != "complete":
                    continue

                payload = json.loads(envelope["data"])
                conv = payload.get("conversation") or {}
                conv_id = conv.get("_id")
                assert isinstance(conv_id, str) and conv_id, (
                    f"complete payload missing conversation._id: {payload!r}"
                )
                msgs = conv.get("messages") or []
                bot_id: str | None = None
                user_id: str | None = None
                for m in reversed(msgs if isinstance(msgs, list) else []):
                    if not isinstance(m, dict):
                        continue
                    if m.get("messageType") != "bot_response":
                        continue
                    mid = m.get("_id") or m.get("id")
                    if isinstance(mid, str) and mid:
                        bot_id = mid
                        break
                for m in msgs if isinstance(msgs, list) else []:
                    if not isinstance(m, dict):
                        continue
                    if m.get("messageType") != "user_query":
                        continue
                    mid = m.get("_id") or m.get("id")
                    if isinstance(mid, str) and mid:
                        user_id = mid
                        break
                assert bot_id, f"no bot_response with _id in messages: {msgs!r}"
                assert user_id, f"no user_query with _id in messages: {msgs!r}"
                return conv_id, bot_id, user_id

        raise AssertionError("conversation stream ended without a complete event")

    # ------------------------------------------------------------------------
    # Tests
    # ------------------------------------------------------------------------

    def test_stream_conversation_response_matches_spec(self) -> None:
        headers = {**self.headers, "Accept": "text/event-stream"}

        with requests.post(
            self.conversation_stream_url,
            headers=headers,
            json={"query": SEARCH_QUERY},
            stream=True,
            timeout=self.stream_timeout,
        ) as resp:
            assert resp.status_code == 200, f"{resp.status_code}: {resp.text}"

            content_type = (resp.headers.get("Content-Type") or "").lower()
            assert "text/event-stream" in content_type, (
                f"expected text/event-stream, got Content-Type={resp.headers.get('Content-Type')!r}"
            )

            accumulated_answer = ""
            saw_complete = False

            for envelope in _iter_sse_envelopes(resp):
                assert_matches_component_schema(envelope, "AssistantStreamSSEEvent")

                payload = json.loads(envelope["data"])
                event = envelope["event"]

                if event == "answer_chunk" and isinstance(payload, dict):
                    acc = payload.get("accumulated")
                    if isinstance(acc, str):
                        accumulated_answer = acc

                if event == "error":
                    raise AssertionError(f"stream emitted error event: {payload!r}")

                if event == "complete":
                    saw_complete = True
                    if not accumulated_answer.strip() and isinstance(payload, dict):
                        conv = payload.get("conversation") or {}
                        msgs = conv.get("messages") or []
                        for m in reversed(msgs if isinstance(msgs, list) else []):
                            if isinstance(m, dict) and m.get("role") == "assistant":
                                content = m.get("content")
                                if isinstance(content, str) and content.strip():
                                    accumulated_answer = content
                                    break
                    break

            assert saw_complete, "stream ended without a complete event"
            assert accumulated_answer.strip(), "stream completed but answer text was empty"

    @pytest.mark.parametrize(
        ("chat_mode", "query"),
        [
            ("internal_search", SEARCH_QUERY),
            ("web_search", "Where is the pipeshub hq located?"),
        ],
    )
    def test_stream_conversation_chat_mode_returns_answer(
        self, chat_mode: str, query: str
    ) -> None:
        # Starts a chat using the chosen search mode and checks the bot replies.
        headers = {**self.headers, "Accept": "text/event-stream"}

        with requests.post(
            self.conversation_stream_url,
            headers=headers,
            json={"query": query, "chatMode": chat_mode},
            stream=True,
            timeout=self.stream_timeout,
        ) as resp:
            assert resp.status_code == 200, f"{resp.status_code}: {resp.text}"

            accumulated_answer = ""
            saw_complete = False

            for envelope in _iter_sse_envelopes(resp):
                assert_matches_component_schema(envelope, "AssistantStreamSSEEvent")

                payload = json.loads(envelope["data"])
                event = envelope["event"]

                if event == "answer_chunk" and isinstance(payload, dict):
                    acc = payload.get("accumulated")
                    if isinstance(acc, str):
                        accumulated_answer = acc

                if event == "error":
                    raise AssertionError(
                        f"stream emitted error event for chatMode={chat_mode!r}: {payload!r}"
                    )

                if event == "complete":
                    saw_complete = True
                    if not accumulated_answer.strip() and isinstance(payload, dict):
                        conv = payload.get("conversation") or {}
                        msgs = conv.get("messages") or []
                        for m in reversed(msgs if isinstance(msgs, list) else []):
                            if isinstance(m, dict) and m.get("role") == "assistant":
                                content = m.get("content")
                                if isinstance(content, str) and content.strip():
                                    accumulated_answer = content
                                    break
                    break

            assert saw_complete, (
                f"stream ended without a complete event for chatMode={chat_mode!r}"
            )
            assert accumulated_answer.strip(), (
                f"stream completed but answer text was empty for chatMode={chat_mode!r}"
            )

    @pytest.mark.parametrize(
        "payload",
        [
            {},
            {"query": ""},
        ],
    )
    def test_stream_conversation_invalid_payload_returns_400(self, payload: dict) -> None:
        headers = {**self.headers, "Accept": "text/event-stream"}

        resp = requests.post(
            self.conversation_stream_url,
            headers=headers,
            json=payload,
            timeout=self.timeout,
        )
        assert resp.status_code == 400, f"{resp.status_code}: {resp.text}"

    def test_stream_conversation_missing_auth_returns_401_or_403(self) -> None:
        headers = {"Accept": "text/event-stream"}

        resp = requests.post(
            f"{self.base_url}/api/v1/conversations/stream",
            headers=headers,
            json={"query": SEARCH_QUERY},
            timeout=self.timeout,
        )
        assert resp.status_code in (401, 403), f"{resp.status_code}: {resp.text}"

    def test_get_conversations_includes_two_stream_created(self) -> None:
        id_a = self._stream_create_conversation_id(
            query="get-conversations positive test conversation A",
        )
        id_b = self._stream_create_conversation_id(
            query="get-conversations positive test conversation B",
        )
        needed = {id_a, id_b}
        found: set[str] = set()
        first_list_body: dict | None = None
        page = 1

        while True:
            resp = requests.get(
                self.conversations_list_url,
                headers=self.headers,
                params={"source": "owned", "limit": 100, "page": page},
                timeout=self.timeout,
            )
            assert resp.status_code == 200, f"{resp.status_code}: {resp.text}"
            body = resp.json()
            if first_list_body is None:
                first_list_body = body

            for row in body.get("conversations") or []:
                if not isinstance(row, dict):
                    continue
                cid = row.get("_id")
                if isinstance(cid, str) and cid in needed:
                    found.add(cid)

            if needed <= found:
                break

            pagination = body.get("pagination") or {}
            if not pagination.get("hasNextPage"):
                pytest.fail(
                    f"Expected both new conversation ids in owned list; "
                    f"needed={needed}, found={found}, last_page={page}"
                )
            page += 1

        assert first_list_body is not None
        assert_response_matches_spec(
            first_list_body, "/conversations", "GET", 200,
        )

    def test_get_conversations_invalid_source_returns_400(self) -> None:
        resp = requests.get(
            self.conversations_list_url,
            headers=self.headers,
            params={"source": "not-owned"},
            timeout=self.timeout,
        )
        assert resp.status_code == 400, f"{resp.status_code}: {resp.text}"

    def test_get_conversation_by_id_response_matches_spec(self) -> None:
        conversation_id = self._stream_create_conversation_id(
            query="integration: get conversation by id",
        )
        resp = requests.get(
            f"{self.conversations_url}/{conversation_id}",
            headers=self.headers,
            timeout=self.timeout,
        )
        assert resp.status_code == 200, f"{resp.status_code}: {resp.text}"
        body = resp.json()
        assert body.get("conversation", {}).get("id") == conversation_id, (
            f"conversation.id mismatch: {body.get('conversation', {})!r}"
        )
        assert body.get("meta", {}).get("conversationId") == conversation_id, (
            f"meta.conversationId mismatch: {body.get('meta', {})!r}"
        )
        assert_response_matches_spec(
            body, "/conversations/{conversationId}", "GET", 200,
        )

    def test_get_conversation_by_id_invalid_conversation_id_returns_400(self) -> None:
        resp = requests.get(
            f"{self.conversations_url}/not-an-objectid",
            headers=self.headers,
            timeout=self.timeout,
        )
        assert resp.status_code == 400, f"{resp.status_code}: {resp.text}"

    def test_get_conversation_by_id_nonexistent_returns_404(self) -> None:
        resp = requests.get(
            f"{self.conversations_url}/{'0' * 24}",
            headers=self.headers,
            timeout=self.timeout,
        )
        assert resp.status_code == 404, f"{resp.status_code}: {resp.text}"

    def test_delete_conversation_lifecycle(self) -> None:
        conversation_id = self._stream_create_conversation_id(
            query="integration: delete conversation lifecycle",
        )
        url = f"{self.conversations_base_url}/{conversation_id}"

        get_before = requests.get(
            url, headers=self.headers, timeout=self.timeout
        )
        assert get_before.status_code == 200, (
            f"{get_before.status_code}: {get_before.text}"
        )
        conv = get_before.json().get("conversation") or {}
        assert conv.get("id") == conversation_id, (
            f"conversation.id mismatch before delete: {conv!r}"
        )

        del_resp = requests.delete(url, headers=self.headers, timeout=self.timeout)
        assert del_resp.status_code == 200, f"{del_resp.status_code}: {del_resp.text}"
        del_body = del_resp.json()
        assert del_body.get("status") == "deleted", f"unexpected delete body: {del_body!r}"
        assert del_body.get("id") == conversation_id, (
            f"delete response id mismatch: {del_body!r}"
        )

        get_after = requests.get(url, headers=self.headers, timeout=self.timeout)
        assert get_after.status_code == 404, (
            f"GET after delete should be 404, got "
            f"{get_after.status_code}: {get_after.text}"
        )

    def test_delete_conversation_invalid_conversation_id_returns_400(self) -> None:
        resp = requests.delete(
            f"{self.conversations_base_url}/not-an-objectid",
            headers=self.headers,
            timeout=self.timeout,
        )
        assert resp.status_code == 400, f"{resp.status_code}: {resp.text}"

    def test_delete_conversation_nonexistent_returns_404(self) -> None:
        resp = requests.delete(
            f"{self.conversations_base_url}/{'0' * 24}",
            headers=self.headers,
            timeout=self.timeout,
        )
        assert resp.status_code == 404, f"{resp.status_code}: {resp.text}"

    def test_patch_archive_conversation_lifecycle(self) -> None:
        conversation_id = self._stream_create_conversation_id(
            query="integration: archive conversation lifecycle",
        )
        url = f"{self.conversations_base_url}/{conversation_id}"
        archive_url = f"{url}/archive"

        archive_resp = requests.patch(
            archive_url,
            headers=self.headers,
            timeout=self.timeout,
        )
        assert archive_resp.status_code == 200, (
            f"{archive_resp.status_code}: {archive_resp.text}"
        )
        archive_body = archive_resp.json()

        assert archive_body.get("id") == conversation_id, (
            f"archive response id mismatch: expected {conversation_id!r}, "
            f"got {archive_body.get('id')!r}"
        )
        assert archive_body.get("status") == "archived", (
            f"status should be 'archived', got {archive_body.get('status')!r}"
        )

        assert_response_matches_spec(
            archive_body,
            "/conversations/{conversationId}/archive",
            "PATCH",
            200,
        )

        get_after = requests.get(url, headers=self.headers, timeout=self.timeout)
        assert get_after.status_code == 404, (
            f"GET after archive should be 404, got "
            f"{get_after.status_code}: {get_after.text}"
        )

        second_archive = requests.patch(
            archive_url,
            headers=self.headers,
            timeout=self.timeout,
        )
        assert second_archive.status_code == 400, (
            f"second archive should be 400, got "
            f"{second_archive.status_code}: {second_archive.text}"
        )

        unarchive_url = f"{url}/unarchive"
        unarchive_resp = requests.patch(
            unarchive_url,
            headers=self.headers,
            timeout=self.timeout,
        )
        assert unarchive_resp.status_code == 200, (
            f"{unarchive_resp.status_code}: {unarchive_resp.text}"
        )
        unarchive_body = unarchive_resp.json()

        assert unarchive_body.get("id") == conversation_id, (
            f"unarchive response id mismatch: expected {conversation_id!r}, "
            f"got {unarchive_body.get('id')!r}"
        )
        assert unarchive_body.get("status") == "unarchived", (
            f"status should be 'unarchived', got {unarchive_body.get('status')!r}"
        )

        assert_response_matches_spec(
            unarchive_body,
            "/conversations/{conversationId}/unarchive",
            "PATCH",
            200,
        )

        get_after_unarchive = requests.get(
            url, headers=self.headers, timeout=self.timeout,
        )
        assert get_after_unarchive.status_code == 200, (
            f"GET after unarchive should be 200, got "
            f"{get_after_unarchive.status_code}: {get_after_unarchive.text}"
        )
        conv = get_after_unarchive.json().get("conversation") or {}
        assert conv.get("id") == conversation_id, (
            f"GET conversation.id mismatch after unarchive: {conv!r}"
        )

        second_unarchive = requests.patch(
            unarchive_url,
            headers=self.headers,
            timeout=self.timeout,
        )
        assert second_unarchive.status_code == 400, (
            f"second unarchive should be 400, got "
            f"{second_unarchive.status_code}: {second_unarchive.text}"
        )

    def test_get_archived_conversations_includes_newly_archived_conversation(
        self,
    ) -> None:
        archives_url = f"{self.conversations_base_url}/show/archives"
        conversation_id = self._stream_create_conversation_id(
            query="integration: list archived conversations membership",
        )

        before = requests.get(
            archives_url,
            headers=self.headers,
            params={"conversationId": conversation_id},
            timeout=self.timeout,
        )
        assert before.status_code == 200, f"{before.status_code}: {before.text}"
        before_body = before.json()
        assert before_body.get("conversations") == [], (
            f"active conversation should not appear in archives: {before_body!r}"
        )
        assert (before_body.get("pagination") or {}).get("totalCount") == 0, (
            f"expected totalCount 0 before archive: {before_body!r}"
        )

        archive_resp = requests.patch(
            f"{self.conversations_base_url}/{conversation_id}/archive",
            headers=self.headers,
            timeout=self.timeout,
        )
        assert archive_resp.status_code == 200, (
            f"{archive_resp.status_code}: {archive_resp.text}"
        )
        archive_body = archive_resp.json()
        assert archive_body.get("id") == conversation_id, (
            f"archive id mismatch: {archive_body!r}"
        )
        assert archive_body.get("status") == "archived", (
            f"expected status archived: {archive_body!r}"
        )

        after = requests.get(
            archives_url,
            headers=self.headers,
            params={"conversationId": conversation_id},
            timeout=self.timeout,
        )
        assert after.status_code == 200, f"{after.status_code}: {after.text}"
        after_body = after.json()
        assert_response_matches_spec(
            after_body, "/conversations/show/archives", "GET", 200,
        )
        rows = after_body.get("conversations") or []
        assert len(rows) == 1, f"expected exactly one archived row: {rows!r}"
        assert rows[0].get("_id") == conversation_id, f"row id mismatch: {rows[0]!r}"
        assert (after_body.get("pagination") or {}).get("totalCount") == 1, (
            f"expected totalCount 1 for filtered archives: {after_body!r}"
        )

        page = 1
        list_body: dict | None = None
        found = False
        while True:
            resp = requests.get(
                archives_url,
                headers=self.headers,
                params={"page": page, "limit": 100},
                timeout=self.timeout,
            )
            assert resp.status_code == 200, f"{resp.status_code}: {resp.text}"
            body = resp.json()
            if list_body is None:
                list_body = body
                assert_response_matches_spec(
                    list_body, "/conversations/show/archives", "GET", 200,
                )
            for c in body.get("conversations") or []:
                if isinstance(c, dict) and c.get("_id") == conversation_id:
                    found = True
                    break
            if found:
                break
            pagination = body.get("pagination") or {}
            if not pagination.get("hasNextPage"):
                pytest.fail(
                    f"archived conversation {conversation_id!r} not found "
                    f"when paging archives; last page={page}"
                )
            page += 1

        unarchive_resp = requests.patch(
            f"{self.conversations_base_url}/{conversation_id}/unarchive",
            headers=self.headers,
            timeout=self.timeout,
        )
        assert unarchive_resp.status_code == 200, (
            f"{unarchive_resp.status_code}: {unarchive_resp.text}"
        )

    def test_get_archived_conversations_search_finds_after_create_and_archive(
        self,
    ) -> None:
        token = f"archsrch_{uuid.uuid4().hex}"
        conversation_id = self._stream_create_conversation_id(
            query=f"integration archived search {token}",
        )
        archive_url = f"{self.conversations_base_url}/{conversation_id}/archive"
        archive_resp = requests.patch(
            archive_url,
            headers=self.headers,
            timeout=self.timeout,
        )
        assert archive_resp.status_code == 200, (
            f"{archive_resp.status_code}: {archive_resp.text}"
        )
        archive_body = archive_resp.json()
        assert archive_body.get("id") == conversation_id, (
            f"archive id mismatch: {archive_body!r}"
        )
        assert archive_body.get("status") == "archived", (
            f"expected status archived: {archive_body!r}"
        )

        search_resp = requests.get(
            self.archived_conversations_search_url,
            headers=self.headers,
            params={"search": token, "limit": 20, "page": 1},
            timeout=self.timeout,
        )
        assert search_resp.status_code == 200, (
            f"{search_resp.status_code}: {search_resp.text}"
        )
        body = search_resp.json()
        assert_response_matches_spec(
            body, "/conversations/show/archives/search", "GET", 200,
        )
        summary = body.get("summary") or {}
        assert summary.get("searchQuery") == token, (
            f"summary.searchQuery mismatch: {summary!r}"
        )
        assert (summary.get("totalMatches") or 0) >= 1, (
            f"expected at least one match: {summary!r}"
        )
        rows = body.get("conversations") or []
        match = next(
            (
                r for r in rows
                if isinstance(r, dict) and r.get("_id") == conversation_id
            ),
            None,
        )
        assert match is not None, (
            f"conversation {conversation_id!r} not in search results: {rows!r}"
        )
        assert match.get("source") == "assistant", f"unexpected source: {match!r}"

        unarchive_resp = requests.patch(
            f"{self.conversations_base_url}/{conversation_id}/unarchive",
            headers=self.headers,
            timeout=self.timeout,
        )
        assert unarchive_resp.status_code == 200, (
            f"{unarchive_resp.status_code}: {unarchive_resp.text}"
        )

    def test_get_archived_conversations_search_missing_search_returns_400(
        self,
    ) -> None:
        resp = requests.get(
            self.archived_conversations_search_url,
            headers=self.headers,
            timeout=self.timeout,
        )
        assert resp.status_code == 400, f"{resp.status_code}: {resp.text}"

    def test_get_archived_conversations_search_empty_search_returns_400(
        self,
    ) -> None:
        resp = requests.get(
            self.archived_conversations_search_url,
            headers=self.headers,
            params={"search": "   "},
            timeout=self.timeout,
        )
        assert resp.status_code == 400, f"{resp.status_code}: {resp.text}"

    def test_get_archived_conversations_search_missing_auth_returns_401_or_403(
        self,
    ) -> None:
        resp = requests.get(
            f"{self.base_url}/api/v1/conversations/show/archives/search",
            headers={"Content-Type": "application/json"},
            params={"search": "any"},
            timeout=self.timeout,
        )
        assert resp.status_code in (401, 403), f"{resp.status_code}: {resp.text}"

    def test_get_archived_conversations_search_active_not_in_results(self) -> None:
        token = f"archsrch_active_{uuid.uuid4().hex}"
        conversation_id = self._stream_create_conversation_id(
            query=f"integration active not in archive search {token}",
        )
        search_resp = requests.get(
            self.archived_conversations_search_url,
            headers=self.headers,
            params={"search": token, "limit": 50},
            timeout=self.timeout,
        )
        assert search_resp.status_code == 200, (
            f"{search_resp.status_code}: {search_resp.text}"
        )
        body = search_resp.json()
        ids = {
            r.get("_id")
            for r in (body.get("conversations") or [])
            if isinstance(r, dict)
        }
        assert conversation_id not in ids, (
            f"active conversation {conversation_id!r} should not appear in "
            f"archived search: {body!r}"
        )

    def test_get_archived_conversations_missing_auth_returns_401_or_403(
        self,
    ) -> None:
        resp = requests.get(
            f"{self.base_url}/api/v1/conversations/show/archives",
            headers={"Content-Type": "application/json"},
            timeout=self.timeout,
        )
        assert resp.status_code in (401, 403), f"{resp.status_code}: {resp.text}"

    def test_get_archived_conversations_invalid_start_date_returns_400(
        self,
    ) -> None:
        resp = requests.get(
            f"{self.conversations_base_url}/show/archives",
            headers=self.headers,
            params={"startDate": "not-a-datetime"},
            timeout=self.timeout,
        )
        assert resp.status_code == 400, f"{resp.status_code}: {resp.text}"

    def test_patch_archive_conversation_invalid_conversation_id_returns_400(
        self,
    ) -> None:
        resp = requests.patch(
            f"{self.conversations_base_url}/not-an-objectid/archive",
            headers=self.headers,
            timeout=self.timeout,
        )
        assert resp.status_code == 400, f"{resp.status_code}: {resp.text}"

    def test_patch_archive_conversation_nonexistent_returns_404(self) -> None:
        resp = requests.patch(
            f"{self.conversations_base_url}/{'0' * 24}/archive",
            headers=self.headers,
            timeout=self.timeout,
        )
        assert resp.status_code == 404, f"{resp.status_code}: {resp.text}"

    def test_patch_unarchive_conversation_not_archived_returns_400(self) -> None:
        conversation_id = self._stream_create_conversation_id(
            query="integration: unarchive without archive",
        )
        resp = requests.patch(
            f"{self.conversations_base_url}/{conversation_id}/unarchive",
            headers=self.headers,
            timeout=self.timeout,
        )
        assert resp.status_code == 400, f"{resp.status_code}: {resp.text}"

    def test_patch_conversation_title_response_matches_spec(self) -> None:
        conversation_id = self._stream_create_conversation_id(
            query="integration: rename title",
        )
        new_title = "Renamed via integration test"

        patch_resp = requests.patch(
            f"{self.conversations_base_url}/{conversation_id}/title",
            headers=self.headers,
            json={"title": new_title},
            timeout=self.timeout,
        )
        assert patch_resp.status_code == 200, (
            f"{patch_resp.status_code}: {patch_resp.text}"
        )

        body = patch_resp.json()

        conv = body.get("conversation") or {}
        assert conv.get("_id") == conversation_id, (
            f"conversation._id mismatch: expected {conversation_id!r}, "
            f"got {conv.get('_id')!r}"
        )
        assert conv.get("title") == new_title, (
            f"conversation.title mismatch: expected {new_title!r}, "
            f"got {conv.get('title')!r}"
        )

        assert_response_matches_spec(
            body, "/conversations/{conversationId}/title", "PATCH", 200,
        )

        get_resp = requests.get(
            f"{self.conversations_base_url}/{conversation_id}",
            headers=self.headers,
            timeout=self.timeout,
        )
        assert get_resp.status_code == 200, (
            f"{get_resp.status_code}: {get_resp.text}"
        )
        get_conv = get_resp.json().get("conversation") or {}
        assert get_conv.get("id") == conversation_id, (
            f"GET conversation.id mismatch: {get_conv!r}"
        )
        assert get_conv.get("title") == new_title, (
            f"GET conversation.title did not persist: expected {new_title!r}, "
            f"got {get_conv.get('title')!r}"
        )

    def test_patch_conversation_title_invalid_conversation_id_returns_400(self) -> None:
        resp = requests.patch(
            f"{self.conversations_base_url}/not-an-objectid/title",
            headers=self.headers,
            json={"title": "x"},
            timeout=self.timeout,
        )
        assert resp.status_code == 400, f"{resp.status_code}: {resp.text}"

    def test_patch_conversation_title_nonexistent_returns_404(self) -> None:
        resp = requests.patch(
            f"{self.conversations_base_url}/{'0' * 24}/title",
            headers=self.headers,
            json={"title": "x"},
            timeout=self.timeout,
        )
        assert resp.status_code == 404, f"{resp.status_code}: {resp.text}"

    @pytest.mark.parametrize(
        "payload",
        [
            {},
            {"title": ""},
            {"title": "a" * 201},
            {"title": 123},
        ],
    )
    def test_patch_conversation_title_invalid_payload_returns_400(
        self, payload: dict,
    ) -> None:
        conversation_id = self._stream_create_conversation_id(
            query="integration: invalid title payload",
        )
        resp = requests.patch(
            f"{self.conversations_base_url}/{conversation_id}/title",
            headers=self.headers,
            json=payload,
            timeout=self.timeout,
        )
        assert resp.status_code == 400, f"{resp.status_code}: {resp.text}"

    # ------------------------------------------------------------------
    # POST /:conversationId/share
    # ------------------------------------------------------------------

    def test_post_share_conversation_response_matches_spec(self) -> None:
        if not SHARE_TARGET_USER_ID:
            pytest.skip("Set PIPESHUB_TEST_SHARE_TARGET_USER_ID to run this test.")

        conversation_id = self._stream_create_conversation_id(
            query="integration: share conversation happy path",
        )
        resp = requests.post(
            f"{self.conversations_base_url}/{conversation_id}/share",
            headers=self.headers,
            json={"userIds": [SHARE_TARGET_USER_ID], "accessLevel": "read"},
            timeout=self.timeout,
        )
        assert resp.status_code == 200, f"{resp.status_code}: {resp.text}"

        body = resp.json()
        assert body.get("id") == conversation_id, (
            f"share response id mismatch: expected {conversation_id!r}, "
            f"got {body.get('id')!r}"
        )
        assert body.get("isShared") is True, (
            f"isShared should be True after sharing, got {body.get('isShared')!r}"
        )

        shared_with = body.get("sharedWith") or []
        target = next(
            (e for e in shared_with if e.get("userId") == SHARE_TARGET_USER_ID),
            None,
        )
        assert target is not None, (
            f"sharedWith should include the target user, got {shared_with!r}"
        )
        assert target.get("accessLevel") == "read", (
            f"accessLevel should be 'read', got {target.get('accessLevel')!r}"
        )

        assert_response_matches_spec(
            body, "/conversations/{conversationId}/share", "POST", 200,
        )

    @pytest.mark.parametrize(
        "payload",
        [
            {},
            {"userIds": []},
            {"userIds": "not-an-array"},
            {"userIds": ["not-an-objectid"]},
        ],
    )
    def test_post_share_conversation_invalid_payload_returns_400(
        self, payload: dict,
    ) -> None:
        # A placeholder id is fine since validation runs before the lookup.
        resp = requests.post(
            f"{self.conversations_base_url}/{'0' * 24}/share",
            headers=self.headers,
            json=payload,
            timeout=self.timeout,
        )
        assert resp.status_code == 400, f"{resp.status_code}: {resp.text}"

    def test_post_share_conversation_nonexistent_returns_404(self) -> None:
        if not SHARE_TARGET_USER_ID:
            pytest.skip("Set PIPESHUB_TEST_SHARE_TARGET_USER_ID to run this test.")

        resp = requests.post(
            f"{self.conversations_base_url}/{'0' * 24}/share",
            headers=self.headers,
            json={"userIds": [SHARE_TARGET_USER_ID]},
            timeout=self.timeout,
        )
        assert resp.status_code == 404, f"{resp.status_code}: {resp.text}"

    def test_post_share_conversation_missing_auth_returns_401_or_403(
        self,
    ) -> None:
        resp = requests.post(
            f"{self.base_url}/api/v1/conversations/{'0' * 24}/share",
            headers={"Content-Type": "application/json"},
            json={"userIds": [SHARE_TARGET_USER_ID]},
            timeout=self.timeout,
        )
        assert resp.status_code in (401, 403), f"{resp.status_code}: {resp.text}"

    # ------------------------------------------------------------------
    # POST /:conversationId/unshare
    # ------------------------------------------------------------------

    def test_post_unshare_conversation_response_matches_spec(self) -> None:
        if not SHARE_TARGET_USER_ID:
            pytest.skip("Set PIPESHUB_TEST_SHARE_TARGET_USER_ID to run this test.")

        conversation_id = self._stream_create_conversation_id(
            query="integration: unshare conversation happy path",
        )

        share_resp = requests.post(
            f"{self.conversations_base_url}/{conversation_id}/share",
            headers=self.headers,
            json={"userIds": [SHARE_TARGET_USER_ID], "accessLevel": "read"},
            timeout=self.timeout,
        )
        assert share_resp.status_code == 200, (
            f"{share_resp.status_code}: {share_resp.text}"
        )

        unshare_resp = requests.post(
            f"{self.conversations_base_url}/{conversation_id}/unshare",
            headers=self.headers,
            json={"userIds": [SHARE_TARGET_USER_ID]},
            timeout=self.timeout,
        )
        assert unshare_resp.status_code == 200, (
            f"{unshare_resp.status_code}: {unshare_resp.text}"
        )

        body = unshare_resp.json()
        assert body.get("id") == conversation_id, (
            f"unshare response id mismatch: expected {conversation_id!r}, "
            f"got {body.get('id')!r}"
        )
        assert body.get("unsharedUsers") == [SHARE_TARGET_USER_ID], (
            f"unsharedUsers should echo request userIds, got "
            f"{body.get('unsharedUsers')!r}"
        )
        # The target user should no longer appear in the remaining share list.
        remaining_ids = [
            e.get("userId") for e in (body.get("sharedWith") or [])
        ]
        assert SHARE_TARGET_USER_ID not in remaining_ids, (
            f"target user should be removed from sharedWith, "
            f"got {remaining_ids!r}"
        )
        # The only shared user was just removed, so the conversation is private again.
        assert body.get("isShared") is False, (
            f"isShared should be False after removing the only sharee, "
            f"got {body.get('isShared')!r}"
        )

        assert_response_matches_spec(
            body, "/conversations/{conversationId}/unshare", "POST", 200,
        )

    @pytest.mark.parametrize(
        "payload",
        [
            {},
            {"userIds": []},
            {"userIds": "not-an-array"},
            {"userIds": ["not-an-objectid"]},
        ],
    )
    def test_post_unshare_conversation_invalid_payload_returns_400(
        self, payload: dict,
    ) -> None:
        resp = requests.post(
            f"{self.conversations_base_url}/{'0' * 24}/unshare",
            headers=self.headers,
            json=payload,
            timeout=self.timeout,
        )
        assert resp.status_code == 400, f"{resp.status_code}: {resp.text}"

    def test_stream_add_message_updates_conversation(self) -> None:
        conversation_id = self._stream_create_conversation_id(
            query="stream-create conversation for message-stream test"
        )

        headers = {**self.headers, "Accept": "text/event-stream"}
        url = self.message_stream_url_tpl.format(conversationId=conversation_id)

        with requests.post(
            url,
            headers=headers,
            json={"query": "follow-up question"},
            stream=True,
            timeout=self.stream_timeout,
        ) as resp:
            assert resp.status_code == 200, f"{resp.status_code}: {resp.text}"

            saw_complete = False
            for envelope in _iter_sse_envelopes(resp):
                assert_matches_component_schema(envelope, "AssistantStreamSSEEvent")
                if envelope["event"] == "error":
                    payload = json.loads(envelope["data"])
                    raise AssertionError(f"stream emitted error event: {payload!r}")
                if envelope["event"] != "complete":
                    continue

                saw_complete = True
                payload = json.loads(envelope["data"])
                conv = payload.get("conversation") or {}
                assert conv.get("_id") == conversation_id, (
                    f"complete conversation id mismatch: {conv.get('_id')!r}"
                )
                msgs = conv.get("messages") or []
                non_empty_contents = [
                    m.get("content") for m in msgs
                    if (m.get("content") or "").strip()
                ]
                assert len(non_empty_contents) >= 2, (
                    f"expected at least 2 non-empty message contents, got {len(non_empty_contents)}"
                )
                break

            assert saw_complete, "stream ended without a complete event"

    @pytest.mark.parametrize(
        "payload",
        [
            {},
            {"query": ""},
        ],
    )
    def test_stream_add_message_invalid_payload_returns_400(self, payload: dict) -> None:
        conversation_id = self._stream_create_conversation_id(
            query="stream-create conversation for invalid-payload test"
        )
        headers = {**self.headers, "Accept": "text/event-stream"}
        url = self.message_stream_url_tpl.format(conversationId=conversation_id)
        resp = requests.post(url, headers=headers, json=payload, timeout=self.timeout)
        assert resp.status_code == 400, f"{resp.status_code}: {resp.text}"

    def test_stream_add_message_invalid_conversation_id_returns_400(self) -> None:
        headers = {**self.headers, "Accept": "text/event-stream"}
        url = self.message_stream_url_tpl.format(conversationId="not-an-objectid")
        resp = requests.post(
            url, headers=headers, json={"query": "hi"}, timeout=self.timeout
        )
        assert resp.status_code == 400, f"{resp.status_code}: {resp.text}"

    def test_stream_add_message_missing_auth_returns_401_or_403(self) -> None:
        headers = {"Accept": "text/event-stream"}
        url = f"{self.base_url}/api/v1/conversations/{'0'*24}/messages/stream"
        resp = requests.post(url, headers=headers, json={"query": "hi"}, timeout=self.timeout)
        assert resp.status_code in (401, 403), f"{resp.status_code}: {resp.text}"

    def test_stream_add_message_nonexistent_conversation_emits_error_event(self) -> None:
        headers = {**self.headers, "Accept": "text/event-stream"}
        url = self.message_stream_url_tpl.format(conversationId="0" * 24)

        with requests.post(
            url,
            headers=headers,
            json={"query": "hi"},
            stream=True,
            timeout=self.stream_timeout,
        ) as resp:
            if resp.status_code != 200:
                assert resp.status_code == 404, f"{resp.status_code}: {resp.text}"
                return

            for envelope in _iter_sse_envelopes(resp):
                if envelope["event"] != "error":
                    continue
                payload = json.loads(envelope["data"])
                msg = payload.get("message") or payload.get("error") or ""
                assert "not found" in str(msg).lower(), f"unexpected error payload: {payload!r}"
                return

        raise AssertionError("stream ended without an error event")

    def test_regenerate_last_bot_message_streams_to_complete(self) -> None:
        conversation_id, message_id = self._stream_create_conversation_and_last_bot_message_id(
            query="integration: regenerate last bot message positive",
        )
        url = self.regenerate_url_tpl.format(
            conversationId=conversation_id,
            messageId=message_id,
        )
        headers = {**self.headers, "Accept": "text/event-stream"}

        with requests.post(
            url,
            headers=headers,
            json={},
            stream=True,
            timeout=self.stream_timeout,
        ) as resp:
            assert resp.status_code == 200, f"{resp.status_code}: {resp.text}"
            content_type = (resp.headers.get("Content-Type") or "").lower()
            assert "text/event-stream" in content_type, (
                f"expected text/event-stream, got Content-Type={resp.headers.get('Content-Type')!r}"
            )

            saw_complete = False
            for envelope in _iter_sse_envelopes(resp):
                assert_matches_component_schema(envelope, "AssistantStreamSSEEvent")
                if envelope["event"] == "error":
                    payload = json.loads(envelope["data"])
                    raise AssertionError(f"regenerate stream emitted error: {payload!r}")
                if envelope["event"] != "complete":
                    continue

                saw_complete = True
                payload = json.loads(envelope["data"])
                conv = payload.get("conversation") or {}
                assert conv.get("_id") == conversation_id, (
                    f"complete conversation id mismatch: {conv.get('_id')!r}"
                )

                msgs = conv.get("messages") or []
                assert msgs, f"complete payload missing messages: {conv!r}"
                last = msgs[-1]
                assert last.get("messageType") == "bot_response", (
                    f"expected last message bot_response, got {last.get('messageType')!r}"
                )
                content = last.get("content") or ""
                assert content.strip(), (
                    f"expected non-empty bot content, got {content!r}"
                )
                break

            assert saw_complete, "regenerate stream ended without a complete event"

    def test_regenerate_missing_auth_returns_401_or_403(self) -> None:
        url = (
            f"{self.base_url}/api/v1/conversations/{'0' * 24}/message/{'0' * 24}/regenerate"
        )
        resp = requests.post(
            url,
            headers={"Content-Type": "application/json"},
            json={},
            timeout=self.timeout,
        )
        assert resp.status_code in (401, 403), f"{resp.status_code}: {resp.text}"

    def test_regenerate_invalid_path_ids_returns_400(self) -> None:
        url = self.regenerate_url_tpl.format(
            conversationId="not-an-objectid",
            messageId="not-an-objectid",
        )
        resp = requests.post(
            url,
            headers=self.headers,
            json={},
            timeout=self.timeout,
        )
        assert resp.status_code == 400, f"{resp.status_code}: {resp.text}"

    def test_regenerate_invalid_body_returns_400(self) -> None:
        conversation_id, message_id = self._stream_create_conversation_and_last_bot_message_id(
            query="integration: regenerate invalid body",
        )
        url = self.regenerate_url_tpl.format(
            conversationId=conversation_id,
            messageId=message_id,
        )
        resp = requests.post(
            url,
            headers=self.headers,
            json={"currentTime": "not-an-iso-datetime"},
            timeout=self.timeout,
        )
        assert resp.status_code == 400, f"{resp.status_code}: {resp.text}"

    def test_regenerate_non_last_message_id_emits_sse_error(self) -> None:
        conversation_id, _ = self._stream_create_conversation_and_last_bot_message_id(
            query="integration: regenerate wrong message id",
        )
        get_resp = requests.get(
            f"{self.conversations_url}/{conversation_id}",
            headers=self.headers,
            timeout=self.timeout,
        )
        assert get_resp.status_code == 200, f"{get_resp.status_code}: {get_resp.text}"
        conv = get_resp.json().get("conversation") or {}
        msgs = conv.get("messages") or []
        user_query_id: str | None = None
        for m in msgs if isinstance(msgs, list) else []:
            if not isinstance(m, dict):
                continue
            if m.get("messageType") != "user_query":
                continue
            mid = m.get("_id") or m.get("id")
            if isinstance(mid, str) and mid:
                user_query_id = mid
                break
        assert user_query_id, f"no user_query message id in conversation: {msgs!r}"

        url = self.regenerate_url_tpl.format(
            conversationId=conversation_id,
            messageId=user_query_id,
        )
        headers = {**self.headers, "Accept": "text/event-stream"}

        with requests.post(
            url,
            headers=headers,
            json={},
            stream=True,
            timeout=self.stream_timeout,
        ) as resp:
            assert resp.status_code == 200, f"{resp.status_code}: {resp.text}"

            for envelope in _iter_sse_envelopes(resp):
                if envelope["event"] != "error":
                    continue
                payload = json.loads(envelope["data"])
                err = payload.get("message") or payload.get("error") or ""
                assert "last message" in str(err).lower(), (
                    f"unexpected error payload: {payload!r}"
                )
                return

        raise AssertionError("regenerate stream ended without an error event")

    def test_post_message_feedback_on_bot_response_matches_spec(self) -> None:
        conversation_id, bot_id, _user_id = (
            self._stream_create_conversation_bot_and_user_message_ids(
                query="integration: message feedback positive",
            )
        )
        url = self.feedback_url_tpl.format(
            conversationId=conversation_id,
            messageId=bot_id,
        )
        payload = {
            "isHelpful": True,
            "ratings": {"accuracy": 5, "relevance": 4},
            "categories": ["excellent_answer"],
            "comments": {
                "positive": "Clear and useful.",
                "negative": "",
                "suggestions": "More examples would help.",
            },
            "metrics": {
                "userInteractionTime": 1200,
                "feedbackSessionId": "integration-test-session",
            },
        }
        resp = requests.post(
            url, headers=self.headers, json=payload, timeout=self.timeout
        )
        assert resp.status_code == 200, f"{resp.status_code}: {resp.text}"
        body = resp.json()
        assert body.get("conversationId") == conversation_id
        assert body.get("messageId") == bot_id
        assert_response_matches_spec(
            body,
            "/conversations/{conversationId}/message/{messageId}/feedback",
            "POST",
            200,
        )

    def test_post_message_feedback_on_user_query_returns_400(self) -> None:
        conversation_id, _bot_id, user_id = (
            self._stream_create_conversation_bot_and_user_message_ids(
                query="integration: message feedback negative user_query",
            )
        )
        url = self.feedback_url_tpl.format(
            conversationId=conversation_id,
            messageId=user_id,
        )
        resp = requests.post(
            url, headers=self.headers, json={}, timeout=self.timeout
        )
        assert resp.status_code == 400, f"{resp.status_code}: {resp.text}"
        lowered = resp.text.lower()
        assert "bot" in lowered or "feedback" in lowered, (
            f"unexpected error body: {resp.text!r}"
        )

    # Reading a conversation sorted oldest first or newest first should both work.
    @pytest.mark.parametrize("sort_order", ["asc", "desc"])
    def test_get_conversation_by_id_with_sort_order_variants(
        self, sort_order: str,
    ) -> None:
        conversation_id = self._stream_create_conversation_id(
            query=f"integration: get conversation sort {sort_order}",
        )
        resp = requests.get(
            f"{self.conversations_base_url}/{conversation_id}",
            headers=self.headers,
            params={"sortOrder": sort_order},
            timeout=self.timeout,
        )
        assert resp.status_code == 200, f"{resp.status_code}: {resp.text}"
        body = resp.json()
        conv = body.get("conversation") or {}
        assert conv.get("id") == conversation_id, (
            f"conversation.id mismatch: {conv!r}"
        )
        assert_response_matches_spec(
            body, "/conversations/{conversationId}", "GET", 200,
        )

    # Asking for one message at a time caps how many come back.
    def test_get_conversation_by_id_with_limit_caps_messages(self) -> None:
        conversation_id = self._stream_create_conversation_id(
            query="integration: get conversation paginated messages",
        )
        resp = requests.get(
            f"{self.conversations_base_url}/{conversation_id}",
            headers=self.headers,
            params={"limit": 1, "page": 1},
            timeout=self.timeout,
        )
        assert resp.status_code == 200, f"{resp.status_code}: {resp.text}"
        body = resp.json()
        conv = body.get("conversation") or {}
        msgs = conv.get("messages") or []
        assert len(msgs) <= 1, (
            f"limit=1 should cap messages at 1, got {len(msgs)}"
        )
        assert_response_matches_spec(
            body, "/conversations/{conversationId}", "GET", 200,
        )

    # A small page on archived search returns at most that many rows.
    def test_get_archived_conversations_search_with_limit_caps_rows(self) -> None:
        token = f"archsrch_pg_{uuid.uuid4().hex}"
        archived_ids: list[str] = []
        for _ in range(2):
            cid = self._stream_create_conversation_id(
                query=f"integration archived pagination {token}",
            )
            archive_resp = requests.patch(
                f"{self.conversations_base_url}/{cid}/archive",
                headers=self.headers,
                timeout=self.timeout,
            )
            assert archive_resp.status_code == 200, (
                f"{archive_resp.status_code}: {archive_resp.text}"
            )
            archived_ids.append(cid)

        try:
            resp = requests.get(
                self.archived_conversations_search_url,
                headers=self.headers,
                params={"search": token, "limit": 1, "page": 1},
                timeout=self.timeout,
            )
            assert resp.status_code == 200, f"{resp.status_code}: {resp.text}"
            body = resp.json()
            rows = body.get("conversations") or []
            assert len(rows) <= 1, (
                f"limit=1 should cap rows at 1, got {len(rows)}"
            )
            assert_response_matches_spec(
                body, "/conversations/show/archives/search", "GET", 200,
            )
        finally:
            for cid in archived_ids:
                requests.patch(
                    f"{self.conversations_base_url}/{cid}/unarchive",
                    headers=self.headers,
                    timeout=self.timeout,
                )
