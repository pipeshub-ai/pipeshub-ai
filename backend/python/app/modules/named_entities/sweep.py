"""Remove entities that no record mentions any more, and their vector points.

Two phases, because a node is created before the edge that links it: a node with
no mention is only a candidate. It is marked ``orphanedAt``; a later pass deletes
it once it has stayed unlinked for the grace period, and unmarks it if a record
linked it meanwhile.

A writer clears the mark on a node before it links it, and that write conflicts
with the sweep's delete of the same node, so a node is never deleted mid-link.

The node is the record of the clean-up still owed: its vector points are deleted
before it, and it is deleted only if that succeeded, so a failed vector delete is
retried by the next pass. Without a vector store only kinds that never get a
point are deleted; the rest wait for a pass that has one. Removing the edges left pointing at a deleted node is
not retried; such an edge is still a true mention, reads skip it, and it is
replaced when its record is reindexed or deleted.

One gap is left: a node claimed by a writer between the candidate lookup and the
vector delete keeps its node and edge but loses a vector point written in that
window, until its record is reindexed. Only semantic search misses the entity.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING

from app.modules.named_entities.domain.kinds import VECTOR_KINDS
from app.modules.named_entities.write_retry import retry_named_entity_write
from app.telemetry.modules.named_entity_metrics import record_sweep
from app.utils.time_conversion import get_epoch_timestamp_in_ms

if TYPE_CHECKING:
    from collections.abc import Callable

    from app.services.graph_db.interface.graph_db_provider import IGraphDBProvider

NAMED_ENTITY_VECTOR_TYPE = "named_entity"
DEFAULT_GRACE_MS = 24 * 60 * 60 * 1000
DEFAULT_BATCH = 200
# A bound per org per pass, so one tenant with a large backlog cannot hold the loop.
MAX_BATCHES_PER_PASS = 50

logger = logging.getLogger(__name__)


@dataclass
class SweepResult:
    marked: int = 0
    cleared: int = 0
    deleted: int = 0
    dangling_removed: int = 0
    values_removed: int = 0
    relinked: int = 0
    vector_failed: bool = False


class NamedEntitySweeper:
    def __init__(
        self,
        graph: IGraphDBProvider,
        entity_vector_store,
        *,
        grace_ms: int = DEFAULT_GRACE_MS,
        batch_size: int = DEFAULT_BATCH,
        now_ms: Callable[[], int] = get_epoch_timestamp_in_ms,
        logger_: logging.Logger | None = None,
    ) -> None:
        self._graph = graph
        self._vectors = entity_vector_store
        self._grace_ms = grace_ms
        self._batch = batch_size
        self._now_ms = now_ms
        self._logger = logger_ or logger

    async def sweep_org(self, org_id: str) -> SweepResult:
        result = SweepResult()
        now = self._now_ms()
        # Unmark first: a node a record linked during the grace period must not be
        # deleted by the delete pass that follows in this same call.
        result.cleared = await self._drain(lambda: self._graph.clear_orphan_named_entity_marks(org_id, self._batch))
        cutoff = now - self._grace_ms
        skip_kinds = None if self._vectors is not None else sorted(kind.value for kind in VECTOR_KINDS)
        for _ in range(MAX_BATCHES_PER_PASS):
            ids = await self._graph.find_orphan_named_entities(
                org_id, self._batch, marked_before_ms=cutoff, skip_kinds=skip_kinds
            )
            if not ids:
                break
            if not await self._delete_vectors(org_id, ids, result):
                break
            deleted = await retry_named_entity_write(
                self._graph,
                lambda ids=ids: self._graph.delete_orphan_named_entities(
                    org_id, len(ids), marked_before_ms=cutoff, entity_ids=ids
                ),
            )
            result.deleted += len(deleted)
            result.relinked += len(ids) - len(deleted)
            await self._delete_dangling(org_id, deleted, result)
            if len(ids) < self._batch:
                break
        result.marked = await self._drain(lambda: self._graph.mark_orphan_named_entities(org_id, now, self._batch))
        result.values_removed = await self._drain(
            lambda: self._graph.delete_dangling_named_entity_values(org_id, self._batch)
        )
        record_sweep("cleared", result.cleared)
        record_sweep("deleted", result.deleted)
        record_sweep("marked", result.marked)
        record_sweep("dangling_removed", result.dangling_removed)
        record_sweep("relinked", result.relinked)
        record_sweep("values_removed", result.values_removed)
        return result

    async def _drain(self, step: Callable) -> int:
        total = 0
        for _ in range(MAX_BATCHES_PER_PASS):
            # A deadlock with a writer on a hot node rolls the batch back; it is run again.
            count = await retry_named_entity_write(self._graph, step)
            total += count
            if count < self._batch:
                break
        return total

    async def _delete_vectors(self, org_id: str, ids: list[str], result: SweepResult) -> bool:
        if self._vectors is None:
            return True
        try:
            await self._vectors.delete_entities(org_id, NAMED_ENTITY_VECTOR_TYPE, ids)
        except Exception as exc:
            result.vector_failed = True
            record_sweep("vector_failed", len(ids))
            self._logger.warning(
                "named entity vector delete failed, nodes kept for the next pass org=%s count=%d error=%s",
                org_id, len(ids), type(exc).__name__,
            )
            return False
        return True

    async def _delete_dangling(self, org_id: str, ids: list[str], result: SweepResult) -> None:
        try:
            result.dangling_removed += await self._graph.delete_dangling_named_entity_mentions(ids)
        except Exception as exc:
            record_sweep("dangling_failed", len(ids))
            self._logger.warning(
                "named entity dangling mention delete failed org=%s count=%d error=%s",
                org_id, len(ids), type(exc).__name__,
            )

    async def sweep_all(self) -> SweepResult:
        total = SweepResult()
        for org in await self._graph.get_all_orgs(active=True) or []:
            org_id = org.get("_key") or org.get("id")
            if not org_id:
                continue
            try:
                part = await self.sweep_org(str(org_id))
            except Exception as exc:
                self._logger.warning("named entity sweep failed org=%s error=%s", org_id, type(exc).__name__)
                continue
            total.marked += part.marked
            total.cleared += part.cleared
            total.deleted += part.deleted
            total.dangling_removed += part.dangling_removed
            total.relinked += part.relinked
            total.vector_failed = total.vector_failed or part.vector_failed
        self._logger.info(
            "named entity sweep marked=%d cleared=%d deleted=%d dangling=%d relinked=%d vector_failed=%s",
            total.marked, total.cleared, total.deleted, total.dangling_removed, total.relinked,
            total.vector_failed,
        )
        return total
