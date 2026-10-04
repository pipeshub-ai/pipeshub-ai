"""Query-side permission layer for knowledge-graph entity search.

Stored entity membership (``connectorIds``/``recordGroupIds`` on vector
points) is only a recall hint for the vector search: it is empty after a
backfill, only ever unioned (so it goes stale), and older points keep it in a
nested layout. Every access decision here is made against the graph instead,
through provider calls that raise on failure — a failed query surfaces as
``EntityAccessError``, never as "no access".

Two permission levels come from the app document's ``permissionModel``:
KB apps and ``APP_LEVEL`` connectors grant every record in the app;
``RECORD_LEVEL`` connectors (and apps with no model set) need a per-record
check even when the user can reach the record's group.
"""
from __future__ import annotations

import asyncio
import base64
import json
import logging
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, TypeVar

from app.config.constants.arangodb import Connectors, PermissionModel
from app.modules.transformers.entity_vectorstore import EntitySearchPass
from app.services.graph_db.common.utils import PermittedEntityRows

if TYPE_CHECKING:
    from collections.abc import Awaitable, Iterable, Mapping

    from app.modules.transformers.entity_vectorstore import EntityVectorStore
    from app.services.graph_db.interface.graph_db_provider import IGraphDBProvider

logger = logging.getLogger(__name__)

RECORD_ENTITY_TYPE = "record"
RECORD_GROUP_ENTITY_TYPE = "record_group"
TAXONOMY_ENTITY_TYPES: frozenset[str] = frozenset(
    {"department", "category", "subcategory", "topic", "language"}
)
SEARCHABLE_ENTITY_TYPES: frozenset[str] = TAXONOMY_ENTITY_TYPES | {
    RECORD_ENTITY_TYPE,
    RECORD_GROUP_ENTITY_TYPE,
}

# Redis TAG queries get too long past this; the connector pass still covers
# these users, just with lower precision.
MAX_RG_FILTER_IDS = 2000
OVERFETCH_FACTOR = 3
OVERFETCH_CAP = 50
MAX_EVALUATED_HITS = 40
SEARCH_DEADLINE_SECONDS = 4.0
# A listing scans in batches; past this it returns what it found with a
# cursor to continue from, instead of running batch after batch.
LISTING_DEADLINE_SECONDS = 8.0
# Candidates each probe round walks per entity, newest first: a small first
# window keeps the common case cheap, later ones reach users whose readable
# records are older (KG-11). Bounded together by PROBE_ROUND_BUDGET.
PROBE_WINDOWS = (20, 180, 800)
# Candidates one probe round may walk across all its entities. A permission
# check runs per walked record-level row: measured at ~0.3 ms on Neo4j and
# ~2 ms on ArangoDB, so a full round stays inside SEARCH_DEADLINE_SECONDS.
PROBE_ROUND_BUDGET = 1500
LISTING_WINDOW_MIN = 100
LISTING_WINDOW_MAX = 500
MAX_SCAN_PER_CALL = 1000
# Server-side limit past the caller's deadline, so the client gives up first
# and the server stops shortly after instead of finishing abandoned work.
SERVER_TIMEOUT_GRACE_SECONDS = 0.5
PREVIEW_RECORD_COUNT = 3
SEARCH_SCOPE_MAX_ENTITIES = 5
SEARCH_SCOPE_MAX_RECORDS = 500
SEARCH_SCOPE_MAX_SCAN = 2000

_ACCESS_CONTEXT_CACHE_KEY = "_kg_entity_access_context"


_T = TypeVar("_T")

class EntityAccessError(Exception):
    """Entity access could not be resolved. Callers report a failure; they
    must never treat this as the user having no access."""


@dataclass(frozen=True)
class EntityAccessContext:
    org_id: str
    user_key: str
    app_level_app_ids: frozenset[str]
    record_level_app_ids: frozenset[str]
    record_group_ids: frozenset[str]
    app_names: Mapping[str, str]

    @property
    def app_ids(self) -> frozenset[str]:
        return self.app_level_app_ids | self.record_level_app_ids

    def connector_scope(self, entity_type: str, entity_id: str) -> list[str]:
        """Connectors whose records may be listed for this entity. A record
        group the user cannot reach only lists records from app-level
        connectors, where app access alone grants them."""
        if entity_type == RECORD_GROUP_ENTITY_TYPE and entity_id not in self.record_group_ids:
            return sorted(self.app_level_app_ids)
        return sorted(self.app_ids)


