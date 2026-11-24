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

import asyncio
from types import SimpleNamespace
from typing import TYPE_CHECKING
from unittest.mock import MagicMock

import pytest
import pytest_asyncio

from app.config.constants.arangodb import EventTypes
from app.modules.reconciliation.block_identity import BlockIdentity
from app.modules.transformers.pipeline import IndexingPipeline
from app.services.vector_db.models import CollectionConfig
from tests.support.in_memory_vector_db import InMemoryVectorDBService
from tests.support.index_world import (
    COLL,
    RID,
    Blob,
    context_of,
    documents,
    index,
    make_store,
    parse,
    record_of,
)

if TYPE_CHECKING:
    import pytest

    from app.modules.transformers.vectorstore import VectorStore

DOC_V1 = "\n\n".join(f"Paragraph number {i} says something quite different about topic {i}." for i in range(6))
DOC_V2 = DOC_V1.replace("Paragraph number 2 says something quite different about topic 2.",
                        "A rewritten second paragraph that now talks about something else entirely.")


@pytest_asyncio.fixture
async def db() -> InMemoryVectorDBService:
    service = InMemoryVectorDBService()
    await service.create_collection(COLL, CollectionConfig(embedding_size=2))
    return service


def _store(db: InMemoryVectorDBService) -> VectorStore:
    return make_store(db)


def _point_ids(db: InMemoryVectorDBService) -> set[str]:
    return set(db.collections[COLL])


def _blocks_of(db: InMemoryVectorDBService) -> set[str]:
    return {p.payload["metadata"]["blockId"] for p in db.collections[COLL].values()}


async def _fresh_points(text: str) -> set[str]:
    """The point ids a clean first index of *text* writes."""
    fresh = InMemoryVectorDBService()
    await fresh.create_collection(COLL, CollectionConfig(embedding_size=2))
    await index(_store(fresh), Blob(), text, event=EventTypes.NEW_RECORD.value)
    return _point_ids(fresh)


async def test_single_flight_ids_and_sweep(db: InMemoryVectorDBService) -> None:
    """Two racing runs: with C-1 ids the point-id sets of the runs are equal."""
    wanted = await _fresh_points(DOC_V1)
    assert len(wanted) == 6
    for run in (1, 2):
        racer = InMemoryVectorDBService()
        await racer.create_collection(COLL, CollectionConfig(embedding_size=2))
        await index(_store(racer), Blob(), DOC_V1, event=EventTypes.NEW_RECORD.value)
        assert _point_ids(racer) == wanted, f"run {run}"
    # Both runs on one collection, no delete between them (the interleaving of a real race).
    store = _store(db)
    for _ in range(2):
        container = parse(DOC_V1)
        ctx = context_of(record_of(container), EventTypes.NEW_RECORD.value)
        await IndexingPipeline.build_reconciliation_context(
            ctx, MagicMock(), SimpleNamespace(blob_storage=Blob(), vector_store=store))  # assigns the content ids
        await store._embed_and_upsert_documents(documents(container), RID, COLL)
    assert _point_ids(db) == wanted


