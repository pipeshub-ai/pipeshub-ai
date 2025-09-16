"""
Startup Service
Initializes token refresh service on application startup
"""

import logging
from typing import Optional

from app.config.key_value_store import KeyValueStore
from app.connectors.core.base.token_service.token_refresh_service import (
    TokenRefreshService,
)
from app.connectors.services.base_arango_service import BaseArangoService


class StartupService:
    """Service for application startup tasks"""

    def __init__(self) -> None:
        self.logger = logging.getLogger(__name__)
        self._token_refresh_service: Optional[TokenRefreshService] = None

    async def initialize(self, key_value_store: KeyValueStore, arango_service: BaseArangoService) -> None:
        """Initialize startup services"""
        try:
            # Initialize token refresh service
            self._token_refresh_service = TokenRefreshService(key_value_store, arango_service)
            await self._token_refresh_service.start()

            self.logger.info("Startup services initialized successfully")

        except Exception as e:
            self.logger.error(f"Error initializing startup services: {e}")
            raise

    async def shutdown(self) -> None:
        """Shutdown startup services"""
        try:
            if self._token_refresh_service:
                await self._token_refresh_service.stop()

            self.logger.info("Startup services shutdown successfully")

        except Exception as e:
            self.logger.error(f"Error shutting down startup services: {e}")


# Global startup service instance
startup_service = StartupService()
