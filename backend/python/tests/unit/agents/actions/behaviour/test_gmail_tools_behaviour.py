"""Behaviour tests for the Gmail agent tools.

Each test drives a tool the way the agent does and checks what Gmail would
receive and what the agent is told back. See ``gmail_tool_fakes`` for what is
real and what is faked.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest
from gcal_tool_fakes import (
    ACCESS_TOKEN,
    CLIENT_SECRET,
    REFRESH_TOKEN,
    TOKEN_PATH,
    FakeGoogleHttp,
    GoogleResponse,
    google_error,
    result,
)
from gmail_tool_fakes import (
    DRAFTS,
    GMAIL,
    MESSAGES,
    SEND,
    attachment_part,
    build_gmail_tool,
    headers,
    sent_mime,
)

from app.agent_loop_lib.tools.decorators import TOOL_META_ATTR

if TYPE_CHECKING:
    from pathlib import Path

    from app.agents.actions.google.gmail.gmail import Gmail

PENDING = {
    "files": "mail_attachments reads any file path the model names from the server and mails it",
    "errors": "Gmail failures reach the agent as the raw HttpError text, with no next step",
    "attachments": "attachments inside nested parts are missed, and an inline one crashes the tool",
    "profile": "get_user_profile reads whichever mailbox the model names",
    "reply": "reply does not thread: it never reads the original's Message-ID or thread",
    "search": "a search whose message details could not be read is presented as complete",
    "recipients": "an empty or address-less recipient list goes to Gmail",
}


def pending(key: str) -> pytest.MarkDecorator:
    return pytest.mark.xfail(strict=True, reason=PENDING[key])


@pytest.fixture(autouse=True)
def no_retry_sleep(monkeypatch: pytest.MonkeyPatch) -> None:
    """googleapiclient backs off between its own retries; the tests don't wait for it."""
    monkeypatch.setattr("googleapiclient.http.time.sleep", lambda _seconds: None)


@pytest.fixture
def http() -> FakeGoogleHttp:
    return FakeGoogleHttp()


@pytest.fixture
def gmail(http: FakeGoogleHttp) -> Gmail:
    return build_gmail_tool(http)


def assert_safe_error(payload: dict[str, Any]) -> str:
    """The error is plain text the agent can relay: no secrets, no raw library dump."""
    message = payload["error"]
    assert isinstance(message, str) and message
    for leaked in (ACCESS_TOKEN, REFRESH_TOKEN, CLIENT_SECRET, "Bearer", "HttpError", "googleapis.com", "<"):
        assert leaked not in message, f"{leaked!r} leaked into: {message}"
    return message


def sends(http: FakeGoogleHttp) -> list[Any]:
    return http.calls("POST", SEND, base=GMAIL)


def metadata(message_id: str, subject: str, sender: str = "ada@example.com", **extra: object) -> dict[str, Any]:
    return {
        "id": message_id, "threadId": f"t-{message_id}", "snippet": f"about {subject}", "labelIds": ["INBOX"],
        "payload": {"headers": headers(("Subject", subject), ("From", sender), ("To", "me@example.com"),
                                       ("Date", "Mon, 28 Sep 2026 10:00:00 +0000"))},
        **extra,
    }


# ---------------------------------------------------------------------------
# send_email / draft_email
# ---------------------------------------------------------------------------


