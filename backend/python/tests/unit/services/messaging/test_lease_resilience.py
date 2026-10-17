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
from app.services.redis.standalone_provider import StandaloneRedisProvider

fakeredis_aioredis = pytest.importorskip("fakeredis.aioredis")


@pytest.fixture
def logger() -> logging.Logger:
    return logging.getLogger("test_lease_resilience")


def _fake_backed() -> "patch":
    """Patch the connection provider to hand out one shared fakeredis
    instance for every client, standalone or per-loop registry alike.

    ``create_client`` and ``get_client`` (the latter used internally by
    ``load_script``) are pinned to the same fake instance so SCRIPT LOAD
    and EVALSHA/other commands see one shared keyspace, exactly as they
    would against one real Redis node.
    """
    fake = fakeredis_aioredis.FakeRedis(decode_responses=True)
    return patch.multiple(
        StandaloneRedisProvider,
        create_client=lambda self, *a, **k: fake,
        get_client=lambda self: fake,
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

        with patch.object(registry._provider, "create_client", lambda *a, **k: MagicMock()):
            async def use() -> None:
                seen.append(registry.client())

            asyncio.run(use())   # loop 1, then closed
            asyncio.run(use())   # loop 2 on the same thread

        assert seen[0] is not seen[1]

    @pytest.mark.asyncio
    async def test_noscript_after_a_client_replacement_reloads_once(
        self, logger
    ) -> None:
        """SHAs are loaded once via the provider (R6), not cached per client,
        so a client replaced after a restart still finds a valid SHA. The one
        remaining failure mode -- a NOSCRIPT reply (e.g. a master that never
        saw the original SCRIPT LOAD) -- must reload and retry exactly once,
        not loop or raise."""
        from redis.exceptions import NoScriptError

        manager = DistributedConcurrencyManager(
            logger, RedisConfig(host="redis", port=6379)
        )
        registry = MagicMock()
        registry.provider.load_script = AsyncMock(return_value="acquire-sha-2")
        client = AsyncMock()
        client.evalsha = AsyncMock(side_effect=[NoScriptError("NOSCRIPT"), 1])
        registry.client = MagicMock(return_value=client)
        manager._registry = registry
        manager._acquire_sha = "acquire-sha"
        manager._renew_sha = "renew-sha"

        assert await manager.try_acquire("indexing", "w1", 4, 60) is True

        assert client.evalsha.call_count == 2
        assert manager._acquire_sha == "acquire-sha-2"
        # The reload only re-loads the script that NOSCRIPT'd, not both.
        registry.provider.load_script.assert_called_once()

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


class TestRenewErrorsReachTheDeadlineLogic:
    """A Redis blip must NOT read as "every lease was lost".

    ``LeaseRenewer`` deliberately tolerates failed renew rounds until its
    safety deadline (``_note_failure``) -- a round that raises is expected
    during a blip. If ``renew_many`` instead maps the failure to
    ``renewed=False`` for each lease, ``_renew_once`` marks every handle lost
    on the very first blip *and* refreshes ``_last_success``, so the deadline
    it was supposed to protect never comes into play.
    """

    @pytest.mark.asyncio
    async def test_a_connection_error_propagates_instead_of_reporting_not_renewed(
        self, logger
    ) -> None:
        with _fake_backed():
            manager = await _manager(logger)
            try:
                assert await manager.try_acquire("indexing", "w1", 4, 60) is True

                client = manager._client()
                with patch.object(
                    client,
                    "pipeline",
                    side_effect=ConnectionError("connection reset by peer"),
                ):
                    with pytest.raises(ConnectionError):
                        await manager.renew_many([("indexing", "w1")], 60)
            finally:
                await manager.cleanup()

    @pytest.mark.asyncio
    async def test_the_renewer_does_not_drop_leases_on_a_failed_round(
        self, logger
    ) -> None:
        manager = MagicMock()
        manager.renew_many = AsyncMock(
            side_effect=ConnectionError("connection reset by peer")
        )
        now = [0.0]
        renewer = LeaseRenewer(
            logger,
            manager,
            lease_seconds=60,
            interval_seconds=1,
            clock=lambda: now[0],
        )
        renewer.add("w1", "record:abc")
        handle = renewer.register("w1")

        with pytest.raises(ConnectionError):
            await renewer._renew_once()

        assert not handle.lost.is_set(), (
            "a single failed renew round must not declare the lease lost"
        )
        assert renewer.seconds_since_success == 0.0

        # ...and the deadline still fires once the blip outlasts it.
        now[0] = 120.0
        renewer._note_failure(ConnectionError("still down"))
        assert handle.lost.is_set()

    @pytest.mark.asyncio
    async def test_a_genuine_zero_reply_still_reports_the_lease_lost(
        self, logger
    ) -> None:
        """The failure path above must not swallow real "not renewed" replies:
        a lease taken over by another owner returns 0, not an exception."""
        with _fake_backed():
            manager = await _manager(logger)
            try:
                assert await manager.try_acquire("indexing", "w1", 4, 60) is True
                results = await manager.renew_many(
                    [("indexing", "w1"), ("indexing", "never-acquired")], 60
                )
                assert results[("indexing", "w1")] is True
                assert results[("indexing", "never-acquired")] is False
            finally:
                await manager.cleanup()


class TestRenewManyTransportShape:
    """`renew_many` renews the whole batch, but how differs by transport.

    Standalone pipelines the lot into one round trip. Cluster cannot --
    redis-py's `ClusterPipeline` rejects EVALSHA outright -- so it fans out,
    and that fan-out must stay inside the connection pool it was given or it
    queues behind itself until the pool wait times out (which is how a
    renewal round starts failing under exactly the load it was added for).
    """

    @pytest.mark.asyncio
    async def test_standalone_uses_a_single_pipeline(self, logger) -> None:
        with _fake_backed():
            manager = await _manager(logger)
            try:
                for owner in ("w1", "w2", "w3"):
                    assert await manager.try_acquire("indexing", owner, 8, 60) is True

                client = manager._client()
                real_pipeline = client.pipeline
                calls = []

                def _counting_pipeline(*args, **kwargs):
                    calls.append(1)
                    return real_pipeline(*args, **kwargs)

                with patch.object(client, "pipeline", _counting_pipeline):
                    results = await manager.renew_many(
                        [("indexing", "w1"), ("indexing", "w2"), ("indexing", "w3")],
                        60,
                    )

                assert len(calls) == 1
                assert all(results.values())
            finally:
                await manager.cleanup()

    @pytest.mark.asyncio
    async def test_cluster_never_exceeds_the_pool_size_in_flight(self, logger) -> None:
        with _fake_backed():
            # A pool smaller than the batch makes the semaphore the binding
            # constraint: at the default 32 connections for 20 leases it
            # never blocks, so peak <= pool_size would hold even if the
            # semaphore were removed entirely.
            manager = DistributedConcurrencyManager(
                logger, RedisConfig(host="redis", port=6379), max_connections=4
            )
            await manager.initialize()
            try:
                leases = [("indexing", f"w{i}") for i in range(20)]
                for pool, owner in leases:
                    await manager.try_acquire(pool, owner, 64, 60)

                # Pretend the transport is a cluster so the fan-out path runs.
                manager._is_cluster = lambda: True  # type: ignore[method-assign]
                pool_size = manager._registry.max_connections

                client = manager._client()
                in_flight = 0
                peak = 0
                real_evalsha = client.evalsha

                async def _tracking_evalsha(*args, **kwargs):
                    nonlocal in_flight, peak
                    in_flight += 1
                    peak = max(peak, in_flight)
                    try:
                        await asyncio.sleep(0)
                        return await real_evalsha(*args, **kwargs)
                    finally:
                        in_flight -= 1

                with patch.object(client, "evalsha", _tracking_evalsha):
                    results = await manager.renew_many(leases, 60)

                assert len(results) == len(leases)
                assert peak > 1, "the cluster path must fan out, not serialize"
                assert peak <= pool_size, (
                    f"{peak} concurrent EVALSHA against a pool of {pool_size}: "
                    "the fan-out queues behind itself and times out under load"
                )
            finally:
                await manager.cleanup()
