"""Unit tests for app.connectors.core.base.connector.instance_lock."""

import asyncio

import pytest

from app.connectors.core.base.connector import instance_lock
from app.connectors.core.base.connector.instance_lock import connector_init_lock


@pytest.fixture(autouse=True)
def _clear_locks():
    instance_lock._init_locks.clear()
    yield
    instance_lock._init_locks.clear()


class TestLockIdentity:
    def test_same_connector_shares_one_lock(self):
        assert connector_init_lock("a") is connector_init_lock("a")

    def test_different_connectors_get_different_locks(self):
        assert connector_init_lock("a") is not connector_init_lock("b")

    @pytest.mark.asyncio
    async def test_concurrent_callers_agree_on_the_lock(self):
        locks = await asyncio.gather(
            *(asyncio.to_thread(connector_init_lock, "a") for _ in range(20))
        )
        assert len({id(lock) for lock in locks}) == 1


class TestSerialization:
    @pytest.mark.asyncio
    async def test_concurrent_get_or_build_builds_once(self):
        """The bug this exists for: N concurrent callers each built their own
        connector, so the per-connector rate limit was multiplied by N."""
        store: dict[str, object] = {}
        builds = 0

        async def get_or_build(connector_id: str) -> object:
            nonlocal builds
            existing = store.get(connector_id)
            if existing is not None:
                return existing
            async with connector_init_lock(connector_id):
                existing = store.get(connector_id)
                if existing is not None:
                    return existing
                builds += 1
                await asyncio.sleep(0.01)  # stands in for init() + connection test
                store[connector_id] = object()
                return store[connector_id]

        results = await asyncio.gather(*(get_or_build("notion") for _ in range(25)))

        assert builds == 1
        assert len({id(r) for r in results}) == 1

    @pytest.mark.asyncio
    async def test_without_the_recheck_every_caller_builds(self):
        """Guards the reason the re-check inside the lock is not optional."""
        store: dict[str, object] = {}
        builds = 0

        async def get_or_build_no_recheck(connector_id: str) -> object:
            nonlocal builds
            if store.get(connector_id) is not None:
                return store[connector_id]
            async with connector_init_lock(connector_id):
                builds += 1
                await asyncio.sleep(0.01)
                store[connector_id] = object()
                return store[connector_id]

        await asyncio.gather(*(get_or_build_no_recheck("notion") for _ in range(10)))
        assert builds == 10

    @pytest.mark.asyncio
    async def test_one_connector_does_not_block_another(self):
        started = asyncio.Event()

        async def hold(connector_id: str) -> None:
            async with connector_init_lock(connector_id):
                started.set()
                await asyncio.sleep(0.3)

        async def other() -> float:
            await started.wait()
            loop = asyncio.get_running_loop()
            t0 = loop.time()
            async with connector_init_lock("other"):
                pass
            return loop.time() - t0

        holder = asyncio.create_task(hold("notion"))
        elapsed = await other()
        await holder
        assert elapsed < 0.1

    @pytest.mark.asyncio
    async def test_lock_is_released_when_a_build_raises(self):
        with pytest.raises(RuntimeError):
            async with connector_init_lock("notion"):
                raise RuntimeError("init blew up")

        lock = connector_init_lock("notion")
        assert not lock.locked()
