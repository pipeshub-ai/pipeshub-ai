"""A sync run records when it started, when it ended and how (scenario BU-06).

"Last synced" in the UI used to read the app doc's updatedAtTimestamp, which any write moves
(starting a run, saving settings), so a run that hung for days still looked recent. The run's
start write now stamps lastSyncStartedAtTimestamp, and its IDLE write stamps
lastSyncFinishedAtTimestamp with lastSyncOutcome in the same update.
"""

import asyncio
import contextlib
import logging
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.config.constants.arangodb import AppStatus
from app.connectors.core.base.connector.connector_service import (
    ConnectorSyncSkippedError,
)
from app.connectors.core.constants import ConnectorStateKeys as Keys
from app.connectors.core.sync.sync_coordinator import SyncLease
from app.connectors.core.sync.sync_runner import run_sync_task

_LOG = logging.getLogger("test")


def _graph_provider() -> AsyncMock:
    gp = AsyncMock()
    gp.get_document = AsyncMock(return_value={"id": "c1", "status": "SYNCING"})
    return gp


def _connector(run_sync) -> MagicMock:
    connector = MagicMock()
    connector.run_sync = run_sync
    return connector


def _start_write(gp: AsyncMock) -> dict:
    (payload,) = gp.batch_upsert_nodes.await_args_list[0].args[0]
    return payload


def _idle_write(gp: AsyncMock) -> dict:
    (idle,) = [c.args[2] for c in gp.update_node.await_args_list if c.args[2].get("status") == AppStatus.IDLE.value]
    return idle


async def _ok() -> None:
    return None


async def _boom() -> None:
    raise RuntimeError("site down")


async def _skip() -> None:
    raise ConnectorSyncSkippedError("DESKTOP_OFFLINE")


@pytest.mark.parametrize(
    "run_sync, outcome",
    [(_ok, "succeeded"), (_boom, "failed"), (_skip, "skipped")],
)
async def test_the_run_records_how_it_ended(run_sync, outcome: str) -> None:
    gp = _graph_provider()

    with contextlib.suppress(RuntimeError):
        await run_sync_task(_connector(run_sync), "c1", gp, _LOG)

    start = _start_write(gp)
    idle = _idle_write(gp)
    assert start[Keys.LAST_SYNC_STARTED_AT] == start["updatedAtTimestamp"]
    assert idle[Keys.LAST_SYNC_OUTCOME] == outcome
    assert idle[Keys.LAST_SYNC_FINISHED_AT] == idle["updatedAtTimestamp"]
    assert idle[Keys.LAST_SYNC_FINISHED_AT] >= start[Keys.LAST_SYNC_STARTED_AT]


async def test_a_stopped_run_is_recorded_as_stopped() -> None:
    gp = _graph_provider()
    started = asyncio.Event()

    async def forever() -> None:
        started.set()
        await asyncio.sleep(3600)

    task = asyncio.create_task(run_sync_task(_connector(forever), "c1", gp, _LOG))
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert _idle_write(gp)[Keys.LAST_SYNC_OUTCOME] == "stopped"


async def test_a_run_whose_lease_was_lost_writes_no_outcome() -> None:
    """Someone else owns the connector now: its status and outcome are theirs to write."""
    gp = _graph_provider()
    lease = SyncLease("c1", "tok", 1)
    started = asyncio.Event()

    async def until_lease_lost() -> None:
        started.set()
        await asyncio.sleep(3600)

    task = asyncio.create_task(
        run_sync_task(_connector(until_lease_lost), "c1", gp, _LOG, lease=lease, coordinator=AsyncMock())
    )
    await started.wait()
    lease.lost.set()
    await asyncio.wait_for(task, 5)

    assert not [c for c in gp.update_node.await_args_list if Keys.LAST_SYNC_OUTCOME in c.args[2]]


async def test_the_connectors_summary_of_the_run_is_stored_with_its_outcome() -> None:
    gp = _graph_provider()

    async def summarised() -> None:
        connector.last_sync_summary = {"pagesSaved": 12, "stopReason": "max_pages"}

    connector = _connector(summarised)

    await run_sync_task(connector, "c1", gp, _LOG)

    import json

    assert json.loads(_idle_write(gp)[Keys.LAST_SYNC_SUMMARY]) == {"pagesSaved": 12, "stopReason": "max_pages"}


async def test_a_connector_without_a_summary_clears_the_last_one() -> None:
    gp = _graph_provider()
    connector = _connector(_ok)
    connector.last_sync_summary = None

    await run_sync_task(connector, "c1", gp, _LOG)

    assert _idle_write(gp)[Keys.LAST_SYNC_SUMMARY] is None


async def test_a_run_stopped_before_it_starts_stores_no_summary_of_the_last_run() -> None:
    gp = _graph_provider()
    connector = _connector(_ok)
    connector.last_sync_summary = {"pagesSaved": 12}  # a cached connector's last run
    writing = asyncio.Event()

    async def slow_start_write(*_: object, **__: object) -> None:
        writing.set()
        await asyncio.sleep(3600)

    gp.batch_upsert_nodes = AsyncMock(side_effect=slow_start_write)
    task = asyncio.create_task(run_sync_task(connector, "c1", gp, _LOG))
    await writing.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert _idle_write(gp)[Keys.LAST_SYNC_SUMMARY] is None
