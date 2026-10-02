"""Unit tests for the shared connector sync run wrapper.

Covers the lifecycle contract both entry points (EventService and
ConnectorFactory startup resume) rely on: ContextVar scoping, heartbeat task
management, close-on-success, mark-failed-on-error, superseded-run protection
and idle-status reset.
"""

import asyncio
import logging
from unittest.mock import AsyncMock

import pytest

from app.connectors.services import sync_lifecycle
from app.connectors.services.sync_lifecycle import run_sync_with_lifecycle
from app.connectors.services.sync_run_context import get_sync_run_id

ORG = "org1"
CONN = "conn1"
RUN = "run-1"

logger = logging.getLogger("test")


class FakeStore:
    def __init__(self, *, current_run: bool = True) -> None:
        self._current_run = current_run
        self.close_discovery = AsyncMock()
        self.mark_failed = AsyncMock()
        self.touch_heartbeat = AsyncMock()

    async def is_current_run(self, org_id, connector_id, run_id) -> bool:
        return self._current_run


class FakeConnector:
    def __init__(self, error: Exception | None = None) -> None:
        self.error = error
        self.seen_run_id: str | None = "unset"

    async def run_sync(self) -> None:
        self.seen_run_id = get_sync_run_id()
        if self.error:
            raise self.error


def make_args(store, connector, *, set_idle=None) -> dict:
    async def get_store() -> FakeStore:
        return store

    return {
        "connector": connector,
        "connector_id": CONN,
        "org_id": ORG,
        "run_id": RUN,
        "logger": logger,
        "get_store": get_store,
        "set_idle_status": set_idle or AsyncMock(),
    }


class TestRunSyncWithLifecycle:
    async def test_success_closes_discovery_and_sets_idle(self) -> None:
        store = FakeStore()
        connector = FakeConnector()
        set_idle = AsyncMock()
        await run_sync_with_lifecycle(**make_args(store, connector, set_idle=set_idle))

        assert connector.seen_run_id == RUN
        store.close_discovery.assert_awaited_once_with(ORG, CONN, expected_run_id=RUN)
        store.mark_failed.assert_not_awaited()
        set_idle.assert_awaited_once()
        # The ContextVar must not leak past the run.
        assert get_sync_run_id() is None

    async def test_failure_marks_run_failed_and_reraises(self) -> None:
        store = FakeStore()
        connector = FakeConnector(error=RuntimeError("boom"))
        set_idle = AsyncMock()
        with pytest.raises(RuntimeError):
            await run_sync_with_lifecycle(**make_args(store, connector, set_idle=set_idle))

        store.mark_failed.assert_awaited_once_with(
            ORG,
            CONN,
            run_id=RUN,
            failure_code="UNKNOWN",
            failure_reason="boom",
        )
        store.close_discovery.assert_not_awaited()
        # Status is still reset so the connector isn't stuck in SYNCING.
        set_idle.assert_awaited_once()

    async def test_superseded_run_leaves_state_alone(self) -> None:
        store = FakeStore(current_run=False)
        connector = FakeConnector()
        set_idle = AsyncMock()
        await run_sync_with_lifecycle(**make_args(store, connector, set_idle=set_idle))

        store.close_discovery.assert_not_awaited()
        store.mark_failed.assert_not_awaited()
        set_idle.assert_not_awaited()

    async def test_superseded_failed_run_does_not_mark_failed(self) -> None:
        store = FakeStore(current_run=False)
        connector = FakeConnector(error=RuntimeError("boom"))
        with pytest.raises(RuntimeError):
            await run_sync_with_lifecycle(**make_args(store, connector))

        store.mark_failed.assert_not_awaited()

    async def test_idle_status_failure_does_not_raise(self) -> None:
        store = FakeStore()
        set_idle = AsyncMock(side_effect=RuntimeError("db down"))
        await run_sync_with_lifecycle(**make_args(store, FakeConnector(), set_idle=set_idle))
        store.close_discovery.assert_awaited_once()

    async def test_heartbeat_touches_run_while_sync_is_slow(self, monkeypatch) -> None:
        monkeypatch.setattr(sync_lifecycle, "HEARTBEAT_INTERVAL_SECONDS", 0.01)
        store = FakeStore()

        class SlowConnector:
            async def run_sync(self) -> None:
                await asyncio.sleep(0.05)

        await run_sync_with_lifecycle(**make_args(store, SlowConnector()))
        store.touch_heartbeat.assert_awaited()
        args, kwargs = store.touch_heartbeat.await_args
        assert args == (ORG, CONN)
        assert kwargs == {"run_id": RUN}

    async def test_no_heartbeat_task_without_run_id(self, monkeypatch) -> None:
        monkeypatch.setattr(sync_lifecycle, "HEARTBEAT_INTERVAL_SECONDS", 0.01)
        store = FakeStore()

        class SlowConnector:
            async def run_sync(self) -> None:
                await asyncio.sleep(0.03)

        args = make_args(store, SlowConnector())
        args["run_id"] = None
        await run_sync_with_lifecycle(**args)
        store.touch_heartbeat.assert_not_awaited()

    async def test_cancellation_propagates_and_marks_failed(self) -> None:
        store = FakeStore()

        class HangingConnector:
            async def run_sync(self) -> None:
                await asyncio.sleep(30)

        task = asyncio.create_task(
            run_sync_with_lifecycle(**make_args(store, HangingConnector()))
        )
        await asyncio.sleep(0.01)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        store.mark_failed.assert_awaited_once()
