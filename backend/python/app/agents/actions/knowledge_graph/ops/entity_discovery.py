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
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from app.agents.actions.knowledge_graph.ops.entity_filters import (
    limited_scope_note,
    load_entity_access_context,
    remember_entities,
)
from app.agents.actions.knowledge_graph.views import (
    _compact_date,
    _short,
    _trunc,
    unnamed_sources_note,
)
from app.models.source_dates import RecordDates
from app.modules.agents.context.named_sources import nameable_source_ids
from app.modules.agents.privacy.ledger import note_shown
from app.modules.agents.qna.chat_state import remember_record_ids
from app.modules.named_entities.domain.kinds import (
    VALUE_KINDS,
    VECTOR_KINDS,
    EntityKind,
    kind_family,
)
from app.modules.retrieval.entity_permissions import (
    NAMED_ENTITY_TYPE,
    RECORD_ENTITY_TYPE,
    RECORD_GROUP_ENTITY_TYPE,
    SEARCH_DEADLINE_SECONDS,
    SEARCHABLE_ENTITY_TYPES,
    EntityAccessContext,
    EntityAccessError,
    EntityHit,
    EntitySearchHits,
    EntitySearchTimedOut,
    search_entities_for_user,
)
from app.modules.retrieval.provenance import graph_record_refs, note_retrieved_by
from app.modules.retrieval.title_hits import MIN_TITLE_COSINE, TITLE_SEARCH_TOP_K
from app.utils.chat_helpers import get_record_id_shortener_if_enabled
from app.utils.error_class import innermost_type
from app.utils.not_indexed import not_indexed_note

if TYPE_CHECKING:
    from app.modules.agents.qna.chat_state import ChatState
    from app.modules.transformers.entity_vectorstore import EntityVectorStore
    from app.services.graph_db.interface.graph_db_provider import IGraphDBProvider
    from app.utils.chat_helpers import RecordIdShortener

logger = logging.getLogger(__name__)

NAMED_ENTITIES_DISABLED_MSG = "Named-entity lookup is not enabled."


async def _named_entities_enabled(state: "ChatState") -> bool:
    from app.services.featureflag.platform_settings import (
        is_named_entity_extraction_enabled,
    )

    return await is_named_entity_extraction_enabled(state.get("config_service"))

_MAX_TOP_K = 25
_DEFAULT_TOP_K = 10
_PREVIEW_ENTITY_COUNT = 3
MAX_QUERIES = 5


_ENTITY_KIND_NAMES: frozenset[str] = frozenset(kind.value for kind in EntityKind)


# Told to the agent with results the deadline cut short, so that a match
# missing from them is not read as one the user cannot reach.
INCOMPLETE_NOTE = (
    "Time ran out before every match could be checked against your access, so an entity "
    "you can reach may be missing. Every result listed is one you can access. "
    "Search again for the rest, or use knowledgegraph__search."
)


def _mark_incomplete(payload: dict[str, Any], hits: list[EntityHit], *, others_incomplete: bool = False) -> None:
    # Only ``EntitySearchHits`` reports a cut-short search; any other list is complete.
    if others_incomplete or (isinstance(hits, EntitySearchHits) and hits.incomplete):
        payload["incomplete"] = True
        payload["incompleteNote"] = INCOMPLETE_NOTE


def _other_types_failed_note(query: str, types: list[str], *, compared: bool) -> str:
    if not compared:
        return (
            f"The names returned could not be compared with {query!r}, so entities of other types "
            "were not searched; the entity meant may be of another type. Search again without "
            "entity_types."
        )
    return (
        f"No returned name matched {query!r} among the entity types asked for ({', '.join(types)}), "
        "and entities of other types could not be searched, so the entity meant may be of "
        "another type. Search again without entity_types."
    )


def _other_types_note(query: str, types: list[str]) -> str:
    return (
        f"No returned name matched {query!r} among the entity types asked for ({', '.join(types)}). "
        "These matches are of other types and may be what was meant. Omit entity_types "
        "unless the user named the kind of thing they want."
    )


