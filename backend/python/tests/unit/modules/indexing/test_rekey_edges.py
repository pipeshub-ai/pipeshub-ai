"""The edges of the move to content ids: what follows the write, the new block index, grouped rows, the early return, the read.

Each test fails when the line it names is removed or changed (scratch reverts in the handoff).
"""
from __future__ import annotations

import random
from unittest.mock import AsyncMock, patch

import pytest_asyncio

from app.core import org_context
from app.models.blocks import (
    Block,
    BlockGroup,
    BlockGroupChildren,
    BlocksContainer,
    BlockType,
    DataFormat,
    GroupType,
)
from app.modules.reconciliation.block_identity import BlockIdentity
from app.services.vector_db.collection_locator import StaticCollectionLocator
from app.services.vector_db.models import CollectionConfig, ScrollResult, VectorPoint
from tests.support.in_memory_vector_db import InMemoryVectorDBService
from tests.support.index_world import (
    COLL,
    ORG,
    RID,
    VRID,
    legacy_record,
    make_store,
    record_of,
    run,
)

WORDS = "alpha beta gamma delta epsilon zeta eta theta iota kappa lambda mu nu xi omicron pi rho sigma tau".split()


def _paragraphs(n: int, seed: int = 7) -> list[str]:
    rng = random.Random(seed)
    return [f"P{i}. " + " ".join(rng.choice(WORDS) for _ in range(rng.randint(8, 14))) + "." for i in range(n)]


def _text_container(paras: list[str]) -> BlocksContainer:
    return BlocksContainer(blocks=[Block(index=i, type=BlockType.TEXT, format=DataFormat.TXT, data=p) for i, p in enumerate(paras)])


def _table_container(rows: list[str]) -> BlocksContainer:
    blocks = [Block(index=i, type=BlockType.TABLE_ROW, format=DataFormat.JSON, parent_index=0,
                    data={"row_natural_language_text": t, "row_number": i}) for i, t in enumerate(rows)]
    group = BlockGroup(index=0, type=GroupType.TABLE, data={"table_summary": "Summary of the table"},
                       children=BlockGroupChildren.from_indices(block_indices=[b.index for b in blocks]))
    return BlocksContainer(blocks=blocks, block_groups=[group])


@pytest_asyncio.fixture
async def db() -> InMemoryVectorDBService:
    service = InMemoryVectorDBService()
    await service.create_collection(COLL, CollectionConfig(embedding_size=2))
    return service


def _blocks(db: InMemoryVectorDBService) -> set[str]:
    return {p.payload["metadata"]["blockId"] for p in db.collections[COLL].values()}


async def test_a_move_that_writes_every_point_resyncs_membership_and_checks_for_orphans(db) -> None:
    paras = _paragraphs(20)
    _, blob = await legacy_record(db, _text_container(paras))
    store = make_store(db)
    await run(store, blob, _text_container(paras))
    assert store._embed_documents_with_retry.await_count == 0, "nothing was embedded: every block moved"
    store._resync_membership_after_write.assert_awaited_once_with(VRID, RID)
    store._cleanup_orphaned_embeddings_if_needed.assert_awaited_once()


async def test_a_record_deleted_during_the_move_leaves_no_orphan_points(db) -> None:
    from app.modules.transformers.vectorstore import VectorStore

    paras = _paragraphs(20)
    _, blob = await legacy_record(db, _text_container(paras))
    store = make_store(db)
    graph = AsyncMock()
    graph.get_document = AsyncMock(return_value=None)                    # the record is gone by the time the points are moved
    graph.get_records_by_virtual_record_id = AsyncMock(return_value=[])  # and no other record shares the virtual record
    store.graph_provider = graph
    store.collection_locator = StaticCollectionLocator([COLL])
    store._cleanup_orphaned_embeddings_if_needed = VectorStore._cleanup_orphaned_embeddings_if_needed.__get__(store)
    with patch("app.services.vector_db.membership.EMPTY_CONFIRM_DELAY_SECONDS", 0), org_context.for_org(ORG, job="test"):
        await run(store, blob, _text_container(paras))
    assert db.collections[COLL] == {}, "the moved points of a deleted record are released, not left behind"


