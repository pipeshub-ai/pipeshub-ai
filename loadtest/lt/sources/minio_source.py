"""MinIO load source — the S3 code path, deterministic and locally hosted.

`MinIOConnector` and `S3Connector` are both `S3CompatibleBaseConnector`
subclasses, so this exercises the same enumeration/graph-write path as real S3
while staying reproducible: no AWS credentials, no rate limits, no network
variance. Use the `s3` source instead only when the goal is a realistic
(non-comparable) run against real AWS.
"""

from __future__ import annotations

import hashlib
import os
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from .base import LoadSource, Unit, registry

# Extension mix, chosen so filter evaluation sees variety. Content is never
# downloaded during a connector-only run (indexing is off), so the bytes only
# need to be stable, not meaningful.
EXTENSIONS = [".txt", ".md", ".pdf", ".docx", ".csv", ".json", ".html", ".pptx"]
# Deterministic depth mix: some records at bucket root, some nested, so the
# record-group hierarchy the connector builds is non-trivial.
DEPTHS = [0, 1, 1, 2, 2, 3]


def _content(key: str, size: int) -> bytes:
    """Stable pseudo-random bytes for `key`, expanded to `size`."""
    out = bytearray()
    counter = 0
    while len(out) < size:
        out += hashlib.sha256(f"{key}:{counter}".encode()).digest()
        counter += 1
    return bytes(out[:size])


@registry.register
class MinioSource(LoadSource):
    name = "minio"
    connector_type = "MinIO"
    deterministic = True
    compose_file = "minio.yml"
    auth_type = "ACCESS_KEY"

    def __init__(self, options: dict[str, Any] | None = None) -> None:
        super().__init__(options)
        opts = self.options
        # Endpoint as the *connector* must reach it (inside the compose network)
        # vs as the *seeder* must reach it (from the host). They differ whenever
        # the harness runs outside the docker network.
        # Container name, not service name: `lt-minio` is resolvable by Docker's
        # embedded DNS from any container on the shared network regardless of
        # which compose project asks, whereas a service-name alias is easy to
        # break by renaming the service.
        self.connector_endpoint = opts.get("connector_endpoint") or os.getenv(
            "LT_MINIO_CONNECTOR_ENDPOINT", "http://lt-minio:9000"
        )
        self.seed_endpoint = opts.get("seed_endpoint") or os.getenv(
            "LT_MINIO_SEED_ENDPOINT", "http://localhost:9100"
        )
        self.access_key = opts.get("access_key") or os.getenv("LT_MINIO_ACCESS_KEY", "loadtest")
        self.secret_key = opts.get("secret_key") or os.getenv("LT_MINIO_SECRET_KEY", "loadtest123")
        self.bucket_prefix = opts.get("bucket_prefix", "lt")
        self.min_size = int(opts.get("min_size", 512))
        self.max_size = int(opts.get("max_size", 4096))
        self.upload_workers = int(opts.get("upload_workers", 16))
        self._client = None

    # -- helpers -----------------------------------------------------------

    @property
    def client(self):
        if self._client is None:
            try:
                import boto3
                from botocore.config import Config
            except ImportError as exc:  # pragma: no cover - dependency guard
                raise RuntimeError(
                    "the minio source needs boto3 (pip install -r loadtest/requirements.txt)"
                ) from exc
            self._client = boto3.client(
                "s3",
                endpoint_url=self.seed_endpoint,
                aws_access_key_id=self.access_key,
                aws_secret_access_key=self.secret_key,
                region_name="us-east-1",
                config=Config(
                    signature_version="s3v4",
                    max_pool_connections=self.upload_workers * 2,
                    retries={"max_attempts": 5, "mode": "standard"},
                ),
            )
        return self._client

    def bucket_name(self, index: int) -> str:
        return f"{self.bucket_prefix}-{index:02d}"

    def _keys(self, unit_index: int, scale: int, seed: int) -> list[tuple[str, int]]:
        """Deterministic (key, size) list. Pure function of its arguments."""
        import random

        rng = random.Random(f"{seed}:{unit_index}")
        keys: list[tuple[str, int]] = []
        for i in range(scale):
            depth = DEPTHS[rng.randrange(len(DEPTHS))]
            parts = [f"d{rng.randrange(8)}" for _ in range(depth)]
            ext = EXTENSIONS[rng.randrange(len(EXTENSIONS))]
            parts.append(f"rec-{i:06d}{ext}")
            keys.append(("/".join(parts), rng.randint(self.min_size, self.max_size)))
        return keys

    # -- lifecycle ---------------------------------------------------------

    def preflight(self) -> list[str]:
        try:
            self.client.list_buckets()
        except Exception as exc:
            return [
                f"MinIO not reachable at {self.seed_endpoint} ({exc}). "
                f"Start it with: docker compose -f loadtest/compose/{self.compose_file} up -d"
            ]
        return []

    def seed(self, units: int, scale: int, seed: int) -> list[Unit]:
        made: list[Unit] = []
        for index in range(units):
            bucket = self.bucket_name(index)
            self._ensure_bucket(bucket)
            entries = self._keys(index, scale, seed)
            self._empty_bucket(bucket)
            self._upload(bucket, entries)
            made.append(
                Unit(
                    index=index,
                    expected_records=scale,
                    label=bucket,
                    auth={
                        "endpointUrl": self.connector_endpoint,
                        "accessKey": self.access_key,
                        "secretKey": self.secret_key,
                        # Defaults to True in the connector's auth schema, which
                        # would make it dial https:// against a plain-HTTP MinIO.
                        "useSsl": self.connector_endpoint.startswith("https://"),
                        # Off only for a plain-HTTP test endpoint; a real https
                        # target should still be verified.
                        "verifySsl": self.connector_endpoint.startswith("https://")
                        and self.options.get("verify_ssl", True),
                    },
                    # Scoping each connector to its own bucket is what makes the
                    # units disjoint: N connectors sync N non-overlapping corpora.
                    sync_filters={
                        "buckets": {
                            "value": [bucket],
                            "operator": "in",
                            "type": "multiselect",
                        }
                    },
                )
            )
        return made

    def teardown(self, units: list[Unit]) -> None:
        for unit in units:
            try:
                self._empty_bucket(unit.label)
            except Exception:
                pass

    # -- storage ops -------------------------------------------------------

    def _ensure_bucket(self, bucket: str) -> None:
        from botocore.exceptions import ClientError

        try:
            self.client.head_bucket(Bucket=bucket)
        except ClientError:
            self.client.create_bucket(Bucket=bucket)

    def _empty_bucket(self, bucket: str) -> None:
        paginator = self.client.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=bucket):
            contents = page.get("Contents") or []
            if contents:
                self.client.delete_objects(
                    Bucket=bucket,
                    Delete={"Objects": [{"Key": o["Key"]} for o in contents]},
                )

    def _upload(self, bucket: str, entries: list[tuple[str, int]]) -> None:
        def put(entry: tuple[str, int]) -> None:
            key, size = entry
            self.client.put_object(Bucket=bucket, Key=key, Body=_content(key, size))

        with ThreadPoolExecutor(max_workers=self.upload_workers) as pool:
            list(pool.map(put, entries))
