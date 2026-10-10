"""Rebuild the stored content of indexed records whose storage document is gone.

Records with identical content share a virtual record id (VRID) and one stored
copy, mapped VRID -> storage document in ``virtualRecordToDocIdMapping``.
Deleting a connector or knowledge base used to delete copies that other
connectors' records still mapped to, and the one-shot rebuild after it could
be lost; those records now fail every read of their content. This sweep finds
them once per ``HEAL_VERSION``: per page of a connector's indexed records it
resolves the mappings, asks storage in one call which documents are gone, and
force re-indexes one live holder per lost VRID (``StorageCleanupHelper.
reindex_one_holder``), whose storage write re-points the mapping for all.
A VRID one of whose holders is already being indexed is left to that run, which
the graph records, so a restart does not publish it again. At most
``MAX_IN_FLIGHT`` of the sweep's re-indexes are outstanding at once: a tick
tops the window up as indexing finishes them, so the pace follows indexing.

It runs in the connectors service, which already publishes these re-index
events (connector and KB delete) on its message producer. Mechanics are
``connector_sweep``'s. A Node without the missing-documents route (mixed
versions mid-upgrade) defers the sweep without marking anything done.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any
from uuid import uuid4

from app.config.constants.arangodb import ProgressStatus
from app.connectors.core.base.data_processor.storage_cleanup import (
    MissingDocumentsRouteUnavailable,
    StorageCleanupHelper,
    indexing_under_way,
)
from app.modules.indexing.connector_sweep import (
    ConnectorSweep,
    PageResult,
    SweepFields,
    key_of,
    run_connector_sweep_loop,
)
from app.modules.indexing.vector_membership_backfill import (
    LeaderLock,
    VectorMembershipBackfillLeaderLock,
)
from app.modules.transformers.blob_storage import BlobStorage
from app.services.graph_db.common.record_visibility import RecordVisibility
from app.services.messaging.utils import MessagingUtils

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable
    from logging import Logger

    from app.services.graph_db.interface.graph_db_provider import IGraphDBProvider

# Bump to sweep every connector again.
HEAL_VERSION = "v1"
LEADER_KEY = "stored_content_heal:leader"
PAGE_SIZE = 100
# Forced re-indexes are full parse/embed runs on the indexing service.
MAX_IN_FLIGHT = 20
MAX_ATTEMPTS = 3
STARTUP_GRACE_SECONDS = 300.0
BUSY_INTERVAL_SECONDS = 5.0
IDLE_INTERVAL_SECONDS = 600.0
ROUTE_UNAVAILABLE_INTERVAL_SECONDS = 1800.0
ERROR_INTERVAL_SECONDS = 60.0
_LEASE_RENEW_EVERY_N_PUBLISHES = 5


class StoredContentHealState:
    """Fields on the app document."""

    STATE = "storedContentHealState"
    AFTER_KEY = "storedContentHealAfterKey"
    CHECKED = "storedContentHealChecked"
    MISSING = "storedContentHealMissing"
    HEALED = "storedContentHealHealed"
    ORPHANED = "storedContentHealOrphaned"
    FAILURES = "storedContentHealFailures"
    ATTEMPTS = "storedContentHealAttempts"
    EXHAUSTED = "storedContentHealExhausted"


def _indexed_vrid(row: dict[str, Any]) -> str | None:
    vrid = row.get("virtualRecordId")
    if (
        not key_of(row)
        or not isinstance(vrid, str)
        or not vrid
        or row.get("isDeleted") is True
        or row.get("indexingStatus") != ProgressStatus.COMPLETED.value
    ):
        return None
    return vrid


class StoredContentHeal(ConnectorSweep):
    """One tick checks one page of one connector's indexed records."""

    name = "stored_content_heal"
    version = HEAL_VERSION
    fields = SweepFields(
        state=StoredContentHealState.STATE,
        after_key=StoredContentHealState.AFTER_KEY,
        attempts=StoredContentHealState.ATTEMPTS,
        exhausted=StoredContentHealState.EXHAUSTED,
        failures=StoredContentHealState.FAILURES,
        counters=(
            StoredContentHealState.CHECKED,
            StoredContentHealState.MISSING,
            StoredContentHealState.HEALED,
            StoredContentHealState.ORPHANED,
        ),
    )
    max_attempts = MAX_ATTEMPTS
    give_up_consequence = "those records' content stays unreadable until they are re-indexed"

    def __init__(
        self,
        *,
        logger: Logger,
        graph_provider: IGraphDBProvider,
        blob_store: BlobStorage,
        storage: StorageCleanupHelper,
        publish: Callable[[str, dict], Awaitable[Any]],
        lock: LeaderLock,
        page_size: int = PAGE_SIZE,
        max_in_flight: int = MAX_IN_FLIGHT,
    ) -> None:
        super().__init__(logger=logger, graph_provider=graph_provider, lock=lock, page_size=page_size)
        self.blob_store = blob_store
        self.storage = storage
        self.publish = publish
        self.max_in_flight = max(1, max_in_flight)
        # Holders this process re-indexed and their org, until indexing is done
        # with them. Only the window is lost with the process; the graph still
        # marks each one under way.
        self._in_flight: dict[str, str] = {}

    async def _capacity(self) -> int:
        by_org: dict[str, list[str]] = {}
        for key, org_id in self._in_flight.items():
            by_org.setdefault(org_id, []).append(key)
        still: dict[str, str] = {}
        for org_id, keys in by_org.items():
            rows = await self.graph.get_records_by_record_ids(
                keys, org_id, visibility=RecordVisibility.ALL,
            )
            still.update({k: org_id for row in rows or [] if indexing_under_way(row) and (k := key_of(row))})
        self._in_flight = still
        return self.max_in_flight - len(still)

    async def process_page(
        self, app: dict[str, Any], app_key: str, rows: list[dict[str, Any]],
    ) -> PageResult:
        candidates = [(row, vrid) for row in rows if (vrid := _indexed_vrid(row))]
        if not candidates:
            return PageResult()
        capacity = await self._capacity()
        if capacity <= 0:
            return PageResult(waiting=True)
        lookups = await self.blob_store.get_document_ids_by_virtual_record_ids(
            list(dict.fromkeys(vrid for _, vrid in candidates))
        )
        doc_of: dict[str, str] = {}
        docs_by_org: dict[str, set[str]] = {}
        for row, vrid in candidates:
            doc_id = (lookups.get(vrid) or {}).get("record_doc_id")
            org_id = row.get("orgId") or app.get("orgId")
            if not doc_id or not org_id:
                continue
            doc_of[vrid] = str(doc_id)
            docs_by_org.setdefault(str(org_id), set()).add(str(doc_id))

        missing_docs: set[str] = set()
        for org_id, doc_ids in docs_by_org.items():
            try:
                missing_docs.update(await self.storage.find_missing_documents(org_id, sorted(doc_ids)))
            except MissingDocumentsRouteUnavailable as exc:
                self.logger.warning(
                    "stored_content_heal: storage has no missing-documents route (HTTP %d) | "
                    "connector=%s org=%s; retrying later, nothing marked done",
                    exc.status, app_key, org_id,
                )
                return PageResult(deferred=True)
        return await self._heal(
            app_key, sorted(docs_by_org), rows, candidates, doc_of, missing_docs, capacity,
        )

    async def _heal(
        self,
        app_key: str,
        org_ids: list[str],
        rows: list[dict[str, Any]],
        candidates: list[tuple[dict[str, Any], str]],
        doc_of: dict[str, str],
        missing_docs: set[str],
        capacity: int,
    ) -> PageResult:
        vrid_of_row = {id(row): vrid for row, vrid in candidates}
        checked: set[str] = set()
        missing: set[str] = set()
        healed = orphaned = failed = 0
        stop_after: str | None = None
        previous_key: str | None = None
        for row in rows:
            vrid = vrid_of_row.get(id(row))
            lost = vrid is not None and vrid in doc_of and doc_of[vrid] in missing_docs
            if lost and vrid not in missing and healed >= capacity:
                stop_after = previous_key
                break
            previous_key = key_of(row)
            if vrid is None or vrid not in doc_of:
                continue
            checked.add(doc_of[vrid])
            if not lost or vrid in missing:
                continue
            missing.add(vrid)
            try:
                outcome = await self.storage.reindex_one_holder(vrid, self.publish)
                if outcome.published:
                    healed += 1
                    self._in_flight[outcome.record_key] = str(row.get("orgId") or org_ids[0])
                elif not outcome.under_way:
                    orphaned += 1
            except asyncio.CancelledError:
                raise
            except Exception:
                failed += 1
                self.logger.warning(
                    "stored_content_heal: could not re-index a holder of VRID %s | connector=%s",
                    vrid, app_key, exc_info=True,
                )
            if healed and healed % _LEASE_RENEW_EVERY_N_PUBLISHES == 0 and not await self.lock.refresh():
                return PageResult(lost_leadership=True)
        if missing:
            self.logger.info(
                "stored_content_heal: lost stored content | connector=%s org=%s missing=%d "
                "healed=%d orphaned=%d failed=%d",
                app_key, ",".join(org_ids), len(missing), healed, orphaned, failed,
            )
        return PageResult(
            counts={
                StoredContentHealState.CHECKED: len(checked),
                StoredContentHealState.MISSING: len(missing),
                StoredContentHealState.HEALED: healed,
                StoredContentHealState.ORPHANED: orphaned,
                StoredContentHealState.FAILURES: failed,
            },
            stop_after=stop_after,
        )


