"""Removing a schedule while a worker claims its due run (the statements of pg-job-queue.service.ts, interleaved by hand).

R: BEGIN; DELETE the schedule.   C: BEGIN; claim the due run R1; insert the next run R2 (waits for R's uncommitted delete of the schedule);
R: DELETE the queued runs, skipping the ones a claimer holds, COMMIT.   C: continues: the schedule is gone, so no next run; COMMIT.
Neither waits for the other in a cycle, and the schedule is not left firing."""

import asyncio
import json
import uuid

import asyncpg


async def test_remove_wins_without_a_deadlock(runtime) -> None:
    q, key = f"race-{uuid.uuid4().hex[:6]}", "c::::every"
    dsn = runtime.cfg.dsn.replace("pipeshub_connectors_login", "pipeshub_node_login")

    async def con() -> asyncpg.Connection:
        c = await asyncpg.connect(dsn)
        await c.execute("SET ROLE pipeshub_node")
        return c

    setup, r, c = await con(), await con(), await con()
    await setup.execute(
        "INSERT INTO platform.sched_repeats (queue, key, name, every_ms, next_ms) VALUES ($1, $2, 'c', 1000, 0)",
        q,
        key,
    )
    await setup.execute(
        "INSERT INTO platform.sched_jobs (queue, id, name, data, opts, state, run_at, created_ms) "
        "VALUES ($1, 'R1', 'c', '{}', $2::jsonb, 'delayed', now() - interval '1 second', 0)",
        q,
        json.dumps({"repeat": {"key": key}}),
    )
    rt, ct = r.transaction(), c.transaction()
    await rt.start()
    await r.execute(
        "DELETE FROM platform.sched_repeats WHERE queue = $1 AND key = $2", q, key
    )
    await ct.start()
    await c.execute(
        "WITH picked AS (SELECT queue, id FROM platform.sched_jobs WHERE queue = $1 AND state IN ('waiting','delayed') AND run_at <= now() "
        "ORDER BY priority, run_at LIMIT 5 FOR UPDATE SKIP LOCKED) "
        "UPDATE platform.sched_jobs j SET state = 'active', attempts_made = j.attempts_made + 1 FROM picked p WHERE j.queue = p.queue AND j.id = p.id",
        q,
    )

    async def claim_inserts_next() -> str:
        try:
            await c.execute(  # the statement of the service: the schedule row is read FOR SHARE, so it waits for R's delete
                "INSERT INTO platform.sched_jobs (queue, id, name, data, opts, state, run_at, created_ms) "
                "SELECT $1, 'R2', 'c', '{}', $2::jsonb, 'delayed', now() + interval '1 second', 0 "
                "WHERE EXISTS (SELECT 1 FROM platform.sched_repeats WHERE queue = $1 AND key = $3 FOR SHARE) ON CONFLICT (queue, id) DO NOTHING",
                q,
                json.dumps({"repeat": {"key": key}}),
                key,
            )
            await c.execute(
                "UPDATE platform.sched_repeats SET next_ms = 1 WHERE queue = $1 AND key = $2",
                q,
                key,
            )
            await ct.commit()
            return "claim committed"
        except Exception as e:
            await ct.rollback()
            return f"claim failed: {type(e).__name__}"

    async def remove_runs() -> str:
        try:
            await r.execute(
                "DELETE FROM platform.sched_jobs j USING (SELECT queue, id FROM platform.sched_jobs WHERE queue = $1 "
                "AND state IN ('waiting','delayed') AND opts #>> '{repeat,key}' = $2 FOR UPDATE SKIP LOCKED) p WHERE j.queue = p.queue AND j.id = p.id",
                q,
                key,
            )
            await rt.commit()
            return "remove committed"
        except Exception as e:
            await rt.rollback()
            return f"remove failed: {type(e).__name__}"

    task = asyncio.create_task(claim_inserts_next())
    await asyncio.sleep(0.3)  # the claimer is waiting for the remover
    results = await asyncio.gather(task, remove_runs())
    try:
        assert results == ["claim committed", "remove committed"], results
        rows = await setup.fetch(
            "SELECT id, state FROM platform.sched_jobs WHERE queue = $1 ORDER BY id", q
        )
        assert [(x["id"], x["state"]) for x in rows] == [
            ("R1", "active")
        ]  # the claimed run finishes; no next run was queued
        assert (
            await setup.fetchval(
                "SELECT count(*) FROM platform.sched_repeats WHERE queue = $1", q
            )
            == 0
        )
    finally:
        await setup.execute("DELETE FROM platform.sched_jobs WHERE queue = $1", q)
        for x in (setup, r, c):
            await x.close()
