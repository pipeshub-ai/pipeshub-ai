"""Unit tests for consumer_concurrency — bridging, distributed leases,
gate-waiter tokens, and ceiling helpers."""

from __future__ import annotations

import asyncio
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.messaging.consumer_concurrency import (
    Admission,
    GateWaiterToken,
    _normalize_operation,
    acquire_distributed_slot,
    acquire_index_slot,
    bridge_to_main_loop,
    clear_retry_tracking,
    effective_index_tier,
    get_gate_waiter_count,
    get_retry_count,
    increment_retry_and_check,
    index_ceiling,
    index_gates_saturated,
    index_lease_pool,
    log_distributed_error,
    parse_ceiling,
    parse_lease_pool,
    pending_task_ceiling,
    release_admission,
    release_distributed_slot,
    report_memory_incident_if_applicable,
)
from app.services.messaging.distributed_concurrency import DistributedLeaseSet
from app.services.messaging.lease import LeaseRenewer
from app.services.resource_governor.models import ParseTier, Pool


def _make_host(
    running=True,
    main_loop=None,
    concurrency_manager=None,
    retry_manager=None,
    governor=None,
    parsing_semaphore=None,
    gate_waiters=0,
):
    host = SimpleNamespace(
        logger=MagicMock(),
        running=running,
        main_loop=main_loop,
        concurrency_manager=concurrency_manager,
        retry_manager=retry_manager,
        _distributed_log_times={},
        governor=governor,
        parsing_semaphore=parsing_semaphore,
        _gate_waiters=gate_waiters,
        _futures_lock=threading.Lock(),
    )
    return host


# ---------------------------------------------------------------------------
# _normalize_operation
# ---------------------------------------------------------------------------


class TestNormalizeOperation:
    def test_plain_operation(self):
        assert _normalize_operation("acquire:indexing") == "acquire:indexing"

    def test_record_operation_collapsed(self):
        assert _normalize_operation("acquire:record:abc-123") == "acquire:record"

    def test_nested_record_operation(self):
        assert _normalize_operation("release:record:some-id") == "release:record"


# ---------------------------------------------------------------------------
# bridge_to_main_loop
# ---------------------------------------------------------------------------


class TestBridgeToMainLoop:
    @pytest.mark.asyncio
    async def test_same_loop_direct_execution(self):
        host = _make_host(main_loop=asyncio.get_running_loop())

        async def coro():
            return 42

        result = await bridge_to_main_loop(host, coro())
        assert result == 42

    @pytest.mark.asyncio
    async def test_none_main_loop_direct_execution(self):
        host = _make_host(main_loop=None)

        async def coro():
            return 99

        result = await bridge_to_main_loop(host, coro())
        assert result == 99

    @pytest.mark.asyncio
    async def test_stopped_main_loop_raises(self):
        loop = MagicMock()
        loop.is_running.return_value = False
        host = _make_host(main_loop=loop)

        async def coro():
            return 1

        current_loop = asyncio.get_running_loop()
        with patch("app.services.messaging.consumer_concurrency.asyncio.get_running_loop", return_value=MagicMock()):
            with pytest.raises(RuntimeError, match="not running"):
                await bridge_to_main_loop(host, coro())


# ---------------------------------------------------------------------------
# log_distributed_error
# ---------------------------------------------------------------------------


class TestLogDistributedError:
    def test_first_log(self):
        host = _make_host()
        log_distributed_error(host, "acquire:indexing", RuntimeError("fail"))
        host.logger.warning.assert_called_once()

    def test_throttled_within_30s(self):
        host = _make_host()
        host._distributed_log_times["acquire:indexing"] = float("inf")
        log_distributed_error(host, "acquire:indexing", RuntimeError("fail"))
        host.logger.warning.assert_not_called()

    def test_record_operation_collapsed(self):
        host = _make_host()
        log_distributed_error(host, "acquire:record:abc", RuntimeError("fail"))
        assert "acquire:record" in host._distributed_log_times


# ---------------------------------------------------------------------------
# acquire_distributed_slot
# ---------------------------------------------------------------------------


