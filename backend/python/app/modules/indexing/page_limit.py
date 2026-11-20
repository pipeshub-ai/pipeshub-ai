"""The PDF page limit: a PDF over ``MAX_INDEXED_PDF_PAGES`` pages is not indexed.

The rule never deletes an index on uncertainty. A record that already has a searchable index and whose content is not
proven changed keeps it, with a note; a new file, changed content, or a record with nothing searchable is refused and
un-indexed.

``check_page_limit`` decides in ``EventProcessor`` from a page count taken before dedup, resume and the IN_PROGRESS write.
When no count could be taken, a record with an index to keep is decided from the whole-file inspection before anything is
written; any other is decided after the parse, where only a refusal is possible. ``apply_keep`` and ``apply_refuse``
perform the writes the decision calls for.
"""

from __future__ import annotations

import base64
import binascii
import logging
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from app.config.constants.arangodb import CollectionNames, ProgressStatus
from app.exceptions.indexing_exceptions import PasswordProtectedError
from app.modules.parsers.pdf.page_count import Unavailable, Unreadable
from app.services.graph_db.common.record_visibility import (
    HAS_INDEXED_COPY,
    is_searchable_record,
)
from app.telemetry.backend import METRICS_BACKEND
from app.utils.user_errors import (
    MAX_INDEXED_PDF_PAGES,
    PDF_PAGE_LIMIT_NOTE,
    pdf_over_page_limit,
)

if TYPE_CHECKING:
    from collections.abc import Mapping

    from app.services.graph_db.interface.graph_db_provider import IGraphDBProvider

_logger = logging.getLogger(__name__)


@dataclass
class LongDocument:
    """What the page-limit check sees. ``snapshot`` is the record document as read before any write; ``inspected`` is the
    whole-file inspection, None for the early check; ``host`` is the event processor running the check."""

    record_id: str
    snapshot: Mapping[str, Any]
    org_id: str
    content: bytes | None
    content_rev: str | None
    md5: str | None
    force_reindex: bool = False
    inspected: Any = None
    keep_eligible: bool = False
    host: Any = None


@dataclass(frozen=True)
class Proceed:
    """Index as usual. ``pages`` is the page count when known."""

    pages: int | None = None
    clear_note: bool = False
    keep_eligible: bool = False


@dataclass(frozen=True)
class Keep:
    """The existing index stays. The status is restored to COMPLETED where the rules allow, and ``note`` is written."""

    note: str | None = None
    pages: int | None = None


@dataclass(frozen=True)
class Refuse:
    """Un-index the record, then write ``status`` with ``reason``."""

    status: str
    reason: str
    pages: int | None = None


REFUSED_REV_PREFIX = "refused:"

PAGE_COUNT_UNAVAILABLE = METRICS_BACKEND.counter(
    "pipeshub_pdf_page_count_unavailable_total",
    "PDFs whose page count could not be taken (a timeout, a dead worker or a closed pool)",
    [],
)

_HEX32 = re.compile(r"^[0-9a-fA-F]{32}$")

# Statuses from which Keep restores COMPLETED: a settled index, or a reset ahead of a re-index.
_RESTORABLE = (
    ProgressStatus.COMPLETED.value,
    ProgressStatus.NOT_STARTED.value,
    ProgressStatus.QUEUED.value,
    ProgressStatus.IN_PROGRESS.value,
)
_RESET = (ProgressStatus.NOT_STARTED.value, ProgressStatus.QUEUED.value)


def normalise_md5(value: object) -> str | None:
    """Lowercase hex of an md5 in any form a connector stores, or None when it is not one (an ETag, a version id)."""
    if isinstance(value, (bytes, bytearray)):
        raw = bytes(value)
        if len(raw) == 16:
            return raw.hex()
        try:
            value = raw.decode("ascii")
        except UnicodeDecodeError:
            return None
    if not isinstance(value, str):
        return None
    text = value.strip()
    if _HEX32.match(text):
        return text.lower()
    if len(text) == 24:
        try:
            raw = base64.b64decode(text, validate=True)
        except (binascii.Error, ValueError):
            return None
        return raw.hex() if len(raw) == 16 else None
    return None


