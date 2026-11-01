"""The typed record query and its result. See ``docs/record-query.md``.

A query is plain JSON an agent can write: ``where`` is a list of conditions
(all must hold), each condition one of the shapes below; ``aggregate``,
``group_by`` and ``describe`` say what to compute. Every field name and value
kind is checked against ``record_fields`` when the query is built, so a
malformed query fails with a message naming what exists, before any database
work.
"""

from __future__ import annotations

import datetime as dt
from typing import Any, Literal, Union

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.modules.retrieval.record_fields import FIELDS, FieldKind, canonical_role, field
from app.modules.retrieval.record_query.paging import NEWEST, OLDEST, decode_cursor

MAX_ROWS = 50
MAX_GROUPS = 20
MAX_DESCRIBED_VALUES = 20
_RANGE_KINDS = (FieldKind.DATE, FieldKind.NUMBER)
_LIST_KINDS = (FieldKind.VALUE, FieldKind.TEXT, FieldKind.RECORD)
_DAY_MS = 86_400_000


def day_start_ms(value: str) -> int:
    day = dt.date.fromisoformat(value[:10])
    return int(dt.datetime.combine(day, dt.time(), tzinfo=dt.UTC).timestamp() * 1000)


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class FieldCondition(_Strict):
    """``{"field": "status", "in": ["OPEN"]}`` or ``{"field": "created", "from": "2026-03-01", "to": ...}``.
    Values of a value field match case-insensitively; date bounds are whole
    UTC days, both inclusive; number bounds are inclusive."""

    field: str
    in_: list[str] | None = Field(default=None, alias="in")
    from_: str | float | None = Field(default=None, alias="from")
    to: str | float | None = None

    @model_validator(mode="after")
    def _matches_kind(self) -> FieldCondition:
        spec = field(self.field)
        if spec.kind in _LIST_KINDS:
            if not self.in_ or self.from_ is not None or self.to is not None:
                raise ValueError(f"{self.field} takes \"in\": [values]")
            if spec.values:
                unknown = {v for v in self.in_ if v.upper() not in spec.values}
                if unknown:
                    raise ValueError(f"{self.field} has no value {sorted(unknown)}; values: {', '.join(spec.values)}")
        elif spec.kind in _RANGE_KINDS:
            if self.in_ is not None or (self.from_ is None and self.to is None):
                raise ValueError(f"{self.field} takes \"from\" and/or \"to\"")
            if spec.kind is FieldKind.DATE:
                for bound in (self.from_, self.to):
                    if bound is not None:
                        day_start_ms(str(bound))
        else:
            raise ValueError(f"{self.field} is matched with its own condition, not \"field\"")
        return self

    def bounds_ms(self) -> tuple[int | None, int | None]:
        """A date range as epoch ms, the end at the last millisecond of its day."""
        start = day_start_ms(str(self.from_)) if self.from_ is not None else None
        end = day_start_ms(str(self.to)) + _DAY_MS - 1 if self.to is not None else None
        return start, end


class _People(_Strict):
    role: str | None = None
    ids: list[str] = Field(min_length=1)

    @field_validator("role")
    @classmethod
    def _known_role(cls, role: str | None) -> str | None:
        return None if role is None else canonical_role(role)


class PersonCondition(_Strict):
    """A person (an id ``search_entities`` returned) in a role; no role means any."""

    person: _People


class _Ids(_Strict):
    ids: list[str] = Field(min_length=1)


class OrganizationCondition(_Strict):
    organization: _Ids


class _Links(_Strict):
    ids: list[str] = Field(min_length=1)
    relation: str | None = None


class LinkCondition(_Strict):
    linked_to: _Links


class _Entity(_Strict):
    type: str
    id: str


class EntityCondition(_Strict):
    """Any entity ``find_records_by_entity`` takes: a topic, a record group, ..."""

    entity: _Entity


class AllOf(_Strict):
    all_of: list[Condition] = Field(min_length=1)


class AnyOf(_Strict):
    any_of: list[Condition] = Field(min_length=1)


class Not(_Strict):
    not_: Condition = Field(alias="not")


Condition = Union[  # noqa: UP007 - pydantic resolves the recursive union by name
    FieldCondition, PersonCondition, OrganizationCondition, LinkCondition, EntityCondition, AllOf, AnyOf, Not,
]


class Aggregate(_Strict):
    op: Literal["count", "sum", "min", "max", "avg"] = "count"
    field: str | None = None

    @model_validator(mode="after")
    def _numeric(self) -> Aggregate:
        if self.op == "count":
            if self.field is not None:
                raise ValueError("count takes no field")
        elif self.field is None or field(self.field).kind not in _RANGE_KINDS:
            numeric = sorted(n for n, f in FIELDS.items() if f.kind is FieldKind.NUMBER)
            raise ValueError(f"{self.op} needs a number field: {', '.join(numeric)}")
        return self