@contextmanager
def _lease_env(*, budget: int = 5, max_backoff: float = 0.04) -> Iterator[MagicMock]:
    """messaging_env stubbed for the lease-acquire path.

    Patched wholesale (the module reads properties off it), so every knob the
    backoff uses has to be a real number or min()/uniform() get a MagicMock.
    """
    with patch("app.services.messaging.consumer_concurrency.messaging_env") as env:
        env.concurrency_lease_seconds = 30
        env.concurrency_acquire_poll_seconds = 0.01
        env.concurrency_acquire_max_backoff_seconds = max_backoff
        env.concurrency_failure_budget = budget
        yield env


class TestAcquireDistributedSlot:
    @pytest.mark.asyncio
    async def test_no_manager_returns_true(self):
        host = _make_host(concurrency_manager=None)
        result = await acquire_distributed_slot(host, "pool", "owner", 5)
        assert result is True

    @pytest.mark.asyncio
    async def test_acquired_on_first_try(self):
        manager = AsyncMock()
        manager.try_acquire = AsyncMock(return_value=True)
        host = _make_host(concurrency_manager=manager, main_loop=asyncio.get_running_loop())

        result = await acquire_distributed_slot(host, "pool", "owner", 5)
        assert result is True

    @pytest.mark.asyncio
    async def test_not_running_returns_false(self):
        manager = AsyncMock()
        manager.try_acquire = AsyncMock(return_value=False)
        host = _make_host(
            running=False,
            concurrency_manager=manager,
            main_loop=asyncio.get_running_loop(),
        )
        result = await acquire_distributed_slot(host, "pool", "owner", 5)
        assert result is False

    @pytest.mark.asyncio
    async def test_deadline_expires(self):
        manager = AsyncMock()
        manager.try_acquire = AsyncMock(return_value=False)
        host = _make_host(
            concurrency_manager=manager,
            main_loop=asyncio.get_running_loop(),
        )
        with _lease_env():
            result = await acquire_distributed_slot(host, "pool", "owner", 5, deadline_seconds=0.05)
        assert result is False

    @pytest.mark.asyncio
    async def test_backoff_never_outlives_the_callers_deadline(self) -> None:
        """The record lease waits 10s while the backoff grows to 5s, so an
        unclamped sleep would hold the outer index permit up to half again as
        long as the caller asked for — the convoy the deadline exists to
        prevent."""
        manager = AsyncMock()
        manager.try_acquire = AsyncMock(return_value=False)
        host = _make_host(
            concurrency_manager=manager,
            main_loop=asyncio.get_running_loop(),
        )
        deadline = 0.08
        # A backoff cap far larger than the deadline: unclamped, the first
        # sleep alone overruns it many times over.
        with _lease_env(max_backoff=2.0):
            start = time.monotonic()
            result = await acquire_distributed_slot(
                host, "record:r1", "owner", 1, deadline_seconds=deadline
            )
            elapsed = time.monotonic() - start

        assert result is False
        assert elapsed < deadline * 4, (
            f"gave the permit back after {elapsed:.3f}s for a {deadline}s deadline"
        )

    async def test_contention_backs_off_instead_of_hammering_redis(self) -> None:
        """The flat 0.5s poll is what made an outage self-sustaining: load on
        Redis scaled with the size of the queue waiting on it, so the busier
        the pipeline the harder it hit the thing it was waiting for. Backoff
        has to bound the attempts a single waiter makes over a window."""
        manager = AsyncMock()
        manager.try_acquire = AsyncMock(return_value=False)
        host = _make_host(
            concurrency_manager=manager,
            main_loop=asyncio.get_running_loop(),
        )
        # base 0.01s growing to a 0.2s cap, mirroring the shipped 0.5s->5s
        # ratio at a scale a unit test can wait out.
        with _lease_env(max_backoff=0.2):
            result = await acquire_distributed_slot(
                host, "parsing:light", "owner", 5, deadline_seconds=0.5
            )
        assert result is False
        # The old flat 0.01s poll would make ~50 attempts in this window.
        assert manager.try_acquire.await_count <= 12

    async def test_deadline_is_honoured_while_redis_is_failing(self) -> None:
        """The error path must respect the caller's deadline too. It used to
        loop straight back to the top, so a record holding the outer index
        permit kept waiting on its 10s per-record lease for as long as the
        failure budget lasted — the case where giving the permit back and
        letting the message be retried matters most."""
        manager = AsyncMock()
        manager.try_acquire = AsyncMock(side_effect=ConnectionError("redis down"))
        host = _make_host(
            concurrency_manager=manager,
            main_loop=asyncio.get_running_loop(),
        )
        started = time.monotonic()
        with _lease_env(budget=1000, max_backoff=0.02):
            result = await acquire_distributed_slot(
                host, "record:abc", "owner", 1, deadline_seconds=0.05
            )
        elapsed = time.monotonic() - started

        assert result is False
        # Bounded by the deadline, not by the (deliberately huge) budget.
        assert elapsed < 1.0
        assert manager.try_acquire.await_count < 1000

    async def test_capacity_lease_fails_open_once_the_budget_is_spent(self) -> None:
        """A capacity lease is a cluster-wide cap over node-local gates. If
        Redis is unreachable, continuing under the local gate over-admits
        across the fleet but keeps every node working — the same bounded
        degradation DISTRIBUTED_INDEXING_CONCURRENCY=false chooses on purpose.
        Blocking forever instead is what wedged production."""
        manager = AsyncMock()
        manager.try_acquire = AsyncMock(side_effect=ConnectionError("redis down"))
        host = _make_host(
            concurrency_manager=manager,
            main_loop=asyncio.get_running_loop(),
        )
        with _lease_env(budget=3):
            result = await acquire_distributed_slot(host, "parsing:light", "owner", 5)
        assert result is True
        assert manager.try_acquire.await_count == 3

    async def test_record_lease_fails_closed_once_the_budget_is_spent(self) -> None:
        """The per-record lease is mutual exclusion, not capacity: failing it
        open would let two deliveries of the same record index concurrently.
        It refuses instead, leaving the message for a retry that can take the
        lease properly."""
        manager = AsyncMock()
        manager.try_acquire = AsyncMock(side_effect=ConnectionError("redis down"))
        host = _make_host(
            concurrency_manager=manager,
            main_loop=asyncio.get_running_loop(),
        )
        with _lease_env(budget=3):
            result = await acquire_distributed_slot(host, "record:abc", "owner", 1)
        assert result is False

    async def test_a_recovered_redis_resets_the_failure_budget(self) -> None:
        """A blip must not accumulate toward the budget across an hour of
        healthy operation, or a long-lived consumer eventually fails open for
        no current reason."""
        manager = AsyncMock()
        manager.try_acquire = AsyncMock(
            side_effect=[ConnectionError("blip"), False, False, True]
        )
        host = _make_host(
            concurrency_manager=manager,
            main_loop=asyncio.get_running_loop(),
        )
        with _lease_env(budget=2):
            result = await acquire_distributed_slot(host, "indexing", "owner", 5)
        assert result is True
        assert manager.try_acquire.await_count == 4


