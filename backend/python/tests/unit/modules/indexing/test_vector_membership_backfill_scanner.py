"""Unit tests for the indexing vector membership backfill scanner."""

import asyncio
import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.config.constants.arangodb import CollectionNames
from app.connectors.core.constants import ConnectorStateKeys
from app.modules.indexing.vector_membership_backfill import (
    MAX_BACKFILL_ATTEMPTS,
    distinct_non_empty_vrids,
    run_vector_membership_backfill_tick,
)


class _Lock:
    def __init__(self, acquired: bool = True) -> None:
        self.acquired = acquired
        self.try_acquire = AsyncMock(side_effect=self._try_acquire)
        self.refresh = AsyncMock(return_value=True)
        self.release = AsyncMock()
        self.close = AsyncMock()

    async def _try_acquire(self) -> bool:
        return self.acquired


def _logger() -> MagicMock:
    return MagicMock()


@pytest.mark.asyncio
async def test_empty_connector_sets_flag_true():
    graph = AsyncMock()
    graph.get_app_needing_vector_membership_backfill = AsyncMock(
        return_value={"_key": "app-1"}
    )
    graph.page_records_for_vector_membership_backfill = AsyncMock(return_value=[])
    graph.update_node = AsyncMock()
    pipeline = AsyncMock()

    await run_vector_membership_backfill_tick(
        logger=_logger(),
        graph_provider=graph,
        pipeline=pipeline,
        lock=_Lock(),
        page_size=50,
        vrid_pause_ms=0,
    )

    pipeline.sync_vector_membership.assert_not_awaited()
    graph.update_node.assert_awaited_once_with(
        "app-1",
        CollectionNames.APPS.value,
        {
            ConnectorStateKeys.VECTOR_MEMBERSHIP_BACKFILLED: True,
            ConnectorStateKeys.VECTOR_MEMBERSHIP_BACKFILL_AFTER_KEY: None,
            ConnectorStateKeys.VECTOR_MEMBERSHIP_BACKFILL_FAILURES: 0,
            ConnectorStateKeys.VECTOR_MEMBERSHIP_BACKFILL_ATTEMPTS: 0,
            # Zero, and recorded: a pass that touched nothing is otherwise
            # indistinguishable from a real one once the flag is set.
            ConnectorStateKeys.VECTOR_MEMBERSHIP_BACKFILL_VRIDS: 0,
            ConnectorStateKeys.VECTOR_MEMBERSHIP_BACKFILL_EXHAUSTED: False,
        },
    )


@pytest.mark.asyncio
async def test_full_page_persists_cursor_then_short_page_completes():
    graph = AsyncMock()
    graph.get_app_needing_vector_membership_backfill = AsyncMock(
        side_effect=[
            {"_key": "app-1"},
            {
                "_key": "app-1",
                ConnectorStateKeys.VECTOR_MEMBERSHIP_BACKFILL_AFTER_KEY: "r2",
            },
        ]
    )
    graph.page_records_for_vector_membership_backfill = AsyncMock(
        side_effect=[
            [
                {"_key": "r1", "virtualRecordId": "v1"},
                {"_key": "r2", "virtualRecordId": "v2"},
            ],
            [{"_key": "r3", "virtualRecordId": "v3"}],
        ]
    )
    graph.update_node = AsyncMock()
    pipeline = AsyncMock()
    lock = _Lock()

    await run_vector_membership_backfill_tick(
        logger=_logger(),
        graph_provider=graph,
        pipeline=pipeline,
        lock=lock,
        page_size=2,
        vrid_pause_ms=0,
    )
    graph.update_node.assert_awaited_with(
        "app-1",
        CollectionNames.APPS.value,
        {
            ConnectorStateKeys.VECTOR_MEMBERSHIP_BACKFILL_AFTER_KEY: "r2",
            ConnectorStateKeys.VECTOR_MEMBERSHIP_BACKFILL_VRIDS: 2,
        },
    )
    assert pipeline.sync_vector_membership.await_count == 2

    await run_vector_membership_backfill_tick(
        logger=_logger(),
        graph_provider=graph,
        pipeline=pipeline,
        lock=lock,
        page_size=2,
        vrid_pause_ms=0,
    )
    graph.page_records_for_vector_membership_backfill.assert_awaited_with(
        "app-1", "r2", 2
    )
    assert graph.update_node.await_args.args[2][
        ConnectorStateKeys.VECTOR_MEMBERSHIP_BACKFILLED
    ] is True
    assert pipeline.sync_vector_membership.await_count == 3


