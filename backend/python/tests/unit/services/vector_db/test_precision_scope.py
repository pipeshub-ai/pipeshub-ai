"""Which key and which counts name an index: the collection on a store with one collection, the org's space on pgvector."""

import logging
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.config.configuration_service import ConfigurationService
from app.config.providers.in_memory_store import InMemoryKeyValueStore
from app.services.messaging.precision_check import ServerState
from app.services.messaging.precision_runtime import build_precision_verdict
from app.services.vector_db.collection_pin import (
    FP32,
    IMAGE_TAG,
    INT8,
    CollectionPinStore,
)
from app.services.vector_db.precision_guard import PrecisionFlip, PrecisionGuard
from tests.unit.services.vector_db.test_collection_pin import _FakeDb
from tests.unit.services.vector_db.test_collection_pin_postgres_kv import (
    _process,
    _Runtime,
)
from tests.unit.services.vector_db.test_precision_derivation_pgvector import (
    ORG,
    _graph,
)
from tests.unit.services.vector_db.test_precision_derivation_pgvector import (
    _guard as _pg_guard,
)
from tests.unit.services.vector_db.test_precision_derivation_pgvector import (
    _Runtime as _PgRuntime,
)
from tests.unit.services.vector_db.test_precision_derivation_pgvector import (
    _service as _pg_service,
)

log = logging.getLogger("scope-test")


def _guard(db=None):  # noqa: ANN202
    config = ConfigurationService(log, InMemoryKeyValueStore(log))
    pins = CollectionPinStore(config, log)
    return PrecisionGuard(pins, db or _FakeDb(), log), pins


@pytest.mark.asyncio
async def test_writers_that_carry_an_org_and_one_that_does_not_share_the_collections_pin() -> None:
    guard, pins = _guard()
    await guard.verify_batch("c", "o1", INT8, fallback=False, intent=INT8)
    with pytest.raises(PrecisionFlip):  # the other writer carries no org and meets the same pin
        await guard.verify_batch("c", None, FP32, fallback=False, intent=INT8)
    assert await pins.keys() == [pins.key("c")]


@pytest.mark.asyncio
async def test_the_pin_is_read_where_it_was_claimed_whatever_org_the_reader_carries() -> None:
    guard, pins = _guard()
    await guard.verify_batch("c", None, INT8, fallback=False, intent=INT8)
    assert (await guard.standing("c", "o1")).source == "pin"
    assert (await guard.standing_for_read("c", "o2")).precision == INT8


@pytest.mark.asyncio
async def test_points_of_one_precision_make_the_collection_that_precision_for_any_org() -> None:
    db = _FakeDb()
    db.add(INT8, org="o1", n=3)
    guard, _ = _guard(db)
    standing = await guard.derive("c", "o2")
    assert (standing.precision, standing.source) == (INT8, "derived")


@pytest.mark.asyncio
async def test_a_collection_of_image_points_only_is_not_legacy_for_an_org_that_owns_none() -> None:
    db = _FakeDb()
    db.add(IMAGE_TAG, org="o1", n=4)
    guard, _ = _guard(db)
    assert (await guard.derive("c", "o2")).source == "none"


@pytest.mark.asyncio
async def test_untagged_points_make_the_collection_legacy_fp32_for_any_org() -> None:
    db = _FakeDb()
    db.add(None, org="o1", n=2)
    guard, _ = _guard(db)
    assert (await guard.derive("c", "o2")).precision == FP32
    with pytest.raises(PrecisionFlip):
        await guard.verify_batch("c", "o2", INT8, fallback=False, intent=INT8)


@pytest.mark.asyncio
async def test_the_pin_key_is_the_same_on_the_postgres_key_value_store() -> None:
    runtime = _Runtime()
    _, pins = _process(runtime)
    guard = PrecisionGuard(pins, _FakeDb(), log)
    await guard.verify_batch("c", "o1", INT8, fallback=False, intent=INT8)
    assert list(runtime.table) == [pins.key("c")]


@pytest.mark.asyncio
async def test_the_verdict_of_a_shared_collection_is_one_index_and_lists_no_orgs() -> None:
    db = _FakeDb()
    db.add(INT8, org="o1", n=3)
    config = ConfigurationService(log, InMemoryKeyValueStore(log))
    registry = MagicMock()
    registry.list_managed_collections = AsyncMock(return_value=[SimpleNamespace(name="c", embedding_model="m")])
    graph = _graph(["o1", "o2"])
    verdict = build_precision_verdict(config, registry, db, graph, log)
    verdict._fetch_states = AsyncMock(return_value={"m": ServerState(INT8, INT8, False)})
    await verdict.refresh()
    assert not verdict.mismatch  # an empty second org no longer reads as legacy fp32 beside int8 points
    graph.get_all_orgs.assert_not_awaited()
    assert (await CollectionPinStore(config, log).get("c", fresh=True)).precision == INT8


@pytest.mark.asyncio
async def test_pgvector_keeps_one_pin_and_one_set_of_counts_per_org() -> None:
    runtime = _PgRuntime()
    guard, pins, _ = _pg_guard(runtime)
    assert guard.scope(ORG) == ORG
    await guard.verify_batch("records", ORG, FP32, fallback=False, intent=FP32)
    assert await pins.keys() == [pins.key("records", ORG)]
    assert guard.per_org_spaces() is True


@pytest.mark.asyncio
async def test_pgvector_verdict_still_derives_for_every_org_of_the_deployment() -> None:
    runtime = _PgRuntime(tagged={"int8-w": 3}, points=3)
    config = ConfigurationService(log, InMemoryKeyValueStore(log))
    registry = MagicMock()
    registry.list_managed_collections = AsyncMock(return_value=[SimpleNamespace(name="records", embedding_model="m")])
    graph = _graph([ORG])
    verdict = build_precision_verdict(config, registry, _pg_service(runtime), graph, log)
    verdict._fetch_states = AsyncMock(return_value={"m": ServerState(FP32, FP32, False)})
    await verdict.refresh()
    assert verdict.mismatch
    graph.get_all_orgs.assert_awaited_with(active=True, raise_on_error=True)