# ---------------------------------------------------------------------------
# release_distributed_slot
# ---------------------------------------------------------------------------


class TestReleaseDistributedSlot:
    @pytest.mark.asyncio
    async def test_no_manager_noop(self):
        host = _make_host(concurrency_manager=None)
        await release_distributed_slot(host, "pool", "owner")

    @pytest.mark.asyncio
    async def test_release_called(self):
        manager = AsyncMock()
        manager.release = AsyncMock()
        host = _make_host(concurrency_manager=manager, main_loop=asyncio.get_running_loop())
        await release_distributed_slot(host, "pool", "owner")
        manager.release.assert_called_once_with("pool", "owner")

    @pytest.mark.asyncio
    async def test_release_error_logged(self):
        manager = AsyncMock()
        manager.release = AsyncMock(side_effect=RuntimeError("redis down"))
        host = _make_host(concurrency_manager=manager, main_loop=asyncio.get_running_loop())
        await release_distributed_slot(host, "pool", "owner")
        host.logger.warning.assert_called()


# ---------------------------------------------------------------------------
# Retry helpers
# ---------------------------------------------------------------------------


class TestRetryHelpers:
    @pytest.mark.asyncio
    async def test_clear_retry_no_manager(self):
        host = _make_host(retry_manager=None)
        await clear_retry_tracking(host, "msg-1")

    @pytest.mark.asyncio
    async def test_clear_retry_calls_manager(self):
        rm = AsyncMock()
        rm.clear = AsyncMock()
        host = _make_host(retry_manager=rm, main_loop=asyncio.get_running_loop())
        await clear_retry_tracking(host, "msg-1")
        rm.clear.assert_called_once_with("msg-1")

    @pytest.mark.asyncio
    async def test_get_retry_count_no_manager(self):
        host = _make_host(retry_manager=None)
        count = await get_retry_count(host, "msg-1")
        assert count == 0

    @pytest.mark.asyncio
    async def test_get_retry_count_with_manager(self):
        rm = AsyncMock()
        rm.get_count = AsyncMock(return_value=3)
        host = _make_host(retry_manager=rm, main_loop=asyncio.get_running_loop())
        count = await get_retry_count(host, "msg-1")
        assert count == 3

    @pytest.mark.asyncio
    async def test_increment_retry_no_manager(self):
        host = _make_host(retry_manager=None)
        count, exhausted = await increment_retry_and_check(host, "msg-1")
        assert count == 0
        assert exhausted is False

    @pytest.mark.asyncio
    async def test_increment_retry_with_manager(self):
        rm = AsyncMock()
        rm.increment_and_check = AsyncMock(return_value=(2, True))
        host = _make_host(retry_manager=rm, main_loop=asyncio.get_running_loop())
        count, exhausted = await increment_retry_and_check(host, "msg-1")
        assert count == 2
        assert exhausted is True


