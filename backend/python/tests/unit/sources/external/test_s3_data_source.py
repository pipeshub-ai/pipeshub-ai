"""S3DataSource and MinIODataSource: error status and client reuse."""

from collections.abc import AsyncIterator
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from unittest.mock import MagicMock

import pytest
from botocore.exceptions import ClientError

from app.sources.external.minio.minio import MinIODataSource
from app.sources.external.s3.s3 import S3DataSource


class _FakeBotoClient:
    def __init__(self, error_status: int | None = None) -> None:
        self.error_status = error_status

    async def list_objects_v2(self, **kwargs: object) -> dict:
        if self.error_status:
            raise ClientError(
                {"Error": {"Code": "AccessDenied", "Message": "Access Denied"},
                 "ResponseMetadata": {"HTTPStatusCode": self.error_status}},
                "ListObjectsV2",
            )
        return {"Contents": []}


class _FakeSession:
    def __init__(self, error_status: int | None = None) -> None:
        self.error_status = error_status
        self.opened = 0

    @asynccontextmanager
    async def _client(self) -> AsyncIterator[_FakeBotoClient]:
        self.opened += 1
        yield _FakeBotoClient(self.error_status)

    def client(self, *args: object, **kwargs: object) -> AbstractAsyncContextManager[_FakeBotoClient]:
        return self._client()


def _sources(session: _FakeSession) -> list:
    s3_client = MagicMock()
    s3_client.get_session.return_value = session
    minio_client = MagicMock()
    minio_client.get_session.return_value = session
    minio_client.get_credentials.return_value = {"endpoint_url": "http://minio:9000"}
    return [S3DataSource(s3_client), MinIODataSource(minio_client)]


@pytest.mark.asyncio
@pytest.mark.parametrize("index", [0, 1], ids=["s3", "minio"])
async def test_client_error_carries_http_status(index: int) -> None:
    source = _sources(_FakeSession(error_status=403))[index]
    response = await source.list_objects_v2(Bucket="b", MaxKeys=1)
    assert response.success is False
    assert response.status_code == 403
    assert response.error.startswith("AccessDenied")
