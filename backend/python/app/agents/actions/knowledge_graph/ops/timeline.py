"""knowledgegraph__search_timeline: what happened over a span of time, in date order.

A summary of a period ("what came up between March and May", "how did the
rollout go") needs material from every part of that period. One search returns
the top matches overall, and those bunch around the single best-matching
moment, so the answer covers that moment and misses the rest of the span.

This tool splits the span into at most ``MAX_PERIODS`` periods and runs the
same semantic search once per period, with that period as a hard date filter,
beside one plain search over the whole span. Each hit of the whole-span search
is filed under the period its record is dated in (or, with no source date, in
a bucket of its own), so the periods together hold at least what a plain search
finds, plus what it ranked too low to return. Without a start, the span reaches
back ``DEFAULT_LOOKBACK_DAYS`` from the end, or further to the earliest record
the whole-span search found; it is never narrowed to where the best matches
cluster.

Each period's best blocks are shown under its own heading, oldest period
first, within one search result's size (``DEFAULT_MAX_RESULT_CHARS``), so a
timeline costs about as much prompt as one search however long the span. Every
period with matches gets a third of the budget split evenly; the rest goes to
the periods where the whole-span search found the most.

Keyword (grep) matching is left out: it scans every stored record file, and
running it once per period would multiply the slowest step of a search.

The search API's ``timeline`` option runs the same search for API callers,
such as an agent over MCP (``timeline_search_response``).

The deployment switch ``TIMELINE_SUMMARIES`` offers the tool and the API
option; it is on unless set to ``false``, and does not follow
``HIGH_ACCURACY_MODE``.
"""

from __future__ import annotations

import asyncio
import calendar
import json
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Any

from app.agent_loop_lib.hooks.middleware.builtin.budget_reduction import (
    DEFAULT_MAX_RESULT_CHARS,
)
from app.agents.actions.knowledge_graph.ops.entity_filters import merge_filter_groups
from app.agents.actions.knowledge_graph.ops.entity_records import (
    resolve_entity_virtual_ids,
)
from app.agents.actions.knowledge_graph.ops.fetch import FETCH_RECORD_TOOL_NAME
from app.agents.actions.knowledge_graph.ops.results import (
    _RECORD_NAME_RE,
    dedupe_append_final_results,
    result_text,
    search_result_summary,
    tool_output,
)
from app.agents.actions.knowledge_graph.ops.scope import KnowledgeScope, _clean_kb
from app.agents.actions.knowledge_graph.ops.search import (
    ENTITY_SCOPE_INCOMPLETE_MESSAGE,
    UNKNOWN_ENTITY_IDS_MESSAGE,
    normalize_source_ids,
    resolve_entity_filter_groups,
    resolve_record_scoped_entities,
    unresolved_entity_ids,
)
from app.agents.actions.knowledge_graph.ops.time_range import parse_time_range
from app.agents.actions.util.tool_summaries import bullet_list
from app.modules.agents.qna.chat_state import (
    remember_record_ids,
    remember_shown_results,
)
from app.modules.retrieval.entity_permissions import EntityAccessError
from app.modules.retrieval.provenance import note_retrieved_by
from app.services.graph_db.interface.graph_db_provider import STRICT_SCOPE_FILTER_KEY
from app.utils.env_utils import enabled_unless_off

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from app.agent_loop_lib.core.messages import Part
    from app.agent_loop_lib.core.types import ToolResult
    from app.agent_loop_lib.tools.base import Tool
    from app.modules.agents.qna.chat_state import ChatState

logger = logging.getLogger(__name__)

TIMELINE_TOOL_NAME = "knowledgegraph__search_timeline"
TIMELINE_SUMMARIES_ENV_VAR = "TIMELINE_SUMMARIES"

MAX_PERIODS = 6
# Search hits asked for per period, and the units kept from them before the
# size cut. Small on purpose: each period shows a few compact extracts.
PERIOD_SEARCH_LIMIT = 10
MAX_UNITS_PER_PERIOD = 10
# Hits of the plain search over the whole span, which also finds the span when
# the caller gives no dates.
SPAN_SEARCH_LIMIT = 50
TIMELINE_RESULT_CHARS = DEFAULT_MAX_RESULT_CHARS

# The statuses knowledgegraph__search treats as a search that failed rather
# than one that found nothing (404 is "no accessible records": empty).
_FAILED_SEARCH_STATUS_CODES = frozenset({202, 422, 500, 503})

_DATE_FIELDS = {
    "created": ("created_after", "created_before", "source_created_after_ms",
                "source_created_before_ms", "sourceCreatedAtTimestamp"),
    "modified": ("modified_after", "modified_before", "source_updated_after_ms",
                 "source_updated_before_ms", "sourceLastModifiedTimestamp"),
}
# A span this short is cut into weeks rather than months.
_WEEKLY_SPAN_DAYS = 31
# Without a start, how far back from the end a timeline reaches at least.
DEFAULT_LOOKBACK_DAYS = 365
_DAY_MS = 24 * 60 * 60 * 1000