class TestSend:
    async def test_the_message_goes_out_from_the_signed_in_mailbox_with_every_recipient(self, gmail, http) -> None:
        http.on("POST", SEND, {"id": "sent-1", "threadId": "t-9"}, base=GMAIL)

        ok, data = result(await gmail.send_email(
            mail_to=["ada@example.com", "Grace Hopper <grace@example.com>"], mail_subject="Q3 plan",
            mail_cc=["cc@example.com"], mail_bcc=["bcc@example.com"], mail_body="Hi\nteam",
        ))

        assert ok is True and data["message_id"] == "sent-1"
        [request] = sends(http)
        assert request.headers["authorization"] == f"Bearer {ACCESS_TOKEN}"
        mime = sent_mime(request.body)
        assert mime["To"] == "ada@example.com, Grace Hopper <grace@example.com>"
        assert (mime["Cc"], mime["Bcc"], mime["Subject"]) == ("cc@example.com", "bcc@example.com", "Q3 plan")
        assert "Hi<br>team" in mime.get_payload(decode=True).decode()

    async def test_a_draft_is_saved_not_sent(self, gmail, http) -> None:
        http.on("POST", DRAFTS, {"id": "d-1", "message": {"id": "m-1"}}, base=GMAIL)

        ok, data = result(await gmail.draft_email(mail_to=["ada@example.com"], mail_subject="Draft"))

        assert ok is True and data["draft_id"] == "d-1"
        assert sends(http) == []
        assert sent_mime(http.calls("POST", DRAFTS, base=GMAIL)[0].body)["Subject"] == "Draft"

    async def test_a_refused_send_is_not_reported_as_sent(self, gmail, http) -> None:
        http.on("POST", SEND, google_error(400, "Invalid To header", "invalidArgument"), base=GMAIL)

        ok, data = result(await gmail.send_email(mail_to=["ada@example.com"], mail_subject="x"))

        assert ok is False
        assert "message_id" not in data

    @pytest.mark.parametrize("tool", ["send_email", "draft_email", "reply"])
    async def test_a_server_file_path_is_never_attached(self, gmail, http, tmp_path: Path, tool) -> None:
        secret = tmp_path / "service.env"
        secret.write_text("DB_PASSWORD=hunter2")
        http.on("GET", f"{MESSAGES}/m-1", metadata("m-1", "Hello"), base=GMAIL)
        args: dict[str, Any] = {"mail_to": ["ada@example.com"], "mail_subject": "Report", "mail_attachments": [str(secret)]}
        if tool == "reply":
            args["message_id"] = "m-1"

        ok, data = result(await getattr(gmail, tool)(**args))

        sent = [r for r in http.requests if r.method == "POST"]
        assert sent == [], "the file was mailed"
        assert ok is False
        assert "attachment_record_ids" in assert_safe_error(data)

    @pytest.mark.parametrize("tool", ["send_email", "draft_email", "reply"])
    def test_the_model_is_not_offered_file_paths_to_attach(self, gmail, tool) -> None:
        names = [p.name for p in getattr(getattr(gmail, tool), TOOL_META_ATTR).parameters]
        assert "mail_attachments" not in names
        assert "attachment_record_ids" in names

    @pending("recipients")
    @pytest.mark.parametrize("mail_to", [[], ["Ada Lovelace"], ["ada@example.com", "  "]])
    async def test_recipients_without_an_address_are_refused_before_sending(self, gmail, http, mail_to) -> None:
        ok, data = result(await gmail.send_email(mail_to=mail_to, mail_subject="Hi"))

        assert http.requests == []
        assert ok is False
        assert "address" in assert_safe_error(data)

    @pending("recipients")
    async def test_a_cc_without_an_address_is_refused_before_sending(self, gmail, http) -> None:
        ok, data = result(await gmail.draft_email(mail_to=["ada@example.com"], mail_subject="Hi", mail_cc=["the team"]))

        assert http.requests == []
        assert ok is False
        assert "the team" in assert_safe_error(data)


# ---------------------------------------------------------------------------
# reply
# ---------------------------------------------------------------------------


