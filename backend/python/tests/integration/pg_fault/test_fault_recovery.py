"""The PostgreSQL-only stack under infrastructure faults, injected while every component is under load (``load.py``).

For each fault: no unit is half committed (a record without its event, or the reverse), nothing acknowledged is lost, the inbox runs an event
again only after its acknowledgement failed, kg drains its log and equals the graph, the configuration cache serves no value older than the
last acknowledged write, a read either fails or equals the permission oracle (no over-, no under-admit, nothing from another org), and every
worker carries on without a restart.
"""
from __future__ import annotations

import asyncio
import os
import sys
import time

import asyncpg
import pytest

from tests.integration.pg_fault.conftest import pg_ctl, start_server
from tests.integration.pg_fault.load import (
    ROLES,
    ConfigLoad,
    InboxConsumer,
    KgLoad,
    Maintainers,
    Reads,
    RecordWrites,
    Search,
    Stack,
    login,
)

pytestmark = [pytest.mark.integration, pytest.mark.timeout(900)]
OUTAGE_S = float(os.environ.get("PG_FAULT_OUTAGE_S", "60"))
# Every 3rd world of the permission corpus, and every 2nd of those through the Knowledge Hub: a fault is not world-specific, and the whole
# corpus is held to the oracle by the conformance suites; 1 and 1 ask everything (the soak does).
WORLD_STEP = int(os.environ.get("PG_FAULT_WORLD_STEP", "3"))
KH_STEP = int(os.environ.get("PG_FAULT_KH_STEP", "2"))


async def restart_fast(stack: Stack, env) -> None:
    await asyncio.to_thread(pg_ctl, env.cluster, "-m", "fast", "restart")


async def restart_immediate(stack: Stack, env) -> None:
    await asyncio.to_thread(pg_ctl, env.cluster, "-m", "immediate", "restart")


async def pooler_restart(stack: Stack, env) -> None:
    assert stack.pooler_port is not None
    await asyncio.to_thread(env.restart_bouncer, stack.pooler_port)


async def terminate_each_role(stack: Stack, env) -> None:
    """Every session of each service role, again and again for a few seconds: whatever statement it runs is cut."""
    con = await asyncpg.connect(stack.admin_dsn)
    try:
        for _ in range(8):
            for role in ROLES:
                await con.execute("SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE usename = $1 AND datname = current_database()", login(role))
                await asyncio.sleep(0.1)
    finally:
        await con.close()


async def outage(stack: Stack, env) -> None:
    await asyncio.to_thread(pg_ctl, env.cluster, "-m", "fast", "stop")
    await asyncio.sleep(OUTAGE_S)
    await asyncio.to_thread(start_server, env.cluster)


FAULTS = {f.__name__: f for f in (restart_fast, restart_immediate, pooler_restart, terminate_each_role, outage)}


@pytest.mark.parametrize("fault", list(FAULTS))
async def test_the_stack_recovers_from_the_fault_without_losing_or_mixing_anything(fault: str, stack: Stack, env) -> None:
    conn_db, query_db = await stack.kernel("connectors"), await stack.kernel("query")
    reads = Reads(stack, conn_db, query_db, step=WORLD_STEP, kh_step=KH_STEP)
    comps = []
    try:
        await reads.preload()
        comps = [RecordWrites(stack, conn_db), InboxConsumer(stack), KgLoad(stack, conn_db, reads.orgs()), ConfigLoad(),
                 Maintainers(stack, os.environ.get("KERNEL_TEST_MIGRATE_PYTHON", sys.executable)), Search(stack), reads]
        for c in comps:
            await c.start()
        await asyncio.sleep(3)
        t0 = time.monotonic()
        await FAULTS[fault](stack, env)
        took = time.monotonic() - t0
        await asyncio.sleep(5)
        await asyncio.gather(*(c.stop_load() for c in comps))
        spent: dict[str, float] = {}

        async def timed(name: str, work) -> object:
            t = time.monotonic()
            try:
                return await work
            finally:
                spent[name] = round(time.monotonic() - t, 1)
        await asyncio.gather(*(timed(f"settle {c.name}", c.settle(90)) for c in comps))
        problems = {c.name: await timed(f"check {c.name}", c.check()) for c in comps}
        seen = {c.name: dict(c.errors) for c in comps if c.errors}
        print(f"{fault}: {took:.1f}s; errors the load saw: {seen}; seconds: {spent}")
        assert seen, "the fault reached no component: it was not injected"
        assert not any(problems.values()), "\n".join(f"{k}: {p}" for k, ps in problems.items() for p in ps)
    finally:
        for c in reversed(comps):
            await c.close()
        await conn_db.close()
        await query_db.close()
