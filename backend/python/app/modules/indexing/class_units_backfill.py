"""Embed class header units and residue units for code records indexed before they existed
(EMBED_UNITS_VERSION).

A code record indexed before this version has its blocks embedded, but no unit for each class (its
signature, docstring and member signatures) and no residue unit (its short top-level spans), until the
file changes. This adds them once per version.

Not a connector reindex, which is how the PARSER_VERSION backfill (app/migrations/code_graph_backfill.py)
works: a reindex sends ``forceReindex``, which forces the record's LLM stages (classify, entities) to run
again, and it fetches every file from its source again. This reads each record's stored blob and embeds
only those units through ``VectorStore.index_class_units``. No source fetch, parse or reconcile, no block
point, no blob write and no pipeline stage: the inputs the LLM stages are fingerprinted on are left as
they are.

Selection is exact and self-maintaining: completed code records are paged, and a record is embedded only
if the vector store has no residue unit for it (code that writes it writes the class units too), checked
once per page for all of its records. A record whose blob is missing or unreadable is counted as failed
and skipped; it gains its units when its file next changes.

Off unless CODE_GRAPH_BACKFILL=true (and CODE_GRAPH_ENABLED not false), like the PARSER_VERSION backfill,
with the same flag shape (/migrations/<name>/<version>). It runs in the indexing service, which holds the
embedding model, once per EMBED_UNITS_VERSION, one page of records at a time with a pause between pages,
on one indexing replica (a leader lock). The flag keeps the cursor and the counts after every page, so a
restart resumes where it stopped. Pages are listed across orgs; each org's records are embedded under
that org's context.
"""
from __future__ import annotations

import asyncio
from dataclasses import asdict, dataclass
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any
from uuid import uuid4

from app.config.constants.arangodb import CollectionNames, ProgressStatus, RecordTypes
from app.core.org_context import for_org, system
from app.models.blocks import BlocksContainer
from app.modules.artifacts.reader import record_artifact_reader
from app.modules.code_graph import enabled as code_graph_switch
from app.modules.code_graph.version import EMBED_UNITS_VERSION
from app.utils.env_utils import env_bool

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable
    from logging import Logger

    from app.config.configuration_service import ConfigurationService
    from app.modules.transformers.vectorstore import VectorStore
    from app.services.graph_db.interface.graph_db_provider import IGraphDBProvider

__all__ = ["FLAG_PREFIX", "BackfillCounts", "backfill_class_units", "mark_done_without_code",
           "run_class_units_backfill"]

FLAG_PREFIX = "/migrations/class_units_backfill/"
LEADER_KEY = "class_units_backfill:leader"
_PAGE = 500
_PAUSE_SECONDS = 1.0
_STARTUP_GRACE_SECONDS = 60.0
_CODE_RECORDS = {"recordType": RecordTypes.CODE_FILE.value, "indexingStatus": ProgressStatus.COMPLETED.value}
_RECORD_FIELDS = ["_key", "orgId", "virtualRecordId", "connectorId", "connectorName", "recordName",
                  "externalRecordId", "recordType", "indexingStatus"]


@dataclass
class BackfillCounts:
    pages: int = 0
    records: int = 0   # completed code records that were checked
    embedded: int = 0  # of them, records that gained units
    units: int = 0     # class and residue units written
    skipped: int = 0   # checked and needing none (already there, or nothing to build)
    failed: int = 0    # blob missing or unreadable, or the embed failed


def _switched_on(logger: Logger) -> bool:
    if not code_graph_switch.CODE_GRAPH_ENABLED:
        logger.info("Class units backfill skipped: CODE_GRAPH_ENABLED is false")
        return False
    if not env_bool("CODE_GRAPH_BACKFILL", False):
        logger.info("Class units backfill skipped: set CODE_GRAPH_BACKFILL=true to add class search units "
                    "to code indexed before %s", EMBED_UNITS_VERSION)
        return False
    return True


