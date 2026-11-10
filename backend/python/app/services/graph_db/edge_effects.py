"""Which nodes gained an edge from a write, as the database itself reported it.

An idempotent edge writer (`batch_create_edges`, `create_edges_if_absent`, `batch_upsert_node_relations` and everything that
calls them) does not say whether it created the edge or found it. A caller that must know, inside one transaction, wraps the call
in :func:`track` (with the edge collections it cares about: re-creating a PERMISSION edge after deleting it is not a change of
structure); the providers report, from the write's own result (`MERGE` created / `UPSERT` had no old document), the ids at
both ends of every edge the statement really created. Nothing is recorded when no tracker is active.
"""

from __future__ import annotations

import contextvars
from contextlib import contextmanager
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Collection, Iterable, Iterator

# every active tracker, outermost first: a tracker opened inside another does not hide the outer one's edges
_tracker: contextvars.ContextVar[tuple[tuple[set[str], frozenset[str] | None], ...]] = contextvars.ContextVar("graph_edge_effects", default=())


@contextmanager
def track(collections: Collection[str] | None = None) -> Iterator[set[str]]:
    """Collects the ids of the nodes at the ends of the edges created inside the block (only in these edge collections, when given)."""
    created: set[str] = set()
    token = _tracker.set((*_tracker.get(), (created, None if collections is None else frozenset(collections))))
    try:
        yield created
    finally:
        _tracker.reset(token)


def tracking() -> bool:
    """Whether a `track` block is active: a provider reports (and pays for the statement that reports) created edges only then, or when
    its caller asked for the count."""
    return bool(_tracker.get())


def note_created(node_ids: Iterable[str | None], collection: str) -> None:
    """Called by a provider with the node ids at both ends of each edge its statement created in `collection`."""
    ids = [i for i in node_ids if i]
    for created, collections in _tracker.get():  # each active tracker takes the effect, by its own collection filter
        if collections is None or collection in collections:
            created.update(ids)
