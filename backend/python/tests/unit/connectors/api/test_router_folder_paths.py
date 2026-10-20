"""Folder filter values are checked wherever sync filters are validated."""

from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from app.connectors.api.router import (
    _require_filter_sections_are_objects,
    _validate_sync_filter_selections,
)


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


@pytest.mark.parametrize("values", ["x", ["folder_paths"], 3])
def test_filter_values_that_are_not_an_object_are_a_400(values: object) -> None:
    with pytest.raises(HTTPException) as exc_info:
        _require_filter_sections_are_objects({"sync": {"values": values}})

    assert exc_info.value.status_code == 400
    assert exc_info.value.detail == "filters.sync.values must be an object"


@pytest.mark.parametrize("filters", [{"sync": {"values": {}}}, {"indexing": {"values": None}}, {"sync": {}}])
def test_filter_values_that_are_an_object_or_unset_are_accepted(filters: dict) -> None:
    _require_filter_sections_are_objects(filters)
