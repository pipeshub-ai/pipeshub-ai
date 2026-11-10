# ruff: noqa: ANN401
"""A write that opens a connector is leased: a process killed inside `leaving_vrid` leaves a lease that runs out, and `recover_expired_writes`
registers the roots the write would have registered and closes it, with no operator. Held both ways: a dead write is recovered (no connector stuck
not current), a live one is not (no connector current while a write is running), and a late closer is not counted twice.
"""

from __future__ import annotations

import asyncio
import json
import time
from typing import Any

import pytest

from app.services.permissions.currency import app_is_current
from app.services.permissions.pending import MAX_PENDING_ROOTS, parse
from app.services.permissions.scope_fields import (
    SCOPE_FIELDS_VERSION,
    current_scope_generation,
)
from app.services.permissions.structure_generation import (
    CLOSED_KEY,
    GENERATION_KEY,
    OPEN_WRITE_GRACE_SECONDS,
    OPEN_WRITE_SECONDS,
    OPEN_WRITES_KEY,
    OPENED_KEY,
    PENDING_KEY,
    UNSAFE_KEY,
    OpenWritesUnreadable,
    ScopeGenerationError,
    close_write,
    leaving_vrid,
    open_write,
    parse_open_writes,
    recover_expired_writes,
    unclosed_writes,
)
from app.services.vector_db.scope_backfill import BACKFILL_KEYS, DONE

DEAD = OPEN_WRITE_SECONDS + OPEN_WRITE_GRACE_SECONDS + 1      # seconds after the open at which the write is read as dead


class Clock:
    def __init__(self) -> None:
        self.now = 1_000_000.0

    def __call__(self) -> float:
        return self.now


class Graph:
    """One app document with the compare-and-set of the real providers; `lost_race` runs just before a swap (another writer got in first)."""

    def __init__(self, **fields: Any) -> None:
        self.app: dict[str, Any] = {GENERATION_KEY: 0, **fields}
        self.lost_race: Any = None
        self.sharers: list[dict] = []
        self.swap_fails_after: int | None = None       # swaps allowed before every later one raises

    async def store_time(self) -> float:
        return time.time()      # the store's clock; a test that needs its own passes a `clock`

    async def get_document(self, key: str, collection: str, **kw: Any) -> dict | None:
        return dict(self.app)

    async def get_nodes_by_field_in(self, *a: Any, **kw: Any) -> list[dict]:
        return self.sharers

    async def update_node(self, key: str, collection: str, updates: dict, *a: Any) -> bool:
        self.app.update(updates)
        return True

    async def increment_node_field(self, key: str, collection: str, field: str, *a: Any) -> int:
        self.app[field] = int(self.app.get(field) or 0) + 1
        return self.app[field]

    async def update_node_fields_if_match(self, key: str, collection: str, updates: dict, expected: dict, *a: Any) -> bool:
        if self.lost_race is not None:
            hook, self.lost_race = self.lost_race, None
            hook(self.app)
        if self.swap_fails_after is not None:
            if self.swap_fails_after <= 0:
                raise RuntimeError("write failed")
            self.swap_fails_after -= 1
        if any(self.app.get(f) != v for f, v in expected.items()):
            return False
        self.app.update(updates)
        return True


def a_finished_pass(app: dict) -> None:
    app.update({BACKFILL_KEYS["state"]: DONE, BACKFILL_KEYS["version"]: SCOPE_FIELDS_VERSION, BACKFILL_KEYS["generation"]: current_scope_generation(),
                BACKFILL_KEYS["structure_generation"]: app.get(GENERATION_KEY, 0), BACKFILL_KEYS["unsafe"]: app.get(UNSAFE_KEY)})


async def test_a_write_that_finishes_leaves_no_lease_and_is_counted_once() -> None:
    g = Graph()
    token = await open_write(g, "c", ["r1"])
    assert unclosed_writes(g.app) == 1 and list(parse_open_writes(g.app[OPEN_WRITES_KEY])) == [token]
    assert await close_write(g, "c", token)
    assert unclosed_writes(g.app) == 0 and not g.app[OPEN_WRITES_KEY] and g.app[OPENED_KEY] == g.app[CLOSED_KEY] == 1
    assert await close_write(g, "c", token) and g.app[CLOSED_KEY] == 1 and UNSAFE_KEY in g.app      # a repeated close counts nothing; it is not trusted either