async def test_moved_points_take_the_block_index_of_the_new_revision(db) -> None:
    paras = _paragraphs(12)
    _, blob = await legacy_record(db, _text_container(paras))
    store = make_store(db)
    shifted = ["A new first paragraph that changes every position after it.", *paras]
    await run(store, blob, _text_container(shifted))
    assert list(store._embed_documents_with_retry.await_args_list[0].args[0]) == [shifted[0]], "only the new block is embedded"
    by_text = {p.payload["page_content"]: p.payload["metadata"]["blockIndex"] for p in db.collections[COLL].values()}
    assert by_text == {text: i for i, text in enumerate(shifted)}


async def test_the_early_return_of_an_all_moved_record_still_sweeps_strays(db) -> None:
    paras = _paragraphs(10)
    _, blob = await legacy_record(db, _text_container(paras))
    await db.upsert_points(COLL, [VectorPoint(id="f" * 8 + "-0000-4000-8000-" + "0" * 12, dense_vector=[1.0, 1.0], payload={
        "page_content": "left by a run of an older revision",
        "metadata": {"orgId": ORG, "virtualRecordId": VRID, "blockId": "stale-block"}})])
    store = make_store(db)
    await run(store, blob, _text_container(paras))
    assert store._embed_documents_with_retry.await_count == 0
    assert "stale-block" not in _blocks(db)


async def test_the_read_makes_earlier_writes_searchable_first(db) -> None:
    paras = _paragraphs(8)
    _, blob = await legacy_record(db, _text_container(paras))
    store = make_store(db)
    order: list[str] = []
    real_scroll = db.scroll_with_vectors

    async def scroll(*args: object, **kwargs: object):  # noqa: ANN202
        order.append("scroll")
        return await real_scroll(*args, **kwargs)

    async def searchable(collection: str) -> None:
        order.append("searchable")

    db.scroll_with_vectors, db.make_searchable = scroll, searchable
    await run(store, blob, _text_container(paras))
    assert order and order[0] == "searchable" and "scroll" in order


async def test_a_grouped_point_whose_rows_are_not_all_moving_is_embedded_not_moved(db, monkeypatch) -> None:
    monkeypatch.setenv("EMBED_POINT_POLICY", "C")
    monkeypatch.setenv("PIPESHUB_EMBED_TOKEN_LIMIT", "512")
    rows = [f"row {i} " + " ".join(WORDS[(i + j) % len(WORDS)] for j in range(6)) for i in range(8)]
    legacy_container = _table_container(rows)
    store, _ = await legacy_record(db, legacy_container)
    grouped = [p for p in db.collections[COLL].values() if p.payload["metadata"].get("rowBlockIds")]
    assert grouped, "policy C groups consecutive table rows into one point"
    old_ids = {b.id for b in legacy_container.blocks}
    # the new revision maps every old block id but one row of a grouped point
    new = _table_container(rows)
    BlockIdentity.assign(new, ORG, VRID)
    old_to_new = {old.id: fresh.id for old, fresh in zip(legacy_container.blocks, new.blocks, strict=True)}
    lost_row = grouped[0].payload["metadata"]["rowBlockIds"][-1]
    del old_to_new[lost_row]
    to_index, to_delete = await store._move_points_to_new_ids(record_of(new), old_to_new, set(), set())
    first_row_old = grouped[0].payload["metadata"]["blockId"]
    assert old_to_new[first_row_old] in to_index and first_row_old in to_delete, "embedded again, old points deleted after"
    listed = [r for r in grouped[0].payload["metadata"]["rowBlockIds"] if r in old_to_new]
    assert len(listed) > 1 and {old_to_new[r] for r in listed} <= to_index, "every row the point held is embedded again, not only the first"
    assert first_row_old in old_ids
    assert first_row_old in _blocks(db), "and left in place until that embed is stored"


