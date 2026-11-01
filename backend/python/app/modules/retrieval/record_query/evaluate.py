"""Evaluating a ``RecordQuery`` over candidate rows: the one place conditions,
unknowns and aggregates are decided.

The engine runs it over the rows the access check admitted; the contract
tests run it over a seeded graph's rows as the reference both providers must
equal. A row is the provider's candidate shape (``docs/record-query.md``).

Conditions use three values: a record that lacks a field a condition reads is
*unknown* for it, neither a match nor a miss. ``all_of`` is false if any part
is false, else unknown if any part is; ``any_of`` is true if any part is,
else unknown if any part is; ``not`` keeps unknown unknown. Unknown rows are
counted per field, never silently dropped.
"""

from __future__ import annotations

import datetime as dt
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from app.modules.retrieval.record_fields import ROLE_EDGES, FieldKind
from app.modules.retrieval.record_fields import field as field_spec
from app.modules.retrieval.record_query.model import (
    AllOf,
    EntityCondition,
    FieldCondition,
    Group,
    LinkCondition,
    Not,
    OrganizationCondition,
    PersonCondition,
    day_start_ms,
)

if TYPE_CHECKING:
    from collections.abc import Iterable

    from app.modules.retrieval.record_query.model import Condition, RecordQuery

Row = dict[str, Any]
_DAY_MS = 86_400_000
ROLE_OF_EDGE: dict[str, str] = {edge: role for role, edges in ROLE_EDGES.items() for edge in edges}


def field_value(row: Row, name: str) -> Any:  # noqa: ANN401 - a field is a value, a number or a date
    """A row's value for a declared field; None when the record does not carry it."""
    ticket, deal = row.get("ticket") or {}, row.get("deal") or {}
    if name == "deal_outcome":
        if not deal or deal.get("isClosed") is None:
            return None
        return "OPEN" if not deal["isClosed"] else ("WON" if deal.get("isWon") else "LOST")
    if name == "resolution_days":
        resolved, created = ticket.get("resolvedAt"), row.get("createdAt")
        if resolved is None or created is None:
            return None
        return (day_start_ms(_iso(resolved)) - day_start_ms(_iso(created))) // _DAY_MS
    if name == "close_date":
        close = deal.get("closeDate")
        return day_start_ms(str(close)) if close else None
    plain = {
        "source": row.get("connectorName"), "record_type": row.get("recordType"),
        "container": row.get("recordGroupId"), "created": row.get("createdAt"), "updated": row.get("updatedAt"),
        "status": ticket.get("status"), "source_status": ticket.get("sourceStatus"),
        "priority": ticket.get("priority"), "resolved": ticket.get("resolvedAt"), "due": ticket.get("dueAt"),
        "amount": deal.get("amount"),
    }
    return plain[name]


def _iso(epoch_ms: int) -> str:
    return dt.datetime.fromtimestamp(epoch_ms / 1000, tz=dt.UTC).date().isoformat()


@dataclass
class Verdict:
    """True, False or None (unknown), and the fields that made it unknown."""

    value: bool | None
    unknown_fields: frozenset[str] = frozenset()


def _field(cond: FieldCondition, row: Row) -> Verdict:
    if cond.field == "container":
        # Only ever top level (RecordQuery checks), where the provider applies it
        # with nested groups included; a row knows only its own group.
        return Verdict(True)
    value = field_value(row, cond.field)
    if value is None:
        return Verdict(None, frozenset({cond.field}))
    spec = field_spec(cond.field)
    if cond.in_ is not None:
        wanted = {v.upper() for v in cond.in_}
        return Verdict(str(value).upper() in wanted)
    if spec.kind is FieldKind.DATE:
        low, high = cond.bounds_ms()
    else:
        low = float(cond.from_) if cond.from_ is not None else None
        high = float(cond.to) if cond.to is not None else None
    return Verdict((low is None or value >= low) and (high is None or value <= high))


