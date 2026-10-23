"""The date-filter safety net: matches a search's date bounds left out.

Date bounds on a search match when a record was written or last changed, not
when the event it describes happened. An agent asked about one week bounds its
searches to that week, and a release page, retrospective or weekly summary
written later about that week is filtered out, however well it matches.

So a search with date bounds also runs once without them, with a small limit
(``OUTSIDE_DATES_SEARCH_LIMIT``), and the best few records it finds that fall
outside the bounds (``MAX_OUTSIDE_DATES_RECORDS``) are listed apart from the
results, each with its dates and labelled as written outside the requested
dates. The bounded results are unchanged: the caller still gets exactly what
it asked for, and decides whether a record from outside its dates is about
the period it asked about.

The extra search drops the date bounds and nothing else: the same query, the
same sources, selection and allow-list, the same entity scope and the same
caller's permissions, so it can only name records the bounded search could
have returned without the dates. It runs beside the bounded search.

Bounds on the last-modified date get the same net. They fail the same way: a
page written during a period and edited after it, or an announcement older
than a "changed since" bound, is left out however well it matches.

A timeline (``ops/timeline``) gets the same net: one plain ranked search
without dates beside its searches, never a second timeline, so it adds one
small search to the up to seven a timeline runs. A timeline's span is also
when records were written, so a write-up after the span is left out of it.

``DATE_FILTER_SAFETY_NET`` turns the net off. It is on unless set to
``false``, ``0``, ``no`` or ``off``, and does not follow ``HIGH_ACCURACY_MODE``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, cast

from app.modules.agents.qna.chat_state import remember_record_ids
from app.utils.env_utils import enabled_unless_off

if TYPE_CHECKING:
    import logging
    from collections.abc import Awaitable, Iterable, Mapping

DATE_FILTER_SAFETY_NET_ENV_VAR = "DATE_FILTER_SAFETY_NET"

# Hits asked of the search without dates. Small on purpose: only its best
# few records outside the bounds are named.
OUTSIDE_DATES_SEARCH_LIMIT = 10
MAX_OUTSIDE_DATES_RECORDS = 3
# Passages of each record the search API returns with it.
_PASSAGES_PER_RECORD = 3
# The best passage the assistant sees for each record, on one line.
_PASSAGE_CHARS = 240

# The time-range keys a bound parses to, and the record field each one bounds.
_BOUNDED_FIELDS = (
    ("source_created_after_ms", "source_created_before_ms", "sourceCreatedAtTimestamp"),
    ("source_updated_after_ms", "source_updated_before_ms", "sourceLastModifiedTimestamp"),
)

OUTSIDE_DATES_API_NOTE = (
    "Also matching, but written outside your dates, so the date filter left "
    "them out of searchResults: the best matches of the same search without "
    "dates, each with its dates. A later write-up, such as release notes, a "
    "retrospective or a weekly summary, can still be about your period."
)


def date_safety_net_enabled() -> bool:
    """On unless ``DATE_FILTER_SAFETY_NET`` is ``false``, ``0``, ``no`` or ``off``.

    Unset or blank is on, so a Compose file that passes the variable through
    empty keeps the default.
    """
    return enabled_unless_off(DATE_FILTER_SAFETY_NET_ENV_VAR)


def wants_outside_dates(time_range: dict[str, int] | None) -> bool:
    """Whether a ranked search bounded by ``time_range`` also runs without it."""
    return bool(time_range) and any(
        time_range.get(key) is not None
        for after_key, before_key, _ in _BOUNDED_FIELDS
        for key in (after_key, before_key)
    ) and date_safety_net_enabled()


def _timestamp_ms(value: Any) -> int | None:  # noqa: ANN401
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0:
        return None
    return int(value)


def outside_bounds(record: dict[str, Any], time_range: dict[str, int]) -> bool:
    """Whether the date filter ``time_range`` leaves ``record`` out.

    A record with no date on a bounded field is left out too: the graph
    filter never matches a missing date.
    """
    for after_key, before_key, date_field in _BOUNDED_FIELDS:
        after, before = time_range.get(after_key), time_range.get(before_key)
        if after is None and before is None:
            continue
        value = _timestamp_ms(record.get(date_field))
        if value is None:
            return True
        if (after is not None and value < after) or (before is not None and value > before):
            return True
    return False


@dataclass
class OutsideDatesRecord:
    """A record the search without dates found and the bounds left out."""

    virtual_record_id: str
    record_id: str
    record: dict[str, Any]
    hits: list[dict[str, Any]] = field(default_factory=list[dict[str, Any]])
    """Its hits in the search without dates, best first."""


def _mapping(value: object) -> dict[str, Any] | None:
    """``value`` when it is a JSON object, else None."""
    return cast("dict[str, Any]", value) if isinstance(value, dict) else None


def _hit_virtual_id(hit: dict[str, Any]) -> str | None:
    vrid = hit.get("virtual_record_id") or (_mapping(hit.get("metadata")) or {}).get("virtualRecordId")
    return vrid if isinstance(vrid, str) and vrid else None


def _record_id(record: dict[str, Any]) -> str | None:
    rid = record.get("_key") or record.get("id")
    return rid if isinstance(rid, str) and rid else None


def shown_ids(
    hits: Iterable[Any], records: Mapping[str, Any] | None = None,
) -> tuple[set[str], set[str]]:
    """The virtual record ids and record ids of a result's ``hits`` and of
    its ``records`` map (virtual record id to record), so the net never
    repeats a record the result already shows."""
    virtual_ids: set[str] = set()
    record_ids: set[str] = set()
    for value in hits:
        hit = _mapping(value)
        if hit is None:
            continue
        vrid = _hit_virtual_id(hit)
        if vrid:
            virtual_ids.add(vrid)
        rid = (_mapping(hit.get("metadata")) or {}).get("recordId")
        if isinstance(rid, str) and rid:
            record_ids.add(rid)
    for vrid, value in (records or {}).items():
        record = _mapping(value)
        if record is None:
            continue
        virtual_ids.add(vrid)
        rid = _record_id(record)
        if rid:
            record_ids.add(rid)
    return virtual_ids, record_ids


async def quietly(search: Awaitable[Any], log: logging.Logger) -> Any:  # noqa: ANN401
    """``search``'s result, or None if it raised. The net only adds to a
    search, so its own failure leaves the result as it would have been, and
    a task no one may await never fails with an exception no one reads."""
    try:
        return await search
    except Exception as exc:
        log.warning("The search without dates failed (%s); results are unchanged", type(exc).__name__)
        return None


def pick_outside_dates(
    response: Any,  # noqa: ANN401
    time_range: dict[str, int],
    *,
    shown_virtual_ids: set[str] | frozenset[str] = frozenset(),
    shown_record_ids: set[str] | frozenset[str] = frozenset(),
    limit: int = MAX_OUTSIDE_DATES_RECORDS,
) -> list[OutsideDatesRecord]:
    """The best ``limit`` records of ``response``, the search without dates,
    that ``time_range`` leaves out, in its rank order.

    A record the bounded search already returned (``shown_*``) is skipped, and
    so is one inside the bounds that it ranked too low to return: that one is
    a matter of ranking, not of dates. A failed search names nothing.
    """
    found = _mapping(response)
    if found is None or found.get("status_code", 200) != 200:
        return []
    records = _mapping(found.get("virtual_to_record_map")) or {}
    picked: dict[str, OutsideDatesRecord] = {}
    skipped: set[str] = set()
    for value in cast("list[Any]", found.get("searchResults") or []):
        hit = _mapping(value)
        vrid = _hit_virtual_id(hit) if hit is not None else None
        if hit is None or vrid is None or vrid in skipped:
            continue
        if vrid in picked:
            picked[vrid].hits.append(hit)
            continue
        record = _mapping(records.get(vrid))
        record_id = _record_id(record) if record is not None else None
        if (
            record is None
            or record_id is None
            or len(picked) >= limit
            or vrid in shown_virtual_ids
            or record_id in shown_record_ids
            or not outside_bounds(record, time_range)
        ):
            skipped.add(vrid)
            continue
        picked[vrid] = OutsideDatesRecord(vrid, record_id, record, [hit])
    return list(picked.values())


def _day(ms: int | None) -> str | None:
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime("%Y-%m-%d") if ms else None


def record_dates_label(record: dict[str, Any]) -> str:
    """``created 2026-05-21, last changed 2026-06-02``, or that it has no date."""
    created = _day(_timestamp_ms(record.get("sourceCreatedAtTimestamp")))
    changed = _day(_timestamp_ms(record.get("sourceLastModifiedTimestamp")))
    parts: list[str] = []
    if created:
        parts.append(f"created {created}")
    if changed and changed != created:
        parts.append(f"last changed {changed}")
    return ", ".join(parts) or "no source date"


def _record_name(record: dict[str, Any]) -> str:
    name = record.get("recordName") or record.get("record_name")
    return name if isinstance(name, str) and name.strip() else "(unnamed)"


def _passage(hits: list[dict[str, Any]]) -> str | None:
    """The best hit's text on one line, cut to ``_PASSAGE_CHARS``; None for an image."""
    for hit in hits:
        content = hit.get("content")
        if not isinstance(content, str) or content.startswith("data:image"):
            continue
        text = " ".join(content.split())
        if text:
            return text if len(text) <= _PASSAGE_CHARS else text[: _PASSAGE_CHARS - 1].rstrip() + "…"
    return None