# ---------------------------------------------------------------------------
# Ceiling helpers
# ---------------------------------------------------------------------------


class TestCeilings:
    def test_index_ceiling_with_governor(self, monkeypatch):
        monkeypatch.setenv("INDEXING_SPLIT_LEASE_POOLS", "true")
        gov = SimpleNamespace(
            ceilings=SimpleNamespace(index=10, index_heavy=4, index_light=6)
        )
        host = _make_host(governor=gov)
        assert index_ceiling(host, ParseTier.HEAVY) == 4
        assert index_ceiling(host, ParseTier.LIGHT) == 6

    def test_index_ceiling_defaults_to_heavy_for_an_unknown_tier(self, monkeypatch) -> None:
        """classify() resolves anything it does not recognise to HEAVY, and
        the ceiling lookup has to agree — an unclassifiable record must draw
        on the smaller budget, never the one sized for fast records."""
        monkeypatch.setenv("INDEXING_SPLIT_LEASE_POOLS", "true")
        gov = SimpleNamespace(
            ceilings=SimpleNamespace(index=10, index_heavy=4, index_light=6)
        )
        host = _make_host(governor=gov)
        assert index_ceiling(host, None) == 4

    def test_index_ceiling_without_governor(self):
        host = _make_host(governor=None)
        with patch("app.services.messaging.consumer_concurrency.messaging_env") as env:
            env.max_concurrent_indexing = 4
            assert index_ceiling(host) == 4

    def test_parse_ceiling_heavy_with_governor(self):
        gov = SimpleNamespace(ceilings=SimpleNamespace(heavy=8, light=20))
        host = _make_host(governor=gov)
        assert parse_ceiling(host, ParseTier.HEAVY) == 8

    def test_parse_ceiling_light_with_governor(self):
        gov = SimpleNamespace(ceilings=SimpleNamespace(heavy=8, light=20))
        host = _make_host(governor=gov)
        assert parse_ceiling(host, ParseTier.LIGHT) == 20

    def test_parse_ceiling_none_tier_defaults_heavy(self):
        gov = SimpleNamespace(ceilings=SimpleNamespace(heavy=8, light=20))
        host = _make_host(governor=gov)
        assert parse_ceiling(host, None) == 8

    def test_parse_ceiling_without_governor(self):
        host = _make_host(governor=None)
        with patch("app.services.messaging.consumer_concurrency.messaging_env") as env:
            env.max_concurrent_parsing = 6
            assert parse_ceiling(host) == 6