def evaluate(cond: Condition, row: Row) -> Verdict:
    if isinstance(cond, FieldCondition):
        return _field(cond, row)
    if isinstance(cond, PersonCondition):
        edges = set(ROLE_EDGES[cond.person.role]) if cond.person.role else set(ROLE_OF_EDGE)
        people = row.get("people") or []
        return Verdict(any(p["role"] in edges and p["id"] in cond.person.ids for p in people))
    if isinstance(cond, OrganizationCondition):
        return Verdict(any(o["id"] in cond.organization.ids for o in row.get("organizations") or []))
    if isinstance(cond, LinkCondition):
        relation = cond.linked_to.relation
        return Verdict(any(
            link["id"] in cond.linked_to.ids and (relation is None or link.get("relation") == relation)
            for link in row.get("links") or []
        ))
    if isinstance(cond, EntityCondition):
        # Only ever top level (RecordQuery checks), where the provider applies it:
        # a row without "entities" was selected for it already.
        if "entities" not in row:
            return Verdict(True)
        return Verdict(any(e["type"] == cond.entity.type and e["id"] == cond.entity.id for e in row["entities"]))
    if isinstance(cond, Not):
        inner = evaluate(cond.not_, row)
        return Verdict(None if inner.value is None else not inner.value, inner.unknown_fields)
    parts = [evaluate(c, row) for c in (cond.all_of if isinstance(cond, AllOf) else cond.any_of)]
    unknown = frozenset().union(*(p.unknown_fields for p in parts if p.value is None))
    decisive = False if isinstance(cond, AllOf) else True
    if any(p.value is decisive for p in parts):
        return Verdict(decisive)
    if unknown:
        return Verdict(None, unknown)
    return Verdict(not decisive)


def evaluate_all(conditions: list[Condition], row: Row) -> Verdict:
    return evaluate(AllOf(all_of=conditions), row) if conditions else Verdict(True)


@dataclass
class Tally:
    """What a scan of admitted rows found, before paging."""

    matched: list[Row] = field(default_factory=list)
    unknown: Counter[str] = field(default_factory=Counter)


def tally(query: RecordQuery, rows: Iterable[Row]) -> Tally:
    out = Tally()
    for row in rows:
        verdict = evaluate_all(query.where, row)
        if verdict.value is True:
            out.matched.append(row)
        elif verdict.value is None:
            out.unknown.update(verdict.unknown_fields)
    return out


def aggregate_value(query: RecordQuery, rows: list[Row]) -> tuple[float | None, int]:
    """The aggregate over ``rows`` and how many rows lacked its field."""
    op = query.aggregate.op
    if op == "count":
        return float(len(rows)), 0
    values = [field_value(r, query.aggregate.field) for r in rows]
    known = [float(v) for v in values if v is not None]
    missing = len(values) - len(known)
    if not known:
        return None, missing
    result = {"sum": sum(known), "min": min(known), "max": max(known), "avg": sum(known) / len(known)}[op]
    return result, missing


def _group_keys(query: RecordQuery, row: Row) -> list[tuple[str, str]]:
    by = query.group_by
    assert by is not None
    if by.field is not None:
        value = field_value(row, by.field)
        return [] if value is None else [(str(value), str(value))]
    if by.month is not None:
        value = field_value(row, by.month)
        return [] if value is None else [(_iso(value)[:7],) * 2]
    if by.organization:
        return [(o["id"], o.get("name") or o["id"]) for o in row.get("organizations") or []]
    edges = set(ROLE_EDGES[by.person_role]) if by.person_role else set(ROLE_OF_EDGE)
    return list({(p["id"], p.get("name") or p["id"]) for p in row.get("people") or [] if p["role"] in edges})


def groups(query: RecordQuery, rows: list[Row]) -> list[Group]:
    """Top groups by count; a row with two people in the role counts for both."""
    if query.group_by is None:
        return []
    members: dict[tuple[str, str], list[Row]] = defaultdict(list)
    for row in rows:
        for key in _group_keys(query, row):
            members[key].append(row)
    ranked = sorted(members.items(), key=lambda kv: (-len(kv[1]), kv[0][1]))[: query.top]
    out = []
    for (key, label), rows_in in ranked:
        value, _ = aggregate_value(query, rows_in) if query.aggregate.op != "count" else (None, 0)
        out.append(Group(key=key, label=label, count=len(rows_in), value=value))
    return out


__all__ = ["ROLE_OF_EDGE", "Row", "Tally", "Verdict", "aggregate_value", "evaluate", "evaluate_all",
           "field_value", "groups", "tally"]
