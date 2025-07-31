from typing import Optional
from qdrant_client import QdrantClient # type: ignore
import logging
from app.config.configuration_service import ConfigurationService, config_node_constants
from app.services.vector_db.interface.vector_db import IVectorDBService

class QdrantService(IVectorDBService):
    def __init__(self, logger: logging.Logger, config_service: ConfigurationService):
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

    async def connect(self):
        try:
            # Get Qdrant configuration
            qdrant_config = await self.config_service.get_config(config_node_constants.QDRANT.value)
            
            self.client = QdrantClient(
                host=qdrant_config["host"],
                port=qdrant_config["port"],
                api_key=qdrant_config["apiKey"],
                prefer_grpc=True,
                https=False,
                timeout=180,
            )
            self.logger.info("✅ Connected to Qdrant successfully")
        except Exception as e:
            self.logger.error(f"❌ Failed to connect to Qdrant: {e}")
            raise

    async def disconnect(self):
        if self.client is not None:
            try:
                self.client.close()  
                self.logger.info("✅ Disconnected from Qdrant successfully")
            except Exception as e:
                self.logger.warning(f"⚠️ Error during disconnect (likely harmless): {e}")
            finally:
                self.client = None

    async def get_service_name(self):
        return "qdrant"

    async def get_service_client(self):
        if self.client is None:
            raise RuntimeError("Client not connected. Call connect() first.")
        return self.client