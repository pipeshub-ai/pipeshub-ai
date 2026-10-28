"""Find accessible records that mention a typed named-entity value, most
recently modified first, paged by cursor, each with the values that matched."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from app.agents.actions.knowledge_graph.ops.entity_discovery import (
    NAMED_ENTITIES_DISABLED_MSG,
    _named_entities_enabled,
)
from app.agents.actions.knowledge_graph.ops.entity_filters import (
    limited_scope_note,
    load_entity_access_context,
)
from app.agents.actions.knowledge_graph.ops.entity_records import (
    LOOKUP_FAILED_MSG,
    record_line,
)
from app.agents.actions.knowledge_graph.views import _read_hint
from app.exceptions.graph_db_exceptions import PermissionVerificationUnavailableError
from app.modules.agents.qna.chat_state import remember_record_ids
from app.modules.retrieval.entity_filters import (
    TOO_BROAD_MESSAGE,
    UNKNOWN_KIND_PREFIX,
    EntityFilterHit,
    EntityFilterResolver,
    EntityFilterUnavailableError,
    entity_query_from,
)
from app.modules.retrieval.entity_permissions import (
    EntityAccessContext,
    EntityAccessError,
    _parse_cursor,
    admitted_node_ids,
)
from app.modules.retrieval.provenance import graph_record_refs, note_retrieved_by
from app.utils.chat_helpers import (
    get_record_id_shortener_if_enabled,
    resolve_frontend_url,
)

if TYPE_CHECKING:
    from app.modules.agents.qna.chat_state import ChatState
    from app.services.graph_db.interface.graph_db_provider import IGraphDBProvider

logger = logging.getLogger(__name__)

_MAX_LIMIT = 50
_DEFAULT_LIMIT = 20
_MAX_READ_HINT_IDS = 8
# Matched values shown per record: enough to tell a subtotal from a line item.
_VALUES_PER_RECORD = 10
# Hits checked per permission query; a page stops at the first readable hit past it.
_CHECK_BATCH = 1000
# Matches listed to check for permission when the first lookup is too broad.
# Only a filter matching more falls back to counting within everything the user
# can read, which loads that whole set.
_SCAN_LIMIT = 20_000
TOO_MANY_RECORDS_MSG = (
    f"Too many readable records match to list (over {_SCAN_LIMIT:,}); narrow the filter "
    "with a tighter range or a name."
)
NO_MATCH_MSG = "No accessible records matched."
NO_FURTHER_RECORDS_MSG = "No further readable records match - the previous page was the last."
_RECORD_TYPES_HINT = " To keep only some records, pass record_types=[...] (FILE, MAIL, TICKET, ...) or use search."


async def execute_find_records_by_value(
    state: "ChatState",
    *,
    kinds: list[str] | None = None,
    name: str | None = None,
    date_from: int | None = None,
    date_to: int | None = None,
    amount_min: float | None = None,
    amount_max: float | None = None,
    currency: str | None = None,
    quantity_min: float | None = None,
    quantity_max: float | None = None,
    dimension: str | None = None,
    percent_min: float | None = None,
    percent_max: float | None = None,
    record_types: list[str] | None = None,
    limit: int = _DEFAULT_LIMIT,
    cursor: str | None = None,
) -> tuple[bool, str]:
    if not state or not state.get("graph_provider"):
        return False, "Knowledge graph tool state not initialized."
    if not await _named_entities_enabled(state):
        return False, NAMED_ENTITIES_DISABLED_MSG
    raw: dict[str, Any] = {}
    # The filter as the model gave it, repeated in the header and the continue call.
    given: dict[str, Any] = {}
    if kinds:
        raw["kinds"] = given["kinds"] = kinds
    if name:
        raw["name"] = given["name"] = name
    if _given(date_from, date_to):
        raw["mentionedDate"] = {"from": date_from or None, "to": date_to or None}
        given.update(_pair("date_from", date_from, "date_to", date_to))
    if _given(amount_min, amount_max) or currency:
        raw["amount"] = {"min": amount_min, "max": amount_max, "currency": currency or ""}
        given.update(_pair("amount_min", amount_min, "amount_max", amount_max, currency=currency))
    if _given(quantity_min, quantity_max) or dimension:
        raw["quantity"] = {"min": quantity_min, "max": quantity_max, "dimension": dimension or ""}
        given.update(_pair("quantity_min", quantity_min, "quantity_max", quantity_max, dimension=dimension))
    if _given(percent_min, percent_max):
        raw["percent"] = {"min": percent_min, "max": percent_max}
        given.update(_pair("percent_min", percent_min, "percent_max", percent_max))
    if not raw:
        return False, "Pass a kind, name, date, amount, quantity, or percentage."
    wanted_types = [str(t).strip().upper() for t in (record_types or []) if str(t).strip()]
    if wanted_types:
        given["record_types"] = wanted_types
    bounded_limit = min(max(1, limit or _DEFAULT_LIMIT), _MAX_LIMIT)
    try:
        offset = _parse_cursor(cursor)
    except ValueError as exc:
        return False, str(exc)
    org_id = state.get("org_id") or ""
    user_id = state.get("user_id") or ""
    graph = state["graph_provider"]
    try:
        resolver = EntityFilterResolver(graph)
        match = await resolver.match(org_id, raw)
        if not match.error and not match.constrained:
            return False, "Pass a kind, name, date, amount, quantity, or percentage."
        context = await load_entity_access_context(state)
        if match.error == TOO_BROAD_MESSAGE:
            # Too broad across the org says nothing yet about what this user may
            # read. List more matches; each page checks only its own window.
            if not context.app_ids:
                return True, NO_MATCH_MSG
            match = await resolver.match(org_id, raw, limit=_SCAN_LIMIT)
            if match.error == TOO_BROAD_MESSAGE:
                # Too many matches to list: count within the user's readable records,
                # so "too many" is never decided by records they cannot read.
                # shortcut: counted over the apps the turn touches, not only the
                # selection inside them (the readable-record query has no
                # selection filter); rows are still gated by the page walk below.
                readable = await graph.get_accessible_virtual_record_ids(
                    user_id, org_id, {"apps": sorted(context.app_ids)}, raise_on_error=True,
                )
                match = await resolver.match(
                    org_id, raw, within=list(dict.fromkeys(readable.values())), limit=_SCAN_LIMIT,
                )
                if match.error == TOO_BROAD_MESSAGE:
                    return False, TOO_MANY_RECORDS_MSG
        if match.error:
            hint = _RECORD_TYPES_HINT if match.error.startswith(UNKNOWN_KIND_PREFIX) else ""
            return False, match.error + hint
        hits = _ordered(match.hits, context, wanted_types)
        page, next_offset = await _readable_page(graph, context, hits, user_id, org_id, offset, bounded_limit)
        records = await _details(graph, org_id, page)
        values = await graph.get_named_entity_values_in_records(
            org_id, entity_query_from(raw), [r["_key"] for r in records], _VALUES_PER_RECORD,
        ) if records else {}
    except (EntityAccessError, EntityFilterUnavailableError, PermissionVerificationUnavailableError):
        logger.warning("find_records_by_value failed", exc_info=True)
        return False, LOOKUP_FAILED_MSG

    if not records:
        if offset:
            return True, NO_FURTHER_RECORDS_MSG
        return True, " ".join(filter(None, (NO_MATCH_MSG, limited_scope_note(context))))

    record_ids = [r["_key"] for r in records]
    remember_record_ids(state, record_ids, names={r["_key"]: r.get("recordName") or "" for r in records})
    note_retrieved_by(state, "entity", graph_record_refs(records))
    shortener = get_record_id_shortener_if_enabled(state)
    frontend_url = await resolve_frontend_url(state.get("config_service"))
    lines = [f"Records matching {_args(given)}, most recently modified first ({len(records)} shown):"]
    for record in records:
        line = record_line(record, context, shortener, frontend_url)
        matched = values.get(record["_key"])
        lines.append(f"{line} | matched: {'; '.join(matched)}" if matched else line)
    lines.append("")
    if next_offset is not None:
        lines.append(f"More records match: {_continue_call(given, bounded_limit, next_offset)}")
    elif offset:
        lines.append("That was the last page.")
    else:
        lines.append(f"That is all {len(records)}.")
    lines.append(
        f"Next: {_read_hint(record_ids[:_MAX_READ_HINT_IDS], shortener)}, or "
        "knowledgegraph__navigate(node_id=...) to see where a record sits."
    )
    return True, "\n".join(lines)


def _given(low: float | None, high: float | None) -> bool:
    """Whether a range was asked for. A strict tool schema makes the model send
    every parameter, so an unused range arrives as 0 to 0, not as absent."""
    return bool(low) or bool(high)


def _pair(low_name: str, low: object, high_name: str, high: object, **extra: object) -> dict[str, Any]:
    """A range's ends, and its unit, as given (an unused end left out)."""
    ends = {low_name: low, high_name: high, **extra}
    return {key: value for key, value in ends.items() if value is not None and value != ""}


