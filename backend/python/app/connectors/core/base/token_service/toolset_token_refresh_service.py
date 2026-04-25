"""
Toolset Token Refresh Service
Handles automatic token refresh for OAuth toolsets
Separate from connector token refresh to avoid interference
"""

import asyncio
import logging
import time
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from app.connectors.core.base.token_service.oauth_service import OAuthConfig

from app.config.configuration_service import ConfigurationService
from app.connectors.core.base.token_service.oauth_service import OAuthToken
from app.utils.oauth_config import get_oauth_config

# Constants
MIN_PATH_PARTS_COUNT = 4  # Minimum path parts: services, toolsets, instance_id, user_id
LOCK_TIMEOUT = 30  # Timeout for acquiring toolset lock (seconds)
TOKEN_REFRESH_MAX_RETRIES = 3  # Max retries for config write verification failures
INITIAL_RETRY_DELAY = 0.3  # Initial retry delay for verification failures (seconds) - increased to allow verification to complete
REFRESH_COOLDOWN = 10  # Minimum seconds between refreshes of same toolset (prevents rapid duplicates)
MIN_IMMEDIATE_RECHECK_DELAY = 1  # Seconds; used when an already-expired token needs refresh immediately
PROACTIVE_REFRESH_WINDOW_SECONDS = 600  # Refresh 10 minutes before expiry for normal tokens
MIN_SHORT_LIVED_REFRESH_WINDOW_SECONDS = 60  # Minimum proactive window for short-lived tokens
SHORT_LIVED_TOKEN_BUFFER_RATIO = 0.2  # For short-lived tokens, refresh ~20% before expiry