@pytest.mark.asyncio
async def test_no_app_needed_is_a_noop():
    graph = AsyncMock()
    graph.get_app_needing_vector_membership_backfill = AsyncMock(return_value=None)
    pipeline = AsyncMock()

    await run_vector_membership_backfill_tick(
        logger=_logger(),
        graph_provider=graph,
        pipeline=pipeline,
        lock=_Lock(),
        page_size=50,
        vrid_pause_ms=0,
    )

    graph.page_records_for_vector_membership_backfill.assert_not_awaited()
    graph.update_node.assert_not_awaited()
    pipeline.sync_vector_membership.assert_not_awaited()


@pytest.mark.asyncio
async def test_lock_not_acquired_skips_work():
    graph = AsyncMock()
    pipeline = AsyncMock()

    await run_vector_membership_backfill_tick(
        logger=_logger(),
        graph_provider=graph,
        pipeline=pipeline,
        lock=_Lock(acquired=False),
        page_size=50,
        vrid_pause_ms=0,
    )

    graph.get_app_needing_vector_membership_backfill.assert_not_awaited()
    pipeline.sync_vector_membership.assert_not_awaited()


@pytest.mark.asyncio
async def test_failed_vrid_continues_page():
    graph = AsyncMock()
    graph.get_app_needing_vector_membership_backfill = AsyncMock(
        return_value={"_key": "app-1"}
    )
    graph.page_records_for_vector_membership_backfill = AsyncMock(
        return_value=[
            {"_key": "r1", "virtualRecordId": "bad"},
            {"_key": "r2", "virtualRecordId": "good"},
        ]
    )
    graph.update_node = AsyncMock()
    pipeline = AsyncMock()
    pipeline.sync_vector_membership = AsyncMock(side_effect=[RuntimeError("boom"), None])

    await run_vector_membership_backfill_tick(
        logger=_logger(),
        graph_provider=graph,
        pipeline=pipeline,
        lock=_Lock(),
        page_size=50,
        vrid_pause_ms=0,
    )

    assert pipeline.sync_vector_membership.await_count == 2
    # The whole page still runs — one poison VRID must not pin the connector.
    update = graph.update_node.await_args.args[2]
    # ...but the connector is NOT marked done: the flag is what stops it being
    # revisited, so claiming success here would strand those VRIDs forever.
    assert ConnectorStateKeys.VECTOR_MEMBERSHIP_BACKFILLED not in update
    assert update[ConnectorStateKeys.VECTOR_MEMBERSHIP_BACKFILL_ATTEMPTS] == 1
    assert update[ConnectorStateKeys.VECTOR_MEMBERSHIP_BACKFILL_AFTER_KEY] is None


@pytest.mark.asyncio
async def test_repeated_failures_eventually_stop_retrying():
    """Bounded retries: a permanently broken VRID must not scan forever."""
    graph = AsyncMock()
    graph.get_app_needing_vector_membership_backfill = AsyncMock(
        return_value={
            "_key": "app-1",
            ConnectorStateKeys.VECTOR_MEMBERSHIP_BACKFILL_ATTEMPTS: 2,
        }
    )
    graph.page_records_for_vector_membership_backfill = AsyncMock(
        return_value=[{"_key": "r1", "virtualRecordId": "bad"}]
    )
    graph.update_node = AsyncMock()
    pipeline = AsyncMock()
    pipeline.sync_vector_membership = AsyncMock(side_effect=RuntimeError("boom"))

    await run_vector_membership_backfill_tick(
        logger=_logger(),
        graph_provider=graph,
        pipeline=pipeline,
        lock=_Lock(),
        page_size=50,
        vrid_pause_ms=0,
    )

    update = graph.update_node.await_args.args[2]
    assert update[ConnectorStateKeys.VECTOR_MEMBERSHIP_BACKFILLED] is True


@pytest.mark.asyncio
async def test_lost_leadership_stops_the_page():
    """Another replica is now on this connector; stop rather than write alongside."""
    graph = AsyncMock()
    graph.get_app_needing_vector_membership_backfill = AsyncMock(
        return_value={"_key": "app-1"}
    )
    graph.page_records_for_vector_membership_backfill = AsyncMock(
        return_value=[
            {"_key": "r1", "virtualRecordId": "v1"},
            {"_key": "r2", "virtualRecordId": "v2"},
        ]
    )
    graph.update_node = AsyncMock()
    pipeline = AsyncMock()

    class _LosingLock:
        async def try_acquire(self) -> bool:
            return True

        async def refresh(self) -> bool:
            return False

        async def release(self) -> None:
            return None

        async def close(self) -> None:
            return None

    await run_vector_membership_backfill_tick(
        logger=_logger(),
        graph_provider=graph,
        pipeline=pipeline,
        lock=_LosingLock(),
        page_size=50,
        vrid_pause_ms=0,
    )

    # Renewal is time-based, so detection happens at the batch boundary rather
    # than after every VRID; what must hold is that the tick stops without
    # recording progress it no longer owns.
    graph.update_node.assert_not_awaited()


