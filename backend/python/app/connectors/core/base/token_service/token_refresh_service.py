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
from app.services.graph_db.interface.graph_db_provider import IGraphDBProvider
from app.utils.oauth_config import get_oauth_config


class TokenRefreshService:
    """Service for managing token refresh across all connectors"""

    def __init__(self, key_value_store: KeyValueStore, graph_provider: IGraphDBProvider) -> None:
        self.key_value_store = key_value_store
        self.graph_provider = graph_provider
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

    async def _is_connector_authenticated(self, connector_id: str) -> bool:
        """
        Check if connector has valid OAuth credentials stored.

        Returns:
            True if connector has refresh_token, False otherwise
        """
        try:
            config_key = f"/services/connectors/{connector_id}/config"
            config = await self.key_value_store.get_key(config_key)

            if not config:
                return False

            credentials = config.get('credentials')
            if not credentials:
                return False

            return bool(credentials.get('refresh_token'))

        except Exception as e:
            self.logger.debug(f"Could not check credentials for connector {connector_id}: {e}")
            return False

    def _is_oauth_connector(self, connector: Dict[str, any]) -> bool:
        """Check if connector uses OAuth authentication."""
        auth_type = connector.get('authType', '')
        return auth_type in ['OAUTH', 'OAUTH_ADMIN_CONSENT']

    async def _filter_authenticated_oauth_connectors(
        self,
        connectors: list
    ) -> list:
        """
        Filter connectors to only include authenticated OAuth connectors.

        Args:
            connectors: List of all connectors from database

        Returns:
            List of authenticated OAuth connectors
        """
        authenticated_connectors = []

        for conn in connectors:
            # Only process OAuth connectors
            if not self._is_oauth_connector(conn):
                continue

            connector_id = conn.get('_key')
            if not connector_id:
                continue

            # Check if connector has credentials
            if await self._is_connector_authenticated(connector_id):
                authenticated_connectors.append(conn)
                self.logger.debug(f"Found authenticated OAuth connector: {connector_id}")

        return authenticated_connectors

    async def _process_connectors_for_refresh(
        self,
        authenticated_connectors: list
    ) -> None:
        """
        Process each authenticated connector for token refresh.
        Deduplicates by connector_id and handles errors gracefully.

        Args:
            authenticated_connectors: List of authenticated OAuth connectors
        """
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
                self.logger.error(f"Failed to process connector {connector_id}: {e}", exc_info=False)

    async def _refresh_all_tokens_internal(self) -> None:
        """Internal method to refresh tokens (called with lock held)"""
        try:
            # 1. Get all connectors from database
            connectors = await self.graph_provider.get_all_documents("apps")

            # 2. Filter for authenticated OAuth connectors
            authenticated_connectors = await self._filter_authenticated_oauth_connectors(connectors)

            self.logger.info(f"Found {len(authenticated_connectors)} authenticated OAuth connectors to refresh")

            # 3. Process each connector for refresh
            await self._process_connectors_for_refresh(authenticated_connectors)

        except Exception as e:
            self.logger.error(f"❌ Error refreshing tokens: {e}", exc_info=True)

    # ============================================================================
    # Helper Methods for OAuth Config Building
    # ============================================================================

    async def _fetch_shared_oauth_config(
        self,
        oauth_config_id: str,
        connector_type: str
    ) -> Dict[str, any]:
        """
        Fetch shared OAuth config from ETCD.

        Returns:
            OAuth config dict if found, empty dict otherwise
        """
        try:
            oauth_config_path = f"/services/oauth/{connector_type.lower().replace(' ', '')}"
            oauth_configs = await self.key_value_store.get_key(oauth_config_path)

            if not oauth_configs or not isinstance(oauth_configs, list):
                self.logger.warning(f"No OAuth configs found for connector type {connector_type}")
                return {}

            # Find the OAuth config by ID
            for oauth_cfg in oauth_configs:
                if oauth_cfg.get("_id") == oauth_config_id:
                    return oauth_cfg

            self.logger.warning(f"OAuth config {oauth_config_id} not found in list")
            return {}

        except Exception as e:
            self.logger.warning(f"Error fetching shared OAuth config {oauth_config_id}: {e}")
            return {}

    def _enrich_from_registry(
        self,
        oauth_flow_config: Dict[str, any],
        connector_type: str
    ) -> None:
        """
        Enrich OAuth config with missing infrastructure fields from registry.
        Modifies oauth_flow_config in-place.
        """
        # Check if enrichment is needed
        if "tokenAccessType" in oauth_flow_config and "additionalParams" in oauth_flow_config:
            return

        try:
            from app.connectors.core.registry.oauth_config_registry import (
                get_oauth_config_registry,
            )
            oauth_registry = get_oauth_config_registry()
            registry_oauth_config = oauth_registry.get_config(connector_type)

            if not registry_oauth_config:
                return

            # Add missing optional fields from registry
            if "tokenAccessType" not in oauth_flow_config and registry_oauth_config.token_access_type:
                oauth_flow_config["tokenAccessType"] = registry_oauth_config.token_access_type

            if "additionalParams" not in oauth_flow_config and registry_oauth_config.additional_params:
                oauth_flow_config["additionalParams"] = registry_oauth_config.additional_params

            self.logger.debug(f"Enriched OAuth config from registry for {connector_type}")

        except Exception as e:
            self.logger.debug(f"Could not enrich OAuth config from registry: {e}")

    def _extract_scopes(
        self,
        shared_oauth_config: Dict[str, any],
        connector_scope: str
    ) -> list:
        """
        Extract appropriate scopes based on connector scope.

        Args:
            shared_oauth_config: OAuth config with scopes
            connector_scope: Connector scope (personal/team/agent)

        Returns:
            List of scope strings
        """
        scopes_data = shared_oauth_config.get("scopes", {})

        if not isinstance(scopes_data, dict):
            return scopes_data if isinstance(scopes_data, list) else []

        # Map connector scope to scope key
        scope_key_map = {
            "personal": "personal_sync",
            "team": "team_sync",
            "agent": "agent"
        }
        scope_key = scope_key_map.get(connector_scope.lower(), "team_sync")

        # Get scopes for the specific connector scope
        scope_list = scopes_data.get(scope_key, [])
        return scope_list if isinstance(scope_list, list) else []

    def _extract_credentials_from_oauth_config(
        self,
        shared_oauth_config: Dict[str, any]
    ) -> tuple[str, str]:
        """
        Extract clientId and clientSecret from OAuth config.

        Returns:
            Tuple of (client_id, client_secret), both may be None
        """
        oauth_config_data = shared_oauth_config.get("config", {})
        if not oauth_config_data:
            return None, None

        client_id = oauth_config_data.get("clientId") or oauth_config_data.get("client_id")
        client_secret = oauth_config_data.get("clientSecret") or oauth_config_data.get("client_secret")

        return client_id, client_secret

    def _build_oauth_flow_from_shared_config(
        self,
        shared_oauth_config: Dict[str, any],
        connector_scope: str,
        connector_type: str
    ) -> Dict[str, any]:
        """
        Build OAuth flow config from shared OAuth config.

        Returns:
            OAuth flow config dict with all necessary fields
        """
        oauth_flow_config = {
            "authorizeUrl": shared_oauth_config.get("authorizeUrl", ""),
            "tokenUrl": shared_oauth_config.get("tokenUrl", ""),
            "redirectUri": shared_oauth_config.get("redirectUri", ""),
        }

        # Add optional infrastructure fields if present
        if "tokenAccessType" in shared_oauth_config:
            oauth_flow_config["tokenAccessType"] = shared_oauth_config["tokenAccessType"]
        if "additionalParams" in shared_oauth_config:
            oauth_flow_config["additionalParams"] = shared_oauth_config["additionalParams"]

        # Enrich from registry if fields are missing
        self._enrich_from_registry(oauth_flow_config, connector_type)

        # Extract and add scopes
        oauth_flow_config["scopes"] = self._extract_scopes(shared_oauth_config, connector_scope)

        return oauth_flow_config

    def _build_oauth_flow_from_auth_config(
        self,
        auth_config: Dict[str, any],
        base_config: Dict[str, any]
    ) -> Dict[str, any]:
        """
        Build/enrich OAuth flow config from auth config (fallback).

        Args:
            auth_config: Auth configuration
            base_config: Base OAuth flow config (may be empty or partially filled)

        Returns:
            Enriched OAuth flow config
        """
        # Fill in missing fields from auth config
        if not base_config.get("authorizeUrl"):
            base_config["authorizeUrl"] = auth_config.get("authorizeUrl", "")
        if not base_config.get("tokenUrl"):
            base_config["tokenUrl"] = auth_config.get("tokenUrl", "")
        if not base_config.get("redirectUri"):
            base_config["redirectUri"] = auth_config.get("redirectUri", "")
        if not base_config.get("scopes"):
            base_config["scopes"] = auth_config.get("scopes", [])

        return base_config

    async def _build_complete_oauth_config(
        self,
        connector_id: str,
        connector_type: str,
        auth_config: Dict[str, any]
    ) -> Dict[str, any]:
        """
        Build complete OAuth flow configuration from all available sources.
        Tries shared OAuth config first, falls back to auth config.

        Args:
            connector_id: Connector ID (for logging)
            connector_type: Connector type
            auth_config: Auth configuration from connector

        Returns:
            Complete OAuth flow config with clientId, clientSecret, and all infrastructure fields

        Raises:
            ValueError: If credentials cannot be found in any source
        """
        oauth_config_id = auth_config.get("oauthConfigId")
        connector_scope = auth_config.get("connectorScope", "team")

        # Try to use shared OAuth config first
        if oauth_config_id:
            shared_oauth_config = await self._fetch_shared_oauth_config(oauth_config_id, connector_type)

            if shared_oauth_config:
                # Build config from shared OAuth config
                oauth_flow_config = self._build_oauth_flow_from_shared_config(
                    shared_oauth_config,
                    connector_scope,
                    connector_type
                )

                # Extract credentials
                client_id, client_secret = self._extract_credentials_from_oauth_config(shared_oauth_config)

                if client_id and client_secret:
                    oauth_flow_config["clientId"] = client_id
                    oauth_flow_config["clientSecret"] = client_secret
                    self.logger.info(f"Using shared OAuth config for connector {connector_id}")
                    return oauth_flow_config

                self.logger.warning("OAuth config found but missing credentials, falling back to auth config")

        # Fallback to auth config
        client_id = auth_config.get("clientId")
        client_secret = auth_config.get("clientSecret")

        if not client_id or not client_secret:
            raise ValueError(
                f"No OAuth credentials found for connector {connector_id} "
                f"in OAuth config or auth config"
            )

        self.logger.info(f"Using credentials from auth config for connector {connector_id}")

        # Build config from auth config
        oauth_flow_config = self._build_oauth_flow_from_auth_config(
            auth_config,
            {}  # Start with empty config
        )
        oauth_flow_config["clientId"] = client_id
        oauth_flow_config["clientSecret"] = client_secret

        return oauth_flow_config

    # ============================================================================
    # Core Token Refresh Logic
    # ============================================================================

    async def _perform_token_refresh(
        self,
        connector_id: str,
        connector_type: str,
        refresh_token: str
    ) -> OAuthToken:
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
            ValueError: If config or credentials are missing
            Exception: If refresh fails
        """
        # 1. Load connector config
        config_key = f"/services/connectors/{connector_id}/config"
        config = await self.key_value_store.get_key(config_key)

        if not config:
            raise ValueError(f"No config found for connector {connector_id}")

        auth_config = config.get('auth', {})

        # 2. Build complete OAuth configuration
        oauth_flow_config = await self._build_complete_oauth_config(
            connector_id,
            connector_type,
            auth_config
        )

        # 3. Create OAuth config object
        oauth_config = get_oauth_config(oauth_flow_config)

        # 4. Create OAuth provider
        from app.connectors.core.base.token_service.oauth_service import OAuthProvider
        oauth_provider = OAuthProvider(
            config=oauth_config,
            key_value_store=self.key_value_store,
            credentials_path=config_key
        )

        try:
            # 5. Perform the token refresh
            self.logger.info(f"🔄 Refreshing token for connector {connector_id}")
            new_token = await oauth_provider.refresh_access_token(refresh_token)
            self.logger.info(f"✅ Successfully refreshed token for connector {connector_id}")

            # 6. Update stored credentials
            config['credentials'] = new_token.to_dict()
            await self.key_value_store.create_key(config_key, config)
            self.logger.info(f"💾 Updated stored credentials for connector {connector_id}")

            return new_token
        finally:
            # Always clean up OAuth provider
            await oauth_provider.close()

    def _is_connector_being_processed(self, connector_id: str) -> bool:
        """Check if connector is currently being processed."""
        return connector_id in self._processing_connectors

    def _mark_connector_processing(self, connector_id: str) -> None:
        """Mark connector as being processed."""
        self._processing_connectors.add(connector_id)

    def _unmark_connector_processing(self, connector_id: str) -> None:
        """Remove connector from processing set."""
        self._processing_connectors.discard(connector_id)

    async def _load_token_from_config(self, connector_id: str) -> tuple[OAuthToken, bool]:
        """
        Load OAuth token from connector config.

        Returns:
            Tuple of (token, has_credentials)
            - token: OAuthToken if found, None otherwise
            - has_credentials: True if connector has valid credentials
        """
        config_key = f"/services/connectors/{connector_id}/config"
        config = await self.key_value_store.get_key(config_key)

        if not config:
            return None, False

        credentials = config.get('credentials')
        if not credentials or not credentials.get('refresh_token'):
            return None, False

        token = OAuthToken.from_dict(credentials)
        return token, True

    async def _handle_token_refresh_workflow(
        self,
        connector_id: str,
        connector_type: str,
        token: OAuthToken
    ) -> None:
        """
        Handle the token refresh workflow based on token expiry status.

        Args:
            connector_id: Connector ID
            connector_type: Connector type
            token: Current OAuth token
        """
        # Log token status
        expiry_time = None
        if token.expires_in:
            expiry_time = token.created_at + timedelta(seconds=token.expires_in)

        self.logger.debug(
            f"Token for connector {connector_id}: "
            f"expires_in={token.expires_in}s, "
            f"expiry_time={expiry_time}, "
            f"is_expired={token.is_expired}"
        )

        # If token not expired, just schedule refresh
        if not token.is_expired:
            self.logger.info(f"✅ Token not expired for connector {connector_id}, scheduling refresh")
            await self.schedule_token_refresh(connector_id, connector_type, token)
            return

        # Token is expired - refresh it now
        self.logger.info(f"🔄 Token expired for connector {connector_id}, refreshing now")
        new_token = await self._perform_token_refresh(connector_id, connector_type, token.refresh_token)

        # Schedule next refresh for the new token
        await self.schedule_token_refresh(connector_id, connector_type, new_token)

    async def _refresh_connector_token(self, connector_id: str, connector_type: str) -> None:
        """
        Check token status and refresh if needed, then schedule next refresh.
        This method orchestrates the token refresh workflow.

        Args:
            connector_id: Connector ID
            connector_type: Connector type
        """
        # Prevent recursion
        if self._is_connector_being_processed(connector_id):
            self.logger.warning(f"⚠️ Already processing connector {connector_id}, skipping to prevent recursion")
            return

        self._mark_connector_processing(connector_id)

        try:
            # Load token from config
            token, has_credentials = await self._load_token_from_config(connector_id)

            if not has_credentials:
                self.logger.debug(f"Connector {connector_id} has no credentials to refresh")
                return

            # Handle refresh workflow
            await self._handle_token_refresh_workflow(connector_id, connector_type, token)

        except RecursionError as e:
            # Special handling for recursion errors
            print(f"RECURSION ERROR in token refresh for {connector_id}: {str(e)[:100]}", flush=True)
        except Exception as e:
            # Use exc_info=False to avoid potential recursion in traceback formatting
            self.logger.error(f"❌ Error refreshing token for connector {connector_id}: {e}", exc_info=False)
        finally:
            # Always remove from processing set
            self._unmark_connector_processing(connector_id)

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

    def _calculate_refresh_delay(self, token: OAuthToken) -> tuple[float, datetime]:
        """
        Calculate delay until token refresh (10 minutes before expiry).

        Returns:
            Tuple of (delay_seconds, refresh_time)
        """
        refresh_time = token.created_at + timedelta(seconds=max(0, token.expires_in - 600))
        delay = (refresh_time - datetime.now()).total_seconds()
        return delay, refresh_time

    async def _refresh_token_immediately(
        self,
        connector_id: str,
        connector_type: str,
        token: OAuthToken
    ) -> tuple[OAuthToken, bool]:
        """
        Perform immediate token refresh.

        Returns:
            Tuple of (new_token, success)
        """
        try:
            new_token = await self._perform_token_refresh(connector_id, connector_type, token.refresh_token)
            self.logger.info(f"🔄 Immediate refresh completed for connector {connector_id}")
            return new_token, True
        except Exception as e:
            self.logger.error(f"❌ Failed to perform immediate refresh for connector {connector_id}: {e}", exc_info=False)
            return None, False

    def _cancel_existing_refresh_task(self, connector_id: str) -> None:
        """Cancel existing refresh task for connector if one exists."""
        if connector_id not in self._refresh_tasks:
            return

        old_task = self._refresh_tasks[connector_id]

        if old_task.done():
            del self._refresh_tasks[connector_id]
            self.logger.debug(f"Removed completed/cancelled task for connector {connector_id}")
        else:
            try:
                old_task.cancel()
                self.logger.debug(f"Cancelled existing refresh task for connector {connector_id} to reschedule")
            except Exception as e:
                self.logger.warning(f"Error cancelling existing task for connector {connector_id}: {e}")

    def _create_refresh_task(
        self,
        connector_id: str,
        connector_type: str,
        delay: float,
        refresh_time: datetime
    ) -> bool:
        """
        Create and store a new refresh task.

        Returns:
            True if task created successfully, False otherwise
        """
        try:
            task = asyncio.create_task(
                self._delayed_refresh(connector_id, connector_type, delay)
            )
            self._refresh_tasks[connector_id] = task
            self.logger.info(
                f"✅ Scheduled token refresh for connector {connector_id} in {delay:.0f} seconds "
                f"({delay/60:.1f} minutes) - will refresh at {refresh_time}"
            )
            return True
        except Exception as e:
            self.logger.error(f"❌ Failed to schedule token refresh for connector {connector_id}: {e}", exc_info=True)
            return False

    async def schedule_token_refresh(
        self,
        connector_id: str,
        connector_type: str,
        token: OAuthToken
    ) -> None:
        """
        Schedule token refresh for a specific connector.
        If the token needs immediate refresh (delay <= 0), refreshes it immediately then schedules.

        Args:
            connector_id: Connector ID
            connector_type: Connector type
            token: Current OAuth token
        """
        if not self._running:
            self.logger.warning(f"⚠️ Token refresh service not running, scheduling anyway for {connector_id}")

        if not token.expires_in:
            self.logger.warning(f"⚠️ Token for connector {connector_id} has no expiry time, cannot schedule refresh")
            return

        self.logger.info(f"🔄 Scheduling token refresh for connector {connector_id} (type: {connector_type})")

        # Calculate refresh delay
        delay, refresh_time = self._calculate_refresh_delay(token)

        # Handle immediate refresh if needed
        if delay <= 0:
            self.logger.warning(
                f"⚠️ Token for connector {connector_id} needs immediate refresh "
                f"(expires_in={token.expires_in}s, delay={delay:.1f}s). Refreshing now..."
            )

            new_token, success = await self._refresh_token_immediately(connector_id, connector_type, token)

            if not success:
                return

            # Recalculate delay with new token
            delay, refresh_time = self._calculate_refresh_delay(new_token)

            if delay <= 0:
                self.logger.error(
                    f"❌ New token for connector {connector_id} is also expired/expiring soon! "
                    f"(expires_in={new_token.expires_in}s, delay={delay:.1f}s). "
                    f"Cannot schedule refresh - will be picked up by periodic check."
                )
                return

            token = new_token
            self.logger.info(f"🔄 Scheduling next refresh for connector {connector_id} with new token")

        # Cancel any existing task
        self._cancel_existing_refresh_task(connector_id)

        # Create new refresh task
        self._create_refresh_task(connector_id, connector_type, delay, refresh_time)

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