async def test_a_killed_write_is_not_current_until_its_lease_runs_out_and_then_it_is_recovered_with_its_roots() -> None:
    clock, g = Clock(), Graph()
    a_finished_pass(g.app)
    assert app_is_current(g.app)
    await open_write(g, "c", ["r1", "r2"], clock=clock)                  # ... and the process dies: nothing closes it
    assert not app_is_current(g.app) and unclosed_writes(g.app) == 1
    assert await recover_expired_writes(g, "c", clock=clock) == 0       # a write still inside its lease is not touched
    assert unclosed_writes(g.app) == 1
    clock.now += OPEN_WRITE_SECONDS + OPEN_WRITE_GRACE_SECONDS - 1
    assert await recover_expired_writes(g, "c", clock=clock) == 0       # (and not inside the grace either)
    clock.now += 2
    assert await recover_expired_writes(g, "c", clock=clock) == 1
    assert unclosed_writes(g.app) == 0 and not g.app[OPEN_WRITES_KEY]
    assert set(parse(g.app[PENDING_KEY])) == {"r1", "r2"} and g.app[GENERATION_KEY] == 1
    assert not app_is_current(g.app)                                     # the generation moved: the roots are hidden until they are rewritten
    assert UNSAFE_KEY not in g.app                                       # the roots were known: no full pass


async def test_a_killed_write_without_roots_is_closed_with_a_mark_that_a_full_pass_covers() -> None:
    clock, g = Clock(), Graph()
    await open_write(g, "c", None, clock=clock)
    clock.now += DEAD
    assert await recover_expired_writes(g, "c", clock=clock) == 1
    assert unclosed_writes(g.app) == 0 and g.app[UNSAFE_KEY] and PENDING_KEY not in g.app
    a_finished_pass(g.app)                                               # a pass that read the mark covers it
    assert app_is_current(g.app)
    g.app[UNSAFE_KEY] = 7
    assert not app_is_current(g.app)


async def test_too_many_roots_are_not_kept_and_the_recovery_marks_the_connector() -> None:
    clock, g = Clock(), Graph()
    await open_write(g, "c", [f"r{i}" for i in range(MAX_PENDING_ROOTS + 1)], clock=clock)
    assert all("r" not in e for e in parse_open_writes(g.app[OPEN_WRITES_KEY]).values())
    clock.now += DEAD
    assert await recover_expired_writes(g, "c", clock=clock) == 1 and g.app[UNSAFE_KEY] and PENDING_KEY not in g.app


async def test_a_write_that_finishes_after_it_was_recovered_is_not_counted_again_and_is_not_trusted() -> None:
    clock, g = Clock(), Graph()
    token = await open_write(g, "c", ["r1"], clock=clock)
    clock.now += DEAD
    assert await recover_expired_writes(g, "c", clock=clock) == 1
    assert await close_write(g, "c", token)                              # (it was only slow)
    assert (g.app[OPENED_KEY], g.app[CLOSED_KEY]) == (1, 1) and unclosed_writes(g.app) == 0 and g.app[UNSAFE_KEY]      # its landing may be after the registration


async def test_only_the_dead_writes_are_closed_and_a_live_one_keeps_the_connector_not_current() -> None:
    clock, g = Clock(), Graph()
    await open_write(g, "c", ["old"], clock=clock)
    clock.now += DEAD
    live = await open_write(g, "c", ["new"], clock=clock)
    assert await recover_expired_writes(g, "c", clock=clock) == 1
    assert unclosed_writes(g.app) == 1 and list(parse_open_writes(g.app[OPEN_WRITES_KEY])) == [live]
    assert set(parse(g.app[PENDING_KEY])) == {"old"}


async def test_a_counter_that_is_not_a_whole_number_keeps_the_dead_leases_while_a_k_lease_is_re_leased() -> None:
    clock, g = Clock(), Graph()
    await open_write(g, "c", ["old"], clock=clock)
    clock.now += DEAD
    held = parse_open_writes(g.app[OPEN_WRITES_KEY])
    held["k-token"] = {"u": 1, "k": 1}
    g.app[OPEN_WRITES_KEY] = json.dumps(held)
    for bad in (True, 1.5, -1, "3"):       # a counter that is not a whole number: nothing is closed, and no record of the roots is dropped
        g.app[CLOSED_KEY] = bad
        assert await recover_expired_writes(g, "c", clock=clock) == 0
        assert set(parse_open_writes(g.app[OPEN_WRITES_KEY])) == set(held) and "k" not in parse_open_writes(g.app[OPEN_WRITES_KEY])["k-token"]
        g.app[OPEN_WRITES_KEY] = json.dumps(held)
    g.app[CLOSED_KEY] = 0       # the counter is fixed: the roots are still there to register
    clock.now += OPEN_WRITE_SECONDS + OPEN_WRITE_GRACE_SECONDS + 1
    assert await recover_expired_writes(g, "c", clock=clock) >= 1 and "old" in parse(g.app[PENDING_KEY])


