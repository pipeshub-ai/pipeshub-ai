"""Qdrant vector database provider.

Fully async — uses AsyncQdrantClient exclusively.
Supports: sparse + dense named vectors, RRF prefetch/fusion.
"""

import asyncio
import threading
import time
from typing import Dict, List, Optional, Union

from qdrant_client import AsyncQdrantClient  # type: ignore
from qdrant_client.http.models import (  # type: ignore
    BinaryQuantization,
    BinaryQuantizationConfig,
    Distance,
    Filter,
    FilterSelector,
    HnswConfigDiff,
    KeywordIndexParams,
    KeywordIndexType,
    Modifier,
    OptimizersConfigDiff,
    ProductQuantization,
    ProductQuantizationConfig,
    CompressionRatio,
    ScalarQuantization,
    ScalarQuantizationConfig,
    ScalarType,
    SparseIndexParams,
    SparseVectorParams,
    VectorParams,
)

from app.config.configuration_service import ConfigurationService
from app.config.constants.service import config_node_constants
from app.services.vector_db.interface.vector_db import IVectorDBService
from app.services.vector_db.models import (
    CollectionConfig,
    DistanceMetric,
    FieldCondition,
    FilterExpression,
    FilterMode,
    FilterValue,
    FusionMethod,
    HealthStatus,
    HNSWConfig,
    HybridSearchRequest,
    MRLConfig,
    QuantizationType,
    ScrollResult,
    SearchResult,
    VectorCollectionInfo,
    VectorDBCapabilities,
    VectorDBHealth,
    VectorPoint,
)
from app.services.vector_db.qdrant.config import QdrantConfig
from app.services.vector_db.qdrant.utils import QdrantUtils
from app.utils.logger import create_logger

logger = create_logger("qdrant_service")


def _is_not_found_error(exc: Exception) -> bool:
    """Return True when *exc* represents a 404-style collection-not-found condition."""
    msg = str(exc).lower()
    # qdrant-client raises UnexpectedResponse with status 404 or a message
    # containing "not found" / "doesn't exist" for missing collections.
    return (
        "not found" in msg
        or "doesn't exist" in msg
        or "does not exist" in msg
        or "404" in msg
        or getattr(exc, "status_code", None) == 404
    )


_DISTANCE_MAP = {
    DistanceMetric.COSINE: Distance.COSINE,
    DistanceMetric.L2: Distance.EUCLID,
    DistanceMetric.DOT_PRODUCT: Distance.DOT,
}

_QDRANT_CAPABILITIES = VectorDBCapabilities(
    supports_sparse_vectors=True,
    supports_server_side_text_search=False,
    supported_fusion_methods=[FusionMethod.RRF],
)


