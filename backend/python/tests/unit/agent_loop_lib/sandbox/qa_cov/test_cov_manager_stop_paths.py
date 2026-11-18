"""Stop and cancel paths of the sandbox manager that the main suites do not
reach: a cancel while the slot-wait note is taken down, a failing note, a
stopped call that ends with an error, and the query service's shutdown when
the live limit or the load ledger fails to stop."""

from __future__ import annotations

import asyncio
import logging
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.agent_loop_lib.core.context import CancellationToken
from app.agent_loop_lib.sandbox import manager as manager_mod
from app.agent_loop_lib.sandbox.manager import SandboxCallStopped, SandboxManager


class _Governor:
    """Calls `on_wait` (the slot was not free at once), then grants."""

    def __init__(self) -> None:
        self.lease = SimpleNamespace(release=AsyncMock())

    async def acquire(self, *, org_id: str | None, wait_s: float, on_wait) -> object:
        await on_wait()
        return self.lease


class TestSlotWaitNote:
    async def test_a_cancel_while_clearing_the_note_gives_the_granted_slot_back(self) -> None:
        notes: list[bool] = []

        async def notify(tool: str, *, waiting: bool) -> None:
            notes.append(waiting)
            if not waiting:
                raise asyncio.CancelledError

        gov = _Governor()
        mgr = SandboxManager(governor=gov, on_slot_wait=notify)  # type: ignore[arg-type]
        with pytest.raises(asyncio.CancelledError):
            await mgr._acquire_slot("org", "run_code")
        assert notes == [True, False]
        gov.lease.release.assert_awaited_once()

    async def test_a_failing_note_is_logged_and_the_slot_kept(self, caplog: pytest.LogCaptureFixture) -> None:
        async def notify(tool: str, *, waiting: bool) -> None:
            if not waiting:
                raise RuntimeError("stream closed")

        gov = _Governor()
        mgr = SandboxManager(governor=gov, on_slot_wait=notify)  # type: ignore[arg-type]
        with caplog.at_level(logging.WARNING, logger=manager_mod.logger.name):
            lease = await mgr._acquire_slot("org", "run_code")
        assert lease is gov.lease
        gov.lease.release.assert_not_awaited()
        assert "clearing the slot-wait note failed" in caplog.text

    async def test_no_note_without_a_listener(self) -> None:
        gov = _Governor()
        mgr = SandboxManager(governor=gov)  # type: ignore[arg-type]
        assert await mgr._acquire_slot(None, "run_code") is gov.lease

    async def test_a_failing_release_is_only_logged(self, caplog: pytest.LogCaptureFixture) -> None:
        lease = SimpleNamespace(release=AsyncMock(side_effect=RuntimeError("lock gone")))
        with caplog.at_level(logging.WARNING, logger=manager_mod.logger.name):
            await SandboxManager()._release_lease(lease)
            await SandboxManager()._release_lease(None)
        assert "governor lease release failed" in caplog.text


class TestStoppedCallErrors:
    async def test_cleanup_that_raises_still_ends_as_stopped(self, caplog: pytest.LogCaptureFixture) -> None:
        token = CancellationToken()
        started = asyncio.Event()

        async def fn() -> str:
            started.set()
            try:
                await asyncio.sleep(60)
            except asyncio.CancelledError:
                raise RuntimeError("remove failed") from None
            return "ran"

        async def stop() -> None:
            await started.wait()
            token.cancel()

        stopper = asyncio.ensure_future(stop())
        with caplog.at_level(logging.DEBUG, logger=manager_mod.logger.name), \
             pytest.raises(SandboxCallStopped):
            await SandboxManager(stop_token=token).unless_stopped(fn, tool="run_code")
        await stopper
        assert "stopped sandbox call ended with an error" in caplog.text

    async def test_a_call_that_outlives_the_drain_is_left_to_finish(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture,
    ) -> None:
        monkeypatch.setattr(manager_mod, "_STOP_DRAIN_S", 0.05)
        token = CancellationToken()
        started = asyncio.Event()
        release = asyncio.Event()

        async def fn() -> str:
            started.set()
            try:
                await asyncio.sleep(60)
            except asyncio.CancelledError:
                await release.wait()
                raise RuntimeError("late error") from None
            return "ran"

        async def stop() -> None:
            await started.wait()
            token.cancel()

        stopper = asyncio.ensure_future(stop())
        with caplog.at_level(logging.WARNING, logger=manager_mod.logger.name), \
             pytest.raises(SandboxCallStopped):
            await SandboxManager(stop_token=token).unless_stopped(fn, tool="run_code")
        await stopper
        assert "did not finish within" in caplog.text
        release.set()
        # The late error is retrieved by the done callback, not left unlogged.
        for _ in range(5):
            await asyncio.sleep(0)


# --- query service shutdown -----------------------------------------------------


async def test_query_shutdown_survives_a_failing_live_limit_and_ledger() -> None:
    from app.query_main import lifespan

    from ....test_query_main import _make_container, _make_graph_provider

    mock_container = _make_container()
    mock_container._graph_provider = _make_graph_provider(orgs=[])
    mock_container.retrieval_service = AsyncMock(return_value=AsyncMock())
    mock_registry = MagicMock()
    mock_registry.list_toolsets.return_value = []
    mock_config_service = MagicMock()
    mock_config_service.close = AsyncMock()
    mock_container.config_service.return_value = mock_config_service
    live_limit = SimpleNamespace(start=MagicMock(), stop=AsyncMock(side_effect=RuntimeError("stop failed")))
    ledger = SimpleNamespace(close=MagicMock(side_effect=OSError("disk gone")))
    stop_consumers = AsyncMock()

    async def _no_alerts(*_a: object) -> None:
        return None

    app = MagicMock()
    app.state = SimpleNamespace()
    with (
        patch("app.query_main.get_initialized_container", new_callable=AsyncMock, return_value=mock_container),
        patch("app.query_main.start_kafka_consumers", new_callable=AsyncMock, return_value=[]),
        patch("app.query_main.stop_kafka_consumers", stop_consumers),
        patch("app.query_main.container", mock_container),
        patch("app.services.sandbox_alerts.loop.run_sandbox_alert_loop", _no_alerts),
        patch("app.agent_loop_lib.sandbox.live_limit.get_live_limit", return_value=live_limit),
        patch("app.agent_loop_lib.sandbox.load_ledger.peek_default_load_ledger", return_value=ledger),
        patch("app.agents.registry.toolset_registry.get_toolset_registry", return_value=mock_registry),
    ):
        async with lifespan(app):
            pass

    live_limit.stop.assert_awaited_once()
    ledger.close.assert_called_once()
    # The steps after them still ran.
    stop_consumers.assert_awaited_once()
    errors = [str(c.args[0]) for c in mock_container.logger.return_value.error.call_args_list]
    assert any("Error stopping the sandbox live limit: stop failed" in e for e in errors), errors
    assert any("Error flushing the sandbox load ledger: disk gone" in e for e in errors), errors
