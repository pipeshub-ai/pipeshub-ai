"""The bootstrap's turns (no database): a long turn carries over between the worker's passes without holding up the other connectors, and a turn that raises is tried again
further apart each time, up to a cap, with jitter."""
from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import TYPE_CHECKING

from app.services.graph_db.postgres.kg import backfill as bf
from app.services.graph_db.postgres.kg import bootstrap
from app.services.graph_db.postgres.kg import worker as worker_mod
from app.services.graph_db.postgres.kg.worker import KgWorker

if TYPE_CHECKING:
    import pytest

FILLS = ("fill_trash", "fill_pc_tops", "fill_grant_marks", "fill_kb_listing", "fill_grant_parents")


def no_db(monkeypatch: pytest.MonkeyPatch, apps: list[str]) -> None:
    async def candidates(_db: object, _org: str) -> list[str]:
        return list(apps)

    async def copied(_db: object, _org: str) -> bool:
        return True

    async def drained(*_a: object, **_k: object) -> SimpleNamespace:
        return SimpleNamespace(rows=0)
    monkeypatch.setattr(bootstrap, "candidates", candidates)
    monkeypatch.setattr(bf, "principals_copied", copied)
    monkeypatch.setattr(bf, "drain_org", drained)


async def test_a_turn_that_outlasts_the_pass_carries_over_and_the_other_connectors_keep_taking_turns(monkeypatch: pytest.MonkeyPatch) -> None:
    no_db(monkeypatch, ["slow", "fast"])
    monkeypatch.setattr(bootstrap, "TURN_WAIT_S", 0.01)
    release, started = asyncio.Event(), []

    async def turn(_db: object, _org: str, app: str, _due: object, _stop: object) -> int:
        started.append(app)
        if app == "slow":           # a verify that takes far longer than a pass
            await release.wait()
        return 1
    monkeypatch.setattr(bootstrap, "_turn", turn)
    turns: dict = {}
    for _ in range(5):
        assert await asyncio.wait_for(bootstrap.bring_to_enforce(None, "org", {}, None, turns), 2.0) >= 1      # never waits for the slow one
    assert started.count("slow") == 1 and started.count("fast") >= 4, started
    assert ("org", "slow") in turns and not turns[("org", "slow")].done()
    release.set()
    await asyncio.sleep(0)
    assert await bootstrap.bring_to_enforce(None, "org", {}, None, turns) >= 1 and ("org", "slow") not in turns      # its end is reaped and counted


async def test_with_more_connectors_than_slots_each_gets_a_turn_and_one_not_due_takes_no_slot(monkeypatch: pytest.MonkeyPatch) -> None:
    """Five connectors and four slots: each turn ends at once and asks to go on (an unfinished copy sets its due to 0). The fifth starts within two passes, and a connector whose
    retry is still ahead is never started (its deadline is left as it was)."""
    apps = ["a", "b", "c", "d", "e"]
    no_db(monkeypatch, apps)
    monkeypatch.setattr(bootstrap, "TURN_WAIT_S", 0.01)
    monkeypatch.setattr(bootstrap, "_STARTED", {}, raising=False)
    started: list[str] = []

    async def turn(_db: object, _org: str, app: str, due: dict, _stop: object) -> int:
        started.append(app)
        due[("org", app)] = 0.0                       # unfinished: it may go on at once
        return 1
    monkeypatch.setattr(bootstrap, "_turn", turn)
    turns: dict = {}
    for _ in range(2):
        await bootstrap.bring_to_enforce(None, "org", {}, None, turns)
        await asyncio.sleep(0)
    assert set(started) == set(apps), started
    started.clear()
    later = bootstrap.time.monotonic() + 3600
    due = {("org", "a"): later}
    for _ in range(6):
        await bootstrap.bring_to_enforce(None, "org", due, None, turns)
        await asyncio.sleep(0)
    assert "a" not in started and {"b", "c", "d", "e"} <= set(started) and due[("org", "a")] == later, started
    counts = [started.count(x) for x in "bcde"]
    assert max(counts) - min(counts) <= 1, counts         # round and round, none ahead of the others


async def test_without_the_workers_turns_a_call_still_waits_for_every_turn(monkeypatch: pytest.MonkeyPatch) -> None:
    no_db(monkeypatch, ["a", "b"])
    ended = []

    async def turn(_db: object, _org: str, app: str, _due: object, _stop: object) -> int:
        await asyncio.sleep(0.05)
        ended.append(app)
        return 1
    monkeypatch.setattr(bootstrap, "_turn", turn)
    assert await bootstrap.bring_to_enforce(None, "org", {}) == 2 and sorted(ended) == ["a", "b"]


