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
    STORE_CLOCK_TIMEOUT_SECONDS,
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


def reaching_the_store_late(g: StoreGraph, store: Clock, times: int, *, after: bool = False) -> None:
    """The next ``times`` compare-and-sets of the leases reach the store minutes after the writer read its clock (``after``: they land on time, and
    the writer hears minutes later)."""
    real, left = g.update_node_fields_if_match, {"n": times}

    async def late(*a: object, **k: object) -> object:
        if OPEN_WRITES_KEY not in a[2]:       # (a registration of roots is timed by nothing)
            return await real(*a, **k)
        if left["n"] and not after:
            left["n"] -= 1
            store.now += 180
        done = await real(*a, **k)
        if left["n"] and after:
            left["n"] -= 1
            store.now += 180
        return done
    g.update_node_fields_if_match = late  # type: ignore[method-assign]


@pytest.mark.parametrize("delayed", [1, 2, 3])
@pytest.mark.parametrize("step", ["open", "renew"])
async def test_a_lease_write_that_reaches_the_store_late_is_refused_by_it_and_written_again_from_a_fresh_read(step: str, delayed: int) -> None:
    """A deadline read minutes before its write would be that much shorter than a lease, and a live write could be recovered early. The store
    checks its own clock in the write and refuses it; the writer reads the clock again. No delay leaves a short deadline, and none is marked `k`."""
    store = Clock()
    g = StoreGraph(store)
    token = await open_write(g, "c", ["r1"]) if step == "renew" else None
    reaching_the_store_late(g, store, delayed)
    token = await open_write(g, "c", ["r1"]) if step == "open" else await renew_write(g, "c", token, ["r1"])
    assert parse_open_writes(g.app[OPEN_WRITES_KEY])[token] == {"u": int(store()) + OPEN_WRITE_SECONDS, "r": ["r1"]}


async def test_a_lease_that_landed_late_is_never_left_short_by_a_race_lost_afterwards() -> None:
    """Before, a late write was repaired by a renewal after it, and a renewal that kept losing its race left the short deadline trusted."""
    store = Clock()
    g = StoreGraph(store)
    a_finished_pass(g.app)
    reaching_the_store_late(g, store, 1)
    swaps = {"n": 0}
    real = g.update_node_fields_if_match

    async def others_write_after_the_first(*a: object, **k: object) -> object:
        swaps["n"] += 1
        if swaps["n"] > 1:                         # from then on another writer always gets in first
            return False
        return await real(*a, **k)
    g.update_node_fields_if_match = others_write_after_the_first  # type: ignore[method-assign]
    token = await open_write(g, "c", ["r1"], attempts=5)
    for entry in parse_open_writes(g.app.get(OPEN_WRITES_KEY)).values():
        assert entry.get("k") or entry["u"] >= int(store()) + OPEN_WRITE_SECONDS - STORE_CLOCK_TIMEOUT_SECONDS
    assert token is None and unclosed_writes(g.app) == 1 and not app_is_current(g.app)      # counted on the plain counter: the operator view shows it


async def test_a_lease_written_in_time_keeps_its_deadline_however_late_the_writer_hears() -> None:
    store = Clock()
    g = StoreGraph(store)
    at = int(store())
    reaching_the_store_late(g, store, 1, after=True)
    token = await open_write(g, "c", ["r1"])
    assert parse_open_writes(g.app[OPEN_WRITES_KEY])[token]["u"] == at + OPEN_WRITE_SECONDS      # measured from when it was written, by the store


@pytest.mark.parametrize("dead", [False, True])
async def test_a_sweep_whose_re_lease_reaches_the_store_late_writes_nothing_and_the_next_one_re_leases(dead: bool) -> None:
    """The sweep re-leases a `k` lease by the store's clock; written minutes after that read, the deadline would be that much short."""
    store = Clock()
    g = StoreGraph(store)
    a_finished_pass(g.app)
    before = json.dumps({"live": {"u": int(store()), "k": 1, "r": ["r1"]}, **({"gone": {"u": int(store()) - DEAD, "r": ["r2"]}} if dead else {})})
    g.app[OPEN_WRITES_KEY] = before
    reaching_the_store_late(g, store, 1)
    assert await recover_expired_writes(g, "c") == 0
    assert g.app[OPEN_WRITES_KEY] == before                                   # refused whole: nothing short was written
    assert await recover_expired_writes(g, "c") == (1 if dead else 0)        # the next sweep, on time
    assert parse_open_writes(g.app[OPEN_WRITES_KEY]) == {"live": {"u": int(store()) + OPEN_WRITE_SECONDS, "r": ["r1"]}}


async def test_a_recovery_whose_registration_is_slow_still_closes_the_dead_write_and_re_leases_the_unknown_one(monkeypatch: pytest.MonkeyPatch) -> None:
    """The registration of the dead write's roots commits on its own and can take longer than a clock read may be old: the re-lease written with the
    close is timed after it, so the close is not refused and the dead lease is not left behind."""
    store = Clock()
    g = StoreGraph(store)
    a_finished_pass(g.app)
    g.app[OPEN_WRITES_KEY] = json.dumps({"live": {"u": int(store()), "k": 1, "r": ["r1"]}, "gone": {"u": int(store()) - DEAD, "r": ["r2"]}})
    real = sg.register_pending_roots

    async def slow(*a: object, **k: object) -> object:
        store.now += 10                       # longer than STORE_CLOCK_TIMEOUT_SECONDS
        return await real(*a, **k)
    monkeypatch.setattr(sg, "register_pending_roots", slow)
    assert await recover_expired_writes(g, "c") == 1
    assert parse_open_writes(g.app[OPEN_WRITES_KEY]) == {"live": {"u": int(store()) + OPEN_WRITE_SECONDS, "r": ["r1"]}}
    assert set(parse(g.app[PENDING_KEY])) == {"r2"} and g.app[sg.CLOSED_KEY] == 1


async def test_a_late_renewal_on_its_last_attempt_leaves_the_old_lease_running() -> None:
    store = Clock()
    g = StoreGraph(store)
    token = await open_write(g, "c", ["r1"])
    lease = parse_open_writes(g.app[OPEN_WRITES_KEY])[token]
    reaching_the_store_late(g, store, 1)
    assert await renew_write(g, "c", token, ["r1"], attempts=1) == token
    assert parse_open_writes(g.app[OPEN_WRITES_KEY])[token] == lease


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
