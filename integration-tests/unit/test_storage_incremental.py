"""Unit tests for storage incremental-sync IT helpers (no live services)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from helper.storage_incremental import (
    assert_incremental_new_files,
    settle_record_baseline,
    sync_until_names_visible,
)

pytestmark = pytest.mark.unit


@pytest.mark.asyncio
async def test_settle_baseline_and_assert_new_files_happy_path() -> None:
    """IDLE settle + count/name assertions succeed for a completed incremental sync."""
    client = MagicMock()
    graph = MagicMock()
    graph.assert_record_paths_or_names_contain = AsyncMock()

    with (
        patch(
            "helper.storage_incremental.wait_for_sync_completion",
            new_callable=AsyncMock,
        ) as wait_sync,
        patch(
            "helper.storage_incremental.async_wait_for_stable_record_count",
            new_callable=AsyncMock,
            return_value=5,
        ) as wait_stable,
    ):
        before = await settle_record_baseline(client, graph, "conn-1", timeout=120)

    assert before == 5
    wait_sync.assert_awaited_once_with(
        client, graph, "conn-1", timeout=120, sync_start_timeout=0
    )
    wait_stable.assert_awaited_once_with(graph, "conn-1")

    await assert_incremental_new_files(
        graph,
        "conn-1",
        before_count=5,
        after_count=7,
        new_names=["a.csv", "b.csv"],
    )
    graph.assert_record_paths_or_names_contain.assert_awaited_once_with(
        "conn-1", ["a.csv", "b.csv"]
    )


@pytest.mark.asyncio
async def test_sync_until_names_visible_retries_then_fails_short_delta() -> None:
    """Retry when names are missing after the first sync; reject too-small count deltas."""
    client = MagicMock()
    graph = MagicMock()
    graph.record_paths_or_names_contain = AsyncMock(side_effect=[False, True, True])
    graph.count_records = AsyncMock(return_value=10)
    restarts: list[str] = []

    def _restart(_client: object, connector_id: str) -> None:
        restarts.append(connector_id)

    with (
        patch(
            "helper.storage_incremental.wait_for_sync_completion",
            new_callable=AsyncMock,
        ) as wait_sync,
        patch(
            "helper.storage_incremental.wait_until_graph_condition",
            new_callable=AsyncMock,
        ),
    ):
        after = await sync_until_names_visible(
            client,
            graph,
            "conn-1",
            ["a.csv", "b.csv"],
            max_attempts=3,
            restart_fn=_restart,
        )

    assert after == 10
    assert restarts == ["conn-1", "conn-1"]
    assert wait_sync.await_count == 2

    with pytest.raises(AssertionError, match="at least 2 new records"):
        await assert_incremental_new_files(
            graph,
            "conn-1",
            before_count=5,
            after_count=6,
            new_names=["a.csv", "b.csv"],
        )
