"""Records that share a virtual record serialise their vector writes and stray sweep, and a run whose lease lapsed stops."""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest
import pytest_asyncio

from app.config.constants.arangodb import EventTypes
from app.exceptions.indexing_exceptions import IndexingError
from app.modules.pipeline import leases as leases_mod
from app.modules.pipeline.leases import LeaseLost, RecordLeases
from app.modules.transformers.vectorstore import VectorStore, VirtualRecordBusy
from app.services.messaging.consumer_concurrency import RequeueWithoutAttempt
from app.services.messaging.error_classifier import (
    MessageErrorClassifier,
    MessageErrorType,
)
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
from tests.support.lease_fakes import FakeLeases

DOC = "\n\n".join(f"Paragraph {i} says something quite different about topic {i}." for i in range(5))


@pytest_asyncio.fixture
async def db() -> InMemoryVectorDBService:
    service = InMemoryVectorDBService()
    await service.create_collection(COLL, CollectionConfig(embedding_size=2))
    return service


@pytest.fixture
def manager(monkeypatch: pytest.MonkeyPatch) -> FakeLeases:
    fake = FakeLeases()
    leases = RecordLeases()
    leases.bind(fake)
    monkeypatch.setattr(VectorStore, "vrid_leases", leases)
    monkeypatch.setattr(leases_mod, "_POLL_S", 0.01)
    monkeypatch.setattr(leases_mod, "_MIN_RENEW_S", 0.05)
    monkeypatch.setattr(leases_mod, "messaging_env", SimpleNamespace(concurrency_lease_seconds=0.15, record_lease_wait_seconds=5.0))
    monkeypatch.setattr("app.modules.transformers.vectorstore.messaging_env", SimpleNamespace(record_lease_wait_seconds=5.0))
    return fake


def _apply_args(record_id: str):  # noqa: ANN202
    container = parse(DOC)
    record = record_of(container)
    record.id = record_id
    return context_of(record, EventTypes.NEW_RECORD.value)


async def test_two_records_of_one_virtual_record_do_not_sweep_and_write_at_the_same_time(db, manager) -> None:
    running, overlap = {"n": 0}, {"max": 0}
    stores = [make_store(db), make_store(db)]
    for store in stores:
        real = store._sweep_stray_points

        async def slow(record, revision, real=real) -> None:
            running["n"] += 1
            overlap["max"] = max(overlap["max"], running["n"])
            await asyncio.sleep(0.1)
            await real(record, revision)
            running["n"] -= 1

        store._sweep_stray_points = slow
    await asyncio.gather(stores[0].apply(_apply_args("rec-A")), stores[1].apply(_apply_args("rec-B")))
    assert overlap["max"] == 1, "the second record waited for the first: one lease on the virtual record"
    assert manager.owner == {}, "released"


