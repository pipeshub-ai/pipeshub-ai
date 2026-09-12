"""Resolve which physical collections hold a virtual record's points.

Membership and VRID-scoped deletes know a virtual record id, not an org or a
connector — but under any strategy whose naming depends on those, "the"
collection is not a thing. What they *do* have, for free, is the set of graph
records behind the VRID: ``resolve_vector_membership`` already fetches every
one of them to recompute ``connectorIds``/``recordGroupIds``. Mapping those
documents through the active strategy is therefore a pure function over data
already in hand — no extra round trip — and it is the only correct answer,
because a VRID's points live in exactly the collections its records resolve to.

``all_collections`` covers the one case where that map is empty: a VRID with
no graph record left anywhere. Deleting it from every managed collection is
safe precisely because nothing references it — a shared VRID would still have
records, and would take the per-record path instead.
"""

from collections.abc import Awaitable, Callable, Mapping, Sequence
from typing import Any

from app.services.vector_db.collection_manifest import (
    CollectionManifestStore,
    ManagedCollection,
)
from app.services.vector_db.collections import CollectionType, sanitize_collection_name
from app.services.vector_db.strategy import (
    CollectionStrategy,
    IncompleteCollectionContext,
    RecordContext,
    resolve_write_collection_name,
)


class UnresolvableVirtualRecord(Exception):
    """No collection could be resolved for any of a VRID's graph records.

    Distinct from "the VRID has no records" (which routes to
    ``all_collections``): this means records exist but none carries what the
    active strategy needs. Raising lets the caller retry rather than write
    membership nowhere and report success.
    """


class StaticCollectionLocator:
    """A locator over a fixed set of names, for tests and fixed-name callers."""

    def __init__(self, names: Sequence[str]) -> None:
        self._names = [sanitize_collection_name(name) for name in names]

    def collections_for_records(
        self, records: Sequence[Mapping[str, Any]]
    ) -> Sequence[str]:
        return list(self._names)

    async def all_collections(
        self, *, fresh: bool = False, strict: bool = False
    ) -> Sequence[str]:
        return list(self._names)


class VirtualRecordCollectionLocator:
    """Maps a VRID's graph records to collections through the active strategy."""

    def __init__(
        self,
        strategy: CollectionStrategy,
        manifest_store: CollectionManifestStore,
        logger,
        collection_type: CollectionType = CollectionType.RECORDS,
        list_managed: Callable[..., Awaitable[Sequence[ManagedCollection]]] | None = None,
    ) -> None:
        self._strategy = strategy
        self._manifest_store = manifest_store
        self._logger = logger
        self._collection_type = collection_type
        # The manifest is only ever written on the indexing write path, so a
        # deployment that upgraded into the registry has a live collection and
        # an empty manifest until something is indexed. Reading the store
        # directly would report nothing there. ``CollectionRegistry
        # .list_managed_collections`` adopts such a collection first, which is
        # why delete paths hand it in; the store stays the fallback for callers
        # that hold no registry.
        self._list_managed = list_managed

    def collections_for_records(
        self, records: Sequence[Mapping[str, Any]]
    ) -> Sequence[str]:
        """Distinct collections these records' points belong to, in first-seen order."""
        names: list[str] = []
        seen: set[str] = set()
        unresolved = 0

        for record in records:
            name = self._resolve(record)
            if name is None:
                unresolved += 1
                continue
            if name not in seen:
                seen.add(name)
                names.append(name)

        if records and not names:
            raise UnresolvableVirtualRecord(
                f"None of the {len(records)} record(s) for this virtual record "
                f"carry what strategy '{self._strategy.strategy_name()}' needs "
                f"to resolve a collection"
            )
        if unresolved:
            self._logger.warning(
                "Skipped %d of %d record(s) while resolving collections: "
                "incomplete context for strategy '%s'",
                unresolved,
                len(records),
                self._strategy.strategy_name(),
            )
        return names

    async def all_collections(
        self, *, fresh: bool = False, strict: bool = False
    ) -> Sequence[str]:
        """Every managed collection of this locator's collection type.

        ``fresh=True`` bypasses the manifest's TTL cache. Delete paths pass it:
        a stale — or, on a deployment that upgraded into the registry, an
        empty — view resolves to fewer collections than exist, and points left
        in the ones it missed are unreachable afterwards.

        ``strict=True`` is forwarded to the registry enumerator, so a delete
        path can tell "nothing is managed" apart from "could not find out".
        """
        if self._list_managed is not None:
            managed = await self._list_managed(fresh=fresh, strict=strict)
        else:
            managed = await self._manifest_store.list(fresh=fresh)
        return [
            entry.name
            for entry in managed
            if entry.collection_type == self._collection_type.value
        ]

    def _resolve(self, record: Mapping[str, Any]) -> str | None:
        ctx = RecordContext.from_graph_document(
            record, collection_type=self._collection_type
        )
        try:
            return resolve_write_collection_name(self._strategy, ctx)
        except IncompleteCollectionContext:
            return None
