"""Unit tests for :mod:`app.connectors.sources.local_fs.connector`.

``connector.py`` registers via ``ConnectorBuilder.build_decorator``, which imports
``connector_registry`` and thus ``ConnectorAppContainer``. Install lightweight
``sys.modules`` shims *before* importing the connector (same pattern as
``test_mariadb_client.py``) so the full DI graph is not loaded.
"""

from __future__ import annotations

import asyncio
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

    async def test_get_filter_options_empty(self, folder_connector: LocalFsConnector):
        out = await folder_connector.get_filter_options("anything")
        assert out.success is True
        assert out.options == []

    async def test_test_connection_empty_root_ok(self, folder_connector: LocalFsConnector):
        folder_connector.sync_root_path = ""
        assert await folder_connector.test_connection_and_access() is True

    async def test_init_no_config_ok(self, folder_connector: LocalFsConnector):
        folder_connector.config_service.get_config = AsyncMock(return_value=None)
        assert await folder_connector.init() is True


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
