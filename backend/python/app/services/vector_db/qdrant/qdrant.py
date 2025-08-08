import logging
from typing import Optional

from qdrant_client import QdrantClient  # type: ignore

from app.config.configuration_service import ConfigurationService
from app.config.constants.service import config_node_constants
from app.services.vector_db.interface.vector_db import IVectorDBService


class QdrantService(IVectorDBService):
    def __init__(self, logger: logging.Logger, config_service: ConfigurationService) -> None:
        self.logger = logger
        self.config_service = config_service
        self.client: Optional[QdrantClient] = None

    @classmethod
    async def create(cls, logger: logging.Logger, config_service: ConfigurationService) -> 'QdrantService':
        """
        Factory method to create and initialize a QdrantService instance.
        Args:
            logger: Logger instance
            config_service: ConfigurationService instance
        Returns:
            QdrantService: Initialized QdrantService instance
        """
        service = cls(logger, config_service)
        await service.connect()
        return service

    async def connect(self) -> None:
        try:
            # Get Qdrant configuration
            qdrant_config = await self.config_service.get_config(config_node_constants.QDRANT.value)
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
            self.logger.info("✅ Connected to Qdrant successfully")
        except Exception as e:
            self.logger.error(f"❌ Failed to connect to Qdrant: {e}")
            raise

    async def disconnect(self) -> None:
        if self.client is not None:
            try:
                self.client.close()
                self.logger.info("✅ Disconnected from Qdrant successfully")
            except Exception as e:
                self.logger.warning(f"⚠️ Error during disconnect (likely harmless): {e}")
            finally:
                self.client = None

    async def get_service_name(self) -> str:
        return "qdrant"

    async def get_service_client(self) -> QdrantClient:
        if self.client is None:
            raise RuntimeError("Client not connected. Call connect() first.")
        return self.client
