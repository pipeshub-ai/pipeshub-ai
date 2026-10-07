"""Unit tests for per-run storage folders (no live services).

A test run clears its folder of a shared bucket in teardown. These pin that
the clear can never reach outside that folder.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from helper.run_folder import folder_filter, new_run_folder, require_run_folder

pytestmark = pytest.mark.unit


class TestRunFolder:
    def test_new_folders_are_unique_top_level_folders(self, monkeypatch):
        monkeypatch.setenv("GITHUB_RUN_ID", "123")
        monkeypatch.setenv("GITHUB_RUN_ATTEMPT", "2")
        a, b = new_run_folder(), new_run_folder()
        assert a != b
        assert a.startswith("it-123-2-") and a.endswith("/") and a.count("/") == 1
        assert require_run_folder(a) == a

    @pytest.mark.parametrize("bad", ["", "/", "it-/", "sets/", "it-x", "it-x/y/", "data/it-x/"])
    def test_anything_but_a_run_folder_is_refused(self, bad):
        with pytest.raises(ValueError):
            require_run_folder(bad)

    def test_folder_filter_limits_the_connector_to_the_folder(self):
        values = folder_filter("it-1-1-abc/")["sync"]["values"]
        assert values == {"folder_paths": {"value": ["it-1-1-abc"], "operator": "in", "type": "list"}}


def _files(root: Path) -> Path:
    (root / "sets" / "1").mkdir(parents=True)
    (root / "sets" / "1" / "a.csv").write_text("x")
    (root / "top.txt").write_text("y")
    return root


class TestS3Helper:
    @staticmethod
    def helper():
        from connectors.s3.s3_storage_helper import S3StorageHelper

        h = S3StorageHelper.__new__(S3StorageHelper)
        h._client = MagicMock()
        return h

    def test_uploads_list_and_clear_stay_in_the_folder(self, tmp_path):
        h = self.helper()
        h.upload_directory("b", _files(tmp_path), prefix="it-r/")
        uploaded = sorted(c.args[2] for c in h._client.upload_file.call_args_list)
        assert uploaded == ["it-r/sets/1/a.csv", "it-r/top.txt"]

        paginator = MagicMock()
        paginator.paginate.return_value = [{"Contents": [{"Key": "it-r/top.txt"}]}]
        h._client.get_paginator.return_value = paginator
        h._client.exceptions = MagicMock()
        h.clear_objects("b", "it-r/")
        for call in paginator.paginate.call_args_list:
            assert call.kwargs["Prefix"] == "it-r/"

    def test_clearing_without_a_run_folder_deletes_nothing(self):
        h = self.helper()
        with pytest.raises(ValueError):
            h.clear_objects("b", "")
        h._client.delete_objects.assert_not_called()


class TestGcsHelper:
    def test_clear_lists_only_the_folder(self):
        from connectors.gcs.gcs_storage_helper import GCSStorageHelper

        h = GCSStorageHelper.__new__(GCSStorageHelper)
        h._client = MagicMock()
        blob = MagicMock()
        h._client.bucket.return_value.list_blobs.return_value = [blob]

        h.clear_objects("b", "it-r/")

        h._client.bucket.return_value.list_blobs.assert_called_once_with(prefix="it-r/")
        blob.delete.assert_called_once()


class TestAzureBlobHelper:
    def test_clear_lists_only_the_folder(self):
        from connectors.azure_blob.azure_blob_storage_helper import AzureBlobStorageHelper

        h = AzureBlobStorageHelper.__new__(AzureBlobStorageHelper)
        h._service = MagicMock()
        container = h._service.get_container_client.return_value
        container.list_blobs.return_value = ["it-r/a"]

        h.clear_objects("c", "it-r/")

        container.list_blobs.assert_called_once_with(name_starts_with="it-r/")
        container.delete_blobs.assert_called_once_with("it-r/a")


class TestAzureFilesHelper:
    def test_clear_deletes_files_then_the_folders_deepest_first(self):
        from connectors.azure_files.azure_files_storage_helper import AzureFilesStorageHelper

        h = AzureFilesStorageHelper.__new__(AzureFilesStorageHelper)
        h._service = MagicMock()
        h.list_objects = MagicMock(return_value=["it-r/sets/1/a.csv", "it-r/top.txt"])
        share = h._service.get_share_client.return_value
        order = []
        share.get_directory_client.side_effect = lambda d: MagicMock(
            delete_directory=lambda: order.append(d),
            get_file_client=lambda name: MagicMock(delete_file=lambda: order.append(f"{d}/{name}")),
        )

        h.clear_objects("s", "it-r/")

        h.list_objects.assert_called_once_with("s", "it-r/")
        assert order == ["it-r/sets/1/a.csv", "it-r/top.txt", "it-r/sets/1", "it-r/sets", "it-r"]

    @staticmethod
    def _helper_with_missing_folder():
        from azure.core.exceptions import ResourceNotFoundError
        from connectors.azure_files.azure_files_storage_helper import AzureFilesStorageHelper

        h = AzureFilesStorageHelper.__new__(AzureFilesStorageHelper)
        h._service = MagicMock()
        share = h._service.get_share_client.return_value
        share.get_directory_client.return_value.list_directories_and_files.side_effect = (
            ResourceNotFoundError("not found")
        )
        return h, share

    def test_a_missing_run_folder_lists_as_empty(self):
        h, _ = self._helper_with_missing_folder()

        assert h.list_objects("s", "it-r/") == []

    def test_a_missing_share_still_raises(self):
        from azure.core.exceptions import ResourceNotFoundError

        h, share = self._helper_with_missing_folder()
        share.get_share_properties.side_effect = ResourceNotFoundError("share not found")

        with pytest.raises(ResourceNotFoundError):
            h.list_objects("s", "it-r/")
