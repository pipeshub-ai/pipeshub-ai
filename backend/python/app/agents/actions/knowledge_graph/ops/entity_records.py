"""``find_records_by_entity`` operation — records connected to one entity
(taxonomy entity, record group, or a record title), limited to what the user
can access, newest first, paged by cursor.

Access is decided by ``app.modules.retrieval.entity_permissions``. An entity
the user cannot reach and an entity with no accessible records get the same
response, so the tool never confirms that inaccessible data exists.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from app.agents.actions.knowledge_graph.listing_citations import cite_listed_records
from app.agents.actions.knowledge_graph.ops.entity_discovery import (
    _named_entities_enabled,
)
from app.agents.actions.knowledge_graph.ops.entity_filters import (
    ENTITY_INDEX_CACHE_KEY,
    limited_scope_note,
    load_entity_access_context,
)
from app.agents.actions.knowledge_graph.views import (
    CITE_NOTE,
    _compact_date,
    _read_hint,
    _short,
    _trunc,
)
from app.models.entities import resolve_weburl
from app.models.source_dates import RecordDates
from app.modules.agents.qna.chat_state import remember_record_ids
from app.modules.named_entities.domain.config import load_named_entity_config
from app.modules.named_entities.linking.candidates import alias_family
from app.modules.retrieval.entity_permissions import (
    RECORD_GROUP_ENTITY_TYPE,
    SEARCH_SCOPE_MAX_ENTITIES,
    SEARCH_SCOPE_MAX_RECORDS,
    SEARCH_SCOPE_MAX_SCAN,
    SEARCHABLE_ENTITY_TYPES,
    EntityAccessContext,
    EntityAccessError,
    EntityRecordPage,
    first_readable_offsets,
    list_accessible_entity_records,
)
from app.modules.retrieval.provenance import graph_record_refs, note_retrieved_by
from app.utils.chat_helpers import (
    get_record_id_shortener_if_enabled,
    resolve_frontend_url,
)

if TYPE_CHECKING:
    from collections.abc import Mapping

    from app.modules.agents.qna.chat_state import ChatState
    from app.utils.chat_helpers import RecordIdShortener

logger = logging.getLogger(__name__)

NAMED_ENTITY_TYPE = "named_entity"

_MAX_LIMIT = 50
_DEFAULT_LIMIT = 20
_MAX_READ_HINT_IDS = 8
NO_ACCESSIBLE_RECORDS_MSG = "No accessible records found for this entity."
NO_FURTHER_RECORDS_MSG = (
    "No further accessible records for this entity - the previous page was the last."
)
LOOKUP_FAILED_MSG = "Lookup failed — try again."
_NARROW_HINT = (
    "Narrow with record_types=[...], or search inside it with "
    "knowledgegraph__search(query=..., entity_ids=[...])."
)
_CAPPED_NOTE = (
    "This entity has more records than one listing can scan; these are the newest "
    "of a sample, and the listing can end before its last record. " + _NARROW_HINT
)


async def execute_find_records_by_entity(
    state: "ChatState",
    entity_id: str | None,
    entity_type: str | None = None,
    record_types: list[str] | None = None,
    limit: int = _DEFAULT_LIMIT,
    cursor: str | None = None,
) -> tuple[bool, str]:
    if not state:
        return False, "Knowledge graph tool state not initialized."
    raw_id = (entity_id or "").strip()
    if not raw_id:
        return False, "entity_id is required."
    if not state.get("graph_provider"):
        return False, "Graph provider not available."

    shortener = get_record_id_shortener_if_enabled(state)
    resolved_id = shortener.resolve(raw_id) if shortener is not None else raw_id
    indexed: dict[str, str] = (state.get(ENTITY_INDEX_CACHE_KEY) or {}).get(resolved_id) or {}
    resolved_type = (entity_type or "").strip().lower() or indexed.get("type")
    supported = ", ".join(sorted(SEARCHABLE_ENTITY_TYPES))
    if not resolved_type:
        return False, (
            "entity_type is required for an entity that search_entities did not return "
            f"in this turn — pass one of: {supported}."
        )
    if resolved_type not in SEARCHABLE_ENTITY_TYPES:
        return False, f"Unsupported entity_type {resolved_type!r} — pass one of: {supported}."
    if resolved_type == "named_entity" and not await _named_entities_enabled(state):
        return False, "Named-entity lookup is not enabled."

    wanted_types = [str(t).strip().upper() for t in (record_types or []) if str(t).strip()] or None
    bounded_limit = min(max(1, limit or _DEFAULT_LIMIT), _MAX_LIMIT)
    try:
        context = await load_entity_access_context(state)
        page = await _first_readable_page(
            state, context, resolved_id, resolved_type, wanted_types, bounded_limit, cursor,
        )
    except ValueError as exc:
        return False, str(exc)
    except EntityAccessError:
        logger.warning("find_records_by_entity failed", exc_info=True)
        return False, LOOKUP_FAILED_MSG

    if not page.records:
        # One reply whatever the entity holds: an unknown id, one whose records are all
        # unreadable and one too large to scan to the end must not be told apart.
        # After a page of results, "none found" reads as "this entity is empty"
        # and contradicts what the caller was just shown; it depends only on the call.
        if cursor:
            return True, NO_FURTHER_RECORDS_MSG
        empty = " ".join(filter(None, (NO_ACCESSIBLE_RECORDS_MSG, limited_scope_note(context))))
        return True, empty + await _alias_note(state, context, resolved_id, resolved_type)

    record_ids = [r["_key"] for r in page.records if r.get("_key")]
    remember_record_ids(
        state, record_ids, names={r["_key"]: r.get("recordName") or "" for r in page.records if r.get("_key")},
    )
    note_retrieved_by(state, "entity", graph_record_refs(page.records))
    name = indexed.get("name") if indexed.get("type") == resolved_type else None
    # Quoted outside the chat too, where "this topic" would point at nothing.
    said = f"{_preposition(resolved_type)} " + (f'{resolved_type} "{_trunc(name)}"' if name else f"a {resolved_type}")
    cite_refs = await cite_listed_records(
        state, state["graph_provider"], context.org_id,
        {r["_key"]: listing_facts(r, said) for r in page.records if r.get("_key")},
    )
    # The first page names the same entity's other nodes, so a count or listing covers them too.
    note = "" if cursor else await _alias_note(state, context, resolved_id, resolved_type)
    return True, note.strip() + ("\n" if note else "") + _render_page(
        page.records,
        context,
        entity_ref=raw_id,
        entity_type=resolved_type,
        entity_name=name,
        next_cursor=page.next_cursor,
        capped=page.capped,
        shortener=shortener,
        cite_refs=cite_refs,
        frontend_url=await resolve_frontend_url(state.get("config_service")),
    )


async def _alias_note(state: "ChatState", context: EntityAccessContext, entity_id: str, entity_type: str) -> str:
    """The same company or product's other nodes, under a learned short or full name,
    with the call that lists each; empty when it has none or alias linking is off."""
    if entity_type != NAMED_ENTITY_TYPE:
        return ""
    try:
        if not (await load_named_entity_config(state.get("config_service"))).link_aliases:
            return ""
        family = await alias_family(state["graph_provider"], context, entity_id)
    except Exception:
        # Only a pointer to more records: the listing stands without it.
        logger.warning("find_records_by_entity: alias lookup failed", exc_info=True)
        return ""
    if not family:
        return ""
    calls = "; ".join(
        f'{_trunc(node.get("name") or "")} ({node.get("kind")}): '
        f'knowledgegraph__find_records_by_entity(entity_id="{node["id"]}", entity_type="{NAMED_ENTITY_TYPE}")'
        for node in family
    )
    return (
        f"\nThe same entity is also stored under a learned alias; list these too for every record "
        f"(and add them to a count): {calls}."
    )


def _continue_call(entity_ref: str, entity_type: str, cursor: str) -> str:
    return (
        "knowledgegraph__find_records_by_entity("
        f'entity_id="{entity_ref}", entity_type="{entity_type}", cursor="{cursor}")'
    )


async def _first_readable_page(
    state: "ChatState",
    context: EntityAccessContext,
    entity_id: str,
    entity_type: str,
    record_types: list[str] | None,
    limit: int,
    cursor: str | None,
) -> EntityRecordPage:
    """The listing's page; when its scan ran out before a readable record, the page
    from the next readable one the probe finds, else that empty page."""
    graph = state["graph_provider"]
    page = await list_accessible_entity_records(
        graph, context, entity_id=entity_id, entity_type=entity_type,
        record_types=record_types, limit=limit, cursor=cursor,
    )
    if page.records or page.next_cursor is None:
        return page
    found = await first_readable_offsets(
        graph, context, [{"id": entity_id, "type": entity_type}],
        record_types=record_types, offset=int(page.next_cursor),
    )
    start = found.get((entity_type, entity_id))
    if start is None:
        return EntityRecordPage(records=[], next_cursor=None)
    return await list_accessible_entity_records(
        graph, context, entity_id=entity_id, entity_type=entity_type,
        record_types=record_types, limit=limit, cursor=str(start),
    )


def _render_page(
    records: list[dict[str, Any]],
    context: EntityAccessContext,
    *,
    entity_ref: str,
    entity_type: str,
    entity_name: str | None,
    next_cursor: str | None,
    capped: bool = False,
    shortener: "RecordIdShortener | None",
    frontend_url: str | None = None,
    cite_refs: "Mapping[str, str] | None" = None,
) -> str:
    cite_refs = cite_refs or {}
    subject = f'{entity_type} "{_trunc(entity_name)}"' if entity_name else f"this {entity_type}"
    order = "newest of a sample" if capped else "newest first"
    lines = [f"Records {_preposition(entity_type)} {subject}, {order} ({len(records)} shown):"]
    lines.extend(cite_note(records, cite_refs))
    lines.extend(
        record_line(record, context, shortener, frontend_url, cite=cite_refs.get(record["_key"])) for record in records
    )
    lines.append("")
    if next_cursor is not None:
        lines.append(f"More records may exist: {_continue_call(entity_ref, entity_type, next_cursor)}")
    if capped:
        lines.append(_CAPPED_NOTE)
    record_ids = [r["_key"] for r in records[:_MAX_READ_HINT_IDS]]
    lines.append(
        f"Next: {_read_hint(record_ids, shortener)}, or knowledgegraph__navigate(node_id=...) "
        "to see where a record sits."
    )
    return "\n".join(lines)


def _preposition(entity_type: str) -> str:
    return "in" if entity_type == RECORD_GROUP_ENTITY_TYPE else "connected to"


def _listing_date(record: dict[str, Any]) -> tuple[str, int] | None:
    return RecordDates.from_graph(record).listing_date(record.get("updatedAtTimestamp"))


def listing_facts(record: dict[str, Any], stated: str) -> str:
    """What an entity listing states about one record, as a citation of it quotes:
    the listing's own words and the date its row shows, never the record's content."""
    dated = _listing_date(record)
    return " · ".join(part for part in (stated, f"{dated[0]} {_compact_date(dated[1])}" if dated else "") if part)


def cite_note(records: list[dict[str, Any]], cite_refs: "Mapping[str, str]") -> list[str]:
    """The line telling the model how to cite a row, when any row can be cited."""
    cited = [cite_refs[r["_key"]] for r in records if r.get("_key") in cite_refs]
    return [CITE_NOTE.format(ref=cited[0])] if cited else []


def record_line(
    record: dict[str, Any],
    context: EntityAccessContext,
    shortener: "RecordIdShortener | None",
    frontend_url: str | None,
    cite: str | None = None,
) -> str:
    """One listed record: type, name, id, its Citation ID when it has one, app, date and url."""
    parts = [
        f"- [{record.get('recordType') or 'RECORD'}] {_trunc(record.get('recordName') or record['_key'])}",
        f"record_id={_short(record['_key'], shortener)}",
    ]
    if cite:
        parts.append(f"cite={cite}")
    app = context.app_names.get(record.get("connectorId"))
    if app:
        parts.append(f"app: {app}")
    dated = _listing_date(record)
    if dated:
        parts.append(f"{dated[0]}: {_compact_date(dated[1])}")
    url = None if record.get("hideWeburl") else resolve_weburl(record.get("webUrl"), frontend_url)
    if url:
        parts.append(f"url={url}")
    return " | ".join(parts)


@dataclass(frozen=True)
class EntitySearchScope:
    """``truncated`` means some connected records were never checked (a cap
    or the scan budget was hit), so an empty or small scope is not the whole
    answer. ``entities_skipped`` counts requested entities past
    ``SEARCH_SCOPE_MAX_ENTITIES`` that did not scope the search at all."""

    virtual_ids: list[str]
    truncated: bool
    entities_skipped: int = 0


async def resolve_entity_virtual_ids(
    state: "ChatState", entities: list[tuple[str, str]],
) -> EntitySearchScope:
    """Deduplicated ``virtualRecordId``s of the accessible records connected to
    each ``(entity_id, entity_type)``, used to scope ``search(entity_ids=[...])``.
    Bounded per call; raises ``EntityAccessError`` on failure so search never
    silently widens to an unscoped query."""
    context = await load_entity_access_context(state)
    graph_provider = state["graph_provider"]
    seen: set[str] = set()
    virtual_ids: list[str] = []
    unique = list(dict.fromkeys(entities))
    entities_skipped = max(0, len(unique) - SEARCH_SCOPE_MAX_ENTITIES)
    truncated = False
    for entity_id, entity_type in unique[:SEARCH_SCOPE_MAX_ENTITIES]:
        remaining = SEARCH_SCOPE_MAX_RECORDS - len(virtual_ids)
        if remaining <= 0:
            truncated = True
            break
        page = await list_accessible_entity_records(
            graph_provider,
            context,
            entity_id=entity_id,
            entity_type=entity_type,
            limit=remaining,
            max_scan=SEARCH_SCOPE_MAX_SCAN,
        )
        truncated = truncated or page.next_cursor is not None or page.capped
        for record in page.records:
            virtual_id = record.get("virtualRecordId")
            if virtual_id and virtual_id not in seen:
                seen.add(virtual_id)
                virtual_ids.append(virtual_id)
    return EntitySearchScope(
        virtual_ids=virtual_ids, truncated=truncated, entities_skipped=entities_skipped,
    )


__all__ = [
    "LOOKUP_FAILED_MSG",
    "NO_ACCESSIBLE_RECORDS_MSG",
    "NO_FURTHER_RECORDS_MSG",
    "execute_find_records_by_entity",
    "EntitySearchScope",
    "record_line",
    "resolve_entity_virtual_ids",
]