def timeline_summaries_enabled() -> bool:
    """On unless ``TIMELINE_SUMMARIES`` is ``false``, ``0``, ``no`` or ``off``.

    Unset or blank is on, so a Compose file that passes the variable through
    empty keeps the default.
    """
    return enabled_unless_off(TIMELINE_SUMMARIES_ENV_VAR)


def without_timeline_unless_enabled(tools: list[Tool]) -> list[Tool]:
    """The request's tools, minus the timeline tool while the switch is off."""
    if timeline_summaries_enabled():
        return tools
    return [tool for tool in tools if tool.name != TIMELINE_TOOL_NAME]


@dataclass(frozen=True)
class Period:
    start_ms: int
    end_ms: int
    """Inclusive."""
    label: str


def _utc(ms: int) -> datetime:
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc)


def _ms(dt: datetime) -> int:
    return int(dt.timestamp() * 1000)


def _now_ms() -> int:
    return _ms(datetime.now(timezone.utc))


def _day(ms: int) -> str:
    return _utc(ms).strftime("%Y-%m-%d")


def _range_label(start_ms: int, end_ms: int) -> str:
    start, end = _day(start_ms), _day(end_ms)
    return start if start == end else f"{start} to {end}"


def _month_bounds(year: int, month: int) -> tuple[int, int]:
    first = datetime(year, month, 1, tzinfo=timezone.utc)
    last = first.replace(day=calendar.monthrange(year, month)[1], hour=23, minute=59, second=59,
                         microsecond=999_000)
    return _ms(first), _ms(last)


