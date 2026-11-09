# ruff: noqa: ANN401
"""A write is never refused for contention on the app document; a write that outlives its lease reopens it instead of landing unfenced; the
operator's close takes the lease list with it.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from app.services.permissions.currency import app_is_current
from app.services.permissions.pending import parse
from app.services.permissions.structure_generation import (
    CLOSED_KEY,
    GENERATION_KEY,
    OPEN_WRITE_GRACE_SECONDS,
    OPEN_WRITE_SECONDS,
    OPEN_WRITES_KEY,
    OPENED_KEY,
    PENDING_KEY,
    UNSAFE_KEY,
    close_write,
    leaving_vrid,
    open_write,
    parse_open_writes,
    recover_expired_writes,
    renew_write,
    unclosed_writes,
)

from .model import Mutation as M
from .rewrite_kit import Env
from .scenario import WorldStore
from .stamp_world import ORG
from .test_open_write_lease import DEAD, Clock, Graph, a_finished_pass

W = WorldStore()


@pytest.fixture(autouse=True)
def _writers_on(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ACL_SCOPE_FIELDS", "on")


class Slow(Graph):
    """Every statement takes a moment, so concurrent writers really interleave between their read and their compare-and-set."""

    async def get_document(self, key: str, collection: str, **kw: Any) -> dict | None:
        await asyncio.sleep(0.001)
        return await super().get_document(key, collection, **kw)

    async def update_node_fields_if_match(self, *a: Any) -> bool:
        await asyncio.sleep(0.001)
        return await super().update_node_fields_if_match(*a)


async def test_fifty_writes_at_once_are_none_refused_and_every_one_is_accounted_for() -> None:
    g = Slow()

    async def one(i: int) -> tuple[str | None, bool]:
        token = await open_write(g, "c", [f"r{i}"])            # (raises if refused: the test fails)
        return token, await close_write(g, "c", token)

    done = await asyncio.gather(*(one(i) for i in range(50)))
    unleased = sum(1 for t, _ in done if t is None)
    stuck = sum(1 for _, closed in done if not closed)
    entries = parse_open_writes(g.app.get(OPEN_WRITES_KEY))
    assert g.app[OPENED_KEY] == 50 and unclosed_writes(g.app) == len(entries) + unleased        # nothing counted twice or lost
    assert len(entries) == stuck                              # a lease is left only by a close that could not land
    clock = Clock()
    clock.now = max([e["u"] for e in entries.values()] or [0]) + OPEN_WRITE_GRACE_SECONDS + 1
    await recover_expired_writes(g, "c", clock=clock)
    assert unclosed_writes(g.app) == unleased                 # what could not be leased is the operator's, and is counted


async def test_a_document_that_keeps_changing_counts_the_write_with_the_plain_counter_and_does_not_refuse_it() -> None:
    g = Graph()

    async def always_lost(*a: Any, **kw: Any) -> bool:
        return False

    g.update_node_fields_if_match = always_lost      # type: ignore[method-assign]
    assert await open_write(g, "c", ["r1"], attempts=3) is None
    assert g.app[OPENED_KEY] == 1 and unclosed_writes(g.app) == 1 and OPEN_WRITES_KEY not in g.app
    assert await close_write(g, "c", None)
    assert unclosed_writes(g.app) == 0


async def test_a_close_that_cannot_land_leaves_a_lease_that_the_sweep_closes() -> None:
    clock, g = Clock(), Graph()
    token = await open_write(g, "c", ["r1"], clock=clock)
    real = g.update_node_fields_if_match
    g.update_node_fields_if_match = lambda *a: _false()      # type: ignore[method-assign]
    assert not await close_write(g, "c", token, attempts=2)
    g.update_node_fields_if_match = real                     # type: ignore[method-assign]
    clock.now += DEAD
    assert await recover_expired_writes(g, "c", clock=clock) == 1 and unclosed_writes(g.app) == 0


async def _false() -> bool:
    return False


async def test_a_renewal_pushes_the_deadline_out_and_a_write_past_it_is_not_recovered() -> None:
    clock, g = Clock(), Graph()
    token = await open_write(g, "c", ["r1"], clock=clock)
    clock.now += OPEN_WRITE_SECONDS - 10
    assert await renew_write(g, "c", token, ["r1"], clock=clock) == token
    clock.now += OPEN_WRITE_SECONDS - 10                     # past the first deadline, inside the renewed one
    assert await recover_expired_writes(g, "c", clock=clock) == 0 and unclosed_writes(g.app) == 1


async def test_a_write_recovered_while_it_was_alive_reopens_when_it_renews_and_is_recovered_again_if_it_dies() -> None:
    clock, g = Clock(), Graph()
    a_finished_pass(g.app)
    token = await open_write(g, "c", ["r1"], clock=clock)
    clock.now += DEAD
    assert await recover_expired_writes(g, "c", clock=clock) == 1            # read as dead while it was only slow
    assert unclosed_writes(g.app) == 0 and set(parse(g.app[PENDING_KEY])) == {"r1"}
    fresh = await renew_write(g, "c", token, ["r1"], clock=clock)           # ... it wakes, and renews before the write that matters
    assert fresh and fresh != token and unclosed_writes(g.app) == 1          # counted again: the connector is not current while it lands
    assert not app_is_current(g.app)
    clock.now += DEAD                                                        # it lands and the process is killed before it registers
    gen = g.app[GENERATION_KEY]
    assert await recover_expired_writes(g, "c", clock=clock) == 1
    assert g.app[GENERATION_KEY] == gen + 1 and set(parse(g.app[PENDING_KEY])) == {"r1"} and unclosed_writes(g.app) == 0     # registered again


async def test_leaving_vrid_hands_its_body_a_renewal_that_survives_a_recovery() -> None:
    clock, g = Clock(), Graph()
    g.sharers = [{"_key": "r-c", "connectorId": "c"}]
    cm = leaving_vrid(g, "me", "v")
    write = await cm.__aenter__()
    lease = next(iter(parse_open_writes(g.app[OPEN_WRITES_KEY]).values()))
    clock.now = lease["u"] + OPEN_WRITE_GRACE_SECONDS + 1
    assert await recover_expired_writes(g, "c", clock=clock) == 1            # a body past its lease
    await write.renew()                                                      # ... renews before its write lands: reopened
    assert unclosed_writes(g.app) == 1
    await cm.gen.aclose()                                                    # the body ends: registers its roots again and closes the new lease
    assert unclosed_writes(g.app) == 0 and set(parse(g.app[PENDING_KEY])) == {"r-c"} and UNSAFE_KEY not in g.app


async def test_a_write_that_closes_after_it_was_recovered_marks_the_connector_for_a_full_pass() -> None:
    clock, g = Clock(), Graph()
    a_finished_pass(g.app)
    token = await open_write(g, "c", ["r1"], clock=clock)
    clock.now += DEAD
    await recover_expired_writes(g, "c", clock=clock)
    assert UNSAFE_KEY not in g.app
    assert await close_write(g, "c", token)
    assert g.app[UNSAFE_KEY] and (g.app[OPENED_KEY], g.app[CLOSED_KEY]) == (1, 1)      # counted once, and not trusted


async def test_the_operators_close_takes_the_lease_list_with_it_even_when_it_does_not_parse() -> None:
    env = await Env.make(W.world("golden/confluence_hr"))
    doc = env.graph.app_docs["w:app"]
    await env.change((M("moved", "page:Handbook", "space:ALL", "space:ALL"),))
    doc.update({OPENED_KEY: 2, OPEN_WRITES_KEY: "{not json"})
    assert (await env.backfill.run_connector(ORG, "w:app", close_open_mark=True)).state == "done"
    assert not doc.get(OPEN_WRITES_KEY) and unclosed_writes(doc) == 0
    clock = Clock()
    assert await recover_expired_writes(env.graph, "w:app", clock=clock) == 0           # nothing left to close twice
    assert unclosed_writes(doc) == 0                                                    # (not "closed ahead of opened", which no one can read)
    # and with a readable list: the entry goes too
    t = await open_write(env.graph, "w:app", ["r"], clock=clock)
    assert t and (await env.backfill.run_connector(ORG, "w:app", close_open_mark=True)).state == "done"
    assert not doc.get(OPEN_WRITES_KEY) and unclosed_writes(doc) == 0
    clock.now += DEAD
    assert await recover_expired_writes(env.graph, "w:app", clock=clock) == 0 and unclosed_writes(doc) == 0


async def test_a_back_fill_is_refused_while_another_run_holds_the_connector_and_gives_it_back_when_done() -> None:
    import pytest

    from app.services.permissions.rewrite_lease import (
        REWRITE_LEASE_KEY,
        RewriteLease,
        read_lease,
    )
    from app.services.vector_db.scope_backfill import ConnectorBusy

    env = await Env.make(W.world("golden/confluence_hr"))
    doc = env.graph.app_docs["w:app"]
    from app.services.vector_db.scope_backfill import ScopeBackfill

    backfill = ScopeBackfill(env.graph, env.vdb, env.loaded.locator, env.backfill._log)      # (the real clock: the lease below is running)
    held = RewriteLease(env.graph, "w:app")
    assert await held.acquire()
    before = {k: v for k, v in doc.items() if k != REWRITE_LEASE_KEY}
    with pytest.raises(ConnectorBusy):
        await backfill.run_connector(ORG, "w:app", restart=True)
    assert {k: v for k, v in doc.items() if k != REWRITE_LEASE_KEY} == before          # it touched nothing
    await held.release()
    assert (await backfill.run_connector(ORG, "w:app", restart=True)).state == "done"
    assert read_lease(doc.get(REWRITE_LEASE_KEY)) is None                                # and the next run is not kept out
