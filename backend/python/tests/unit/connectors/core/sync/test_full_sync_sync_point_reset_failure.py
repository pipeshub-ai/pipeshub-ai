"""A full sync whose sync points could not be deleted unlinks nothing and owes one.

The full-sync prep (``EventService._start_sync_with_lease``) deletes the
connector's sync points, and connectors that read "full" from a missing cursor
(Jira, Linear, GitHub, Box, S3...) only then re-read everything. When that
delete fails, the sync still runs as a full one, the connector finds its cursor
and writes only what changed, and the unlink at the end of the run takes the
links of everything it skipped. The real-database side is in
tests/integration/graph_db/test_full_sync_unlink_real_backends.py.
"""

import asyncio
import logging
import uuid
from collections.abc import Awaitable
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.config.constants.arangodb import CollectionNames
from app.connectors.core.base.data_processor.data_source_entities_processor import (
    DataSourceEntitiesProcessor,
)
from app.connectors.core.constants import ConnectorStateKeys
from app.connectors.core.sync.full_sync_coverage import FullSyncCoverage
from app.connectors.core.sync.sync_coordinator import SyncLease
from app.connectors.services.event_service import EventService

_LOG = logging.getLogger("test")
RECORDS = CollectionNames.RECORDS.value
RECORD_GROUPS = CollectionNames.RECORD_GROUPS.value
_OWED = ConnectorStateKeys.PENDING_FULL_SYNC

# What an earlier sync stored, at the version it read (its cursor is 1): r1-r3 in
# g1, r4 and r5 in g2.
_STORED = {
    (RECORD_GROUPS, "g1"): 1, (RECORD_GROUPS, "g2"): 1, **{(RECORDS, f"r{i}"): 1 for i in range(1, 6)},
}
# The source now: r4 changed since the cursor, r5 was deleted.
_SOURCE = {
    (RECORD_GROUPS, "g1"): 1, (RECORD_GROUPS, "g2"): 1,
    (RECORDS, "r1"): 1, (RECORDS, "r2"): 1, (RECORDS, "r3"): 1, (RECORDS, "r4"): 2,
}
_GROUP_OF = {"r1": "g1", "r2": "g1", "r3": "g1", "r4": "g2"}
_GONE = set(_STORED) - set(_SOURCE)


class _InlineCoordinator:
    shutting_down = False

    def __init__(self) -> None:
        self.task: asyncio.Task | None = None

    async def spawn(self, lease: SyncLease, coro: Awaitable[None]) -> asyncio.Task:
        self.task = asyncio.get_running_loop().create_task(coro)
        lease.task = self.task
        return self.task

    async def end(self, lease: SyncLease) -> bool:
        return True


class _Graph:
    """The app doc, the connector's cursor and which stored nodes still have their links."""

    def __init__(self, connector_id: str, app: dict) -> None:
        self.connector_id = connector_id
        self.app = {"id": connector_id, "isActive": True, **app}
        self.cursor: int | None = 1
        self.linked = set(_STORED)
        self.delete_sync_points: object = (1, True)
        self.provider = AsyncMock()
        self.provider.get_document = AsyncMock(side_effect=self._get_document)
        self.provider.update_node = AsyncMock(side_effect=self._update)
        self.provider.batch_upsert_nodes = AsyncMock(side_effect=self._upsert)
        self.provider.delete_sync_points_by_connector_id = AsyncMock(side_effect=self._delete_sync_points)
        self.provider.unlink_nodes_missed_by_full_sync = AsyncMock(side_effect=self._unlink)
        self.provider.kh_scope_mark_stale = AsyncMock(return_value=1)
        self.provider.kh_scope_enabled = AsyncMock(return_value=False)
        self.provider.is_write_conflict = MagicMock(return_value=False)

    async def _get_document(self, document_key: str, collection: str, **_: object) -> dict | None:
        return dict(self.app) if document_key == self.connector_id else None

    async def _update(self, key: str, collection: str, updates: dict, *_: object, **__: object) -> bool:
        if key == self.connector_id:
            self.app.update(updates)
        return True

    async def _upsert(self, nodes: list[dict], collection: str, *_: object, **__: object) -> bool:
        for node in nodes:
            if node.get("id") == self.connector_id:
                self.app.update(node)
        return True

    async def _delete_sync_points(self, connector_id: str, **_: object) -> tuple[int, bool]:
        outcome = self.delete_sync_points
        if isinstance(outcome, Exception):
            raise outcome
        if outcome[1]:
            self.cursor = None
        return outcome

    async def _unlink(self, connector_id: str, seen: dict[str, set[str]], **_: object) -> tuple[int, bool]:
        missed = {node for node in self.linked if node[1] not in seen.get(node[0], set())}
        self.linked -= missed
        return len(missed), True

    def wrongly_unlinked(self) -> int:
        return len(set(_SOURCE) - self.linked)

    def really_gone_unlinked(self) -> int:
        return len(_GONE - self.linked)


