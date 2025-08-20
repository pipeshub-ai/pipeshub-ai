from typing import Any, Dict, Optional, Union

from qdrant_client import AsyncQdrantClient, QdrantClient  # type: ignore
from qdrant_client.http.models import Filter  # type: ignore

from app.config.configuration_service import ConfigurationService
from app.config.constants.service import config_node_constants
from app.services.vector_db.interface.vector_db import IVectorDBService
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
                timeout=180,
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
                timeout=180,
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
        collection_name: str,
        vectors_config: dict,
        sparse_vectors_config: dict,
        optimizers_config: dict,
        quantization_config: dict,
    ) -> None:
        """Create a collection"""
        if self.client is None:
            raise RuntimeError("Client not connected. Call connect() first.")
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
        index_type: str = "persistent",
    ) -> None:
        """Create an index"""
        if self.client is None:
            raise RuntimeError("Client not connected. Call connect_sync() or connect_async() first.")
        self.client.create_payload_index(collection_name, field_name, field_schema, index_type)
        logger.info(f"✅ Created index {field_name} on collection {collection_name}")

    async def filter_collection(
        self,
        filter_mode: Union[str, QdrantFilterMode] = QdrantFilterMode.MUST,
        must: Optional[Dict[str, Any]] = None,
        should: Optional[Dict[str, Any]] = None,
        must_not: Optional[Dict[str, Any]] = None,
        min_should_match: Optional[int] = None,
        **kwargs: Any,
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