def _ordered(
    hits: tuple[EntityFilterHit, ...], context: EntityAccessContext, record_types: list[str],
) -> list[EntityFilterHit]:
    """In-scope hits of the wanted types, one per record, most recently modified first.
    The order is what a cursor offsets into, so ties fall back to the record id."""
    unique = {
        hit.record_id: hit
        for hit in hits
        if hit.connector_id in context.app_ids and (not record_types or hit.record_type.upper() in record_types)
    }
    return sorted(unique.values(), key=lambda hit: (-hit.sort_ts, hit.record_id))


async def _readable_page(
    graph: "IGraphDBProvider",
    context: EntityAccessContext,
    hits: list[EntityFilterHit],
    user_id: str,
    org_id: str,
    offset: int,
    limit: int,
) -> tuple[list[EntityFilterHit], int | None]:
    """Up to ``limit`` readable hits from position ``offset``, and the position of
    the next readable one (``None`` when there is none). Only the window walked is
    checked, so a later page never re-checks earlier ones. ``filter_accessible_record_ids``
    also drops placeholders and internal records, which the entity gate alone would not."""
    page: list[EntityFilterHit] = []
    for start in range(offset, len(hits), _CHECK_BATCH):
        batch = hits[start:start + _CHECK_BATCH]
        readable = await graph.filter_accessible_record_ids(
            [hit.record_id for hit in batch], user_id, org_id,
        )
        if context.row_scopes:
            readable = set(readable) & await admitted_node_ids(graph, context, readable)
        for position, hit in enumerate(batch, start):
            if hit.record_id not in readable:
                continue
            if len(page) == limit:
                return page, position
            page.append(hit)
    return page, None


