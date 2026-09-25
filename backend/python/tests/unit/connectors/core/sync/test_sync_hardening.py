"""Coordinator, drain and finalizer behaviour found missing by live testing."""
import asyncio
import logging
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.config.constants.arangodb import AppStatus
from app.connectors.core.constants import ConnectorStateKeys
from app.connectors.core.sync import sync_runner
from app.connectors.core.sync.sync_coordinator import (
    Admission,
    LocalSyncCoordinator,
    stop_wait_sec,
)

LOG = logging.getLogger("t")


class TestStopSignalsTheLease:
    @pytest.mark.asyncio
    async def test_a_leased_sync_is_signalled_not_cancelled(self) -> None:
        """Cancelling a task before its first step skips run_sync_task entirely,
        finalizer included, so the lease was held until restart."""
        coordinator = LocalSyncCoordinator(LOG)
        admission, lease = await coordinator.begin("c1")
        assert admission is Admission.GRANTED

        started = asyncio.Event()

        async def body() -> None:
            started.set()
            await asyncio.sleep(5)

        task = await coordinator.spawn(lease, body())
        assert await coordinator.request_stop("c1") is True

        assert lease.stop_requested.is_set()
        assert not task.cancelled()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    @pytest.mark.asyncio
    async def test_nothing_held_reports_nothing_stopped(self) -> None:
        coordinator = LocalSyncCoordinator(LOG)
        assert await coordinator.request_stop("c1") is False


class TestAdmittedButNotSpawnedCounts:
    @pytest.mark.asyncio
    async def test_active_keys_include_a_lease_without_a_task(self) -> None:
        """The vector-store rebuild gate reads this; a sync in its full-sync prep
        holds a lease but has no task yet."""
        coordinator = LocalSyncCoordinator(LOG)
        await coordinator.begin("c1")
        assert coordinator.active_keys() == ["c1"]

    @pytest.mark.asyncio
    async def test_held_since_is_the_admission_time(self) -> None:
        coordinator = LocalSyncCoordinator(LOG)
        _, lease = await coordinator.begin("c1")
        assert coordinator.held_since_ms("c1") == lease.acquired_at_ms
        assert coordinator.held_since_ms("other") is None

    @pytest.mark.asyncio
    async def test_shutdown_is_remembered(self) -> None:
        coordinator = LocalSyncCoordinator(LOG)
        assert coordinator.shutting_down is False
        await coordinator.cancel_all()
        assert coordinator.shutting_down is True


class TestStopWaitParsing:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [("30", 30.0), ("0", 0.0), ("2.5", 2.5), ("abc", 15.0), ("15s", 15.0),
         ("", 15.0), ("-1", 15.0), ("inf", 15.0), ("nan", 15.0), ("9999", 600.0)],
    )
    def test_a_bad_value_falls_back_instead_of_crashing(self, raw, expected, monkeypatch) -> None:
        """Parsed at import, a bad value used to keep the service from starting."""
        monkeypatch.setenv("CONNECTOR_SYNC_DELETE_STOP_WAIT_SEC", raw)
        assert stop_wait_sec(LOG) == expected


def _drain_env(rows: list[dict], specs: dict[str, object]):
    graph = AsyncMock()
    state = {r["id"]: dict(r) for r in rows}

    async def get_nodes(_collection, _field, _values, _fields):
        await asyncio.sleep(0)
        return [dict(v) for v in state.values() if v.get("status") == AppStatus.QUEUED.value]

    async def update_node(cid, _collection, updates):
        await asyncio.sleep(0)
        state[cid].update(updates)

    graph.get_nodes_by_field_in = AsyncMock(side_effect=get_nodes)
    graph.update_node = AsyncMock(side_effect=update_node)

    from app.connectors.core.sync.sync_dispatcher import SubmitResult

    dispatcher = MagicMock()
    dispatcher.submit = AsyncMock(return_value=SubmitResult.ACCEPTED)
    coordinator = MagicMock()
    coordinator.try_claim_once = AsyncMock(return_value=True)

    async def resolve(_gp, ids, _logger):
        return [specs[i] for i in ids if i in specs]

    patches = (
        patch("app.connectors.core.sync.sync_dispatcher.get_dispatcher", return_value=dispatcher),
        patch("app.connectors.core.sync.sync_coordinator.get_coordinator", return_value=coordinator),
        patch.object(sync_runner, "resolve_resync_specs", AsyncMock(side_effect=resolve)),
    )
    return graph, state, dispatcher, patches


