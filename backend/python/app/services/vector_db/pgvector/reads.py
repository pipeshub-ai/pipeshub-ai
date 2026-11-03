"""Reads of the points tables. This change carries the count; scroll and retrieve follow with the read path."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.core.db import Tx


async def count(tx: Tx, org: str, collection_id: int, space_id: int) -> int:
    """Exact count of the org's live points of one collection and space (tombstoned points, kept in HNSW safe mode, are not live)."""
    return await tx.fetchval(
        """/* pipeshub:pgvector.count */
        SELECT count(*) FROM retrieval.points p
        WHERE p.org_id = $1 AND p.collection_id = $2 AND p.space_id = $3
          AND NOT EXISTS (SELECT 1 FROM retrieval.point_tombstones t WHERE t.org_id = $1 AND t.point_sk = p.point_sk)""",
        org, collection_id, space_id,
    )
