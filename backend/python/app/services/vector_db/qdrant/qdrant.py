import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Dict, List, Optional, Tuple, Union

from qdrant_client import AsyncQdrantClient, QdrantClient  # type: ignore
from qdrant_client.http.models import (  # type: ignore
    Distance,
    Filter,
    FilterSelector,
    KeywordIndexParams,
    KeywordIndexType,
    Modifier,
    OptimizersConfigDiff,
    PointStruct,
    ScalarQuantization,
    ScalarQuantizationConfig,
    ScalarType,
    SparseIndexParams,
    SparseVectorParams,
    VectorParams,
)

from app.config.configuration_service import ConfigurationService
from app.config.constants.service import config_node_constants
from app.services.vector_db.const.const import VECTOR_DB_COLLECTION_NAME
from app.services.vector_db.interface.vector_db import IVectorDBService
from app.services.vector_db.models import (
    CollectionConfig,
    DistanceMetric,
    FilterExpression,
    FilterMode,
    FilterValue,
    HybridSearchRequest,
    SearchResult,
    VectorPoint,
)
from app.services.vector_db.qdrant.config import QdrantConfig
from app.services.vector_db.qdrant.utils import QdrantUtils
from app.utils.logger import create_logger

logger = create_logger("qdrant_service")

_DISTANCE_MAP = {
    DistanceMetric.COSINE: Distance.COSINE,
    DistanceMetric.L2: Distance.EUCLID,
    DistanceMetric.DOT_PRODUCT: Distance.DOT,
}


