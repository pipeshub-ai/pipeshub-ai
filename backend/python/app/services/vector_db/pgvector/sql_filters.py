"""``FilterExpression`` to SQL: a boolean fragment over the aliases ``p`` (points), ``g`` (point_groups) and ``pt`` (point_text).

Pure: values are only ever bound (``$n``), never inlined. The org and collection predicates are the caller's (``$1``, ``$2``);
this module compiles everything else, and reports which joins the fragment needs.

| key                                              | SQL                                                          |
| ------------------------------------------------ | ------------------------------------------------------------ |
| metadata.orgId                                   | ``p.org_id`` (the caller already pins it; ANDed again)       |
| metadata.virtualRecordId / blockId               | ``p.vrid`` / ``p.block_id``                                  |
| metadata.entityType / entityId / kind / level    | ``p.ent_type`` / ``ent_id`` / ``ent_kind`` / ``ent_level``   |
| connectorIds / recordGroupIds / rootRecordGroupIds / pathIds / aclScope | an EXISTS on ``group_members`` (PK-prefix probe) |
| scopeStamp / scopeFieldsVersion / scopeGeneration | an EXISTS on ``point_groups`` (PK probe)                    |
| values_count_lte on a membership array           | ``cardinality(coalesce(g.<array>, '{}')) <= n`` (absent = holds) |
| any other payload key                            | ``pt.payload #>> '{a,b}'`` on ``point_text``                 |
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from app.services.vector_db.pgvector.errors import PgVectorUnsupportedError

if TYPE_CHECKING:
    from app.services.vector_db.models import FieldCondition, FilterExpression

PROMOTED = {
    "metadata.orgId": "p.org_id",
    "metadata.virtualRecordId": "p.vrid",
    "metadata.blockId": "p.block_id",
    "metadata.entityType": "p.ent_type",
    "metadata.entityId": "p.ent_id",
    "metadata.kind": "p.ent_kind",
    "metadata.level": "p.ent_level",
}
# group_members.kind and the point_groups column of each membership array
MEMBERSHIP = {
    "connectorIds": (1, "connector_ids"),
    "recordGroupIds": (2, "record_group_ids"),
    "rootRecordGroupIds": (3, "root_record_group_ids"),
    # the permission scope lists of a group (7a): members of kinds 4 (path) and 5 (override scope)
    "pathIds": (4, "path_ids"),
    "aclScope": (5, "acl_scope"),
}
# Scope conditions select the stored stamps as data (a repair or back-fill job must be able to find stale or future ones): they are not gated by trust.
# Visibility is decided only by the search scope filter (scope_filter.py), which checks trust; a search refuses these keys (visibility.scope_of).
# the scalar scope fields of a group: point_groups column. They are stored on the group, not in the payload, so a payload lookup would never find them.
SCOPE_LISTS = frozenset({"pathIds", "aclScope"})
SCOPE_COLUMNS = {"scopeStamp": "scope_stamp", "scopeFieldsVersion": "scope_version", "scopeGeneration": "scope_generation"}


@dataclass
class Compiled:
    where: str = "true"
    params: list[Any] = field(default_factory=list)
    needs_group: bool = False
    needs_text: bool = False


def as_text(value: object) -> str:
    """Payload values compare as text: ``True`` is ``'true'`` (what ``#>>`` gives for a JSON boolean)."""
    return ("true" if value else "false") if isinstance(value, bool) else str(value)


def values_of(cond: FieldCondition) -> list[str] | None:
    """The values a condition matches on, as text; None when it only bounds a count."""
    if cond.values is not None:
        return [as_text(v) for v in cond.values]
    if cond.value is None:
        return None
    return [as_text(v) for v in cond.value] if isinstance(cond.value, list) else [as_text(cond.value)]


class _Builder:
    def __init__(self, first_param: int, org_param: int, coll_param: int) -> None:
        self.n = first_param - 1
        self.org, self.coll = org_param, coll_param
        self.params: list[Any] = []
        self.needs_group = False
        self.needs_text = False

    def bind(self, value: Any, cast: str) -> str:  # noqa: ANN401
        self.params.append(value)
        self.n += 1
        return f"${self.n}::{cast}"

    def condition(self, cond: FieldCondition, clause: str) -> str:
        parts: list[str] = []
        vals = values_of(cond)
        if vals is not None:
            parts.append(self._match(cond.key, vals))
        if cond.values_count_lte is not None:
            if clause != "must":
                raise ValueError(
                    f"values_count_lte on '{cond.key}' is only supported in the MUST clause, not {clause.upper()}"
                )
            parts.append(self._count(cond.key, cond.values_count_lte))
        if not parts:
            raise ValueError(f"filter condition on '{cond.key}' has neither a value nor a count bound")
        return " AND ".join(parts)

    def _match(self, key: str, vals: list[str]) -> str:
        if not vals:                     # an empty IN-list matches nothing
            return "false"
        if key in PROMOTED:
            return f"{PROMOTED[key]} = ANY({self.bind(vals, 'text[]')})"
        if key in MEMBERSHIP:
            kind, _ = MEMBERSHIP[key]
            # a probe on the primary key prefix (org, collection, group): one row per matching member
            return (
                "EXISTS (SELECT 1 FROM retrieval.group_members m "
                f"WHERE m.org_id = ${self.org} AND m.collection_id = ${self.coll} AND m.group_key = p.group_key "
                f"AND m.kind = {kind} AND m.member_id = ANY({self.bind(vals, 'text[]')}))"
            )
        if key in SCOPE_COLUMNS:
            # a probe of the group's row by primary key (no join, so it holds in every statement); NULL (never stamped) matches nothing
            return (
                "EXISTS (SELECT 1 FROM retrieval.point_groups sg "
                f"WHERE sg.org_id = ${self.org} AND sg.collection_id = ${self.coll} AND sg.group_key = p.group_key "
                f"AND sg.{SCOPE_COLUMNS[key]}::text = ANY({self.bind(vals, 'text[]')}))"
            )
        self.needs_text = True
        path = self.bind(key.split("."), "text[]")
        return f"pt.payload #>> {path} = ANY({self.bind(vals, 'text[]')})"

    def _count(self, key: str, bound: int) -> str:
        if key not in MEMBERSHIP:
            raise ValueError(f"values_count_lte is only defined for the membership arrays, not '{key}'")
        self.needs_group = True
        column = MEMBERSHIP[key][1]
        count = f"cardinality(coalesce(g.{column}, '{{}}')) <= {self.bind(int(bound), 'int')}"
        if key in SCOPE_LISTS:
            # a scope list is empty only in a group that was stamped; NULL means never stamped, which is not "no entries"
            return f"(g.scope_version IS NOT NULL AND {count})"
        # an absent group row holds the bound, like an absent array on a point
        return count


def compile_filter(expr: FilterExpression, *, first_param: int, org_param: int = 1, coll_param: int = 2) -> Compiled:
    """``must`` ANDs, ``should`` ORs (at least one when present), ``must_not`` negates each condition. A ``min_should_match`` other than
    1 is refused: the SQL cannot honour it, and ignoring it would match points the caller excluded."""
    if expr.min_should_match not in (None, 1):
        raise PgVectorUnsupportedError("a filter takes any-of SHOULD conditions only (min_should_match is not supported)")
    b = _Builder(first_param, org_param, coll_param)
    parts = [f"({b.condition(c, 'must')})" for c in expr.must]
    if expr.should:
        parts.append("(" + " OR ".join(f"({b.condition(c, 'should')})" for c in expr.should) + ")")
    # coalesce: a point without the field is not matched by the condition, so must_not keeps it (as the other backends do)
    parts += [f"NOT coalesce(({b.condition(c, 'must_not')}), false)" for c in expr.must_not]
    return Compiled(" AND ".join(parts) or "true", b.params, b.needs_group, b.needs_text)


def single_org(expr: FilterExpression) -> str | None:
    """The one org the filter pins in MUST (``metadata.orgId``), or None when there is none or it names several."""
    found: list[str] = []
    for cond in expr.must:
        if cond.key == "metadata.orgId":
            vals = values_of(cond)
            if vals is None or len(vals) != 1:
                return None
            found.append(vals[0])
    return found[0] if len(set(found)) == 1 else None