async def backfill_class_units(
    *,
    graph_provider: IGraphDBProvider,
    vector_store: VectorStore,
    blob_store: Any,  # noqa: ANN401 - BlobStorage; only get_record_from_storage is read
    config_service: ConfigurationService,
    logger: Logger,
    page_size: int = _PAGE,
    pause_seconds: float = _PAUSE_SECONDS,
    keep_lease: Any = None,  # noqa: ANN401 - async () -> bool; False stops the run (another replica took over)
) -> BackfillCounts | None:
    """Run (or resume) this version's backfill; None when it is switched off or already done."""
    if not _switched_on(logger):
        return None
    flag_key = f"{FLAG_PREFIX}{EMBED_UNITS_VERSION}"
    flag = await config_service.get_config(flag_key, use_cache=False)
    flag = flag if isinstance(flag, dict) else {}
    if flag.get("done") is True:
        return None
    after = flag.get("after")
    counts = BackfillCounts(**{k: int(flag.get(k) or 0) for k in BackfillCounts.__dataclass_fields__})
    if after:
        logger.info("Class units backfill for %s resuming after %s (%s)", EMBED_UNITS_VERSION, after, asdict(counts))

    while True:
        if keep_lease is not None and not await keep_lease():
            logger.warning("Class units backfill stopped: its leader lease was lost")
            return counts
        with system("class_units_backfill"):  # a page spans orgs; each org's records are embedded under it
            rows = await graph_provider.get_documents_paginated(
                collection=CollectionNames.RECORDS.value, limit=page_size, filters=_CODE_RECORDS,
                sort_field="_key", after_key=after, return_fields=_RECORD_FIELDS, raise_on_error=True,
            ) or []
        if rows:
            await _backfill_page(rows, vector_store, blob_store, logger, counts)
            counts.pages += 1
            after = rows[-1].get("_key")
        done = len(rows) < page_size
        await config_service.set_config(flag_key, {"done": done, **({} if done else {"after": after}), **asdict(counts)})
        logger.info("Class units backfill page %d: %s", counts.pages, asdict(counts))
        if done:
            logger.info("Class units backfill for %s done: %d record(s) gained %d class unit(s), %d failed",
                        EMBED_UNITS_VERSION, counts.embedded, counts.units, counts.failed)
            return counts
        await asyncio.sleep(pause_seconds)


async def _backfill_page(rows: list[dict], vector_store: VectorStore, blob_store: Any,  # noqa: ANN401
                         logger: Logger, counts: BackfillCounts) -> None:
    by_org: dict[str, dict[str, dict]] = {}
    for row in rows:
        vrid = row.get("virtualRecordId")
        # Re-checked: a page is filtered in the query, and a row can change while the run walks the pages.
        if (row.get("recordType") == RecordTypes.CODE_FILE.value
                and row.get("indexingStatus") == ProgressStatus.COMPLETED.value
                and vrid and row.get("orgId")):
            by_org.setdefault(row["orgId"], {}).setdefault(vrid, row)
    reader = record_artifact_reader(blob_store)
    for org_id, records in sorted(by_org.items()):
        with for_org(org_id, job="class_units_backfill"):
            await _backfill_org_records(org_id, records, vector_store, reader, logger, counts)


async def _backfill_org_records(org_id: str, records: dict[str, dict], vector_store: VectorStore,
                                reader: Any, logger: Logger, counts: BackfillCounts) -> None:  # noqa: ANN401
    """Embed the units of one org's records on a page (virtual record id -> record) that lack them."""
    views = {vrid: _record_view(row) for vrid, row in records.items()}
    have = await vector_store.virtual_records_with_residue_units([(org_id, v, view) for v, view in views.items()])
    counts.records += len(records)
    counts.skipped += len(have & records.keys())
    pending = [vrid for vrid in records if vrid not in have]
    try:  # one lookup for the page; without it each read resolves its own id
        lookups = await reader.lookup_many(pending) if pending else {}
    except Exception as exc:
        logger.warning("Class units backfill: batched record lookup failed, reading one by one: %s", exc)
        lookups = {}
    for vrid in pending:
        try:
            stored = await reader.read_record(vrid, org_id, lookup=lookups.get(vrid))
            containers = (stored or {}).get("block_containers")
            if not containers:
                raise LookupError("no stored blocks")
            blocks = BlocksContainer.model_validate(containers)
            written = await vector_store.index_class_units(
                records[vrid]["_key"], vrid, org_id, blocks, views[vrid],
                stored_now=_stored_blocks(reader, vrid, org_id))
        except Exception as exc:  # one record must not stop the backfill; it is counted and logged
            counts.failed += 1
            logger.warning("Class units backfill skipped record %s (%s): %s", records[vrid]["_key"], vrid, exc)
            continue
        if written:
            counts.embedded += 1
            counts.units += written
        else:
            counts.skipped += 1