def content_state(snapshot: Mapping[str, Any], content_rev: str | None, md5: str | None) -> str:
    """``unchanged``, ``changed`` or ``uncertain`` for the bytes in hand against what the record last indexed.

    ``indexedMd5`` (the md5 of the bytes last fully indexed) decides when the record has it. Without it (a record indexed
    before the field existed) only an md5 mismatch proves a change, and a ``contentRev`` mismatch alone proves nothing,
    because a blob re-embed writes a revision of the same shape as a byte revision. Such a record keeps its index, even if
    its source changed since, until its first index after the upgrade writes the field.
    """
    # The md5 of the bytes last fully indexed. A sync overwrites md5Checksum with the source's value of the new file
    # before the event, so only this field says what the index holds. Absent on a record indexed before it existed.
    indexed = normalise_md5(snapshot.get("indexedMd5"))
    current = normalise_md5(md5)
    if indexed and current:
        return "unchanged" if indexed == current else "changed"
    if content_rev and snapshot.get("contentRev") == content_rev:
        return "unchanged"
    stored = normalise_md5(snapshot.get("md5Checksum"))
    if stored and current:
        return "unchanged" if stored == current else "changed"
    return "uncertain"


async def _keep_eligible(doc: LongDocument) -> bool:
    snapshot = doc.snapshot
    if str(snapshot.get("contentRev") or "").startswith(REFUSED_REV_PREFIX):
        # A refusal that did not finish un-indexing: the record is refused, whatever else its fields say now.
        return False
    if content_state(snapshot, doc.content_rev, doc.md5) == "changed":
        return False
    if is_searchable_record(snapshot):
        return True
    vrid = snapshot.get("virtualRecordId")
    if not vrid:
        return False
    # Neither COMPLETED nor flagged: a record indexed before the flag existed, or one never indexed (stored blocks alone
    # are what a store-only run leaves). Only stored content together with vectors says an index exists.
    try:
        return bool(
            await doc.host.vrid_has_stored_content(doc.org_id, vrid) and await doc.host.vrid_has_points(doc.org_id, vrid)
        )
    except Exception:
        # Storage that cannot answer is uncertainty: the index is kept.
        _logger.warning("could not tell whether %s has an index; keeping it", doc.record_id, exc_info=True)
        return True


async def _stored_content_ok(doc: LongDocument) -> bool:
    """Whether keeping is safe: the stored content is there, or at least the vectors still are."""
    vrid = doc.snapshot.get("virtualRecordId")
    if not vrid:
        return True
    try:
        if await doc.host.vrid_has_stored_content(doc.org_id, vrid):
            return True
        return bool(await doc.host.vrid_has_points(doc.org_id, vrid))
    except Exception:
        _logger.warning("could not tell whether %s has stored content; keeping its index", doc.record_id, exc_info=True)
        return True


async def _over_limit(doc: LongDocument, pages: int, *, eligible: bool) -> Keep | Refuse:
    if eligible and await _stored_content_ok(doc):
        return Keep(note=PDF_PAGE_LIMIT_NOTE, pages=pages)
    return Refuse(
        status=ProgressStatus.FILE_TYPE_NOT_SUPPORTED.value, reason=pdf_over_page_limit(pages), pages=pages
    )


async def check_page_limit(doc: LongDocument) -> Proceed | Keep | Refuse:
    """Decide ``doc``. Early (``doc.inspected is None``): count the pages here. Late: the inspection's count decides."""
    clears_note = doc.snapshot.get("indexingNote") == PDF_PAGE_LIMIT_NOTE
    if doc.inspected is not None:
        pages = getattr(doc.inspected, "page_count", None)
        if not isinstance(pages, int):
            return Proceed()
        if pages <= MAX_INDEXED_PDF_PAGES:
            return Proceed(pages=pages if pages >= 1 else None, clear_note=clears_note)
        return await _over_limit(doc, pages, eligible=doc.keep_eligible)

    from app.modules.parsers.pdf.page_count import count_pdf_pages

    try:
        counted = await count_pdf_pages(doc.content or b"", label=doc.record_id)
    except PasswordProtectedError:
        # The inspection reports a locked file, as it always did.
        return Proceed()
    if isinstance(counted, int):
        if counted <= MAX_INDEXED_PDF_PAGES:
            return Proceed(pages=counted, clear_note=clears_note)
        return await _over_limit(doc, counted, eligible=await _keep_eligible(doc))
    if isinstance(counted, Unavailable):
        PAGE_COUNT_UNAVAILABLE.inc()
    else:
        assert isinstance(counted, Unreadable)
    # No count: the caller decides from the whole-file inspection (before it writes anything when ``keep_eligible``, so
    # that an in-limit file is still indexed and an over-limit one leaves its index as it is).
    return Proceed(keep_eligible=await _keep_eligible(doc))


