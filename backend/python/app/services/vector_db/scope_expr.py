"""The scope filter (:class:`~app.services.vector_db.filters.ScopeFilter`) as a store-neutral boolean tree over the fields a point carries,
for the stores whose points hold the scope fields themselves (Qdrant, OpenSearch, Redis). The rules are the ones the pgvector SQL applies to a
group's row (`pgvector/scope_filter.py`); each store translates this tree, and :func:`evaluate` is the reference a translation is tested against.

A point is admitted only when ALL hold; everything else, including a missing field, is **not** admitted (fail closed):

* its field version and scope generation are the ones the filter names (a negative generation, or no current connector, trusts nothing);
* it has at least one connector and every connector is in ``current`` (:class:`Within`);
* by its stamp: ``container`` and a record group, root group or connector the viewer reads; any of ``container`` / ``union`` / ``intersection`` /
  ``override`` and ``aclScope`` meets ``reach``; ``union`` / ``intersection`` and ``pathIds`` meets ``reach`` and does not meet ``excluded``;
* whatever the stamp, ``pathIds`` does not meet ``pending``, and a ``container`` point does not belong to a ``blocked`` connector.

An empty list matches nothing (:func:`has` of no values is FALSE), never everything.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from app.services.permissions.scope_fields import (
    ACL_SCOPE_FIELD,
    PATH_IDS_FIELD,
    SCOPE_GENERATION_FIELD,
    SCOPE_STAMP_FIELD,
    SCOPE_VERSION_FIELD,
)
from app.services.vector_db.const.const import (
    CONNECTOR_IDS_FIELD,
    RECORD_GROUP_IDS_FIELD,
    ROOT_RECORD_GROUP_IDS_FIELD,
)

if TYPE_CHECKING:
    from collections.abc import Iterable

    from app.services.vector_db.filters import ScopeFilter

#: Stamps that carry a scope id of the record itself, and the stamps whose path decides.
OWN_ID_STAMPS = ("container", "union", "intersection", "override")
PATH_STAMPS = ("union", "intersection")


@dataclass(frozen=True)
class Has:
    """The point's field holds any of ``values`` (an array field) or equals one (a scalar one)."""

    field: str
    values: tuple[str, ...]


@dataclass(frozen=True)
class Is:
    """The point's integer field equals ``value``."""

    field: str
    value: int


@dataclass(frozen=True)
class Within:
    """The point's array field holds at least one value and every value is in ``values``."""

    field: str
    values: tuple[str, ...]


@dataclass(frozen=True)
class All:
    parts: tuple[Node, ...]


@dataclass(frozen=True)
class Some:
    parts: tuple[Node, ...]


@dataclass(frozen=True)
class Not:
    part: Node


Node = Has | Is | Within | All | Some | Not
TRUE: Node = All(())
FALSE: Node = Some(())


def has(field: str, values: Iterable[str]) -> Node:
    vals = tuple(sorted(set(values)))
    return Has(field, vals) if vals else FALSE


def all_of(*parts: Node) -> Node:
    if any(p == FALSE for p in parts):
        return FALSE
    kept = tuple(p for p in parts if p != TRUE)
    return kept[0] if len(kept) == 1 else All(kept)


def some_of(*parts: Node) -> Node:
    if any(p == TRUE for p in parts):
        return TRUE
    kept = tuple(p for p in parts if p != FALSE)
    return kept[0] if len(kept) == 1 else Some(kept)


def not_(part: Node) -> Node:
    return FALSE if part == TRUE else TRUE if part == FALSE else part.part if isinstance(part, Not) else Not(part)


def admits(flt: ScopeFilter) -> Node:
    """The tree of the points ``flt`` admits (FALSE for a filter that can trust nothing)."""
    if flt.trusts_nothing:
        return FALSE
    # a subtree being rewritten is hidden whatever the stamp; a connector with a pending root is not read from its container stamps
    not_pending = not_(has(PATH_IDS_FIELD, flt.pending))
    not_blocked = not_(has(CONNECTOR_IDS_FIELD, flt.blocked))
    return all_of(
        Is(SCOPE_VERSION_FIELD, flt.version), Is(SCOPE_GENERATION_FIELD, flt.generation),
        Within(CONNECTOR_IDS_FIELD, tuple(sorted(flt.current))), not_pending,
        some_of(
            all_of(has(SCOPE_STAMP_FIELD, ("container",)), not_blocked,
                   some_of(has(RECORD_GROUP_IDS_FIELD, flt.readable_groups), has(ROOT_RECORD_GROUP_IDS_FIELD, flt.readable_groups),
                           has(CONNECTOR_IDS_FIELD, flt.open_apps))),
            all_of(has(SCOPE_STAMP_FIELD, OWN_ID_STAMPS), has(ACL_SCOPE_FIELD, flt.reach),
                   some_of(not_(has(SCOPE_STAMP_FIELD, ("container",))), not_blocked)),
            all_of(has(SCOPE_STAMP_FIELD, PATH_STAMPS), has(PATH_IDS_FIELD, flt.reach), not_(has(PATH_IDS_FIELD, flt.excluded)))))


def _values(payload: dict[str, Any], field: str) -> list[Any]:
    got = payload.get(field)
    return [] if got is None else list(got) if isinstance(got, (list, tuple)) else [got]


def evaluate(node: Node, payload: dict[str, Any]) -> bool:
    """Whether ``payload`` (a point's top-level payload) satisfies ``node``; the reference semantics of every translation."""
    if isinstance(node, Has):
        return bool(set(map(str, _values(payload, node.field))) & set(node.values))
    if isinstance(node, Is):
        got = payload.get(node.field)
        return isinstance(got, int) and not isinstance(got, bool) and got == node.value
    if isinstance(node, Within):
        got = set(map(str, _values(payload, node.field)))
        return bool(got) and got <= set(node.values)
    if isinstance(node, All):
        return all(evaluate(p, payload) for p in node.parts)
    if isinstance(node, Some):
        return any(evaluate(p, payload) for p in node.parts)
    return not evaluate(node.part, payload)


#: The most ids one list may hold in a query, per store (Qdrant and OpenSearch bind them as a list, Redis writes them into the query text; OpenSearch
#: refuses a ``terms`` list longer than 65,536). Past it the filter is refused (`ScopeFilterTooLarge`), never truncated: a cut list would admit less
#: for a positive list and more for ``excluded``.
MAX_IDS = {"Qdrant": 50_000, "OpenSearch": 60_000, "Redis": 20_000}


def check_size(flt: ScopeFilter, store: str) -> None:
    from app.services.vector_db.filters import ScopeFilterTooLarge

    limit = MAX_IDS[store]
    for name in ("current", "open_apps", "readable_groups", "readable_scopes", "excluded", "pending", "blocked", "reach"):
        if len(getattr(flt, name)) > limit:
            raise ScopeFilterTooLarge(f"{store}: the filter's {name} list holds more than {limit} ids")