# Words models put in entity_types that are not entity types, with the type
# that holds them.
_TYPE_HINTS: dict[str, str] = {
    "project": "a Jira project or Confluence space is 'record_group'; an epic or initiative is 'record'",
    "epic": "an epic is a 'record'",
    "issue": "an issue or ticket is a 'record'",
    "ticket": "an issue or ticket is a 'record'",
    "task": "a task is a 'record'",
    "page": "a page, document or file is a 'record'",
    "document": "a page, document or file is a 'record'",
    "file": "a page, document or file is a 'record'",
    "space": "a Confluence space is a 'record_group'",
    "channel": "a channel is a 'record_group'",
    "folder": "a folder is a 'record_group'",
    "team": "a team is a 'department' or a 'record_group'",
}


def _entity_types_note(ignored: list[str]) -> str:
    hints = list(dict.fromkeys(_TYPE_HINTS[t] for t in ignored if t in _TYPE_HINTS))
    return (
        f"Not entity types, so not searched as such: {', '.join(ignored)}."
        + (f" {'; '.join(hints)[:1].upper()}{'; '.join(hints)[1:]}." if hints else "")
        + " Omit entity_types to search every type."
    )


@dataclass(frozen=True)
class _OtherTypes:
    """What the search of other types found, when a type filter matched no name.
    ``incomplete``: its deadline left matches unchecked. ``failed``: it could not run;
    ``compared``: whether the filtered names were compared before it failed."""

    hits: list[EntityHit] = field(default_factory=list)
    incomplete: bool = False
    failed: bool = False
    compared: bool = True


async def _other_type_matches(
    store: "EntityVectorStore",
    graph: "IGraphDBProvider",
    context: EntityAccessContext,
    query: str,
    hits: list[EntityHit],
    *,
    requested_types: list[str],
    top_k: int,
    include_named: bool,
) -> _OtherTypes:
    """Entities of other types whose names match ``query``, when none of ``hits``
    (a search narrowed to some types) does. A model guesses the type of a name
    it has never seen ("SAAS Launch" as a project when it is an epic), and the
    narrowed search then returns its nearest, unrelated entities as if they
    matched: its scores are rank fused, so only the cosine of a name to the
    query says whether it matches. Every entity returned passed the access
    check.

    The search is of the types not asked for, so requested types cannot take its
    candidate slots, and reads ``TITLE_SEARCH_TOP_K`` candidates before names are
    compared, as the record-title lookup does, so a match ranked just past the few
    results shown is still found. It gets the entity search's own budget
    (``SEARCH_DEADLINE_SECONDS``), so it cannot hold the filtered answer back for
    longer than one more search. A deadline or a failure is reported, not read as
    "nothing else matched": the narrowed answer is exactly the one the model would
    otherwise take as complete."""
    try:
        return await asyncio.wait_for(
            _search_other_types(
                store, graph, context, query, hits,
                requested_types=requested_types, top_k=top_k, include_named=include_named,
            ),
            timeout=SEARCH_DEADLINE_SECONDS,
        )
    except TimeoutError:
        return _OtherTypes(incomplete=True)


async def _search_other_types(
    store: "EntityVectorStore",
    graph: "IGraphDBProvider",
    context: EntityAccessContext,
    query: str,
    hits: list[EntityHit],
    *,
    requested_types: list[str],
    top_k: int,
    include_named: bool,
) -> _OtherTypes:
    try:
        cosines = await store.dense_similarities(query, [h.name for h in hits]) if hits else []
    except Exception:
        logger.warning("search_entities: the names returned could not be compared", exc_info=True)
        return _OtherTypes(failed=True, compared=False)
    if any(c >= MIN_TITLE_COSINE for c in cosines):
        return _OtherTypes()
    other_types = sorted(
        SEARCHABLE_ENTITY_TYPES - set(requested_types) - (set() if include_named else {NAMED_ENTITY_TYPE})
    )
    if not other_types:
        return _OtherTypes()
    try:
        wider = await search_entities_for_user(
            store, graph, context, query, entity_types=other_types,
            top_k=max(top_k, TITLE_SEARCH_TOP_K), include_named=include_named,
        )
        shown = {(h.entity_type, h.entity_id) for h in hits}
        candidates = [h for h in wider if (h.entity_type, h.entity_id) not in shown]
        names_cosines = await store.dense_similarities(query, [h.name for h in candidates])
    except EntitySearchTimedOut:
        return _OtherTypes(incomplete=True)
    except Exception:
        logger.warning("search_entities: the search of other entity types failed", exc_info=True)
        return _OtherTypes(failed=True)
    matched = [h for h, c in zip(candidates, names_cosines) if c >= MIN_TITLE_COSINE][:top_k]
    return _OtherTypes(matched, incomplete=isinstance(wider, EntitySearchHits) and wider.incomplete)


