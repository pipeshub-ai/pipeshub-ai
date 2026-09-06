"""CollectionRegistry: owns vector DB collection lifecycle on top of a strategy.

Orchestration only. The three things every strategy needs, regardless of how
many collections it produces, are each owned elsewhere:

- *which name* — ``CollectionStrategy`` (pure), reached through
  ``resolve_write_collection_name`` so validation and sanitization happen once
- *what we manage* — ``CollectionManifestStore`` (TTL-cached, merge-on-write)
- *does it exist right now* — ``_ExistenceCache`` below, a short-TTL memo that
  keeps steady-state indexing from round-tripping to the vector DB per record

What is left here is the sequencing: resolve, create on first use, verify the
dimension, ensure payload indexes, record what we created, and — on the delete
path — establish the one data-dependent fact a strategy may not compute for
itself before asking it what to do.

Point-level deletion (the safe, graph-verified VRID dance) stays in
``IndexingPipeline``, where the graph provider already lives; this registry
only resolves *which* collection(s) an operation targets and executes the
uncontroversial collection-level operations.
"""

import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, replace

from app.exceptions.indexing_exceptions import VectorStoreError
from app.services.vector_db.collection_manifest import (
    CollectionManifestStore,
    ManagedCollection,
)
from app.services.vector_db.collections import CollectionType, sanitize_collection_name
from app.services.vector_db.const.const import (
    CONNECTOR_IDS_FIELD,
    PAYLOAD_KEYWORD_INDEXES,
)
from app.services.vector_db.interface.vector_db import IVectorDBService
from app.services.vector_db.models import CollectionConfig
from app.services.vector_db.strategy import (
    CollectionStrategy,
    DeleteAction,
    DeleteContext,
    DeleteScope,
    QueryContext,
    RecordContext,
    resolve_write_collection_name,
)

_EXISTENCE_CACHE_TTL_SECONDS = 60

# Signature of the optional probe that answers "is this connector the last one
# writing to `collection_name`?". Only a multi-collection strategy needs the
# answer; see CollectionRegistry.resolve_delete_scope.
LivenessProbe = Callable[[DeleteContext, str], Awaitable[bool]]


@dataclass(frozen=True)
class _ExistenceEntry:
    """A cached "this collection exists" observation.

    ``dimension`` is the vector width verified when the entry was written, or
    ``None`` when existence was confirmed without checking it (the read path
    does not know or care about the dimension). Keeping it on the entry is what
    stops a cached existence hit from also short-circuiting the write path's
    dimension check after an embedding-model swap.
    """

    checked_at: float
    dimension: int | None = None


class _ExistenceCache:
    """Short-TTL memo of "this collection exists (at this width)"."""

    def __init__(self, ttl_seconds: float = _EXISTENCE_CACHE_TTL_SECONDS) -> None:
        self._ttl = ttl_seconds
        self._entries: dict[str, _ExistenceEntry] = {}

    def is_fresh(self, name: str) -> bool:
        return self._fresh(name) is not None

    def matches_dimension(self, name: str, dimension: int) -> bool:
        """True only when the cache also proves the width still matches.

        An entry written by the read path (dimension ``None``) deliberately
        does not satisfy this: the write path must re-verify, or an embedding
        model swapped mid-process would upsert the new width into the old
        collection for up to a full TTL.
        """
        entry = self._fresh(name)
        return entry is not None and entry.dimension == dimension

    def mark(self, name: str, dimension: int | None = None) -> None:
        self._entries[name] = _ExistenceEntry(
            checked_at=time.monotonic(), dimension=dimension
        )

    def invalidate(self, name: str) -> None:
        self._entries.pop(name, None)

    def _fresh(self, name: str) -> _ExistenceEntry | None:
        entry = self._entries.get(name)
        if entry is None:
            return None
        if (time.monotonic() - entry.checked_at) >= self._ttl:
            return None
        return entry


