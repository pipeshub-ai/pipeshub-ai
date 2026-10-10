"""The sweep after a full sync: only a sync that finished and read everything removes
the edges it did not write again (FS-01, JIRA-DC-03)."""
import logging
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.connectors.core.base.connector.connector_service import BaseConnector
from app.connectors.core.sync.sync_coordinator import SyncLease
from app.connectors.core.sync.sync_runner import run_sync_task

GENERATION = 1_760_000_000_000


def _graph_provider() -> AsyncMock:
    gp = AsyncMock()
    gp.batch_upsert_nodes = AsyncMock()
    gp.sweep_connector_sync_edges = AsyncMock(return_value=(3, True))
    return gp


def _connector(run_sync: AsyncMock) -> MagicMock:
    connector = MagicMock()
    connector.run_sync = run_sync
    connector.keep_stored_access = lambda reason: BaseConnector.keep_stored_access(connector, reason)
    return connector


@pytest.mark.asyncio
async def test_a_sync_that_raises_mid_way_sweeps_nothing() -> None:
    gp = _graph_provider()
    connector = _connector(AsyncMock(side_effect=RuntimeError("source unreachable")))

    with pytest.raises(RuntimeError):
        await run_sync_task(connector, "c1", gp, logging.getLogger("t"), sweep_generation=GENERATION)

    gp.sweep_connector_sync_edges.assert_not_awaited()
    gp.delete_connector_sync_edges.assert_not_awaited()


@pytest.mark.asyncio
async def test_a_successful_full_sync_sweeps_its_own_tag() -> None:
    gp = _graph_provider()

    await run_sync_task(_connector(AsyncMock()), "c1", gp, logging.getLogger("t"), sweep_generation=GENERATION)

    gp.sweep_connector_sync_edges.assert_awaited_once_with("c1", GENERATION)


@pytest.mark.asyncio
async def test_an_incremental_sync_sweeps_nothing() -> None:
    gp = _graph_provider()

    await run_sync_task(_connector(AsyncMock()), "c1", gp, logging.getLogger("t"))

    gp.sweep_connector_sync_edges.assert_not_awaited()


@pytest.mark.asyncio
async def test_a_sync_that_kept_stored_access_sweeps_nothing() -> None:
    """A kept project, group or role was not rewritten, so its edges still carry the tag."""
    gp = _graph_provider()
    connector = _connector(AsyncMock())
    connector.run_sync = AsyncMock(
        side_effect=lambda: connector.keep_stored_access("the permission scheme of project ENG could not be read")
    )

    await run_sync_task(connector, "c1", gp, logging.getLogger("t"), sweep_generation=GENERATION)

    gp.sweep_connector_sync_edges.assert_not_awaited()


@pytest.mark.asyncio
async def test_a_reason_kept_by_an_earlier_run_does_not_block_this_sweep() -> None:
    gp = _graph_provider()
    connector = _connector(AsyncMock())
    connector.stored_access_kept = "an earlier incremental run"

    await run_sync_task(connector, "c1", gp, logging.getLogger("t"), sweep_generation=GENERATION)

    gp.sweep_connector_sync_edges.assert_awaited_once()


@pytest.mark.asyncio
async def test_a_stopped_full_sync_sweeps_nothing() -> None:
    gp = _graph_provider()
    lease = SyncLease("c1", "tok", 1)
    lease.stop_requested.set()

    await run_sync_task(
        _connector(AsyncMock()), "c1", gp, logging.getLogger("t"),
        sweep_generation=GENERATION, lease=lease, coordinator=AsyncMock(),
    )

    gp.sweep_connector_sync_edges.assert_not_awaited()


@pytest.mark.asyncio
async def test_a_failed_sweep_does_not_fail_the_sync() -> None:
    gp = _graph_provider()
    gp.sweep_connector_sync_edges = AsyncMock(side_effect=RuntimeError("db down"))

    await run_sync_task(_connector(AsyncMock()), "c1", gp, logging.getLogger("t"), sweep_generation=GENERATION)

    gp.sweep_connector_sync_edges.assert_awaited_once()


