"""run_sync_task's ``on_finished`` hook: told how the run ended, before the run lets go of the connector."""

import asyncio
import contextlib
import logging
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.config.constants.arangodb import AppStatus
from app.connectors.core.base.connector.connector_service import (
    ConnectorSyncSkippedError,
)
from app.connectors.core.sync import sync_runner
from app.connectors.core.sync.sync_coordinator import SyncLease
from app.connectors.core.sync.sync_runner import run_sync_task

_LOG = logging.getLogger("test")


def _graph_provider(events: list[str]) -> AsyncMock:
    gp = AsyncMock()
    gp.get_document = AsyncMock(return_value={"id": "c1", "status": "SYNCING"})

    async def update_node(key: str, collection: str, fields: dict, *a: object, **k: object) -> bool:
        if fields.get("status") == AppStatus.IDLE.value:
            events.append("idle")
        return True

    gp.update_node = AsyncMock(side_effect=update_node)
    return gp


def _connector(run_sync) -> MagicMock:
    connector = MagicMock()
    connector.run_sync = run_sync
    return connector


async def _ok() -> None:
    return None


async def _boom() -> None:
    raise RuntimeError("site down")


async def _skip() -> None:
    raise ConnectorSyncSkippedError("DESKTOP_OFFLINE")


@pytest.mark.parametrize(
    "run_sync, reason",
    [(_ok, None), (_boom, "sync failed"), (_skip, "DESKTOP_OFFLINE")],
)
async def test_the_hook_hears_how_the_run_ended_before_it_is_marked_idle(run_sync, reason: str | None) -> None:
    events: list[str] = []
    heard: list[str | None] = []

    async def on_finished(failure_reason: str | None) -> None:
        heard.append(failure_reason)
        events.append("hook")

    with contextlib.suppress(RuntimeError):
        await run_sync_task(_connector(run_sync), "c1", _graph_provider(events), _LOG, on_finished=on_finished)

    assert heard == [reason]
    assert events == ["hook", "idle"]


async def test_a_failing_hook_does_not_keep_the_connector_from_going_idle() -> None:
    events: list[str] = []

    async def on_finished(failure_reason: str | None) -> None:
        raise RuntimeError("hook down")

    await run_sync_task(_connector(_ok), "c1", _graph_provider(events), _LOG, on_finished=on_finished)

    assert events == ["idle"]


async def test_without_a_hook_the_run_ends_as_before() -> None:
    events: list[str] = []

    await run_sync_task(_connector(_ok), "c1", _graph_provider(events), _LOG)

    assert events == ["idle"]


@pytest.mark.parametrize(("event", "reason"), [("lost", "lease_lost"), ("stop_requested", "requested")])
async def test_a_run_stopped_through_its_lease_is_not_reported_as_finished(event: str, reason: str) -> None:
    """Both end the run without raising, so only the outcome says it was cut short."""
    lease = SyncLease("c1", "tok", 1)
    heard: list[str | None] = []

    async def run_sync() -> None:
        getattr(lease, event).set()
        await asyncio.sleep(10)

    async def on_finished(failure_reason: str | None) -> None:
        heard.append(failure_reason)

    await run_sync_task(
        _connector(run_sync), "c1", _graph_provider([]), _LOG, lease=lease, coordinator=AsyncMock(), on_finished=on_finished,
    )

    assert heard == [reason]


async def test_a_cancelled_hook_still_lets_the_run_finalize() -> None:
    order: list[str] = []

    async def on_finished(failure_reason: str | None) -> None:
        order.append("hook")
        raise asyncio.CancelledError

    async def finalize(*args: object, **kwargs: object) -> None:
        order.append("finalize")

    with patch.object(sync_runner, "_finalize", new=finalize), pytest.raises(asyncio.CancelledError):
        await sync_runner._finish_then_finalize(on_finished, None, AsyncMock(), _LOG, "c1")

    assert order == ["hook", "finalize"]
