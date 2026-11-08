"""The projection worker: woken by a notification at the writer's commit, with a safety poll; a projection that gives up is retried further apart each time."""
from __future__ import annotations

import asyncio
import os
import time

import pytest

from app.services.graph_db.postgres.kg import drainer
from app.services.graph_db.postgres.kg.worker import KgWorker

from .kg_fresh import differences
from .pg_support import new_org
from .test_kg_derivation import ctx, prov
from .test_kg_projection_races import OPS, world2

pytestmark = [pytest.mark.integration, pytest.mark.timeout(120)]


@pytest.fixture(autouse=True)
def projection_on(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PG_KG_PROJECT", "1")
    monkeypatch.delenv("PG_KG_FLUSH", raising=False)


async def log_rows(p, org: str) -> int:
    async def go(tx) -> int:
        return await tx.fetchval("SELECT count(*) FROM kg.graph_changes WHERE org_id = $1", org)
    with ctx(org):
        return await p._q(go, None)


async def until(cond, seconds: float) -> bool:
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        ok = cond()
        if (await ok) if asyncio.iscoroutine(ok) else ok:
            return True
        await asyncio.sleep(0.05)
    return False


async def test_a_notification_wakes_the_worker_long_before_its_poll(pg_db) -> None:
    p, org = prov(pg_db), new_org()

    async def orgs() -> list[str]:
        return [org]
    worker = KgWorker(pg_db, orgs, dsn=os.environ["PG_CORE_URL"], poll_s=3600)          # only a notification can wake it in time
    worker.start()
    try:
        assert await until(lambda: worker.passes >= 1, 20)                               # the worker looked once and sleeps
        await world2(p, org)
        for op in OPS[5:9]:
            await op(p, org)

        async def empty() -> bool:
            return await log_rows(p, org) == 0
        assert await until(empty, 20), "the worker did not wake"
    finally:
        await worker.stop()
    assert not await differences(p, org)


async def test_without_a_listener_the_poll_finds_the_log(pg_db) -> None:
    p, org = prov(pg_db), new_org()

    async def orgs() -> list[str]:
        return [org]
    await world2(p, org)
    worker = KgWorker(pg_db, orgs, dsn=None, poll_s=0.2)
    worker.start()
    try:
        async def empty() -> bool:
            return await log_rows(p, org) == 0
        assert await until(empty, 20)
    finally:
        await worker.stop()
    assert not await differences(p, org)


async def test_a_worker_whose_projection_gives_up_keeps_the_log_and_finishes_once_it_can(pg_db, monkeypatch) -> None:
    p, org = prov(pg_db), new_org()
    await world2(p, org)
    real, calls = drainer.project, {"n": 0}

    async def flaky(*a: object, **k: object) -> object:
        calls["n"] += 1
        if calls["n"] <= 2:
            raise RuntimeError("the projection kept reaching connectors it had not locked")
        return await real(*a, **k)
    monkeypatch.setattr(drainer, "project", flaky)

    async def orgs() -> list[str]:
        return [org]
    worker = KgWorker(pg_db, orgs, dsn=None, poll_s=1.0)
    worker.start()
    try:
        async def empty() -> bool:
            return await log_rows(p, org) == 0
        assert await until(empty, 30)
    finally:
        await worker.stop()
    assert calls["n"] >= 3 and not await differences(p, org)
