"""What to say about a record that exists but whose content is not indexed.

Search only finds embedded content, so a record whose ``indexingStatus`` is
anything but ``COMPLETED`` is invisible to it, and reading it by id has nothing
to return. Said badly, both read as "this does not exist", and a model that
hears that names a neighbouring record or reports that the work was never done.

Every caller here has already passed the same permission check that guards an
indexed record. Nothing in this module reads a store, so it cannot reveal a
record the caller may not see.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Final

from app.config.constants.arangodb import ProgressStatus
from app.models.entities import resolve_weburl
from app.models.source_dates import RecordDates

# The typed answer of a knowledge read whose records exist, may be read, and are still being indexed (KN-09).
INDEX_NOT_READY: Final = "index_not_ready"

# Statuses whose record should become readable without anyone acting on it.
_PENDING = frozenset({
    ProgressStatus.NOT_STARTED.value,
    ProgressStatus.QUEUED.value,
    ProgressStatus.IN_PROGRESS.value,
})

_WHY = {
    ProgressStatus.AUTO_INDEX_OFF.value:
        "automatic indexing is off for it, so it stays unindexed until someone indexes it",
    "FOLDER": "it is a folder, with no readable content of its own",
    ProgressStatus.PAUSED.value: "indexing of its source is paused",
    ProgressStatus.FAILED.value: "indexing it failed",
    ProgressStatus.PASSWORD_PROTECTED.value:
        "it is password-protected, so its text cannot be read",
    ProgressStatus.FILE_TYPE_NOT_SUPPORTED.value: "its file type cannot be indexed",
    ProgressStatus.EMPTY.value: "it had no text to index",
    ProgressStatus.ENABLE_MULTIMODAL_MODELS.value:
        "indexing it needs a multimodal model, and none is enabled",
}


def is_not_indexed(status: str | None) -> bool:
    """True for a known status other than ``COMPLETED``; unknown says nothing."""
    return bool(status) and status != ProgressStatus.COMPLETED.value


def is_index_pending(status: str | None) -> bool:
    """True while indexing will make the record readable without anyone acting on it."""
    return status in _PENDING


def index_not_ready_line(name: str | None) -> str:
    """The plain line for a readable record that is still being indexed (KN-09)."""
    return (
        f"{name or 'This record'} is still being indexed, so its contents cannot be searched yet; "
        "try again in a few minutes."
    )


_PREPARING = {
    "waiting": "it is being prepared for reading and was not ready in time, so asking again shortly should work",
    "deferred": "this call could only prepare a few records at once, so asking for it again should work",
}


def not_indexed_note(status: str | None, *, preparing: str | None = None, name: str | None = None) -> str:
    """One paragraph telling the model the record exists but cannot be read; a pending one leads with the plain
    line the person can be told (KN-09).

    ``preparing`` is set when the record is being parsed for this read: ``waiting``
    (not ready in time) or ``deferred`` (not started: the call asked for too many).
    Both are keyword-only: each is an optional string, and a name taken for a
    ``preparing`` state would drop the line silently."""
    shown = status or "unknown"
    if preparing in _PREPARING:
        return (
            f"Content being prepared: this record exists, and its content is not indexed yet (status: {shown}); "
            f"{_PREPARING[preparing]}.\n"
            "Its text cannot be read yet, so use its name, link and dates and say its content "
            "was not ready."
        )
    why = "it is waiting to be indexed, so asking again shortly may work" if shown in _PENDING else _WHY.get(shown)
    lines = [
        *([index_not_ready_line(name)] if shown in _PENDING else []),
        f"Not indexed: this record exists, but its content is not indexed yet (status: {shown})"
        + (f"; {why}." if why else "."),
        "Search cannot find its text and its content cannot be read, so a search that misses it "
        "does not mean it does not exist. Use its name, link and dates, and say its content "
        "could not be read.",
    ]
    return "\n".join(lines)


def _date(epoch_ms: Any) -> str | None:  # noqa: ANN401
    if not isinstance(epoch_ms, (int, float)) or isinstance(epoch_ms, bool) or epoch_ms <= 0:
        return None
    return datetime.fromtimestamp(epoch_ms / 1000, tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def record_metadata_lines(doc: dict[str, Any], frontend_url: str | None = None) -> list[str]:
    """Identity lines for a raw records-collection document.

    The fallback for when the typed record (``Record.to_llm_context()``)
    cannot be loaded: a record whose type has no type collection, or a graph
    read that failed.
    """
    lines = [f"Record ID: {doc.get('_key') or doc.get('id') or ''}"]
    pairs = [
        ("Name", doc.get("recordName")),
        ("Connector", doc.get("connectorName")),
        ("Type", doc.get("recordType")),
        ("External ID", doc.get("externalRecordId")),
    ]
    lines.extend(f"{label}: {value}" for label, value in pairs if value)
    dates = RecordDates.from_graph(doc)
    if dates.origin is None:
        date_pairs = [
            ("Created At", _date(doc.get("sourceCreatedAtTimestamp") or doc.get("createdAtTimestamp"))),
            ("Last Updated At", _date(doc.get("sourceLastModifiedTimestamp") or doc.get("updatedAtTimestamp"))),
        ]
        lines.extend(f"{label}: {value}" for label, value in date_pairs if value)
    else:
        lines.extend(dates.header_lines(lambda ms: _date(ms) or "N/A"))
    web_url = None if doc.get("hideWeburl") else resolve_weburl(doc.get("webUrl"), frontend_url)
    if web_url:
        lines.append(f"Web URL: {web_url}")
    return lines
