"""The connection test of S3 and MinIO when the key may read only some of the chosen folders.

A MinIO or IAM policy often grants s3:GetObject per folder (``bucket/team-a/*``). Seen live on
MinIO: with the Folders filter naming a readable and an unreadable folder, the test probed only
the first and refused the key, so the readable folder never synced.
"""

import logging
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.connectors.core.base.connector.connector_service import ConnectorInitError
from app.connectors.core.registry.filters import (
    Filter,
    FilterCollection,
    FilterType,
    ListOperator,
    MultiselectOperator,
)
from app.connectors.sources.minio.connector import MinIOConnector
from app.connectors.sources.s3.connector import S3Connector
from app.sources.client.s3.s3 import S3Response

BUCKET = "shared"
OBJECTS = ["a-private/payroll.txt", "b-public/handbook.txt", "c-public/notes.txt"]
READABLE = ("b-public/", "c-public/")


def _sync_filters(folders: list[str] | None) -> FilterCollection:
    filters = [Filter(key="buckets", type=FilterType.MULTISELECT, operator=MultiselectOperator.IN, value=[BUCKET])]
    if folders is not None:
        filters.append(Filter(key="folder_paths", type=FilterType.LIST, operator=ListOperator.IN, value=folders))
    return FilterCollection(filters=filters)


def _data_source() -> MagicMock:
    async def list_objects_v2(Bucket: str, MaxKeys: int, Prefix: str | None = None) -> S3Response:
        keys = [k for k in OBJECTS if k.startswith(Prefix or "")][:MaxKeys]
        return S3Response(success=True, data={"Contents": [{"Key": k} for k in keys]})

    async def head_object(Bucket: str, Key: str) -> S3Response:
        if Key.startswith(READABLE):
            return S3Response(success=True, data={})
        return S3Response(success=False, error="403: Forbidden", status_code=403)

    source = MagicMock()
    source.list_objects_v2 = AsyncMock(side_effect=list_objects_v2)
    source.head_object = AsyncMock(side_effect=head_object)
    return source


def _connector(cls: type, folders: list[str] | None):  # noqa: ANN202
    processor = MagicMock()
    processor.org_id = "org-1"
    kwargs = {"endpoint_url": "http://10.0.0.5:9000"} if cls is MinIOConnector else {}
    connector = cls(
        logging.getLogger("test.object_store"), processor, MagicMock(), MagicMock(), "conn-1", "team", "user-1", **kwargs
    )
    connector.data_source = _data_source()
    connector.sync_filters = _sync_filters(folders)
    connector.notify = AsyncMock()
    return connector


CONNECTORS = pytest.mark.parametrize("cls", [S3Connector, MinIOConnector], ids=["s3", "minio"])


@CONNECTORS
@pytest.mark.asyncio
async def test_an_unreadable_folder_next_to_a_readable_one_only_warns(cls: type) -> None:
    connector = _connector(cls, ["b-public", "a-private"])
    assert await connector.test_connection_and_access() is True
    connector.notify.assert_awaited_once()
    notice = connector.notify.await_args.kwargs
    assert notice["title"].endswith("Some folders cannot be read")
    assert "folder 'a-private/' of bucket 'shared'" in notice["message"]
    assert "s3:GetObject on arn:aws:s3:::shared/a-private/*" in notice["message"]


@CONNECTORS
@pytest.mark.asyncio
async def test_every_chosen_folder_readable_sends_no_warning(cls: type) -> None:
    connector = _connector(cls, ["b-public", "c-public"])
    assert await connector.test_connection_and_access() is True
    connector.notify.assert_not_awaited()


@CONNECTORS
@pytest.mark.asyncio
async def test_no_readable_chosen_folder_is_refused_naming_it(cls: type) -> None:
    connector = _connector(cls, ["a-private"])
    with pytest.raises(ConnectorInitError, match="folder 'a-private/' of bucket 'shared'"):
        await connector.test_connection_and_access()


@CONNECTORS
@pytest.mark.asyncio
async def test_an_unreadable_bucket_points_to_the_folders_filter(cls: type) -> None:
    # The first object listed is the unreadable one; the key may still read other folders.
    connector = _connector(cls, None)
    with pytest.raises(ConnectorInitError, match="choose those in the Folders filter"):
        await connector.test_connection_and_access()
