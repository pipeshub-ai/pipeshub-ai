"""The stray-point sweep: removes the points a record's current revision does not reference."""
from __future__ import annotations

import contextlib
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


async def _sweep(db: InMemoryVectorDBService, keep: set[str], **kw: object) -> SweepResult:
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


async def test_a_store_that_pages_only_so_far_still_loses_the_strays_found_before_the_ceiling(db, monkeypatch) -> None:
    from app.services.vector_db.models import ScrollResult, ScrollWindowExceededError

    monkeypatch.setattr(stray_sweep, "_PAGE_SIZE", 10)
    await db.upsert_points(COLL, [_point(f"p-{i:03d}", "keep" if i % 2 else f"stray-{i}") for i in range(40)])
    real = db.scroll
    pages = 0

    async def window(*args: object, **kwargs: object) -> ScrollResult:
        nonlocal pages
        pages += 1
        if pages == 3:
            raise ScrollWindowExceededError("ceiling")
        return await real(*args, **kwargs)

    db.scroll = window
    result = await _sweep(db, {"keep"})
    assert result.truncated and result.stray_blocks == 10
    assert all(p.payload["metadata"]["blockId"] == "keep" or int(p.id[2:]) >= 20 for p in db.collections[COLL].values())


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


async def test_a_pass_that_stops_at_its_bound_gives_where_the_next_one_resumes(db, monkeypatch) -> None:
    monkeypatch.setattr(stray_sweep, "_PAGE_SIZE", 10)
    await db.upsert_points(COLL, [_point(f"k-{i:03d}", "keep") for i in range(40)] + [_point(f"s-{i:03d}", f"stray-{i}") for i in range(20)])
    seen: list[int] = []
    resume = None
    for _ in range(6):
        result = await _sweep(db, {"keep"}, max_points=25, resume=resume)
        seen.append(result.scanned)
        assert result.scanned <= 25, "a pass stays within its bound"
        resume = result.next_offset
        if resume is None:
            break
    assert {p.payload["metadata"]["blockId"] for p in db.collections[COLL].values()} == {"keep"}
    assert len(seen) > 1


async def test_the_store_resumes_a_bounded_sweep_on_its_next_pass(db, monkeypatch) -> None:
    monkeypatch.setenv("INDEXING_STRAY_SWEEP_MAX_POINTS", "25")
    monkeypatch.setattr(stray_sweep, "_PAGE_SIZE", 10)
    await db.upsert_points(COLL, [_point(f"k-{i:03d}", "b0") for i in range(40)] + [_point(f"s-{i:03d}", f"stray-{i}") for i in range(10)])
    store = _store(db)
    record = _record(["b0"])
    for _ in range(4):
        await store._sweep_stray_points(record, record.block_containers)
    assert {p.payload["metadata"]["blockId"] for p in db.collections[COLL].values()} == {"b0"}


async def test_a_run_whose_record_is_on_another_revision_does_not_sweep(db) -> None:
    """A run that lost its lease and resumes must not delete the points of the revision that took over."""
    await db.upsert_points(COLL, [_point("newer", "b-of-the-newer-revision")])
    store = _store(db)
    record = _record(["b0"])
    record.id, record.md5_hash = "rec-1", "md5-of-this-run"
    store.graph_provider.get_document = AsyncMock(return_value={"_key": "rec-1", "virtualRecordId": VRID, "md5Checksum": "md5-of-the-newer-run"})
    await store._sweep_stray_points(record, record.block_containers)
    assert _ids(db) == {"newer"}
    store.graph_provider.get_document = AsyncMock(return_value={"_key": "rec-1", "virtualRecordId": VRID, "md5Checksum": "md5-of-this-run"})
    await store._sweep_stray_points(record, record.block_containers)
    assert _ids(db) == set(), "the same revision is swept"


async def test_a_record_that_cannot_be_read_is_not_swept(db) -> None:
    await db.upsert_points(COLL, [_point("p", "b-old")])
    store = _store(db)
    store.graph_provider.get_document = AsyncMock(side_effect=RuntimeError("graph down"))
    record = _record(["b0"])
    await store._sweep_stray_points(record, record.block_containers)
    assert _ids(db) == {"p"}


