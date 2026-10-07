"""A small, stateful Gmail API, served through ``FakeGoogleHttp``.

Each user has a mailbox of messages grouped into threads, and a history log the way
Gmail keeps one: every delivery, trash, label change and permanent delete gets the
next history id, ``users.history.list`` returns the records after ``startHistoryId``
(narrowed by ``labelId`` and ``historyTypes``), and an id older than the mailbox's
retained history answers 404, which is Gmail's signal to do a full sync.

Callers are identified by the token they send: a service account impersonating a
user may only read that user's mailbox, and ``userId="me"`` means the caller.
Listings page with a small page size so every walk has to follow ``nextPageToken``.
Admin Directory and Drive (for Drive-linked attachments) come from ``DriveWorld`` on
the same fake HTTP, so they are not re-implemented here.
"""

from __future__ import annotations

import base64
import itertools
import re
from dataclasses import dataclass, field
from typing import Any, Optional
from urllib.parse import parse_qs, unquote, urlsplit

from google_behaviour_fakes import (
    ApiRequest,
    FakeGoogleHttp,
    google_error,
    paginate,
)

GMAIL_API = "/gmail/v1/users/([^/]+)"
_HISTORY_TYPES = {
    "messageAdded": "messagesAdded",
    "messageDeleted": "messagesDeleted",
    "labelAdded": "labelsAdded",
    "labelRemoved": "labelsRemoved",
}


def b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode()


@dataclass
class Message:
    id: str
    thread_id: str
    labels: list[str]
    internal_date: int
    headers: dict[str, str]
    body_html: str
    attachments: list[dict[str, Any]] = field(default_factory=list)
    deleted: bool = False
    history_id: int = 0

    def resource(self) -> dict[str, Any]:
        parts: list[dict[str, Any]] = [
            {"partId": "0", "mimeType": "text/html", "filename": "", "body": {"size": len(self.body_html), "data": b64(self.body_html.encode())}}
        ]
        for n, att in enumerate(self.attachments, start=1):
            parts.append({
                "partId": str(n),
                "mimeType": att["mime"],
                "filename": att["filename"],
                "body": {"attachmentId": att["attachment_id"], "size": len(att["data"])},
            })
        return {
            "id": self.id,
            "threadId": self.thread_id,
            "labelIds": list(self.labels),
            "internalDate": str(self.internal_date),
            "historyId": str(self.history_id),
            "snippet": self.body_html[:40],
            "payload": {
                "mimeType": "multipart/mixed",
                "headers": [{"name": k, "value": v} for k, v in self.headers.items()],
                "parts": parts,
            },
        }


@dataclass
class _HistoryRecord:
    id: int
    message_id: str
    thread_id: str
    kind: str
    label_ids: list[str]
    labels_before: set[str]
    labels_after: set[str]


class Mailbox:
    def __init__(self, email: str) -> None:
        self.email = email
        self.messages: dict[str, Message] = {}
        self.history: list[_HistoryRecord] = []
        self.history_floor = 0