class TestParseLeasePool:
    def test_light_pool(self):
        assert parse_lease_pool(ParseTier.LIGHT) == "parsing:light"

    def test_heavy_pool(self):
        assert parse_lease_pool(ParseTier.HEAVY) == "parsing"

    def test_none_defaults_heavy(self):
        assert parse_lease_pool(None) == "parsing"



class TestIndexLeasePoolSplitFlag:
    """The cluster-wide lease pool for light records is split only once
    INDEXING_SPLIT_LEASE_POOLS is on, because a previous-build replica admits
    every record into the shared ``indexing`` pool at the full budget. While
    both builds run, a separate light pool is additive and the fleet can
    exceed MAX_CONCURRENT_INDEXING by ``index_light``."""

    def _gov(self) -> None:
        return SimpleNamespace(
            ceilings=SimpleNamespace(index=10, index_heavy=4, index_light=6)
        )

    def test_off_by_default_light_shares_the_legacy_pool_and_budget(self) -> None:
        host = _make_host(governor=self._gov())

        assert index_lease_pool(ParseTier.LIGHT) == "indexing"
        assert index_lease_pool(ParseTier.HEAVY) == "indexing"
        # Both tiers must send the *total*, matching what a previous-build
        # replica sends for the same pool — the Lua script enforces whichever
        # limit the caller passes, so a smaller one here would shrink the
        # shared pool for everybody.
        assert index_ceiling(host, ParseTier.LIGHT) == 10
        assert index_ceiling(host, ParseTier.HEAVY) == 10

    def test_on_light_takes_its_own_pool_and_tier_budget(self, monkeypatch) -> None:
        monkeypatch.setenv("INDEXING_SPLIT_LEASE_POOLS", "true")
        host = _make_host(governor=self._gov())

        assert index_lease_pool(ParseTier.LIGHT) == "indexing:light"
        assert index_lease_pool(ParseTier.HEAVY) == "indexing"
        assert index_ceiling(host, ParseTier.LIGHT) == 6
        assert index_ceiling(host, ParseTier.HEAVY) == 4

    def test_heavy_keeps_the_legacy_pool_name_in_both_modes(self, monkeypatch) -> None:
        """Renaming the heavy pool would split it from previous-build
        replicas too, doubling the cluster-wide cap during any rollout."""
        assert index_lease_pool(None) == "indexing"
        monkeypatch.setenv("INDEXING_SPLIT_LEASE_POOLS", "true")
        assert index_lease_pool(None) == "indexing"


# ---------------------------------------------------------------------------
# GateWaiterToken
# ---------------------------------------------------------------------------





