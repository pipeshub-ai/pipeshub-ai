"""The sweep loop: one replica at a time, and a grace period with a floor."""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.modules.indexing import named_entity_sweep as loop


class _Lock:
    instances: list["_Lock"] = []

    def __init__(self, logger, redis_config, owner, ttl_seconds, key) -> None:
        self.key, self.ttl_seconds = key, ttl_seconds
        self.leader = True
        self.released = False
        _Lock.instances.append(self)

    async def try_acquire(self) -> bool:
        return self.leader

    async def release(self) -> None:
        self.released = True

    async def close(self) -> None:
        return None


async def _one_pass(monkeypatch, *, leader: bool, grace: str | None = None):
    _Lock.instances = []
    sweeper = MagicMock()
    sweeper.return_value.sweep_all = AsyncMock()
    sleeps = []

    async def sleep(seconds):
        sleeps.append(seconds)
        if len(sleeps) > 1:
            raise asyncio.CancelledError

    if grace is not None:
        monkeypatch.setenv("NAMED_ENTITY_ORPHAN_GRACE_SECONDS", grace)

    class _Leader(_Lock):
        def __init__(self, *args, **kwargs) -> None:
            super().__init__(*args, **kwargs)
            self.leader = leader

    container = MagicMock()
    with patch.object(loop, "VectorMembershipBackfillLeaderLock", _Leader), \
            patch.object(loop, "NamedEntitySweeper", sweeper), \
            patch.object(loop, "resolve_entity_store", AsyncMock(return_value=None)), \
            patch.object(loop.MessagingUtils, "_get_redis_config", AsyncMock(return_value={})), \
            patch.object(loop.asyncio, "sleep", sleep), \
            pytest.raises(asyncio.CancelledError):
        await loop.run_named_entity_sweep_loop(container, MagicMock())
    return sweeper, _Lock.instances[0]


async def test_only_the_lease_holder_sweeps(monkeypatch):
    sweeper, lock = await _one_pass(monkeypatch, leader=False)
    sweeper.return_value.sweep_all.assert_not_awaited()
    assert lock.key == loop.LEADER_KEY

    sweeper, lock = await _one_pass(monkeypatch, leader=True)
    sweeper.return_value.sweep_all.assert_awaited_once()
    assert lock.released


async def test_a_grace_period_below_the_floor_is_raised_to_it(monkeypatch):
    sweeper, _ = await _one_pass(monkeypatch, leader=True, grace="1")
    assert sweeper.call_args.kwargs["grace_ms"] == int(loop.MIN_GRACE_SECONDS * 1000)
