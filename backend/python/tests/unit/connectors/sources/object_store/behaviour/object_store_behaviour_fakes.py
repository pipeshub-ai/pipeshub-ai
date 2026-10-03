"""Fakes for behaviour tests of the S3, MinIO, GCS and Azure Blob connectors.

Only two things are faked: the object store and our own databases. Each fake
store answers the listing call its connector's data source makes, with the
same response shape and paging, so the connector's sync code runs for real.
"""

from __future__ import annotations

import hashlib
import logging
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Any

from app.services.graph_db.common.record_visibility import (
    RecordVisibility,
    matches_visibility,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from app.connectors.core.base.connector.connector_service import BaseConnector
    from app.models.entities import FileRecord, Record

CONNECTOR_ID = "objstore-1"
BUCKET = "bucket-1"


@dataclass
class Response:
    """The success/data/error envelope every object-store data source returns."""

    success: bool
    data: Any = None
    error: str | None = None


@dataclass
class StoredObject:
    key: str
    body: bytes
    modified: datetime
    created: datetime


@dataclass
class FakeObjectStore:
    """One bucket or container, listed in key order a page at a time.

    ``fail_page`` makes the listing fail on that page (0-based): ``"error"``
    returns an unsuccessful response, ``"raise"`` raises mid-listing.
    """

    objects: dict[str, StoredObject] = field(default_factory=dict)
    fail_page: int | None = None
    fail_mode: str = "error"
    clock: datetime = field(default_factory=lambda: datetime(2026, 1, 1, tzinfo=timezone.utc))

    def tick(self) -> datetime:
        self.clock += timedelta(minutes=1)
        return self.clock

    def put(self, key: str, text: str | None = None) -> None:
        # Distinct bodies by default: the connectors take equal content at a new key for a move.
        text = key if text is None else text
        now = self.tick()
        existing = self.objects.get(key)
        self.objects[key] = StoredObject(key, text.encode(), now, existing.created if existing else now)

    def delete(self, key: str) -> None:
        del self.objects[key]

    def rename(self, key: str, new_key: str) -> None:
        """Copy then delete, as S3 does: same content, new key and modified time."""
        old = self.objects.pop(key)
        now = self.tick()
        self.objects[new_key] = StoredObject(new_key, old.body, now, now)

    def page(self, prefix: str | None, start: int, size: int) -> tuple[list[StoredObject], int | None]:
        if self.fail_page == start // size:
            if self.fail_mode == "raise":
                raise ConnectionError("connection reset while listing")
            raise _ListingFailed("service unavailable")
        keys = sorted(k for k in self.objects if k.startswith(prefix or ""))
        chunk = keys[start:start + size]
        following = start + size if start + size < len(keys) else None
        return [self.objects[k] for k in chunk], following


class _ListingFailed(Exception):
    pass


def _md5(body: bytes) -> str:
    return hashlib.md5(body).hexdigest()


class FakeS3DataSource:
    """``list_objects_v2`` over a fake bucket, continuation tokens included."""

    def __init__(self, store: FakeObjectStore) -> None:
        self.store = store

    async def list_objects_v2(
        self, Bucket: str, MaxKeys: int = 1000, ContinuationToken: str | None = None, Prefix: str | None = None, **_: object,
    ) -> Response:
        start = int(ContinuationToken) if ContinuationToken else 0
        try:
            objects, following = self.store.page(Prefix, start, MaxKeys)
        except _ListingFailed as e:
            return Response(False, error=f"ServiceUnavailable: {e}")
        data: dict[str, Any] = {"IsTruncated": following is not None, "KeyCount": len(objects)}
        if objects:
            # S3 leaves "Contents" out of an empty page.
            data["Contents"] = [
                {"Key": o.key, "LastModified": o.modified, "ETag": f'"{_md5(o.body)}"', "Size": len(o.body)}
                for o in objects
            ]
        if following is not None:
            data["NextContinuationToken"] = str(following)
        return Response(True, data)

    async def list_buckets(self) -> Response:
        return Response(True, {"Buckets": [{"Name": BUCKET, "CreationDate": self.store.clock}]})

    async def get_bucket_location(self, Bucket: str, **_: object) -> Response:
        return Response(True, {"LocationConstraint": None})


class FakeGCSDataSource:
    """``list_blobs`` over a fake bucket, shaped as ``GCSDataSource.list_blobs`` returns it."""

    def __init__(self, store: FakeObjectStore) -> None:
        self.store = store

    async def list_blobs(
        self, bucket_name: str, max_results: int = 1000, page_token: str | None = None, prefix: str | None = None, **_: object,
    ) -> Response:
        start = int(page_token) if page_token else 0
        try:
            objects, following = self.store.page(prefix, start, max_results)
        except _ListingFailed as e:
            return Response(False, error=f"GCS API error: {e}")
        return Response(True, {
            "Contents": [
                {
                    "Key": o.key,
                    "Size": len(o.body),
                    "LastModified": o.modified.isoformat(),
                    "TimeCreated": o.created.isoformat(),
                    "Md5Hash": _md5(o.body),
                }
                for o in objects
            ],
            "IsTruncated": following is not None,
            "NextContinuationToken": str(following) if following is not None else None,
        })

    async def list_buckets(self) -> Response:
        return Response(True, {"Buckets": [{"name": BUCKET}]})

    async def get_bucket_properties(self, bucket_name: str) -> Response:
        return Response(True, {"name": bucket_name})


class FakeAzureBlobDataSource:
    """``list_blobs`` over a fake container: one async iterator that pages internally, like the SDK's."""

    page_size = 2

    def __init__(self, store: FakeObjectStore) -> None:
        self.store = store

    async def list_blobs(self, container_name: str, prefix: str | None = None, **_: object) -> Response:
        store, size = self.store, self.page_size

        async def blobs() -> AsyncIterator[dict[str, Any]]:
            start = 0
            while True:
                try:
                    objects, following = store.page(prefix, start, size)
                except _ListingFailed as e:
                    raise ConnectionError(str(e)) from e
                for o in objects:
                    yield {
                        "name": o.key,
                        "last_modified": o.modified,
                        "creation_time": o.created,
                        "etag": f'"{_md5(o.body)}-{o.modified.timestamp()}"',
                        "size": len(o.body),
                        "content_type": None,
                        "content_md5": _md5(o.body),
                    }
                if following is None:
                    return
                start = following

        return Response(True, blobs())

    async def list_containers(self) -> Response:
        return Response(True, [{"name": BUCKET}])

    async def get_container_properties(self, container_name: str) -> Response:
        return Response(True, {"name": container_name})


class FakeRecordsDb:
    """In-memory stand-in for ``DataSourceEntitiesProcessor``.

    Lookups return what the graph providers return: a plain ``Record`` from
    ``get_record_by_external_id`` and ``get_record_by_external_revision_id``,
    and a ``FileRecord`` only from ``get_file_record_by_id``. ``failing`` names
    methods that raise as if the database were down.
    """

    def __init__(self, org_id: str = "org-1") -> None:
        self.org_id = org_id
        self.records: dict[str, FileRecord] = {}
        self.record_groups: dict[str, Any] = {}
        self.deleted: list[str] = []
        self.failing: set[str] = set()

    def _check(self, method: str) -> None:
        if method in self.failing:
            raise RuntimeError(f"database unavailable ({method})")

    def by_path(self) -> dict[str, FileRecord]:
        return {r.external_record_id: r for r in self.records.values()}

    def paths(self) -> set[str]:
        return set(self.by_path())

    @staticmethod
    def _base(stored: FileRecord) -> Record:
        from app.models.entities import Record

        return Record.from_arango_base_record(stored.to_arango_base_record())

    async def get_record_by_external_id(self, connector_id: str, external_record_id: str) -> Record | None:
        stored = self.by_path().get(external_record_id)
        return self._base(stored) if stored else None

    async def get_record_by_external_revision_id(self, connector_id: str, external_revision_id: str) -> Record | None:
        stored = next((r for r in self.records.values() if r.external_revision_id == external_revision_id), None)
        return self._base(stored) if stored else None

    async def get_file_record_by_id(self, record_id: str) -> FileRecord | None:
        from app.models.entities import FileRecord

        stored = self.records.get(record_id)
        if not isinstance(stored, FileRecord):
            return None
        return FileRecord.from_arango_record(stored.to_arango_record(), stored.to_arango_base_record())

    async def get_records_in_record_group(
        self, connector_id: str, external_group_id: str, limit: int, after_key: str | None = None,
        *, visibility: RecordVisibility = RecordVisibility.LIVE,
    ) -> list[Record]:
        """Live records only unless asked, as both graph stores answer."""
        self._check("get_records_in_record_group")
        if external_group_id not in self.record_groups:
            return []
        page = sorted(
            (
                r for r in self.records.values()
                if r.external_record_group_id == external_group_id and matches_visibility(r, visibility)
            ),
            key=lambda r: r.id,
        )
        return [self._base(r) for r in page if after_key is None or r.id > after_key][:limit]

    async def on_new_records(self, records_with_permissions: list[tuple[Any, list[Any]]]) -> None:
        self._check("on_new_records")
        for record, _ in records_with_permissions:
            # The processor upserts by external id, keeping the stored record's id.
            same_path = self.by_path().get(record.external_record_id)
            if same_path and same_path.id != record.id:
                record.id = same_path.id
            self.records[record.id] = record

    async def on_new_record_groups(self, groups: list[tuple[Any, list[Any]]]) -> None:
        for group, _ in groups:
            self.record_groups[group.external_group_id] = group

    async def on_record_deleted(self, record_id: str, **_: object) -> None:
        self._check("on_record_deleted")
        if self.records.pop(record_id, None) is not None:
            self.deleted.append(record_id)

    async def delete_parent_child_edge_to_record(self, record_id: str) -> None:
        return None

    async def ensure_team_app_edge(self, connector_id: str) -> None:
        return None

    async def get_user_by_user_id(self, user_id: str) -> None:
        return None


class FakeCheckpointStore:
    """In-memory sync-point collection behind ``DataStoreProvider.transaction()``; writes merge."""

    def __init__(self) -> None:
        self.sync_points: dict[str, dict[str, Any]] = {}

    async def get_sync_point(self, key: str, raise_on_error: bool = False) -> dict[str, Any] | None:
        return self.sync_points.get(key)

    async def update_sync_point(self, key: str, data: dict[str, Any]) -> None:
        self.sync_points.setdefault(key, {}).update(data)

    async def delete_sync_point(self, key: str) -> None:
        self.sync_points.pop(key, None)

    @asynccontextmanager
    async def transaction(self) -> AsyncIterator[FakeCheckpointStore]:
        yield self


class FakeConfigService:
    """Serves one connector's config document, whose ``filters`` the sync reloads each run."""

    def __init__(self) -> None:
        self.sync_filters: dict[str, Any] = {}

    def set_extensions(self, operator: str, extensions: list[str]) -> None:
        self.sync_filters["file_extensions"] = {"operator": operator, "value": extensions, "type": "multiselect"}

    async def get_config(self, path: str, default: object = None, **_: object) -> object:
        if path == f"/services/connectors/{CONNECTOR_ID}/config":
            return {"auth": {}, "filters": {"sync": {"values": dict(self.sync_filters)}}}
        return default


def make_connector(
    kind: str, store: FakeObjectStore, db: FakeRecordsDb, checkpoints: FakeCheckpointStore, config: FakeConfigService,
) -> BaseConnector:
    """A team-scoped connector of ``kind`` wired to the fakes, as ``init()`` would leave it for one bucket."""
    logger = logging.getLogger(f"test.objstore.{kind}")
    args = (logger, db, checkpoints, config, CONNECTOR_ID, "team", "creator-1")
    if kind == "s3":
        from app.connectors.sources.s3.connector import S3Connector

        connector = S3Connector(*args)
        connector.data_source = FakeS3DataSource(store)
    elif kind == "minio":
        from app.connectors.sources.minio.connector import MinIOConnector

        connector = MinIOConnector(*args)
        connector.data_source = FakeS3DataSource(store)
    elif kind == "gcs":
        from app.connectors.sources.google_cloud_storage.connector import GCSConnector

        connector = GCSConnector(*args)
        connector.data_source = FakeGCSDataSource(store)
    elif kind == "azure_blob":
        from app.connectors.sources.azure_blob.connector import AzureBlobConnector

        connector = AzureBlobConnector(*args)
        connector.data_source = FakeAzureBlobDataSource(store)
        connector.account_name = "acct"
        connector.container_name = BUCKET
        return connector
    else:
        raise ValueError(kind)
    connector.bucket_name = BUCKET
    connector.batch_size = 2
    return connector
