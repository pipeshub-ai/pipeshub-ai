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


class TestModuleSingleton:
    def test_singleton_exists(self):
        from app.connectors.core.sync.task_manager import sync_task_manager

        assert isinstance(sync_task_manager, SyncTaskManager)