def _stored_blocks(reader: Any, vrid: str, org_id: str) -> Callable[[], Awaitable[BlocksContainer | None]]:  # noqa: ANN401
    """The stored record, read again. A reindex between the backfill's read and its write replaces changed
    classes' ids and the residue's text, and no stored metadata would name units written from the older
    blob, so nothing would remove them: a stale class unit would answer as whichever class now sits at its
    index. The blob is written before the reindex's vector writes, so this re-read after our write sees it."""
    async def current() -> BlocksContainer | None:
        stored = (await reader.read_record(vrid, org_id) or {}).get("block_containers")
        return BlocksContainer.model_validate(stored) if stored else None
    return current


def _record_view(row: dict) -> SimpleNamespace:
    """The fields of a record the units' collection and context line read, from its graph document."""
    return SimpleNamespace(connector_id=row.get("connectorId"), connector_name=row.get("connectorName"),
                           record_name=row.get("recordName"), external_record_id=row.get("externalRecordId"))


async def mark_done_without_code(graph_provider: IGraphDBProvider, config_service: ConfigurationService,
                                 logger: Logger) -> bool:
    """Whether this version's backfill is done; marked so when the deployment holds no code record yet.

    Every code record indexed from now on gets its class and residue units, so with none indexed before
    there is nothing to backfill, whether or not the backfill is switched on, and a path-free search_code
    can say it is complete. Records are not org-scoped here: the flag is the deployment's."""
    flag_key = f"{FLAG_PREFIX}{EMBED_UNITS_VERSION}"
    flag = await config_service.get_config(flag_key, use_cache=False)
    if isinstance(flag, dict) and flag.get("done") is True:
        return True
    if flag:
        return False
    with system("class_units_backfill"):
        any_code = await graph_provider.get_documents_paginated(
            collection=CollectionNames.RECORDS.value, limit=1, filters={"recordType": RecordTypes.CODE_FILE.value},
            return_fields=["_key"], raise_on_error=True)
    if any_code:
        return False
    await config_service.set_config(flag_key, {"done": True, **asdict(BackfillCounts())})
    logger.info("Class units backfill for %s: no code indexed before it, so nothing to add", EMBED_UNITS_VERSION)
    return True


async def run_class_units_backfill(app_container: Any, graph_provider: IGraphDBProvider) -> None:  # noqa: ANN401
    """Startup task on indexing: wait out startup, take the leader lease, run the backfill once."""
    from app.modules.indexing.vector_membership_backfill import (
        VectorMembershipBackfillLeaderLock,
    )
    from app.services.messaging.utils import MessagingUtils

    logger = app_container.logger()
    try:
        if await mark_done_without_code(graph_provider, app_container.config_service(), logger):
            return
    except Exception:  # the backfill below, or the next start, decides instead
        logger.warning("Class units backfill: could not check for code records", exc_info=True)
    if not _switched_on(logger):
        return
    await asyncio.sleep(_STARTUP_GRACE_SECONDS)
    lock = VectorMembershipBackfillLeaderLock(
        logger, await MessagingUtils._get_redis_config(app_container), f"class-units:{uuid4().hex}", key=LEADER_KEY,
    )
    try:
        if not await lock.try_acquire():
            logger.info("Class units backfill left to the replica holding its lease")
            return
        vector_store = await _resolved(app_container.vector_store)
        blob_store = await _resolved(app_container.blob_storage)
        await backfill_class_units(graph_provider=graph_provider, vector_store=vector_store, blob_store=blob_store,
                                   config_service=app_container.config_service(), logger=logger,
                                   keep_lease=lock.refresh)
    except asyncio.CancelledError:
        raise
    except Exception:
        logger.exception("Class units backfill failed; it resumes from its last page on the next start")
    finally:
        await lock.release()
        await lock.close()


async def _resolved(provider: Any) -> Any:  # noqa: ANN401 - a dependency-injector provider
    value = provider()
    return await value if asyncio.iscoroutine(value) or isinstance(value, asyncio.Future) else value
