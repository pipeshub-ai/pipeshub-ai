"""Restore records' stored labels from the spellings on their own edges.

Records enriched before each record kept its own extracted labels have, in
their stored copy, the names of the canonical nodes their categories,
subcategories, topics and languages resolved to. Their ``belongsTo*`` edges
kept the record's own spelling (``extractedName``, and every spelling in
``extractedNames`` once edges carry it), so the labels are
restored from those, with no extraction or model call. The record summary
vector is embedded from the summary alone and carries no labels, so the
stored copy is the only thing rewritten.

Only a record whose edges spell a node differently from the node's name is
read from storage. A stored copy stamped ``own_labels`` (every record the
resolver indexes now is) is left as it is; in any other, each stored label
that names a linked node is replaced by the record's spellings of that node.
Only an actual change is written, stamped, so a re-run leaves it. A record extracted
after this process started, or being indexed, is left alone: indexing writes
its own labels. A record with an edge to a canonical node that does not
record the spelling (an edge copied onto a deduplicated record before copies
carried it) is counted and left for a reindex, which writes the record's
spellings onto its existing edges.

Mechanics are ``connector_sweep``'s: one Redis leader, one page of one
connector per tick, a resumable cursor on the app document, bounded attempts.
The repaired, skipped, missing and failure counters describe the latest pass. The loop
ends once every connector is done.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any
from uuid import uuid4

from app.config.constants.arangodb import CollectionNames
from app.modules.entity_resolution.normalizer import normalize_name, spelling_key
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
from app.modules.transformers.blob_storage import StorageDocumentNotFoundError
from app.services.graph_db.taxonomy import TaxonomyLink, taxonomy_links
from app.services.messaging.utils import MessagingUtils
from app.utils.time_conversion import get_epoch_timestamp_in_ms

if TYPE_CHECKING:
    from logging import Logger

    from app.modules.transformers.blob_storage import BlobStorage
    from app.services.graph_db.interface.graph_db_provider import IGraphDBProvider

# Bump to run the repair again on every connector.
REPAIR_VERSION = "v1"
LEADER_KEY = "record_label_repair:leader"
PAGE_SIZE = 100
MAX_ATTEMPTS = 3
STARTUP_GRACE_SECONDS = 90.0
BUSY_INTERVAL_SECONDS = 1.0
# While no connector is listed (none yet, or the listing failed).
IDLE_INTERVAL_SECONDS = 600.0
ERROR_INTERVAL_SECONDS = 60.0
_LEASE_RENEW_EVERY_N_RECORDS = 10

# Set on a stored semantic_metadata whose labels are the record's own: by the
# resolver on every record it indexes, and by this repair on what it restores.
OWN_LABELS = "own_labels"

_RECORDS = CollectionNames.RECORDS.value
_CATEGORIES = CollectionNames.CATEGORIES.value
_SUBCATEGORY_SLOTS = (
    (CollectionNames.SUBCATEGORIES1.value, "sub_category_level_1"),
    (CollectionNames.SUBCATEGORIES2.value, "sub_category_level_2"),
    (CollectionNames.SUBCATEGORIES3.value, "sub_category_level_3"),
)
_LIST_SLOTS = (
    (CollectionNames.TOPICS.value, "topics"),
    (CollectionNames.LANGUAGES.value, "languages"),
)


class RecordLabelRepairState:
    """Fields on the app document."""

    STATE = "recordLabelRepairState"
    AFTER_KEY = "recordLabelRepairAfterKey"
    REPAIRED = "recordLabelRepairRepaired"
    SKIPPED = "recordLabelRepairSkipped"
    # Records whose stored copy no longer exists (see _MissingStoredCopy).
    MISSING = "recordLabelRepairMissing"
    FAILURES = "recordLabelRepairFailures"
    ATTEMPTS = "recordLabelRepairAttempts"
    EXHAUSTED = "recordLabelRepairExhausted"


class _MissingStoredCopy(Exception):
    """The record's stored copy is gone. Deleting the connector that first
    stored deduplicated content removed it while records of other connectors
    still used it, until a reindex rebuilds it, and that reindex writes the
    record's own labels too. Reading again cannot succeed, so this is not a
    failure that a retry of the connector could fix."""


def _ordered_spellings(current: object, links: list[TaxonomyLink]) -> list[str]:
    """The stored labels with each linked node's name replaced by the
    record's own spellings of that node, in the stored order, then the
    spellings of nodes the stored labels did not name.

    Only unmarked stored labels come here (see ``OWN_LABELS``), and each of
    those was written either by the earlier rewrite, as a linked node's name,
    or before resolution existed, as the record's own word on a legacy node.
    A label is matched to a remaining link whose node name equals it, else
    to one whose name differs only in case, spacing or punctuation (merges
    and migrations move edges only between such names); the link is used
    once. A label that matches no remaining link stays.
    """
    stored = [v for v in (current if isinstance(current, list) else []) if isinstance(v, str) and v.strip()]
    remaining = sorted(links, key=lambda link: link.spelling or "")
    ordered: list[str] = []
    for value in stored:
        exact = normalize_name(value)
        match = next((link for link in remaining if normalize_name(link.name) == exact), None)
        if match is None:
            loose = spelling_key(exact)
            match = next(
                (link for link in remaining if spelling_key(normalize_name(link.name)) == loose), None,
            )
        if match is None:
            ordered.append(value)
            continue
        remaining.remove(match)
        ordered.extend(match.spellings)
    for link in remaining:
        ordered.extend(link.spellings)
    seen: set[str] = set()
    unique: list[str] = []
    for spelling in ordered:
        if spelling and normalize_name(spelling) not in seen:
            seen.add(normalize_name(spelling))
            unique.append(spelling)
    return unique


def own_label_fields(
    semantic_metadata: dict[str, Any], links: list[TaxonomyLink],
) -> dict[str, Any] | None:
    """The label fields of a stored ``semantic_metadata`` rebuilt from the
    record's own edges, or ``None`` when an edge does not record its spelling.

    Each stored label that names a linked node gives way to the record's
    spellings of that node (``_ordered_spellings``). The subcategory chain
    hangs off the category as on the index path, and an absent subcategory
    level is ``None``.
    """
    if any(not link.spellings for link in links):
        return None
    by_collection: dict[str, list[TaxonomyLink]] = {}
    for link in links:
        by_collection.setdefault(link.collection, []).append(link)

    fields: dict[str, Any] = {
        "categories": _ordered_spellings(
            semantic_metadata.get("categories"), by_collection.get(_CATEGORIES, []),
        )[:1],
    }
    parent = bool(fields["categories"])
    for collection, slot in _SUBCATEGORY_SLOTS:
        spellings = _ordered_spellings(
            [semantic_metadata.get(slot)], by_collection.get(collection, []),
        )
        fields[slot] = spellings[0] if parent and spellings else None
        parent = fields[slot] is not None
    for collection, slot in _LIST_SLOTS:
        fields[slot] = _ordered_spellings(semantic_metadata.get(slot), by_collection.get(collection, []))
    return fields


def _patched(semantic_metadata: dict[str, Any], fields: dict[str, Any]) -> dict[str, Any]:
    # Stored as model_dump(exclude_none=True): an absent level is no key, and
    # an empty slot the stored copy did not have is not added.
    patched = dict(semantic_metadata)
    patched[OWN_LABELS] = True
    for slot, value in fields.items():
        if value is None or (value == [] and slot not in semantic_metadata):
            patched.pop(slot, None)
        else:
            patched[slot] = value
    return patched


class RecordLabelRepair(ConnectorSweep):
    """One tick repairs one page of one connector's records."""

    name = "record_label_repair"
    version = REPAIR_VERSION
    fields = SweepFields(
        state=RecordLabelRepairState.STATE,
        after_key=RecordLabelRepairState.AFTER_KEY,
        attempts=RecordLabelRepairState.ATTEMPTS,
        exhausted=RecordLabelRepairState.EXHAUSTED,
        failures=RecordLabelRepairState.FAILURES,
        counters=(
            RecordLabelRepairState.REPAIRED,
            RecordLabelRepairState.SKIPPED,
            RecordLabelRepairState.MISSING,
        ),
    )
    max_attempts = MAX_ATTEMPTS
    give_up_consequence = "those records keep their labels until reindexed"

    def __init__(
        self,
        *,
        logger: Logger,
        graph_provider: IGraphDBProvider,
        blob_store: BlobStorage,
        lock: LeaderLock,
        cutoff_ms: int,
        page_size: int = PAGE_SIZE,
    ) -> None:
        super().__init__(logger=logger, graph_provider=graph_provider, lock=lock, page_size=page_size)
        self.blob_store = blob_store
        self.cutoff_ms = cutoff_ms

    async def process_page(
        self, app: dict[str, Any], app_key: str, rows: list[dict[str, Any]],
    ) -> PageResult:
        keys = [k for row in rows if (k := key_of(row))]
        links_by_record: dict[str, list[TaxonomyLink]] = {}
        if keys:
            for link in taxonomy_links(await self.graph.get_record_taxonomy_links(keys)):
                links_by_record.setdefault(link.record_id, []).append(link)

        repaired = skipped = missing = failed = 0
        for processed, row in enumerate(rows, start=1):
            key = key_of(row)
            links = links_by_record.get(key or "")
            if not key or not links:
                continue
            if any(not link.spellings for link in links):
                skipped += 1
                continue
            if not any(link.extracted_names and link.spellings != (link.name,) for link in links):
                continue
            try:
                if await self._repair_record(key, row.get("virtualRecordId"), links):
                    repaired += 1
            except asyncio.CancelledError:
                raise
            except _MissingStoredCopy:
                missing += 1
            except Exception:
                failed += 1
                self.logger.warning(
                    "record_label_repair: record %s of connector %s failed; continuing the page",
                    key, app_key, exc_info=True,
                )
            if processed % _LEASE_RENEW_EVERY_N_RECORDS == 0 and not await self.lock.refresh():
                return PageResult(lost_leadership=True)
        if skipped:
            self.logger.info(
                "record_label_repair: %d record(s) of connector %s have an edge without its "
                "spelling; left for a reindex", skipped, app_key,
            )
        if missing:
            self.logger.info(
                "record_label_repair: %d record(s) of connector %s have no stored copy (removed "
                "with content they shared); left for the reindex that rebuilds it", missing, app_key,
            )
        return PageResult(counts={
            RecordLabelRepairState.REPAIRED: repaired,
            RecordLabelRepairState.SKIPPED: skipped,
            RecordLabelRepairState.MISSING: missing,
            RecordLabelRepairState.FAILURES: failed,
        })

    def _settled(self, record: dict[str, Any]) -> bool:
        extracted_at = record.get("lastExtractionTimestamp")
        return (
            isinstance(extracted_at, (int, float))
            and extracted_at < self.cutoff_ms
            and not record.get("processingStartedAt")
        )

    async def _repair_record(
        self, key: str, virtual_record_id: object, links: list[TaxonomyLink],
    ) -> bool:
        record = await self.graph.get_document(key, _RECORDS, raise_on_error=True)
        if not record or not self._settled(record):
            return False
        org_id = record.get("orgId")
        vrid = virtual_record_id or record.get("virtualRecordId")
        if not org_id or not isinstance(vrid, str) or not vrid:
            return False
        lookup = await self.blob_store.get_document_id_by_virtual_record_id(vrid)
        if not lookup or not lookup.get("record_doc_id"):
            return False
        try:
            stored = await self.blob_store.get_record_from_storage(vrid, org_id, lookup_result=lookup)
        except StorageDocumentNotFoundError as exc:
            raise _MissingStoredCopy from exc
        # A virtual record id can be shared; its copy is repaired with the
        # record that wrote it.
        if not isinstance(stored, dict) or str(stored.get("id") or "") != key:
            return False
        semantic = stored.get("semantic_metadata")
        if not isinstance(semantic, dict) or semantic.get(OWN_LABELS) is True:
            return False
        fields = own_label_fields(semantic, links)
        if fields is None:
            return False
        patched = _patched(semantic, fields)
        if {**patched, OWN_LABELS: None} == {**semantic, OWN_LABELS: None}:
            return False
        latest = await self.graph.get_document(key, _RECORDS, raise_on_error=True)
        if (
            not latest
            or not self._settled(latest)
            or latest.get("lastExtractionTimestamp") != record.get("lastExtractionTimestamp")
        ):
            return False
        try:
            await self.blob_store.update_record_buffer(
                org_id, lookup["record_doc_id"], {**stored, "semantic_metadata": patched}, vrid,
            )
        except StorageDocumentNotFoundError as exc:
            raise _MissingStoredCopy from exc
        return True


