"""Tests for app.sources.external.azure.azure_files."""

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.sources.external.azure.azure_files import AzureFilesDataSource


def _listing(items):
    captured = {}

    def list_directories_and_files(**kwargs):
        captured.update(kwargs)

        async def gen():
            for item in items:
                yield item

        return gen()

    directory_client = MagicMock()
    directory_client.list_directories_and_files = list_directories_and_files
    share_client = MagicMock()
    share_client.get_directory_client.return_value = directory_client
    service = MagicMock()
    service.get_share_client.return_value = share_client
    client = MagicMock()
    client.get_async_share_service_client = AsyncMock(return_value=service)
    return AzureFilesDataSource(client), captured


class TestListDirectoriesAndFiles:

    @pytest.mark.asyncio
    async def test_requests_timestamps_and_etags(self):
        # Without them the service returns only name, size and FileId, which
        # left the connector nothing that changes when a file is edited.
        written = datetime(2026, 9, 18, 6, 3, tzinfo=timezone.utc)
        item = SimpleNamespace(
            name="employees.csv", is_directory=False, size=120, file_id="13835128424026341376",
            last_modified=written, last_write_time=written, creation_time=written,
            etag='"0x8DF"', content_length=120, content_settings=None,
        )
        ds, captured = _listing([item])

        response = await ds.list_directories_and_files("share", "sets/1")

        assert captured["include"] == ["timestamps", "Etag"]
        listed = response.data[0]
        assert listed["last_write_time"] == written
        assert listed["creation_time"] == written
        assert listed["last_modified"] == written
        assert listed["etag"] == '"0x8DF"'
        assert listed["path"] == "sets/1/employees.csv"