def outside_dates_pointer(count: int) -> str:
    """The line at the top of a result whose net found ``count`` records."""
    noun = "record" if count == 1 else "records"
    verb = "is" if count == 1 else "are"
    return (
        f"Note: {count} matching {noun} written outside your dates {verb} listed "
        "at the end, apart from these results.\n\n"
    )


def render_outside_dates(picked: list[OutsideDatesRecord], fetch_tool: str) -> str:
    """The assistant's list of ``picked``, closing a search result."""
    if not picked:
        return ""
    them = "it" if len(picked) == 1 else "them"
    lines = [
        f"\n\nAlso matching, but written outside your dates, so the date filter left {them} "
        "out of the results above. A later write-up, such as release notes, a retrospective "
        "or a weekly summary, can still be about your period: if one may answer the "
        f"question, read it with `{fetch_tool}` before answering.",
    ]
    for index, item in enumerate(picked, 1):
        lines.append(
            f"{index}. Record ID: {item.record_id} | {_record_name(item.record)} "
            f"| {record_dates_label(item.record)} (outside your dates)"
        )
        passage = _passage(item.hits)
        if passage:
            lines.append(f"   Match: {passage}")
    return "\n".join(lines)


def outside_dates_for_model(state: Any, picked: list[OutsideDatesRecord], fetch_tool: str) -> str:  # noqa: ANN401
    """``render_outside_dates`` for the assistant's result, its record ids
    shortened as the rest of the result's are. The model may now pass them to
    the fetch tool: the search that found them applied the user's access."""
    if not picked:
        return ""
    from app.utils.chat_helpers import get_record_id_shortener_if_enabled

    remember_record_ids(
        state,
        [item.record_id for item in picked],
        names={item.record_id: _record_name(item.record) for item in picked},
    )
    text = render_outside_dates(picked, fetch_tool)
    shortener = get_record_id_shortener_if_enabled(state)
    return shortener.shorten_record_ids_in_text(text) if shortener is not None else text