async def _details(graph: "IGraphDBProvider", org_id: str, page: list[EntityFilterHit]) -> list[dict[str, Any]]:
    """The page's record documents in page order; one deleted since it was matched is dropped."""
    if not page:
        return []
    rows = await graph.get_records_by_record_ids([hit.record_id for hit in page], org_id)
    if not rows:
        # The fetch reports a failure as no rows; a whole page vanishing is that, not "no more records".
        raise EntityAccessError("record details lookup failed")
    by_id = {str(row.get("_key") or row.get("id")): row for row in rows}
    return [{**by_id[hit.record_id], "_key": hit.record_id} for hit in page if hit.record_id in by_id]


def _args(given: dict[str, Any]) -> str:
    return ", ".join(f"{key}={_literal(value)}" for key, value in given.items())


def _literal(value: object) -> str:
    if isinstance(value, str):
        return f'"{value}"'
    if isinstance(value, list):
        return "[" + ", ".join(_literal(item) for item in value) + "]"
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


def _continue_call(given: dict[str, Any], limit: int, offset: int) -> str:
    args = [_args(given)]
    if limit != _DEFAULT_LIMIT:
        args.append(f"limit={limit}")
    args.append(f'cursor="{offset}"')
    return f"knowledgegraph__find_records_by_value({', '.join(args)})"
