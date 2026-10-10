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

Mechanics follow ``vector_membership_backfill``: one Redis leader, one page
of one connector per tick, a resumable cursor on the app document, bounded
attempts. The repaired, skipped and failure counters describe the latest
pass. The loop ends once every connector is done.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any
from uuid import uuid4

from app.config.constants.arangodb import CollectionNames
from app.modules.entity_resolution.normalizer import normalize_name, spelling_key
from app.modules.indexing.vector_membership_backfill import (
    LeaderLock,
    VectorMembershipBackfillLeaderLock,
)
from app.services.graph_db.entity_index_queries import APP_STATUS_DELETING
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
_LEASE_RENEW_EVERY_N_RECORDS = 10
_BACKOFF_FACTOR = 2
_MAX_BACKOFF_MULTIPLIER = 16

# Set on a stored semantic_metadata whose labels are the record's own: by the
# resolver on every record it indexes, and by this repair on what it restores.
OWN_LABELS = "own_labels"

_APPS = CollectionNames.APPS.value
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
    FAILURES = "recordLabelRepairFailures"
    ATTEMPTS = "recordLabelRepairAttempts"
    EXHAUSTED = "recordLabelRepairExhausted"


def _int(value: Any) -> int:  # noqa: ANN401
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


def _key_of(doc: dict[str, Any]) -> str | None:
    key = doc.get("_key") or doc.get("id")
    return key if isinstance(key, str) and key else None


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