def test_distinct_non_empty_vrids_skips_blanks_and_dupes():
    assert distinct_non_empty_vrids(
        [
            {"_key": "a", "virtualRecordId": "v1"},
            {"_key": "b", "virtualRecordId": ""},
            {"_key": "c", "virtualRecordId": None},
            {"_key": "d", "virtualRecordId": "v1"},
            {"_key": "e", "virtualRecordId": "v2"},
        ]
    ) == ["v1", "v2"]


@pytest.mark.asyncio
async def test_leader_lock_skips_when_redis_is_down():
    from app.modules.indexing.vector_membership_backfill import (
        VectorMembershipBackfillLeaderLock,
    )
    from app.services.messaging.config import RedisConfig

    lock = VectorMembershipBackfillLeaderLock(
        MagicMock(),
        RedisConfig(host="localhost", port=6379),
        owner="owner-1",
    )
    mock_client = AsyncMock()
    mock_client.ping = AsyncMock(side_effect=ConnectionError("down"))
    mock_client.aclose = AsyncMock()

    mock_provider = MagicMock()
    mock_provider.create_client = MagicMock(return_value=mock_client)

    with patch(
        "app.services.redis.connection_provider_factory.get_redis_provider",
        return_value=mock_provider,
    ):
        assert await lock.try_acquire() is False
    mock_client.aclose.assert_awaited()


@pytest.mark.asyncio
async def test_tick_failure_backs_off_and_recovers():
    """Backfill yields to trouble instead of holding cadence through an incident."""
    from app.modules.indexing import vector_membership_backfill as mod

    sleeps: list[float] = []

    async def _sleep(seconds):
        sleeps.append(seconds)
        if len(sleeps) >= 3:
            raise asyncio.CancelledError

    container = MagicMock()
    container.logger = MagicMock(return_value=_logger())
    graph = AsyncMock()

    call = {"n": 0}

    async def _tick(**kwargs):
        call["n"] += 1
        if call["n"] == 1:
            raise RuntimeError("db unavailable")

    env = {
        "VECTOR_MEMBERSHIP_BACKFILL_STARTUP_GRACE_SECONDS": "0",
        "VECTOR_MEMBERSHIP_BACKFILL_INTERVAL_SECONDS": "10",
    }
    with patch.dict(os.environ, env), \
         patch.object(mod.asyncio, "sleep", _sleep), \
         patch.object(mod, "run_vector_membership_backfill_tick", _tick), \
         patch.object(mod, "_resolve_indexing_pipeline", AsyncMock(return_value=MagicMock())), \
         patch.object(mod.MessagingUtils, "_get_redis_config", AsyncMock(return_value=MagicMock())):
        with pytest.raises(asyncio.CancelledError):
            await mod.run_vector_membership_backfill_loop(container, graph)

    # first tick failed -> doubled; second tick clean -> back to base
    assert sleeps[0] == 20
    assert sleeps[1] == 10


@pytest.mark.asyncio
async def test_page_query_error_does_not_mark_connector_done():
    """A swallowed query error would look identical to a finished connector."""
    graph = AsyncMock()
    graph.get_app_needing_vector_membership_backfill = AsyncMock(
        return_value={"_key": "app-1"}
    )
    graph.page_records_for_vector_membership_backfill = AsyncMock(
        side_effect=RuntimeError("arango unavailable")
    )
    graph.update_node = AsyncMock()

    with pytest.raises(RuntimeError):
        await run_vector_membership_backfill_tick(
            logger=_logger(),
            graph_provider=graph,
            pipeline=AsyncMock(),
            lock=_Lock(),
            page_size=50,
            vrid_pause_ms=0,
        )

    graph.update_node.assert_not_awaited()


@pytest.mark.asyncio
async def test_clean_pass_clears_failure_counters():
    """Stale counters would make a later re-run give up after one pass."""
    graph = AsyncMock()
    graph.get_app_needing_vector_membership_backfill = AsyncMock(
        return_value={
            "_key": "app-1",
            ConnectorStateKeys.VECTOR_MEMBERSHIP_BACKFILL_ATTEMPTS: 2,
        }
    )
    graph.page_records_for_vector_membership_backfill = AsyncMock(return_value=[])
    graph.update_node = AsyncMock()

    await run_vector_membership_backfill_tick(
        logger=_logger(),
        graph_provider=graph,
        pipeline=AsyncMock(),
        lock=_Lock(),
        page_size=50,
        vrid_pause_ms=0,
    )

    update = graph.update_node.await_args.args[2]
    assert update[ConnectorStateKeys.VECTOR_MEMBERSHIP_BACKFILLED] is True
    assert update[ConnectorStateKeys.VECTOR_MEMBERSHIP_BACKFILL_ATTEMPTS] == 0
    assert update[ConnectorStateKeys.VECTOR_MEMBERSHIP_BACKFILL_FAILURES] == 0


