"""
MCP Token Refresh Service

Handles automatic OAuth token refresh for MCP server instances.
Mirrors the ToolsetTokenRefreshService pattern but adapted for MCP's split
token storage (main auth at /services/mcp-servers/{id}/{uid}, OAuth tokens
at /services/mcp-servers/{id}/{uid}/oauth-tokens).
"""

import asyncio
import logging
import time
from datetime import datetime, timedelta
from typing import Any, Dict, Optional

import httpx

from app.agents.constants.mcp_server_constants import (
    get_mcp_server_config_path,
    get_mcp_server_instances_path,
    get_mcp_server_oauth_client_path,
    get_mcp_server_oauth_tokens_path,
)
from app.config.configuration_service import ConfigurationService

# ============================================================================
# Constants — mirrors toolset_token_refresh_service.py values
# ============================================================================

# Path segment counts
MIN_PATH_PARTS_COUNT = 4     # /services/mcp-servers/{instanceId}/{userId}
MAX_PATH_PARTS_COUNT = 4     # sub-paths (oauth-tokens, oauth-client, oauth-state) have 5+ parts

LOCK_TIMEOUT = 30            # Timeout for acquiring per-instance lock (seconds)
TOKEN_REFRESH_MAX_RETRIES = 3
INITIAL_RETRY_DELAY = 0.3
REFRESH_COOLDOWN = 10        # Minimum seconds between refreshes of same instance
MIN_IMMEDIATE_RECHECK_DELAY = 1
PROACTIVE_REFRESH_WINDOW_SECONDS = 600          # Refresh 10 minutes before expiry
MIN_SHORT_LIVED_REFRESH_WINDOW_SECONDS = 60     # Minimum window for short-lived tokens
SHORT_LIVED_TOKEN_BUFFER_RATIO = 0.2            # 20% buffer for short-lived tokens


