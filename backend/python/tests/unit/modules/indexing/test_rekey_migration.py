"""A record stored under random block ids keeps its vectors when it moves to content-derived ids.

Embedding is often a paid call, so the first re-parse of such a record moves each unchanged block's
stored point to its new id (same vector, same payload apart from the block id) and embeds only what
is new or changed. A store that cannot return vectors falls back to embedding, and says so in a counter.
"""
from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
import pytest_asyncio

from app.modules.indexing import rekey_metrics
from app.modules.reconciliation.service import ReconciliationService
from app.services.vector_db.errors import VectorsNotFetchable
from app.services.vector_db.models import CollectionConfig
from tests.support.in_memory_vector_db import InMemoryVectorDBService
from tests.unit.modules.indexing.test_replica_race import (
    COLL,
    RID,
    VRID,
    _Blob,
    _index,
    _parse,
    _point_ids,
    _record,
    _store,
    store_documents,
)

N = 100


def _doc(changed: tuple[int, ...] = ()) -> str:
    return "\n\n".join(
        f"Paragraph {i} " + ("was rewritten entirely. " if i in changed else "is untouched. ") + f"Topic number {i}."
        for i in range(N)
    )


class _Recorder:
    def __init__(self) -> None:
        self.calls: list[tuple[str, ...]] = []

    def inc(self, *labels: str, value: float = 1.0) -> None:
        self.calls.append((*labels, value))


@pytest_asyncio.fixture
async def db() -> InMemoryVectorDBService:
    service = InMemoryVectorDBService()
    await service.create_collection(COLL, CollectionConfig(embedding_size=2))
    return service


def _embedded_texts(store) -> list[str]:
    return [t for call in store._embed_documents_with_retry.await_args_list for t in call.args[0]]


def _vector_of(text: str) -> list[float]:
    return [float(len(text)), float(sum(map(ord, text)) % 97)]


async def _legacy_record(store, db) -> _Blob:
    """The record as an older version stored it: random block ids, metadata of no scheme."""
    store._embed_documents_with_retry = AsyncMock(side_effect=lambda texts, _rid: [_vector_of(t) for t in texts])
    legacy = _parse(_doc())
    await store._embed_and_upsert_documents(store_documents(legacy), RID, COLL)
    store._embed_documents_with_retry.reset_mock()
    blob = _Blob()
    blob.saved = ReconciliationService().build_metadata(legacy).to_dict()
    assert len(db.collections[COLL]) == N
    return blob


async def test_unchanged_blocks_are_moved_with_their_vectors_and_nothing_is_embedded(db) -> None:
    store = _store(db)
    blob = await _legacy_record(store, db)
    legacy_vectors = {p.payload["page_content"]: p.dense_vector for p in db.collections[COLL].values()}
    await _index(store, blob, _doc())
    assert _embedded_texts(store) == []
    fresh = InMemoryVectorDBService()
    await fresh.create_collection(COLL, CollectionConfig(embedding_size=2))
    fresh_store = _store(fresh)
    await _index(fresh_store, _Blob(), _doc(), event="newRecord")
    assert _point_ids(db) == _point_ids(fresh), "the point ids equal the content-derived ids"
    assert {p.payload["page_content"]: p.dense_vector for p in db.collections[COLL].values()} == legacy_vectors
    assert blob.saved["idScheme"] == "content-v1"
    block_ids = {p.payload["metadata"]["blockId"] for p in db.collections[COLL].values()}
    assert block_ids == {p.payload["metadata"]["blockId"] for p in fresh.collections[COLL].values()}


async def test_only_the_changed_blocks_are_embedded(db) -> None:
    store = _store(db)
    blob = await _legacy_record(store, db)
    await _index(store, blob, _doc(changed=(3, 40, 77)))
    texts = _embedded_texts(store)
    assert len(texts) == 3 and all("was rewritten" in t for t in texts)
    assert len(db.collections[COLL]) == N
    assert not any("is untouched" in p.payload["page_content"] and "Paragraph 3 " in p.payload["page_content"]
                   for p in db.collections[COLL].values())


async def test_a_store_that_cannot_return_vectors_embeds_again_and_counts_it(db, monkeypatch) -> None:
    recorder = _Recorder()
    monkeypatch.setattr(rekey_metrics, "REKEY_FALLBACK", recorder)
    store = _store(db)
    blob = await _legacy_record(store, db)
    db.scroll_with_vectors = AsyncMock(side_effect=VectorsNotFetchable("no vectors"))
    await _index(store, blob, _doc())
    assert len(_embedded_texts(store)) == N
    assert sum(c[-1] for c in recorder.calls) == N and {c[0] for c in recorder.calls} == {"unsupported"}
    assert len(db.collections[COLL]) == N, "the old points are gone, no strays"
    assert _point_ids(db) == await _content_points()


async def test_a_partial_read_embeds_the_blocks_and_keeps_the_old_points_until_they_are_stored(db, monkeypatch) -> None:
    recorder = _Recorder()
    monkeypatch.setattr(rekey_metrics, "REKEY_FALLBACK", recorder)
    store = _store(db)
    blob = await _legacy_record(store, db)
    real = db.scroll_with_vectors

    async def one_point_missing(*args, **kwargs):
        page = await real(*args, **kwargs)
        page.points = page.points[1:]
        return page

    db.scroll_with_vectors = one_point_missing
    first_embed_saw: list[int] = []
    embed = store._embed_documents_with_retry.side_effect

    async def spying_embed(texts, rid):
        first_embed_saw.append(len(db.collections[COLL]))
        return embed(texts, rid)

    store._embed_documents_with_retry = AsyncMock(side_effect=spying_embed)
    await _index(store, blob, _doc())
    assert {c[0] for c in recorder.calls} == {"error"}
    assert len(_embedded_texts(store)) == N
    assert _point_ids(db) == await _content_points()
    assert first_embed_saw and first_embed_saw[0] == N, "every old point was still stored when the first embed ran"


async def test_moved_blocks_are_counted(db, monkeypatch) -> None:
    moved = _Recorder()
    monkeypatch.setattr(rekey_metrics, "REKEYED", moved)
    store = _store(db)
    blob = await _legacy_record(store, db)
    await _index(store, blob, _doc(changed=(5,)))
    assert sum(c[-1] for c in moved.calls) == N - 1


async def test_the_move_happens_once(db) -> None:
    store = _store(db)
    blob = await _legacy_record(store, db)
    await _index(store, blob, _doc())
    after = _point_ids(db)
    await _index(store, blob, _doc())
    assert _point_ids(db) == after and _embedded_texts(store) == []


async def test_a_store_without_the_method_refuses_with_the_typed_error() -> None:
    from app.services.vector_db.interface.vector_db import IVectorDBService

    class Bare(InMemoryVectorDBService):
        scroll_with_vectors = IVectorDBService.scroll_with_vectors

    with pytest.raises(VectorsNotFetchable):
        await Bare().scroll_with_vectors(COLL, None, 10)  # type: ignore[arg-type]


async def _content_points() -> set[str]:
    fresh = InMemoryVectorDBService()
    await fresh.create_collection(COLL, CollectionConfig(embedding_size=2))
    await _index(_store(fresh), _Blob(), _doc(), event="newRecord")
    return _point_ids(fresh)


_ = (VRID, _record)
