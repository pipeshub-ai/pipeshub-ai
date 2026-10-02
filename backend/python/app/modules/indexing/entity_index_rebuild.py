"""Rebuild the entity index (the ``entities`` vector collection) from the graph.

Entity points are otherwise written only when a record passes through
indexing, so an upgraded deployment starts with an empty or partial index,
and a changed embedding model leaves vectors from the old one. This loop
projects what the graph already holds, with no extraction or LLM call:

- per connector (app document): its record groups, then its indexed records;
- per org (org document): its canonical taxonomy nodes, departments and
  people, with membership read from the graph, deleting points no record
  reaches;
- per org, every ``SWEEP_INTERVAL_MS``: a sweep deleting points whose node is
  gone or belongs to another org (deleted record groups and users, rejected
  stale winners).

A document is done when its ``entityIndexState`` equals the current marker,
``v<ENTITY_INDEX_VERSION>:<embedding fingerprint>``, so a model change re-runs
every pass, and each point records the model that embedded it, so a point
from another model is re-embedded whenever it is next written. Mechanics
follow ``vector_membership_backfill``: one Redis leader, one page per tick, a
resumable cursor on the document, bounded attempts, backoff on failure.
"""

from __future__ import annotations

import asyncio
import inspect
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any
from uuid import uuid4

from app.config.constants.arangodb import CollectionNames, ProgressStatus
from app.models.entities import EntityRecord, EntityType
from app.modules.entity_resolution.models import KINDS_BY_COLLECTION
from app.modules.indexing.entity_projection import project_taxonomy_nodes
from app.modules.indexing.vector_membership_backfill import (
    LeaderLock,
    VectorMembershipBackfillLeaderLock,
)
from app.services.messaging.utils import MessagingUtils
from app.utils.time_conversion import get_epoch_timestamp_in_ms

if TYPE_CHECKING:
    from collections.abc import Callable
    from logging import Logger

    from app.modules.transformers.entity_vectorstore import (
        EntityPointRef,
        EntityVectorStore,
    )
    from app.services.graph_db.interface.graph_db_provider import IGraphDBProvider

# Bump when what a pass writes changes, so every document is re-projected.
# 2: people (KG-13).
ENTITY_INDEX_VERSION = 2

LEADER_KEY = "entity_index_rebuild:leader"
PAGE_SIZE = 200
# Smaller: membership is read per node, and a hub node ("English") reaches
# most of a tenant's records.
TAXONOMY_PAGE_SIZE = 50
MAX_ATTEMPTS = 3
# Consecutive ticks that raised on one document before it is given up, so a
# document that always fails cannot hold the loop. High enough to ride out a
# database outage under backoff.
MAX_TICK_ERRORS = 10
STARTUP_GRACE_SECONDS = 60.0
# Between ticks that did work, and while idle or not leader.
BUSY_INTERVAL_SECONDS = 2.0
IDLE_INTERVAL_SECONDS = 60.0
SWEEP_INTERVAL_MS = 24 * 60 * 60 * 1000
SWEEP_POINTS_PER_TICK = 2000
# One graph lookup per collection per this many points.
_SWEEP_LOOKUP_BATCH = 500
_BACKOFF_FACTOR = 2
_MAX_BACKOFF_MULTIPLIER = 16

_APPS = CollectionNames.APPS.value
_ORGS = CollectionNames.ORGS.value
_RECORDS = CollectionNames.RECORDS.value
_RECORD_GROUPS = CollectionNames.RECORD_GROUPS.value
_DEPARTMENTS = CollectionNames.DEPARTMENTS.value
_USERS = CollectionNames.USERS.value

CONNECTOR_SOURCES: tuple[str, ...] = (_RECORD_GROUPS, _RECORDS)
ENTITY_INDEX_TAXONOMY_SOURCES: tuple[str, ...] = (
    CollectionNames.CATEGORIES.value,
    _DEPARTMENTS,
    CollectionNames.LANGUAGES.value,
    CollectionNames.SUBCATEGORIES1.value,
    CollectionNames.SUBCATEGORIES2.value,
    CollectionNames.SUBCATEGORIES3.value,
    CollectionNames.TOPICS.value,
    _USERS,
)

