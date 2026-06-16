"""
Runtime utilities for the GitLab connector.

Responsibilities:
- Dedicated thread-pool lifecycle (creation / shutdown).
- Per-operation wall-clock budget: ``_execute_gitlab_op``.
- OAuth-retry decorator: ``_call_with_auth_retry``.
- Convenience wrappers for sync/async data-source calls: ``_ds_call``, ``_ds_call_async``.
- Page-streaming helper: ``_paged_list``.
- Token refresh plumbing: ``_refresh_token_if_needed``, ``_force_refresh_oauth_token``,
  ``_apply_access_token_to_clients``.
- Auth-error detection: ``_is_auth_error``.
"""

from __future__ import annotations

import asyncio
import inspect
from collections.abc import Awaitable, Callable
from concurrent.futures import ThreadPoolExecutor
from typing import TYPE_CHECKING, Any

from gitlab.exceptions import GitlabAuthenticationError

from app.sources.client.gitlab.gitlab import GitLabResponse

from .constants import (
    GITLAB_CLOUD_URL,
    _AUTH_ERROR_MARKERS,
    _GITLAB_EXECUTOR_MAX_WORKERS,
    _GITLAB_OP_DEFAULT_TIMEOUT_SECONDS,
    _GITLAB_PAGE_BATCH_TIMEOUT_SECONDS,
)

if TYPE_CHECKING:
    from app.connectors.sources.gitlab1.connector import GitLabConnector