async def test_a_crash_between_the_registration_and_the_close_repeats_the_registration_and_loses_nothing() -> None:
    clock, g = Clock(), Graph()
    await open_write(g, "c", ["r1"], clock=clock)
    clock.now += DEAD
    g.swap_fails_after = 1                                               # the registration lands, the close does not
    with pytest.raises(RuntimeError):
        await recover_expired_writes(g, "c", clock=clock)
    assert set(parse(g.app[PENDING_KEY])) == {"r1"} and unclosed_writes(g.app) == 1       # registered, not yet closed
    g.swap_fails_after = None
    assert await recover_expired_writes(g, "c", clock=clock) == 1
    assert unclosed_writes(g.app) == 0 and set(parse(g.app[PENDING_KEY])) == {"r1"} and g.app[GENERATION_KEY] == 2


async def test_a_recovery_that_loses_the_race_closes_nothing() -> None:
    clock, g = Clock(), Graph()
    await open_write(g, "c", ["r1"], clock=clock)
    clock.now += DEAD
    g.lost_race = lambda app: app.update({CLOSED_KEY: 5})              # another sweep changed the counter first
    assert await recover_expired_writes(g, "c", clock=clock) == 0


async def test_opens_and_closes_at_once_lose_no_write() -> None:
    g = Graph()
    tokens = await asyncio.gather(*(open_write(g, "c", [f"r{i}"]) for i in range(10)))
    assert g.app[OPENED_KEY] == 10 and len(parse_open_writes(g.app[OPEN_WRITES_KEY])) == 10
    await asyncio.gather(*(close_write(g, "c", t) for t in tokens[:6]))
    assert unclosed_writes(g.app) == 4 and set(parse_open_writes(g.app[OPEN_WRITES_KEY])) == set(tokens[6:])


async def test_a_value_that_does_not_parse_is_never_read_as_none() -> None:
    clock, g = Clock(), Graph(**{OPEN_WRITES_KEY: "{not json"})
    assert await open_write(g, "c", ["r1"]) is None and unclosed_writes(g.app) == 1       # the plain counter, as before; no lease to recover by
    assert await close_write(g, "c", None) and unclosed_writes(g.app) == 0
    clock.now += DEAD
    with pytest.raises(OpenWritesUnreadable):
        await recover_expired_writes(g, "c", clock=clock)
    for bad in ('{"t": {"u": "soon"}}', '{"t": {"u": 5, "r": "x"}}', "[]", "3"):
        with pytest.raises(OpenWritesUnreadable):
            parse_open_writes(bad)


async def test_no_such_connector_refuses_the_write() -> None:
    class Missing(Graph):
        async def get_document(self, *a: Any, **kw: Any) -> None:
            return None

    with pytest.raises(ScopeGenerationError):
        await open_write(Missing(), "c", ["r1"])


async def test_a_process_killed_inside_leaving_vrid_is_recovered_by_the_sweep_without_an_operator() -> None:
    clock, g = Clock(), Graph()
    g.sharers = [{"_key": "r-c", "connectorId": "c"}]
    a_finished_pass(g.app)
    cm = leaving_vrid(g, "me", "v")
    await cm.__aenter__()                                                # the write starts ... and the process is killed: __aexit__ never runs
    assert not app_is_current(g.app) and unclosed_writes(g.app) == 1
    lease = next(iter(parse_open_writes(g.app[OPEN_WRITES_KEY]).values()))
    assert lease["r"] == ["r-c"]
    clock.now = lease["u"] + OPEN_WRITE_GRACE_SECONDS + 1
    assert await recover_expired_writes(g, "c", clock=clock) == 1
    assert unclosed_writes(g.app) == 0 and set(parse(g.app[PENDING_KEY])) == {"r-c"}
    await cm.gen.aclose()                                                # the write was only slow: it registers its roots again and finds its lease gone
    assert unclosed_writes(g.app) == 0 and (g.app[OPENED_KEY], g.app[CLOSED_KEY]) == (1, 1) and set(parse(g.app[PENDING_KEY])) == {"r-c"}


async def test_leaving_vrid_that_finishes_leaves_the_connector_closed_with_its_roots_registered() -> None:
    g = Graph()
    g.sharers = [{"_key": "r-c", "connectorId": "c"}]
    async with leaving_vrid(g, "me", "v"):
        assert unclosed_writes(g.app) == 1
    assert unclosed_writes(g.app) == 0 and not g.app[OPEN_WRITES_KEY] and set(parse(g.app[PENDING_KEY])) == {"r-c"}