class TestOnlyGrantedLeasesAreRecorded:
    """A capacity lease fails *open*: once the failure budget is spent,
    acquire returns True and the record proceeds under the node-local gate
    alone. It holds no Redis lease at that point, and must not be recorded as
    holding one — the renewer would get 0 back for it the moment Redis
    recovered and mark the owner lost, aborting a record that was processing
    perfectly well under its real leases. Because every in-flight record fails
    open together, that would abort the whole batch at once, at exactly the
    moment Redis had just come back."""

    def _host(self, side_effect=None, return_value=None) -> None:
        manager = AsyncMock()
        manager.try_acquire = AsyncMock(side_effect=side_effect, return_value=return_value)
        return _make_host(
            concurrency_manager=manager, main_loop=asyncio.get_running_loop()
        )

    def _leases(self) -> None:
        renewer = LeaseRenewer(
            MagicMock(), AsyncMock(), lease_seconds=30, interval_seconds=1
        )
        return DistributedLeaseSet(renewer=renewer), renewer

    @pytest.mark.parametrize("pool", ["indexing", "indexing:light", "parsing", "parsing:light"])
    @pytest.mark.asyncio
    async def test_a_failed_open_capacity_lease_is_not_recorded(self, pool) -> None:
        host = self._host(side_effect=ConnectionError("redis down"))
        leases, renewer = self._leases()

        with _lease_env(budget=3):
            admitted = await acquire_distributed_slot(
                host, pool, "w1", 4, leases=leases
            )

        assert admitted is True, "capacity leases fail open so the node keeps working"
        assert leases.snapshot() == []
        assert renewer._handles.get("w1") is None

    @pytest.mark.parametrize("pool", ["indexing", "parsing:light"])
    @pytest.mark.asyncio
    async def test_a_record_survives_redis_recovering_after_a_failed_open_lease(
        self, pool
    ) -> None:
        """The regression: fail open on a capacity pool, then let Redis come
        back and renew. The owner must not be marked lost."""
        host = self._host(side_effect=ConnectionError("redis down"))
        leases, renewer = self._leases()
        with _lease_env(budget=3):
            await acquire_distributed_slot(host, pool, "w1", 4, leases=leases)

        # Redis is back: the real per-record lease is granted and recorded.
        host.concurrency_manager.try_acquire = AsyncMock(return_value=True)
        with _lease_env(budget=3):
            await acquire_distributed_slot(host, "record:r1", "w1", 1, leases=leases)
        handle = renewer.register("w1")

        renewer._manager.renew_many = AsyncMock(
            return_value={("record:r1", "w1"): True}
        )
        await renewer._renew_once()

        assert renewer._manager.renew_many.await_args.args[0] == [("record:r1", "w1")]
        assert not handle.lost.is_set(), (
            "a lease Redis never granted was renewed and lost the record"
        )

    @pytest.mark.asyncio
    async def test_a_granted_lease_is_recorded(self) -> None:
        host = self._host(return_value=True)
        leases, renewer = self._leases()

        with _lease_env():
            assert await acquire_distributed_slot(
                host, "indexing", "w1", 4, leases=leases
            ) is True

        assert leases.snapshot() == [("indexing", "w1")]
        assert renewer._handles["w1"].pools == {"indexing"}

    @pytest.mark.asyncio
    async def test_a_denied_exclusivity_lease_is_not_recorded(self) -> None:
        """record:<id> fails closed, so a denial is a denial — nothing to
        record, and the caller gives the permit back."""
        host = self._host(side_effect=ConnectionError("redis down"))
        leases, _ = self._leases()

        with _lease_env(budget=3):
            admitted = await acquire_distributed_slot(
                host, "record:r1", "w1", 1, leases=leases
            )

        assert admitted is False
        assert leases.snapshot() == []


class TestEffectiveIndexTier:
    """When MAX_CONCURRENT_INDEXING is too small to split — a total of 1
    cannot be two tiers each floored at 1 — resolve_ceilings collapses light
    to zero and everything routes to heavy. The gate, the lease limit and the
    lease pool name all have to agree, or the cap is not actually enforced:
    AdmissionGate admits into an empty pool regardless of its limit, and the
    distributed lease rejects a limit below 1 outright."""

    def _host(self, index_light: int) -> None:
        return _make_host(
            governor=SimpleNamespace(
                ceilings=SimpleNamespace(
                    index=1 + index_light, index_heavy=1, index_light=index_light
                )
            )
        )

    def test_light_collapses_to_heavy_when_it_has_no_budget(self) -> None:
        host = self._host(index_light=0)
        assert effective_index_tier(host, ParseTier.LIGHT) is ParseTier.HEAVY
        assert effective_index_tier(host, ParseTier.HEAVY) is ParseTier.HEAVY
        assert effective_index_tier(host, None) is ParseTier.HEAVY

    def test_light_is_left_alone_when_it_has_a_budget(self) -> None:
        host = self._host(index_light=6)
        assert effective_index_tier(host, ParseTier.LIGHT) is ParseTier.LIGHT
        assert effective_index_tier(host, ParseTier.HEAVY) is ParseTier.HEAVY

    def test_unknown_tier_resolves_to_heavy(self) -> None:
        """classify() resolves anything it does not recognise to HEAVY, and
        this has to agree — an unclassifiable record draws on the smaller
        budget, never the one sized for fast records."""
        assert effective_index_tier(self._host(6), None) is ParseTier.HEAVY

    def test_without_a_governor_the_tier_is_unchanged(self) -> None:
        host = _make_host(governor=None)
        assert effective_index_tier(host, ParseTier.LIGHT) is ParseTier.LIGHT

    def test_it_is_idempotent(self) -> None:
        """Consumers resolve once at the top of a message and pass the result
        down, so re-resolving must not shift the answer again."""
        host = self._host(index_light=0)
        once = effective_index_tier(host, ParseTier.LIGHT)
        assert effective_index_tier(host, once) is once

    def test_a_collapsed_light_tier_never_asks_for_a_zero_lease_limit(self) -> None:
        """try_acquire rejects a limit below 1, and acquire_distributed_slot
        cannot tell that ValueError from a Redis failure — it would back off
        through the failure budget on every single light record."""
        host = self._host(index_light=0)
        assert index_ceiling(host, ParseTier.LIGHT) >= 1


