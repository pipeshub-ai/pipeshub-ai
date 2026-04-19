"""Azure Blob storage SDK wrapper for connector integration tests."""

from __future__ import annotations

from pathlib import Path
from typing import List

from azure.storage.blob import BlobServiceClient, ContentSettings  # type: ignore[import-not-found]


def _iter_files(root: Path):
    for path in root.rglob("*"):
        if path.is_file():
            yield path


class AzureBlobStorageHelper:
    """Wrapper around azure-storage-blob for test usage."""

    def __init__(self, connection_string: str) -> None:
        self._service = BlobServiceClient.from_connection_string(connection_string)

    def list_objects(self, container: str) -> List[str]:
        container_client = self._service.get_container_client(container)
        return [b.name for b in container_client.list_blobs()]

    def upload_directory(self, container: str, root: Path) -> int:
        root = root.resolve()
        container_client = self._service.get_container_client(container)
        count = 0
        for file_path in _iter_files(root):
            key = str(file_path.relative_to(root).as_posix())
            blob_client = container_client.get_blob_client(key)
            with file_path.open("rb") as f:
                blob_client.upload_blob(f, overwrite=True)
            count += 1
        return count

    def upload_blob(self, container: str, key: str, data: bytes, content_type: str | None = None) -> None:
        container_client = self._service.get_container_client(container)
        blob_client = container_client.get_blob_client(key)
        cs = ContentSettings(content_type=content_type) if content_type else None
        blob_client.upload_blob(data, overwrite=True, content_settings=cs)

    def overwrite_blob(self, container: str, key: str, data: bytes, content_type: str | None = None) -> None:
        self.upload_blob(container, key, data, content_type)

    def get_blob_metadata(self, container: str, key: str) -> dict:
        container_client = self._service.get_container_client(container)
        blob_client = container_client.get_blob_client(key)
        props = blob_client.get_blob_properties()
        return {
            "etag": props.etag,
            "last_modified": props.last_modified,
            "size": props.size,
            "content_md5": props.content_settings.content_md5 if props.content_settings else None,
        }

    def rename_object(self, container: str, old_key: str, new_key: str) -> None:
        container_client = self._service.get_container_client(container)
        src_blob = container_client.get_blob_client(old_key)
        data = src_blob.download_blob().readall()

        dest_blob = container_client.get_blob_client(new_key)
        dest_blob.upload_blob(data, overwrite=True)
        src_blob.delete_blob()

    def move_object(self, container: str, old_key: str, new_key: str) -> None:
        self.rename_object(container, old_key, new_key)

    def clear_objects(self, container: str) -> None:
        container_client = self._service.get_container_client(container)
        blobs = list(container_client.list_blobs())
        if blobs:
            container_client.delete_blobs(*blobs)
