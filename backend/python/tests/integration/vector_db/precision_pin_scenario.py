"""One precision-pin scenario that every vector store must pass: the real guard and pin store over a real index.

The pin lives in the key-value store (an in-memory one here: its own backends are covered elsewhere); what each vector
store answers is the tag filters the guard counts and deletes with: positive equality on the tag, an image point beside
the text points, and a delete by tag that leaves the rest of the record alone.
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import pytest

from app.config.configuration_service import ConfigurationService
from app.config.providers.in_memory_store import InMemoryKeyValueStore
from app.services.messaging.precision_flip import PrecisionFlip
from app.services.vector_db.collection_pin import (
    FP32,
    IMAGE_TAG,
    INT8,
    STAMP_TAG,
    CollectionPinStore,
)
from app.services.vector_db.models import VectorPoint
from app.services.vector_db.precision_guard import PrecisionGuard
from tests.integration.vector_db.helpers import make_dense, point_id

if TYPE_CHECKING:
    from app.services.vector_db.interface.vector_db import IVectorDBService

log = logging.getLogger("precision-pin-it")


def point(org: str, name: str, vrid: str, tag: str | None, i: int = 0) -> VectorPoint:
    metadata: dict = {"orgId": org, "virtualRecordId": vrid, "blockId": name}
    if tag is not None:
        metadata["embedPrecision"] = tag
    vector = [0.0] * 8
    vector[i % 8] = 1.0
    return VectorPoint(
        id=point_id(f"precision-pin/{org}/{name}"),
        dense_vector=make_dense(vector),
        payload={"page_content": f"text of {name}", "metadata": metadata},
    )


def guard_for(service: IVectorDBService) -> tuple[PrecisionGuard, CollectionPinStore]:
    pins = CollectionPinStore(ConfigurationService(log, InMemoryKeyValueStore(log)), log)
    return PrecisionGuard(pins, service, log), pins


async def counts(guard: PrecisionGuard, collection: str, org: str) -> dict[str, int]:
    return await guard.tag_counts(collection, org)


async def run_pin_scenario(service: IVectorDBService, collection: str, org: str) -> None:
    """An empty index is claimed, the other precision is refused, a leak is removed alone, a cleanup frees the pin."""
    guard, pins = guard_for(service)
    zero = {INT8: 0, FP32: 0, IMAGE_TAG: 0, STAMP_TAG: 0}
    assert await counts(guard, collection, org) == zero

    await guard.verify_batch(collection, org, INT8, fallback=False, intent=INT8)  # the first writer claims
    assert (await pins.get(collection, guard.scope(org), fresh=True)).precision == INT8
    assert await pins.keys() == [pins.key(collection, guard.scope(org))]  # one pin for the index, under the key its store uses

    await service.upsert_points(collection, [
        point(org, "t1", "v1", INT8, 0), point(org, "t2", "v1", INT8, 1), point(org, "t3", "v1", INT8, 2),
        point(org, "img", "v1", IMAGE_TAG, 3),
    ])
    await service.make_searchable(collection)
    assert await counts(guard, collection, org) == {INT8: 3, FP32: 0, IMAGE_TAG: 1, STAMP_TAG: 0}

    with pytest.raises(PrecisionFlip):
        await guard.verify_batch(collection, org, FP32, fallback=False, intent=INT8)  # the other precision is refused
    await guard.verify_batch(collection, org, INT8, fallback=False, intent=INT8)

    # A leaked batch of the other precision: found by its tag, and only it is removed (the image point stays).
    await service.upsert_points(collection, [point(org, "leak", "v1", FP32, 4)])
    await service.make_searchable(collection)
    assert await guard.leaked(collection, org, "v1", INT8) == 1
    await guard.delete_leaked(collection, org, "v1", INT8)
    await service.make_searchable(collection)
    assert await counts(guard, collection, org) == {INT8: 3, FP32: 0, IMAGE_TAG: 1, STAMP_TAG: 0}

    # Without a pin the index still says what it holds, from the tags.
    await pins.release_collection(collection)
    standing = await guard.derive(collection, org)
    assert (standing.precision, standing.source) == (INT8, "derived")

    # The cleanup: points first, then the pin. The other precision may then claim.
    expression = await service.filter_collection(must={"orgId": org})
    await service.delete_points(collection, expression)
    await service.make_searchable(collection)
    await pins.release_collection(collection)
    await guard.verify_batch(collection, org, FP32, fallback=False, intent=INT8)
    assert (await pins.get(collection, guard.scope(org), fresh=True)).precision == FP32


async def run_legacy_scenario(service: IVectorDBService, collection: str, org: str) -> None:
    """Points with no tag are an fp32 index: an int8 writer is refused, an fp32 one goes on."""
    guard, pins = guard_for(service)
    await service.upsert_points(collection, [point(org, "old1", "v9", None, 0), point(org, "old2", "v9", None, 1)])
    await service.make_searchable(collection)
    standing = await guard.derive(collection, org)
    assert (standing.precision, standing.source) == (FP32, "legacy")
    with pytest.raises(PrecisionFlip):
        await guard.verify_batch(collection, org, INT8, fallback=False, intent=INT8)
    assert await pins.get(collection, guard.scope(org), fresh=True) is None  # a refusal claims nothing
    await guard.verify_batch(collection, org, FP32, fallback=False, intent=INT8)
