"""Resolve typed entity filters into the records that mention a match.

Matching grants nothing. It is one capped graph lookup, and each caller then
checks only these hits against what the user may read, so a filter never
loads the user's whole accessible corpus to intersect a few thousand records.
A truncated match is an error, not a partial answer: a capped set would
silently drop records the filter named.
"""

from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from app.modules.named_entities.domain.kinds import EntityKind, kind_family
from app.modules.named_entities.graph_ops import NamedEntityQuery, filter_constraints

if TYPE_CHECKING:
    from collections.abc import Callable

_LOW = -1e308
_HIGH = 1e308

TOO_BROAD_MESSAGE = (
    "This entity filter matches too many documents. Narrow it: a tighter range, a name, or fewer kinds."
)
_KINDS = frozenset(kind.value for kind in EntityKind)
UNKNOWN_KIND_PREFIX = "Unknown entity kind: "
_CURRENCY = re.compile(r"^[A-Z]{3}$")


class EntityFilterUnavailableError(Exception):
    """The named-entity graph could not answer; never the same as no match."""


@dataclass(frozen=True, slots=True)
class EntityFilterHit:
    record_id: str
    virtual_record_id: str
    connector_id: str
    record_type: str = ""
    # Last source modification (else last update), for newest-first listings.
    sort_ts: int = 0


@dataclass(frozen=True, slots=True)
class EntityFilterMatch:
    hits: tuple[EntityFilterHit, ...] = ()
    error: str | None = None
    # False when the filter names no constraint, so it must not narrow anything.
    constrained: bool = True

    @property
    def virtual_record_ids(self) -> list[str]:
        return list(dict.fromkeys(hit.virtual_record_id for hit in self.hits if hit.virtual_record_id))


class EntityFilterResolver:
    def __init__(self, graph: Any) -> None:
        self._graph = graph

    async def match(
        self, org_id: str, raw: dict[str, Any], *, within: list[str] | None = None, limit: int | None = None,
    ) -> EntityFilterMatch:
        """With ``within`` (record ids the caller may read) only those records count,
        including toward "too broad". ``limit`` raises how many matches may be listed
        before the filter is too broad (the graph caps it)."""
        if not isinstance(raw, dict):
            return EntityFilterMatch(error="entityFilters must be an object")
        entity_ids = [str(item) for item in (raw.get("entityIds") or []) if item]
        try:
            query = entity_query_from(raw)
        except ValueError as exc:
            return EntityFilterMatch(error=str(exc))
        if not entity_ids and query is None:
            return EntityFilterMatch(constrained=False)
        options: dict[str, Any] = {}
        if within is not None:
            options["within"] = within
        if limit is not None:
            options["limit"] = limit
        try:
            result = await self._graph.get_records_for_named_entities(org_id, entity_ids or None, query, **options)
        except Exception as exc:
            raise EntityFilterUnavailableError("named-entity lookup failed") from exc
        if result.get("truncated"):
            return EntityFilterMatch(error=TOO_BROAD_MESSAGE)
        hits = tuple(
            EntityFilterHit(
                record_id=str(row["recordId"]),
                virtual_record_id=str(row.get("virtualRecordId") or ""),
                connector_id=str(row.get("connectorId") or ""),
                record_type=str(row.get("recordType") or ""),
                sort_ts=_timestamp(row.get("sortTs")),
            )
            for row in result.get("hits") or []
            if row.get("recordId")
        )
        return EntityFilterMatch(hits=hits)


def _timestamp(value: object) -> int:
    return int(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else 0


def _number(value: object, field: str) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(f"{field} must be a finite number")
    return value


def _epoch_ms(value: object, field: str) -> int | None:
    """Epoch milliseconds, or an ISO-8601 date or date-time (a date alone is UTC midnight)."""
    if value is None or (isinstance(value, (int, float)) and not isinstance(value, bool)):
        return None if value is None else int(_number(value, field))
    if isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.strip())
        except ValueError:
            pass
        else:
            return int((parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)).timestamp() * 1000)
    raise ValueError(f"{field} must be epoch milliseconds or an ISO-8601 date")


def _bound(low: float | None, high: float | None) -> tuple[float | None, float | None]:
    if low is None and high is None:
        return None, None
    if low is not None and high is not None and low > high:
        raise ValueError("a range's start must not be after its end")
    return (_LOW if low is None else low), (_HIGH if high is None else high)


