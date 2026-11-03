"""Retries a Gmail call through rate limits and brief outages, within a bounded wait.

Gmail's per-user quota is counted per minute (6,000 units for projects created since May 2026; one full-format
threads.get costs 120: https://developers.google.com/workspace/gmail/api/reference/quota). Over it, Gmail answers
429 (or 403) rateLimitExceeded with "Retry after <time>" in the message and no Retry-After header. The client
library's own retries back off for a few seconds only, so a sync that reads more than about fifty threads a
minute from one mailbox would lose threads without this.

A call that still fails after its wait (`max_wait_s` for a rate limit, `max_outage_wait_s` for a server or
network error) raises the last error: the caller records it and the sync does not claim success. One policy
serves one mailbox's sync; once `total_wait_s` is spent it waits no more, so a long outage fails the mailbox in
minutes instead of stalling the run for every thread.
"""

from __future__ import annotations

import asyncio
import random
import time
from typing import TYPE_CHECKING, TypeVar

from app.connectors.sources.google.common.gmail_errors import (
    GmailFailure,
    classify,
    retry_after_seconds,
)

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable
    from logging import Logger

T = TypeVar("T")


async def _sleep(seconds: float) -> None:
    # Looked up when a policy is made, so tests can record the waits instead of sleeping.
    await asyncio.sleep(seconds)

_RETRYABLE = {GmailFailure.RATE_LIMITED, GmailFailure.TRANSIENT}


class GmailCallPolicy:
    def __init__(
        self,
        logger: Logger,
        *,
        max_wait_s: float = 300.0,
        max_outage_wait_s: float = 30.0,
        total_wait_s: float = 900.0,
        base_delay_s: float = 2.0,
        max_delay_s: float = 65.0,
        sleep: Callable[[float], Awaitable[None]] | None = None,
        clock: Callable[[], float] = time.time,
        jitter: Callable[[], float] = random.random,
    ) -> None:
        self.logger = logger
        self.max_wait_s = max_wait_s
        self.max_outage_wait_s = min(max_outage_wait_s, max_wait_s)
        self.total_wait_s = total_wait_s
        self.base_delay_s = base_delay_s
        self.max_delay_s = max_delay_s
        self._sleep = sleep or _sleep
        self._clock = clock
        self._jitter = jitter
        self.waited_s = 0.0
        self.retries = 0

    def _delay(self, failure: GmailFailure, error: BaseException, attempt: int) -> float:
        if failure is GmailFailure.RATE_LIMITED:
            named = retry_after_seconds(error, self._clock())
            if named is not None:
                return min(named + 1.0, self.max_delay_s)
        backoff = self.base_delay_s * (2 ** attempt)
        return min(backoff * (0.5 + self._jitter() / 2), self.max_delay_s)

    async def run(self, call: Callable[[], Awaitable[T]], what: str) -> T:
        """`call()` until it succeeds, or until a failure that waiting cannot fix, or the wait budget is spent."""
        spent = 0.0
        attempt = 0
        while True:
            try:
                return await call()
            except Exception as error:
                failure = classify(error)
                if failure not in _RETRYABLE:
                    raise
                delay = self._delay(failure, error, attempt)
                limit = self.max_wait_s if failure is GmailFailure.RATE_LIMITED else self.max_outage_wait_s
                if spent + delay > limit or self.waited_s + delay > self.total_wait_s:
                    self.logger.warning(f"Gmail {what}: still failing after {spent:.0f}s of retries ({failure.value})")
                    raise
                self.logger.info(f"Gmail {what}: {failure.value}; retrying in {delay:.1f}s")
                await self._sleep(delay)
                spent += delay
                self.waited_s += delay
                self.retries += 1
                attempt += 1
