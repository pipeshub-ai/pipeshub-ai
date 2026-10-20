"""Downloading a synced object through the S3 and MinIO connectors.

Sync stores the key exactly as the listing returns it (botocore decodes the
listing). A signed URL must name that same key, or the GET misses the object.
"""

import pytest
from object_store_behaviour_fakes import (
    FakeCheckpointStore,
    FakeConfigService,
    FakeObjectStore,
    FakeRecordsDb,
    make_connector,
)

from app.connectors.core.base.connector.connector_service import BaseConnector

KEYS = ["plain.txt", "reports/100%25.pdf", "a%20b.pdf", "a b+c.pdf", "50% off.txt"]


@pytest.fixture(params=["s3", "minio"])
def connector(
    request: pytest.FixtureRequest, store: FakeObjectStore, db: FakeRecordsDb,
    checkpoints: FakeCheckpointStore, config: FakeConfigService,
) -> BaseConnector:
    return make_connector(request.param, store, db, checkpoints, config)


class TestSignedUrl:
    @pytest.mark.asyncio
    @pytest.mark.parametrize("key", KEYS)
    async def test_the_signed_url_fetches_the_synced_object(self, connector, store, db, key) -> None:
        store.put(key)
        await connector.run_sync()
        record = db.by_path()[f"bucket-1/{key}"]

        url = await connector.get_signed_url(record)

        fetched = connector.data_source.object_at(url)
        assert fetched is not None and fetched.key == key
