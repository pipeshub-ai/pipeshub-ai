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
        self._refresh_lock = asyncio.Lock()  # Prevent concurrent refresh operations
        self._processing_connectors: set = set()  # Track connectors currently being processed to prevent recursion

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
        """Refresh tokens for all authenticated connectors (regardless of active status)"""
        # Prevent concurrent execution
        async with self._refresh_lock:
            await self._refresh_all_tokens_internal()

    async def _refresh_all_tokens_internal(self) -> None:
        """Internal method to refresh tokens (called with lock held)"""
        try:
            # Get all connectors from database
            connectors = await self.arango_service.get_all_documents("apps")

            # Filter for authenticated connectors with OAuth tokens
            # Check if connector has credentials stored (isAuthenticated or has credentials in config)
            authenticated_connectors = []
            for conn in connectors:
                auth_type = conn.get('authType', '')
                connector_id = conn.get('_key')

                # Only process OAuth connectors
                if auth_type not in ['OAUTH', 'OAUTH_ADMIN_CONSENT']:
                    continue

                # Check if connector has credentials (authenticated)
                try:
                    config_key = f"/services/connectors/{connector_id}/config"
                    config = await self.key_value_store.get_key(config_key)
                    if config and config.get('credentials') and config['credentials'].get('refresh_token'):
                        authenticated_connectors.append(conn)
                        self.logger.debug(f"Found authenticated OAuth connector: {connector_id}")
                except Exception as e:
                    self.logger.debug(f"Could not check credentials for connector {connector_id}: {e}")
                    continue

            self.logger.info(f"Found {len(authenticated_connectors)} authenticated OAuth connectors to refresh")

            # Deduplicate by connector_id to prevent processing same connector twice
            processed_connectors = set()
            for connector in authenticated_connectors:
                connector_id = connector.get('_key')
                if not connector_id:
                    self.logger.debug("Skipping connector with no ID")
                    continue
                if connector_id in processed_connectors:
                    self.logger.debug(f"Skipping duplicate connector: {connector_id}")
                    continue
                processed_connectors.add(connector_id)

                connector_type = connector.get('type', '')

                # Process this connector (will schedule if not expired, or refresh if expired)
                try:
                    await self._refresh_connector_token(connector_id, connector_type)
                except Exception as e:
                    self.logger.error(f"Failed to process connector {connector_id}: {e}", exc_info=False)  # Don't log full trace to avoid recursion in logging

        except Exception as e:
            self.logger.error(f"❌ Error refreshing tokens: {e}", exc_info=True)

    async def _perform_token_refresh(self, connector_id: str, connector_type: str, refresh_token: str) -> OAuthToken:
        """
        Core token refresh logic - performs the actual OAuth token refresh.
        This is the single source of truth for token refresh operations.

        Args:
            connector_id: The connector ID
            connector_type: The connector type
            refresh_token: The refresh token to use

        Returns:
            The new OAuthToken after refresh

        Raises:
            Exception: If refresh fails
        """
        config_key = f"/services/connectors/{connector_id}/config"
        config = await self.key_value_store.get_key(config_key)

        if not config:
            raise ValueError(f"No config found for connector {connector_id}")

        auth_config = config.get('auth', {})
        oauth_config = get_oauth_config(connector_type, auth_config)

        # Create OAuth provider
        from app.connectors.core.base.token_service.oauth_service import OAuthProvider
        oauth_provider = OAuthProvider(
            config=oauth_config,
            key_value_store=self.key_value_store,
            credentials_path=config_key
        )

        try:
            # Perform the token refresh
            self.logger.info(f"🔄 Refreshing token for connector {connector_id}")
            new_token = await oauth_provider.refresh_access_token(refresh_token)
            self.logger.info(f"✅ Successfully refreshed token for connector {connector_id}")

            # Update stored credentials
            config['credentials'] = new_token.to_dict()
            await self.key_value_store.create_key(config_key, config)
            self.logger.info(f"💾 Updated stored credentials for connector {connector_id}")

            return new_token
        finally:
            # Always clean up OAuth provider
            await oauth_provider.close()

    async def _refresh_connector_token(self, connector_id: str, connector_type: str) -> None:
        """
        Check token status and refresh if needed, then schedule next refresh.
        This method orchestrates the token refresh workflow.
        """
        # Prevent recursion - if already processing this connector, skip
        if connector_id in self._processing_connectors:
            self.logger.warning(f"⚠️ Already processing connector {connector_id}, skipping to prevent recursion")
            return

        self._processing_connectors.add(connector_id)
        try:
            config_key = f"/services/connectors/{connector_id}/config"
            config = await self.key_value_store.get_key(config_key)

            if not config or not config.get('credentials'):
                return

            credentials = config['credentials']
            if not credentials.get('refresh_token'):
                return

            # Create token from stored credentials
            token = OAuthToken.from_dict(credentials)

            # Calculate expiry time for logging
            expiry_time = None
            if token.expires_in:
                expiry_time = token.created_at + timedelta(seconds=token.expires_in)
            self.logger.debug(
                f"Token for connector {connector_id}: "
                f"expires_in={token.expires_in}s, "
                f"expiry_time={expiry_time}, "
                f"is_expired={token.is_expired}"
            )

            # If token not expired, just schedule refresh and return
            if not token.is_expired:
                self.logger.info(f"✅ Token not expired for connector {connector_id}, scheduling refresh")
                await self.schedule_token_refresh(connector_id, connector_type, token)
                return

            # Token is expired - refresh it using the core refresh method
            self.logger.info(f"🔄 Token expired for connector {connector_id}, refreshing now")
            new_token = await self._perform_token_refresh(connector_id, connector_type, token.refresh_token)

            # Schedule next refresh for the new token
            await self.schedule_token_refresh(connector_id, connector_type, new_token)

        except RecursionError as e:
            # Special handling for recursion errors to avoid further recursion in logging
            print(f"RECURSION ERROR in token refresh for {connector_id}: {str(e)[:100]}", flush=True)
        except Exception as e:
            # Use exc_info=False to avoid potential recursion in traceback formatting
            self.logger.error(f"❌ Error refreshing token for connector {connector_id}: {e}", exc_info=False)
        finally:
            # Always remove from processing set to allow future attempts
            self._processing_connectors.discard(connector_id)

    async def _periodic_refresh_check(self) -> None:
        """Periodically check and refresh tokens"""
        self.logger.info("🔄 Starting periodic token refresh check (every 5 minutes)")
        while self._running:
            try:
                await asyncio.sleep(300)  # Check every 5 minutes
                if self._running:
                    self.logger.debug("🔄 Running periodic token refresh check...")
                    await self._refresh_all_tokens()
            except asyncio.CancelledError:
                self.logger.info("🛑 Periodic refresh check cancelled")
                break
            except Exception as e:
                self.logger.error(f"❌ Error in periodic refresh check: {e}", exc_info=True)

    async def refresh_connector_token(self, connector_id: str, connector_type: str) -> None:
        """Manually refresh token for a specific connector"""
        await self._refresh_connector_token(connector_id, connector_type)

    async def schedule_token_refresh(self, connector_id: str, connector_type: str, token: OAuthToken) -> None:
        """
        Schedule token refresh for a specific connector.
        If the token needs immediate refresh (delay <= 0), refreshes it immediately then schedules.
        """
        if not self._running:
            self.logger.warning(f"⚠️ Token refresh service not running, cannot schedule refresh for connector {connector_id}")
            # Service might not be started yet, but we can still schedule the task
            # It will be picked up when service starts

        self.logger.info(f"🔄 Scheduling token refresh for connector {connector_id} (type: {connector_type})")

        if not token.expires_in:
            self.logger.warning(f"⚠️ Token for connector {connector_id} has no expiry time, cannot schedule refresh")
            return

        # Calculate refresh time (refresh 10 minutes before expiry for safety)
        refresh_time = token.created_at + timedelta(seconds=max(0, token.expires_in - 600))
        delay = (refresh_time - datetime.now()).total_seconds()

        if delay <= 0:
            # Token needs immediate refresh (refresh time has already passed)
            # Use the core refresh method to avoid code duplication
            self.logger.warning(
                f"⚠️ Token for connector {connector_id} needs immediate refresh "
                f"(expires_in={token.expires_in}s, delay={delay:.1f}s). Refreshing now..."
            )

            try:
                # Call the core refresh method (no recursion - this is the single source of truth)
                new_token = await self._perform_token_refresh(connector_id, connector_type, token.refresh_token)

                # Calculate schedule with the NEW token
                new_refresh_time = new_token.created_at + timedelta(seconds=max(0, new_token.expires_in - 600))
                new_delay = (new_refresh_time - datetime.now()).total_seconds()

                if new_delay <= 0:
                    # Safeguard: New token is also expired/expiring? Something is seriously wrong
                    self.logger.error(
                        f"❌ New token for connector {connector_id} is also expired/expiring soon! "
                        f"(expires_in={new_token.expires_in}s, delay={new_delay:.1f}s). "
                        f"Cannot schedule refresh - will be picked up by periodic check."
                    )
                    return

                # Use the new token for scheduling
                token = new_token
                delay = new_delay
                refresh_time = new_refresh_time
                self.logger.info(f"🔄 Scheduling next refresh for connector {connector_id} with new token")

            except Exception as e:
                self.logger.error(f"❌ Failed to perform immediate refresh for connector {connector_id}: {e}", exc_info=False)
                return

        # Check if there's an existing valid task
        if connector_id in self._refresh_tasks:
            old_task = self._refresh_tasks[connector_id]
            if old_task.done():
                # Task already completed or cancelled, remove it
                del self._refresh_tasks[connector_id]
                self.logger.debug(f"Removed completed/cancelled task for connector {connector_id}")
            else:
                # Task is still running - cancel it to reschedule with new timing
                # This ensures we always have the most up-to-date refresh schedule
                try:
                    old_task.cancel()
                    self.logger.debug(f"Cancelled existing refresh task for connector {connector_id} to reschedule")
                except Exception as e:
                    self.logger.warning(f"Error cancelling existing task for connector {connector_id}: {e}")

        # Schedule new refresh
        try:
            task = asyncio.create_task(
                self._delayed_refresh(connector_id, connector_type, delay)
            )
            self._refresh_tasks[connector_id] = task
            self.logger.info(
                f"✅ Scheduled token refresh for connector {connector_id} in {delay:.0f} seconds "
                f"({delay/60:.1f} minutes) - will refresh at {refresh_time}"
            )
        except Exception as e:
            self.logger.error(f"❌ Failed to schedule token refresh for connector {connector_id}: {e}", exc_info=True)

    async def _delayed_refresh(self, connector_id: str, connector_type: str, delay: float) -> None:
        """Delayed token refresh"""
        try:
            await asyncio.sleep(delay)
            self.logger.info(f"⏰ Scheduled refresh time reached for connector {connector_id}, refreshing token...")
            await self._refresh_connector_token(connector_id, connector_type)
        except asyncio.CancelledError:
            # This is expected when rescheduling - don't log as error
            self.logger.debug(f"🔄 Token refresh task cancelled for connector {connector_id} (likely rescheduled)")
            raise
        except Exception as e:
            self.logger.error(f"❌ Error in delayed token refresh for connector {connector_id}: {e}", exc_info=True)
        finally:
            # Remove task from tracking only if it's this task
            if connector_id in self._refresh_tasks and self._refresh_tasks[connector_id].done():
                del self._refresh_tasks[connector_id]
