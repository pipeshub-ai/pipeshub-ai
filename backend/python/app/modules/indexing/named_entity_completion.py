"""Extract again the named entities of records whose extraction left text unread.

Indexing extracts within a call budget (``max_llm_windows``) or with an agent
that can stop before reading every block. Such an extraction is written as it
is, and ``SinkOrchestrator`` queues the record (``needs_reextract``): a job on the
record's persist-retry marker with ``reextract`` set. This loop works those jobs:
it reads the record's stored blocks from its blob and extracts in single calls
with ``REEXTRACT_MAX_WINDOWS`` windows. Nothing is parsed or embedded again.

The jobs run in the background on one replica (a Redis leader lease), and only
while at least half of the indexing LLM slots are free, so they never slow the
indexing of new content. Extraction takes seconds to minutes, so it runs without
the record's lease; the write then takes the lease, like a persist retry, and
lands only while its job is still the one it read. A normal write of the record
(it was indexed again meanwhile) drops the job in its own transaction, and that
write extracted afresh.

The blob keeps the extraction indexing stored. A persist retry that later
re-projects it writes those entities again, and that write queues the record
for a re-extraction once more, so the record settles on the complete set.
"""

from __future__ import annotations

import asyncio
import inspect
import math
import os
from typing import TYPE_CHECKING, Any
from uuid import uuid4

from app.config.constants.arangodb import CollectionNames
from app.modules.indexing.named_entity_persist_retry import (
    _BUSY,
    StoredContentUnreadableError,
    _count_failure,
    _under_record_lease,
)
from app.modules.indexing.vector_membership_backfill import (
    VectorMembershipBackfillLeaderLock,
)
from app.modules.named_entities.stage import REEXTRACT_MAX_WINDOWS
from app.modules.named_entities.strategies.single_call import WINDOW_TIMEOUT_SECONDS
from app.services.graph_db.common.record_visibility import is_live_record
from app.services.messaging.utils import MessagingUtils
from app.utils.concurrency import (
    MAX_CONCURRENT_INDEXING_LLM_CALLS,
    indexing_llm_slots_remaining,
)
from app.utils.worker_scaling import scaled

if TYPE_CHECKING:
    from collections.abc import Callable
    from logging import Logger

    from app.modules.transformers.sink_orchestrator import SinkOrchestrator
    from app.services.graph_db.interface.graph_db_provider import IGraphDBProvider

MAX_REEXTRACT_ATTEMPTS = 5
# A ceiling for a hung run, not a budget: every window may use all of its calls.
EXTRACT_TIMEOUT_SECONDS = REEXTRACT_MAX_WINDOWS * WINDOW_TIMEOUT_SECONDS
# Records per pass: each is up to REEXTRACT_MAX_WINDOWS model calls.
PAGE_SIZE = 4
STARTUP_GRACE_SECONDS = 300.0
DEFAULT_INTERVAL_SECONDS = 60.0
LEADER_KEY = "named_entity_completion:leader"
_LEASE_MARGIN_SECONDS = 15 * 60


def llm_mostly_free() -> bool:
    """At least half of the indexing LLM slots are free in this process."""
    return indexing_llm_slots_remaining() * 2 >= scaled(MAX_CONCURRENT_INDEXING_LLM_CALLS)


async def complete_named_entities(
    *,
    graph_provider: IGraphDBProvider,
    sink: SinkOrchestrator | None,
    logger: Logger,
    page_size: int = PAGE_SIZE,
    concurrency_manager=None,  # noqa: ANN001 - the indexing concurrency manager, when distributed
    still_leader: Callable[[], bool] | None = None,
) -> int:
    """Work the due re-extraction jobs. Returns how many records were written.
    ``still_leader``: checked before each record; the pass stops once it is false."""
    if sink is None or not llm_mostly_free():
        return 0
    rows = await graph_provider.get_due_named_entity_persist_retries(page_size, reextract=True)
    written = 0
    for row in rows:
        if still_leader is not None and not still_leader():
            logger.warning("named_entity_completion: lost the leader lease; stopping this pass")
            break
        record_id, org_id, due = row.get("recordId"), row.get("orgId"), row.get("dueAt")
        if not record_id or due is None:
            continue
        attempts = int(row.get("attempts") or 0)
        if attempts > MAX_REEXTRACT_ATTEMPTS:
            logger.error(
                "named_entity_completion: giving up on record %s after %d failures; "
                "it is extracted again when next indexed", record_id, attempts,
            )
            await graph_provider.clear_named_entity_persist_retry(record_id, due)
            continue
        try:
            outcome = await _complete_one(graph_provider, sink, concurrency_manager, record_id, org_id, due, attempts)
        except Exception as exc:
            logger.warning("named_entity_completion: record %s failed: %s", record_id, type(exc).__name__)
            await _count_failure(graph_provider, logger, org_id, record_id, type(exc).__name__, attempts)
            continue
        if outcome is _BUSY or outcome in ("failed", "superseded"):
            # Being indexed now (that write is newer), queued again by the sink, or
            # already replaced by a newer write.
            continue
        if outcome == "extract_failed":
            await _count_failure(graph_provider, logger, org_id, record_id, "extract_failed", attempts)
            continue
        if outcome == "written":
            written += 1
            continue
        # Gone, nothing to read, or nothing better to write: the job is done.
        await graph_provider.clear_named_entity_persist_retry(record_id, due)
    if rows:
        logger.info("named_entity_completion: %d due, %d written", len(rows), written)
    return written


