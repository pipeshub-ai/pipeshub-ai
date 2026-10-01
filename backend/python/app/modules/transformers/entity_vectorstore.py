"""EntityVectorStore — provider-agnostic indexing of knowledge graph entities.

Entities (categories, topics, departments, people, record groups, connectors)
are stored in a dedicated ``entities`` vector collection, separate from the
``records`` document collection.  Each entity point has:

  page_content  = "<name>"  (aliases are payload only — EntityRecord.embedding_text)
  metadata      = EntityRecord.to_vector_payload()  (slim — see models.entities)
  connectorIds / recordGroupIds = top-level payload siblings of metadata, not
                                  nested in it (mirrors the records
                                  collection's VectorChunkPayload) — see
                                  ``upsert_entities_batch``.

The deterministic point ID is derived from ``orgId:entityType:entityId`` via
UUID5 so that re-upserts are idempotent without a prior delete.

Reference counting / provenance (which connectors reference an entity, how
many records link to it) is owned by the graph DB, not this vector index —
this store is a disposable search projection that can be rebuilt from the
graph. Connector-disconnect cleanup here
scrolls only the points scoped to that connector (top-level ``connectorIds``
+ ``metadata.orgId``, the same bound a single filtered delete would use) and
shrinks each one's membership rather than deleting it outright, since a
taxonomy/record-group entity can be shared with another still-live
connector — see ``delete_entities_by_connector``.

Membership arrays (``connectorIds``, ``recordGroupIds``) are merged, not
replaced. The same entity point is shared by every record that references it
(deterministic ID above), but each caller only knows about the one record it
currently has in hand — a plain payload overwrite would leave the point
remembering only the last writer's single group/connector instead of the
union across all of them. See ``_merge_membership`` and the per-entity lock
in ``_entity_lock``.

That lock is keyed by ``(event loop, entity)``, so it serialises writers only
within one loop of one process. It does not cover the two cases that occur in
practice: indexing runs some work on the main loop and some on a worker-thread
loop, which take different locks for the same entity, and separate indexing
workers hold separate instances entirely. Either pair can merge against the
same stale read and drop one another's membership. Search tolerates that:
membership only narrows recall, and every hit is checked against the graph.
The one irreversible step is connector cleanup deleting a point that merely
looks exclusive, so it asks the graph first (``membership_lookup``); the
lost membership itself is restored when the affected record is reindexed.
"""

from __future__ import annotations

import asyncio
import contextlib
import time
import uuid
import weakref
from collections import OrderedDict
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING, Any

from app.config.constants.ai_models import DEFAULT_EMBEDDING_MODEL
from app.config.constants.arangodb import QdrantCollectionNames
from app.config.constants.service import config_node_constants
from app.exceptions.indexing_exceptions import VectorStoreError
from app.services.vector_db.const.const import (
    CONNECTOR_IDS_FIELD,
    RECORD_GROUP_IDS_FIELD,
)
from app.services.vector_db.models import (
    CollectionConfig,
    FilterExpression,
    SearchResult,
    VectorPoint,
)
from app.services.vector_db.sparse_embeddings import (
    SparseEmbedder,
    get_default_sparse_embedder,
)
from app.telemetry.modules import entity_index_metrics
from app.utils.aimodels import get_default_embedding_model, get_embedding_model

if TYPE_CHECKING:
    import logging

    from app.config.configuration_service import ConfigurationService
    from app.models.entities import EntityRecord
    from app.services.vector_db.interface.vector_db import IVectorDBService

# (entity refs) -> {(type, id): {"connectorIds": [...], "recordGroupIds": [...]}},
# from the graph; see IGraphDBProvider.get_taxonomy_entity_membership.
MembershipLookup = Callable[
    [list[dict[str, str]]], Awaitable[dict[tuple[str, str], dict[str, list[str]]]]
]


class _MembershipReadError(Exception):
    """The stored membership for an entity could not be read.

    Distinct from "this entity has no point yet": the caller must skip the
    write rather than treat the entity as new. See ``_fetch_existing_states``.
    """


_ENTITIES_COLLECTION = QdrantCollectionNames.ENTITIES.value


class _Page(Enum):
    """What connector cleanup did with one scrolled page."""

    CHANGED = "changed"
    ALREADY_DONE = "already_done"  # every point gone or already stripped
    NO_ENTITIES = "no_entities"  # only points without an entity id or type


# Ids of unwritten entities named in one warning; the count is always given.
_LOGGED_FAILED_IDS = 20


@dataclass
class EntityWriteOutcome:
    """What one ``upsert_entities_batch`` call did, one count per entity."""

    written: int = 0
    membership_only: int = 0
    unchanged: int = 0
    skipped: int = 0
    failed: int = 0


@dataclass(frozen=True)
class EntityPointRef:
    """Which graph node an entity point projects; see ``page_entity_points``."""

    entity_type: str
    entity_id: str
    level: str | None = None

_CONFIDENCE_THRESHOLD = 0.0

# A failed initialisation (embedding endpoint down, dimension mismatch) is not
# retried for this long, so every caller does not re-send a probe embedding.
_INIT_RETRY_SECONDS = 30.0
# Times connector cleanup processes the same unchanged page before giving up.
_CLEANUP_PAGE_ATTEMPTS = 2

_QUERY_VECTOR_CACHE_SIZE = 64

# Metadata key recording which model embedded the point (``embedding_fingerprint``).
EMBEDDING_MODEL_FIELD = "embeddingModel"

_STRING_METADATA_FIELDS = (
    "entityId", "entityType", "orgId", "name", "canonicalName", "domain", "typeCategory", "level",
    EMBEDDING_MODEL_FIELD,
)


def _as_text(value: object) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def _entity_metadata(payload: dict[str, Any] | None) -> dict[str, Any]:
    """``payload``'s metadata with its string fields as strings.

    Redis keeps every hash field as a string and guesses the type back on
    read, so subcategory level "1" comes back as 1 and a topic named "2024"
    as 2024; compared against strings, those never match.
    """
    meta = dict((payload or {}).get("metadata") or {})
    for field_name in _STRING_METADATA_FIELDS:
        value = meta.get(field_name)
        if value is not None and not isinstance(value, str):
            meta[field_name] = _as_text(value)
    aliases = meta.get("aliases")
    if isinstance(aliases, list):
        meta["aliases"] = [_as_text(a) for a in aliases if a is not None]
    return meta