async def apply_keep(doc: LongDocument, keep: Keep) -> None:
    """Perform a ``Keep``: the existing index stays, the status is restored where the rules allow, and the note is written.

    Every write is one conditional write on the revision (``contentRev``) and virtual record id the decision read, so a
    revision that was queued meanwhile is never marked COMPLETED or noted: it is left to its writer.
    """
    graph = doc.host.graph_provider
    snapshot = doc.snapshot
    record_id = doc.record_id
    status = snapshot.get("indexingStatus")
    forced = bool(doc.force_reindex)
    restores = status in _RESTORABLE or (
        status == ProgressStatus.AUTO_INDEX_OFF.value and forced and snapshot.get(HAS_INDEXED_COPY) is True
    )

    fields: dict[str, Any] = {}
    if keep.note is not None:
        fields["indexingNote"] = keep.note
    if keep.pages is not None and keep.pages >= 1:
        fields["pageCount"] = keep.pages
    if not snapshot.get(HAS_INDEXED_COPY) and snapshot.get("virtualRecordId"):
        fields[HAS_INDEXED_COPY] = True
    if not restores:
        # The status stays what it was, and so does the reason the event handler cleared on its way in.
        reason = snapshot.get("reason")
        if reason:
            fields["reason"] = reason
    revision = {"virtualRecordId": snapshot.get("virtualRecordId"), "contentRev": snapshot.get("contentRev")}
    restored = restores and status == ProgressStatus.COMPLETED.value
    written = False
    if restores and not restored:
        completed = {**fields, "indexingStatus": ProgressStatus.COMPLETED.value}
        restored = written = await _write_if_same_revision(graph, record_id, completed, revision, status)
        if not restored:
            # Lost: re-read once. A reset that landed meanwhile (NOT_STARTED, QUEUED) is retried from where it now is,
            # while the record is still on the revision that was read.
            now = await graph.get_document(record_id, CollectionNames.RECORDS.value) or {}
            current = now.get("indexingStatus")
            if (
                current in _RESET
                and all(now.get(name) == value for name, value in revision.items())
                and await _write_if_same_revision(graph, record_id, completed, revision, current)
            ):
                restored = written = True
            else:
                _logger.info(
                    "record %s: the status moved from %s to %s while it was being kept; left to its writer",
                    record_id, status, current,
                )
                # The status stays what it was, and so does the reason the event handler cleared on its way in.
                if snapshot.get("reason"):
                    fields["reason"] = snapshot["reason"]
    if fields and not written and not await _write_if_same_revision(graph, record_id, fields, revision, None):
        _logger.info("record %s changed while its page-limit note was being written; left to its writer", record_id)
    _logger.info("record %s kept its index (%s)", record_id, "status restored" if restored else f"status {status} unchanged")


async def _write_if_same_revision(
    graph: IGraphDBProvider, record_id: str, fields: Mapping[str, Any], revision: Mapping[str, Any], status: str | None
) -> bool:
    """One write of ``fields`` that applies only while the record still has ``revision`` and, when given, ``status``."""
    expected = dict(revision)
    if status is not None:
        expected["indexingStatus"] = status
    return bool(await graph.update_node_fields_if_match(record_id, CollectionNames.RECORDS.value, dict(fields), expected))


async def apply_refuse(doc: LongDocument, refuse: Refuse) -> None:
    """Perform a ``Refuse``: the status and a revision tombstone land first, then the record is un-indexed.

    The tombstone is a ``contentRev`` no stage job carries, so any job still queued for this record fails its revision
    check, a job of the same revision included. ``hasIndexedCopy`` is cleared because only EMPTY clears it otherwise.
    """
    from app.modules.indexing.unindex import unindex_record

    host = doc.host
    extra: dict[str, Any] = {
        "contentRev": f"{REFUSED_REV_PREFIX}{doc.content_rev}",
        HAS_INDEXED_COPY: False,
    }
    # The record's revision becomes a tombstone, so the count of the bytes it held goes with it (the reason names the pages).
    extra["pageCount"] = None
    if doc.snapshot.get("indexingNote") == PDF_PAGE_LIMIT_NOTE:
        extra["indexingNote"] = None
    await host.processor._mark_record(
        doc.record_id, ProgressStatus(refuse.status), reason=refuse.reason, extra_fields=extra
    )
    await unindex_record(doc.snapshot, doc.org_id, processor=host.processor, logger=host.logger)
