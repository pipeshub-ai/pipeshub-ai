# ruff: noqa: ANN401
"""One rewriter per connector: a second one is refused while the lease runs, takes over when it has run out, and a held-up one that comes back
after the takeover writes nothing. Held to the oracle: no stale field in the store (no over-admit), and the roots come off (no under-admit).
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from app.services.permissions.rewrite_lease import (
    REWRITE_LEASE_KEY,
    REWRITE_LEASE_SECONDS,
    LeaseLost,
    RewriteLease,
    read_lease,
)
from app.services.vector_db.scope_rewrite import ScopeRewriteJob

from .model import Mutation as M
from .rewrite_kit import Env, Log, Tap
from .scenario import WorldStore
from .stamp_world import ORG
from .test_open_write_lease import Clock, Graph

W = WorldStore()
MOVE = M("moved", "page:Handbook", "space:ALL", "space:ALL")


@pytest.fixture(autouse=True)
def _writers_on(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ACL_SCOPE_FIELDS", "on")


async def test_a_second_holder_is_refused_until_the_lease_runs_out_and_then_takes_over() -> None:
    clock, g = Clock(), Graph()
    a, b = RewriteLease(g, "c", clock=clock), RewriteLease(g, "c", clock=clock)
    assert await a.acquire() and not await b.acquire()
    clock.now += REWRITE_LEASE_SECONDS - 1
    assert not await b.acquire()
    clock.now += 2
    assert await b.acquire()
    with pytest.raises(LeaseLost):
        await a.renew()                                  # the one that was held up finds it is no longer the holder
    assert not a.holds(g.app) and b.holds(g.app)
    await a.release()                                    # (only the holder gives it back)
    assert b.holds(g.app)
    await b.release()
    assert read_lease(g.app[REWRITE_LEASE_KEY]) is None


async def test_the_holder_renews_and_a_lease_that_does_not_parse_is_taken_over() -> None:
    clock, g = Clock(), Graph(**{REWRITE_LEASE_KEY: "{nope"})
    a = RewriteLease(g, "c", clock=clock)
    assert await a.acquire()
    clock.now += REWRITE_LEASE_SECONDS - 1
    await a.renew()
    clock.now += REWRITE_LEASE_SECONDS - 1
    assert not await RewriteLease(g, "c", clock=clock).acquire()       # renewed, so still held


async def test_a_lease_lost_to_a_race_on_the_document_is_not_acquired() -> None:
    clock, g = Clock(), Graph()
    other = RewriteLease(g, "c", clock=clock)
    g.lost_race = lambda app: app.update({REWRITE_LEASE_KEY: other._value()})
    assert not await RewriteLease(g, "c", clock=clock).acquire()


async def test_an_app_write_loses_the_race_when_the_lease_changes_between_the_read_and_the_swap() -> None:
    clock, g = Clock(), Graph()
    a, other = RewriteLease(g, "c", clock=clock), RewriteLease(g, "c", clock=clock)
    assert await a.acquire()
    g.lost_race = lambda app: app.update({REWRITE_LEASE_KEY: other._value()})      # taken over after `a` read it, before its swap
    with pytest.raises(LeaseLost):
        await a.update_app({"x": 1})
    assert "x" not in g.app and other.holds(g.app)                                  # the holder that lost writes nothing
    # the same holder's own deadline moving under it is no loss: the swap is read again and lands
    clock2, g2 = Clock(), Graph()
    b = RewriteLease(g2, "c", clock=clock2)
    assert await b.acquire()
    clock2.now += 5
    g2.lost_race = lambda app: app.update({REWRITE_LEASE_KEY: b._value()})
    await b.update_app({"x": 1})
    assert g2.app["x"] == 1 and b.holds(g2.app)


async def test_a_busy_connector_is_neither_read_nor_written() -> None:
    env = await Env.make(W.world("golden/confluence_hr"))
    await env.change((MOVE,))
    env.job = ScopeRewriteJob(env.graph, env.vdb, env.loaded.locator, settle_seconds=0.2)      # (the real clock: the lease below is running)
    held = RewriteLease(env.graph, "w:app")
    assert await held.acquire()
    writes: list[str] = []
    Tap(env, _nothing, lambda name: _note(writes, name) if name == "update_payloads_by_ids" else _nothing(name))
    res = await env.job.run_connector(ORG, "w:app")
    assert res.state == "busy" and not writes and env.pending()          # under-admit guard: the roots are still there for the holder
    await held.release()
    await env.settle()                                                    # recovery: once it is free the same job finishes
    assert not env.pending() and not await env.problems()


async def test_a_finished_run_gives_the_lease_back_and_a_failed_one_too(monkeypatch: pytest.MonkeyPatch) -> None:
    env = await Env.make(W.world("golden/confluence_hr"))
    await env.change((MOVE,))
    await env.settle()
    assert read_lease(env.graph.app_docs["w:app"].get(REWRITE_LEASE_KEY)) is None
    await env.change((M("moved", "page:Handbook", "space:HR", "space:HR"),))

    async def broken(*a: Any, **k: Any) -> Any:
        raise RuntimeError("graph down")

    monkeypatch.setattr(env.graph.stamps, "subtree_vrids", broken)
    with pytest.raises(Exception, match="left pending|graph down"):
        await env.job.run_connector(ORG, "w:app")
    assert read_lease(env.graph.app_docs["w:app"].get(REWRITE_LEASE_KEY)) is None


async def test_a_killed_rewriter_holds_the_connector_only_until_its_lease_runs_out() -> None:
    clock = Clock()
    env = await Env.make(W.world("golden/confluence_hr"))
    env.job = ScopeRewriteJob(env.graph, env.vdb, env.loaded.locator, settle_seconds=0.2, clock=clock)
    await env.change((MOVE,))

    class Killed(BaseException):
        pass

    async def die(name: str) -> str | None:
        if name == "retrieve_points":
            raise Killed
        return None

    tap = Tap(env, die)
    with pytest.raises(Killed):
        await env.job.run_connector(ORG, "w:app")
    tap.remove()
    assert env.pending()                                                  # nothing came off
    assert (await env.job.run_connector(ORG, "w:app")).state == "busy"   # its lease is still running ...
    clock.now += REWRITE_LEASE_SECONDS + 1
    assert (await env.job.run_connector(ORG, "w:app")).state == "done"   # ... and then the next run takes over
    assert not env.pending() and not await env.problems()


async def _held_up(clock: Clock, takeover: Any) -> tuple[Env, Any, list[str], Log]:
    """A reads the graph and is held up before its first request, its lease runs out and `takeover` runs (another holder), then A goes on."""
    env = await Env.make(W.world("golden/confluence_hr"))
    env.job = a = ScopeRewriteJob(env.graph, env.vdb, env.loaded.locator, settle_seconds=0.2, clock=clock)
    await env.change((MOVE,))
    held, a_writes, seen = {"done": False}, [], Log()

    async def hold(name: str) -> str | None:
        if name == "retrieve_points" and not held["done"]:
            held["done"] = True
            clock.now += REWRITE_LEASE_SECONDS + 1
            await takeover(env)
        return None

    async def after(name: str) -> None:
        if name == "update_payloads_by_ids":
            a_writes.append(name)
            await env.audit(seen)

    tap = Tap(env, hold, after)
    try:
        res = await a.run_connector(ORG, "w:app")
    finally:
        tap.remove()
    assert held["done"]
    return env, res, a_writes, seen


async def test_a_held_up_rewriter_whose_lease_was_taken_sends_nothing() -> None:
    """The lease layer alone: the new holder has not written anything yet, so only the lease says A must stop (the generation did not move)."""
    clock, taken = Clock(), {}

    async def takeover(env: Env) -> None:
        taken["lease"] = RewriteLease(env.graph, "w:app", clock=clock)
        assert await taken["lease"].acquire()

    env, res, a_writes, seen = await _held_up(clock, takeover)
    assert res.state == "busy" and not a_writes and env.pending()          # nothing sent; the roots stay for the new holder
    assert not seen.leaks and not seen.pending_admits
    await taken["lease"].release()
    await env.settle()                                                      # recovery: the next run finishes and everything is exact
    assert not env.pending() and not await env.problems()


async def test_a_held_up_rewriter_after_a_complete_takeover_leaves_the_store_exact() -> None:
    """The new holder (its own loop, as another process) finishes first. Whatever A then does, the store never holds older fields than the graph's."""
    clock = Clock()

    async def takeover(env: Env) -> None:
        b = ScopeRewriteJob(env.graph, env.vdb, env.loaded.locator, settle_seconds=0.5, clock=clock)

        async def b_runs() -> Any:
            return await b.run_connector(ORG, "w:app")

        assert (await asyncio.to_thread(asyncio.run, b_runs())).state == "done"

    env, res, a_writes, seen = await _held_up(clock, takeover)
    assert res.state in ("done", "busy")
    assert not seen.leaks and not seen.pending_admits and not env.pending() and not await env.problems()


async def _nothing(_: str) -> None:
    return None


async def _note(into: list[str], name: str) -> None:
    into.append(name)