class TestReply:
    async def test_a_reply_joins_the_original_thread_with_its_message_id_headers(self, gmail, http) -> None:
        original = {
            "id": "m-1", "threadId": "thread-7",
            "payload": {"headers": headers(
                ("Subject", "Budget"), ("Message-ID", "<orig@mail.example.com>"),
                ("References", "<first@mail.example.com>"),
            )},
        }
        http.on("GET", f"{MESSAGES}/m-1", original, base=GMAIL)
        http.on("POST", SEND, {"id": "sent-2", "threadId": "thread-7"}, base=GMAIL)

        ok, _ = result(await gmail.reply(message_id="m-1", mail_to=["ada@example.com"], mail_subject="Re: Budget",
                                         mail_body="Agreed"))

        assert ok is True
        [send] = sends(http)
        assert send.body["threadId"] == "thread-7"
        mime = sent_mime(send.body)
        assert mime["In-Reply-To"] == "<orig@mail.example.com>"
        assert mime["References"] == "<first@mail.example.com> <orig@mail.example.com>"

    async def test_a_reply_to_a_message_that_cannot_be_read_is_not_sent(self, gmail, http) -> None:
        http.on("GET", f"{MESSAGES}/m-gone", google_error(404, "Requested entity was not found.", "notFound"), base=GMAIL)

        ok, data = result(await gmail.reply(message_id="m-gone", mail_to=["ada@example.com"], mail_subject="Re: x"))

        assert ok is False
        assert "search_emails" in assert_safe_error(data)
        assert sends(http) == []

    async def test_an_email_sent_in_answer_to_a_message_threads_the_same_way(self, gmail, http) -> None:
        http.on("GET", f"{MESSAGES}/m-1", {"id": "m-1", "threadId": "thread-7", "payload": {
            "headers": headers(("Message-ID", "<orig@mail.example.com>"))}}, base=GMAIL)
        http.on("POST", SEND, {"id": "sent-3"}, base=GMAIL)

        ok, _ = result(await gmail.send_email(mail_to=["ada@example.com"], mail_subject="Re: Budget",
                                              thread_id="thread-7", message_id="m-1"))

        assert ok is True
        [send] = sends(http)
        assert send.body["threadId"] == "thread-7"
        assert (sent_mime(send.body)["In-Reply-To"], sent_mime(send.body)["References"]) == (
            "<orig@mail.example.com>", "<orig@mail.example.com>")


# ---------------------------------------------------------------------------
# search_emails
# ---------------------------------------------------------------------------


class TestSearch:
    async def test_search_lists_matches_with_their_headers(self, gmail, http) -> None:
        http.on("GET", MESSAGES, {"messages": [{"id": "m-1", "threadId": "t-m-1"}, {"id": "m-2", "threadId": "t-m-2"}],
                                  "nextPageToken": "p2", "resultSizeEstimate": 40}, base=GMAIL)
        http.on("GET", f"{MESSAGES}/m-1", metadata("m-1", "Invoice"), base=GMAIL)
        http.on("GET", f"{MESSAGES}/m-2", metadata("m-2", "Standup"), base=GMAIL)

        ok, data = result(await gmail.search_emails(query="from:ada is:unread", max_results=2, page_token="p1"))

        assert ok is True
        list_call = http.calls("GET", MESSAGES, base=GMAIL)[0]
        assert list_call.query == {"q": "from:ada is:unread", "maxResults": "2", "pageToken": "p1", "alt": "json"}
        assert [m["subject"] for m in data["messages"]] == ["Invoice", "Standup"]
        assert data["messages"][0]["from"] == "ada@example.com"
        assert (data["nextPageToken"], data["resultSizeEstimate"]) == ("p2", 40)
        assert {c.query["format"] for c in http.calls("GET", f"{MESSAGES}/m-.", base=GMAIL)} == {"metadata"}

    async def test_a_message_deleted_since_the_search_is_left_out(self, gmail, http) -> None:
        http.on("GET", MESSAGES, {"messages": [{"id": "m-1"}, {"id": "m-2"}]}, base=GMAIL)
        http.on("GET", f"{MESSAGES}/m-1", metadata("m-1", "Kept"), base=GMAIL)
        http.on("GET", f"{MESSAGES}/m-2", google_error(404, "Not Found", "notFound"), base=GMAIL)

        ok, data = result(await gmail.search_emails(query="x"))

        assert ok is True
        assert [m["id"] for m in data["messages"]] == ["m-1"]

    async def test_no_matches_is_an_empty_list(self, gmail, http) -> None:
        http.on("GET", MESSAGES, {"resultSizeEstimate": 0}, base=GMAIL)

        ok, data = result(await gmail.search_emails(query="nothing"))

        assert ok is True and data["messages"] == []

    async def test_messages_whose_details_could_not_be_read_are_called_out(self, gmail, http) -> None:
        http.on("GET", MESSAGES, {"messages": [{"id": "m-1"}, {"id": "m-2"}]}, base=GMAIL)
        http.on("GET", f"{MESSAGES}/m-1", metadata("m-1", "Kept"), base=GMAIL)
        http.on("GET", f"{MESSAGES}/m-2", google_error(500, "Backend Error", "backendError"), base=GMAIL)

        ok, data = result(await gmail.search_emails(query="x"))

        assert ok is True
        assert [m["id"] for m in data["messages"]] == ["m-1", "m-2"]
        assert data["unreadable_message_ids"] == ["m-2"]
        assert "1 of 2" in data["note"] and "get_email_details" in data["note"]

    @pytest.mark.parametrize("max_results", [0, -5, 501])
    async def test_a_result_count_gmail_cannot_serve_is_refused_up_front(self, gmail, http, max_results) -> None:
        ok, data = result(await gmail.search_emails(query="x", max_results=max_results))

        assert ok is False
        assert "500" in assert_safe_error(data)
        assert http.requests == []


