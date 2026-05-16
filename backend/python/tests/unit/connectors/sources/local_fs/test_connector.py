"""Unit tests for :mod:`app.connectors.sources.local_fs.connector`.

``connector.py`` registers via ``ConnectorBuilder.build_decorator``, which imports
``connector_registry`` and thus ``ConnectorAppContainer``. Install lightweight
``sys.modules`` shims *before* importing the connector (same pattern as
``test_mariadb_client.py``) so the full DI graph is not loaded.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import sys
import types
from pathlib import Path
from typing import Optional
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# --- Import shims (must run before ``from app.connectors...connector import``) ---
if "app.containers.connector" not in sys.modules:
    _stub_container = types.ModuleType("app.containers.connector")

    class _ContainerMeta(type):
        def __getattr__(cls, name):
            return None

    class _ConnectorAppContainer(metaclass=_ContainerMeta):
        pass

    _stub_container.ConnectorAppContainer = _ConnectorAppContainer
    sys.modules["app.containers.connector"] = _stub_container

if "redis" not in sys.modules:
    _redis_exc = types.ModuleType("redis.exceptions")
    _redis_exc.ConnectionError = type("RedisConnectionError", (Exception,), {})
    _redis_exc.TimeoutError = type("RedisTimeoutError", (Exception,), {})
    sys.modules["redis.exceptions"] = _redis_exc

    _redis_backoff = types.ModuleType("redis.backoff")

    class _ExponentialBackoff:
        pass

    _redis_backoff.ExponentialBackoff = _ExponentialBackoff
    sys.modules["redis.backoff"] = _redis_backoff

    _redis_retry = types.ModuleType("redis.asyncio.retry")

    class _Retry:
        pass

    _redis_retry.Retry = _Retry
    sys.modules["redis.asyncio.retry"] = _redis_retry

    _redis_asyncio = types.ModuleType("redis.asyncio")
    _redis_asyncio.Redis = type("Redis", (), {})
    sys.modules["redis.asyncio"] = _redis_asyncio

    _redis = types.ModuleType("redis")
    _redis.asyncio = _redis_asyncio
    sys.modules["redis"] = _redis

if "etcd3" not in sys.modules:
    _etcd3 = types.ModuleType("etcd3")
    _etcd3.client = type("client", (), {})
    sys.modules["etcd3"] = _etcd3
# --- end shims ---

import aiohttp  # noqa: E402
from fastapi import HTTPException  # noqa: E402

from app.config.constants.arangodb import Connectors, ProgressStatus  # noqa: E402
from app.config.constants.http_status_code import HttpStatusCode  # noqa: E402
from app.connectors.core.registry.filters import (  # noqa: E402
    BooleanOperator,
    DatetimeOperator,
    Filter,
    FilterCollection,
    FilterType,
    IndexingFilterKey,
    MultiselectOperator,
    SyncFilterKey,
)
from app.connectors.sources.local_fs.connector import (  # noqa: E402
    LOCAL_FS_CONNECTOR_NAME,
    LOCAL_FS_STORAGE_PATH_PREFIX,
    LocalFsApp,
    LocalFsConnector,
    SYNC_ROOT_PATH_KEY,
    _file_stat_matches_date_filters as local_fs_passes_date_filters,
    _get_created_timestamp_ms as stat_created_epoch_ms,
    _get_sync_config_value as sync_value_from_config,
    _parse_sync_batch_size as parse_batch_size_from_sync,
    _parse_sync_settings as read_sync_settings_from_config,
    _validate_sync_root_path as validate_host_path,
)
from app.connectors.sources.local_fs.models import LocalFsFileEvent  # noqa: E402
from app.models.entities import FileRecord, OriginTypes, RecordType, RecordGroupType, User  # noqa: E402
from app.models.permission import PermissionType  # noqa: E402


class TestLocalFsApp:
    def test_init_sets_connector_type(self):
        app = LocalFsApp("conn-x")
        assert app.get_connector_id() == "conn-x"


@pytest.fixture
def folder_connector() -> LocalFsConnector:
    logger = MagicMock()
    proc = MagicMock()
    proc.org_id = "org-1"
    return LocalFsConnector(
        logger,
        proc,
        MagicMock(),
        MagicMock(),
        "connector-instance-1",
        "personal",
        "test-user",
    )


class TestLocalFsConnectorHelpers:
    def test_record_group_external_id(self, folder_connector: LocalFsConnector):
        assert folder_connector._record_group_external_id() == (
            "local_fs:connector-instance-1"
        )

    def test_external_record_id_normalized(self, folder_connector: LocalFsConnector):
        a = folder_connector._external_record_id_for_rel_path("a\\b.txt")
        b = folder_connector._external_record_id_for_rel_path("a/b.txt")
        assert a == b

    def test_external_record_id_nfc_equivalent_to_nfd(
        self, folder_connector: LocalFsConnector
    ):
        # macOS APFS often hands NFD-encoded filenames to chokidar; user-space
        # APIs use NFC. Both forms must hash identically.
        nfc = "café.txt"            # café in NFC
        nfd = "café.txt"           # café in NFD
        assert nfc != nfd
        assert (
            folder_connector._external_record_id_for_rel_path(nfc)
            == folder_connector._external_record_id_for_rel_path(nfd)
        )

    def test_extract_storage_document_id_top_level_id(self):
        assert (
            LocalFsConnector._extract_storage_document_id({"_id": "abc"}) == "abc"
        )
        assert (
            LocalFsConnector._extract_storage_document_id({"id": "xyz"}) == "xyz"
        )
        assert (
            LocalFsConnector._extract_storage_document_id({"documentId": "qq"})
            == "qq"
        )

    def test_extract_storage_document_id_mongo_extended_oid(self):
        assert (
            LocalFsConnector._extract_storage_document_id({"_id": {"$oid": "m1"}})
            == "m1"
        )

    def test_extract_storage_document_id_wrapped_response(self):
        # Some internal callers wrap the document under data/document/result.
        assert (
            LocalFsConnector._extract_storage_document_id(
                {"data": {"_id": "wrapped"}}
            )
            == "wrapped"
        )
        assert (
            LocalFsConnector._extract_storage_document_id(
                {"document": {"id": "doc-x"}}
            )
            == "doc-x"
        )

    def test_extract_storage_document_id_rejects_non_string_id(self):
        # {"_id": false} or {"_id": [...]} should NOT silently produce a string;
        # must surface as a clean BAD_GATEWAY rather than letting str(False)
        # flow through as a fake document id.
        with pytest.raises(HTTPException) as ei:
            LocalFsConnector._extract_storage_document_id({"_id": False})
        assert ei.value.status_code == HttpStatusCode.BAD_GATEWAY.value
        with pytest.raises(HTTPException) as ei:
            LocalFsConnector._extract_storage_document_id({"_id": ["a"]})
        assert ei.value.status_code == HttpStatusCode.BAD_GATEWAY.value

    def test_decode_storage_buffer_payload_node_buffer_envelope(self):
        body = LocalFsConnector._decode_storage_buffer_payload(
            {"type": "Buffer", "data": [104, 105]}
        )
        assert body == b"hi"

    def test_decode_storage_buffer_payload_data_wrapped(self):
        body = LocalFsConnector._decode_storage_buffer_payload(
            {"data": {"type": "Buffer", "data": [65, 66, 67]}}
        )
        assert body == b"ABC"

    def test_decode_storage_buffer_payload_unknown_shape_raises(self):
        with pytest.raises(HTTPException) as ei:
            LocalFsConnector._decode_storage_buffer_payload({"weird": "x"})
        assert ei.value.status_code == HttpStatusCode.BAD_GATEWAY.value

    def test_require_org_id_raises_when_unset(
        self, folder_connector: LocalFsConnector
    ):
        folder_connector.data_entities_processor.org_id = None
        with pytest.raises(HTTPException) as ei:
            folder_connector._require_org_id()
        assert ei.value.status_code == HttpStatusCode.BAD_REQUEST.value

    def test_resolve_event_file_path_ok(self, folder_connector: LocalFsConnector, tmp_path: Path):
        root = tmp_path / "root"
        root.mkdir()
        f = root / "sub" / "f.txt"
        f.parent.mkdir()
        f.write_text("x", encoding="utf-8")
        p = folder_connector._resolve_event_file_path(root, "sub/f.txt")
        assert p.is_file()

    def test_resolve_event_file_path_rejects_escape(
        self, folder_connector: LocalFsConnector, tmp_path: Path
    ):
        root = tmp_path / "root"
        root.mkdir()
        with pytest.raises(HTTPException) as ei:
            folder_connector._resolve_event_file_path(root, "../outside")
        assert ei.value.status_code == HttpStatusCode.BAD_REQUEST.value

    def test_parse_user_from_graph_result_none(self, folder_connector: LocalFsConnector):
        assert folder_connector._parse_user_from_graph_result(None) is None

    def test_parse_user_from_graph_result_passthrough(
        self, folder_connector: LocalFsConnector
    ):
        u = User(email="a@b.com", id="u1")
        assert folder_connector._parse_user_from_graph_result(u) is u

    def test_parse_user_from_graph_result_from_dict(
        self, folder_connector: LocalFsConnector
    ):
        u = folder_connector._parse_user_from_graph_result(
            {"id": "x", "email": "e@x.com", "orgId": "o1"}
        )
        assert u is not None
        assert u.id == "x"
        assert u.email == "e@x.com"

    def test_extension_allowed_empty_filter(self, folder_connector: LocalFsConnector):
        coll = FilterCollection(filters=[])
        assert folder_connector._extension_allowed(Path("a.PDF"), coll) is True

    def test_extension_allowed_restricted(self, folder_connector: LocalFsConnector):
        coll = FilterCollection(
            filters=[
                Filter(
                    key=SyncFilterKey.FILE_EXTENSIONS.value,
                    type=FilterType.MULTISELECT,
                    operator=MultiselectOperator.IN,
                    value=["pdf", "txt"],
                )
            ]
        )
        assert folder_connector._extension_allowed(Path("x.pdf"), coll) is True
        assert folder_connector._extension_allowed(Path("x.md"), coll) is False

    def test_build_file_record_sets_indexing_off_when_files_disabled(
        self, folder_connector: LocalFsConnector, tmp_path: Path
    ):
        root = tmp_path
        f = root / "n.txt"
        f.write_text("hi", encoding="utf-8")
        st = f.stat()
        owner = User(email="o@x.com", id="owner-1", org_id="org-1")
        folder_connector._owner_user_for_permissions = owner
        indexing = FilterCollection(
            filters=[
                Filter(
                    key=IndexingFilterKey.FILES.value,
                    type=FilterType.BOOLEAN,
                    operator=BooleanOperator.IS,
                    value=False,
                )
            ]
        )
        rec, perms = folder_connector._build_file_record(
            f,
            root,
            "rg-ext",
            indexing,
            st=st,
        )
        assert isinstance(rec, FileRecord)
        assert rec.local_fs_relative_path == "n.txt"
        assert rec.indexing_status == ProgressStatus.AUTO_INDEX_OFF.value
        assert len(perms) == 1
        assert perms[0].type == PermissionType.OWNER

    def test_build_file_record_implicit_stat_when_st_omitted(
        self, folder_connector: LocalFsConnector, tmp_path: Path
    ):
        """When ``st`` is omitted, the connector calls ``abs_path.stat()`` (line 542–543)."""
        root = tmp_path
        f = root / "implicit.txt"
        f.write_bytes(b"12345")
        folder_connector._owner_user_for_permissions = User(
            email="o@x.com", id="owner-1", org_id="org-1"
        )
        rec, _perms = folder_connector._build_file_record(
            f,
            root,
            "rg-ext",
            FilterCollection(filters=[]),
            st=None,
            owner=None,
        )
        assert rec.size_in_bytes == 5
        assert rec.local_fs_relative_path == "implicit.txt"

    def test_build_file_record_empty_permissions_without_owner(
        self, folder_connector: LocalFsConnector, tmp_path: Path
    ):
        """No ``owner`` and no ``_owner_user_for_permissions`` ⇒ no OWNER rows."""
        root = tmp_path
        f = root / "solo.txt"
        f.write_text("x", encoding="utf-8")
        folder_connector._owner_user_for_permissions = None
        rec, perms = folder_connector._build_file_record(
            f,
            root,
            "rg-ext",
            FilterCollection(filters=[]),
            st=f.stat(),
            owner=None,
        )
        assert isinstance(rec, FileRecord)
        assert rec.local_fs_relative_path == "solo.txt"
        assert perms == []

    def test_build_storage_file_record_indexing_off_and_no_owner_perms(
        self, folder_connector: LocalFsConnector
    ):
        """Storage-path records: FILES filter off and no owner ⇒ no permissions rows."""
        ev = LocalFsFileEvent(
            type="CREATED",
            path="x.txt",
            oldPath=None,
            timestamp=1_700_000_000,
            size=4,
            isDirectory=False,
            sha256=None,
            mimeType=None,
        )
        folder_connector._owner_user_for_permissions = None
        indexing = FilterCollection(
            filters=[
                Filter(
                    key=IndexingFilterKey.FILES.value,
                    type=FilterType.BOOLEAN,
                    operator=BooleanOperator.IS,
                    value=False,
                )
            ]
        )
        rec, perms = folder_connector._build_storage_file_record(
            "folder/x.txt",
            ev,
            "doc-storage-1",
            "rg-ext",
            indexing,
            len(b"data"),
            owner=None,
        )
        assert rec.indexing_status == ProgressStatus.AUTO_INDEX_OFF.value
        assert rec.path == f"{LOCAL_FS_STORAGE_PATH_PREFIX}doc-storage-1"
        assert rec.local_fs_relative_path == "folder/x.txt"
        assert perms == []

    def test_to_app_user(self, folder_connector: LocalFsConnector):
        u = User(email="u@x.com", id="uid", org_id="org-1", full_name="U")
        app_u = folder_connector._to_app_user(u)
        assert app_u.email == "u@x.com"
        assert app_u.connector_id == "connector-instance-1"

    def test_reindex_records_empty_noop(self, folder_connector: LocalFsConnector):
        async def _run() -> None:
            folder_connector.data_entities_processor.reindex_existing_records = AsyncMock()
            await folder_connector.reindex_records([])
            folder_connector.data_entities_processor.reindex_existing_records.assert_not_awaited()

        asyncio.run(_run())

    def test_reindex_records_delegates_to_processor(
        self, folder_connector: LocalFsConnector
    ):
        async def _run() -> None:
            folder_connector.data_entities_processor.reindex_existing_records = AsyncMock()
            rec = MagicMock()
            await folder_connector.reindex_records([rec])
            folder_connector.data_entities_processor.reindex_existing_records.assert_awaited_once_with(
                [rec]
            )

        asyncio.run(_run())


@pytest.mark.asyncio
class TestLocalFsConnectorAsync:
    async def test_apply_file_event_batch_no_sync_root(self, folder_connector: LocalFsConnector):
        folder_connector.config_service.get_config = AsyncMock(
            return_value={"sync": {SYNC_ROOT_PATH_KEY: ""}}
        )
        with pytest.raises(HTTPException) as ei:
            await folder_connector.apply_file_event_batch([])
        assert ei.value.status_code == HttpStatusCode.BAD_REQUEST.value
        assert "not configured" in ei.value.detail.lower()

    async def test_apply_file_event_batch_invalid_path(self, folder_connector: LocalFsConnector):
        folder_connector.config_service.get_config = AsyncMock(
            return_value={"sync": {SYNC_ROOT_PATH_KEY: "/nonexistent/path/xyz123"}}
        )
        with pytest.raises(HTTPException) as ei:
            await folder_connector.apply_file_event_batch([])
        assert ei.value.status_code == HttpStatusCode.BAD_REQUEST.value

    async def test_apply_file_event_batch_rejects_directory_event(
        self, folder_connector: LocalFsConnector, tmp_path: Path
    ):
        folder_connector.config_service.get_config = AsyncMock(
            return_value={"sync": {SYNC_ROOT_PATH_KEY: str(tmp_path)}}
        )
        user = User(email="u@x.com", id="u1", org_id="org-1")
        txn = MagicMock()
        txn.__aenter__ = AsyncMock(return_value=txn)
        txn.__aexit__ = AsyncMock(return_value=None)
        txn.graph_provider.get_document = AsyncMock(
            return_value={"createdBy": "u1"}
        )
        txn.get_user_by_user_id = AsyncMock(return_value=user)
        folder_connector.data_store_provider.transaction = MagicMock(
            return_value=txn
        )
        with patch(
            "app.connectors.sources.local_fs.connector.load_connector_filters",
            new=AsyncMock(
                return_value=(FilterCollection(filters=[]), FilterCollection(filters=[]))
            ),
        ):
            folder_connector.data_entities_processor.on_new_app_users = AsyncMock()
            folder_connector.data_entities_processor.on_new_record_groups = AsyncMock()
            ev = LocalFsFileEvent(
                type="CREATED",
                path="x",
                oldPath=None,
                timestamp=1,
                size=1,
                isDirectory=True,
            )
            with pytest.raises(HTTPException) as ei:
                await folder_connector.apply_file_event_batch([ev])
            assert ei.value.status_code == HttpStatusCode.BAD_REQUEST.value

    async def test_stream_record_returns_bytes(
        self, folder_connector: LocalFsConnector, tmp_path: Path
    ):
        f = tmp_path / "blob.bin"
        f.write_bytes(b"hello-stream")
        folder_connector.config_service.get_config = AsyncMock(
            return_value={"sync": {SYNC_ROOT_PATH_KEY: str(tmp_path)}}
        )
        rec = FileRecord(
            record_name="blob.bin",
            record_type=RecordType.FILE,
            external_record_id="e1",
            version=0,
            origin=OriginTypes.CONNECTOR,
            connector_name=Connectors.LOCAL_FS,
            connector_id="c1",
            is_file=True,
            path=str(f),
            mime_type="application/octet-stream",
            record_group_type=RecordGroupType.DRIVE,
        )
        resp = await folder_connector.stream_record(rec)
        chunks: list[bytes] = []
        async for chunk in resp.body_iterator:
            chunks.append(chunk)
        body = b"".join(chunks)
        assert body == b"hello-stream"
        content_disposition = resp.headers.get("content-disposition", "")
        assert 'attachment; filename="blob.bin"' in content_disposition

    async def test_stream_record_uses_safe_content_disposition_for_unicode_name(
        self, folder_connector: LocalFsConnector, tmp_path: Path
    ):
        f = tmp_path / "unicode.bin"
        f.write_bytes(b"hello-unicode")
        folder_connector.config_service.get_config = AsyncMock(
            return_value={"sync": {SYNC_ROOT_PATH_KEY: str(tmp_path)}}
        )
        rec = FileRecord(
            record_name="3.10.12\u202fPM.png",
            record_type=RecordType.FILE,
            external_record_id="e2",
            version=0,
            origin=OriginTypes.CONNECTOR,
            connector_name=Connectors.LOCAL_FS,
            connector_id="c1",
            is_file=True,
            path=str(f),
            mime_type="application/octet-stream",
            record_group_type=RecordGroupType.DRIVE,
        )
        resp = await folder_connector.stream_record(rec)
        content_disposition = resp.headers.get("content-disposition", "")
        # U+202F is stripped by sanitize_filename_for_content_disposition (latin-1).
        assert "\u202f" not in content_disposition
        assert 'attachment; filename="3.10.12PM.png"' in content_disposition

    async def test_stream_record_storage_path_delegates_to_storage(
        self, folder_connector: LocalFsConnector
    ):
        rec = FileRecord(
            record_name="blob.bin",
            record_type=RecordType.FILE,
            external_record_id="e1",
            version=0,
            origin=OriginTypes.CONNECTOR,
            connector_name=Connectors.LOCAL_FS,
            connector_id="c1",
            is_file=True,
            path=f"{LOCAL_FS_STORAGE_PATH_PREFIX}doc-123",
            mime_type="application/octet-stream",
            record_group_type=RecordGroupType.DRIVE,
        )
        expected = MagicMock()
        folder_connector._stream_storage_record = AsyncMock(return_value=expected)

        resp = await folder_connector.stream_record(rec)

        assert resp is expected
        folder_connector._stream_storage_record.assert_awaited_once_with(rec, "doc-123")

    async def test_apply_file_event_batch_reset_before_apply_rebuilds_from_disk(
        self, folder_connector: LocalFsConnector, tmp_path: Path
    ):
        fresh = tmp_path / "fresh.txt"
        fresh.write_text("hello reset", encoding="utf-8")

        folder_connector.config_service.get_config = AsyncMock(
            return_value={"sync": {SYNC_ROOT_PATH_KEY: str(tmp_path)}}
        )
        user = User(email="u@x.com", id="u1", org_id="org-1")
        txn = MagicMock()
        txn.__aenter__ = AsyncMock(return_value=txn)
        txn.__aexit__ = AsyncMock(return_value=None)
        txn.graph_provider.get_document = AsyncMock(
            return_value={"createdBy": "u1"}
        )
        txn.get_user_by_user_id = AsyncMock(return_value=user)
        stale_1 = MagicMock(external_record_id="stale-1")
        stale_2 = MagicMock(external_record_id="stale-2")
        txn.get_records_by_status = AsyncMock(side_effect=[[stale_1, stale_2], []])
        txn.delete_record_by_external_id = AsyncMock()
        folder_connector.data_store_provider.transaction = MagicMock(
            return_value=txn
        )

        with patch(
            "app.connectors.sources.local_fs.connector.load_connector_filters",
            new=AsyncMock(
                return_value=(FilterCollection(filters=[]), FilterCollection(filters=[]))
            ),
        ):
            folder_connector.data_entities_processor.on_new_app_users = AsyncMock()
            folder_connector.data_entities_processor.on_new_record_groups = AsyncMock()
            folder_connector.data_entities_processor.on_new_records = AsyncMock()
            stats = await folder_connector.apply_file_event_batch(
                [
                    LocalFsFileEvent(
                        type="CREATED",
                        path="fresh.txt",
                        oldPath=None,
                        timestamp=1,
                        size=fresh.stat().st_size,
                        isDirectory=False,
                    )
                ],
                reset_before_apply=True,
            )

        assert stats.deleted == 2
        assert stats.processed == 1
        assert txn.delete_record_by_external_id.await_count == 2
        txn.delete_record_by_external_id.assert_any_await(
            folder_connector.connector_id, "stale-1", user.id
        )
        txn.delete_record_by_external_id.assert_any_await(
            folder_connector.connector_id, "stale-2", user.id
        )
        folder_connector.data_entities_processor.on_new_records.assert_awaited()

    async def test_stream_record_not_file_record(self, folder_connector: LocalFsConnector):
        from app.models.entities import Record

        rec = Record(
            record_name="x",
            record_type=RecordType.FILE,
            external_record_id="e",
            version=0,
            origin=OriginTypes.CONNECTOR,
            connector_name=Connectors.LOCAL_FS,
            connector_id="c1",
        )
        with pytest.raises(HTTPException) as ei:
            await folder_connector.stream_record(rec)
        assert ei.value.status_code == HttpStatusCode.BAD_REQUEST.value

    async def test_stream_record_rejects_path_outside_sync_root(
        self, folder_connector: LocalFsConnector, tmp_path: Path
    ):
        """Paths must stay under the configured sync root (defense in depth)."""
        safe = tmp_path / "allowed.txt"
        safe.write_text("ok", encoding="utf-8")
        outside = tmp_path.parent / f"outside-localfs-{tmp_path.name}.txt"
        outside.write_text("secret", encoding="utf-8")
        folder_connector.config_service.get_config = AsyncMock(
            return_value={"sync": {SYNC_ROOT_PATH_KEY: str(tmp_path)}}
        )
        rec = FileRecord(
            record_name="outside.txt",
            record_type=RecordType.FILE,
            external_record_id="e-out",
            version=0,
            origin=OriginTypes.CONNECTOR,
            connector_name=Connectors.LOCAL_FS,
            connector_id="c1",
            is_file=True,
            path=str(outside),
            mime_type="text/plain",
            record_group_type=RecordGroupType.DRIVE,
        )
        with pytest.raises(HTTPException) as ei:
            await folder_connector.stream_record(rec)
        assert ei.value.status_code == HttpStatusCode.FORBIDDEN.value
        outside.unlink(missing_ok=True)

    async def test_get_filter_options_empty(self, folder_connector: LocalFsConnector):
        out = await folder_connector.get_filter_options("anything")
        assert out.success is True
        assert out.options == []

    async def test_test_connection_empty_root_ok(self, folder_connector: LocalFsConnector):
        folder_connector.sync_root_path = ""
        assert await folder_connector.test_connection_and_access() is True

    async def test_get_signed_url_returns_none(self, folder_connector: LocalFsConnector):
        """Local FS does not expose signed URLs (files are local or storage-backed)."""
        assert await folder_connector.get_signed_url(MagicMock()) is None

    async def test_test_connection_invalid_root_is_non_blocking(
        self, folder_connector: LocalFsConnector
    ):
        folder_connector.sync_root_path = "/nonexistent/path/for-local-fs"
        assert await folder_connector.test_connection_and_access() is True
        folder_connector.logger.warning.assert_called()

    async def test_init_no_config_ok(self, folder_connector: LocalFsConnector):
        folder_connector.config_service.get_config = AsyncMock(return_value=None)
        assert await folder_connector.init() is True

    async def test_reload_sync_settings_reads_nested_custom_values(
        self, folder_connector: LocalFsConnector, tmp_path: Path
    ):
        folder_connector.config_service.get_config = AsyncMock(
            return_value={
                "sync": {
                    "customValues": {
                        "sync_root_path": str(tmp_path),
                        "include_subfolders": "false",
                        "batchSize": "11",
                    }
                }
            }
        )

        await folder_connector._reload_sync_settings()

        assert folder_connector.sync_root_path == str(tmp_path)
        assert folder_connector.include_subfolders is False
        assert folder_connector.batch_size == 11

    async def test_apply_uploaded_file_event_batch_uses_storage_without_backend_path(
        self, folder_connector: LocalFsConnector, tmp_path: Path
    ):
        folder_connector.config_service.get_config = AsyncMock(
            return_value={"sync": {"customValues": {SYNC_ROOT_PATH_KEY: str(tmp_path / "desktop-only")}}}
        )
        user = User(email="u@x.com", id="u1", org_id="org-1")
        txn = MagicMock()
        txn.__aenter__ = AsyncMock(return_value=txn)
        txn.__aexit__ = AsyncMock(return_value=None)
        txn.graph_provider.get_document = AsyncMock(return_value={"createdBy": "u1"})
        txn.get_user_by_user_id = AsyncMock(return_value=user)
        txn.get_record_by_external_id = AsyncMock(return_value=None)
        folder_connector.data_store_provider.transaction = MagicMock(return_value=txn)

        async def fake_upload(**kwargs):
            assert kwargs["content"] == b"hello upload"
            assert kwargs["rel_path"] == "notes/a.txt"
            return "doc-123"

        folder_connector._upload_storage_file = AsyncMock(side_effect=fake_upload)

        with patch(
            "app.connectors.sources.local_fs.connector.load_connector_filters",
            new=AsyncMock(return_value=(FilterCollection(filters=[]), FilterCollection(filters=[]))),
        ):
            folder_connector.data_entities_processor.on_new_app_users = AsyncMock()
            folder_connector.data_entities_processor.on_new_record_groups = AsyncMock()
            folder_connector.data_entities_processor.on_new_records = AsyncMock()
            stats = await folder_connector.apply_uploaded_file_event_batch(
                [
                    LocalFsFileEvent(
                        type="CREATED",
                        path="notes/a.txt",
                        timestamp=1000,
                        size=12,
                        isDirectory=False,
                        contentField="file_0",
                        sha256="2d119f1cd272958a492a144af600b9dc36531f73027b34073967345b027021b1",
                        mimeType="text/plain",
                    )
                ],
                {"file_0": b"hello upload"},
            )

        assert stats.processed == 1
        folder_connector.data_entities_processor.on_new_records.assert_awaited_once()
        records = folder_connector.data_entities_processor.on_new_records.await_args.args[0]
        record, permissions = records[0]
        assert record.path == f"{LOCAL_FS_STORAGE_PATH_PREFIX}doc-123"
        assert record.record_name == "a.txt"
        assert record.external_revision_id == "2d119f1cd272958a492a144af600b9dc36531f73027b34073967345b027021b1"
        assert permissions[0].type == PermissionType.OWNER

    async def test_apply_uploaded_delete_removes_storage_document_and_record(
        self, folder_connector: LocalFsConnector, tmp_path: Path
    ):
        folder_connector.config_service.get_config = AsyncMock(
            return_value={"sync": {"customValues": {SYNC_ROOT_PATH_KEY: str(tmp_path / "desktop-only")}}}
        )
        user = User(email="u@x.com", id="u1", org_id="org-1")
        existing = FileRecord(
            record_name="old.txt",
            record_type=RecordType.FILE,
            external_record_id=folder_connector._external_record_id_for_rel_path("old.txt"),
            version=0,
            origin=OriginTypes.CONNECTOR,
            connector_name=Connectors.LOCAL_FS,
            connector_id=folder_connector.connector_id,
            is_file=True,
            path=f"{LOCAL_FS_STORAGE_PATH_PREFIX}doc-del",
            mime_type="text/plain",
            record_group_type=RecordGroupType.DRIVE,
        )
        txn = MagicMock()
        txn.__aenter__ = AsyncMock(return_value=txn)
        txn.__aexit__ = AsyncMock(return_value=None)
        txn.graph_provider.get_document = AsyncMock(return_value={"createdBy": "u1"})
        txn.get_user_by_user_id = AsyncMock(return_value=user)
        txn.get_record_by_external_id = AsyncMock(return_value=existing)
        txn.delete_record_by_external_id = AsyncMock()
        folder_connector.data_store_provider.transaction = MagicMock(return_value=txn)
        folder_connector._delete_storage_document = AsyncMock()

        with patch(
            "app.connectors.sources.local_fs.connector.load_connector_filters",
            new=AsyncMock(return_value=(FilterCollection(filters=[]), FilterCollection(filters=[]))),
        ):
            folder_connector.data_entities_processor.on_new_app_users = AsyncMock()
            folder_connector.data_entities_processor.on_new_record_groups = AsyncMock()
            stats = await folder_connector.apply_uploaded_file_event_batch(
                [
                    LocalFsFileEvent(
                        type="DELETED",
                        path="old.txt",
                        timestamp=1000,
                        isDirectory=False,
                    )
                ],
                {},
            )

        assert stats.deleted == 1
        # Storage GC reuses the open aiohttp session; allow any session arg.
        assert folder_connector._delete_storage_document.await_count == 1
        gc_call = folder_connector._delete_storage_document.await_args
        assert gc_call.args == ("doc-del",)
        assert "session" in gc_call.kwargs
        txn.delete_record_by_external_id.assert_awaited_once_with(
            folder_connector.connector_id,
            folder_connector._external_record_id_for_rel_path("old.txt"),
            user.id,
        )

    async def test_apply_uploaded_rename_upserts_before_deleting_old(
        self, folder_connector: LocalFsConnector, tmp_path: Path
    ):
        """Rename ordering invariant: the new record must be persisted via
        on_new_records before the old record's external_id is removed.
        Without this, a mid-batch failure would drop the old row leaving
        nothing in its place — visible data loss in search.
        """
        folder_connector.config_service.get_config = AsyncMock(
            return_value={"sync": {"customValues": {SYNC_ROOT_PATH_KEY: str(tmp_path / "desktop-only")}}}
        )
        user = User(email="u@x.com", id="u1", org_id="org-1")
        old_ext_id = folder_connector._external_record_id_for_rel_path("a/old.txt")
        existing_old = FileRecord(
            record_name="old.txt",
            record_type=RecordType.FILE,
            external_record_id=old_ext_id,
            version=0,
            origin=OriginTypes.CONNECTOR,
            connector_name=Connectors.LOCAL_FS,
            connector_id=folder_connector.connector_id,
            is_file=True,
            path=f"{LOCAL_FS_STORAGE_PATH_PREFIX}doc-old",
            mime_type="text/plain",
            record_group_type=RecordGroupType.DRIVE,
        )

        # data_store_provider.transaction is called twice: once for the bulk
        # pre-fetch (returns existing_old for old_ext_id; None for new_ext_id),
        # and once when _delete_external_ids runs after the upsert flush.
        bulk_txn = MagicMock()
        bulk_txn.__aenter__ = AsyncMock(return_value=bulk_txn)
        bulk_txn.__aexit__ = AsyncMock(return_value=None)
        bulk_txn.graph_provider.get_document = AsyncMock(return_value={"createdBy": "u1"})
        bulk_txn.get_user_by_user_id = AsyncMock(return_value=user)

        async def _bulk_lookup(connector_id, ext_id):
            if ext_id == old_ext_id:
                return existing_old
            return None

        bulk_txn.get_record_by_external_id = AsyncMock(side_effect=_bulk_lookup)
        bulk_txn.delete_record_by_external_id = AsyncMock()
        folder_connector.data_store_provider.transaction = MagicMock(
            return_value=bulk_txn
        )
        folder_connector._upload_storage_file = AsyncMock(return_value="doc-new")
        folder_connector._delete_storage_document = AsyncMock()

        # Track relative ordering of upsert vs old-delete vs old-blob GC.
        order: list[str] = []
        order_lock = asyncio.Lock()

        async def _record_upsert(_records):
            async with order_lock:
                order.append("upsert_new")

        async def _record_delete(_connector_id, _ext_id, _uid):
            async with order_lock:
                order.append("delete_old_record")

        async def _record_gc(_doc_id, **_kw):
            async with order_lock:
                order.append("gc_old_blob")

        with patch(
            "app.connectors.sources.local_fs.connector.load_connector_filters",
            new=AsyncMock(return_value=(FilterCollection(filters=[]), FilterCollection(filters=[]))),
        ):
            folder_connector.data_entities_processor.on_new_app_users = AsyncMock()
            folder_connector.data_entities_processor.on_new_record_groups = AsyncMock()
            folder_connector.data_entities_processor.on_new_records = AsyncMock(
                side_effect=_record_upsert
            )
            bulk_txn.delete_record_by_external_id = AsyncMock(
                side_effect=_record_delete
            )
            folder_connector._delete_storage_document = AsyncMock(
                side_effect=_record_gc
            )

            await folder_connector.apply_uploaded_file_event_batch(
                [
                    LocalFsFileEvent(
                        type="RENAMED",
                        path="a/new.txt",
                        oldPath="a/old.txt",
                        timestamp=1000,
                        size=4,
                        isDirectory=False,
                        contentField="file_0",
                        sha256=hashlib.sha256(b"data").hexdigest(),
                        mimeType="text/plain",
                    )
                ],
                {"file_0": b"data"},
            )

        # The upsert of the new record MUST land before the old-row delete.
        # The old-blob GC must run after the row is gone (so a half-failed
        # batch can't strand an in-storage blob whose record is still live).
        assert order.index("upsert_new") < order.index("delete_old_record")
        assert order.index("delete_old_record") < order.index("gc_old_blob")
        folder_connector._upload_storage_file.assert_awaited_once()


@pytest.mark.asyncio
async def test_handle_webhook_notification_logs():
    logger = MagicMock()
    proc = MagicMock()
    c = LocalFsConnector(logger, proc, MagicMock(), MagicMock(), "id", "personal", "u")
    c.handle_webhook_notification({})
    logger.debug.assert_called()


@pytest.mark.asyncio
async def test_cleanup_logs():
    logger = MagicMock()
    proc = MagicMock()
    c = LocalFsConnector(logger, proc, MagicMock(), MagicMock(), "id", "personal", "u")
    await c.cleanup()
    logger.info.assert_called()


def test_local_fs_connector_name_constant():
    assert LOCAL_FS_CONNECTOR_NAME == "Local FS"


# ===========================================================================
# Merged from former test_local_fs_connector_helpers.py
# ===========================================================================

def _make_stat(
    *,
    mtime_s: float,
    ctime_s: float,
    birthtime_s: float | None = None,
    size: int = 100,
) -> types.SimpleNamespace:
    ns: dict[str, float | int] = {
        "st_mtime": mtime_s,
        "st_ctime": ctime_s,
        "st_size": size,
    }
    if birthtime_s is not None:
        ns["st_birthtime"] = birthtime_s
    return types.SimpleNamespace(**ns)


def test_stat_created_epoch_ms_prefers_birthtime():
    st = _make_stat(mtime_s=0, ctime_s=0, birthtime_s=2.5)
    assert stat_created_epoch_ms(st) == 2500


def test_stat_created_epoch_ms_falls_back_to_ctime():
    st = _make_stat(mtime_s=0, ctime_s=1.25)
    assert stat_created_epoch_ms(st) == 1250


@pytest.mark.parametrize(
    "sync_cfg, expected",
    [
        ({}, 50),
        ({"batchSize": "10"}, 10),
        ({"batch_size": 3}, 3),
        ({"batchSize": "", "batch_size": "7"}, 7),
        ({"customValues": {"batchSize": "8"}}, 8),
        ({"values": {"batch_size": "9"}}, 9),
        ({"batchSize": "0"}, 1),
        ({"batchSize": "not-int"}, 50),
    ],
)
def test_parse_batch_size_from_sync(sync_cfg, expected):
    assert parse_batch_size_from_sync(sync_cfg) == expected


def test_read_sync_settings_accepts_custom_values_shape():
    root, include, batch_size = read_sync_settings_from_config(
        {
            "sync": {
                "customValues": {
                    "sync_root_path": "/Users/me/Documents",
                    "include_subfolders": "false",
                    "batchSize": "17",
                }
            }
        }
    )

    assert root == "/Users/me/Documents"
    assert include is False
    assert batch_size == 17


def test_read_sync_settings_flat_values_take_priority():
    root, include, batch_size = read_sync_settings_from_config(
        {
            "sync": {
                "sync_root_path": "/server/mount",
                "include_subfolders": True,
                "batchSize": "3",
                "customValues": {
                    "sync_root_path": "/desktop/path",
                    "include_subfolders": "false",
                    "batchSize": "99",
                },
            }
        }
    )

    assert root == "/server/mount"
    assert include is True
    assert batch_size == 3


def test_validate_host_path_empty_ok():
    ok, detail = validate_host_path("   ")
    assert ok is True
    assert detail == ""


def test_validate_host_path_readable_dir(tmp_path: Path):
    d = tmp_path / "sync"
    d.mkdir()
    ok, detail = validate_host_path(str(d))
    assert ok is True
    assert Path(detail).resolve() == d.resolve()


def test_validate_host_path_missing(tmp_path: Path):
    missing = tmp_path / "nope"
    ok, detail = validate_host_path(str(missing))
    assert ok is False
    assert "does not exist" in detail


def test_local_fs_passes_date_filters_no_filters():
    st = _make_stat(mtime_s=1000, ctime_s=1000)
    empty = FilterCollection(filters=[])
    assert local_fs_passes_date_filters(st, empty) is True


def _dt_between_filter(key: str, start_ms: int, end_ms: int) -> Filter:
    return Filter(
        key=key,
        type=FilterType.DATETIME,
        operator=DatetimeOperator.IS_BETWEEN,
        value={"start": start_ms, "end": end_ms},
    )


def test_local_fs_passes_modified_window():
    st = _make_stat(mtime_s=3.0, ctime_s=1.0)
    flt = _dt_between_filter(SyncFilterKey.MODIFIED.value, 2000, 4000)
    coll = FilterCollection(filters=[flt])
    assert local_fs_passes_date_filters(st, coll) is True


def test_local_fs_fails_modified_before_window():
    st = _make_stat(mtime_s=1.0, ctime_s=1.0)
    flt = _dt_between_filter(SyncFilterKey.MODIFIED.value, 2000, 4000)
    coll = FilterCollection(filters=[flt])
    assert local_fs_passes_date_filters(st, coll) is False


def test_local_fs_passes_created_window():
    st = _make_stat(mtime_s=10.0, ctime_s=5.0, birthtime_s=3.0)
    flt = _dt_between_filter(SyncFilterKey.CREATED.value, 2000, 4000)
    coll = FilterCollection(filters=[flt])
    assert local_fs_passes_date_filters(st, coll) is True


def test_local_fs_fails_created_outside_window():
    st = _make_stat(mtime_s=10.0, ctime_s=5.0, birthtime_s=1.0)
    flt = _dt_between_filter(SyncFilterKey.CREATED.value, 2000, 4000)
    coll = FilterCollection(filters=[flt])
    assert local_fs_passes_date_filters(st, coll) is False


def test_local_fs_fails_modified_after_window():
    """Modified time after the upper bound must be filtered out (the diff path
    not exercised by the existing 'before window' test)."""
    st = _make_stat(mtime_s=10.0, ctime_s=1.0)
    flt = _dt_between_filter(SyncFilterKey.MODIFIED.value, 2000, 4000)
    coll = FilterCollection(filters=[flt])
    assert local_fs_passes_date_filters(st, coll) is False


def test_local_fs_passes_when_filter_present_but_empty():
    """An empty datetime filter (no bounds set) is a no-op, not a hard reject."""
    st = _make_stat(mtime_s=10.0, ctime_s=10.0)
    empty = Filter(
        key=SyncFilterKey.MODIFIED.value,
        type=FilterType.DATETIME,
        operator=DatetimeOperator.IS_BETWEEN,
        value={},
    )
    coll = FilterCollection(filters=[empty])
    assert local_fs_passes_date_filters(st, coll) is True


def test_local_fs_combines_modified_and_created_filters():
    """Both filters must hold simultaneously; a passing modified is not enough
    to override a failing created window."""
    st = _make_stat(mtime_s=3.0, ctime_s=5.0, birthtime_s=10.0)  # created at 10000ms
    flt_mod = _dt_between_filter(SyncFilterKey.MODIFIED.value, 2000, 4000)
    flt_cre = _dt_between_filter(SyncFilterKey.CREATED.value, 2000, 4000)
    coll = FilterCollection(filters=[flt_mod, flt_cre])
    assert local_fs_passes_date_filters(st, coll) is False


def test_stat_created_epoch_ms_birthtime_zero_falls_back_to_ctime():
    """Some FAT/ext4 mounts surface birthtime=0 — must NOT be treated as 1970."""
    # The current implementation uses `if birth is not None` rather than truthy
    # check; this test pins down that behavior so a future "if not birth" rewrite
    # would surface here.
    st = _make_stat(mtime_s=0, ctime_s=5.0, birthtime_s=0.0)
    assert stat_created_epoch_ms(st) == 0


# --- _sync_value_from_config -------------------------------------------------

def test_sync_value_flat_takes_priority():
    cfg = {"key": "flat", "values": {"key": "nested-values"}}
    assert sync_value_from_config(cfg, "key") == "flat"


def test_sync_value_falls_through_empty_string():
    """Empty string at the flat level should defer to the nested value, not lock in ''."""
    cfg = {"key": "", "customValues": {"key": "from-custom"}}
    assert sync_value_from_config(cfg, "key") == "from-custom"


def test_sync_value_returns_default_when_missing():
    assert sync_value_from_config({}, "missing", default="d") == "d"
    assert sync_value_from_config({"other": 1}, "missing", default=42) == 42


def test_sync_value_returns_default_for_non_dict_input():
    assert sync_value_from_config("not-a-dict", "k", default=7) == 7  # type: ignore[arg-type]
    assert sync_value_from_config(None, "k", default=None) is None  # type: ignore[arg-type]


def test_sync_value_values_takes_priority_over_custom_values():
    """When both nested keys exist, ``values`` wins over ``customValues``
    (matches the iteration order in the implementation)."""
    cfg = {
        "values": {"key": "from-values"},
        "customValues": {"key": "from-custom"},
    }
    assert sync_value_from_config(cfg, "key") == "from-values"


# --- _parse_batch_size_from_sync edge cases ---------------------------------

def test_parse_batch_size_negative_floored_to_one():
    assert parse_batch_size_from_sync({"batchSize": "-5"}) == 1


def test_parse_batch_size_whitespace_trimmed():
    assert parse_batch_size_from_sync({"batchSize": "  17 "}) == 17


def test_parse_batch_size_falls_back_for_non_dict_sync_cfg():
    # Anything non-dict ⇒ default 50 (must not raise).
    assert parse_batch_size_from_sync(None) == 50  # type: ignore[arg-type]
    assert parse_batch_size_from_sync("oops") == 50  # type: ignore[arg-type]


# --- _read_sync_settings_from_config edge cases -----------------------------

def test_read_sync_settings_none_config_returns_defaults():
    root, include, batch_size = read_sync_settings_from_config(None)
    assert root == ""
    assert include is True  # default
    assert batch_size == 50


def test_read_sync_settings_empty_config_returns_defaults():
    root, include, batch_size = read_sync_settings_from_config({})
    assert root == ""
    assert include is True
    assert batch_size == 50


def test_read_sync_settings_missing_sync_key_returns_defaults():
    root, include, batch_size = read_sync_settings_from_config({"other": "stuff"})
    assert root == ""
    assert include is True
    assert batch_size == 50


def test_read_sync_settings_strips_whitespace_from_root():
    root, _, _ = read_sync_settings_from_config(
        {"sync": {"sync_root_path": "  /some/path  "}}
    )
    assert root == "/some/path"


# --- _validate_host_path edge cases -----------------------------------------

def test_validate_host_path_not_a_directory(tmp_path: Path):
    f = tmp_path / "regular.txt"
    f.write_text("x", encoding="utf-8")
    ok, detail = validate_host_path(str(f))
    assert ok is False
    assert "not a directory" in detail


def test_validate_host_path_resolves_user_expansion(tmp_path: Path, monkeypatch):
    """``~`` must be expanded before the existence check."""
    monkeypatch.setenv("HOME", str(tmp_path))
    ok, detail = validate_host_path("~")
    assert ok is True
    assert Path(detail).resolve() == tmp_path.resolve()


def test_validate_host_path_resolve_raises_oserror(monkeypatch):
    import pathlib

    def _boom(self, *args, **kwargs):
        raise OSError("simulated mount failure")

    monkeypatch.setattr(pathlib.Path, "resolve", _boom)
    ok, detail = validate_host_path("/any/path")
    assert ok is False
    assert "simulated mount failure" in detail


def test_validate_host_path_not_readable(tmp_path: Path, monkeypatch):
    d = tmp_path / "nor"
    d.mkdir()

    def _access(path, mode):
        if mode == os.R_OK:
            return False
        return True

    monkeypatch.setattr(
        "app.connectors.sources.local_fs.connector.os.access",
        _access,
    )
    ok, detail = validate_host_path(str(d))
    assert ok is False
    assert "not readable" in detail


def test_validate_host_path_not_searchable(tmp_path: Path, monkeypatch):
    d = tmp_path / "nox"
    d.mkdir()

    def _access(path, mode):
        if mode == os.R_OK:
            return True
        if mode == os.X_OK:
            return False
        return True

    monkeypatch.setattr(
        "app.connectors.sources.local_fs.connector.os.access",
        _access,
    )
    ok, detail = validate_host_path(str(d))
    assert ok is False
    assert "searchable" in detail


# --- LocalFsConnector static helpers ---------------------------------------

class TestStorageDocumentIdFromPath:
    def test_returns_id_after_prefix(self):
        path = f"{LOCAL_FS_STORAGE_PATH_PREFIX}doc-abc123"
        assert (
            LocalFsConnector._storage_document_id_from_path(path) == "doc-abc123"
        )

    def test_strips_whitespace_after_prefix(self):
        path = f"{LOCAL_FS_STORAGE_PATH_PREFIX}  doc-xyz  "
        assert (
            LocalFsConnector._storage_document_id_from_path(path) == "doc-xyz"
        )

    def test_returns_none_for_empty_or_missing_prefix(self):
        assert LocalFsConnector._storage_document_id_from_path(None) is None
        assert LocalFsConnector._storage_document_id_from_path("") is None
        assert LocalFsConnector._storage_document_id_from_path("/abs/path") is None
        assert (
            LocalFsConnector._storage_document_id_from_path("storagex://other")
            is None
        )

    def test_returns_none_for_prefix_only(self):
        assert (
            LocalFsConnector._storage_document_id_from_path(
                LOCAL_FS_STORAGE_PATH_PREFIX
            )
            is None
        )

    def test_returns_none_for_prefix_plus_whitespace(self):
        assert (
            LocalFsConnector._storage_document_id_from_path(
                f"{LOCAL_FS_STORAGE_PATH_PREFIX}    "
            )
            is None
        )


class TestStorageSafeDocumentName:
    def test_strips_extension_and_path(self):
        assert (
            LocalFsConnector._build_storage_document_name("a/b/notes.txt")
            == "notes"
        )

    def test_handles_windows_separator(self):
        assert (
            LocalFsConnector._build_storage_document_name("a\\b\\notes.txt")
            == "notes"
        )

    def test_returns_file_for_empty(self):
        assert LocalFsConnector._build_storage_document_name("") == "file"
        assert LocalFsConnector._build_storage_document_name("/") == "file"

    def test_truncates_to_180_chars(self):
        long = "x" * 500 + ".txt"
        out = LocalFsConnector._build_storage_document_name(long)
        assert len(out) == 180
        assert out == "x" * 180

    def test_no_extension_keeps_full_name(self):
        assert LocalFsConnector._build_storage_document_name("README") == "README"


class TestStorageUploadFilename:
    def test_keeps_original_name_when_extension_present(self):
        assert (
            LocalFsConnector._build_storage_upload_filename("a/b/c.txt", "text/plain")
            == "c.txt"
        )

    def test_appends_bin_when_no_extension(self):
        assert (
            LocalFsConnector._build_storage_upload_filename("README", "text/plain")
            == "README.bin"
        )

    def test_appends_bin_for_octet_stream_unguessable(self):
        # ``foo.unknownext`` has no mimetype guess ⇒ bin fallback when caller
        # also gave us ``application/octet-stream``.
        assert (
            LocalFsConnector._build_storage_upload_filename(
                "foo.unknownext", "application/octet-stream"
            )
            == "foo.bin"
        )

    def test_replaces_path_separators(self):
        assert (
            LocalFsConnector._build_storage_upload_filename("a/b.txt", "text/plain")
            == "b.txt"
        )

    def test_handles_windows_separator(self):
        assert (
            LocalFsConnector._build_storage_upload_filename("a\\b.txt", "text/plain")
            == "b.txt"
        )


class TestNormalizeUploadedRelPath:
    def test_strips_and_normalizes_separators(self):
        assert (
            LocalFsConnector._normalize_uploaded_rel_path("  a\\b\\c.txt  ")
            == "a/b/c.txt"
        )

    def test_rejects_empty(self):
        with pytest.raises(Exception) as ei:
            LocalFsConnector._normalize_uploaded_rel_path("")
        assert ei.value.status_code == 400  # type: ignore[attr-defined]

    def test_rejects_absolute(self):
        with pytest.raises(Exception) as ei:
            LocalFsConnector._normalize_uploaded_rel_path("/abs/path")
        assert ei.value.status_code == 400  # type: ignore[attr-defined]

    def test_rejects_dot_segments(self):
        for bad in ("a/./b", "a/../b", "..", ".", "a//b"):
            with pytest.raises(Exception) as ei:
                LocalFsConnector._normalize_uploaded_rel_path(bad)
            assert ei.value.status_code == 400, bad  # type: ignore[attr-defined]

    def test_accepts_simple_relative(self):
        assert LocalFsConnector._normalize_uploaded_rel_path("a.txt") == "a.txt"
        assert (
            LocalFsConnector._normalize_uploaded_rel_path("nested/dir/file.txt")
            == "nested/dir/file.txt"
        )


class TestDecodeStorageBufferPayloadCorners:
    def test_empty_buffer_envelope(self):
        body = LocalFsConnector._decode_storage_buffer_payload(
            {"type": "Buffer", "data": []}
        )
        assert body == b""

    def test_raw_bytes_passthrough(self):
        assert (
            LocalFsConnector._decode_storage_buffer_payload(b"raw") == b"raw"
        )
        assert (
            LocalFsConnector._decode_storage_buffer_payload(bytearray(b"ba"))
            == b"ba"
        )

    def test_data_list_without_buffer_type(self):
        # Some legacy callers drop ``"type": "Buffer"`` and just send {"data":[...]}.
        assert (
            LocalFsConnector._decode_storage_buffer_payload({"data": [120, 121]})
            == b"xy"
        )

    def test_data_inner_bytes(self):
        assert (
            LocalFsConnector._decode_storage_buffer_payload({"data": b"raw"})
            == b"raw"
        )

    def test_data_inner_dict_recurses_to_buffer_envelope(self):
        """Exercises the ``inner`` dict branch that delegates back to the decoder."""
        body = LocalFsConnector._decode_storage_buffer_payload(
            {"data": {"type": "Buffer", "data": [90]}}
        )
        assert body == b"Z"


class TestExtractStorageDocumentIdCorners:
    def test_handles_circular_reference_without_recursing(self):
        """Self-referential payload would loop forever without seen-set tracking."""
        d: dict = {"data": None}
        d["data"] = d  # cycle
        with pytest.raises(Exception) as ei:
            LocalFsConnector._extract_storage_document_id(d)
        assert ei.value.status_code == 502  # type: ignore[attr-defined]

    def test_walks_result_wrapper(self):
        assert (
            LocalFsConnector._extract_storage_document_id(
                {"result": {"document": {"_id": "deep"}}}
            )
            == "deep"
        )

    def test_walks_nested_oid_under_wrapped_keys(self):
        assert (
            LocalFsConnector._extract_storage_document_id(
                {
                    "result": {
                        "data": {"_id": {"oid": "from-wrapped-alt"}},
                    }
                }
            )
            == "from-wrapped-alt"
        )

    def test_oid_alternative_lowercase(self):
        # ``oid`` (no $) is an accepted alternative for the Mongo extended form.
        assert (
            LocalFsConnector._extract_storage_document_id({"_id": {"oid": "alt"}})
            == "alt"
        )

    def test_empty_string_id_treated_as_missing(self):
        with pytest.raises(Exception) as ei:
            LocalFsConnector._extract_storage_document_id({"_id": ""})
        assert ei.value.status_code == 502  # type: ignore[attr-defined]


# ===========================================================================
# Merged from former test_connector_storage_and_sync.py
# ===========================================================================



# --------------------------------------------------------------------------- #
# Helpers / fakes                                                             #
# --------------------------------------------------------------------------- #


class _FakeResponse:
    """Minimal aiohttp response that supports `async with` and `.text()`."""

    def __init__(
        self,
        status: int,
        text: str = "",
        *,
        headers: Optional[dict[str, str]] = None,
        raise_on: Optional[Exception] = None,
    ) -> None:
        self.status = status
        self._text = text
        self.headers = headers or {}
        self._raise = raise_on

    async def text(self) -> str:
        if self._raise is not None:
            raise self._raise
        return self._text

    async def __aenter__(self) -> "_FakeResponse":
        if self._raise is not None:
            # aiohttp surfaces both connection errors (ClientError) and
            # asyncio.TimeoutError out of the context-manager entry, so we mimic
            # that here regardless of the exception type the caller queued.
            raise self._raise
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:  # noqa: D401
        return None


class _FakeSession:
    """Fake aiohttp.ClientSession.

    Each call to .post()/.get()/.delete() pops the next queued response so a
    single batch test can chain multiple requests. Records the method/url/headers
    of every call for assertions.
    """

    def __init__(self, responses):
        # responses: list of (method, _FakeResponse) — popped in order.
        self._responses = list(responses)
        self.calls: list[dict] = []

    def _take(self, method: str, url: str, **kwargs) -> _FakeResponse:
        self.calls.append({"method": method, "url": url, **kwargs})
        if not self._responses:
            raise AssertionError(f"Unexpected {method} {url}: no response queued")
        expected_method, response = self._responses.pop(0)
        assert expected_method == method, (
            f"Expected next call to be {expected_method}, got {method}"
        )
        return response

    def post(self, url, *, data=None, headers=None, **kwargs):
        return self._take("post", url, data=data, headers=headers, **kwargs)

    def put(self, url, *, data=None, headers=None, **kwargs):
        return self._take("put", url, data=data, headers=headers, **kwargs)

    def get(self, url, *, headers=None):
        return self._take("get", url, headers=headers)

    def delete(self, url, *, headers=None):
        return self._take("delete", url, headers=headers)

    async def __aenter__(self) -> "_FakeSession":
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:  # noqa: D401
        return None


def _patch_session(monkeypatch, session: _FakeSession) -> None:
    """Make `aiohttp.ClientSession(...)` return our fake session."""
    monkeypatch.setattr(
        "app.connectors.sources.local_fs.connector.aiohttp.ClientSession",
        lambda *a, **kw: session,
    )




# --------------------------------------------------------------------------- #
# init() and test_connection_and_access()                                     #
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
class TestInit:
    async def test_init_with_valid_config(self, folder_connector, tmp_path):
        folder_connector.config_service.get_config = AsyncMock(
            return_value={
                "sync": {
                    SYNC_ROOT_PATH_KEY: str(tmp_path),
                    "include_subfolders": "false",
                    "batchSize": "7",
                }
            }
        )
        ok = await folder_connector.init()
        assert ok is True
        assert folder_connector.sync_root_path == str(tmp_path)
        assert folder_connector.include_subfolders is False
        assert folder_connector.batch_size == 7
        folder_connector.logger.info.assert_called()

    async def test_init_with_invalid_path_warns(self, folder_connector):
        folder_connector.config_service.get_config = AsyncMock(
            return_value={
                "sync": {SYNC_ROOT_PATH_KEY: "/does/not/exist/local-fs-test"}
            }
        )
        ok = await folder_connector.init()
        assert ok is True
        folder_connector.logger.warning.assert_called()

    async def test_init_with_empty_path_logs_setup_hint(self, folder_connector):
        folder_connector.config_service.get_config = AsyncMock(
            return_value={"sync": {SYNC_ROOT_PATH_KEY: ""}}
        )
        ok = await folder_connector.init()
        assert ok is True
        # Logged the "complete setup in the app" info line.
        folder_connector.logger.info.assert_called()

    async def test_init_swallows_exceptions_returns_false(self, folder_connector):
        folder_connector.config_service.get_config = AsyncMock(
            side_effect=RuntimeError("etcd down")
        )
        ok = await folder_connector.init()
        assert ok is False
        folder_connector.logger.error.assert_called()


# --------------------------------------------------------------------------- #
# _iter_file_paths                                                            #
# --------------------------------------------------------------------------- #


class TestIterFilePaths:
    def test_recurses_when_include_subfolders_true(self, folder_connector, tmp_path):
        (tmp_path / "a.txt").write_text("a", encoding="utf-8")
        sub = tmp_path / "sub"
        sub.mkdir()
        (sub / "b.txt").write_text("b", encoding="utf-8")

        folder_connector.include_subfolders = True
        out = folder_connector._iter_file_paths(tmp_path)

        names = sorted(p.name for p in out)
        assert names == ["a.txt", "b.txt"]

    def test_top_level_only_when_include_subfolders_false(
        self, folder_connector, tmp_path
    ):
        (tmp_path / "a.txt").write_text("a", encoding="utf-8")
        sub = tmp_path / "sub"
        sub.mkdir()
        (sub / "b.txt").write_text("b", encoding="utf-8")

        folder_connector.include_subfolders = False
        out = folder_connector._iter_file_paths(tmp_path)

        assert [p.name for p in out] == ["a.txt"]


# --------------------------------------------------------------------------- #
# _storage_base_url and _storage_token                                        #
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
class TestStorageBaseUrl:
    async def test_uses_endpoint_from_dict_config(self, folder_connector):
        folder_connector.config_service.get_config = AsyncMock(
            return_value={"storage": {"endpoint": "http://storage.local:9000/"}}
        )
        url = await folder_connector._storage_base_url()
        assert url == "http://storage.local:9000"

    async def test_parses_json_string_config(self, folder_connector):
        folder_connector.config_service.get_config = AsyncMock(
            return_value=json.dumps({"storage": {"endpoint": "http://s.local"}})
        )
        url = await folder_connector._storage_base_url()
        assert url == "http://s.local"

    async def test_falls_back_to_default_on_bad_json(self, folder_connector):
        folder_connector.config_service.get_config = AsyncMock(
            return_value="not-valid-json{{"
        )
        url = await folder_connector._storage_base_url()
        # Default endpoint is whatever DefaultEndpoints.STORAGE_ENDPOINT.value is;
        # the only contract is that we got SOMETHING and it's not the bad string.
        assert url and "{" not in url

    async def test_falls_back_to_default_on_none(self, folder_connector):
        folder_connector.config_service.get_config = AsyncMock(return_value=None)
        url = await folder_connector._storage_base_url()
        assert url

    async def test_uses_cache_when_set(self, folder_connector):
        folder_connector._batch_storage_url_cache = "http://cached"
        folder_connector.config_service.get_config = AsyncMock(
            return_value={"storage": {"endpoint": "http://other"}}
        )
        url = await folder_connector._storage_base_url()
        assert url == "http://cached"
        folder_connector.config_service.get_config.assert_not_awaited()

    async def test_populates_cache_when_attribute_pre_seeded(self, folder_connector):
        # Caller pre-seeds an empty cache to opt in (matches batch context manager).
        folder_connector._batch_storage_url_cache = None
        folder_connector.config_service.get_config = AsyncMock(
            return_value={"storage": {"endpoint": "http://x"}}
        )
        url = await folder_connector._storage_base_url()
        assert url == "http://x"
        assert folder_connector._batch_storage_url_cache == "http://x"


@pytest.mark.asyncio
class TestStorageToken:
    async def test_calls_generate_jwt_with_org_and_scope(self, folder_connector):
        with patch(
            "app.connectors.sources.local_fs.connector.generate_jwt",
            new=AsyncMock(return_value="tok"),
        ) as gen:
            tok = await folder_connector._storage_token()
            assert tok == "tok"
            gen.assert_awaited_once()
            args, _ = gen.call_args
            assert args[1]["orgId"] == "org-1"
            assert args[1]["scopes"] == ["storage:token"]

    async def test_uses_cache_when_set(self, folder_connector):
        folder_connector._batch_storage_token_cache = "cached-tok"
        with patch(
            "app.connectors.sources.local_fs.connector.generate_jwt",
            new=AsyncMock(return_value="other-tok"),
        ) as gen:
            tok = await folder_connector._storage_token()
            assert tok == "cached-tok"
            gen.assert_not_awaited()

    async def test_populates_cache_when_attribute_pre_seeded(self, folder_connector):
        folder_connector._batch_storage_token_cache = None
        with patch(
            "app.connectors.sources.local_fs.connector.generate_jwt",
            new=AsyncMock(return_value="fresh"),
        ):
            tok = await folder_connector._storage_token()
            assert tok == "fresh"
            assert folder_connector._batch_storage_token_cache == "fresh"


# --------------------------------------------------------------------------- #
# _upload_storage_file + _do_upload                                           #
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
class TestUploadStorageFile:
    async def test_new_upload_returns_extracted_id(self, folder_connector, monkeypatch):
        # Storage returns {"_id": "doc-new"}
        session = _FakeSession([("post", _FakeResponse(201, json.dumps({"_id": "doc-new"})))])
        _patch_session(monkeypatch, session)

        doc_id = await folder_connector._upload_storage_file(
            rel_path="a/b.txt",
            content=b"hello",
            mime_type="text/plain",
            org_id="org-1",
            storage_url="http://storage.local",
            storage_token="tok",
        )
        assert doc_id == "doc-new"
        assert session.calls[0]["url"].endswith("/api/v1/document/internal/upload")
        # The Authorization header is forwarded.
        assert (
            session.calls[0]["headers"]["Authorization"] == "Bearer tok"
        )

    async def test_uploadNextVersion_returns_existing_id_unchanged(
        self, folder_connector, monkeypatch
    ):
        session = _FakeSession([("post", _FakeResponse(200, "{}"))])
        _patch_session(monkeypatch, session)

        doc_id = await folder_connector._upload_storage_file(
            rel_path="a/b.txt",
            content=b"hi",
            mime_type="text/plain",
            existing_document_id="doc-existing",
            org_id="org-1",
            storage_url="http://storage.local",
            storage_token="tok",
        )
        assert doc_id == "doc-existing"
        assert "uploadNextVersion" in session.calls[0]["url"]

    async def test_resolves_url_token_org_when_omitted(
        self, folder_connector, monkeypatch
    ):
        folder_connector._storage_base_url = AsyncMock(return_value="http://lazy")
        folder_connector._storage_token = AsyncMock(return_value="lazy-tok")
        session = _FakeSession([("post", _FakeResponse(201, json.dumps({"id": "id1"})))])
        _patch_session(monkeypatch, session)

        doc_id = await folder_connector._upload_storage_file(
            rel_path="x.txt", content=b"d", mime_type=None,
        )
        assert doc_id == "id1"
        folder_connector._storage_base_url.assert_awaited_once()
        folder_connector._storage_token.assert_awaited_once()

    async def test_non_2xx_raises_bad_gateway(self, folder_connector, monkeypatch):
        session = _FakeSession([("post", _FakeResponse(503, '{"err":"down"}'))])
        _patch_session(monkeypatch, session)

        with pytest.raises(HTTPException) as ei:
            await folder_connector._upload_storage_file(
                rel_path="x.txt", content=b"d", mime_type=None,
                org_id="o", storage_url="http://x", storage_token="t",
            )
        assert ei.value.status_code == HttpStatusCode.BAD_GATEWAY.value

    async def test_direct_upload_301_put_then_returns_header_doc_id(
        self, folder_connector, monkeypatch
    ):
        session = _FakeSession(
            [
                (
                    "post",
                    _FakeResponse(
                        301,
                        "{}",
                        headers={
                            "Location": "https://s3.example/presigned-put",
                            "x-document-id": "doc-presigned",
                        },
                    ),
                ),
                ("put", _FakeResponse(200, "")),
            ]
        )
        _patch_session(monkeypatch, session)

        doc_id = await folder_connector._upload_storage_file(
            rel_path="a/b.txt",
            content=b"payload-bytes",
            mime_type="text/plain",
            org_id="org-1",
            storage_url="http://storage.local",
            storage_token="tok",
        )
        assert doc_id == "doc-presigned"
        assert session.calls[0]["method"] == "post"
        assert session.calls[1]["method"] == "put"
        assert session.calls[1]["url"] == "https://s3.example/presigned-put"
        assert session.calls[1]["data"] == b"payload-bytes"

    async def test_direct_upload_301_extracts_id_from_json_when_no_header(
        self, folder_connector, monkeypatch
    ):
        body = json.dumps({"document": {"_id": "doc-from-json"}})
        session = _FakeSession(
            [
                (
                    "post",
                    _FakeResponse(
                        301,
                        body,
                        headers={"Location": "https://s3.example/p"},
                    ),
                ),
                ("put", _FakeResponse(200, "")),
            ]
        )
        _patch_session(monkeypatch, session)

        doc_id = await folder_connector._upload_storage_file(
            rel_path="x.txt",
            content=b"x",
            mime_type=None,
            org_id="o",
            storage_url="http://x",
            storage_token="t",
        )
        assert doc_id == "doc-from-json"

    async def test_direct_upload_301_upload_next_version_returns_existing_id(
        self, folder_connector, monkeypatch
    ):
        session = _FakeSession(
            [
                (
                    "post",
                    _FakeResponse(
                        301,
                        "{}",
                        headers={"Location": "https://s3.example/p"},
                    ),
                ),
                ("put", _FakeResponse(200, "")),
            ]
        )
        _patch_session(monkeypatch, session)

        doc_id = await folder_connector._upload_storage_file(
            rel_path="a.txt",
            content=b"z",
            mime_type="text/plain",
            existing_document_id="keep-me",
            org_id="org-1",
            storage_url="http://storage.local",
            storage_token="tok",
        )
        assert doc_id == "keep-me"

    async def test_direct_upload_presigned_put_failure_raises_bad_gateway(
        self, folder_connector, monkeypatch
    ):
        session = _FakeSession(
            [
                (
                    "post",
                    _FakeResponse(
                        301,
                        "{}",
                        headers={"Location": "https://s3.example/p"},
                    ),
                ),
                ("put", _FakeResponse(403, "AccessDenied")),
            ]
        )
        _patch_session(monkeypatch, session)

        with pytest.raises(HTTPException) as ei:
            await folder_connector._upload_storage_file(
                rel_path="x.txt",
                content=b"d",
                mime_type=None,
                org_id="o",
                storage_url="http://x",
                storage_token="t",
            )
        assert ei.value.status_code == HttpStatusCode.BAD_GATEWAY.value

    async def test_direct_upload_redirect_without_location_raises_bad_gateway(
        self, folder_connector, monkeypatch
    ):
        """302/301 without Location must not follow an undefined presigned PUT."""
        session = _FakeSession(
            [
                (
                    "post",
                    _FakeResponse(302, "{}", headers={}),
                ),
            ]
        )
        _patch_session(monkeypatch, session)

        with pytest.raises(HTTPException) as ei:
            await folder_connector._upload_storage_file(
                rel_path="x.txt",
                content=b"d",
                mime_type=None,
                org_id="o",
                storage_url="http://x",
                storage_token="t",
            )
        assert ei.value.status_code == HttpStatusCode.BAD_GATEWAY.value
        detail = ei.value.detail
        assert isinstance(detail, dict)
        assert "Location" in detail["message"]

    async def test_direct_upload_put_ok_but_non_dict_post_body_missing_doc_id(
        self, folder_connector, monkeypatch
    ):
        """Redirect flow: POST body is not JSON ⇒ payload is str; no doc id headers."""
        session = _FakeSession(
            [
                (
                    "post",
                    _FakeResponse(
                        302,
                        "plain-text-not-json",
                        headers={"Location": "https://s3.example/presigned"},
                    ),
                ),
                ("put", _FakeResponse(200, "")),
            ]
        )
        _patch_session(monkeypatch, session)

        with pytest.raises(HTTPException) as ei:
            await folder_connector._upload_storage_file(
                rel_path="x.txt",
                content=b"d",
                mime_type=None,
                org_id="o",
                storage_url="http://x",
                storage_token="t",
            )
        assert ei.value.status_code == HttpStatusCode.BAD_GATEWAY.value
        assert "document id" in str(ei.value.detail).lower()

    async def test_timeout_raises_gateway_timeout(self, folder_connector, monkeypatch):
        session = _FakeSession(
            [("post", _FakeResponse(0, "", raise_on=asyncio.TimeoutError()))]
        )
        _patch_session(monkeypatch, session)

        with pytest.raises(HTTPException) as ei:
            await folder_connector._upload_storage_file(
                rel_path="x.txt", content=b"d", mime_type=None,
                org_id="o", storage_url="http://x", storage_token="t",
            )
        assert ei.value.status_code == HttpStatusCode.GATEWAY_TIMEOUT.value

    async def test_client_error_raises_bad_gateway(self, folder_connector, monkeypatch):
        session = _FakeSession(
            [("post", _FakeResponse(0, "", raise_on=aiohttp.ClientError("dns")))]
        )
        _patch_session(monkeypatch, session)

        with pytest.raises(HTTPException) as ei:
            await folder_connector._upload_storage_file(
                rel_path="x.txt", content=b"d", mime_type=None,
                org_id="o", storage_url="http://x", storage_token="t",
            )
        assert ei.value.status_code == HttpStatusCode.BAD_GATEWAY.value

    async def test_caller_session_reused(self, folder_connector):
        """When a caller hands in a session, no new ClientSession is constructed."""
        # Don't patch ClientSession — if our code mistakenly opens one, the real
        # constructor would run. Instead, we hand a fake session in directly.
        session = _FakeSession([("post", _FakeResponse(201, json.dumps({"_id": "d"})))])
        doc_id = await folder_connector._upload_storage_file(
            rel_path="a.txt",
            content=b"x",
            mime_type=None,
            org_id="o",
            storage_url="http://x",
            storage_token="t",
            session=session,  # type: ignore[arg-type]
        )
        assert doc_id == "d"
        assert len(session.calls) == 1


# --------------------------------------------------------------------------- #
# _delete_external_ids — empty list short-circuit                              #
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
class TestDeleteExternalIds:
    async def test_empty_list_does_not_open_transaction(
        self, folder_connector, monkeypatch
    ):
        spy = MagicMock()
        folder_connector.data_store_provider.transaction = spy
        await folder_connector._delete_external_ids([], "user-1")
        spy.assert_not_called()


# --------------------------------------------------------------------------- #
# _prepare_upsert_record — symlinks + extension filter                        #
# --------------------------------------------------------------------------- #


class TestPrepareUpsertRecord:
    def test_returns_none_when_rel_path_points_at_directory(
        self, folder_connector, tmp_path: Path
    ) -> None:
        """After resolve, path must be a regular file (not a directory)."""
        (tmp_path / "only_dir").mkdir()
        empty = FilterCollection(filters=[])
        out = folder_connector._prepare_upsert_record(
            tmp_path, "only_dir", "rg", empty, empty, owner=None
        )
        assert out is None

    def test_skips_file_with_disallowed_extension(
        self, folder_connector, tmp_path: Path
    ) -> None:
        f = tmp_path / "nope.md"
        f.write_text("z", encoding="utf-8")
        sync_f = FilterCollection(
            filters=[
                Filter(
                    key=SyncFilterKey.FILE_EXTENSIONS.value,
                    type=FilterType.MULTISELECT,
                    operator=MultiselectOperator.IN,
                    value=["pdf"],
                )
            ]
        )
        empty = FilterCollection(filters=[])
        out = folder_connector._prepare_upsert_record(
            tmp_path, "nope.md", "rg", sync_f, empty, owner=None
        )
        assert out is None


# --------------------------------------------------------------------------- #
# _delete_storage_document + _do_delete_blob                                  #
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
class TestDeleteStorageDocument:
    async def test_noop_when_document_id_missing(self, folder_connector):
        # Must not even resolve URL/token.
        folder_connector._storage_base_url = AsyncMock()
        folder_connector._storage_token = AsyncMock()
        await folder_connector._delete_storage_document(None)
        await folder_connector._delete_storage_document("")
        folder_connector._storage_base_url.assert_not_awaited()

    async def test_success_calls_delete(self, folder_connector, monkeypatch):
        session = _FakeSession([("delete", _FakeResponse(204, ""))])
        _patch_session(monkeypatch, session)
        folder_connector._storage_base_url = AsyncMock(return_value="http://x")
        folder_connector._storage_token = AsyncMock(return_value="t")
        await folder_connector._delete_storage_document("doc-1")
        assert session.calls[0]["url"].endswith("/document/internal/doc-1/")

    async def test_4xx_swallowed_with_warning(self, folder_connector, monkeypatch):
        session = _FakeSession([("delete", _FakeResponse(404, "missing"))])
        _patch_session(monkeypatch, session)
        folder_connector._storage_base_url = AsyncMock(return_value="http://x")
        folder_connector._storage_token = AsyncMock(return_value="t")
        # Best-effort: must not raise.
        await folder_connector._delete_storage_document("doc-2")
        folder_connector.logger.warning.assert_called()

    async def test_timeout_logged_not_raised(self, folder_connector, monkeypatch):
        session = _FakeSession(
            [("delete", _FakeResponse(0, "", raise_on=asyncio.TimeoutError()))]
        )
        _patch_session(monkeypatch, session)
        folder_connector._storage_base_url = AsyncMock(return_value="http://x")
        folder_connector._storage_token = AsyncMock(return_value="t")
        await folder_connector._delete_storage_document("doc-3")
        folder_connector.logger.warning.assert_called()

    async def test_client_error_logged_not_raised(self, folder_connector, monkeypatch):
        session = _FakeSession(
            [("delete", _FakeResponse(0, "", raise_on=aiohttp.ClientError("rst")))]
        )
        _patch_session(monkeypatch, session)
        folder_connector._storage_base_url = AsyncMock(return_value="http://x")
        folder_connector._storage_token = AsyncMock(return_value="t")
        await folder_connector._delete_storage_document("doc-4")
        folder_connector.logger.warning.assert_called()

    async def test_caller_session_reused(self, folder_connector):
        session = _FakeSession([("delete", _FakeResponse(204, ""))])
        await folder_connector._delete_storage_document(
            "doc-5",
            storage_url="http://x",
            storage_token="t",
            session=session,  # type: ignore[arg-type]
        )
        assert session.calls[0]["url"].endswith("/document/internal/doc-5/")


# --------------------------------------------------------------------------- #
# _stream_storage_record                                                      #
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
class TestStreamStorageRecord:
    def _record(self) -> FileRecord:
        return FileRecord(
            record_name="r.bin",
            record_type=RecordType.FILE,
            external_record_id="e",
            version=0,
            origin=OriginTypes.CONNECTOR,
            connector_name=Connectors.LOCAL_FS,
            connector_id="c1",
            is_file=True,
            path=f"{LOCAL_FS_STORAGE_PATH_PREFIX}doc-1",
            mime_type="application/octet-stream",
            record_group_type=RecordGroupType.DRIVE,
        )

    async def test_returns_decoded_buffer_on_success(
        self, folder_connector, monkeypatch
    ):
        folder_connector._storage_base_url = AsyncMock(return_value="http://x")
        folder_connector._storage_token = AsyncMock(return_value="t")
        body = json.dumps({"type": "Buffer", "data": [104, 105]})
        session = _FakeSession([("get", _FakeResponse(200, body))])
        _patch_session(monkeypatch, session)

        resp = await folder_connector._stream_storage_record(self._record(), "doc-1")
        assert resp.body == b"hi"
        assert resp.media_type == "application/octet-stream"

    async def test_non_2xx_raises_bad_gateway(self, folder_connector, monkeypatch):
        folder_connector._storage_base_url = AsyncMock(return_value="http://x")
        folder_connector._storage_token = AsyncMock(return_value="t")
        session = _FakeSession([("get", _FakeResponse(500, "boom"))])
        _patch_session(monkeypatch, session)

        with pytest.raises(HTTPException) as ei:
            await folder_connector._stream_storage_record(self._record(), "doc-1")
        assert ei.value.status_code == HttpStatusCode.BAD_GATEWAY.value

    async def test_timeout_raises_gateway_timeout(self, folder_connector, monkeypatch):
        folder_connector._storage_base_url = AsyncMock(return_value="http://x")
        folder_connector._storage_token = AsyncMock(return_value="t")
        session = _FakeSession(
            [("get", _FakeResponse(0, "", raise_on=asyncio.TimeoutError()))]
        )
        _patch_session(monkeypatch, session)

        with pytest.raises(HTTPException) as ei:
            await folder_connector._stream_storage_record(self._record(), "doc-1")
        assert ei.value.status_code == HttpStatusCode.GATEWAY_TIMEOUT.value

    async def test_client_error_raises_bad_gateway(self, folder_connector, monkeypatch):
        folder_connector._storage_base_url = AsyncMock(return_value="http://x")
        folder_connector._storage_token = AsyncMock(return_value="t")
        session = _FakeSession(
            [("get", _FakeResponse(0, "", raise_on=aiohttp.ClientError("dns")))]
        )
        _patch_session(monkeypatch, session)

        with pytest.raises(HTTPException) as ei:
            await folder_connector._stream_storage_record(self._record(), "doc-1")
        assert ei.value.status_code == HttpStatusCode.BAD_GATEWAY.value

    async def test_non_json_body_falls_back_to_raw_bytes(
        self, folder_connector, monkeypatch
    ):
        """A 200 response whose body isn't JSON must be returned as raw bytes,
        not raise a JSONDecodeError."""
        folder_connector._storage_base_url = AsyncMock(return_value="http://x")
        folder_connector._storage_token = AsyncMock(return_value="t")
        session = _FakeSession([("get", _FakeResponse(200, "raw-bytes-body"))])
        _patch_session(monkeypatch, session)
        resp = await folder_connector._stream_storage_record(self._record(), "doc-1")
        assert resp.body == b"raw-bytes-body"


# --------------------------------------------------------------------------- #
# run_sync                                                                     #
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
class TestRunSync:
    async def test_empty_root_warns_and_exits(self, folder_connector):
        folder_connector.config_service.get_config = AsyncMock(
            return_value={"sync": {SYNC_ROOT_PATH_KEY: ""}}
        )
        await folder_connector.run_sync()
        folder_connector.logger.warning.assert_called()

    async def test_unreadable_path_logs_and_defers(self, folder_connector):
        folder_connector.config_service.get_config = AsyncMock(
            return_value={"sync": {SYNC_ROOT_PATH_KEY: "/nope/local-fs-nowhere"}}
        )
        await folder_connector.run_sync()
        folder_connector.logger.info.assert_called()

    async def test_no_owner_returns_early(self, folder_connector, tmp_path):
        folder_connector.config_service.get_config = AsyncMock(
            return_value={"sync": {SYNC_ROOT_PATH_KEY: str(tmp_path)}}
        )
        folder_connector._resolve_owner_user = AsyncMock(return_value=None)
        # Must NOT call on_new_record_groups when there's no owner.
        folder_connector.data_entities_processor.on_new_app_users = AsyncMock()
        folder_connector.data_entities_processor.on_new_record_groups = AsyncMock()
        await folder_connector.run_sync()
        folder_connector.data_entities_processor.on_new_app_users.assert_not_awaited()
        folder_connector.data_entities_processor.on_new_record_groups.assert_not_awaited()

    async def test_full_sync_emits_records_and_handles_skips(
        self, folder_connector, tmp_path
    ):
        # Three real files + one symlink that must be skipped.
        f1 = tmp_path / "a.txt"
        f1.write_text("a", encoding="utf-8")
        f2 = tmp_path / "b.md"
        f2.write_text("b", encoding="utf-8")
        sub = tmp_path / "sub"
        sub.mkdir()
        f3 = sub / "c.txt"
        f3.write_text("c", encoding="utf-8")
        sym = tmp_path / "sym.txt"
        try:
            sym.symlink_to(f1)
        except OSError:
            sym = None  # symlink unavailable in this env

        folder_connector.config_service.get_config = AsyncMock(
            return_value={"sync": {SYNC_ROOT_PATH_KEY: str(tmp_path), "batchSize": "2"}}
        )
        owner = User(email="o@x.com", id="owner-1", org_id="org-1")
        folder_connector._resolve_owner_user = AsyncMock(return_value=owner)
        folder_connector._reset_existing_records = AsyncMock(return_value=0)
        folder_connector.data_entities_processor.on_new_app_users = AsyncMock()
        folder_connector.data_entities_processor.on_new_record_groups = AsyncMock()
        folder_connector.data_entities_processor.on_new_records = AsyncMock()

        with patch(
            "app.connectors.sources.local_fs.connector.load_connector_filters",
            new=AsyncMock(
                return_value=(FilterCollection(filters=[]), FilterCollection(filters=[]))
            ),
        ):
            await folder_connector.run_sync()

        # batch_size=2 + 3 real files ⇒ at least one mid-iter flush + one final
        # flush ⇒ on_new_records called >= 2 times.
        assert folder_connector.data_entities_processor.on_new_records.await_count >= 2
        folder_connector.data_entities_processor.on_new_app_users.assert_awaited_once()
        folder_connector.data_entities_processor.on_new_record_groups.assert_awaited_once()
        # Owner must be cleared from instance state.
        assert folder_connector._owner_user_for_permissions is None

    async def test_exception_propagates_and_clears_owner(
        self, folder_connector, tmp_path
    ):
        folder_connector.config_service.get_config = AsyncMock(
            return_value={"sync": {SYNC_ROOT_PATH_KEY: str(tmp_path)}}
        )
        folder_connector._resolve_owner_user = AsyncMock(
            side_effect=RuntimeError("graph down")
        )
        with pytest.raises(RuntimeError):
            await folder_connector.run_sync()
        assert folder_connector._owner_user_for_permissions is None
        folder_connector.logger.error.assert_called()

    async def test_run_incremental_sync_delegates(self, folder_connector, tmp_path):
        folder_connector.config_service.get_config = AsyncMock(
            return_value={"sync": {SYNC_ROOT_PATH_KEY: ""}}
        )
        await folder_connector.run_incremental_sync()
        folder_connector.logger.warning.assert_called()

    async def test_per_file_exception_is_logged_and_iteration_continues(
        self, folder_connector, tmp_path
    ):
        """If processing one file blows up, the loop must log + continue, not abort."""
        f1 = tmp_path / "boom.txt"
        f1.write_text("a", encoding="utf-8")
        f2 = tmp_path / "ok.txt"
        f2.write_text("b", encoding="utf-8")

        folder_connector.config_service.get_config = AsyncMock(
            return_value={"sync": {SYNC_ROOT_PATH_KEY: str(tmp_path)}}
        )
        owner = User(email="o@x.com", id="owner-1", org_id="org-1")
        folder_connector._resolve_owner_user = AsyncMock(return_value=owner)
        folder_connector._reset_existing_records = AsyncMock(return_value=0)
        folder_connector.data_entities_processor.on_new_app_users = AsyncMock()
        folder_connector.data_entities_processor.on_new_record_groups = AsyncMock()
        folder_connector.data_entities_processor.on_new_records = AsyncMock()

        # Make _extension_allowed raise on the first file only.
        original = folder_connector._extension_allowed
        call_count = {"n": 0}

        def _flaky(path, filters):
            call_count["n"] += 1
            if call_count["n"] == 1:
                raise RuntimeError("simulated stat error")
            return original(path, filters)

        folder_connector._extension_allowed = _flaky  # type: ignore[assignment]

        with patch(
            "app.connectors.sources.local_fs.connector.load_connector_filters",
            new=AsyncMock(
                return_value=(FilterCollection(filters=[]), FilterCollection(filters=[]))
            ),
        ):
            await folder_connector.run_sync()

        # The flaky file was skipped but the second one got through.
        folder_connector.logger.warning.assert_called()
        folder_connector.data_entities_processor.on_new_records.assert_awaited()


# --------------------------------------------------------------------------- #
# Misc small helpers                                                          #
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
class TestMisc:
    async def test_bulk_get_records_dedupes_and_skips_empty(self, folder_connector):
        txn = MagicMock()
        txn.__aenter__ = AsyncMock(return_value=txn)
        txn.__aexit__ = AsyncMock(return_value=None)
        seen: list[str] = []

        async def _lookup(connector_id, ext_id):
            seen.append(ext_id)
            return MagicMock(external_record_id=ext_id) if ext_id == "x" else None

        txn.get_record_by_external_id = AsyncMock(side_effect=_lookup)
        folder_connector.data_store_provider.transaction = MagicMock(return_value=txn)

        result = await folder_connector._bulk_get_records_by_external_ids(
            ["x", "x", "y", "", None]  # type: ignore[list-item]
        )
        # "x" deduped, "" / None skipped, "y" preserved → at most two lookups.
        assert set(seen) <= {"x", "y"}
        assert "x" in result
        assert "y" not in result

    async def test_bulk_get_records_empty_input_short_circuits(
        self, folder_connector
    ):
        txn = MagicMock()
        txn.__aenter__ = AsyncMock(return_value=txn)
        txn.__aexit__ = AsyncMock(return_value=None)
        folder_connector.data_store_provider.transaction = MagicMock(return_value=txn)
        out = await folder_connector._bulk_get_records_by_external_ids([])
        assert out == {}
        # Transaction must NOT have been opened.
        folder_connector.data_store_provider.transaction.assert_not_called()

    async def test_get_record_by_external_id_delegates(self, folder_connector):
        record = MagicMock(external_record_id="ext-1")
        txn = MagicMock()
        txn.__aenter__ = AsyncMock(return_value=txn)
        txn.__aexit__ = AsyncMock(return_value=None)
        txn.get_record_by_external_id = AsyncMock(return_value=record)
        folder_connector.data_store_provider.transaction = MagicMock(return_value=txn)

        out = await folder_connector._get_record_by_external_id("ext-1")
        assert out is record

    async def test_storage_document_id_for_external_id_resolves_when_record_has_storage_path(
        self, folder_connector
    ):
        record = MagicMock(path=f"{LOCAL_FS_STORAGE_PATH_PREFIX}doc-77")
        folder_connector._get_record_by_external_id = AsyncMock(return_value=record)
        out = await folder_connector._storage_document_id_for_external_id("e-1")
        assert out == "doc-77"

    async def test_storage_document_id_for_external_id_none_when_no_record(
        self, folder_connector
    ):
        folder_connector._get_record_by_external_id = AsyncMock(return_value=None)
        out = await folder_connector._storage_document_id_for_external_id("e-1")
        assert out is None

    async def test_test_connection_and_access_with_valid_path(
        self, folder_connector, tmp_path
    ):
        folder_connector.sync_root_path = str(tmp_path)
        assert await folder_connector.test_connection_and_access() is True
        # No warning when path is fine.
        folder_connector.logger.warning.assert_not_called()


# --------------------------------------------------------------------------- #
# create_connector classmethod                                                #
# --------------------------------------------------------------------------- #


class TestEventDateFilters:
    """Cover the static event-timestamp variant of the date filter."""

    def _filter(self, key, start, end):
        from app.connectors.core.registry.filters import (
            DatetimeOperator,
            Filter,
            FilterType,
        )

        return Filter(
            key=key,
            type=FilterType.DATETIME,
            operator=DatetimeOperator.IS_BETWEEN,
            value={"start": start, "end": end},
        )

    def test_no_filters_passes(self):
        ev = LocalFsFileEvent(
            type="CREATED", path="x", timestamp=1000, isDirectory=False,
        )
        assert (
            LocalFsConnector._event_matches_date_filters(
                ev, FilterCollection(filters=[])
            )
            is True
        )

    def test_modified_in_range(self):
        from app.connectors.core.registry.filters import SyncFilterKey

        ev = LocalFsFileEvent(
            type="MODIFIED", path="x", timestamp=3000, isDirectory=False,
        )
        flt = self._filter(SyncFilterKey.MODIFIED.value, 2000, 4000)
        assert (
            LocalFsConnector._event_matches_date_filters(
                ev, FilterCollection(filters=[flt])
            )
            is True
        )

    def test_modified_before_range(self):
        from app.connectors.core.registry.filters import SyncFilterKey

        ev = LocalFsFileEvent(
            type="MODIFIED", path="x", timestamp=1000, isDirectory=False,
        )
        flt = self._filter(SyncFilterKey.MODIFIED.value, 2000, 4000)
        assert (
            LocalFsConnector._event_matches_date_filters(
                ev, FilterCollection(filters=[flt])
            )
            is False
        )

    def test_modified_after_range(self):
        from app.connectors.core.registry.filters import SyncFilterKey

        ev = LocalFsFileEvent(
            type="MODIFIED", path="x", timestamp=5000, isDirectory=False,
        )
        flt = self._filter(SyncFilterKey.MODIFIED.value, 2000, 4000)
        assert (
            LocalFsConnector._event_matches_date_filters(
                ev, FilterCollection(filters=[flt])
            )
            is False
        )

    def test_created_filter_uses_event_timestamp(self):
        from app.connectors.core.registry.filters import SyncFilterKey

        ev = LocalFsFileEvent(
            type="CREATED", path="x", timestamp=1000, isDirectory=False,
        )
        flt = self._filter(SyncFilterKey.CREATED.value, 5000, 6000)
        assert (
            LocalFsConnector._event_matches_date_filters(
                ev, FilterCollection(filters=[flt])
            )
            is False
        )


@pytest.mark.asyncio
async def test_create_connector_builds_instance():
    logger = MagicMock()
    data_store_provider = MagicMock()
    config_service = MagicMock()
    proc = MagicMock()
    proc.org_id = "org-classmethod"

    proc.initialize = AsyncMock()

    with patch(
        "app.connectors.sources.local_fs.connector.DataSourceEntitiesProcessor",
        return_value=proc,
    ) as processor_cls:
        conn = await LocalFsConnector.create_connector(
            logger,
            data_store_provider,
            config_service,
            "conn-id-x",
            "personal",
            "kushagra",
        )
        processor_cls.assert_called_once_with(
            logger, data_store_provider, config_service
        )
        proc.initialize.assert_awaited_once()
        assert isinstance(conn, LocalFsConnector)
        assert conn.connector_id == "conn-id-x"
        assert conn.data_entities_processor is proc


# --------------------------------------------------------------------------- #
# apply_file_event_batch — DELETED, RENAMED, unsupported event branches       #
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
class TestApplyFileEventBatchBranches:
    async def _setup(self, folder_connector, tmp_path: Path):
        folder_connector.config_service.get_config = AsyncMock(
            return_value={"sync": {SYNC_ROOT_PATH_KEY: str(tmp_path)}}
        )
        owner = User(email="u@x.com", id="u1", org_id="org-1")
        folder_connector._ensure_owner_and_record_group = AsyncMock(
            return_value=(
                owner,
                FilterCollection(filters=[]),
                FilterCollection(filters=[]),
                folder_connector._record_group_external_id(),
            )
        )
        folder_connector.data_entities_processor.on_new_records = AsyncMock()
        folder_connector._delete_external_ids = AsyncMock()
        return owner

    async def test_deleted_event_buffers_and_flushes(
        self, folder_connector, tmp_path
    ):
        await self._setup(folder_connector, tmp_path)
        ev = LocalFsFileEvent(
            type="DELETED", path="gone.txt", timestamp=1, isDirectory=False,
        )
        stats = await folder_connector.apply_file_event_batch([ev])
        assert stats.deleted == 1
        folder_connector._delete_external_ids.assert_awaited()

    async def test_unsupported_event_type_raises(self, folder_connector, tmp_path):
        await self._setup(folder_connector, tmp_path)
        ev = LocalFsFileEvent(
            type="WAT", path="a.txt", timestamp=1, isDirectory=False,
        )
        with pytest.raises(HTTPException) as ei:
            await folder_connector.apply_file_event_batch([ev])
        assert ei.value.status_code == HttpStatusCode.BAD_REQUEST.value

    async def test_empty_path_raises(self, folder_connector, tmp_path):
        await self._setup(folder_connector, tmp_path)
        ev = LocalFsFileEvent(
            type="CREATED", path="   ", timestamp=1, isDirectory=False,
        )
        with pytest.raises(HTTPException) as ei:
            await folder_connector.apply_file_event_batch([ev])
        assert ei.value.status_code == HttpStatusCode.BAD_REQUEST.value

    async def test_rename_with_vanished_new_path_downgrades_to_delete(
        self, folder_connector, tmp_path
    ):
        await self._setup(folder_connector, tmp_path)
        # Old file exists so the resolver doesn't trip.
        old = tmp_path / "old.txt"
        old.write_text("x", encoding="utf-8")
        # New file deliberately absent — _prepare_upsert_record returns None.
        ev = LocalFsFileEvent(
            type="RENAMED",
            path="missing_new.txt",
            oldPath="old.txt",
            timestamp=1,
            isDirectory=False,
        )
        # _prepare_upsert_record returning None drives the downgrade branch.
        folder_connector._prepare_upsert_record = MagicMock(return_value=None)
        stats = await folder_connector.apply_file_event_batch([ev])
        # The downgrade enqueues a delete-only for the OLD path.
        assert stats.deleted == 1
        folder_connector._delete_external_ids.assert_awaited()

    async def test_rename_happy_path_upserts_new_then_deletes_old(
        self, folder_connector, tmp_path
    ):
        owner = await self._setup(folder_connector, tmp_path)
        old = tmp_path / "old.txt"
        old.write_text("x", encoding="utf-8")
        new = tmp_path / "new.txt"
        new.write_text("y", encoding="utf-8")
        ev = LocalFsFileEvent(
            type="RENAMED",
            path="new.txt",
            oldPath="old.txt",
            timestamp=1,
            isDirectory=False,
        )
        # Return a sentinel record so the rename takes the "new ext_id != old"
        # path and queues the old path for delete-after-upsert.
        sentinel = MagicMock()
        folder_connector._prepare_upsert_record = MagicMock(return_value=sentinel)
        stats = await folder_connector.apply_file_event_batch([ev])
        assert stats.processed == 1
        assert stats.deleted == 1  # old ext_id deleted via delete_after_upsert
        folder_connector.data_entities_processor.on_new_records.assert_awaited()
        folder_connector._delete_external_ids.assert_awaited()


# --------------------------------------------------------------------------- #
# stream_record — local-file fallback path                                    #
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
class TestStreamRecordLocalFile:
    async def test_streams_local_file_chunks(self, folder_connector, tmp_path):
        f = tmp_path / "data.bin"
        f.write_bytes(b"local-bytes")
        folder_connector.config_service.get_config = AsyncMock(
            return_value={"sync": {SYNC_ROOT_PATH_KEY: str(tmp_path)}}
        )
        rec = FileRecord(
            record_name="data.bin",
            record_type=RecordType.FILE,
            external_record_id="e",
            version=0,
            origin=OriginTypes.CONNECTOR,
            connector_name=Connectors.LOCAL_FS,
            connector_id="c1",
            is_file=True,
            path=str(f),  # NOT a storage:// path → goes through local-file branch
            mime_type="application/octet-stream",
            record_group_type=RecordGroupType.DRIVE,
        )
        resp = await folder_connector.stream_record(rec)
        chunks: list[bytes] = []
        async for chunk in resp.body_iterator:
            chunks.append(chunk)
        assert b"".join(chunks) == b"local-bytes"

    async def test_returns_404_when_local_file_missing(
        self, folder_connector, tmp_path
    ):
        ghost = tmp_path / "gone.bin"  # never created
        folder_connector.config_service.get_config = AsyncMock(
            return_value={"sync": {SYNC_ROOT_PATH_KEY: str(tmp_path)}}
        )
        rec = FileRecord(
            record_name="gone.bin",
            record_type=RecordType.FILE,
            external_record_id="e",
            version=0,
            origin=OriginTypes.CONNECTOR,
            connector_name=Connectors.LOCAL_FS,
            connector_id="c1",
            is_file=True,
            path=str(ghost),
            mime_type="application/octet-stream",
            record_group_type=RecordGroupType.DRIVE,
        )
        with pytest.raises(HTTPException) as ei:
            await folder_connector.stream_record(rec)
        assert ei.value.status_code == HttpStatusCode.NOT_FOUND.value

    async def test_returns_400_when_root_unconfigured(self, folder_connector):
        folder_connector.config_service.get_config = AsyncMock(
            return_value={"sync": {SYNC_ROOT_PATH_KEY: ""}}
        )
        rec = FileRecord(
            record_name="x",
            record_type=RecordType.FILE,
            external_record_id="e",
            version=0,
            origin=OriginTypes.CONNECTOR,
            connector_name=Connectors.LOCAL_FS,
            connector_id="c1",
            is_file=True,
            path="/anywhere/x",
            mime_type="text/plain",
            record_group_type=RecordGroupType.DRIVE,
        )
        with pytest.raises(HTTPException) as ei:
            await folder_connector.stream_record(rec)
        assert ei.value.status_code == HttpStatusCode.BAD_REQUEST.value

    async def test_returns_400_when_root_invalid(self, folder_connector):
        folder_connector.config_service.get_config = AsyncMock(
            return_value={
                "sync": {SYNC_ROOT_PATH_KEY: "/no/such/dir/local-fs-x"}
            }
        )
        rec = FileRecord(
            record_name="x",
            record_type=RecordType.FILE,
            external_record_id="e",
            version=0,
            origin=OriginTypes.CONNECTOR,
            connector_name=Connectors.LOCAL_FS,
            connector_id="c1",
            is_file=True,
            path="/no/such/dir/local-fs-x/x",
            mime_type="text/plain",
            record_group_type=RecordGroupType.DRIVE,
        )
        with pytest.raises(HTTPException) as ei:
            await folder_connector.stream_record(rec)
        assert ei.value.status_code == HttpStatusCode.BAD_REQUEST.value

    async def test_local_path_resolve_oserror_returns_400(
        self, folder_connector, tmp_path, monkeypatch
    ):
        """Local-file branch: ``(sync_root / rel_path).resolve()`` can raise ``OSError``."""
        import pathlib

        probe = tmp_path / "local.bin"
        probe.write_bytes(b"x")
        folder_connector.config_service.get_config = AsyncMock(
            return_value={"sync": {SYNC_ROOT_PATH_KEY: str(tmp_path)}}
        )
        rec = FileRecord(
            record_name="local.bin",
            record_type=RecordType.FILE,
            external_record_id="e",
            version=0,
            origin=OriginTypes.CONNECTOR,
            connector_name=Connectors.LOCAL_FS,
            connector_id="c1",
            is_file=True,
            path="local.bin",
            mime_type="application/octet-stream",
            record_group_type=RecordGroupType.DRIVE,
        )

        orig_resolve = pathlib.Path.resolve

        def selective(self, *args, **kwargs):
            # Fail only for the leaf file path, not ``Path(sync_root)`` during validation.
            if getattr(self, "name", None) == "local.bin":
                raise OSError("broken symlink chain")
            return orig_resolve(self, *args, **kwargs)

        monkeypatch.setattr(pathlib.Path, "resolve", selective)

        with pytest.raises(HTTPException) as ei:
            await folder_connector.stream_record(rec)
        assert ei.value.status_code == HttpStatusCode.BAD_REQUEST.value
        assert "Invalid file path" in str(ei.value.detail)


# --------------------------------------------------------------------------- #
# _ensure_owner_and_record_group                                              #
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
class TestEnsureOwnerAndRecordGroup:
    async def test_raises_400_when_owner_cannot_be_resolved(
        self, folder_connector, tmp_path: Path
    ) -> None:
        folder_connector._resolve_owner_user = AsyncMock(return_value=None)
        with pytest.raises(HTTPException) as ei:
            await folder_connector._ensure_owner_and_record_group(tmp_path)
        assert ei.value.status_code == HttpStatusCode.BAD_REQUEST.value
        assert "owner" in str(ei.value.detail).lower()


# --------------------------------------------------------------------------- #
# _resolve_owner_user — missing app doc / missing createdBy / bad user shape  #
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
class TestResolveOwnerUser:
    def _txn(self, *, app_doc, user_raw=None):
        txn = MagicMock()
        txn.__aenter__ = AsyncMock(return_value=txn)
        txn.__aexit__ = AsyncMock(return_value=None)
        txn.txn = MagicMock()
        txn.graph_provider.get_document = AsyncMock(return_value=app_doc)
        txn.get_user_by_user_id = AsyncMock(return_value=user_raw)
        return txn

    async def test_returns_none_when_app_doc_missing(self, folder_connector):
        txn = self._txn(app_doc=None)
        folder_connector.data_store_provider.transaction = MagicMock(return_value=txn)

        out = await folder_connector._resolve_owner_user()
        assert out is None
        folder_connector.logger.error.assert_called()

    async def test_returns_none_when_app_doc_has_no_created_by(self, folder_connector):
        txn = self._txn(app_doc={"_id": "apps/x"})
        folder_connector.data_store_provider.transaction = MagicMock(return_value=txn)

        out = await folder_connector._resolve_owner_user()
        assert out is None
        folder_connector.logger.error.assert_called()

    async def test_logs_error_when_user_lookup_returns_none(self, folder_connector):
        txn = self._txn(app_doc={"createdBy": "u-1"}, user_raw=None)
        folder_connector.data_store_provider.transaction = MagicMock(return_value=txn)

        out = await folder_connector._resolve_owner_user()
        assert out is None
        folder_connector.logger.error.assert_called()


# --------------------------------------------------------------------------- #
# _storage_document_id_for_external_id                                        #
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
class TestStorageDocumentIdForExternalId:
    async def test_resolves_document_id_when_record_has_storage_path(
        self, folder_connector
    ):
        record = MagicMock(path=f"{LOCAL_FS_STORAGE_PATH_PREFIX}doc-xyz")
        folder_connector._get_record_by_external_id = AsyncMock(return_value=record)

        out = await folder_connector._storage_document_id_for_external_id("ext-1")

        assert out == "doc-xyz"

    async def test_resolves_none_when_record_missing(self, folder_connector):
        folder_connector._get_record_by_external_id = AsyncMock(return_value=None)

        out = await folder_connector._storage_document_id_for_external_id("ext-1")

        assert out is None


# --------------------------------------------------------------------------- #
# _reset_existing_records — delete_storage_documents=True branch              #
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
class TestResetExistingRecordsStorageGc:
    async def test_collects_and_deletes_storage_doc_ids(self, folder_connector):
        rec1 = MagicMock(
            external_record_id="e-1",
            path=f"{LOCAL_FS_STORAGE_PATH_PREFIX}doc-a",
        )
        rec2 = MagicMock(
            external_record_id="e-2",
            path=f"{LOCAL_FS_STORAGE_PATH_PREFIX}doc-b",
        )
        # Record without a storage-prefixed path → must NOT be GC'd.
        rec3 = MagicMock(
            external_record_id="e-3",
            path="/some/local/path",
        )

        # Two rounds: first returns the three records, second returns [] so
        # the outer while-loop exits after the storage GC fires.
        rounds = [[rec1, rec2, rec3], []]
        txn = MagicMock()
        txn.__aenter__ = AsyncMock(return_value=txn)
        txn.__aexit__ = AsyncMock(return_value=None)
        txn.get_records_by_status = AsyncMock(side_effect=rounds)
        txn.delete_record_by_external_id = AsyncMock()
        folder_connector.data_store_provider.transaction = MagicMock(return_value=txn)
        folder_connector._delete_storage_document = AsyncMock()

        n = await folder_connector._reset_existing_records(
            "owner-1", delete_storage_documents=True
        )

        assert n == 3
        # The two storage-prefixed records were forwarded to GC; the local one wasn't.
        gc_args = [
            call.args[0]
            for call in folder_connector._delete_storage_document.await_args_list
        ]
        assert sorted(gc_args) == ["doc-a", "doc-b"]

    async def test_skips_rows_without_external_record_id(
        self, folder_connector
    ) -> None:
        """Records with no ``external_record_id`` are ignored (no DB delete)."""
        bare = MagicMock(external_record_id=None, path="anything")
        ok = MagicMock(external_record_id="keep-me", path=None)
        rounds = [[bare, ok], []]
        txn = MagicMock()
        txn.__aenter__ = AsyncMock(return_value=txn)
        txn.__aexit__ = AsyncMock(return_value=None)
        txn.get_records_by_status = AsyncMock(side_effect=rounds)
        txn.delete_record_by_external_id = AsyncMock()
        folder_connector.data_store_provider.transaction = MagicMock(return_value=txn)

        n = await folder_connector._reset_existing_records(
            "owner-1", delete_storage_documents=False
        )

        assert n == 1
        txn.delete_record_by_external_id.assert_awaited_once_with(
            folder_connector.connector_id, "keep-me", "owner-1"
        )


# --------------------------------------------------------------------------- #
# apply_uploaded_file_event_batch — SHA-256 mismatch skip-with-warning branch #
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
class TestApplyUploadedSha256Mismatch:
    async def test_mismatched_sha_event_skipped_warning_logged(
        self, folder_connector, tmp_path, monkeypatch
    ):
        folder_connector.config_service.get_config = AsyncMock(
            return_value={"sync": {SYNC_ROOT_PATH_KEY: str(tmp_path)}}
        )
        owner = User(email="u@x.com", id="u1", org_id="org-1")
        folder_connector._ensure_owner_and_record_group = AsyncMock(
            return_value=(
                owner,
                FilterCollection(filters=[]),
                FilterCollection(filters=[]),
                folder_connector._record_group_external_id(),
            )
        )
        folder_connector._bulk_get_records_by_external_ids = AsyncMock(return_value={})
        folder_connector.data_entities_processor.on_new_records = AsyncMock()
        # Should NOT be called for the mismatched event.
        folder_connector._upload_storage_file = AsyncMock(return_value="doc-fresh")
        # The aiohttp.ClientSession context manager opened inside the method —
        # never actually used because we mock out _upload_storage_file, but
        # the `async with` still needs a working object.
        _patch_session(monkeypatch, _FakeSession([]))

        # CREATED with WRONG sha — must be skipped + warning.
        bad_event = LocalFsFileEvent(
            type="CREATED",
            path="bad.txt",
            timestamp=1,
            isDirectory=False,
            contentField="file_bad",
            sha256="00" * 32,  # never matches
            mimeType="text/plain",
        )
        # CREATED whose sha matches — must succeed.
        import hashlib as _hashlib

        good_bytes = b"hello upload"
        good_event = LocalFsFileEvent(
            type="CREATED",
            path="good.txt",
            timestamp=1,
            isDirectory=False,
            contentField="file_good",
            sha256=_hashlib.sha256(good_bytes).hexdigest(),
            mimeType="text/plain",
        )

        stats = await folder_connector.apply_uploaded_file_event_batch(
            [bad_event, good_event],
            {"file_bad": b"actually-different", "file_good": good_bytes},
        )

        # The bad event was skipped, the good one processed.
        assert stats.processed == 1
        assert folder_connector._upload_storage_file.await_count == 1
        # Warning logged for the mismatch.
        assert any(
            "SHA-256 mismatch" in (call.args[0] if call.args else "")
            for call in folder_connector.logger.warning.call_args_list
        )


# --------------------------------------------------------------------------- #
# apply_uploaded_file_event_batch — validation + rename GC                     #
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
class TestApplyUploadedFileEventBatchValidation:
    """Branches inside ``apply_uploaded_file_event_batch`` (errors before upload)."""

    async def _base_setup(self, folder_connector, tmp_path: Path):
        folder_connector.config_service.get_config = AsyncMock(
            return_value={"sync": {SYNC_ROOT_PATH_KEY: str(tmp_path)}}
        )
        owner = User(email="u@x.com", id="u1", org_id="org-1")
        folder_connector._ensure_owner_and_record_group = AsyncMock(
            return_value=(
                owner,
                FilterCollection(filters=[]),
                FilterCollection(filters=[]),
                folder_connector._record_group_external_id(),
            )
        )
        folder_connector._bulk_get_records_by_external_ids = AsyncMock(return_value={})

    async def test_directory_event_raises_in_processing_loop(
        self, folder_connector, tmp_path, monkeypatch
    ) -> None:
        await self._base_setup(folder_connector, tmp_path)
        folder_connector.data_entities_processor.on_new_records = AsyncMock()
        folder_connector._upload_storage_file = AsyncMock(return_value="doc-x")
        _patch_session(monkeypatch, _FakeSession([]))

        ev = LocalFsFileEvent(
            type="CREATED",
            path="dir_placeholder",
            timestamp=1,
            isDirectory=True,
            contentField="f1",
        )
        with pytest.raises(HTTPException) as ei:
            await folder_connector.apply_uploaded_file_event_batch(
                [ev], {"f1": b"x"}
            )
        assert ei.value.status_code == HttpStatusCode.BAD_REQUEST.value

    async def test_unsupported_event_type_raises(
        self, folder_connector, tmp_path, monkeypatch
    ) -> None:
        await self._base_setup(folder_connector, tmp_path)
        folder_connector.data_entities_processor.on_new_records = AsyncMock()
        folder_connector._upload_storage_file = AsyncMock(return_value="doc-x")
        _patch_session(monkeypatch, _FakeSession([]))

        ev = LocalFsFileEvent(
            type="UNKNOWN_OP",
            path="x.txt",
            timestamp=1,
            isDirectory=False,
            contentField="f1",
        )
        with pytest.raises(HTTPException) as ei:
            await folder_connector.apply_uploaded_file_event_batch(
                [ev], {"f1": b"y"}
            )
        assert ei.value.status_code == HttpStatusCode.BAD_REQUEST.value

    async def test_missing_content_field_raises_422(
        self, folder_connector, tmp_path, monkeypatch
    ) -> None:
        await self._base_setup(folder_connector, tmp_path)
        folder_connector._upload_storage_file = AsyncMock(return_value="doc-x")
        _patch_session(monkeypatch, _FakeSession([]))

        ev = LocalFsFileEvent(
            type="CREATED",
            path="x.txt",
            timestamp=1,
            isDirectory=False,
            contentField=None,
            mimeType="text/plain",
        )
        with pytest.raises(HTTPException) as ei:
            await folder_connector.apply_uploaded_file_event_batch([ev], {})
        assert ei.value.status_code == HttpStatusCode.UNPROCESSABLE_ENTITY.value

    async def test_missing_upload_part_raises_422(
        self, folder_connector, tmp_path, monkeypatch
    ) -> None:
        await self._base_setup(folder_connector, tmp_path)
        folder_connector._upload_storage_file = AsyncMock(return_value="doc-x")
        _patch_session(monkeypatch, _FakeSession([]))

        ev = LocalFsFileEvent(
            type="CREATED",
            path="x.txt",
            timestamp=1,
            isDirectory=False,
            contentField="missing_key",
            mimeType="text/plain",
        )
        with pytest.raises(HTTPException) as ei:
            await folder_connector.apply_uploaded_file_event_batch([ev], {})
        assert ei.value.status_code == HttpStatusCode.UNPROCESSABLE_ENTITY.value


@pytest.mark.asyncio
class TestApplyUploadedRenameOldBlobGc:
    async def test_rename_schedules_delete_of_prior_storage_blob(
        self, folder_connector, tmp_path, monkeypatch
    ) -> None:
        """RENAMED with distinct ids + old storage doc GC when blob id changes."""
        folder_connector.config_service.get_config = AsyncMock(
            return_value={"sync": {SYNC_ROOT_PATH_KEY: str(tmp_path)}}
        )
        owner = User(email="u@x.com", id="u1", org_id="org-1")
        folder_connector._ensure_owner_and_record_group = AsyncMock(
            return_value=(
                owner,
                FilterCollection(filters=[]),
                FilterCollection(filters=[]),
                folder_connector._record_group_external_id(),
            )
        )
        old_ext = folder_connector._external_record_id_for_rel_path("old_name.txt")
        new_ext = folder_connector._external_record_id_for_rel_path("new_name.txt")
        assert old_ext != new_ext
        old_rec = FileRecord(
            record_name="old_name.txt",
            record_type=RecordType.FILE,
            external_record_id=old_ext,
            version=0,
            origin=OriginTypes.CONNECTOR,
            connector_name=Connectors.LOCAL_FS,
            connector_id=folder_connector.connector_id,
            is_file=True,
            path=f"{LOCAL_FS_STORAGE_PATH_PREFIX}doc-old-blob",
            mime_type="text/plain",
            record_group_type=RecordGroupType.DRIVE,
        )
        folder_connector._bulk_get_records_by_external_ids = AsyncMock(
            return_value={old_ext: old_rec}
        )
        folder_connector._upload_storage_file = AsyncMock(return_value="doc-new-blob")
        folder_connector._delete_storage_document = AsyncMock()
        folder_connector.data_entities_processor.on_new_records = AsyncMock()
        _patch_session(monkeypatch, _FakeSession([]))

        content = b"renamed body"
        import hashlib as _h

        ev = LocalFsFileEvent(
            type="RENAMED",
            path="new_name.txt",
            oldPath="old_name.txt",
            timestamp=1_700_000_000,
            size=len(content),
            isDirectory=False,
            contentField="part1",
            sha256=_h.sha256(content).hexdigest(),
            mimeType="text/plain",
        )
        await folder_connector.apply_uploaded_file_event_batch(
            [ev], {"part1": content}
        )

        folder_connector._delete_storage_document.assert_awaited()
        deleted_ids = {
            c.args[0] for c in folder_connector._delete_storage_document.await_args_list
        }
        assert "doc-old-blob" in deleted_ids


# --------------------------------------------------------------------------- #
# _do_upload — non-JSON 2xx body falls back to raw text                       #
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
class TestUploadStorageFileNonJsonBody:
    async def test_existing_id_returned_when_body_is_not_json(
        self, folder_connector, monkeypatch
    ):
        # 200 OK but body is plain text — JSONDecodeError branch then
        # existing_document_id short-circuit.
        session = _FakeSession([("post", _FakeResponse(200, "OK plain"))])
        _patch_session(monkeypatch, session)

        doc_id = await folder_connector._upload_storage_file(
            rel_path="x.txt",
            content=b"d",
            mime_type=None,
            existing_document_id="doc-keep",
            org_id="org-1",
            storage_url="http://x",
            storage_token="t",
        )
        assert doc_id == "doc-keep"


# --------------------------------------------------------------------------- #
# stream_record — OSError on local file open                                  #
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
class TestStreamRecordLocalFileOsError:
    async def test_open_oserror_raises_400(
        self, folder_connector, tmp_path, monkeypatch
    ):
        f = tmp_path / "data.bin"
        f.write_bytes(b"local-bytes")
        folder_connector.config_service.get_config = AsyncMock(
            return_value={"sync": {SYNC_ROOT_PATH_KEY: str(tmp_path)}}
        )
        rec = FileRecord(
            record_name="data.bin",
            record_type=RecordType.FILE,
            external_record_id="e",
            version=0,
            origin=OriginTypes.CONNECTOR,
            connector_name=Connectors.LOCAL_FS,
            connector_id="c1",
            is_file=True,
            path=str(f),
            mime_type="application/octet-stream",
            record_group_type=RecordGroupType.DRIVE,
        )

        # Force Path.open to raise OSError without touching the real fs.
        from pathlib import Path as _Path

        original_open = _Path.open

        def _boom(self, *args, **kwargs):
            if self == f.resolve():
                raise OSError("permission denied")
            return original_open(self, *args, **kwargs)

        monkeypatch.setattr(_Path, "open", _boom)

        with pytest.raises(HTTPException) as ei:
            await folder_connector.stream_record(rec)
        assert ei.value.status_code == HttpStatusCode.BAD_REQUEST.value
        assert "Cannot read file" in str(ei.value.detail)