_SWEPT_TYPES: tuple[str, ...] = (
    EntityType.CATEGORY.value,
    EntityType.SUBCATEGORY.value,
    EntityType.TOPIC.value,
    EntityType.LANGUAGE.value,
    EntityType.DEPARTMENT.value,
    EntityType.RECORD_GROUP.value,
    EntityType.PERSON.value,
)
# Node collection of a swept point; subcategories are resolved by level.
_SWEPT_COLLECTIONS: dict[str, str] = {
    EntityType.CATEGORY.value: CollectionNames.CATEGORIES.value,
    EntityType.TOPIC.value: CollectionNames.TOPICS.value,
    EntityType.LANGUAGE.value: CollectionNames.LANGUAGES.value,
    EntityType.DEPARTMENT.value: _DEPARTMENTS,
    EntityType.RECORD_GROUP.value: _RECORD_GROUPS,
    EntityType.PERSON.value: _USERS,
}
_SUBCATEGORY_COLLECTIONS: dict[str, str] = {
    kind.level: collection for collection, kind in KINDS_BY_COLLECTION.items() if kind.level
}


class EntityIndexState:
    """Field names on app and org documents."""

    STATE = "entityIndexState"
    # The marker the cursor and counters below were built for; under any
    # other marker they are stale and the pass restarts.
    TARGET = "entityIndexTarget"
    PHASE = "entityIndexPhase"
    AFTER_KEY = "entityIndexAfterKey"
    ATTEMPTS = "entityIndexAttempts"
    FAILURES = "entityIndexFailures"
    EXHAUSTED = "entityIndexExhausted"
    ERRORS = "entityIndexErrors"
    SWEPT_AT = "entityIndexSweptAt"
    SWEEP_OFFSET = "entityIndexSweepOffset"
    SWEEP_FAILURES = "entityIndexSweepFailures"


def entity_index_marker(fingerprint: str) -> str:
    return f"v{ENTITY_INDEX_VERSION}:{fingerprint}"


def fingerprint_of(marker: object) -> str | None:
    if not isinstance(marker, str) or not marker.startswith("v") or ":" not in marker:
        return None
    version, fingerprint = marker.split(":", 1)
    return fingerprint if version[1:].isdigit() and fingerprint else None


def _text(value: object) -> str:
    return value.strip() if isinstance(value, str) else ""


def _name(value: object) -> str:
    """The name as stored, or "" when blank. Not stripped: index time writes
    it as-is, and a different payload would re-embed every unchanged point."""
    return value if isinstance(value, str) and value.strip() else ""


def _int(value: Any) -> int:  # noqa: ANN401
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


@dataclass
class _Pass:
    """One document's pass, as read at the start of a tick."""

    collection: str
    key: str
    doc: dict[str, Any]
    sources: tuple[str, ...]
    marker: str

    @property
    def fresh(self) -> bool:
        """The stored progress belongs to another marker (or none)."""
        return self.doc.get(EntityIndexState.TARGET) != self.marker

    def _progress(self, field: str) -> object:
        return None if self.fresh else self.doc.get(field)

    @property
    def phase(self) -> str:
        phase = self._progress(EntityIndexState.PHASE)
        return phase if phase in self.sources else self.sources[0]

    @property
    def after_key(self) -> str | None:
        if self._progress(EntityIndexState.PHASE) not in self.sources:
            return None
        key = self._progress(EntityIndexState.AFTER_KEY)
        return key if isinstance(key, str) and key else None

    @property
    def attempts(self) -> int:
        return _int(self._progress(EntityIndexState.ATTEMPTS))

    @property
    def failures(self) -> int:
        return _int(self._progress(EntityIndexState.FAILURES))

    @property
    def errors(self) -> int:
        return _int(self._progress(EntityIndexState.ERRORS))


