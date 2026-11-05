"""Find accessible records that mention a typed named-entity value, newest
first, paged by cursor, each with the values that matched."""

from __future__ import annotations

import asyncio
import bisect
import logging
import math
import re
import time
from dataclasses import replace
from datetime import date
from typing import TYPE_CHECKING, Any

from app.agents.actions.knowledge_graph.listing_citations import cite_listed_records
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
    cite_note,
    listing_facts,
    record_line,
)
from app.agents.actions.knowledge_graph.ops.entity_search import (
    DEADLINE_SECONDS,
    accepted_caveats,
    build_linker,
    unlinked_line,
)
from app.agents.actions.knowledge_graph.ops.time_range import parse_iso_to_epoch_ms
from app.agents.actions.knowledge_graph.views import _read_hint
from app.exceptions.graph_db_exceptions import PermissionVerificationUnavailableError
from app.models.entities import RecordType
from app.modules.agents.qna.chat_state import remember_record_ids
from app.modules.named_entities.domain.config import load_named_entity_config
from app.modules.named_entities.domain.kinds import VALUE_KINDS, EntityKind
from app.modules.named_entities.linking.mentions import parse_mentions
from app.modules.retrieval.entity_filters import (
    TOO_BROAD_MESSAGE,
    UNKNOWN_KIND_PREFIX,
    EntityFilterHit,
    EntityFilterResolver,
    EntityFilterUnavailableError,
    entity_query_from,
)
from app.modules.retrieval.entity_permissions import (
    LISTING_DEADLINE_SECONDS,
    EntityAccessContext,
    EntityAccessError,
    admitted_node_ids,
    parse_cursor,
    readable_spellings,
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

UNCONVERTED_NOTE = "Amounts in every currency, not converted; pass currency to compare like with like."

_MAX_LIMIT = 50
_DAY_MS = 86_400_000
_DEFAULT_LIMIT = 20
_MAX_READ_HINT_IDS = 8
# Matched values shown per record: enough to tell a subtotal from a line item.
_VALUES_PER_RECORD = 10
# Hits checked per permission query; a page stops at the first readable hit past it.
_CHECK_BATCH = 1000
# Hits one page may check past its first readable one before it hands back a
# cursor to continue from. Before that the walk goes on to the deadline: a cursor
# handed back over unreadable hits alone would say the value is mentioned.
_MAX_SCAN = 5 * _CHECK_BATCH
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
_RECORD_TYPES = frozenset(record_type.value for record_type in RecordType)
_MAX_ENTITY_IDS = 20
_NODE_KINDS = frozenset(kind.value for kind in EntityKind) - {kind.value for kind in VALUE_KINDS}
NOTHING_TO_MATCH_MSG = "Pass entity_ids, a name, a kind, a date, an amount, a quantity, or a percentage."
NAME_TOO_SLOW_MSG = "The name lookup took too long; try again, or pass entity_ids an entity search returned."
NO_SOURCE_MSG = (
    "None of the named sources is available in this conversation; omit source_ids to look in every "
    "source, or pass an app or knowledge base id that list_files or navigate shows."
)


def _percent_hint(low: float | None, high: float | None) -> str:
    """Percentages are fractions; "15 to 20" read as 1,500% to 2,000% matches nothing."""
    if any(bound is not None and abs(bound) > 1 for bound in (low, high)):
        return "Percent bounds are fractions: 0.15 for 15%."
    return ""


async def execute_find_records_by_value(
    state: "ChatState",
    *,
    kinds: list[str] | None = None,
    name: str | None = None,
    entity_ids: list[str] | None = None,
    date_from: str | int | None = None,
    date_to: str | int | None = None,
    amount_min: float | None = None,
    amount_max: float | None = None,
    currency: str | None = None,
    quantity_min: float | None = None,
    quantity_max: float | None = None,
    dimension: str | None = None,
    percent_min: float | None = None,
    percent_max: float | None = None,
    record_types: list[str] | None = None,
    source_ids: list[str] | None = None,
    limit: int = _DEFAULT_LIMIT,
    cursor: str | None = None,
) -> tuple[bool, str]:
    if not state or not state.get("graph_provider"):
        return False, "Knowledge graph tool state not initialized."
    if not await _named_entities_enabled(state):
        return False, NAMED_ENTITIES_DISABLED_MSG
    # A blank currency is none: the filter strips it, so the note must too.
    currency, dimension = (currency or "").strip(), (dimension or "").strip()
    amount_min, amount_max = _range(amount_min, amount_max)
    quantity_min, quantity_max = _range(quantity_min, quantity_max)
    percent_min, percent_max = _range(percent_min, percent_max)
    raw: dict[str, Any] = {}
    # The filter as the model gave it, repeated in the header and the continue call.
    given: dict[str, Any] = {}
    if kinds:
        given["kinds"] = kinds
        # Kinds are matched folded, as the shared filter folds them.
        raw["kinds"] = kinds = [str(kind).strip().casefold() for kind in kinds]
    asked_ids = list(dict.fromkeys(str(item).strip() for item in (entity_ids or []) if str(item).strip()))
    if len(asked_ids) > _MAX_ENTITY_IDS:
        return False, f"Pass at most {_MAX_ENTITY_IDS} entity_ids."
    if asked_ids:
        given["entity_ids"] = asked_ids
    name = (name or "").strip()
    if _given(date_from, date_to):
        start, start_error = _date_bound(date_from, upper=False)
        end, end_error = _date_bound(date_to, upper=True)
        if start_error or end_error:
            return False, start_error or end_error
        if isinstance(date_from, int) and isinstance(date_to, int) and start is not None and end is not None and end <= start:
            # One day asked as a point in epoch ms (date_to = date_from): the 24 hours from it.
            # As a zero-width window it matched only the ranges around the day, never the day itself.
            # Stated as applied, so the header shows the day the window covers.
            date_to = end = start + _DAY_MS
        if start is not None and end is not None and start > end:
            return False, "date_from must be on or before date_to."
        raw["mentionedDate"] = {"from": start, "to": end}
        # An unset end the schema sent as 0 or blank is not repeated.
        given.update(_pair(
            "date_from", date_from if start is not None else None, "date_to", date_to if end is not None else None,
        ))
    if amount_min is not None or amount_max is not None or currency:
        raw["amount"] = {"min": amount_min, "max": amount_max, "currency": currency or ""}
        given.update(_pair("amount_min", amount_min, "amount_max", amount_max, currency=currency))
    if quantity_min is not None or quantity_max is not None or dimension:
        raw["quantity"] = {"min": quantity_min, "max": quantity_max, "dimension": dimension or ""}
        given.update(_pair("quantity_min", quantity_min, "quantity_max", quantity_max, dimension=dimension))
    if percent_min is not None or percent_max is not None:
        raw["percent"] = {"min": percent_min, "max": percent_max}
        given.update(_pair("percent_min", percent_min, "percent_max", percent_max))
    if not raw and not asked_ids and not name:
        return False, NOTHING_TO_MATCH_MSG
    wanted_types = [str(t).strip().upper() for t in (record_types or []) if str(t).strip()]
    unknown_types = sorted(set(wanted_types) - _RECORD_TYPES)
    if unknown_types:
        return False, (
            f"Unknown record type: {', '.join(unknown_types)}. Record types are: {', '.join(sorted(_RECORD_TYPES))}; "
            "to find a kind of document (an invoice, a contract), search for it instead."
        )
    if wanted_types:
        given["record_types"] = wanted_types
    wanted_sources = list(dict.fromkeys(str(s).strip() for s in (source_ids or []) if str(s).strip()))
    if wanted_sources:
        given["source_ids"] = wanted_sources
    bounded_limit = min(max(1, limit or _DEFAULT_LIMIT), _MAX_LIMIT)
    try:
        resume = _parse_resume(cursor)
    except ValueError as exc:
        return False, str(exc)
    org_id = state.get("org_id") or ""
    user_id = state.get("user_id") or ""
    graph = state["graph_provider"]
    shortener = get_record_id_shortener_if_enabled(state)
    ids = [shortener.resolve(item) if shortener is not None else item for item in asked_ids]
    caveats = ""
    try:
        context = await load_entity_access_context(state)
        if name:
            linked, failure, caveats = await _link_name(state, graph, context, name, kinds)
            if failure:
                return False, failure
            ids.extend(linked)
            # Later pages list the same entities without linking the name again.
            given["entity_ids"] = list(dict.fromkeys([*asked_ids, *linked]))
        if ids:
            raw["entityIds"] = list(dict.fromkeys(ids))
            # The ids name their entities: an entity kind would only drop one the linker took
            # under a related kind, or a merge survivor of another kind. Value kinds still apply.
            rest = [kind for kind in raw.pop("kinds", None) or [] if kind not in _NODE_KINDS]
            if rest:
                raw["kinds"] = rest
        resolver = EntityFilterResolver(graph)
        match = await resolver.match(org_id, raw)
        if not match.error and not match.constrained:
            return False, NOTHING_TO_MATCH_MSG
        sources = context.app_ids & set(wanted_sources) if wanted_sources else context.app_ids
        if match.error == TOO_BROAD_MESSAGE:
            # Too broad across the org says nothing yet about what this user may
            # read. List more matches; each page checks only its own window.
            if not sources:
                # As a value nobody mentions answers below.
                return True, NO_SOURCE_MSG if wanted_sources else NO_MATCH_MSG
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
        # Every page matches the filter again: the cursor is a place in the
        # ordering, not a stored result set, so nothing is held between calls.
        if not sources:
            return True, NO_SOURCE_MSG if wanted_sources else NO_MATCH_MSG
        hits = _ordered(match.hits, sources, wanted_types)
        window = entity_query_from(raw) if "mentionedDate" in raw else None
        if window is not None and window.date_start_ms is not None and hits:
            hits = await _date_ranked(graph, org_id, hits, window.date_start_ms, window.date_end_ms)
        start = resume if isinstance(resume, int) else bisect.bisect_right([_sort_key(h) for h in hits], resume)
        page, next_cursor, budget_spent = await _readable_page(
            graph, context, hits, user_id, org_id, start, bounded_limit,
        )
        records = await _details(graph, org_id, page)
    except (EntityAccessError, EntityFilterUnavailableError, PermissionVerificationUnavailableError):
        logger.warning("find_records_by_value failed", exc_info=True)
        return False, LOOKUP_FAILED_MSG
    values = await _matched_values(graph, org_id, raw, records)
    # Unconverted amounts both over- and under-match ("EUR 48,000" is not over 50,000),
    # so the note goes with an empty answer too.
    unconverted = UNCONVERTED_NOTE if "amount" in raw and not currency else ""

    if not records:
        # One reply whatever stopped the walk: any other would say the value is
        # mentioned in records this user cannot read.
        if cursor:
            return True, " ".join(filter(None, (NO_FURTHER_RECORDS_MSG, unconverted)))
        return True, " ".join(filter(None, (
            NO_MATCH_MSG, unconverted, _percent_hint(percent_min, percent_max), limited_scope_note(context),
        )))

    record_ids = [r["_key"] for r in records]
    remember_record_ids(state, record_ids, names={r["_key"]: r.get("recordName") or "" for r in records})
    note_retrieved_by(state, "entity", graph_record_refs(records))
    # The whole filter the record matched (a name too, which the values do not show), then
    # the values extraction read from it: extraction's reading, not the record's words.
    cite_refs = await cite_listed_records(state, graph, org_id, {
        r["_key"]: listing_facts(r, " · ".join(filter(None, (
            f"matched {_args(given)}",
            f"extracted values: {'; '.join(values[r['_key']])}" if values.get(r["_key"]) else "",
        ))))
        for r in records
    })
    frontend_url = await resolve_frontend_url(state.get("config_service"))
    about = await _spelled(graph, org_id, raw.get("entityIds") or [], record_ids)
    order = (
        "records naming a date in the range first, then those whose dates only span it, narrowest first; "
        "newest first within each"
        if "mentionedDate" in raw else "newest first"
    )
    lines = [f"Records {about}matching {_args(given)}, {order} ({len(records)} shown):"]
    if caveats:
        lines.append(f"Note on name: {caveats.strip()}")
    if unconverted:
        lines.append(unconverted)
    lines.extend(cite_note(records, cite_refs))
    for record in records:
        line = record_line(record, context, shortener, frontend_url, cite=cite_refs.get(record["_key"]))
        matched = values.get(record["_key"])
        lines.append(f"{line} | matched: {'; '.join(matched)}" if matched else line)
    lines.append("")
    if next_cursor is not None:
        more = "More records may match" if budget_spent else "More records match"
        lines.append(f"{more}: {_continue_call(given, bounded_limit, next_cursor)}")
    elif cursor:
        lines.append("That was the last page.")
    else:
        lines.append(f"That is all {len(records)}.")
    lines.append(
        f"Next: {_read_hint(record_ids[:_MAX_READ_HINT_IDS], shortener)}, or "
        "knowledgegraph__navigate(node_id=...) to see where a record sits."
    )
    return True, "\n".join(lines)


async def _link_name(
    state: "ChatState", graph: "IGraphDBProvider", context: EntityAccessContext, name: str, kinds: list[str] | None,
) -> tuple[list[str], str | None, str]:
    """The entity ids ``name`` links to, as ``search_by_entities`` links it, or why it links
    to none; and what the accepted ones may leave out (namesakes, longer names not included).
    A name is never a prefix here: "Lum" must not list every Lumora record."""
    hint = [kind for kind in kinds or [] if kind in _NODE_KINDS]
    parsed = parse_mentions([{"name": name, **({"kind": hint[0]} if len(hint) == 1 else {})}])
    if not parsed.mentions:
        return [], " ".join([*parsed.notes, "Pass the entity's name, or entity_ids an entity search returned."]), ""
    try:
        config = await load_named_entity_config(state.get("config_service"))
        linking = await asyncio.wait_for(
            build_linker(state, graph, context, aliases=config.link_aliases).link(
                parsed.mentions, question=str(state.get("query") or ""),
            ),
            timeout=DEADLINE_SECONDS,
        )
    except TimeoutError:
        return [], NAME_TOO_SLOW_MSG, ""
    except EntityAccessError:
        raise
    except Exception:
        logger.warning("find_records_by_value: name lookup failed", exc_info=True)
        return [], LOOKUP_FAILED_MSG, ""
    result = linking.results[0]
    # A cut-short access check is never mentioned, beside an accepted name or not: only
    # candidates not yet found readable keep a check running, so saying so would hint
    # that unreadable ones exist.
    if result.accepted:
        return [candidate.entity_id for candidate in result.accepted], None, accepted_caveats(result, linking.names)
    # The ones it may be, with the ids to pass; each passed the permission check to be offered.
    options = [*result.namesakes, *result.unchecked]
    ids = "; ".join(f'{linking.names.get(c.entity_id, c.name)}: "{c.entity_id}"' for c in options)
    return [], (
        f"name was not used: {unlinked_line(result, linking.names).removeprefix('- ')} "
        + (f"To use one, pass its id as entity_ids: {ids}." if ids else "Or pass entity_ids an entity search returned.")
    ), ""


async def _spelled(graph: "IGraphDBProvider", org_id: str, entity_ids: list[str], record_ids: list[str]) -> str:
    """"mentioning <names> and " for the listed entities, as the listed records spell them;
    an entity none of them spells is not named. Only shown, so a failed lookup shows nothing."""
    if not entity_ids:
        return ""
    try:
        spelled = await readable_spellings(graph, org_id, entity_ids, record_ids)
    except Exception:
        logger.warning("find_records_by_value: entity names lookup failed", exc_info=True)
        return ""
    names = list(dict.fromkeys(spelled[i] for i in entity_ids if spelled.get(i)))
    if not names:
        return ""
    # The ids match a record mentioning any one of them, not all.
    return f"mentioning {names[0]} and " if len(names) == 1 else f"mentioning one of {', '.join(names)} and "


def _range(low: float | None, high: float | None) -> tuple[float | None, float | None]:
    """A range's ends with an unused 0 left out. A strict schema sends an unused end
    as 0, so "above 0.2" arrives as 0.2 to 0: a 0 end is dropped where it would leave
    the range empty or backwards, and kept where it bounds one ("0 to 100" leaves out
    refunds, "-500 to 0" is losses)."""
    if low == 0 and high == 0:
        return None, None
    if low == 0 and high is not None and high < 0:
        low = None
    if high == 0 and low is not None and low > 0:
        high = None
    return low, high


def _given(low: float | None, high: float | None) -> bool:
    """Whether a range was asked for. A strict tool schema makes the model send
    every parameter, so an unused range arrives as 0 to 0, not as absent."""
    return bool(low) or bool(high)


def _pair(low_name: str, low: object, high_name: str, high: object, **extra: object) -> dict[str, Any]:
    """A range's ends, and its unit, as given (an unused end left out)."""
    ends = {low_name: low, high_name: high, **extra}
    return {key: value for key, value in ends.items() if value is not None and value != ""}


_EXTENDED_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}(?:$|[T ])")


