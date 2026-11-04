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
from unittest.mock import AsyncMock
from urllib.parse import quote, unquote, urlsplit

from app.connectors.core.sync.full_sync_coverage import FullSyncSeen
from app.services.graph_db.common.record_visibility import (
    RecordVisibility,
    matches_visibility,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Callable

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
    status_code: int | None = None


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
    ``fail_prefix`` makes every listing of that prefix fail the same way.
    ``before_page`` maps a page (0-based) to a change made, once, just before it is served.
    """

    objects: dict[str, StoredObject] = field(default_factory=dict)
    fail_page: int | None = None
    fail_prefix: str | None = None
    fail_mode: str = "error"
    before_page: dict[int, Callable[[], None]] = field(default_factory=dict)
    clock: datetime = field(default_factory=lambda: datetime(2026, 1, 1, tzinfo=timezone.utc))

    def tick(self) -> datetime:
        self.clock += timedelta(minutes=1)
        return self.clock

    def now_ms(self) -> int:
        return int(self.clock.timestamp() * 1000)

    def put(self, key: str, text: str | None = None, modified: datetime | None = None) -> None:
        """``modified`` dates the object as given, as a multipart upload is dated by its start."""
        # Distinct bodies by default: the connectors take equal content at a new key for a move.
        text = key if text is None else text
        now = self.tick()
        existing = self.objects.get(key)
        self.objects[key] = StoredObject(key, text.encode(), modified or now, existing.created if existing else now)

    def delete(self, key: str) -> None:
        del self.objects[key]

    def rename(self, key: str, new_key: str) -> None:
        """Copy then delete, as S3 does: same content, new key and modified time."""
        old = self.objects.pop(key)
        now = self.tick()
        self.objects[new_key] = StoredObject(new_key, old.body, now, now)

    def page(self, prefix: str | None, start: int, size: int) -> tuple[list[StoredObject], int | None]:
        change = self.before_page.pop(start // size, None)
        if change:
            change()
        if self.fail_page == start // size or (self.fail_prefix is not None and prefix == self.fail_prefix):
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


class _FakeDataSource:
    """One fake store per bucket or container; ``BUCKET`` is the first. ``hidden`` ones are left out of the bucket list."""

    def __init__(self, store: FakeObjectStore) -> None:
        self.store = store
        self.stores: dict[str, FakeObjectStore] = {BUCKET: store}
        self.hidden: set[str] = set()

    def add_bucket(self, name: str) -> FakeObjectStore:
        self.stores[name] = FakeObjectStore()
        return self.stores[name]

    def bucket_names(self) -> list[str]:
        return [name for name in self.stores if name not in self.hidden]


class FakeS3DataSource(_FakeDataSource):
    """``list_objects_v2`` over fake buckets, continuation tokens included.

    ``head_fails`` makes ``head_object`` fail: ``"error"`` answers 503, ``"raise"`` raises.
    ``denied`` buckets are in the bucket list, but listing them answers 403, as for a key without s3:ListBucket.
    """

    head_fails: str | None = None

    def __init__(self, store: FakeObjectStore) -> None:
        super().__init__(store)
        self.denied: set[str] = set()

    async def head_object(self, Bucket: str, Key: str, **_: object) -> Response:
        if self.head_fails == "raise":
            raise ConnectionError("connection reset during HeadObject")
        if self.head_fails == "error":
            return Response(False, error="503: Service Unavailable", status_code=503)
        o = self.stores[Bucket].objects.get(Key)
        if o is None:
            return Response(False, error="404: Not Found", status_code=404)
        return Response(True, {"ETag": f'"{_md5(o.body)}"', "ContentLength": len(o.body), "LastModified": o.modified})

    async def list_objects_v2(
        self, Bucket: str, MaxKeys: int = 1000, ContinuationToken: str | None = None, Prefix: str | None = None, **_: object,
    ) -> Response:
        if Bucket not in self.stores:
            return Response(False, error="NoSuchBucket: The specified bucket does not exist")
        if Bucket in self.denied:
            return Response(False, error="AccessDenied: User is not authorized to perform: s3:ListBucket", status_code=403)
        start = int(ContinuationToken) if ContinuationToken else 0
        try:
            objects, following = self.stores[Bucket].page(Prefix, start, MaxKeys)
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

    async def list_buckets(self, **_: object) -> Response:
        return Response(True, {"Buckets": [{"Name": n, "CreationDate": self.store.clock} for n in self.bucket_names()]})

    async def get_bucket_location(self, Bucket: str, **_: object) -> Response:
        return Response(True, {"LocationConstraint": None})

    async def generate_presigned_url(self, ClientMethod: str, Params: dict[str, str], **_: object) -> Response:
        """Signs as boto does: the URL path is the given key, percent-encoded."""
        return Response(True, f"https://{Params['Bucket']}.s3.amazonaws.com/{quote(Params['Key'])}?X-Amz-Signature=x")

    def object_at(self, url: str) -> StoredObject | None:
        """What a GET of a signed URL returns: the object at the key it names, if any."""
        parts = urlsplit(url)
        return self.stores[parts.netloc.split(".")[0]].objects.get(unquote(parts.path[1:]))


class FakeGCSDataSource(_FakeDataSource):
    """``list_blobs`` over fake buckets, shaped as ``GCSDataSource.list_blobs`` returns it."""

    async def list_blobs(
        self, bucket_name: str, max_results: int = 1000, page_token: str | None = None, prefix: str | None = None, **_: object,
    ) -> Response:
        start = int(page_token) if page_token else 0
        try:
            objects, following = self.stores[bucket_name].page(prefix, start, max_results)
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
        return Response(True, {"Buckets": [{"name": n} for n in self.bucket_names()]})

    async def get_bucket_properties(self, bucket_name: str) -> Response:
        return Response(True, {"name": bucket_name})


class FakeAzureBlobDataSource(_FakeDataSource):
    """``list_blobs`` over fake containers: one async iterator that pages internally, like the SDK's."""

    page_size = 2

    async def list_blobs(self, container_name: str, prefix: str | None = None, **_: object) -> Response:
        store, size = self.stores[container_name], self.page_size

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
        return Response(True, [{"name": n} for n in self.bucket_names()])

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
        self.written: list[str] = []
        self.moved: list[tuple[str, str]] = []
        self.failing: set[str] = set()
        # Groups whose delete answers False, as the processor does when the delete fails.
        self.refused_group_deletes: set[str] = set()
        # Users by userId, and every permission written: records only ever gain edges, groups get replaced.
        self.users: dict[str, Any] = {}
        self.record_permissions: dict[str, list[Any]] = {}
        self.group_permissions: dict[str, list[Any]] = {}
        self.app_users: list[Any] = []
        # What the running full sync wrote, as the processor notes it for run_sync_task's unlink.
        self.seen: FullSyncSeen | None = None

    def begin_full_sync_run(self, connector_id: str) -> FullSyncSeen:
        self.seen = FullSyncSeen()
        return self.seen

    def end_full_sync_run(self, connector_id: str, run: FullSyncSeen) -> None:
        self.seen = None

    def _note_seen(self, collection: str, node_id: str) -> None:
        if self.seen is not None:
            self.seen.add(collection, node_id)

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
        for record, permissions in records_with_permissions:
            self.written.append(record.external_record_id)
            self.record_permissions.setdefault(record.external_record_id, []).extend(permissions)
            # The processor upserts by external id, keeping the stored record's id.
            same_path = self.by_path().get(record.external_record_id)
            if same_path and same_path.id != record.id:
                record.id = same_path.id
            self.records[record.id] = record
            self._note_seen("records", record.id)

    async def on_records_moved(self, moves: list[tuple[str, Any, list[Any]]]) -> None:
        """In place: the stored record at the old external id takes the new path and keeps its id."""
        self._check("on_records_moved")
        for old_external_id, record, permissions in moves:
            self.moved.append((old_external_id, record.external_record_id))
            stored = self.by_path().get(old_external_id)
            if stored:
                record.id = stored.id
            self.records[record.id] = record
            self.record_permissions.setdefault(record.external_record_id, []).extend(permissions)
            self._note_seen("records", record.id)

    async def on_new_record_groups(self, groups: list[tuple[Any, list[Any]]]) -> None:
        self._check("on_new_record_groups")
        for group, permissions in groups:
            self._note_seen("recordGroups", group.id)
            self.record_groups[group.external_group_id] = group
            self.group_permissions[group.external_group_id] = list(permissions)

    async def get_nodes_by_filters(
        self, collection: str, filters: dict[str, Any], return_fields: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        """Stored documents, camelCase keys as the graph providers return them; record groups only."""
        self._check("get_nodes_by_filters")
        if collection != "recordGroups":
            raise NotImplementedError(collection)
        docs = [g.to_arango_base_record_group() for g in self.record_groups.values()]
        return [d for d in docs if all(d.get(k) == v for k, v in filters.items())]

    async def on_record_group_deleted(self, external_group_id: str, connector_id: str) -> bool:
        if external_group_id in self.refused_group_deletes:
            return False
        return self.record_groups.pop(external_group_id, None) is not None

    async def on_record_deleted(self, record_id: str, **_: object) -> None:
        self._check("on_record_deleted")
        if self.records.pop(record_id, None) is not None:
            self.deleted.append(record_id)

    async def delete_parent_child_edge_to_record(self, record_id: str) -> None:
        return None

    async def ensure_team_app_edge(self, connector_id: str) -> None:
        return None

    async def get_user_by_user_id(self, user_id: str, *, raise_on_error: bool = False) -> object:
        """As the graph providers answer: a failed query is None unless the caller asks it to raise."""
        if "get_user_by_user_id" in self.failing:
            if raise_on_error:
                self._check("get_user_by_user_id")
            return None
        return self.users.get(user_id)

    async def on_new_app_users(self, users: list[Any]) -> None:
        self.app_users.extend(users)


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
        # "raise" or "empty" makes reads of the config fail that way; ``fail_from`` is the first read (0-based) to fail.
        self.fail_mode: str | None = None
        self.fail_from = 0
        self.reads = 0

    def set_selection(self, filter_name: str, names: list[str], operator: str = "in") -> None:
        self.sync_filters[filter_name] = {"operator": operator, "value": names, "type": "multiselect"}

    def set_folders(self, folders: list[str]) -> None:
        self.sync_filters["folder_paths"] = {"operator": "in", "value": folders, "type": "list"}

    def set_extensions(self, operator: str, extensions: list[str]) -> None:
        self.sync_filters["file_extensions"] = {"operator": operator, "value": extensions, "type": "multiselect"}

    def set_dates(self, filter_name: str, after: datetime | None = None, before: datetime | None = None) -> None:
        """A date filter as the UI saves it: epoch-ms bounds, relative ranges already made absolute."""
        operator = "is_between" if after and before else "is_after" if after else "is_before"
        bounds = {"start": after, "end": before}
        value = {k: int(v.timestamp() * 1000) if v else None for k, v in bounds.items()}
        self.sync_filters[filter_name] = {"operator": operator, "value": value, "type": "datetime"}

    async def get_config(self, path: str, default: object = None, **_: object) -> object:
        if path == f"/services/connectors/{CONNECTOR_ID}/config":
            self.reads += 1
            if self.fail_mode and self.reads > self.fail_from:
                if self.fail_mode == "raise":
                    raise ConnectionError("configuration store unavailable")
                return None
            return {"auth": {}, "filters": {"sync": {"values": dict(self.sync_filters)}}}
        return default


def make_connector(
    kind: str, store: FakeObjectStore, db: FakeRecordsDb, checkpoints: FakeCheckpointStore, config: FakeConfigService,
    scope: str = "team",
) -> BaseConnector:
    """A connector of ``kind`` (team-scoped unless told) wired to the fakes, as ``init()`` would leave it for one bucket."""
    logger = logging.getLogger(f"test.objstore.{kind}")
    args = (logger, db, checkpoints, config, CONNECTOR_ID, scope, "creator-1")
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
    connector.list_page_size = 2
    return connector


def runner_graph() -> AsyncMock:
    """The graph provider ``run_sync_task`` writes the app's status to.

    Its ``unlink_nodes_missed_by_full_sync`` is the step after a full sync that strips the
    edges of every record that sync did not write.
    """
    from app.config.constants.arangodb import AppStatus

    graph = AsyncMock()
    graph.get_document = AsyncMock(return_value={"id": CONNECTOR_ID, "status": AppStatus.FULL_SYNCING.value})
    graph.unlink_nodes_missed_by_full_sync = AsyncMock(return_value=(0, True))
    return graph


async def run_full_sync_task(connector: BaseConnector, graph: AsyncMock) -> None:
    """Run a full sync the way the scheduler does, through ``run_sync_task``; what ``run_sync`` raises is raised too."""
    from app.config.constants.arangodb import AppStatus
    from app.connectors.core.sync.sync_runner import run_sync_task

    await run_sync_task(
        connector, CONNECTOR_ID, graph, logging.getLogger("test.objstore.runner"),
        start_status=AppStatus.FULL_SYNCING.value,
    )


def idle_write(graph: AsyncMock) -> dict[str, Any]:
    """The finalizer's IDLE write, which also carries pendingFullSync when a full sync is owed."""
    from app.config.constants.arangodb import AppStatus

    writes = [c.args[2] for c in graph.update_node.await_args_list if c.args[2].get("status") == AppStatus.IDLE.value]
    assert len(writes) == 1, writes
    return writes[0]


async def sync_with_failed_listing(connector: BaseConnector) -> None:
    """Azure Blob fails the run when a listing fails, so it shows as failed (PP-334); the others return."""
    import pytest

    from app.connectors.sources.azure_blob.connector import AzureBlobConnector

    if isinstance(connector, AzureBlobConnector):
        with pytest.raises(RuntimeError, match="sync failed"):
            await connector.run_incremental_sync()
    else:
        await connector.run_incremental_sync()
