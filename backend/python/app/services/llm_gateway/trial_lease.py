"""One renewer for the half-open trial leases this process holds, off the loop the calls run on.

A trial can take minutes (a hang runs to the ceiling), and its lease is 60 s. If the lease were renewed by a task on the call's own
loop, a stall of that loop (the stage workers share one loop with everything else they do) would let the lease lapse, a second
trial would start and the first one's result would be dropped. So the process registers each trial with this renewer, which runs on
the **main** loop (``start`` is called from there at start-up), one task for every lease, like the consumers' ``LeaseRenewer``.
Where no renewer is running (a service whose calls already run on its main loop, a test) the gateway renews from a task of
the call's loop, as before.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import threading

from app.core.org_context import spawn_detached
from app.services.llm_gateway.fleet_breaker import FleetBreaker, FleetBreakerUnavailable
from app.services.llm_gateway.fleet_redis import get_fleet_redis

TRIAL_LEASE_RENEW_S = 20.0
# A round renews its leases side by side, at most this many at once, and gives up on the rest at its deadline (they are
# renewed next round). Rounds start every TRIAL_LEASE_RENEW_S whatever a round took, so a lease (TRIAL_LEASE_MS, 60 s)
# goes at most TRIAL_LEASE_RENEW_S + RENEW_ROUND_DEADLINE_S between renewals, however slow Redis is.
RENEW_CONCURRENCY = 16
RENEW_ROUND_DEADLINE_S = 15.0
logger = logging.getLogger(__name__)


class TrialLeaseRenewer:
    def __init__(
        self,
        interval_s: float = TRIAL_LEASE_RENEW_S,
        *,
        concurrency: int = RENEW_CONCURRENCY,
        round_deadline_s: float = RENEW_ROUND_DEADLINE_S,
    ) -> None:
        self._interval = interval_s
        self._concurrency = concurrency
        self._round_deadline = round_deadline_s
        self._leases: dict[str, tuple[str, str]] = {}
        self._lock = threading.Lock()
        self._task: asyncio.Task[None] | None = None

    @property
    def running(self) -> bool:
        return self._task is not None and not self._task.done()

    def start(self) -> None:
        """Start on the running loop (the main loop). Idempotent."""
        if not self.running:
            self._task = spawn_detached(self._run(), name="trial_lease_renewer")

    async def stop(self) -> None:
        task, self._task = self._task, None
        if task is not None:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

    def register(self, pk: str, cls: str, token: str) -> None:
        with self._lock:
            self._leases[token] = (pk, cls)

    def unregister(self, token: str) -> None:
        with self._lock:
            self._leases.pop(token, None)

    @property
    def held(self) -> int:
        with self._lock:
            return len(self._leases)

    async def renew_once(self, breaker: FleetBreaker | None = None) -> int:
        """Renew every registered lease; one whose holder no longer holds it is dropped. Returns how many were renewed."""
        if breaker is None:
            handle = get_fleet_redis()
            if handle is None:
                return 0
            breaker = FleetBreaker(handle)
        with self._lock:
            snapshot = dict(self._leases)
        gate = asyncio.Semaphore(self._concurrency)
        renewed = 0

        async def renew(token: str, pk: str, cls: str) -> None:
            nonlocal renewed
            async with gate:
                try:
                    if await breaker.renew(pk, cls, token):
                        renewed += 1
                    else:
                        self.unregister(token)
                except FleetBreakerUnavailable:
                    pass  # the next round; the lease has 60 s

        # The round's deadline cancels the renewals still waiting; what they did not reach is renewed next round.
        with contextlib.suppress(TimeoutError):
            async with asyncio.timeout(self._round_deadline):
                await asyncio.gather(*(renew(token, pk, cls) for token, (pk, cls) in snapshot.items()))
        return renewed

    async def _run(self) -> None:
        loop = asyncio.get_running_loop()
        while True:
            started = loop.time()
            try:
                await self.renew_once()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.warning("trial lease renewal round failed", exc_info=True)
            await asyncio.sleep(max(0.0, self._interval - (loop.time() - started)))


_renewer = TrialLeaseRenewer()


def get_trial_renewer() -> TrialLeaseRenewer:
    return _renewer
