"""Remove vector points that the record's current revision no longer references.

Block ids follow content (`reconciliation/block_identity.py`), so a point id is the same on every
run that indexes the same block, and two racing runs write the same points. What can still be
left behind is a point under a block id the current revision does not have: written by a run of
an older revision, by an interrupted run, or under the random ids of a record indexed before ids
followed content. This pass lists the record's points through `IVectorDBService` and deletes the
ones whose block id is not in the revision's set.

It is a backstop, never the mechanism, so:
- it is idempotent: a second pass finds nothing;
- it is bounded: at most `INDEXING_STRAY_SWEEP_MAX_POINTS` points are listed per record per run
  (the rest are met by a later run), and deletes go out in batches; Redis reads past its
  10,000-result window by cursor (`redis_vector._cursor_keys`); a store that still cannot list further ends the
  listing there, and the strays found before it are removed;
- a point with no block id is left alone (what it is, is unknown);
- a failure is logged and never fails the record's indexing.
"""
from __future__ import annotations

import contextlib
import logging
import os
from dataclasses import dataclass
from typing import TYPE_CHECKING

from app.services.vector_db.const.const import VIRTUAL_RECORD_ID_FIELD
from app.services.vector_db.models import (
    FilterValue,
    ScrollWindowExceededError,
    VectorChunkPayload,
)

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Iterable
    from contextlib import AbstractAsyncContextManager

    from app.services.vector_db.interface.vector_db import IVectorDBService

BLOCK_ID_FIELD = "metadata.blockId"
_PAGE_SIZE = 500
_DELETE_BATCH = 200
_DEFAULT_MAX_POINTS = 100_000
_logger = logging.getLogger("stray_sweep")


def sweep_enabled() -> bool:
    return os.getenv("INDEXING_STRAY_SWEEP", "on").strip().lower() not in {"off", "0", "false", "no"}


def sweep_max_points() -> int:
    try:
        return max(1, int(os.getenv("INDEXING_STRAY_SWEEP_MAX_POINTS", _DEFAULT_MAX_POINTS)))
    except ValueError:
        return _DEFAULT_MAX_POINTS


@dataclass(frozen=True)
class SweepResult:
    scanned: int = 0
    stray_blocks: int = 0
    truncated: bool = False
    next_offset: str | None = None
    # The kept block ids the listing found a point for, when it listed the record from its start to its end (None: it did not).
    seen_blocks: frozenset[str] | None = None


async def sweep_stray_points(
    vector_db: IVectorDBService,
    collection_name: str,
    *,
    org_id: str,
    virtual_record_id: str,
    keep_block_ids: Iterable[str],
    max_points: int | None = None,
    resume: str | None = None,
    may_delete: Callable[[], Awaitable[bool]] | None = None,
    fence: Callable[[], AbstractAsyncContextManager[None]] | None = None,
) -> SweepResult:
    """Delete the points of *virtual_record_id* whose block id is not in *keep_block_ids*.

    A pass lists at most *max_points* points. When the record has more, ``SweepResult.next_offset`` is where the listing stopped (moved
    back by the points the pass deleted before it, for a store whose cursor is a position): pass it as *resume* to continue there.
    *may_delete* is asked before each delete batch (the listing can be long): when it says no, nothing more is deleted. *fence* wraps
    each delete call (the caller keeps its lease confirmed while the call is in flight).
    """
    if not org_id or not virtual_record_id:
        return SweepResult()
    keep = set(keep_block_ids)
    limit = max_points if max_points is not None else sweep_max_points()
    scroll_filter = await vector_db.filter_collection(must={"orgId": org_id, "virtualRecordId": virtual_record_id})

    strays: set[str] = set()
    kept_seen: set[str] = set()
    stray_points = 0
    scanned = 0
    truncated = False
    offset = resume
    stopped_at: str | None = None
    while True:
        try:
            page = await vector_db.scroll(
                collection_name=collection_name, scroll_filter=scroll_filter, limit=min(_PAGE_SIZE, limit - scanned),
                offset=offset, with_payload=[VIRTUAL_RECORD_ID_FIELD, BLOCK_ID_FIELD],
            )
        except ScrollWindowExceededError:
            # A store that cannot list past some depth ends the listing; the strays found so far are still removed below.
            truncated = True
            break
        for point in page.points or []:
            scanned += 1
            block_id = VectorChunkPayload.from_dict(point.payload or {}).metadata.blockId
            if block_id and block_id not in keep:
                strays.add(block_id)
                stray_points += 1
            elif block_id:
                kept_seen.add(block_id)
        if not page.next_offset or page.next_offset == offset:
            break
        offset = page.next_offset
        if scanned >= limit:
            truncated = True
            stopped_at = offset
            break

    ordered = sorted(strays)
    removed = 0
    refused = False
    for start in range(0, len(ordered), _DELETE_BATCH):
        if may_delete is not None and not await may_delete():
            refused = True
            break
        batch: list[str | int | float | bool] = [*ordered[start:start + _DELETE_BATCH]]
        must: dict[str, FilterValue] = {"orgId": org_id, "virtualRecordId": virtual_record_id, "blockId": batch}
        delete_filter = await vector_db.filter_collection(must=must)
        async with fence() if fence is not None else contextlib.nullcontext():
            await vector_db.delete_points(collection_name, delete_filter)
        removed += len(batch)
    if refused:
        # Strays that were listed and not deleted sit before any resume point: the next pass starts over.
        stopped_at = None
        truncated = True
    if strays or truncated:
        _logger.info(
            "stray sweep of %s: scanned %d points, removed the points of %d of %d stale blocks%s",
            virtual_record_id, scanned, removed, len(strays),
            (" (refused: the record is on another revision or could not be read; the next pass starts over)" if refused
             else " (stopped at its bound; the next pass resumes there)" if stopped_at is not None
             else " (the store could not list further; points past that depth were not checked)") if truncated else "",
        )
    if stopped_at is not None:
        stopped_at = vector_db.scroll_offset_after_delete(stopped_at, stray_points)
    complete = resume is None and not truncated and not refused
    return SweepResult(
        scanned=scanned, stray_blocks=removed, truncated=truncated, next_offset=stopped_at,
        seen_blocks=frozenset(kept_seen) if complete else None,
    )