# ---------------------------------------------------------------------------
# get_email_details / get_email_attachments / get_user_profile
# ---------------------------------------------------------------------------


class TestReadOne:
    async def test_details_read_the_full_message(self, gmail, http) -> None:
        http.on("GET", f"{MESSAGES}/m-1", metadata("m-1", "Hello"), base=GMAIL)

        ok, data = result(await gmail.get_email_details(message_id="m-1"))

        assert ok is True and data["id"] == "m-1"
        assert http.calls("GET", f"{MESSAGES}/m-1", base=GMAIL)[0].query == {"format": "full", "alt": "json"}

    async def test_top_level_attachments_are_listed(self, gmail, http) -> None:
        http.on("GET", f"{MESSAGES}/m-1", {"id": "m-1", "payload": {"parts": [
            {"mimeType": "text/plain", "filename": "", "body": {"size": 5}},
            attachment_part("report.pdf", "att-1", size=2048),
        ]}}, base=GMAIL)

        ok, data = result(await gmail.get_email_attachments(message_id="m-1"))

        assert ok is True
        assert data == [{"attachment_id": "att-1", "filename": "report.pdf", "mime_type": "application/pdf", "size": 2048}]

    async def test_attachments_inside_nested_parts_are_listed(self, gmail, http) -> None:
        http.on("GET", f"{MESSAGES}/m-1", {"id": "m-1", "payload": {"mimeType": "multipart/mixed", "parts": [
            {"mimeType": "multipart/related", "parts": [
                {"mimeType": "multipart/alternative", "parts": [{"mimeType": "text/html", "body": {"size": 9}}]},
                attachment_part("logo.png", "att-img", mime="image/png"),
            ]},
            attachment_part("report.pdf", "att-pdf"),
        ]}}, base=GMAIL)

        ok, data = result(await gmail.get_email_attachments(message_id="m-1"))

        assert ok is True
        assert sorted(a["filename"] for a in data) == ["logo.png", "report.pdf"]

    async def test_a_small_attachment_carried_inline_does_not_break_the_list(self, gmail, http) -> None:
        http.on("GET", f"{MESSAGES}/m-1", {"id": "m-1", "payload": {"parts": [
            {"filename": "note.txt", "mimeType": "text/plain", "body": {"data": "aGk=", "size": 2}},
            attachment_part("report.pdf", "att-1"),
        ]}}, base=GMAIL)

        ok, data = result(await gmail.get_email_attachments(message_id="m-1"))

        assert ok is True
        assert [(a["filename"], a["attachment_id"]) for a in data] == [("note.txt", None), ("report.pdf", "att-1")]

    async def test_a_message_that_is_itself_one_attachment_lists_it(self, gmail, http) -> None:
        http.on("GET", f"{MESSAGES}/m-1", {"id": "m-1", "payload": attachment_part("scan.pdf", "att-9")}, base=GMAIL)

        ok, data = result(await gmail.get_email_attachments(message_id="m-1"))

        assert ok is True
        assert [a["filename"] for a in data] == ["scan.pdf"]

    async def test_profile_is_the_signed_in_users(self, gmail, http) -> None:
        http.on("GET", "/users/me/profile", {"emailAddress": "me@example.com", "messagesTotal": 12,
                                              "threadsTotal": 7, "historyId": "99"}, base=GMAIL)

        ok, data = result(await gmail.get_user_profile())

        assert ok is True
        assert data == {"email_address": "me@example.com", "messages_total": 12, "threads_total": 7, "history_id": "99"}

    async def test_another_persons_mailbox_is_never_asked_for(self, gmail, http) -> None:
        ok, data = result(await gmail.get_user_profile(user_id="ceo@example.com"))

        assert ok is False
        assert "signed-in" in assert_safe_error(data)
        assert http.requests == []


