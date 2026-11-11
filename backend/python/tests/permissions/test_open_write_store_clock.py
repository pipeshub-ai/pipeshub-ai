"""A write's lease is written and judged by the store's clock, so a sweeper whose own clock is far off cannot recover a live write (which would
let the connector read current, the write land, and a process killed before its next renewal leave the old scope served), and cannot leave a dead
one standing either.
"""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest

from app.services.permissions import structure_generation as sg
from app.services.permissions.currency import app_is_current
from app.services.permissions.pending import parse
from app.services.permissions.structure_generation import (
    GENERATION_KEY,
    OPEN_WRITE_GRACE_SECONDS,
    OPEN_WRITE_SECONDS,
    OPEN_WRITES_KEY,
    PENDING_KEY,
    open_write,
    parse_open_writes,
    recover_expired_writes,
    renew_write,
    store_now,
    unclosed_writes,
)

from .test_open_write_lease import DEAD, Clock, Graph, a_finished_pass


class StoreGraph(Graph):
    """A graph whose store has a clock of its own (`clock`), as the real providers do."""

    def __init__(self, clock: Clock) -> None:
        super().__init__()
        self.clock = clock

    async def store_time(self) -> float:
        return self.clock()


def host_clock(monkeypatch: pytest.MonkeyPatch, offset: float, store: Clock) -> None:
    """The local clock of the process that calls `time.time()` in the module: the store's time plus ``offset``."""
    monkeypatch.setattr(sg, "time", SimpleNamespace(time=lambda: store() + offset))


@pytest.mark.parametrize("skew", [3600, 700, 130, -3600])
async def test_a_sweeper_with_a_wrong_clock_neither_recovers_a_live_write_nor_keeps_a_dead_one(monkeypatch: pytest.MonkeyPatch, skew: int) -> None:
    store = Clock()
    g = StoreGraph(store)
    a_finished_pass(g.app)
    token = await open_write(g, "c", ["r1"])                    # the writer opens: its deadline is the store's time plus the lease
    assert parse_open_writes(g.app[OPEN_WRITES_KEY])[token]["u"] == int(store()) + OPEN_WRITE_SECONDS
    store.now += 100
    host_clock(monkeypatch, skew, store)                        # the sweeper's host is off
    assert await recover_expired_writes(g, "c") == 0                 # live: left alone, so the connector never reads current while the write lands
    assert unclosed_writes(g.app) == 1 and not app_is_current(g.app)
    # ... the write lands and the process is killed: nobody closes it
    store.now += DEAD
    assert await recover_expired_writes(g, "c") == 1                 # dead by the store's clock whatever the sweeper's host says
    assert set(parse(g.app[PENDING_KEY])) == {"r1"} and not app_is_current(g.app) and g.app[GENERATION_KEY] == 1


async def test_a_dead_write_is_not_recovered_inside_its_grace_by_the_store_clock(monkeypatch: pytest.MonkeyPatch) -> None:
    store = Clock()
    g = StoreGraph(store)
    await open_write(g, "c", ["r1"])
    store.now += OPEN_WRITE_SECONDS + OPEN_WRITE_GRACE_SECONDS - 1
    host_clock(monkeypatch, 3600, store)
    assert await recover_expired_writes(g, "c") == 0
    store.now += 2
    assert await recover_expired_writes(g, "c") == 1


@pytest.mark.parametrize("before_commit", [False, True])
@pytest.mark.parametrize("delayed", [1, 2, 3])
@pytest.mark.parametrize("step", ["open", "renew"])
async def test_a_lease_write_that_lands_late_is_measured_again_from_when_it_landed(monkeypatch: pytest.MonkeyPatch, step: str, delayed: int,
                                                                                    before_commit: bool) -> None:
    """A deadline read before a write that took minutes is that much shorter than a lease: a live write could be recovered early. The lease is
    measured again from when the write landed; when that renewal is late as well, it is marked `k` and the sweep never judges it by its deadline."""
    monkeypatch.setattr(sg, "STORE_CLOCK_TIMEOUT_SECONDS", 0.05)
    store = Clock()
    g = StoreGraph(store)
    token = await open_write(g, "c", ["r1"]) if step == "renew" else None
    real, slow = g.update_node_fields_if_match, {"left": delayed}

    async def late(*a: object, **k: object) -> object:
        if slow["left"] and before_commit:        # minutes pass between the clock read and the commit
            slow["left"] -= 1
            await asyncio.sleep(0.1)
            store.now += 180
            return await real(*a, **k)
        done = await real(*a, **k)
        if slow["left"]:
            slow["left"] -= 1
            await asyncio.sleep(0.1)
            store.now += 180                      # the write committed, and minutes passed before the writer heard
        return done
    g.update_node_fields_if_match = late  # type: ignore[method-assign]
    token = await open_write(g, "c", ["r1"]) if step == "open" else await renew_write(g, "c", token, ["r1"])
    lease = parse_open_writes(g.app[OPEN_WRITES_KEY])[token]
    if delayed == 1:
        assert lease["u"] == int(store()) + OPEN_WRITE_SECONDS and "k" not in lease
    else:
        assert lease.get("k") == 1
        store.now += OPEN_WRITE_SECONDS + OPEN_WRITE_GRACE_SECONDS + 1
        assert await recover_expired_writes(g, "c") == 0          # not judged by a deadline that could be short: re-leased first
        assert token in parse_open_writes(g.app[OPEN_WRITES_KEY])