class ToolsetTokenRefreshService:
    """Service for managing token refresh across all OAuth toolsets"""

    def __init__(self, configuration_service: ConfigurationService) -> None:
        self.configuration_service = configuration_service
        self.logger = logging.getLogger("connector_service")
        self._refresh_tasks: dict[str, asyncio.Task] = {}
        self._running = False
        self._refresh_lock = asyncio.Lock()  # Prevent concurrent refresh operations
        self._processing_toolsets: set = set()  # Track toolsets currently being processed to prevent recursion
        self._toolset_locks: dict[str, asyncio.Lock] = {}  # Per-toolset locks to prevent concurrent refreshes
        self._schedule_locks: dict[str, asyncio.Lock] = {}  # Per-toolset locks for atomic task scheduling
        self._last_refresh_time: dict[str, float] = {}  # Track last successful refresh time (prevents duplicates)

    def _get_toolset_lock(self, config_path: str) -> asyncio.Lock:
        """
        Get or create a lock for the specified toolset.
        Thread-safe lock creation using dictionary get with default.

        Args:
            config_path: Toolset config path

        Returns:
            asyncio.Lock for this toolset
        """
        if config_path not in self._toolset_locks:
            self._toolset_locks[config_path] = asyncio.Lock()
        return self._toolset_locks[config_path]

    def _get_schedule_lock(self, config_path: str) -> asyncio.Lock:
        """
        Get or create a lock for scheduling refresh tasks for a specific toolset.
        This makes "check existing task + create task" atomic per config path.
        """
        if config_path not in self._schedule_locks:
            self._schedule_locks[config_path] = asyncio.Lock()
        return self._schedule_locks[config_path]

    async def start(self) -> None:
        """Start the toolset token refresh service"""
        if self._running:
            return

        self._running = True
        self.logger.info("Starting toolset token refresh service")

        # Start refresh tasks for all authenticated toolsets
        await self._refresh_all_tokens()

        # Start periodic refresh check
        asyncio.create_task(self._periodic_refresh_check())

        # Start periodic lock cleanup
        asyncio.create_task(self._cleanup_old_locks())

    async def stop(self) -> None:
        """Stop the toolset token refresh service"""
        self._running = False

        # Cancel all refresh tasks
        for task in self._refresh_tasks.values():
            task.cancel()

        self._refresh_tasks.clear()
        self.logger.info("Toolset token refresh service stopped")

    async def _cleanup_old_locks(self) -> None:
        """
        Periodically clean up locks for toolsets that no longer exist or are inactive.
        Prevents unbounded memory growth of the lock dictionary.
        """
        while self._running:
            try:
                await asyncio.sleep(3600)  # Run every hour

                # Get list of current toolset paths
                try:
                    current_paths = set(await self.configuration_service.list_keys_in_directory("/services/toolsets/"))
                except Exception as e:
                    self.logger.warning(f"Could not list toolsets for lock cleanup: {e}")
                    continue

                # Find locks for paths that no longer exist
                stale_locks = [path for path in self._toolset_locks if path not in current_paths]

                if stale_locks:
                    self.logger.info(f"🧹 Cleaning up {len(stale_locks)} stale toolset locks")
                    for path in stale_locks:
                        # Only remove if lock is not currently held
                        lock = self._toolset_locks.get(path)
                        if lock and not lock.locked():
                            del self._toolset_locks[path]
                            self.logger.debug(f"Removed stale lock for {path}")

                # Clean up stale scheduling locks
                stale_schedule_locks = [path for path in self._schedule_locks if path not in current_paths]
                if stale_schedule_locks:
                    self.logger.info(f"🧹 Cleaning up {len(stale_schedule_locks)} stale schedule locks")
                    for path in stale_schedule_locks:
                        lock = self._schedule_locks.get(path)
                        if lock and not lock.locked():
                            del self._schedule_locks[path]
                            self.logger.debug(f"Removed stale schedule lock for {path}")

                # Clean up refresh timestamps for removed paths to avoid unbounded growth.
                for path in list(self._last_refresh_time.keys()):
                    if path not in current_paths:
                        del self._last_refresh_time[path]

            except asyncio.CancelledError:
                break
            except Exception as e:
                self.logger.error(f"Error in lock cleanup: {e}", exc_info=True)

    async def _refresh_all_tokens(self) -> None:
        """Refresh tokens for all authenticated toolsets"""
        # Prevent concurrent execution
        async with self._refresh_lock:
            await self._refresh_all_tokens_internal()

    async def _is_toolset_authenticated(self, config_path: str) -> bool:
        """
        Check if toolset has valid OAuth credentials stored.

        Args:
            config_path: Toolset config path (e.g., /services/toolsets/{instanceId}/{userId})

        Returns:
            True if toolset has refresh_token, False otherwise
        """
        try:
            config = await self.configuration_service.get_config(config_path)

            if not config:
                self.logger.debug(f"⚠️ No config found for toolset: {config_path}")
                return False

            # Check authentication flag
            is_authenticated = config.get("isAuthenticated", False)
            if not is_authenticated:
                self.logger.debug(f"⚠️ Toolset not authenticated (isAuthenticated=False): {config_path}")
                return False

            # Check auth type - only OAuth toolsets need token refresh
            # If auth type is not set, check if it has OAuth credentials (likely OAuth)
            auth_config = config.get("auth", {})
            auth_type = auth_config.get("type")

            # If auth type is explicitly API_TOKEN, skip (no token refresh needed)
            if auth_type == "API_TOKEN":
                self.logger.debug(f"⚠️ Toolset is API_TOKEN type (no refresh needed): {config_path}")
                return False

            # If auth type is OAUTH or not set (but has refresh_token), proceed
            # Many toolsets might not have explicit auth.type set but still use OAuth
            if auth_type and auth_type != "OAUTH":
                self.logger.debug(f"⚠️ Toolset has unknown auth type (type={auth_type}): {config_path}")
                return False

            credentials = config.get("credentials")
            if not credentials:
                self.logger.debug(f"⚠️ No credentials found for toolset: {config_path}")
                return False

            has_refresh_token = bool(credentials.get("refresh_token"))
            if not has_refresh_token:
                self.logger.debug(f"⚠️ No refresh_token in credentials for toolset: {config_path}")
                return False

            return True

        except Exception as e:
            self.logger.debug(f"Could not check credentials for toolset {config_path}: {e}")
            return False

    async def _refresh_all_tokens_internal(self) -> None:
        """Internal method to refresh tokens for all authenticated toolsets"""
        try:
            # Get all toolset config paths from etcd
            # NEW ARCHITECTURE: Toolsets are stored at /services/toolsets/{instanceId}/{userId}
            # LEGACY: Old format was /services/toolsets/{userId}/{toolset_type}
            try:
                all_keys = await self.configuration_service.list_keys_in_directory("/services/toolsets/")
                self.logger.info(f"🔍 Found {len(all_keys)} toolset keys in etcd (scanning /services/toolsets/)")

                if all_keys:
                    self.logger.debug(f"📋 Sample keys found (first 5): {all_keys[:5]}")
                    for key in all_keys[:5]:
                        path_parts = key.strip("/").split("/")
                        self.logger.debug(f"   - Key: {key} -> Parts: {path_parts} (count: {len(path_parts)})")

                # Use dict instead of set for better tracking and deduplication
                processed_toolsets = {}
                skipped_counts = {
                    "not_authenticated": 0,
                    "no_config": 0,
                    "invalid_path": 0,
                    "duplicate": 0
                }

                for config_path in all_keys:
                    try:
                        # Skip if already processed (key exists in dict)
                        if config_path in processed_toolsets:
                            self.logger.debug(f"⏭️ Skipping duplicate key: {config_path}")
                            skipped_counts["duplicate"] += 1
                            continue

                        # Mark as seen IMMEDIATELY to prevent processing duplicates
                        # even if validation fails
                        processed_toolsets[config_path] = None

                        # Extract instanceId and userId from path
                        # Format (new):    /services/toolsets/{instanceId}/{userId}
                        # Format (legacy): /services/toolsets/{userId}/{toolset_type}
                        path_parts = config_path.strip("/").split("/")
                        if len(path_parts) < MIN_PATH_PARTS_COUNT:
                            self.logger.info(f"⚠️ Skipping invalid path format: {config_path} (parts: {path_parts}, count: {len(path_parts)}, expected: 4)")
                            skipped_counts["invalid_path"] += 1
                            continue

                        # NEW ARCHITECTURE: /services/toolsets/{instanceId}/{userId}
                        # path_parts[2] = instanceId (UUID)
                        # path_parts[3] = userId
                        instance_id_or_type = path_parts[2]
                        user_id = path_parts[3]

                        # Check if path_parts[2] is a UUID (new format) or toolset type (legacy)
                        # New format: instanceId is a UUID (e.g., "107344f6-66cb-46f9-89f1-22d0bdae99cb")
                        # Legacy format: would be toolset type (e.g., "slack", "jira")
                        try:
                            import uuid
                            # Try to parse as UUID - if it works, it's new format
                            uuid.UUID(instance_id_or_type)
                        except (ValueError, AttributeError):
                            # Not a UUID, so it's old/legacy format
                            # Skip legacy formats during migration
                            self.logger.debug(
                                f"⏭️ Skipping legacy format path (will be migrated): {config_path}. "
                                f"Expected instanceId (UUID) at path_parts[2], got: {instance_id_or_type}"
                            )
                            skipped_counts["invalid_path"] += 1
                            continue

                        # At this point, we have confirmed new format with instanceId
                        instance_id = instance_id_or_type

                        # Get config to check authentication
                        config = await self.configuration_service.get_config(config_path)
                        if not config:
                            self.logger.debug(f"⚠️ No config found for toolset: {config_path}")
                            skipped_counts["no_config"] += 1
                            continue

                        # Get toolsetType from stored config (required in new architecture)
                        toolset_type = config.get("toolsetType")
                        if not toolset_type:
                            self.logger.warning(
                                f"⚠️ No toolsetType in config for {config_path}. "
                                f"This is required for new architecture. Skipping."
                            )
                            skipped_counts["no_config"] += 1
                            continue

                        # Check if toolset is authenticated and has OAuth
                        if not await self._is_toolset_authenticated(config_path):
                            self.logger.debug(
                                f"⚠️ Toolset not authenticated or missing refresh_token: {config_path} "
                                f"(instance: {instance_id}, user: {user_id}, type: {toolset_type})"
                            )
                            skipped_counts["not_authenticated"] += 1
                            continue

                        # After successful authentication check, update with metadata
                        toolset_info = {
                            "instanceId": instance_id,
                            "userId": user_id,
                            "toolsetType": toolset_type
                        }
                        processed_toolsets[config_path] = toolset_info

                        self.logger.info(
                            f"✅ Found authenticated OAuth toolset: {config_path} "
                            f"(instance: {instance_id}, user: {user_id}, type: {toolset_type})"
                        )

                        # Process this toolset for refresh
                        try:
                            await self._refresh_toolset_token(config_path, toolset_type)
                        except Exception as e:
                            self.logger.error(f"Failed to process toolset {config_path}: {e}", exc_info=False)

                    except Exception as e:
                        self.logger.warning(f"Error processing toolset config {config_path}: {e}")
                        continue

                self.logger.info(
                    f"📊 Toolset scan summary: "
                    f"{len([v for v in processed_toolsets.values() if v])} authenticated OAuth toolsets processed, "
                    f"{skipped_counts['duplicate']} duplicates skipped, "
                    f"{skipped_counts['not_authenticated']} not authenticated, "
                    f"{skipped_counts['no_config']} no config, "
                    f"{skipped_counts['invalid_path']} invalid path format"
                )

            except Exception as e:
                self.logger.error(f"Error listing toolset keys from etcd: {e}", exc_info=True)

        except Exception as e:
            self.logger.error(f"❌ Error refreshing toolset tokens: {e}", exc_info=True)

    # ============================================================================
    # Helper Methods for OAuth Config Building
    # ============================================================================

    async def _load_admin_oauth_config(
        self,
        config_path: str,
        toolset_type: str
    ) -> dict[str, any] | None:
        """
        Load admin-created OAuth config for a toolset instance.

        For the new instance-based architecture, the user auth record stores
        `oauthConfigId` (and `orgId`) so we can look up the admin OAuth config at
        /services/oauths/toolsets/{toolsetType}.

        Args:
            config_path: User auth path (/services/toolsets/{instanceId}/{userId})
            toolset_type: Toolset type (e.g. "googledrive")

        Returns:
            Admin OAuth config dict, or None if not found.
        """
        try:
            user_config = await self.configuration_service.get_config(config_path)
            if not user_config:
                return None

            oauth_config_id = user_config.get("oauthConfigId")
            org_id = user_config.get("orgId")
            if not oauth_config_id or not org_id:
                return None

            admin_path = f"/services/oauths/toolsets/{toolset_type.lower()}"
            configs = await self.configuration_service.get_config(admin_path, default=[])
            if not isinstance(configs, list):
                return None

            for cfg in configs:
                if cfg.get("_id") == oauth_config_id and cfg.get("orgId") == org_id:
                    return cfg

            return None
        except Exception as e:
            self.logger.debug(f"Could not load admin OAuth config: {e}")
            return None

    def _get_toolset_oauth_config_from_registry(
        self,
        toolset_type: str
    ) -> Optional['OAuthConfig']:
        """
        Get OAuth config from toolset registry.

        Args:
            toolset_type: Toolset type (lowercase, e.g., "googledrive")

        Returns:
            OAuthConfig object if found, None otherwise
        """
        try:
            from app.agents.registry.toolset_registry import get_toolset_registry

            toolset_registry = get_toolset_registry()
            if not toolset_registry:
                return None

            # Get toolset metadata by type (lowercase)
            metadata = toolset_registry.get_toolset_metadata(toolset_type)
            if not metadata:
                return None

            # Get the config from metadata
            config = metadata.get("config", {})
            oauth_configs = config.get("_oauth_configs", {})

            # Get the OAUTH config (stored during toolset build)
            if "OAUTH" not in oauth_configs:
                return None

            oauth_config = oauth_configs["OAUTH"]

            # Validate it's an OAuthConfig object
            if not hasattr(oauth_config, 'authorize_url') or not hasattr(oauth_config, 'redirect_uri'):
                return None

            return oauth_config

        except Exception as e:
            self.logger.debug(f"Could not get OAuth config from toolset registry for {toolset_type}: {e}")
            return None

    def _enrich_from_toolset_registry(
        self,
        oauth_flow_config: dict[str, any],
        toolset_type: str
    ) -> None:
        """
        Enrich OAuth config with missing infrastructure fields from toolset registry.
        Modifies oauth_flow_config in-place.
        """
        # Check if enrichment is needed
        if "tokenAccessType" in oauth_flow_config and "additionalParams" in oauth_flow_config:
            return

        try:
            # Get OAuth config from toolset registry
            oauth_config_obj = self._get_toolset_oauth_config_from_registry(toolset_type)

            if oauth_config_obj:
                # Add missing optional fields from registry
                if "tokenAccessType" not in oauth_flow_config:
                    token_access_type = getattr(oauth_config_obj, 'token_access_type', None)
                    if token_access_type:
                        oauth_flow_config["tokenAccessType"] = token_access_type

                if "additionalParams" not in oauth_flow_config:
                    additional_params = getattr(oauth_config_obj, 'additional_params', None)
                    if additional_params:
                        oauth_flow_config["additionalParams"] = additional_params

                # Add scope_parameter_name if not already set and different from default
                if "scopeParameterName" not in oauth_flow_config:
                    scope_param_name = getattr(oauth_config_obj, 'scope_parameter_name', None)
                    if scope_param_name and scope_param_name != "scope":
                        oauth_flow_config["scopeParameterName"] = scope_param_name

                # Add token_response_path if not already set
                if "tokenResponsePath" not in oauth_flow_config:
                    token_response_path = getattr(oauth_config_obj, 'token_response_path', None)
                    if token_response_path:
                        oauth_flow_config["tokenResponsePath"] = token_response_path

                self.logger.debug(f"Enriched OAuth config from registry for {toolset_type}")

        except Exception as e:
            self.logger.debug(f"Could not enrich OAuth config from registry: {e}")

    async def _build_complete_oauth_config(
        self,
        config_path: str,
        toolset_type: str,
        auth_config: dict[str, any]
    ) -> dict[str, any]:
        """
        Build complete OAuth flow configuration for toolset.

        NEW ARCHITECTURE (2026-03+): OAuth credentials (clientId, clientSecret, etc.)
        are stored centrally and fetched using get_oauth_credentials_for_toolset helper.

        Args:
            config_path: Toolset config path (/services/toolsets/{instanceId}/{userId})
            toolset_type: Toolset type
            auth_config: Auth configuration from toolset config (may contain oauthConfigId)

        Returns:
            Complete OAuth flow config with clientId, clientSecret, and all infrastructure fields

        Raises:
            ValueError: If credentials cannot be found
        """
        # Load the full user toolset config to get oauthConfigId and other metadata
        try:
            full_user_config = await self.configuration_service.get_config(
                config_path,
                default=None,
                use_cache=False
            )

            if not full_user_config or not isinstance(full_user_config, dict):
                raise ValueError(f"Could not load toolset config from {config_path}")

            # Use the new centralized OAuth credential fetching
            from app.api.routes.toolsets import get_oauth_credentials_for_toolset

            oauth_creds = await get_oauth_credentials_for_toolset(
                toolset_config=full_user_config,
                config_service=self.configuration_service,
                logger=self.logger
            )

            # oauth_creds now contains ALL OAuth config fields dynamically
            # (clientId, clientSecret, tenantId, domain, scopes, URLs, etc.)
            client_id = oauth_creds.get("clientId")
            client_secret = oauth_creds.get("clientSecret")

            if not client_id or not client_secret:
                raise ValueError(
                    f"OAuth credentials incomplete for {config_path}. "
                    f"Available fields: {list(oauth_creds.keys())}"
                )

            # Merge OAuth credentials into auth_config (preserves all fields)
            # Priority: oauth_creds (central config) > auth_config (user overrides)
            for key, value in oauth_creds.items():
                if key not in auth_config:  # Don't overwrite user overrides
                    auth_config[key] = value

            self.logger.debug(
                f"✅ Fetched OAuth credentials for token refresh from centralized config. "
                f"Fields: {list(oauth_creds.keys())}"
            )

        except Exception as e:
            self.logger.error(
                f"Failed to fetch OAuth credentials for {config_path}: {e}. "
                f"Falling back to legacy auth_config (if available).",
                exc_info=True
            )
            # Fallback to legacy: credentials directly in auth_config
            client_id = auth_config.get("clientId")
            client_secret = auth_config.get("clientSecret")

            if not client_id or not client_secret:
                raise ValueError(
                    f"No OAuth credentials found for toolset {config_path}. "
                    f"New architecture fetch failed AND legacy credentials not in auth_config."
                )

        # Try to get OAuth config from registry for fallback URLs and scopes
        oauth_config_obj = self._get_toolset_oauth_config_from_registry(toolset_type)

        # Build OAuth flow config - auth_config now has all fields from oauth_creds merged
        oauth_flow_config = {
            "clientId": client_id,
            "clientSecret": client_secret,
        }

        # Get URLs - prefer auth_config (which now includes oauth_creds), fallback to registry
        if auth_config.get("authorizeUrl"):
            oauth_flow_config["authorizeUrl"] = auth_config["authorizeUrl"]
        elif oauth_config_obj and hasattr(oauth_config_obj, 'authorize_url'):
            oauth_flow_config["authorizeUrl"] = oauth_config_obj.authorize_url
        else:
            oauth_flow_config["authorizeUrl"] = ""

        if auth_config.get("tokenUrl"):
            oauth_flow_config["tokenUrl"] = auth_config["tokenUrl"]
        elif oauth_config_obj and hasattr(oauth_config_obj, 'token_url'):
            oauth_flow_config["tokenUrl"] = oauth_config_obj.token_url
        else:
            oauth_flow_config["tokenUrl"] = ""

        # Redirect URI - prefer auth_config value (full URL), fallback to registry
        if auth_config.get("redirectUri"):
            oauth_flow_config["redirectUri"] = auth_config["redirectUri"]
        elif oauth_config_obj and hasattr(oauth_config_obj, 'redirect_uri'):
            # Registry redirect_uri is a path, try to construct full URL
            redirect_path = oauth_config_obj.redirect_uri
            try:
                endpoints = await self.configuration_service.get_config("/services/endpoints")
                if isinstance(endpoints, dict):
                    fallback_url = endpoints.get("frontend", {}).get("publicEndpoint", "http://localhost:3001")
                    oauth_flow_config["redirectUri"] = f"{fallback_url.rstrip('/')}/{redirect_path}"
                else:
                    oauth_flow_config["redirectUri"] = f"http://localhost:3001/{redirect_path}"
            except Exception:
                oauth_flow_config["redirectUri"] = redirect_path
        else:
            oauth_flow_config["redirectUri"] = ""

        # Get scopes - prefer auth_config (which includes oauth_creds), fallback to registry
        user_scopes = auth_config.get("scopes", [])
        if isinstance(user_scopes, list) and len(user_scopes) > 0:
            oauth_flow_config["scopes"] = user_scopes
        elif oauth_config_obj and hasattr(oauth_config_obj, 'scopes'):
            from app.connectors.core.registry.auth_builder import OAuthScopeType
            registry_scopes = oauth_config_obj.scopes.get_scopes_for_type(OAuthScopeType.AGENT)
            oauth_flow_config["scopes"] = registry_scopes if registry_scopes else []
        else:
            oauth_flow_config["scopes"] = []

        # Add optional fields - all available from auth_config now (includes oauth_creds)
        if "tokenAccessType" in auth_config:
            oauth_flow_config["tokenAccessType"] = auth_config["tokenAccessType"]
        elif oauth_config_obj and hasattr(oauth_config_obj, 'token_access_type') and oauth_config_obj.token_access_type:
            oauth_flow_config["tokenAccessType"] = oauth_config_obj.token_access_type

        if "additionalParams" in auth_config:
            oauth_flow_config["additionalParams"] = auth_config["additionalParams"]
        elif oauth_config_obj and hasattr(oauth_config_obj, 'additional_params') and oauth_config_obj.additional_params:
            oauth_flow_config["additionalParams"] = oauth_config_obj.additional_params

        if "scopeParameterName" in auth_config:
            oauth_flow_config["scopeParameterName"] = auth_config["scopeParameterName"]
        elif oauth_config_obj and hasattr(oauth_config_obj, 'scope_parameter_name') and oauth_config_obj.scope_parameter_name != "scope":
            oauth_flow_config["scopeParameterName"] = oauth_config_obj.scope_parameter_name

        if "tokenResponsePath" in auth_config:
            oauth_flow_config["tokenResponsePath"] = auth_config["tokenResponsePath"]
        elif oauth_config_obj and hasattr(oauth_config_obj, 'token_response_path') and oauth_config_obj.token_response_path:
            oauth_flow_config["tokenResponsePath"] = oauth_config_obj.token_response_path

        # Add provider-specific fields (tenantId for Microsoft, domain for Slack, etc.)
        # These are now available in auth_config from oauth_creds
        for field_name in ["tenantId", "domain", "workspace", "companyUrl", "baseUrl"]:
            if field_name in auth_config:
                oauth_flow_config[field_name] = auth_config[field_name]

        # Enrich from registry if fields are still missing
        self._enrich_from_toolset_registry(oauth_flow_config, toolset_type)

        return oauth_flow_config

    # ============================================================================
    # Core Token Refresh Logic
    # ============================================================================

    async def _perform_token_refresh(
        self,
        config_path: str,
        toolset_type: str,
        refresh_token: str
    ) -> OAuthToken:
        """
        Core token refresh logic - performs the actual OAuth token refresh for toolsets.

        Args:
            config_path: Toolset config path (e.g., /services/toolsets/{instanceId}/{userId})
            toolset_type: The toolset type
            refresh_token: The refresh token to use

        Returns:
            The new OAuthToken after refresh
        Raises:
            ValueError: If config or credentials are missing
            Exception: If refresh fails
        """
        # 1. Load toolset config
        config = await self.configuration_service.get_config(config_path)

        if not config:
            raise ValueError(f"No config found for toolset {config_path}")

        auth_config = config.get("auth", {})

        # Verify OAuth auth type (lenient check)
        # If toolset has refresh_token, we assume it's OAuth even if type is not set
        # This handles legacy configs and configs created before type field was enforced
        auth_type = auth_config.get("type", "").upper()
        if auth_type and auth_type != "OAUTH":
            # Only fail if type is explicitly set to non-OAUTH (e.g., "API_TOKEN")
            raise ValueError(
                f"Toolset {config_path} is configured for {auth_type}, not OAuth. "
                f"Token refresh only works for OAuth toolsets."
            )

        # If type is not set (empty string or None), we trust _is_toolset_authenticated
        # which already verified the toolset has valid OAuth credentials (refresh_token)
        if not auth_type:
            self.logger.debug(
                f"⚠️ Toolset {config_path} has no auth.type set, but has refresh_token. "
                f"Assuming OAuth (legacy config)."
            )

        # 2. Build complete OAuth configuration
        oauth_flow_config = await self._build_complete_oauth_config(
            config_path,
            toolset_type,
            auth_config
        )

        # Validate required OAuth fields
        if not oauth_flow_config.get("tokenUrl"):
            raise ValueError(
                f"Missing tokenUrl in OAuth config for toolset {config_path}. "
                f"Required for token refresh."
            )
        if not oauth_flow_config.get("clientId"):
            raise ValueError(
                f"Missing clientId in OAuth config for toolset {config_path}."
            )
        if not oauth_flow_config.get("clientSecret"):
            raise ValueError(
                f"Missing clientSecret in OAuth config for toolset {config_path}."
            )

        # 3. Create OAuth config object
        oauth_config = get_oauth_config(oauth_flow_config)

        # 4. Create OAuth provider
        from app.connectors.core.base.token_service.oauth_service import OAuthProvider
        oauth_provider = OAuthProvider(
            config=oauth_config,
            configuration_service=self.configuration_service,
            credentials_path=config_path
        )

        try:
            # 5. Perform the token refresh
            self.logger.info(f"🔄 Refreshing token for toolset {config_path} (type: {toolset_type})")
            new_token = await oauth_provider.refresh_access_token(refresh_token)
            self.logger.info(f"✅ Successfully refreshed token for toolset {config_path}")

            # 6. Update stored credentials with retry on verification failure
            config["credentials"] = new_token.to_dict()
            config["updatedAt"] = int(datetime.now().timestamp() * 1000)  # Epoch timestamp in ms

            # Retry logic to handle transient verification failures from race conditions
            max_retries = TOKEN_REFRESH_MAX_RETRIES
            retry_delay = INITIAL_RETRY_DELAY
            last_error = None

            for attempt in range(max_retries):
                # Add pre-write delay on retries to ensure previous write/verification completes
                if attempt > 0:
                    pre_write_delay = 0.1  # 100ms delay before retry
                    self.logger.debug(
                        f"⏸️ Waiting {pre_write_delay}s before retry attempt {attempt + 1} "
                        f"for toolset {config_path} (allows previous verification to complete)"
                    )
                    await asyncio.sleep(pre_write_delay)

                try:
                    success = await self.configuration_service.set_config(config_path, config)

                    if success:
                        if attempt > 0:
                            self.logger.info(
                                f"💾 Updated stored credentials for toolset {config_path} "
                                f"on attempt {attempt + 1}/{max_retries}"
                            )
                        else:
                            self.logger.info(f"💾 Updated stored credentials for toolset {config_path}")

                        # Add small delay after successful write to ensure verification completes
                        # This prevents interference if another operation starts immediately
                        await asyncio.sleep(0.05)
                        break  # Success!

                    # set_config returned False (verification failed)
                    last_error = "Verification failed"
                    if attempt < max_retries - 1:
                        self.logger.warning(
                            f"⚠️ Verification failed for toolset {config_path} "
                            f"(attempt {attempt + 1}/{max_retries}). "
                            f"Will wait {retry_delay}s before next attempt..."
                        )
                        await asyncio.sleep(retry_delay)
                        retry_delay *= 2  # Exponential backoff

                except Exception as e:
                    last_error = str(e)
                    self.logger.warning(
                        f"⚠️ Error saving credentials for toolset {config_path} "
                        f"(attempt {attempt + 1}/{max_retries}): {e}"
                    )
                    if attempt < max_retries - 1:
                        await asyncio.sleep(retry_delay)
                        retry_delay *= 2
            else:
                # All retries exhausted
                raise Exception(
                    f"Failed to save refreshed credentials for {config_path} "
                    f"after {max_retries} attempts. Last error: {last_error}"
                )

            # Update last refresh time to prevent duplicate refreshes
            self._last_refresh_time[config_path] = time.time()
            self.logger.debug(f"📝 Updated last refresh time for {config_path}")

            return new_token
        finally:
            # Always clean up OAuth provider
            await oauth_provider.close()

    def _is_toolset_being_processed(self, config_path: str) -> bool:
        """Check if toolset is currently being processed."""
        return config_path in self._processing_toolsets

    def _mark_toolset_processing(self, config_path: str) -> None:
        """Mark toolset as being processed."""
        self._processing_toolsets.add(config_path)

    def _unmark_toolset_processing(self, config_path: str) -> None:
        """Remove toolset from processing set."""
        self._processing_toolsets.discard(config_path)

    async def _load_token_from_config(self, config_path: str) -> tuple[OAuthToken | None, bool]:
        """
        Load OAuth token from toolset config.

        Args:
            config_path: Toolset config path

        Returns:
            Tuple of (token, has_credentials)
            - token: OAuthToken if found, None otherwise
            - has_credentials: True if toolset has valid credentials
        """
        config = await self.configuration_service.get_config(config_path)

        if not config:
            return None, False

        # Check authentication flag
        is_authenticated = config.get("isAuthenticated", False)
        if not is_authenticated:
            return None, False

        credentials = config.get("credentials")
        if not credentials or not credentials.get("refresh_token"):
            return None, False

        token = OAuthToken.from_dict(credentials)
        return token, True

    async def _handle_token_refresh_workflow(
        self,
        config_path: str,
        toolset_type: str,
        token: OAuthToken
    ) -> None:
        """
        Handle the token refresh workflow based on token expiry status.

        Args:
            config_path: Toolset config path
            toolset_type: Toolset type
            token: Current OAuth token
        """
        # Log token status
        expiry_time = None
        if token.expires_in:
            expiry_time = token.created_at + timedelta(seconds=token.expires_in)

        self.logger.debug(
            f"Token for toolset {config_path}: "
            f"expires_in={token.expires_in}s, "
            f"expiry_time={expiry_time}, "
            f"is_expired={token.is_expired}"
        )

        # Proactive refresh: once token enters refresh window (delay <= 0), refresh now.
        # This ensures refresh happens at/before threshold, not only after actual expiry.
        delay, refresh_time = self._calculate_refresh_delay(token)
        if delay <= 0:
            self.logger.info(
                f"🔄 Token reached refresh threshold for {config_path}; refreshing now "
                f"(refresh_time={refresh_time}, now={datetime.now()})"
            )
            new_token = await self._perform_token_refresh(config_path, toolset_type, token.refresh_token)
            await self.schedule_token_refresh(config_path, toolset_type, new_token)
            return

        # Token is still outside the refresh window, schedule refresh task.
        self.logger.info(
            f"✅ Token not in refresh window for toolset {config_path}, scheduling refresh "
            f"(delay={delay:.1f}s, refresh_time={refresh_time})"
        )
        await self.schedule_token_refresh(config_path, toolset_type, token)

    # TODO: Migrate to 3.12
    async def _refresh_toolset_token(self, config_path: str, toolset_type: str) -> None:
        """
        Check token status and refresh if needed, then schedule next refresh.
        Uses per-toolset lock to prevent concurrent refreshes of the same toolset.

        Args:
            config_path: Toolset config path
            toolset_type: Toolset type
        """
        # Get the lock for this specific toolset
        toolset_lock = self._get_toolset_lock(config_path)

        # Try to acquire lock with timeout to prevent deadlocks
        # Use asyncio.wait_for for Python 3.10 compatibility (asyncio.timeout requires 3.11+)
        try:
            # Acquire lock with timeout
            await asyncio.wait_for(toolset_lock.acquire(), timeout=LOCK_TIMEOUT)
        except asyncio.TimeoutError:
            self.logger.error(
                f"❌ Timeout waiting for refresh lock for toolset {config_path}. "
                f"Another refresh operation may be stuck. Lock will be released when that operation completes."
            )
            return
        except Exception as e:
            self.logger.error(f"❌ Error acquiring lock for toolset {config_path}: {e}", exc_info=True)
            return

        # Lock acquired - ensure it's released in all cases
        try:
            self.logger.debug(f"🔒 Acquired refresh lock for toolset {config_path}")

            # Prevent recursion (redundant with lock, but keep for safety)
            if self._is_toolset_being_processed(config_path):
                self.logger.warning(
                    f"⚠️ Already processing toolset {config_path} "
                    f"(should not happen with locks, possible deadlock)"
                )
                return

            self._mark_toolset_processing(config_path)

            try:
                # Load token from config
                token, has_credentials = await self._load_token_from_config(config_path)

                if not has_credentials:
                    self.logger.debug(f"Toolset {config_path} has no credentials to refresh")
                    return

                # Handle refresh workflow
                await self._handle_token_refresh_workflow(config_path, toolset_type, token)

            except RecursionError as e:
                self.logger.error(f"RECURSION ERROR in toolset token refresh for {config_path}: {str(e)[:100]}")
            except Exception as e:
                import traceback
                error_details = traceback.format_exc()
                self.logger.error(
                    f"❌ Error refreshing token for toolset {config_path}: {e}\n"
                    f"Error type: {type(e).__name__}\n"
                    f"Traceback:\n{error_details}"
                )
            finally:
                self._unmark_toolset_processing(config_path)
                self.logger.debug(f"🔓 Released refresh lock for toolset {config_path}")

        finally:
            # Always release the lock
            toolset_lock.release()

    async def _periodic_refresh_check(self) -> None:
        """Periodically check and refresh tokens for all toolsets"""
        self.logger.info("🔄 Starting periodic toolset token refresh check (every 5 minutes)")
        while self._running:
            try:
                await asyncio.sleep(300)  # Check every 5 minutes
                if self._running:
                    self.logger.debug("🔄 Running periodic toolset token refresh check...")
                    await self._refresh_all_tokens()
            except asyncio.CancelledError:
                self.logger.info("🛑 Periodic toolset refresh check cancelled")
                break
            except Exception as e:
                self.logger.error(f"❌ Error in periodic toolset refresh check: {e}", exc_info=True)

    async def refresh_toolset_token(self, config_path: str, toolset_type: str) -> None:
        """Manually refresh token for a specific toolset"""
        await self._refresh_toolset_token(config_path, toolset_type)

    def _calculate_refresh_delay(self, token: OAuthToken) -> tuple[float, datetime]:
        """
        Calculate delay until proactive token refresh.

        Strategy:
          - Normal tokens: refresh 10 minutes before expiry.
          - Short-lived tokens: use a smaller buffer (20% of TTL, minimum 60s).

        Returns:
            Tuple of (delay_seconds, refresh_time)
        """
        expires_in = int(token.expires_in or 0)
        if expires_in <= 0:
            return 0.0, datetime.now()

        if expires_in > PROACTIVE_REFRESH_WINDOW_SECONDS:
            refresh_window = PROACTIVE_REFRESH_WINDOW_SECONDS
        else:
            refresh_window = max(
                MIN_SHORT_LIVED_REFRESH_WINDOW_SECONDS,
                int(expires_in * SHORT_LIVED_TOKEN_BUFFER_RATIO)
            )
            # Keep the window strictly less than TTL to avoid immediate refresh loops.
            refresh_window = min(refresh_window, max(1, expires_in - 1))

        refresh_time = token.created_at + timedelta(seconds=max(0, expires_in - refresh_window))
        delay = (refresh_time - datetime.now()).total_seconds()
        return delay, refresh_time

    async def _refresh_token_immediately(
        self,
        config_path: str,
        toolset_type: str,
        token: OAuthToken
    ) -> tuple[OAuthToken | None, bool]:
        """
        Perform immediate token refresh.

        Returns:
            Tuple of (new_token, success)
        """
        try:
            new_token = await self._perform_token_refresh(config_path, toolset_type, token.refresh_token)
            self.logger.info(f"🔄 Immediate refresh completed for toolset {config_path}")
            return new_token, True
        except Exception as e:
            self.logger.error(f"❌ Failed to perform immediate refresh for toolset {config_path}: {e}", exc_info=False)
            return None, False

    def _cancel_existing_refresh_task(self, config_path: str) -> None:
        """
        Cancel existing refresh task and remove from tracking immediately.
        This prevents race conditions where a task is cancelled but still runs.
        """
        if config_path not in self._refresh_tasks:
            return

        old_task = self._refresh_tasks[config_path]

        if old_task.done():
            del self._refresh_tasks[config_path]
            self.logger.debug(f"✅ Removed completed task for toolset {config_path}")
            return

        # Cancel the task
        old_task.cancel()
        self.logger.info(f"⏹️ Cancelled existing refresh task for toolset {config_path}")

        # Remove from tracking IMMEDIATELY (don't wait for task to finish)
        # This prevents duplicate scheduling if a new task is created before cancel completes
        del self._refresh_tasks[config_path]

    def cancel_refresh_task(self, config_path: str) -> None:
        """
        Public method to cancel a refresh task for a specific toolset config path.
        Used when credentials are deleted to prevent errors from trying to refresh deleted credentials.

        Args:
            config_path: Toolset config path (e.g., /services/toolsets/{instanceId}/{userId})
        """
        self._cancel_existing_refresh_task(config_path)

    def cancel_refresh_tasks_for_instance(self, instance_id: str) -> int:
        """
        Cancel all refresh tasks for all users of a specific toolset instance.
        Used when an instance is deleted to prevent errors from trying to refresh deleted credentials.

        Args:
            instance_id: Toolset instance ID

        Returns:
            Number of tasks cancelled
        """
        cancelled_count = 0
        instance_prefix = f"/services/toolsets/{instance_id}/"

        # Find all tasks that match this instance prefix
        tasks_to_cancel = [
            config_path for config_path in self._refresh_tasks
            if config_path.startswith(instance_prefix)
        ]

        for config_path in tasks_to_cancel:
            self._cancel_existing_refresh_task(config_path)
            cancelled_count += 1

        if cancelled_count > 0:
            self.logger.info(
                f"⏹️ Cancelled {cancelled_count} refresh task(s) for instance {instance_id}"
            )

        return cancelled_count

    def _create_refresh_task(
        self,
        config_path: str,
        toolset_type: str,
        delay: float,
        refresh_time: datetime
    ) -> bool:
        """
        Create and store a new refresh task.

        Returns:
            True if task created successfully, False otherwise
        """
        try:
            # Defensive guard: avoid overwriting a still-running task due to unexpected caller race.
            existing_task = self._refresh_tasks.get(config_path)
            if existing_task and not existing_task.done() and not existing_task.cancelled():
                self.logger.warning(
                    f"⚠️ Refusing to create duplicate refresh task for {config_path} - task already running"
                )
                return False

            task = asyncio.create_task(
                self._delayed_refresh(config_path, toolset_type, delay)
            )
            self._refresh_tasks[config_path] = task
            self.logger.info(
                f"✅ Scheduled token refresh for toolset {config_path} (type: {toolset_type}) "
                f"in {delay:.0f} seconds ({delay/60:.1f} minutes) - will refresh at {refresh_time}"
            )
            return True
        except Exception as e:
            self.logger.error(f"❌ Failed to schedule token refresh for toolset {config_path}: {e}", exc_info=True)
            return False

    async def schedule_token_refresh(
        self,
        config_path: str,
        toolset_type: str,
        token: OAuthToken
    ) -> None:
        """
        Schedule token refresh for a specific toolset.
        Scheduling is atomic per config_path to prevent duplicate tasks.

        Notes:
            - If token is already at/past refresh threshold, schedule immediate re-check
              so locked refresh workflow can refresh it safely.

        Args:
            config_path: Toolset config path (e.g., /services/toolsets/{instanceId}/{userId})
            toolset_type: Toolset type
            token: Current OAuth token
        """
        if not self._running:
            self.logger.warning(f"⚠️ Toolset token refresh service not running, scheduling anyway for {config_path}")

        if not token.expires_in:
            self.logger.warning(f"⚠️ Token for toolset {config_path} has no expiry time, cannot schedule refresh")
            return

        schedule_lock = self._get_schedule_lock(config_path)
        async with schedule_lock:
            # Atomic check for existing task
            existing_task = self._refresh_tasks.get(config_path)
            if existing_task:
                current_task = asyncio.current_task()
                # If this call comes from the currently running delayed task for the same path,
                # allow self-rescheduling by clearing the old task reference first.
                if existing_task is current_task:
                    del self._refresh_tasks[config_path]
                else:
                    # If the existing task is still running (not done, not cancelled), keep it
                    if not existing_task.done() and not existing_task.cancelled():
                        self.logger.info(
                            f"⏭️ Skipping duplicate schedule request for {config_path} - "
                            f"valid refresh task already exists and is running"
                        )
                        return

                    # Existing task is dead; safe to remove
                    self.logger.debug(
                        f"📝 Existing task for {config_path} is done/cancelled, will create new task"
                    )
                    del self._refresh_tasks[config_path]

            # Prevent immediate re-refresh if we just completed one
            current_time = time.time()
            last_refresh = self._last_refresh_time.get(config_path, 0)
            time_since_last_refresh = current_time - last_refresh

            if time_since_last_refresh < REFRESH_COOLDOWN:
                self.logger.info(
                    f"⏭️ Skipping schedule request for {config_path} - "
                    f"refresh completed {time_since_last_refresh:.1f}s ago (cooldown: {REFRESH_COOLDOWN}s)"
                )
                return

            self.logger.info(f"🔄 Scheduling token refresh for toolset {config_path} (type: {toolset_type})")

            # Calculate refresh delay
            delay, refresh_time = self._calculate_refresh_delay(token)

            # If token is at/past threshold, schedule immediate re-check.
            if delay <= 0:
                delay = MIN_IMMEDIATE_RECHECK_DELAY
                refresh_time = datetime.now() + timedelta(seconds=delay)
                self.logger.info(
                    f"⏱️ Token for toolset {config_path} reached refresh threshold. "
                    f"Scheduling immediate re-check in {delay:.0f}s."
                )

            # Create new refresh task atomically while schedule lock is held
            self._create_refresh_task(config_path, toolset_type, delay, refresh_time)

    async def _delayed_refresh(self, config_path: str, toolset_type: str, delay: float) -> None:
        """Delayed token refresh for toolset"""
        try:
            await asyncio.sleep(delay)
            self.logger.info(f"⏰ Scheduled refresh time reached for toolset {config_path}, refreshing token...")
            await self._refresh_toolset_token(config_path, toolset_type)
        except asyncio.CancelledError:
            # This is expected when rescheduling - don't log as error
            self.logger.debug(f"🔄 Token refresh task cancelled for toolset {config_path} (likely rescheduled)")
            raise
        except Exception as e:
            self.logger.error(f"❌ Error in delayed token refresh for toolset {config_path}: {e}", exc_info=True)
        finally:
            # Remove task from tracking only if this exact task is still the tracked one.
            schedule_lock = self._get_schedule_lock(config_path)
            async with schedule_lock:
                tracked_task = self._refresh_tasks.get(config_path)
                if tracked_task is asyncio.current_task():
                    del self._refresh_tasks[config_path]
