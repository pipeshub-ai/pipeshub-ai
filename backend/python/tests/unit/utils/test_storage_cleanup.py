from unittest.mock import AsyncMock, MagicMock

import pytest

from app.utils.storage_cleanup import get_unreferenced_virtual_record_ids


@pytest.mark.asyncio
async def test_filters_virtual_record_ids_with_live_references() -> None:
    graph_provider = MagicMock()
    graph_provider.get_records_by_virtual_record_id = AsyncMock(
        side_effect=[[], ["live-record"]]
    )

    result = await get_unreferenced_virtual_record_ids(
        ["unreferenced", "shared", "unreferenced"], graph_provider
    )

    assert result == ["unreferenced"]
    assert graph_provider.get_records_by_virtual_record_id.await_count == 2
    assert all(
        call.kwargs["raise_on_error"] is True
        for call in graph_provider.get_records_by_virtual_record_id.await_args_list
    )


@pytest.mark.asyncio
async def test_graph_lookup_failure_fails_closed() -> None:
    graph_provider = MagicMock()
    graph_provider.get_records_by_virtual_record_id = AsyncMock(
        side_effect=RuntimeError("graph unavailable")
    )

    with pytest.raises(RuntimeError, match="graph unavailable"):
        await get_unreferenced_virtual_record_ids(["unknown"], graph_provider)