class CollectionRegistry:
    def __init__(
        self,
        vector_db_service: IVectorDBService,
        strategy: CollectionStrategy,
        collection_config_factory: Callable[[int, bool], CollectionConfig],
        manifest_store: CollectionManifestStore,
        logger,
        liveness_probe: LivenessProbe | None = None,
        max_collections_advisory: int | None = None,
    ) -> None:
        self._vector_db_service = vector_db_service
        self._strategy = strategy
        self._collection_config_factory = collection_config_factory
        self._manifest_store = manifest_store
        self._logger = logger
        self._liveness_probe = liveness_probe
        # Just the number, not the whole capabilities object: the registry has
        # no other reason to know what the provider supports.
        self._max_collections_advisory = max_collections_advisory
        self._existence = _ExistenceCache()

    @property
    def strategy(self) -> CollectionStrategy:
        """The resolved strategy, for collaborators that only need name resolution.

        Handing out the strategy rather than a second one built from config
        keeps every component in a process agreeing on where a record's points
        live — a dedup check that disagreed with the write path would skip
        indexing a record whose vectors were never written.
        """
        return self._strategy

    @property
    def strategy_name(self) -> str:
        return self._strategy.strategy_name()

    @property
    def manifest_store(self) -> CollectionManifestStore:
        return self._manifest_store

    def resolve_write_collection(self, ctx: RecordContext) -> str:
        """Name this context resolves to. Pure, validated, sanitized, no I/O."""
        return resolve_write_collection_name(self._strategy, ctx)

    # ------------------------------------------------------------------
    # Manifest
    # ------------------------------------------------------------------

    async def list_managed_collections(
        self, *, fresh: bool = False
    ) -> list[ManagedCollection]:
        """Every collection this registry manages.

        Self-heals an empty manifest by adopting the strategy's collection when
        it already exists in the vector DB, so callers can treat this as the
        authoritative enumeration on deployments that upgraded into the
        registry as well as on fresh ones.

        Pass ``fresh=True`` from anything that drops or recreates collections:
        acting on a stale view there destroys data.
        """
        managed = await self._manifest_store.list(fresh=fresh)
        if managed:
            return managed
        try:
            await self._adopt_untracked_collections()
        except Exception as e:
            # Enumeration must not become a hard dependency on vector DB
            # reachability; callers degrade to "nothing managed".
            self._logger.warning("Could not probe for untracked collections: %s", e)
        return await self._manifest_store.list(fresh=True)

    async def _adopt_untracked_collections(self) -> None:
        """Bring pre-manifest collections under management, once.

        The manifest is only ever written by ``ensure_collection`` on the
        indexing write path. A deployment that upgraded into the registry
        therefore has a live collection and an empty manifest — and every
        consumer of ``list_managed_collections`` (the model-change guard, the
        rebuild flow) would see "nothing to do" and silently skip the
        collection that actually holds all the data. Adopting here rather than
        at each call site means there is no second place to forget.

        Only possible for an org-agnostic strategy. Under one whose naming
        depends on record context there is no single name to probe, and
        inventing one would adopt a collection that does not exist.
        """
        if self._strategy.required_axes:
            self._logger.debug(
                "Skipping untracked-collection adoption: strategy '%s' names "
                "collections from record context, so there is no default to probe",
                self.strategy_name,
            )
            return

        for collection_type in CollectionType:
            name = self.resolve_write_collection(
                RecordContext(org_id="", collection_type=collection_type)
            )
            dimension = await self._existing_dimension(name)
            if dimension is None:
                continue
            await self._manifest_store.record(
                ManagedCollection(
                    name=name,
                    collection_type=collection_type.value,
                    embedding_dimension=dimension,
                    strategy_name=self.strategy_name,
                )
            )
            self._logger.info(
                "Adopted pre-existing collection '%s' (dimension %s) into the manifest",
                name,
                dimension,
            )

    # ------------------------------------------------------------------
    # Write-path lifecycle
    # ------------------------------------------------------------------

    def build_collection_config(
        self, embedding_size: int, sparse_idf: bool = False
    ) -> CollectionConfig:
        """The exact config this registry creates collections with.

        Public so a caller that reconciles an existing collection toward the
        managed layout describes the same target the registry would create,
        instead of assembling a second, drifting copy of it.
        """
        return self._collection_config_factory(embedding_size, sparse_idf)

    async def ensure_collection(
        self, ctx: RecordContext, embedding_size: int, sparse_idf: bool = False
    ) -> str:
        """Resolve ``ctx`` to a collection name, creating it on first use.

        Byte-identical to the pre-strategy behaviour under
        ``SingleCollectionStrategy``: always the same name, dimension checked
        against the existing collection, payload indexes ensured.
        """
        name = self.resolve_write_collection(ctx)
        if self._existence.matches_dimension(name, embedding_size):
            return name

        existing_dim = await self._existing_dimension(name)
        if existing_dim is not None:
            self._assert_dimension(name, existing_dim, embedding_size)
            await self._ensure_payload_indexes(name)
            self._existence.mark(name, dimension=existing_dim)
            await self._record_in_manifest(name, ctx, embedding_size)
            return name

        await self._warn_if_over_advisory_ceiling(name)

        config = self._collection_config_factory(embedding_size, sparse_idf)
        try:
            await self._vector_db_service.create_collection(
                collection_name=name, config=config
            )
            self._logger.info("Created collection '%s'", name)
            await self._ensure_payload_indexes(name)
        except Exception as e:
            if "already exists" not in str(e).lower():
                self._logger.error("Error creating collection '%s': %s", name, e)
                raise VectorStoreError(
                    "Failed to create collection",
                    details={"collection": name, "error": str(e)},
                )
            self._logger.info(
                "Collection '%s' was created concurrently; verifying dimension", name
            )
            concurrent_dim = await self._existing_dimension(name)
            if concurrent_dim is not None:
                self._assert_dimension(name, concurrent_dim, embedding_size)
            await self._ensure_payload_indexes(name)

        self._existence.mark(name, dimension=embedding_size)
        await self._record_in_manifest(name, ctx, embedding_size)
        return name

    def _assert_dimension(self, name: str, existing: int, required: int) -> None:
        if existing == required:
            return
        raise VectorStoreError(
            f"Embedding model dimension mismatch: collection '{name}' was "
            f"created with dimension {existing} but the current model produces "
            f"dimension {required}. Re-index by deleting the collection and "
            f"re-running indexing, or switch back to the original embedding model.",
            details={
                "collection": name,
                "existing_dim": existing,
                "required_dim": required,
            },
        )

    async def _warn_if_over_advisory_ceiling(self, name: str) -> None:
        """Warn — never refuse — when a new collection crosses the provider's ceiling.

        The number is a practical guideline (shard budgets, per-index memory),
        not a hard limit, and it is reached gradually as connectors are added.
        Refusing to create the collection would fail indexing for whichever
        record happened to arrive at the boundary; a warning tells the operator
        while their data keeps flowing.
        """
        if not self._max_collections_advisory:
            return
        try:
            managed = await self._manifest_store.list()
        except Exception:
            return
        if len(managed) + 1 > self._max_collections_advisory:
            self._logger.warning(
                "Creating collection '%s' brings this deployment to %d collections, "
                "above the %d that provider '%s' recommends. Strategy '%s' is "
                "producing more collections than expected — check whether it is "
                "grouping on the axis you intended.",
                name,
                len(managed) + 1,
                self._max_collections_advisory,
                self._vector_db_service.get_service_name(),
                self.strategy_name,
            )

    async def _record_in_manifest(
        self, name: str, ctx: RecordContext, embedding_size: int
    ) -> None:
        await self._manifest_store.record(
            ManagedCollection(
                name=name,
                collection_type=ctx.collection_type.value,
                embedding_dimension=embedding_size,
                strategy_name=self.strategy_name,
                embedding_model=ctx.embedding_model,
            )
        )

    async def _existing_dimension(self, name: str) -> int | None:
        info = await self._vector_db_service.get_collection_info(name)
        return info.dense_dimension if info.exists else None

    async def _ensure_payload_indexes(self, name: str) -> None:
        for field_name, schema in PAYLOAD_KEYWORD_INDEXES:
            try:
                await self._vector_db_service.create_index(
                    collection_name=name, field_name=field_name, field_schema=schema
                )
            except Exception as e:
                err = str(e).lower()
                if "already exists" in err or "already exist" in err or "conflict" in err:
                    continue
                self._logger.warning(
                    "Failed to create payload index %s on %s: %s", field_name, name, e
                )

    def invalidate(self, name: str) -> None:
        """Force the next resolution for ``name`` to re-check the vector DB.

        Call this after a "collection not found" error surfaces from a data
        operation — the cache would otherwise keep assuming the collection
        exists until its TTL expires, failing every write in between.
        """
        self._existence.invalidate(name)

    # ------------------------------------------------------------------
    # Read-path resolution
    # ------------------------------------------------------------------

    async def resolve_for_query(self, ctx: QueryContext) -> list[str]:
        """Resolve read collections, filtered to ones that actually exist.

        A non-existent collection is silently skipped rather than erroring — a
        strategy can legitimately name a collection before anything has been
        indexed into it.
        """
        managed = await self._manifest_store.list()
        candidates = [
            sanitize_collection_name(name)
            for name in self._strategy.resolve_read_collections(ctx, managed)
        ]
        existing: list[str] = []
        for name in candidates:
            if self._existence.is_fresh(name):
                existing.append(name)
                continue
            try:
                if await self._vector_db_service.collection_exists(name):
                    self._existence.mark(name)
                    existing.append(name)
            except Exception as e:
                self._logger.warning(
                    "Could not check existence of collection %s: %s", name, e
                )
        return existing

    # ------------------------------------------------------------------
    # Delete-path resolution (execution stays in IndexingPipeline, which
    # holds the graph provider needed for the safe per-VRID delete dance)
    # ------------------------------------------------------------------

    async def resolve_delete_scope(self, ctx: DeleteContext) -> DeleteScope:
        """Resolve how this connector's data should be removed.

        Establishes ``is_last_writer_to_collection`` before asking the
        strategy, because that is the fact a strategy needs to choose
        ``DROP_COLLECTION`` over ``FILTERED_DELETE`` and the one thing it may
        not compute itself (it would need graph I/O, which strategies are
        deliberately barred from). Without a ``liveness_probe`` the flag stays
        ``None`` — "unproven", never "safe" — and a ``DROP_COLLECTION`` asked
        for on an unproven flag is downgraded here rather than at the point of
        execution, so no caller can route around the check.
        """
        resolved_ctx = await self._with_liveness(ctx)
        scope = self._strategy.resolve_delete_scope(resolved_ctx)
        action = scope.action
        filter_field = scope.filter_field
        filter_values = scope.filter_values
        if (
            action == DeleteAction.DROP_COLLECTION
            and resolved_ctx.is_last_writer_to_collection is not True
        ):
            self._logger.warning(
                "Strategy '%s' asked to drop %s for connector %s, but this "
                "deployment cannot prove no other connector writes there; "
                "downgrading to a filtered delete.",
                self.strategy_name,
                scope.collection_names,
                ctx.connector_id,
            )
            action = DeleteAction.FILTERED_DELETE
            # A drop scope carries no filter, so supply the membership field
            # every point already carries before handing it to the executor.
            filter_field = filter_field or CONNECTOR_IDS_FIELD
            if not filter_values:
                filter_values = [ctx.connector_id] if ctx.connector_id else None
        return DeleteScope(
            action=action,
            collection_names=[
                sanitize_collection_name(n) for n in scope.collection_names
            ],
            filter_field=filter_field,
            filter_values=filter_values,
        )

    async def _with_liveness(self, ctx: DeleteContext) -> DeleteContext:
        if self._liveness_probe is None or ctx.is_last_writer_to_collection is not None:
            return ctx
        target = self.resolve_write_collection(
            RecordContext(
                org_id=ctx.org_id,
                collection_type=ctx.collection_type,
                connector_id=ctx.connector_id,
                connector_name=ctx.connector_name,
            )
        )
        try:
            is_last = await self._liveness_probe(ctx, target)
        except Exception as e:
            # An unproven flag costs a filtered delete; a wrongly-true one
            # drops a collection another connector still writes to.
            self._logger.warning(
                "Liveness probe failed for connector %s on %s: %s",
                ctx.connector_id,
                target,
                e,
            )
            return ctx
        return replace(ctx, is_last_writer_to_collection=is_last)

    async def delete_collection(self, name: str) -> None:
        """Idempotent drop: a missing collection is treated as success."""
        await self._drop(name)
        await self._manifest_store.forget(name)

    async def _drop(self, name: str) -> None:
        try:
            await self._vector_db_service.delete_collection(name)
        except Exception as e:
            if not _is_collection_missing(e):
                raise
            self._logger.info("Collection %s already absent", name)
        self._existence.invalidate(name)

    # ------------------------------------------------------------------
    # Model-change rebuild
    # ------------------------------------------------------------------

    async def recreate_all_collections(
        self, records_dimension: int, sparse_idf: bool = False
    ) -> list[str]:
        """Drop and recreate every managed collection for a new embedding model.

        Used by the ``deleteVectorCollection`` rebuild flow. Recreates each
        collection under its existing name — already resolved by the strategy
        that created it — rather than re-resolving, so a rebuild triggered mid
        strategy-change still targets the collections that actually hold data.

        ``records_dimension`` applies to ``CollectionType.RECORDS`` entries,
        which is what an embedding-model change moves. Any other dataset keeps
        the width recorded for it: an entities collection embedded by a
        different model must not be silently rebuilt at the records model's
        dimension.

        The drop deliberately does not go through ``delete_collection``: that
        forgets the manifest entry, and a create failing straight afterwards
        would leave the collection gone *and* unmanaged — invisible to the
        model-change guard and to the next rebuild, with nothing left to find
        it by.
        """
        managed = await self.list_managed_collections(fresh=True)
        recreated: list[str] = []
        for entry in managed:
            dimension = (
                records_dimension
                if entry.collection_type == CollectionType.RECORDS.value
                else entry.embedding_dimension
            )
            await self._drop(entry.name)
            await self._vector_db_service.create_collection(
                collection_name=entry.name,
                config=self._collection_config_factory(dimension, sparse_idf),
            )
            await self._ensure_payload_indexes(entry.name)
            self._existence.mark(entry.name, dimension=dimension)
            await self._manifest_store.record(
                ManagedCollection(
                    name=entry.name,
                    collection_type=entry.collection_type,
                    embedding_dimension=dimension,
                    strategy_name=self.strategy_name,
                    embedding_model=entry.embedding_model,
                )
            )
            recreated.append(entry.name)
        return recreated


def _is_collection_missing(error: Exception) -> bool:
    err = str(error).lower()
    return any(
        token in err
        for token in (
            "not found",
            "not exist",
            "doesn't exist",
            "does not exist",
            "unknown collection",
            "404",
        )
    )
