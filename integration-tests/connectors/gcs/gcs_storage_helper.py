"""GCS storage SDK wrapper for connector integration tests."""

from __future__ import annotations

import json
from pathlib import Path
from typing import List

from google.cloud import storage as gcs_storage  # type: ignore[import-not-found]
from google.oauth2 import service_account  # type: ignore[import-not-found]


def _iter_files(root: Path):
    for path in root.rglob("*"):
        if path.is_file():
            yield path


class GCSStorageHelper:
    """Wrapper around google-cloud-storage with a test-friendly API."""

    def __init__(self, service_account_json: str | None) -> None:
        raw_value = (service_account_json or "").strip()

        try:
            path = Path(raw_value).expanduser()
            if path.is_file():
                credentials = service_account.Credentials.from_service_account_file(str(path))
                self._client = gcs_storage.Client(credentials=credentials)
                return
        except (TypeError, ValueError, OSError):
            pass

        try:
            info = json.loads(raw_value)
            credentials = service_account.Credentials.from_service_account_info(info)
            self._client = gcs_storage.Client(credentials=credentials)
        except json.JSONDecodeError as e:
            raise ValueError(
                "GCS_SERVICE_ACCOUNT_JSON must be a valid file path or a JSON string."
            ) from e

    def list_objects(self, bucket: str) -> List[str]:
        bkt = self._client.bucket(bucket)
        return [blob.name for blob in bkt.list_blobs()]

    def upload_directory(self, bucket: str, root: Path) -> int:
        root = root.resolve()
        bkt = self._client.bucket(bucket)
        count = 0
        for file_path in _iter_files(root):
            key = str(file_path.relative_to(root).as_posix())
            blob = bkt.blob(key)
            blob.upload_from_filename(str(file_path))
            count += 1
        return count

    def upload_blob(self, bucket: str, key: str, data: bytes, content_type: str | None = None) -> None:
        bkt = self._client.bucket(bucket)
        blob = bkt.blob(key)
        blob.upload_from_string(data, content_type=content_type or "application/octet-stream")

    def overwrite_blob(self, bucket: str, key: str, data: bytes, content_type: str | None = None) -> None:
        self.upload_blob(bucket, key, data, content_type)

    def get_blob_metadata(self, bucket: str, key: str) -> dict:
        bkt = self._client.bucket(bucket)
        blob = bkt.blob(key)
        blob.reload()
        return {
            "generation": blob.generation,
            "md5_hash": blob.md5_hash,
            "etag": blob.etag,
            "size": blob.size,
            "updated": blob.updated,
        }

    def rename_object(self, bucket: str, old_key: str, new_key: str) -> None:
        bkt = self._client.bucket(bucket)
        blob = bkt.blob(old_key)
        new_blob = bkt.rename_blob(blob, new_key)
        _ = new_blob

    def move_object(self, bucket: str, old_key: str, new_key: str) -> None:
        self.rename_object(bucket, old_key, new_key)

    def clear_objects(self, bucket: str) -> None:
        bkt = self._client.bucket(bucket)
        for blob in bkt.list_blobs():
            blob.delete()