# R1-24: tags belong to one generation. A sync that does not sweep removes its own
# (and any older) tags, and only a running sync's tag reads as "not there yet".


def _tag(generation: int) -> dict:
    return {"_to": "recordGroups/g1", "pendingSweep": generation}


@pytest.mark.asyncio
@pytest.mark.parametrize("outcome", ["raises", "kept", "stopped", "sweep_failed"])
async def test_a_full_sync_that_does_not_sweep_clears_its_tags(outcome) -> None:
    gp = _graph_provider()
    gp.clear_connector_sync_edge_tags = AsyncMock(return_value=(5, True))
    connector = _connector(AsyncMock())
    lease = coordinator = None
    if outcome == "raises":
        connector.run_sync = AsyncMock(side_effect=RuntimeError("source unreachable"))
    elif outcome == "kept":
        connector.run_sync = AsyncMock(side_effect=lambda: connector.keep_stored_access("group list unreadable"))
    elif outcome == "stopped":
        lease, coordinator = SyncLease("c1", "tok", 1), AsyncMock()
        lease.stop_requested.set()
    else:
        gp.sweep_connector_sync_edges = AsyncMock(return_value=(0, False))

    try:
        await run_sync_task(
            connector, "c1", gp, logging.getLogger("t"),
            sweep_generation=GENERATION, lease=lease, coordinator=coordinator,
        )
    except RuntimeError:
        assert outcome == "raises"

    gp.clear_connector_sync_edge_tags.assert_awaited_once_with("c1", GENERATION)


@pytest.mark.asyncio
async def test_a_swept_full_sync_and_an_incremental_one_clear_nothing() -> None:
    gp = _graph_provider()
    gp.clear_connector_sync_edge_tags = AsyncMock(return_value=(0, True))

    await run_sync_task(_connector(AsyncMock()), "c1", gp, logging.getLogger("t"), sweep_generation=GENERATION)
    await run_sync_task(_connector(AsyncMock()), "c1", gp, logging.getLogger("t"))

    gp.clear_connector_sync_edge_tags.assert_not_awaited()


@pytest.mark.asyncio
async def test_only_the_running_full_syncs_tag_reads_as_absent() -> None:
    from app.services.graph_db.common.sync_sweep import awaits_sweep

    gp = _graph_provider()
    seen: dict[str, bool] = {}

    async def run_sync() -> None:
        seen["own"] = awaits_sweep(_tag(GENERATION))
        seen["older"] = awaits_sweep(_tag(GENERATION - 1))

    await run_sync_task(_connector(AsyncMock(side_effect=run_sync)), "c1", gp, logging.getLogger("t"),
                        sweep_generation=GENERATION)

    assert seen == {"own": True, "older": False}
    assert not awaits_sweep(_tag(GENERATION)), "a finished sync's tag is stored state again"


@pytest.mark.asyncio
async def test_a_sync_that_kept_its_gates_sweeps_everything_else_and_untags_the_gates() -> None:
    """R1-10: a user listing that may have missed users keeps their gates, not the
    rest of what the sync did not write again."""
    gp = _graph_provider()
    gp.clear_connector_sync_edge_tags = AsyncMock(return_value=(2, True))
    connector = _connector(AsyncMock())
    connector.keep_stored_gates = lambda reason: BaseConnector.keep_stored_gates(connector, reason)
    connector.run_sync = AsyncMock(side_effect=lambda: connector.keep_stored_gates("user search"))

    await run_sync_task(connector, "c1", gp, logging.getLogger("t"), sweep_generation=GENERATION)

    gp.sweep_connector_sync_edges.assert_awaited_once_with("c1", GENERATION, keep_collections=("userAppRelation",))
    gp.clear_connector_sync_edge_tags.assert_awaited_once_with("c1", GENERATION)