async def run_record_label_repair_loop(
    app_container: Any,  # noqa: ANN401
    graph_provider: IGraphDBProvider,
) -> None:
    from app.modules.transformers.blob_storage import BlobStorage

    logger = app_container.logger()
    cutoff_ms = get_epoch_timestamp_in_ms()
    owner = f"label-repair:{uuid4().hex}"

    async def make_lock() -> LeaderLock:
        redis_config = await MessagingUtils._get_redis_config(app_container)
        return VectorMembershipBackfillLeaderLock(logger, redis_config, owner, key=LEADER_KEY)

    async def make_sweep(lock: LeaderLock) -> RecordLabelRepair:
        return RecordLabelRepair(
            logger=logger,
            graph_provider=graph_provider,
            blob_store=BlobStorage(logger, app_container.config_service(), graph_provider),
            lock=lock,
            cutoff_ms=cutoff_ms,
        )

    await run_connector_sweep_loop(
        logger=logger,
        name="record_label_repair",
        make_lock=make_lock,
        make_sweep=make_sweep,
        startup_grace_seconds=STARTUP_GRACE_SECONDS,
        busy_interval_seconds=BUSY_INTERVAL_SECONDS,
        idle_interval_seconds=IDLE_INTERVAL_SECONDS,
        deferred_interval_seconds=IDLE_INTERVAL_SECONDS,
        error_interval_seconds=ERROR_INTERVAL_SECONDS,
    )


__all__ = [
    "REPAIR_VERSION",
    "RecordLabelRepair",
    "RecordLabelRepairState",
    "own_label_fields",
    "run_record_label_repair_loop",
]
