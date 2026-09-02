"""Failure-mode coverage for the distributed lease layer.

The production incident these guard against: a stalled worker loop cancelled
in-flight Redis commands, redis-py closed each cancelled command's connection,
an unbounded pool replaced every one with a fresh TCP connect, and every
waiter re-attacked at a flat 0.5s — so Redis hit its 10,000-client limit and
never recovered. Each test here pins one link of that chain, or one of the
recovery paths that has to survive it.
"""
from __future__ import annotations

import asyncio
import logging
import threading
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.messaging.config import RedisConfig
from app.services.messaging.distributed_concurrency import (
    DistributedConcurrencyManager,
    DistributedLeaseSet,
    LeaseKind,
    lease_kind,
)
from app.services.messaging.lease import LeaseRenewer
from app.services.messaging.redis_client import RedisClientRegistry

fakeredis_aioredis = pytest.importorskip("fakeredis.aioredis")


@pytest.fixture
def logger() -> logging.Logger:
    return logging.getLogger("test_lease_resilience")


def _fake_backed() -> "patch":
    """Patch the registry to hand out fakeredis clients, one per loop."""
    return patch.object(
        RedisClientRegistry,
        "_build_client",
        lambda self: fakeredis_aioredis.FakeRedis(decode_responses=True),
    )


async def _manager(logger: logging.Logger) -> DistributedConcurrencyManager:
    manager = DistributedConcurrencyManager(
        logger, RedisConfig(host="redis", port=6379)
    )
    await manager.initialize()
    return manager


class TestLeaseKindClassification:
    """The split that decides whether an unreachable Redis stops the work."""

    @pytest.mark.parametrize(
        "pool",
        ["indexing", "indexing:light", "parsing", "parsing:light"],
    )
    def test_capacity_pools_may_fail_open(self, pool: str) -> None:
        assert lease_kind(pool) is LeaseKind.CAPACITY

    @pytest.mark.parametrize("pool", ["record:abc-123", "recovery"])
    def test_exclusivity_pools_must_fail_closed(self, pool: str) -> None:
        """Failing these open means one record indexed twice, or two replicas
        running stale-record recovery at once."""
        assert lease_kind(pool) is LeaseKind.EXCLUSIVITY

    def test_an_unknown_pool_defaults_to_capacity(self) -> None:
        """A new capacity pool added later must not silently start blocking
        the pipeline whenever Redis blips."""
        assert lease_kind("some-future-pool") is LeaseKind.CAPACITY


class TestRedisRestart:
    @pytest.mark.asyncio
    async def test_leases_lost_to_a_flushed_redis_are_reported_not_assumed(
        self, logger
    ) -> None:
        """Redis restarting without persistence drops the lease keys. Every
        holder has to find out — silently continuing would mean the whole
        fleet believes it holds leases nobody can see."""
        with _fake_backed():
            manager = await _manager(logger)
            try:
                assert await manager.try_acquire("indexing", "w1", 4, 60) is True

                # Simulate the restart: the keys are simply gone.
                await manager._client().flushall()

                assert await manager.renew("indexing", "w1", 60) is False
                # And the pool is immediately usable again for a fresh claim.
                assert await manager.try_acquire("indexing", "w1", 4, 60) is True
            finally:
                await manager.cleanup()

    @pytest.mark.asyncio
    async def test_renewer_marks_every_holder_lost_after_a_flush(self, logger) -> None:
        with _fake_backed():
            manager = await _manager(logger)
            renewer = LeaseRenewer(
                logger, manager, lease_seconds=1.5, interval_seconds=0.02
            )
            try:
                for owner in ("w1", "w2"):
                    assert await manager.try_acquire("indexing", owner, 4, 60)
                    renewer.add(owner, "indexing")
                handles = {o: renewer.register(o) for o in ("w1", "w2")}

                await manager._client().flushall()
                renewer.start()
                await asyncio.wait_for(
                    asyncio.gather(*(h.lost.wait() for h in handles.values())),
                    timeout=3,
                )
            finally:
                await renewer.stop()
                await manager.cleanup()


