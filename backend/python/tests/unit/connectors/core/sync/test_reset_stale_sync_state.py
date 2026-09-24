"""The startup sweep that clears sync state left behind by a crash.

It is destructive by design — it writes IDLE over whatever it decides is stale —
so the interesting cases are all about when it must refuse to act. `peek_many`
answering "nothing is live" is not the same as knowing nothing is, and acting on
that difference resets a peer's running sync, after which the start guard reads
IDLE and lets a second one begin.

The single-process case still has to work, because there the sweep is the only
thing that ever unwedges a connector stuck SYNCING after a crash.
"""

import logging
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.config.constants.arangodb import AppStatus

_LOG = logging.getLogger("test")


def _graph(stuck=(), locked=()) -> AsyncMock:
    gp = AsyncMock()

    async def _by_field(collection, field, values, fields):
        if field == "status":
            return [{"id": c} for c in stuck]
        return [{"id": c, "status": "SYNCING"} for c in locked]

    gp.get_nodes_by_field_in = AsyncMock(side_effect=_by_field)
    gp.batch_upsert_nodes = AsyncMock()
    return gp


def _manager(*, live=(), reports_liveness=True, peek_raises=False) -> MagicMock:
    m = MagicMock()
    m.reports_liveness = reports_liveness
    m.peek_many = AsyncMock(
        side_effect=RuntimeError("redis down") if peek_raises else None,
        return_value=set(live),
    )
    return m


async def _sweep(graph, manager, *, workers=1, external=False):
    from app.connectors_main import reset_stale_sync_state

    with patch(
        "app.connectors_main.get_coordinator", return_value=manager
    ), patch(
        "app.connectors_main.max_connector_workers", return_value=workers
    ), patch(
        "app.connectors_main.sync_executor_enabled", return_value=external
    ):
        await reset_stale_sync_state(graph, _LOG)


def _written(graph):
    out = []
    for call in graph.batch_upsert_nodes.await_args_list:
        out.extend(call.args[0])
    return out


class TestItRefusesToGuess:
    @pytest.mark.asyncio
    async def test_no_liveness_and_several_workers_means_hands_off(self) -> None:
        """A peer is probably syncing these; resetting invites a second sync."""
        gp = _graph(stuck=["c1", "c2"])
        await _sweep(gp, _manager(reports_liveness=False), workers=4)
        gp.batch_upsert_nodes.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_no_liveness_and_an_external_executor_means_hands_off(self) -> None:
        gp = _graph(stuck=["c1"])
        await _sweep(gp, _manager(reports_liveness=False), workers=1, external=True)
        gp.batch_upsert_nodes.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_a_failed_liveness_read_touches_nothing(self) -> None:
        gp = _graph(stuck=["c1"])
        await _sweep(gp, _manager(peek_raises=True))
        gp.batch_upsert_nodes.assert_not_awaited()


class TestTheSingleProcessCaseStillWorks:
    @pytest.mark.asyncio
    async def test_one_worker_with_no_liveness_still_repairs(self) -> None:
        """OSS installs a null lease. If the guard caught this case too, a crash
        would leave the connector SYNCING until someone edited the database."""
        gp = _graph(stuck=["c1"])
        await _sweep(gp, _manager(reports_liveness=False), workers=1, external=False)
        assert [n["status"] for n in _written(gp)] == [AppStatus.IDLE.value]

    @pytest.mark.asyncio
    async def test_nothing_stale_writes_nothing(self) -> None:
        gp = _graph()
        await _sweep(gp, _manager())
        gp.batch_upsert_nodes.assert_not_awaited()


class TestWhatItLeavesAlone:
    @pytest.mark.asyncio
    async def test_a_connector_holding_a_live_lease_is_untouched(self) -> None:
        gp = _graph(stuck=["c1", "c2"])
        await _sweep(gp, _manager(live=["c1"]))
        ids = [n["id"] for n in _written(gp)]
        assert "c1" not in ids
        assert "c2" in ids

    @pytest.mark.asyncio
    async def test_a_deleting_connector_keeps_deleting(self) -> None:
        """It is mid-deletion, not mid-sync. IDLE would invite a resync into a
        connector whose records are being removed underneath it."""
        gp = AsyncMock()

        async def _by_field(collection, field, values, fields):
            if field == "status":
                return []
            return [{"id": "c1", "status": "DELETING"}]

        gp.get_nodes_by_field_in = AsyncMock(side_effect=_by_field)
        gp.batch_upsert_nodes = AsyncMock()
        await _sweep(gp, _manager())
        written = _written(gp)
        assert len(written) == 1
        # The stuck lock is released, but no status is written at all —
        # writing IDLE here is exactly the mistake being avoided.
        assert "status" not in written[0]
        assert written[0]["isLocked"] is False