async def run_stored_content_heal_loop(
    app_container: Any,  # noqa: ANN401
    graph_provider: IGraphDBProvider,
    *,
    sleep: Callable[[float], Awaitable[Any]] | None = None,
) -> None:
    logger = app_container.logger()
    owner = f"stored-content-heal:{uuid4().hex}"

    async def make_lock() -> LeaderLock:
        redis_config = await MessagingUtils._get_redis_config(app_container)
        return VectorMembershipBackfillLeaderLock(logger, redis_config, owner, key=LEADER_KEY)

    storage = StorageCleanupHelper(logger, graph_provider, app_container.config_service())

    async def make_sweep(lock: LeaderLock) -> StoredContentHeal | None:
        producer = getattr(app_container, "messaging_producer", None)
        if producer is None:
            return None

        async def publish(topic: str, event: dict) -> bool:
            return await producer.send_message(topic=topic, message=event)

        return StoredContentHeal(
            logger=logger,
            graph_provider=graph_provider,
            blob_store=BlobStorage(logger, app_container.config_service(), graph_provider),
            storage=storage,
            publish=publish,
            lock=lock,
        )

    try:
        await run_connector_sweep_loop(
            logger=logger,
            name="stored_content_heal",
            make_lock=make_lock,
            make_sweep=make_sweep,
            startup_grace_seconds=STARTUP_GRACE_SECONDS,
            busy_interval_seconds=BUSY_INTERVAL_SECONDS,
            idle_interval_seconds=IDLE_INTERVAL_SECONDS,
            deferred_interval_seconds=ROUTE_UNAVAILABLE_INTERVAL_SECONDS,
            error_interval_seconds=ERROR_INTERVAL_SECONDS,
            sleep=sleep,
        )
    finally:
        await storage.close()


__all__ = [
    "HEAL_VERSION",
    "StoredContentHeal",
    "StoredContentHealState",
    "run_stored_content_heal_loop",
]
