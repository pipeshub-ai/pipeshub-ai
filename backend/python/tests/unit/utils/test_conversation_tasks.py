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
