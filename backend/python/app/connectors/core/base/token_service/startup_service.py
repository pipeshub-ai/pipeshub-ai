"""
Startup Service
Initializes token refresh service on application startup
"""

import logging
from typing import Optional

from app.config.configuration_service import ConfigurationService
from app.connectors.core.base.token_service.token_refresh_service import (
    TokenRefreshService,
)
from app.connectors.services.base_arango_service import BaseArangoService


class StartupService:
    """Service for application startup tasks"""

    def __init__(self) -> None:
        self.logger = logging.getLogger(__name__)
        self._token_refresh_service: Optional[TokenRefreshService] = None

    async def initialize(self, configuration_service: ConfigurationService, arango_service: BaseArangoService) -> None:
        """Initialize startup services"""
        try:
            # Initialize token refresh service
            self._token_refresh_service = TokenRefreshService(configuration_service, arango_service)
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

    def get_token_refresh_service(self) -> Optional[TokenRefreshService]:
        """Get the token refresh service instance"""
        return self._token_refresh_service


# Global startup service instance
startup_service = StartupService()