class TestIndexGatesSaturated:
    """Backpressure used to count only tasks *queued* for admission, and a
    task stopped counting the moment it was admitted. With both index pools
    full and nothing queued behind them — the steady state while a batch of
    slow records is in flight — the waiter count reads zero while the node
    cannot start a single further record, and the consumer keeps claiming
    messages that then sit in its PEL or hold a Kafka partition."""

    def _host(self, heavy: tuple[int, int], light: tuple[int, int]) -> None:
        """(in_use, limit) per pool, as plain stand-ins for the real gates."""
        gates = {
            Pool.INDEX_HEAVY: SimpleNamespace(in_use=heavy[0], limit=heavy[1]),
            Pool.INDEX_LIGHT: SimpleNamespace(in_use=light[0], limit=light[1]),
        }
        return _make_host(governor=SimpleNamespace(gate=lambda p: gates[p]))

    def test_both_pools_full_is_saturated(self) -> None:
        assert index_gates_saturated(self._host(heavy=(4, 4), light=(6, 6))) is True

    def test_room_in_either_pool_is_not_saturated(self) -> None:
        """The check is ``all``, not ``any``, on purpose: a full heavy pool
        must not stop the node reading records the light pool can still
        start — that is the head-of-line blocking the tier split removes."""
        assert index_gates_saturated(self._host(heavy=(4, 4), light=(0, 6))) is False
        assert index_gates_saturated(self._host(heavy=(0, 4), light=(6, 6))) is False

    def test_idle_is_not_saturated(self) -> None:
        assert index_gates_saturated(self._host(heavy=(0, 4), light=(0, 6))) is False

    def test_over_limit_counts_as_saturated(self) -> None:
        """AdmissionGate's deadlock guard admits an oversized request alone,
        so in_use can legitimately exceed limit."""
        assert index_gates_saturated(self._host(heavy=(5, 4), light=(6, 6))) is True

    def test_without_a_governor_it_reports_false(self) -> None:
        """One static semaphore, no per-pool occupancy to read — the waiter
        count stays the only signal, exactly as before the governor."""
        assert index_gates_saturated(_make_host(governor=None)) is False


class TestAdmissionRelease:
    @pytest.mark.asyncio
    async def test_release_is_idempotent(self) -> None:
        """The handler drops this permit on INDEXING_COMPLETE, well before the
        wrapper's ``finally`` runs. A second release would hand back a permit
        the gate never issued, letting one more record in than the limit."""
        released = []
        admission = Admission(
            tier=ParseTier.HEAVY, _release=lambda: released.append(1)
        )

        assert release_admission(admission) is True
        assert release_admission(admission) is False
        assert released == [1]

    def test_releasing_nothing_is_a_no_op(self) -> None:
        assert release_admission(None) is False

    @pytest.mark.asyncio
    async def test_legacy_semaphore_path_releases_what_it_took(self) -> None:
        semaphore = asyncio.Semaphore(1)
        host = _make_host(governor=None)
        host.indexing_semaphore = semaphore

        admission = await acquire_index_slot(host, ParseTier.LIGHT)
        assert semaphore.locked()
        assert release_admission(admission) is True
        assert not semaphore.locked()


