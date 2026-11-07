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

from app.modules.retrieval.person_identity import group_people, person_key
from app.modules.retrieval.record_fields import ROLE_EDGES, ROLE_OF_EDGE, FieldKind
from app.modules.retrieval.record_fields import field as field_spec
from app.modules.retrieval.record_query.model import (
    AllOf,
    EntityCondition,
    FieldCondition,
    Group,
    LinkCondition,
    MentionCondition,
    Not,
    OrganizationCondition,
    PersonCondition,
    day_start_ms,
)
from app.services.graph_db.common.record_query_rows import (
    CLASSIFIED_ENTITY_TYPES,
    classification_failed,
    classification_pending,
)

if TYPE_CHECKING:
    from collections.abc import Iterable

    from app.modules.retrieval.record_query.model import Condition, RecordQuery

Row = dict[str, Any]
_DAY_MS = 86_400_000
# The unknown reason of a row that a condition only classification decides,
# when the record has not been classified yet.
NOT_CLASSIFIED = "classification"
# ... and when its classification failed: not known either, and not retried on use.
CLASSIFICATION_FAILED = "classification_failed"
# The unknown reason of a row a mentions condition did not find: extraction
# misses values, so no extracted mention is not "does not mention".
MENTIONS = "mentions"


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


def _undecided(row: Row) -> Verdict:
    """A row without the classification edge a condition needs: a miss once
    classification succeeded (or does not apply), else unknown."""
    if classification_pending(row):
        return Verdict(None, frozenset({NOT_CLASSIFIED}))
    if classification_failed(row):
        return Verdict(None, frozenset({CLASSIFICATION_FAILED}))
    return Verdict(False)


# Entity types reached over edges extraction writes from the text; the recall
# check decides them from the row, as people and organisations.
EDGE_ENTITY_TYPES = frozenset({"person", "organization", "named_entity"})


def evaluate(cond: Condition, row: Row, *, recall: bool = False) -> Verdict:
    """``recall`` is the recall check's reading of a search hit the scan did
    not match (``recall.py``): a missing person or organisation edge is
    unknown there, not a miss, since extraction is what writes most of them;
    a role filled by someone else, or a field holding another value, is still a miss."""
    if isinstance(cond, FieldCondition):
        return _field(cond, row)
    if isinstance(cond, PersonCondition):
        edges = set(ROLE_EDGES[cond.person.role]) if cond.person.role else set(ROLE_OF_EDGE)
        return _person(row, edges, set(cond.person.ids or ()), recall=recall)
    if isinstance(cond, MentionCondition):
        if cond.record_ids is not None and row.get("_key") in cond.record_ids:
            return Verdict(True)
        return Verdict(None, frozenset({MENTIONS}))
    if isinstance(cond, OrganizationCondition):
        if any(o["id"] in cond.organization.ids for o in row.get("organizations") or []):
            return Verdict(True)
        if recall:
            return Verdict(None, frozenset({"organization"}))
        # Classification writes the MENTIONS edges; before it runs, no link is no answer.
        return _undecided(row)
    if isinstance(cond, LinkCondition):
        relation = cond.linked_to.relation
        return Verdict(any(
            link["id"] in cond.linked_to.ids and (relation is None or link.get("relation") == relation)
            for link in row.get("links") or []
        ))
    if isinstance(cond, EntityCondition):
        if recall and cond.entity.type in EDGE_ENTITY_TYPES:
            if cond.entity.type == "person":
                return _person(row, set(ROLE_OF_EDGE), {cond.entity.id}, recall=True)
            if any(o["id"] == cond.entity.id for o in row.get("organizations") or []):
                return Verdict(True)
            return Verdict(None, frozenset({cond.entity.type}))
        # Only ever top level (RecordQuery checks), where the provider applies it.
        # Edges to a classified entity exist only once classification has run, so
        # the provider also passes rows not classified yet and names, in
        # "entities", the classified entities a row does link to.
        if cond.entity.type not in CLASSIFIED_ENTITY_TYPES or "entities" not in row:
            return Verdict(True)
        if any(e["type"] == cond.entity.type and e["id"] == cond.entity.id for e in row["entities"]):
            return Verdict(True)
        return _undecided(row)
    if isinstance(cond, Not):
        inner = evaluate(cond.not_, row, recall=recall)
        return Verdict(None if inner.value is None else not inner.value, inner.unknown_fields)
    parts = [evaluate(c, row, recall=recall) for c in (cond.all_of if isinstance(cond, AllOf) else cond.any_of)]
    unknown = frozenset().union(*(p.unknown_fields for p in parts if p.value is None))
    decisive = False if isinstance(cond, AllOf) else True
    if any(p.value is decisive for p in parts):
        return Verdict(decisive)
    if unknown:
        return Verdict(None, unknown)
    return Verdict(not decisive)


# Set on a recall row: the exact ``RECORD_TYPE:EDGE_TYPE`` pairs its connector's
# records carry (``role_carriers.py``), absent when not known.
ROLE_CARRIERS = "roleCarriers"