def _without_id(doc: dict[str, Any]) -> dict[str, Any]:
    """``doc`` without ``id``, a graph's copy of ``_key``, as the gateway sends
    the main results' records and hit metadata."""
    return {key: value for key, value in doc.items() if key != "id"}


def outside_dates_payload(
    picked: list[OutsideDatesRecord], response: dict[str, Any],
) -> dict[str, Any]:
    """The search API's ``outsideDates`` field: ``picked`` as ``searchResults``
    and ``records``, shaped like the response's own, under a note."""
    documents: dict[str, dict[str, Any]] = {}
    for value in cast("list[Any]", response.get("records") or []):
        doc = _mapping(value)
        rid = _record_id(doc) if doc is not None else None
        if doc is not None and rid is not None and rid not in documents:
            documents[rid] = doc
    hits: list[dict[str, Any]] = []
    records: list[dict[str, Any]] = []
    for item in picked:
        for hit in item.hits[:_PASSAGES_PER_RECORD]:
            metadata = _mapping(hit.get("metadata"))
            hits.append({**hit, "metadata": _without_id(metadata)} if metadata is not None else hit)
        records.append(_without_id(documents.get(item.record_id, item.record)))
    return {"note": OUTSIDE_DATES_API_NOTE, "searchResults": hits, "records": records}


__all__ = [
    "DATE_FILTER_SAFETY_NET_ENV_VAR",
    "MAX_OUTSIDE_DATES_RECORDS",
    "OUTSIDE_DATES_API_NOTE",
    "OUTSIDE_DATES_SEARCH_LIMIT",
    "OutsideDatesRecord",
    "date_safety_net_enabled",
    "outside_bounds",
    "outside_dates_for_model",
    "outside_dates_payload",
    "outside_dates_pointer",
    "pick_outside_dates",
    "quietly",
    "record_dates_label",
    "render_outside_dates",
    "shown_ids",
    "wants_outside_dates",
]