@pytest.mark.parametrize("dead", [False, True])
async def test_a_sweep_whose_re_lease_lands_late_marks_it_unknown_again(monkeypatch: pytest.MonkeyPatch, dead: bool) -> None:
    """The sweep re-leases a `k` lease by the store's clock; written minutes after that read, the deadline would be that much short."""
    monkeypatch.setattr(sg, "STORE_CLOCK_TIMEOUT_SECONDS", 0.05)
    store = Clock()
    g = StoreGraph(store)
    a_finished_pass(g.app)
    g.app[OPEN_WRITES_KEY] = json.dumps({"live": {"u": int(store()), "k": 1, "r": ["r1"]},
                                         **({"gone": {"u": int(store()) - DEAD, "r": ["r2"]}} if dead else {})})
    real, slow = g.update_node_fields_if_match, {"left": 1}

    async def late(*a: object, **k: object) -> object:
        if slow["left"]:
            slow["left"] -= 1
            await asyncio.sleep(0.1)
            store.now += 180                      # the re-lease commits minutes after the sweep read the clock
        return await real(*a, **k)
    g.update_node_fields_if_match = late  # type: ignore[method-assign]
    assert await recover_expired_writes(g, "c") == (1 if dead else 0)
    assert parse_open_writes(g.app[OPEN_WRITES_KEY]) == {"live": {"u": int(store()) - 180 + OPEN_WRITE_SECONDS, "k": 1, "r": ["r1"]}}
    store.now += OPEN_WRITE_SECONDS + OPEN_WRITE_GRACE_SECONDS
    assert await recover_expired_writes(g, "c") == 0          # not judged on the short deadline: re-leased again, on time now
    assert parse_open_writes(g.app[OPEN_WRITES_KEY])["live"] == {"u": int(store()) + OPEN_WRITE_SECONDS, "r": ["r1"]}


async def test_a_late_renewal_on_its_last_attempt_is_still_measured_again(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sg, "STORE_CLOCK_TIMEOUT_SECONDS", 0.05)
    store = Clock()
    g = StoreGraph(store)
    token = await open_write(g, "c", ["r1"])
    real, slow = g.update_node_fields_if_match, {"left": 1}

    async def landing_late(*a: object, **k: object) -> object:
        done = await real(*a, **k)
        if slow["left"]:
            slow["left"] -= 1
            await asyncio.sleep(0.1)
            store.now += 180
        return done
    g.update_node_fields_if_match = landing_late  # type: ignore[method-assign]
    assert await renew_write(g, "c", token, ["r1"], attempts=1) == token
    assert parse_open_writes(g.app[OPEN_WRITES_KEY])[token]["u"] == int(store()) + OPEN_WRITE_SECONDS


async def test_a_lease_whose_follow_up_renewal_fails_is_closed_not_left_to_the_sweep(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sg, "STORE_CLOCK_TIMEOUT_SECONDS", 0.05)
    store = Clock()
    g = StoreGraph(store)
    real, slow = g.update_node_fields_if_match, {"left": 1}

    async def landing_late(*a: object, **k: object) -> object:
        done = await real(*a, **k)
        if slow["left"]:
            slow["left"] -= 1
            await asyncio.sleep(0.1)
        return done

    async def no_renewal(*a: object, **k: object) -> object:
        raise RuntimeError("store gone")
    g.update_node_fields_if_match = landing_late  # type: ignore[method-assign]
    monkeypatch.setattr(sg, "renew_write", no_renewal)
    with pytest.raises(RuntimeError):
        await open_write(g, "c", ["r1"])
    assert parse_open_writes(g.app.get(OPEN_WRITES_KEY)) == {} and unclosed_writes(g.app) == 0


async def test_a_renewal_pushes_the_deadline_by_the_store_clock(monkeypatch: pytest.MonkeyPatch) -> None:
    store = Clock()
    g = StoreGraph(store)
    token = await open_write(g, "c", ["r1"])
    store.now += OPEN_WRITE_SECONDS - 10
    host_clock(monkeypatch, -3600, store)                       # the writer's host is behind: its own clock would have set a past deadline
    assert await renew_write(g, "c", token, ["r1"]) == token
    assert parse_open_writes(g.app[OPEN_WRITES_KEY])[token]["u"] == int(store()) + OPEN_WRITE_SECONDS
    host_clock(monkeypatch, 3600, store)
    store.now += OPEN_WRITE_SECONDS - 10
    assert await recover_expired_writes(g, "c") == 0


