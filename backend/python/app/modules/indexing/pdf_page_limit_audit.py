"""A one-time start-up audit of the indexed PDFs that have more pages than the limit allows.

Those files stay indexed until their content changes. The audit finds the ones it can prove are over the limit and writes
the page-limit note on them, so people can see why a later change will not be indexed. It never deletes anything.

It reads no more than it must:
- ``pageCount`` when the record has one;
- no stored read for a file under ``SIZE_FLOOR_BYTES`` (it gets its note at its next re-index);
- otherwise the first ``SCAN_CAP_BYTES`` of its stored record, never decoded. A compressed record is classified from its
  first bytes and reported, not inflated: that would download and decompress the largest records at start-up.

One replica runs it, under a leader lock, for at most ``BUDGET_SECONDS`` per start with a saved cursor, so a large install
finishes over several starts. ``PDF_PAGE_LIMIT_AUDIT=false`` skips it.
"""

from __future__ import annotations

import asyncio
import re
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from app.config.constants.arangodb import CollectionNames, ProgressStatus
from app.core.org_context import for_org, system
from app.modules.indexing.connector_off_events import ConnectorState, connector_state
from app.utils.env_utils import env_bool
from app.utils.time_conversion import get_epoch_timestamp_in_ms
from app.utils.user_errors import MAX_INDEXED_PDF_PAGES, PDF_PAGE_LIMIT_NOTE

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable
    from logging import Logger

    from app.config.configuration_service import ConfigurationService
    from app.modules.indexing.vector_membership_backfill import LeaderLock
    from app.services.graph_db.interface.graph_db_provider import IGraphDBProvider

    PrefixReader = Callable[[str, str, int], Awaitable[tuple[bytes, int | None] | None]]

FLAG_KEY = "/migrations/pdf_page_limit_audit"
CURSOR_KEY = "/migrations/pdf_page_limit_audit_cursor"
LEASE_KEY = "pipeshub:migrations:pdf_page_limit_audit"
LEASE_TTL_SECONDS = 600
BUDGET_SECONDS = 600.0
CONCURRENCY = 2
PAGE_SIZE = 200
SIZE_FLOOR_BYTES = 1 << 20
SCAN_CAP_BYTES = 8 << 20
_CLASSIFY_BYTES = 64
_COMPRESSED_PREFIX = b'{"isCompressed": true'
_PAGE_NUMBER = re.compile(rb'"page_number":\s*(\d+)')
_JOB = "pdf_page_limit_audit"



@dataclass
class AuditResult:
    ran: bool = False
    complete: bool = False
    over_limit: int = 0
    scanned: int = 0
    not_checked: int = 0
    compressed: int = 0
    noted: int = 0

    def line(self) -> str:
        return (
            f"At least {self.over_limit} indexed PDFs have more than {MAX_INDEXED_PDF_PAGES} pages; they stay indexed "
            f"until their content changes ({self.scanned} scanned, {self.not_checked} not checked, "
            f"{self.compressed} large records not checked)"
        )


def _is_pdf(record: dict[str, Any]) -> bool:
    return str(record.get("extension") or "").lower().lstrip(".") == "pdf" or record.get("mimeType") == "application/pdf"


def first_page_over_limit(body: bytes) -> int | None:
    """The first ``page_number`` in ``body`` above the limit, or None. A page number that large proves the file is over."""
    for match in _PAGE_NUMBER.finditer(body):
        number = int(match.group(1))
        if number > MAX_INDEXED_PDF_PAGES:
            return number
    return None


async def _list_apps(graph: IGraphDBProvider) -> list[dict[str, Any]]:
    apps: list[dict[str, Any]] = []
    after: str | None = None
    while True:
        with system(_JOB):
            page = await graph.get_documents_paginated(
                CollectionNames.APPS.value, limit=PAGE_SIZE, sort_field="_key", after_key=after, raise_on_error=True,
            )
        apps.extend(page or [])
        if not page or len(page) < PAGE_SIZE:
            return apps
        after = page[-1].get("_key") or page[-1].get("id")
        if not after:
            return apps


async def _inspect(record: dict[str, Any], org_id: str, read_prefix: PrefixReader, result: AuditResult) -> bool:
    """Whether the record is proven over the limit. Counts what it could not check."""
    result.scanned += 1
    count = record.get("pageCount")
    if isinstance(count, int) and count >= 1:
        return count > MAX_INDEXED_PDF_PAGES
    size = record.get("sizeInBytes")
    if isinstance(size, int) and 0 < size < SIZE_FLOOR_BYTES:
        return False
    vrid = record.get("virtualRecordId")
    if not vrid:
        result.not_checked += 1
        return False
    try:
        read = await read_prefix(vrid, org_id, SCAN_CAP_BYTES)
    except Exception:
        result.not_checked += 1
        return False
    if read is None:
        result.not_checked += 1
        return False
    body, _total = read
    if body[:_CLASSIFY_BYTES].startswith(_COMPRESSED_PREFIX):
        result.compressed += 1
        return False
    if first_page_over_limit(body) is not None:
        return True
    result.not_checked += 1
    return False