@pytest.mark.asyncio
async def test_lease_renewal_is_batched_not_per_vrid():
    """A 50-item page must not spend 50 Redis round trips renewing a 600s lease."""
    graph = AsyncMock()
    graph.get_app_needing_vector_membership_backfill = AsyncMock(
        return_value={"_key": "app-1"}
    )
    graph.page_records_for_vector_membership_backfill = AsyncMock(
        return_value=[{"_key": f"r{i}", "virtualRecordId": f"v{i}"} for i in range(50)]
    )
    graph.update_node = AsyncMock()

    refreshes = {"n": 0}

    class _CountingLock:
        async def try_acquire(self) -> bool:
            return True

        async def refresh(self) -> bool:
            refreshes["n"] += 1
            return True

        async def release(self) -> None:
            return None

        async def close(self) -> None:
            return None

    await run_vector_membership_backfill_tick(
        logger=_logger(),
        graph_provider=graph,
        pipeline=AsyncMock(),
        lock=_CountingLock(),
        page_size=50,
        vrid_pause_ms=0,
    )

    assert refreshes["n"] <= 6, f"renewed {refreshes['n']} times for 50 VRIDs"


@pytest.mark.asyncio
async def test_exhausted_pass_is_not_recorded_as_success():
    """Giving up stops the scan, but must not look like a clean pass.

    The completion flag is what stops this connector being revisited, so on its
    own it makes a connector that never succeeded indistinguishable from a
    healthy one — and zeroing the counters would erase the evidence too.
    """
    graph = AsyncMock()
    graph.get_app_needing_vector_membership_backfill = AsyncMock(
        return_value={
            "_key": "app-1",
            ConnectorStateKeys.VECTOR_MEMBERSHIP_BACKFILL_ATTEMPTS: MAX_BACKFILL_ATTEMPTS - 1,
            ConnectorStateKeys.VECTOR_MEMBERSHIP_BACKFILL_FAILURES: 0,
        }
    )
    graph.page_records_for_vector_membership_backfill = AsyncMock(
        return_value=[{"_key": "r1", "virtualRecordId": "vr-1"}]
    )
    graph.update_node = AsyncMock()

    pipeline = AsyncMock()
    pipeline.sync_vector_membership = AsyncMock(side_effect=RuntimeError("boom"))

    await run_vector_membership_backfill_tick(
        logger=_logger(),
        graph_provider=graph,
        pipeline=pipeline,
        lock=_Lock(),
        page_size=50,
        vrid_pause_ms=0,
    )

    _key, _collection, fields = graph.update_node.await_args.args
    assert fields[ConnectorStateKeys.VECTOR_MEMBERSHIP_BACKFILL_EXHAUSTED] is True
    # The evidence survives: a zeroed failure count would hide why it stopped.
    assert fields[ConnectorStateKeys.VECTOR_MEMBERSHIP_BACKFILL_FAILURES] == 1


@pytest.mark.asyncio
async def test_rewound_pass_does_not_inherit_the_previous_vrid_count():
    """A rewind re-walks from the start, so carrying the total forward would
    count the same VRIDs twice."""
    graph = AsyncMock()
    graph.get_app_needing_vector_membership_backfill = AsyncMock(
        return_value={
            "_key": "app-1",
            ConnectorStateKeys.VECTOR_MEMBERSHIP_BACKFILL_ATTEMPTS: 0,
            ConnectorStateKeys.VECTOR_MEMBERSHIP_BACKFILL_VRIDS: 7,
        }
    )
    graph.page_records_for_vector_membership_backfill = AsyncMock(
        return_value=[{"_key": "r1", "virtualRecordId": "vr-1"}]
    )
    graph.update_node = AsyncMock()

    pipeline = AsyncMock()
    pipeline.sync_vector_membership = AsyncMock(side_effect=RuntimeError("boom"))

    await run_vector_membership_backfill_tick(
        logger=_logger(),
        graph_provider=graph,
        pipeline=pipeline,
        lock=_Lock(),
        page_size=50,
        vrid_pause_ms=0,
    )

    _key, _collection, fields = graph.update_node.await_args.args
    assert fields[ConnectorStateKeys.VECTOR_MEMBERSHIP_BACKFILL_VRIDS] == 0
