"""Exactly one half-open trial across processes, on a real Redis with Redis TIME (no test clock)."""

import asyncio
import multiprocessing as mp
import uuid

from tests.support.spend_world import require_spend_redis

PROCS, JOBS = 8, 4
PK, CLS = "pkmp", "l"


def _fleet(url: str, namespace: str):  # noqa: ANN202
    import redis.asyncio as aioredis

    from app.services.llm_gateway.fleet_redis import FleetRedis

    client = aioredis.Redis.from_url(url, decode_responses=True)
    return FleetRedis(lambda: client, namespace), client


def _worker(url: str, namespace: str, index: int, barrier, out) -> None:
    async def run() -> None:
        from app.services.llm_gateway.fleet_breaker import FleetBreaker

        fleet, client = _fleet(url, namespace)
        breaker = FleetBreaker(fleet)
        barrier.wait(30)
        codes = await asyncio.gather(*(breaker.admit(PK, CLS, f"p{index}-j{j}") for j in range(JOBS)))
        out.put([(c.code, c.token) for c in codes])
        await client.aclose()

    asyncio.run(run())


async def test_exactly_one_trial_is_granted_across_processes() -> None:
    url = require_spend_redis()
    namespace = f"mp{uuid.uuid4().hex[:8]}"
    fleet, client = _fleet(url, namespace)
    try:
        seconds, micros = await client.time()
        now_ms = int(seconds) * 1000 + int(micros) // 1000
        # a breaker whose 30 s cooldown ended a second ago, by Redis TIME
        await client.hset(
            fleet.key(f"sg:{{p:{PK}:{CLS}}}:br"),
            mapping={"state": "open", "opened_ms": now_ms - 31_000, "cooldown_ms": 30_000, "trial_holder": "", "trial_until": 0},
        )
        context = mp.get_context("spawn")
        barrier, out = context.Barrier(PROCS), context.Queue()
        procs = [context.Process(target=_worker, args=(url, namespace, i, barrier, out)) for i in range(PROCS)]
        for p in procs:
            p.start()
        results = [out.get(timeout=60) for _ in procs]
        for p in procs:
            p.join(30)
        codes = [code for batch in results for code, _ in batch]
        assert codes.count(2) == 1, f"{codes.count(2)} trials granted among {len(codes)} concurrent admissions"
        assert codes.count(0) == 0, "nobody may send as if the breaker were closed"
    finally:
        keys = [k async for k in client.scan_iter(match=f"{namespace}:*")]
        if keys:
            await client.delete(*keys)
        await client.aclose()
