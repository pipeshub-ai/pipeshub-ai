"""The nameFolded backfill runs once, in batches, and records that it ran (SC-6)."""
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.migrations.code_block_name_folded_migration import (
    MIGRATION_FLAG_KEY,
    run_code_block_name_folded_backfill,
)

pytestmark = pytest.mark.asyncio


def _config(flag=None) -> MagicMock:
    config = MagicMock()
    config.get_config = AsyncMock(return_value=flag)
    config.set_config = AsyncMock(return_value=True)
    return config


async def test_backfills_in_batches_until_none_is_left_then_sets_the_flag() -> None:
    graph = MagicMock()
    graph.backfill_block_name_folded = AsyncMock(side_effect=[5000, 120, 0])
    config = _config()

    assert await run_code_block_name_folded_backfill(config, graph, MagicMock()) == 5120
    assert graph.backfill_block_name_folded.await_count == 3
    config.set_config.assert_awaited_once_with(MIGRATION_FLAG_KEY, {"done": True, "blocks": 5120})


async def test_does_nothing_once_done() -> None:
    graph = MagicMock()
    graph.backfill_block_name_folded = AsyncMock()
    assert await run_code_block_name_folded_backfill(
        _config({"done": True}), graph, MagicMock()) == 0
    graph.backfill_block_name_folded.assert_not_awaited()


async def test_a_failed_batch_leaves_the_flag_unset() -> None:
    graph = MagicMock()
    graph.backfill_block_name_folded = AsyncMock(side_effect=RuntimeError("db down"))
    config = _config()
    with pytest.raises(RuntimeError):
        await run_code_block_name_folded_backfill(config, graph, MagicMock())
    config.set_config.assert_not_awaited()
