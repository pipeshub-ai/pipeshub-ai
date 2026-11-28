"""The reads of the property projection sweep (graph_0024): the records whose projected property values are out of date.

Both selections are vetted reads (the planner may use their indexes: row-level security keeps a plain statement from them). The org is the ambient
one, never the argument; an argument the read refuses (a page over its bound) raises, so no page is silently cut short. The index is a revision's,
never DDL at run time. The index methods name no org: the index is the store's.
"""
from __future__ import annotations

import math
from decimal import Decimal

from app.config.constants.arangodb import CollectionNames
from app.core.org_context import current_org_id
from app.services.graph_db.postgres.base import PgBase, _doc

_RECORDS = CollectionNames.RECORDS.value
CHANGE_INDEX = "graph.nodes_record_change"
PROJECTION_PAGE_MAX = 1000


class PropertySweepMixin(PgBase):
    # Both selections are vetted reads (the planner may use their indexes: row-level security keeps a plain statement from them). The org is the
    # ambient one, never the argument; an argument the read refuses (a page over its bound) raises, so no page is silently cut short.

    @staticmethod
    def _projection_page(limit: int) -> int:
        n = max(1, int(limit))
        if n > PROJECTION_PAGE_MAX:
            raise ValueError(f"a page of records to project is at most {PROJECTION_PAGE_MAX}")
        return n

    async def get_records_updated_since(self, connector_id: str, org_id: str, *, since: int | float, after_id: str, props_hash: str, props_gen: int,
                                        limit: int, transaction: str | None = None) -> list[dict]:
        """``graph.records_updated_since`` (index nodes_record_change): the stale live records after the cursor ``(since, after_id)``, in
        ``(updatedAtTimestamp, id)`` order, the id by bytes."""
        current_org_id()
        n = self._projection_page(limit)
        # numeric, from the text of the number: a bigint would cut a fractional write time and a float would widen it, and either re-reads a page
        if isinstance(since, bool) or not isinstance(since, (int, float)) or not math.isfinite(since):
            raise ValueError("since is a finite number")
        cursor = Decimal(repr(since))
        rows = await self._q(lambda tx: tx.fetch("/* pipeshub:graph.records_updated_since */ SELECT node_id, doc FROM graph.records_updated_since($1, $2, $3, $4, $5, $6)",
                                                 connector_id, cursor, props_hash, int(props_gen), after_id or "", n), transaction)
        return [self._shape(_doc(r["doc"]), _RECORDS, None) for r in rows]

    async def get_records_needing_projection(self, connector_id: str, org_id: str, *, after_key: str | None, props_hash: str, props_gen: int,
                                             limit: int, transaction: str | None = None) -> list[dict]:
        """``graph.records_needing_projection``: the stale live records of the connector after ``after_key``, by id (bytes)."""
        current_org_id()
        n = self._projection_page(limit)
        rows = await self._q(lambda tx: tx.fetch("/* pipeshub:graph.records_needing_projection */ SELECT node_id, doc FROM graph.records_needing_projection($1, $2, $3, $4, $5)",
                                                 connector_id, props_hash, int(props_gen), after_key or "", n), transaction)
        return [self._shape(_doc(r["doc"]), _RECORDS, None) for r in rows]

    async def ensure_record_change_index(self) -> None:
        """The index is a revision's (graph_0024 builds ``nodes_record_change`` online, before the upgrade's transaction), never DDL at run time: this
        only refuses to go on where the migration has not run."""
        if not await self._q(lambda tx: tx.fetchval("/* pipeshub:graph.record_change_index_exists */ SELECT to_regclass($1) IS NOT NULL", CHANGE_INDEX), None):
            raise RuntimeError("The record change index is missing: run the database migrations (graph_0024)")

    async def record_change_index_online(self) -> bool:
        """The index exists and is valid (an invalid one, left by a stopped build, is not used)."""
        return bool(await self._q(lambda tx: tx.fetchval(
            "/* pipeshub:graph.record_change_index_online */ SELECT coalesce((SELECT indisvalid FROM pg_index WHERE indexrelid = to_regclass($1)), false)",
            CHANGE_INDEX), None))

