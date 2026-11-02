"""Edge cases of keeping stored access when a permission read fails or returns part of the data.

The ServiceNow cases are in ``servicenow/test_partial_read_edge_cases.py``."""

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest
from test_salesforce_connector import _make_connector


def _composite(items) -> MagicMock:
    return MagicMock(success=True, data={"compositeResponse": items}, error=None)


async def _drain(connector, incomplete) -> list[tuple[str, str, str]]:
    return [
        x async for x in connector._iter_record_access(
            user_id="005000000000001", user_email="a@x.com", record_ids=["006000000000001"],
            api_version="59.0", incomplete=incomplete,
        )
    ]


@pytest.mark.asyncio
async def test_sf_a_non_200_composite_item_marks_the_user_incomplete() -> None:
    c = _make_connector()
    c.data_source = MagicMock()
    c.data_source.composite = AsyncMock(return_value=_composite([{"httpStatusCode": 503, "body": []}]))
    inc: set = set()
    assert await _drain(c, inc) == [] and inc == {"a@x.com"}


@pytest.mark.asyncio
async def test_sf_an_invalid_user_id_marks_the_user_incomplete() -> None:
    c = _make_connector()
    inc: set = set()
    out = [x async for x in c._iter_record_access(
        user_id="bad id'", user_email="a@x.com", record_ids=["006000000000001"], api_version="59.0", incomplete=inc)]
    assert out == [] and inc == {"a@x.com"}


@pytest.mark.asyncio
async def test_sf_a_raising_composite_call_marks_the_user_incomplete() -> None:
    c = _make_connector()
    c.data_source = MagicMock()
    c.data_source.composite = AsyncMock(side_effect=RuntimeError("socket reset"))
    inc: set = set()
    assert await _drain(c, inc) == [] and inc == {"a@x.com"}


@pytest.mark.asyncio
async def test_sf_a_failed_read_of_record_group_access_marks_the_user_incomplete() -> None:
    c = _make_connector()
    c.data_source = MagicMock()
    c.data_source.composite = AsyncMock(return_value=MagicMock(success=False, data=None, error="503"))
    inc: set = set()
    await c._sync_permissions_for_user(
        user={"Id": "005000000000001", "Email": "a@x.com"}, salesforce_external_ids=[],
        salesforce_record_group_external_ids=["001000000000001"], api_version="59.0",
        semaphore=asyncio.Semaphore(1), incomplete=inc)
    assert inc == {"a@x.com"}