async def test_the_revision_is_asked_again_before_each_delete_batch(db, monkeypatch) -> None:
    """The listing can be long: a run superseded while it lists must not delete the batches that follow."""
    monkeypatch.setattr(stray_sweep, "_DELETE_BATCH", 5)
    await db.upsert_points(COLL, [_point(f"s-{i:02d}", f"stray-{i:02d}") for i in range(20)])
    asked = {"n": 0}

    async def may_delete() -> bool:
        asked["n"] += 1
        return asked["n"] <= 2

    result = await _sweep(db, set(), may_delete=may_delete)
    assert asked["n"] == 3 and len(_ids(db)) == 10, "two batches of 5 went, the third was refused"
    assert result.stray_blocks == 10, "the blocks removed, not the blocks found"


async def test_a_refused_pass_publishes_no_resume_point_and_the_next_pass_still_finds_its_strays(db, monkeypatch) -> None:
    """A pass cut by its bound and then refused must not leave a cursor past the strays it listed and did not delete."""
    monkeypatch.setattr(stray_sweep, "_PAGE_SIZE", 10)
    monkeypatch.setattr(stray_sweep, "_DELETE_BATCH", 5)
    await db.upsert_points(COLL, [_point(f"s-{i:02d}", f"stray-{i:02d}") for i in range(30)])

    async def never() -> bool:
        return False

    refused = await _sweep(db, set(), max_points=20, may_delete=never)
    assert refused.truncated and refused.next_offset is None and refused.stray_blocks == 0 and len(_ids(db)) == 30
    resume = refused.next_offset
    for _ in range(5):
        result = await _sweep(db, set(), max_points=20, resume=resume)
        resume = result.next_offset
        if resume is None:
            break
    assert _ids(db) == set(), "the first window's strays were not skipped"


async def test_the_store_asks_again_before_each_delete_batch_not_only_before_the_listing(db, monkeypatch) -> None:
    """The record changes revision after the pre-check: the batches still to go are not deleted (the wiring of may_delete)."""
    monkeypatch.setattr(stray_sweep, "_DELETE_BATCH", 5)
    await db.upsert_points(COLL, [_point(f"s-{i:02d}", f"stray-{i:02d}") for i in range(12)])
    store = _store(db)
    record = _record(["b0"])
    record.id, record.md5_hash = "rec-1", "md5-of-this-run"
    same = {"_key": "rec-1", "virtualRecordId": VRID, "md5Checksum": "md5-of-this-run"}
    other = {**same, "md5Checksum": "md5-of-a-newer-run"}
    store.graph_provider.get_document = AsyncMock(side_effect=[same, same, other, other, other])   # pre-check, batch 1, then superseded
    await store._sweep_stray_points(record, record.block_containers)
    assert len(_ids(db)) == 7, "one batch of 5 went; the record was on another revision before the next"


async def test_each_delete_call_of_the_sweep_runs_inside_the_fence(db, monkeypatch) -> None:
    monkeypatch.setattr(stray_sweep, "_DELETE_BATCH", 5)
    await db.upsert_points(COLL, [_point(f"s-{i:02d}", f"stray-{i:02d}") for i in range(12)])
    events: list[str] = []

    @contextlib.asynccontextmanager
    async def fence():  # noqa: ANN202
        events.append("enter")
        yield
        events.append("exit")

    real_delete = db.delete_points

    async def spy(*args: object, **kwargs: object) -> None:
        events.append("delete")
        await real_delete(*args, **kwargs)

    db.delete_points = spy  # type: ignore[method-assign]
    await _sweep(db, set(), fence=fence)
    assert events == ["enter", "delete", "exit"] * 3, "three batches, each delete between the fence's enter and exit"


async def test_the_stores_sweep_deletes_through_the_lease_fence(db, monkeypatch) -> None:
    from app.modules.transformers import vectorstore as vs_mod

    fenced: list[str] = []

    @contextlib.asynccontextmanager
    async def counting(what: str):  # noqa: ANN202
        fenced.append(what)
        yield

    monkeypatch.setattr(vs_mod, "_fenced", counting)
    await db.upsert_points(COLL, [_point("stale-point", "stale-block")])
    store = _store(db)
    record = _record(["b0"])
    await store._sweep_stray_points(record, record.block_containers)
    assert fenced == ["the stray sweep"], "the sweep's delete went through the fence"