class TestNetworkDisconnect:
    @pytest.mark.asyncio
    async def test_renewer_survives_a_blip_and_recovers(self, logger) -> None:
        """A few failed rounds inside the lease window must not abort records —
        the lease still has time on it and the next round can recover."""
        manager = AsyncMock()
        calls = {"n": 0}

        async def flaky(leases, lease_seconds) -> dict[tuple[str, str], bool]:
            calls["n"] += 1
            if calls["n"] <= 3:
                raise ConnectionError("connection reset by peer")
            return dict.fromkeys(leases, True)

        manager.renew_many = AsyncMock(side_effect=flaky)
        renewer = LeaseRenewer(
            logger, manager, lease_seconds=30, interval_seconds=0.02
        )
        handle = renewer.register("w1")
        renewer.add("w1", "indexing")

        recovered = asyncio.Event()

        async def watch() -> None:
            # Polling a plain counter the renewer increments from its own task;
            # there is no event to await without reaching into the renewer.
            while calls["n"] < 5:  # noqa: ASYNC110
                await asyncio.sleep(0.01)
            recovered.set()

        watcher = asyncio.create_task(watch())
        renewer.start()
        try:
            await asyncio.wait_for(recovered.wait(), timeout=3)
        finally:
            watcher.cancel()
            await renewer.stop()

        assert not handle.lost.is_set()

    @pytest.mark.asyncio
    async def test_a_reconnect_after_the_deadline_does_not_unmark_a_lost_lease(
        self, logger
    ) -> None:
        """Once a holder is told its lease is gone it must stay gone: the rest
        of the fleet may already have reassigned that record."""

        class Clock:
            now = 0.0

            def __call__(self) -> float:
                return self.now

        clock = Clock()
        manager = AsyncMock()
        manager.renew_many = AsyncMock(side_effect=ConnectionError("down"))
        renewer = LeaseRenewer(
            logger, manager, lease_seconds=1.0, interval_seconds=0.02, clock=clock
        )
        handle = renewer.register("w1")
        renewer.add("w1", "indexing")

        renewer.start()
        try:
            clock.now = 100.0
            await asyncio.wait_for(handle.lost.wait(), timeout=2)
            # Redis comes back.
            manager.renew_many = AsyncMock(return_value={("indexing", "w1"): True})
            await asyncio.sleep(0.1)
        finally:
            await renewer.stop()

        assert handle.lost.is_set()


class TestWorkerThreadRestart:
    """stop() then start() gives the consumer a brand-new event loop. Anything
    still bound to the old one has to be discarded, not reused."""

    def test_a_client_bound_to_a_closed_loop_is_replaced(self, logger) -> None:
        registry = RedisClientRegistry(
            logger,
            RedisConfig(host="redis", port=6379),
            max_connections=4,
            socket_timeout_seconds=2.0,
        )
        seen: list[object] = []

        with patch.object(
            RedisClientRegistry, "_build_client", lambda self: MagicMock()
        ):
            async def use() -> None:
                seen.append(registry.client())

            asyncio.run(use())   # loop 1, then closed
            asyncio.run(use())   # loop 2 on the same thread

        assert seen[0] is not seen[1]

    def test_scripts_are_not_reused_across_a_replaced_client(self, logger) -> None:
        """The subtle one: the script cache used to be keyed by id(client), so
        a client allocated at a freed address inherited Script objects still
        bound to the closed client and every lease op went to a dead pool."""
        manager = DistributedConcurrencyManager(
            logger, RedisConfig(host="redis", port=6379)
        )
        clients = [MagicMock(name="c1"), MagicMock(name="c2")]
        for c in clients:
            c.register_script = MagicMock(side_effect=lambda _s: MagicMock())

        registry = MagicMock()
        registry.client = MagicMock(side_effect=clients)
        manager._registry = registry

        first = manager._scripts()
        second = manager._scripts()

        assert first != second
        assert clients[0].register_script.call_count == 2
        assert clients[1].register_script.call_count == 2

    @pytest.mark.asyncio
    async def test_a_restarted_renewer_does_not_inherit_stale_handles(
        self, logger
    ) -> None:
        """Each worker loop gets its own renewer; a record from the previous
        run must not be renewed by the new one."""
        manager = AsyncMock()
        manager.renew_many = AsyncMock(return_value={})
        old = LeaseRenewer(logger, manager, lease_seconds=30, interval_seconds=0.02)
        old.add("stale-owner", "indexing")

        new = LeaseRenewer(logger, manager, lease_seconds=30, interval_seconds=0.02)

        assert new.register("fresh-owner").pools == set()
        assert "stale-owner" not in new._handles