def _two_tables(first: list[str], second: list[str]) -> BlocksContainer:
    """Table 0 then table 1, so the second table's group index and its rows' positions depend on the first."""
    blocks: list[Block] = []
    groups: list[BlockGroup] = []
    for group_index, rows in enumerate((first, second)):
        members = [Block(index=len(blocks) + i, type=BlockType.TABLE_ROW, format=DataFormat.JSON, parent_index=group_index,
                         data={"row_natural_language_text": t, "row_number": i}) for i, t in enumerate(rows)]
        blocks.extend(members)
        groups.append(BlockGroup(index=group_index, type=GroupType.TABLE, data={"table_summary": f"Summary of table {rows[0]}"},
                                 children=BlockGroupChildren.from_indices(block_indices=[m.index for m in members])))
    return BlocksContainer(blocks=blocks, block_groups=groups)


async def test_a_moved_group_point_takes_the_new_group_index_in_block_group_index(db) -> None:
    rows_a = [f"row {i} " + " ".join(WORDS[(i + j) % len(WORDS)] for j in range(6)) for i in range(4)]
    rows_x = [f"other {i} " + " ".join(WORDS[(i * 3 + j) % len(WORDS)] for j in range(6)) for i in range(4)]
    legacy = BlocksContainer(blocks=_two_tables(rows_a, rows_x).blocks[:4], block_groups=[_two_tables(rows_a, rows_x).block_groups[0]])
    _, blob = await legacy_record(db, legacy)
    store = make_store(db)
    new = _two_tables(rows_x, rows_a)             # table A is now the second group
    await run(store, blob, new)
    index_of = {g.id: g.index for g in new.block_groups}
    group_points = [p for p in db.collections[COLL].values() if p.payload["metadata"].get("isBlockGroup")]
    assert group_points
    for point in group_points:
        meta = point.payload["metadata"]
        assert meta["blockGroupIndex"] == index_of[meta["blockId"]], "a group point carries its index in blockGroupIndex"
        assert "blockIndex" not in meta
    assert {p.payload["metadata"]["blockGroupIndex"] for p in group_points} == {0, 1}


async def test_a_read_that_repeats_one_point_and_skips_another_is_not_taken_for_complete(db) -> None:
    """A positional cursor can do this; the counts match, but the skipped block's old points must not be deleted unembedded."""
    paras = _paragraphs(6)
    _, blob = await legacy_record(db, _text_container(paras))
    store = make_store(db)
    real = db.scroll_with_vectors

    async def repeats_one(*args: object, **kwargs: object) -> ScrollResult:
        page = await real(*args, **kwargs)
        page.points = [*page.points[:-1], page.points[0]]
        return page

    db.scroll_with_vectors = repeats_one
    await run(store, blob, _text_container(paras))
    assert len(store._embed_documents_with_retry.await_args_list[0].args[0]) == len(paras), "every block was embedded again"
    assert {p.payload["page_content"] for p in db.collections[COLL].values()} == set(paras)


async def test_a_block_whose_point_was_lost_is_embedded_not_counted_as_moved(db) -> None:
    paras = _paragraphs(10)
    _, blob = await legacy_record(db, _text_container(paras))
    lost = next(pid for pid, p in db.collections[COLL].items() if p.payload["page_content"] == paras[4])
    del db.collections[COLL][lost]                      # an interrupted index left this block without its point
    store = make_store(db)
    await run(store, blob, _text_container(paras))
    assert [list(c.args[0]) for c in store._embed_documents_with_retry.await_args_list] == [[paras[4]]]
    assert {p.payload["page_content"] for p in db.collections[COLL].values()} == set(paras)


async def test_a_block_that_never_had_a_point_is_not_embedded(db) -> None:
    paras = _paragraphs(6)
    _, blob = await legacy_record(db, _text_container([*paras, ""]))   # a blank block has nothing to embed
    store = make_store(db)
    await run(store, blob, _text_container([*paras, ""]))
    assert store._embed_documents_with_retry.await_count == 0
