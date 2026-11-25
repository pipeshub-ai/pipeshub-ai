"""The long soak of the PostgreSQL-only stack: the components of ``load.py`` under a steady load for hours, with a fault every cycle, against
the Compose services (``deployment/docker-compose/tests/pg_fault_soak.sh`` starts them and runs this). Not run in CI.

Each cycle builds fresh components (a new org each, so the data grows as it would), runs the load, injects the next fault, lets the load run on,
stops it, waits for the background work to settle and checks every component, as the fault tests do. Every minute it samples the server:
connections per login, WAL written, database size, the containers' memory and the driver's own. One JSON line per sample and per cycle goes to
``--out``; the exit code is 1 when any cycle found a problem.

    python -m tests.integration.pg_fault.soak --hours 6 --out soak.jsonl   (environment: see ``_stack``)
"""
from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import os
import resource
import subprocess
import sys
import time
from typing import TYPE_CHECKING

from app.core.org_context import spawn_detached
from tests.integration.pg_fault.load import (
    ROLES,
    ConfigLoad,
    InboxConsumer,
    KgLoad,
    Reads,
    RecordWrites,
    Search,
    Stack,
    login,
    maintainers_work,
)

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable


def _docker(*args: str) -> str:
    return subprocess.run(["docker", *args], check=True, capture_output=True, text=True).stdout.strip()


def _stack() -> Stack:
    """``PG_SOAK_PASSWORDS``: JSON {role: password} with ``admin`` for postgres; ``PG_SOAK_SERVER_PORT`` / ``PG_SOAK_POOLER_PORT``: the published ports."""
    pw = json.loads(os.environ["PG_SOAK_PASSWORDS"])
    server, pooler = int(os.environ["PG_SOAK_SERVER_PORT"]), int(os.environ["PG_SOAK_POOLER_PORT"])

    def dsn(role: str, port: int | None) -> str:
        return f"postgresql://{login(role)}:{pw[role]}@127.0.0.1:{port or server}/pipeshub"
    return Stack(dsn, server, pooler, f"postgresql://postgres:{pw['admin']}@127.0.0.1:{server}/pipeshub", "")


async def _wait_up(stack: Stack, seconds: float = 300) -> None:
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        with contextlib.suppress(Exception):
            con = await stack.admin()
            await con.close()
            return
        await asyncio.sleep(1)
    raise RuntimeError("the server did not come back")


def _faults(stack: Stack, pg: str, pooler: str, outage_s: float) -> dict[str, Callable[[], Awaitable[None]]]:
    async def restart_fast() -> None:
        await asyncio.to_thread(_docker, "restart", "-t", "60", pg)        # the image stops with SIGINT: a fast shutdown

    async def restart_immediate() -> None:
        await asyncio.to_thread(_docker, "kill", "-s", "QUIT", pg)         # SIGQUIT to the postmaster: an immediate shutdown, crash recovery at start
        await asyncio.to_thread(_docker, "wait", pg)
        await asyncio.to_thread(_docker, "start", pg)

    async def pooler_restart() -> None:
        await asyncio.to_thread(_docker, "restart", "-t", "10", pooler)

    async def terminate_each_role() -> None:
        con = await stack.admin()
        try:
            for _ in range(8):
                for role in (*ROLES, "kv"):
                    await con.execute("SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE usename = $1", login(role))
                    await asyncio.sleep(0.1)
        finally:
            await con.close()

    async def outage() -> None:
        await asyncio.to_thread(_docker, "stop", "-t", "60", pg)
        await asyncio.sleep(outage_s)
        await asyncio.to_thread(_docker, "start", pg)
    return {f.__name__: f for f in (restart_fast, restart_immediate, pooler_restart, terminate_each_role, outage)}