class _CursorConnector:
    """Re-reads everything only when it finds no cursor, as Jira, Linear or S3 do."""

    def __init__(self, graph: _Graph) -> None:
        self.graph = graph
        self.data_entities_processor = DataSourceEntitiesProcessor(_LOG, MagicMock(), MagicMock())
        self.data_entities_processor.org_id = "o1"

    async def run_sync(self) -> None:
        cursor = self.graph.cursor
        for (collection, node_id), version in _SOURCE.items():
            if cursor is None or version > cursor:
                self.data_entities_processor._note_full_sync_seen(self.graph.connector_id, collection, node_id)
                if node_id in _GROUP_OF:
                    # As the processor does: a record it links also marks its group seen.
                    self.data_entities_processor._note_full_sync_seen(
                        self.graph.connector_id, RECORD_GROUPS, _GROUP_OF[node_id]
                    )
        self.graph.cursor = max(_SOURCE.values())

    def full_sync_coverage(self) -> FullSyncCoverage:
        return FullSyncCoverage()

    async def cleanup(self) -> None:
        return None


async def _start(graph: _Graph, payload: dict) -> None:
    service = EventService(_LOG, MagicMock(), graph.provider)
    coordinator = _InlineCoordinator()
    lease = SyncLease(graph.connector_id, f"tok-{uuid.uuid4().hex[:6]}", 1)
    with patch.object(service, "_ensure_connector", AsyncMock(return_value=_CursorConnector(graph))):
        ack, handed_off = await service._start_sync_with_lease(
            "jira", {"orgId": "o1", "connectorId": graph.connector_id, **payload}, lease, coordinator
        )
    assert ack and handed_off and coordinator.task is not None
    await asyncio.wait_for(coordinator.task, timeout=10)


def _graph(**app: object) -> _Graph:
    return _Graph(f"c-{uuid.uuid4().hex[:8]}", app)


@pytest.mark.asyncio
async def test_a_full_sync_whose_sync_points_were_deleted_unlinks_only_what_is_gone() -> None:
    graph = _graph()

    await _start(graph, {"fullSync": True})

    assert (graph.wrongly_unlinked(), graph.really_gone_unlinked()) == (0, 1)
    assert not graph.app.get(_OWED)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "delete_outcome",
    [(0, False), RuntimeError("sync points unreachable")],
    ids=["returned_false", "raised"],
)
async def test_a_full_sync_whose_sync_points_were_not_deleted_unlinks_nothing_and_owes_one(
    delete_outcome: object,
) -> None:
    graph = _graph()
    graph.delete_sync_points = delete_outcome

    await _start(graph, {"fullSync": True})

    assert (graph.wrongly_unlinked(), graph.really_gone_unlinked()) == (0, 0), (
        f"wrongly unlinked: {graph.wrongly_unlinked()}, really gone unlinked: {graph.really_gone_unlinked()}"
    )
    assert graph.app.get(_OWED) is True

    # The owed full sync, once the sync points can be deleted, removes what is gone.
    graph.delete_sync_points = (1, True)
    await _start(graph, {"fullSync": False})

    assert (graph.wrongly_unlinked(), graph.really_gone_unlinked()) == (0, 1)
    assert not graph.app.get(_OWED)


@pytest.mark.asyncio
async def test_a_full_sync_resumed_after_a_restart_whose_sync_points_were_not_deleted_unlinks_nothing() -> None:
    graph = _graph(**{_OWED: True, ConnectorStateKeys.PENDING_RESYNC: True, "status": "IDLE"})
    graph.delete_sync_points = (0, False)

    await _start(graph, {"fullSync": False, "bootResume": True})

    assert (graph.wrongly_unlinked(), graph.really_gone_unlinked()) == (0, 0), (
        f"wrongly unlinked: {graph.wrongly_unlinked()}, really gone unlinked: {graph.really_gone_unlinked()}"
    )
    assert graph.app.get(_OWED) is True


@pytest.mark.asyncio
async def test_a_full_sync_of_a_connector_with_no_sync_points_yet_unlinks_what_is_gone() -> None:
    graph = _graph()
    graph.cursor = None
    graph.delete_sync_points = (0, True)

    await _start(graph, {"fullSync": True})

    assert (graph.wrongly_unlinked(), graph.really_gone_unlinked()) == (0, 1)
    assert not graph.app.get(_OWED)


@pytest.mark.asyncio
async def test_a_full_sync_whose_reset_failed_twice_in_a_row_still_owes_one() -> None:
    graph = _graph()
    graph.delete_sync_points = (0, False)

    await _start(graph, {"fullSync": True})
    assert (graph.wrongly_unlinked(), graph.really_gone_unlinked()) == (0, 0)
    assert graph.app.get(_OWED) is True

    await _start(graph, {"fullSync": False})
    assert (graph.wrongly_unlinked(), graph.really_gone_unlinked()) == (0, 0)
    assert graph.app.get(_OWED) is True

    graph.delete_sync_points = (1, True)
    await _start(graph, {"fullSync": False})

    assert (graph.wrongly_unlinked(), graph.really_gone_unlinked()) == (0, 1)
    assert not graph.app.get(_OWED)
