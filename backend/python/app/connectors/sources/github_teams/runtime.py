"""
Runtime utilities for the GitHub Teams connector.

The data source is fully async (httpx), so there is no thread pool and no
blocking-call machinery here — just the plumbing every call shares:
- ``ds_call``: every data-source call goes through it — wall-clock budget,
  one OAuth refresh on 401, jittered retries on transient statuses.
- A strictly-paced path for Search API calls (``search_call``) — GitHub's
  Search budget is 30 req/min and overruns 403 hard, unlike the core budget
  where a failed sync simply retries on the next schedule.
- Token refresh plumbing: ``refresh_token_if_needed``, ``force_refresh_oauth_token``.
- Auth-error detection: ``_is_auth_error`` (status-code based, with a
  substring fallback for responses that carry no status).
"""

from __future__ import annotations

import asyncio
import random
import time
from collections import deque
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any

from app.sources.client.github.github import GitHubResponse

from .constants import (
    GITHUB_SEARCH_CONCURRENCY,
    GITHUB_SEARCH_WINDOW_BUDGET,
    GITHUB_SEARCH_WINDOW_SECONDS,
    HTTP_UNAUTHORIZED,
    _AUTH_ERROR_MARKERS,
    _GITHUB_MAX_RETRIES,
    _GITHUB_OP_DEFAULT_TIMEOUT_SECONDS,
    _GITHUB_RETRY_BASE_DELAY_SECONDS,
    _GITHUB_RETRY_MAX_DELAY_SECONDS,
    _RETRYABLE_STATUS_CODES,
)

if TYPE_CHECKING:
    from app.connectors.sources.github_teams.connector import GitHubTeamsConnector


