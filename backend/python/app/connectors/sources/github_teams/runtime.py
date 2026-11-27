"""
Runtime utilities for the GitHub Teams connector.

The data source is fully async (httpx), so there is no thread pool and no
blocking-call machinery here — just the plumbing every call shares:
- ``ds_call``: every data-source call goes through it — wall-clock budget,
  one OAuth refresh on 401, a bounded wait on a rate limit (up to the reset
  inside a sync run), jittered retries on transient statuses.
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
from datetime import datetime, timezone
from enum import Enum
from typing import TYPE_CHECKING, Any

from app.config.constants.http_status_code import HttpStatusCode
from app.connectors.core.base.connector.failure_notices import NO_REFRESH_TOKEN
from app.connectors.core.base.error.stream_errors import extract_source_status
from app.connectors.core.base.token_service.oauth_service import RefreshTokenInvalidError
from app.connectors.core.sync.run_context import get_run_sync_id, note_backoff_wait
from app.sources.client.github.github import GitHubResponse
from app.utils.credential_refresh_log import EVENT_CHECK_FAILED, log_refresh_failure
from app.utils.error_class import classify_error

from .constants import (
    GITHUB_SEARCH_CONCURRENCY,
    GITHUB_SEARCH_WINDOW_BUDGET,
    GITHUB_SEARCH_WINDOW_SECONDS,
    HTTP_UNAUTHORIZED,
    _AUTH_ERROR_MARKERS,
    _GITHUB_MAX_RETRIES,
    _GITHUB_OP_DEFAULT_TIMEOUT_SECONDS,
    _GITHUB_RATE_LIMIT_MAX_WAIT_SECONDS,
    _GITHUB_RETRY_BASE_DELAY_SECONDS,
    _GITHUB_RETRY_MAX_DELAY_SECONDS,
    _GITHUB_SECONDARY_RATE_LIMIT_WAIT_SECONDS,
    _GITHUB_SYNC_RATE_LIMIT_MAX_WAIT_SECONDS,
    _REPO_LOOKUP_TTL_SECONDS,
    _RETRYABLE_STATUS_CODES,
    _RETRYABLE_TRANSPORT_ERRORS,
)

if TYPE_CHECKING:
    from app.connectors.sources.github_teams.connector import GitHubTeamsConnector


class RefreshOutcome(str, Enum):
    """How an on-demand token refresh ended. Only the two refusals mean the owner must act."""

    REFRESHED = "refreshed"
    CANNOT_REFRESH = "cannot_refresh"  # an API token, or no refresh token stored
    REJECTED = "rejected"  # GitHub rejected the refresh token
    CLIENT_REJECTED = "client_rejected"  # GitHub refused the OAuth app itself: its settings need fixing, not the sign-in
    UNAVAILABLE = "unavailable"  # the refresh could not be tried or failed for a passing reason

    @property
    def refused(self) -> bool:
        return self in (RefreshOutcome.CANNOT_REFRESH, RefreshOutcome.REJECTED)


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

        # Why the last refresh could not renew the token, None when that may pass.
        self._cannot_refresh: str | None = None

        # repo id -> (when fetched, repository). See ``get_repo_by_id``.
        self._repo_lookups: dict[int, tuple[float, object]] = {}
        self._repo_lookup_locks: dict[int, asyncio.Lock] = {}

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
        """True for transient failures (429/5xx, a dropped connection) worth a backed-off retry."""
        if response is None or response.success:
            return False
        # The 504 on a wall-clock timeout is stamped locally for status
        # mapping, not reported by GitHub — retrying an op that already burned
        # its full budget would multiply the stall by _GITHUB_MAX_RETRIES.
        if response.exception_type == asyncio.TimeoutError.__name__:
            return False
        if response.status_code is None:
            return response.exception_type in _RETRYABLE_TRANSPORT_ERRORS
        return response.status_code in _RETRYABLE_STATUS_CODES

    @staticmethod
    def _is_rate_limited(response: GitHubResponse | None) -> bool:
        return response is not None and response.is_rate_limited is True

    @staticmethod
    def _rate_limit_wait_seconds(response: GitHubResponse, limited: int) -> float | None:
        """How long to wait out a rate limit in-process, or None if too long.

        Bounded because a stream request is itself on the indexing service's
        HTTP timeout; an hour-long primary reset has to be retried later. A sync
        run has no caller waiting on it, so it waits for the reset itself.
        """
        seconds = response.retry_after_seconds
        if seconds is None or (seconds == 0 and limited > 1):
            # No Retry-After (a secondary limit), or a reset our clock has passed while
            # GitHub still refuses (clock skew): at least a minute, doubling each time,
            # never a hot loop. docs.github.com/en/rest/using-the-rest-api/rate-limits-for-the-rest-api
            seconds = _GITHUB_SECONDARY_RATE_LIMIT_WAIT_SECONDS * 2 ** (limited - 1)
        cap = (
            _GITHUB_SYNC_RATE_LIMIT_MAX_WAIT_SECONDS if get_run_sync_id() is not None
            else _GITHUB_RATE_LIMIT_MAX_WAIT_SECONDS
        )
        if seconds > cap:
            return None
        # +1s covers the reset epoch's one-second resolution; jitter keeps
        # concurrent workers from all resuming on the same tick.
        return seconds + 1 + random.random() * min(5.0, seconds * 0.1)

    @staticmethod
    def _resets_at(response: GitHubResponse) -> str:
        if response.retry_after_seconds is None:
            return "an unknown time"
        return RuntimeHelper._utc(time.time() + response.retry_after_seconds)

    @staticmethod
    def _utc(epoch: float) -> str:
        return datetime.fromtimestamp(epoch, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

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

    async def refresh_token_if_needed(self) -> bool:
        """Sync the active client token from etcd when the background refresher has rotated it.

        No-op for ``API_TOKEN`` auth (PATs do not expire via OAuth). Returns False when the stored
        token could not be read or installed, so a caller that just refreshed does not retry the old one.
        """
        c = self.c
        if not c.external_client:
            return True
        try:
            config_path = f"/services/connectors/{c.connector_id}/config"
            config = await c.config_service.get_config(config_path)
            if not config:
                return False
            auth_type = (config.get("auth") or {}).get("authType", "OAUTH")
            if auth_type == "API_TOKEN":
                return True
            fresh_token = (config.get("credentials") or {}).get("access_token", "")
            if not fresh_token:
                return False
            current_token = c.external_client.get_client().get_token()
            if current_token != fresh_token:
                self.logger.debug("Updating GitHub client with refreshed OAuth token")
                self._apply_access_token_to_clients(fresh_token)
            return True
        except Exception as e:
            self.logger.warning("Could not refresh GitHub token: %s", e)
            return False

    async def force_refresh_oauth_token(self) -> RefreshOutcome:
        """Trigger an OAuth refresh via the central ``TokenRefreshService`` and sync
        the SDK with the rotated access token.

        Used reactively when a GitHub API call returns 401, so we do not wait
        for the background refresher to catch up. No-op for ``API_TOKEN`` auth.
        Returns why it did not refresh, so a passing failure (the refresh service
        down, the config unreadable, GitHub's token endpoint failing) is not
        taken for a sign-in the owner has to repair.
        """
        c = self.c
        self._cannot_refresh = None
        try:
            from app.connectors.core.base.token_service.startup_service import (
                startup_service,
            )

            config_path = f"/services/connectors/{c.connector_id}/config"
            config = await c.config_service.get_config(config_path)
            if not config:
                self.logger.error("Connector config not found; cannot refresh GitHub token.")
                return RefreshOutcome.UNAVAILABLE

            # Read before the refresh service: an API token or a missing refresh token cannot be refreshed
            # whether or not the service is running.
            auth_config = config.get("auth", {}) or {}
            if auth_config.get("authType", "OAUTH") == "API_TOKEN":
                self.logger.debug("API_TOKEN auth does not use OAuth refresh.")
                self._cannot_refresh = "an access token is not renewed automatically"
                return RefreshOutcome.CANNOT_REFRESH

            stored_token = (config.get("credentials") or {}).get("access_token")
            in_use = self._current_token()
            if stored_token and in_use and stored_token != in_use:
                # Signed in again since this run started.
                self._apply_access_token_to_clients(stored_token)
                return RefreshOutcome.REFRESHED

            refresh_token = (config.get("credentials") or {}).get("refresh_token")
            if not refresh_token:
                self.logger.error("No refresh token in connector config; cannot refresh GitHub token.")
                self._cannot_refresh = NO_REFRESH_TOKEN
                return RefreshOutcome.CANNOT_REFRESH

            refresh_service = startup_service.get_token_refresh_service()
            if not refresh_service:
                self.logger.error("Token refresh service unavailable; cannot refresh GitHub token.")
                return RefreshOutcome.UNAVAILABLE

            connector_type = (
                c.connector_name.value if hasattr(c.connector_name, "value") else str(c.connector_name)
            )
            await refresh_service.refresh_now(c.connector_id, connector_type, refresh_token)
            if not await self.refresh_token_if_needed():
                # Refreshed, but the client still holds the old token: retrying would read its 401 as a refusal.
                self.logger.error("GitHub token was refreshed but could not be installed; not retrying with the old one.")
                return RefreshOutcome.UNAVAILABLE
            return RefreshOutcome.REFRESHED
        except RefreshTokenInvalidError as e:
            log_refresh_failure(self.logger, EVENT_CHECK_FAILED, e)
            # A refused OAuth client (invalid_client, incorrect_client_credentials, ...) raises the same error as a
            # dead refresh token, but signing in again through the same app cannot fix it.
            if classify_error(e) == "misconfigured":
                self.logger.error(
                    "GitHub refused this connector's OAuth app (client ID or secret) when refreshing the token; "
                    "fix the OAuth app in the connector's settings."
                )
                return RefreshOutcome.CLIENT_REJECTED
            self._cannot_refresh = "GitHub also rejected the refresh token"
            return RefreshOutcome.REJECTED
        except Exception as e:
            log_refresh_failure(self.logger, EVENT_CHECK_FAILED, e)
            return RefreshOutcome.UNAVAILABLE

    def _current_token(self) -> str | None:
        client = self.c.external_client
        token = client.get_client().get_token() if client else None
        return token if isinstance(token, str) else None

    async def _note_rejected_credentials(self, label: str, response: GitHubResponse, why: str | None) -> None:
        """Mark the run as signed out, once a second call is refused too: one endpoint's 401 is not
        enough to stop a sync. GitHub answers the same 401 for a revoked, expired or wrong token."""
        c = self.c
        if why is None or c.run_state.rejected is not None or c.data_source is None:
            return
        sent_with = self._current_token()
        confirm = await self._execute_github_op(c.data_source.get_authenticated, op_label="confirm credentials")
        if self._is_rate_limited(confirm):
            # The caller gets the original 401, which does not show the limit that cut the check short.
            c.run_state.rate_limited = True
            return
        if not self._is_auth_error(confirm):
            return
        if self._current_token() != sent_with:
            # A new sign-in was installed while the check ran; the next call uses it.
            return
        detail = response.api_message or response.error or "Bad credentials"
        self.logger.error(
            "GitHub rejected the connector's credentials on %s (HTTP 401: %s) and they cannot be renewed (%s). "
            "The token was revoked, expired or is wrong, or its OAuth app was removed. The sync stops until "
            "the connector is reconnected.", label, detail, why,
        )
        c.run_state.reject(
            f"GitHub rejected this connector's sign-in ({why}). It may have been revoked or expired, or the "
            "OAuth app may have been removed. Syncing stopped and nothing was removed. Reconnect it, or replace "
            "its token, in the connector's settings; if that fails, check the OAuth app in GitHub.",
            unrefreshable_token=sent_with if why == NO_REFRESH_TOKEN else None,
        )

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
                status_code=HttpStatusCode.GATEWAY_TIMEOUT.value,
                exception_type=asyncio.TimeoutError.__name__,
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
                status_code=extract_source_status(e),
                exception_type=type(e).__name__,
            )

    async def get_repo_by_id(self, repo_id: int) -> GitHubResponse:
        """``GET /repositories/{id}`` through ``ds_call``, remembered per repository.

        Tickets, pull requests and code files name their repository only by
        numeric id, and every one of them is streamed separately. Without this,
        each paid a lookup before its real request: one extra call per PR or
        issue, and half of what a code file costs. The answer is the same for
        every record in the repository, so it is fetched once and kept for
        ``_REPO_LOOKUP_TTL_SECONDS``.

        Only a successful lookup is kept; a failure is returned as it was and
        the next record asks again. Concurrent streams for the same repository
        wait for one lookup rather than each making their own.
        """
        cached = self._fresh_repo_lookup(repo_id)
        if cached is not None:
            return GitHubResponse(success=True, data=cached)
        lock = self._repo_lookup_locks.setdefault(repo_id, asyncio.Lock())
        async with lock:
            cached = self._fresh_repo_lookup(repo_id)
            if cached is not None:
                return GitHubResponse(success=True, data=cached)
            response = await self.ds_call(self.c.data_source.get_repo_by_id, repo_id)
            if response.success and response.data is not None:
                self._repo_lookups[repo_id] = (time.monotonic(), response.data)
            return response

    async def forget_repo_lookup(self, repo_id: int) -> None:
        """Drop the remembered lookup so the next ``get_repo_by_id`` asks GitHub.

        For a caller whose request 404'd on what the lookup said, such as a
        default branch renamed since it was fetched. Takes the lookup lock so a
        lookup already in flight, which may predate the rename, finishes first
        and is dropped rather than stored after this returns.
        """
        lock = self._repo_lookup_locks.setdefault(repo_id, asyncio.Lock())
        async with lock:
            self._repo_lookups.pop(repo_id, None)

    def _fresh_repo_lookup(self, repo_id: int) -> object | None:
        entry = self._repo_lookups.get(repo_id)
        if entry is None:
            return None
        fetched_at, repo = entry
        if time.monotonic() - fetched_at >= _REPO_LOOKUP_TTL_SECONDS:
            return None
        return repo

    async def ds_call(
        self,
        method: Callable[..., Awaitable[GitHubResponse]],
        /,
        *args: Any,
        _github_timeout: float | None = None,
        **kwargs: Any,
    ) -> GitHubResponse:
        """Run a ``GitHubAsyncDataSource`` method, retrying on auth and transient failures.

        The OAuth token is refreshed at most once; a rate limit that resets
        soon is waited out, and transient statuses get up to
        ``_GITHUB_MAX_RETRIES`` attempts with jittered exponential backoff. The
        timeout budget applies per attempt, not to the retry chain as a whole.
        """
        label = getattr(method, "__name__", "<github op>")

        async def op() -> GitHubResponse:
            return await method(*args, **kwargs)

        refreshed = False
        limited = 0  # rate limits met so far; only they lengthen the next rate-limit wait
        response = await self._execute_github_op(op, timeout=_github_timeout, op_label=label)

        for attempt in range(1, _GITHUB_MAX_RETRIES + 1):
            if self._is_auth_error(response):
                if refreshed:
                    await self._note_rejected_credentials(label, response, "also after a token refresh")
                    return response
                self.logger.info("GitHub op %s returned auth error; refreshing OAuth token and retrying.", label)
                outcome = await self.force_refresh_oauth_token()
                if outcome is RefreshOutcome.CLIENT_REJECTED:
                    self.c.run_state.note(
                        "GitHub refused this connector's OAuth app (client ID or secret) when renewing its token. "
                        "Fix the OAuth app in the connector's settings."
                    )
                elif outcome.refused:
                    await self._note_rejected_credentials(label, response, self._cannot_refresh)
                if outcome is not RefreshOutcome.REFRESHED:
                    return response
                refreshed = True
            elif self._is_rate_limited(response):
                limited += 1
                wait = self._rate_limit_wait_seconds(response, limited)
                reason = response.api_message or response.exception_type
                resets_at = self._resets_at(response)
                # Returned as a 429, not waited out: a sync aborts with its
                # checkpoint withheld, and a stream answers 429 so indexing retries.
                if wait is None:
                    self.c.run_state.rate_limited = True
                    self.logger.warning(
                        "GitHub rate limit on %s (%s); resets at %s, later than this call waits. "
                        "Leaving it to a later retry.",
                        label, reason, resets_at,
                    )
                    return response
                if attempt == _GITHUB_MAX_RETRIES:
                    self.c.run_state.rate_limited = True
                    self.logger.warning(
                        "GitHub rate limit on %s (%s) still in force after %s attempts; resets at %s.",
                        label, reason, attempt, resets_at,
                    )
                    return response
                self.logger.warning(
                    "GitHub rate limit on %s (%s); waiting %.0fs, until %s, resets at %s (attempt %s/%s).",
                    label, reason, wait, self._utc(time.time() + wait), resets_at, attempt, _GITHUB_MAX_RETRIES,
                )
                note_backoff_wait(wait)
                await asyncio.sleep(wait)
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

        # The retries can end on an answer the loop never looked at.
        if refreshed and self._is_auth_error(response):
            await self._note_rejected_credentials(label, response, "also after a token refresh")
        elif self._is_rate_limited(response):
            self.c.run_state.rate_limited = True
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