class TestGateWaiterToken:
    def test_init_increments(self):
        host = _make_host(gate_waiters=0)
        GateWaiterToken(host)
        assert host._gate_waiters == 1

    def test_admit_decrements(self):
        host = _make_host(gate_waiters=0)
        token = GateWaiterToken(host)
        assert host._gate_waiters == 1
        token.admit()
        assert host._gate_waiters == 0

    def test_admit_idempotent(self):
        host = _make_host(gate_waiters=0)
        token = GateWaiterToken(host)
        token.admit()
        token.admit()
        assert host._gate_waiters == 0

    def test_release_without_admit(self):
        host = _make_host(gate_waiters=0)
        token = GateWaiterToken(host)
        assert host._gate_waiters == 1
        token.release()
        assert host._gate_waiters == 0

    def test_release_after_admit_noop(self):
        host = _make_host(gate_waiters=0)
        token = GateWaiterToken(host)
        token.admit()
        assert host._gate_waiters == 0
        token.release()
        assert host._gate_waiters == 0

    def test_release_idempotent(self):
        host = _make_host(gate_waiters=0)
        token = GateWaiterToken(host)
        token.release()
        token.release()
        assert host._gate_waiters == 0

    def test_get_gate_waiter_count(self):
        host = _make_host(gate_waiters=5)
        assert get_gate_waiter_count(host) == 5


# ---------------------------------------------------------------------------
# pending_task_ceiling
# ---------------------------------------------------------------------------


class TestPendingTaskCeiling:
    def test_explicit_env_var(self):
        host = _make_host()
        with patch.dict("os.environ", {"MAX_PENDING_INDEXING_TASKS": "50"}):
            with patch("app.services.messaging.consumer_concurrency.messaging_env") as env:
                env.max_pending_indexing_tasks = 50
                assert pending_task_ceiling(host) == 50

    def test_governor_derived(self):
        gov = SimpleNamespace(ceilings=SimpleNamespace(index=5, index_heavy=2, index_light=3, heavy=3))
        host = _make_host(governor=gov)
        with patch.dict("os.environ", {}, clear=True):
            result = pending_task_ceiling(host)
        # index total 5 * 2 read-ahead = 10, raised to the 64 floor.
        assert result == 64

    def test_default_from_env(self):
        host = _make_host(governor=None)
        with patch.dict("os.environ", {}, clear=True):
            with patch("app.services.messaging.consumer_concurrency.messaging_env") as env:
                env.max_pending_indexing_tasks = 32
                assert pending_task_ceiling(host) == 32


# ---------------------------------------------------------------------------
# Admission / release_admission
# ---------------------------------------------------------------------------


class TestParsingAdmissionCost:
    def test_release_calls_callback(self):
        callback = MagicMock()
        admission = Admission(cost=3, _release=callback)
        release_admission(admission)
        callback.assert_called_once()

    def test_release_none_noop(self):
        release_admission(None)


# ---------------------------------------------------------------------------
# report_memory_incident_if_applicable
# ---------------------------------------------------------------------------


class TestReportMemoryIncident:
    def test_no_governor_noop(self):
        host = _make_host(governor=None)
        report_memory_incident_if_applicable(host, "msg-1", MemoryError())

    def test_non_memory_error_noop(self):
        gov = MagicMock()
        host = _make_host(governor=gov)
        report_memory_incident_if_applicable(host, "msg-1", RuntimeError("boom"))
        gov.report_memory_incident.assert_not_called()

    def test_memory_error_reported(self):
        gov = MagicMock()
        host = _make_host(governor=gov)
        report_memory_incident_if_applicable(host, "msg-1", MemoryError())
        gov.report_memory_incident.assert_called_once()
        assert "msg-1" in gov.report_memory_incident.call_args[0][0]
