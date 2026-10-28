"""``search_entities`` operation — semantic lookup over knowledge-graph
entities (departments, categories, subcategories, topics, languages, record
groups, record titles), limited to what the user can access.

Access is decided by ``app.modules.retrieval.entity_permissions``; this module
only shapes the result for the model and remembers the returned ids for
``find_records_by_entity`` and ``search(entity_ids=[...])``.
"""
from __future__ import annotations

import asyncio
import json
import logging
from typing import TYPE_CHECKING, Any

from app.agents.actions.knowledge_graph.ops.entity_filters import (
    limited_scope_note,
    load_entity_access_context,
    remember_entities,
)
from app.agents.actions.knowledge_graph.views import _compact_date, _short, _trunc
from app.models.source_dates import RecordDates
from app.modules.agents.qna.chat_state import remember_record_ids
from app.modules.named_entities.domain.kinds import (
    VALUE_KINDS,
    VECTOR_KINDS,
    EntityKind,
)
from app.modules.retrieval.entity_permissions import (
    RECORD_ENTITY_TYPE,
    SEARCHABLE_ENTITY_TYPES,
    EntityAccessContext,
    EntityAccessError,
    EntityHit,
    EntitySearchTimedOut,
    search_entities_for_user,
)
from app.modules.retrieval.provenance import graph_record_refs, note_retrieved_by
from app.utils.chat_helpers import get_record_id_shortener_if_enabled

if TYPE_CHECKING:
    from app.modules.agents.qna.chat_state import ChatState
    from app.utils.chat_helpers import RecordIdShortener

logger = logging.getLogger(__name__)

NAMED_ENTITIES_DISABLED_MSG = "Named-entity lookup is not enabled."


async def _named_entities_enabled(state: "ChatState") -> bool:
    from app.services.featureflag.platform_settings import is_named_entity_extraction_enabled

    return await is_named_entity_extraction_enabled(state.get("config_service"))

_MAX_TOP_K = 25
_DEFAULT_TOP_K = 10
_PREVIEW_ENTITY_COUNT = 3
MAX_QUERIES = 5


_ENTITY_KIND_NAMES: frozenset[str] = frozenset(kind.value for kind in EntityKind)


def _error(message: str) -> str:
    return json.dumps({"status": "error", "message": message})