def _date_bound(value: str | float | None, *, upper: bool) -> tuple[int | None, str | None]:
    """Epoch ms for a bound the model wrote as an ISO date or date-time; a date-only
    end takes in its whole day. A number (a Python caller, or a call replayed from
    before dates were ISO) is epoch ms. A strict schema sends an unused end as 0 or ""."""
    label = "date_to" if upper else "date_from"
    if not isinstance(value, str):
        if not value:
            return None, None
        if not math.isfinite(value) or _written_as_digits(value):
            return None, f"{label}: {value!r} is not a date. Use 'YYYY-MM-DD' or a full datetime with a timezone."
        return int(value), None
    text = value.strip()
    if not text:
        return None, None
    if not _EXTENDED_DATE.match(text):
        # "20260301" would otherwise read as a date-time with no timezone.
        return None, f"{label}: {text!r} is not a date. Use 'YYYY-MM-DD' or a full datetime with a timezone."
    return parse_iso_to_epoch_ms(text, is_upper_bound=upper, field_label=label)


def _written_as_digits(value: float) -> bool:
    """A year (2026) or a compact date (20260301) sent as a number: as epoch ms either
    is a moment on 1 January 1970, never what was meant. Any other number is epoch ms,
    including a date before 1970."""
    if value != int(value):
        return False
    digits = str(int(value))
    if len(digits) == 4:
        return True
    if len(digits) != 8:
        return False
    try:
        date(int(digits[:4]), int(digits[4:6]), int(digits[6:]))
    except ValueError:
        return False
    return True


