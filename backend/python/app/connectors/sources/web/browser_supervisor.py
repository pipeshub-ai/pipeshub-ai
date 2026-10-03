"""Keeps the shared headless browser usable: relaunches it when it dies, recycles it on long crawls."""

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from typing import Generic, TypeVar

from app.services.base_client import CircuitBreaker
from app.telemetry.modules import web_browser_metrics as metrics

B = TypeVar("B")

# Playwright's driver process is gone: every call fails at once until a relaunch.
_DEAD_MARKERS = ("connection closed while reading from the driver",)
# A single crashed tab reports this too, so it only counts once the browser is seen disconnected.
_MAYBE_DEAD_MARKERS = ("has been closed",)

_TEARDOWN_TIMEOUT_SECONDS = 15.0
_LAUNCH_TIMEOUT_SECONDS = 90.0
_RELAUNCH_FAILURE_THRESHOLD = 3
_RELAUNCH_COOLDOWN_SECONDS = 60.0

REASON_CRASH = "crash"
REASON_RECYCLE = "recycle"


class BrowserUnavailableError(Exception):
    """The headless browser is down and could not be relaunched."""


class BrowserSupervisor(Generic[B]):
    """Owns one browser's lifecycle. Every method must run on the browser's own event loop."""

    def __init__(
        self,
        launch: Callable[[], Awaitable[B]],
        teardown: Callable[[B], Awaitable[None]],
        is_connected: Callable[[B], bool],
        *,
        recycle_after_pages: int = 0,
        breaker: CircuitBreaker | None = None,
        logger: logging.Logger | None = None,
    ) -> None:
        self._launch = launch
        self._teardown = teardown
        self._is_connected = is_connected
        self._recycle_after_pages = recycle_after_pages
        self._logger = logger or logging.getLogger(__name__)
        self._breaker = breaker or CircuitBreaker(
            "web-headless-browser",
            failure_threshold=_RELAUNCH_FAILURE_THRESHOLD,
            cooldown_seconds=_RELAUNCH_COOLDOWN_SECONDS,
            logger=self._logger,
        )
        self._lock = asyncio.Lock()
        self._browser: B | None = None
        self._launched_at = 0.0
        self._pages_served = 0
        # Bumped on every relaunch, so callers that all saw the same death relaunch once between them.
        self.generation = 0

    @property
    def browser(self) -> B | None:
        return self._browser

    async def start(self) -> None:
        self._browser = await self._launch()
        self._launched_at = time.monotonic()

    async def stop(self) -> None:
        browser, self._browser = self._browser, None
        if browser is not None:
            await self._teardown_quietly(browser)

    def note_pages(self, count: int) -> None:
        self._pages_served += count

    def is_dead_error(self, error: str | None) -> bool:
        """Whether a failed fetch means the browser is gone, rather than that one page failed."""
        if not error:
            return False
        text = error.lower()
        if any(marker in text for marker in _DEAD_MARKERS):
            return True
        return any(marker in text for marker in _MAYBE_DEAD_MARKERS) and not self._connected()

    async def ensure_ready(self) -> None:
        """Fail at once while relaunches keep failing; otherwise make sure a browser is up."""
        if self._breaker.is_open:
            metrics.record_rejected_fetch("relaunch_failing")
            raise BrowserUnavailableError("The headless browser is down and recent restarts failed")
        if self._browser is None:
            await self.recover(self.generation)

    async def recover(self, seen_generation: int) -> None:
        """Relaunch after a death seen at ``seen_generation``, unless another caller already did."""
        async with self._lock:
            if self.generation != seen_generation and self._browser is not None:
                return
            if self._breaker.is_open:
                metrics.record_rejected_fetch("relaunch_failing")
                raise BrowserUnavailableError("The headless browser is down and recent restarts failed")
            await self._relaunch(REASON_CRASH)

    async def recycle_if_due(self, gate: asyncio.Semaphore, permits: int) -> None:
        """Relaunch after ``recycle_after_pages`` pages, to bound what a long-lived browser and driver hold.

        ``gate`` is the semaphore every fetch runs under; holding all its permits
        means no page is mid-load when the old browser goes.
        """
        if not self._recycle_due():
            return
        async with self._lock:
            if not self._recycle_due():
                return
            held = 0
            try:
                for _ in range(permits):
                    await gate.acquire()
                    held += 1
                await self._relaunch(REASON_RECYCLE)
            finally:
                for _ in range(held):
                    gate.release()

    def _recycle_due(self) -> bool:
        return (
            self._recycle_after_pages > 0
            and self._browser is not None
            and self._pages_served >= self._recycle_after_pages
        )

    def _connected(self) -> bool:
        if self._browser is None:
            return False
        try:
            return bool(self._is_connected(self._browser))
        except Exception:
            # Can't tell, so the error stays what it most often is: one failed page.
            return True

    async def _relaunch(self, reason: str) -> None:
        old, self._browser = self._browser, None
        uptime = time.monotonic() - self._launched_at
        pages = self._pages_served
        if old is not None:
            await self._teardown_quietly(old)
        # After a cooldown this claims the breaker's single probe, so a failed relaunch re-opens it in full.
        self._breaker.should_attempt_probe()
        try:
            self._browser = await asyncio.wait_for(self._launch(), timeout=_LAUNCH_TIMEOUT_SECONDS)
        except Exception as e:
            self._breaker.record_failure()
            metrics.record_restart(reason, ok=False)
            self._logger.error("❌ Headless browser restart failed (reason=%s): %s", reason, e)
            raise BrowserUnavailableError(f"The headless browser could not be restarted: {e}") from e
        self._breaker.record_success()
        self.generation += 1
        self._pages_served = 0
        self._launched_at = time.monotonic()
        metrics.record_restart(reason, ok=True)
        self._logger.warning(
            "Headless browser restarted (reason=%s, generation=%d, previous uptime=%.0fs, pages served=%d)",
            reason, self.generation, uptime, pages,
        )

    async def _teardown_quietly(self, browser: B) -> None:
        try:
            await asyncio.wait_for(self._teardown(browser), timeout=_TEARDOWN_TIMEOUT_SECONDS)
        except Exception as e:
            # A dead browser rarely closes cleanly, and there is nothing left to close.
            self._logger.debug("Closing the old headless browser failed: %s", e)