async def test_a_turn_that_raises_waits_longer_each_time_up_to_the_cap_and_a_turn_that_does_not_starts_over(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(bootstrap, "_FAILED", {})
    monkeypatch.setattr(bootstrap.random, "uniform", lambda _a, b: b)        # the top of the jitter
    outcome = {"raise": True}

    async def fill(*_a: object, **_k: object) -> tuple[str, None]:
        if outcome["raise"]:
            raise TimeoutError("a statement outlasted its budget")
        return "enforce", None
    for name in FILLS:
        monkeypatch.setattr(bf, name, fill)
    due: dict = {}
    waits = []
    for _ in range(9):
        due.pop(("org", "app"), None)
        t0 = bootstrap.time.monotonic()
        assert await bootstrap._turn(None, "org", "app", due, None) == 0
        waits.append(due[("org", "app")] - t0)
    want = [min(bootstrap.RETRY_S * 2 ** i, bootstrap.ERROR_RETRY_MAX_S) for i in range(9)]
    assert all(abs(w - x) < 1.0 for w, x in zip(waits, want, strict=True)), (waits, want)
    assert waits[-1] <= bootstrap.ERROR_RETRY_MAX_S + 1.0 and bootstrap._FAILED[("org", "app")] == 9
    outcome["raise"] = False
    due.pop(("org", "app"), None)
    assert await bootstrap._turn(None, "org", "app", due, None) == 1 and ("org", "app") not in bootstrap._FAILED
    outcome["raise"] = True
    t0 = bootstrap.time.monotonic()
    await bootstrap._turn(None, "org", "app", due, None)
    assert due[("org", "app")] - t0 <= bootstrap.RETRY_S + 1.0                 # one failure after a success is a first failure again


def test_the_jitter_keeps_each_wait_between_half_and_all_of_its_step() -> None:
    for n in (1, 2, 5, 40):
        step = min(bootstrap.RETRY_S * 2 ** (n - 1), bootstrap.ERROR_RETRY_MAX_S)
        assert all(step / 2 <= bootstrap.error_retry_s(n) <= step for _ in range(50))


async def test_stop_cuts_a_turn_carried_over_from_an_earlier_pass(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(worker_mod, "STOP_TIMEOUT_S", 0.05)

    async def no_orgs() -> list[str]:
        return []
    w = KgWorker(None, no_orgs, dsn=None, poll_s=0.05)  # type: ignore[arg-type]
    stuck = asyncio.create_task(asyncio.Event().wait())          # a verify that never ends by itself
    w._turns[("org", "app")] = stuck
    await asyncio.wait_for(w.stop(), 2.0)
    assert stuck.cancelled()


async def test_a_started_worker_starts_turns_while_its_drain_waits_on_a_verifys_lock(monkeypatch: pytest.MonkeyPatch) -> None:
    """The drain of a change of a connector whose verify holds its lock waits until the verify commits; the other connectors' turns must not wait with it."""
    from app.services.graph_db.postgres.kg import net
    no_db(monkeypatch, ["a", "b"])
    monkeypatch.setattr(bootstrap, "TURN_WAIT_S", 0.01)
    blocked, started = asyncio.Event(), []

    async def drain(*_a: object, **_k: object) -> int:
        await blocked.wait()                          # the lock of a verify in flight
        return 0

    async def none(*_a: object, **_k: object) -> int:
        return 0

    async def turn(_db: object, _org: str, app: str, _due: object, _stop: object) -> int:
        started.append(app)
        return 1

    async def orgs() -> list[str]:
        return ["org"]
    monkeypatch.setattr(worker_mod, "drain_or_retry", drain)
    monkeypatch.setattr(net, "demote_pending", none)
    monkeypatch.setattr(net, "verify_due", none)
    monkeypatch.setattr(bootstrap, "_turn", turn)
    w = KgWorker(None, orgs, dsn=None, poll_s=0.05)  # type: ignore[arg-type]
    w.start()
    try:
        for _ in range(200):
            if len(started) >= 6:
                break
            await asyncio.sleep(0.01)
        assert len(started) >= 6 and set(started) == {"a", "b"}, started
    finally:
        blocked.set()
        await w.stop()