class GroupBy(_Strict):
    """Exactly one of: a value field, people in a role, organisations, or the month of a date field."""

    field: str | None = None
    person_role: str | None = None
    by_person: bool = False
    organization: bool = False
    month: str | None = None

    @field_validator("person_role")
    @classmethod
    def _known_role(cls, role: str | None) -> str | None:
        return None if role is None else canonical_role(role)

    @model_validator(mode="after")
    def _one(self) -> GroupBy:
        chosen = [self.field is not None, self.by_person or self.person_role is not None, self.organization,
                  self.month is not None]
        if sum(chosen) != 1:
            raise ValueError("group_by takes exactly one of field, person_role/by_person, organization, month")
        if self.field is not None and field(self.field).kind not in (FieldKind.VALUE, FieldKind.TEXT):
            raise ValueError(f"group by {self.field} is not possible; group by a value field or use month")
        if self.month is not None and field(self.month).kind is not FieldKind.DATE:
            raise ValueError(f"month needs a date field, not {self.month}")
        return self


def walk_conditions(conditions: list[Condition]) -> list[Condition]:
    """Every condition, nested ones included, parents first."""
    out: list[Condition] = []
    for cond in conditions:
        out.append(cond)
        if isinstance(cond, AllOf):
            out += walk_conditions(cond.all_of)
        elif isinstance(cond, AnyOf):
            out += walk_conditions(cond.any_of)
        elif isinstance(cond, Not):
            out += walk_conditions([cond.not_])
    return out


def _is_container(cond: object) -> bool:
    return isinstance(cond, FieldCondition) and cond.field == "container"


class RecordQuery(_Strict):
    where: list[Condition] = Field(default_factory=list)
    aggregate: Aggregate = Field(default_factory=Aggregate)
    group_by: GroupBy | None = None
    top: int = Field(default=10, ge=1, le=MAX_GROUPS)
    limit: int = Field(default=20, ge=0, le=MAX_ROWS)
    sort: str = NEWEST
    cursor: str | None = None
    describe: list[str] = Field(default_factory=list)

    @field_validator("sort")
    @classmethod
    def _sortable(cls, sort: str) -> str:
        """``newest``, ``oldest`` or ``"<date or number field> asc|desc"``; one key only."""
        sort = " ".join(sort.lower().split())
        if sort in (NEWEST, OLDEST):
            return sort
        name, _, direction = sort.partition(" ")
        if direction not in ("asc", "desc") or name not in FIELDS or FIELDS[name].kind not in _RANGE_KINDS:
            sortable = sorted(n for n, f in FIELDS.items() if f.kind in _RANGE_KINDS)
            raise ValueError(f"sort is newest, oldest, or \"<field> asc\" / \"<field> desc\" with one of "
                             f"{', '.join(sortable)}")
        return sort

    @model_validator(mode="after")
    def _entities_on_top(self) -> RecordQuery:
        def nested(cond: Condition) -> bool:
            if isinstance(cond, EntityCondition) or _is_container(cond):
                return True
            if isinstance(cond, (AllOf, AnyOf)):
                return any(nested(c) for c in (cond.all_of if isinstance(cond, AllOf) else cond.any_of))
            return isinstance(cond, Not) and nested(cond.not_)

        if any(nested(c) for c in self.where if not (isinstance(c, EntityCondition) or _is_container(c))):
            raise ValueError(
                "an entity or container condition goes in the top-level where list, not inside all_of, any_of or not"
            )
        return self

    @model_validator(mode="after")
    def _cursor_fits(self) -> RecordQuery:
        if self.cursor:
            decode_cursor(self.sort, self.cursor)
        return self

    @field_validator("describe")
    @classmethod
    def _describable(cls, names: list[str]) -> list[str]:
        for name in names:
            if field(name).kind not in (FieldKind.VALUE, FieldKind.TEXT):
                raise ValueError(f"describe lists values of value fields; {name} is {field(name).kind.value}")
        return names


class Group(BaseModel):
    key: str
    label: str
    count: int
    value: float | None = None


class RecordQueryResult(BaseModel):
    total: int
    approximate: bool = False
    value: float | None = None
    groups: list[Group] = Field(default_factory=list)
    unknown: dict[str, int] = Field(default_factory=dict)
    described: dict[str, list[Group]] = Field(default_factory=dict)
    rows: list[dict[str, Any]] = Field(default_factory=list)
    # Where ``rows`` starts among the matches, for "records 21-40 of 57".
    offset: int = 0
    next_cursor: str | None = None


for _model in (AllOf, AnyOf, Not, RecordQuery):
    _model.model_rebuild()


__all__ = [
    "MAX_DESCRIBED_VALUES",
    "MAX_GROUPS",
    "MAX_ROWS",
    "Aggregate",
    "AllOf",
    "AnyOf",
    "Condition",
    "EntityCondition",
    "FieldCondition",
    "Group",
    "GroupBy",
    "LinkCondition",
    "Not",
    "OrganizationCondition",
    "PersonCondition",
    "RecordQuery",
    "RecordQueryResult",
    "day_start_ms",
    "walk_conditions",
]
