"""QA-S7e-28: ``pointedBlocks`` records what the writers wrote, never what the store lists.

The set saved with a record is (the previous set for the blocks this run left alone) plus (the blocks this run wrote a point for).
A listing of the store is used only to find a recorded block whose point is gone, which is written again and stays recorded.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
import pytest_asyncio

from app.models.blocks import BlocksContainer
from app.services.vector_db.models import CollectionConfig
from tests.support.in_memory_vector_db import InMemoryVectorDBService
from tests.support.index_world import COLL, Blob, index, make_store

if TYPE_CHECKING:
    from app.modules.transformers.vectorstore import VectorStore

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



# ---- the vector-only reindex after an embedding-model change (no blob write, no diff) ----

def _with_image() -> BlocksContainer:
    from app.models.blocks import Block, BlockType, DataFormat

    blocks = [Block(index=i, type=BlockType.TEXT, format=DataFormat.TXT, data=f"Paragraph {i} about revenue and costs in quarter {i}.")
              for i in range(5)]
    blocks.append(Block(index=5, type=BlockType.IMAGE, format=DataFormat.BASE64, data={"uri": "data:image/png;base64,iVBORw0KGgo="}))
    return BlocksContainer(blocks=blocks)


def _model_store(db: InMemoryVectorDBService, multimodal: bool) -> VectorStore:
    """A store whose embedding model is multimodal (the provider returns a vector for an image) or text only."""
    from unittest.mock import AsyncMock

    from app.services.embeddings.multimodal.interface import ImageEmbeddingResult

    store = make_store(db)
    store.get_embedding_model_instance = AsyncMock(return_value=multimodal)
    store.image_calls = 0

    async def images(chunks: list, b64s: list, record_id: str = "") -> list:
        store.image_calls += len(chunks)
        return store._build_image_points(chunks, [ImageEmbeddingResult(index=i, embedding=[1.0, 2.0], error=None) for i in range(len(chunks))])

    store._process_image_embeddings = images
    return store


async def _vector_only_reindex(db: InMemoryVectorDBService, store: VectorStore, blob: Blob, container: BlocksContainer) -> None:
    """What the record handler does after a model change: every point of the record is deleted, the stored blocks are applied with
    ``skip_blob`` and no diff, and the sink saves the reconciliation metadata the apply recorded."""
    from types import SimpleNamespace
    from unittest.mock import MagicMock

    from app.modules.transformers.sink_orchestrator import SinkOrchestrator
    from tests.support.index_world import context_of, record_of

    db.collections[COLL].clear()
    ctx = context_of(record_of(container), "reindexRecord")
    await SinkOrchestrator._rewrite_recorded_points(SimpleNamespace(blob_storage=blob, logger=MagicMock()), ctx)
    await store.apply(ctx)
    blob.saved = ctx.reconciliation_context.new_metadata


async def _first_index(db: InMemoryVectorDBService, multimodal: bool) -> tuple[Blob, BlocksContainer]:
    from tests.support.index_world import run

    blob, container = Blob(), _with_image()
    store = _model_store(db, multimodal)
    await run(store, blob, container, "newRecord")
    return blob, container


def _image_block(container: BlocksContainer) -> str:
    return container.blocks[5].id


async def test_a_change_to_a_multimodal_model_records_the_image_points_and_heals_a_later_loss(db) -> None:
    from tests.support.index_world import run

    blob, container = await _first_index(db, multimodal=False)
    assert _image_block(container) not in blob.saved["pointedBlocks"], "a text model gives an undescribed image no point"
    await _vector_only_reindex(db, _model_store(db, True), blob, container)
    assert _image_block(container) in blob.saved["pointedBlocks"] and len(blob.saved["pointedBlocks"]) == 6
    image_point = next(pid for pid, p in db.collections[COLL].items() if p.payload["metadata"]["blockId"] == _image_block(container))
    del db.collections[COLL][image_point]
    store = _model_store(db, True)
    await run(store, blob, container)
    assert store.image_calls == 1 and _image_block(container) in _block_ids(db), "the lost image point was written again"
    assert len(blob.saved["pointedBlocks"]) == 6


async def test_a_change_to_a_text_model_drops_the_images_and_stops_embedding_them(db) -> None:
    from tests.support.index_world import run

    blob, container = await _first_index(db, multimodal=True)
    assert _image_block(container) in blob.saved["pointedBlocks"]
    await _vector_only_reindex(db, _model_store(db, False), blob, container)
    assert _image_block(container) not in blob.saved["pointedBlocks"] and len(blob.saved["pointedBlocks"]) == 5
    store = _model_store(db, False)
    await run(store, blob, container)
    assert _embedded(store) == [] and store.image_calls == 0, "an ordinary run does not heal a block that has no point"


async def test_a_legacy_record_changed_in_part_records_the_points_it_moved(db) -> None:
    """QA-S7e-33 (Z5): the unchanged blocks of a record from before content ids are moved, not embedded. The writer's output includes
    the moved points: left out, they would be absent from the recorded set, and their later loss would never be healed."""
    from tests.support.index_world import legacy_record, run

    _, blob = await legacy_record(db, _text_only())
    changed = _text_only(change=2)
    await run(make_store(db), blob, changed)
    stored = _block_ids(db)
    assert "pointedBlocks" in blob.saved, "a record changed in part after a move is recorded"
    assert stored == set(blob.saved["pointedBlocks"]) and len(stored) == 5


def _text_only(change: int | None = None) -> BlocksContainer:
    from app.models.blocks import Block, BlockType, DataFormat

    return BlocksContainer(blocks=[
        Block(index=i, type=BlockType.TEXT, format=DataFormat.TXT,
              data=f"Paragraph {i} about revenue and costs in quarter {i}." + (" Changed in part." if i == change else ""))
        for i in range(5)])
