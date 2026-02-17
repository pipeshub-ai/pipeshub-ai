"""
Startup Service
Initializes token refresh services on application startup
Handles both connector and toolset token refresh services separately
"""

import logging
from typing import Optional

from app.config.configuration_service import ConfigurationService
from app.connectors.core.base.token_service.token_refresh_service import (
    TokenRefreshService,
)
from app.services.graph_db.interface.graph_db_provider import IGraphDBProvider
from app.connectors.core.base.token_service.toolset_token_refresh_service import (
    ToolsetTokenRefreshService,
)


class StartupService:
    """Service for application startup tasks"""

    def __init__(self) -> None:
        self.logger = logging.getLogger(__name__)
        self._token_refresh_service: Optional[TokenRefreshService] = None
        self._toolset_token_refresh_service: Optional[ToolsetTokenRefreshService] = None


    async def initialize(self, configuration_service: ConfigurationService, graph_provider: IGraphDBProvider) -> None:
        """Initialize startup services"""
        try:
            # Initialize token refresh service
            self._token_refresh_service = TokenRefreshService(configuration_service, graph_provider)

            await self._token_refresh_service.start()
            self.logger.info("✅ Connector token refresh service initialized")

            # Initialize toolset token refresh service (separate from connectors)
            self._toolset_token_refresh_service = ToolsetTokenRefreshService(configuration_service)
            await self._toolset_token_refresh_service.start()
            self.logger.info("✅ Toolset token refresh service initialized")

            self.logger.info("Startup services initialized successfully")

        except Exception as e:
            self.logger.error(f"Error initializing startup services: {e}")
            raise

    async def shutdown(self) -> None:
        """Shutdown startup services"""
        try:
            if self._token_refresh_service:
                await self._token_refresh_service.stop()
                self.logger.info("✅ Connector token refresh service stopped")

            if self._toolset_token_refresh_service:
                await self._toolset_token_refresh_service.stop()
                self.logger.info("✅ Toolset token refresh service stopped")

            self.logger.info("Startup services shutdown successfully")

        except Exception as e:
            self.logger.error(f"Error shutting down startup services: {e}")

    def get_token_refresh_service(self) -> Optional[TokenRefreshService]:
        """Get the connector token refresh service instance (legacy/production)"""
        return self._token_refresh_service

    def get_toolset_token_refresh_service(self) -> Optional[ToolsetTokenRefreshService]:
        """Get the toolset token refresh service instance"""
        return self._toolset_token_refresh_service


# Global startup service instance
startup_service = StartupService()
