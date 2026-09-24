"""MCP Token Refresh Service.

Background OAuth token refresh for MCP server instances — separate from the connector and
toolset refresh services (`token_refresh_service.py`, `toolset_token_refresh_service.py`) but
follows the same `start`/`stop`/`_periodic_refresh_check`/`schedule_token_refresh` shape.

Unlike the reference PR, refresh here always uses the `tokenUrl` persisted on the credential
record itself (set at OAuth-code-exchange time) rather than looking it up from a catalog
template — this is what makes refresh work for custom (non-catalog) MCP servers.
"""
import asyncio
import logging
import time
from datetime import datetime, timedelta, timezone
from typing import Dict, Optional

from app.agents.constants.mcp_server_constants import get_mcp_oauth_states_prefix
from app.agents.mcp import oauth_client as oauth_client_module
from app.agents.mcp import token_refresh as mcp_token_refresh
from app.agents.mcp.models import OAuthTokens
from app.config.configuration_service import ConfigurationService
from app.utils.request_context import new_system_root, reset_context, set_context
from app.utils.time_conversion import get_epoch_timestamp_in_ms

logger = logging.getLogger("connector_service")

CREDENTIALS_PREFIX = "/services/mcp/credentials/"
MIN_PATH_PARTS_COUNT = 5  # "", services, mcp, credentials, instanceId, userId
LOCK_TIMEOUT_SECONDS = 30
REFRESH_COOLDOWN_SECONDS = 10
MIN_IMMEDIATE_RECHECK_DELAY_SECONDS = 1
PROACTIVE_REFRESH_WINDOW_SECONDS = 600  # refresh 10 minutes before expiry
MIN_SHORT_LIVED_REFRESH_WINDOW_SECONDS = 60
SHORT_LIVED_TOKEN_BUFFER_RATIO = 0.2
MAX_REFRESH_TOKEN_INVALID_FAILURES = 3
PERIODIC_CHECK_INTERVAL_SECONDS = 300  # 5 minutes
STATE_SWEEP_INTERVAL_SECONDS = 300