async def _wait_for_a_renewal_round(manager, timeout: float = 3.0) -> None:
    """Block until the renewer has actually completed a round.

    `LeaseRenewer` floors its interval at 0.1s (lease.py), so a test that asks
    for 0.02s and then sleeps 0.1s is racing the first round — and losing it
    silently, which leaves every assertion downstream vacuously true.
    """
    import time as _time

    deadline = _time.monotonic() + timeout
    while _time.monotonic() < deadline:
        if manager.renew_many.await_args_list:
            return
        await asyncio.sleep(0.01)
    raise AssertionError("the renewer never completed a round")


class TestLeaseSetAndRenewerStayInStep:
    """The consumers add and discard leases from a dozen places across the
    handler pump and two teardown paths. Deriving the renewed set from that
    bookkeeping is what stops a lease being renewed after release."""

    @pytest.mark.asyncio
    async def test_release_stops_renewal_immediately(self, logger) -> None:
        manager = AsyncMock()
        manager.renew_many = AsyncMock(return_value={})
        renewer = LeaseRenewer(logger, manager, lease_seconds=30, interval_seconds=0.02)
        leases = DistributedLeaseSet(renewer=renewer)

        leases.add("indexing", "w1")
        leases.add("parsing:light", "w1")
        leases.discard("parsing:light")

        renewer.start()
        try:
            await _wait_for_a_renewal_round(manager)
        finally:
            await renewer.stop()

        # "indexing" proves a round really happened and that the discard was
        # specific rather than the renewer having stopped altogether — without
        # it, an empty set satisfies the "not in" below while proving nothing.
        renewed_pools = {
            pool
            for call in manager.renew_many.await_args_list
            for pool, _owner in call.args[0]
        }
        assert "indexing" in renewed_pools
        assert "parsing:light" not in renewed_pools

    @pytest.mark.asyncio
    async def test_unregister_stops_renewal_for_every_pool_at_once(
        self, logger
    ) -> None:
        """The teardown path unregisters the owner before it releases each
        lease, so a cancellation mid-teardown cannot leave the renewer
        refreshing a record that has stopped."""
        manager = AsyncMock()
        manager.renew_many = AsyncMock(return_value={})
        renewer = LeaseRenewer(logger, manager, lease_seconds=30, interval_seconds=0.02)
        leases = DistributedLeaseSet(renewer=renewer)
        leases.add("indexing", "w1")
        leases.add("record:r1", "w1")

        # A second owner that stays registered. Without it the renewer has
        # nothing to renew, `_renew_once` returns before calling `renew_many`
        # at all, and "w1 was never renewed" holds for the uninteresting
        # reason that the renewer did nothing whatsoever.
        renewer.add("w2", "indexing")

        renewer.unregister("w1")

        renewer.start()
        try:
            await _wait_for_a_renewal_round(manager)
        finally:
            await renewer.stop()

        renewed_owners = {
            owner
            for call in manager.renew_many.await_args_list
            for _pool, owner in call.args[0]
        }
        assert renewed_owners == {"w2"}, "an unregistered owner was still renewed"


