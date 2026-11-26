"""Worker-side self-fencing (REPORT 6.1.5, notes/07 #8, #10).

``validUntil = sentAt + leaseMs - margin`` on the suspend-aware clock: a worker
that slept through its lease has lost it even if its monotonic clock barely
moved. Every side effect checks it first. Renewal never takes the commit lock,
retries transient failures until ``validUntil``, and gives up ownership only on
404/409 (``OwnershipLost``) or expiry.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from harness_core.journal.types import JournalUnavailable, OwnershipLost
from harness_core.logs import HarnessLog

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from harness_core.clock import Clock
    from harness_core.journal.ports import JournalPort
    from harness_core.journal.types import AttemptToken


class LeaseLost(Exception):
    """This worker no longer owns the run; perform no further effect."""


class Lease:
    def __init__(
        self, clock: Clock, *, sent_at: float, lease_ms: int, margin_s: float
    ) -> None:
        self._clock = clock
        self._margin_s = margin_s
        self._valid_until = sent_at + lease_ms / 1000 - margin_s
        self.lost_reason: str | None = None
        # True when this worker stopped itself because its own lease clock ran out.
        self.self_fenced = False

    @property
    def valid_until(self) -> float:
        return self._valid_until

    def extend(self, *, sent_at: float, lease_ms: int) -> None:
        self._valid_until = max(
            self._valid_until, sent_at + lease_ms / 1000 - self._margin_s
        )

    def remaining(self) -> float:
        return self._valid_until - self._clock.suspend_aware()

    def mark_lost(self, reason: str) -> None:
        self.lost_reason = self.lost_reason or reason

    def expired(self) -> bool:
        return self._clock.suspend_aware() >= self._valid_until

    def check(self) -> None:
        if self.lost_reason is not None:
            raise LeaseLost(self.lost_reason)
        if self.expired():
            self.self_fenced = True
            self.mark_lost("lease expired (self-fence)")
            raise LeaseLost(self.lost_reason or "lease expired")


class JournalGone(Exception):
    """The journal stayed unavailable until only enough lease was left to release."""


async def while_leased[T](
    call: Callable[[], Awaitable[T]],
    *,
    lease: Lease,
    retry_s: float,
    sleep: Callable[[float], Awaitable[None]],
    on_retry: Callable[[int], None],
) -> T:
    """Retry ``call`` through a transient journal outage while the lease holds.

    It gives up (``JournalGone``) while the claim can still be released, so the
    caller hands the run back instead of failing it or letting the lease lapse.
    """
    retries = 0
    while True:
        try:
            return await call()
        except JournalUnavailable:
            if lease.remaining() <= retry_s:
                raise JournalGone from None
            retries += 1
            on_retry(retries)
            await sleep(retry_s)


class LeaseKeeper:
    """Renews off the execution path; shares no lock with commits."""

    def __init__(
        self,
        journal: JournalPort,
        token: AttemptToken,
        lease: Lease,
        clock: Clock,
        *,
        interval_s: float,
        retry_s: float = 0.5,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._journal = journal
        self._token = token
        self._lease = lease
        self._clock = clock
        self._interval_s = interval_s
        self._retry_s = retry_s
        self._sleep = sleep
        self._log = HarnessLog.for_token("harness_core.kernel.durable", token)

    async def renew_once(self) -> bool:
        """One renewal; ``False`` once ownership is gone."""
        while True:
            if self._lease.lost_reason is not None:
                return False
            if self._lease.expired():
                self._lease.self_fenced = True
                self._lease.mark_lost("lease expired before renewal succeeded")
                self._log.warning(
                    "run.self_fence", reason="renewal_did_not_succeed_in_time"
                )
                return False
            sent_at = self._clock.suspend_aware()
            try:
                grant = await self._journal.renew(self._token)
            except OwnershipLost as exc:
                self._lease.mark_lost(f"renew refused: {exc}")
                self._log.warning("lease.renew_refused", reason=type(exc).__name__)
                return False
            except JournalUnavailable as exc:
                self._log.warning(
                    "lease.renew_retry", reason=type(exc).__name__, retryS=self._retry_s
                )
                await self._sleep(self._retry_s)
                continue
            self._lease.extend(sent_at=sent_at, lease_ms=grant.lease_ms)
            self._log.debug("lease.renewed", leaseMs=grant.lease_ms)
            return True

    async def run(self) -> None:
        while await self.renew_once():
            await self._sleep(self._interval_s)


__all__ = ["JournalGone", "Lease", "LeaseKeeper", "LeaseLost", "while_leased"]
