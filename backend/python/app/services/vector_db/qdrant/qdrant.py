import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Dict, List, Optional, Tuple, Union

from qdrant_client import AsyncQdrantClient, QdrantClient  # type: ignore
from qdrant_client.http.models import (  # type: ignore
    Distance,
    Filter,  # type: ignore
    FilterSelector,
    KeywordIndexParams,
    KeywordIndexType,
    Modifier,
    OptimizersConfigDiff,
    PointStruct,
    QueryRequest,
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
from app.services.vector_db.interface.vector_db import FilterValue, IVectorDBService
from app.services.vector_db.qdrant.config import QdrantConfig
from app.services.vector_db.qdrant.filter import QdrantFilterMode
from app.services.vector_db.qdrant.utils import QdrantUtils
from app.utils.logger import create_logger

logger = create_logger("qdrant_service")

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
        """
        Factory method to create and initialize a QdrantService instance.
        Args:
            logger: Logger instance
            config_service: ConfigurationService instance
        Returns:
            QdrantService: Initialized QdrantService instance
        """
        service = cls(config, is_async=False)
        await service.connect_sync()
        return service

    @classmethod
    async def create_async(
        cls,
        config: ConfigurationService | QdrantConfig,
    ) -> 'QdrantService':
        """
        Factory method to create and initialize a QdrantService instance with async client.
        Args:
            logger: Logger instance
            config_service: ConfigurationService instance
        Returns:
            QdrantService: Initialized QdrantService instance with async client
        """
        service = cls(config, is_async=True)
        await service.connect_async()
        return service

    async def connect_async(self) -> None:
        """Connect to Qdrant using async client"""
        try:
            # Get Qdrant configuration
            if isinstance(self.config_service, ConfigurationService):
                qdrant_config = await self.config_service.get_config(config_node_constants.QDRANT.value)
            else:
                qdrant_config = self.config_service.qdrant_config
            if not qdrant_config:
                raise ValueError("Qdrant configuration not found")

            self.client = AsyncQdrantClient(
                host=qdrant_config.get("host"), # type: ignore
                port=qdrant_config.get("port"), # type: ignore
                api_key=qdrant_config.get("apiKey"), # type: ignore
                prefer_grpc=True,
                https=False,
                timeout=300,  # Increased timeout for large batches
                grpc_options={
                    'grpc.max_send_message_length': 64 * 1024 * 1024,  # 64MB
                    'grpc.max_receive_message_length': 64 * 1024 * 1024,  # 64MB
                    'grpc.keepalive_time_ms': 30000,
                    'grpc.keepalive_timeout_ms': 10000,
                    'grpc.http2.max_pings_without_data': 0,
                    'grpc.keepalive_permit_without_calls': 1,
                },
            )
            logger.info("✅ Connected to Qdrant with async client successfully")
        except Exception as e:
            logger.error(f"❌ Failed to connect to Qdrant with async client: {e}")
            raise

    async def connect(self) -> None:
        if self.is_async:
            await self.connect_async()
        else:
            await self.connect_sync()

    async def connect_sync(self) -> None:
        try:
            # Get Qdrant configuration
            if isinstance(self.config_service, ConfigurationService):
                qdrant_config = await self.config_service.get_config(config_node_constants.QDRANT.value)
            else:
                qdrant_config = self.config_service.qdrant_config
            if not qdrant_config:
                raise ValueError("Qdrant configuration not found")

            self.client = QdrantClient(
                host=qdrant_config.get("host"), # type: ignore
                port=qdrant_config.get("port"), # type: ignore
                api_key=qdrant_config.get("apiKey"), # type: ignore
                prefer_grpc=True,
                https=False,
                timeout=300,  # Increased timeout for large batches
                grpc_options={
                    'grpc.max_send_message_length': 64 * 1024 * 1024,  # 64MB
                    'grpc.max_receive_message_length': 64 * 1024 * 1024,  # 64MB
                    'grpc.keepalive_time_ms': 30000,
                    'grpc.keepalive_timeout_ms': 10000,
                    'grpc.http2.max_pings_without_data': 0,
                    'grpc.keepalive_permit_without_calls': 1,
                },
            )
            logger.info("✅ Connected to Qdrant successfully")
        except Exception as e:
            logger.error(f"❌ Failed to connect to Qdrant: {e}")
            raise

    async def disconnect(self) -> None:
        if self.client is not None:
            try:
                self.client.close()
                logger.info("✅ Disconnected from Qdrant successfully")
            except Exception as e:
                logger.warning(f"⚠️ Error during disconnect (likely harmless): {e}")
            finally:
                self.client = None

    def get_service_name(self) -> str:
        return "qdrant"

    def get_service(self) -> 'QdrantService':
        return self

    def get_service_client(self) -> QdrantClient | AsyncQdrantClient:
        return self.client

    async def get_collections(self) -> object:
        """Get all collections"""
        if self.client is None:
            raise RuntimeError("Client not connected. Call connect() first.")
        return self.client.get_collections()

    async def get_collection(
        self,
        collection_name: str,
    ) -> object:
        """Get a collection"""
        if self.client is None:
            raise RuntimeError("Client not connected. Call connect() first.")
        return self.client.get_collection(collection_name)

    async def delete_collection(
        self,
        collection_name: str,
    ) -> None:
        """Delete a collection"""
        if self.client is None:
            raise RuntimeError("Client not connected. Call connect() first.")
        self.client.delete_collection(collection_name)

    async def create_collection(
        self,
        embedding_size: int=1024,
        collection_name: str = VECTOR_DB_COLLECTION_NAME,
        sparse_idf: bool = False,
        vectors_config: Optional[dict] = None,
        sparse_vectors_config: Optional[dict] = None,
        optimizers_config: Optional[dict] = None,
        quantization_config: Optional[dict] = None,
    ) -> None:
        """Create a collection with default vector configuration if not provided"""
        if self.client is None:
            raise RuntimeError("Client not connected. Call connect() first.")

        # Set default values if not provided
        if vectors_config is None:
            vectors_config = {"dense": VectorParams(size=embedding_size, distance=Distance.COSINE)}

        if sparse_vectors_config is None:
            sparse_vectors_config = {
                "sparse": SparseVectorParams(
                    index=SparseIndexParams(on_disk=False),
                    modifier=Modifier.IDF if sparse_idf else None
                )
            }

        if optimizers_config is None:
            # Note: indexing_threshold and memmap_threshold are set globally in docker-compose
            optimizers_config = OptimizersConfigDiff(
                default_segment_number=8,
            )

        if quantization_config is None:
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
        logger.info(f"✅ Created collection {collection_name}")

    async def create_index(
        self,
        collection_name: str,
        field_name: str,
        field_schema: dict,
    ) -> None:
        """Create an index"""
        if self.client is None:
            raise RuntimeError("Client not connected. Call connect_sync() or connect_async() first.")

        if field_schema.get("type") == "keyword":
            field_schema = KeywordIndexParams(
                type=KeywordIndexType.KEYWORD,
            )

        # TODO: Add handling for Async client
        self.client.create_payload_index(collection_name, field_name, field_schema)

    async def filter_collection(
        self,
        filter_mode: Union[str, QdrantFilterMode] = QdrantFilterMode.MUST,
        must: Optional[Dict[str, FilterValue]] = None,
        should: Optional[Dict[str, FilterValue]] = None,
        must_not: Optional[Dict[str, FilterValue]] = None,
        min_should_match: Optional[int] = None,
        **kwargs: FilterValue,
    ) -> Filter:
        """
        Simple filter builder supporting must (AND), should (OR), and must_not (NOT) conditions

        Args:
            mode: Default filter mode for kwargs - FilterMode.MUST, FilterMode.SHOULD, or string
            must: Dictionary of conditions that MUST all be true (AND logic)
            should: Dictionary of conditions where at least one SHOULD be true (OR logic)
            must_not: Dictionary of conditions that MUST NOT be true (NOT logic)
            min_should_match: Minimum number of should conditions that must match
            **kwargs: Additional filters treated according to 'mode' parameter
        Returns:
            Filter: Qdrant Filter object
        Examples:
            # Simple AND (default mode)
            filter_collection(orgId="123", status="active")
            # Explicit FilterMode enum
            filter_collection(
                mode=FilterMode.SHOULD,
                department="IT",
                role="admin"
            )
            # String mode (converted to enum)
            filter_collection(mode="should", department="IT", role="admin")
            # Explicit must/should/must_not
            filter_collection(
                must={"orgId": "123"},
                should={"department": "IT", "role": "admin"},
                must_not={"status": "deleted"}
            )
            # Mixed with mode - kwargs go to specified mode
            filter_collection(
                mode=FilterMode.SHOULD,
                must={"orgId": "123"},      # Explicit must
                department="IT",            # Goes to should (because of mode)
                role="admin"                # Goes to should (because of mode)
            )
            # Complex business logic
            filter_collection(
                must={"orgId": "123", "active": True},
                should={"roles": ["admin", "user"], "departments": ["IT", "Engineering"]},
                must_not={"banned": True, "status": "deleted"},
                min_should_match=1
            )
        """
        if self.client is None:
            raise RuntimeError("Client not connected. Call connect() first.")

        # Convert string mode to enum
        if isinstance(filter_mode, str):
            try:
                filter_mode = QdrantFilterMode(filter_mode.lower())
            except ValueError:
                raise ValueError(f"Invalid mode '{filter_mode}'. Must be 'must', 'should', or 'must_not'")

        # Distribute kwargs based on mode
        all_must_filters = dict(must) if must else {}
        all_should_filters = dict(should) if should else {}
        all_must_not_filters = dict(must_not) if must_not else {}

        # Add kwargs to appropriate filter group based on mode
        if kwargs:
            if filter_mode == QdrantFilterMode.MUST:
                all_must_filters.update(kwargs)
            elif filter_mode == QdrantFilterMode.SHOULD:
                all_should_filters.update(kwargs)
            elif filter_mode == QdrantFilterMode.MUST_NOT:
                all_must_not_filters.update(kwargs)

        # Build conditions for each filter type
        must_conditions = QdrantUtils.build_conditions(all_must_filters) if all_must_filters else []
        should_conditions = QdrantUtils.build_conditions(all_should_filters) if all_should_filters else []
        must_not_conditions = QdrantUtils.build_conditions(all_must_not_filters) if all_must_not_filters else []

        # Validate we have at least some conditions
        if not must_conditions and not should_conditions and not must_not_conditions:
            logger.warning("No filters provided - returning empty filter")
            return Filter(should=[])  # Empty filter matches nothing

        # Build filter based on what we have
        filter_parts = {}

        if must_conditions:
            filter_parts["must"] = must_conditions

        if should_conditions:
            filter_parts["should"] = should_conditions
            if min_should_match is not None:
                filter_parts["min_should_match"] = min_should_match

        if must_not_conditions:
            filter_parts["must_not"] = must_not_conditions

        return Filter(**filter_parts)

    async def scroll(
        self,
        collection_name: str,
        scroll_filter: Filter,
        limit: int,
    ) -> object:
        """Scroll through a collection"""
        if self.client is None:
            raise RuntimeError("Client not connected. Call connect() first.")
        return self.client.scroll(collection_name, scroll_filter, limit)

    def overwrite_payload(
        self,
        collection_name: str,
        payload: dict,
        points: Filter,
    ) -> None:
        """Overwrite a payload"""
        if self.client is None:
            raise RuntimeError("Client not connected. Call connect() first.")
        self.client.overwrite_payload(collection_name, payload, points)

    def query_nearest_points(
        self,
        collection_name: str,
        requests: List[QueryRequest],
    ) -> List[List[PointStruct]]:
        """Query batch points"""
        if self.client is None:
            raise RuntimeError("Client not connected. Call connect() first.")
        return self.client.query_batch_points(collection_name, requests)

    def upsert_points(
        self,
        collection_name: str,
        points: List[PointStruct],
        batch_size: int = 1000,  # Optimal batch size for Qdrant
        max_workers: int = 5,  # Number of parallel upload threads
    ) -> None:
        """Upsert points with parallel batching for better performance"""
        if self.client is None:
            raise RuntimeError("Client not connected. Call connect() first.")

        start_time = time.perf_counter()
        total_points = len(points)
        logger.info(f"⏱️ Starting upsert of {total_points} points to collection '{collection_name}' (batch size: {batch_size}, parallel workers: {max_workers})")

        # If points fit in one batch, upload directly
        if total_points <= batch_size:
            self.client.upsert(collection_name, points)
        else:
            # Split into batches
            batches = []
            for i in range(0, total_points, batch_size):
                batch_end = min(i + batch_size, total_points)
                batch = points[i:batch_end]
                batch_num = (i // batch_size) + 1
                batches.append((batch_num, batch))

            total_batches = len(batches)
            completed_batches = 0

            # Upload batches in parallel using ThreadPoolExecutor
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
                            f"📦 Uploaded batch {batch_num}/{total_batches}: {batch_size_actual} points "
                            f"in {batch_elapsed:.2f}s ({batch_size_actual/batch_elapsed:.1f} points/s) "
                            f"[{completed_batches}/{total_batches} complete]"
                        )
                    except Exception as e:
                        batch_info = futures[future]
                        logger.error(f"❌ Failed to upload batch {batch_info[0]}: {str(e)}")
                        raise

        elapsed_time = time.perf_counter() - start_time
        throughput = total_points / elapsed_time if elapsed_time > 0 else 0
        logger.info(
            f"✅ Completed upsert of {total_points} points in {elapsed_time:.2f}s "
            f"(throughput: {throughput:.1f} points/s, avg: {elapsed_time/total_points*1000:.2f}ms per point)"
        )

    def delete_points(
        self,
        collection_name: str,
        filter: Filter,
    ) -> None:
        """Delete points"""
        if self.client is None:
            raise RuntimeError("Client not connected. Call connect() first.")
        self.client.delete(
            collection_name=collection_name,
            points_selector=FilterSelector(
                filter=filter
            ),
        )
        logger.info(f"✅ Deleted points from collection '{collection_name}'")
