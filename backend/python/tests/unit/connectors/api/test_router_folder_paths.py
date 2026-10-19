"""Folder filter values are checked wherever sync filters are validated."""

from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from app.connectors.api.router import _validate_sync_filter_selections


def _registry() -> AsyncMock:
    registry = AsyncMock()
    registry.get_connector_metadata = AsyncMock(
        return_value={"config": {"filters": {"sync": {"schema": {"fields": [{"name": "folder_paths"}]}}}}}
    )
    return registry


def _config(value: object) -> dict:
    return {"filters": {"sync": {"values": {"folder_paths": {"operator": "in", "value": value}}}}}


async def test_a_folder_that_climbs_out_of_the_share_is_a_400() -> None:
    with pytest.raises(HTTPException) as exc_info:
        await _validate_sync_filter_selections(_registry(), "SMB", _config(["../../etc"]), "saving")

    assert exc_info.value.status_code == 400
    assert '"../../etc"' in exc_info.value.detail


async def test_a_folder_inside_the_share_is_accepted() -> None:
    await _validate_sync_filter_selections(_registry(), "SMB", _config(["finance/restricted"]), "saving")