class RecordLabelRepair:
    """One tick repairs one page of one connector's records."""

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
        self.logger = logger
        self.graph = graph_provider
        self.blob_store = blob_store
        self.lock = lock
        self.cutoff_ms = cutoff_ms
        self.page_size = max(1, page_size)

    async def tick(self) -> str:
        """``not_leader``, ``no_apps``, ``idle`` (every connector done) or ``page``."""
        if not await self.lock.try_acquire():
            return "not_leader"
        apps = await self.graph.get_all_documents(_APPS)
        if not apps:
            return "no_apps"
        pending = sorted(
            (
                app for app in apps
                if _key_of(app)
                and app.get(RecordLabelRepairState.STATE) != REPAIR_VERSION
                and app.get("status") != APP_STATUS_DELETING
            ),
            key=lambda app: _key_of(app) or "",
        )
        if not pending:
            return "idle"
        await self._page(pending[0])
        return "page"

    async def _page(self, app: dict[str, Any]) -> None:
        app_key = _key_of(app) or ""
        after_key = app.get(RecordLabelRepairState.AFTER_KEY)
        after_key = after_key if isinstance(after_key, str) and after_key else None
        rows = await self.graph.page_records_for_vector_membership_backfill(
            app_key, after_key, self.page_size,
        )
        keys = [k for row in rows if (k := _key_of(row))]
        links_by_record: dict[str, list[TaxonomyLink]] = {}
        if keys:
            for link in taxonomy_links(await self.graph.get_record_taxonomy_links(keys)):
                links_by_record.setdefault(link.record_id, []).append(link)

        repaired = skipped = failed = 0
        for processed, row in enumerate(rows, start=1):
            key = _key_of(row)
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
            except Exception:
                failed += 1
                self.logger.warning(
                    "record_label_repair: record %s of connector %s failed; continuing the page",
                    key, app_key, exc_info=True,
                )
            if processed % _LEASE_RENEW_EVERY_N_RECORDS == 0 and not await self.lock.refresh():
                self.logger.warning(
                    "record_label_repair: lost leadership mid-page | connector=%s after=%s; "
                    "the next leader resumes from the same cursor", app_key, after_key,
                )
                return
        if not await self.lock.refresh():
            return

        # The counters describe one pass: the first page of a pass starts them
        # again, whether the pass is new or a retry.
        def _counted(field: str, page_count: int) -> int:
            return page_count + (_int(app.get(field)) if after_key else 0)

        totals = {
            RecordLabelRepairState.REPAIRED: _counted(RecordLabelRepairState.REPAIRED, repaired),
            RecordLabelRepairState.SKIPPED: _counted(RecordLabelRepairState.SKIPPED, skipped),
            RecordLabelRepairState.FAILURES: _counted(RecordLabelRepairState.FAILURES, failed),
        }
        self.logger.info(
            "record_label_repair: page done | connector=%s after=%s records=%d repaired=%d "
            "skipped=%d failed=%d", app_key, after_key, len(rows), repaired, skipped, failed,
        )
        if skipped:
            self.logger.info(
                "record_label_repair: %d record(s) of connector %s have an edge without its "
                "spelling; left for a reindex", skipped, app_key,
            )
        last_key = keys[-1] if keys else None
        if len(rows) >= self.page_size and last_key:
            await self.graph.update_node(app_key, _APPS, {
                RecordLabelRepairState.AFTER_KEY: last_key, **totals,
            })
            return
        await self._finish(app, app_key, totals)

    async def _finish(self, app: dict[str, Any], app_key: str, totals: dict[str, int]) -> None:
        failures = totals[RecordLabelRepairState.FAILURES]
        attempts = _int(app.get(RecordLabelRepairState.ATTEMPTS)) + (1 if failures else 0)
        if failures and attempts < MAX_ATTEMPTS:
            self.logger.warning(
                "record_label_repair: connector %s had %d failure(s); retrying from the start "
                "(attempt %d/%d)", app_key, failures, attempts, MAX_ATTEMPTS,
            )
            await self.graph.update_node(app_key, _APPS, {
                RecordLabelRepairState.AFTER_KEY: None,
                RecordLabelRepairState.ATTEMPTS: attempts,
                **totals,
            })
            return
        if failures:
            self.logger.error(
                "record_label_repair: giving up on connector %s after %d attempts with %d "
                "failure(s); those records keep their labels until reindexed",
                app_key, attempts, failures,
            )
        else:
            self.logger.info(
                "record_label_repair: connector %s done | repaired=%d skipped=%d",
                app_key, totals[RecordLabelRepairState.REPAIRED], totals[RecordLabelRepairState.SKIPPED],
            )
        await self.graph.update_node(app_key, _APPS, {
            RecordLabelRepairState.STATE: REPAIR_VERSION,
            RecordLabelRepairState.AFTER_KEY: None,
            RecordLabelRepairState.ATTEMPTS: attempts,
            RecordLabelRepairState.EXHAUSTED: bool(failures),
            **totals,
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
        stored = await self.blob_store.get_record_from_storage(vrid, org_id, lookup_result=lookup)
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
        await self.blob_store.update_record_buffer(
            org_id, lookup["record_doc_id"], {**stored, "semantic_metadata": patched}, vrid,
        )
        return True


async def run_record_label_repair_loop(
    app_container: Any,  # noqa: ANN401
    graph_provider: IGraphDBProvider,
) -> None:
    from app.modules.transformers.blob_storage import BlobStorage

    logger = app_container.logger()
    cutoff_ms = get_epoch_timestamp_in_ms()
    logger.info("record_label_repair: starting in %.0fs", STARTUP_GRACE_SECONDS)
    await asyncio.sleep(STARTUP_GRACE_SECONDS)

    owner = f"label-repair:{uuid4().hex}"
    lock: VectorMembershipBackfillLeaderLock | None = None
    repair: RecordLabelRepair | None = None
    backoff = 1
    try:
        while True:
            interval = IDLE_INTERVAL_SECONDS
            try:
                if lock is None:
                    redis_config = await MessagingUtils._get_redis_config(app_container)
                    lock = VectorMembershipBackfillLeaderLock(logger, redis_config, owner, key=LEADER_KEY)
                    repair = None
                if repair is None:
                    repair = RecordLabelRepair(
                        logger=logger,
                        graph_provider=graph_provider,
                        blob_store=BlobStorage(logger, app_container.config_service(), graph_provider),
                        lock=lock,
                        cutoff_ms=cutoff_ms,
                    )
                outcome = await repair.tick()
                backoff = 1
                if outcome == "idle":
                    logger.info("record_label_repair: every connector is done")
                    return
                if outcome == "page":
                    interval = BUSY_INTERVAL_SECONDS
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("record_label_repair: tick failed")
                if lock is not None:
                    await lock.close()
                    lock = None
                backoff = min(backoff * _BACKOFF_FACTOR, _MAX_BACKOFF_MULTIPLIER)
                interval = BUSY_INTERVAL_SECONDS * 60 * backoff
            await asyncio.sleep(interval)
    finally:
        if lock is not None:
            await lock.release()
            await lock.close()


__all__ = [
    "REPAIR_VERSION",
    "RecordLabelRepair",
    "RecordLabelRepairState",
    "own_label_fields",
    "run_record_label_repair_loop",
]
