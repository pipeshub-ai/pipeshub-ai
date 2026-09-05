# pyright: ignore-file

"""MinIO storage helper.

MinIO speaks the S3 API, so every operation the lifecycle helpers need is
already implemented in ``S3StorageHelper``. The only difference is that boto3
has to be pointed at the MinIO endpoint instead of AWS, and that path-style
addressing is required — MinIO does not serve virtual-host style buckets on a
bare host name.
"""

from __future__ import annotations

import boto3
from botocore.config import Config

from connectors.s3.s3_storage_helper import S3StorageHelper


class MinioStorageHelper(S3StorageHelper):
    """S3StorageHelper pointed at a MinIO server."""

    def __init__(
        self,
        access_key: str,
        secret_key: str,
        endpoint_url: str,
        region_name: str = "us-east-1",
    ) -> None:
        self._region = region_name
        self._client = boto3.client(
            "s3",
            aws_access_key_id=access_key,
            aws_secret_access_key=secret_key,
            endpoint_url=endpoint_url,
            region_name=region_name,
            # MinIO resolves buckets by path, not by sub-domain.
            config=Config(s3={"addressing_style": "path"}),
        )
