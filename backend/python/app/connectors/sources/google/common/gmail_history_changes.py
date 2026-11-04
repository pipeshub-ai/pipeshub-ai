"""Folds a mailbox's Gmail history into the final state of each message it touched.

History is read without a label filter. A label filter (INBOX, SENT) misses mail a filter archives on arrival or
files under a label, and Google does not say whether the filter uses the labels a message had when it changed or
has now, so a trashed or deleted message could go unreported. Each record's typed lists (messagesAdded,
messagesDeleted, labelsAdded, labelsRemoved) carry the message's current labelIds; a message listed only in
`messages` is reported with unknown labels, and the caller reads it.
https://developers.google.com/workspace/gmail/api/reference/rest/v1/users.history
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Iterable

#: Mail in these is out of a sync's scope, as users.threads.list leaves it out.
OUT_OF_SCOPE_LABELS = frozenset({"TRASH", "SPAM"})
HISTORY_TYPES = ["messageAdded", "messageDeleted", "labelAdded", "labelRemoved"]


@dataclass(frozen=True)
class MessageState:
    message_id: str
    thread_id: str | None
    labels: tuple[str, ...] | None  # None: not reported; read the message to know

    @property
    def in_scope(self) -> bool:
        return self.labels is None or not (set(self.labels) & OUT_OF_SCOPE_LABELS)


@dataclass
class MailboxChanges:
    present: dict[str, MessageState] = field(default_factory=dict)
    deleted: set[str] = field(default_factory=set)
    last_history_id: str | None = None

    @property
    def empty(self) -> bool:
        return not self.present and not self.deleted


def _ref(message: dict[str, Any]) -> tuple[str | None, str | None]:
    return message.get("id"), message.get("threadId")


def fold_history(records: Iterable[dict[str, Any]]) -> MailboxChanges:
    """The final state of every message the records touch, in history order (oldest first)."""
    changes = MailboxChanges()
    ordered = sorted((r for r in records if r), key=lambda r: int(r.get("id") or 0))
    for record in ordered:
        for item in record.get("messages") or []:
            mid, tid = _ref(item)
            if mid and mid not in changes.present and mid not in changes.deleted:
                changes.present[mid] = MessageState(mid, tid, None)
        for kind in ("messagesAdded", "labelsAdded", "labelsRemoved"):
            for item in record.get(kind) or []:
                message = item.get("message") or {}
                mid, tid = _ref(message)
                if not mid or mid in changes.deleted:
                    continue
                labels = message.get("labelIds")
                changes.present[mid] = MessageState(mid, tid, tuple(labels) if labels is not None else None)
        for item in record.get("messagesDeleted") or []:
            mid, _ = _ref(item.get("message") or {})
            if mid:
                changes.deleted.add(mid)
                changes.present.pop(mid, None)
        if record.get("id"):
            changes.last_history_id = str(record["id"])
    return changes