async def test_without_the_lease_the_same_two_records_overlap(db, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(VectorStore, "vrid_leases", None)
    running, overlap = {"n": 0}, {"max": 0}
    stores = [make_store(db), make_store(db)]
    for store in stores:
        real = store._sweep_stray_points

        async def slow(record, revision, real=real) -> None:
            running["n"] += 1
            overlap["max"] = max(overlap["max"], running["n"])
            await asyncio.sleep(0.1)
            await real(record, revision)
            running["n"] -= 1

        store._sweep_stray_points = slow
    await asyncio.gather(stores[0].apply(_apply_args("rec-A")), stores[1].apply(_apply_args("rec-B")))
    assert overlap["max"] == 2


async def test_a_run_whose_lease_lapsed_stops_before_its_next_delete(db, manager) -> None:
    store = make_store(db)
    real = store._sweep_stray_points
    reached: list[str] = []

    async def slow(record, revision) -> None:
        manager.renews = False          # the next renewal says the lease is gone
        await asyncio.sleep(0.5)        # the sweep is still under way when the loss is noticed
        reached.append("the sweep went on after the lease was lost")
        await real(record, revision)

    store._sweep_stray_points = slow
    with pytest.raises(VirtualRecordBusy) as raised:
        await store.apply(_apply_args("rec-A"))
    assert isinstance(raised.value.__cause__, LeaseLost)
    assert reached == [], "cancelled at the next await: nothing is deleted after the loss"


async def test_a_record_that_waits_past_the_limit_is_handed_back_not_dead_lettered(db, manager, monkeypatch: pytest.MonkeyPatch) -> None:
    """The wait for a sibling record of the same virtual record ends in a hand-back with no attempt counted: the format processors
    re-raise an IndexingError and wrap anything else as a terminal DocumentProcessingError, so it is one, and a RequeueWithoutAttempt."""
    monkeypatch.setattr("app.modules.transformers.vectorstore.messaging_env", SimpleNamespace(record_lease_wait_seconds=0.1))
    monkeypatch.setattr(leases_mod, "messaging_env", SimpleNamespace(concurrency_lease_seconds=5.0, record_lease_wait_seconds=0.1))
    manager.owner[f"record:vrid:{ORG}:{VRID}"] = "a sibling still writing"
    store = make_store(db)
    with pytest.raises(VirtualRecordBusy) as raised:
        await store.apply(_apply_args("rec-B"))
    assert isinstance(raised.value, IndexingError) and isinstance(raised.value, RequeueWithoutAttempt)
    assert raised.value.counts_toward_backstop is False and raised.value.pauses_consumer is False
    assert MessageErrorClassifier.classify_by_exception(raised.value) != MessageErrorType.TERMINAL


async def test_a_run_that_lost_the_lease_deletes_nothing_in_the_sweep(db, manager) -> None:
    store = make_store(db)
    real = store._sweep_stray_points

    async def lapsed(record, revision) -> None:
        await db.upsert_points(COLL, [VectorPoint(id="f" * 8 + "-0000-4000-8000-" + "0" * 12, dense_vector=[1.0, 1.0], payload={
            "page_content": "kept by the newer run", "metadata": {"orgId": ORG, "virtualRecordId": VRID, "blockId": "newer-runs-block"}})])
        manager.take_away()
        await real(record, revision)

    store._sweep_stray_points = lapsed
    with pytest.raises(VirtualRecordBusy):
        await store.apply(_apply_args("rec-A"))
    assert "newer-runs-block" in {p.payload["metadata"]["blockId"] for p in db.collections[COLL].values()}


@pytest.mark.parametrize("delete", ["blocks", "embeddings"])
async def test_a_run_that_lost_the_lease_does_not_delete_replaced_blocks_or_embeddings(db, manager, delete: str) -> None:
    """The reconcile's and the re-key's deletes confirm the lease too: they all go through these two methods."""
    store = make_store(db)
    deleted: list[str] = []
    lost_at: list[int] = []
    real_delete = db.delete_points

    async def spy(*args: object, **kwargs: object) -> None:
        deleted.append("delete")
        await real_delete(*args, **kwargs)

    async def lapsed(record, revision) -> None:
        lost_at.append(len(deleted))
        manager.take_away()
        if delete == "blocks":
            await store.delete_blocks_by_ids({"some-block"}, VRID, COLL, org_id=ORG)
        else:
            await store.delete_embeddings(VRID, COLL, org_id=ORG)

    store._sweep_stray_points = lapsed
    db.delete_points = spy  # type: ignore[method-assign]
    with pytest.raises(VirtualRecordBusy):
        await store.apply(_apply_args("rec-A"))
    assert len(deleted) == lost_at[0], "no delete went out after the lease was lost"


async def test_a_lost_lease_inside_the_reconcile_is_a_hand_back_not_a_failed_attempt(db, manager) -> None:
    """The first delete of a full replace runs inside index_documents, whose catch-all wraps unexpected errors as IndexingError: a lost
    lease must still come out as VirtualRecordBusy (a hand-back), not as a failure counted against the record."""
    store = make_store(db)
    real = store._apply_locked

    async def lapsed(ctx) -> object:
        manager.take_away()
        return await real(ctx)

    store._apply_locked = lapsed
    ctx = _apply_args("rec-A")
    ctx.event_type = EventTypes.UPDATE_RECORD.value
    with pytest.raises(VirtualRecordBusy):
        await store.apply(ctx)