class QdrantService(IVectorDBService):
    def __init__(
        self,
        config_service: ConfigurationService | QdrantConfig,
        is_async: bool = False,
    ) -> None:
        self.config_service = config_service
        self.client: Optional[QdrantClient | AsyncQdrantClient] = None
        self.is_async = is_async

    @classmethod
    async def create_sync(
        cls,
        config: ConfigurationService | QdrantConfig,
    ) -> 'QdrantService':
        service = cls(config, is_async=False)
        await service.connect_sync()
        return service

    @classmethod
    async def create_async(
        cls,
        config: ConfigurationService | QdrantConfig,
    ) -> 'QdrantService':
        service = cls(config, is_async=True)
        await service.connect_async()
        return service

    async def connect_async(self) -> None:
        try:
            if isinstance(self.config_service, ConfigurationService):
                qdrant_config = await self.config_service.get_config(config_node_constants.QDRANT.value)
            else:
                qdrant_config = self.config_service.qdrant_config
            if not qdrant_config:
                raise ValueError("Qdrant configuration not found")

            self.client = AsyncQdrantClient(
                host=qdrant_config.get("host"),  # type: ignore
                port=qdrant_config.get("port"),  # type: ignore
                api_key=qdrant_config.get("apiKey"),  # type: ignore
                prefer_grpc=True,
                https=False,
                timeout=300,
                grpc_options={
                    'grpc.max_send_message_length': 64 * 1024 * 1024,
                    'grpc.max_receive_message_length': 64 * 1024 * 1024,
                    'grpc.keepalive_time_ms': 30000,
                    'grpc.keepalive_timeout_ms': 10000,
                    'grpc.http2.max_pings_without_data': 0,
                    'grpc.keepalive_permit_without_calls': 1,
                },
            )
            logger.info("Connected to Qdrant with async client successfully")
        except Exception as e:
            logger.error(f"Failed to connect to Qdrant with async client: {e}")
            raise

    async def connect(self) -> None:
        if self.is_async:
            await self.connect_async()
        else:
            await self.connect_sync()

    async def connect_sync(self) -> None:
        try:
            if isinstance(self.config_service, ConfigurationService):
                qdrant_config = await self.config_service.get_config(config_node_constants.QDRANT.value)
            else:
                qdrant_config = self.config_service.qdrant_config
            if not qdrant_config:
                raise ValueError("Qdrant configuration not found")

            self.client = QdrantClient(
                host=qdrant_config.get("host"),  # type: ignore
                port=qdrant_config.get("port"),  # type: ignore
                api_key=qdrant_config.get("apiKey"),  # type: ignore
                prefer_grpc=True,
                https=False,
                timeout=300,
                grpc_options={
                    'grpc.max_send_message_length': 64 * 1024 * 1024,
                    'grpc.max_receive_message_length': 64 * 1024 * 1024,
                    'grpc.keepalive_time_ms': 30000,
                    'grpc.keepalive_timeout_ms': 10000,
                    'grpc.http2.max_pings_without_data': 0,
                    'grpc.keepalive_permit_without_calls': 1,
                },
            )
            logger.info("Connected to Qdrant successfully")
        except Exception as e:
            logger.error(f"Failed to connect to Qdrant: {e}")
            raise

    async def disconnect(self) -> None:
        if self.client is not None:
            try:
                self.client.close()
                logger.info("Disconnected from Qdrant successfully")
            except Exception as e:
                logger.warning(f"Error during disconnect (likely harmless): {e}")
            finally:
                self.client = None

    def get_service_name(self) -> str:
        return "qdrant"

    def get_service(self) -> 'QdrantService':
        return self

    def get_service_client(self) -> QdrantClient | AsyncQdrantClient:
        return self.client

    async def get_collections(self) -> object:
        if self.client is None:
            raise RuntimeError("Client not connected. Call connect() first.")
        return self.client.get_collections()

    async def get_collection(
        self,
        collection_name: str,
    ) -> object:
        if self.client is None:
            raise RuntimeError("Client not connected. Call connect() first.")
        return self.client.get_collection(collection_name)

    async def delete_collection(
        self,
        collection_name: str,
    ) -> None:
        if self.client is None:
            raise RuntimeError("Client not connected. Call connect() first.")
        self.client.delete_collection(collection_name)

    async def create_collection(
        self,
        collection_name: str = VECTOR_DB_COLLECTION_NAME,
        config: Optional[CollectionConfig] = None,
    ) -> None:
        if self.client is None:
            raise RuntimeError("Client not connected. Call connect() first.")

        if config is None:
            config = CollectionConfig()

        qdrant_distance = _DISTANCE_MAP.get(config.distance_metric, Distance.COSINE)

        vectors_config = {"dense": VectorParams(size=config.embedding_size, distance=qdrant_distance)}
        sparse_vectors_config = {
            "sparse": SparseVectorParams(
                index=SparseIndexParams(on_disk=False),
                modifier=Modifier.IDF if config.sparse_idf else None
            )
        } if config.enable_sparse else None

        optimizers_config = OptimizersConfigDiff(default_segment_number=8)
        quantization_config = ScalarQuantization(
            scalar=ScalarQuantizationConfig(
                type=ScalarType.INT8,
                quantile=0.95,
                always_ram=True
            )
        )

        self.client.create_collection(
            collection_name=collection_name,
            vectors_config=vectors_config,
            sparse_vectors_config=sparse_vectors_config,
            optimizers_config=optimizers_config,
            quantization_config=quantization_config,
        )
        logger.info(f"Created collection {collection_name}")

    async def create_index(
        self,
        collection_name: str,
        field_name: str,
        field_schema: dict,
    ) -> None:
        if self.client is None:
            raise RuntimeError("Client not connected. Call connect_sync() or connect_async() first.")

        if field_schema.get("type") == "keyword":
            field_schema = KeywordIndexParams(
                type=KeywordIndexType.KEYWORD,
            )

        self.client.create_payload_index(collection_name, field_name, field_schema)

    async def filter_collection(
        self,
        filter_mode: Union[str, FilterMode] = FilterMode.MUST,
        must: Optional[Dict[str, FilterValue]] = None,
        should: Optional[Dict[str, FilterValue]] = None,
        must_not: Optional[Dict[str, FilterValue]] = None,
        min_should_match: Optional[int] = None,
        **kwargs: FilterValue,
    ) -> FilterExpression:
        if self.client is None:
            raise RuntimeError("Client not connected. Call connect() first.")

        if isinstance(filter_mode, str):
            try:
                filter_mode = FilterMode(filter_mode.lower())
            except ValueError:
                raise ValueError(f"Invalid mode '{filter_mode}'. Must be 'must', 'should', or 'must_not'")

        all_must_filters = dict(must) if must else {}
        all_should_filters = dict(should) if should else {}
        all_must_not_filters = dict(must_not) if must_not else {}

        if kwargs:
            if filter_mode == FilterMode.MUST:
                all_must_filters.update(kwargs)
            elif filter_mode == FilterMode.SHOULD:
                all_should_filters.update(kwargs)
            elif filter_mode == FilterMode.MUST_NOT:
                all_must_not_filters.update(kwargs)

        must_conditions = QdrantUtils.build_conditions(all_must_filters) if all_must_filters else []
        should_conditions = QdrantUtils.build_conditions(all_should_filters) if all_should_filters else []
        must_not_conditions = QdrantUtils.build_conditions(all_must_not_filters) if all_must_not_filters else []

        if not must_conditions and not should_conditions and not must_not_conditions:
            logger.warning("No filters provided - returning empty filter")
            return FilterExpression()

        from app.services.vector_db.models import FieldCondition as GenericFieldCondition

        def _qdrant_to_generic(conditions):
            result = []
            for c in conditions:
                if hasattr(c.match, 'any'):
                    result.append(GenericFieldCondition(key=c.key, values=c.match.any))
                else:
                    result.append(GenericFieldCondition(key=c.key, value=c.match.value))
            return result

        return FilterExpression(
            must=_qdrant_to_generic(must_conditions),
            should=_qdrant_to_generic(should_conditions),
            must_not=_qdrant_to_generic(must_not_conditions),
            min_should_match=min_should_match if should_conditions else None,
        )

    async def scroll(
        self,
        collection_name: str,
        scroll_filter: FilterExpression,
        limit: int,
    ) -> object:
        if self.client is None:
            raise RuntimeError("Client not connected. Call connect() first.")
        qdrant_filter = QdrantUtils.filter_expression_to_qdrant(scroll_filter)
        return self.client.scroll(collection_name, qdrant_filter, limit)

    def overwrite_payload(
        self,
        collection_name: str,
        payload: dict,
        points: FilterExpression,
    ) -> None:
        if self.client is None:
            raise RuntimeError("Client not connected. Call connect() first.")
        qdrant_filter = QdrantUtils.filter_expression_to_qdrant(points)
        self.client.overwrite_payload(collection_name, payload, qdrant_filter)

    def query_nearest_points(
        self,
        collection_name: str,
        requests: List[HybridSearchRequest],
    ) -> List[List[SearchResult]]:
        if self.client is None:
            raise RuntimeError("Client not connected. Call connect() first.")

        qdrant_requests = [QdrantUtils.search_request_to_qdrant(req) for req in requests]
        raw_results = self.client.query_batch_points(collection_name, qdrant_requests)

        results: List[List[SearchResult]] = []
        for batch_result in raw_results:
            batch_search_results = [
                QdrantUtils.qdrant_result_to_search_result(point)
                for point in batch_result.points
            ]
            results.append(batch_search_results)
        return results

    def upsert_points(
        self,
        collection_name: str,
        points: List[VectorPoint],
        batch_size: int = 1000,
        max_workers: int = 5,
    ) -> None:
        if self.client is None:
            raise RuntimeError("Client not connected. Call connect() first.")

        qdrant_points = [QdrantUtils.vector_point_to_qdrant(p) for p in points]

        start_time = time.perf_counter()
        total_points = len(qdrant_points)
        logger.info(f"Starting upsert of {total_points} points to collection '{collection_name}' (batch size: {batch_size}, parallel workers: {max_workers})")

        if total_points <= batch_size:
            self.client.upsert(collection_name, qdrant_points)
        else:
            batches = []
            for i in range(0, total_points, batch_size):
                batch_end = min(i + batch_size, total_points)
                batch = qdrant_points[i:batch_end]
                batch_num = (i // batch_size) + 1
                batches.append((batch_num, batch))

            total_batches = len(batches)
            completed_batches = 0

            def upload_batch(batch_info: Tuple[int, List[PointStruct]]) -> Tuple[int, int, float]:
                batch_num, batch = batch_info
                batch_start = time.perf_counter()
                self.client.upsert(collection_name, batch)
                batch_elapsed = time.perf_counter() - batch_start
                return batch_num, len(batch), batch_elapsed

            with ThreadPoolExecutor(max_workers=max_workers) as executor:
                futures = {executor.submit(upload_batch, batch_info): batch_info for batch_info in batches}

                for future in as_completed(futures):
                    try:
                        batch_num, batch_size_actual, batch_elapsed = future.result()
                        completed_batches += 1
                        logger.info(
                            f"Uploaded batch {batch_num}/{total_batches}: {batch_size_actual} points "
                            f"in {batch_elapsed:.2f}s ({batch_size_actual/batch_elapsed:.1f} points/s) "
                            f"[{completed_batches}/{total_batches} complete]"
                        )
                    except Exception as e:
                        batch_info = futures[future]
                        logger.error(f"Failed to upload batch {batch_info[0]}: {str(e)}")
                        raise

        elapsed_time = time.perf_counter() - start_time
        throughput = total_points / elapsed_time if elapsed_time > 0 else 0
        logger.info(
            f"Completed upsert of {total_points} points in {elapsed_time:.2f}s "
            f"(throughput: {throughput:.1f} points/s, avg: {elapsed_time/total_points*1000:.2f}ms per point)"
        )

    def delete_points(
        self,
        collection_name: str,
        filter: FilterExpression,
    ) -> None:
        if self.client is None:
            raise RuntimeError("Client not connected. Call connect() first.")
        qdrant_filter = QdrantUtils.filter_expression_to_qdrant(filter)
        self.client.delete(
            collection_name=collection_name,
            points_selector=FilterSelector(
                filter=qdrant_filter
            ),
        )
        logger.info(f"Deleted points from collection '{collection_name}'")
