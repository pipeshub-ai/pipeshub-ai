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
  (the rest are met by a later run), and deletes go out in batches; a store that can page only so
  far (Redis, 10,000 results) ends the listing there, and the strays found before it are removed;
- a point with no block id is left alone (what it is, is unknown);
- a failure is logged and never fails the record's indexing.
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import TYPE_CHECKING

from app.services.vector_db.const.const import VIRTUAL_RECORD_ID_FIELD
from app.services.vector_db.models import ScrollWindowExceededError, VectorChunkPayload

if TYPE_CHECKING:
    from collections.abc import Iterable

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


async def sweep_stray_points(
    vector_db: IVectorDBService,
    collection_name: str,
    *,
    org_id: str,
    virtual_record_id: str,
    keep_block_ids: Iterable[str],
    max_points: int | None = None,
) -> SweepResult:
    """Delete the points of *virtual_record_id* whose block id is not in *keep_block_ids*."""
    if not org_id or not virtual_record_id:
        return SweepResult()
    keep = set(keep_block_ids)
    limit = max_points if max_points is not None else sweep_max_points()
    scroll_filter = await vector_db.filter_collection(must={"orgId": org_id, "virtualRecordId": virtual_record_id})

    strays: set[str] = set()
    scanned = 0
    truncated = False
    offset = None
    while True:
        try:
            page = await vector_db.scroll(
                collection_name=collection_name, scroll_filter=scroll_filter, limit=_PAGE_SIZE,
                offset=offset, with_payload=[VIRTUAL_RECORD_ID_FIELD, BLOCK_ID_FIELD],
            )
        except ScrollWindowExceededError:
            # A store that pages only so far (Redis: 10,000 results) cannot list the rest of this record. The strays
            # found so far are still removed below; the points past the window are not reached.
            truncated = True
            break
        for point in page.points or []:
            scanned += 1
            block_id = VectorChunkPayload.from_dict(point.payload or {}).metadata.blockId
            if block_id and block_id not in keep:
                strays.add(block_id)
        if not page.next_offset or page.next_offset == offset:
            break
        if scanned >= limit:
            truncated = True
            break
        offset = page.next_offset

    ordered = sorted(strays)
    for start in range(0, len(ordered), _DELETE_BATCH):
        batch = ordered[start:start + _DELETE_BATCH]
        delete_filter = await vector_db.filter_collection(
            must={"orgId": org_id, "virtualRecordId": virtual_record_id, "blockId": batch}
        )
        await vector_db.delete_points(collection_name, delete_filter)
    if strays or truncated:
        _logger.info(
            "stray sweep of %s: scanned %d points, removed the points of %d stale blocks%s",
            virtual_record_id, scanned, len(strays), " (bounded; the rest next run)" if truncated else "",
        )
    return SweepResult(scanned=scanned, stray_blocks=len(strays), truncated=truncated)