class EntityIndexRebuilder:
    """One tick does one unit of work: a page of a connector's pass, else a
    page of an org's taxonomy pass, else a chunk of an org's sweep."""

    def __init__(
        self,
        *,
        logger: Logger,
        graph_provider: IGraphDBProvider,
        store: EntityVectorStore,
        lock: LeaderLock,
        page_size: int = PAGE_SIZE,
        sweep_points_per_tick: int = SWEEP_POINTS_PER_TICK,
        now_ms: Callable[[], int] = get_epoch_timestamp_in_ms,
    ) -> None:
        self.logger = logger
        self.graph = graph_provider
        self.store = store
        self.lock = lock
        self.page_size = max(1, page_size)
        self.taxonomy_page_size = min(self.page_size, TAXONOMY_PAGE_SIZE)
        self.sweep_points_per_tick = max(1, sweep_points_per_tick)
        self.now_ms = now_ms

    async def tick(self) -> str:
        """``not_leader``, ``idle``, ``connector``, ``taxonomy`` or ``sweep``."""
        if not await self.lock.try_acquire():
            return "not_leader"
        marker = entity_index_marker(await self.store.embedding_fingerprint())

        app = await self.graph.get_entity_index_candidate(_APPS, marker)
        if app and (key := _key_of(app)):
            await self._run_page(_Pass(_APPS, key, app, CONNECTOR_SOURCES, marker))
            return "connector"

        sweep_before = self.now_ms() - SWEEP_INTERVAL_MS
        org = await self.graph.get_entity_index_candidate(
            _ORGS, marker, sweep_before=sweep_before,
        )
        if not org or not (key := _key_of(org)):
            return "idle"
        if org.get(EntityIndexState.STATE) != marker:
            await self._run_page(_Pass(_ORGS, key, org, ENTITY_INDEX_TAXONOMY_SOURCES, marker))
            return "taxonomy"
        await self._sweep_chunk(key, org)
        return "sweep"

    # ------------------------------------------------------------------
    # Passes
    # ------------------------------------------------------------------

    async def _run_page(self, run: _Pass) -> None:
        try:
            await self._pass_page(run)
        except _LostLeadership:
            # The next leader re-reads this page from the same cursor; counting
            # its failures here would charge them twice.
            self.logger.warning(
                "entity_index_rebuild: lost leadership | %s=%s phase=%s; state not recorded",
                run.collection, run.key, run.phase,
            )
        except Exception:
            await self._charge_error(run)
            raise

    async def _charge_error(self, run: _Pass) -> None:
        errors = run.errors + 1
        update: dict[str, Any] = dict(_FRESH_PROGRESS) if run.fresh else {}
        update |= {EntityIndexState.TARGET: run.marker, EntityIndexState.ERRORS: errors}
        if errors >= MAX_TICK_ERRORS:
            self.logger.error(
                "entity_index_rebuild: giving up after %d consecutive errors | %s=%s phase=%s",
                errors, run.collection, run.key, run.phase,
            )
            update |= {
                EntityIndexState.STATE: run.marker,
                EntityIndexState.PHASE: None,
                EntityIndexState.AFTER_KEY: None,
                EntityIndexState.EXHAUSTED: True,
                EntityIndexState.ERRORS: 0,
            }
        try:
            await self.graph.update_node(run.key, run.collection, update)
        except Exception:
            self.logger.warning(
                "entity_index_rebuild: could not record the error | %s=%s",
                run.collection, run.key, exc_info=True,
            )

    async def _pass_page(self, run: _Pass) -> None:
        phase, after_key = run.phase, run.after_key
        limit = self.page_size if run.collection == _APPS else self.taxonomy_page_size
        rows = await self.graph.page_entity_index_source(phase, run.key, after_key, limit)
        if run.collection == _APPS:
            failed = await self._project_connector_rows(run, phase, rows)
        else:
            failed = await self._project_taxonomy_rows(run, phase, rows)
        self.logger.info(
            "entity_index_rebuild: page done | %s=%s phase=%s after=%s rows=%d failed=%d",
            run.collection, run.key, phase, after_key, len(rows), failed,
        )
        if not await self.lock.refresh():
            raise _LostLeadership
        await self._advance(run, phase, rows, failed, limit)

    async def _advance(
        self, run: _Pass, phase: str, rows: list[dict], failed: int, limit: int,
    ) -> None:
        failures = run.failures + failed
        update: dict[str, Any] = dict(_FRESH_PROGRESS) if run.fresh else {}
        update |= {
            EntityIndexState.TARGET: run.marker,
            EntityIndexState.FAILURES: failures,
            EntityIndexState.ERRORS: 0,
        }
        last_key = _key_of(rows[-1]) if rows else None
        if len(rows) >= limit and last_key:
            update |= {EntityIndexState.PHASE: phase, EntityIndexState.AFTER_KEY: last_key}
            await self.graph.update_node(run.key, run.collection, update)
            return
        if len(rows) >= limit:
            self.logger.error(
                "entity_index_rebuild: full page without a key | %s=%s phase=%s; "
                "skipping the rest of the phase",
                run.collection, run.key, phase,
            )
            failures += 1
            update[EntityIndexState.FAILURES] = failures
        index = run.sources.index(phase)
        if index + 1 < len(run.sources):
            update |= {
                EntityIndexState.PHASE: run.sources[index + 1],
                EntityIndexState.AFTER_KEY: None,
            }
            await self.graph.update_node(run.key, run.collection, update)
            return
        await self._complete(run, failures)

    async def _complete(self, run: _Pass, failures: int) -> None:
        reset: dict[str, Any] = dict(_FRESH_PROGRESS) if run.fresh else {}
        reset |= {
            EntityIndexState.TARGET: run.marker,
            EntityIndexState.PHASE: None,
            EntityIndexState.AFTER_KEY: None,
            EntityIndexState.ERRORS: 0,
        }
        if not failures:
            self.logger.info(
                "entity_index_rebuild: pass complete | %s=%s marker=%s",
                run.collection, run.key, run.marker,
            )
            await self.graph.update_node(run.key, run.collection, reset | {
                EntityIndexState.STATE: run.marker,
                EntityIndexState.ATTEMPTS: 0,
                EntityIndexState.FAILURES: 0,
                EntityIndexState.EXHAUSTED: False,
            })
            return
        attempts = run.attempts + 1
        if attempts < MAX_ATTEMPTS:
            self.logger.warning(
                "entity_index_rebuild: pass had %d failure(s); retrying | %s=%s attempt=%d/%d",
                failures, run.collection, run.key, attempts, MAX_ATTEMPTS,
            )
            await self.graph.update_node(run.key, run.collection, reset | {
                EntityIndexState.ATTEMPTS: attempts,
                EntityIndexState.FAILURES: 0,
            })
            return
        # Recorded as done so the loop moves on, with the evidence kept: the
        # failed entities are rewritten when their records are next indexed,
        # since a point from another model never compares as unchanged.
        self.logger.error(
            "entity_index_rebuild: giving up after %d attempts with %d failure(s) | %s=%s",
            attempts, failures, run.collection, run.key,
        )
        await self.graph.update_node(run.key, run.collection, reset | {
            EntityIndexState.STATE: run.marker,
            EntityIndexState.ATTEMPTS: attempts,
            EntityIndexState.FAILURES: failures,
            EntityIndexState.EXHAUSTED: True,
        })

    async def _project_connector_rows(self, run: _Pass, phase: str, rows: list[dict]) -> int:
        entities = []
        for row in rows:
            key, name, org_id = _key_of(row), _name(row.get("name")), _text(row.get("orgId"))
            if not key or not name or not org_id:
                continue
            if phase == _RECORD_GROUPS:
                entities.append(EntityRecord.for_record_group(key, name, org_id, run.key))
                continue
            if row.get("indexingStatus") != ProgressStatus.COMPLETED.value or row.get("isDeleted"):
                continue
            entities.append(EntityRecord.for_record(
                key, name, org_id, run.key, _text(row.get("recordGroupId")) or None,
            ))
        if not entities:
            return 0
        # Replace mode: a record's and a group's membership is exactly their
        # own connector and group, as on the index path.
        return (await self.store.upsert_entities_batch(entities, merge_membership=False)).failed

    async def _project_taxonomy_rows(self, run: _Pass, phase: str, rows: list[dict]) -> int:
        async def _still_leader() -> None:
            # A hub node's membership read can take minutes; writing after the
            # lease lapsed would race the next leader on the same page.
            if not await self.lock.refresh():
                raise _LostLeadership

        return await project_taxonomy_nodes(
            graph=self.graph, store=self.store, org_id=run.key, collection=phase,
            rows=rows, logger=self.logger, before_write=_still_leader,
        )

    # ------------------------------------------------------------------
    # Sweep
    # ------------------------------------------------------------------

    async def _sweep_chunk(self, org_id: str, org: dict[str, Any]) -> None:
        offset = org.get(EntityIndexState.SWEEP_OFFSET)
        offset = offset if isinstance(offset, str) and offset else None
        try:
            # Scrolling fails too, not only lookups: Redis refuses offsets
            # past its 10,000-result window on a large org.
            refs, next_offset = await self._scan(org_id, offset)
            stale = await self._stale(org_id, refs)
            if not await self.lock.refresh():
                self.logger.warning(
                    "entity_index_rebuild: lost leadership during sweep | org=%s; nothing deleted",
                    org_id,
                )
                return
            # Counted too: a delete one backend always rejects would otherwise
            # keep this org first in line and starve every other org.
            for entity_type, ids in sorted(stale.items()):
                await self.store.delete_entities(org_id, entity_type, ids)
        except Exception:
            failures = _int(org.get(EntityIndexState.SWEEP_FAILURES)) + 1
            if failures < MAX_ATTEMPTS:
                await self.graph.update_node(org_id, _ORGS, {
                    EntityIndexState.SWEEP_FAILURES: failures,
                })
            else:
                self.logger.error(
                    "entity_index_rebuild: sweep abandoned after %d failures | org=%s; "
                    "retried after the next interval", failures, org_id,
                )
                await self.graph.update_node(org_id, _ORGS, {
                    EntityIndexState.SWEPT_AT: self.now_ms(),
                    EntityIndexState.SWEEP_OFFSET: None,
                    EntityIndexState.SWEEP_FAILURES: 0,
                })
            raise
        stale_keys = {(t, i) for t, ids in stale.items() for i in ids}
        deleted = sum((r.entity_type, r.entity_id) in stale_keys for r in refs)
        if next_offset is not None and deleted:
            # On a positional cursor (Redis) the deleted points no longer hold
            # their places, so the stored offset would skip that many.
            next_offset = self.store.offset_after_delete(next_offset, deleted)
        self.logger.info(
            "entity_index_rebuild: sweep chunk | org=%s offset=%s scanned=%d stale=%d "
            "next=%s done=%s",
            org_id, offset, len(refs), deleted, next_offset, next_offset is None,
        )
        if next_offset is not None:
            await self.graph.update_node(org_id, _ORGS, {
                EntityIndexState.SWEEP_OFFSET: next_offset,
                EntityIndexState.SWEEP_FAILURES: 0,
            })
            return
        await self.graph.update_node(org_id, _ORGS, {
            EntityIndexState.SWEPT_AT: self.now_ms(),
            EntityIndexState.SWEEP_OFFSET: None,
            EntityIndexState.SWEEP_FAILURES: 0,
        })

    async def _scan(
        self, org_id: str, offset: str | None,
    ) -> tuple[list[EntityPointRef], str | None]:
        refs: list[EntityPointRef] = []
        while len(refs) < self.sweep_points_per_tick:
            page, offset = await self.store.page_entity_points(
                org_id, list(_SWEPT_TYPES), offset=offset,
                limit=min(_SWEEP_LOOKUP_BATCH, self.sweep_points_per_tick - len(refs)),
            )
            refs.extend(page)
            if offset is None:
                break
        return refs, offset

    async def _stale(self, org_id: str, refs: list[EntityPointRef]) -> dict[str, list[str]]:
        """Points whose node is gone, merged into another, or belongs to
        another org, by type.

        A node without an org is kept: departments are global, and legacy
        taxonomy nodes still carry records until they are migrated."""
        by_collection: dict[str, list[EntityPointRef]] = {}
        for ref in refs:
            if ref.entity_type == EntityType.SUBCATEGORY.value:
                collection = _SUBCATEGORY_COLLECTIONS.get(ref.level or "")
            else:
                collection = _SWEPT_COLLECTIONS.get(ref.entity_type)
            if collection:
                by_collection.setdefault(collection, []).append(ref)

        stale: dict[str, list[str]] = {}
        for collection, group in by_collection.items():
            for start in range(0, len(group), _SWEEP_LOOKUP_BATCH):
                batch = group[start:start + _SWEEP_LOOKUP_BATCH]
                rows = await self.graph.get_nodes_by_field_in(
                    collection, "id", sorted({r.entity_id for r in batch}),
                    return_fields=["id", "orgId", "mergedInto"], raise_on_error=True,
                )
                owner = {
                    _key_of(row): row.get("orgId") for row in rows or [] if not row.get("mergedInto")
                }
                for ref in batch:
                    if ref.entity_id not in owner or owner[ref.entity_id] not in (None, org_id):
                        stale.setdefault(ref.entity_type, []).append(ref.entity_id)
        return stale


