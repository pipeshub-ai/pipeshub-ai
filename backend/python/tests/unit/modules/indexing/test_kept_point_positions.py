"""A reconciled update keeps an unchanged block's point, and the point is refreshed to say where the block now sits.

A kept point is not embedded again. Blocks added or removed before it move its block, and a stored position from an earlier
revision makes a reader cite another block. After a reconciled run every point of a kept block carries the block's current
``blockIndex`` (``blockGroupIndex`` for a group), and, when the run knows it, the revision it was written for; the points written
by the update carry that revision as well. The refresh rewrites points with their stored vectors, so nothing is embedded again.
"""
from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio

from app.config.constants.arangodb import EventTypes
from app.models.blocks import (
    Block,
    BlockGroup,
    BlockGroupChildren,
    BlocksContainer,
    BlockType,
    DataFormat,
    GroupType,
)
from app.modules.indexing.embed_resume import EmbedAttempt, begin_attempt, end_attempt
from app.modules.pipeline.leases import LeaseLost
from app.services.vector_db.errors import VectorsNotFetchable
from app.services.vector_db.models import CollectionConfig, ScrollResult
from tests.support.in_memory_vector_db import InMemoryVectorDBService
from tests.support.index_world import (
    COLL,
    VRID,
    Blob,
    embedded_texts,
    legacy_record,
    make_store,
    run,
)

if TYPE_CHECKING:
    from collections.abc import Iterator

NEW = EventTypes.NEW_RECORD.value


def _text(paras: list[str]) -> BlocksContainer:
    return BlocksContainer(blocks=[Block(index=i, type=BlockType.TEXT, format=DataFormat.TXT, data=p) for i, p in enumerate(paras)])


def _paras(*keys: str) -> list[str]:
    return [f"Paragraph {k} says something only paragraph {k} says." for k in keys]


def _text_then_table(paras: list[str], rows: list[str]) -> BlocksContainer:
    blocks = [Block(index=i, type=BlockType.TEXT, format=DataFormat.TXT, data=p) for i, p in enumerate(paras)]
    first = len(blocks)
    blocks += [Block(index=first + i, type=BlockType.TABLE_ROW, format=DataFormat.JSON, parent_index=0,
                     data={"row_natural_language_text": t, "row_number": i}) for i, t in enumerate(rows)]
    group = BlockGroup(index=0, type=GroupType.TABLE, data={"table_summary": "Quarterly cost per site"},
                       children=BlockGroupChildren.from_indices(block_indices=list(range(first, len(blocks)))))
    return BlocksContainer(blocks=blocks, block_groups=[group])


def _tables(*tables: list[str]) -> BlocksContainer:
    blocks: list[Block] = []
    groups: list[BlockGroup] = []
    for g, rows in enumerate(tables):
        members = [Block(index=len(blocks) + i, type=BlockType.TABLE_ROW, format=DataFormat.JSON, parent_index=g,
                         data={"row_natural_language_text": t, "row_number": i}) for i, t in enumerate(rows)]
        blocks += members
        groups.append(BlockGroup(index=g, type=GroupType.TABLE, data={"table_summary": f"Table of {rows[0]}"},
                                 children=BlockGroupChildren.from_indices(block_indices=[m.index for m in members])))
    return BlocksContainer(blocks=blocks, block_groups=groups)


@pytest_asyncio.fixture
async def db() -> InMemoryVectorDBService:
    service = InMemoryVectorDBService()
    await service.create_collection(COLL, CollectionConfig(embedding_size=2))
    return service


@pytest.fixture(autouse=True)
def _no_attempt() -> Iterator[None]:
    end_attempt()
    yield
    end_attempt()


def _attempt(rev: str) -> None:
    begin_attempt(EmbedAttempt(delivery_id=f"delivery-{rev}", rev=rev, vrid=VRID))


def _metas(db: InMemoryVectorDBService) -> list[dict]:
    return [p.payload["metadata"] for p in db.collections[COLL].values() if not p.payload["metadata"].get("isRecordSummary")]


def _assert_positions_follow(db: InMemoryVectorDBService, container: BlocksContainer) -> None:
    blocks = {b.id: b.index for b in container.blocks}
    groups = {g.id: g.index for g in container.block_groups}
    metas = _metas(db)
    assert metas
    for meta in metas:
        if meta.get("isBlockGroup"):
            assert meta["blockGroupIndex"] == groups[meta["blockId"]]
        else:
            assert meta["blockIndex"] == blocks[meta["blockId"]], meta