def plan_periods(start_ms: int, end_ms: int, *, max_periods: int = MAX_PERIODS) -> list[Period]:
    """Split ``[start_ms, end_ms]`` into at most ``max_periods`` periods.

    A span of up to a month is cut into weeks of whole days; a longer one into
    calendar months, consecutive months grouped evenly when there are more than
    ``max_periods``. The first and last periods are clipped to the span.
    """
    if end_ms < start_ms:
        start_ms, end_ms = end_ms, start_ms
    start, end = _utc(start_ms), _utc(end_ms)
    first_day = start.replace(hour=0, minute=0, second=0, microsecond=0)
    days = (end.date() - start.date()).days + 1
    if days <= _WEEKLY_SPAN_DAYS:
        count = max(1, min(max_periods, -(-days // 7)))
        size, extra = divmod(days, count)
        periods: list[Period] = []
        offset = 0
        for index in range(count):
            length = size + (1 if index < extra else 0)
            lo = max(_ms(first_day + timedelta(days=offset)), start_ms)
            hi = min(_ms(first_day + timedelta(days=offset + length)) - 1, end_ms)
            periods.append(Period(lo, hi, _range_label(lo, hi)))
            offset += length
        return periods

    months: list[tuple[int, int]] = []
    year, month = start.year, start.month
    while (year, month) <= (end.year, end.month):
        months.append((year, month))
        year, month = (year + 1, 1) if month == 12 else (year, month + 1)

    groups: list[list[tuple[int, int]]] = []
    size, extra = divmod(len(months), max_periods) if len(months) > max_periods else (1, 0)
    index = 0
    while index < len(months):
        take = size + (1 if len(groups) < extra else 0)
        groups.append(months[index:index + take])
        index += take

    periods = []
    for group in groups:
        lo = max(_month_bounds(*group[0])[0], start_ms)
        hi = min(_month_bounds(*group[-1])[1], end_ms)
        label = _range_label(lo, hi)
        if len(group) == 1:
            label = f"{_utc(lo).strftime('%B %Y')} ({label})"
        periods.append(Period(lo, hi, label))
    return periods


def record_date_ms(record: Any, date_field: str) -> int | None:  # noqa: ANN401
    """The source timestamp a timeline places ``record`` by, in epoch ms."""
    if not isinstance(record, dict):
        return None
    value = record.get(_DATE_FIELDS[date_field][4])
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0:
        return None
    return int(value)


def span_of(records: dict[str, Any], date_field: str) -> tuple[int, int] | None:
    dates = [d for record in records.values() if (d := record_date_ms(record, date_field)) is not None]
    return (min(dates), max(dates)) if dates else None


def _json(status: str, message: str) -> str:
    payload: dict[str, Any] = {"status": status, "message": message}
    if status == "success":
        payload.update({"results": [], "result_count": 0})
    return json.dumps(payload)


@dataclass
class _Scope:
    filter_groups: dict[str, list[str]]
    entity_filter_groups: dict[str, list[str]]
    """Department/category/topic/language filters, kept apart so an empty
    timeline can retry without them, as knowledgegraph__search does."""
    controls: dict[str, Any]
    """``strictScope`` and the request's ``entityFilters``: set on the merged
    groups, never merged, since they are not lists of names."""
    virtual_record_ids: list[str] | None
    notes: list[str]

    def groups(self, *, with_entities: bool) -> dict[str, Any]:
        merged: dict[str, Any] = merge_filter_groups(
            self.filter_groups, self.entity_filter_groups if with_entities else None,
        )
        merged.update(self.controls)
        return merged


async def _resolve_scope(
    state: ChatState, source_ids: list[str] | None, entity_ids: list[str] | None,
) -> _Scope | str:
    """The same scope knowledgegraph__search would search, or an error result.

    One retrieval call per period covers every named source together; there is
    no per-source fan-out.
    """
    agent_filters = state.get("filters", {}) or {}
    if state.get("is_placeholder_agent"):
        raw_apps: list[str] = list(state.get("apps") or [])
        raw_kbs: list[str] = list(state.get("kb") or [])
    else:
        raw_apps = list(agent_filters.get("apps") or [])
        raw_kbs = list(agent_filters.get("kb") or [])
    scope = KnowledgeScope(app_ids=tuple(raw_apps), kb_ids=_clean_kb(raw_kbs))
    scope = scope.narrow_to(normalize_source_ids(source_ids))

    requested = [e for e in dict.fromkeys(entity_ids or []) if e]
    unknown = unresolved_entity_ids(state, requested)
    if requested and len(unknown) == len(requested):
        return _json("error", UNKNOWN_ENTITY_IDS_MESSAGE.format(ids=unknown))

    controls: dict[str, Any] = {}
    if agent_filters.get(STRICT_SCOPE_FILTER_KEY):
        controls[STRICT_SCOPE_FILTER_KEY] = True
    if agent_filters.get("entityFilters"):
        controls["entityFilters"] = agent_filters["entityFilters"]

    notes: list[str] = []
    if unknown:
        notes.append(
            f"Note: entity_ids {unknown} were not returned by search_entities in this "
            "turn and were ignored."
        )
    virtual_record_ids: list[str] | None = None
    record_scoped = resolve_record_scoped_entities(state, entity_ids)
    if record_scoped:
        try:
            entity_scope = await resolve_entity_virtual_ids(state, record_scoped)
        except EntityAccessError:
            logger.warning("knowledgegraph__search_timeline: entity scoping failed", exc_info=True)
            return _json("error", "Could not scope the timeline to the requested entities — try again.")
        if not entity_scope.virtual_ids:
            incomplete = entity_scope.truncated or entity_scope.entities_skipped
            return _json(
                "success",
                ENTITY_SCOPE_INCOMPLETE_MESSAGE if incomplete
                else "No accessible records found for the requested entities.",
            )
        virtual_record_ids = entity_scope.virtual_ids
        if entity_scope.truncated or entity_scope.entities_skipped:
            notes.append(
                "Note: the requested record group has more records than one search can "
                "cover, so only part of it was searched."
            )
    return _Scope(
        filter_groups=scope.to_filter_groups(),
        entity_filter_groups=resolve_entity_filter_groups(state, entity_ids),
        controls=controls,
        virtual_record_ids=virtual_record_ids,
        notes=notes,
    )


@dataclass
class _PeriodHits:
    period: Period | None
    """None for the bucket of matches whose record has no source date."""
    results: list[dict[str, Any]]
    records: dict[str, Any]
    failed: bool = False
    incomplete: str | None = None
    """The retrieval's notice when this period's search reached only part of
    the index: an empty period then does not show nothing exists."""
    weight: int = 0
    """Records of the whole-span search filed here: where the matches are
    densest, the period gets more of the result."""

    @property
    def label(self) -> str:
        return self.period.label if self.period is not None else "No source date"


def _hit_key(hit: dict[str, Any]) -> tuple[Any, Any, Any]:
    metadata = hit.get("metadata") or {}
    vrid = hit.get("virtual_record_id") or metadata.get("virtualRecordId")
    block = hit.get("block_index", metadata.get("blockNum"))
    return vrid, block, hit.get("content") if block is None else None


def _add_whole_span_hits(
    hits: list[_PeriodHits], undated: _PeriodHits, whole: dict[str, Any], date_field: str,
) -> None:
    """File each hit of the whole-span search under the period its record is
    dated in, beside that period's own hits, or under ``undated``.

    The whole-span search is a plain search over the span, so the buckets
    together always hold at least what that search found; the per-period
    searches add what it ranked too low to return.
    """
    records = whole.get("virtual_to_record_map") or {}
    buckets = [*hits, undated]
    seen = [{_hit_key(hit) for hit in bucket.results} for bucket in buckets]
    placed: list[set[str]] = [set() for _ in buckets]
    for hit in whole.get("searchResults") or []:
        key = _hit_key(hit)
        date = record_date_ms(records.get(key[0]), date_field)
        index = len(hits)
        if date is not None:
            index = next(
                (i for i, h in enumerate(hits) if h.period.start_ms <= date <= h.period.end_ms), -1,
            )
            if index < 0:
                continue
        if key not in seen[index]:
            seen[index].add(key)
            buckets[index].results.append(hit)
        if key[0] in records:
            buckets[index].records.setdefault(key[0], records[key[0]])
        placed[index].add(key[0])
    for bucket, vrids in zip(buckets, placed):
        bucket.weight = len(vrids)


def _failed(result: Any, log: logging.Logger) -> bool:  # noqa: ANN401
    if isinstance(result, BaseException):
        log.warning("knowledgegraph__search_timeline: a search failed: %s", result)
        return True
    return result is None or result.get("status_code", 200) in _FAILED_SEARCH_STATUS_CODES


@dataclass
class _Collected:
    hits: list[_PeriodHits]
    """The periods, oldest first, then the undated bucket when it has matches."""
    start_ms: int
    end_ms: int


async def _collect(
    search: Any,  # noqa: ANN401
    start_ms: int | None,
    end_ms: int | None,
    date_field: str,
    log: logging.Logger,
    *,
    period_limit: int = PERIOD_SEARCH_LIMIT,
) -> _Collected | str:
    """Run the whole-span and per-period searches and file what they found.

    Without a start, the span is the ``DEFAULT_LOOKBACK_DAYS`` before the end,
    reaching further back to the earliest record the whole-span search found.
    It is never narrowed to where the best matches are: they bunch around one
    moment, which is what a timeline has to get past. With no end either, that
    search has no date filter, so a match whose record has no source date is
    still found (and listed apart). ``period_limit`` is the hits asked for in
    each period.
    """
    after_key, before_key = _DATE_FIELDS[date_field][2:4]

    def period_searches(periods: list[Period]) -> list[Any]:
        return [search({after_key: p.start_ms, before_key: p.end_ms}, period_limit) for p in periods]

    if start_ms is None:
        whole: Any
        try:
            whole = await search({before_key: end_ms} if end_ms is not None else None, SPAN_SEARCH_LIMIT)
        except Exception as exc:
            # Like a failed search in the gather below: the periods still run.
            whole = exc
        whole_failed = _failed(whole, log)
        end_ms = end_ms if end_ms is not None else _now_ms()
        start_ms = end_ms - DEFAULT_LOOKBACK_DAYS * _DAY_MS
        span = None if whole_failed else span_of(whole.get("virtual_to_record_map") or {}, date_field)
        if span is not None:
            start_ms = min(start_ms, span[0])
        periods = plan_periods(start_ms, end_ms)
        per_period = await asyncio.gather(*period_searches(periods), return_exceptions=True)
    else:
        end_ms = end_ms if end_ms is not None else _now_ms()
        periods = plan_periods(start_ms, end_ms)
        whole, *per_period = await asyncio.gather(
            search({after_key: start_ms, before_key: end_ms}, SPAN_SEARCH_LIMIT),
            *period_searches(periods),
            return_exceptions=True,
        )
        whole_failed = _failed(whole, log)
    log.info(
        "knowledgegraph__search_timeline: periods=%d field=%s", len(periods), date_field,
    )

    hits = [
        _PeriodHits(period, [], {}, failed=True) if _failed(result, log)
        else _PeriodHits(period, list(result.get("searchResults") or []),
                         dict(result.get("virtual_to_record_map") or {}),
                         incomplete=result.get("searchIncomplete") or None)
        for period, result in zip(periods, per_period)
    ]
    undated = _PeriodHits(None, [], {})
    if not whole_failed:
        _add_whole_span_hits(hits, undated, whole, date_field)
    if whole_failed and all(h.failed for h in hits):
        return _json("error", "The timeline searches failed. Try again, or use knowledgegraph__search.")
    return _Collected([*hits, undated] if undated.results else hits, start_ms, end_ms)


async def execute_timeline_search(
    state: ChatState | None,
    query: str | None,
    *,
    start: str | None = None,
    end: str | None = None,
    date_field: str | None = None,
    source_ids: list[str] | None = None,
    entity_ids: list[str] | None = None,
) -> str | list[Part]:
    if not query or not query.strip():
        return _json("error", "No search query provided")
    if not state:
        return _json("error", "Tool state not initialized")
    date_field = (date_field or "created").strip().lower()
    if date_field not in _DATE_FIELDS:
        return _json("error", "date_field must be 'created' or 'modified'.")
    after_arg, before_arg, after_key, before_key, _ = _DATE_FIELDS[date_field]

    bounds, bounds_error = parse_time_range(**{after_arg: start, before_arg: end})
    if bounds_error is not None:
        return bounds_error.replace(after_arg, "start").replace(before_arg, "end")
    start_ms = (bounds or {}).get(after_key)
    end_ms = (bounds or {}).get(before_key)
    if end_ms is not None:
        end_ms = min(end_ms, _now_ms())
    if start_ms is not None and end_ms is not None and start_ms > end_ms:
        return _json("error", "start must be on or before end — got an inverted range.")

    log = state.get("logger", logger)
    retrieval_service = state.get("retrieval_service")
    if not retrieval_service or not state.get("graph_provider"):
        return _json("error", "Retrieval services not available")

    try:
        scope = await _resolve_scope(state, source_ids, entity_ids)
        if isinstance(scope, str):
            return scope

        def searcher(filter_groups: dict[str, Any]) -> Any:  # noqa: ANN401
            async def search(time_range: dict[str, int] | None, limit: int) -> dict[str, Any] | None:
                return await retrieval_service.search_with_filters(
                    queries=[query],
                    org_id=state.get("org_id", ""),
                    user_id=state.get("user_id", ""),
                    limit=limit,
                    filter_groups=filter_groups,
                    time_range=time_range,
                    virtual_record_ids_from_tool=scope.virtual_record_ids,
                )
            return search

        with_entities = bool(scope.entity_filter_groups)
        collected = await _collect(
            searcher(scope.groups(with_entities=with_entities)), start_ms, end_ms, date_field, log,
        )
        notes = list(scope.notes)
        if with_entities and isinstance(collected, _Collected) and not any(h.results for h in collected.hits):
            # Entity filters are a hard AND at the graph layer; a missing edge
            # empties the scope although matching records exist.
            retried = await _collect(
                searcher(scope.groups(with_entities=False)), start_ms, end_ms, date_field, log,
            )
            if isinstance(retried, _Collected) and any(h.results for h in retried.hits):
                collected = retried
                notes.append(
                    "Note: nothing matched inside the requested department/category/topic/"
                    "language, so these results are NOT limited to it."
                )
        if isinstance(collected, str):
            return collected
        if not any(h.results for h in collected.hits):
            message = "No results found in any period."
            if any(h.failed or h.incomplete for h in collected.hits):
                message += (
                    " Some periods could not be searched in full, so this does not show the "
                    "information is missing."
                )
            message += (
                " Before concluding it does not exist, search with knowledgegraph__search and no "
                "date filters: a record's source date may fall outside this span."
            )
            return _json("success", " ".join([message, *notes]))
        if start_ms is None:
            notes.append(
                f"No start was given, so this covers {_day(collected.start_ms)} on: a year before the "
                "end, or the earliest of the best matches if that is older. Pass start to look further back."
            )
        return await _render_timeline(state, query, date_field, "".join(f"{n}\n" for n in notes), collected)
    except Exception as exc:
        log.error("knowledgegraph__search_timeline error: %s", exc, exc_info=True)
        return _json("error", f"Timeline search error: {exc}")


async def timeline_search_response(
    search: Callable[[dict[str, int] | None, int], Awaitable[dict[str, Any] | None]],
    *,
    start_ms: int | None,
    end_ms: int | None,
    date_field: str,
    period_limit: int = PERIOD_SEARCH_LIMIT,
    log: logging.Logger = logger,
) -> dict[str, Any] | None:
    """The search API's ``timeline`` option: this tool's period-by-period
    search, returned in the search API's own response shape.

    ``search(time_range, limit)`` runs one search of the caller's query with
    the caller's filters, adding ``time_range`` (this module's
    ``source_*_ms`` keys, or None) to any other bounds it has. The caller
    supplies it, so permissions and filters are exactly those of a plain
    search. The periods, the whole-span search and the filing of its hits
    are ``_collect``'s, as for the assistant's tool.

    ``searchResults`` come period by period, oldest first, and by record date
    within a period; each hit carries ``timelinePeriod``, its index in
    ``timeline.periods``. When nothing was found and no search succeeded,
    the first search's own response is returned, as a plain search would
    have returned it (a refused filter, no accessible records). None when
    every search raised.
    """
    if end_ms is not None:
        end_ms = min(end_ms, _now_ms())
    responses: list[dict[str, Any]] = []

    async def recorded(time_range: dict[str, int] | None, limit: int) -> dict[str, Any] | None:
        result = await search(time_range, limit)
        if isinstance(result, dict):
            responses.append(result)
        return result

    collected = await _collect(
        recorded, start_ms, end_ms, date_field, log, period_limit=period_limit,
    )
    found = isinstance(collected, _Collected) and any(h.results for h in collected.hits)
    if not found and not any(r.get("status_code", 200) == 200 for r in responses):
        return responses[0] if responses else None
    if isinstance(collected, str):
        return None
    return _api_response(collected, responses, date_field, start_given=start_ms is not None)


def _api_response(
    collected: _Collected,
    responses: list[dict[str, Any]],
    date_field: str,
    *,
    start_given: bool,
) -> dict[str, Any]:
    search_results: list[dict[str, Any]] = []
    record_map: dict[str, Any] = {}
    record_ids: set[str] = set()
    periods: list[dict[str, Any]] = []
    for index, bucket in enumerate(collected.hits):
        ordered = sorted(
            bucket.results,
            key=lambda hit, records=bucket.records: (
                record_date_ms(records.get(_hit_key(hit)[0]), date_field) or 0
            ),
        )
        in_period: set[str] = set()
        for hit in ordered:
            search_results.append({**hit, "timelinePeriod": index})
            record_id = (hit.get("metadata") or {}).get("recordId")
            if isinstance(record_id, str) and record_id:
                in_period.add(record_id)
        record_ids |= in_period
        record_map.update(bucket.records)
        period: dict[str, Any] = {
            "label": bucket.label,
            "start": _day(bucket.period.start_ms) if bucket.period else None,
            "end": _day(bucket.period.end_ms) if bucket.period else None,
            "records": len(in_period),
            "matches": len(ordered),
        }
        if bucket.failed:
            period["searchFailed"] = True
        if bucket.incomplete:
            period["searchIncomplete"] = bucket.incomplete
        periods.append(period)

    records: dict[str, Any] = {}
    omitted: dict[str, int] = {}
    incomplete: str | None = None
    for response in responses:
        for record in response.get("records") or []:
            key = record.get("_key") if isinstance(record, dict) else None
            if key in record_ids and key not in records:
                records[key] = record
        for record_id, count in (response.get("omittedPassages") or {}).items():
            if record_id in record_ids and isinstance(count, int):
                omitted[record_id] = max(omitted.get(record_id, 0), count)
        incomplete = incomplete or response.get("searchIncomplete") or None

    notes: list[str] = []
    if not start_given:
        notes.append(
            f"No start date was given, so this covers {_day(collected.start_ms)} on: a year "
            "before the end, or the earliest of the best matches if that is older."
        )
    payload: dict[str, Any] = {
        "searchResults": search_results,
        "records": list(records.values()),
        "status": "success",
        "status_code": 200,
        "message": (
            "Query processed successfully. Relevant records retrieved." if search_results
            else "No results found in any period."
        ),
        "virtual_to_record_map": record_map,
        "timeline": {
            "dateField": date_field,
            "start": _day(collected.start_ms),
            "end": _day(collected.end_ms),
            "periods": periods,
            "notes": notes,
        },
    }
    if omitted:
        payload["omittedPassages"] = omitted
    if incomplete:
        payload["searchIncomplete"] = incomplete
    return payload


_NOT_SEARCHED = "This period's own search failed."


def _too_long_note(vrids: list[str], records: dict[str, Any], named: dict[str, str]) -> str:
    """Names the records whose best match was too long to show, so the model
    can read them; adds them to ``named``."""
    lines = []
    for vrid in vrids:
        record = records.get(vrid)
        if not isinstance(record, dict):
            continue
        record_id = str(record.get("id") or record.get("_key") or "")
        name = str(record.get("record_name") or record.get("recordName") or "")
        if not record_id:
            continue
        named[record_id] = name
        lines.append(f"- {name} (Record ID: {record_id})")
    if not lines:
        return ""
    return (
        f"Also matched, too long to show here; read with {FETCH_RECORD_TOOL_NAME} if it matters:\n"
        + "\n".join(lines) + "\n"
    )


def _shares(budget: int, weights: list[int | None]) -> list[int]:
    """Characters for each period; ``None`` for a period with nothing to show.

    A third of the budget is split evenly between the periods that have
    something, so a quiet period still shows its best match; the rest goes by
    weight, so the period where the matches are densest shows the most.
    """
    shown = [w for w in weights if w is not None]
    if not shown:
        return [0 for _ in weights]
    floor = budget // (3 * len(shown))
    rest = max(budget - floor * len(shown), 0)
    total = sum(shown)
    return [
        0 if w is None else floor + (rest * w // total if total else rest // len(shown))
        for w in weights
    ]


def _footer(date_field: str) -> str:
    after_arg, before_arg = _DATE_FIELDS[date_field][:2]
    return (
        "\n\nAnswer period by period in date order, and cite each fact from the blocks under "
        "its period. A period with no blocks may only mean nothing matched this query: before "
        "saying nothing happened then, search it with knowledgegraph__search and "
        f"{after_arg}/{before_arg}."
    )


@dataclass
class _Shown:
    """What a timeline put in front of the model, for the citation pipeline."""

    sections: list[str] = field(default_factory=list)
    units: list[dict[str, Any]] = field(default_factory=list)
    records: dict[str, Any] = field(default_factory=dict)
    named: dict[str, str] = field(default_factory=dict)
    images: list[dict[str, Any]] = field(default_factory=list)


def _status_line(period_hits: _PeriodHits, has_matches: bool) -> str:  # noqa: FBT001
    if not has_matches:
        if period_hits.failed:
            return f"{_NOT_SEARCHED} Search it again before drawing conclusions about it.\n"
        if period_hits.incomplete:
            return (
                f"No matches, but {period_hits.incomplete} Search it again before drawing "
                "conclusions about it.\n"
            )
        return "No matching records.\n"
    if period_hits.failed:
        return f"{_NOT_SEARCHED} These are its matches from the search of the whole span.\n"
    if period_hits.incomplete:
        return f"Note: {period_hits.incomplete} A record missing here may still exist.\n"
    return ""


async def _render_timeline(
    state: ChatState, query: str, date_field: str, notes: str, collected: _Collected,
) -> str | list[Part]:
    from app.modules.retrieval.context.renderer import KnowledgeRenderer
    from app.utils.chat_helpers import (
        CitationRefMapper,
        ImageBudget,
        get_record_id_shortener_if_enabled,
    )
    from app.utils.image_admission import admission_from_state

    hits = collected.hits
    contexts = await _build_contexts(state, hits)
    ref_mapper = state.get("citation_ref_mapper")
    if not isinstance(ref_mapper, CitationRefMapper):
        ref_mapper = CitationRefMapper()
    image_budget = state.get("image_budget")
    if not isinstance(image_budget, ImageBudget):
        image_budget = state["image_budget"] = ImageBudget()
    image_admission = admission_from_state(state)
    shortener = get_record_id_shortener_if_enabled(state)

    dated = sum(1 for h in hits if h.period is not None)
    placed_by = "created" if date_field == "created" else "last modified"
    undated_note = (
        " Matches whose record has no source date come last." if dated < len(hits) else ""
    )
    header = (
        f'Timeline for "{query}": {dated} period{"s" if dated != 1 else ""} from '
        f"{_day(collected.start_ms)} to {_day(collected.end_ms)}, oldest first. Records are placed by "
        f"the date they were {placed_by} at their source.{undated_note} Each period shows its "
        "best-matching blocks: a sample, not every record.\n"
        f"{notes}"
    )
    footer = _footer(date_field)
    has_matches = [context is not None and bool(context.ranked) for context in contexts]
    headings = [f"\n### {h.label}\n" for h in hits]
    status = [_status_line(h, matched) for h, matched in zip(hits, has_matches)]
    budget = TIMELINE_RESULT_CHARS - len(header) - len(footer) - sum(map(len, headings)) - sum(map(len, status))
    shares = _shares(budget, [h.weight if matched else None for h, matched in zip(hits, has_matches)])

    shown = _Shown(sections=[header])
    # Negative after a period overspends, so later periods give it back
    # instead of the size cap cutting the newest period and the footer.
    carry = 0
    for heading, line, period_hits, context, period_share in zip(headings, status, hits, contexts, shares):
        shown.sections.append(heading + line)
        if context is None or not context.ranked:
            continue
        share = period_share + carry
        renderer = KnowledgeRenderer(
            context.virtual_record_id_to_result,
            ref_mapper=ref_mapper,
            is_multimodal_llm=bool(state.get("is_multimodal_llm", False)),
            by_relevance=False,
            record_id_shortener=shortener,
            image_budget=image_budget,
            image_admission=image_admission,
        )
        used = _render_period(renderer, context, period_hits, date_field, max(share, 0), shown, shortener)
        carry = share - used
    shown.sections.append(footer)

    state["citation_ref_mapper"] = ref_mapper
    _remember_shown(state, shown)
    return tool_output("".join(shown.sections), shown.images, state)


async def _build_contexts(state: ChatState, hits: list[_PeriodHits]) -> list[Any]:
    """Each period's hits as the units the model can be shown (None: no hits)."""
    from app.modules.retrieval.context.builder import KnowledgeContextBuilder
    from app.modules.transformers.blob_storage import BlobStorage

    config_service = state.get("config_service")
    graph_provider = state.get("graph_provider")
    builder = KnowledgeContextBuilder(
        blob_store=BlobStorage(
            logger=state.get("logger", logger), config_service=config_service, graph_provider=graph_provider,
        ),
        graph_provider=graph_provider,
        org_id=state.get("org_id", ""),
        user_id=state.get("user_id", ""),
        config_service=config_service,
    )
    is_multimodal_llm = bool(state.get("is_multimodal_llm", False))
    return await asyncio.gather(*(
        builder.build(h.results, h.records, is_multimodal_llm=is_multimodal_llm,
                      max_units=min(MAX_UNITS_PER_PERIOD + h.weight, SPAN_SEARCH_LIMIT))
        if h.results else _nothing()
        for h in hits
    ))


def _render_period(
    renderer: Any,  # noqa: ANN401
    context: Any,  # noqa: ANN401
    period_hits: _PeriodHits,
    date_field: str,
    share: int,
    shown: _Shown,
    shortener: Any,  # noqa: ANN401
) -> int:
    """Render one period's best units within ``share`` characters, its records
    oldest first, into ``shown``. Returns the characters used."""
    from app.modules.retrieval.context.renderer import Selection

    ranked = context.ranked
    selection = renderer.select(ranked, share)
    too_long: list[str] = []
    if (selection.chars or 0) > share:
        # The renderer always keeps a result's best unit, however long. One long
        # unit (a pull request diff) would take every later period's share, so it
        # is named for the fetch tool instead of shown.
        oversized = {id(unit) for unit in ranked if (renderer.select([unit], share).chars or 0) > share}
        too_long = list(dict.fromkeys(
            unit.get("virtual_record_id") or "" for unit in ranked if id(unit) in oversized
        ))
        selection = renderer.select([unit for unit in ranked if id(unit) not in oversized], share)

    by_record: dict[str, list[dict[str, Any]]] = {}
    for unit in selection.ranked:
        by_record.setdefault(unit.get("virtual_record_id") or "", []).append(unit)
    used = 0
    for vrid in sorted(by_record, key=lambda v: record_date_ms(period_hits.records.get(v), date_field) or 0):
        rendered = renderer.render(Selection(ranked=by_record[vrid]))
        shown.sections.append(rendered.text + "\n")
        used += len(rendered.text) + 1
        shown.units.extend(rendered.ranked)
        shown.images.extend(rendered.images)
        record = context.virtual_record_id_to_result.get(vrid)
        if record is not None:
            shown.records[vrid] = record
    if too_long:
        note = _too_long_note(too_long, context.virtual_record_id_to_result, shown.named)
        if shortener is not None:
            note = shortener.shorten_record_ids_in_text(note)
        shown.sections.append(note)
        used += len(note)
    return used


def _remember_shown(state: ChatState, shown: _Shown) -> None:
    """Record what was shown the way knowledgegraph__search does: the citation
    pipeline, the fetch tool and the retrieval trace all read these."""
    from app.utils.chat_helpers import merge_into_records_map

    state["final_results"] = dedupe_append_final_results(state.get("final_results", []), shown.units)
    live = state.get("virtual_record_id_to_result")
    if not isinstance(live, dict):
        live = state["virtual_record_id_to_result"] = {}
    merge_into_records_map(live, shown.records)
    existing = state.get("tool_records")
    existing = existing if isinstance(existing, list) else []
    existing_ids = {r.get("_id") for r in existing if isinstance(r, dict)}
    state["tool_records"] = existing + [
        r for r in shown.records.values() if not (isinstance(r, dict) and r.get("_id") in existing_ids)
    ]
    note_retrieved_by(state, "vector", [
        (vrid, record.get("id") or record.get("_key"), record.get("record_name") or record.get("recordName"))
        for vrid, record in shown.records.items() if isinstance(record, dict)
    ])
    remember_shown_results(state, shown.units, shown.records)
    if shown.named:
        remember_record_ids(state, shown.named, names=shown.named)


async def _nothing() -> None:
    return None


_HEADER_RE = re.compile(r'^Timeline for ".*": (\d+) periods? from', re.MULTILINE)


def timeline_result_summary(args: dict[str, Any], result: ToolResult) -> str | None:
    """One-line activity summary of a timeline, plus the record names it showed."""
    text = result_text(result.content)
    match = _HEADER_RE.search(text) if text else None
    if not match:
        return search_result_summary(args, result)
    periods = int(match.group(1))
    names = [name.strip() for name in _RECORD_NAME_RE.findall(text) if name.strip()]
    header = (
        f"Searched {periods} period{'s' if periods != 1 else ''}, "
        f"found {len(names)} record{'s' if len(names) != 1 else ''}"
    )
    return header + "\n" + bullet_list(names) if names else header


__all__ = [
    "MAX_PERIODS",
    "TIMELINE_SUMMARIES_ENV_VAR",
    "TIMELINE_TOOL_NAME",
    "Period",
    "execute_timeline_search",
    "plan_periods",
    "record_date_ms",
    "span_of",
    "timeline_result_summary",
    "timeline_search_response",
    "timeline_summaries_enabled",
    "without_timeline_unless_enabled",
]
