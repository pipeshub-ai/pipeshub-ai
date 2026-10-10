"""Which of a connector's edges a full sync replaces, and how it finds the stale ones.

A full sync tags these edges with its generation first and deletes the ones still
carrying that tag only after it succeeded. Every write that re-produces an edge
clears the tag, so whatever keeps it is an edge the source no longer has. A sync
that fails, is stopped or keeps stored access clears its tags instead, leaving the
stored edges, and so everyone's access, as they were.
"""

from collections.abc import Iterator, Mapping
from contextlib import contextmanager

from app.config.constants.arangodb import CollectionNames

PENDING_SWEEP = "pendingSweep"

# Grants first: a delete that stops part way leaves access removed, not orphaned.
SYNC_EDGE_COLLECTIONS: tuple[str, ...] = (
    CollectionNames.PERMISSION.value,
    CollectionNames.USER_APP_RELATION.value,
    CollectionNames.INHERIT_PERMISSIONS.value,
    CollectionNames.ANYONE.value,
    CollectionNames.BELONGS_TO.value,
    CollectionNames.NODE_RELATIONS.value,
    CollectionNames.ENTITY_RELATIONS.value,
)

# A gate can be written before a sync first reaches its source, so one is no sign
# that the sync read anything.
SWEEP_EVIDENCE_COLLECTIONS: tuple[str, ...] = tuple(
    c for c in SYNC_EDGE_COLLECTIONS if c != CollectionNames.USER_APP_RELATION.value
)

# The generations of the full syncs running in this process. Only their tags mean
# "not produced yet": one a crashed sync left behind marks an edge that is stored,
# and the next full sync's tag replaces it.
_running: set[int] = set()


@contextmanager
def full_sync_running(generation: int | None) -> Iterator[None]:
    """While the full sync tagged ``generation`` runs, the edges carrying its tag read as absent."""
    if generation is None:
        yield
        return
    _running.add(generation)
    try:
        yield
    finally:
        _running.discard(generation)


def running_generations() -> list[int]:
    return sorted(_running)


def awaits_sweep(edge: Mapping | None) -> bool:
    """An edge only a running full sync's tag keeps: to that sync it is not there yet."""
    if not edge:
        return False
    tag = edge.get(PENDING_SWEEP)
    return tag is not None and tag in _running
