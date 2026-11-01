"""One person, however many graph nodes name them.

A connector writes one ``users`` node per account and record_people one
``person`` node per outsider, so a human can be several nodes. Nodes sharing
an email (case-insensitive) are one person, as ``find_users`` and
``find_records_by_person`` already treat them; a node without an email is a
person on its own. Every lookup is org-scoped by the provider: an id from
another org, or no node at all, resolves to nothing, never to its owner.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Iterable

    from app.services.graph_db.interface.graph_db_provider import IGraphDBProvider

logger = logging.getLogger(__name__)

# Nodes one lookup returns: a handful per email in practice, so this only
# bounds a caller that names hundreds of people.
MAX_PERSON_NODES = 200


def person_key(email: str | None, node_id: str) -> str:
    """What makes two nodes one person: their email, else the node itself."""
    address = (email or "").strip().casefold()
    return address or f"id:{node_id}"


@dataclass(frozen=True)
class Person:
    key: str
    ids: tuple[str, ...]
    name: str
    email: str

    @property
    def label(self) -> str:
        """Readable: the name with the email that tells namesakes apart."""
        if self.name and self.email:
            return f"{self.name} ({self.email})"
        return self.name or self.email or self.ids[0]


def group_people(nodes: Iterable[dict[str, Any]]) -> list[Person]:
    """``{"id", "name", "email"}`` nodes as people, in first-seen order; a
    person's ids sorted, its name and email the first non-blank ones in id
    order, so the label does not depend on which node was seen first."""
    grouped: dict[str, list[dict[str, Any]]] = {}
    for node in nodes:
        node_id = str(node.get("id") or "")
        if node_id:
            grouped.setdefault(person_key(node.get("email"), node_id), []).append(node)
    people = []
    for key, found in grouped.items():
        members = sorted(found, key=lambda m: str(m["id"]))
        people.append(Person(
            key=key,
            ids=tuple(sorted({str(m["id"]) for m in members})),
            name=next((n.strip() for m in members if (n := m.get("name") or "") and n.strip()), ""),
            email=next((e.strip() for m in members if (e := m.get("email") or "") and e.strip()), ""),
        ))
    return people


async def people_of(graph: IGraphDBProvider, org_id: str, ids: Iterable[str]) -> list[Person]:
    """The people ``ids`` name in ``org_id``, each with every node of theirs."""
    asked = list(dict.fromkeys(str(i) for i in ids if i))
    if not asked or not org_id:
        return []
    nodes = await graph.get_person_nodes(org_id, asked, limit=MAX_PERSON_NODES)
    people = group_people(nodes)
    logger.debug(
        "person identity org=%s asked=%d nodes=%d people=%d",
        org_id, len(asked), len(nodes), len(people),
    )
    return people


async def expand_person_ids(graph: IGraphDBProvider, org_id: str, ids: Iterable[str]) -> list[str]:
    """``ids`` and every other node id of the same people, ``ids`` first.
    An id that names no node of the org is kept as given: it matches nothing."""
    asked = list(dict.fromkeys(str(i) for i in ids if i))
    people = await people_of(graph, org_id, asked)
    return list(dict.fromkeys([*asked, *(i for p in people for i in p.ids)]))


__all__ = [
    "MAX_PERSON_NODES",
    "Person",
    "expand_person_ids",
    "group_people",
    "people_of",
    "person_key",
]
