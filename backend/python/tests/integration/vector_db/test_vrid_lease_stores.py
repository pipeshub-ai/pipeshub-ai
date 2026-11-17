"""A run whose lease on the virtual record lapsed deletes nothing, on real stores (a newer run kept a point the old one lists as stray).

Requires: docker compose -f tests/integration/compose/vector-db.yml up -d
"""
from __future__ import annotations

import contextlib
from types import SimpleNamespace

import pytest

from app.modules.pipeline import leases as leases_mod
from app.modules.pipeline.leases import RecordLeases
from app.modules.transformers.vectorstore import VectorStore, VirtualRecordBusy
from app.services.vector_db.models import VectorPoint
from tests.integration.vector_db.conftest import make_collection
from tests.integration.vector_db.helpers import (
    DIM,
    make_collection_config,
    make_dense,
    point_id,
)
from tests.support.index_world import (
    ORG,
    VRID,
    context_of,
    make_store,
    parse,
    record_of,
)
from tests.support.lease_fakes import FakeLeases

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="module"), pytest.mark.timeout(600)]


@pytest.fixture(params=["qdrant", "redis", "opensearch"])
def svc(request: pytest.FixtureRequest):  # noqa: ANN201
    name = {"qdrant": "qdrant_service", "redis": "redis_service", "opensearch": "opensearch_service"}[request.param]
    return request.getfixturevalue(name)


async def test_a_run_that_lost_the_lease_does_not_sweep_away_the_newer_runs_point(svc, monkeypatch: pytest.MonkeyPatch) -> None:
    manager = FakeLeases()
    leases = RecordLeases()
    leases.bind(manager)
    monkeypatch.setattr(VectorStore, "vrid_leases", leases)
    monkeypatch.setattr(leases_mod, "messaging_env", SimpleNamespace(concurrency_lease_seconds=30.0, record_lease_wait_seconds=20.0))
    monkeypatch.setattr("app.modules.transformers.vectorstore.messaging_env", SimpleNamespace(record_lease_wait_seconds=20.0))
    col = make_collection("vrid_lease")
    await svc.create_collection(col, make_collection_config())
    for field in ("metadata.blockId", "metadata.virtualRecordId", "metadata.orgId"):
        with contextlib.suppress(Exception):
            await svc.create_index(col, field, {"type": "keyword"})
    try:
        store = make_store(svc, dim=DIM, collection=col)
        real = store._sweep_stray_points

        async def lapsed(record, revision) -> None:
            await svc.upsert_points(col, [VectorPoint(id=point_id("newer"), dense_vector=make_dense([1.0]), payload={
                "page_content": "kept by the newer run", "metadata": {"orgId": ORG, "virtualRecordId": VRID, "blockId": "newer-block"}})])
            await svc.make_searchable(col)
            manager.take_away()
            await real(record, revision)

        store._sweep_stray_points = lapsed
        with pytest.raises(VirtualRecordBusy):
            await store.apply(context_of(record_of(parse("A paragraph that is long enough to be a point of its own.")), "newRecord"))
        await svc.make_searchable(col)
        flt = await svc.filter_collection(must={"orgId": ORG, "virtualRecordId": VRID, "blockId": ["newer-block"]})
        assert await svc.count_points(col, flt) == 1
    finally:
        await svc.delete_collection(col)
