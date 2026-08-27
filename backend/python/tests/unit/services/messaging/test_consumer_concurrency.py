"""Unit tests for consumer_concurrency — bridging, distributed leases,
gate-waiter tokens, and ceiling helpers."""

from __future__ import annotations

import asyncio
import threading
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.messaging.consumer_concurrency import (
    GateWaiterToken,
    ParsingAdmission,
    _normalize_operation,
    acquire_distributed_slot,
    bridge_to_main_loop,
    clear_retry_tracking,
    get_gate_waiter_count,
    get_retry_count,
    increment_retry_and_check,
    index_ceiling,
    log_distributed_error,
    parse_ceiling,
    parse_lease_pool,
    pending_task_ceiling,
    release_distributed_slot,
    release_parsing_slot,
    report_memory_incident_if_applicable,
    schedule_on_main_loop,
)
from app.services.resource_governor.models import ParseTier


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
        with patch("app.services.messaging.consumer_concurrency.messaging_env") as env:
            env.concurrency_lease_seconds = 30
            env.concurrency_acquire_poll_seconds = 0.01
            result = await acquire_distributed_slot(host, "pool", "owner", 5, deadline_seconds=0.05)
        assert result is False


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
    def test_index_ceiling_with_governor(self):
        gov = SimpleNamespace(ceilings=SimpleNamespace(index=10))
        host = _make_host(governor=gov)
        assert index_ceiling(host) == 10

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


# ---------------------------------------------------------------------------
# GateWaiterToken
# ---------------------------------------------------------------------------


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
        gov = SimpleNamespace(ceilings=SimpleNamespace(index=5, heavy=3))
        host = _make_host(governor=gov)
        with patch.dict("os.environ", {}, clear=True):
            result = pending_task_ceiling(host)
        assert result == 20

    def test_default_from_env(self):
        host = _make_host(governor=None)
        with patch.dict("os.environ", {}, clear=True):
            with patch("app.services.messaging.consumer_concurrency.messaging_env") as env:
                env.max_pending_indexing_tasks = 32
                assert pending_task_ceiling(host) == 32


# ---------------------------------------------------------------------------
# ParsingAdmission / release_parsing_slot
# ---------------------------------------------------------------------------


class TestParsingAdmission:
    def test_release_calls_callback(self):
        callback = MagicMock()
        admission = ParsingAdmission(cost=3, _release=callback)
        release_parsing_slot(admission)
        callback.assert_called_once()

    def test_release_none_noop(self):
        release_parsing_slot(None)


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
