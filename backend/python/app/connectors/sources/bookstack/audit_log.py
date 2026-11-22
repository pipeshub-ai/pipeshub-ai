"""BookStack's audit log, read by event id.

One job: hand the connector every audit event after the last one it applied, or say that it could not.

The log is read with ``filter[id:gt]`` and ``sort=+id``, never by time. BookStack compares a ``created_at`` filter
with times stored in the instance's own time zone (APP_TIMEZONE) while the API shows them in UTC, so a UTC
cursor skips every recent event on an instance west of UTC; the server's clock can also differ from PipesHub's,
and several events share a second. Event ids are BookStack's auto-increment key: they only grow.

Events also say what they are about in two ways (``app/Activity/Tools/ActivityLogger.php``): shelves, books,
chapters and pages through ``loggable_id`` / ``loggable_type``; users and roles only as ``"(id) name"`` in
``detail``. Purging an item from the recycle bin clears ``loggable_id`` on all its past events and leaves its bare
name in ``detail``; a restore names the item as ``"Deletion (n) for page (id) name"``.
"""

from __future__ import annotations

import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

from app.sources.client.bookstack.bookstack import BookStackResponse

PAGE_SIZE = 500
_NAMED = re.compile(r"^\((\d+)\)\s")
_RESTORED = re.compile(r"^Deletion \(\d+\) for (page|chapter|book|bookshelf) \((\d+)\)")

ListAuditLog = Callable[..., Awaitable[BookStackResponse]]


@dataclass
class AuditRead:
    """What one read of the log found: the events in id order, and whether it reached the end."""

    events: list[dict] = field(default_factory=list)
    complete: bool = False
    status: int | None = None
    message: str | None = None

    @property
    def last_id(self) -> int | None:
        return max((int(e["id"]) for e in self.events), default=None)


def _is_listing(body: object) -> bool:
    return isinstance(body, dict) and isinstance(body.get("data"), list) and isinstance(body.get("total"), int)


class AuditLogReader:
    def __init__(self, list_audit_log: ListAuditLog, page_size: int = PAGE_SIZE) -> None:
        self._list = list_audit_log
        self._page_size = page_size

    async def latest_id(self) -> AuditRead:
        """A read whose ``last_id`` is the newest event id (0 for an empty log); ``complete`` False when the
        log could not be read, with BookStack's status and message."""
        response = await self._list(count=1, sort="-id")
        read = AuditRead(status=response.status_code, message=response.error)
        if not response.success or not _is_listing(response.data):
            return read
        read.events = [e for e in response.data["data"] if isinstance(e, dict) and e.get("id") is not None]
        read.complete = True
        return read

    async def read_after(self, after_id: int) -> AuditRead:
        """Every event with an id above ``after_id``. Events that arrive while it reads come after the ones read,
        since the listing is in id order, so paging by offset neither skips nor repeats one."""
        read = AuditRead()
        offset = 0
        while True:
            response = await self._list(count=self._page_size, offset=offset, sort="+id",
                                        filter={"id:gt": str(after_id)})
            read.status, read.message = response.status_code, response.error
            if not response.success or not _is_listing(response.data):
                return read
            page = response.data["data"]
            read.events.extend(e for e in page if isinstance(e, dict) and e.get("id") is not None)
            offset += len(page)
            if not page:
                # An empty page short of the total is a cut-off listing, not the end.
                read.complete = offset >= response.data["total"]
                return read
            if offset >= response.data["total"]:
                read.complete = True
                return read


def entity_ref(event: dict) -> tuple[str, int] | None:
    """(type, id) of the shelf, book, chapter or page an event is about, while BookStack still names it."""
    kind, raw = event.get("loggable_type"), event.get("loggable_id")
    if kind in ("page", "chapter", "book", "bookshelf") and isinstance(raw, int) and not isinstance(raw, bool):
        return kind, raw
    if kind in ("page", "chapter", "book", "bookshelf") and isinstance(raw, str) and raw.isdigit():
        return kind, int(raw)
    return None


def named_id(event: dict) -> int | None:
    """The user or role id in a user or role event's ``"(id) name"`` detail."""
    match = _NAMED.match(str(event.get("detail") or ""))
    return int(match.group(1)) if match else None


def restored_ref(event: dict) -> tuple[str, int] | None:
    """(type, id) a ``recycle_bin_restore`` event brought back."""
    match = _RESTORED.match(str(event.get("detail") or ""))
    return (match.group(1), int(match.group(2))) if match else None
