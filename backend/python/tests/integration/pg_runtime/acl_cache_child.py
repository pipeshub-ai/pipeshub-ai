"""One of two processes of test_acl_cache.py: "query" caches a user's accessible-record map and keeps asking; "connectors" drops it."""

import asyncio
import json
import sys
import time
from logging import getLogger
from types import SimpleNamespace

from app.services.cache.accessible_records_cache import AccessibleRecordsCache


class Cfg:
    async def get_redis_config(self):  # noqa: ANN201
        return SimpleNamespace(
            host="localhost", port=6379, password=None, db=0, tls=False
        )


async def main(role: str, graph_file: str) -> None:
    cache = await AccessibleRecordsCache.create(getLogger("child"), Cfg())
    print(json.dumps({"cache": type(cache).__name__}), flush=True)
    if role == "connectors":
        await asyncio.sleep(0.3)
        t = time.time()
        await cache.invalidate_connector("org", "c1")
        print(json.dumps({"invalidated_at": t, "done": time.time()}), flush=True)
        await cache.close()
        return

    async def loader() -> dict[str, str]:
        with open(graph_file) as f:
            return json.load(f)

    for _ in range(
        100
    ):  # wait until the bus is up (an unhealthy bus bypasses the cache)
        if cache.enabled:
            break
        await asyncio.sleep(0.1)
    first = await cache.get_or_compute_user_connector("org", "c1", "u1", loader)
    again = await cache.get_or_compute_user_connector("org", "c1", "u1", loader)
    print(
        json.dumps(
            {
                "READY": True,
                "first": first,
                "cached": again == first,
                "enabled": cache.enabled,
            }
        ),
        flush=True,
    )
    deadline = time.time() + 6
    while time.time() < deadline:
        got = await cache.get_or_compute_user_connector("org", "c1", "u1", loader)
        if not got:
            print(json.dumps({"seen_at": time.time()}), flush=True)
            await cache.close()
            return
        await asyncio.sleep(0.02)
    print(json.dumps({"STALE": True}), flush=True)
    await cache.close()


asyncio.run(main(sys.argv[1], sys.argv[2]))