def _ordered(
    hits: tuple[EntityFilterHit, ...], sources: set[str] | frozenset[str], record_types: list[str],
) -> list[EntityFilterHit]:
    """Hits in ``sources`` of the wanted types, one per record, newest first.
    The order is what a cursor offsets into, so ties fall back to the record id."""
    unique = {
        hit.record_id: hit
        for hit in hits
        if hit.connector_id in sources and (not record_types or hit.record_type.upper() in record_types)
    }
    return sorted(unique.values(), key=_sort_key)


async def _date_ranked(
    graph: "IGraphDBProvider", org_id: str, hits: list[EntityFilterHit], start_ms: int, end_ms: int,
) -> list[EntityFilterHit]:
    """``hits`` with the records naming a date in the window first, then those whose dates
    only span it, narrowest span first: on a one-day question, the records that name the
    day, then its month, quarter and year, then the year-long contracts in force that day.
    Ranked, not dropped: such a contract still matches. A failed read fails the page
    rather than listing it unranked: a ranked cursor would skip hits in an unranked order."""
    try:
        spans = await graph.get_named_entity_date_spans(org_id, [hit.record_id for hit in hits], start_ms, end_ms)
    except Exception as exc:
        raise EntityFilterUnavailableError("named-entity date spans lookup failed") from exc
    return sorted((replace(hit, rank=_rank(spans.get(hit.record_id))) for hit in hits), key=_sort_key)


