"""Two runs that index the same record leave the same points (TC-5), and the sweep cleans the rest.

Each run parses the bytes itself, so each parser-minted block id differs. With content-derived ids
(C-1) both runs name the blocks, and so the points, alike: the point-id sets are equal. Without
them (the negative controls) the runs write twice the points, and the sweep, which keeps only the
points the current revision references, is what brings the record back to the wanted set.

The vector store is the in-memory service (the same contract as the real providers); embedding is
a deterministic stub. Everything else is the real code: the parser, the id step, the reconciliation
diff, `VectorStore.apply`, its point ids and the sweep.
"""
from __future__ import annotations

from types import SimpleNamespace
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock

import pytest_asyncio

from app.config.constants.arangodb import EventTypes
from app.models.blocks import Block, BlocksContainer, BlockType, DataFormat
from app.modules.reconciliation.block_identity import BlockIdentity
from app.modules.transformers.pipeline import IndexingPipeline
from app.modules.transformers.transformer import TransformContext
from app.modules.transformers.vectorstore import VectorStore
from app.services.vector_db.models import CollectionConfig, VectorDBCapabilities
from tests.support.in_memory_vector_db import InMemoryVectorDBService
from tests.support.vector_db import make_collection_registry

if TYPE_CHECKING:
    import pytest

ORG, VRID, RID, COLL = "a" * 24, "vr-race", "rec-race", "records"
DOC_V1 = "\n\n".join(f"Paragraph number {i} says something quite different about topic {i}." for i in range(6))
DOC_V2 = DOC_V1.replace("Paragraph number 2 says something quite different about topic 2.",
                        "A rewritten second paragraph that now talks about something else entirely.")


@pytest_asyncio.fixture
async def db() -> InMemoryVectorDBService:
    service = InMemoryVectorDBService()
    await service.create_collection(COLL, CollectionConfig(embedding_size=2))
    return service


def _store(db: InMemoryVectorDBService) -> VectorStore:
    db.get_capabilities = MagicMock(return_value=VectorDBCapabilities())
    store = VectorStore(
        logger=MagicMock(), config_service=AsyncMock(), graph_provider=AsyncMock(),
        collection_registry=make_collection_registry(COLL), vector_db_service=db,
    )
    store.graph_provider.get_document = AsyncMock(return_value={"_key": RID})
    store.get_embedding_model_instance = AsyncMock(return_value=False)
    store._ensure_collection = AsyncMock(return_value=COLL)
    store._cleanup_orphaned_embeddings_if_needed = AsyncMock()
    store._resync_membership_after_write = AsyncMock()
    store._embed_documents_with_retry = AsyncMock(side_effect=lambda texts, _rid: [[1.0, 0.0] for _ in texts])
    store._compute_sparse_embeddings = AsyncMock(side_effect=lambda texts: [None for _ in texts])
    return store


def _parse(text: str) -> BlocksContainer:
    # One block per paragraph, minted with the random ids every parser mints.
    return BlocksContainer(blocks=[
        Block(index=i, type=BlockType.TEXT, format=DataFormat.TXT, data=p)
        for i, p in enumerate(text.split("\n\n"))
    ])


def _record(container) -> MagicMock:
    record = MagicMock()
    record.id, record.org_id, record.virtual_record_id = RID, ORG, VRID
    record.block_containers = container
    record.semantic_metadata = None
    return record


def _context(record, event: str, prev: str | None = VRID) -> MagicMock:
    ctx = MagicMock(spec=TransformContext)
    ctx.record, ctx.event_type, ctx.prev_virtual_record_id = record, event, prev
    ctx.reconciliation_context, ctx.settings = None, {}
    return ctx


class _Blob:
    """Holds the reconciliation metadata the way the blob store does, as a dict."""

    def __init__(self) -> None:
        self.saved: dict | None = None
        self.get_reconciliation_metadata = AsyncMock(side_effect=lambda *_: self.saved)


async def _index(store: VectorStore, blob: _Blob, text: str, *, event: str = EventTypes.UPDATE_RECORD.value) -> None:
    """One run: parse the bytes, assign ids, diff against the stored metadata, index, store the metadata."""
    container = _parse(text)
    record = _record(container)
    ctx = _context(record, event)
    sink = SimpleNamespace(blob_storage=blob, vector_store=store)
    ctx.reconciliation_context = await IndexingPipeline.build_reconciliation_context(ctx, MagicMock(), sink)
    await store.apply(ctx)
    blob.saved = ctx.reconciliation_context.new_metadata


def _point_ids(db: InMemoryVectorDBService) -> set[str]:
    return set(db.collections[COLL])


def _blocks_of(db: InMemoryVectorDBService) -> set[str]:
    return {p.payload["metadata"]["blockId"] for p in db.collections[COLL].values()}


async def _fresh_points(text: str) -> set[str]:
    """The point ids a clean first index of *text* writes."""
    fresh = InMemoryVectorDBService()
    await fresh.create_collection(COLL, CollectionConfig(embedding_size=2))
    await _index(_store(fresh), _Blob(), text, event=EventTypes.NEW_RECORD.value)
    return _point_ids(fresh)