def _person(row: Row, edges: set[str], ids: set[str], *, recall: bool) -> Verdict:
    in_role = [p for p in row.get("people") or [] if p["role"] in edges]
    if any(p["id"] in ids for p in in_role):
        return Verdict(True)
    if not recall or in_role:
        return Verdict(False)
    carried = row.get(ROLE_CARRIERS)
    if carried is not None and not any(f"{row.get('recordType')}:{edge}" in carried for edge in edges):
        # No record of this type from this connector ever has the role: a missed edge is impossible.
        return Verdict(False)
    return Verdict(None, frozenset({"person"}))


def evaluate_all(conditions: list[Condition], row: Row, *, recall: bool = False) -> Verdict:
    return evaluate(AllOf(all_of=conditions), row, recall=recall) if conditions else Verdict(True)


@dataclass
class Tally:
    """What a scan of admitted rows found, before paging."""

    matched: list[Row] = field(default_factory=list)
    unknown: Counter[str] = field(default_factory=Counter)
    # Rows left unknown only because they are not classified yet, newest first.
    not_classified: list[Row] = field(default_factory=list)


def tally(query: RecordQuery, rows: Iterable[Row], *, keep_not_classified: int = 0) -> Tally:
    out = Tally()
    for row in rows:
        verdict = evaluate_all(query.where, row)
        if verdict.value is True:
            out.matched.append(row)
        elif verdict.value is None:
            out.unknown.update(verdict.unknown_fields)
            if NOT_CLASSIFIED in verdict.unknown_fields and len(out.not_classified) < keep_not_classified:
                out.not_classified.append(row)
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
    return [(person_key(p.get("email"), p["id"]), "") for p in _people_in_role(by.person_role, row)]


def _people_in_role(role: str | None, row: Row) -> list[dict[str, Any]]:
    edges = set(ROLE_EDGES[role]) if role else set(ROLE_OF_EDGE)
    return [p for p in row.get("people") or [] if p["role"] in edges]


def groups(query: RecordQuery, rows: list[Row]) -> list[Group]:
    """Top groups by count; a row with two people in the role counts for both.
    People group by person (``person_identity``), not by node: the key is
    one of their ids, the label their name and email."""
    by = query.group_by
    if by is None:
        return []
    members: dict[tuple[str, str], list[Row]] = defaultdict(list)
    for row in rows:
        for key in dict.fromkeys(_group_keys(query, row)):
            members[key].append(row)
    if by.by_person or by.person_role is not None:
        people = {
            p.key: p for p in group_people(
                {"id": n["id"], "name": n.get("name"), "email": n.get("email")}
                for row in rows for n in _people_in_role(by.person_role, row)
            )
        }
        members = {(people[key].ids[0], people[key].label): rows_in for (key, _), rows_in in members.items()}
    ranked = sorted(members.items(), key=lambda kv: (-len(kv[1]), kv[0][1]))[: query.top]
    out = []
    for (key, label), rows_in in ranked:
        value, _ = aggregate_value(query, rows_in) if query.aggregate.op != "count" else (None, 0)
        out.append(Group(key=key, label=label, count=len(rows_in), value=value))
    return out


# Rows grouped by organisation that fall in no organisation, by why. Never
# merged into a real group: an unclassified record may yet name one.
NO_ORGANIZATION = "no_organization"
UNASSIGNED_LABELS = {
    NO_ORGANIZATION: "(no organization)",
    NOT_CLASSIFIED: "unknown (not classified yet)",
    CLASSIFICATION_FAILED: "unknown (classification failed)",
}


def unassigned(query: RecordQuery, rows: list[Row]) -> list[Group]:
    """Matched rows a classification-derived grouping (organisation) puts in no
    group: those not classified yet, those whose classification failed, and
    those classified with no organisation, so the groups and these add up."""
    if query.group_by is None or not query.group_by.organization:
        return []
    members: dict[str, list[Row]] = defaultdict(list)
    for row in rows:
        if row.get("organizations"):
            continue
        reason = (NOT_CLASSIFIED if classification_pending(row)
                  else CLASSIFICATION_FAILED if classification_failed(row) else NO_ORGANIZATION)
        members[reason].append(row)
    out = []
    for reason in UNASSIGNED_LABELS:
        if members.get(reason):
            value, _ = aggregate_value(query, members[reason]) if query.aggregate.op != "count" else (None, 0)
            out.append(Group(key=reason, label=UNASSIGNED_LABELS[reason], count=len(members[reason]), value=value))
    return out


__all__ = ["EDGE_ENTITY_TYPES", "CLASSIFICATION_FAILED", "MENTIONS", "NOT_CLASSIFIED", "NO_ORGANIZATION", "UNASSIGNED_LABELS", "unassigned", "ROLE_OF_EDGE", "Row", "Tally", "Verdict", "aggregate_value", "evaluate", "evaluate_all",
           "field_value", "groups", "tally"]