async def _complete_one(
    graph_provider: IGraphDBProvider,
    sink: SinkOrchestrator,
    concurrency_manager,  # noqa: ANN001
    record_id: str,
    org_id: str | None,
    due: int,
    attempts: int,
) -> Any:  # noqa: ANN401 - a persist outcome, "gone", "extract_failed" or _BUSY
    from app.events.processor import convert_record_dict_to_record
    from app.models.blocks import BlocksContainer
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
    containers = blob.get("block_containers")
    if not containers:
        return "gone"
    record_obj = convert_record_dict_to_record(record)
    record_obj.block_containers = BlocksContainer.model_validate(containers)
    ctx = TransformContext(record=record_obj, settings={"skip_blob": True})
    extraction = await asyncio.wait_for(sink.extract_named_entities(ctx, reextract=True), EXTRACT_TIMEOUT_SECONDS)
    if extraction.status == "FAILED":
        return "extract_failed"
    if extraction.status == "SKIPPED":
        return "gone"
    return await _under_record_lease(
        concurrency_manager, record_id,
        lambda: sink.persist_named_entities(ctx, extraction, retry_due=due, retry_attempts=attempts),
    )


def _record_lease_manager(app_container: Any) -> Any:  # noqa: ANN401
    """The record consumer's concurrency manager, which holds ``record:<id>``
    leases when indexing is distributed; None on a single instance."""
    consumers = getattr(app_container, "kafka_consumers", None) or []
    if not consumers or len(consumers[0]) < 2:
        return None
    return getattr(consumers[0][1], "concurrency_manager", None)


async def _keep_lease(lock: VectorMembershipBackfillLeaderLock, lost: asyncio.Event, every: float) -> None:
    """Refresh the leader lease while a pass runs: one record can take longer
    than the lease, and a second replica would start the same jobs."""
    while True:
        await asyncio.sleep(every)
        if not await lock.refresh():
            lost.set()
            return


async def _pass_as_leader(
    lock: VectorMembershipBackfillLeaderLock, app_container: Any, graph_provider: IGraphDBProvider, logger: Logger,  # noqa: ANN401
) -> None:
    event_processor = app_container.event_processor()
    if inspect.isawaitable(event_processor):
        event_processor = await event_processor
    lost = asyncio.Event()
    keeper = asyncio.create_task(_keep_lease(lock, lost, max(1.0, lock.ttl_seconds / 3)))
    try:
        await complete_named_entities(
            graph_provider=graph_provider,
            sink=getattr(event_processor, "sink_orchestrator", None),
            logger=logger,
            concurrency_manager=_record_lease_manager(app_container),
            still_leader=lambda: not lost.is_set(),
        )
    finally:
        keeper.cancel()
        await asyncio.gather(keeper, return_exceptions=True)


def record_consumer_loop(consumers: list[Any] | None) -> asyncio.AbstractEventLoop | None:
    """The record consumer's running worker loop, where indexing holds its LLM slots."""
    if not consumers or len(consumers[0]) < 2:
        return None
    loop = getattr(consumers[0][1], "worker_loop", None)
    return loop if loop is not None and loop.is_running() else None


def _env_float(name: str, default: float) -> float:
    try:
        value = float(os.getenv(name, default))
    except ValueError:
        return default
    return max(1.0, value) if math.isfinite(value) else default


async def run_named_entity_completion_loop(
    app_container: Any,  # noqa: ANN401
    graph_provider: IGraphDBProvider,
) -> None:
    logger = app_container.logger()
    interval = _env_float("NAMED_ENTITY_COMPLETION_INTERVAL_SECONDS", DEFAULT_INTERVAL_SECONDS)
    await asyncio.sleep(STARTUP_GRACE_SECONDS)
    logger.info("named_entity_completion: starting, interval=%.0fs", interval)
    lock: VectorMembershipBackfillLeaderLock | None = None
    owner = f"named-entity-completion:{uuid4().hex}"
    try:
        while True:
            try:
                if lock is None:
                    redis_config = await MessagingUtils._get_redis_config(app_container)
                    lock = VectorMembershipBackfillLeaderLock(
                        logger, redis_config, owner, ttl_seconds=int(interval) + _LEASE_MARGIN_SECONDS, key=LEADER_KEY,
                    )
                if await lock.try_acquire():
                    await _pass_as_leader(lock, app_container, graph_provider, logger)
                else:
                    logger.debug("named_entity_completion: another replica holds the lease")
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("named_entity_completion: pass failed")
            await asyncio.sleep(interval)
    finally:
        if lock is not None:
            await lock.release()
            await lock.close()
