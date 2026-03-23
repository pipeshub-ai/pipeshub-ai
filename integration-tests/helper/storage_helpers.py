"""
Storage helper utilities for connector integration tests.

These thin wrappers provide a unified, test-friendly API over the various
cloud storage SDKs used by the S3, GCS, Azure Blob, and Azure Files
connectors:

- S3StorageHelper
- GCSStorageHelper
- AzureBlobStorageHelper
- AzureFilesStorageHelper

The goal is to keep all cloud-specific SDK usage in one place and expose a
small, consistent surface area to tests:

- create_bucket / create_container / create_share
- list_objects
- upload_directory
- rename_object
- move_object
- delete_bucket / delete_container / delete_share
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Iterable, List

import boto3
from google.cloud import storage as gcs_storage  # type: ignore[import-not-found]
from google.oauth2 import service_account  # type: ignore[import-not-found]
from azure.core.exceptions import ResourceExistsError  # type: ignore[import-not-found]
from azure.storage.blob import (  # type: ignore[import-not-found]
    BlobServiceClient,
)
from azure.storage.fileshare import (  # type: ignore[import-not-found]
    ShareServiceClient,
    ShareDirectoryClient,
)


def _iter_files(root: Path) -> Iterable[Path]:
    """Yield all regular files under a directory, recursively."""
    for path in root.rglob("*"):
        if path.is_file():
            yield path


class S3StorageHelper:
    """Lightweight wrapper around boto3 for S3 operations used in tests."""

    def __init__(
        self,
        access_key: str,
        secret_key: str,
        region_name: str | None = None,
    ) -> None:
        region = region_name or os.getenv("S3_REGION") or "us-east-1"
        self._region = region
        self._client = boto3.client(
            "s3",
            aws_access_key_id=access_key,
            aws_secret_access_key=secret_key,
            region_name=region,
        )

    # ---------------------------- bucket lifecycle ---------------------------- #

    def create_bucket(self, bucket: str) -> None:
        if self._region == "us-east-1":
            self._client.create_bucket(Bucket=bucket)
        else:
            self._client.create_bucket(
                Bucket=bucket,
                CreateBucketConfiguration={"LocationConstraint": self._region},
            )

    def list_objects(self, bucket: str) -> List[str]:
        keys: List[str] = []
        paginator = self._client.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=bucket):
            contents = page.get("Contents") or []
            for obj in contents:
                key = obj.get("Key")
                if key:
                    keys.append(key)
        return keys

    def upload_directory(self, bucket: str, root: Path) -> int:
        root = root.resolve()
        count = 0
        for file_path in _iter_files(root):
            key = str(file_path.relative_to(root).as_posix())
            self._client.upload_file(str(file_path), bucket, key)
            count += 1
        return count

    def upload_object(self, bucket: str, key: str, data: bytes, content_type: str | None = None) -> None:
        """Upload a single object from bytes."""
        extra = {}
        if content_type:
            extra["ContentType"] = content_type
        self._client.put_object(Bucket=bucket, Key=key, Body=data, **extra)

    def overwrite_object(self, bucket: str, key: str, data: bytes, content_type: str | None = None) -> None:
        """Overwrite an existing object with new content."""
        self.upload_object(bucket, key, data, content_type)

    def get_object_metadata(self, bucket: str, key: str) -> dict:
        """Return object metadata (ETag, ContentLength, LastModified, etc.)."""
        resp = self._client.head_object(Bucket=bucket, Key=key)
        return {
            "etag": resp.get("ETag", "").strip('"'),
            "content_length": resp.get("ContentLength"),
            "last_modified": resp.get("LastModified"),
        }

    def rename_object(self, bucket: str, old_key: str, new_key: str) -> None:
        self._client.copy_object(
            Bucket=bucket,
            CopySource={"Bucket": bucket, "Key": old_key},
            Key=new_key,
        )
        self._client.delete_object(Bucket=bucket, Key=old_key)

    def move_object(self, bucket: str, old_key: str, new_key: str) -> None:
        self.rename_object(bucket, old_key, new_key)

    def clear_objects(self, bucket: str) -> None:
        """Delete all objects in the bucket without deleting the bucket itself."""
        paginator = self._client.get_paginator("list_object_versions")
        for page in paginator.paginate(Bucket=bucket):
            to_delete = []
            for obj in page.get("Versions", []):
                to_delete.append({"Key": obj["Key"], "VersionId": obj["VersionId"]})
            for marker in page.get("DeleteMarkers", []):
                to_delete.append(
                    {"Key": marker["Key"], "VersionId": marker["VersionId"]}
                )
            if to_delete:
                self._client.delete_objects(
                    Bucket=bucket, Delete={"Objects": to_delete}
                )
        remaining = self.list_objects(bucket)
        if remaining:
            self._client.delete_objects(
                Bucket=bucket,
                Delete={"Objects": [{"Key": k} for k in remaining]},
            )

    def delete_bucket(self, bucket: str) -> None:
        self.clear_objects(bucket)
        self._client.delete_bucket(Bucket=bucket)


class GCSStorageHelper:
    """Wrapper around google-cloud-storage with a test-friendly API."""

    def __init__(self, service_account_json: str) -> None:
        # Accept either a path to a JSON file or the JSON payload itself.
        path = Path(service_account_json)
        if path.exists():
            credentials = service_account.Credentials.from_service_account_file(
                str(path)
            )
        else:
            info = json.loads(service_account_json)
            credentials = service_account.Credentials.from_service_account_info(info)

        self._client = gcs_storage.Client(credentials=credentials)

    def create_bucket(self, bucket: str) -> None:
        self._client.create_bucket(bucket)

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
        """Upload a single blob from bytes."""
        bkt = self._client.bucket(bucket)
        blob = bkt.blob(key)
        blob.upload_from_string(data, content_type=content_type or "application/octet-stream")

    def overwrite_blob(self, bucket: str, key: str, data: bytes, content_type: str | None = None) -> None:
        """Overwrite an existing blob with new content (same key, new ETag/generation)."""
        self.upload_blob(bucket, key, data, content_type)

    def get_blob_metadata(self, bucket: str, key: str) -> dict:
        """Return blob metadata (generation, md5_hash, etag, size, etc.)."""
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
        # `rename_blob` already copies and deletes the source.
        _ = new_blob

    def move_object(self, bucket: str, old_key: str, new_key: str) -> None:
        self.rename_object(bucket, old_key, new_key)

    def clear_objects(self, bucket: str) -> None:
        """Delete all objects in the bucket without deleting the bucket itself."""
        bkt = self._client.bucket(bucket)
        for blob in bkt.list_blobs():
            blob.delete()

    def delete_bucket(self, bucket: str) -> None:
        self.clear_objects(bucket)
        self._client.bucket(bucket).delete()


class AzureBlobStorageHelper:
    """Wrapper around azure-storage-blob for test usage."""

    def __init__(self, connection_string: str) -> None:
        self._service = BlobServiceClient.from_connection_string(connection_string)

    def create_container(self, container: str) -> None:
        self._service.create_container(container)

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
        """Upload a single blob from bytes."""
        from azure.storage.blob import ContentSettings as BlobContentSettings  # type: ignore[import-not-found]
        container_client = self._service.get_container_client(container)
        blob_client = container_client.get_blob_client(key)
        cs = BlobContentSettings(content_type=content_type) if content_type else None
        blob_client.upload_blob(data, overwrite=True, content_settings=cs)

    def overwrite_blob(self, container: str, key: str, data: bytes, content_type: str | None = None) -> None:
        """Overwrite an existing blob with new content."""
        self.upload_blob(container, key, data, content_type)

    def get_blob_metadata(self, container: str, key: str) -> dict:
        """Return blob properties (etag, last_modified, size, etc.)."""
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
        # Implement rename as download + re-upload to avoid dealing with copy URLs.
        container_client = self._service.get_container_client(container)
        src_blob = container_client.get_blob_client(old_key)
        data = src_blob.download_blob().readall()

        dest_blob = container_client.get_blob_client(new_key)
        dest_blob.upload_blob(data, overwrite=True)
        src_blob.delete_blob()

    def move_object(self, container: str, old_key: str, new_key: str) -> None:
        self.rename_object(container, old_key, new_key)

    def clear_objects(self, container: str) -> None:
        """Delete all blobs in the container without deleting the container itself."""
        container_client = self._service.get_container_client(container)
        blobs = list(container_client.list_blobs())
        if blobs:
            container_client.delete_blobs(*blobs)

    def delete_container(self, container: str) -> None:
        self.clear_objects(container)
        self._service.get_container_client(container).delete_container()


class AzureFilesStorageHelper:
    """Wrapper around azure-storage-file-share for test usage."""

    def __init__(self, connection_string: str) -> None:
        self._service = ShareServiceClient.from_connection_string(connection_string)

    def create_share(self, share: str) -> None:
        self._service.create_share(share)

    def _iter_files_in_share(
        self, share: str, directory_path: str = ""
    ) -> Iterable[str]:
        share_client = self._service.get_share_client(share)
        directory: ShareDirectoryClient
        if directory_path:
            directory = share_client.get_directory_client(directory_path)
        else:
            directory = share_client.get_directory_client("")

        for item in directory.list_directories_and_files():
            name = item["name"]
            path = f"{directory_path}/{name}" if directory_path else name
            if item["is_directory"]:
                yield from self._iter_files_in_share(share, path)
            else:
                yield path

    def list_objects(self, share: str) -> List[str]:
        return list(self._iter_files_in_share(share))

    def _ensure_azure_files_directory(self, share_client: object, dir_name: str) -> object:
        """Create directory and all parents; return client for dir_name."""
        if not dir_name:
            return share_client.get_directory_client("")  # type: ignore[return-value]
        current = share_client.get_directory_client("")
        for part in dir_name.split("/"):
            if not part:
                continue
            current = current.get_subdirectory_client(part)
            try:
                current.create_directory()
            except ResourceExistsError:
                pass
        return share_client.get_directory_client(dir_name)

    def upload_directory(self, share: str, root: Path) -> int:
        root = root.resolve()
        share_client = self._service.get_share_client(share)
        count = 0
        for file_path in _iter_files(root):
            rel_path = file_path.relative_to(root).as_posix()
            dir_name, _, file_name = rel_path.rpartition("/")

            if dir_name:
                directory_client = self._ensure_azure_files_directory(share_client, dir_name)
            else:
                directory_client = share_client.get_directory_client("")

            file_client = directory_client.get_file_client(file_name)
            data = file_path.read_bytes()
            file_client.upload_file(data)
            count += 1

        return count

    def upload_file(self, share: str, key: str, data: bytes) -> None:
        """Upload a single file from bytes, creating parent directories as needed."""
        share_client = self._service.get_share_client(share)
        dir_name, _, file_name = key.rpartition("/")
        if dir_name:
            directory_client = self._ensure_azure_files_directory(share_client, dir_name)
        else:
            directory_client = share_client.get_directory_client("")
        file_client = directory_client.get_file_client(file_name)
        file_client.upload_file(data)

    def overwrite_file(self, share: str, key: str, data: bytes) -> None:
        """Overwrite an existing file with new content."""
        share_client = self._service.get_share_client(share)
        dir_name, _, file_name = key.rpartition("/")
        if dir_name:
            directory_client = share_client.get_directory_client(dir_name)
        else:
            directory_client = share_client.get_directory_client("")
        file_client = directory_client.get_file_client(file_name)
        file_client.upload_file(data)

    def get_file_metadata(self, share: str, key: str) -> dict:
        """Return file properties (etag, last_modified, size, etc.)."""
        share_client = self._service.get_share_client(share)
        dir_name, _, file_name = key.rpartition("/")
        if dir_name:
            directory_client = share_client.get_directory_client(dir_name)
        else:
            directory_client = share_client.get_directory_client("")
        file_client = directory_client.get_file_client(file_name)
        props = file_client.get_file_properties()
        return {
            "etag": props.etag,
            "last_modified": props.last_modified,
            "size": props.size,
        }

    def rename_object(self, share: str, old_path: str, new_path: str) -> None:
        self._rename_within_share(share, old_path, new_path)

    def move_object(self, share: str, old_path: str, new_path: str) -> None:
        self._rename_within_share(share, old_path, new_path)

    def _rename_within_share(self, share: str, old_path: str, new_path: str) -> None:
        """
        Rename/move using the File service Rename API (not copy+delete).

        Copy+delete creates a new file with a new SMB file_id, so the connector cannot
        match the new object to the existing Record via external_revision_id (file_id).
        Server-side rename preserves file_id, which matches how real clients move files
        and allows move/rename detection in AzureFilesConnector._process_azure_files_item.
        """
        share_client = self._service.get_share_client(share)
        old_dir, _, old_name = old_path.rpartition("/")
        new_dir, _, _new_name = new_path.rpartition("/")

        if new_dir:
            self._ensure_azure_files_directory(share_client, new_dir)

        src_dir_client = (
            share_client.get_directory_client(old_dir)
            if old_dir
            else share_client.get_directory_client("")  # type: ignore[call-arg]
        )
        src_file_client = src_dir_client.get_file_client(old_name)
        dest_path = new_path.strip("/")
        src_file_client.rename_file(dest_path, overwrite=True)

    def clear_objects(self, share: str) -> None:
        """Delete all files in the share without deleting the share itself."""
        share_client = self._service.get_share_client(share)
        for path in list(self._iter_files_in_share(share)):
            dir_name, _, file_name = path.rpartition("/")
            directory_client = (
                share_client.get_directory_client(dir_name)
                if dir_name
                else share_client.get_directory_client("")
            )
            file_client = directory_client.get_file_client(file_name)
            file_client.delete_file()

    def delete_share(self, share: str) -> None:
        self.clear_objects(share)
        self._service.get_share_client(share).delete_share()

