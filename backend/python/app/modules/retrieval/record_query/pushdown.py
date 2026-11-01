"""The part of a query a provider applies while it pages candidates.

Only top-level conditions on indexed facts go down: source, record type,
container, created or updated window, a person in a role, an organisation, a
link, an entity. They narrow the scan; the evaluator then decides every
condition again on the returned rows, so pushing one down never changes an
answer, only how many candidates are read. Field conditions such as status
or amount stay in the evaluator, where unknown values are counted.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, TypedDict

from app.modules.retrieval.record_fields import role_edges
from app.modules.retrieval.record_query.model import (
    EntityCondition,
    FieldCondition,
    LinkCondition,
    OrganizationCondition,
    PersonCondition,
)

if TYPE_CHECKING:
    from app.modules.retrieval.record_query.model import RecordQuery


class PushedPeople(TypedDict):
    edges: list[str]
    ids: list[str]


class PushedLinks(TypedDict):
    ids: list[str]
    relation: str | None


class Pushdown(TypedDict, total=False):
    """Every key present must hold (AND); a list inside one key is any of it (OR).

    - ``sources``: ``Record.connectorName`` values.
    - ``record_types``: ``Record.recordType`` values.
    - ``containers``: record group ids; a record in a nested group matches.
    - ``created`` / ``updated``: ``[start_ms | None, end_ms | None]``, inclusive,
      on ``sourceCreatedAtTimestamp`` / ``sourceLastModifiedTimestamp``.
    - ``people``: each entry needs an edge of one of ``edges`` to one of ``ids``.
    - ``organizations``: each entry is a list of organisation ids, any of which;
      a record not classified yet also passes (classification writes MENTIONS).
    - ``links``: each entry needs a record link to one of ``ids`` (of
      ``relation`` when given), in either direction.
    - ``entities``: each ``{"type", "id"}`` must be connected as for
      ``get_entity_candidate_records``; for a type only classification links
      (``CLASSIFIED_ENTITY_TYPES``), a record not classified yet also passes.
    """

    sources: list[str]
    record_types: list[str]
    containers: list[str]
    created: list[int | None]
    updated: list[int | None]
    people: list[PushedPeople]
    organizations: list[list[str]]
    links: list[PushedLinks]
    entities: list[dict[str, str]]


_LIST_FIELDS = {"source": "sources", "record_type": "record_types", "container": "containers"}
_WINDOW_FIELDS = ("created", "updated")


def pushdown(query: RecordQuery) -> Pushdown:
    out: dict[str, Any] = {}
    for cond in query.where:
        if isinstance(cond, FieldCondition) and cond.field in _LIST_FIELDS and cond.in_:
            key = _LIST_FIELDS[cond.field]
            values = cond.in_ if cond.field == "container" else [v.upper() for v in cond.in_]
            # Two conditions on one field must both hold: keep what they share.
            out[key] = sorted(set(out[key]) & set(values)) if key in out else values
        elif isinstance(cond, FieldCondition) and cond.field in _WINDOW_FIELDS:
            start, end = cond.bounds_ms()
            if cond.field in out:
                # Both windows must hold: the later start and the earlier end.
                starts = [x for x in (start, out[cond.field][0]) if x is not None]
                ends = [x for x in (end, out[cond.field][1]) if x is not None]
                start, end = (max(starts) if starts else None), (min(ends) if ends else None)
            out[cond.field] = [start, end]
        elif isinstance(cond, PersonCondition):
            out.setdefault("people", []).append(
                PushedPeople(edges=list(role_edges(cond.person.role)), ids=list(cond.person.ids)),
            )
        elif isinstance(cond, OrganizationCondition):
            out.setdefault("organizations", []).append(list(cond.organization.ids))
        elif isinstance(cond, LinkCondition):
            out.setdefault("links", []).append(PushedLinks(ids=list(cond.linked_to.ids), relation=cond.linked_to.relation))
        elif isinstance(cond, EntityCondition):
            out.setdefault("entities", []).append({"type": cond.entity.type, "id": cond.entity.id})
    return Pushdown(**out)


def is_selective(pushed: Pushdown) -> bool:
    """Whether the scan is narrowed by something indexed beyond source and type."""
    return any(pushed.get(k) for k in ("containers", "created", "updated", "people", "organizations", "links",
                                        "entities"))


__all__ = ["PushedLinks", "PushedPeople", "Pushdown", "is_selective", "pushdown"]
