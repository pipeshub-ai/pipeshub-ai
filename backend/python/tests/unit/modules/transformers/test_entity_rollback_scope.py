"""The entity writer's rollback of a rejected batch, and the index its precision counts need."""

import logging
from unittest.mock import AsyncMock

import pytest

from app.modules.transformers.entity_vectorstore import EntityVectorStore
from app.services.messaging.precision_flip import PrecisionFlip
from app.services.vector_db.collection_pin import FP32, INT8
from app.services.vector_db.const.const import EMBED_PRECISION_FIELD
from tests.unit.services.messaging.test_precision_switch_converges import (  # noqa: F401  (the fixture is used by name)
    ENTITIES,
    _entity,
    _entity_write,
    world,
)

log = logging.getLogger("rollback-scope")


@pytest.mark.asyncio
async def test_the_entity_index_carries_a_payload_index_on_the_precision_tag() -> None:
    """Redis cannot filter on a field its index lacks, so the guard's count would fail and no pin would be claimed."""
    store = object.__new__(EntityVectorStore)
    store.logger = log
    store.vector_db_service = AsyncMock()
    await store._ensure_payload_indexes("entities")
    fields = [call.kwargs["field_name"] for call in store.vector_db_service.create_index.await_args_list]
    assert EMBED_PRECISION_FIELD in fields


@pytest.mark.asyncio
async def test_a_rejected_batch_does_not_remove_a_replacement_of_the_pins_precision(world) -> None:  # noqa: F811
    """Another replica writes the same entity at the new precision between this batch's write and its confirmation."""
    await world.pins.claim(ENTITIES, INT8)
    world.vdb.add(ENTITIES, INT8, entityType="person", entityId="p0")
    world.served["precision"] = INT8
    real = world.vdb.upsert_points

    async def write_then_other_replica_replaces(collection_name, points):  # noqa: ANN202
        await real(collection_name, points)
        await world.pins.release(ENTITIES)
        await world.pins.claim(ENTITIES, FP32)
        for point in points:  # the same entity, rewritten with valid vectors at the pin's precision
            meta = {**point.payload["metadata"], "embedPrecision": FP32}
            await real(collection_name, [type(point)(id=point.id, dense_vector=point.dense_vector, sparse_vector=None, payload={**point.payload, "metadata": meta})])

    world.vdb.upsert_points = write_then_other_replica_replaces
    with pytest.raises(PrecisionFlip):
        await _entity_write(world)
    kept = [p for p in world.vdb.points[ENTITIES] if p.get("entityId") == _entity().entity_id]
    assert [p["embedPrecision"] for p in kept] == [FP32]


@pytest.mark.asyncio
async def test_a_rollback_that_fails_still_hands_the_batch_back(world) -> None:  # noqa: F811
    """The delete's own error must not replace the PrecisionFlip: the record would be counted as written."""
    world.vdb.add(ENTITIES, INT8, entityType="person", entityId="p0")
    world.served["precision"] = INT8
    async def failing_delete(collection, expression, refresh=False):  # noqa: ANN202
        raise ConnectionError("vector store down")

    async def pin_is_fp32_so_the_write_is_refused_after_it_lands(collection_name, points):  # noqa: ANN202
        await world.pins.release(ENTITIES)
        await world.pins.claim(ENTITIES, FP32)
        world.vdb.delete_points = failing_delete
        await world.vdb.__class__.upsert_points(world.vdb, collection_name, points)

    # The check in front of the write passes (the pin is int8 then); the pin has moved by the time of the write.
    await world.pins.release(ENTITIES)
    await world.pins.claim(ENTITIES, INT8)
    world.vdb.upsert_points = pin_is_fp32_so_the_write_is_refused_after_it_lands
    with pytest.raises(PrecisionFlip):
        await _entity_write(world)
