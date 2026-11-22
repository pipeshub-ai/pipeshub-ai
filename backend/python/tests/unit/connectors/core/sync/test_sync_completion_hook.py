"""run_sync_task's ``on_finished`` hook: told how the run ended, before the run lets go of the connector."""

import contextlib
import logging
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.config.constants.arangodb import AppStatus
from app.connectors.core.base.connector.connector_service import (
    ConnectorSyncSkippedError,
)
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