# ---------------------------------------------------------------------------
# Failures: plain language, a next step, nothing secret
# ---------------------------------------------------------------------------


class TestFailures:
    @pytest.mark.parametrize(("response", "expected"), [
        (google_error(401, "Invalid Credentials", "authError"), "Reconnect the Gmail toolset"),
        (google_error(403, "Request had insufficient authentication scopes.", "insufficientPermissions"),
         "allow Gmail access"),
        (google_error(404, "Requested entity was not found.", "notFound"), "search_emails"),
        (google_error(500, "Backend Error", "backendError"), "Try again"),
    ])
    async def test_failures_are_explained_with_a_next_step(self, gmail, http, response, expected) -> None:
        http.on("GET", f"{MESSAGES}/m-1", response, base=GMAIL)

        ok, data = result(await gmail.get_email_details(message_id="m-1"))

        assert ok is False
        assert expected in assert_safe_error(data)

    async def test_rate_limit_is_retried_then_tells_the_agent_to_wait(self, gmail, http) -> None:
        http.on("GET", MESSAGES, google_error(429, "Too many requests", "rateLimitExceeded", {"retry-after": "20"}),
                base=GMAIL)

        ok, data = result(await gmail.search_emails(query="x"))

        assert ok is False
        assert "Wait 20 seconds" in assert_safe_error(data)
        assert len(http.calls("GET", MESSAGES, base=GMAIL)) == 4

    async def test_a_revoked_sign_in_says_to_reconnect(self, http) -> None:
        gmail = build_gmail_tool(http, refreshable=True)
        http.on("GET", "/users/me/profile", google_error(401, "Invalid Credentials", "authError"), base=GMAIL)
        http.on("POST", TOKEN_PATH, GoogleResponse(400, {"error": "invalid_grant", "error_description": "revoked"}),
                base="")

        ok, data = result(await gmail.get_user_profile())

        assert ok is False
        assert "Reconnect the Gmail toolset" in assert_safe_error(data)

    async def test_a_failed_send_explains_itself(self, gmail, http) -> None:
        http.on("POST", SEND, google_error(403, "Daily sending quota exceeded", "dailyLimitExceeded"), base=GMAIL)

        ok, data = result(await gmail.send_email(mail_to=["ada@example.com"], mail_subject="x"))

        assert ok is False
        assert "sending" in assert_safe_error(data).lower()