class TestDrain:
    @pytest.mark.asyncio
    async def test_an_inactive_queued_connector_is_dropped_not_published(self) -> None:
        spec_b = MagicMock(connector_id="b")
        graph, state, dispatcher, patches = _drain_env(
            [
                {"id": "a", "status": AppStatus.QUEUED.value, "pendingResync": True,
                 ConnectorStateKeys.IS_ACTIVE: False},
                {"id": "b", "status": AppStatus.QUEUED.value, "pendingResync": True,
                 ConnectorStateKeys.IS_ACTIVE: True},
            ],
            {"a": MagicMock(connector_id="a"), "b": spec_b},
        )
        with patches[0], patches[1], patches[2]:
            started = await sync_runner.drain_queued_syncs(graph, LOG)

        assert started == ["b"]
        dispatcher.submit.assert_awaited_once_with(spec_b)
        assert state["a"]["status"] == AppStatus.IDLE.value
        assert state["a"]["pendingResync"] is False

    @pytest.mark.asyncio
    async def test_concurrent_drains_publish_each_connector_once(self) -> None:
        """Finalizers in one process drain concurrently (shutdown fires them all);
        two passes over the same rows each published every queued connector."""
        graph, _state, dispatcher, patches = _drain_env(
            [{"id": "q", "status": AppStatus.QUEUED.value, "pendingResync": True,
              "updatedAtTimestamp": 10**15}],
            {"q": MagicMock(connector_id="q")},
        )
        with patches[0], patches[1], patches[2]:
            await asyncio.gather(*(sync_runner.drain_queued_syncs(graph, LOG) for _ in range(3)))

        assert dispatcher.submit.await_count == 1


class TestFinalize:
    @pytest.mark.asyncio
    @pytest.mark.parametrize("doc", [None, {"id": "c1", "status": "DELETING"}])
    async def test_no_idle_write_over_a_delete(self, doc) -> None:
        """The status write is an upsert: after a delete it resurrected a ghost App
        node, and during one it let a second DELETE past the guard."""
        graph = AsyncMock()
        graph.get_document = AsyncMock(return_value=doc)
        graph.batch_upsert_nodes = AsyncMock()
        with patch.object(sync_runner, "drain_queued_syncs", AsyncMock(return_value=[])):
            await sync_runner._finalize(graph, LOG, "c1", None, None)
        graph.batch_upsert_nodes.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_idle_is_written_normally(self) -> None:
        graph = AsyncMock()
        graph.get_document = AsyncMock(return_value={"id": "c1", "status": "SYNCING"})
        graph.batch_upsert_nodes = AsyncMock()
        with patch.object(sync_runner, "drain_queued_syncs", AsyncMock(return_value=[])):
            await sync_runner._finalize(graph, LOG, "c1", None, None)
        payload = graph.batch_upsert_nodes.await_args.args[0][0]
        assert payload["status"] == AppStatus.IDLE.value

    @pytest.mark.asyncio
    async def test_the_queue_is_drained_before_a_self_reissue(self) -> None:
        """Re-issuing first handed the freed slot straight back to the same
        connector; one whose schedule is shorter than its sync held it forever."""
        order: list[str] = []
        graph = AsyncMock()
        graph.get_document = AsyncMock(return_value={"id": "c1", "status": "SYNCING"})
        with patch.object(
            sync_runner, "drain_queued_syncs",
            AsyncMock(side_effect=lambda *_a, **_k: order.append("drain") or []),
        ), patch.object(
            sync_runner, "_reissue_pending_resync",
            AsyncMock(side_effect=lambda *_a, **_k: order.append("reissue")),
        ):
            await sync_runner._finalize(graph, LOG, "c1", None, None, resync_spec=MagicMock())
        assert order == ["drain", "reissue"]
