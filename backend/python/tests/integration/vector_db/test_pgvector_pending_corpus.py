"""The corpus leak table (`tests/permissions/test_pending_corpus.py`) against real pgvector rows: a structural change is half done (the old
stamps are in the index, the viewer's scopes come from the new world) and the SQL rule must admit nothing of the changed subtree and nothing
the new world denies. Each scenario is its own organisation.

Run: KERNEL_TEST_PG_BINS=<pg bin> KERNEL_TEST_PGBOUNCER=<pgbouncer> pytest tests/integration/vector_db/test_pgvector_pending_corpus.py -m integration
"""
from __future__ import annotations

import hashlib
from typing import TYPE_CHECKING

import pytest

from app.services.vector_db.models import (
    FieldCondition,
    FilterExpression,
    HybridSearchRequest,
    VectorPoint,
)
from app.services.vector_db.pgvector.config import PgVectorConfig
from app.services.vector_db.pgvector.pgvector import PgVectorService
from tests.integration.db.conftest import cluster, env  # noqa: F401  (fixtures)
from tests.permissions.scope_corpus import SCENARIOS, build, leaks

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

pytestmark = [pytest.mark.integration, pytest.mark.timeout(600)]
Q = [1.0, 0.0, 0.0, 0.0]


@pytest.fixture
async def store(env) -> AsyncIterator[PgVectorService]:  # noqa: F811
    port = env.start_bouncer()
    svc = await PgVectorService.create(PgVectorConfig(dsn=env.dsn("indexing", port), jobs_dsn=env.dsn("jobs", port), pool_max=4, placement="none"))
    yield svc
    await svc.disconnect()


@pytest.mark.parametrize("scenario", SCENARIOS, ids=[s.id for s in SCENARIOS])
async def test_a_half_done_change_admits_nothing_of_its_subtree_and_nothing_the_new_world_denies(store, scenario) -> None:
    corpus = await build(scenario)
    org = hashlib.sha1(scenario.id.encode()).hexdigest()[:24]
    points = [VectorPoint(id=hashlib.md5(f"{scenario.id}/{rid}".encode()).hexdigest(), dense_vector=[1.0, i / 100, 0.0, 0.0],
                          payload={"page_content": rid, "metadata": {"orgId": org, "virtualRecordId": rid, "blockId": rid, "embeddingModel": "p:m:4"}, **payload})
              for i, (rid, payload) in enumerate(corpus.points.items())]
    await store.upsert_points("records", points)
    admitted: dict[str, set[str]] = {}
    for viewer, (scopes, _) in corpus.viewers.items():
        flt = FilterExpression(must=[FieldCondition(key="metadata.orgId", value=org)])
        req = HybridSearchRequest(dense_query=Q, filter=flt, limit=1000, scope_filter=corpus.scope_filter(scopes))
        [hits] = await store.query_nearest_points("records", [req])
        admitted[viewer] = {h.payload["metadata"]["virtualRecordId"] for h in hits}
    assert leaks(corpus, admitted) == []
