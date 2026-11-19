"""A ``start_block`` that the installed content locator reads as a block address is refused for a record whose content it holds in
several stored records; with the default locator every number is a position, as it always was."""
from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException

from app.api.routes import records as route
from app.modules.retrieval.content_locator import (
    SingleHolderLocator,
    set_locator_factory,
)
from tests.unit.api.routes.test_records_read import _read, _request

if TYPE_CHECKING:
    from collections.abc import Iterator

BIG = 1 << 40


@pytest.fixture(autouse=True)
def _demo_data_visible() -> Iterator[None]:
    with patch.object(route, "is_hidden_demo_record", AsyncMock(return_value=False)):
        yield


class Addressing(SingleHolderLocator):
    def __init__(self, *, several: bool = True, boom: bool = False) -> None:
        self.several, self.boom, self.asked = several, boom, []

    def is_address(self, index: int) -> bool:
        return index > 1 << 20

    async def holds_several(self, vrids: object) -> bool:
        self.asked.append(vrids)
        if self.boom:
            raise RuntimeError("unreadable")
        return self.several


async def test_with_the_default_locator_any_start_block_is_a_position_and_nothing_extra_is_read() -> None:
    request, graph, _ = _request()
    fetch = AsyncMock(return_value={"ok": False})

    with patch.object(route, "_fetch_multiple_records_impl", fetch):
        await _read(request)
        reads_without = graph.get_document.await_count
        await _read(request, start_block=BIG)

    assert fetch.await_count == 2
    assert graph.get_document.await_count == 2 * reads_without


async def test_a_block_address_on_a_record_held_in_several_stored_records_is_a_422_and_nothing_is_read() -> None:
    locator = Addressing()
    set_locator_factory(lambda graph: locator)
    request, graph, _ = _request()
    graph.get_document = AsyncMock(return_value={"connectorId": "c-1", "virtualRecordId": "v-1"})
    fetch = AsyncMock()

    with patch.object(route, "_fetch_multiple_records_impl", fetch), pytest.raises(HTTPException) as err:
        await _read(request, start_block=BIG)

    assert err.value.status_code == 422
    assert "block address" in err.value.detail
    assert locator.asked == [["v-1"]]
    fetch.assert_not_awaited()


async def test_a_position_below_the_address_range_is_never_checked() -> None:
    locator = Addressing()
    set_locator_factory(lambda graph: locator)
    request, _graph, _ = _request()
    fetch = AsyncMock(return_value={"ok": False})

    with patch.object(route, "_fetch_multiple_records_impl", fetch):
        await _read(request, start_block=40)

    assert locator.asked == []
    fetch.assert_awaited_once()


async def test_an_address_on_a_record_held_in_one_stored_record_is_read_as_a_position() -> None:
    set_locator_factory(lambda graph: Addressing(several=False))
    request, graph, _ = _request()
    graph.get_document = AsyncMock(return_value={"connectorId": "c-1", "virtualRecordId": "v-1"})
    fetch = AsyncMock(return_value={"ok": False})

    with patch.object(route, "_fetch_multiple_records_impl", fetch):
        await _read(request, start_block=BIG)

    fetch.assert_awaited_once()


async def test_a_layout_that_cannot_be_read_refuses_with_a_503_instead_of_guessing() -> None:
    set_locator_factory(lambda graph: Addressing(boom=True))
    request, graph, _ = _request()
    graph.get_document = AsyncMock(return_value={"connectorId": "c-1", "virtualRecordId": "v-1"})
    fetch = AsyncMock()

    with patch.object(route, "_fetch_multiple_records_impl", fetch), pytest.raises(HTTPException) as err:
        await _read(request, start_block=BIG)

    assert err.value.status_code == 503
    fetch.assert_not_awaited()
