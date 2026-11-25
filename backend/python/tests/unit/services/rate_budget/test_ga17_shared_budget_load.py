"""GA-17 (G-31, C20.32): agents hammer Slack while a full Slack sync runs.

A fake Slack (no network) admits ``VENDOR_PER_S`` calls in any one-second window and answers the rest 429 with a
Retry-After, as Slack does. The connectors service runs a full sync with two fetchers; the query service runs four
interactive agents and one background agent calling as fast as they can. The two services hold separate
``RedisRateBudget`` instances over one Redis, as two processes would. Expected: the sync completes, interactive calls
come first, the background agent is not starved, and no 429 storm.

The baseline test runs the same load the way it ran before the budget (the sync's own per-process limiter, agents
unpaced) and shows the rig does see the storm.
"""

from __future__ import annotations

import asyncio
import contextlib
import functools
import time
from collections import Counter, deque
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

from app.services.rate_budget import (
    AccountBudget,
    BudgetLimits,
    RateAccount,
    RatePriority,
    RedisRateBudget,
    budget_scope,
    budgeted_call,
    note_throttled,
)
from app.sources.client.resilience import ResiliencePolicy
from tests.support.fake_redis_connection_provider import FakeRedisConnectionProvider

VENDOR_PER_S = 60
LIMITS = BudgetLimits(rate=40, burst=8)
PAGES = 30
ACCOUNT = RateAccount("org-1", "slack")

Op = Callable[[], Awaitable[None]]
Call = Callable[[Op], Awaitable[None]]


class VendorThrottled(Exception):
    status_code = 429

    def __init__(self, retry_after_s: float) -> None:
        super().__init__("ratelimited")
        self.retry_after_s = retry_after_s


@dataclass
class FakeSlack:
    per_s: int
    window: deque[float] = field(default_factory=deque)
    answered: Counter[tuple[str, int]] = field(default_factory=Counter)

    async def call(self, who: str) -> None:
        now = time.monotonic()
        while self.window and now - self.window[0] >= 1.0:
            _ = self.window.popleft()
        if len(self.window) >= self.per_s:
            self.answered[(who, 429)] += 1
            raise VendorThrottled(retry_after_s=1.0)
        self.window.append(now)
        self.answered[(who, 200)] += 1
        await asyncio.sleep(0.002)

    def count(self, who: str, status: int) -> int:
        return self.answered[(who, status)]

    @property
    def throttled(self) -> int:
        return sum(n for (_, status), n in self.answered.items() if status == 429)


async def _sync(vendor: FakeSlack, take: Op, fetchers: int = 2) -> None:
    pages: asyncio.Queue[int] = asyncio.Queue()
    for page in range(PAGES):
        pages.put_nowait(page)

    async def fetcher() -> None:
        while not pages.empty():
            page = pages.get_nowait()
            await take()
            try:
                await vendor.call("sync")
            except VendorThrottled as exc:
                await note_throttled(exc.retry_after_s)
                pages.put_nowait(page)

    await asyncio.gather(*(fetcher() for _ in range(fetchers)))


async def _agent(vendor: FakeSlack, who: str, call: Call, waits: list[float], done: asyncio.Event) -> None:
    while not done.is_set():
        asked = time.monotonic()
        with contextlib.suppress(VendorThrottled):
            await call(functools.partial(_timed, vendor, who, asked, waits))
        await asyncio.sleep(0.005)


async def _timed(vendor: FakeSlack, who: str, asked: float, waits: list[float]) -> None:
    waits.append(time.monotonic() - asked)
    await vendor.call(who)


async def test_ga17_agents_hammer_slack_while_a_full_sync_runs_and_nothing_starves_or_storms() -> None:
    redis = FakeRedisConnectionProvider()
    connectors = AccountBudget(RedisRateBudget(redis), ACCOUNT, LIMITS)
    query = AccountBudget(RedisRateBudget(redis), ACCOUNT, LIMITS)
    vendor = FakeSlack(VENDOR_PER_S)
    done = asyncio.Event()
    interactive_waits: list[float] = []
    background_waits: list[float] = []
    sync_waits: list[float] = []

    def agent_call(priority: RatePriority) -> Call:
        async def call(op: Op) -> None:
            async with budgeted_call(query, priority, max_wait_s=20):
                await op()

        return call

    async def sync_take() -> None:
        asked = time.monotonic()
        _ = await connectors.acquire()
        sync_waits.append(time.monotonic() - asked)

    agents = [
        asyncio.create_task(_agent(vendor, "interactive", agent_call(RatePriority.INTERACTIVE), interactive_waits, done))
        for _ in range(4)
    ]
    agents.append(
        asyncio.create_task(_agent(vendor, "background", agent_call(RatePriority.BACKGROUND), background_waits, done))
    )
    started = time.monotonic()
    try:
        with budget_scope(connectors, RatePriority.SYNC):
            await asyncio.wait_for(_sync(vendor, sync_take), timeout=20)
    finally:
        done.set()
        await asyncio.gather(*agents)
    elapsed = time.monotonic() - started

    assert vendor.count("sync", 200) == PAGES, "the sync completes"
    assert vendor.throttled == 0, f"no 429 storm: {dict(vendor.answered)}"
    interactive, background = vendor.count("interactive", 200), vendor.count("background", 200)
    # Interactive calls come first: the sync gets about its floor (30% of the rate), the agents the rest.
    assert interactive >= 1.5 * PAGES, f"interactive calls come first: {dict(vendor.answered)}"
    assert elapsed >= PAGES / (0.5 * LIMITS.rate), "the sync is held near its floor, not given the whole rate"
    assert interactive_waits and sync_waits
    assert background >= 2, "the background agent is not starved"
    # The account stayed under its budget: burst plus rate over the run.
    assert interactive + background + PAGES <= LIMITS.burst + LIMITS.rate * elapsed + 2


async def test_ga17_baseline_without_the_shared_budget_the_same_load_storms() -> None:
    vendor = FakeSlack(VENDOR_PER_S)
    done = asyncio.Event()
    sync_policy = ResiliencePolicy(rate_limit=LIMITS.rate)

    async def unpaced(op: Op) -> None:
        await op()

    agents = [asyncio.create_task(_agent(vendor, "interactive", unpaced, [], done)) for _ in range(4)]
    started = time.monotonic()
    try:
        await asyncio.wait_for(_sync(vendor, sync_policy.acquire), timeout=20)
        # The sync's own limiter lets its first pages through at once; the agents keep calling as long as a sync would.
        await asyncio.sleep(max(0.0, 1.5 - (time.monotonic() - started)))
    finally:
        done.set()
        await asyncio.gather(*agents)

    assert vendor.throttled > 10, f"the rig sees the storm the budget prevents: {dict(vendor.answered)}"