class RuntimeHelper:
    """Handles all low-level GitHub API invocation plumbing for ``GitHubTeamsConnector``.

    Created once per connector instance (inside ``GitHubTeamsConnector.__init__``).
    Reads/writes connector state through ``self.c`` to avoid duplicating attribute
    storage.
    """

    def __init__(self, connector: "GitHubTeamsConnector") -> None:
        self.c = connector
        self.logger = connector.logger

        # Search API has its own 30 req/min budget, wholly separate from the
        # 5,000 req/hr core budget. A semaphore plus a sliding one-minute
        # window keeps the repo-picker search from ever tripping a 403 while
        # letting bursts (scoped+public pair) run back-to-back.
        self._search_semaphore = asyncio.Semaphore(GITHUB_SEARCH_CONCURRENCY)
        self._search_call_times: deque[float] = deque()
        self._search_lock = asyncio.Lock()

    # ------------------------------------------------------------------
    # Auth-error detection
    # ------------------------------------------------------------------

    @staticmethod
    def _is_auth_error(response: GitHubResponse | None) -> bool:
        """True when a failed ``GitHubResponse`` indicates an OAuth/token failure."""
        if response is None or response.success:
            return False
        if response.status_code is not None:
            return response.status_code == HTTP_UNAUTHORIZED
        err = (response.error or "").lower()
        return any(marker in err for marker in _AUTH_ERROR_MARKERS)

    @staticmethod
    def _is_retryable_error(response: GitHubResponse | None) -> bool:
        """True for transient failures (429/5xx) worth a backed-off retry."""
        if response is None or response.success:
            return False
        return response.status_code in _RETRYABLE_STATUS_CODES

    # ------------------------------------------------------------------
    # Token refresh
    # ------------------------------------------------------------------

    def _apply_access_token_to_clients(self, access_token: str) -> None:
        """Store a rotated access token on the client wrapper.

        The async data source reads the token live from the wrapper on every
        request, so updating it here is all a rotation needs.
        """
        if not access_token:
            return
        c = self.c
        if c.external_client:
            internal_client = c.external_client.get_client()
            if internal_client.get_token() != access_token:
                internal_client.set_token(access_token)

    async def refresh_token_if_needed(self) -> None:
        """Sync the active client token from etcd when the background refresher has rotated it.

        No-op for ``API_TOKEN`` auth (PATs do not expire via OAuth).
        """
        c = self.c
        if not c.external_client:
            return
        try:
            config_path = f"/services/connectors/{c.connector_id}/config"
            config = await c.config_service.get_config(config_path)
            if not config:
                return
            auth_type = (config.get("auth") or {}).get("authType", "OAUTH")
            if auth_type == "API_TOKEN":
                return
            fresh_token = (config.get("credentials") or {}).get("access_token", "")
            if not fresh_token:
                return
            current_token = c.external_client.get_client().get_token()
            if current_token != fresh_token:
                self.logger.debug("Updating GitHub client with refreshed OAuth token")
                self._apply_access_token_to_clients(fresh_token)
        except Exception as e:
            self.logger.warning("Could not refresh GitHub token: %s", e)

    async def force_refresh_oauth_token(self) -> bool:
        """Trigger an OAuth refresh via the central ``TokenRefreshService`` and sync
        the SDK with the rotated access token.

        Used reactively when a GitHub API call returns 401, so we do not wait
        for the background refresher to catch up. No-op for ``API_TOKEN`` auth.
        """
        c = self.c
        try:
            from app.connectors.core.base.token_service.startup_service import (
                startup_service,
            )

            refresh_service = startup_service.get_token_refresh_service()
            if not refresh_service:
                self.logger.error("Token refresh service unavailable; cannot refresh GitHub token.")
                return False

            config_path = f"/services/connectors/{c.connector_id}/config"
            config = await c.config_service.get_config(config_path)
            if not config:
                self.logger.error("Connector config not found; cannot refresh GitHub token.")
                return False

            auth_config = config.get("auth", {}) or {}
            if auth_config.get("authType", "OAUTH") == "API_TOKEN":
                self.logger.debug("API_TOKEN auth does not use OAuth refresh.")
                return False

            refresh_token = (config.get("credentials") or {}).get("refresh_token")
            if not refresh_token:
                self.logger.error("No refresh token in connector config; cannot refresh GitHub token.")
                return False

            connector_type = (
                c.connector_name.value if hasattr(c.connector_name, "value") else str(c.connector_name)
            )
            await refresh_service.refresh_now(c.connector_id, connector_type, refresh_token)
            await self.refresh_token_if_needed()
            return True
        except Exception as e:
            self.logger.error("GitHub OAuth token refresh failed: %s", e, exc_info=True)
            return False

    # ------------------------------------------------------------------
    # Core invocation
    # ------------------------------------------------------------------

    async def _execute_github_op(
        self,
        op: Callable[[], Awaitable[GitHubResponse]],
        *,
        timeout: float | None = None,
        op_label: str | None = None,
    ) -> GitHubResponse:
        """Run one async GitHub data-source op under a wall-clock budget.

        On timeout the coroutine is cancelled cleanly and ``success=False`` is
        returned so the caller's fallback logic takes over.
        """
        budget = timeout if timeout is not None else _GITHUB_OP_DEFAULT_TIMEOUT_SECONDS
        label = op_label or getattr(op, "__name__", "<github op>")

        try:
            return await asyncio.wait_for(op(), timeout=budget)
        except asyncio.CancelledError:
            raise
        except asyncio.TimeoutError:
            self.logger.error(
                "GitHub op %s exceeded %.0fs wall-clock budget; returning success=False.",
                label,
                budget,
            )
            return GitHubResponse(
                success=False,
                data=None,
                error=f"GitHub op timed out after {budget:.0f}s",
            )
        except Exception as e:
            # Data-source methods normally convert exceptions to success=False,
            # but anything raised outside that try (or by a non-data-source op)
            # would otherwise unwind past callers' `if not res.success` fallbacks.
            self.logger.error("GitHub op %s raised %s: %s", label, type(e).__name__, e, exc_info=True)
            return GitHubResponse(
                success=False,
                data=None,
                error=str(e),
                status_code=getattr(e, "status", None),
                exception_type=type(e).__name__,
            )

    async def ds_call(
        self,
        method: Callable[..., Awaitable[GitHubResponse]],
        /,
        *args: Any,
        _github_timeout: float | None = None,
        **kwargs: Any,
    ) -> GitHubResponse:
        """Run a ``GitHubAsyncDataSource`` method, retrying on auth and transient failures.

        The OAuth token is refreshed at most once; transient statuses get up to
        ``_GITHUB_MAX_RETRIES`` attempts with jittered exponential backoff. The
        timeout budget applies per attempt, not to the retry chain as a whole.
        """
        label = getattr(method, "__name__", "<github op>")

        async def op() -> GitHubResponse:
            return await method(*args, **kwargs)

        refreshed = False
        response = await self._execute_github_op(op, timeout=_github_timeout, op_label=label)

        for attempt in range(1, _GITHUB_MAX_RETRIES + 1):
            if self._is_auth_error(response):
                if refreshed:
                    return response
                refreshed = True
                self.logger.info("GitHub op %s returned auth error; refreshing OAuth token and retrying.", label)
                if not await self.force_refresh_oauth_token():
                    return response
            elif self._is_retryable_error(response):
                if attempt == _GITHUB_MAX_RETRIES:
                    self.logger.warning(
                        "GitHub op %s still failing after %s attempts (status=%s); giving up.",
                        label, attempt, response.status_code,
                    )
                    return response
                delay = min(
                    _GITHUB_RETRY_BASE_DELAY_SECONDS * (2 ** (attempt - 1)),
                    _GITHUB_RETRY_MAX_DELAY_SECONDS,
                )
                delay *= 0.5 + random.random()  # jitter, so concurrent workers don't retry in lockstep
                self.logger.info(
                    "GitHub op %s failed with status %s; retrying in %.1fs (attempt %s/%s).",
                    label, response.status_code, delay, attempt, _GITHUB_MAX_RETRIES,
                )
                await asyncio.sleep(delay)
            else:
                return response

            response = await self._execute_github_op(op, timeout=_github_timeout, op_label=label)

        return response

    # ------------------------------------------------------------------
    # Search API — separate rate-limit pool
    # ------------------------------------------------------------------

    async def search_call(
        self,
        method: Callable[..., Awaitable[GitHubResponse]],
        /,
        *args: Any,
        **kwargs: Any,
    ) -> GitHubResponse:
        """Run a Search API method (``search_repositories``) against
        the 30 req/min budget.

        Paces with a sliding one-minute window (not a fixed inter-call gap):
        GitHub's budget is per minute and 403s hard on overrun, but bursts
        within the window are fine — a fixed gap added ~2.1s of dead latency
        to every picker call for no extra safety.
        """

        def _purge(now: float) -> None:
            while (
                self._search_call_times
                and now - self._search_call_times[0] >= GITHUB_SEARCH_WINDOW_SECONDS
            ):
                self._search_call_times.popleft()

        async with self._search_semaphore:
            async with self._search_lock:
                now = time.monotonic()
                _purge(now)
                if len(self._search_call_times) >= GITHUB_SEARCH_WINDOW_BUDGET:
                    wait_for = GITHUB_SEARCH_WINDOW_SECONDS - (now - self._search_call_times[0])
                    if wait_for > 0:
                        await asyncio.sleep(wait_for)
                    _purge(time.monotonic())
                self._search_call_times.append(time.monotonic())
            return await self.ds_call(method, *args, **kwargs)

