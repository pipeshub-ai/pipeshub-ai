"""record_in_group answers from the record's BELONGS_TO edges, and a failed read raises."""

from unittest.mock import AsyncMock

import pytest

from tests.unit.connectors.core.test_permission_change_cache_invalidation import (
    _processor,
)


async def test_a_record_filed_in_the_group_is_in_it() -> None:
    tx = AsyncMock()
    tx.get_edges_from_node = AsyncMock(return_value=[{"_to": "recordGroups/drive-g"}, {"_to": "recordGroups/swm-a"}])
    proc = _processor(tx, [])

    assert await proc.record_in_group("rec-1", "swm-a") is True
    assert await proc.record_in_group("rec-1", "other") is False
    tx.get_edges_from_node.assert_awaited_with("records/rec-1", "belongsTo", raise_on_error=True)


async def test_a_failed_read_is_raised_not_read_as_not_in_the_group() -> None:
    tx = AsyncMock()
    tx.get_edges_from_node = AsyncMock(side_effect=RuntimeError("graph down"))
    proc = _processor(tx, [])

    with pytest.raises(RuntimeError, match="graph down"):
        await proc.record_in_group("rec-1", "swm-a")
