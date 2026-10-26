"""PP-120: S3 and MinIO notifications name the connector instance, so two connections can be told apart."""

from unittest.mock import AsyncMock

import pytest
from object_store_behaviour_fakes import (
    FakeCheckpointStore,
    FakeConfigService,
    FakeObjectStore,
    FakeRecordsDb,
    Response,
    make_connector,
)


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["s3", "minio"])
async def test_failed_bucket_list_is_titled_with_the_instance_name(kind: str) -> None:
    connector = make_connector(kind, FakeObjectStore(), FakeRecordsDb(), FakeCheckpointStore(), FakeConfigService())
    connector.connector_instance_name = "Finance Storage"
    connector.bucket_name = None
    connector.data_source.list_buckets = AsyncMock(return_value=Response(False, error="AccessDenied: Access Denied"))
    connector.notify = AsyncMock()

    # A failed ListBuckets fails the sync (PP-117).
    with pytest.raises(ConnectionError):
        await connector.run_sync()

    sent = connector.notify.await_args.kwargs
    assert sent["title"].startswith("Finance Storage")
    if kind == "minio":
        assert "S3" not in sent["title"] and "S3" not in sent["message"]


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["s3", "minio"])
async def test_access_denied_listing_is_titled_with_the_instance_name(kind: str) -> None:
    store = FakeObjectStore()
    store.put("a.txt")
    connector = make_connector(kind, store, FakeRecordsDb(), FakeCheckpointStore(), FakeConfigService())
    connector.connector_instance_name = "Finance Storage"
    connector.data_source.list_objects_v2 = AsyncMock(return_value=Response(False, error="AccessDenied: Access Denied"))
    connector.notify = AsyncMock()

    await connector.run_sync()

    assert connector.notify.await_args.kwargs["title"].startswith("Finance Storage")