def entity_query_from(raw: dict[str, Any]) -> NamedEntityQuery | None:
    """The typed query, or None when the filter names no constraint. Raises
    ValueError, with a message for the caller, on input the graph must not see."""
    kinds = [str(kind).strip().casefold() for kind in (raw.get("kinds") or [])]
    unknown = sorted(set(kinds) - _KINDS)
    if unknown:
        raise ValueError(
            f"{UNKNOWN_KIND_PREFIX}{', '.join(unknown)}. Kinds are entity types, not document types; "
            f"valid kinds: {', '.join(sorted(_KINDS))}."
        )
    name = raw.get("name")
    if name is not None and not str(name).strip():
        raise ValueError("name must not be blank")
    date = raw.get("mentionedDate") or {}
    amount = raw.get("amount") or {}
    quantity = raw.get("quantity") or {}
    percent = raw.get("percent") or {}
    date_start, date_end = _bound(_epoch_ms(date.get("from"), "mentionedDate.from"), _epoch_ms(date.get("to"), "mentionedDate.to"))
    amount_min, amount_max = _bound(_number(amount.get("min"), "amount.min"), _number(amount.get("max"), "amount.max"))
    currency = str(amount.get("currency") or "").strip().upper()
    if currency and not _CURRENCY.match(currency):
        raise ValueError("amount.currency must be a three-letter ISO 4217 code")
    if currency and amount_min is None:
        amount_min, amount_max = _LOW, _HIGH
    quantity_min, quantity_max = _bound(_number(quantity.get("min"), "quantity.min"), _number(quantity.get("max"), "quantity.max"))
    dimension = str(quantity.get("dimension") or "").strip().casefold()
    if dimension and quantity_min is None:
        quantity_min, quantity_max = _LOW, _HIGH
    percent_min, percent_max = _bound(_number(percent.get("min"), "percent.min"), _number(percent.get("max"), "percent.max"))
    name = str(name or "").strip()
    if name:
        # A name is looked up under every kind the extractor may have filed it under;
        # kinds alone ("records mentioning a product") stay exact, or they would widen to every company.
        kinds = list(dict.fromkeys(k.value for kind in kinds for k in kind_family(EntityKind(kind))))
    if not any([kinds, name, date_start is not None, amount_min is not None, quantity_min is not None, percent_min is not None]):
        return None
    return NamedEntityQuery(
        kinds=kinds or None,
        name_prefix=name,
        date_start_ms=date_start,
        date_end_ms=date_end,
        amount_min=amount_min,
        amount_max=amount_max,
        currency=currency,
        quantity_min=quantity_min,
        quantity_max=quantity_max,
        dimension=dimension,
        percent_min=percent_min,
        percent_max=percent_max,
        limit=5000,
    )


_FREE_TEXT_MAX = 80


def describe_entity_filter(raw: object) -> str | None:
    """What a request's ``entityFilters`` limits records to, in words, or None
    when it is invalid or names no constraint. Built from the graph's own
    constraints (``filter_constraints``), so the words combine its parts the way
    the match does. Free text (a name, a quantity's dimension) is set by the
    client, so it is quoted, kept to one line and capped."""
    if not isinstance(raw, dict):
        return None
    ids = raw.get("entityIds")
    entity_ids = [str(item) for item in ids if item] if isinstance(ids, list) else []
    try:
        query = entity_query_from(raw)
    except ValueError:
        return None
    if not entity_ids and query is None:
        return None
    constraints = filter_constraints(entity_ids, query)
    name = query.name_prefix if query is not None else ""
    described = [" or ".join(filter(None, (_part(part, name) for part in parts))) for parts in constraints]
    return " and ".join(filter(None, described)) or None


def _part(part: Any, name: str) -> str:  # noqa: ANN401
    """``name`` is the filter's own spelling; the graph matches it lowercased."""
    binds = part.binds
    kinds = " or ".join(binds.get("kinds") or ())
    if part.source == "node":
        ids, prefix = binds.get("ids") or [], binds.get("prefix") or ""
        subject = (
            "the entity picked for this conversation" if len(ids) == 1
            else f"one of {len(ids)} entities picked for this conversation" if ids
            else "an entity"
        )
        if not (ids or prefix):
            return f"any entity of kind {kinds}"
        named = f" whose name starts with {_quoted(name or prefix)}" if prefix else ""
        return f"{subject}{f' ({kinds})' if kinds else ''}{named}"
    if binds.get("dateStart") is not None:
        # The graph matches a mention overlapping [start, end): the end is exclusive.
        span = _span(binds["dateStart"], binds["dateEnd"], _iso_date, "on or after", "before", "up to but not including")
        return f"a date {span}"
    if binds.get("amountMin") is not None:
        cur = f" {binds['currency']}" if binds.get("currency") else ""
        span = _span(binds["amountMin"], binds["amountMax"], lambda v: f"{_digits(v)}{cur}")
        return f"an amount {span}" if span else f"an amount in{cur}" if cur else "any amount"
    if binds.get("quantityMin") is not None:
        unit = f" {_quoted(binds['dimension'])}" if binds.get("dimension") else ""
        span = _span(binds["quantityMin"], binds["quantityMax"], lambda v: f"{_digits(v)}{unit}")
        return f"a quantity {span}" if span else f"a quantity in{unit}" if unit else "any quantity"
    if binds.get("percentMin") is not None:
        # The API takes percentages as fractions: 0.125 is 12.5%.
        span = _span(binds["percentMin"], binds["percentMax"], lambda v: f"{_digits(round(v * 100, 10))}%")
        return f"a percentage {span}" if span else "any percentage"
    return f"any {kinds} value" if kinds else ""


def _span(
    low: float, high: float | None, show: Callable[[float], str],
    at_least: str = "of at least", at_most: str = "of at most", to: str = "to",
) -> str:
    has_low, has_high = low > _LOW, high is not None and high < _HIGH
    if has_low and has_high:
        return f"from {show(low)} {to} {show(high)}"
    if has_low:
        return f"{at_least} {show(low)}"
    return f"{at_most} {show(high)}" if has_high else ""


def _digits(value: float) -> str:
    """Every digit the graph compares: ``:g`` would round 1234567 to 1.23457e+06."""
    return f"{int(value):,}" if float(value).is_integer() else f"{value:,}"


def _iso_date(ms: float) -> str:
    """The date alone at UTC midnight, else the full UTC time the graph compares."""
    try:
        moment = datetime.fromtimestamp(ms / 1000, tz=UTC)
    except (ValueError, OverflowError, OSError):
        return f"{ms:.0f} ms since 1970"
    return moment.date().isoformat() if moment == moment.replace(hour=0, minute=0, second=0, microsecond=0) \
        else moment.isoformat().replace("+00:00", "Z")


def _quoted(text: str) -> str:
    one_line = " ".join(str(text).split())
    capped = one_line if len(one_line) <= _FREE_TEXT_MAX else one_line[:_FREE_TEXT_MAX] + "…"
    return json.dumps(capped, ensure_ascii=False)