class Down(StoreGraph):
    """The store's clock fails (`down`) or answers (`up`)."""

    down = True

    async def store_time(self) -> float:
        if self.down:
            raise ConnectionError("down")
        return self.clock()


async def test_a_store_clock_that_cannot_be_read_recovers_nothing_but_never_refuses_the_write() -> None:
    g = Down(Clock())
    token = await open_write(g, "c", ["r1"])                    # the write goes on, with a lease marked unknown
    assert token is not None and unclosed_writes(g.app) == 1 and parse_open_writes(g.app[OPEN_WRITES_KEY])[token]["k"] == 1
    g.app[OPEN_WRITES_KEY] = '{"t":{"u":1}}'                    # long dead by any clock
    assert await store_now(g) is None
    assert await recover_expired_writes(g, "c") == 0 and unclosed_writes(g.app) == 1       # the operator view says why (store_clock_unknown)


async def test_a_store_clock_that_answers_too_late_is_not_trusted(monkeypatch: pytest.MonkeyPatch) -> None:
    """A late answer is a time already that old: a lease judged by it could be recovered while its write still runs."""
    from app.services.permissions import structure_generation

    monkeypatch.setattr(structure_generation, "STORE_CLOCK_TIMEOUT_SECONDS", 0.05)
    g = Down(Clock())
    g.down = False
    real = g.store_time

    async def late() -> float:
        await asyncio.sleep(0.5)
        return await real()
    g.store_time = late  # type: ignore[method-assign]
    assert await store_now(g) is None
    g.app[OPEN_WRITES_KEY] = '{"t":{"u":1}}'
    assert await recover_expired_writes(g, "c") == 0


@pytest.mark.parametrize("behind", [3600, 700])
async def test_a_lease_written_without_the_store_clock_by_a_host_behind_it_is_not_recovered_while_live(monkeypatch: pytest.MonkeyPatch, behind: int) -> None:
    store = Clock()
    g = Down(store)
    a_finished_pass(g.app)
    host_clock(monkeypatch, -behind, store)                     # the writer's host is behind the store
    token = await open_write(g, "c", ["r1"])                    # clock down: local time minus `behind` plus a lease is a deadline already past
    assert parse_open_writes(g.app[OPEN_WRITES_KEY])[token]["u"] < store() and not app_is_current(g.app)
    g.down = False                                              # the sweep reads the store's clock
    assert await recover_expired_writes(g, "c") == 0            # not recovered: the connector stays not current
    assert unclosed_writes(g.app) == 1 and not app_is_current(g.app)
    store.now += OPEN_WRITE_SECONDS - 10                        # the write is still running: it renews, the clock back
    assert await renew_write(g, "c", token, ["r1"]) == token
    assert await recover_expired_writes(g, "c") == 0 and unclosed_writes(g.app) == 1
    store.now += OPEN_WRITE_SECONDS - 10
    assert await recover_expired_writes(g, "c") == 0
    store.now += DEAD                                           # it dies: still recovered, by a lease the store measured
    assert await recover_expired_writes(g, "c") == 1 and set(parse(g.app[PENDING_KEY])) == {"r1"}


async def test_a_renewal_that_reads_the_store_clock_clears_the_unknown_mark() -> None:
    g = Down(Clock())
    token = await open_write(g, "c", ["r1"])
    assert parse_open_writes(g.app[OPEN_WRITES_KEY])[token]["k"] == 1
    g.down = False
    assert await renew_write(g, "c", token, ["r1"]) == token
    assert "k" not in parse_open_writes(g.app[OPEN_WRITES_KEY])[token]


async def test_a_renewal_with_the_store_clock_down_never_shortens_the_lease(monkeypatch: pytest.MonkeyPatch) -> None:
    store = Clock()
    g = Down(store)
    g.down = False
    token = await open_write(g, "c", ["r1"])
    before = parse_open_writes(g.app[OPEN_WRITES_KEY])[token]["u"]
    g.down = True
    host_clock(monkeypatch, -3600, store)                       # a host far behind the store
    assert await renew_write(g, "c", token, ["r1"]) == token
    entry = parse_open_writes(g.app[OPEN_WRITES_KEY])[token]
    assert entry["u"] == before and entry["k"] == 1
    g.down = False
    assert await recover_expired_writes(g, "c") == 0 and unclosed_writes(g.app) == 1


async def test_a_provider_without_a_store_clock_reads_as_unreadable() -> None:
    class NoClock(Graph):
        async def store_time(self) -> float:
            raise NotImplementedError

    for g in (NoClock(), object()):                             # raises NotImplementedError, or defines nothing
        assert await store_now(g) is None
