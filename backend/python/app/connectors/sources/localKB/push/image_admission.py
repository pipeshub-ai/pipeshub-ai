"""Who may use the image materialisers, and in what order.

A push with ``storage://`` images takes one of 4 slots per process (2 running, 2 waiting) the moment its
body is known to hold references, without blocking: a fifth push is answered 503 ``PUSH_BUSY`` at once. A
holder then waits at most 10 s for one of the 2 materialiser positions; waiters are served least-served-first
(the user with the fewest materialiser-seconds in the last 60 s), so one account sending many pushes cannot keep
another account's single push waiting. Two budgets bound the time one caller can take: 30 materialiser-seconds
per user per rolling 60 s (then 429), and, while another organisation has a waiter, half the pool for one
organisation. A push without references never reaches this module.
"""

from __future__ import annotations

import asyncio
import contextlib
import random
import time
from collections import defaultdict, deque
from typing import TYPE_CHECKING

from .contract import PushRefusal

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Callable

SLOTS = 4
RUNNING = 2
WAIT_S = 10.0
USER_BUDGET_S = 30.0
WINDOW_S = 60.0
ORG_SHARE = 0.5


def _busy(status: int, message: str) -> PushRefusal:
    return PushRefusal(status, "PUSH_BUSY", message, extra={"retryAfter": random.randint(1, 3)})


class ImageSlot:
    def __init__(self, owner: ImageAdmission, user: str, org: str) -> None:
        self._owner, self.user, self.org = owner, user, org
        self._released = False
        self._holding = False

    @contextlib.asynccontextmanager
    async def running(self) -> AsyncIterator[None]:
        """Wait for a materialiser position (at most 10 s), hold it for the body, charge the seconds used."""
        if self._released:
            raise RuntimeError("the image slot was released")
        await self._owner._wait_turn(self)
        started = self._owner.clock()
        try:
            yield
        finally:
            self._owner._finish(self, self._owner.clock() - started)

    def release(self) -> None:
        if not self._released:
            self._released = True
            self._owner._taken -= 1

    def __enter__(self) -> ImageSlot:
        return self

    def __exit__(self, *exc: object) -> None:
        self.release()


class ImageAdmission:
    def __init__(self, *, slots: int = SLOTS, running: int = RUNNING, wait_s: float = WAIT_S,
                 user_budget_s: float = USER_BUDGET_S, clock: Callable[[], float] = time.monotonic) -> None:
        self.slots, self.running_max, self.wait_s, self.user_budget_s, self.clock = slots, running, wait_s, user_budget_s, clock
        self._taken = 0
        self._running: list[ImageSlot] = []
        self._waiters: list[tuple[int, ImageSlot, asyncio.Future[None]]] = []
        self._seq = 0
        self._usage: dict[str, deque[tuple[float, float]]] = defaultdict(deque)  # user -> (end time, seconds)
        self._org_usage: dict[str, deque[tuple[float, float]]] = defaultdict(deque)

    def reserve(self, user: str, org: str) -> ImageSlot:
        """Take a slot now or raise 503. Over the user's budget: 429."""
        if self._used(self._usage, user) >= self.user_budget_s:
            raise _busy(429, "You have used your share of image processing; retry shortly.")
        if self._taken >= self.slots:
            raise _busy(503, "The server is busy with other pushes that contain images.")
        self._taken += 1
        return ImageSlot(self, user, org)

    # ---- fairness -------------------------------------------------------------------------------

    def _used(self, table: dict[str, deque[tuple[float, float]]], key: str) -> float:
        rows = table[key]
        horizon = self.clock() - WINDOW_S
        while rows and rows[0][0] < horizon:
            rows.popleft()
        return sum(s for _, s in rows)

    def _org_blocked(self, slot: ImageSlot) -> bool:
        """An organisation may hold only its share of the positions while another organisation waits."""
        other_waiting = any(w.org != slot.org for _, w, f in self._waiters if not f.done())
        mine = sum(1 for r in self._running if r.org == slot.org)
        return other_waiting and mine >= max(1, int(self.running_max * ORG_SHARE))

    def _pick(self) -> tuple[int, ImageSlot, asyncio.Future[None]] | None:
        live = [w for w in self._waiters if not w[2].done()]
        eligible = [w for w in live if not self._org_blocked(w[1])]
        if not eligible or len(self._running) >= self.running_max:
            return None
        return min(eligible, key=lambda w: (self._used(self._usage, w[1].user), w[0]))

    def _dispatch(self) -> None:
        while (w := self._pick()) is not None:
            self._waiters.remove(w)
            self._running.append(w[1])
            w[2].set_result(None)
        self._waiters = [w for w in self._waiters if not w[2].done()]

    async def _wait_turn(self, slot: ImageSlot) -> None:
        loop = asyncio.get_running_loop()
        future: asyncio.Future[None] = loop.create_future()
        self._seq += 1
        entry = (self._seq, slot, future)
        self._waiters.append(entry)
        self._dispatch()
        try:
            await asyncio.wait_for(asyncio.shield(future), self.wait_s)
        except TimeoutError:
            self._abandon(entry)
            raise _busy(503, "The image processors are busy; retry.") from None
        except asyncio.CancelledError:
            self._abandon(entry)
            raise

    def _abandon(self, entry: tuple[int, ImageSlot, asyncio.Future[None]]) -> None:
        if entry in self._waiters:
            self._waiters.remove(entry)
        if entry[2].done() and not entry[2].cancelled() and entry[1] in self._running:
            self._running.remove(entry[1])  # it was handed a position it will never use
        entry[2].cancel()
        self._dispatch()

    def _finish(self, slot: ImageSlot, seconds: float) -> None:
        if slot in self._running:
            self._running.remove(slot)
        now = self.clock()
        self._usage[slot.user].append((now, seconds))
        self._org_usage[slot.org].append((now, seconds))
        self._dispatch()
