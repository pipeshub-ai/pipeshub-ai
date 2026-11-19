"""The retrieval service reads the stored record that holds a hit's content, keyed as the flattening looks it up."""
from __future__ import annotations

import inspect
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.modules.retrieval import retrieval_service
from app.modules.retrieval.content_locator import SingleHolderLocator
from app.modules.retrieval.retrieval_service import _load_content_record


def _hit(**metadata: object) -> dict:
    return {"metadata": {"virtualRecordId": "v1", "isBlockGroup": True, **metadata}}


async def _load(hit: dict, store: dict, record: object = "rec") -> tuple[bool, AsyncMock]:
    async def fake(key: str, target: dict, *_a: object, **_k: object) -> None:
        target[key] = record

    reader = AsyncMock(side_effect=fake)
    locator = SingleHolderLocator()
    with patch.object(retrieval_service, "get_record", reader):
        found = await _load_content_record(hit, store, MagicMock(), "org-1", {"v1": {}}, locator)
    return found, reader


@pytest.mark.asyncio
async def test_a_hit_with_no_other_holder_is_read_by_its_own_record_id_as_before() -> None:
    store: dict = {}
    found, reader = await _load(_hit(), store)
    assert found and list(store) == ["v1"]
    (args, kwargs) = reader.await_args
    assert args[0] == "v1"
    assert kwargs == {}, "the call is exactly the one it always was"


@pytest.mark.asyncio
async def test_a_hit_whose_content_is_held_elsewhere_is_read_from_that_stored_record() -> None:
    store: dict = {}
    found, reader = await _load(_hit(contentVrid="c1", holderIndex=2), store)
    assert found and list(store) == ["c1"], "stored under the key the flattening looks it up by"
    (args, kwargs) = reader.await_args
    assert args[0] == "c1"
    assert kwargs["identity_vrid"] == "v1" and kwargs["holder_index"] == 2
    assert isinstance(kwargs["content_locator"], SingleHolderLocator)


@pytest.mark.asyncio
async def test_a_stored_record_is_read_once_for_every_hit_on_it() -> None:
    store: dict = {}
    _, reader = await _load(_hit(contentVrid="c1"), store)
    again, reader2 = await _load(_hit(contentVrid="c1"), store)
    assert again and reader.await_count == 1 and reader2.await_count == 0


@pytest.mark.asyncio
async def test_a_record_that_cannot_be_read_is_reported_missing() -> None:
    found, _ = await _load(_hit(contentVrid="c1"), {}, record=None)
    assert found is False


def test_the_search_goes_through_the_helper_not_a_read_by_the_hits_own_id() -> None:
    source = inspect.getsource(retrieval_service.RetrievalService.search_with_filters)
    assert "_load_content_record(" in source
    assert "await get_record(virtual_id" not in source
