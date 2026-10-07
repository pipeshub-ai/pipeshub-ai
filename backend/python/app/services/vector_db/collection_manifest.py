"""The set of vector DB collections this deployment is responsible for.

Derived, self-healing state — never the source of truth for whether a
collection *exists* (the provider is), only for which ones we manage. It
exists because ``IVectorDBService.get_collections()`` returns a
provider-specific shape, so there is no portable way to ask "which of these
are ours" without one.

Two properties matter, and both come from the manifest's consumers being the
most destructive paths in the system — the embedding-model-change guard and
the rebuild flow:

*Freshness.* Entries are TTL-cached, not memoised for the process lifetime.
A query-service process must be able to see a collection the indexing service
created after it started, or the guard waves through a model change while
that collection still holds vectors from the outgoing model.

*Convergence under concurrent writers.* No vector DB or KV store here offers a
compare-and-set over a map, so every mutation re-reads and merges rather than
overwriting a snapshot. Two processes can still interleave inside that
read-merge-write, but ``CollectionRegistry.ensure_collection`` re-records on
every existence-cache miss — at least once per existence-cache TTL per active
collection per process — so an entry lost to a race reappears on its own. That
is why this is a merge rather than a lock: the convergence is what makes the
remaining race survivable, not the mutual exclusion.
"""

import asyncio
import time
from dataclasses import asdict, dataclass

MANIFEST_CONFIG_KEY = "/services/vectordb/collection_manifest"

# Long enough that steady-state indexing does not re-read per record, short
# enough that a collection created by another service becomes visible to this
# one well inside a human's "did my model change get rejected?" loop.
_MANIFEST_TTL_SECONDS = 30


class ManifestConflictError(Exception):
    """Two logical collection types claim one physical collection name.

    A strategy bug, not a transient condition: silently overwriting the entry
    would make the rebuild flow recreate the collection at the wrong dimension
    for one of the two datasets.
    """


@dataclass(frozen=True)
class ManagedCollection:
    """One entry of the persisted collection manifest."""

    name: str
    collection_type: str
    embedding_dimension: int
    strategy_name: str
    embedding_model: str | None = None