async def test_without_c1_ids_the_runs_double_the_points_and_the_sweep_restores_the_wanted_set(
    db: InMemoryVectorDBService, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Negative control: random ids (no `BlockIdentity`) leave strays; the sweep keeps the last run's."""
    wanted = await _fresh_points(DOC_V1)
    monkeypatch.setattr(BlockIdentity, "assign", staticmethod(lambda *_a, **_k: False))
    store = _store(db)
    last = None
    for _ in range(2):
        last = parse(DOC_V1)
        await store._embed_and_upsert_documents(documents(last), RID, COLL)
    assert len(_point_ids(db)) == 12 and not _point_ids(db) & wanted
    await store._sweep_stray_points(record_of(last), last)
    assert len(_point_ids(db)) == 6
    assert _blocks_of(db) == {b.id for b in last.blocks}


async def test_reindex_of_unchanged_content_upserts_in_place(db: InMemoryVectorDBService) -> None:
    store, blob = _store(db), Blob()
    await index(store, blob, DOC_V1, event=EventTypes.NEW_RECORD.value)
    first = _point_ids(db)
    for _ in range(2):
        await index(store, blob, DOC_V1)
        assert _point_ids(db) == first


async def test_changed_content_replaces_its_points_and_leaves_no_strays(db: InMemoryVectorDBService) -> None:
    store, blob = _store(db), Blob()
    await index(store, blob, DOC_V1, event=EventTypes.NEW_RECORD.value)
    before = _point_ids(db)
    await index(store, blob, DOC_V2)
    after = _point_ids(db)
    assert after == await _fresh_points(DOC_V2)
    assert len(before - after) == 1 and len(after - before) == 1, "one paragraph changed, one point replaced"
    # And a stale revision's point, written late by a slower run, goes on the next pass.
    stale = next(iter(db.collections[COLL].values()))
    late = stale.__class__(id="f" * 8 + "-0000-4000-8000-" + "0" * 12, dense_vector=[1.0, 0.0],
                           payload={**stale.payload, "metadata": {**stale.payload["metadata"], "blockId": "old-rev"}})
    await db.upsert_points(COLL, [late])
    await index(store, blob, DOC_V2)
    assert _point_ids(db) == after


async def test_points_written_with_old_random_ids_are_cleaned_on_the_next_run(db: InMemoryVectorDBService) -> None:
    """A record indexed before ids followed content: random block ids, metadata of no scheme."""
    store, blob = _store(db), Blob()
    legacy = parse(DOC_V1)  # random ids, as stored before
    await store._embed_and_upsert_documents(documents(legacy), RID, COLL)
    legacy_blocks = {b.id for b in legacy.blocks}
    assert _blocks_of(db) == legacy_blocks
    blob.saved = _legacy_metadata(legacy)
    await index(store, blob, DOC_V1)  # a re-parse of the very same content
    assert _point_ids(db) == await _fresh_points(DOC_V1)
    assert not _blocks_of(db) & legacy_blocks
    assert blob.saved["idScheme"] == "content-v1"
    await index(store, blob, DOC_V1)  # and it happens once
    assert _point_ids(db) == await _fresh_points(DOC_V1)


async def test_legacy_metadata_that_hides_the_old_points_is_swept_too(db: InMemoryVectorDBService) -> None:
    """Metadata already of the new scheme, but a racing run left random-id points beside the right ones."""
    store, blob = _store(db), Blob()
    await index(store, blob, DOC_V1, event=EventTypes.NEW_RECORD.value)
    right = _point_ids(db)
    ghost = parse(DOC_V1)  # a racing run of the old code
    await store._embed_and_upsert_documents(documents(ghost), RID, COLL)
    assert len(_point_ids(db)) == 12
    await index(store, blob, DOC_V1)  # nothing changed: the diff is empty, the sweep still runs
    assert _point_ids(db) == right


def _legacy_metadata(container) -> dict:
    from app.modules.reconciliation.service import ReconciliationService

    return ReconciliationService().build_metadata(container).to_dict()


@pytest.mark.parametrize("policy", ["A", "C"])
async def test_two_concurrent_runs_of_one_record_give_equal_point_sets(
    db: InMemoryVectorDBService, policy: str, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The race itself: two replicas index the same bytes at the same time, each with its own parse (its own random ids)."""
    monkeypatch.setenv("EMBED_POINT_POLICY", policy)
    wanted = await _fresh_points(DOC_V1)
    await asyncio.gather(
        index(_store(db), Blob(), DOC_V1, event=EventTypes.NEW_RECORD.value),
        index(_store(db), Blob(), DOC_V1, event=EventTypes.NEW_RECORD.value),
    )
    assert _point_ids(db) == wanted


async def test_two_concurrent_runs_without_content_ids_double_the_points(
    db: InMemoryVectorDBService, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Negative control for the test above: with random ids the runs share nothing."""
    wanted = await _fresh_points(DOC_V1)
    monkeypatch.setattr(BlockIdentity, "assign", staticmethod(lambda *_a, **_k: False))
    await asyncio.gather(
        index(_store(db), Blob(), DOC_V1, event=EventTypes.NEW_RECORD.value),
        index(_store(db), Blob(), DOC_V1, event=EventTypes.NEW_RECORD.value),
    )
    assert not _point_ids(db) & wanted and len(_point_ids(db)) >= len(wanted)
