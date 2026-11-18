"""A blank record or block id in a filter is refused, not dropped, on real Redis and Qdrant (and nothing is deleted).

Requires: docker compose -f deployment/docker-compose/docker-compose.integration.vector-db.yml up -d
Run: pytest tests/integration/vector_db/test_blank_filter_real_stores.py -m integration --timeout=120

``build_generic_conditions`` skipped a blank value, so ``delete_embeddings("")`` became "delete every point of the org".
"""

import asyncio
import logging
from collections.abc import AsyncIterator
from unittest.mock import AsyncMock

import pytest

from app.exceptions.indexing_exceptions import VectorStoreError
from app.modules.transformers.vectorstore import VectorStore
from app.services.vector_db.const.const import PAYLOAD_KEYWORD_INDEXES
from app.services.vector_db.filters import BlankFilterValue
from app.services.vector_db.models import VectorPoint
from tests.integration.vector_db.conftest import make_collection
from tests.integration.vector_db.helpers import (
    make_collection_config,
    make_dense,
    point_id,
)
from tests.support.vector_db import make_collection_registry

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="module")]

ORG = "5f0c1d2e3a4b5c6d7e8f9a0b"
POINTS = [
    VectorPoint(
        id=point_id(f"blank{n}"),
        dense_vector=make_dense([1.0 if n == i else 0.0 for i in range(8)]),
        payload={"page_content": f"text {n}", "metadata": {"orgId": ORG, "virtualRecordId": f"vr-{n % 2}", "blockId": f"b{n}"}},
    )
    for n in range(4)
]


@pytest.fixture(params=["redis", "qdrant"])
def store(request) -> tuple:
    """The param's own store only: each backend job starts one store and selects its params with -k, so the other is never requested."""
    return request.param, request.getfixturevalue(f"{request.param}_service")


@pytest.fixture
async def seeded(store) -> AsyncIterator[tuple]:
    kind, svc = store
    name = make_collection("blank")
    await svc.create_collection(name, make_collection_config())
    for field, schema in PAYLOAD_KEYWORD_INDEXES:
        await svc.create_index(name, field, schema)
    await svc.upsert_points(name, POINTS)
    for _ in range(100):
        if (await svc.get_collection_info(name)).points_count == len(POINTS):
            break
        await asyncio.sleep(0.1)
    yield svc, name
    await svc.delete_collection(name)


async def _total(svc, name: str) -> int:
    return await svc.count_points(name, await svc.filter_collection(must={"orgId": ORG}))


def _store(svc, name: str) -> VectorStore:
    return VectorStore(logger=logging.getLogger("blank-filter-it"), config_service=AsyncMock(), graph_provider=AsyncMock(),
                       collection_registry=make_collection_registry(name), vector_db_service=svc)


@pytest.mark.parametrize("blank", ["", "   ", None])
async def test_delete_embeddings_of_a_blank_record_deletes_nothing(seeded, blank) -> None:
    svc, name = seeded
    with pytest.raises(VectorStoreError) as raised:
        await _store(svc, name).delete_embeddings(blank, name, org_id=ORG)
    assert isinstance(raised.value.__cause__, BlankFilterValue)
    assert await _total(svc, name) == len(POINTS)


async def test_delete_blocks_of_a_blank_record_deletes_nothing(seeded) -> None:
    svc, name = seeded
    with pytest.raises(VectorStoreError):
        await _store(svc, name).delete_blocks_by_ids({"b0"}, "", name, org_id=ORG)
    assert await _total(svc, name) == len(POINTS)


async def test_a_blank_block_id_is_refused_in_a_filter(seeded) -> None:
    svc, name = seeded
    with pytest.raises(BlankFilterValue):
        await svc.filter_collection(must={"orgId": ORG, "virtualRecordId": "vr-0", "blockId": ""})


async def test_the_record_that_was_named_is_still_deletable(seeded) -> None:
    svc, name = seeded
    await _store(svc, name).delete_embeddings("vr-0", name, org_id=ORG)
    for _ in range(50):
        if await _total(svc, name) == 2:
            break
        await asyncio.sleep(0.1)
    assert await _total(svc, name) == 2
    by_record = [await svc.count_points(name, await svc.filter_collection(must={"orgId": ORG, "virtualRecordId": vr})) for vr in ("vr-0", "vr-1")]
    assert by_record == [0, 2]
