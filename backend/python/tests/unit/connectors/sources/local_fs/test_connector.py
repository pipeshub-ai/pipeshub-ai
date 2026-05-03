"""Unit tests for :mod:`app.connectors.sources.local_fs.connector`.

``connector.py`` registers via ``ConnectorBuilder.build_decorator``, which imports
``connector_registry`` and thus ``ConnectorAppContainer``. Install lightweight
``sys.modules`` shims *before* importing the connector (same pattern as
``test_mariadb_client.py``) so the full DI graph is not loaded.
"""

from __future__ import annotations

import asyncio
import hashlib
import sys
import types
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# --- Import shims (must run before ``from app.connectors...connector import``) ---
if "app.containers.connector" not in sys.modules:
    _stub_container = types.ModuleType("app.containers.connector")

    class _ConnectorAppContainer:
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

from fastapi import HTTPException  # noqa: E402

from app.config.constants.arangodb import Connectors, ProgressStatus  # noqa: E402
from app.config.constants.http_status_code import HttpStatusCode  # noqa: E402
from app.connectors.core.registry.filters import (  # noqa: E402
    BooleanOperator,
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

    def test_coerce_user_none(self, folder_connector: LocalFsConnector):
        assert folder_connector._coerce_user(None) is None

    def test_coerce_user_passthrough(self, folder_connector: LocalFsConnector):
        u = User(email="a@b.com", id="u1")
        assert folder_connector._coerce_user(u) is u

    def test_coerce_user_from_dict(self, folder_connector: LocalFsConnector):
        u = folder_connector._coerce_user(
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
        assert rec.indexing_status == ProgressStatus.AUTO_INDEX_OFF.value
        assert len(perms) == 1
        assert perms[0].type == PermissionType.OWNER

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
