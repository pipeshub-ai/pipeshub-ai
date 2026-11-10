"""The fence of a write never keeps a rewrite from finishing, and it is a value that every connector can compare.

A virtual record shared by two connectors carries one `scopeFence`. It is a time taken before the graph was read, not a connector's generation
(two counters that nothing orders): a rewrite of connector B at generation 1 would otherwise be refused for ever by the fence connector A left
at generation 4. A fence above this host's time (another host's clock runs ahead, or an earlier run left it) is followed when it is a plausible
time: the next round reads the graph again, after that write, and writes above it. A value that is no time is not followed: the root stays
pending and the log says why.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

import pytest

from app.services.vector_db import scope_rewrite
from app.services.vector_db.scope_rewrite import (
    MAX_FENCE_SKEW_MICROS,
    RewriteIncomplete,
)

from .model import Mutation as M
from .rewrite_kit import Env, Log, Tap
from .scenario import WorldStore
from .stamp_world import ORG

MOVE = M("moved", "page:Handbook", "space:ALL", "space:ALL")
HOUR = 3600 * 10**6


@pytest.fixture(autouse=True)
def _writers_on(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ACL_SCOPE_FIELDS", "on")


def now_us() -> int:
    return scope_rewrite._micros(scope_rewrite._clock())


async def hr(fence: int | None = None) -> Env:
    env = await Env.make(WorldStore().world("golden/confluence_hr"), settle_seconds=0.2)
    if fence is not None:
        for p in env.vdb._points(env.loaded.collection).values():
            p.payload["scopeFence"] = fence       # a writer was here with this fence
    return env


def clock_at(monkeypatch: pytest.MonkeyPatch, micros: int) -> None:
    monkeypatch.setattr(scope_rewrite, "_clock", lambda: datetime.fromtimestamp(micros / 1_000_000, UTC))


MINUTE = 60 * 10**6


@pytest.mark.parametrize("ahead", [MINUTE, 9 * MINUTE])
async def test_a_fence_a_few_minutes_above_the_stores_time_does_not_keep_a_rewrite_pending(ahead: int) -> None:
    env = await hr(now_us() + ahead)
    await env.change((MOVE,))
    await env.settle()
    seen = Log()
    await env.audit(seen)
    assert not await env.problems() and not seen.leaks and not seen.pending_admits


async def test_a_host_whose_clock_runs_minutes_behind_still_finishes(monkeypatch: pytest.MonkeyPatch) -> None:
    env = await hr()
    # a host five minutes ahead wrote the points; this one runs four minutes behind the real time: nine minutes apart, inside the window
    for p in env.vdb._points(env.loaded.collection).values():
        p.payload["scopeFence"] = now_us() + 5 * MINUTE
    clock_at(monkeypatch, now_us() - 4 * MINUTE)
    await env.change((MOVE,))
    await env.settle()
    assert not await env.problems()


@pytest.mark.parametrize("fence", [HOUR, 10**18, 2**53 + 2**20, 2**53 - 1, 2**53 - 5000])
async def test_a_fence_that_is_further_ahead_than_the_window_is_not_followed_and_the_root_stays(fence: int, caplog: pytest.LogCaptureFixture) -> None:
    env = await hr(fence if fence > HOUR else now_us() + fence)
    await env.change((MOVE,))
    with caplog.at_level(logging.ERROR), pytest.raises(RewriteIncomplete, match="no plausible time"):
        await env.job.run_connector(ORG, "w:app")
    assert env.pending() and any("not a plausible time" in r.getMessage() for r in caplog.records)
    seen = Log()
    await env.audit(seen)
    assert not seen.leaks and not seen.pending_admits
    for p in env.vdb._points(env.loaded.collection).values():      # repaired (indexed again): the next run finishes
        p.payload.pop("scopeFence")
    await env.settle()
    assert not await env.problems()


async def test_the_edge_of_the_window_is_followed_and_one_past_it_is_not(monkeypatch: pytest.MonkeyPatch) -> None:
    now = now_us()
    clock_at(monkeypatch, now)
    env = await hr(now + MAX_FENCE_SKEW_MICROS + 1)
    await env.change((MOVE,))
    with pytest.raises(RewriteIncomplete):
        await env.job.run_connector(ORG, "w:app")
    for p in env.vdb._points(env.loaded.collection).values():
        p.payload["scopeFence"] = now + MAX_FENCE_SKEW_MICROS
    await env.settle()
    assert not await env.problems()


async def test_a_follow_writes_a_value_of_its_own_above_the_fence_it_found(monkeypatch: pytest.MonkeyPatch) -> None:
    """Two followers that saw the same fence must not write the same value (stores accept an equal one, so the last would win whatever it read)."""
    ahead = now_us() + 5 * MINUTE
    env = await hr(ahead)
    await env.change((MOVE,))
    monkeypatch.setattr(scope_rewrite.secrets, "randbelow", lambda _n: 1234)
    await env.settle()
    fences = {p.payload["scopeFence"] for p in env.vdb._points(env.loaded.collection).values()} - {ahead}
    assert fences and all(f >= ahead + 1 + 1234 for f in fences), (ahead, fences)


async def test_a_stale_writer_is_still_refused_by_a_fence_that_is_a_time(monkeypatch: pytest.MonkeyPatch) -> None:
    """The fence is the time the graph was read at: of two writers, the one that read later wins, whatever the order they write in."""
    env = await hr()
    pid = env.loaded.points["w:v-page:Handbook"][0]
    later = now_us() + 5_000_000
    await env.vdb.update_payload_by_ids(env.loaded.collection, [pid], {"recordGroupIds": ["w:space:HR"]}, org_id=ORG, fence=later)
    await env.vdb.update_payload_by_ids(env.loaded.collection, [pid], {"recordGroupIds": ["old"]}, org_id=ORG, fence=later - 1)
    got = (await env.vdb.retrieve_points(env.loaded.collection, [pid]))[0].payload
    assert got["recordGroupIds"] == ["w:space:HR"] and got["scopeFence"] == later


async def test_a_generation_that_moves_under_a_page_costs_no_round_and_does_not_wedge_it() -> None:
    """A busy connector moves its generation again and again: each move is a read of the graph again, not a round, so a page that makes progress
    ends, and a connector that never stops moving ends the run (the root stays) instead of wedging it."""
    env = await hr()
    await env.change((MOVE,))
    bumps = {"n": 0}

    async def busy(name: str) -> None:
        if name == "retrieve_points" and bumps["n"] < 8:      # between the graph read and the write: more moves than a page has rounds
            bumps["n"] += 1
            await env.graph.increment_node_field("w:app", "apps", "scopeProjectionGeneration")

    Tap(env, busy)
    res = await env.job.run_connector(ORG, "w:app")
    assert bumps["n"] == 8 and res.state == "done" and not env.pending()

    env = await hr()
    await env.change((MOVE,))

    async def never_still(name: str) -> None:
        if name == "retrieve_points":
            await env.graph.increment_node_field("w:app", "apps", "scopeProjectionGeneration")

    Tap(env, never_still)
    with pytest.raises(RewriteIncomplete, match="kept changing"):
        await env.job.run_connector(ORG, "w:app")
    assert env.pending()
    seen = Log()
    await env.audit(seen)
    assert not seen.leaks and not seen.pending_admits


async def test_a_page_that_needs_two_real_rounds_after_several_moves_still_ends() -> None:
    """Moves cost no round: four moves (more than the three rounds a page has) and then a write the store acknowledged and lost, so that a second
    real round is needed, must still end done."""
    env = await hr()
    await env.change((MOVE,))
    state = {"moves": 0, "lost": False}

    async def before(name: str) -> str | None:
        if name == "retrieve_points" and state["moves"] < 4:
            state["moves"] += 1
            await env.graph.increment_node_field("w:app", "apps", "scopeProjectionGeneration")
        if name == "update_payloads_by_ids" and state["moves"] >= 4 and not state["lost"]:
            state["lost"] = True
            return "drop"
        return None

    Tap(env, before)
    res = await env.job.run_connector(ORG, "w:app")
    assert state["moves"] == 4 and state["lost"] and res.state == "done" and not env.pending()