def _show(
    state: "ChatState", context: EntityAccessContext, hits: list[EntityHit], *, with_kind: bool,
    count_unnamed: bool = False,
) -> list[dict[str, Any]]:
    """Render ``hits`` for the agent and remember what it was shown, so later
    calls can resolve the entity ids and the record ids it may cite."""
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
    results, shown_record_ids, apps_named_alone = _render_hits(
        hits, context, shortener, with_kind=with_kind, count_unnamed=count_unnamed,
    )
    names = {h.entity_id: h.name for h in hits}
    names.update(
        (r["_key"], r.get("recordName") or "") for h in hits for r in h.records if r.get("_key")
    )
    remember_record_ids(state, shown_record_ids, names=names)
    note_shown(nodes=apps_named_alone)
    shown = set(shown_record_ids)
    note_retrieved_by(state, "entity", graph_record_refs(
        row for hit in hits for row in hit.records if row.get("_key") in shown
    ))
    return results


def _error(message: str, *, retryable: bool = False) -> str:
    """``retryable``: the search could not run (not a bad request), so an API caller is told to try again, not to fix its request."""
    return json.dumps({"status": "error", "message": message, **({"retryable": True} if retryable else {})})


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
        return False, _error("Entity search is not available in this deployment", retryable=True)

    requested = [str(t).strip().lower() for t in (entity_types or []) if str(t).strip()]
    kinds = [str(k).strip().lower() for k in (named_entity_kinds or []) if str(k).strip()]
    # A kind put in entity_types ("organization") was ignored, leaving only the
    # other types searched; it is a named-entity kind. One that is also an
    # entity type ("person") stays one too, so it is searched with extraction off.
    # Beside an explicit named_entity such a type means only the type: it must not narrow named_entity to its kind.
    named_asked = "named_entity" in requested
    kinds = list(dict.fromkeys([
        *kinds, *(t for t in requested if t in _ENTITY_KIND_NAMES and not (named_asked and t in SEARCHABLE_ENTITY_TYPES)),
    ]))
    requested = [t for t in requested if t not in _ENTITY_KIND_NAMES or t in SEARCHABLE_ENTITY_TYPES]
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
        return False, _error(
            f"Unsupported entity_types {requested}; supported: {supported}. {_entity_types_note(requested)}"
        )

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
            kinds=_with_related_kinds(kinds) or None,
            top_k=bounded_top_k,
            include_named=enabled,
        )
    except EntitySearchTimedOut:
        logger.warning("search_entities timed out", exc_info=True)
        return False, _error(
            "Entity search timed out before it could confirm which matches you can access — "
            "this is not an empty result. Try again, or use knowledgegraph__search.",
            retryable=True,
        )
    except EntityAccessError as exc:
        logger.warning(
            "search_entities failed",
            extra={"error_type": type(exc).__name__, "cause_type": innermost_type(exc)},
        )
        return False, _error("Entity search failed — try again.", retryable=True)

    nameable = nameable_source_ids(state)
    hits, unlisted = _listable_hits(hits, nameable)
    ignored = sorted(set(requested) - SEARCHABLE_ENTITY_TYPES)
    others = await _other_type_matches(
        entity_vector_store, graph_provider, context, query, hits,
        requested_types=valid_types, top_k=bounded_top_k,
        # Named entities were asked for only by kinds this search cannot serve: searching every
        # kind now would answer the request the kind note just declined.
        include_named=enabled and not (asked_kinds and not kinds),
    ) if valid_types else _OtherTypes()
    # Matches of other types are listed by the same rule as the hits.
    other_hits, other_unlisted = _listable_hits(others.hits, nameable)
    unlisted += other_unlisted

    payload: dict[str, Any] = {"status": "success"}
    if hits:
        payload["results"] = _show(state, context, hits, with_kind=entity_search_offered, count_unnamed=nameable is not None)
    else:
        payload["results"] = []
        # Entities left out as unnamed sources are not "no match": the note below says so instead.
        if not unlisted:
            # With matches of other types, only the types asked for came up empty.
            payload["message"] = (
                "No accessible entities of the types asked for matched" if other_hits else "No accessible entities matched"
            )
        if note := limited_scope_note(context):
            payload["scopeNote"] = note
    if other_hits:
        payload["otherTypeMatches"] = _show(
            state, context, other_hits, with_kind=entity_search_offered, count_unnamed=nameable is not None,
        )
        payload["otherTypeNote"] = _other_types_note(query, valid_types)
    elif others.failed:
        payload["otherTypeNote"] = _other_types_failed_note(query, valid_types, compared=others.compared)
    if unlisted:
        payload["note"] = unnamed_sources_note(unlisted)
    if ignored:
        payload["ignoredEntityTypes"] = ignored
        payload["entityTypesNote"] = _entity_types_note(ignored)
    if kind_notes:
        payload["kindNotes"] = kind_notes
    _mark_incomplete(payload, hits, others_incomplete=others.incomplete)
    return True, json.dumps(payload)


