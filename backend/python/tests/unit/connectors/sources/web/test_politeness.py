"""Per-site and process-wide fetch limits (scenarios RS-02, RS-04, RS-06, DI-13)."""

import asyncio

import pytest

from app.connectors.sources.web.politeness import (
    BACKOFF_WINDOW_SECONDS,
    MAX_INTERVAL_SECONDS,
    RECOVER_AFTER,
    RECOVER_SECONDS,
    FetchSlots,
    HostGate,
    crawl_concurrency,
    fetch_slots_for,
)


class Clock:
    """Virtual time: sleeping advances it and lets other tasks run."""

    def __init__(self) -> None:
        self.t = 0.0

    def now(self) -> float:
        return self.t

    async def sleep(self, seconds: float) -> None:
        self.t += seconds
        await asyncio.sleep(0)


@pytest.mark.parametrize(
    "cpu, expected", [(None, 4), (0.5, 4), (2, 8), (4, 16), (32, 32), (64, 32)],
)
def test_process_fetch_slots_follow_the_cpus(cpu: float | None, expected: int) -> None:
    assert fetch_slots_for(cpu, env={}) == expected


def test_operators_can_set_the_limits() -> None:
    assert fetch_slots_for(64, env={"WEB_FETCH_MAX_CONCURRENCY": "6"}) == 6
    assert crawl_concurrency(env={"WEB_CRAWL_CONCURRENCY": "9"}) == 9
    assert crawl_concurrency(env={"WEB_CRAWL_CONCURRENCY": "lots"}) == 8


async def _hold(gate: HostGate, clock: Clock, host: str, active: list[int], peak: list[int], interval=None) -> None:
    async with gate.slot(host, min_interval=interval, now=clock.now, sleep=clock.sleep):
        active[0] += 1
        peak[0] = max(peak[0], active[0])
        await clock.sleep(1)
        active[0] -= 1


async def test_no_more_than_the_per_site_limit_at_once() -> None:
    gate, clock, active, peak = HostGate(2), Clock(), [0], [0]
    await asyncio.gather(*(_hold(gate, clock, "a.test", active, peak) for _ in range(6)))
    assert peak[0] == 2


async def test_sites_have_separate_limits() -> None:
    gate, clock, active, peak = HostGate(1), Clock(), [0], [0]
    await asyncio.gather(*(_hold(gate, clock, f"site{i}.test", active, peak) for i in range(3)))
    assert peak[0] == 3


async def test_crawl_delay_spaces_requests_and_is_capped() -> None:
    gate, clock = HostGate(4), Clock()
    starts: list[float] = []

    async def one(interval: float) -> None:
        async with gate.slot("a.test", min_interval=interval, now=clock.now, sleep=clock.sleep):
            starts.append(clock.t)

    for _ in range(3):
        await one(5)
    assert [b - a for a, b in zip(starts, starts[1:])] == pytest.approx([5, 5], abs=0.1)

    starts.clear()
    for _ in range(2):
        await one(3600)
    assert starts[1] - starts[0] == pytest.approx(MAX_INTERVAL_SECONDS, abs=0.1)


def test_rate_limits_cut_concurrency_then_spacing_and_success_wins_them_back() -> None:
    gate = HostGate(4)
    for t in (0, 20, 40):
        gate.observe("a.test", 429 if t != 20 else 503, now=t)
    site = gate._site("a.test")
    assert (site.limit, site.extra_interval) == (1, 1.0)
    for _ in range(RECOVER_AFTER):
        gate.observe("a.test", 200, now=41)
    assert (site.limit, site.extra_interval) == (1, 0.5)
    for _ in range(RECOVER_AFTER * 4):
        gate.observe("a.test", 200, now=41)
    assert (site.limit, site.extra_interval) == (4, 0.0)


def test_a_slowed_site_also_recovers_with_time_without_a_rate_limit() -> None:
    gate = HostGate(4)
    for t in (0, 20, 40, 60):
        gate.observe("a.test", 429, now=t)
    site = gate._site("a.test")
    assert (site.limit, site.extra_interval) == (1, 2.0)
    gate.observe("a.test", 200, now=60 + RECOVER_SECONDS - 1)
    assert site.extra_interval == 2.0
    gate.observe("a.test", 200, now=60 + RECOVER_SECONDS)
    assert site.extra_interval == 1.0
    gate.observe("a.test", 200, now=60 + 2 * RECOVER_SECONDS)
    assert site.extra_interval == 0.5


def test_a_burst_of_rate_limits_backs_off_once() -> None:
    gate = HostGate(4)
    for t in (0, 0.1, 0.2, BACKOFF_WINDOW_SECONDS - 0.1):
        gate.observe("a.test", 429, now=t)
    assert gate._site("a.test").limit == 2
    gate.observe("a.test", 429, now=BACKOFF_WINDOW_SECONDS)
    assert gate._site("a.test").limit == 1


def test_errors_other_than_rate_limits_change_nothing() -> None:
    gate = HostGate(3)
    gate.observe("a.test", 404, now=0)
    gate.observe("a.test", None, now=0)
    assert gate._site("a.test").limit == 3


async def test_process_slots_cap_every_connector_together() -> None:
    slots, clock, active, peak = FetchSlots(3), Clock(), [0], [0]

    async def one() -> None:
        async with slots.slot():
            active[0] += 1
            peak[0] = max(peak[0], active[0])
            await clock.sleep(1)
            active[0] -= 1

    await asyncio.gather(*(one() for _ in range(10)))
    assert peak[0] == 3 and slots.active == 0


async def test_a_slot_is_given_back_when_the_fetch_fails() -> None:
    gate, clock = HostGate(1), Clock()
    with pytest.raises(RuntimeError):
        async with gate.slot("a.test", min_interval=None, now=clock.now, sleep=clock.sleep):
            raise RuntimeError("boom")
    assert gate._site("a.test").active == 0