async def _note(graph: IGraphDBProvider, record: dict[str, Any], result: AuditResult) -> None:
    if record.get("indexingNote") == PDF_PAGE_LIMIT_NOTE:
        return
    applied = await graph.update_node_fields_if_match(
        record.get("_key") or record.get("id"),
        CollectionNames.RECORDS.value,
        {"indexingNote": PDF_PAGE_LIMIT_NOTE},
        {
            "indexingStatus": ProgressStatus.COMPLETED.value,
            "virtualRecordId": record.get("virtualRecordId"),
            "contentRev": record.get("contentRev"),
        },
    )
    if applied:
        result.noted += 1


async def _audit_app(
    graph: IGraphDBProvider,
    app: dict[str, Any],
    after: str | None,
    read_prefix: PrefixReader,
    result: AuditResult,
    lock: LeaderLock,
    clock: Callable[[], float],
    deadline: float,
    save_cursor: Callable[[str, str | None], Awaitable[None]],
    concurrency: int,
) -> bool:
    """Walk one app's COMPLETED records. False when the budget ran out or the lock was lost (the cursor is saved)."""
    app_id = app.get("_key") or app.get("id")
    org_id = app.get("orgId")
    gate = asyncio.Semaphore(concurrency)

    async def one(record: dict[str, Any]) -> None:
        async with gate:
            if await _inspect(record, org_id, read_prefix, result):
                result.over_limit += 1
                await _note(graph, record, result)

    while True:
        with for_org(org_id, job=_JOB):
            page = await graph.get_documents_paginated(
                CollectionNames.RECORDS.value,
                limit=PAGE_SIZE,
                filters={"connectorId": app_id, "indexingStatus": ProgressStatus.COMPLETED.value},
                sort_field="_key",
                after_key=after,
                raise_on_error=True,
            )
            await asyncio.gather(*(one(r) for r in page or [] if _is_pdf(r)))
        if not page or len(page) < PAGE_SIZE:
            return True
        after = page[-1].get("_key") or page[-1].get("id")
        if not after:
            return True
        await save_cursor(app_id, after)
        if clock() >= deadline or not await lock.refresh():
            return False


async def run_pdf_page_limit_audit(
    config_service: ConfigurationService,
    graph: IGraphDBProvider,
    read_prefix: PrefixReader,
    logger: Logger,
    *,
    lock: LeaderLock,
    clock: Callable[[], float] = time.monotonic,
    budget_seconds: float = BUDGET_SECONDS,
    concurrency: int = CONCURRENCY,
) -> AuditResult:
    """Run (or resume) the audit on the replica that holds the lock. ``read_prefix(vrid, org_id, max_bytes)`` returns
    the first bytes of a stored record and its size, or None."""
    result = AuditResult()
    if not env_bool("PDF_PAGE_LIMIT_AUDIT", True):
        logger.info("PDF page-limit audit skipped: PDF_PAGE_LIMIT_AUDIT is false")
        return result
    try:
        if await config_service.get_config(FLAG_KEY, use_cache=False, raise_on_error=True):
            return result
        cursor = await config_service.get_config(CURSOR_KEY, use_cache=False, raise_on_error=True) or {}
    except Exception:
        logger.warning("PDF page-limit audit skipped: its state could not be read", exc_info=True)
        return result
    if not await lock.try_acquire():
        logger.info("PDF page-limit audit skipped: the lock is held elsewhere or unavailable")
        return result

    result.ran = True
    deadline = clock() + budget_seconds

    async def save_cursor(app_id: str, after: str | None) -> None:
        try:
            await config_service.set_config(CURSOR_KEY, {"app": app_id, "after": after})
        except Exception:
            logger.warning("PDF page-limit audit: its cursor was not saved", exc_info=True)

    try:
        complete = True
        resume_app, resume_after = cursor.get("app"), cursor.get("after")
        for app in sorted(await _list_apps(graph), key=lambda a: str(a.get("_key") or a.get("id") or "")):
            app_id = app.get("_key") or app.get("id")
            if not app_id or not app.get("orgId"):
                continue
            if resume_app and str(app_id) < str(resume_app):
                continue
            if connector_state(app) in (ConnectorState.DELETING, ConnectorState.REMOVED):
                continue
            after = resume_after if app_id == resume_app else None
            if not await _audit_app(
                graph, app, after, read_prefix, result, lock, clock, deadline, save_cursor, concurrency
            ):
                complete = False
                break
            await save_cursor(str(app_id) + "￿", None)
        result.complete = complete
        if complete:
            await config_service.set_config(FLAG_KEY, {"timestamp": get_epoch_timestamp_in_ms(), "over_limit": result.over_limit})
            try:
                await config_service.delete_config(CURSOR_KEY)
            except Exception:
                logger.warning("PDF page-limit audit: its cursor was not removed", exc_info=True)
    except Exception:
        logger.exception("PDF page-limit audit failed; the next start resumes it")
        result.complete = False
    finally:
        await lock.release()
    logger.info(result.line())
    return result
