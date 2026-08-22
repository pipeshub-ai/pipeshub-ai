from abc import ABC, abstractmethod
from typing import Dict, List, Optional, Union

from app.services.vector_db.models import (
    CollectionConfig,
    FilterExpression,
    FilterMode,
    FilterValue,
    HybridSearchRequest,
    ScrollResult,
    SearchResult,
    VectorCollectionInfo,
    VectorDBCapabilities,
    VectorDBHealth,
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

    @abstractmethod
    def get_capabilities(self) -> VectorDBCapabilities:
        """Return static capability descriptor for this provider."""
        raise NotImplementedError

    @abstractmethod
    async def health_check(self) -> VectorDBHealth:
        """Perform a live health check and return a structured result."""
        raise NotImplementedError

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
        **filters: FilterValue,
    ) -> FilterExpression:
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
    ) -> ScrollResult:
        raise NotImplementedError

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
    ) -> None:
        raise NotImplementedError

    @abstractmethod
    async def overwrite_payload(
        self,
        collection_name: str,
        payload: dict,
        points: FilterExpression,
    ) -> None:
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

    def schedule_idle_force_merge(
        self,
        collection_name: str,
        idle_seconds: float = 300.0,
        max_segments: int = 1,
    ) -> None:
        """Schedule a debounced force_merge after an idle period.

        No-op for providers that manage segment lifecycle internally (e.g.
        Qdrant).  Override in OpenSearch where callers want to collapse
        segments automatically after a burst of ingest without running
        force_merge mid-burst.
        """