class RuntimeHelper:
    """
    Handles all low-level GitLab API invocation plumbing for ``GitLabConnector``.

    Created once per connector instance (inside ``GitLabConnector.__init__``).
    Reads/writes connector state through ``self.c`` to avoid duplicating attribute
    storage.
    """

    def __init__(self, connector: "GitLabConnector") -> None:
        self.c = connector
        self.logger = connector.logger

        # Dedicated thread pool — isolates python-gitlab blocking calls from
        # the shared loop executor so a hung GitLab instance only stalls
        # this connector's GitLab work.
        self._executor: ThreadPoolExecutor = ThreadPoolExecutor(
            max_workers=_GITLAB_EXECUTOR_MAX_WORKERS,
            thread_name_prefix=f"gitlab-{connector.connector_id[:8]}",
        )
        # Expose on the connector so _paged_list and _execute_gitlab_op
        # can reference it from inside the connector too.
        connector._gitlab_executor = self._executor

    # ------------------------------------------------------------------
    # Executor lifecycle
    # ------------------------------------------------------------------

    def shutdown(self, wait: bool = False) -> None:
        """Shut down the dedicated thread pool.

        ``wait=False`` abandons in-flight threads rather than blocking
        cleanup — mirroring the original connector.cleanup() behaviour.
        """
        self._executor.shutdown(wait=wait, cancel_futures=not wait)

    # ------------------------------------------------------------------
    # Auth-error detection
    # ------------------------------------------------------------------

    @staticmethod
    def _is_auth_error_exc(exc: BaseException) -> bool:
        """True when an exception is a python-gitlab authentication error."""
        return isinstance(exc, GitlabAuthenticationError)

    @staticmethod
    def _is_auth_error(response: GitLabResponse | None) -> bool:
        """True when a failed ``GitLabResponse`` indicates an OAuth auth failure.

        Prefers ``isinstance(exc, GitlabAuthenticationError)`` when the original
        exception is available.  Falls back to substring matching on the serialised
        error string for responses produced by ``GitLabDataSource`` that already
        caught and stringified the exception.
        """
        if response is None or response.success:
            return False
        err = (response.error or "").lower()
        return any(marker in err for marker in _AUTH_ERROR_MARKERS)

    # ------------------------------------------------------------------
    # Token refresh
    # ------------------------------------------------------------------

    def _apply_access_token_to_clients(self, access_token: str) -> None:
        """Push a refreshed access token to the REST SDK and the GraphQL bearer token."""
        if not access_token:
            return
        c = self.c
        if c.external_client:
            internal_client = c.external_client.get_client()
            if internal_client.get_token() != access_token:
                internal_client.set_token(access_token)
        if c.data_source is not None:
            c.data_source.token = access_token

    async def refresh_token_if_needed(self) -> None:
        """Sync the active client token from etcd when the background refresher has rotated it.

        No-op for ``API_TOKEN`` auth (PATs do not expire via OAuth).  For OAuth,
        compares the currently-held token against etcd and calls
        ``_apply_access_token_to_clients`` only when they differ.
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
                self.logger.debug("Updating GitLab client with refreshed OAuth token")
                self._apply_access_token_to_clients(fresh_token)
        except Exception as e:
            self.logger.warning("Could not refresh GitLab token: %s", e)

    async def force_refresh_oauth_token(self) -> bool:
        """Trigger an OAuth refresh via the central ``TokenRefreshService`` and sync
        the SDK with the rotated access token.

        Used reactively when a GitLab API call returns 401, so we do not wait for
        the background refresher to catch up.  No-op for ``API_TOKEN`` auth.
        Returns ``True`` when the refresh succeeded.
        """
        c = self.c
        try:
            from app.connectors.core.base.token_service.startup_service import (
                startup_service,
            )

            refresh_service = startup_service.get_token_refresh_service()
            if not refresh_service:
                self.logger.error("Token refresh service unavailable; cannot refresh GitLab token.")
                return False

            config_path = f"/services/connectors/{c.connector_id}/config"
            config = await c.config_service.get_config(config_path)
            if not config:
                self.logger.error("Connector config not found; cannot refresh GitLab token.")
                return False

            auth_config = config.get("auth", {}) or {}
            if auth_config.get("authType", "OAUTH") == "API_TOKEN":
                self.logger.debug("API_TOKEN auth does not use OAuth refresh.")
                return False

            refresh_token = (config.get("credentials") or {}).get("refresh_token")
            if not refresh_token:
                self.logger.error("No refresh token in connector config; cannot refresh GitLab token.")
                return False

            connector_type = (
                c.connector_name.value if hasattr(c.connector_name, "value") else str(c.connector_name)
            )
            await refresh_service.refresh_now(c.connector_id, connector_type, refresh_token)
            # Sync the SDK with the new token from etcd
            await self.refresh_token_if_needed()
            return True
        except Exception as e:
            self.logger.error("GitLab OAuth token refresh failed: %s", e, exc_info=True)
            return False

    # ------------------------------------------------------------------
    # Core invocation
    # ------------------------------------------------------------------

    async def _execute_gitlab_op(
        self,
        op: Callable[[], GitLabResponse | Awaitable[GitLabResponse]],
        *,
        timeout: float | None = None,
        op_label: str | None = None,
    ) -> GitLabResponse:
        """Run a GitLab data-source op without blocking the event loop.

        Sync ops run on the connector's dedicated ``_executor`` (not the
        loop's shared default pool) so a hung GitLab instance can only stall
        this connector's work.

        ``timeout`` is a wall-clock budget applied to the entire logical call.
        On expiry the coroutine is cancelled and ``success=False`` is returned
        so the caller's fallback logic (creator permissions, skip-and-continue)
        takes over.  The worker thread itself is left to unwind via
        python-gitlab's per-request timeout; we cannot cancel it.
        """
        budget = timeout if timeout is not None else _GITLAB_OP_DEFAULT_TIMEOUT_SECONDS
        label = op_label or getattr(op, "__name__", "<gitlab op>")

        if inspect.iscoroutinefunction(op):
            async def _async_op() -> GitLabResponse:
                return await op()  # type: ignore[misc]
            coro: Awaitable[GitLabResponse] = _async_op()
        else:
            def _invoke_sync_op() -> GitLabResponse:
                outcome = op()
                if inspect.isawaitable(outcome):
                    raise RuntimeError(
                        "GitLab sync op returned a coroutine; use _ds_call_async instead."
                    )
                return outcome  # type: ignore[return-value]
            loop = asyncio.get_running_loop()
            coro = loop.run_in_executor(self._executor, _invoke_sync_op)

        try:
            return await asyncio.wait_for(coro, timeout=budget)
        except asyncio.TimeoutError:
            self.logger.error(
                "GitLab op %s exceeded %.0fs wall-clock budget; abandoning the "
                "in-flight worker and returning success=False. "
                "Increase the budget or narrow sync_filters if this fires under normal load.",
                label,
                budget,
            )
            return GitLabResponse(
                success=False,
                data=None,
                error=f"GitLab op timed out after {budget:.0f}s",
            )

    async def call_with_auth_retry(
        self,
        op: Callable[[], GitLabResponse | Awaitable[GitLabResponse]],
        *,
        timeout: float | None = None,
        op_label: str | None = None,
    ) -> GitLabResponse:
        """Run a GitLab data-source op; on a 401-style failure, refresh the OAuth
        token once and retry.

        ``timeout`` applies independently to each attempt — a stuck first call
        that triggers a refresh-and-retry still gets the full budget on retry.
        """
        response = await self._execute_gitlab_op(op, timeout=timeout, op_label=op_label)
        if not self._is_auth_error(response):
            return response

        self.logger.info("GitLab API returned auth error; refreshing OAuth token and retrying once.")
        if not await self.force_refresh_oauth_token():
            return response
        return await self._execute_gitlab_op(op, timeout=timeout, op_label=op_label)

    async def ds_call(
        self,
        method: Callable[..., GitLabResponse],
        /,
        *args: Any,
        _gitlab_timeout: float | None = None,
        **kwargs: Any,
    ) -> GitLabResponse:
        """Run a synchronous ``GitLabDataSource`` method with OAuth retry on 401.

        ``_gitlab_timeout`` lets callers override the default wall-clock budget
        for a single logical call (e.g. give a large membership sweep more
        headroom than a simple ``get_user`` lookup).  Underscore prefix avoids
        collision with python-gitlab kwargs.
        """
        def op() -> GitLabResponse:
            return method(*args, **kwargs)

        return await self.call_with_auth_retry(
            op,
            timeout=_gitlab_timeout,
            op_label=getattr(method, "__name__", None),
        )

    async def ds_call_async(
        self,
        method: Callable[..., Awaitable[GitLabResponse]],
        /,
        *args: Any,
        _gitlab_timeout: float | None = None,
        **kwargs: Any,
    ) -> GitLabResponse:
        """Run an async ``GitLabDataSource`` method (e.g. GraphQL) with OAuth retry."""
        async def op() -> GitLabResponse:
            return await method(*args, **kwargs)

        return await self.call_with_auth_retry(
            op,
            timeout=_gitlab_timeout,
            op_label=getattr(method, "__name__", None),
        )

    async def paged_list(
        self,
        method: Callable[..., GitLabResponse],
        /,
        *args: Any,
        progress_label: str,
        progress_every: int = 500,
        **kwargs: Any,
    ) -> GitLabResponse:
        """Stream a ``list_*`` operation page-by-page and log INFO progress.

        Replaces ``get_all=True`` on large sweeps.  With ``get_all=True``
        python-gitlab materialises every page inside one blocking call with no
        log output — on a large EE tenant that can take an hour and look
        indistinguishable from a hang.

        This helper asks the SDK for a ``GitlabList`` iterator and drains it
        in batches of ``progress_every`` items off-loop, emitting one INFO
        line per batch so the operator can see forward motion.

        Returns a ``GitLabResponse`` whose ``data`` is a fully-materialised
        list.  On a mid-iteration failure, returns the partial list under
        ``success=False`` so the caller can distinguish "scan aborted midway"
        from "scan completed and found nothing".
        """
        iter_res = await self.ds_call(method, *args, iterator=True, **kwargs)
        if not iter_res.success or iter_res.data is None:
            return iter_res

        paged_iter = iter(iter_res.data)
        items: list[Any] = []

        def _drain(it: Any, n: int) -> tuple[list[Any], bool, Exception | None]:
            out: list[Any] = []
            for _ in range(n):
                try:
                    out.append(next(it))
                except StopIteration:
                    return out, True, None
                except Exception as e:
                    return out, True, e
            return out, False, None

        loop = asyncio.get_running_loop()
        while True:
            try:
                batch, done, err = await asyncio.wait_for(
                    loop.run_in_executor(self._executor, _drain, paged_iter, progress_every),
                    timeout=_GITLAB_PAGE_BATCH_TIMEOUT_SECONDS,
                )
            except asyncio.TimeoutError:
                self.logger.error(
                    "%s: page batch exceeded %.0fs wall-clock budget after %s items; "
                    "abandoning the worker thread. Increase the budget or narrow sync_filters.",
                    progress_label,
                    _GITLAB_PAGE_BATCH_TIMEOUT_SECONDS,
                    len(items),
                )
                return GitLabResponse(
                    success=False,
                    data=items,
                    error=f"GitLab page batch timed out after {_GITLAB_PAGE_BATCH_TIMEOUT_SECONDS:.0f}s",
                )

            items.extend(batch)
            if items:
                self.logger.info("%s: fetched %s so far", progress_label, len(items))
            if err is not None:
                self.logger.error(
                    "%s: error after %s items: %s",
                    progress_label,
                    len(items),
                    err,
                    exc_info=err,
                )
                return GitLabResponse(success=False, data=items, error=str(err))
            if done:
                break

        self.logger.info("%s: complete, total=%s", progress_label, len(items))
        return GitLabResponse(success=True, data=items)
