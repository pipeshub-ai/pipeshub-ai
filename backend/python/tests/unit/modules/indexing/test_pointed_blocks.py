"""QA-S7e-28: ``pointedBlocks`` records what the writers wrote, never what the store lists.

The set saved with a record is (the previous set for the blocks this run left alone) plus (the blocks this run wrote a point for).
A listing of the store is used only to find a recorded block whose point is gone, which is written again and stays recorded.
"""
from __future__ import annotations

import pytest
import pytest_asyncio

from app.services.vector_db.models import CollectionConfig
from tests.support.in_memory_vector_db import InMemoryVectorDBService
from tests.support.index_world import COLL, Blob, index, make_store

pytestmark = pytest.mark.asyncio

TEXT = "\n\n".join(f"Paragraph {i} about revenue and costs in quarter {i}." for i in range(5))


@pytest_asyncio.fixture
async def db() -> InMemoryVectorDBService:
    service = InMemoryVectorDBService()
    await service.create_collection(COLL, CollectionConfig(embedding_size=2))
    return service


def _block_ids(db: InMemoryVectorDBService) -> set[str]:
    return {p.payload["metadata"]["blockId"] for p in db.collections[COLL].values()}


def _embedded(store) -> list[str]:
    return [t for c in store._embed_documents_with_retry.await_args_list for t in c.args[0]]


async def _indexed(db: InMemoryVectorDBService, text: str = TEXT) -> Blob:
    blob = Blob()
    await index(make_store(db), blob, text)
    return blob


async def test_a_first_index_records_the_blocks_it_wrote(db) -> None:
    blob = await _indexed(db)
    assert set(blob.saved["pointedBlocks"]) == _block_ids(db) and len(blob.saved["pointedBlocks"]) == 5


async def test_a_point_lost_after_recording_stays_recorded_and_is_written_again(db) -> None:
    blob = await _indexed(db)
    lost_id, lost_block = next((pid, p.payload["metadata"]["blockId"]) for pid, p in db.collections[COLL].items())
    del db.collections[COLL][lost_id]
    store = make_store(db)
    await index(store, blob, TEXT)  # nothing changed
    assert lost_block in blob.saved["pointedBlocks"] and len(blob.saved["pointedBlocks"]) == 5
    assert lost_block in _block_ids(db), "the lost point is back in the store"
    assert len(_embedded(store)) == 1, "only the lost block was embedded"


async def test_an_unchanged_block_carries_forward_without_being_listed(db) -> None:
    blob = await _indexed(db)
    before = set(blob.saved["pointedBlocks"])
    changed = TEXT.replace("Paragraph 2 about revenue", "Paragraph 2 about something else")
    store = make_store(db)
    await index(store, blob, changed)
    after = set(blob.saved["pointedBlocks"])
    assert len(after) == 5 and len(before & after) == 4, "four blocks carried, the changed one is a new block"
    assert after == _block_ids(db)


async def test_a_block_removed_by_a_content_change_leaves_the_set(db) -> None:
    blob = await _indexed(db)
    before = set(blob.saved["pointedBlocks"])
    await index(make_store(db), blob, "\n\n".join(TEXT.split("\n\n")[:4]))
    after = set(blob.saved["pointedBlocks"])
    assert len(after) == 4 and after < before
    assert after == _block_ids(db)


async def test_a_listing_that_hit_its_bound_neither_heals_nor_changes_the_set(db, monkeypatch) -> None:
    """The sweep's listing stops after 2 points: it is not whole, so it can say nothing is missing and records nothing."""
    blob = await _indexed(db)
    before = list(blob.saved["pointedBlocks"])
    monkeypatch.setenv("INDEXING_STRAY_SWEEP_MAX_POINTS", "2")
    store = make_store(db)
    await index(store, blob, TEXT)
    assert sorted(blob.saved["pointedBlocks"]) == sorted(before)
    assert _embedded(store) == [], "a truncated listing is not read as five missing blocks"


async def test_a_record_with_no_recorded_set_changed_in_part_leaves_it_unrecorded(db) -> None:
    blob = await _indexed(db)
    del blob.saved["pointedBlocks"]
    await index(make_store(db), blob, TEXT.replace("Paragraph 2 about revenue", "Paragraph 2 about something else"))
    assert "pointedBlocks" not in blob.saved, "nothing says which unchanged blocks have a point: not guessed from the store"