def _listable_hits(hits: list[EntityHit], nameable: frozenset[str] | None) -> tuple[list[EntityHit], int]:
    """The hits a run may list, and how many it leaves out. A collection is a record group, and a group's hit carries
    its name and id: when the run may name only some sources (`nameable_source_ids`), a group is listed only when it
    is one of them or every record it shows is in one. Any other may be a collection the run doesn't name."""
    if nameable is None:
        return hits, 0
    listed = [
        hit for hit in hits
        if hit.entity_type != RECORD_GROUP_ENTITY_TYPE
        or hit.entity_id in nameable
        or (hit.records and all(r.get("connectorId") in nameable for r in hit.records))
    ]
    return listed, len(hits) - len(listed)


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


def _with_related_kinds(kinds: list[str]) -> list[str]:
    """``kinds`` and the kinds the extractor files the same names under, so a
    product filed as its company is still found."""
    widened: list[str] = []
    for value in kinds:
        try:
            family = kind_family(EntityKind(value))
        except ValueError:
            family = ()
        widened.extend([value, *(kind.value for kind in family if kind in VECTOR_KINDS)])
    return list(dict.fromkeys(widened))


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
    count_unnamed: bool = False,
) -> tuple[list[dict[str, Any]], list[str], list[str]]:
    """The rendered hits, the records they show, and the apps they name for records they don't show: those apps
    reach the model by name alone, so the label must list them. `count_unnamed`: the context names only the run's
    nameable sources, and each hit says in how many other sources its records are (`otherSources`)."""
    results: list[dict[str, Any]] = []
    shown_record_ids: list[str] = []
    # (app, record) for each record whose app an entity lists.
    named: list[tuple[str, str]] = []
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
        unnamed = {r["connectorId"] for r in hit.records if r.get("connectorId") and r["connectorId"] not in context.app_names}
        if count_unnamed and unnamed:
            item["otherSources"] = len(unnamed)
        if hit.entity_type == RECORD_ENTITY_TYPE:
            item["entityId"] = _short(hit.entity_id, shortener)
            if not hit.indexed:
                item["indexingStatus"] = hit.indexing_status
                item["note"] = not_indexed_note(hit.indexing_status)
            shown_record_ids.append(hit.entity_id)
        elif previews_left > 0 and hit.records:
            previews_left -= 1
            item["records"] = [_preview_row(r, context, shortener) for r in hit.records]
            if hit.more_records:
                item["moreRecords"] = True
            shown_record_ids.extend(r["_key"] for r in hit.records if r.get("_key"))
        named.extend((r["connectorId"], r.get("_key")) for r in hit.records if r.get("connectorId") in context.app_names)
        results.append(item)
    shown = set(shown_record_ids)
    return results, shown_record_ids, sorted({app for app, key in named if key not in shown})


def _preview_row(
    row: dict[str, Any],
    context: EntityAccessContext,
    shortener: "RecordIdShortener | None",
) -> dict[str, Any]:
    preview: dict[str, Any] = {
        "recordId": _short(row["_key"], shortener),
        "name": _trunc(row.get("recordName") or row["_key"]),
    }
    # The schema's `type` is a string; a row stored without one leaves it out rather than null.
    if isinstance(row.get("recordType"), str) and row["recordType"]:
        preview["type"] = row["recordType"]
    app = context.app_names.get(row.get("connectorId"))
    if app:
        preview["app"] = app
    dated = RecordDates.from_graph(row).listing_date(row.get("updatedAtTimestamp"))
    if dated:
        preview[dated[0]] = _compact_date(dated[1])
    return preview


__all__ = ["execute_search_entities"]