# Below every span a covering date can have: a hit whose dates were not read.
_UNRANKED = 10**9


def _rank(found: tuple[bool, int] | None) -> int:
    """0 for a record naming a date in the window; otherwise 1 + the whole days its
    narrowest covering date spans, so a 3-day trip comes before its month."""
    if found is None:
        return _UNRANKED
    named, span = found
    return 0 if named else min(1 + -(-span // _DAY_MS), _UNRANKED - 1)


def _sort_key(hit: EntityFilterHit) -> tuple[int, int, str]:
    return hit.rank, -hit.sort_ts, hit.record_id


def _cursor_of(hit: EntityFilterHit) -> str:
    # The first tier (every listing without a date window) keeps the two-part form;
    # the "r" keeps a later tier's cursor apart from a two-part one whose id holds ":".
    return f"{hit.sort_ts}:{hit.record_id}" if hit.rank == 0 else f"r{hit.rank}:{hit.sort_ts}:{hit.record_id}"


def _parse_resume(cursor: str | None) -> tuple[int, int, str] | int:
    """Where a page starts: after the ``"<sortTs>:<recordId>"`` (first tier) or
    ``"r<rank>:<sortTs>:<recordId>"`` this tool prints, so records changed between pages
    neither repeat nor get skipped by a shifted position; or a plain position, as
    other listings print one."""
    text = (cursor or "").strip()
    parts = text.split(":", 2)
    if len(parts) == 3 and parts[2] and parts[0][:1] == "r" and parts[0][1:].isdigit() and parts[1].lstrip("-").isdigit():
        return int(parts[0][1:]), -int(parts[1]), parts[2]
    head, sep, record_id = text.partition(":")
    if sep and record_id and head.lstrip("-").isdigit():
        return 0, -int(head), record_id
    return parse_cursor(cursor)


async def _readable_page(
    graph: "IGraphDBProvider",
    context: EntityAccessContext,
    hits: list[EntityFilterHit],
    user_id: str,
    org_id: str,
    start: int,
    limit: int,
) -> tuple[list[EntityFilterHit], str | None, bool]:
    """Up to ``limit`` readable hits from position ``start``; the cursor to continue
    after them (``None`` when no readable hit is left); and whether the page
    stopped at ``_MAX_SCAN`` checked hits rather than at a readable one. Only the
    window walked is checked, so a later page never re-checks earlier ones.
    ``filter_accessible_record_ids`` also drops placeholders and internal records,
    which the entity gate alone would not.

    Until a readable hit is found the walk is bounded by the deadline, not by
    ``_MAX_SCAN``, and stopping there returns an empty page with no cursor, as
    a value with no readable hit gets."""
    page: list[EntityFilterHit] = []
    deadline = time.monotonic() + LISTING_DEADLINE_SECONDS
    counted_from = start
    for offset in range(start, len(hits), _CHECK_BATCH):
        if not page and offset > start and time.monotonic() >= deadline:
            logger.info(
                "find_records_by_value: walk stopped at its deadline with no readable hit org=%s checked=%d",
                org_id, offset - start,
            )
            return [], None, False
        batch = hits[offset:offset + _CHECK_BATCH]
        readable = set(await graph.filter_accessible_record_ids(
            [hit.record_id for hit in batch], user_id, org_id,
        ))
        if context.row_scopes:
            readable &= await admitted_node_ids(graph, context, readable)
        for index, hit in enumerate(batch):
            if hit.record_id not in readable:
                continue
            if len(page) == limit:
                return page, _cursor_of(page[-1]), False
            if not page:
                # The budget counts from the first readable hit, not from the start of its batch.
                counted_from = offset + index
            page.append(hit)
        checked = offset + len(batch) - counted_from
        if page and checked >= _MAX_SCAN and offset + len(batch) < len(hits):
            return page, _cursor_of(batch[-1]), True
    return page, None, False


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


async def _matched_values(
    graph: "IGraphDBProvider", org_id: str, raw: dict[str, Any], records: list[dict[str, Any]],
) -> dict[str, list[str]]:
    """The values each listed record matched on; only shown, so a failed lookup
    lists the records without them rather than failing the page."""
    if not records:
        return {}
    try:
        return await graph.get_named_entity_values_in_records(
            org_id, entity_query_from(raw), [r["_key"] for r in records], _VALUES_PER_RECORD,
        )
    except Exception:
        logger.warning("find_records_by_value: matched values lookup failed", exc_info=True)
        return {}


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


def _continue_call(given: dict[str, Any], limit: int, cursor: str) -> str:
    args = [_args(given)]
    if limit != _DEFAULT_LIMIT:
        args.append(f"limit={limit}")
    args.append(f'cursor="{cursor}"')
    return f"knowledgegraph__find_records_by_value({', '.join(args)})"
