"""Resolve typed entity filters into the records that mention a match.

Matching grants nothing. It is one capped graph lookup, and each caller then
checks only these hits against what the user may read, so a filter never
loads the user's whole accessible corpus to intersect a few thousand records.
A truncated match is an error, not a partial answer: a capped set would
silently drop records the filter named.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from app.modules.named_entities.domain.kinds import EntityKind
from app.modules.named_entities.graph_ops import NamedEntityQuery

_LOW = -1e308
_HIGH = 1e308

TOO_BROAD_MESSAGE = (
    "This entity filter matches too many documents. Narrow it: a tighter range, a name, or fewer kinds."
)
_KINDS = frozenset(kind.value for kind in EntityKind)
_CURRENCY = re.compile(r"^[A-Z]{3}$")


class EntityFilterUnavailableError(Exception):
    """The named-entity graph could not answer; never the same as no match."""


@dataclass(frozen=True, slots=True)
class EntityFilterHit:
    record_id: str
    virtual_record_id: str
    connector_id: str


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
            query = _query_from(raw)
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
            )
            for row in result.get("hits") or []
            if row.get("recordId")
        )
        return EntityFilterMatch(hits=hits)


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


def _query_from(raw: dict[str, Any]) -> NamedEntityQuery | None:
    """The typed query, or None when the filter names no constraint. Raises
    ValueError, with a message for the caller, on input the graph must not see."""
    kinds = [str(kind).strip().casefold() for kind in (raw.get("kinds") or [])]
    unknown = sorted(set(kinds) - _KINDS)
    if unknown:
        raise ValueError(f"Unknown entity kind: {', '.join(unknown)}")
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
