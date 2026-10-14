"""Write again the named entities of records whose graph write failed.

A failed named-entity write never fails the document's own indexing: the
record keeps its last good entities, and ``SinkOrchestrator`` records a retry
keyed by the record. The stale-recovery pass, under its lock, calls
``retry_pending_named_entity_persists`` for the retries that are due. A retry
re-projects the extraction stored in the record's blob, with no model call, so
it writes whatever the record's latest indexing produced.

A retry holds the record's lease (``record:<id>``, the one the indexing
consumer holds while it processes the record) from reading the blob to the end
of the write, so a retry and the record's own indexing never interleave. It only
tries the lease: a record being indexed is skipped, since that indexing writes
newer entities anyway. The write is bounded well under the time a delivery
waits for the lease, so a retry never makes the consumer give up on an update.
On ArangoDB, and on Neo4j with explicit transactions, the write is also
conditional: it lands only while its retry is still the one it read, in the
same transaction as the mention and value replace, and a normal write of the
record drops any pending retry in its own transaction.

A retry that fails again is rescheduled with a doubled backoff. After
``MAX_PERSIST_ATTEMPTS`` failures it is dropped with an error log: the record
is written again when it is next indexed. Only a confirmed absence ends a retry
early: the record is gone or deleted, or its blob was read and holds no
extraction. A read that fails (the graph or storage is still unhealthy, which
is how the retry came about) counts as a failure and is retried.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING
from uuid import uuid4

from app.config.constants.arangodb import CollectionNames
from app.services.graph_db.common.record_visibility import is_live_record

if TYPE_CHECKING:
    from logging import Logger

    from app.modules.transformers.sink_orchestrator import SinkOrchestrator
    from app.services.graph_db.interface.graph_db_provider import IGraphDBProvider

MAX_PERSIST_ATTEMPTS = 8
# Under INDEXING_RECORD_LEASE_WAIT_SECONDS (10 s by default): a delivery that
# waits longer for the record lease is treated as already handled.
RETRY_WRITE_TIMEOUT_SECONDS = 8.0
_LEASE_SECONDS = 60.0


async def retry_pending_named_entity_persists(
    *,
    graph_provider: IGraphDBProvider,
    sink: SinkOrchestrator | None,
    logger: Logger,
    page_size: int,
    concurrency_manager=None,
) -> int:
    """Retry the due named-entity writes. Returns how many records were written."""
    if sink is None:
        return 0
    rows = await graph_provider.get_due_named_entity_persist_retries(page_size)
    written = 0
    for row in rows:
        record_id, org_id, due = row.get("recordId"), row.get("orgId"), row.get("dueAt")
        if not record_id or due is None:
            continue
        attempts = int(row.get("attempts") or 0)
        try:
            if attempts > MAX_PERSIST_ATTEMPTS:
                logger.error(
                    "named_entity_persist_retry: giving up on record %s after %d failures; "
                    "it is written again when next indexed", record_id, attempts,
                )
                await graph_provider.clear_named_entity_persist_retry(record_id, due)
                continue
            outcome = await _under_record_lease(
                concurrency_manager, record_id,
                lambda record_id=record_id, org_id=org_id, due=due, attempts=attempts: _retry_one(
                    graph_provider, sink, record_id, org_id, due, attempts,
                ),
            )
            if outcome is _BUSY:
                continue  # being indexed now; that write is newer than this retry
        except Exception as exc:
            # The persist itself never raises, so this is the record or blob read:
            # count it as a failure so the backoff grows instead of retrying every pass.
            logger.warning("named_entity_persist_retry: record %s failed: %s", record_id, type(exc).__name__)
            await _count_failure(graph_provider, logger, org_id, record_id, type(exc).__name__, attempts)
            continue
        if outcome in ("failed", "superseded"):
            continue  # rescheduled by the sink, or already replaced by a newer write
        # A write that landed has dropped the retry in its own transaction; this
        # ends the ones that had nothing to write.
        await graph_provider.clear_named_entity_persist_retry(record_id, due)
        if outcome == "written":
            written += 1
    if rows:
        logger.info("named_entity_persist_retry: %d due, %d written", len(rows), written)
    return written


_BUSY = object()


async def _under_record_lease(concurrency_manager, record_id: str, step):
    if concurrency_manager is None:
        return await asyncio.wait_for(step(), RETRY_WRITE_TIMEOUT_SECONDS)
    pool, owner = f"record:{record_id}", f"named-entity-retry:{uuid4().hex}"
    if not await concurrency_manager.try_acquire(pool, owner, 1, _LEASE_SECONDS):
        return _BUSY
    try:
        return await asyncio.wait_for(step(), RETRY_WRITE_TIMEOUT_SECONDS)
    finally:
        await concurrency_manager.release(pool, owner)


class StoredContentUnreadableError(Exception):
    """The blob lookup found nothing. It does not raise on a graph error, so an
    empty answer is not proof the content is gone."""


async def _retry_one(
    graph_provider, sink, record_id: str, org_id: str | None, due: int, attempts: int = 0,
) -> str | None:
    from app.events.processor import convert_record_dict_to_record
    from app.models.blocks import SemanticMetadata
    from app.modules.transformers.blob_storage import StorageDocumentNotFoundError
    from app.modules.transformers.transformer import TransformContext

    # Raises when the read fails, so None is a record that is really gone.
    record = await graph_provider.get_document(record_id, CollectionNames.RECORDS.value, raise_on_error=True)
    if not record or not is_live_record(record) or not record.get("virtualRecordId"):
        return "gone"
    try:
        blob = await sink.blob_storage.get_record_from_storage(record["virtualRecordId"], org_id or record.get("orgId"))
    except StorageDocumentNotFoundError:
        return "gone"
    if not blob:
        raise StoredContentUnreadableError(record_id)
    raw = blob.get("semantic_metadata")
    if not raw:
        return None
    record_obj = convert_record_dict_to_record(record)
    record_obj.semantic_metadata = SemanticMetadata.model_validate(raw)
    ctx = TransformContext(record=record_obj, settings={"skip_blob": True})
    # The count goes along: a claim that committed before a failure leaves no
    # marker to count from, and the retry must still stop after its last attempt.
    return await sink.reproject_named_entities(ctx, retry_due=due, retry_attempts=attempts)


async def _count_failure(
    graph_provider, logger, org_id: str | None, record_id: str, error: str, attempts: int,
) -> None:
    try:
        await graph_provider.schedule_named_entity_persist_retry(org_id or "", record_id, error, attempts)
    except Exception:
        logger.warning("named_entity_persist_retry: could not count the failure on %s", record_id)
