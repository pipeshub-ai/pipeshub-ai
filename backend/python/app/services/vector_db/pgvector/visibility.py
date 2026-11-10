"""Which points a search may return: the membership filter, parsed once and used by both legs. (Not ``vector_db/membership.py``, which
builds the membership arrays a point is written with.)

``scope_of`` turns a ``FilterExpression`` into a :class:`Scope`. The lexical leg passes its arrays to ``retrieval.search_sparse``; the
dense leg gets the SQL fragments of the same scope from :meth:`Scope.candidates` (exact tiers: the groups first) and
:meth:`Scope.probe` (the vector index: one probe per row). Every rule about who may see what lives here, so a change to it is a
change to this file and to the vetted function, nowhere else.

SQL parameter numbers are fixed so the fragments can be dropped into any statement: ``$1`` org, ``$2`` collection, and ``$5`` apps,
``$6`` groups, ``$7`` roots, ``$8`` direct records, ``$9`` the tool's narrowing list (``$3`` is the space and ``$4`` the query vector
of the dense statements, ``$10`` the number of rows and ``$11`` the rerank depth). Conditions on the promoted point columns (entity type, kind,
level, block, ...) are ``extra``: the dense leg binds them from ``$12``; the lexical function cannot apply them, so the lexical leg asks it for
more rows and keeps those that satisfy them (``sparse.leg``).
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING

from app.services.vector_db.models import FilterExpression
from app.services.vector_db.pgvector import scope_filter as scope_sql
from app.services.vector_db.pgvector.errors import PgVectorUnsupportedError
from app.services.vector_db.pgvector.sql_filters import (
    PROMOTED,
    compile_filter,
    values_of,
)

if TYPE_CHECKING:
    from app.services.vector_db.filters import ScopeFilter
    from app.services.vector_db.models import FieldCondition

# the membership arrays a search arm may name (the scope lists are not arms: a search that took one would drop it and widen)
_ARM_KEYS = frozenset({"connectorIds", "recordGroupIds", "rootRecordGroupIds"})
_VRID = "metadata.virtualRecordId"
PER_ROW_PROBE = True      # False only to measure the hashed form (benchmarks)
_GM = "FROM retrieval.group_members m WHERE m.org_id = $1 AND m.collection_id = $2"


@dataclass(frozen=True)
class Scope:
    """What a search filters on. All four membership arms ``None``: the whole org. An arm that is an empty list matches nothing."""

    apps: list[str] | None = None
    groups: list[str] | None = None
    roots: list[str] | None = None
    direct: list[str] | None = None
    must_vrids: list[str] | None = None
    extra: FilterExpression | None = None      # conditions on promoted point columns, for the dense leg
    #: What the viewer may read by the stamps of the groups (see ``scope_filter``): ANDed with everything above, after the extra conditions.
    scope_filter: ScopeFilter | None = None

    @property
    def whole_org(self) -> bool:
        return self.apps is None and self.groups is None and self.roots is None and self.direct is None

    def args(self) -> tuple[list[str] | None, ...]:
        """The values of ``$5`` to ``$9``."""
        return self.apps, self.groups, self.roots, self.direct, self.must_vrids

    def key(self) -> str:
        """A cache key: a digest of a canonical encoding, equal for equal scopes (lists and extra conditions) and, unlike ``hash()``, the
        same in every process and without collisions to speak of."""
        flt = None if self.scope_filter is None else [*self.scope_filter.lists(), self.scope_filter.version, self.scope_filter.generation]
        canon = json.dumps([self.args(), repr(self.extra), flt], separators=(",", ":"))
        return hashlib.sha256(canon.encode()).hexdigest()

    @property
    def filtered(self) -> bool:
        """Whether conditions beyond the membership arms apply to the rows (the lexical function cannot apply them: see ``lexical``)."""
        return self.extra is not None or self.scope_filter is not None

    @property
    def matches_nothing(self) -> bool:
        """Whether the scope filter can trust nothing, so every statement renders ``AND false``: the answer is empty and nothing needs asking."""
        return self.scope_filter is not None and self.scope_filter.trusts_nothing

    def extra_sql(self, first_param: int = 12) -> tuple[str, list]:
        """``(" AND (...)", parameters from first_param)`` for the extra conditions and the scope filter, or ``("", [])``."""
        sql, params = "", []
        if self.extra is not None:
            c = compile_filter(self.extra, first_param=first_param)
            sql, params = f" AND ({c.where})", c.params
        if self.scope_filter is not None:
            more, bound = scope_sql.predicate(self.scope_filter, first_param + len(params))
            sql, params = sql + more, [*params, *bound]
        return sql, params

    def narrowed_to(self, org_connectors: frozenset[str] | None) -> Scope:
        """The arms are dropped when ``apps`` lists every connector of the org: the arms are alternatives, so the connector arm alone
        admits every record that has a connector membership row. The result is a superset of what the arms admit (a record whose
        membership is not written yet is outside the arms and inside the whole org); the graph check after the search removes the points
        the user may not see. The caller of an all-apps user would otherwise pay a membership probe per row for nothing."""
        if not self.apps or not org_connectors or not org_connectors <= set(self.apps):      # no connector, or none the user reads: keep the arms
            return self
        return replace(self, apps=None, groups=None, roots=None, direct=None)

    def must(self) -> str:
        return "" if self.must_vrids is None else " AND p.group_key = ANY($9::text[])"

    def tail(self) -> str:
        """Everything a statement adds to its WHERE besides org, collection, space and the membership arms."""
        return self.must() + self.extra_sql()[0]

    def candidates(self) -> str:
        """For the exact tiers: ``AND p.group_key IN (the groups the arms admit)`` plus the narrowing list. Cheap when the arms are
        narrow (index probes), which is when this form is used."""
        if self.whole_org:
            return self.tail()
        arms = (f"SELECT m.group_key {_GM} AND m.kind = 1 AND m.member_id = ANY($5::text[]) "
                f"UNION SELECT m.group_key {_GM} AND m.kind = 2 AND m.member_id = ANY($6::text[]) "
                f"UNION SELECT m.group_key {_GM} AND m.kind = 3 AND m.member_id = ANY($7::text[]) "
                "UNION SELECT unnest($8::text[])")
        return f" AND p.group_key IN ({arms}){self.tail()}"

    def probe(self) -> str:
        """For the vector index: ``AND EXISTS (...)`` checked on each row the graph walk returns (a primary-key prefix probe)."""
        if self.whole_org:
            return self.tail()
        arms = ("(m.kind = 1 AND m.member_id = ANY($5::text[])) OR (m.kind = 2 AND m.member_id = ANY($6::text[])) "
                "OR (m.kind = 3 AND m.member_id = ANY($7::text[]))")
        # OFFSET 0 keeps the planner from turning the EXISTS into a hashed subplan that reads every membership row the arms admit once per
        # statement (57,640 rows, 7+ ms, for a user who reaches a big share of an org where each record is one chunk): the index tier asks
        # for a few hundred rows, so a primary-key probe per row it returns costs less, and its cost does not grow with the user's reach.
        barrier = " OFFSET 0" if PER_ROW_PROBE else ""
        return (f" AND (p.group_key = ANY($8::text[]) OR EXISTS (SELECT 1 {_GM} AND m.group_key = p.group_key AND ({arms}){barrier}))"
                f"{self.tail()}")


def scope_of(expr: FilterExpression, scope_filter: ScopeFilter | None = None) -> Scope:
    """The scope of ``expr``: ``metadata.orgId`` (the caller pins the org), ``virtualRecordId`` in MUST (a narrowing list), in SHOULD the
    arms ``connectorIds``, ``recordGroupIds``, ``rootRecordGroupIds`` and ``virtualRecordId``, and other conditions on promoted point
    columns (``extra``). Anything else cannot be expressed by a search and raises (it would otherwise be dropped and widen the search)."""
    if expr.min_should_match not in (None, 1):
        raise PgVectorUnsupportedError("a vector search takes any-of SHOULD conditions only (min_should_match is not supported)")
    must_vrids: list[str] | None = None
    extra_must: list[FieldCondition] = []
    for cond in expr.must:
        vals = values_of(cond)
        if cond.key == "metadata.orgId" and cond.values_count_lte is None:
            continue
        if cond.key == _VRID and vals is not None and cond.values_count_lte is None:
            must_vrids = vals if must_vrids is None else [v for v in must_vrids if v in set(vals)]
        elif cond.key in PROMOTED and cond.values_count_lte is None:
            extra_must.append(cond)
        else:
            raise PgVectorUnsupportedError(f"a vector search cannot filter on '{cond.key}' in MUST")
    if any(c.key not in PROMOTED or c.values_count_lte is not None for c in expr.must_not):
        raise PgVectorUnsupportedError("a vector search takes must_not conditions on point columns only")
    arms: dict[str, list[str]] = {}
    for cond in expr.should:
        vals = values_of(cond)
        if (cond.key != _VRID and cond.key not in _ARM_KEYS) or vals is None or cond.values_count_lte is not None:
            raise PgVectorUnsupportedError(f"a vector search cannot filter on '{cond.key}' in SHOULD")
        arms.setdefault(cond.key, []).extend(vals)
    extra = FilterExpression(must=extra_must, must_not=list(expr.must_not)) if extra_must or expr.must_not else None
    if not arms:
        return Scope(must_vrids=must_vrids, extra=extra, scope_filter=scope_filter)
    return Scope(arms.get("connectorIds", []), arms.get("recordGroupIds", []), arms.get("rootRecordGroupIds", []),
                 arms.get(_VRID, []), must_vrids, extra, scope_filter)
