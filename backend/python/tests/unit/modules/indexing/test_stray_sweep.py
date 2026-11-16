"""The stray-point sweep: removes the points a record's current revision does not reference."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest_asyncio

from app.modules.indexing import stray_sweep
from app.modules.indexing.stray_sweep import SweepResult, sweep_stray_points
from app.modules.transformers.vectorstore import VectorStore
from app.services.vector_db.models import (
    CollectionConfig,
    VectorDBCapabilities,
    VectorPoint,
)
from tests.support.in_memory_vector_db import InMemoryVectorDBService
from tests.support.vector_db import make_collection_registry

A, B, VRID, COLL = "a" * 24, "b" * 24, "vr-1", "records"


def _point(pid: str, block: str | None, org: str = A, vrid: str = VRID) -> VectorPoint:
    meta = {"orgId": org, "virtualRecordId": vrid}
    if block is not None:
        meta["blockId"] = block
    return VectorPoint(id=pid, dense_vector=[1.0], payload={"page_content": pid, "metadata": meta})


@pytest_asyncio.fixture
async def db() -> InMemoryVectorDBService:
    service = InMemoryVectorDBService()
    await service.create_collection(COLL, CollectionConfig(embedding_size=1))
    return service


def _ids(db: InMemoryVectorDBService) -> set[str]:
    return set(db.collections[COLL])


async def _sweep(db: InMemoryVectorDBService, keep: set[str], **kw: int) -> SweepResult:
    return await sweep_stray_points(db, COLL, org_id=A, virtual_record_id=VRID, keep_block_ids=keep, **kw)


async def test_removes_every_point_of_an_unreferenced_block_and_keeps_the_rest(db) -> None:
    await db.upsert_points(COLL, [_point("p1", "b1"), _point("p2", "b1"), _point("p3", "b2"), _point("p4", "gone")])
    result = await _sweep(db, {"b1", "b2"})
    assert _ids(db) == {"p1", "p2", "p3"}
    assert result.stray_blocks == 1 and result.scanned == 4 and not result.truncated


async def test_is_idempotent(db) -> None:
    await db.upsert_points(COLL, [_point("p1", "b1"), _point("p2", "gone")])
    await _sweep(db, {"b1"})
    second = await _sweep(db, {"b1"})
    assert _ids(db) == {"p1"} and second.stray_blocks == 0


async def test_only_the_record_and_org_are_touched(db) -> None:
    await db.upsert_points(COLL, [
        _point("mine", "gone"),
        _point("other-record", "gone", vrid="vr-2"),
        _point("other-org", "gone", org=B),
    ])
    await _sweep(db, set())
    assert _ids(db) == {"other-record", "other-org"}


async def test_a_point_with_no_block_id_is_left_alone(db) -> None:
    await db.upsert_points(COLL, [_point("unknown", None), _point("gone", "old")])
    await _sweep(db, set())
    assert _ids(db) == {"unknown"}


async def test_pages_through_a_large_record_and_deletes_in_batches(db, monkeypatch) -> None:
    monkeypatch.setattr(stray_sweep, "_PAGE_SIZE", 50)
    monkeypatch.setattr(stray_sweep, "_DELETE_BATCH", 7)
    await db.upsert_points(COLL, [_point(f"keep-{i}", "keep") for i in range(60)]
                           + [_point(f"s-{i}", f"stray-{i}") for i in range(100)])
    db.delete_points = AsyncMock(wraps=db.delete_points)
    result = await _sweep(db, {"keep"})
    assert _ids(db) == {f"keep-{i}" for i in range(60)}
    assert result.stray_blocks == 100 and db.delete_points.await_count == 15  # ceil(100 / 7)


async def test_the_scan_is_bounded_per_record_and_the_next_run_finishes(db, monkeypatch) -> None:
    monkeypatch.setattr(stray_sweep, "_PAGE_SIZE", 10)
    await db.upsert_points(COLL, [_point(f"s-{i:03d}", f"stray-{i}") for i in range(50)])
    first = await _sweep(db, set(), max_points=20)
    assert first.truncated and first.scanned == 20 and len(_ids(db)) == 30
    for _ in range(3):
        await _sweep(db, set(), max_points=20)
    assert _ids(db) == set()


async def test_without_an_org_or_record_nothing_is_scanned(db) -> None:
    await db.upsert_points(COLL, [_point("p", "gone")])
    assert (await sweep_stray_points(db, COLL, org_id="", virtual_record_id=VRID, keep_block_ids=[])).scanned == 0
    assert (await sweep_stray_points(db, COLL, org_id=A, virtual_record_id="", keep_block_ids=[])).scanned == 0
    assert _ids(db) == {"p"}


def _store(db) -> VectorStore:
    db.get_capabilities = MagicMock(return_value=VectorDBCapabilities())
    return VectorStore(
        logger=MagicMock(), config_service=AsyncMock(), graph_provider=AsyncMock(),
        collection_registry=make_collection_registry(COLL), vector_db_service=db,
    )


def _record(blocks: list[str], groups: list[str] | None = None) -> MagicMock:
    from app.models.blocks import (
        Block,
        BlockGroup,
        BlocksContainer,
        BlockType,
        DataFormat,
        GroupType,
    )

    record = MagicMock()
    record.org_id, record.virtual_record_id = A, VRID
    record.block_containers = BlocksContainer(
        blocks=[Block(id=b, index=i, type=BlockType.TEXT, format=DataFormat.TXT, data=b) for i, b in enumerate(blocks)],
        block_groups=[BlockGroup(id=g, index=i, type=GroupType.TEXT_SECTION) for i, g in enumerate(groups or [])],
    )
    return record


async def test_the_store_keeps_blocks_groups_the_summary_and_the_residue(db) -> None:
    await db.upsert_points(COLL, [
        _point("blk", "b1"), _point("grp", "g1"), _point("sum", f"{VRID}_summary"),
        _point("res", f"{VRID}_code_residue"), _point("old", "b-old"),
    ])
    record = _record(["b1"], ["g1"])
    await _store(db)._sweep_stray_points(record, record.block_containers)
    assert _ids(db) == {"blk", "grp", "sum", "res"}


async def test_a_failing_sweep_does_not_fail_the_indexed_record(db) -> None:
    store = _store(db)
    db.scroll = AsyncMock(side_effect=RuntimeError("store down"))
    record = _record(["b1"])
    await store._sweep_stray_points(record, record.block_containers)  # does not raise
    store.logger.warning.assert_called_once()


async def test_the_sweep_can_be_switched_off(db, monkeypatch) -> None:
    await db.upsert_points(COLL, [_point("old", "b-old")])
    monkeypatch.setenv("INDEXING_STRAY_SWEEP", "off")
    record = _record(["b1"])
    await _store(db)._sweep_stray_points(record, record.block_containers)
    assert _ids(db) == {"old"}
