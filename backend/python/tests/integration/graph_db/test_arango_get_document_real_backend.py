"""A strict get_document against a real ArangoDB: an absent document is None, a missing collection raises.

ArangoDB answers both with HTTP 404 and tells them apart only by errorNum (1202 document, 1203 collection), so
the check is on the real engine. Run against a throwaway engine:

  docker run -d --rm --name arango-ensure-it -p 18529:8529 \\
      -e ARANGO_ROOT_PASSWORD=ensure-it-pass arangodb:3.12
"""

import logging
import os
import uuid
from collections.abc import AsyncIterator
from unittest.mock import AsyncMock

import pytest

from app.config.constants.arangodb import CollectionNames
from app.exceptions.graph_db_exceptions import GraphQueryError
from app.services.graph_db.arango.arango_http_provider import ArangoHTTPProvider
from tests.support import it_backends

pytestmark = pytest.mark.integration

ARANGO_PASSWORD = os.environ.get("ARANGO_IT_PASSWORD", "ensure-it-pass")
COLLECTION = CollectionNames.TOPICS.value


@pytest.fixture
async def arango() -> AsyncIterator[ArangoHTTPProvider]:
    url = it_backends.endpoint("ARANGO_IT_URL")
    config = AsyncMock()
    config.get_config = AsyncMock(return_value={
        "url": url, "username": "root", "password": ARANGO_PASSWORD, "db": "ensure_it",
    })
    provider = ArangoHTTPProvider(logging.getLogger("arango-get-document-it"), config)
    if not await provider.connect():
        it_backends.unavailable("ARANGO_IT_URL", f"ArangoDB not reachable at {url}")
    assert provider.http_client is not None
    if not await provider.http_client.has_collection(COLLECTION):
        await provider.http_client.create_collection(COLLECTION, edge=False, schema=None)
    yield provider
    await provider.disconnect()


class TestArangoStrictGetDocument:
    # "" would be the path `topics/` (404, errorNum 1203 like a missing collection); "%" is 400, errorNum 1205.
    @pytest.mark.parametrize("key", ["absent", "", "%"], ids=["absent_key", "empty_key", "illegal_key"])
    async def test_an_absent_document_is_none(self, arango: ArangoHTTPProvider, key: str) -> None:
        key = f"{key}-{uuid.uuid4()}" if key == "absent" else key
        assert await arango.get_document(key, COLLECTION, raise_on_error=True) is None

    async def test_a_missing_collection_raises(self, arango: ArangoHTTPProvider) -> None:
        missing = f"no_such_{uuid.uuid4().hex}"

        with pytest.raises(GraphQueryError, match="ArangoDB answered 404"):
            await arango.get_document("k", missing, raise_on_error=True)
        assert await arango.get_document("k", missing) is None  # without the flag, as before