class _LostLeadership(Exception):
    """Another replica may now hold this document; record nothing."""


_FRESH_PROGRESS: dict[str, Any] = {
    EntityIndexState.PHASE: None,
    EntityIndexState.AFTER_KEY: None,
    EntityIndexState.ATTEMPTS: 0,
    EntityIndexState.FAILURES: 0,
    EntityIndexState.EXHAUSTED: False,
}


def _key_of(doc: dict[str, Any]) -> str | None:
    key = doc.get("_key") or doc.get("id")
    return key if isinstance(key, str) and key else None


async def _resolve_store(app_container: Any) -> EntityVectorStore | None:  # noqa: ANN401
    getter = getattr(app_container, "entity_vector_store", None)
    if getter is None:
        return None
    store = getter() if callable(getter) else getter
    if inspect.isawaitable(store):
        store = await store
    return store


async def run_entity_index_rebuild_loop(
    app_container: Any,  # noqa: ANN401
    graph_provider: IGraphDBProvider,
) -> None:
    logger = app_container.logger()
    logger.info("entity_index_rebuild: starting in %.0fs", STARTUP_GRACE_SECONDS)
    await asyncio.sleep(STARTUP_GRACE_SECONDS)

    owner = f"entity-index:{uuid4().hex}"
    lock: VectorMembershipBackfillLeaderLock | None = None
    rebuilder: EntityIndexRebuilder | None = None
    backoff = 1
    try:
        while True:
            interval = IDLE_INTERVAL_SECONDS
            try:
                if lock is None:
                    redis_config = await MessagingUtils._get_redis_config(app_container)
                    lock = VectorMembershipBackfillLeaderLock(
                        logger, redis_config, owner, key=LEADER_KEY,
                    )
                    rebuilder = None
                if rebuilder is None:
                    store = await _resolve_store(app_container)
                    if store is not None:
                        rebuilder = EntityIndexRebuilder(
                            logger=logger, graph_provider=graph_provider, store=store, lock=lock,
                        )
                if rebuilder is None:
                    logger.warning("entity_index_rebuild: entity store unavailable; skipping tick")
                else:
                    outcome = await rebuilder.tick()
                    backoff = 1
                    if outcome not in ("idle", "not_leader"):
                        interval = BUSY_INTERVAL_SECONDS
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("entity_index_rebuild: tick failed")
                if lock is not None:
                    await lock.close()
                    lock = None
                backoff = min(backoff * _BACKOFF_FACTOR, _MAX_BACKOFF_MULTIPLIER)
                interval = IDLE_INTERVAL_SECONDS * backoff
            await asyncio.sleep(interval)
    finally:
        if lock is not None:
            await lock.release()
            await lock.close()


__all__ = [
    "ENTITY_INDEX_TAXONOMY_SOURCES",
    "ENTITY_INDEX_VERSION",
    "EntityIndexRebuilder",
    "EntityIndexState",
    "entity_index_marker",
    "fingerprint_of",
    "run_entity_index_rebuild_loop",
]
