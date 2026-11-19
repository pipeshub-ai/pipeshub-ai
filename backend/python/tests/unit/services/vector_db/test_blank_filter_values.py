"""A filter that names a record or a block with no value is refused by every store, never widened.

``build_generic_conditions`` used to skip a blank value, so ``delete_embeddings("")`` became "delete every point of the org".
"""

import logging
from unittest.mock import AsyncMock

import pytest

from app.exceptions.indexing_exceptions import VectorStoreError
from app.modules.transformers.vectorstore import VectorStore
from app.services.vector_db.filters import REQUIRED_VALUE_KEYS, BlankFilterValue
from app.services.vector_db.pgvector.pgvector import PgVectorService
from tests.support.vector_db import make_collection_registry

from .test_vector_db_contract import PROVIDER_FACTORIES

ORG = "5f0c1d2e3a4b5c6d7e8f9a0b"
BLANKS = [None, "", "   ", "\t\n"]


@pytest.fixture(params=[*PROVIDER_FACTORIES, "pgvector"])
def filter_collection(request) -> object:
    """Each store's own ``filter_collection`` (the pgvector one uses no connection)."""
    if request.param == "pgvector":
        return lambda **kw: PgVectorService.filter_collection(object(), **kw)
    return PROVIDER_FACTORIES[request.param]().filter_collection


@pytest.mark.parametrize("key", sorted(REQUIRED_VALUE_KEYS))
@pytest.mark.parametrize("blank", BLANKS)
@pytest.mark.parametrize("clause", ["must", "should", "must_not"])
async def test_a_blank_identifying_value_raises_in_every_clause(filter_collection, key, blank, clause) -> None:
    kwargs = {"must": {"orgId": ORG}, clause: {key: blank}} if clause != "must" else {"must": {"orgId": ORG, key: blank}}
    with pytest.raises(BlankFilterValue):
        await filter_collection(**kwargs)


async def test_the_key_may_come_prefixed(filter_collection) -> None:
    with pytest.raises(BlankFilterValue):
        await filter_collection(must={"orgId": ORG, "metadata.virtualRecordId": ""})


async def test_a_blank_under_a_longer_path_that_ends_in_an_identifying_name_is_not_refused(filter_collection) -> None:
    flt = await filter_collection(must={"orgId": ORG, "metadata.source.blockId": None})
    assert {c.key for c in flt.must} == {"metadata.orgId"}


async def test_a_real_value_and_an_unrelated_blank_still_build(filter_collection) -> None:
    flt = await filter_collection(must={"orgId": ORG, "virtualRecordId": "vr-1", "connectorId": ""})
    assert {c.key for c in flt.must} == {"metadata.orgId", "metadata.virtualRecordId"}


async def test_an_empty_list_still_matches_nothing(filter_collection) -> None:
    flt = await filter_collection(must={"orgId": ORG, "virtualRecordId": []})
    assert [c.values for c in flt.must if c.key == "metadata.virtualRecordId"] == [[]]


@pytest.mark.parametrize("store", list(PROVIDER_FACTORIES))
@pytest.mark.parametrize("blank", ["", "  ", None])
async def test_delete_embeddings_with_a_blank_record_deletes_nothing(store, blank) -> None:
    svc = PROVIDER_FACTORIES[store]()
    svc.delete_points = AsyncMock()
    vs = VectorStore(logger=logging.getLogger("t"), config_service=AsyncMock(), graph_provider=AsyncMock(),
                     collection_registry=make_collection_registry("records"), vector_db_service=svc)
    with pytest.raises(VectorStoreError):
        await vs.delete_embeddings(blank, "records", org_id=ORG)
    svc.delete_points.assert_not_awaited()


@pytest.mark.parametrize("store", list(PROVIDER_FACTORIES))
async def test_delete_blocks_with_a_blank_record_deletes_nothing(store) -> None:
    svc = PROVIDER_FACTORIES[store]()
    svc.delete_points = AsyncMock()
    vs = VectorStore(logger=logging.getLogger("t"), config_service=AsyncMock(), graph_provider=AsyncMock(),
                     collection_registry=make_collection_registry("records"), vector_db_service=svc)
    with pytest.raises(VectorStoreError):
        await vs.delete_blocks_by_ids({"b1"}, "", "records", org_id=ORG)
    svc.delete_points.assert_not_awaited()