async def execute_search_entities(
    state: "ChatState",
    query: str | None,
    entity_types: list[str] | None = None,
    top_k: int | None = _DEFAULT_TOP_K,
    named_entity_kinds: list[str] | None = None,
    *,
    queries: list[str] | None = None,
    entity_search_offered: bool = False,
) -> tuple[bool, str]:
    """Returns ``(success, json)``. A failure to resolve access or run the
    search is reported as a failed call, never as "no matching entities".

    ``entity_search_offered`` (``search_by_entities`` is granted, see
    ``entity_search_surface``): ``queries`` looks up several names in one call,
    concurrently, as ``{"status", "queries": [{"query", "results" | "message"}]}``
    with each query echoed, a single ``query`` keeping the one-search shape;
    named-entity kinds this search cannot serve are explained in ``kindNotes``
    and never widen it; and hits carry their named-entity kind."""
    if entity_search_offered:
        many = [q.strip() for q in (queries or []) if isinstance(q, str) and q.strip()]
        if many:
            asked = list(dict.fromkeys([*([query.strip()] if query and query.strip() else []), *many]))
            return await _search_many(state, asked, entity_types, top_k, named_entity_kinds)
    if not query or not query.strip():
        return False, _error("No query provided")
    if not state:
        return False, _error("Tool state not initialized")

    entity_vector_store = state.get("entity_vector_store")
    graph_provider = state.get("graph_provider")
    if not entity_vector_store or not graph_provider:
        return False, _error("Entity search is not available in this deployment")

    requested = [str(t).strip().lower() for t in (entity_types or []) if str(t).strip()]
    kinds = [str(k).strip().lower() for k in (named_entity_kinds or []) if str(k).strip()]
    # A kind put in entity_types ("organization") was ignored, leaving only the
    # other types searched; it is a named-entity kind.
    kinds = list(dict.fromkeys([*kinds, *(t for t in requested if t in _ENTITY_KIND_NAMES)]))
    requested = [t for t in requested if t not in _ENTITY_KIND_NAMES]
    kind_notes: list[str] = []
    asked_kinds = bool(kinds)
    if entity_search_offered:
        kinds, kind_notes = _searchable_kinds(kinds)
    enabled = await _named_entities_enabled(state)
    if not enabled:
        wanted_named = asked_kinds or "named_entity" in requested
        requested = [t for t in requested if t != "named_entity"]
        kinds = []
        kind_notes = []
        # An empty type list means "every type"; never let the flag turn a
        # named-entity search into that.
        if wanted_named and not requested:
            return False, _error(NAMED_ENTITIES_DISABLED_MSG)
    elif asked_kinds and not kinds:
        # Only kinds this search cannot serve: an empty kind list would widen
        # the named-entity part to every kind, so it is left out, and the other
        # entity types asked for are still searched.
        requested = [t for t in requested if t != "named_entity"]
        if not requested:
            return True, json.dumps({
                "status": "success", "message": "No accessible entities matched", "results": [],
                "kindNotes": kind_notes,
            })
    elif kinds and "named_entity" not in requested:
        requested.append("named_entity")
    valid_types = [t for t in dict.fromkeys(requested) if t in SEARCHABLE_ENTITY_TYPES]
    if requested and not valid_types:
        supported = ", ".join(sorted(SEARCHABLE_ENTITY_TYPES))
        return False, _error(f"Unsupported entity_types {requested}; supported: {supported}")

    bounded_top_k = max(
        1, min(top_k if top_k is not None else _DEFAULT_TOP_K, _MAX_TOP_K)
    )
    try:
        context = await load_entity_access_context(state)
        hits = await search_entities_for_user(
            entity_vector_store,
            graph_provider,
            context,
            query,
            entity_types=valid_types or None,
            kinds=kinds or None,
            top_k=bounded_top_k,
            include_named=enabled,
        )
    except EntitySearchTimedOut:
        logger.warning("search_entities timed out", exc_info=True)
        return False, _error(
            "Entity search timed out before it could confirm which matches you can access — "
            "this is not an empty result. Try again, or use knowledgegraph__search."
        )
    except EntityAccessError:
        logger.warning("search_entities failed", exc_info=True)
        return False, _error("Entity search failed — try again.")

    ignored = sorted(set(requested) - SEARCHABLE_ENTITY_TYPES)
    if not hits:
        payload: dict[str, Any] = {
            "status": "success",
            "message": "No accessible entities matched",
            "results": [],
        }
        if note := limited_scope_note(context):
            payload["scopeNote"] = note
        if ignored:
            payload["ignoredEntityTypes"] = ignored
        if kind_notes:
            payload["kindNotes"] = kind_notes
        return True, json.dumps(payload)

    remember_entities(
        state,
        [
            {
                "entityId": h.entity_id, "entityType": h.entity_type, "name": h.name,
                "filterName": h.graph_filter_name,
            }
            for h in hits
        ],
    )
    shortener = get_record_id_shortener_if_enabled(state)
    results, shown_record_ids = _render_hits(hits, context, shortener, with_kind=entity_search_offered)
    names = {h.entity_id: h.name for h in hits}
    names.update(
        (r["_key"], r.get("recordName") or "") for h in hits for r in h.records if r.get("_key")
    )
    remember_record_ids(state, shown_record_ids, names=names)
    shown = set(shown_record_ids)
    note_retrieved_by(state, "entity", graph_record_refs(
        row for hit in hits for row in hit.records if row.get("_key") in shown
    ))

    payload = {"status": "success", "results": results}
    if ignored:
        payload["ignoredEntityTypes"] = ignored
    if kind_notes:
        payload["kindNotes"] = kind_notes
    return True, json.dumps(payload)


