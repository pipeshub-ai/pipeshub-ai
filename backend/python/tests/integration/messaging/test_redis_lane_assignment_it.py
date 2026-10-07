"""The lane map's scripts against a real Redis server.

The unit tests run the same scripts on fakeredis; this proves them on the
real Lua runtime, including two separate OS processes placing the same
connectors at once, which is what two indexing or connectors replicas do.

Requires:
  docker compose -f deployment/docker-compose/docker-compose.integration.messaging.yml up -d
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
from collections import Counter
from pathlib import Path

import pytest

from app.services.messaging.lanes.assignment import (
    LaneAssignments,
    lane_map_key,
    lane_meta_key,
    read_lane_map,
    write_lane_count,
)
from app.services.messaging.lanes.hash_router import stable_lane
from app.services.redis.config import RedisConnectionConfig
from app.services.redis.connection_provider_factory import get_redis_provider

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

LANES = 8

# One placer process: places every connector it is given, in its own order,
# and prints what it got.
_PLACER = """
import asyncio, json, logging, sys
from app.services.messaging.lanes.assignment import LaneAssignments
from app.services.redis.config import RedisConnectionConfig
from app.services.redis.connection_provider_factory import get_redis_provider

host, port, topic, order = sys.argv[1], int(sys.argv[2]), sys.argv[3], json.loads(sys.argv[4])

async def main():
    provider = get_redis_provider(RedisConnectionConfig.from_host_port(host=host, port=port))
    assignments = LaneAssignments(logging.getLogger("placer"), provider, topic=topic, fallback_lane_count=8)
    lanes = await asyncio.gather(*(assignments.lane_for(c) for c in order))
    await assignments.aclose()
    print(json.dumps(dict(zip(order, lanes))))

asyncio.run(main())
"""


def _provider(host: str, port: int):  # noqa: ANN202
    return get_redis_provider(RedisConnectionConfig.from_host_port(host=host, port=port))


@pytest.fixture
async def topic(redis_available: tuple[str, int], unique_suffix: str):  # noqa: ANN201
    name = f"record-events-it-{unique_suffix}"
    yield name
    host, port = redis_available
    client = _provider(host, port).get_client()
    await client.delete(lane_map_key(name), lane_meta_key(name))


async def test_two_processes_placing_the_same_connectors_agree_and_never_stack_a_lane(
    redis_available: tuple[str, int], topic: str
) -> None:
    host, port = redis_available
    connectors = [f"connector-{i}" for i in range(100)]
    orders = [connectors, list(reversed(connectors))]

    # Pinned to the backend root: pytest may run from the repository root,
    # where `app` is not importable in a child interpreter.
    backend_root = Path(__file__).resolve().parents[3]

    async def run(order: list[str]) -> dict[str, int]:
        process = await asyncio.create_subprocess_exec(
            sys.executable, "-c", _PLACER, host, str(port), topic, json.dumps(order),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=backend_root,
            env={**os.environ, "PYTHONPATH": str(backend_root)},
        )
        out, err = await asyncio.wait_for(process.communicate(), timeout=120)
        assert process.returncode == 0, err.decode()[-2000:]
        return json.loads(out.decode().strip().splitlines()[-1])

    first, second = await asyncio.gather(*(run(order) for order in orders))

    assert first == second, "both processes were given the same lane for every connector"
    entries = await read_lane_map(_provider(host, port).get_client(), topic)
    assert {c: e.lane for c, e in entries.items()} == first
    per_lane = Counter(first.values())
    assert max(per_lane.values()) - min(per_lane.values()) <= 1
    meta = await _provider(host, port).get_client().hgetall(lane_meta_key(topic))
    assert sum(int(meta.get(f"large:{lane}", 0)) for lane in range(LANES)) == 100


async def test_colliding_connectors_are_separated_and_a_free_hash_lane_is_kept(
    redis_available: tuple[str, int], topic: str
) -> None:
    host, port = redis_available
    provider = _provider(host, port)
    await write_lane_count(provider.get_client(), topic, LANES)
    assignments = LaneAssignments(
        logging.getLogger("it"), provider, topic=topic, fallback_lane_count=LANES
    )
    same_lane = [c for c in (f"c-{i}" for i in range(200)) if stable_lane(c, LANES) == 2][:3]

    lanes = [await assignments.lane_for(c) for c in same_lane]

    assert lanes[0] == 2
    assert len(set(lanes)) == 3
    entries = await read_lane_map(provider.get_client(), topic)
    assert entries[same_lane[0]].prev_lane is None
    assert all(entries[c].prev_lane == 2 for c in same_lane[1:])
    await assignments.aclose()


async def test_a_script_flushed_by_a_restart_is_loaded_again(
    redis_available: tuple[str, int], topic: str
) -> None:
    host, port = redis_available
    provider = _provider(host, port)
    assignments = LaneAssignments(
        logging.getLogger("it"), provider, topic=topic, fallback_lane_count=LANES, cache_seconds=0
    )
    await assignments.lane_for("c-1")
    await provider.get_client().script_flush()

    await assignments.lane_for("c-2")

    assert set(await read_lane_map(provider.get_client(), topic)) == {"c-1", "c-2"}
    await assignments.aclose()
