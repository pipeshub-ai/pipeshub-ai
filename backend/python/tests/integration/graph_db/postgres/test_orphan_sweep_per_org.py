"""The orphan mapping sweep on PostgreSQL, where each org has its own mapping row for a shared virtual record id.

Two orgs each have a mapping row for one id and no record left on it, and a third org still holds points on it. Each org's sweep deletes
its own points and keeps its own row while the third org holds the id. The back-off one org's kept row gets does not stop another org's
row from being swept, or keep it waiting after its own back-off runs out. Once the third org's points are gone, each org's row is
released on its own sweep after its own back-off.

The graph is the real PostgreSQL provider. The vector store is the in-memory one, which answers the hold check the way the scroll does
on Qdrant, OpenSearch and Redis. The same case on the pgvector store, which answers it with ``retrieval.vrid_held_elsewhere``, is
``test_the_orphan_sweep_keeps_and_releases_each_orgs_mapping_row`` in ``tests/integration/vector_db/test_pgvector_provider_core.py``.

Needs a migrated database: PG_CORE_URL (a `pipeshub_connectors_login` DSN), PG_POOLER=none.
"""
from __future__ import annotations

import logging
import uuid
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock

import pytest

import app.indexing_main as m
from app.config.constants.arangodb import CollectionNames
from app.modules.indexing.run import IndexingPipeline
from app.services.vector_db import membership
from app.services.vector_db.collection_locator import StaticCollectionLocator
from app.services.vector_db.models import VectorPoint
from tests.support.in_memory_vector_db import InMemoryVectorDBService
from tests.support.vector_db import make_collection_registry

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

pytestmark = [pytest.mark.integration, pytest.mark.timeout(120)]

logger = logging.getLogger("pg-orphan-sweep")
MAPPING = CollectionNames.VIRTUAL_RECORD_TO_DOC_ID_MAPPING.value
COLL = "records"


def _point(org: str, vrid: str) -> VectorPoint:
    return VectorPoint(id=f"p-{org}", dense_vector=[1.0], payload={"metadata": {"orgId": org, "virtualRecordId": vrid}})


@pytest.fixture
async def world(pg_provider, pg_db, org_ctx, monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[dict]:
    monkeypatch.setattr(membership, "EMPTY_CONFIRM_DELAY_SECONDS", 0)
    monkeypatch.setattr(membership, "_shared_row_kept", set())
    monkeypatch.setattr(m, "_orphan_sweep_cursor", {})
    monkeypatch.setattr(m, "_orphan_kept_until", {})
    o1, o2, holder = (uuid.uuid4().hex[:24] for _ in range(3))
    vrid = f"vr-{uuid.uuid4().hex[:12]}"
    vdb = InMemoryVectorDBService()
    vdb.collections[COLL] = {p.id: p for p in (_point(o1, vrid), _point(o2, vrid), _point(holder, vrid))}
    deletes: list[str] = []
    delete_points = vdb.delete_points

    async def spy(**kwargs) -> None:
        deletes.append(kwargs["collection_name"])
        await delete_points(**kwargs)

    vdb.delete_points = spy
    pipeline = IndexingPipeline(
        logger=logger, config_service=AsyncMock(), graph_provider=pg_provider,
        collection_registry=make_collection_registry(), vector_db_service=vdb,
    )
    pipeline.collection_locator = StaticCollectionLocator([COLL])
    try:
        for org in (o1, o2):
            with org_ctx(org):
                assert await pg_provider.create_node_if_absent(
                    vrid, MAPPING, {"id": vrid, "orgId": org, "virtualRecordId": vrid, "documentId": f"doc-{org}"},
                )

        async def sweep(org: str) -> int:
            with org_ctx(org):
                return await m._sweep_orphaned_virtual_record_mappings(
                    graph_provider=pg_provider, pipeline=pipeline, logger=logger, page_size=100, org_id=org,
                )

        async def row(org: str) -> dict | None:
            with org_ctx(org):
                return await pg_provider.get_document(vrid, MAPPING)

        yield {"o1": o1, "o2": o2, "holder": holder, "vrid": vrid, "vdb": vdb, "deletes": deletes, "sweep": sweep, "row": row}
    finally:
        for org in (o1, o2):
            with org_ctx(org):
                await pg_db.run(lambda tx, org=org: tx.execute("DELETE FROM graph.nodes WHERE org_id = $1", org), write=True)


async def test_each_orgs_row_is_kept_and_released_on_its_own(world: dict) -> None:
    o1, o2, vrid, points = world["o1"], world["o2"], world["vrid"], world["vdb"].collections[COLL]

    assert await world["sweep"](o1) == 0
    assert await world["sweep"](o2) == 0, "o1's kept row held back o2's"
    assert set(points) == {f"p-{world['holder']}"}
    assert await world["row"](o1) is not None and await world["row"](o2) is not None
    assert set(m._orphan_kept_until) == {(o1, vrid), (o2, vrid)}
    deletes = len(world["deletes"])

    assert [await world["sweep"](o1), await world["sweep"](o2)] == [0, 0]
    assert len(world["deletes"]) == deletes, "a backed-off row was swept again"

    del points[f"p-{world['holder']}"]
    m._orphan_kept_until[(o1, vrid)] = 0.0  # o1's back-off ran out, o2's did not
    assert await world["sweep"](o1) == 1
    assert await world["row"](o1) is None
    assert await world["sweep"](o2) == 0
    assert await world["row"](o2) is not None, "o1's recheck released o2's row"

    m._orphan_kept_until[(o2, vrid)] = 0.0
    assert await world["sweep"](o2) == 1
    assert await world["row"](o2) is None
    assert m._orphan_kept_until == {}