class TestConcurrentLeaseBookkeeping:
    def test_lease_set_is_safe_under_concurrent_mutation(self, logger) -> None:
        """DistributedLeaseSet advertises thread safety; mirroring into the
        renewer must not break that."""
        manager = AsyncMock()
        renewer = LeaseRenewer(logger, manager, lease_seconds=30, interval_seconds=1)
        leases = DistributedLeaseSet(renewer=renewer)
        errors: list[BaseException] = []

        def churn(n: int) -> None:
            try:
                for i in range(200):
                    leases.add(f"pool-{n}-{i}", f"owner-{n}")
                    leases.discard(f"pool-{n}-{i}")
            except BaseException as exc:  # recorded, asserted on below
                errors.append(exc)

        threads = [threading.Thread(target=churn, args=(n,)) for n in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert errors == []
        assert leases.snapshot() == []

    def test_a_discard_cannot_land_between_an_add_and_its_renewer_mirror(
        self, logger
    ) -> None:
        """The churn test above uses a pool name per thread, so it can only
        prove the structures survive concurrent use. This forces the one
        interleaving that matters: a discard of the *same* pool arriving while
        an add is partway through. If the renewer mirror sits outside the
        lock, the discard pops the pool before the renewer has it, finds
        nothing to remove, and the add then registers a lease that is already
        released — renewed for the rest of the owner's life.
        """
        manager = AsyncMock()
        renewer = LeaseRenewer(logger, manager, lease_seconds=30, interval_seconds=1)
        leases = DistributedLeaseSet(renewer=renewer)

        inside_mirror = threading.Event()
        real_add = renewer.add

        def slow_add(owner: str, pool: str) -> None:
            inside_mirror.set()
            time.sleep(0.05)  # the window a racing discard would exploit
            real_add(owner, pool)

        renewer.add = slow_add

        adder = threading.Thread(target=leases.add, args=("indexing", "w1"))
        adder.start()
        assert inside_mirror.wait(timeout=2.0)
        discarder = threading.Thread(target=leases.discard, args=("indexing",))
        discarder.start()
        adder.join(timeout=5.0)
        discarder.join(timeout=5.0)
        assert not adder.is_alive() and not discarder.is_alive()

        assert leases.snapshot() == []
        handle = renewer._handles.get("w1")
        assert handle is None or handle.pools == set(), (
            "the renewer is still holding a lease the lease set has released"
        )


class TestConsumerLifecycle:
    """Exercises the real worker thread, not a stand-in: the renewer is
    created inside it, bound to its loop, and has to go away with it."""

    def _consumer(self, logger: logging.Logger) -> object:
        from app.services.messaging.config import RedisStreamsConfig
        from app.services.messaging.redis_streams.indexing_consumer import (
            IndexingRedisStreamsConsumer,
        )

        config = RedisStreamsConfig(
            host="localhost", port=6379, db=0, client_id="c", group_id="g",
            topics=["record-events"],
        )
        manager = MagicMock()
        manager.renew_many = AsyncMock(return_value={})
        return IndexingRedisStreamsConsumer(
            logger, config, concurrency_manager=manager
        )

    def test_worker_thread_start_creates_a_renewer_on_its_own_loop(
        self, logger
    ) -> None:
        consumer = self._consumer(logger)
        consumer._start_worker_thread()
        try:
            assert consumer.worker_loop_ready.wait(timeout=5.0)
            assert consumer.lease_renewer is not None
        finally:
            consumer._stop_worker_thread()

    def test_shutdown_drops_the_renewer_with_its_loop(self, logger) -> None:
        """Left dangling, a lease set built after the restart could attach to
        a renewer whose task is cancelled and whose loop is closed, and then
        silently never renew."""
        consumer = self._consumer(logger)
        consumer._start_worker_thread()
        try:
            assert consumer.worker_loop_ready.wait(timeout=5.0)
        finally:
            consumer._stop_worker_thread()

        assert consumer.lease_renewer is None

    def test_restart_builds_a_fresh_renewer(self, logger) -> None:
        consumer = self._consumer(logger)
        consumer._start_worker_thread()
        try:
            assert consumer.worker_loop_ready.wait(timeout=5.0)
            first = consumer.lease_renewer
        finally:
            consumer._stop_worker_thread()

        consumer._start_worker_thread()
        try:
            assert consumer.worker_loop_ready.wait(timeout=5.0)
            assert consumer.lease_renewer is not None
            assert consumer.lease_renewer is not first
        finally:
            consumer._stop_worker_thread()

    def test_no_renewer_when_distributed_concurrency_is_disabled(
        self, logger
    ) -> None:
        """DISTRIBUTED_INDEXING_CONCURRENCY=false leaves concurrency_manager
        None, and nothing to renew."""
        consumer = self._consumer(logger)
        consumer.concurrency_manager = None
        consumer._start_worker_thread()
        try:
            assert consumer.worker_loop_ready.wait(timeout=5.0)
            assert consumer.lease_renewer is None
        finally:
            consumer._stop_worker_thread()