@dataclass(frozen=True)
class EntityHit:
    entity_id: str
    entity_type: str
    name: str
    score: float
    records: list[dict[str, Any]]
    more_records: bool
    aliases: tuple[str, ...] = ()


@dataclass(frozen=True)
class EntityRecordPage:
    """``capped``: the entity has more records than the provider scans, so
    the page is newest within a sample and an end of paging is not the end
    of the entity's records."""

    records: list[dict[str, Any]]
    next_cursor: str | None
    capped: bool = False


@dataclass
class _Probe:
    hit: dict[str, Any]
    entity_id: str
    entity_type: str
    connector_ids: list[str]
    permitted: list[dict[str, Any]] = field(default_factory=list)
    exhausted: bool = False
    capped: bool = False
    walked: int = 0


async def get_entity_access_context(
    state: dict[str, Any] | None,
    graph_provider: "IGraphDBProvider | None",
    *,
    org_id: str,
    user_id: str,
    source_ids: Iterable[str] | None = None,
    strict: bool = False,
    exclude_app_ids: Iterable[str] | None = None,
) -> EntityAccessContext:
    """Resolve (or reuse for this request) the apps and record groups the
    user can reach, narrowed to the agent's configured sources.

    ``strict`` with no sources reaches nothing, as in retrieval: without it an
    empty scope means every app the user can access, which would widen a
    project or agent whose sources were all removed.
    """
    if not org_id or not user_id or graph_provider is None:
        raise EntityAccessError("Entity access needs an org, a user and a graph provider")

    scope = tuple(sorted({s for s in (source_ids or ()) if s}))
    excluded = frozenset(a for a in (exclude_app_ids or ()) if a)
    cache_key = (scope, bool(strict) and not scope, tuple(sorted(excluded)))
    cache = state.get(_ACCESS_CONTEXT_CACHE_KEY) if state is not None else None
    if isinstance(cache, dict) and cache_key in cache:
        return cache[cache_key]

    if strict and not scope:
        context = EntityAccessContext(
            org_id=org_id,
            user_key="",
            app_level_app_ids=frozenset(),
            record_level_app_ids=frozenset(),
            record_group_ids=frozenset(),
            app_names={},
        )
    else:
        context = await _load_access_context(graph_provider, org_id, user_id, scope, excluded)

    if state is not None:
        if not isinstance(cache, dict):
            cache = {}
            state[_ACCESS_CONTEXT_CACHE_KEY] = cache
        cache[cache_key] = context
    return context


async def _load_access_context(
    graph_provider: "IGraphDBProvider",
    org_id: str,
    user_id: str,
    scope: tuple[str, ...],
    excluded: frozenset[str],
) -> EntityAccessContext:
    try:
        raw = await graph_provider.get_entity_access_context(
            user_id, org_id, list(scope) or None, exclude_app_ids=excluded,
        )
    except Exception as exc:
        raise EntityAccessError("Could not resolve the user's entity access") from exc
    if not raw:
        raise EntityAccessError("User not found")
    user_key = raw.get("user_key")
    if not user_key:
        raise EntityAccessError("User has no graph key")

    app_level: set[str] = set()
    record_level: set[str] = set()
    app_names: dict[str, str] = {}
    for app in raw.get("apps") or []:
        app_id = app.get("id")
        if not app_id:
            continue
        app_names[app_id] = app.get("name") or app.get("type") or app_id
        if (
            app.get("type") == Connectors.KNOWLEDGE_BASE.value
            or app.get("permissionModel") == PermissionModel.APP_LEVEL.value
        ):
            app_level.add(app_id)
        else:
            record_level.add(app_id)

    return EntityAccessContext(
        org_id=org_id,
        user_key=user_key,
        app_level_app_ids=frozenset(app_level),
        record_level_app_ids=frozenset(record_level),
        record_group_ids=frozenset(r for r in raw.get("record_group_ids") or [] if r),
        app_names=app_names,
    )