class MCPTokenRefreshService:
    """
    Background service that proactively refreshes OAuth tokens for all
    authenticated MCP server instances before they expire.

    Lifecycle:
      1. start() → _refresh_all_tokens() (initial scan) + periodic task every 5 min
      2. After OAuth callback → schedule_token_refresh() adds a per-instance task
      3. _delayed_refresh() wakes up at refresh threshold → _refresh_mcp_token()
      4. _perform_token_refresh() calls the provider token URL and writes back to etcd
    """

    def __init__(self, configuration_service: ConfigurationService) -> None:
        self.configuration_service = configuration_service
        self.logger = logging.getLogger("connector_service")
        self._refresh_tasks: Dict[str, asyncio.Task] = {}
        self._running = False
        self._refresh_lock = asyncio.Lock()
        self._processing_instances: set = set()
        self._instance_locks: Dict[str, asyncio.Lock] = {}
        self._schedule_locks: Dict[str, asyncio.Lock] = {}
        self._last_refresh_time: Dict[str, float] = {}

    # =========================================================================
    # Lock helpers
    # =========================================================================

    def _get_instance_lock(self, config_path: str) -> asyncio.Lock:
        if config_path not in self._instance_locks:
            self._instance_locks[config_path] = asyncio.Lock()
        return self._instance_locks[config_path]

    def _get_schedule_lock(self, config_path: str) -> asyncio.Lock:
        if config_path not in self._schedule_locks:
            self._schedule_locks[config_path] = asyncio.Lock()
        return self._schedule_locks[config_path]

    # =========================================================================
    # Lifecycle
    # =========================================================================

    async def start(self) -> None:
        """Start the MCP token refresh service."""
        if self._running:
            return

        self._running = True
        self.logger.info("Starting MCP token refresh service")

        await self._refresh_all_tokens()

        asyncio.create_task(self._periodic_refresh_check())
        asyncio.create_task(self._cleanup_old_locks())

    async def stop(self) -> None:
        """Stop the service and cancel all scheduled refresh tasks."""
        self._running = False

        for config_path, task in list(self._refresh_tasks.items()):
            if not task.done():
                task.cancel()
                self.logger.debug(f"Cancelled MCP refresh task for {config_path}")

        self._refresh_tasks.clear()
        self.logger.info("MCP token refresh service stopped")

    # =========================================================================
    # Periodic tasks
    # =========================================================================

    async def _periodic_refresh_check(self) -> None:
        """Rescan etcd every 5 minutes and re-schedule any missed refreshes."""
        self.logger.info("Starting periodic MCP token refresh check (every 5 minutes)")
        while self._running:
            try:
                await asyncio.sleep(300)
                if self._running:
                    self.logger.debug("Running periodic MCP token refresh check...")
                    await self._refresh_all_tokens()
            except asyncio.CancelledError:
                self.logger.info("Periodic MCP refresh check cancelled")
                break
            except Exception as e:
                self.logger.error(f"Error in periodic MCP refresh check: {e}", exc_info=True)

    async def _cleanup_old_locks(self) -> None:
        """Periodically clean up stale lock entries to prevent unbounded growth."""
        while self._running:
            try:
                await asyncio.sleep(3600)

                current_paths = set(self._refresh_tasks.keys())

                for path in list(self._instance_locks.keys()):
                    if path not in current_paths:
                        lock = self._instance_locks.get(path)
                        if lock and not lock.locked():
                            del self._instance_locks[path]

                for path in list(self._schedule_locks.keys()):
                    if path not in current_paths:
                        lock = self._schedule_locks.get(path)
                        if lock and not lock.locked():
                            del self._schedule_locks[path]

                for path in list(self._last_refresh_time.keys()):
                    if path not in current_paths:
                        del self._last_refresh_time[path]

            except asyncio.CancelledError:
                break
            except Exception as e:
                self.logger.error(f"Error in MCP lock cleanup: {e}", exc_info=True)

    # =========================================================================
    # Full scan
    # =========================================================================

    async def _refresh_all_tokens(self) -> None:
        """Refresh tokens for all OAuth-authenticated MCP server instances."""
        async with self._refresh_lock:
            await self._refresh_all_tokens_internal()

    async def _refresh_all_tokens_internal(self) -> None:
        """Internal: scan /services/mcp-servers/ and schedule refresh per instance."""
        try:
            all_keys = await self.configuration_service.list_keys_in_directory(
                "/services/mcp-servers/"
            )
            self.logger.info(
                f"Found {len(all_keys)} MCP server keys in etcd (scanning /services/mcp-servers/)"
            )

            processed: dict[str, None] = {}
            skipped = {"not_oauth": 0, "no_tokens": 0, "invalid_path": 0, "duplicate": 0}

            for config_path in all_keys:
                try:
                    if config_path in processed:
                        skipped["duplicate"] += 1
                        continue
                    processed[config_path] = None

                    # Valid user-auth paths have exactly 4 segments:
                    # /services/mcp-servers/{instanceId}/{userId}
                    # Sub-paths (oauth-tokens, oauth-client, oauth-state) have 5 segments.
                    path_parts = config_path.strip("/").split("/")
                    if len(path_parts) != MIN_PATH_PARTS_COUNT:
                        skipped["invalid_path"] += 1
                        continue

                    # Filter: services / mcp-servers / {instanceId} / {userId}
                    if path_parts[0] != "services" or path_parts[1] != "mcp-servers":
                        skipped["invalid_path"] += 1
                        continue

                    instance_id = path_parts[2]
                    user_id = path_parts[3]

                    # Skip well-known non-user-auth suffixes that appear as the userId segment
                    if user_id in ("oauth-client", "oauth-state", "oauth-tokens"):
                        skipped["invalid_path"] += 1
                        continue

                    # Check if OAuth-authenticated with refresh token
                    is_oauth = await self._is_mcp_oauth_authenticated(config_path, instance_id, user_id)
                    if not is_oauth:
                        skipped["not_oauth"] += 1
                        continue

                    # Load token to get expires_at
                    tokens = await self._load_oauth_tokens(instance_id, user_id)
                    if not tokens:
                        skipped["no_tokens"] += 1
                        continue

                    expires_at = tokens.get("expires_at", 0)

                    await self._handle_refresh_workflow(config_path, instance_id, user_id, expires_at)

                except Exception as e:
                    self.logger.error(
                        f"Error processing MCP server path {config_path}: {e}", exc_info=True
                    )

            self.logger.info(
                f"MCP token scan complete. Skipped: {skipped}"
            )

        except Exception as e:
            self.logger.error(f"Error in _refresh_all_tokens_internal: {e}", exc_info=True)

    # =========================================================================
    # Authentication checks
    # =========================================================================

    async def _is_mcp_oauth_authenticated(
        self, config_path: str, instance_id: str, user_id: str
    ) -> bool:
        """
        Return True if this config path is an OAuth-authenticated MCP server
        that has a refresh token available.
        """
        try:
            config = await self.configuration_service.get_config(config_path)
            if not config or not isinstance(config, dict):
                return False

            if not config.get("isAuthenticated", False):
                return False

            if config.get("authMode", "").lower() != "oauth":
                return False

            # Check that oauth-tokens sub-path has a refresh_token
            tokens = await self._load_oauth_tokens(instance_id, user_id)
            if not tokens or not tokens.get("refresh_token"):
                return False

            return True

        except Exception as e:
            self.logger.debug(f"Could not check MCP OAuth auth for {config_path}: {e}")
            return False

    # =========================================================================
    # Token storage helpers
    # =========================================================================

    async def _load_oauth_tokens(
        self, instance_id: str, user_id: str
    ) -> dict[str, Any] | None:
        """Load OAuth tokens from the dedicated tokens sub-path."""
        path = get_mcp_server_oauth_tokens_path(instance_id, user_id)
        try:
            data = await self.configuration_service.get_config(path, default=None)
            return data if isinstance(data, dict) else None
        except Exception:
            return None

    async def _save_oauth_tokens(
        self, instance_id: str, user_id: str, tokens: dict[str, Any]
    ) -> None:
        """Persist OAuth tokens to the dedicated tokens sub-path."""
        path = get_mcp_server_oauth_tokens_path(instance_id, user_id)
        await self.configuration_service.set_config(path, tokens)

    # =========================================================================
    # Delay calculation
    # =========================================================================

    def _calculate_refresh_delay(self, expires_at: int) -> tuple[float, datetime]:
        """
        Calculate delay (seconds) until proactive token refresh.

        MCP tokens use `expires_at` (Unix epoch seconds) rather than
        OAuthToken's `created_at + expires_in`.

        Strategy:
          - Normal tokens (TTL > 600s): refresh 10 minutes before expiry.
          - Short-lived tokens: refresh at 20% of remaining TTL before expiry,
            minimum 60s window.
        """
        now_ts = time.time()
        expires_in = int(expires_at) - int(now_ts)  # remaining seconds

        if expires_in <= 0:
            return 0.0, datetime.now()

        if expires_in > PROACTIVE_REFRESH_WINDOW_SECONDS:
            refresh_window = PROACTIVE_REFRESH_WINDOW_SECONDS
        else:
            refresh_window = max(
                MIN_SHORT_LIVED_REFRESH_WINDOW_SECONDS,
                int(expires_in * SHORT_LIVED_TOKEN_BUFFER_RATIO),
            )
            # Keep window strictly less than remaining TTL to avoid refresh loops
            refresh_window = min(refresh_window, max(1, expires_in - 1))

        delay = max(0.0, expires_in - refresh_window)
        refresh_time = datetime.now() + timedelta(seconds=delay)
        return delay, refresh_time

    # =========================================================================
    # Refresh workflow
    # =========================================================================

    async def _handle_refresh_workflow(
        self, config_path: str, instance_id: str, user_id: str, expires_at: int
    ) -> None:
        """Decide whether to refresh now or schedule a future refresh."""
        delay, refresh_time = self._calculate_refresh_delay(expires_at)

        if delay <= 0:
            self.logger.info(
                f"MCP token reached refresh threshold for {config_path}; refreshing now"
            )
            new_expires_at = await self._perform_token_refresh(config_path, instance_id, user_id)
            if new_expires_at:
                await self.schedule_token_refresh(config_path, instance_id, user_id, new_expires_at)
            return

        self.logger.info(
            f"MCP token not yet in refresh window for {config_path}, "
            f"scheduling refresh in {delay:.1f}s (at {refresh_time})"
        )
        await self.schedule_token_refresh(config_path, instance_id, user_id, expires_at)

    async def _refresh_mcp_token(
        self, config_path: str, instance_id: str, user_id: str
    ) -> None:
        """
        Check token status and refresh if needed, then re-schedule next refresh.
        Uses per-instance lock to prevent concurrent refreshes.
        """
        instance_lock = self._get_instance_lock(config_path)

        try:
            await asyncio.wait_for(instance_lock.acquire(), timeout=LOCK_TIMEOUT)
        except asyncio.TimeoutError:
            self.logger.error(
                f"Timeout waiting for refresh lock for MCP instance {config_path}"
            )
            return
        except Exception as e:
            self.logger.error(f"Error acquiring lock for MCP instance {config_path}: {e}")
            return

        try:
            if config_path in self._processing_instances:
                self.logger.warning(f"Already processing MCP instance {config_path}")
                return

            self._processing_instances.add(config_path)
            try:
                # Reload current tokens
                tokens = await self._load_oauth_tokens(instance_id, user_id)
                if not tokens or not tokens.get("refresh_token"):
                    self.logger.debug(
                        f"MCP instance {config_path} has no OAuth tokens to refresh"
                    )
                    return

                expires_at = tokens.get("expires_at", 0)
                await self._handle_refresh_workflow(config_path, instance_id, user_id, expires_at)

            except Exception as e:
                self.logger.error(
                    f"Error refreshing MCP token for {config_path}: {e}", exc_info=True
                )
            finally:
                self._processing_instances.discard(config_path)

        finally:
            instance_lock.release()

    async def _perform_token_refresh(
        self, config_path: str, instance_id: str, user_id: str
    ) -> int | None:
        """
        Execute an OAuth token refresh and persist the new tokens.

        Returns the new `expires_at` (Unix epoch seconds) on success, or None on failure.
        """
        try:
            # 1. Load current refresh token
            tokens = await self._load_oauth_tokens(instance_id, user_id)
            if not tokens or not tokens.get("refresh_token"):
                self.logger.warning(f"No refresh token for MCP instance {config_path}")
                return None

            refresh_token = tokens["refresh_token"]

            # 2. Load instance metadata to find serverType
            instances_data = await self.configuration_service.get_config(
                get_mcp_server_instances_path(), default=None
            )
            if not isinstance(instances_data, list):
                self.logger.warning(
                    f"Could not load MCP instances list for refresh of {config_path}"
                )
                return None

            instance = next(
                (i for i in instances_data if i.get("_id") == instance_id), None
            )
            if not instance:
                self.logger.warning(
                    f"MCP instance {instance_id} not found in instances list"
                )
                return None

            server_type = instance.get("serverType", "")

            # 3. Resolve token URL from registry template
            from app.agents.mcp.registry import get_mcp_server_registry
            registry = get_mcp_server_registry()
            template = registry.get_template(server_type)
            self.logger.info(f"Template: {template}")
            if not template or not template.auth or not template.auth.oauth2_token_url:
                self.logger.warning(
                    f"No OAuth token URL in template for server type '{server_type}' "
                    f"(instance {instance_id})"
                )
                return None

            token_url = template.auth.oauth2_token_url

            # 4. Load OAuth client credentials (clientId / clientSecret)
            oauth_client = await self.configuration_service.get_config(
                get_mcp_server_oauth_client_path(instance_id), default=None
            )
            if not isinstance(oauth_client, dict) or not oauth_client.get("clientId"):
                self.logger.warning(
                    f"No OAuth client credentials for MCP instance {instance_id}"
                )
                return None

            client_id = oauth_client["clientId"]
            client_secret = oauth_client.get("clientSecret", "")

            # 5. Perform the token refresh HTTP call
            self.logger.info(
                f"Refreshing OAuth token for MCP instance {instance_id} "
                f"(type: {server_type}, path: {config_path})"
            )

            refresh_payload = {
                "grant_type": "refresh_token",
                "client_id": client_id,
                "client_secret": client_secret,
                "refresh_token": refresh_token,
            }

            async with httpx.AsyncClient(timeout=30.0) as http_client:
                resp = await http_client.post(
                    token_url,
                    data=refresh_payload,
                    headers={"Content-Type": "application/x-www-form-urlencoded"},
                )

            if resp.status_code != 200:
                self.logger.error(
                    f"OAuth token refresh failed for MCP instance {instance_id}: "
                    f"HTTP {resp.status_code} — {resp.text[:300]}"
                )
                return None

            token_data = resp.json()
            access_token = token_data.get("access_token", "")
            if not access_token:
                self.logger.error(
                    f"OAuth provider returned empty access_token for MCP instance {instance_id}"
                )
                return None

            new_refresh_token = token_data.get("refresh_token") or refresh_token
            expires_in = int(token_data.get("expires_in", 3600))
            new_expires_at = int(time.time()) + expires_in
            scope = token_data.get("scope", tokens.get("scope", ""))

            # 6. Persist updated tokens to oauth-tokens path
            new_tokens: dict[str, Any] = {
                "access_token": access_token,
                "refresh_token": new_refresh_token,
                "token_type": token_data.get("token_type", "Bearer"),
                "expires_at": new_expires_at,
                "scope": scope,
            }

            retry_delay = INITIAL_RETRY_DELAY
            last_error: str | None = None

            for attempt in range(TOKEN_REFRESH_MAX_RETRIES):
                if attempt > 0:
                    await asyncio.sleep(0.1)

                try:
                    await self._save_oauth_tokens(instance_id, user_id, new_tokens)

                    # Also patch the main auth record's credentials for runtime use
                    auth_path = get_mcp_server_config_path(instance_id, user_id)
                    user_auth = await self.configuration_service.get_config(
                        auth_path, default=None
                    )
                    if isinstance(user_auth, dict):
                        user_auth.setdefault("credentials", {})
                        user_auth["credentials"]["access_token"] = access_token
                        user_auth["credentials"]["expires_at"] = new_expires_at
                        user_auth["credentials"]["scope"] = scope
                        user_auth["updatedAt"] = int(time.time() * 1000)
                        await self.configuration_service.set_config(auth_path, user_auth)

                    self.logger.info(
                        f"Successfully refreshed MCP OAuth token for {config_path} "
                        f"(new expires_at={new_expires_at}, attempt={attempt + 1})"
                    )
                    self._last_refresh_time[config_path] = time.time()
                    return new_expires_at

                except Exception as e:
                    last_error = str(e)
                    self.logger.warning(
                        f"Error saving refreshed MCP token for {config_path} "
                        f"(attempt {attempt + 1}/{TOKEN_REFRESH_MAX_RETRIES}): {e}"
                    )
                    if attempt < TOKEN_REFRESH_MAX_RETRIES - 1:
                        await asyncio.sleep(retry_delay)
                        retry_delay *= 2

            self.logger.error(
                f"Failed to save refreshed MCP token for {config_path} "
                f"after {TOKEN_REFRESH_MAX_RETRIES} attempts. Last error: {last_error}"
            )
            return None

        except Exception as e:
            self.logger.error(
                f"Error performing MCP token refresh for {config_path}: {e}", exc_info=True
            )
            return None

    # =========================================================================
    # Scheduling
    # =========================================================================

    async def schedule_token_refresh(
        self,
        config_path: str,
        instance_id: str,
        user_id: str,
        expires_at: int,
    ) -> None:
        """
        Schedule a proactive token refresh for a specific MCP server instance.

        Args:
            config_path: etcd path /services/mcp-servers/{instanceId}/{userId}
            instance_id: MCP server instance UUID
            user_id: User or agent identifier
            expires_at: Token expiry as Unix epoch seconds
        """
        if not expires_at:
            self.logger.warning(
                f"MCP token for {config_path} has no expiry time, skipping schedule"
            )
            return

        schedule_lock = self._get_schedule_lock(config_path)
        async with schedule_lock:
            existing_task = self._refresh_tasks.get(config_path)
            if existing_task:
                current_task = asyncio.current_task()
                if existing_task is current_task:
                    del self._refresh_tasks[config_path]
                elif not existing_task.done() and not existing_task.cancelled():
                    self.logger.debug(
                        f"MCP refresh task already scheduled for {config_path}, skipping"
                    )
                    return
                else:
                    del self._refresh_tasks[config_path]

            # Cooldown check
            current_time = time.time()
            last_refresh = self._last_refresh_time.get(config_path, 0)
            if current_time - last_refresh < REFRESH_COOLDOWN:
                self.logger.debug(
                    f"Skipping MCP schedule for {config_path} — "
                    f"refreshed {current_time - last_refresh:.1f}s ago"
                )
                return

            delay, refresh_time = self._calculate_refresh_delay(expires_at)

            if delay <= 0:
                delay = MIN_IMMEDIATE_RECHECK_DELAY
                refresh_time = datetime.now() + timedelta(seconds=delay)

            self._create_refresh_task(config_path, instance_id, user_id, delay, refresh_time)

    def _create_refresh_task(
        self,
        config_path: str,
        instance_id: str,
        user_id: str,
        delay: float,
        refresh_time: datetime,
    ) -> bool:
        """Create and register an asyncio refresh task."""
        try:
            existing = self._refresh_tasks.get(config_path)
            if existing and not existing.done() and not existing.cancelled():
                self.logger.warning(
                    f"Refusing to create duplicate MCP refresh task for {config_path}"
                )
                return False

            task = asyncio.create_task(
                self._delayed_refresh(config_path, instance_id, user_id, delay)
            )
            self._refresh_tasks[config_path] = task
            self.logger.info(
                f"Scheduled MCP token refresh for {config_path} "
                f"in {delay:.0f}s ({delay / 60:.1f}min) — will refresh at {refresh_time}"
            )
            return True
        except Exception as e:
            self.logger.error(
                f"Failed to create MCP refresh task for {config_path}: {e}", exc_info=True
            )
            return False

    async def _delayed_refresh(
        self, config_path: str, instance_id: str, user_id: str, delay: float
    ) -> None:
        """Sleep until the refresh threshold, then execute the refresh."""
        try:
            await asyncio.sleep(delay)
            self.logger.info(
                f"Scheduled refresh time reached for MCP instance {config_path}, refreshing..."
            )
            await self._refresh_mcp_token(config_path, instance_id, user_id)
        except asyncio.CancelledError:
            self.logger.debug(
                f"MCP token refresh task cancelled for {config_path} (likely rescheduled)"
            )
            raise
        except Exception as e:
            self.logger.error(
                f"Error in delayed MCP token refresh for {config_path}: {e}", exc_info=True
            )
        finally:
            schedule_lock = self._get_schedule_lock(config_path)
            async with schedule_lock:
                tracked = self._refresh_tasks.get(config_path)
                if tracked is asyncio.current_task():
                    del self._refresh_tasks[config_path]

    # =========================================================================
    # Cancellation
    # =========================================================================

    def cancel_refresh_task(self, config_path: str) -> None:
        """
        Cancel any scheduled refresh task for the given config path.
        Called when credentials are deleted or revoked.
        """
        self._cancel_existing_refresh_task(config_path)

    def cancel_refresh_tasks_for_instance(self, instance_id: str) -> int:
        """
        Cancel all refresh tasks for every user of a specific MCP instance.
        Called when an MCP server instance is deleted.

        Returns the number of tasks cancelled.
        """
        cancelled = 0
        prefix = f"/services/mcp-servers/{instance_id}/"
        for config_path in list(self._refresh_tasks.keys()):
            if config_path.startswith(prefix):
                self._cancel_existing_refresh_task(config_path)
                cancelled += 1

        if cancelled:
            self.logger.info(
                f"Cancelled {cancelled} MCP refresh task(s) for instance {instance_id}"
            )
        return cancelled

    def _cancel_existing_refresh_task(self, config_path: str) -> None:
        if config_path not in self._refresh_tasks:
            return

        old_task = self._refresh_tasks[config_path]
        if old_task.done():
            del self._refresh_tasks[config_path]
            return

        old_task.cancel()
        self.logger.info(f"Cancelled MCP refresh task for {config_path}")
        del self._refresh_tasks[config_path]

    # =========================================================================
    # Public manual refresh
    # =========================================================================

    async def refresh_mcp_token(
        self, config_path: str, instance_id: str, user_id: str
    ) -> None:
        """Manually trigger a token refresh for a specific MCP server instance."""
        await self._refresh_mcp_token(config_path, instance_id, user_id)