class QdrantService(IVectorDBService):
    """Fully-async Qdrant provider implementing IVectorDBService."""

    def __init__(
        self,
        config_service: ConfigurationService | QdrantConfig,
    ) -> None:
        self.config_service = config_service
        # The grpc.aio (and httpx) transports inside AsyncQdrantClient are
        # permanently bound to the event loop running when they are first
        # used. This service is shared across loops (the main loop for health
        # checks / API and the indexing consumer's worker-thread loop), so a
        # single client instance would raise "attached to a different loop".
        # Instead we keep one client per event loop, created lazily from the
        # kwargs resolved in connect().
        self._client_kwargs: Optional[dict] = None
        self._clients: Dict[Optional[asyncio.AbstractEventLoop], AsyncQdrantClient] = {}
        self._clients_lock = threading.Lock()
        # Explicitly assigned client (tests / legacy callers); served to every
        # loop as-is when set.
        self._client_override: Optional[AsyncQdrantClient] = None

    @property
    def client(self) -> Optional[AsyncQdrantClient]:
        """Return the AsyncQdrantClient bound to the current event loop."""
        if self._client_override is not None:
            return self._client_override
        if self._client_kwargs is None:
            return None
        return self._get_client_for_current_loop()

    @client.setter
    def client(self, value: Optional[AsyncQdrantClient]) -> None:
        self._client_override = value

    def _get_client_for_current_loop(self) -> AsyncQdrantClient:
        try:
            loop: Optional[asyncio.AbstractEventLoop] = asyncio.get_running_loop()
        except RuntimeError:
            loop = None

        with self._clients_lock:
            client = self._clients.get(loop)
            if client is None:
                client = AsyncQdrantClient(**self._client_kwargs)  # type: ignore[arg-type]
                self._clients[loop] = client
                if len(self._clients) > 1:
                    logger.info(
                        "Created additional Qdrant client for event loop %r "
                        "(%d clients total)",
                        loop,
                        len(self._clients),
                    )
            return client

    # ------------------------------------------------------------------
    # Factory
    # ------------------------------------------------------------------

    @classmethod
    async def create(
        cls,
        config: ConfigurationService | QdrantConfig,
    ) -> "QdrantService":
        service = cls(config)
        await service.connect()
        return service

    # Keep legacy names for backward compatibility with VectorDBFactory
    @classmethod
    async def create_sync(cls, config: ConfigurationService | QdrantConfig) -> "QdrantService":
        return await cls.create(config)

    @classmethod
    async def create_async(cls, config: ConfigurationService | QdrantConfig) -> "QdrantService":
        return await cls.create(config)

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def connect(self) -> None:
        try:
            if isinstance(self.config_service, ConfigurationService):
                raw = await self.config_service.get_config(
                    config_node_constants.QDRANT.value
                )
            else:
                raw = self.config_service.qdrant_config

            if not raw:
                raise ValueError("Qdrant configuration not found")

            cfg = QdrantConfig.from_dict(raw)

            client_kwargs: dict = dict(
                host=cfg.host,
                port=cfg.port,
                # api_key may be empty string — only pass it when truthy
                api_key=cfg.api_key or None,
                prefer_grpc=cfg.prefer_grpc,
                https=cfg.https,
                timeout=cfg.timeout,
            )
            if cfg.prefer_grpc:
                client_kwargs["grpc_options"] = {
                    "grpc.max_send_message_length": 64 * 1024 * 1024,
                    "grpc.max_receive_message_length": 64 * 1024 * 1024,
                    "grpc.keepalive_time_ms": 30000,
                    "grpc.keepalive_timeout_ms": 10000,
                    "grpc.http2.max_pings_without_data": 0,
                    "grpc.keepalive_permit_without_calls": 1,
                }

            self._client_kwargs = client_kwargs
            # Eagerly create the client for the current loop so connect()
            # fails fast on bad kwargs; clients for other loops are created
            # lazily on first use.
            self._get_client_for_current_loop()
            logger.info(
                f"Connected to Qdrant at {cfg.host}:{cfg.port} "
                f"(grpc={cfg.prefer_grpc}, https={cfg.https})"
            )
        except Exception as e:
            self._client_kwargs = None
            logger.error(f"Failed to connect to Qdrant: {e}")
            raise

    async def disconnect(self) -> None:
        with self._clients_lock:
            clients = list(self._clients.values())
            self._clients.clear()
        if self._client_override is not None:
            clients.append(self._client_override)
            self._client_override = None
        self._client_kwargs = None

        if not clients:
            return
        for client in clients:
            try:
                # Clients bound to other (possibly already-stopped) event
                # loops cannot be closed from here; log and move on.
                await client.close()
            except Exception as e:
                logger.warning(f"Error during Qdrant disconnect: {e}")
        logger.info("Disconnected from Qdrant")

    # ------------------------------------------------------------------
    # Identity
    # ------------------------------------------------------------------

    def get_service_name(self) -> str:
        return "qdrant"

    def get_service(self) -> "QdrantService":
        return self

    def get_service_client(self) -> AsyncQdrantClient:
        return self.client  # type: ignore

    # ------------------------------------------------------------------
    # Capabilities and health
    # ------------------------------------------------------------------

    def get_capabilities(self) -> VectorDBCapabilities:
        return _QDRANT_CAPABILITIES

    async def health_check(self) -> VectorDBHealth:
        start = time.monotonic()
        if self.client is None:
            return VectorDBHealth(
                status=HealthStatus.UNHEALTHY,
                message="Client not connected",
            )
        try:
            info = await self.client.get_collections()
            latency_ms = (time.monotonic() - start) * 1000
            # Try to get server version
            try:
                version_info = await self.client.get_service_info()
                version = getattr(version_info, "version", None)
            except Exception:
                version = None
            return VectorDBHealth(
                status=HealthStatus.HEALTHY,
                latency_ms=round(latency_ms, 2),
                server_version=str(version) if version else None,
                message=f"{len(info.collections)} collection(s) visible",
            )
        except Exception as e:
            latency_ms = (time.monotonic() - start) * 1000
            return VectorDBHealth(
                status=HealthStatus.UNHEALTHY,
                latency_ms=round(latency_ms, 2),
                message=str(e),
            )

    # ------------------------------------------------------------------
    # Collection management
    # ------------------------------------------------------------------

    async def create_collection(
        self,
        collection_name: str = "records",
        config: Optional[CollectionConfig] = None,
    ) -> None:
        self._assert_connected()
        if config is None:
            config = CollectionConfig()

        qdrant_distance = _DISTANCE_MAP.get(config.distance_metric, Distance.COSINE)

        # Resolve target dimensionality (MRL dimension reduction or full size)
        effective_dim = config.embedding_size
        if config.mrl is not None and config.mrl.dimensions is not None:
            effective_dim = config.mrl.dimensions

        vectors_config = {
            "dense": VectorParams(size=effective_dim, distance=qdrant_distance)
        }
        sparse_vectors_config = (
            {
                "sparse": SparseVectorParams(
                    index=SparseIndexParams(on_disk=False),
                    modifier=Modifier.IDF if config.sparse_idf else None,
                )
            }
            if config.enable_sparse
            else None
        )
        optimizers_config = OptimizersConfigDiff(default_segment_number=8)

        # Build HNSW config from knobs (only forward non-None values)
        hnsw_config: Optional[HnswConfigDiff] = None
        if config.hnsw is not None:
            hnsw_kwargs = {}
            if config.hnsw.m is not None:
                hnsw_kwargs["m"] = config.hnsw.m
            if config.hnsw.ef_construct is not None:
                hnsw_kwargs["ef_construct"] = config.hnsw.ef_construct
            if config.hnsw.full_scan_threshold is not None:
                hnsw_kwargs["full_scan_threshold"] = config.hnsw.full_scan_threshold
            if hnsw_kwargs:
                hnsw_config = HnswConfigDiff(**hnsw_kwargs)

        # Build quantization config from knobs.
        # NONE means "no quantization" — pass None, letting Qdrant use its own default.
        # SCALAR (the CollectionConfig default) preserves the original INT8 behaviour.
        quantization_config: Optional[object]
        q_type = config.quantization
        if q_type == QuantizationType.NONE:
            quantization_config = None
        elif q_type == QuantizationType.PRODUCT:
            quantization_config = ProductQuantization(
                product=ProductQuantizationConfig(
                    compression=CompressionRatio.X4,
                    always_ram=True,
                )
            )
        elif q_type == QuantizationType.BINARY:
            quantization_config = BinaryQuantization(
                binary=BinaryQuantizationConfig(always_ram=True)
            )
        else:
            # SCALAR (default) — INT8 scalar quantization
            quantization_config = ScalarQuantization(
                scalar=ScalarQuantizationConfig(
                    type=ScalarType.INT8,
                    quantile=0.95,
                    always_ram=True,
                )
            )

        create_kwargs: dict = dict(
            collection_name=collection_name,
            vectors_config=vectors_config,
            sparse_vectors_config=sparse_vectors_config,
            optimizers_config=optimizers_config,
        )
        if quantization_config is not None:
            create_kwargs["quantization_config"] = quantization_config
        if hnsw_config is not None:
            create_kwargs["hnsw_config"] = hnsw_config

        await self.client.create_collection(**create_kwargs)  # type: ignore
        logger.info(f"Created Qdrant collection '{collection_name}'")

    async def get_collections(self) -> object:
        self._assert_connected()
        return await self.client.get_collections()  # type: ignore

    async def get_collection(self, collection_name: str) -> object:
        self._assert_connected()
        return await self.client.get_collection(collection_name)  # type: ignore

    async def get_collection_info(self, collection_name: str) -> VectorCollectionInfo:
        """Return normalised collection metadata.

        Only swallows NotFoundError (collection doesn't exist yet).
        Connectivity / auth errors are re-raised so callers can distinguish
        "not created yet" (safe to create) from "cluster unreachable" (unsafe).
        """
        try:
            raw = await self.get_collection(collection_name)
            vectors = raw.config.params.vectors  # type: ignore
            dense_dim: Optional[int] = None
            if isinstance(vectors, dict):
                dense_params = vectors.get("dense")
                if dense_params is not None:
                    dense_dim = getattr(dense_params, "size", None)
            points_count: int = getattr(raw, "points_count", 0) or 0
            return VectorCollectionInfo(
                name=collection_name,
                exists=True,
                dense_dimension=dense_dim,
                points_count=points_count,
            )
        except Exception as exc:
            # Treat only "not found" as a normal "doesn't exist" response.
            # Any other exception (timeout, auth, connection refused) propagates
            # so the caller can't accidentally recreate a live collection.
            if _is_not_found_error(exc):
                return VectorCollectionInfo(name=collection_name, exists=False)
            raise

    async def collection_exists(self, collection_name: str) -> bool:
        """Return True if the collection exists; False on 404.

        Re-raises on connectivity/auth errors (not a 404).
        """
        try:
            await self.get_collection(collection_name)
            return True
        except Exception as exc:
            if _is_not_found_error(exc):
                return False
            raise

    async def delete_collection(self, collection_name: str) -> None:
        self._assert_connected()
        await self.client.delete_collection(collection_name)  # type: ignore
        logger.info(f"Deleted Qdrant collection '{collection_name}'")

    async def create_index(
        self,
        collection_name: str,
        field_name: str,
        field_schema: dict,
    ) -> None:
        self._assert_connected()
        if field_schema.get("type") == "keyword":
            schema = KeywordIndexParams(type=KeywordIndexType.KEYWORD)
        else:
            schema = field_schema  # type: ignore
        await self.client.create_payload_index(collection_name, field_name, schema)  # type: ignore

    # ------------------------------------------------------------------
    # Filter construction
    # ------------------------------------------------------------------

    async def filter_collection(
        self,
        filter_mode: Union[str, FilterMode] = FilterMode.MUST,
        must: Optional[Dict[str, FilterValue]] = None,
        should: Optional[Dict[str, FilterValue]] = None,
        must_not: Optional[Dict[str, FilterValue]] = None,
        min_should_match: Optional[int] = None,
        **kwargs: FilterValue,
    ) -> FilterExpression:
        from app.services.vector_db.filters import build_filter_expression

        return build_filter_expression(
            filter_mode,
            must=must,
            should=should,
            must_not=must_not,
            min_should_match=min_should_match,
            extra_kwargs=kwargs or None,
            build_conditions=QdrantUtils.build_conditions_generic,
        )

    # ------------------------------------------------------------------
    # Data operations — all async
    # ------------------------------------------------------------------

    async def scroll(
        self,
        collection_name: str,
        scroll_filter: FilterExpression,
        limit: int,
        offset: Optional[str] = None,
    ) -> ScrollResult:
        self._assert_connected()
        qdrant_filter = QdrantUtils.filter_expression_to_qdrant(scroll_filter)
        raw_points, next_offset = await self.client.scroll(  # type: ignore
            collection_name=collection_name,
            scroll_filter=qdrant_filter,
            limit=limit,
            with_payload=True,
            offset=offset,
        )
        points = [
            VectorPoint(
                id=str(p.id),
                payload=p.payload or {},
            )
            for p in raw_points
        ]
        return ScrollResult(
            points=points,
            next_offset=str(next_offset) if next_offset is not None else None,
        )

    async def query_nearest_points(
        self,
        collection_name: str,
        requests: List[HybridSearchRequest],
    ) -> List[List[SearchResult]]:
        self._assert_connected()
        qdrant_requests = [QdrantUtils.search_request_to_qdrant(req) for req in requests]
        raw_results = await self.client.query_batch_points(  # type: ignore
            collection_name=collection_name,
            requests=qdrant_requests,
        )
        results: List[List[SearchResult]] = []
        for batch_result in raw_results:
            results.append(
                [
                    QdrantUtils.qdrant_result_to_search_result(p)
                    for p in batch_result.points
                ]
            )
        return results

    async def upsert_points(
        self,
        collection_name: str,
        points: List[VectorPoint],
        batch_size: int = 512,
    ) -> None:
        self._assert_connected()
        qdrant_points = [QdrantUtils.vector_point_to_qdrant(p) for p in points]

        start = time.perf_counter()
        logger.debug(
            f"Upserting {len(qdrant_points)} points into '{collection_name}' "
            f"(batch_size={batch_size})"
        )

        for i in range(0, len(qdrant_points), batch_size):
            batch = qdrant_points[i : i + batch_size]
            await self.client.upsert(  # type: ignore
                collection_name=collection_name,
                points=batch,
            )

        elapsed = time.perf_counter() - start
        logger.info(
            f"Upsert complete: {len(qdrant_points)} points in {elapsed:.2f}s "
            f"({len(qdrant_points)/elapsed:.0f} pts/s)"
        )

    async def delete_points(
        self,
        collection_name: str,
        filter: FilterExpression,
    ) -> None:
        if filter.is_empty():
            raise ValueError(
                "delete_points called with an empty filter — this would wipe the entire "
                "collection. Populate at least one filter condition (e.g. virtualRecordId)."
            )
        self._assert_connected()
        qdrant_filter = QdrantUtils.filter_expression_to_qdrant(filter)
        await self.client.delete(  # type: ignore
            collection_name=collection_name,
            points_selector=FilterSelector(filter=qdrant_filter),
        )
        logger.info(f"Deleted points from Qdrant collection '{collection_name}'")

    async def overwrite_payload(
        self,
        collection_name: str,
        payload: dict,
        points: FilterExpression,
    ) -> None:
        self._assert_connected()
        qdrant_filter = QdrantUtils.filter_expression_to_qdrant(points)
        await self.client.overwrite_payload(  # type: ignore
            collection_name=collection_name,
            payload=payload,
            points=FilterSelector(filter=qdrant_filter),
        )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _assert_connected(self) -> None:
        if self.client is None:
            raise RuntimeError("Qdrant client not connected. Call connect() first.")