async def _sample(stack: Stack, containers: list[str], wal0: list[str]) -> dict:
    out: dict = {"t": time.time(), "driver_rss_mb": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss // 1024}
    try:
        con = await stack.admin()
        try:
            out["connections"] = {r["usename"] or "-": r["n"] for r in await con.fetch(
                "SELECT usename, count(*) AS n FROM pg_stat_activity WHERE backend_type = 'client backend' GROUP BY usename")}
            wal0[:] = wal0 or [await con.fetchval("SELECT pg_current_wal_lsn()::text")]
            out["wal_mb"] = round(float(await con.fetchval("SELECT pg_wal_lsn_diff(pg_current_wal_lsn(), $1::text::pg_lsn)", wal0[0])) / 2**20, 1)
            out["wal_dir_mb"] = round(float(await con.fetchval("SELECT coalesce(sum(size), 0) FROM pg_ls_waldir()")) / 2**20, 1)
            out["db_mb"] = round(await con.fetchval("SELECT pg_database_size(current_database())") / 2**20, 1)
            out["queue_open"] = await con.fetchval("SELECT count(*) FROM platform.queue_deliveries WHERE status <> 'dead'")
            out["kg_log"] = await con.fetchval("SELECT count(*) FROM kg.graph_changes")
        finally:
            await con.close()
    except Exception as e:
        out["sample_error"] = type(e).__name__
    with contextlib.suppress(Exception):
        stats = _docker("stats", "--no-stream", "--format", "{{.Name}} {{.MemUsage}}", *containers)
        out["mem"] = dict(line.split(" ", 1) for line in stats.splitlines())
    with contextlib.suppress(Exception):
        out["restarts"] = {c: int(_docker("inspect", "-f", "{{.RestartCount}}", c)) for c in containers}
    return out


async def _cycle(stack: Stack, reads: Reads, fault: Callable[[], Awaitable[None]], run_s: float) -> dict:
    conn_db = await stack.kernel("connectors")
    writes = RecordWrites(stack, conn_db)
    comps = [writes, InboxConsumer(stack), KgLoad(stack, conn_db, [*reads.orgs(), writes.org]), ConfigLoad(), Search(stack), reads]
    started = []
    try:
        for c in comps:
            await c.start()
            started.append(c)
        await asyncio.sleep(run_s / 2)
        t0 = time.monotonic()
        await fault()
        await _wait_up(stack)
        took = time.monotonic() - t0
        await asyncio.sleep(run_s / 2)
        await asyncio.gather(*(c.stop_load() for c in comps))
        await asyncio.gather(*(c.settle(180) for c in comps))
        problems = {c.name: await c.check() for c in comps}
        problems["maintainers"] = await maintainers_work(stack)
        return {"fault_s": round(took, 1), "errors": {c.name: dict(c.errors) for c in comps if c.errors},
                "problems": {k: v[:20] for k, v in problems.items() if v},
                "volume": {"records": len(comps[0].outcome), "events": len(comps[1].sent), "config_writes": comps[3].tried,  # type: ignore[attr-defined]
                           "points": len(comps[4].acked), "searches": comps[4].runs, "reads": reads.runs}}  # type: ignore[attr-defined]
    finally:
        for c in reversed(started):
            if c is not reads:
                with contextlib.suppress(Exception):
                    await c.close()
        reads.errors.clear()
        reads.wrong.clear()
        reads.runs = 0
        await conn_db.close()


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--hours", type=float, default=6.0)
    ap.add_argument("--cycle-min", type=float, default=15.0, help="load per cycle, half before the fault and half after")
    ap.add_argument("--outage-s", type=float, default=60.0)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    stack = _stack()
    for k, v in stack.environ().items():
        os.environ.setdefault(k, v)
    pg, pooler = os.environ["PG_SOAK_PG_CONTAINER"], os.environ["PG_SOAK_POOLER_CONTAINER"]
    containers = [pg, pooler, *os.environ.get("PG_SOAK_WORKERS", "").split()]
    faults = _faults(stack, pg, pooler, a.outage_s)
    end, failed, wal0 = time.monotonic() + a.hours * 3600, 0, []
    query_db, writer = await stack.kernel("query"), await stack.kernel("connectors")
    reads = Reads(stack, writer, query_db)
    await reads.preload()

    def emit(rec: dict) -> None:
        with open(a.out, "a") as f:
            f.write(json.dumps(rec) + "\n")

    async def sampler() -> None:
        while True:
            emit({"sample": await _sample(stack, containers, wal0)})
            await asyncio.sleep(60)
    sampling = spawn_detached(sampler(), name="pg-soak-sampler")
    try:
        n = 0
        while time.monotonic() < end:
            name = list(faults)[n % len(faults)]
            n += 1
            rec = {"cycle": n, "fault": name, "t": time.time(), **await _cycle(stack, reads, faults[name], a.cycle_min * 60)}
            failed += bool(rec["problems"])
            emit(rec)
            print(json.dumps({k: rec[k] for k in ("cycle", "fault", "fault_s", "problems")}), flush=True)
    finally:
        sampling.cancel()
        with contextlib.suppress(Exception):
            await reads.close()
        await query_db.close()
        await writer.close()
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
