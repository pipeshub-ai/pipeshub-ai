"""What one full sync re-read, so the links it did not re-read can be removed after it.

A full sync used to delete every sync edge of the connector before it started and
re-link each node as it re-read it. A sync cut off partway left everything it had
not reached yet unlinked. Now nothing is deleted up front: the processor notes
each node the sync writes, and only once the sync has run to the end are the
nodes it never wrote unlinked (``IGraphDBProvider.unlink_nodes_missed_by_full_sync``).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.services.graph_db.common.full_sync_unlink import TRACKED_COLLECTIONS


@dataclass(frozen=True)
class FullSyncCoverage:
    """How much of the source a full sync that returned normally actually read.

    ``complete``: False when the connector skipped part of the source after an
    error it chose not to raise (a channel whose history failed, say). Nothing is
    unlinked then, as for a sync that was cut off.

    ``window_start_ms``: the sync reads only items created at or after this time
    (a "sync messages from" filter). A record created before it that the sync did
    not see is outside the window, not gone, so it keeps its links. ``None``
    means the sync reads everything, so whatever it did not see is gone.

    ``windowed_group_types``: record group types that the window applies to as
    well (a Slack thread, which is read only when its first message is inside
    the window). A group of another type is always read, so an unseen one is gone.
    """

    complete: bool = True
    window_start_ms: int | None = None
    windowed_group_types: frozenset[str] = frozenset()


#: Not a collection: the records whose permission edges this full sync has
#: already rewritten from a list the connector fetched. Kept apart from the seen
#: records, since a metadata or content update sees a record without saying
#: anything about who may read it.
PERMISSIONS_REWRITTEN = "permissionsRewritten"


@dataclass
class FullSyncSeen:
    """Ids of the nodes, per collection, that one full sync has written so far."""

    by_collection: dict[str, set[str]] = field(
        default_factory=lambda: {collection: set() for collection in TRACKED_COLLECTIONS}
    )
    permissions_rewritten: set[str] = field(default_factory=set)

    def add(self, collection: str, node_id: str | None) -> None:
        if not node_id:
            return
        if collection == PERMISSIONS_REWRITTEN:
            self.permissions_rewritten.add(node_id)
        elif collection in self.by_collection:
            self.by_collection[collection].add(node_id)

    def has(self, collection: str, node_id: str) -> bool:
        if collection == PERMISSIONS_REWRITTEN:
            return node_id in self.permissions_rewritten
        return node_id in self.by_collection.get(collection, ())

    def count(self) -> int:
        return sum(len(ids) for ids in self.by_collection.values())
