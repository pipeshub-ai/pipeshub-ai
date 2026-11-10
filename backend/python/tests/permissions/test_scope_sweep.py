# ruff: noqa: ANN401
"""The sweep closes what a dead process left and finishes what is pending, per connector, without one connector's failure reaching another.

Held to the oracle: after the sweep the store is exact (no stale field admitted) and the connector is current again (no under-admit left behind);
before it, a connector with a write in flight or a root pending is not current.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from app.services.permissions.structure_generation import (
    GENERATION_KEY,
    OPEN_WRITE_GRACE_SECONDS,
    OPEN_WRITE_SECONDS,
    OPEN_WRITES_KEY,
    OPENED_KEY,
    PENDING_KEY,
    open_write,
    unclosed_writes,
)
from app.services.vector_db.scope_backfill import BACKFILL_KEYS
from app.services.vector_db.scope_sweep import (
    BACKOFF_FIRST_SECONDS,
    BACKOFF_MAX_SECONDS,
    SWEEP_KEY,
    ScopeSweep,
    read_state,
)

from .model import Mutation as M
from .rewrite_kit import Env, Log, Tap
from .scenario import WorldStore
from .stamp_world import ORG
from .test_open_write_lease import Clock

W = WorldStore()
MOVE = M("moved", "page:Handbook", "space:ALL", "space:ALL")
APP = "w:app"
DEAD = OPEN_WRITE_SECONDS + OPEN_WRITE_GRACE_SECONDS + 1


@pytest.fixture(autouse=True)
def _writers_on(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ACL_SCOPE_FIELDS", "on")


async def swept(extra: tuple[str, ...] = ()) -> tuple[Env, ScopeSweep, Clock]:
    clock = Clock()
    env = await Env.make(W.world("golden/confluence_hr"))
    names = [APP, *extra]
    for n in names:
        env.graph.app_docs.setdefault(n, {"_key": n})

    async def get_org_apps(org: str, **kw: Any) -> list[dict]:
        return [env.graph.app_docs.setdefault(n, {"_key": n}) for n in names]

    env.graph.get_org_apps = get_org_apps      # type: ignore[attr-defined]
    return env, ScopeSweep(env.graph, env.vdb, env.loaded.locator, clock=clock), clock


def row(rows: list, connector: str = APP) -> Any:
    return next(r for r in rows if r.connector_id == connector)


async def test_a_pending_root_is_rewritten_and_the_connector_is_current_again() -> None:
    env, sweep, _ = await swept()
    await env.change((MOVE,))
    assert APP not in await env.current()
    assert row(await sweep.run_once(ORG)).action == "done"
    assert not env.pending() and not await env.problems()
    assert row(await sweep.run_once(ORG)).action == "idle"          # nothing left to do: nothing touched


async def test_a_connector_nothing_changed_on_is_left_alone() -> None:
    env, sweep, _ = await swept()
    writes: list[str] = []

    async def note(name: str) -> None:
        writes.append(name)

    Tap(env, _no, lambda n: note(n) if n in ("update_payloads_by_ids", "update_node_fields_if_match") else _no(n))
    assert row(await sweep.run_once(ORG)).action == "idle" and not writes


async def test_a_killed_write_is_recovered_by_the_sweep_and_the_store_ends_exact() -> None:
    env, sweep, clock = await swept()
    g0 = env.graph.app_docs[APP].get(GENERATION_KEY, 0)
    await env.change((MOVE,))
    env.graph.app_docs[APP].update({PENDING_KEY: None, GENERATION_KEY: g0})      # the process died between the graph write and the registration: nothing was recorded
    await open_write(env.graph, APP, ["w:page:Handbook"], clock=clock)
    seen = Log()
    await env.audit(seen)
    assert APP not in await env.current() and not seen.leaks and not seen.pending_admits   # not current while the write is open: its stamps are not used
    assert row(await sweep.run_once(ORG)).action == "open_write"            # a live write is left alone
    assert unclosed_writes(env.graph.app_docs[APP]) == 1
    clock.now += DEAD
    assert row(await sweep.run_once(ORG)).action == "done"
    assert unclosed_writes(env.graph.app_docs[APP]) == 0 and not env.graph.app_docs[APP].get(OPEN_WRITES_KEY)
    await env.audit(seen)
    assert not seen.leaks and not seen.pending_admits and not seen.inexact
    assert not env.pending() and not await env.problems()                    # recovered: current again and exact, with no operator


async def test_a_change_of_the_whole_connector_gets_a_full_pass_that_brings_the_membership_right() -> None:
    env, sweep, _ = await swept()
    await env.change((MOVE,), whole=True)                                    # bumped, nothing listed: only a full pass covers it
    assert not env.pending() and APP not in await env.current()
    for _ in range(5):
        r = row(await sweep.run_once(ORG))
        if r.action != "full_pass_running":
            break
    assert r.action == "full_pass" and APP in await env.current()
    assert not await env.problems()                                          # scope fields and membership both equal the graph's
    assert row(await sweep.run_once(ORG)).action == "idle"


async def test_a_connector_nobody_back_filled_is_reported_for_a_first_pass_and_not_started() -> None:
    env, sweep, _ = await swept()
    await env.change((MOVE,), whole=True)
    env.graph.app_docs[APP][PENDING_KEY] = '{"w:page:Handbook":1}'           # something pending, and the change reached more than subtrees
    for key in BACKFILL_KEYS.values():
        env.graph.app_docs[APP].pop(key, None)
    r = row(await sweep.run_once(ORG))
    assert r.action == "needs_full_pass" and BACKFILL_KEYS["state"] not in env.graph.app_docs[APP]


async def test_counters_that_do_not_read_are_named_and_not_taken_for_a_full_pass() -> None:
    env, sweep, _ = await swept()
    await env.change((MOVE,), whole=True)
    env.graph.app_docs[APP].update({OPENED_KEY: 1, "scopeProjectionClosed": 2})      # closed ahead of opened
    assert row(await sweep.run_once(ORG)).action == "writes_unreadable"


async def test_a_recovery_that_fails_does_not_keep_the_pending_roots_from_being_rewritten(monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture) -> None:
    from app.services.vector_db import scope_sweep

    env, sweep, _ = await swept()
    await env.change((MOVE,))

    async def broken(*a: Any, **k: Any) -> int:
        raise RuntimeError("document locked")

    monkeypatch.setattr(scope_sweep, "recover_expired_writes", broken)
    assert row(await sweep.run_once(ORG)).action == "done" and not env.pending() and not await env.problems()
    assert "could not recover" in caplog.text


async def test_a_failure_that_cannot_even_be_recorded_does_not_stop_the_next_connector() -> None:
    env, sweep, _ = await swept(extra=("ghost",))
    await env.change((MOVE,))
    env.graph.app_docs["ghost"][SWEEP_KEY] = json.dumps({"n": 2, "e": "old", "t": 1})      # a record to clear: ghost has nothing to do now
    real = env.graph.update_node

    async def refuse(key: str, collection: str, updates: dict, *a: Any) -> bool:
        if SWEEP_KEY in updates:
            raise RuntimeError("write failed")
        return await real(key, collection, updates, *a)

    env.graph.update_node = refuse      # type: ignore[method-assign]
    rows = await sweep.run_once(ORG)
    assert row(rows, "ghost").action == "failed" and row(rows).action == "done"      # (ghost sorts first)
    assert not env.pending() and not await env.problems()


async def test_the_loop_survives_a_listing_that_fails_and_reports_the_last_round() -> None:
    from app.services.vector_db.scope_sweep import SweepRow, sweep_forever

    calls: list[str] = []
    listing = iter([RuntimeError("down"), ["o1", "o2"], ["o1", "o2"]])

    async def orgs() -> list[str]:
        nxt = next(listing)
        if isinstance(nxt, Exception):
            raise nxt
        return nxt

    class S:
        n = 0

        async def run_once(self, org: str, connectors: Any) -> list:
            self.n += 1
            calls.append(org)
            if self.n == 1:
                raise RuntimeError("cannot list")      # one organisation's connectors
            return [SweepRow("a", "failed" if self.n == 4 else "done")]

    waited: list[float] = []

    async def sleep(s: float) -> None:
        waited.append(s)

    failed = await sweep_forever(S(), orgs, 7, log=__import__("logging").getLogger("t"), rounds=3, sleep=sleep)      # type: ignore[arg-type]
    assert calls == ["o1", "o2", "o1", "o2"][:len(calls)] and len(calls) == 4 and waited == [7, 7]
    assert failed is True            # the third round's second organisation reported a failed connector
    good = S()
    good.n = 1
    assert await sweep_forever(good, orgs_ok, 1, log=__import__("logging").getLogger("t"), rounds=1, sleep=sleep) is False      # type: ignore[arg-type]


async def orgs_ok() -> list[str]:
    return ["x"]


async def test_writes_with_no_lease_are_reported_for_the_operator_and_not_waited_on() -> None:
    env, sweep, _ = await swept()
    await env.change((MOVE,), whole=True)
    env.graph.app_docs[APP][OPENED_KEY] = 1                      # opened by code that leaves no lease: nothing will ever close it
    r = row(await sweep.run_once(ORG))
    assert r.action == "open_write_unleased" and "--close-open-mark" in r.detail


async def test_a_connector_nobody_back_filled_is_left_alone() -> None:
    env, sweep, _ = await swept()
    await env.change((MOVE,), whole=True)
    for key in BACKFILL_KEYS.values():
        env.graph.app_docs[APP].pop(key, None)
    assert row(await sweep.run_once(ORG)).action == "idle"      # (not once current, so nothing to keep current)


async def test_a_connector_nobody_back_filled_does_not_walk_its_pending_roots_every_pass() -> None:
    env, sweep, _ = await swept()
    await env.change((MOVE,))
    for key in BACKFILL_KEYS.values():
        env.graph.app_docs[APP].pop(key, None)
    ran: list[str] = []
    real = sweep._job.run_connector

    async def spy(org: str, connector: str) -> Any:
        ran.append(connector)
        return await real(org, connector)

    sweep._job.run_connector = spy      # type: ignore[method-assign]
    assert row(await sweep.run_once(ORG)).action == "needs_full_pass" and not ran


async def test_a_connector_being_deleted_is_not_recovered_rewritten_or_marked() -> None:
    env, sweep, _ = await swept()
    await env.change((MOVE,))
    env.graph.app_docs[APP]["status"] = "DELETING"
    before = json.dumps(env.graph.app_docs[APP], sort_keys=True)
    touched: list[str] = []

    async def note(name: str) -> None:
        touched.append(name)

    Tap(env, _no, lambda n: note(n) if n in ("update_payloads_by_ids", "update_node_fields_if_match", "update_node") else _no(n))
    assert row(await sweep.run_once(ORG)).action == "deleting"
    assert not touched and json.dumps(env.graph.app_docs[APP], sort_keys=True) == before


async def test_a_failing_connector_backs_off_and_does_not_hold_the_others() -> None:
    env, sweep, clock = await swept(extra=("ghost",))
    await env.change((MOVE,))
    env.graph.app_docs["ghost"].update({PENDING_KEY: '{"x":1}', BACKFILL_KEYS["state"]: "done"})      # not a connector of the organisation: its rewrite fails every time
    first = await sweep.run_once(ORG)
    assert row(first, "ghost").action == "failed" and row(first).action == "done"      # the healthy one finished
    state = read_state(env.graph.app_docs["ghost"][SWEEP_KEY])
    assert state.failures == 1 and state.retry_at == int(clock.now) + BACKOFF_FIRST_SECONDS and "UnknownConnector" in (state.error or "")
    assert row(await sweep.run_once(ORG), "ghost").action == "waiting"         # no second attempt inside the wait
    clock.now += BACKOFF_FIRST_SECONDS + 1
    assert row(await sweep.run_once(ORG), "ghost").action == "failed"
    assert read_state(env.graph.app_docs["ghost"][SWEEP_KEY]).failures == 2
    for _ in range(12):                                                          # the wait doubles and stops at an hour
        clock.now += BACKOFF_MAX_SECONDS + 1
        await sweep.run_once(ORG)
    assert read_state(env.graph.app_docs["ghost"][SWEEP_KEY]).retry_at - int(clock.now) == BACKOFF_MAX_SECONDS


async def test_a_connector_that_works_again_loses_its_failure_record() -> None:
    env, sweep, clock = await swept(extra=("ghost",))
    env.graph.app_docs["ghost"].update({PENDING_KEY: '{"x":1}', BACKFILL_KEYS["state"]: "done"})
    assert row(await sweep.run_once(ORG), "ghost").action == "failed" and env.graph.app_docs["ghost"].get(SWEEP_KEY)
    env.graph.app_docs["ghost"].update({PENDING_KEY: None, BACKFILL_KEYS["state"]: None})      # whatever was wrong is gone
    clock.now += BACKOFF_FIRST_SECONDS + 1
    assert row(await sweep.run_once(ORG), "ghost").action == "idle" and not env.graph.app_docs["ghost"].get(SWEEP_KEY)


async def test_a_second_sweeper_does_not_work_a_connector_another_rewriter_holds() -> None:
    from app.services.permissions.rewrite_lease import RewriteLease

    env, sweep, clock = await swept()
    await env.change((MOVE,))
    held = RewriteLease(env.graph, APP, clock=clock)
    assert await held.acquire()
    assert row(await sweep.run_once(ORG)).action == "busy" and env.pending()      # the roots stay for the holder
    await held.release()
    assert row(await sweep.run_once(ORG)).action == "done" and not await env.problems()


async def test_an_unparseable_failure_record_is_no_history() -> None:
    assert read_state("{nope").failures == 0 and read_state(None).failures == 0
    assert read_state(json.dumps({"n": 2, "e": "x", "t": 9})).failures == 2


async def _no(_: str) -> None:
    return None
