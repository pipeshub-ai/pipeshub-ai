"""Copy a primary record's taxonomy and entity points to the duplicates that
were parked QUEUED while it indexed (same MD5, same org).

The promotion that un-parks them sets ``duplicateReconcilePending`` on the
primary in the same write, so the work cannot be lost to a crash. The record
handler reconciles right after the promotion. When that fails, the stale
record recovery loop retries it here (``retry_pending_duplicate_reconciles``),
since a redelivered event finds nothing QUEUED and would not run it again.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from app.config.constants.arangodb import CollectionNames, ProgressStatus
from app.services.graph_db.interface.graph_db_provider import (
    DUPLICATE_RECONCILE_ATTEMPTS_FIELD,
    DUPLICATE_RECONCILE_DUE_AT_FIELD,
    DUPLICATE_RECONCILE_GRACE_MS,
    DUPLICATE_RECONCILE_PENDING_FIELD,
)
from app.utils.time_conversion import get_epoch_timestamp_in_ms

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable
    from logging import Logger

    from app.services.graph_db.interface.graph_db_provider import IGraphDBProvider

# Failed retries wait GRACE * 2**attempts (20, 40, 80, 160 minutes), so a
# short outage does not use up the budget.
MAX_RECONCILE_ATTEMPTS = 5

_RECORDS = CollectionNames.RECORDS.value
_RECONCILED_STATUSES = frozenset({ProgressStatus.COMPLETED.value, ProgressStatus.EMPTY.value})


class DuplicateReconciler:
    def __init__(
        self,
        *,
        graph_provider: IGraphDBProvider,
        sink: Any,  # noqa: ANN401 - SinkOrchestrator; None when entities are off
        sync_vector_membership: Callable[[str], Awaitable[None]],
        logger: Logger,
    ) -> None:
        self.graph = graph_provider
        self.sink = sink
        self.sync_vector_membership = sync_vector_membership
        self.logger = logger

    async def reconcile(self, record_id: str, virtual_record_id: str | None) -> bool:
        """Copy taxonomy edges and entity points from ``record_id`` to every
        same-org sibling of its virtual record. Idempotent: edge copy is an
        upsert, and membership is recomputed from the graph.

        Returns whether every sibling was reconciled; failures are logged.
        """
        if not virtual_record_id:
            return True
        try:
            sibling_keys = [
                key for key in (await self.graph.get_records_by_virtual_record_id(virtual_record_id) or [])
                if key and key != record_id
            ]
            if not sibling_keys:
                return True
            source_doc = await self.graph.get_document(record_id, _RECORDS)
            org_id = (source_doc or {}).get("orgId")
            if not org_id:
                return True

            complete = True
            for sibling_key in sibling_keys:
                sibling_doc = await self.graph.get_document(sibling_key, _RECORDS)
                # get_records_by_virtual_record_id is not org-scoped; a
                # virtualRecordId shared across orgs must not carry this
                # org's taxonomy to another tenant's record.
                if sibling_doc is None or sibling_doc.get("orgId") != org_id:
                    if sibling_doc is not None:
                        self.logger.warning(
                            "Skipping cross-org duplicate %s for record %s (vrid=%s)",
                            sibling_key, record_id, virtual_record_id,
                        )
                    continue
                # The copy reports failure as False rather than raising.
                if not await self.graph.copy_document_relationships(record_id, sibling_key):
                    self.logger.warning(
                        "Taxonomy copy to duplicate %s of record %s failed", sibling_key, record_id,
                    )
                    complete = False
                    continue
                if self.sink is not None and not await self.sink.sync_entities_for_duplicate(sibling_doc):
                    self.logger.warning(
                        "Entity sync for duplicate %s of record %s failed", sibling_key, record_id,
                    )
                    complete = False

            await self.sync_vector_membership(virtual_record_id)
            return complete
        except Exception as e:
            self.logger.warning(
                "Failed to reconcile promoted duplicates for record %s (vrid=%s): %s",
                record_id, virtual_record_id, e,
            )
            return False


async def retry_pending_duplicate_reconciles(
    *,
    graph_provider: IGraphDBProvider,
    reconciler: DuplicateReconciler,
    logger: Logger,
    page_size: int,
    now_ms: Callable[[], int] = get_epoch_timestamp_in_ms,
) -> int:
    """Reconcile primaries whose pending flag is due: their handler did not
    clear it within ``DUPLICATE_RECONCILE_GRACE_MS`` of the promotion.

    Holds no ``record:<id>`` lease. The consumer drops an event whose record
    lease it cannot take within seconds, so holding it through a reconcile
    would lose a real update or delete; recovery's own lock already keeps
    sweeps apart, and a reconcile is idempotent. What must not race is the
    flag: it is cleared only while its due time is still the one this sweep
    read, so a promotion made meanwhile keeps its flag.

    A failure is counted on the record and pushes the due time back; after
    ``MAX_RECONCILE_ATTEMPTS`` the flag is cleared and the count kept, so an
    operator can find the record. Returns how many records were reconciled.
    """
    rows = await graph_provider.get_records_pending_duplicate_reconcile(
        due_before_ms=now_ms(), limit=page_size,
    )
    reconciled = 0
    for row in rows:
        record_id = row.get("_key")
        if not record_id:
            continue
        due = row.get(DUPLICATE_RECONCILE_DUE_AT_FIELD)
        try:
            if await _retry_one(graph_provider, reconciler, logger, record_id, now_ms):
                reconciled += 1
        except Exception:
            logger.warning(
                "duplicate_reconcile: retry failed for record %s", record_id, exc_info=True,
            )
            try:
                await _record_failure(
                    graph_provider, logger, record_id,
                    _int(row.get(DUPLICATE_RECONCILE_ATTEMPTS_FIELD)), due, now_ms,
                )
            except Exception:
                logger.warning("duplicate_reconcile: could not count the failure on %s", record_id)
    if rows:
        logger.info(
            "duplicate_reconcile: retried %d pending record(s), reconciled %d", len(rows), reconciled,
        )
    return reconciled


async def _retry_one(
    graph_provider: IGraphDBProvider,
    reconciler: DuplicateReconciler,
    logger: Logger,
    record_id: str,
    now_ms: Callable[[], int],
) -> bool:
    record = await graph_provider.get_document(record_id, _RECORDS)
    if not record or record.get(DUPLICATE_RECONCILE_PENDING_FIELD) is not True:
        return False
    due = record.get(DUPLICATE_RECONCILE_DUE_AT_FIELD)
    fence = {DUPLICATE_RECONCILE_PENDING_FIELD: True, DUPLICATE_RECONCILE_DUE_AT_FIELD: due}
    if record.get("indexingStatus") not in _RECONCILED_STATUSES:
        # Reindexed or failed since the promotion: there is nothing to copy
        # now, and its next completion re-arms the flag if needed.
        await graph_provider.update_node_fields_if_match(
            record_id, _RECORDS, _cleared(0), fence,
        )
        logger.info(
            "duplicate_reconcile: record %s is %s; dropped its pending reconcile",
            record_id, record.get("indexingStatus"),
        )
        return False
    if await reconciler.reconcile(record_id, record.get("virtualRecordId")):
        if await graph_provider.update_node_fields_if_match(record_id, _RECORDS, _cleared(0), fence):
            logger.info("duplicate_reconcile: reconciled record %s on retry", record_id)
            return True
        logger.info(
            "duplicate_reconcile: record %s was promoted again during the retry; flag kept",
            record_id,
        )
        return False
    await _record_failure(
        graph_provider, logger, record_id,
        _int(record.get(DUPLICATE_RECONCILE_ATTEMPTS_FIELD)), due, now_ms,
    )
    return False


async def _record_failure(
    graph_provider: IGraphDBProvider,
    logger: Logger,
    record_id: str,
    previous_attempts: int,
    due: object,
    now_ms: Callable[[], int],
) -> None:
    attempts = previous_attempts + 1
    fence = {DUPLICATE_RECONCILE_PENDING_FIELD: True, DUPLICATE_RECONCILE_DUE_AT_FIELD: due}
    if attempts >= MAX_RECONCILE_ATTEMPTS:
        if await graph_provider.update_node_fields_if_match(
            record_id, _RECORDS, _cleared(attempts), fence,
        ):
            logger.error(
                "duplicate_reconcile: giving up on record %s after %d attempts; its duplicates "
                "may lack taxonomy or entity points until reindexed",
                record_id, attempts,
            )
        return
    await graph_provider.update_node_fields_if_match(record_id, _RECORDS, {
        DUPLICATE_RECONCILE_ATTEMPTS_FIELD: attempts,
        DUPLICATE_RECONCILE_DUE_AT_FIELD: now_ms() + DUPLICATE_RECONCILE_GRACE_MS * 2 ** attempts,
    }, fence)


def _cleared(attempts: int) -> dict[str, Any]:
    return {
        DUPLICATE_RECONCILE_PENDING_FIELD: False,
        DUPLICATE_RECONCILE_ATTEMPTS_FIELD: attempts,
        DUPLICATE_RECONCILE_DUE_AT_FIELD: None,
    }


def _int(value: object) -> int:
    try:
        return max(0, int(value or 0))  # type: ignore[call-overload]
    except (TypeError, ValueError):
        return 0


__all__ = [
    "MAX_RECONCILE_ATTEMPTS",
    "DuplicateReconciler",
    "retry_pending_duplicate_reconciles",
]
