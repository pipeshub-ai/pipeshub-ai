"""
Token Refresh Service
Handles automatic token refresh for OAuth connectors
"""

import asyncio
import logging
from datetime import datetime, timedelta
from typing import Dict

from app.config.key_value_store import KeyValueStore
from app.connectors.core.base.token_service.oauth_service import OAuthToken
from app.connectors.services.base_arango_service import BaseArangoService
from app.utils.oauth_config import get_oauth_config


class TokenRefreshService:
    """Service for managing token refresh across all connectors"""

    def __init__(self, key_value_store: KeyValueStore, arango_service: BaseArangoService) -> None:
        self.key_value_store = key_value_store
        self.arango_service = arango_service
        self.logger = logging.getLogger(__name__)
        self._refresh_tasks: Dict[str, asyncio.Task] = {}
        self._running = False

    async def start(self) -> None:
        """Start the token refresh service"""
        if self._running:
            return

        self._running = True
        self.logger.info("Starting token refresh service")

        # Start refresh tasks for all active connectors
        await self._refresh_all_tokens()

        # Start periodic refresh check
        asyncio.create_task(self._periodic_refresh_check())

    async def stop(self) -> None:
        """Stop the token refresh service"""
        self._running = False

        # Cancel all refresh tasks
        for task in self._refresh_tasks.values():
            task.cancel()

        self._refresh_tasks.clear()
        self.logger.info("Token refresh service stopped")

    async def _refresh_all_tokens(self) -> None:
        """Refresh tokens for all active connectors"""
        try:
            # Get all active connectors from database
            connectors = await self.arango_service.get_all_documents("apps")
            active_connectors = [conn for conn in connectors if conn.get('isActive', False)]

            for connector in active_connectors:
                connector_name = connector['name']
                auth_type = connector.get('authType', '')
                # Only refresh OAuth tokens
                if auth_type in ['OAUTH', 'OAUTH_ADMIN_CONSENT']:
                    await self._refresh_connector_token(connector_name)

        except Exception as e:
            self.logger.error(f"❌ Error refreshing tokens: {e}")

    async def _refresh_connector_token(self, connector_name: str) -> None:
        """Refresh token for a specific connector"""

        try:
            filtered_app_name = connector_name.replace(" ", "").lower()
            config_key = f"/services/connectors/{filtered_app_name}/config"
            config = await self.key_value_store.get_key(config_key)

            if not config or not config.get('credentials'):
                return

            credentials = config['credentials']
            if not credentials.get('refresh_token'):
                return

            auth_config = config.get('auth', {})


            # Create OAuth config
            oauth_config = get_oauth_config(connector_name, auth_config)

            # Create OAuth provider
            from app.connectors.core.base.token_service.oauth_service import (
                OAuthProvider,
            )
            oauth_provider = OAuthProvider(
                config=oauth_config,
                key_value_store=self.key_value_store,
                credentials_path=f"/services/connectors/{filtered_app_name}/config"
            )

            # Create token from stored credentials
            token = OAuthToken.from_dict(credentials)

            # If token not expired, ensure a scheduled refresh is set and return
            if not token.is_expired:
                try:
                    await self.schedule_token_refresh(connector_name, token)
                finally:
                    await oauth_provider.close()
                return

            # Refresh token using OAuth provider
            new_token = await oauth_provider.refresh_access_token(token.refresh_token)

            # Clean up OAuth provider
            await oauth_provider.close()

            # Update stored credentials
            config['credentials'] = new_token.to_dict()
            await self.key_value_store.create_key(config_key, config)

            self.logger.info(f"Refreshed token for connector {connector_name}")

            # Schedule next refresh for the new token
            await self.schedule_token_refresh(connector_name, new_token)

        except Exception as e:
            self.logger.error(f"❌ Error refreshing token for {connector_name}: {e}")

    async def _periodic_refresh_check(self) -> None:
        """Periodically check and refresh tokens"""
        while self._running:
            try:
                await asyncio.sleep(300)  # Check every 5 minutes
                await self._refresh_all_tokens()
            except asyncio.CancelledError:
                break
            except Exception as e:
                self.logger.error(f"❌ Error in periodic refresh check: {e}")

    async def refresh_connector_token(self, connector_name: str) -> None:
        """Manually refresh token for a specific connector"""
        await self._refresh_connector_token(connector_name)

    async def schedule_token_refresh(self, connector_name: str, token: OAuthToken) -> None:
        """Schedule token refresh for a specific connector"""

        self.logger.info("Scheduling token refresh for connector %s", connector_name)
        if not token.expires_in:
            return

        # Calculate refresh time (refresh 5 minutes before expiry)
        # Refresh 10 minutes before expiry for safety
        refresh_time = token.created_at + timedelta(seconds=max(0, token.expires_in - 600))
        delay = (refresh_time - datetime.now()).total_seconds()

        if delay > 0:
            # Cancel existing task if any
            if connector_name in self._refresh_tasks:
                self._refresh_tasks[connector_name].cancel()

            # Schedule new refresh
            self._refresh_tasks[connector_name] = asyncio.create_task(
                self._delayed_refresh(connector_name, delay)
            )

        self.logger.info("Scheduled token refresh for connector %s", connector_name)

    async def _delayed_refresh(self, connector_name: str, delay: float) -> None:
        """Delayed token refresh"""
        await asyncio.sleep(delay)
        await self._refresh_connector_token(connector_name)
