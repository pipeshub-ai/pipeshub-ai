"""The weight-precision pin and its tag filters on Qdrant, OpenSearch and Redis.

Run: pytest tests/integration/vector_db/test_precision_pin_backends.py -m integration -k qdrant (or opensearch, redis)
The backend is a parameter, so a job that runs one backend deselects the others; a backend that cannot be reached
makes the test fail in the jobs that run it.
"""
import pytest

from app.services.vector_db.const.const import PAYLOAD_KEYWORD_INDEXES
from app.services.vector_db.models import CollectionConfig
from tests.integration.vector_db.conftest import make_collection
from tests.integration.vector_db.helpers import DIM
from tests.integration.vector_db.precision_pin_scenario import (
    run_legacy_scenario,
    run_pin_scenario,
)

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="module")]
ORG = "org-precision-pin"


@pytest.fixture(params=["qdrant", "opensearch", "redis"])
def backend(request):  # noqa: ANN201
    """The service of the backend this case is for (indirect: only that backend is connected)."""
    return request.getfixturevalue(f"{request.param}_service"), request.param


async def _collection(service, prefix: str) -> str:
    name = make_collection(prefix)
    await service.create_collection(name, CollectionConfig(embedding_size=DIM))
    for field, schema in PAYLOAD_KEYWORD_INDEXES:  # what the collection registry creates on every collection
        await service.create_index(collection_name=name, field_name=field, field_schema=schema)
    return name


async def test_the_pin_is_claimed_refused_released_and_the_tags_filter(backend) -> None:
    service, label = backend
    collection = await _collection(service, f"pin_{label}")
    try:
        await run_pin_scenario(service, collection, ORG)
    finally:
        await service.delete_collection(collection)


async def test_an_index_with_untagged_points_is_fp32(backend) -> None:
    service, label = backend
    collection = await _collection(service, f"legacy_{label}")
    try:
        await run_legacy_scenario(service, collection, ORG)
    finally:
        await service.delete_collection(collection)