async def test_single_flight_ids_and_sweep(db: InMemoryVectorDBService) -> None:
    """Two racing runs: with C-1 ids the point-id sets of the runs are equal."""
    wanted = await _fresh_points(DOC_V1)
    assert len(wanted) == 6
    for run in (1, 2):
        racer = InMemoryVectorDBService()
        await racer.create_collection(COLL, CollectionConfig(embedding_size=2))
        await _index(_store(racer), _Blob(), DOC_V1, event=EventTypes.NEW_RECORD.value)
        assert _point_ids(racer) == wanted, f"run {run}"
    # Both runs on one collection, no delete between them (the interleaving of a real race).
    store = _store(db)
    for _ in range(2):
        container = _parse(DOC_V1)
        record = _record(container)
        ctx = _context(record, EventTypes.NEW_RECORD.value)
        ctx.reconciliation_context = await IndexingPipeline.build_reconciliation_context(
            ctx, MagicMock(), SimpleNamespace(blob_storage=_Blob(), vector_store=store))
        docs = store_documents(container)
        await store._embed_and_upsert_documents(docs, RID, COLL)
    assert _point_ids(db) == wanted


def store_documents(container: BlocksContainer) -> list:
    from app.modules.transformers.vectorstore import _process_text_blocks

    return _process_text_blocks(container.blocks, VRID, ORG, None)


async def test_without_c1_ids_the_runs_double_the_points_and_the_sweep_restores_the_wanted_set(
    db: InMemoryVectorDBService, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Negative control: random ids (no `BlockIdentity`) leave strays; the sweep keeps the last run's."""
    wanted = await _fresh_points(DOC_V1)
    monkeypatch.setattr(BlockIdentity, "assign", staticmethod(lambda *_a, **_k: False))
    store = _store(db)
    last = None
    for _ in range(2):
        last = _parse(DOC_V1)
        await store._embed_and_upsert_documents(store_documents(last), RID, COLL)
    assert len(_point_ids(db)) == 12 and not _point_ids(db) & wanted
    await store._sweep_stray_points(_record(last), last)
    assert len(_point_ids(db)) == 6
    assert _blocks_of(db) == {b.id for b in last.blocks}


async def test_reindex_of_unchanged_content_upserts_in_place(db: InMemoryVectorDBService) -> None:
    store, blob = _store(db), _Blob()
    await _index(store, blob, DOC_V1, event=EventTypes.NEW_RECORD.value)
    first = _point_ids(db)
    for _ in range(2):
        await _index(store, blob, DOC_V1)
        assert _point_ids(db) == first


async def test_changed_content_replaces_its_points_and_leaves_no_strays(db: InMemoryVectorDBService) -> None:
    store, blob = _store(db), _Blob()
    await _index(store, blob, DOC_V1, event=EventTypes.NEW_RECORD.value)
    before = _point_ids(db)
    await _index(store, blob, DOC_V2)
    after = _point_ids(db)
    assert after == await _fresh_points(DOC_V2)
    assert len(before - after) == 1 and len(after - before) == 1, "one paragraph changed, one point replaced"
    # And a stale revision's point, written late by a slower run, goes on the next pass.
    stale = next(iter(db.collections[COLL].values()))
    late = stale.__class__(id="f" * 8 + "-0000-4000-8000-" + "0" * 12, dense_vector=[1.0, 0.0],
                           payload={**stale.payload, "metadata": {**stale.payload["metadata"], "blockId": "old-rev"}})
    await db.upsert_points(COLL, [late])
    await _index(store, blob, DOC_V2)
    assert _point_ids(db) == after


async def test_points_written_with_old_random_ids_are_cleaned_on_the_next_run(db: InMemoryVectorDBService) -> None:
    """A record indexed before ids followed content: random block ids, metadata of no scheme."""
    store, blob = _store(db), _Blob()
    legacy = _parse(DOC_V1)  # random ids, as stored before
    await store._embed_and_upsert_documents(store_documents(legacy), RID, COLL)
    legacy_blocks = {b.id for b in legacy.blocks}
    assert _blocks_of(db) == legacy_blocks
    blob.saved = _legacy_metadata(legacy)
    await _index(store, blob, DOC_V1)  # a re-parse of the very same content
    assert _point_ids(db) == await _fresh_points(DOC_V1)
    assert not _blocks_of(db) & legacy_blocks
    assert blob.saved["idScheme"] == "content-v1"
    await _index(store, blob, DOC_V1)  # and it happens once
    assert _point_ids(db) == await _fresh_points(DOC_V1)


async def test_legacy_metadata_that_hides_the_old_points_is_swept_too(db: InMemoryVectorDBService) -> None:
    """Metadata already of the new scheme, but a racing run left random-id points beside the right ones."""
    store, blob = _store(db), _Blob()
    await _index(store, blob, DOC_V1, event=EventTypes.NEW_RECORD.value)
    right = _point_ids(db)
    ghost = _parse(DOC_V1)  # a racing run of the old code
    await store._embed_and_upsert_documents(store_documents(ghost), RID, COLL)
    assert len(_point_ids(db)) == 12
    await _index(store, blob, DOC_V1)  # nothing changed: the diff is empty, the sweep still runs
    assert _point_ids(db) == right


def _legacy_metadata(container) -> dict:
    from app.modules.reconciliation.service import ReconciliationService

    return ReconciliationService().build_metadata(container).to_dict()