async def test_a_kept_block_that_moves_down_takes_its_new_index(db) -> None:
    store, blob = make_store(db), Blob()
    await run(store, blob, _text(_paras("a", "b", "c", "d")), event=NEW)
    store._embed_documents_with_retry.reset_mock()
    v2 = _text(_paras("new", "a", "b", "c", "d"))
    await run(store, blob, v2)
    assert embedded_texts(store) == _paras("new"), "only the inserted paragraph is embedded"
    _assert_positions_follow(db, v2)


async def test_a_kept_block_that_moves_up_after_a_removal_takes_its_new_index(db) -> None:
    store, blob = make_store(db), Blob()
    await run(store, blob, _text(_paras("a", "b", "c", "d")), event=NEW)
    store._embed_documents_with_retry.reset_mock()
    v2 = _text(_paras("a", "c", "d"))           # only a removal: nothing to embed at all
    await run(store, blob, v2)
    assert embedded_texts(store) == []
    _assert_positions_follow(db, v2)


async def test_a_kept_group_takes_its_new_group_index_and_its_rows_their_new_index(db) -> None:
    rows_a, rows_b = [f"alpha row {i} of the first table" for i in range(3)], [f"beta row {i} of the other table" for i in range(3)]
    store, blob = make_store(db), Blob()
    await run(store, blob, _tables(rows_a, rows_b), event=NEW)
    v2 = _tables(rows_b)                        # the first table is gone: table B is group 0, its rows start at 0
    await run(store, blob, v2)
    _assert_positions_follow(db, v2)
    assert {m["blockGroupIndex"] for m in _metas(db) if m.get("isBlockGroup")} == {0}
    assert all("blockIndex" not in m for m in _metas(db) if m.get("isBlockGroup")), "a group never carries blockIndex"


async def test_rows_of_a_kept_table_follow_a_paragraph_inserted_above_it(db) -> None:
    rows = [f"site {s} cost {i}" for i, s in enumerate(("Alpha", "Bravo", "Charlie"))]
    store, blob = make_store(db), Blob()
    await run(store, blob, _text_then_table(_paras("intro"), rows), event=NEW)
    v2 = _text_then_table(_paras("intro", "inserted"), rows)
    await run(store, blob, v2)
    _assert_positions_follow(db, v2)


async def test_the_vectors_of_refreshed_points_are_the_stored_ones(db) -> None:
    store, blob = make_store(db), Blob()
    await run(store, blob, _text(_paras("a", "b", "c")), event=NEW)
    before = {p.payload["page_content"]: (pid, p.dense_vector) for pid, p in db.collections[COLL].items()}
    await run(store, blob, _text(_paras("x", "a", "b", "c")))
    after = {p.payload["page_content"]: (pid, p.dense_vector) for pid, p in db.collections[COLL].items()}
    for text in _paras("a", "b", "c"):
        assert after[text] == before[text], "same point id, same vector"


async def test_every_point_carries_the_revision_of_the_update(db) -> None:
    store, blob = make_store(db), Blob()
    _attempt("rev-1")
    await run(store, blob, _text(_paras("a", "b", "c")), event=NEW)
    _attempt("rev-2")
    await run(store, blob, _text(_paras("a", "changed", "c")))
    assert {m.get("contentRev") for m in _metas(db)} == {"rev-2"}, "kept and newly written points alike"


async def test_points_written_by_an_update_carry_the_revision_and_setup(db) -> None:
    store, blob = make_store(db), Blob()
    await run(store, blob, _text(_paras("a")), event=NEW)
    _attempt("rev-2")
    await run(store, blob, _text(_paras("a", "b")))
    new = [p.payload["metadata"] for p in db.collections[COLL].values() if "Paragraph b " in p.payload["page_content"]]
    assert new and all(m["contentRev"] == "rev-2" and m["embedKey"] == store._embed_key() for m in new)


async def test_a_new_revision_of_unchanged_content_rewrites_the_revision_and_embeds_nothing(db) -> None:
    store, blob = make_store(db), Blob()
    _attempt("rev-1")
    await run(store, blob, _text(_paras("a", "b")), event=NEW)
    store._embed_documents_with_retry.reset_mock()
    _attempt("rev-2")                           # the source sent other bytes for the same blocks
    await run(store, blob, _text(_paras("a", "b")))
    assert embedded_texts(store) == []
    assert {m.get("contentRev") for m in _metas(db)} == {"rev-2"}