class GmailWorld:
    def __init__(self, http: FakeGoogleHttp, *, page_size: int = 2) -> None:
        self.http = http
        self.page_size = page_size
        self.history_page_size = page_size
        self.mailboxes: dict[str, Mailbox] = {}
        self.aliases: dict[str, str] = {}
        self._history_ids = itertools.count(100)
        self._attachment_ids = itertools.count(1)
        self._install_routes()

    # --- building the world --------------------------------------------------

    def add_mailbox(self, email: str) -> Mailbox:
        self.mailboxes[email] = Mailbox(email)
        return self.mailboxes[email]

    def deliver(
        self,
        owner: str,
        message_id: str,
        *,
        thread_id: Optional[str] = None,
        subject: str = "Hello",
        sender: str = "someone@elsewhere.com",
        to: Optional[str] = None,
        labels: Optional[list[str]] = None,
        date_ms: int = 1_700_000_000_000,
        body: str = "<p>hi</p>",
        attachments: Optional[list[tuple[str, str, bytes]]] = None,
    ) -> Message:
        """Put a message in ``owner``'s mailbox; ``labels`` default to INBOX, or SENT when ``owner`` sent it."""
        default_labels = ["SENT"] if sender.endswith(f"<{owner}>") or sender == owner else ["INBOX", "UNREAD"]
        message = Message(
            id=message_id,
            thread_id=thread_id or message_id,
            labels=list(labels or default_labels),
            internal_date=date_ms,
            headers={"Subject": subject, "From": sender, "To": to or owner, "Message-ID": f"<{message_id}@mail>"},
            body_html=body,
            attachments=[
                {"filename": name, "mime": mime, "data": data, "attachment_id": f"att-{next(self._attachment_ids)}"}
                for name, mime, data in (attachments or [])
            ],
        )
        mailbox = self.mailboxes[owner]
        mailbox.messages[message_id] = message
        self._record(mailbox, message, "messagesAdded", [], set(), set(message.labels))
        return message

    def add_labels(self, owner: str, message_id: str, *labels: str) -> None:
        mailbox = self.mailboxes[owner]
        message = mailbox.messages[message_id]
        before = set(message.labels)
        message.labels = sorted(before | set(labels))
        self._record(mailbox, message, "labelsAdded", list(labels), before, set(message.labels))

    def trash(self, owner: str, message_id: str) -> None:
        mailbox = self.mailboxes[owner]
        message = mailbox.messages[message_id]
        before = set(message.labels)
        message.labels = sorted((before - {"INBOX"}) | {"TRASH"})
        self._record(mailbox, message, "labelsAdded", ["TRASH"], before, set(message.labels))
        if "INBOX" in before:
            self._record(mailbox, message, "labelsRemoved", ["INBOX"], before, set(message.labels))

    def delete(self, owner: str, message_id: str) -> None:
        mailbox = self.mailboxes[owner]
        message = mailbox.messages[message_id]
        message.deleted = True
        self._record(mailbox, message, "messagesDeleted", [], set(message.labels), set(message.labels))

    def expire_history_before_now(self, owner: str) -> None:
        """Gmail only keeps history for a while; older start ids then answer 404."""
        self.mailboxes[owner].history_floor = self.current_history_id(owner)

    def current_history_id(self, owner: str) -> int:
        history = self.mailboxes[owner].history
        return history[-1].id if history else 1

    def _record(self, mailbox: Mailbox, message: Message, kind: str, label_ids: list[str], before: set[str], after: set[str]) -> None:
        record = _HistoryRecord(next(self._history_ids), message.id, message.thread_id, kind, label_ids, before, after)
        message.history_id = record.id
        mailbox.history.append(record)

    # --- HTTP ----------------------------------------------------------------

    def _install_routes(self) -> None:
        r = self.http.route
        r("GET", f"{GMAIL_API}/profile", self._profile)
        r("GET", f"{GMAIL_API}/threads", self._threads_list)
        r("GET", f"{GMAIL_API}/threads/([^/]+)", self._threads_get)
        r("GET", f"{GMAIL_API}/messages/([^/]+)", self._messages_get)
        r("GET", f"{GMAIL_API}/messages/([^/]+)/attachments/([^/]+)", self._attachments_get)
        r("GET", f"{GMAIL_API}/history", self._history_list)

    def _mailbox(self, req: ApiRequest) -> tuple[Optional[Mailbox], Optional[object]]:
        caller = self.aliases.get(req.identity or "", req.identity)
        user_id = unquote(req.path.split("/users/")[1].split("/")[0])
        target = caller if user_id == "me" else user_id
        if target != caller:
            return None, google_error(403, "forbidden", "Delegation denied for " + str(caller))
        mailbox = self.mailboxes.get(target or "")
        if mailbox is None:
            return None, google_error(400, "failedPrecondition", "Mail service not enabled")
        return mailbox, None

    @staticmethod
    def _segment(req: ApiRequest, name: str) -> str:
        return unquote(req.path.split(f"/{name}/")[1].split("/")[0])

    def _profile(self, req: ApiRequest) -> object:
        mailbox, error = self._mailbox(req)
        if error:
            return error
        live = [m for m in mailbox.messages.values() if not m.deleted]
        return {
            "emailAddress": mailbox.email,
            "messagesTotal": len(live),
            "threadsTotal": len({m.thread_id for m in live}),
            "historyId": str(self.current_history_id(mailbox.email)),
        }

    def _matches_q(self, messages: list[Message], q: str) -> bool:
        if not q:
            return True
        for term in q.split():
            if m := re.fullmatch(r"after:(\d+)", term):
                if not any(msg.internal_date // 1000 >= int(m.group(1)) for msg in messages):
                    return False
            elif m := re.fullmatch(r"before:(\d+)", term):
                if not any(msg.internal_date // 1000 < int(m.group(1)) for msg in messages):
                    return False
            else:
                raise AssertionError(f"GmailWorld does not understand search term {term!r}")
        return True

    def _threads_list(self, req: ApiRequest) -> object:
        mailbox, error = self._mailbox(req)
        if error:
            return error
        threads: dict[str, list[Message]] = {}
        for message in mailbox.messages.values():
            if message.deleted or {"SPAM", "TRASH"} & set(message.labels):
                continue
            threads.setdefault(message.thread_id, []).append(message)
        listed = [
            {"id": tid, "historyId": str(max(m.history_id for m in msgs)), "snippet": ""}
            for tid, msgs in sorted(threads.items(), key=lambda kv: -max(m.internal_date for m in kv[1]))
            if self._matches_q(msgs, req.query.get("q", ""))
        ]
        page = paginate(listed, req.query, default_size=self.page_size, key="threads")
        page["resultSizeEstimate"] = len(listed)
        if not page["threads"]:
            del page["threads"]
        return page

    def _threads_get(self, req: ApiRequest) -> object:
        mailbox, error = self._mailbox(req)
        if error:
            return error
        thread_id = self._segment(req, "threads")
        messages = sorted(
            (m for m in mailbox.messages.values() if m.thread_id == thread_id and not m.deleted),
            key=lambda m: m.internal_date,
        )
        if not messages:
            return google_error(404, "notFound", "Requested entity was not found.")
        return {"id": thread_id, "historyId": str(max(m.history_id for m in messages)), "messages": [m.resource() for m in messages]}

    def _live_message(self, req: ApiRequest) -> tuple[Optional[Message], Optional[object]]:
        mailbox, error = self._mailbox(req)
        if error:
            return None, error
        message = mailbox.messages.get(self._segment(req, "messages"))
        if message is None or message.deleted:
            return None, google_error(404, "notFound", "Requested entity was not found.")
        return message, None

    def _messages_get(self, req: ApiRequest) -> object:
        message, error = self._live_message(req)
        return error or message.resource()

    def _attachments_get(self, req: ApiRequest) -> object:
        message, error = self._live_message(req)
        if error:
            return error
        attachment_id = self._segment(req, "attachments")
        for att in message.attachments:
            if att["attachment_id"] == attachment_id:
                return {"size": len(att["data"]), "data": b64(att["data"])}
        return google_error(404, "notFound", "Invalid attachment token")

    def _history_list(self, req: ApiRequest) -> object:
        mailbox, error = self._mailbox(req)
        if error:
            return error
        start = int(req.query["startHistoryId"])
        if start < mailbox.history_floor:
            return google_error(404, "notFound", "Requested entity was not found.")
        # historyTypes is repeated; the shared request object keeps only its last value.
        raw_query = parse_qs(urlsplit(req.url).query)
        wanted = {_HISTORY_TYPES[t] for t in raw_query.get("historyTypes", [])} or set(_HISTORY_TYPES.values())
        label = req.query.get("labelId")
        records = []
        for record in mailbox.history:
            if record.id <= start or record.kind not in wanted:
                continue
            if label and label not in (record.labels_before | record.labels_after):
                continue
            ref = {"id": record.message_id, "threadId": record.thread_id, "labelIds": sorted(record.labels_after)}
            item: dict[str, Any] = {"id": str(record.id), "messages": [ref]}
            if record.kind in ("labelsAdded", "labelsRemoved"):
                item[record.kind] = [{"message": ref, "labelIds": record.label_ids}]
            else:
                item[record.kind] = [{"message": ref}]
            records.append(item)
        page = paginate(records, req.query, default_size=self.history_page_size, key="history")
        page["historyId"] = str(self.current_history_id(mailbox.email))
        if not page["history"]:
            del page["history"]
        return page
