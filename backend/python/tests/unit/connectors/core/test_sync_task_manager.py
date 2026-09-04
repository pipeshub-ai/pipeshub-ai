"""Tests for app.connectors.core.sync.task_manager — SyncTaskManager."""

import asyncio

import pytest

from app.connectors.core.sync.task_manager import SyncTaskManager


class TestSyncTaskManagerInit:
    def test_initial_state(self):
        mgr = SyncTaskManager()
        assert mgr._tasks == {}
        assert mgr.is_running("any") is False


class TestStartSync:
    @pytest.mark.asyncio
    async def test_start_creates_task(self):
        mgr = SyncTaskManager()

        async def coro():
            await asyncio.sleep(10)

        task = await mgr.start_sync("c1", coro())
        assert isinstance(task, asyncio.Task)
        assert mgr.is_running("c1") is True
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    @pytest.mark.asyncio
    async def test_start_replaces_existing(self):
        mgr = SyncTaskManager()

        async def slow_coro():
            await asyncio.sleep(10)

        t1 = await mgr.start_sync("c1", slow_coro())
        t2 = await mgr.start_sync("c1", slow_coro())
        assert t1.done() or t1.cancelled()
        assert mgr.is_running("c1") is True
        t2.cancel()
        try:
            await t2
        except asyncio.CancelledError:
            pass

    @pytest.mark.asyncio
    async def test_start_multiple_connectors(self):
        mgr = SyncTaskManager()

        async def coro():
            await asyncio.sleep(10)

        t1 = await mgr.start_sync("c1", coro())
        t2 = await mgr.start_sync("c2", coro())
        assert mgr.is_running("c1") is True
        assert mgr.is_running("c2") is True
        t1.cancel()
        t2.cancel()
        try:
            await t1
        except asyncio.CancelledError:
            pass
        try:
            await t2
        except asyncio.CancelledError:
            pass


class TestCancelSync:
    @pytest.mark.asyncio
    async def test_cancel_running(self):
        mgr = SyncTaskManager()

        async def coro():
            await asyncio.sleep(10)

        await mgr.start_sync("c1", coro())
        assert mgr.is_running("c1") is True
        await mgr.cancel_sync("c1")
        assert mgr.is_running("c1") is False

    @pytest.mark.asyncio
    async def test_cancel_nonexistent(self):
        mgr = SyncTaskManager()
        # Should not raise
        await mgr.cancel_sync("nonexistent")

    @pytest.mark.asyncio
    async def test_cancel_already_done(self):
        mgr = SyncTaskManager()

        async def instant():
            return "done"

        task = await mgr.start_sync("c1", instant())
        await asyncio.sleep(0.05)  # let task complete
        await mgr.cancel_sync("c1")  # should be no-op


class TestCancelAll:
    @pytest.mark.asyncio
    async def test_cancel_all_tasks(self):
        mgr = SyncTaskManager()

        async def coro():
            await asyncio.sleep(10)

        await mgr.start_sync("c1", coro())
        await mgr.start_sync("c2", coro())
        await mgr.start_sync("c3", coro())
        await mgr.cancel_all()
        assert mgr.is_running("c1") is False
        assert mgr.is_running("c2") is False
        assert mgr.is_running("c3") is False

    @pytest.mark.asyncio
    async def test_cancel_all_empty(self):
        mgr = SyncTaskManager()
        await mgr.cancel_all()  # Should not raise


class TestIsRunning:
    @pytest.mark.asyncio
    async def test_running_task(self):
        mgr = SyncTaskManager()

        async def coro():
            await asyncio.sleep(10)

        task = await mgr.start_sync("c1", coro())
        assert mgr.is_running("c1") is True
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    @pytest.mark.asyncio
    async def test_completed_task(self):
        mgr = SyncTaskManager()

        async def instant():
            return "done"

        await mgr.start_sync("c1", instant())
        await asyncio.sleep(0.05)
        assert mgr.is_running("c1") is False

    def test_nonexistent_connector(self):
        mgr = SyncTaskManager()
        assert mgr.is_running("nonexistent") is False


class TestOnTaskDone:
    @pytest.mark.asyncio
    async def test_successful_completion_removes_from_registry(self):
        mgr = SyncTaskManager()

        async def instant():
            return "done"

        await mgr.start_sync("c1", instant())
        await asyncio.sleep(0.05)
        assert "c1" not in mgr._tasks

    @pytest.mark.asyncio
    async def test_failed_task_removes_from_registry(self):
        mgr = SyncTaskManager()

        async def failing():
            raise ValueError("sync error")

        await mgr.start_sync("c1", failing())
        await asyncio.sleep(0.05)
        assert "c1" not in mgr._tasks

    @pytest.mark.asyncio
    async def test_cancelled_task_removes_from_registry(self):
        mgr = SyncTaskManager()

        async def slow():
            await asyncio.sleep(10)

        await mgr.start_sync("c1", slow())
        await mgr.cancel_sync("c1")
        await asyncio.sleep(0.05)
        assert "c1" not in mgr._tasks

    @pytest.mark.asyncio
    async def test_replaced_task_does_not_remove_newer(self):
        mgr = SyncTaskManager()

        async def slow():
            await asyncio.sleep(10)

        t1 = await mgr.start_sync("c1", slow())
        t2 = await mgr.start_sync("c1", slow())
        # t1 done callback should NOT remove t2 from registry
        await asyncio.sleep(0.05)
        assert mgr._tasks.get("c1") is t2
        t2.cancel()
        try:
            await t2
        except asyncio.CancelledError:
            pass


