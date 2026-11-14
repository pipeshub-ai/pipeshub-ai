"""The Drive connector's list of limited folders still to check, on a real graph store's sync points.

When a shared drive folder turns limited, the folder is noted on the connector's checkpoint at once, and the run then
checks its descendants a bounded number at a time, keeping a cursor per folder. The list is a list of objects, which a
Neo4j property cannot hold (``SET sp += $data`` refuses a list of maps), so it is stored as JSON text. These tests
write and read it through the real ``SyncPoint`` on each store: folders queued one after another all stay, a folder is
queued once, and a cursor written back is read back.

Runs on Neo4j and ArangoDB (backend-matrix). Environment: NEO4J_IT_URI, NEO4J_IT_PASSWORD, ARANGO_IT_URL,
ARANGO_IT_PASSWORD.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import uuid
from typing import TYPE_CHECKING

import pytest

from app.connectors.core.base.data_store.graph_data_store import GraphDataStore
from app.connectors.core.base.sync_point.sync_point import SyncDataPointType, SyncPoint
from app.connectors.sources.google.drive.team.connector import GoogleDriveTeamConnector
from tests.integration.real_graph import (
    backend_unavailable,
    connect_arango,
    connect_neo4j,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

pytestmark = [pytest.mark.integration, pytest.mark.timeout(300)]

ARANGO_DB = "drive_limited_pending_it"
logger = logging.getLogger("drive-limited-pending-it")


@pytest.fixture(params=["neo4j", "arango"])
async def connector(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[GoogleDriveTeamConnector]:
    async with contextlib.AsyncExitStack() as cleanup:
        try:
            graph = await (
                connect_neo4j(logger, monkeypatch) if request.param == "neo4j" else connect_arango(logger, ARANGO_DB)
            )
        except Exception as exc:
            backend_unavailable(request.param, exc)
        disconnect = getattr(graph, "disconnect", None)
        if disconnect is not None:
            cleanup.push_async_callback(disconnect)
        monkeypatch.setenv("SECRET_KEY", "limited-pending-it-secret")
        run = uuid.uuid4().hex[:10]
        # Only what the queue touches: its sync point and its lock.
        drive = GoogleDriveTeamConnector.__new__(GoogleDriveTeamConnector)
        drive.drive_delta_sync_point = SyncPoint(
            f"drive-pending-{run}", f"org-pending-{run}", SyncDataPointType.RECORDS, GraphDataStore(logger, graph)
        )
        drive._limited_pending_lock = asyncio.Lock()
        yield drive


async def test_folders_queued_one_after_another_all_stay(connector: GoogleDriveTeamConnector) -> None:
    await connector._queue_limited_subtree("sd-1", "folder-a")
    await connector._queue_limited_subtree("sd-1", "folder-b")
    await connector._queue_limited_subtree("sd-1", "folder-a")  # queued once

    assert await connector._load_limited_pending() == {("sd-1", "folder-a"): None, ("sd-1", "folder-b"): None}


async def test_a_cursor_written_back_is_read_back(connector: GoogleDriveTeamConnector) -> None:
    await connector._save_limited_pending([
        {"drive": "sd-1", "folder": "folder-a", "after": "record-0042"},
        {"drive": "sd-2", "folder": "folder-c", "after": None},
    ])

    assert await connector._load_limited_pending() == {
        ("sd-1", "folder-a"): "record-0042", ("sd-2", "folder-c"): None,
    }

    await connector._save_limited_pending([])
    assert await connector._load_limited_pending() == {}
