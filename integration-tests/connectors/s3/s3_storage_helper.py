"""S3 storage SDK wrapper for connector integration tests."""

from __future__ import annotations

import os
from pathlib import Path
from typing import List

import boto3
from botocore.exceptions import ClientError


def _iter_files(root: Path):
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
        extra = {}
        if content_type:
            extra["ContentType"] = content_type
        self._client.put_object(Bucket=bucket, Key=key, Body=data, **extra)

    def overwrite_object(self, bucket: str, key: str, data: bytes, content_type: str | None = None) -> None:
        self.upload_object(bucket, key, data, content_type)

    def get_object_metadata(self, bucket: str, key: str) -> dict:
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

    def _clear_objects_versioned(self, bucket: str) -> None:
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

    def _clear_objects_current_only(self, bucket: str) -> None:
        """Delete current object versions only (no ListObjectVersions / version deletes)."""
        paginator = self._client.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=bucket):
            contents = page.get("Contents") or []
            if not contents:
                continue
            self._client.delete_objects(
                Bucket=bucket,
                Delete={"Objects": [{"Key": obj["Key"]} for obj in contents]},
            )

    def clear_objects(self, bucket: str) -> None:
        try:
            self._clear_objects_versioned(bucket)
        except ClientError as e:
            err = e.response.get("Error", {}) or {}
            if err.get("Code") != "AccessDenied":
                raise
            # IAM often grants ListBucket/DeleteObject but not ListBucketVersions; that
            # is enough when the bucket is non-versioned (typical for integration tests).
            self._clear_objects_current_only(bucket)

    def delete_bucket(self, bucket: str) -> None:
        self.clear_objects(bucket)
        self._client.delete_bucket(Bucket=bucket)