class TestWhoOwnsARegistry:
    """Sync tasks moved onto the coordinator; reindex kept its own.

    The split is the point. Reindex takes no lease and its keys are per-request
    composites rather than connector ids, so sharing a registry with syncs would
    let a connector-wide cancel take out an unrelated reindex — and, worse, let
    reindex state answer a question about whether a *sync* is running.
    """

    def test_reindex_still_has_a_module_singleton(self):
        from app.connectors.core.sync.task_manager import reindex_task_manager

        assert isinstance(reindex_task_manager, SyncTaskManager)

    def test_there_is_no_sync_singleton_any_more(self):
        import app.connectors.core.sync.task_manager as tm

        assert not hasattr(tm, "sync_task_manager")

    def test_the_coordinator_owns_the_sync_registry(self):
        import logging

        from app.connectors.core.sync.sync_coordinator import LocalSyncCoordinator

        assert isinstance(
            LocalSyncCoordinator(logging.getLogger("t"))._tasks, SyncTaskManager
        )


class TestStartIfIdle:
    """start_if_idle is the safe counterpart to start_sync: it declines rather
    than cancelling, so an in-flight sync keeps its progress and its status."""

    @pytest.mark.asyncio
    async def test_starts_when_idle(self):
        mgr = SyncTaskManager()

        async def coro():
            return "done"

        task = await mgr.start_if_idle("c1", coro())
        assert task is not None
        await task
        assert mgr.is_running("c1") is False

    @pytest.mark.asyncio
    async def test_declines_and_closes_coro_when_busy(self):
        mgr = SyncTaskManager()

        async def running():
            await asyncio.sleep(10)

        first = await mgr.start_if_idle("c1", running())
        assert first is not None

        declined_ran = False

        async def second():
            nonlocal declined_ran
            declined_ran = True

        # The declined coroutine must be closed, not left dangling — an
        # un-awaited coroutine emits a RuntimeWarning and leaks its frame.
        result = await mgr.start_if_idle("c1", second())
        assert result is None
        assert declined_ran is False
        assert mgr.is_running("c1") is True

        first.cancel()
        try:
            await first
        except asyncio.CancelledError:
            pass

    @pytest.mark.asyncio
    async def test_does_not_cancel_the_running_task(self):
        mgr = SyncTaskManager()
        finished = asyncio.Event()

        async def running():
            try:
                await asyncio.sleep(0.05)
                finished.set()
            except asyncio.CancelledError:
                raise

        task = await mgr.start_if_idle("c1", running())

        async def other():
            pass

        await mgr.start_if_idle("c1", other())
        await task
        assert finished.is_set() is True


class TestRequestStop:
    @pytest.mark.asyncio
    async def test_stops_running_task(self):
        mgr = SyncTaskManager()

        async def coro():
            await asyncio.sleep(10)

        task = await mgr.start_sync("c1", coro())
        assert mgr.request_stop("c1") is True
        try:
            await task
        except asyncio.CancelledError:
            pass
        assert mgr.is_running("c1") is False

    @pytest.mark.asyncio
    async def test_returns_immediately_without_awaiting_unwind(self):
        """request_stop must not block on cleanup: a task wedged in a blocking
        SDK call would otherwise hang the HTTP handler that asked it to stop."""
        mgr = SyncTaskManager()
        started = asyncio.Event()
        cleanup_done = asyncio.Event()

        async def slow_cleanup():
            started.set()
            try:
                await asyncio.sleep(10)
            finally:
                await asyncio.sleep(0.05)
                cleanup_done.set()

        task = await mgr.start_sync("c1", slow_cleanup())
        await started.wait()
        assert mgr.request_stop("c1") is True
        # Still unwinding, so still counted as running — which is what keeps
        # the decline gate closed against an immediate restart.
        assert cleanup_done.is_set() is False
        assert mgr.is_running("c1") is True
        try:
            await task
        except asyncio.CancelledError:
            pass
        assert cleanup_done.is_set() is True

    def test_idle_key_returns_false(self):
        assert SyncTaskManager().request_stop("nonexistent") is False

    @pytest.mark.asyncio
    async def test_completed_task_returns_false(self):
        mgr = SyncTaskManager()

        async def instant():
            return "done"

        await mgr.start_sync("c1", instant())
        await asyncio.sleep(0.05)
        assert mgr.request_stop("c1") is False