async def _search_many(
    state: "ChatState",
    queries: list[str],
    entity_types: list[str] | None,
    top_k: int | None,
    named_entity_kinds: list[str] | None,
) -> tuple[bool, str]:
    asked = queries[:MAX_QUERIES]
    raw = await asyncio.gather(*(
        execute_search_entities(state, q, entity_types, top_k, named_entity_kinds, entity_search_offered=True)
        for q in asked
    ), return_exceptions=True)
    answers: list[tuple[bool, str]] = []
    for q, answer in zip(asked, raw):
        if isinstance(answer, Exception):
            # One query failing must not lose the others' results.
            logger.warning("search_entities: query %r failed", q, exc_info=answer)
            answers.append((False, _error("Entity search failed — try again.")))
        elif isinstance(answer, BaseException):
            raise answer
        else:
            answers.append(answer)
    grouped: list[dict[str, Any]] = []
    for q, (ok, text) in zip(asked, answers):
        payload = json.loads(text)
        entry: dict[str, Any] = {"query": q}
        if ok:
            entry.update({k: v for k, v in payload.items() if k != "status"})
        else:
            entry["error"] = payload.get("message", "Entity search failed")
        grouped.append(entry)
    if not any(ok for ok, _ in answers):
        return False, json.dumps({"status": "error", "message": grouped[0].get("error"), "queries": grouped})
    result: dict[str, Any] = {"status": "success", "queries": grouped}
    if len(queries) > MAX_QUERIES:
        result["note"] = f"Only the first {MAX_QUERIES} queries were searched."
    return True, json.dumps(result)


def _searchable_kinds(requested: list[str]) -> tuple[list[str], list[str]]:
    """Kinds the entity vector search can filter on, and a note per kind it
    cannot: people and addresses are never embedded, values are not entities,
    and anything else is not a kind."""
    kinds: list[str] = []
    notes: list[str] = []
    for value in requested:
        try:
            kind = EntityKind(value)
        except ValueError:
            notes.append(f"'{value}' is not a named-entity kind and was ignored.")
            continue
        if kind in VECTOR_KINDS:
            kinds.append(value)
        elif kind in VALUE_KINDS:
            notes.append(f"'{value}' values are found with knowledgegraph__find_records_by_value.")
        else:
            notes.append(
                f"'{value}' entities are not in this search; look them up by name with "
                "knowledgegraph__search_by_entities."
            )
    return kinds, notes


def _render_hits(
    hits: list[EntityHit],
    context: EntityAccessContext,
    shortener: "RecordIdShortener | None",
    *,
    with_kind: bool = False,
) -> tuple[list[dict[str, Any]], list[str]]:
    results: list[dict[str, Any]] = []
    shown_record_ids: list[str] = []
    previews_left = _PREVIEW_ENTITY_COUNT
    for hit in hits:
        item: dict[str, Any] = {
            "entityId": hit.entity_id,
            "entityType": hit.entity_type,
            "name": _trunc(hit.name),
        }
        if with_kind and hit.kind:
            item["kind"] = hit.kind
        apps = sorted({
            context.app_names[r["connectorId"]]
            for r in hit.records
            if r.get("connectorId") in context.app_names
        })
        if apps:
            item["apps"] = apps
        if hit.entity_type == RECORD_ENTITY_TYPE:
            item["entityId"] = _short(hit.entity_id, shortener)
            shown_record_ids.append(hit.entity_id)
        elif previews_left > 0 and hit.records:
            previews_left -= 1
            item["records"] = [_preview_row(r, context, shortener) for r in hit.records]
            if hit.more_records:
                item["moreRecords"] = True
            shown_record_ids.extend(r["_key"] for r in hit.records if r.get("_key"))
        results.append(item)
    return results, shown_record_ids


def _preview_row(
    row: dict[str, Any],
    context: EntityAccessContext,
    shortener: "RecordIdShortener | None",
) -> dict[str, Any]:
    preview: dict[str, Any] = {
        "recordId": _short(row["_key"], shortener),
        "name": _trunc(row.get("recordName") or row["_key"]),
        "type": row.get("recordType"),
    }
    app = context.app_names.get(row.get("connectorId"))
    if app:
        preview["app"] = app
    dated = RecordDates.from_graph(row).listing_date(row.get("updatedAtTimestamp"))
    if dated:
        preview[dated[0]] = _compact_date(dated[1])
    return preview


__all__ = ["execute_search_entities"]
