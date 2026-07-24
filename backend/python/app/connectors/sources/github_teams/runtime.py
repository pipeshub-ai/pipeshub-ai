"""
Runtime utilities for the GitHub Teams connector.

Responsibilities:
- Dedicated thread-pool lifecycle (creation / shutdown) — PyGithub is
  synchronous, so every call must be off-loaded to avoid blocking the event
  loop.
- Per-operation wall-clock budget: ``_execute_github_op``.
- OAuth-retry decorator: ``call_with_auth_retry``.
- Convenience wrapper for ``GitHubDataSource`` calls: ``ds_call``.
- Rate-limit awareness: proactive pause when the core budget is low; a
  separate, strictly-budgeted path for Search API calls (``search_call``).
- Token refresh plumbing: ``refresh_token_if_needed``, ``force_refresh_oauth_token``.
- Auth-error detection: ``_is_auth_error`` (string-based — ``GitHubResponse``
  does not preserve the original exception type).
"""

from __future__ import annotations

import asyncio
import inspect
import time
from collections.abc import Awaitable, Callable
from concurrent.futures import ThreadPoolExecutor
from typing import TYPE_CHECKING, Any

from app.sources.client.github.github import GitHubResponse

from .constants import (
    GITHUB_CORE_RATE_LIMIT_FLOOR,
    GITHUB_SEARCH_CONCURRENCY,
    GITHUB_SEARCH_MIN_INTERVAL_SECONDS,
    _AUTH_ERROR_MARKERS,
    _GITHUB_EXECUTOR_MAX_WORKERS,
    _GITHUB_OP_DEFAULT_TIMEOUT_SECONDS,
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

        # Dedicated thread pool — isolates PyGithub's blocking calls from the
        # shared loop executor so a hung GitHub Enterprise instance or a
        # slow org can only stall this connector's work.
        self._executor: ThreadPoolExecutor = ThreadPoolExecutor(
            max_workers=_GITHUB_EXECUTOR_MAX_WORKERS,
            thread_name_prefix=f"github-{connector.connector_id[:8]}",
        )
        connector._github_executor = self._executor

        # Search API has its own 30 req/min budget, wholly separate from the
        # 5,000 req/hr core budget. A semaphore plus a minimum inter-call gap
        # keeps the reverse-lookup phase from ever tripping a 403.
        self._search_semaphore = asyncio.Semaphore(GITHUB_SEARCH_CONCURRENCY)
        self._last_search_call_monotonic: float = 0.0
        self._search_lock = asyncio.Lock()

    # ------------------------------------------------------------------
    # Executor lifecycle
    # ------------------------------------------------------------------

    def shutdown(self, wait: bool = False) -> None:
        """Shut down the dedicated thread pool.

        ``wait=False`` abandons in-flight threads rather than blocking cleanup.
        """
        self._executor.shutdown(wait=wait, cancel_futures=not wait)

    # ------------------------------------------------------------------
    # Auth-error detection
    # ------------------------------------------------------------------

    @staticmethod
    def _is_auth_error(response: GitHubResponse | None) -> bool:
        """True when a failed ``GitHubResponse`` indicates an OAuth/token failure.

        ``GitHubDataSource`` methods catch PyGithub exceptions and stringify
        them into ``GitHubResponse.error``, so we cannot ``isinstance`` check
        the original exception — substring matching is the only option here.
        """
        if response is None or response.success:
            return False
        err = (response.error or "").lower()
        return any(marker in err for marker in _AUTH_ERROR_MARKERS)

    # ------------------------------------------------------------------
    # Token refresh
    # ------------------------------------------------------------------

    def _apply_access_token_to_clients(self, access_token: str) -> None:
        """Push a refreshed access token to the PyGithub-backed SDK client."""
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
        op: Callable[[], GitHubResponse | Awaitable[GitHubResponse]],
        *,
        timeout: float | None = None,
        op_label: str | None = None,
    ) -> GitHubResponse:
        """Run a GitHub data-source op without blocking the event loop.

        On timeout the coroutine is cancelled and ``success=False`` is
        returned so the caller's fallback logic takes over. The worker
        thread itself is left to unwind; we cannot cancel it.
        """
        budget = timeout if timeout is not None else _GITHUB_OP_DEFAULT_TIMEOUT_SECONDS
        label = op_label or getattr(op, "__name__", "<github op>")

        if inspect.iscoroutinefunction(op):
            async def _async_op() -> GitHubResponse:
                return await op()  # type: ignore[misc]
            coro: Awaitable[GitHubResponse] = _async_op()
        else:
            def _invoke_sync_op() -> GitHubResponse:
                outcome = op()
                if inspect.isawaitable(outcome):
                    raise RuntimeError(
                        "GitHub sync op returned a coroutine; call it via an async wrapper."
                    )
                return outcome  # type: ignore[return-value]
            loop = asyncio.get_running_loop()
            coro = loop.run_in_executor(self._executor, _invoke_sync_op)

        try:
            return await asyncio.wait_for(coro, timeout=budget)
        except asyncio.TimeoutError:
            self.logger.error(
                "GitHub op %s exceeded %.0fs wall-clock budget; abandoning the "
                "in-flight worker and returning success=False.",
                label,
                budget,
            )
            return GitHubResponse(
                success=False,
                data=None,
                error=f"GitHub op timed out after {budget:.0f}s",
            )

    async def call_with_auth_retry(
        self,
        op: Callable[[], GitHubResponse | Awaitable[GitHubResponse]],
        *,
        timeout: float | None = None,
        op_label: str | None = None,
    ) -> GitHubResponse:
        """Run a GitHub data-source op; on a 401-style failure, refresh the OAuth
        token once and retry.
        """
        response = await self._execute_github_op(op, timeout=timeout, op_label=op_label)
        if not self._is_auth_error(response):
            return response

        self.logger.info("GitHub API returned auth error; refreshing OAuth token and retrying once.")
        if not await self.force_refresh_oauth_token():
            return response
        return await self._execute_github_op(op, timeout=timeout, op_label=op_label)

    async def ds_call(
        self,
        method: Callable[..., GitHubResponse],
        /,
        *args: Any,
        _github_timeout: float | None = None,
        **kwargs: Any,
    ) -> GitHubResponse:
        """Run a synchronous (PyGithub-backed) ``GitHubDataSource`` method with OAuth
        retry on 401. Do NOT pass an ``async def`` method here — use ``ds_call_async``
        (e.g. ``get_img_bytes``, ``get_attachment_files_content``, which call out via
        ``httpx.AsyncClient`` directly rather than PyGithub)."""
        def op() -> GitHubResponse:
            return method(*args, **kwargs)

        return await self.call_with_auth_retry(
            op,
            timeout=_github_timeout,
            op_label=getattr(method, "__name__", None),
        )

    async def ds_call_async(
        self,
        method: Callable[..., Awaitable[GitHubResponse]],
        /,
        *args: Any,
        _github_timeout: float | None = None,
        **kwargs: Any,
    ) -> GitHubResponse:
        """Run an ``async def`` ``GitHubDataSource`` method (httpx-backed, not
        PyGithub) with OAuth retry on 401."""
        async def op() -> GitHubResponse:
            return await method(*args, **kwargs)

        return await self.call_with_auth_retry(
            op,
            timeout=_github_timeout,
            op_label=getattr(method, "__name__", None),
        )

    # ------------------------------------------------------------------
    # Search API — separate rate-limit pool
    # ------------------------------------------------------------------

    async def search_call(
        self,
        method: Callable[..., GitHubResponse],
        /,
        *args: Any,
        **kwargs: Any,
    ) -> GitHubResponse:
        """Run a Search API method (``search_users_by_email``,
        ``search_commits_by_author_email``) against the 30 req/min budget.

        Serializes calls with a minimum inter-call gap in addition to the
        concurrency cap, since GitHub enforces the Search budget strictly
        and returns 403 (not a graceful 429) on overrun.
        """
        async with self._search_semaphore:
            async with self._search_lock:
                elapsed = time.monotonic() - self._last_search_call_monotonic
                wait_for = GITHUB_SEARCH_MIN_INTERVAL_SECONDS - elapsed
                if wait_for > 0:
                    await asyncio.sleep(wait_for)
                self._last_search_call_monotonic = time.monotonic()
            return await self.ds_call(method, *args, **kwargs)

    # ------------------------------------------------------------------
    # Rate limit awareness (core budget)
    # ------------------------------------------------------------------

    async def check_rate_limit_and_pause(self) -> None:
        """Pause until reset if the core REST budget is below the floor.

        Called at sync start and periodically during long sweeps. Best-effort:
        any failure to read the rate limit is logged and ignored so it never
        blocks a sync.
        """
        c = self.c
        if not c.data_source:
            return
        try:
            res = await self.ds_call(c.data_source.get_rate_limit)
            if not res.success or not res.data:
                return
            rate = getattr(res.data, "core", None) or getattr(res.data, "rate", res.data)
            remaining = getattr(rate, "remaining", None)
            reset = getattr(rate, "reset", None)
            if remaining is None or reset is None:
                return
            if remaining > GITHUB_CORE_RATE_LIMIT_FLOOR:
                return
            import datetime as _dt
            now = _dt.datetime.now(_dt.timezone.utc)
            reset_utc = reset if reset.tzinfo else reset.replace(tzinfo=_dt.timezone.utc)
            sleep_for = max((reset_utc - now).total_seconds(), 0) + 1
            self.logger.warning(
                "GitHub core rate limit low (%s remaining); pausing %.0fs until reset.",
                remaining, sleep_for,
            )
            await asyncio.sleep(min(sleep_for, 3600))
        except Exception as e:
            self.logger.debug("check_rate_limit_and_pause: could not read rate limit: %s", e)
