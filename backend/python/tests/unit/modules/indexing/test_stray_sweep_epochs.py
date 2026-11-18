"""The stray-point sweep keys on a block id alone: it never takes a point of a block the revision keeps, whatever epoch or precision wrote
that point, every delete names the writer's organisation, and it runs only after the record's batches were confirmed."""
from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
import pytest_asyncio

from app.modules.indexing.stray_sweep import sweep_stray_points
from app.services.messaging.precision_flip import PrecisionFlip
from app.services.vector_db.models import CollectionConfig, VectorPoint
from tests.support.in_memory_vector_db import InMemoryVectorDBService
from tests.support.index_world import (
    COLL,
    ORG,
    VRID,
    context_of,
    make_store,
    parse,
    record_of,
)

OTHER_ORG = "f" * 24
NEW_EPOCH = "r" + "ab" * 15


def _point(pid: str, block: str, *, org: str = ORG, epoch: str = "t0", precision: str = "fp32") -> VectorPoint:
    meta = {"orgId": org, "virtualRecordId": VRID, "blockId": block, "embedEpoch": epoch, "embedPrecision": precision}
    return VectorPoint(id=pid, dense_vector=[1.0, 0.0], payload={"page_content": pid, "metadata": meta})


@pytest_asyncio.fixture
async def db() -> InMemoryVectorDBService:
    service = InMemoryVectorDBService()
    await service.create_collection(COLL, CollectionConfig(embedding_size=2))
    return service


async def test_points_of_a_kept_block_survive_whatever_their_epoch_or_precision(db) -> None:
    """Another writer's points of the same kept block (a newer epoch, the other precision) are not strays."""
    await db.upsert_points(COLL, [
        _point("mine", "kept"),
        _point("newer-epoch", "kept", epoch=NEW_EPOCH),
        _point("other-precision", "kept", precision="int8-w"),
        _point("stray-old", "gone"),
        _point("stray-new", "gone", epoch=NEW_EPOCH, precision="int8-w"),
    ])
    await sweep_stray_points(db, COLL, org_id=ORG, virtual_record_id=VRID, keep_block_ids={"kept"})
    assert set(db.collections[COLL]) == {"mine", "newer-epoch", "other-precision"}


async def test_every_delete_of_the_sweep_names_the_organisation_and_leaves_another_alone(db) -> None:
    await db.upsert_points(COLL, [_point("mine", "gone"), _point("theirs", "gone", org=OTHER_ORG)])
    seen: list[dict] = []
    real = db.delete_points

    async def spying(collection, expression, *a, **k):  # noqa: ANN202
        seen.append(dict(expression) if isinstance(expression, dict) else {"expression": expression})
        return await real(collection, expression, *a, **k)

    db.delete_points = spying
    await sweep_stray_points(db, COLL, org_id=ORG, virtual_record_id=VRID, keep_block_ids=set())
    assert seen and set(db.collections[COLL]) == {"theirs"}
    # the in-memory store refuses a delete without an organisation, as every provider does (OrgRequired), so it was named


async def test_a_sweep_without_an_organisation_deletes_nothing(db) -> None:
    await db.upsert_points(COLL, [_point("p", "gone")])
    result = await sweep_stray_points(db, COLL, org_id="", virtual_record_id=VRID, keep_block_ids=set())
    assert result.scanned == 0 and set(db.collections[COLL]) == {"p"}


async def test_a_record_whose_batch_was_rolled_back_is_not_swept(db) -> None:
    """The sweep follows the confirmed write: a refused batch raises before it, so it cannot run against a half-written record."""
    store = make_store(db)
    container = parse("alpha\n\nbeta")
    store._sweep_stray_points = AsyncMock(return_value=None)
    store.index_documents = AsyncMock(side_effect=PrecisionFlip("the index holds int8-w"))
    ctx = context_of(record_of(container), "updateRecord")
    with pytest.raises(PrecisionFlip):
        await store.apply(ctx)
    store._sweep_stray_points.assert_not_awaited()