async def test_without_a_known_revision_only_the_position_is_refreshed(db) -> None:
    store, blob = make_store(db), Blob()
    _attempt("rev-1")
    await run(store, blob, _text(_paras("a", "b")), event=NEW)
    end_attempt()                               # a kind whose run knows no revision (code, SQL)
    v2 = _text(_paras("new", "a", "b"))
    await run(store, blob, v2)
    _assert_positions_follow(db, v2)
    kept = [m for m in _metas(db) if "new" not in str(m.get("blockId"))]
    assert {m.get("contentRev") for m in kept if m["blockIndex"] > 0} == {"rev-1"}


async def test_points_that_are_already_current_are_not_written_again(db) -> None:
    store, blob = make_store(db), Blob()
    await run(store, blob, _text(_paras("a", "b")), event=NEW)
    await run(store, blob, _text(_paras("x", "a", "b")))
    db.upsert_points = AsyncMock(wraps=db.upsert_points)
    await run(store, blob, _text(_paras("x", "a", "b")))
    db.upsert_points.assert_not_awaited()


async def test_a_store_that_cannot_return_vectors_keeps_the_record_indexed(db) -> None:
    store, blob = make_store(db), Blob()
    await run(store, blob, _text(_paras("a", "b")), event=NEW)
    db.scroll_with_vectors = AsyncMock(side_effect=VectorsNotFetchable("no vectors"))
    v2 = _text(_paras("x", "a", "b"))
    assert await run(store, blob, v2) is True
    assert embedded_texts(store)[-1:] == _paras("x"), "the update itself is indexed"
    store.logger.info.assert_any_call(
        "Record rec-race: the vector store cannot return stored vectors; kept points keep their stored position")


async def test_a_failed_refresh_never_fails_the_record(db) -> None:
    store, blob = make_store(db), Blob()
    await run(store, blob, _text(_paras("a", "b")), event=NEW)
    real_scroll = db.scroll
    calls = {"n": 0}

    async def failing_listing(*args: object, **kwargs: object) -> ScrollResult:
        if kwargs.get("with_payload") and "metadata.blockIndex" in kwargs["with_payload"]:
            calls["n"] += 1
            raise RuntimeError("store unavailable")
        return await real_scroll(*args, **kwargs)

    db.scroll = failing_listing
    await run(store, blob, _text(_paras("x", "a", "b")))
    assert calls["n"] == 1
    store.logger.warning.assert_any_call(
        "Record rec-race: could not refresh the kept points' position and revision: store unavailable")


async def test_refreshed_points_are_followed_by_a_membership_resync(db) -> None:
    store, blob = make_store(db), Blob()
    await run(store, blob, _text(_paras("a", "b")), event=NEW)
    store._resync_membership_after_write.reset_mock()
    await run(store, blob, _text(_paras("a", "b")[1:]))   # a removal only: no embed, so only the refresh writes
    store._resync_membership_after_write.assert_awaited()


async def test_moved_points_of_a_legacy_record_take_the_revision_of_the_run(db) -> None:
    container = _text(_paras("a", "b", "c"))
    _, blob = await legacy_record(db, container)
    store = make_store(db)
    _attempt("rev-9")
    await run(store, blob, _text(_paras("a", "b", "c")))
    assert embedded_texts(store) == []
    assert {m.get("contentRev") for m in _metas(db)} == {"rev-9"}


async def test_a_lost_lease_during_the_refresh_hands_the_delivery_back(db) -> None:
    store, blob = make_store(db), Blob()
    await run(store, blob, _text(_paras("a", "b")), event=NEW)
    store._resync_membership_after_write = AsyncMock(side_effect=LeaseLost("lease gone"))
    with pytest.raises(LeaseLost):
        await run(store, blob, _text(_paras("a", "b")[1:]))   # a removal only: the refresh is the one write


async def test_a_refresh_that_fails_after_writing_still_resyncs_membership(db) -> None:
    store, blob = make_store(db), Blob()
    await run(store, blob, _text(_paras("a", "b", "c")), event=NEW)
    store._resync_membership_after_write.reset_mock()
    real_upsert = db.upsert_points

    async def write_then_fail(*args: object, **kwargs: object) -> None:
        await real_upsert(*args, **kwargs)
        raise RuntimeError("store dropped the connection")

    db.upsert_points = write_then_fail
    await run(store, blob, _text(_paras("b", "c")))          # no embed: only the refresh writes
    store._resync_membership_after_write.assert_awaited()
    store.logger.warning.assert_any_call(
        "Record rec-race: could not refresh the kept points' position and revision: store dropped the connection")
