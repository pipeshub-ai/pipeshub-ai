"""Releases a record's QUEUED md5 copies once its enrichment has really ended.

A record's copies wait (QUEUED) while its ``extractionStatus`` is QUEUED or IN_PROGRESS:
they share its enrichment, and promoting them earlier would copy a taxonomy that does
not exist yet with a status nothing would ever finalise. The record handler releases
them when the record path ends; with classification as a stage, the record path ends
first, so the classify stage releases them when it writes the record's final
``extractionStatus``. Same steps as the record handler's ``_release_queued_copies``:
promote, reconcile their taxonomy, clear ``duplicateReconcilePending``.
"""

import asyncio
import logging
from collections.abc import Awaitable, Callable, Sequence

from app.config.constants.arangodb import CollectionNames, ProgressStatus
from app.modules.indexing.duplicate_reconcile import DuplicateReconciler
from app.services.cache.invalidation_hooks import notify_record_indexed
from app.services.graph_db.interface.graph_db_provider import (
    DUPLICATE_RECONCILE_PENDING_FIELD,
    IGraphDBProvider,
)

SyncVectorMembership = Callable[[str], Awaitable[None]]

_RELEASED_ON = frozenset({ProgressStatus.COMPLETED.value, ProgressStatus.EMPTY.value})
RECONCILE_ATTEMPTS = 2
RECONCILE_RETRY_DELAY_S = 1.0


class QueuedCopiesNotReleasedError(Exception):
    """The promotion did not run; the caller must retry, or the copies stay QUEUED for good."""


class QueuedCopies:
    """Bound to the vector-membership sync when the indexing service has built it. Unbound,
    a reconcile reports itself incomplete and the pending flag leaves it to the retry sweep."""

    def __init__(self, graph: IGraphDBProvider, sink: object | None, logger: logging.Logger) -> None:
        super().__init__()
        self._graph = graph
        self._sink = sink
        self._logger = logger
        self._sync: SyncVectorMembership | None = None

    def bind(self, sync_vector_membership: SyncVectorMembership | None) -> None:
        self._sync = sync_vector_membership

    async def _sync_vector_membership(self, virtual_record_id: str) -> None:
        if self._sync is None:
            raise RuntimeError("vector membership sync is not bound to the stage runtime")
        await self._sync(virtual_record_id)

    async def release(self, record_ids: Sequence[str]) -> None:
        """Promote and reconcile each record's QUEUED copies. Raises
        ``QueuedCopiesNotReleasedError`` when a promotion could not run."""
        for record_id in record_ids:
            await self._release_one(record_id)

    async def _release_one(self, record_id: str) -> None:
        record = await self._graph.get_document(record_id, CollectionNames.RECORDS.value, raise_on_error=True)
        if record is None:
            return
        indexing_status = record.get("indexingStatus")
        if indexing_status not in _RELEASED_ON:
            return
        virtual_record_id = record.get("virtualRecordId")
        had_pending = bool(record.get(DUPLICATE_RECONCILE_PENDING_FIELD))
        promoted = await self._graph.update_queued_duplicates_status(record_id, indexing_status, virtual_record_id)
        if promoted < 0:
            raise QueuedCopiesNotReleasedError(record_id)
        if promoted > 0 or had_pending:
            await self._reconcile(record_id, virtual_record_id)
        if promoted > 0 and indexing_status == ProgressStatus.COMPLETED.value:
            await notify_record_indexed(
                connector_name=record.get("connectorName"),
                connector_id=record.get("connectorId"),
                external_record_group_id=record.get("externalGroupId"),
                org_id=record.get("orgId"),
            )

    async def _reconcile(self, record_id: str, virtual_record_id: str | None) -> None:
        reconciler = DuplicateReconciler(
            graph_provider=self._graph,
            sink=self._sink,
            sync_vector_membership=self._sync_vector_membership,
            logger=self._logger,
        )
        for attempt in range(RECONCILE_ATTEMPTS):
            if attempt:
                await asyncio.sleep(RECONCILE_RETRY_DELAY_S)
            if await reconciler.reconcile(record_id, virtual_record_id):
                try:
                    await self._graph.update_node(
                        record_id, CollectionNames.RECORDS.value, {DUPLICATE_RECONCILE_PENDING_FIELD: False}
                    )
                except Exception as exc:
                    # Only costs one redundant reconcile later.
                    self._logger.warning("Could not clear the reconcile flag on record %s: %s", record_id, exc)
                return
        self._logger.warning(
            "Duplicates of record %s still need reconciling; left pending for the recovery retry", record_id
        )