async def _fetch_permitted(
    graph_provider: "IGraphDBProvider",
    context: EntityAccessContext,
    refs: list[dict[str, Any]],
    *,
    record_types: list[str] | None,
    limit_per_entity: int,
    offset: int,
    window: int,
    deadline: float,
) -> dict[tuple[str, str], PermittedEntityRows]:
    """Permitted rows of one candidate window per ref, checked in the query.
    App-level rows pass on app access; the rest on a permission role.
    Domain, "anyone" and link shares grant no access, as in every check."""
    timeout = max(0.0, deadline - time.monotonic()) + SERVER_TIMEOUT_GRACE_SECONDS
    try:
        return await graph_provider.get_permitted_entity_records(
            refs,
            context.org_id,
            context.user_key,
            app_level_connector_ids=sorted(context.app_level_app_ids),
            record_types=record_types,
            limit_per_entity=limit_per_entity,
            offset=offset,
            window=window,
            timeout_seconds=timeout,
        )
    except Exception as exc:
        raise EntityAccessError("Entity record lookup failed") from exc


async def _run_probes(
    graph_provider: "IGraphDBProvider",
    context: EntityAccessContext,
    probes: list[_Probe],
    deadline: float,
) -> tuple[int, bool]:
    """Find permitted records for every probe in rounds, one query per round,
    each walking a wider window of the newest candidates, until each probe is
    decided or exhausted, or the deadline passes. Returns the rounds run and
    whether the deadline cut them short; an undecided probe is then left out,
    never guessed."""
    rounds = 0
    timed_out = False
    for round_index, planned_window in enumerate(PROBE_WINDOWS):
        pending = [
            p for p in probes
            if p.connector_ids and not p.exhausted
            and (round_index == 0 or not _is_kept(context, p))
        ]
        if not pending:
            break
        if time.monotonic() >= deadline:
            timed_out = True
            break
        rounds += 1
        window = max(1, min(planned_window, PROBE_ROUND_BUDGET // len(pending)))
        # Undecided probes have walked the same windows so far, so one offset
        # serves them all.
        offset = pending[0].walked
        try:
            by_entity = await _within(deadline, _fetch_permitted(
                graph_provider,
                context,
                [{"id": p.entity_id, "type": p.entity_type, "connectorIds": p.connector_ids} for p in pending],
                record_types=None,
                limit_per_entity=PREVIEW_RECORD_COUNT + 1,
                offset=offset,
                window=window,
                deadline=deadline,
            ))
        except TimeoutError:
            timed_out = True
            break
        for probe in pending:
            rows = _rows_for(by_entity, probe.entity_type, probe.entity_id)
            probe.permitted.extend(rows)
            probe.walked += rows.examined
            probe.exhausted = rows.window_size < window and rows.examined >= rows.window_size
            probe.capped = probe.capped or rows.capped
    for probe in probes:
        if not probe.connector_ids:
            probe.exhausted = True
    return rounds, timed_out


async def _within(deadline: float, awaitable: Awaitable[_T]) -> _T:
    """Await ``awaitable``, giving up (TimeoutError) when ``deadline`` passes."""
    return await asyncio.wait_for(awaitable, timeout=max(0.0, deadline - time.monotonic()))


def _rows_for(
    by_entity: dict[tuple[str, str], PermittedEntityRows], entity_type: str, entity_id: str,
) -> PermittedEntityRows:
    # Not ``.get(...) or ...``: an empty window is falsy, and swapping it for a
    # fresh one would lose its size and ``capped``.
    rows = by_entity.get((entity_type, entity_id))
    return rows if rows is not None else PermittedEntityRows()


def _is_kept(context: EntityAccessContext, probe: _Probe) -> bool:
    if probe.entity_type == RECORD_GROUP_ENTITY_TYPE and probe.entity_id in context.record_group_ids:
        return True
    return bool(probe.permitted)


def _search_passes(context: EntityAccessContext) -> list[tuple[str, frozenset[str], frozenset[str], bool]]:
    passes: list[tuple[str, frozenset[str], frozenset[str], bool]] = []
    rg_ids = (
        context.record_group_ids
        if len(context.record_group_ids) <= MAX_RG_FILTER_IDS
        else frozenset()
    )
    if rg_ids or context.app_level_app_ids:
        passes.append(("precise", rg_ids, context.app_level_app_ids, False))
    if context.record_level_app_ids:
        passes.append(("record_level", frozenset(), context.record_level_app_ids, False))
    if context.app_ids:
        # Catches entities whose stored membership is empty, partial or in the
        # legacy nested layout; every hit is still checked against the graph.
        passes.append(("org_wide", frozenset(), frozenset(), True))
    return passes


async def search_entities_for_user(
    entity_vector_store: "EntityVectorStore",
    graph_provider: "IGraphDBProvider",
    context: EntityAccessContext,
    query: str,
    *,
    entity_types: list[str] | None = None,
    top_k: int = 10,
) -> list[EntityHit]:
    """Vector search in widening passes, keeping only entities the graph
    confirms the user can reach, best score first.

    Every pass goes to the vector DB in one request. The hits are
    de-duplicated in pass order and the union is probed together, so a call
    costs one vector request and at most ``len(PROBE_WINDOWS)`` graph
    queries, each checking permissions in the query, all within
    ``SEARCH_DEADLINE_SECONDS``.
    """
    started = time.monotonic()
    deadline = started + SEARCH_DEADLINE_SECONDS
    fetch_k = min(max(top_k * OVERFETCH_FACTOR, top_k), OVERFETCH_CAP)
    passes = _search_passes(context)
    if not passes or time.monotonic() >= deadline:
        return []
    try:
        per_pass = await entity_vector_store.search_entities_passes(
            query,
            context.org_id,
            [EntitySearchPass(rg_ids, connector_ids, org_wide) for _, rg_ids, connector_ids, org_wide in passes],
            entity_types=entity_types,
            top_k=fetch_k,
        )
    except Exception as exc:
        raise EntityAccessError("Entity vector search failed") from exc

    seen: set[tuple[str, str]] = set()
    probes: list[_Probe] = []
    stats: list[str] = []
    for (pass_name, *_), hits in zip(passes, per_pass):
        added = 0
        for hit in hits:
            entity_id = hit.get("entityId")
            entity_type = hit.get("entityType")
            if not entity_id or entity_type not in SEARCHABLE_ENTITY_TYPES:
                continue
            if (entity_type, entity_id) in seen or len(probes) >= MAX_EVALUATED_HITS:
                continue
            seen.add((entity_type, entity_id))
            added += 1
            probes.append(_Probe(
                hit=hit,
                entity_id=entity_id,
                entity_type=entity_type,
                connector_ids=context.connector_scope(entity_type, entity_id),
            ))
        stats.append(f"{pass_name}=hits:{len(hits)},new:{added}")

    rounds, timed_out = await _run_probes(graph_provider, context, probes, deadline) if probes else (0, False)
    kept = [
        EntityHit(
            entity_id=probe.entity_id,
            entity_type=probe.entity_type,
            name=probe.hit.get("name") or probe.hit.get("canonicalName") or probe.entity_id,
            score=float(probe.hit.get("score") or 0.0),
            records=probe.permitted[:PREVIEW_RECORD_COUNT],
            more_records=(
                len(probe.permitted) > PREVIEW_RECORD_COUNT
                or not probe.exhausted
                or probe.capped
            ),
            aliases=tuple(str(a) for a in (probe.hit.get("aliases") or []) if a),
        )
        for probe in probes
        if _is_kept(context, probe)
    ]
    kept.sort(key=lambda h: h.score, reverse=True)
    logger.info(
        "entity search org=%s passes=[%s] evaluated=%d rounds=%d kept=%d deadline_hit=%s latency_ms=%d",
        context.org_id, "; ".join(stats), len(probes), rounds, len(kept), timed_out,
        int((time.monotonic() - started) * 1000),
    )
    return kept[:top_k]


def _decode_cursor_json(raw: str) -> int | None:
    """Offset out of a ``{"offset": N}`` envelope, plain or base64."""
    candidates = [raw]
    try:
        padded = raw + "=" * (-len(raw) % 4)
        candidates.append(base64.urlsafe_b64decode(padded.encode()).decode())
    except Exception:  # noqa: BLE001 - any malformed input is simply not a cursor
        pass
    for candidate in candidates:
        try:
            payload = json.loads(candidate)
        except (TypeError, ValueError):
            continue
        offset = payload.get("offset") if isinstance(payload, dict) else None
        # bool is an int subclass, and {"offset": true} is not an offset.
        if isinstance(offset, int) and not isinstance(offset, bool):
            return offset
    return None


def _parse_cursor(cursor: str | None) -> int:
    """Offset from a cursor string.

    Accepts the plain integer this module emits, and also the base64/JSON
    ``{"offset": N}`` envelope a model reconstructs when it paraphrases the
    cursor instead of copying it. The value is not a trust boundary — every
    row the offset reaches is permission-checked, and ``max_scan`` bounds the
    scan — so refusing a cursor whose meaning is unambiguous only costs the
    caller a page.
    """
    if cursor is None or cursor == "":
        return 0
    raw = cursor.strip()
    try:
        offset = int(raw)
    except (TypeError, ValueError):
        offset = _decode_cursor_json(raw)
    if offset is None or offset < 0:
        raise ValueError(
            f"Invalid cursor {cursor!r} - pass the cursor exactly as the previous "
            "page printed it, or omit it to start from the first page."
        )
    return offset


async def list_accessible_entity_records(
    graph_provider: "IGraphDBProvider",
    context: EntityAccessContext,
    *,
    entity_id: str,
    entity_type: str,
    record_types: list[str] | None = None,
    limit: int = 20,
    cursor: str | None = None,
    max_scan: int = MAX_SCAN_PER_CALL,
) -> EntityRecordPage:
    """Records connected to one entity that the user can access, newest
    first. ``next_cursor`` is an offset into the org- and connector-scoped
    candidate list; every row is checked in the query, so a forged cursor
    exposes nothing. No totals are returned. ``capped`` is set when the
    provider's scan of the entity hit its cap (see ``EntityRecordPage``)."""
    if entity_type not in SEARCHABLE_ENTITY_TYPES:
        raise ValueError(f"Unsupported entity type {entity_type!r}")
    offset = _parse_cursor(cursor)
    limit = max(1, limit)
    connector_ids = context.connector_scope(entity_type, entity_id)
    if not connector_ids:
        return EntityRecordPage(records=[], next_cursor=None)

    records: list[dict[str, Any]] = []
    scanned = 0
    capped = False
    deadline = time.monotonic() + LISTING_DEADLINE_SECONDS
    planned_window = min(max(limit * 4, LISTING_WINDOW_MIN), LISTING_WINDOW_MAX)
    while scanned < max_scan:
        if scanned and time.monotonic() >= deadline:
            logger.info(
                "entity listing stopped at its deadline org=%s entity=%s/%s offset=%d found=%d",
                context.org_id, entity_type, entity_id, offset, len(records),
            )
            return EntityRecordPage(records=records, next_cursor=str(offset), capped=capped)
        window = min(planned_window, max_scan - scanned)
        # Sparse access widens the next window, so finding a few readable
        # records among many takes a few queries, not dozens.
        planned_window = min(planned_window * 2, LISTING_WINDOW_MAX)
        fetch = _fetch_permitted(
            graph_provider,
            context,
            [{"id": entity_id, "type": entity_type, "connectorIds": connector_ids}],
            record_types=record_types,
            limit_per_entity=limit - len(records),
            offset=offset,
            window=window,
            deadline=deadline,
        )
        if not scanned:
            by_entity = await fetch
        else:
            # A later window cut by the deadline ends the page where it is,
            # instead of failing the call and losing what was found.
            try:
                by_entity = await _within(deadline, fetch)
            except TimeoutError:
                logger.info(
                    "entity listing window cut at its deadline org=%s entity=%s/%s offset=%d found=%d",
                    context.org_id, entity_type, entity_id, offset, len(records),
                )
                return EntityRecordPage(records=records, next_cursor=str(offset), capped=capped)
        rows = _rows_for(by_entity, entity_type, entity_id)
        capped = capped or rows.capped
        records.extend(rows)
        offset += rows.examined
        scanned += rows.examined
        if len(records) >= limit:
            more = rows.examined < rows.window_size or rows.window_size == window
            return EntityRecordPage(
                records=records[:limit],
                next_cursor=str(offset) if more else None,
                capped=capped,
            )
        if rows.window_size < window:
            if capped:
                logger.info(
                    "entity listing reached the scan cap org=%s entity=%s/%s offset=%d",
                    context.org_id, entity_type, entity_id, offset,
                )
            return EntityRecordPage(records=records, next_cursor=None, capped=capped)
    return EntityRecordPage(records=records, next_cursor=str(offset), capped=capped)


__all__ = [
    "EntityAccessContext",
    "EntityAccessError",
    "EntityHit",
    "EntityRecordPage",
    "PREVIEW_RECORD_COUNT",
    "RECORD_ENTITY_TYPE",
    "RECORD_GROUP_ENTITY_TYPE",
    "SEARCHABLE_ENTITY_TYPES",
    "SEARCH_SCOPE_MAX_ENTITIES",
    "SEARCH_SCOPE_MAX_RECORDS",
    "SEARCH_SCOPE_MAX_SCAN",
    "TAXONOMY_ENTITY_TYPES",
    "get_entity_access_context",
    "list_accessible_entity_records",
    "search_entities_for_user",
]
