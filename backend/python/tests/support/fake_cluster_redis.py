"""In-memory Redis Cluster double for unit tests (R5, R17).

Wraps a single `fakeredis.aioredis.FakeRedis` instance and enforces the one
Redis Cluster rule that `fakeredis` does not: multi-key commands whose keys
land in different hash slots must raise, exactly as `redis-py`'s
`RedisCluster` does for `MGET`/`EVAL`/`EVALSHA`. This is what makes a unit
test fail if shared code (`RetryManager`, `AccessibleRecordsCache`,
`DistributedConcurrencyManager`, ...) regresses back to a multi-key command
that only breaks in production against a real cluster or AWS MemoryDB.

`DEL`/`EXISTS` are deliberately NOT slot-checked here: `redis-py`'s
`RedisClusterPipeline`/command layer already splits those per slot
internally, so they are safe today (see the compatibility matrix in the
MemoryDB readiness plan) -- only `MGET`, `EVAL`, and `EVALSHA` are genuinely
atomic-and-unsplit in a way that raises CROSSSLOT.

Usage::

    fake = FakeClusterRedis()
    manager = RetryManager(logger=..., redis_client=fake)
    await manager.initialize()
    await manager.has_pending_retries(["a", "b"])  # ids may span slots
"""
from __future__ import annotations

from typing import Any

import pytest
from redis.crc import key_slot
from redis.exceptions import ClusterCrossSlotError

# `importorskip`, not a plain `import`: `fakeredis` lives in the optional
# `dev` extra, and every importer of this helper had its own
# `pytest.importorskip("fakeredis.aioredis")` guard placed *below* the line
# that imports this module -- so without the extra installed, collection
# raised ModuleNotFoundError instead of skipping. Guarding here covers every
# importer at once, including the ones that import this lazily inside a test
# body, and any added later.
fakeredis = pytest.importorskip("fakeredis")

__all__ = ["FakeClusterRedis"]


def _to_bytes(key: Any) -> bytes:  # noqa: ANN401 - mirrors redis-py's own key coercion
    return key.encode() if isinstance(key, str) else key


def _assert_same_slot(keys: "list[Any]") -> None:
    if len(keys) <= 1:
        return
    slots = {key_slot(_to_bytes(k)) for k in keys}
    if len(slots) > 1:
        raise ClusterCrossSlotError(
            f"Command spans multiple hash slots: {keys!r} -> slots {sorted(slots)}"
        )


class FakeClusterRedis:
    """`redis.asyncio.Redis`-shaped double that raises CROSSSLOT like a real
    Redis Cluster / MemoryDB deployment would, backed by one in-process
    `fakeredis` server (single-node SCAN and all, since only the multi-key
    command rules -- not sharding -- are what shared code must respect)."""

    def __init__(
        self,
        *,
        server: "fakeredis.FakeServer | None" = None,
        version: tuple[int, ...] = (7,),
        decode_responses: bool = True,
    ) -> None:
        self._server = server or fakeredis.FakeServer(version=version)
        self._redis = fakeredis.aioredis.FakeRedis(
            server=self._server, decode_responses=decode_responses
        )

    @property
    def server(self) -> "fakeredis.FakeServer":
        """Pass to another `FakeClusterRedis(server=...)` so both wrap the
        same in-memory dataset -- mirrors several client objects (`get_client()`,
        `create_client()`, pub/sub) all talking to one real Redis/MemoryDB
        endpoint."""
        return self._server

    async def mget(self, keys: "list[Any]", *args: Any) -> list[Any]:  # noqa: ANN401
        all_keys = list(keys) + list(args) if not isinstance(keys, (str, bytes)) else [keys, *args]
        _assert_same_slot(all_keys)
        return await self._redis.mget(all_keys)

    async def eval(self, script: str, numkeys: int, *keys_and_args: Any) -> Any:  # noqa: ANN401
        _assert_same_slot(list(keys_and_args[:numkeys]))
        return await self._redis.eval(script, numkeys, *keys_and_args)

    async def evalsha(self, sha: str, numkeys: int, *keys_and_args: Any) -> Any:  # noqa: ANN401
        _assert_same_slot(list(keys_and_args[:numkeys]))
        return await self._redis.evalsha(sha, numkeys, *keys_and_args)

    def pipeline(self, transaction: bool = True) -> Any:  # noqa: ANN401
        """Non-transactional pipelines legitimately span slots (a real
        `ClusterPipeline` routes each command to its own node); `transaction=True`
        (WATCH/MULTI/EXEC) does not survive a cluster hop (R3) but nothing in
        this codebase uses it anymore, so it is intentionally left unchecked
        here rather than half-emulated.
        """
        return self._redis.pipeline(transaction=transaction)

    def __getattr__(self, name: str) -> Any:  # noqa: ANN401
        return getattr(self._redis, name)