class CollectionManifestStore:
    """KV-backed, TTL-cached, merge-on-write set of managed collections."""

    def __init__(self, config_service, logger) -> None:
        self._config_service = config_service
        self._logger = logger
        self._cache: dict[str, ManagedCollection] | None = None
        self._cached_at: float = 0.0
        self._lock = asyncio.Lock()

    async def list(self, *, fresh: bool = False, strict: bool = False) -> list[ManagedCollection]:
        """Every managed collection.

        ``fresh=True`` bypasses the TTL cache. Use it on paths that drop or
        recreate collections, where acting on a stale view destroys data.

        ``strict=True`` raises when the KV store cannot be read, instead of
        answering as if the manifest were empty. A delete path needs it.
        """
        entries = await self._read(fresh=fresh, strict=strict)
        return list(entries.values())

    async def get(self, name: str, *, fresh: bool = False) -> ManagedCollection | None:
        return (await self._read(fresh=fresh)).get(name)

    async def record(self, entry: ManagedCollection) -> None:
        """Add or update one entry, preserving entries written elsewhere.

        No-ops when the stored entry is already identical, so the steady-state
        re-record from ``ensure_collection`` does not write to the KV store on
        every existence-cache miss.
        """
        async with self._lock:
            # The write below replaces the whole manifest. A read that failed
            # answers {}, and writing that back with one entry added would erase
            # every other managed collection -- a flapping store turned one
            # ensure_collection into a wiped manifest.
            entries = await self._read(fresh=True, raise_if_unreadable=True)
            existing = entries.get(entry.name)
            if existing == entry:
                return
            if existing is not None and existing.collection_type != entry.collection_type:
                raise ManifestConflictError(
                    f"Collection '{entry.name}' is already managed as "
                    f"'{existing.collection_type}' and cannot also be "
                    f"'{entry.collection_type}'."
                )
            entries[entry.name] = entry
            await self._write(entries)

    async def forget(self, name: str) -> None:
        """Remove one entry, preserving entries written elsewhere."""
        async with self._lock:
            # Not a clobber risk on its own -- an empty read finds nothing to
            # pop and returns before writing -- but it would report a forget
            # that did not happen.
            entries = await self._read(fresh=True, raise_if_unreadable=True)
            if entries.pop(name, None) is None:
                return
            await self._write(entries)

    # ------------------------------------------------------------------

    async def _read(
        self, *, fresh: bool, strict: bool = False, raise_if_unreadable: bool = False
    ) -> dict[str, ManagedCollection]:
        """The manifest as stored.

        ``strict`` raises on a store that cannot be read *and* on malformed
        content -- for a delete, which acts on what is missing.

        ``raise_if_unreadable`` raises only on the first. It is for a
        read-modify-write: acting on a read that failed would write the whole
        manifest back from nothing. Malformed content stays permissive there,
        because rewriting it is how a bad manifest repairs itself.
        """
        if not fresh and self._cache is not None:
            if (time.monotonic() - self._cached_at) < _MANIFEST_TTL_SECONDS:
                return dict(self._cache)

        raw = await self._config_service.get_config(
            MANIFEST_CONFIG_KEY, default={}, raise_on_error=strict or raise_if_unreadable
        )
        # Only a missing value becomes {}. `or {}` would also turn a stored
        # [] / "" / 0 / false into {} before the check below, and a strict
        # reader would take a malformed manifest for an empty one.
        if raw is None:
            raw = {}
        if not isinstance(raw, dict):
            # The per-entry guard below only covers a malformed *entry*; a
            # non-mapping here would raise on .items() and fail every read
            # until someone edits the KV store by hand. Treat it as empty so
            # enumeration keeps working and the next ensure_collection
            # rewrites it in the current shape -- except for a strict reader,
            # which acts on what is missing: a delete would drop mappings for
            # collections it could not see.
            if strict:
                raise ValueError(
                    f"Collection manifest at {MANIFEST_CONFIG_KEY} is a "
                    f"{type(raw).__name__}, not a mapping"
                )
            self._logger.warning(
                "Collection manifest at %s is a %s, not a mapping; treating it as empty",
                MANIFEST_CONFIG_KEY,
                type(raw).__name__,
            )
            raw = {}
        entries: dict[str, ManagedCollection] = {}
        for name, payload in raw.items():
            try:
                entries[name] = ManagedCollection(**payload)
            except TypeError as e:
                # A manifest written by a newer/older shape. Dropping the entry
                # here rather than raising keeps enumeration working; the next
                # ensure_collection re-records it in the current shape. A strict
                # reader cannot drop it: the collection still exists, and a
                # delete that never saw it would leave its points behind while
                # dropping the mapping that finds them.
                if strict:
                    raise ValueError(
                        f"Collection manifest entry {name!r} is not in the "
                        f"current shape: {e}"
                    ) from e
                self._logger.warning(
                    "Dropping malformed collection manifest entry for %s", name
                )
        self._cache = entries
        self._cached_at = time.monotonic()
        return dict(entries)

    async def _write(self, entries: dict[str, ManagedCollection]) -> None:
        raw = {name: asdict(entry) for name, entry in entries.items()}
        # set_config answers False on any store failure rather than raising.
        # Ignoring it made record() succeed while nothing was stored, and left
        # the cache below claiming the new state: a collection in use that the
        # manifest does not list, which is what makes a delete miss its points.
        written = await self._config_service.set_config(MANIFEST_CONFIG_KEY, raw)
        if written is False:
            raise RuntimeError(
                f"Could not save the collection manifest to {MANIFEST_CONFIG_KEY}; "
                "the configuration store rejected the write. It will be retried."
            )
        self._cache = dict(entries)
        self._cached_at = time.monotonic()