class MCPTokenRefreshService:
    """Manages background OAuth token refresh across all MCP server instances."""

    def __init__(self, configuration_service: ConfigurationService) -> None:
        self.configuration_service = configuration_service
        self.logger = logger
        self._refresh_tasks: Dict[str, asyncio.Task] = {}
        self._background_tasks: list[asyncio.Task] = []
        self._running = False
        self._refresh_lock = asyncio.Lock()
        self._credential_locks: Dict[str, asyncio.Lock] = {}
        self._schedule_locks: Dict[str, asyncio.Lock] = {}
        self._last_refresh_time: Dict[str, float] = {}
        self._invalid_refresh_failures: Dict[str, int] = {}

    def _get_credential_lock(self, config_path: str) -> asyncio.Lock:
        if config_path not in self._credential_locks:
            self._credential_locks[config_path] = asyncio.Lock()
        return self._credential_locks[config_path]

    def _get_schedule_lock(self, config_path: str) -> asyncio.Lock:
        if config_path not in self._schedule_locks:
            self._schedule_locks[config_path] = asyncio.Lock()
        return self._schedule_locks[config_path]

    async def start(self, wait_for_initial_refresh: bool = True) -> None:
        if self._running:
            return
        self._running = True
        self.logger.info("Starting MCP token refresh service")

        if wait_for_initial_refresh:
            await self._refresh_all_tokens()
        else:
            self._background_tasks.append(asyncio.create_task(self._refresh_all_tokens()))

        self._background_tasks.append(asyncio.create_task(self._periodic_refresh_check()))
        self._background_tasks.append(asyncio.create_task(self._periodic_state_sweep()))

    async def stop(self) -> None:
        self._running = False
        # Cancel the initial-scan/periodic loop tasks too, not just per-credential delayed
        # refreshes — without this, an in-flight initial scan (or a periodic loop mid-sleep)
        # keeps running after stop() returns and can schedule new refresh tasks post-shutdown,
        # since `_running = False` alone is only checked at the loops' next iteration.
        for task in self._background_tasks:
            task.cancel()
        self._background_tasks.clear()
        for task in self._refresh_tasks.values():
            task.cancel()
        self._refresh_tasks.clear()
        self.logger.info("MCP token refresh service stopped")

    # ------------------------------------------------------------------
    # Discovery of authenticated OAuth credentials
    # ------------------------------------------------------------------

    async def _refresh_all_tokens(self) -> None:
        token = set_context(new_system_root())
        try:
            async with self._refresh_lock:
                await self._refresh_all_tokens_internal()
        finally:
            reset_context(token)

    async def _refresh_all_tokens_internal(self) -> None:
        try:
            all_keys = await self.configuration_service.list_keys_in_directory(CREDENTIALS_PREFIX)
        except Exception as e:
            self.logger.error(f"Error listing MCP credential keys: {e}", exc_info=True)
            return

        for config_path in all_keys:
            try:
                # /services/mcp/credentials/{instanceId}/{userId} -> exactly 5 parts after strip
                # ("services","mcp","credentials",instanceId,userId). Skips nested sub-keys like
                # .../dcr-client that also live under this prefix.
                path_parts = config_path.strip("/").split("/")
                if len(path_parts) != MIN_PATH_PARTS_COUNT:
                    continue

                record, has_oauth = await self._load_token_from_config(config_path)
                if not has_oauth or record is None:
                    continue

                await self._refresh_credential(config_path)
            except Exception as e:
                self.logger.warning(f"Error processing MCP credential {config_path}: {e}")

    async def _load_token_from_config(self, config_path: str) -> tuple[Optional[OAuthTokens], bool]:
        record = await self.configuration_service.get_config(config_path, default=None, use_cache=False)
        if not isinstance(record, dict):
            return None, False
        if not record.get("isAuthenticated"):
            return None, False
        tokens_dict = record.get("oauthTokens")
        if not isinstance(tokens_dict, dict) or not tokens_dict.get("refreshToken"):
            return None, False
        try:
            token = OAuthTokens(**tokens_dict)
        except Exception as e:
            self.logger.debug(f"Could not parse OAuth tokens for {config_path}: {e}")
            return None, False
        return token, True

    # ------------------------------------------------------------------
    # Refresh execution
    # ------------------------------------------------------------------

    async def _perform_token_refresh(self, config_path: str, refresh_token: str) -> OAuthTokens:  # noqa: ARG002
        """`refresh_token` is accepted for backward-compat call-site parity (see
        `_refresh_credential`) but the actual refresh token used is always the one
        currently persisted on the credential record — resolved, along with the DCR/admin
        OAuth client, by the shared `app.agents.mcp.token_refresh.refresh_credential_record`
        (also used by the `/oauth/refresh` route and, on-demand, the agent-loop runtime).
        """
        path_parts = config_path.strip("/").split("/")
        instance_id, user_id = path_parts[-2], path_parts[-1]

        new_tokens = await mcp_token_refresh.refresh_credential_record(
            instance_id, user_id, self.configuration_service,
        )

        self._last_refresh_time[config_path] = time.time()
        self._invalid_refresh_failures.pop(config_path, None)
        return new_tokens

    async def _handle_refresh_token_invalid(self, config_path: str, error: Exception) -> None:
        failures = self._invalid_refresh_failures.get(config_path, 0) + 1
        self._invalid_refresh_failures[config_path] = failures

        if failures < MAX_REFRESH_TOKEN_INVALID_FAILURES:
            self.logger.error(
                f"MCP refresh token rejected for {config_path} (failure {failures}/{MAX_REFRESH_TOKEN_INVALID_FAILURES}): {error}"
            )
            return

        self.logger.error(f"MCP refresh token rejected for {config_path} {failures} times, deactivating: {error}")
        self._invalid_refresh_failures.pop(config_path, None)
        try:
            record = await self.configuration_service.get_config(config_path, default=None, use_cache=False)
            if isinstance(record, dict):
                record["isAuthenticated"] = False
                record["deauthReason"] = "refresh_token_invalid"
                record["updatedAt"] = get_epoch_timestamp_in_ms()
                await self.configuration_service.set_config(config_path, record)
        except Exception as e:
            self.logger.error(f"Error marking MCP credential {config_path} unauthenticated: {e}")

    async def _refresh_credential(self, config_path: str) -> None:
        lock = self._get_credential_lock(config_path)
        try:
            await asyncio.wait_for(lock.acquire(), timeout=LOCK_TIMEOUT_SECONDS)
        except asyncio.TimeoutError:
            self.logger.error(f"Timeout waiting for MCP refresh lock for {config_path}")
            return

        try:
            token, has_oauth = await self._load_token_from_config(config_path)
            if not has_oauth or token is None:
                return

            delay, refresh_time = self._calculate_refresh_delay(token)
            if delay <= 0:
                try:
                    new_token = await self._perform_token_refresh(config_path, token.refresh_token)
                    await self.schedule_token_refresh(config_path, new_token)
                except oauth_client_module.MCPRefreshTokenInvalidError as e:
                    await self._handle_refresh_token_invalid(config_path, e)
                except Exception as e:
                    self.logger.error(f"Error refreshing MCP token for {config_path}: {e}", exc_info=True)
            else:
                await self.schedule_token_refresh(config_path, token)
        finally:
            lock.release()

    def _calculate_refresh_delay(self, token: OAuthTokens) -> tuple[float, datetime]:
        expires_in = int(token.expires_in or 0)
        now = datetime.now(timezone.utc)
        if expires_in <= 0:
            return 0.0, now

        if expires_in > PROACTIVE_REFRESH_WINDOW_SECONDS:
            refresh_window = PROACTIVE_REFRESH_WINDOW_SECONDS
        else:
            refresh_window = max(MIN_SHORT_LIVED_REFRESH_WINDOW_SECONDS, int(expires_in * SHORT_LIVED_TOKEN_BUFFER_RATIO))
            refresh_window = min(refresh_window, max(1, expires_in - 1))

        created_at = token.created_at if token.created_at.tzinfo else token.created_at.replace(tzinfo=timezone.utc)
        refresh_time = created_at + timedelta(seconds=max(0, expires_in - refresh_window))
        delay = (refresh_time - now).total_seconds()
        return delay, refresh_time

    # ------------------------------------------------------------------
    # Scheduling
    # ------------------------------------------------------------------

    async def schedule_token_refresh(self, config_path: str, token: OAuthTokens) -> None:
        if not token.expires_in:
            self.logger.debug(f"MCP token for {config_path} has no expiry; skipping schedule")
            return

        schedule_lock = self._get_schedule_lock(config_path)
        async with schedule_lock:
            existing_task = self._refresh_tasks.get(config_path)
            if existing_task:
                current_task = asyncio.current_task()
                if existing_task is current_task:
                    del self._refresh_tasks[config_path]
                elif not existing_task.done() and not existing_task.cancelled():
                    return
                else:
                    del self._refresh_tasks[config_path]

            delay, refresh_time = self._calculate_refresh_delay(token)
            if delay <= 0:
                # Cooldown only guards this branch — repeatedly computing delay<=0 (e.g. a
                # pathologically short-lived token) would otherwise hammer the refresh
                # endpoint. It must NOT gate the normal case below: `_refresh_credential`
                # calls this right after a successful refresh to schedule the NEXT one from
                # the new token's real expiry, and `_last_refresh_time` was just set to
                # "now" by that same refresh, so an unconditional cooldown check here would
                # always suppress that reschedule and leave recovery to the 5-minute
                # periodic sweep — too slow for short-lived tokens.
                time_since_last = time.time() - self._last_refresh_time.get(config_path, 0)
                if time_since_last < REFRESH_COOLDOWN_SECONDS:
                    return
                delay = MIN_IMMEDIATE_RECHECK_DELAY_SECONDS

            task = asyncio.create_task(self._delayed_refresh(config_path, delay))
            self._refresh_tasks[config_path] = task
            self.logger.info(f"Scheduled MCP token refresh for {config_path} in {delay:.0f}s (at {refresh_time})")

    async def _delayed_refresh(self, config_path: str, delay: float) -> None:
        try:
            await asyncio.sleep(delay)
            await self._refresh_credential(config_path)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            self.logger.error(f"Error in delayed MCP token refresh for {config_path}: {e}", exc_info=True)
        finally:
            schedule_lock = self._get_schedule_lock(config_path)
            async with schedule_lock:
                if self._refresh_tasks.get(config_path) is asyncio.current_task():
                    del self._refresh_tasks[config_path]

    def cancel_refresh_task(self, config_path: str) -> None:
        task = self._refresh_tasks.get(config_path)
        if not task:
            return
        if not task.done():
            task.cancel()
        del self._refresh_tasks[config_path]

    def cancel_refresh_tasks_for_instance(self, instance_id: str) -> int:
        prefix = f"{CREDENTIALS_PREFIX}{instance_id}/"
        to_cancel = [p for p in self._refresh_tasks if p.startswith(prefix)]
        for path in to_cancel:
            self.cancel_refresh_task(path)
        return len(to_cancel)

    # ------------------------------------------------------------------
    # Periodic passes
    # ------------------------------------------------------------------

    async def _periodic_refresh_check(self) -> None:
        self.logger.info("Starting periodic MCP token refresh check (every 5 minutes)")
        while self._running:
            try:
                await asyncio.sleep(PERIODIC_CHECK_INTERVAL_SECONDS)
                if self._running:
                    await self._refresh_all_tokens()
            except asyncio.CancelledError:
                break
            except Exception as e:
                self.logger.error(f"Error in periodic MCP refresh check: {e}", exc_info=True)

    async def _periodic_state_sweep(self) -> None:
        """Sweep expired OAuth CSRF states — belt-and-suspenders alongside the opportunistic
        sweep triggered on every `/oauth/authorize` call (catches states nobody ever revisits).
        """
        while self._running:
            try:
                await asyncio.sleep(STATE_SWEEP_INTERVAL_SECONDS)
                if not self._running:
                    break
                now_ms = get_epoch_timestamp_in_ms()
                keys = await self.configuration_service.list_keys_in_directory(get_mcp_oauth_states_prefix())
                for key in keys:
                    state_data = await self.configuration_service.get_config(key, default=None)
                    if isinstance(state_data, dict) and state_data.get("expiresAt", 0) < now_ms:
                        await self.configuration_service.delete_config(key)
            except asyncio.CancelledError:
                break
            except Exception as e:
                self.logger.debug(f"MCP OAuth state sweep failed (non-fatal): {e}")
