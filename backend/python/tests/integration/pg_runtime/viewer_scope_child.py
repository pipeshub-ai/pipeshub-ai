"""One of the processes of test_viewer_scope_cache.py: "query" reads an organisation's ACL version and waits for it to move;
"connectors" bumps it, as a permission write does."""

import asyncio
import json
import logging
import sys
import time
from types import SimpleNamespace

# Before app is imported: its log lines then go to stderr, not to the stdout this script answers on (docs/logging.md).
logging.basicConfig()

from app.services.permissions.cache import ViewerScopeCache  # noqa: E402


class Cfg:
    async def get_redis_config(self):  # noqa: ANN201
        return SimpleNamespace(host="localhost", port=6379, password=None, db=0, tls=False)


async def main(role: str, org: str) -> None:
    cache = await ViewerScopeCache.create(Cfg())  # type: ignore[arg-type]
    print(json.dumps({"client": cache._client is not None, "bus": getattr(cache, "_bus", None) is not None}), flush=True)
    if role == "connectors":
        await asyncio.sleep(0.3)
        await cache.bump(org)
        print(json.dumps({"done": time.time(), "pending": sorted(cache._unbumped)}), flush=True)
        await cache.aclose()
        return
    first = None
    for _ in range(100):      # wait until the bus is up: an unhealthy bus answers None, and nothing is cached
        first = await cache.version(org)
        if first is not None:
            break
        await asyncio.sleep(0.1)
    print(json.dumps({"READY": True, "first": first, "again": await cache.version(org)}), flush=True)
    deadline = time.time() + 6
    while time.time() < deadline:
        now = await cache.version(org)
        if now is not None and now != first:
            print(json.dumps({"seen_at": time.time()}), flush=True)
            await cache.aclose()
            return
        await asyncio.sleep(0.02)
    print(json.dumps({"STALE": True}), flush=True)
    await cache.aclose()


asyncio.run(main(sys.argv[1], sys.argv[2]))