class EntityVectorStore:
    """Manages embedding and retrieval of knowledge-graph entities in the
    dedicated ``entities`` vector collection.

    Designed to be a singleton per process (DI Singleton provider) so the
    embedding model and sparse embedder are initialised once and reused.
    """

    def __init__(
        self,
        logger: logging.Logger,
        config_service: ConfigurationService,
        vector_db_service: IVectorDBService,
        collection_name: str = _ENTITIES_COLLECTION,
        *,
        recreate_on_dimension_mismatch: bool = False,
    ) -> None:
        self.logger = logger
        self.config_service = config_service
        self.vector_db_service = vector_db_service
        self.collection_name = collection_name
        # Only the indexing service sets this: it runs the rebuild that
        # repopulates the collection (app.modules.indexing.entity_index_rebuild).
        self.recreate_on_dimension_mismatch = recreate_on_dimension_mismatch
        self._model_id = ""
        self._embedding_size = 0

        self._capabilities = vector_db_service.get_capabilities()
        self._dense_embeddings = None
        self._sparse_embedder: SparseEmbedder | None = None
        self._sparse_lock: asyncio.Lock | None = None
        self._initialized = False
        self._init_failed_at: float | None = None
        self._init_lock = asyncio.Lock()
        self._query_vector_cache: OrderedDict[str, tuple[list[float], Any]] = OrderedDict()
        # Bumped by each reset; a query embedded under an older generation is
        # returned but not cached (see ``_query_vectors``).
        self._generation = 0

        # Per-entity locks guarding the membership read-merge-write below; see
        # ``_entity_lock``. Weakly held so the map does not grow with every
        # entity ever touched.
        self._entity_locks: "weakref.WeakValueDictionary[tuple[int, str], asyncio.Lock]" = (
            weakref.WeakValueDictionary()
        )

    # ------------------------------------------------------------------
    # Initialisation (lazy, once per process)
    # ------------------------------------------------------------------

    async def _ensure_initialized(self) -> None:
        """Lazily initialise embeddings and the collection (idempotent)."""
        if self._initialized:
            return
        async with self._init_lock:
            if self._initialized:
                return
            if (
                self._init_failed_at is not None
                and time.monotonic() - self._init_failed_at < _INIT_RETRY_SECONDS
            ):
                raise VectorStoreError(
                    "Entity vector store initialisation failed recently; retrying later",
                    details={"collection": self.collection_name},
                )
            try:
                await self._init_embeddings()
                await self._init_collection()
            except Exception:
                self._init_failed_at = time.monotonic()
                raise
            self._init_failed_at = None
            self._initialized = True

    async def _reset_if_collection_changed(self) -> None:
        """After a failed search: when the collection no longer has the
        dimension this store initialised with, drop the initialisation so the
        next call re-reads the model config and the collection.

        The indexing service recreates the collection for a new model
        (``recreate_on_dimension_mismatch``); a query or connector service
        initialised before would otherwise send old-model vectors, and fail,
        until it restarts. Checked only on failure, so a healthy search costs
        no extra round trip."""
        try:
            info = await self.vector_db_service.get_collection_info(self.collection_name)
        except Exception:
            return
        if not (info.exists and info.dense_dimension) or info.dense_dimension == self._embedding_size:
            return
        self.logger.warning(
            "Entity collection '%s' now has dimension %s, this store %s; re-initialising",
            self.collection_name, info.dense_dimension, self._embedding_size,
        )
        async with self._init_lock:
            # Checked again: a re-initialisation that held the lock meanwhile
            # may already have the new model, which a reset would undo.
            if info.dense_dimension == self._embedding_size:
                return
            self._initialized = False
            self._init_failed_at = None
            self._generation += 1
            self._query_vector_cache.clear()

    async def collection_exists(self) -> bool:
        """Needs no embeddings. Delete paths skip when it is False (nothing to
        delete), and the chat routes hide the entity tools."""
        return await self.vector_db_service.collection_exists(self.collection_name)

    async def _init_embeddings(self) -> None:
        ai_models = await self.config_service.get_config(
            config_node_constants.AI_MODELS.value, use_cache=False
        )
        embedding_configs = (ai_models or {}).get("embedding", [])
        if not embedding_configs:
            self._dense_embeddings = get_default_embedding_model()
            self._model_id = f"default:{DEFAULT_EMBEDDING_MODEL}"
        else:
            config = next(
                (c for c in embedding_configs if c.get("isDefault")), embedding_configs[0]
            )
            self._dense_embeddings = get_embedding_model(config["provider"], config)
            # get_embedding_model uses the first of a comma-separated list.
            models = str((config.get("configuration") or {}).get("model") or "")
            model = next((m.strip() for m in models.split(",") if m.strip()), "")
            self._model_id = f"{config['provider']}:{model}"

        loop = asyncio.get_running_loop()
        sample = await loop.run_in_executor(
            None, self._dense_embeddings.embed_query, "test"
        )
        self._embedding_size = len(sample)

        if self._capabilities.supports_sparse_vectors:
            if self._sparse_lock is None:
                self._sparse_lock = asyncio.Lock()
            async with self._sparse_lock:
                if self._sparse_embedder is None:
                    embedder = await get_default_sparse_embedder()
                    await embedder._ensure_initialized()
                    self._sparse_embedder = embedder

    async def _init_collection(self) -> None:
        info = await self.vector_db_service.get_collection_info(self.collection_name)
        if info.exists and info.dense_dimension and info.dense_dimension != self._embedding_size:
            if not self.recreate_on_dimension_mismatch:
                raise VectorStoreError(
                    f"Entity collection dimension mismatch: existing={info.dense_dimension}, "
                    f"model={self._embedding_size}. The indexing service recreates it.",
                    details={"collection": self.collection_name},
                )
            # The collection is a projection of the graph; the rebuild's
            # marker includes the dimension, so every pass re-runs and fills it.
            self.logger.warning(
                "Recreating entity collection '%s': dimension %s, model now produces %s",
                self.collection_name, info.dense_dimension, self._embedding_size,
            )
            await self.vector_db_service.delete_collection(self.collection_name)
            info = None
        if info is None or not info.exists:
            await self.vector_db_service.create_collection(
                collection_name=self.collection_name,
                config=CollectionConfig(
                    embedding_size=self._embedding_size,
                    enable_sparse=self._capabilities.supports_sparse_vectors,
                ),
            )
            self.logger.info("Created entity vector collection '%s'", self.collection_name)
        # Ensured on every start, not only at creation: create_index is
        # idempotent on every provider, and a process that died between the
        # two left the collection without them. connectorIds and
        # recordGroupIds are top-level payload siblings of metadata (not
        # nested in it) — see ``upsert_entities_batch``.
        for field, schema in [
            ("metadata.orgId", {"type": "keyword"}),
            ("metadata.entityType", {"type": "keyword"}),
            ("metadata.entityId", {"type": "keyword"}),
            ("metadata.level", {"type": "keyword"}),
            (CONNECTOR_IDS_FIELD, {"type": "keyword"}),
            (RECORD_GROUP_IDS_FIELD, {"type": "keyword"}),
        ]:
            await self.vector_db_service.create_index(
                collection_name=self.collection_name,
                field_name=field,
                field_schema=schema,
            )

    async def embedding_fingerprint(self) -> str:
        """``provider:model:dimension`` of the model writing this collection.

        Initialises the store. A different fingerprint means stored vectors
        were embedded by another model and must be rewritten."""
        await self._ensure_initialized()
        return self._fingerprint()

    def _fingerprint(self) -> str:
        return f"{self._model_id}:{self._embedding_size}"

    # ------------------------------------------------------------------
    # Deterministic point ID
    # ------------------------------------------------------------------

    @staticmethod
    def _point_id(org_id: str, entity_type: str, entity_id: str) -> str:
        """Derive a stable UUID5 so the same entity always maps to the same point."""
        namespace = uuid.UUID("6ba7b810-9dad-11d1-80b4-00c04fd430c8")
        return str(uuid.uuid5(namespace, f"{org_id}:{entity_type}:{entity_id}"))

    @staticmethod
    def _entity_key(org_id: str, entity_type: str, entity_id: str) -> str:
        return f"{org_id}:{entity_type}:{entity_id}"

    # ------------------------------------------------------------------
    # Embedding helpers
    # ------------------------------------------------------------------

    async def _embed(self, texts: list[str]) -> list[list[float]]:
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, self._dense_embeddings.embed_documents, texts)

    async def _embed_sparse(self, texts: list[str]) -> list[Any]:
        if not self._sparse_embedder:
            return [None] * len(texts)
        return await self._sparse_embedder.embed_documents(texts)

    # ------------------------------------------------------------------
    # Public API — upsert
    # ------------------------------------------------------------------

    async def upsert_entity(self, entity: EntityRecord) -> None:
        """Embed and upsert a single entity into the entities collection."""
        await self.upsert_entities_batch([entity])

    async def upsert_entities_batch(
        self,
        entities: list[EntityRecord],
        batch_size: int = 64,
        *,
        merge_membership: bool = True,
    ) -> EntityWriteOutcome:
        """Batch-embed and upsert a list of EntityRecord objects.

        Returns what happened to each entity (``EntityWriteOutcome``), counts
        it in ``pipeshub_entity_index_writes_total``, and logs the ids of the
        entities that were not written. A failed batch never raises.

        ``connectorIds``/``recordGroupIds`` are merged with whatever is
        already stored for that entity, not replaced. A point whose stored
        text, metadata, membership and embedding model already match is not
        written; one whose only change is membership has its arrays rewritten
        by id without re-embedding; a point embedded by another model is
        re-embedded.

        Embedding happens before the entities' locks are taken (see
        ``_entity_lock``), so records sharing a popular entity do not wait on
        each other's embedding call. Under the locks the state is read again
        and membership is merged against that read, so two concurrent
        batches cannot drop each other's update; a point whose content
        changed between the two reads is embedded there.

        ``merge_membership=False`` writes ``entity.connector_ids``/
        ``record_group_ids`` as-is instead of unioning with what is already
        stored, for record and record-group points, whose membership is
        exactly their own connector and group. A failed state read then
        rewrites the batch in full rather than skipping it.
        """
        await self._ensure_initialized()
        outcome = EntityWriteOutcome()
        if not entities:
            return outcome
        failed_keys: list[str] = []
        coalesced = self._coalesce_by_key(entities)
        for start in range(0, len(coalesced), batch_size):
            batch = coalesced[start : start + batch_size]
            named = [e for e in batch if e.name.strip()]
            outcome.skipped += len(batch) - len(named)
            for entity in batch:
                if not entity.name.strip():
                    self.logger.warning(
                        "Skipping entity with empty name: %s / %s", entity.entity_type, entity.entity_id,
                    )
            if not named:
                continue
            # Each entity is counted when its write lands; whatever a failure
            # leaves unaccounted is counted failed, so none is counted twice.
            settled: set[str] = set()
            try:
                await self._upsert_batch(named, outcome, settled, merge_membership=merge_membership)
            except _MembershipReadError as exc:
                self.logger.warning("Skipping entity upsert batch, membership unknown: %s", exc)
            except Exception as exc:
                self.logger.error("Failed to upsert entity batch starting at %d: %s", start, exc)
            unwritten = [e for e in named if self._key_of(e) not in settled]
            outcome.failed += len(unwritten)
            failed_keys.extend(f"{e.entity_type.value}/{e.entity_id}" for e in unwritten)
        self._report(outcome, failed_keys)
        return outcome

    def _key_of(self, entity: EntityRecord) -> str:
        return self._point_id(entity.org_id, entity.entity_type.value, entity.entity_id)

    async def _upsert_batch(
        self,
        named: list[EntityRecord],
        outcome: EntityWriteOutcome,
        settled: set[str],
        *,
        merge_membership: bool,
    ) -> None:
        """Write one batch, counting each entity in ``outcome`` and adding its
        point id to ``settled`` only once its write has landed (or none was
        needed). Raises on failure; the caller counts the rest as failed."""
        # Read and embed without the locks. A failed read in merge mode skips
        # the batch: merging against an assumed-empty state would drop every
        # other writer's membership.
        try:
            early = await self._fetch_existing_states(named)
        except _MembershipReadError as exc:
            if merge_membership:
                raise
            self.logger.warning("Entity state read failed; rewriting %d points in full: %s", len(named), exc)
            early = {}
        vectors = await self._embed_changed(named, early)

        async with contextlib.AsyncExitStack() as locks:
            # Sorted, deduped acquisition order rules out circular waits
            # between overlapping concurrent batches.
            for key in sorted({self._entity_key(e.org_id, e.entity_type.value, e.entity_id) for e in named}):
                await locks.enter_async_context(self._entity_lock(key))
            try:
                states = await self._fetch_existing_states(named)
            except _MembershipReadError as exc:
                if merge_membership:
                    raise
                self.logger.warning("Entity state re-read failed; rewriting %d points in full: %s", len(named), exc)
                states = {}

            pending: list[tuple[EntityRecord, list[str], list[str]]] = []
            for entity in named:
                point_id = self._point_id(entity.org_id, entity.entity_type.value, entity.entity_id)
                existing = states.get(point_id)
                if merge_membership and existing is not None:
                    connector_ids = self._union_ids(existing["connectorIds"], entity.connector_ids)
                    record_group_ids = self._union_ids(existing["recordGroupIds"], entity.record_group_ids)
                else:
                    connector_ids = self._union_ids([], entity.connector_ids)
                    record_group_ids = self._union_ids([], entity.record_group_ids)
                if existing is not None and self._same_content(existing, entity, self._fingerprint()):
                    if (
                        list(existing["connectorIds"]) != connector_ids
                        or list(existing["recordGroupIds"]) != record_group_ids
                    ):
                        # The stored vector is still right; only the arrays
                        # move. Written by id: a search-based update cannot
                        # see a point the index has not refreshed yet.
                        await self.vector_db_service.update_payload_by_ids(
                            self.collection_name, [point_id],
                            {CONNECTOR_IDS_FIELD: connector_ids, RECORD_GROUP_IDS_FIELD: record_group_ids},
                        )
                        outcome.membership_only += 1
                    else:
                        outcome.unchanged += 1
                    settled.add(point_id)
                    continue
                pending.append((entity, connector_ids, record_group_ids))

            if not pending:
                return
            # Content changed between the reads: embed it here, under the lock.
            late = [entity for entity, _, _ in pending
                    if self._point_id(entity.org_id, entity.entity_type.value, entity.entity_id) not in vectors]
            if late:
                vectors |= await self._embed_entities(late)

            points = []
            for entity, connector_ids, record_group_ids in pending:
                point_id = self._point_id(entity.org_id, entity.entity_type.value, entity.entity_id)
                dense, sparse = vectors[point_id]
                points.append(VectorPoint(
                    id=point_id,
                    dense_vector=dense,
                    sparse_vector=sparse,
                    payload={
                        "page_content": entity.embedding_text,
                        "metadata": {**entity.to_vector_payload(), EMBEDDING_MODEL_FIELD: self._fingerprint()},
                        CONNECTOR_IDS_FIELD: connector_ids,
                        RECORD_GROUP_IDS_FIELD: record_group_ids,
                    },
                ))
            await self.vector_db_service.upsert_points(collection_name=self.collection_name, points=points)
            outcome.written += len(points)
            settled.update(point.id for point in points)

    async def _embed_changed(
        self, entities: list[EntityRecord], states: dict[str, dict[str, Any]],
    ) -> dict[str, tuple[list[float], Any]]:
        """Vectors, by point id, for the entities whose stored content does
        not already match (per ``states``)."""
        changed = [
            e for e in entities
            if not self._same_content(
                states.get(self._point_id(e.org_id, e.entity_type.value, e.entity_id)) or {}, e, self._fingerprint(),
            )
        ]
        return await self._embed_entities(changed) if changed else {}

    async def _embed_entities(self, entities: list[EntityRecord]) -> dict[str, tuple[list[float], Any]]:
        texts = [e.embedding_text for e in entities]
        dense = await self._embed(texts)
        sparse = await self._embed_sparse(texts)
        return {
            self._point_id(e.org_id, e.entity_type.value, e.entity_id): (d, sp)
            for e, d, sp in zip(entities, dense, sparse)
        }

    def _report(self, outcome: EntityWriteOutcome, failed_keys: list[str]) -> None:
        for name in ("written", "membership_only", "unchanged", "skipped", "failed"):
            entity_index_metrics.record_writes("upsert", name, getattr(outcome, name))
        if failed_keys:
            shown = ", ".join(failed_keys[:_LOGGED_FAILED_IDS])
            more = len(failed_keys) - _LOGGED_FAILED_IDS
            self.logger.warning(
                "Entity points not written (%d): %s%s",
                len(failed_keys), shown, f" and {more} more" if more > 0 else "",
            )

    @staticmethod
    def _coalesce_by_key(entities: list["EntityRecord"]) -> list["EntityRecord"]:
        """Collapse repeats of one entity, unioning their membership.

        Two extracted names in a record can resolve to the same canonical node,
        so one batch can carry that entity twice. Both would build the same
        deterministic point id, and the later one would overwrite the earlier
        one's membership instead of adding to it.
        """
        merged: dict[tuple[str, str, str], EntityRecord] = {}
        for entity in entities:
            key = (entity.org_id, entity.entity_type.value, entity.entity_id)
            first = merged.get(key)
            if first is None:
                merged[key] = entity
                continue
            merged[key] = first.model_copy(update={
                "connector_ids": EntityVectorStore._union_ids(
                    first.connector_ids, entity.connector_ids
                ),
                "record_group_ids": EntityVectorStore._union_ids(
                    first.record_group_ids, entity.record_group_ids
                ),
                "aliases": EntityVectorStore._union_ids(
                    first.aliases, entity.aliases
                ),
            })
        return list(merged.values())

    def _entity_lock(self, key: str) -> asyncio.Lock:
        """Get-or-create the lock for an entity key on the running event loop.

        No guard lock is needed: there is no ``await`` between the lookup and
        the insert, so this is atomic on a single-threaded event loop. Keyed
        by ``(loop, entity)`` for the same reason as ``membership.py``'s
        ``_vrid_lock`` — indexing runs some work on a worker-thread loop and
        some on the main loop, and a single shared lock per key would turn a
        cross-loop call into a hard error.
        """
        try:
            loop_id = id(asyncio.get_running_loop())
        except RuntimeError:
            loop_id = 0
        lock_map_key = (loop_id, key)
        lock = self._entity_locks.get(lock_map_key)
        if lock is None:
            lock = asyncio.Lock()
            self._entity_locks[lock_map_key] = lock
        return lock

    async def _fetch_existing_states(
        self, entities: list[EntityRecord]
    ) -> dict[str, dict[str, Any]]:
        """Each entity's stored membership and text, keyed by point id.

        Read by id, not by search: a search does not see writes an OpenSearch
        index has not refreshed yet, so a merge against it would drop an
        update made seconds earlier. ``exists`` is False for an entity's
        first-ever write. A lookup *failure* raises ``_MembershipReadError``:
        the point ID is deterministic, so upserting against an assumed-empty
        state would replace the stored membership with only what this caller
        knows about. Caller must hold the entities' locks (``_entity_lock``)
        across this read and the eventual write.
        """
        ids = [
            self._point_id(e.org_id, e.entity_type.value, e.entity_id) for e in entities
        ]
        try:
            points = await self.vector_db_service.retrieve_points(self.collection_name, ids)
        except Exception as exc:
            raise _MembershipReadError(
                f"membership read failed for {len(ids)} entities"
            ) from exc
        by_id = {point.id: point.payload or {} for point in points}
        states: dict[str, dict[str, Any]] = {}
        for point_id in ids:
            payload = by_id.get(point_id)
            if payload is None:
                states[point_id] = {
                    "exists": False,
                    "connectorIds": [],
                    "recordGroupIds": [],
                    "page_content": None,
                    "metadata": None,
                }
                continue
            states[point_id] = {
                "exists": True,
                "connectorIds": list(payload.get(CONNECTOR_IDS_FIELD) or []),
                "recordGroupIds": list(payload.get(RECORD_GROUP_IDS_FIELD) or []),
                "page_content": payload.get("page_content"),
                "metadata": _entity_metadata(payload),
            }
        return states

    @staticmethod
    def _same_content(existing: dict[str, Any], entity: EntityRecord, fingerprint: str) -> bool:
        """True when the stored text and metadata are what ``entity`` would
        write and the stored vector came from the current model, so it needs
        no re-embedding."""
        if not existing.get("exists") or existing.get("page_content") != entity.embedding_text:
            return False
        metadata = dict(existing.get("metadata") or {})
        return (
            metadata.pop(EMBEDDING_MODEL_FIELD, None) == fingerprint
            and metadata == entity.to_vector_payload()
        )

    @staticmethod
    def _union_ids(existing: list[str], new: list[str]) -> list[str]:
        """Dedup, order-preserving union."""
        seen: set[str] = set()
        merged: list[str] = []
        for value in (*existing, *new):
            if value and value not in seen:
                seen.add(value)
                merged.append(value)
        return merged

    # ------------------------------------------------------------------
    # Public API — delete
    # ------------------------------------------------------------------

    async def delete_entity(self, org_id: str, entity_type: str, entity_id: str) -> None:
        """Delete a single entity point from the collection.

        Filters on the same ``(orgId, entityType, entityId)`` triple the
        point ID is derived from (see ``_point_id``) — scoping by
        ``entityId``+``orgId`` alone would also match a different-typed
        entity that happened to reuse the same graph-node key.

        Needs no embeddings, so a down or misconfigured embedding provider
        cannot fail a record delete.
        """
        try:
            if not await self.collection_exists():
                return
            filter_expr = await self.vector_db_service.filter_collection(
                must={
                    "metadata.entityId": entity_id,
                    "metadata.entityType": entity_type,
                    "metadata.orgId": org_id,
                }
            )
            await self.vector_db_service.delete_points(self.collection_name, filter_expr)
            self.logger.info(
                "Deleted entity %s/%s from vector store", entity_type, entity_id
            )
        except Exception as exc:
            self.logger.error("Failed to delete entity %s: %s", entity_id, exc)

    async def delete_entities(
        self, org_id: str, entity_type: str, entity_ids: list[str],
    ) -> None:
        """Delete ``entity_ids`` of one type in one org. Raises on failure.

        Needs no embeddings."""
        ids = [i for i in entity_ids if i]
        # Providers drop empty filter values, which would widen the delete.
        if not org_id or not entity_type or not ids:
            return
        if not await self.collection_exists():
            return
        await self.vector_db_service.delete_points(
            self.collection_name, await self._entities_filter(org_id, entity_type, ids),
        )

    async def page_entity_points(
        self,
        org_id: str,
        entity_types: list[str],
        *,
        offset: str | None = None,
        limit: int = 500,
    ) -> tuple[list[EntityPointRef], str | None]:
        """One page of the org's points of ``entity_types``, and the offset of
        the next (``None`` at the end). Needs no embeddings."""
        if not org_id:
            raise ValueError("page_entity_points needs an org")
        if not entity_types or not await self.collection_exists():
            return [], None
        page = await self.vector_db_service.scroll(
            collection_name=self.collection_name,
            scroll_filter=await self.vector_db_service.filter_collection(
                must={"metadata.orgId": org_id, "metadata.entityType": list(entity_types)},
            ),
            limit=limit,
            offset=offset,
            with_payload=["metadata.entityId", "metadata.entityType", "metadata.level"],
        )
        refs = []
        for point in page.points:
            meta = _entity_metadata(point.payload)
            if meta.get("entityId") and meta.get("entityType"):
                refs.append(EntityPointRef(meta["entityType"], meta["entityId"], meta.get("level")))
        return refs, page.next_offset

    def offset_after_delete(self, next_offset: str | None, deleted: int) -> str | None:
        """``next_offset`` from ``page_entity_points`` after ``deleted`` points
        of that page were deleted (see ``IVectorDBService.scroll_offset_after_delete``)."""
        return self.vector_db_service.scroll_offset_after_delete(next_offset, deleted)

    async def delete_entities_for_org(self, org_id: str) -> None:
        """Remove ALL entity vectors for an organisation (e.g. on org deletion)."""
        try:
            if not await self.collection_exists():
                return
            filter_expr = await self.vector_db_service.filter_collection(
                must={"metadata.orgId": org_id}
            )
            await self.vector_db_service.delete_points(self.collection_name, filter_expr)
            self.logger.info("Deleted all entity vectors for org %s", org_id)
        except Exception as exc:
            self.logger.error("Failed to delete entity vectors for org %s: %s", org_id, exc)

    async def delete_entities_by_connector(
        self,
        org_id: str,
        connector_id: str,
        record_group_ids: list[str] | None = None,
        membership_lookup: MembershipLookup | None = None,
    ) -> None:
        """Remove *connector_id*'s footprint from the entities collection.

        Taxonomy entities shared with another connector lose this connector
        and its record groups; entities that only this connector referenced,
        and its record and record-group points, are deleted.
        *record_group_ids* are the connector's record groups as the graph
        knew them before deletion; without them they are recovered from the
        connector's points.

        *membership_lookup* returns, from the graph, the connectors and record
        groups that still reach given taxonomy entities. Stored membership can
        miss a connector (two indexing workers merging concurrently), which
        makes a shared entity look exclusive; with the lookup such a point is
        rewritten from the graph instead of deleted.

        Raises on failure so the caller can report it and retry; a retry
        resumes where the previous attempt stopped. Needs no embeddings.
        """
        if not await self.collection_exists():
            return
        await self._shrink_connector_membership(
            org_id, connector_id, record_group_ids, membership_lookup=membership_lookup,
        )
        self.logger.info(
            "Removed connector-scoped entities | org=%s connector=%s", org_id, connector_id,
        )

    async def _shrink_connector_membership(
        self,
        org_id: str,
        connector_id: str,
        record_group_ids: list[str] | None = None,
        page_size: int = 100,
        *,
        membership_lookup: MembershipLookup | None = None,
    ) -> None:
        """Strip the connector from shared taxonomy points, then delete the rest.

        1. Collect the connector's record group ids: the graph's, plus those on
           its RECORD_GROUP points (and on its RECORD points when the graph
           gave none). These points are only deleted in step 3, so a retry
           after a failure still finds them.
        2. Page through the taxonomy points naming the connector. Each page is
           re-read by id under the entities' locks: a shared point gets its
           stripped membership written by id (no re-embedding), an exclusive
           one is deleted. A changed page leaves the filter, so paging starts
           over from the top and never goes deep (Redis caps search offsets at
           10k). A page the fresh read shows is already done (search lagging
           by-id writes, as on OpenSearch) is stepped past by offset.
        3. Delete what is left of the connector in one filtered call: its
           RECORD and RECORD_GROUP points, and any point without a type.
           Taxonomy points are never swept; one that a concurrently indexed
           record re-tagged with the connector is left and logged.

        The locks serialise this with writers in the same indexing process
        only; another instance can still re-tag a point between the re-read
        and the write. A page whose writes do not take is retried, then
        raises, as does a full page of points without an id. Two cleanups of
        one connector running at once on Redis can step past each other's
        pages with a numeric offset; the second run's final check logs what
        is left.
        """
        from app.models.entities import EntityType

        scoped_types = [EntityType.RECORD.value, EntityType.RECORD_GROUP.value]
        group_ids = set(record_group_ids or ())
        group_ids |= await self._connector_group_ids(
            org_id,
            connector_id,
            scoped_types if record_group_ids is None else [EntityType.RECORD_GROUP.value],
            page_size,
        )

        taxonomy_filter = await self.vector_db_service.filter_collection(
            must={"metadata.orgId": org_id, CONNECTOR_IDS_FIELD: connector_id},
            must_not={"metadata.entityType": scoped_types},
        )
        previous_page: set[str] = set()
        attempts = 0
        offset: str | None = None
        # Points a fresh read showed are done (stripped or gone). Search lags
        # by-id writes on OpenSearch, so they keep coming back until the next
        # refresh; skipping them costs no locks and no reads.
        handled: set[str] = set()
        while True:
            page = await self.vector_db_service.scroll(
                collection_name=self.collection_name,
                scroll_filter=taxonomy_filter,
                limit=page_size,
                offset=offset,
                with_payload=[
                    "metadata.entityId", "metadata.entityType",
                    CONNECTOR_IDS_FIELD, RECORD_GROUP_IDS_FIELD,
                ],
            )
            if not page.points:
                break
            page_ids = {point.id for point in page.points}
            attempts = attempts + 1 if page_ids == previous_page else 1
            if attempts > _CLEANUP_PAGE_ATTEMPTS:
                raise RuntimeError(
                    f"Connector cleanup made no progress on {len(page_ids)} points "
                    f"(org={org_id} connector={connector_id}); a retry resumes it"
                )
            previous_page = page_ids
            result = await self._strip_or_delete(
                page.points, org_id, connector_id, group_ids, membership_lookup, handled=handled,
            )
            if result is _Page.ALREADY_DONE:
                # Search lags by-id writes (OpenSearch refreshes every 30 s), so
                # a page this run already handled can come back; step past it.
                offset = page.next_offset
                if offset is None:
                    break
                continue
            if result is _Page.CHANGED:
                # The page left the filter; start over so paging never goes deep.
                offset = None
                continue
            # Only points without an entity id or type are on this page. A
            # short page is everything left in the filter, so the sweep below
            # removes them; a full one may hide shared points behind it.
            if len(page.points) < page_size:
                break
            raise RuntimeError(
                f"Connector cleanup found {len(page.points)} points without an entity "
                f"id or type (org={org_id} connector={connector_id})"
            )

        # Never taxonomy types: a record indexed while this ran can re-tag a
        # shared entity with the connector, and deleting it here would drop
        # every other connector's membership with it.
        shared_types = [
            t.value for t in EntityType if t.value not in scoped_types
        ]
        remaining = await self.vector_db_service.filter_collection(
            must={"metadata.orgId": org_id, CONNECTOR_IDS_FIELD: connector_id},
            must_not={"metadata.entityType": shared_types},
        )
        await self.vector_db_service.delete_points(self.collection_name, remaining)
        shared_filter = await self.vector_db_service.filter_collection(
            must={
                "metadata.orgId": org_id,
                CONNECTOR_IDS_FIELD: connector_id,
                "metadata.entityType": shared_types,
            },
        )
        leftover = await self.vector_db_service.scroll(
            collection_name=self.collection_name,
            scroll_filter=shared_filter,
            limit=1,
            with_payload=["metadata.entityId"],
        )
        if leftover.points:
            self.logger.warning(
                "Shared entities still name deleted connector %s in org %s after cleanup "
                "(re-tagged by a record indexed meanwhile); left in place",
                connector_id, org_id,
            )

    async def _connector_group_ids(
        self, org_id: str, connector_id: str, entity_types: list[str], page_size: int,
    ) -> set[str]:
        filter_expr = await self.vector_db_service.filter_collection(
            must={
                "metadata.orgId": org_id,
                CONNECTOR_IDS_FIELD: connector_id,
                "metadata.entityType": entity_types,
            },
        )
        group_ids: set[str] = set()
        offset: str | None = None
        while True:
            page = await self.vector_db_service.scroll(
                collection_name=self.collection_name,
                scroll_filter=filter_expr,
                limit=page_size,
                offset=offset,
                with_payload=[RECORD_GROUP_IDS_FIELD],
            )
            for point in page.points:
                group_ids.update(g for g in (point.payload.get(RECORD_GROUP_IDS_FIELD) or []) if g)
            offset = page.next_offset
            if offset is None:
                return group_ids

    async def _strip_or_delete(
        self,
        points: list[VectorPoint],
        org_id: str,
        connector_id: str,
        group_ids: set[str],
        membership_lookup: MembershipLookup | None = None,
        *,
        handled: set[str] | None = None,
    ) -> _Page:
        """Apply step 2 to one page.

        The page's points are read again by id under their entities' locks,
        and the stripped membership is computed from that read, not from the
        scroll: a record indexed since the page was scrolled keeps the
        connector it just added. Stripped membership is written by id, which
        every backend applies at once; a search-based update would skip a
        point rewritten since the last OpenSearch refresh.
        """
        refs = [
            (point.id, meta["entityType"], meta["entityId"])
            for point in points
            if (meta := _entity_metadata(point.payload)).get("entityId") and meta.get("entityType")
        ]
        if not refs:
            return _Page.NO_ENTITIES
        handled = handled if handled is not None else set()
        refs = [ref for ref in refs if ref[0] not in handled]
        if not refs:
            return _Page.ALREADY_DONE
        async with contextlib.AsyncExitStack() as locks:
            for key in sorted({self._entity_key(org_id, entity_type, entity_id) for _, entity_type, entity_id in refs}):
                await locks.enter_async_context(self._entity_lock(key))
            fresh = {
                point.id: point.payload or {}
                for point in await self.vector_db_service.retrieve_points(
                    self.collection_name, [point_id for point_id, _, _ in refs],
                )
            }
            stripped: dict[tuple[tuple[str, ...], tuple[str, ...]], list[str]] = {}
            exclusive: dict[str, list[str]] = {}
            for point_id, entity_type, entity_id in refs:
                payload = fresh.get(point_id)
                stored = list((payload or {}).get(CONNECTOR_IDS_FIELD) or [])
                if payload is None or connector_id not in stored:
                    # Verified done: gone, or already stripped. Only such
                    # points are skipped later; a written one is re-checked.
                    handled.add(point_id)
                    continue
                connectors = tuple(c for c in stored if c != connector_id)
                if not connectors:
                    exclusive.setdefault(entity_type, []).append(entity_id)
                    continue
                groups = tuple(g for g in (payload.get(RECORD_GROUP_IDS_FIELD) or []) if g not in group_ids)
                stripped.setdefault((connectors, groups), []).append(point_id)
            if not stripped and not exclusive:
                return _Page.ALREADY_DONE

            for (connectors, groups), point_ids in stripped.items():
                await self.vector_db_service.update_payload_by_ids(
                    self.collection_name, point_ids,
                    {CONNECTOR_IDS_FIELD: list(connectors), RECORD_GROUP_IDS_FIELD: list(groups)},
                )
            if exclusive and membership_lookup is not None:
                exclusive = await self._rewrite_still_reached(
                    org_id, exclusive, membership_lookup, connector_id, group_ids,
                    point_ids={(t, e): point_id for point_id, t, e in refs},
                )
            for entity_type, entity_ids in exclusive.items():
                filter_expr = await self._entities_filter(org_id, entity_type, entity_ids)
                await self.vector_db_service.delete_points(self.collection_name, filter_expr, refresh=True)
        return _Page.CHANGED

    async def _rewrite_still_reached(
        self,
        org_id: str,
        exclusive: dict[str, list[str]],
        membership_lookup: MembershipLookup,
        connector_id: str,
        group_ids: set[str],
        point_ids: dict[tuple[str, str], str] | None = None,
    ) -> dict[str, list[str]]:
        """Rewrite, from the graph, the exclusive-looking points that other
        connectors' records still reach. Returns the ids left to delete.

        The deleted connector and its groups are dropped from what the graph
        reports (a sync racing the deletion could still name them); written
        back, they would keep the point in the cleanup filter forever."""
        graph = await membership_lookup([
            {"id": entity_id, "type": entity_type}
            for entity_type, entity_ids in exclusive.items()
            for entity_id in entity_ids
        ])
        to_delete: dict[str, list[str]] = {}
        for entity_type, entity_ids in exclusive.items():
            for entity_id in entity_ids:
                membership = graph.get((entity_type, entity_id)) or {}
                connectors = [c for c in membership.get("connectorIds") or [] if c != connector_id]
                if not connectors:
                    to_delete.setdefault(entity_type, []).append(entity_id)
                    continue
                groups = [g for g in membership.get("recordGroupIds") or [] if g not in group_ids]
                point_id = (point_ids or {}).get((entity_type, entity_id)) or self._point_id(
                    org_id, entity_type, entity_id,
                )
                await self.vector_db_service.update_payload_by_ids(
                    self.collection_name, [point_id],
                    {CONNECTOR_IDS_FIELD: connectors, RECORD_GROUP_IDS_FIELD: groups},
                )
        return to_delete

    async def _entities_filter(
        self, org_id: str, entity_type: str, entity_ids: list[str],
    ) -> FilterExpression:
        return await self.vector_db_service.filter_collection(
            must={
                "metadata.orgId": org_id,
                "metadata.entityType": entity_type,
                "metadata.entityId": entity_ids,
            },
        )

    # ------------------------------------------------------------------
    # Public API — search
    # ------------------------------------------------------------------

    async def search_entities(
        self,
        query: str,
        org_id: str,
        accessible_record_group_ids: set[str],
        accessible_connector_ids: set[str],
        entity_types: list[str] | None = None,
        top_k: int = 10,
        score_threshold: float = _CONFIDENCE_THRESHOLD,
        *,
        allow_org_wide: bool = False,
    ) -> list[dict[str, Any]]:
        """Semantically search for entities matching *query*, scoped to what
        the caller can reach.

        *accessible_record_group_ids*/*accessible_connector_ids* are
        **required** (plain id sets, not an ACL object — this class stays
        graph-free) so a direct call cannot silently produce an unfiltered,
        org-wide search. Only ``allow_org_wide=True`` drops the membership
        filter, for callers that verify every hit against the graph (see
        ``app.modules.retrieval.entity_permissions``) — stored membership
        can be empty or stale, so it is a recall hint, never an access check.

        Filter shape: ``must={orgId[, entityType]}`` AND
        ``should={recordGroupIds, connectorIds}`` (at least one must match —
        no ``min_should_match``, since KB/Collection records have no record
        group by design and are reachable only via ``connectorIds``; see
        ``services/vector_db/membership.py``).

        Returns a list of dicts:
            {entityId, entityType, name, score, connectorIds, recordGroupIds}

        Raises on a vector DB failure so callers can tell it apart from "no
        match".
        """
        await self._ensure_initialized()

        if not query.strip() or not org_id:
            return []

        if not allow_org_wide and not accessible_record_group_ids and not accessible_connector_ids:
            # Omitting the should-group here would leave only the org/type
            # must-filter, silently widening back to an org-wide search.
            return []

        dense_vec, sparse_vec = await self._query_vectors(query)

        must_conditions: dict[str, Any] = {"metadata.orgId": org_id}
        if entity_types:
            must_conditions["metadata.entityType"] = entity_types  # list → "any of" filter

        should_conditions: dict[str, Any] = {}
        if accessible_record_group_ids:
            should_conditions[RECORD_GROUP_IDS_FIELD] = sorted(accessible_record_group_ids)
        if accessible_connector_ids:
            should_conditions[CONNECTOR_IDS_FIELD] = sorted(accessible_connector_ids)

        filter_expr = await self.vector_db_service.filter_collection(
            must=must_conditions,
            should=should_conditions,
        )

        from app.services.vector_db.models import FusionMethod, HybridSearchRequest

        request = HybridSearchRequest(
            dense_query=dense_vec,
            sparse_query=sparse_vec,
            text_query=query,
            filter=filter_expr,
            limit=top_k,
            fusion_method=FusionMethod.RRF,
            with_payload=True,
        )

        try:
            batch_results: list[list[SearchResult]] = (
                await self.vector_db_service.query_nearest_points(
                    collection_name=self.collection_name,
                    requests=[request],
                )
            )
        except Exception as exc:
            self.logger.error("Entity search failed for query '%s': %s", query, exc)
            await self._reset_if_collection_changed()
            raise

        results_for_query = batch_results[0] if batch_results else []
        output: list[dict[str, Any]] = []
        for hit in results_for_query:
            if hit.score < score_threshold:
                continue
            meta = _entity_metadata(hit.payload)
            output.append(
                {
                    "entityId": meta.get("entityId"),
                    "entityType": meta.get("entityType"),
                    "name": meta.get("name", hit.payload.get("page_content", "")),
                    "canonicalName": meta.get("canonicalName"),
                    "aliases": meta.get("aliases") or [],
                    "score": round(hit.score, 4),
                    "connectorIds": hit.payload.get(CONNECTOR_IDS_FIELD) or [],
                    "recordGroupIds": hit.payload.get(RECORD_GROUP_IDS_FIELD) or [],
                }
            )
        return output

    async def _query_vectors(self, query: str) -> tuple[list[float], Any]:
        """Dense and sparse vectors for ``query``, cached: one entity search
        runs up to three passes over the same query."""
        cached = self._query_vector_cache.get(query)
        if cached is not None:
            self._query_vector_cache.move_to_end(query)
            return cached
        generation = self._generation
        loop = asyncio.get_running_loop()
        dense_vec = await loop.run_in_executor(None, self._dense_embeddings.embed_query, query)
        sparse_vec = None
        if self._sparse_embedder:
            sparse_results = await self._sparse_embedder.embed_documents([query])
            sparse_vec = sparse_results[0] if sparse_results else None
        if generation != self._generation:
            # The store reset while this was in flight: the vector is the old
            # model's, and cached it would fail every later search for the text.
            return dense_vec, sparse_vec
        self._query_vector_cache[query] = (dense_vec, sparse_vec)
        if len(self._query_vector_cache) > _QUERY_VECTOR_CACHE_SIZE:
            self._query_vector_cache.popitem(last=False)
        return dense_vec, sparse_vec

    async def find_best_matches(
        self,
        names: list[str],
        org_id: str,
        entity_type: str,
        level: str | None = None,
    ) -> list[dict[str, Any] | None]:
        """The single nearest existing entity for each of ``names``, within
        one org, one entity type and (for subcategories) one level.

        Used by ``app.modules.entity_resolution`` to pick the winner offered
        to the merge-decision model. There is deliberately no score
        threshold: the model decides every pair, so the ranking only has to
        put the best candidate first. Hybrid dense + BM25 with RRF is used
        for that, since lexical near-variants are the common case.

        Returns one entry per input name, ``None`` when the name is blank or
        no point of that type/level exists yet. Raises on a vector DB
        failure so the caller can count it and fall back.
        """
        await self._ensure_initialized()
        cleaned = [(name or "").strip() for name in names]
        results: list[dict[str, Any] | None] = [None] * len(cleaned)
        if not org_id or not entity_type:
            return results
        indices = [i for i, name in enumerate(cleaned) if name]
        if not indices:
            return results

        texts = [cleaned[i] for i in indices]
        dense_vecs = await self._embed(texts)
        sparse_vecs = await self._embed_sparse(texts)

        must_conditions: dict[str, Any] = {
            "metadata.orgId": org_id,
            "metadata.entityType": entity_type,
        }
        if level:
            must_conditions["metadata.level"] = level
        filter_expr = await self.vector_db_service.filter_collection(must=must_conditions)

        from app.services.vector_db.models import FusionMethod, HybridSearchRequest

        requests = [
            HybridSearchRequest(
                dense_query=dense,
                sparse_query=sparse,
                text_query=text,
                filter=filter_expr,
                limit=1,
                fusion_method=FusionMethod.RRF,
                with_payload=True,
            )
            for text, dense, sparse in zip(texts, dense_vecs, sparse_vecs)
        ]
        try:
            batch_results = await self.vector_db_service.query_nearest_points(
                collection_name=self.collection_name, requests=requests,
            )
        except Exception:
            await self._reset_if_collection_changed()
            raise

        for position, index in enumerate(indices):
            hits = batch_results[position] if position < len(batch_results) else []
            if not hits:
                continue
            hit = hits[0]
            meta = _entity_metadata(hit.payload)
            if meta.get("orgId") != org_id or meta.get("entityType") != entity_type:
                continue
            if (meta.get("level") or None) != (level or None):
                continue
            entity_id = meta.get("entityId")
            if not entity_id:
                continue
            results[index] = {
                "entityId": entity_id,
                "entityType": meta.get("entityType"),
                "name": meta.get("name") or hit.payload.get("page_content") or entity_id,
                "aliases": list(meta.get("aliases") or []),
                "level": meta.get("level"),
                "score": round(hit.score, 4),
            }
        return results
