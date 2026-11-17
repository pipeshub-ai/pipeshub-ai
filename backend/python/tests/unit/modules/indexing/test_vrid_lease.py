"""Records that share a virtual record serialise their vector writes and stray sweep, and a run whose lease lapsed stops."""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest
import pytest_asyncio

from app.config.constants.arangodb import EventTypes
from app.modules.pipeline import leases as leases_mod
from app.modules.pipeline.leases import LeaseLost, RecordLeases
from app.modules.transformers.vectorstore import VectorStore
from app.services.distributed.interface import IDistributedLeaseManager
from app.services.vector_db.models import CollectionConfig
from tests.support.in_memory_vector_db import InMemoryVectorDBService
from tests.support.index_world import COLL, context_of, make_store, parse, record_of

DOC = "\n\n".join(f"Paragraph {i} says something quite different about topic {i}." for i in range(5))


class _Leases(IDistributedLeaseManager):
    """One owner per pool; renewal can be made to fail."""

    def __init__(self) -> None:
        self.owner: dict[str, str] = {}
        self.renews = True

    async def initialize(self) -> None: ...
    async def cleanup(self) -> None: ...

    async def try_acquire(self, pool: str, owner: str, limit: int, lease_seconds: float) -> bool:
        if pool in self.owner:
            return False
        self.owner[pool] = owner
        return True

    async def renew(self, pool: str, owner: str, lease_seconds: float) -> bool:
        return self.renews and self.owner.get(pool) == owner

    async def renew_many(self, leases, lease_seconds: float) -> dict:
        return dict.fromkeys(leases, self.renews)

    async def release(self, pool: str, owner: str) -> None:
        if self.owner.get(pool) == owner:
            del self.owner[pool]


@pytest_asyncio.fixture
async def db() -> InMemoryVectorDBService:
    service = InMemoryVectorDBService()
    await service.create_collection(COLL, CollectionConfig(embedding_size=2))
    return service


@pytest.fixture
def manager(monkeypatch: pytest.MonkeyPatch) -> _Leases:
    fake = _Leases()
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
    with pytest.raises(LeaseLost):
        await store.apply(_apply_args("rec-A"))
    assert reached == [], "cancelled at the next await: nothing is deleted after the loss"
