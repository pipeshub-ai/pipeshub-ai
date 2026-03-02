"""
Startup Service
Initializes token refresh services on application startup
Handles both connector and toolset token refresh services separately
"""

import asyncio
import logging
from typing import Optional

from app.config.configuration_service import ConfigurationService
from app.connectors.core.base.token_service.token_refresh_service import (
    TokenRefreshService,
)
from app.connectors.core.base.token_service.toolset_token_refresh_service import (
    ToolsetTokenRefreshService,
)
from app.services.graph_db.interface.graph_db_provider import IGraphDBProvider


class StartupService:
    """Service for application startup tasks"""

    def __init__(self) -> None:
        self.logger = logging.getLogger(__name__)
        self._token_refresh_service: Optional[TokenRefreshService] = None
        self._toolset_token_refresh_service: Optional[ToolsetTokenRefreshService] = None
        self._initialize_lock = asyncio.Lock()
        self._initialized = False


    async def initialize(self, configuration_service: ConfigurationService, graph_provider: IGraphDBProvider) -> None:
        """Initialize startup services"""
        async with self._initialize_lock:
            if self._initialized:
                self.logger.info("⏭️ Startup services already initialized, skipping duplicate initialize call")
                return

            try:
                # Initialize connector token refresh service
                token_refresh_service = TokenRefreshService(configuration_service, graph_provider)
                await token_refresh_service.start()
                self._token_refresh_service = token_refresh_service
                self.logger.info("✅ Connector token refresh service initialized")

                # Initialize toolset token refresh service (separate from connectors)
                toolset_token_refresh_service = ToolsetTokenRefreshService(configuration_service)
                await toolset_token_refresh_service.start()
                self._toolset_token_refresh_service = toolset_token_refresh_service
                self.logger.info("✅ Toolset token refresh service initialized")

                self._initialized = True
                self.logger.info("Startup services initialized successfully")

            except Exception as e:
                # Best-effort cleanup for partial initialization
                if self._token_refresh_service:
                    try:
                        await self._token_refresh_service.stop()
                    except Exception:
                        pass
                    self._token_refresh_service = None

                if self._toolset_token_refresh_service:
                    try:
                        await self._toolset_token_refresh_service.stop()
                    except Exception:
                        pass
                    self._toolset_token_refresh_service = None

                self._initialized = False
                self.logger.error(f"Error initializing startup services: {e}")
                raise

    async def shutdown(self) -> None:
        """Shutdown startup services"""
        async with self._initialize_lock:
            try:
                if self._token_refresh_service:
                    await self._token_refresh_service.stop()
                    self.logger.info("✅ Connector token refresh service stopped")
                    self._token_refresh_service = None

                if self._toolset_token_refresh_service:
                    await self._toolset_token_refresh_service.stop()
                    self.logger.info("✅ Toolset token refresh service stopped")
                    self._toolset_token_refresh_service = None

                self._initialized = False
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
