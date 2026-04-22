"""Tests for app.utils.conversation_tasks — task registration and collection."""

import asyncio
from unittest.mock import patch

import pytest

from app.utils.conversation_tasks import (
    _conversation_tasks,
    register_task,
    pop_tasks,
    _rows_to_csv_bytes,
    await_and_collect_results,
)


@pytest.fixture(autouse=True)
def _clear_conversation_tasks():
    """Clear the module-level dict before and after every test."""
    _conversation_tasks.clear()
    yield
    _conversation_tasks.clear()


class TestRegisterTask:
    @pytest.mark.asyncio
    async def test_register_single_task(self):
        mock_task = asyncio.get_event_loop().create_future()
        mock_task.set_result(None)
        register_task("conv1", mock_task)
        assert "conv1" in _conversation_tasks
        assert len(_conversation_tasks["conv1"]) == 1

    @pytest.mark.asyncio
    async def test_register_multiple_tasks(self):
        loop = asyncio.get_event_loop()
        t1 = loop.create_future()
        t1.set_result(None)
        t2 = loop.create_future()
        t2.set_result(None)
        register_task("conv1", t1)
        register_task("conv1", t2)
        assert len(_conversation_tasks["conv1"]) == 2

    @pytest.mark.asyncio
    async def test_register_different_conversations(self):
        loop = asyncio.get_event_loop()
        t1 = loop.create_future()
        t1.set_result(None)
        t2 = loop.create_future()
        t2.set_result(None)
        register_task("conv1", t1)
        register_task("conv2", t2)
        assert "conv1" in _conversation_tasks
        assert "conv2" in _conversation_tasks


class TestPopTasks:
    @pytest.mark.asyncio
    async def test_pop_existing(self):
        t = asyncio.get_event_loop().create_future()
        t.set_result(None)
        _conversation_tasks["conv1"] = [t]
        tasks = pop_tasks("conv1")
        assert len(tasks) == 1
        assert "conv1" not in _conversation_tasks

    def test_pop_nonexistent(self):
        tasks = pop_tasks("nonexistent")
        assert tasks == []


class TestRowsToCsvBytes:
    def test_basic_csv(self):
        result = _rows_to_csv_bytes(["name", "age"], [("Alice", 30), ("Bob", 25)])
        text = result.decode("utf-8")
        assert "name,age" in text
        assert "Alice,30" in text
        assert "Bob,25" in text

    def test_empty_rows(self):
        result = _rows_to_csv_bytes(["col1"], [])
        text = result.decode("utf-8")
        assert "col1" in text

    def test_special_characters(self):
        result = _rows_to_csv_bytes(["data"], [('hello, "world"',)])
        text = result.decode("utf-8")
        assert "hello" in text


class TestAwaitAndCollectResults:
    @pytest.mark.asyncio
    async def test_no_tasks(self):
        results = await await_and_collect_results("conv1")
        assert results == []

    @pytest.mark.asyncio
    async def test_successful_tasks(self):
        async def good_task():
            return {"url": "http://example.com"}

        task = asyncio.create_task(good_task())
        register_task("conv1", task)
        results = await await_and_collect_results("conv1")
        assert len(results) == 1
        assert results[0]["url"] == "http://example.com"

    @pytest.mark.asyncio
    async def test_none_result_excluded(self):
        async def none_task():
            return None

        task = asyncio.create_task(none_task())
        register_task("conv1", task)
        results = await await_and_collect_results("conv1")
        assert results == []

    @pytest.mark.asyncio
    async def test_failed_tasks_are_logged_and_skipped(self):
        async def failing_task():
            raise ValueError("task error")

        task = asyncio.create_task(failing_task())
        register_task("conv1", task)
        results = await await_and_collect_results("conv1")
        assert results == []

    @pytest.mark.asyncio
    async def test_mixed_tasks(self):
        async def good():
            return {"ok": True}

        async def bad():
            raise RuntimeError("fail")

        async def none_result():
            return None

        for coro in [good(), bad(), none_result()]:
            task = asyncio.create_task(coro)
            register_task("conv1", task)

        results = await await_and_collect_results("conv1")
        assert len(results) == 1
        assert results[0]["ok"] is True

    @pytest.mark.asyncio
    async def test_nested_task_registration_is_drained(self):
        """A task that itself registers another task must not be lost.

        This is the C5 scenario: if a tool schedules an upload task, and that
        upload task discovers more work (e.g. per-file sub-tasks it also
        registers), the stream-layer drainer must wait for all of them.
        """
        async def parent():
            async def child():
                return {"type": "artifacts", "artifacts": [{"name": "child"}]}

            child_task = asyncio.create_task(child())
            register_task("conv1", child_task)
            return {"type": "artifacts", "artifacts": [{"name": "parent"}]}

        parent_task = asyncio.create_task(parent())
        register_task("conv1", parent_task)

        results = await await_and_collect_results("conv1")
        names = {a["name"] for r in results for a in r.get("artifacts", [])}
        assert names == {"parent", "child"}

    @pytest.mark.asyncio
    async def test_drain_stops_at_pass_limit(self, monkeypatch):
        """Infinite nesting must be bounded — we refuse to spin forever."""
        import app.utils.conversation_tasks as ct

        monkeypatch.setattr(ct, "_MAX_DRAIN_PASSES", 3)

        counter = {"n": 0}

        async def spawner():
            counter["n"] += 1

            async def noop():
                return None

            register_task("conv_loop", asyncio.create_task(spawner()))
            return None

        register_task("conv_loop", asyncio.create_task(spawner()))
        results = await ct.await_and_collect_results("conv_loop")

        # No results because all tasks returned None.
        assert results == []
        # We cap the number of passes — so `counter` is bounded (NOT infinite).
        # Exact bound depends on how many tasks each pass popped; the only
        # contract is "finite and reasonably small".
        assert counter["n"] < 50

    @pytest.mark.asyncio
    async def test_overall_timeout_cancels_slow_task(self, monkeypatch):
        """A task that never completes must be cancelled once the drain deadline is hit."""
        import app.utils.conversation_tasks as ct

        monkeypatch.setattr(ct, "_DRAIN_OVERALL_TIMEOUT_S", 0.1)

        async def slow():
            await asyncio.sleep(10)
            return {"should_not": "arrive"}

        task = asyncio.create_task(slow())
        register_task("conv_slow", task)
        results = await ct.await_and_collect_results("conv_slow")
        assert results == []
        assert task.cancelled() or task.done()
