import json
from abc import ABC, abstractmethod
from collections.abc import Mapping, Sequence
from typing import Dict, List, Optional, Union

from app.services.vector_db.models import (
    CollectionConfig,
    FilterExpression,
    FilterMode,
    FilterValue,
    HybridSearchRequest,
    ScrollResult,
    SearchFailureKind,
    SearchResult,
    VectorCollectionInfo,
    VectorDBCapabilities,
    VectorDBHealth,
    VectorDBMemoryReport,
    VectorPoint,
)


class IVectorDBService(ABC):
    """Abstract interface for vector database providers.

    All data-path methods are **async** so they can be safely awaited in
    async request handlers and background tasks without blocking the event loop.

    Providers
    ---------
    Qdrant   – sparse + dense vectors, native RRF prefetch/fusion
    OpenSearch – dense KNN + server-side BM25, RRF via score-ranker pipeline
    Redis    – dense KNN + server-side BM25, RRF via FT.HYBRID (Redis >= 8.4)

    Capability negotiation
    ----------------------
    Call ``get_capabilities()`` at startup to learn whether a provider accepts
    client-computed sparse vectors or relies on server-side text search for the
    lexical leg.  Indexing and retrieval paths branch on these flags so the
    *call-site code never hard-codes provider names*.
    """

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    @abstractmethod
    async def connect(self) -> None:
        raise NotImplementedError

    @abstractmethod
    async def disconnect(self) -> None:
        raise NotImplementedError

    # ------------------------------------------------------------------
    # Identity
    # ------------------------------------------------------------------

    @abstractmethod
    def get_service_name(self) -> str:
        raise NotImplementedError

    @abstractmethod
    def get_service(self) -> "IVectorDBService":
        raise NotImplementedError

    @abstractmethod
    def get_service_client(self) -> object:
        """Return the underlying raw client (for langchain adapters if needed)."""
        raise NotImplementedError

    # ------------------------------------------------------------------
    # Capabilities and health (new)
    # ------------------------------------------------------------------

    #: Whether a search request may carry a ``ScopeFilter`` (``filters.ScopeFilter``); a store that cannot refuses one.
    supports_scope_filter = False

    @abstractmethod
    def get_capabilities(self) -> VectorDBCapabilities:
        """Return static capability descriptor for this provider."""
        raise NotImplementedError

    @abstractmethod
    async def health_check(self) -> VectorDBHealth:
        """Perform a live health check and return a structured result."""
        raise NotImplementedError

    async def memory_report(self) -> VectorDBMemoryReport | None:
        """Estimate the memory search needs to stay fast, against what the store has.

        None when the provider cannot estimate it. Concrete so providers that
        manage their own memory (OpenSearch, Redis) need not implement it.
        """
        return None

    def transient_search_error(self, error: BaseException) -> SearchFailureKind | None:
        """Whether ``error``, raised by this provider's client during a search,
        is one a second attempt may get past: a timeout or the store being
        briefly unreachable. None for everything else (a bad request, auth),
        which a retry would only repeat.

        Concrete so a provider with nothing client-specific to classify keeps
        the builtin ``TimeoutError``/``ConnectionError`` handling of
        ``search_retry`` and nothing more.
        """
        return None

    # ------------------------------------------------------------------
    # Collection management
    # ------------------------------------------------------------------

    @abstractmethod
    async def create_collection(
        self,
        collection_name: str = "records",
        config: Optional[CollectionConfig] = None,
    ) -> None:
        raise NotImplementedError

    async def reconcile_storage_layout(
        self,
        collection_name: str = "records",
        config: Optional[CollectionConfig] = None,
    ) -> Optional[str]:
        """Advance an existing collection one step toward ``config``'s storage layout.

        Returns the field that was changed, or None when nothing was applied.
        Intentionally concrete: providers that cannot retune a live collection
        inherit the no-op rather than being forced to stub it out.
        """
        return None

    async def reconcile_lexical_scoring(
        self,
        collection_name: str = "records",
        config: Optional[CollectionConfig] = None,
    ) -> Optional[str]:
        """Bring an existing collection's keyword scoring in line with ``config``.

        Unlike ``reconcile_storage_layout`` this must be cheap enough to run on
        every startup: it may change how stored data is scored, never rewrite
        it. Returns the setting that was changed, or None when nothing was.
        Concrete for the same reason as ``reconcile_storage_layout``.
        """
        return None

    @abstractmethod
    async def get_collections(self) -> object:
        raise NotImplementedError

    @abstractmethod
    async def get_collection(self, collection_name: str) -> object:
        """Return raw provider-specific collection info (for legacy callers)."""
        raise NotImplementedError

    @abstractmethod
    async def get_collection_info(self, collection_name: str) -> VectorCollectionInfo:
        """Return normalised, provider-neutral collection metadata."""
        raise NotImplementedError

    async def org_holds_points(self) -> bool:
        """For providers with ``spaces_per_org``: whether the calling org holds any point (of any collection, deleted-but-kept rows
        included). A read only; an org that holds none can change its embedding model."""
        raise NotImplementedError

    async def org_space_accepts(self, identity: str | None, dimension: int, current_identity: str | None = None) -> bool:
        """For providers with ``spaces_per_org``: whether the calling org's active space already takes vectors of this model identity
        (``provider:model:dimension``, None when unknown) and dimension, so changing to it changes nothing. ``current_identity`` is the one
        configured now: a nameless space that holds points is read as named by it. A read only."""
        raise NotImplementedError

    async def get_collection_stamp(self, collection_name: str) -> str | None:
        """The collection's stamp. Only for providers whose capabilities say ``stamps_in_catalog``."""
        raise NotImplementedError

    async def set_collection_stamp(self, collection_name: str, stamp: str) -> str:
        """Sets the stamp unless the collection has one; answers the stamp in force. Only for ``stamps_in_catalog`` providers."""
        raise NotImplementedError

    async def orgs_of_virtual_records(self, virtual_record_ids: list[str]) -> dict[str, set[str]] | None:
        """The orgs holding points of each virtual record (absent: none), or None when the provider cannot say without being told an org."""
        return None

    @abstractmethod
    async def collection_exists(self, collection_name: str) -> bool:
        raise NotImplementedError

    @abstractmethod
    async def delete_collection(self, collection_name: str) -> None:
        raise NotImplementedError

    @abstractmethod
    async def create_index(
        self,
        collection_name: str,
        field_name: str,
        field_schema: dict,
    ) -> None:
        raise NotImplementedError

    # ------------------------------------------------------------------
    # Filter construction
    # ------------------------------------------------------------------

    @abstractmethod
    async def filter_collection(
        self,
        filter_mode: Union[str, FilterMode] = FilterMode.MUST,
        must: Optional[Dict[str, FilterValue]] = None,
        should: Optional[Dict[str, FilterValue]] = None,
        must_not: Optional[Dict[str, FilterValue]] = None,
        min_should_match: Optional[int] = None,
        max_values: Optional[Dict[str, int]] = None,
        **filters: FilterValue,
    ) -> FilterExpression:
        """Build a provider-native filter.

        ``max_values`` bounds an array field's length (``{"connectorIds": 1}``
        matches a point whose only owner is the one named in ``must``). It is
        always ANDed, never an alternative to a value match.
        """
        raise NotImplementedError

    # ------------------------------------------------------------------
    # Data operations — all async
    # ------------------------------------------------------------------

    @abstractmethod
    async def scroll(
        self,
        collection_name: str,
        scroll_filter: FilterExpression,
        limit: int,
        offset: Optional[str] = None,
        with_payload: Optional[List[str]] = None,
    ) -> ScrollResult:
        """Page through matching points.

        ``with_payload`` restricts which payload fields come back (dotted paths
        such as ``metadata.virtualRecordId``). Callers that need a couple of
        fields should pass it: the default pulls the whole payload including
        ``page_content``, which on a large scan is megabytes of chunk text
        transferred and discarded.
        """
        raise NotImplementedError

    def scroll_offset_after_delete(
        self, next_offset: Optional[str], deleted: int,
    ) -> Optional[str]:
        """``next_offset`` from ``scroll``, adjusted after the caller deleted
        ``deleted`` of the points that page returned.

        A key-based cursor (the next id, a ``search_after`` value) is
        unaffected. A positional one must step back, or the next page skips
        as many points as were deleted.
        """
        return next_offset

    @abstractmethod
    async def retrieve_points(
        self,
        collection_name: str,
        ids: List[str],
    ) -> List[VectorPoint]:
        """Points by id, without vectors; ids with no point are omitted.

        Unlike ``scroll``, this reads writes that are not yet searchable (an
        OpenSearch index only refreshes every 30s), so a read-modify-write can
        use it without losing an update made moments earlier.
        """
        raise NotImplementedError

    #: The store takes ``wait=False`` on ``update_payloads_by_ids`` (the write returns once queued, not once applied). A caller that uses it
    #: calls ``flush_writes`` and then reads the points back before it relies on the write.
    deferred_writes: bool = False

    async def flush_writes(self, collection_name: str, point_ids: Sequence[str] = ()) -> None:
        """Return once the writes queued so far on these points (``wait=False``) have been applied, as far as the store can tell. A
        best effort: the caller still reads the points back (the store may order queued writes of different shards as it likes)."""

    async def make_searchable(self, collection_name: str) -> None:
        """Make every write acknowledged so far visible to search (not only to a get by id). A store that searches what it has applied
        (Qdrant, pgvector, Redis) has nothing to do; OpenSearch refreshes its index, which otherwise happens at the index's refresh interval
        (30 s here). The scope rewrite calls it before each read of the points it rewrites."""

    async def count_points(self, collection_name: str, count_filter: FilterExpression) -> int:
        """Exact number of points the filter matches (it must pin ``metadata.orgId``); ``PartialResult`` if the store cannot answer completely.
        The scope rewrite checks its scroll against it, so a store without it cannot be rewritten."""
        raise NotImplementedError

    #: The store makes ``update_payloads_by_ids(..., fence=n)`` conditional, atomically per point: a point whose ``scopeFence`` is above ``n``
    #: is left as it is (no error), any other takes the payload and ``scopeFence = n``. A store without it is written to unconditionally; the
    #: caller then relies on checking the connector's generation just before each request (pgvector has its own fence,
    #: ``computed_at``).
    generation_fence: bool = False

    async def update_payloads_by_ids(self, collection_name: str, updates: Mapping[str, dict], *, org_id: str, **extra: object) -> None:
        """``update_payload_by_ids`` with a payload of its own for each point: the points that share a payload are written together here;
        a store that can take different payloads in one request overrides this (the groups are separate requests: the default is not atomic
        across them). ``extra`` is passed on (pgvector's ``computed_at``, ``fence`` where ``generation_fence``)."""
        by_payload: dict[str, list[str]] = {}
        for point_id, payload in updates.items():
            by_payload.setdefault(json.dumps(payload, sort_keys=True), []).append(point_id)
        for payload, ids in by_payload.items():
            await self.update_payload_by_ids(collection_name, ids, json.loads(payload), org_id=org_id, **extra)  # type: ignore[call-arg]

    async def swap_array_value(
        self,
        collection_name: str,
        field: str,
        old: str,
        new: str,
        points: FilterExpression,
        *,
        fence: int | None = None,
    ) -> int | None:
        """Replace ``old`` by ``new`` in the array field ``field`` of every point the filter matches, in one request; returns the
        number of points changed, or None when the store cannot do it (the caller writes the points by id instead). The filter must
        pin ``metadata.orgId``. Unlike ``update_payload_by_ids`` it finds its points with a search, so a point written moments ago may
        be missed: the caller verifies what it wrote. With ``fence`` (a store with ``generation_fence``) a point whose ``scopeFence`` is
        above it is left alone, and a point changed takes it."""
        return None

    async def existing_point_ids(self, collection_name: str, ids: list[str]) -> set[str]:
        """Which of ``ids`` have a point, reading no payload and no vector.

        Like ``retrieve_points`` this sees writes that are not yet searchable. A provider that can
        answer without transferring payloads overrides it; this default asks for the points.
        """
        return {p.id for p in await self.retrieve_points(collection_name, ids)}

    @abstractmethod
    async def update_payload_by_ids(
        self,
        collection_name: str,
        point_ids: List[str],
        payload: dict,
        *,
        org_id: str,
    ) -> None:
        """Merge ``payload`` into the points of ``org_id`` with these ids, by id.

        Ids that belong to another org are left alone, as if they did not exist; a
        missing or blank ``org_id`` raises ``OrgRequired``.

        Unlike ``set_payload``, which finds its points with a search (an
        OpenSearch ``update_by_query`` cannot see a document until the index
        refreshes), this reaches a point the moment it was written. Ids with
        no point are ignored.
        """
        raise NotImplementedError

    # A search answered from less than the full search is reported with degraded_notice.note(reason) or SearchResult.degraded (see models).
    @abstractmethod
    async def query_nearest_points(
        self,
        collection_name: str,
        requests: List[HybridSearchRequest],
    ) -> List[List[SearchResult]]:
        raise NotImplementedError

    @abstractmethod
    async def upsert_points(
        self,
        collection_name: str,
        points: List[VectorPoint],
    ) -> None:
        raise NotImplementedError

    @abstractmethod
    async def delete_points(
        self,
        collection_name: str,
        filter: FilterExpression,
        refresh: bool = False,
    ) -> None:
        """Delete matching points.

        Like ``overwrite_payload`` and ``set_payload``, the filter must pin ``metadata.orgId`` to one
        non-blank org in MUST (``filters.require_filter_org``); otherwise ``OrgRequired`` is raised and
        nothing is touched. An org-only filter is allowed: it removes a whole organisation.

        ``refresh=True`` asks the provider to make the write immediately
        visible to a subsequent read. Only pass it when the caller re-reads the
        matched set to decide whether it is done — on OpenSearch it forces a
        shard refresh, creating a Lucene segment per call, which is exactly the
        segment proliferation the 30s ``refresh_interval`` exists to avoid.
        """
        raise NotImplementedError

    @abstractmethod
    async def overwrite_payload(
        self,
        collection_name: str,
        payload: dict,
        points: FilterExpression,
    ) -> None:
        """Replace the payload of matching points. ``points`` must pin ``metadata.orgId`` (see ``delete_points``)."""
        raise NotImplementedError

    @abstractmethod
    async def set_payload(
        self,
        collection_name: str,
        payload: dict,
        filter: FilterExpression,
        refresh: bool = False,
    ) -> None:
        """Merge ``payload`` into matching points without replacing the rest.

        See ``delete_points`` for ``refresh`` and for the org the filter must pin.

        Must not use Qdrant ``overwrite_payload`` (that drops ``page_content``).
        Rejects an empty filter — the same safety rule as ``delete_points``.
        """
        raise NotImplementedError

    # ------------------------------------------------------------------
    # Performance utilities (optional — providers that don't support these
    # inherit the no-op default; override in OpenSearch / others as needed)
    # ------------------------------------------------------------------

    async def force_merge(
        self,
        collection_name: str,
        max_segments: int = 1,
    ) -> None:
        """Merge index segments for optimal search performance.

        No-op for providers that manage segment lifecycle internally (e.g.
        Qdrant).  Override in providers that expose a merge API (e.g.
        OpenSearch / Elasticsearch).
        """

    async def warmup(self, collection_name: str) -> None:
        """Pre-load indexes into memory to eliminate cold-start latency.

        No-op for providers whose indexes are always in memory (e.g. Qdrant
        with ``always_ram=True``).  Override in providers that use memory-
        mapped files and expose an explicit warmup API (e.g. OpenSearch).
        """

