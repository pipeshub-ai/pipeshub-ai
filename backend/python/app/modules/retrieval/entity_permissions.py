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

import logging
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from app.config.constants.arangodb import Connectors, PermissionModel

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping

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
PROBE_BATCH = 20
PROBE_MAX_ROUNDS = 3
CANDIDATE_BATCH_MIN = 40
CANDIDATE_BATCH_MAX = 200
MAX_SCAN_PER_CALL = 1000
PREVIEW_RECORD_COUNT = 3
SEARCH_SCOPE_MAX_ENTITIES = 5
SEARCH_SCOPE_MAX_RECORDS = 500
SEARCH_SCOPE_MAX_SCAN = 2000

_ACCESS_CONTEXT_CACHE_KEY = "_kg_entity_access_context"


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


@dataclass(frozen=True)
class EntityRecordPage:
    records: list[dict[str, Any]]
    next_cursor: str | None


@dataclass
class _Probe:
    hit: dict[str, Any]
    entity_id: str
    entity_type: str
    connector_ids: list[str]
    permitted: list[dict[str, Any]] = field(default_factory=list)
    exhausted: bool = False


async def get_entity_access_context(
    state: dict[str, Any] | None,
    graph_provider: "IGraphDBProvider | None",
    *,
    org_id: str,
    user_id: str,
    source_ids: Iterable[str] | None = None,
) -> EntityAccessContext:
    """Resolve (or reuse for this request) the apps and record groups the
    user can reach, narrowed to the agent's configured sources."""
    if not org_id or not user_id or graph_provider is None:
        raise EntityAccessError("Entity access needs an org, a user and a graph provider")

    scope_key = tuple(sorted({s for s in (source_ids or ()) if s}))
    cache = state.get(_ACCESS_CONTEXT_CACHE_KEY) if state is not None else None
    if isinstance(cache, dict) and scope_key in cache:
        return cache[scope_key]

    try:
        raw = await graph_provider.get_entity_access_context(
            user_id, org_id, list(scope_key) or None,
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

    context = EntityAccessContext(
        org_id=org_id,
        user_key=user_key,
        app_level_app_ids=frozenset(app_level),
        record_level_app_ids=frozenset(record_level),
        record_group_ids=frozenset(r for r in raw.get("record_group_ids") or [] if r),
        app_names=app_names,
    )
    if state is not None:
        if not isinstance(cache, dict):
            cache = {}
            state[_ACCESS_CONTEXT_CACHE_KEY] = cache
        cache[scope_key] = context
    return context


async def _filter_permitted_rows(
    graph_provider: "IGraphDBProvider",
    context: EntityAccessContext,
    rows: list[dict[str, Any]],
) -> set[str]:
    """Keys of ``rows`` the user may see: app-level connector rows pass on
    app access alone, record-level rows go through one batched graph check."""
    permitted: set[str] = set()
    needs_check: list[str] = []
    for row in rows:
        key = row.get("_key")
        connector_id = row.get("connectorId")
        if not key or connector_id not in context.app_ids:
            continue
        if connector_id in context.app_level_app_ids:
            permitted.add(key)
        else:
            needs_check.append(key)
    if needs_check:
        try:
            permitted |= await graph_provider.filter_nodes_with_permission_role(
                [{"id": key, "type": "record"} for key in dict.fromkeys(needs_check)],
                context.user_key,
                context.org_id,
                raise_on_error=True,
            )
        except Exception as exc:
            raise EntityAccessError("Record permission check failed") from exc
    return permitted


async def _fetch_candidates(
    graph_provider: "IGraphDBProvider",
    context: EntityAccessContext,
    refs: list[dict[str, Any]],
    *,
    record_types: list[str] | None,
    limit_per_entity: int,
    offset: int,
) -> dict[str, list[dict[str, Any]]]:
    try:
        return await graph_provider.get_entity_candidate_records(
            refs,
            context.org_id,
            record_types=record_types,
            limit_per_entity=limit_per_entity,
            offset=offset,
        )
    except Exception as exc:
        raise EntityAccessError("Entity record lookup failed") from exc


async def _run_probes(
    graph_provider: "IGraphDBProvider",
    context: EntityAccessContext,
    probes: list[_Probe],
) -> int:
    """Fetch candidates for every probe in rounds (one candidate query plus
    one permission check per round) until each is decided or exhausted.
    Returns the number of rounds run."""
    rounds = 0
    for round_index in range(PROBE_MAX_ROUNDS):
        pending = [
            p for p in probes
            if p.connector_ids and not p.exhausted
            and (round_index == 0 or not _is_kept(context, p))
        ]
        if not pending:
            break
        rounds += 1
        by_entity = await _fetch_candidates(
            graph_provider,
            context,
            [{"id": p.entity_id, "type": p.entity_type, "connectorIds": p.connector_ids} for p in pending],
            record_types=None,
            limit_per_entity=PROBE_BATCH,
            offset=round_index * PROBE_BATCH,
        )
        rows_by_probe = {id(p): by_entity.get(p.entity_id) or [] for p in pending}
        permitted = await _filter_permitted_rows(
            graph_provider, context, [row for rows in rows_by_probe.values() for row in rows],
        )
        for probe in pending:
            rows = rows_by_probe[id(probe)]
            probe.permitted.extend(row for row in rows if row.get("_key") in permitted)
            probe.exhausted = len(rows) < PROBE_BATCH
    for probe in probes:
        if not probe.connector_ids:
            probe.exhausted = True
    return rounds


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
    confirms the user can reach, best score first."""
    started = time.monotonic()
    fetch_k = min(max(top_k * OVERFETCH_FACTOR, top_k), OVERFETCH_CAP)
    seen: set[tuple[str, str]] = set()
    kept: list[EntityHit] = []
    evaluated = 0
    stats: list[str] = []

    for pass_name, rg_ids, connector_ids, org_wide in _search_passes(context):
        if len(kept) >= top_k or evaluated >= MAX_EVALUATED_HITS:
            break
        if time.monotonic() - started > SEARCH_DEADLINE_SECONDS:
            stats.append(f"{pass_name}=skipped(deadline)")
            break
        try:
            hits = await entity_vector_store.search_entities(
                query,
                context.org_id,
                set(rg_ids),
                set(connector_ids),
                entity_types=entity_types,
                top_k=fetch_k,
                allow_org_wide=org_wide,
            )
        except Exception as exc:
            raise EntityAccessError("Entity vector search failed") from exc

        probes: list[_Probe] = []
        for hit in hits:
            entity_id = hit.get("entityId")
            entity_type = hit.get("entityType")
            if not entity_id or entity_type not in SEARCHABLE_ENTITY_TYPES:
                continue
            if (entity_type, entity_id) in seen:
                continue
            seen.add((entity_type, entity_id))
            probes.append(_Probe(
                hit=hit,
                entity_id=entity_id,
                entity_type=entity_type,
                connector_ids=context.connector_scope(entity_type, entity_id),
            ))
        probes = probes[: MAX_EVALUATED_HITS - evaluated]
        evaluated += len(probes)

        rounds = await _run_probes(graph_provider, context, probes) if probes else 0
        pass_kept = 0
        for probe in probes:
            if not _is_kept(context, probe):
                continue
            pass_kept += 1
            kept.append(EntityHit(
                entity_id=probe.entity_id,
                entity_type=probe.entity_type,
                name=probe.hit.get("name") or probe.hit.get("canonicalName") or probe.entity_id,
                score=float(probe.hit.get("score") or 0.0),
                records=probe.permitted[:PREVIEW_RECORD_COUNT],
                more_records=len(probe.permitted) > PREVIEW_RECORD_COUNT or not probe.exhausted,
            ))
        stats.append(
            f"{pass_name}=hits:{len(hits)},evaluated:{len(probes)},kept:{pass_kept},rounds:{rounds}"
        )

    kept.sort(key=lambda h: h.score, reverse=True)
    logger.info(
        "entity search org=%s passes=[%s] kept=%d latency_ms=%d",
        context.org_id, "; ".join(stats), len(kept), int((time.monotonic() - started) * 1000),
    )
    return kept[:top_k]


def _parse_cursor(cursor: str | None) -> int:
    if cursor is None or cursor == "":
        return 0
    try:
        offset = int(cursor)
    except (TypeError, ValueError):
        raise ValueError(f"Invalid cursor {cursor!r}") from None
    if offset < 0:
        raise ValueError(f"Invalid cursor {cursor!r}")
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
    candidate list; every row is re-checked, so a forged cursor exposes
    nothing. No totals are returned."""
    if entity_type not in SEARCHABLE_ENTITY_TYPES:
        raise ValueError(f"Unsupported entity type {entity_type!r}")
    offset = _parse_cursor(cursor)
    limit = max(1, limit)
    connector_ids = context.connector_scope(entity_type, entity_id)
    if not connector_ids:
        return EntityRecordPage(records=[], next_cursor=None)

    records: list[dict[str, Any]] = []
    scanned = 0
    while scanned < max_scan:
        size = min(max(limit * 2, CANDIDATE_BATCH_MIN), CANDIDATE_BATCH_MAX, max_scan - scanned)
        by_entity = await _fetch_candidates(
            graph_provider,
            context,
            [{"id": entity_id, "type": entity_type, "connectorIds": connector_ids}],
            record_types=record_types,
            limit_per_entity=size,
            offset=offset,
        )
        batch = by_entity.get(entity_id) or []
        permitted = await _filter_permitted_rows(graph_provider, context, batch)
        for index, row in enumerate(batch):
            if row.get("_key") not in permitted:
                continue
            records.append(row)
            if len(records) == limit:
                more = index + 1 < len(batch) or len(batch) == size
                return EntityRecordPage(
                    records=records,
                    next_cursor=str(offset + index + 1) if more else None,
                )
        offset += len(batch)
        scanned += len(batch)
        if len(batch) < size:
            return EntityRecordPage(records=records, next_cursor=None)
    return EntityRecordPage(records=records, next_cursor=str(offset))


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
