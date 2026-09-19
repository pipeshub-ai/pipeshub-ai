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
import sys
import time
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

try:
    # redis is a pinned dependency (pyproject: redis==5.2.1) and provides every
    # name stubbed below. Stub only when it is genuinely absent: installing the
    # stub over a working install shadows it, and the stub then has to keep pace
    # with every redis symbol the app imports -- which it did not, so importing
    # this module first in a collection run failed outright.
    import redis.asyncio.cluster  # noqa: F401
    from redis.exceptions import NoScriptError  # noqa: F401
except ImportError:
    _redis_exc = types.ModuleType("redis.exceptions")
    _redis_exc.ConnectionError = type("RedisConnectionError", (Exception,), {})
    _redis_exc.TimeoutError = type("RedisTimeoutError", (Exception,), {})
    # app.services.messaging.distributed_concurrency imports this name at module
    # scope; without it the stub shadows a perfectly good installed redis and
    # the module fails to import whenever nothing else pulled redis in first.
    _redis_exc.NoScriptError = type("RedisNoScriptError", (Exception,), {})
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

from app.config.constants.arangodb import Connectors, MimeTypes, ProgressStatus  # noqa: E402
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
from app.connectors.sources.local_fs import connector as local_fs_module  # noqa: E402
from app.connectors.sources.local_fs.connector import (  # noqa: E402
    LOCAL_FS_CONNECTOR_NAME,
    LOCAL_FS_SERVICE_TOKEN_TTL_SECONDS,
    LOCAL_FS_STORAGE_PATH_PREFIX,
    LocalFsApp,
    LocalFsConnector,
    LocalFsDesktopOfflineError,
    LocalFsDesktopUnreachableError,
    LocalFsDesktopRemoteError,
    LocalFsDesktopTimeoutError,
    LocalFsDeviceMismatchError,
    LocalFsDeviceUnclaimedError,
    LocalFsRootUnavailableError,
    SYNC_ROOT_PATH_KEY,
    _get_datetime_filter_bounds_ms as datetime_filter_bounds_ms,
    _get_sync_config_value as sync_value_from_config,
    _parse_sync_batch_size as parse_batch_size_from_sync,
    _client_path_for_display as client_path_for_display,
    _client_path_leaf_name as client_path_leaf_name,
    _parse_sync_settings as read_sync_settings_from_config,
)
from app.connectors.sources.local_fs.models import (  # noqa: E402
    LocalFsFileEvent,
    LocalFsFileEventBatchStats,
    LocalFsPullBatch,
)
from app.models.entities import (  # noqa: E402
    AppMetadata,
    FileRecord,
    OriginTypes,
    Record,
    RecordGroupType,
    RecordType,
    User,
)
from app.models.permission import PermissionType  # noqa: E402


class TestLocalFsApp:
    def test_init_sets_connector_type(self):
        app = LocalFsApp("conn-x")
        assert app.get_connector_id() == "conn-x"


OWNER_DEVICE_ID = "dev-owner"
OWNER_DEVICE_NAME = "owner-laptop"


def _app_metadata(
    owner_device_id: Optional[str], owner_device_name: Optional[str] = OWNER_DEVICE_NAME
) -> AppMetadata:
    return AppMetadata.from_db_document(
        {
            "_key": "connector-instance-1",
            "name": "Local FS",
            "type": LOCAL_FS_CONNECTOR_NAME,
            "ownerDeviceId": owner_device_id,
            "ownerDeviceName": owner_device_name,
        }
    )


@pytest.fixture
def folder_connector() -> LocalFsConnector:
    logger = MagicMock()
    proc = MagicMock()
    proc.org_id = "org-1"
    # data_entities_processor is the seam every owner/record lookup now goes
    # through (no more tx_store); default these to harmless AsyncMocks so
    # tests that don't care about a given call don't hit
    # "MagicMock can't be used in 'await' expression".
    proc.get_user_by_user_id = AsyncMock(return_value=None)
    proc.get_record_by_external_id = AsyncMock(return_value=None)
    proc.get_records_by_status = AsyncMock(return_value=[])
    proc.on_record_deleted = AsyncMock()
    proc.on_new_app_users = AsyncMock()
    proc.on_new_record_groups = AsyncMock()
    proc.on_new_records = AsyncMock()
    proc.on_records_moved = AsyncMock()
    proc.reindex_existing_records = AsyncMock()
    proc.get_app_by_id = AsyncMock(return_value=_app_metadata(OWNER_DEVICE_ID))
    return LocalFsConnector(
        logger,
        proc,
        MagicMock(),
        MagicMock(),
        "connector-instance-1",
        "personal",
        "test-user",
    )


async def apply_batch(
    connector: LocalFsConnector,
    events: list[LocalFsFileEvent],
    *,
    emitted_folder_paths: set[str] | None = None,
    seen_external_ids: set[str] | None = None,
) -> LocalFsFileEventBatchStats:
    """Apply one page the way run_sync does, so tests exercise the real path."""
    await connector._reload_sync_settings()
    root = client_path_for_display(connector.sync_root_path)
    owner, rg_external = await connector._ensure_owner_and_record_group(root)
    # Through the module so tests patching ``connector.load_connector_filters``
    # still intercept it, exactly as they do for run_sync.
    sync_filters, indexing_filters = await local_fs_module.load_connector_filters(
        connector.config_service, "localfs", connector.connector_id, connector.logger
    )
    return await connector._apply_file_event_batch(
        events,
        owner=owner,
        sync_filters=sync_filters,
        indexing_filters=indexing_filters,
        external_record_group_id=rg_external,
        root_for_display=root,
        emitted_folder_paths=(
            emitted_folder_paths if emitted_folder_paths is not None else set()
        ),
        seen_external_ids=seen_external_ids,
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

    def test_folder_record_uses_file_record_type_with_folder_flag(
        self, folder_connector: LocalFsConnector, tmp_path: Path
    ):
        ev = LocalFsFileEvent(
            type="DIR_CREATED", path="docs", timestamp=1234, isDirectory=True,
        )
        folder_record, _permissions = folder_connector._build_folder_record(
            "docs",
            tmp_path,
            folder_connector._record_group_external_id(),
            ev,
        )

        assert folder_record.record_type == RecordType.FILE
        assert folder_record.is_file is False
        assert folder_record.mime_type == MimeTypes.FOLDER.value

    def test_folder_record_source_created_at_uses_birthtime(
        self, folder_connector: LocalFsConnector, tmp_path: Path
    ):
        ev = LocalFsFileEvent(
            type="DIR_CREATED",
            path="docs",
            timestamp=9_000,
            mtimeMs=5_000,
            birthtimeMs=1_000,
            isDirectory=True,
        )
        folder_record, _permissions = folder_connector._build_folder_record(
            "docs",
            tmp_path,
            folder_connector._record_group_external_id(),
            ev,
        )
        assert folder_record.source_created_at == 1_000
        assert folder_record.source_updated_at == 5_000
        # created_at/updated_at are PipesHub's own bookkeeping, not source
        # metadata — those stay on the observed event time.
        assert folder_record.created_at == 9_000
        assert folder_record.updated_at == 9_000

    def test_folder_record_source_created_at_falls_back_without_birthtime(
        self, folder_connector: LocalFsConnector, tmp_path: Path
    ):
        ev = LocalFsFileEvent(
            type="DIR_CREATED",
            path="docs",
            timestamp=9_000,
            mtimeMs=5_000,
            isDirectory=True,
        )
        folder_record, _permissions = folder_connector._build_folder_record(
            "docs",
            tmp_path,
            folder_connector._record_group_external_id(),
            ev,
        )
        assert folder_record.source_created_at == 5_000

    def test_folder_record_ancestor_placeholder_leaves_source_times_unset(
        self, folder_connector: LocalFsConnector, tmp_path: Path
    ):
        # "docs" is being synthesized as an ancestor of some descendant file's
        # event — its mtime/birthtime describe that file, not this folder, so
        # they must not be written as this folder's source timestamps.
        ev = LocalFsFileEvent(
            type="CREATED",
            path="docs/report.pdf",
            timestamp=9_000,
            mtimeMs=5_000,
            birthtimeMs=1_000,
            isDirectory=False,
        )
        folder_record, _permissions = folder_connector._build_folder_record(
            "docs",
            tmp_path,
            folder_connector._record_group_external_id(),
            ev,
            is_ancestor_placeholder=True,
        )
        assert folder_record.source_created_at is None
        assert folder_record.source_updated_at is None
        # Bookkeeping fields still advance on every touch, ancestor or not.
        assert folder_record.created_at == 9_000
        assert folder_record.updated_at == 9_000

    def test_build_parent_folder_records_marks_ancestors_as_placeholders(
        self, folder_connector: LocalFsConnector, tmp_path: Path
    ):
        ev = LocalFsFileEvent(
            type="CREATED",
            path="a/b/report.pdf",
            timestamp=9_000,
            mtimeMs=5_000,
            birthtimeMs=1_000,
            isDirectory=False,
        )
        records = folder_connector._build_parent_folder_records(
            "a/b/report.pdf",
            tmp_path,
            folder_connector._record_group_external_id(),
            ev,
            set(),
        )
        paths = {r[0].local_fs_relative_path for r in records}
        assert paths == {"a", "a/b"}
        for record, _perms in records:
            assert record.source_created_at is None
            assert record.source_updated_at is None

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
        assert folder_connector._pass_extension_filters(Path("a.PDF"), coll) is True

    def test_extension_allowed_in(self, folder_connector: LocalFsConnector):
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
        assert folder_connector._pass_extension_filters(Path("x.pdf"), coll) is True
        assert folder_connector._pass_extension_filters(Path("x.PDF"), coll) is True
        assert folder_connector._pass_extension_filters(Path("x.md"), coll) is False
        assert folder_connector._pass_extension_filters(Path("README"), coll) is False

    def test_extension_allowed_not_in(self, folder_connector: LocalFsConnector):
        coll = FilterCollection(
            filters=[
                Filter(
                    key=SyncFilterKey.FILE_EXTENSIONS.value,
                    type=FilterType.MULTISELECT,
                    operator=MultiselectOperator.NOT_IN,
                    value=["pdf", "txt"],
                )
            ]
        )
        assert folder_connector._pass_extension_filters(Path("x.pdf"), coll) is False
        assert folder_connector._pass_extension_filters(Path("x.PDF"), coll) is False
        assert folder_connector._pass_extension_filters(Path("x.md"), coll) is True
        assert folder_connector._pass_extension_filters(Path("README"), coll) is True

    def test_extension_filter_skips_directories(self, folder_connector: LocalFsConnector):
        coll = FilterCollection(
            filters=[
                Filter(
                    key=SyncFilterKey.FILE_EXTENSIONS.value,
                    type=FilterType.MULTISELECT,
                    operator=MultiselectOperator.IN,
                    value=["pdf"],
                )
            ]
        )
        assert (
            folder_connector._pass_extension_filters(
                Path("docs"), coll, is_directory=True
            )
            is True
        )
        assert (
            folder_connector._pass_extension_filters(
                Path("archive.pdf"), coll, is_directory=True
            )
            is True
        )

    def test_build_file_record_indexing_off_and_no_owner_perms(
        self, folder_connector: LocalFsConnector
    ):
        """Manual indexing on and no owner ⇒ auto-index off, no permissions rows."""
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
                    key=IndexingFilterKey.ENABLE_MANUAL_SYNC.value,
                    type=FilterType.BOOLEAN,
                    operator=BooleanOperator.IS,
                    value=True,
                )
            ]
        )
        rec, perms = folder_connector._build_file_record(
            "folder/x.txt",
            ev,
            "rg-ext",
            indexing,
            owner=None,
        )
        assert rec.indexing_status == ProgressStatus.AUTO_INDEX_OFF.value
        assert rec.local_fs_relative_path == "folder/x.txt"
        assert rec.weburl is None
        assert rec.hide_weburl is True
        assert perms == []

    def test_build_file_record_path_is_never_blank(
        self, folder_connector: LocalFsConnector
    ):
        # stream_record rejects a blank path with a 400, which the indexing
        # consumer treats as terminal — so the record would never be retried.
        ev = LocalFsFileEvent(
            type="CREATED",
            path="x.txt",
            timestamp=1_700_000_000,
            size=4,
            isDirectory=False,
        )
        rec, _perms = folder_connector._build_file_record(
            "folder/x.txt", ev, "rg-ext", FilterCollection(filters=[]), owner=None
        )
        assert rec.path == "folder/x.txt"

    def test_build_file_record_source_created_at_uses_birthtime(
        self, folder_connector: LocalFsConnector
    ):
        ev = LocalFsFileEvent(
            type="CREATED",
            path="x.txt",
            timestamp=9_000,
            mtimeMs=5_000,
            birthtimeMs=1_000,
            size=4,
            isDirectory=False,
        )
        rec, _perms = folder_connector._build_file_record(
            "folder/x.txt", ev, "rg-ext", FilterCollection(filters=[]), owner=None
        )
        assert rec.source_created_at == 1_000
        assert rec.source_updated_at == 5_000
        # File clocks must stay in source_*: created_at/updated_at are what the
        # stranded-record sweep ages rows on, so an old file would look stranded
        # the moment it is synced and be republished on every tick.
        assert rec.created_at == 9_000
        assert rec.updated_at == 9_000

    def test_build_file_record_source_created_at_falls_back_to_mtime(
        self, folder_connector: LocalFsConnector
    ):
        # No birthtimeMs at all (pre-upgrade desktop or a replayed journal entry).
        ev = LocalFsFileEvent(
            type="CREATED",
            path="x.txt",
            timestamp=9_000,
            mtimeMs=5_000,
            size=4,
            isDirectory=False,
        )
        rec, _perms = folder_connector._build_file_record(
            "folder/x.txt", ev, "rg-ext", FilterCollection(filters=[]), owner=None
        )
        assert rec.source_created_at == 5_000
        assert rec.source_updated_at == 5_000

    def test_build_file_record_source_created_at_ignores_unusable_birthtime(
        self, folder_connector: LocalFsConnector
    ):
        # A filesystem with no btime support reports 0 rather than omitting
        # the field; that must not be read as "created in 1970".
        ev = LocalFsFileEvent(
            type="CREATED",
            path="x.txt",
            timestamp=9_000,
            mtimeMs=5_000,
            birthtimeMs=0,
            size=4,
            isDirectory=False,
        )
        rec, _perms = folder_connector._build_file_record(
            "folder/x.txt", ev, "rg-ext", FilterCollection(filters=[]), owner=None
        )
        assert rec.source_created_at == 5_000

    def test_build_file_record_prefers_desktop_mime_over_guess(
        self, folder_connector: LocalFsConnector
    ):
        # mimetypes.guess_type has no .webp entry before Python 3.13, so without
        # the desktop's value the record is stored as application/unknown, which
        # maps back to no extension and leaves process_image with no parser.
        ev = LocalFsFileEvent(
            type="CREATED",
            path="photo.webp",
            timestamp=1_700_000_000,
            size=4,
            isDirectory=False,
            mimeType="image/webp",
        )
        rec, _perms = folder_connector._build_file_record(
            "folder/photo.webp",
            ev,
            "rg-ext",
            FilterCollection(filters=[]),
            owner=None,
        )
        assert rec.mime_type == "image/webp"
        assert rec.extension == "webp"

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
    async def test_stream_record_fetches_from_desktop(
        self, folder_connector: LocalFsConnector
    ):
        folder_connector._fetch_desktop_content = AsyncMock(
            return_value=b"hello-stream"
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
            path="blob.bin",
            local_fs_relative_path="blob.bin",
            mime_type="application/octet-stream",
            record_group_type=RecordGroupType.DRIVE,
        )
        resp = await folder_connector.stream_record(rec)
        assert resp.body == b"hello-stream"
        assert 'attachment; filename="blob.bin"' in resp.headers.get(
            "content-disposition", ""
        )

    async def test_stream_record_uses_safe_content_disposition_for_unicode_name(
        self, folder_connector: LocalFsConnector
    ):
        folder_connector._fetch_desktop_content = AsyncMock(return_value=b"x")
        rec = FileRecord(
            record_name="3.10.12 PM.png",
            record_type=RecordType.FILE,
            external_record_id="e2",
            version=0,
            origin=OriginTypes.CONNECTOR,
            connector_name=Connectors.LOCAL_FS,
            connector_id="c1",
            is_file=True,
            path="3.10.12 PM.png",
            local_fs_relative_path="3.10.12 PM.png",
            mime_type="application/octet-stream",
            record_group_type=RecordGroupType.DRIVE,
        )
        resp = await folder_connector.stream_record(rec)
        content_disposition = resp.headers.get("content-disposition", "")
        # U+202F is stripped by sanitize_filename_for_content_disposition (latin-1).
        assert " " not in content_disposition
        assert 'attachment; filename="3.10.12PM.png"' in content_disposition

    async def test_stream_record_offline_desktop_is_retryable_503(
        self, folder_connector: LocalFsConnector
    ):
        # 503 is TRANSIENT to the indexing consumer, so the record is retried
        # when the machine comes back instead of being failed permanently.
        folder_connector._fetch_desktop_content = AsyncMock(
            side_effect=LocalFsDesktopOfflineError(
                "The desktop that owns this file is not connected"
            )
        )
        rec = FileRecord(
            record_name="a.txt",
            record_type=RecordType.FILE,
            external_record_id="e3",
            version=0,
            origin=OriginTypes.CONNECTOR,
            connector_name=Connectors.LOCAL_FS,
            connector_id="c1",
            is_file=True,
            path="a.txt",
            local_fs_relative_path="a.txt",
            mime_type="text/plain",
            record_group_type=RecordGroupType.DRIVE,
        )
        with pytest.raises(HTTPException) as ei:
            await folder_connector.stream_record(rec)
        assert ei.value.status_code == HttpStatusCode.SERVICE_UNAVAILABLE.value
        assert (
            ei.value.detail
            == "The desktop that owns this file is not connected"
        )

    async def test_stream_record_unreadable_file_is_terminal_404(
        self, folder_connector: LocalFsConnector
    ):
        folder_connector._fetch_desktop_content = AsyncMock(
            side_effect=LocalFsDesktopRemoteError(
                "ROOT_UNREADABLE", "gone", retryable=False
            )
        )
        rec = FileRecord(
            record_name="a.txt",
            record_type=RecordType.FILE,
            external_record_id="e4",
            version=0,
            origin=OriginTypes.CONNECTOR,
            connector_name=Connectors.LOCAL_FS,
            connector_id="c1",
            is_file=True,
            path="a.txt",
            local_fs_relative_path="a.txt",
            mime_type="text/plain",
            record_group_type=RecordGroupType.DRIVE,
        )
        with pytest.raises(HTTPException) as ei:
            await folder_connector.stream_record(rec)
        assert ei.value.status_code == HttpStatusCode.NOT_FOUND.value

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

    async def test_get_filter_options_not_implemented(
        self, folder_connector: LocalFsConnector
    ):
        with pytest.raises(NotImplementedError):
            await folder_connector.get_filter_options("anything")

    async def test_test_connection_empty_root_ok(self, folder_connector: LocalFsConnector):
        folder_connector.sync_root_path = ""
        assert await folder_connector.test_connection_and_access() is True

    async def test_get_signed_url_returns_none(self, folder_connector: LocalFsConnector):
        """Local FS does not expose signed URLs (files are local or storage-backed)."""
        assert await folder_connector.get_signed_url(MagicMock()) is None

    async def test_test_connection_desktop_only_path_ok(
        self, folder_connector: LocalFsConnector
    ):
        folder_connector.sync_root_path = r"C:\Users\me\Documents"
        assert await folder_connector.test_connection_and_access() is True
        folder_connector.logger.warning.assert_not_called()

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

    async def test_apply_file_event_batch_builds_record_without_backend_path(
        self, folder_connector: LocalFsConnector, tmp_path: Path
    ):
        folder_connector.config_service.get_config = AsyncMock(
            return_value={"sync": {"customValues": {SYNC_ROOT_PATH_KEY: str(tmp_path / "desktop-only")}}}
        )
        user = User(email="u@x.com", id="u1", org_id="org-1")
        folder_connector.data_entities_processor.get_user_by_user_id = AsyncMock(
            return_value=user
        )

        with patch(
            "app.connectors.sources.local_fs.connector.load_connector_filters",
            new=AsyncMock(return_value=(FilterCollection(filters=[]), FilterCollection(filters=[]))),
        ):
            folder_connector.data_entities_processor.on_new_app_users = AsyncMock()
            folder_connector.data_entities_processor.on_new_record_groups = AsyncMock()
            folder_connector.data_entities_processor.on_new_records = AsyncMock()
            stats = await apply_batch(folder_connector, 
                [
                    LocalFsFileEvent(
                        type="CREATED",
                        path="notes/a.txt",
                        timestamp=1000,
                        size=12,
                        isDirectory=False,
                        sha256="2d119f1cd272958a492a144af600b9dc36531f73027b34073967345b027021b1",
                        mimeType="text/plain",
                    )
                ],
            )

        assert stats.processed == 1
        folder_connector.data_entities_processor.on_new_records.assert_awaited_once()
        records = folder_connector.data_entities_processor.on_new_records.await_args.args[0]
        folder_record, _folder_permissions = records[0]
        record, permissions = records[1]
        assert folder_record.local_fs_relative_path == "notes"
        assert folder_record.is_file is False
        assert folder_record.mime_type == MimeTypes.FOLDER.value
        assert folder_record.weburl is None
        assert folder_record.hide_weburl is True
        assert record.weburl is None
        assert record.hide_weburl is True
        assert record.parent_external_record_id == folder_connector._external_record_id_for_rel_path("notes")
        assert record.path == "notes/a.txt"
        assert record.record_name == "a.txt"
        assert record.external_revision_id == "2d119f1cd272958a492a144af600b9dc36531f73027b34073967345b027021b1"
        assert permissions[0].type == PermissionType.OWNER

    async def test_apply_file_event_batch_skips_event_missing_sha256(
        self, folder_connector: LocalFsConnector, tmp_path: Path
    ):
        """No fallback revision: an event with no content hash from the
        watcher is skipped and counted rather than given a synthetic
        timestamp/size revision that would defeat content-change detection.
        """
        folder_connector.config_service.get_config = AsyncMock(
            return_value={"sync": {"customValues": {SYNC_ROOT_PATH_KEY: str(tmp_path / "desktop-only")}}}
        )
        user = User(email="u@x.com", id="u1", org_id="org-1")
        folder_connector.data_entities_processor.get_user_by_user_id = AsyncMock(
            return_value=user
        )

        with patch(
            "app.connectors.sources.local_fs.connector.load_connector_filters",
            new=AsyncMock(return_value=(FilterCollection(filters=[]), FilterCollection(filters=[]))),
        ):
            folder_connector.data_entities_processor.on_new_app_users = AsyncMock()
            folder_connector.data_entities_processor.on_new_record_groups = AsyncMock()
            folder_connector.data_entities_processor.on_new_records = AsyncMock()
            stats = await apply_batch(folder_connector,
                [
                    LocalFsFileEvent(
                        type="CREATED",
                        path="notes/a.txt",
                        timestamp=1000,
                        size=12,
                        isDirectory=False,
                        sha256=None,
                        mimeType="text/plain",
                    )
                ],
            )

        assert stats.skipped == 1
        assert stats.processed == 0
        folder_connector.data_entities_processor.on_new_records.assert_not_awaited()

    async def test_apply_file_event_batch_emits_parent_folders(
        self, folder_connector: LocalFsConnector, tmp_path: Path
    ):
        folder_connector.config_service.get_config = AsyncMock(
            return_value={"sync": {"customValues": {SYNC_ROOT_PATH_KEY: str(tmp_path / "desktop-only")}}}
        )
        user = User(email="u@x.com", id="u1", org_id="org-1")
        folder_connector.data_entities_processor.get_user_by_user_id = AsyncMock(
            return_value=user
        )
        folder_connector._upload_storage_file = AsyncMock(return_value="doc-123")

        with patch(
            "app.connectors.sources.local_fs.connector.load_connector_filters",
            new=AsyncMock(return_value=(FilterCollection(filters=[]), FilterCollection(filters=[]))),
        ):
            folder_connector.data_entities_processor.on_new_app_users = AsyncMock()
            folder_connector.data_entities_processor.on_new_record_groups = AsyncMock()
            folder_connector.data_entities_processor.on_new_records = AsyncMock()
            stats = await apply_batch(folder_connector, 
                [
                    LocalFsFileEvent(
                        type="CREATED",
                        path="notes/projects/a.txt",
                        timestamp=1000,
                        size=12,
                        isDirectory=False,
                        sha256=hashlib.sha256(b"hello upload").hexdigest(),
                        mimeType="text/plain",
                    )
                ],
            )

        assert stats.processed == 1
        records = folder_connector.data_entities_processor.on_new_records.await_args.args[0]
        emitted = {record.local_fs_relative_path: record for record, _perms in records}
        assert emitted["notes"].is_file is False
        assert emitted["notes"].mime_type == MimeTypes.FOLDER.value
        assert emitted["notes"].parent_external_record_id is None
        assert emitted["notes/projects"].is_file is False
        assert emitted["notes/projects"].parent_external_record_id == folder_connector._external_record_id_for_rel_path("notes")
        assert emitted["notes/projects/a.txt"].parent_external_record_id == folder_connector._external_record_id_for_rel_path("notes/projects")
        assert emitted["notes/projects/a.txt"].parent_record_type == RecordType.FILE

    async def test_apply_delete_removes_storage_document_and_record(
        self, folder_connector: LocalFsConnector, tmp_path: Path
    ):
        folder_connector.config_service.get_config = AsyncMock(
            return_value={"sync": {"customValues": {SYNC_ROOT_PATH_KEY: str(tmp_path / "desktop-only")}}}
        )
        user = User(email="u@x.com", id="u1", org_id="org-1")
        existing = FileRecord(
            id="rec-del-1",
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
        folder_connector.data_entities_processor.get_user_by_user_id = AsyncMock(
            return_value=user
        )
        folder_connector.data_entities_processor.get_record_by_external_id = AsyncMock(
            return_value=existing
        )
        folder_connector.data_entities_processor.on_record_deleted = AsyncMock()
        folder_connector._delete_storage_document = AsyncMock()

        with patch(
            "app.connectors.sources.local_fs.connector.load_connector_filters",
            new=AsyncMock(return_value=(FilterCollection(filters=[]), FilterCollection(filters=[]))),
        ):
            folder_connector.data_entities_processor.on_new_app_users = AsyncMock()
            folder_connector.data_entities_processor.on_new_record_groups = AsyncMock()
            stats = await apply_batch(folder_connector, 
                [
                    LocalFsFileEvent(
                        type="DELETED",
                        path="old.txt",
                        timestamp=1000,
                        isDirectory=False,
                    )
                ],
            )

        assert stats.deleted == 1
        # _delete_external_ids resolves and GCs the blob itself, so the record
        # and its storage document go in one pass — no second delete.
        assert folder_connector._delete_storage_document.await_count == 1
        assert folder_connector._delete_storage_document.await_args.args == ("doc-del",)
        folder_connector.data_entities_processor.on_record_deleted.assert_awaited_once_with(
            record_id=existing.id,
        )

    async def test_apply_rename_uses_on_records_moved(
        self, folder_connector: LocalFsConnector, tmp_path: Path
    ):
        """RENAMED/MOVED update the DB record in place via on_records_moved
        (same vertex, re-pointed parent edge) instead of deleting the old
        row and creating a new one — no on_record_deleted for the old path.
        """
        folder_connector.config_service.get_config = AsyncMock(
            return_value={"sync": {"customValues": {SYNC_ROOT_PATH_KEY: str(tmp_path / "desktop-only")}}}
        )
        user = User(email="u@x.com", id="u1", org_id="org-1")
        folder_connector.data_entities_processor.get_user_by_user_id = AsyncMock(
            return_value=user
        )

        with patch(
            "app.connectors.sources.local_fs.connector.load_connector_filters",
            new=AsyncMock(return_value=(FilterCollection(filters=[]), FilterCollection(filters=[]))),
        ):
            folder_connector.data_entities_processor.on_new_app_users = AsyncMock()
            folder_connector.data_entities_processor.on_new_record_groups = AsyncMock()
            folder_connector.data_entities_processor.on_new_records = AsyncMock()
            folder_connector.data_entities_processor.on_records_moved = AsyncMock()
            folder_connector.data_entities_processor.on_record_deleted = AsyncMock()

            stats = await apply_batch(folder_connector,
                [
                    LocalFsFileEvent(
                        type="RENAMED",
                        path="a/new.txt",
                        oldPath="a/old.txt",
                        timestamp=1000,
                        size=4,
                        isDirectory=False,
                        sha256=hashlib.sha256(b"data").hexdigest(),
                        mimeType="text/plain",
                    )
                ],
            )

        old_ext_id = folder_connector._external_record_id_for_rel_path("a/old.txt")
        folder_connector.data_entities_processor.on_records_moved.assert_awaited_once()
        moves = folder_connector.data_entities_processor.on_records_moved.await_args.args[0]
        assert len(moves) == 1
        moved_old_ext_id, new_record, _perms = moves[0]
        assert moved_old_ext_id == old_ext_id
        assert new_record.local_fs_relative_path == "a/new.txt"
        assert new_record.record_name == "new.txt"

        # on_new_records may still be awaited for the unrelated ancestor
        # folder ("a"), but never for the renamed file itself, and the old
        # row is never explicitly deleted — on_records_moved retires it.
        if folder_connector.data_entities_processor.on_new_records.await_args is not None:
            upserted_paths = {
                r.local_fs_relative_path
                for r, _p in folder_connector.data_entities_processor.on_new_records.await_args.args[0]
            }
            assert "a/new.txt" not in upserted_paths
            assert "a/old.txt" not in upserted_paths
        folder_connector.data_entities_processor.on_record_deleted.assert_not_awaited()
        assert stats.processed == 1
        assert stats.deleted == 0

    async def test_apply_move_passes_source_hash_as_revision_for_content_check(
        self, folder_connector: LocalFsConnector, tmp_path: Path
    ):
        """on_records_moved decides content_changed by comparing
        external_revision_id against the stored value, so the connector must
        forward the desktop's sha256 unchanged — a rename with an identical
        hash must produce a move whose new record's revision matches the
        event's hash (so on_records_moved treats it as a pure rename and
        skips re-indexing), and a MOVED event with a different hash must
        carry that new hash through (so on_records_moved treats it as a
        content change and re-queues indexing).
        """
        folder_connector.config_service.get_config = AsyncMock(
            return_value={"sync": {"customValues": {SYNC_ROOT_PATH_KEY: str(tmp_path / "desktop-only")}}}
        )
        user = User(email="u@x.com", id="u1", org_id="org-1")
        folder_connector.data_entities_processor.get_user_by_user_id = AsyncMock(
            return_value=user
        )
        same_hash = hashlib.sha256(b"unchanged content").hexdigest()
        new_hash = hashlib.sha256(b"changed content").hexdigest()

        with patch(
            "app.connectors.sources.local_fs.connector.load_connector_filters",
            new=AsyncMock(return_value=(FilterCollection(filters=[]), FilterCollection(filters=[]))),
        ):
            folder_connector.data_entities_processor.on_new_app_users = AsyncMock()
            folder_connector.data_entities_processor.on_new_record_groups = AsyncMock()
            folder_connector.data_entities_processor.on_new_records = AsyncMock()
            folder_connector.data_entities_processor.on_records_moved = AsyncMock()

            await apply_batch(folder_connector,
                [
                    LocalFsFileEvent(
                        type="MOVED",
                        path="b/unchanged.txt",
                        oldPath="a/unchanged.txt",
                        timestamp=1000,
                        size=17,
                        isDirectory=False,
                        sha256=same_hash,
                        mimeType="text/plain",
                    ),
                    LocalFsFileEvent(
                        type="MOVED",
                        path="b/changed.txt",
                        oldPath="a/changed.txt",
                        timestamp=1000,
                        size=15,
                        isDirectory=False,
                        sha256=new_hash,
                        mimeType="text/plain",
                    ),
                ],
            )

        moves = folder_connector.data_entities_processor.on_records_moved.await_args.args[0]
        by_path = {record.local_fs_relative_path: record for _old, record, _perms in moves}
        assert by_path["b/unchanged.txt"].external_revision_id == same_hash
        assert by_path["b/changed.txt"].external_revision_id == new_hash


@pytest.mark.asyncio
async def test_handle_webhook_notification_logs():
    logger = MagicMock()
    proc = MagicMock()
    c = LocalFsConnector(logger, proc, MagicMock(), MagicMock(), "id", "personal", "u")
    c.handle_webhook_notification({})
    logger.debug.assert_called()


@pytest.mark.asyncio
async def test_cleanup_drops_cached_service_tokens():
    logger = MagicMock()
    proc = MagicMock()
    c = LocalFsConnector(logger, proc, MagicMock(), MagicMock(), "id", "personal", "u")
    c._desktop_token_cache = "desktop-tok"
    c._batch_storage_token_cache = "storage-tok"
    c._batch_storage_url_cache = "http://storage"
    c._owner_user_for_permissions = MagicMock()

    await c.cleanup()

    assert c._desktop_token_cache is None
    assert c._batch_storage_token_cache is None
    assert c._batch_storage_url_cache is None
    assert c._owner_user_for_permissions is None
    logger.info.assert_called()


def test_local_fs_connector_name_constant():
    assert LOCAL_FS_CONNECTOR_NAME == "Local FS"


# ===========================================================================
# Merged from former test_local_fs_connector_helpers.py
# ===========================================================================

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


class TestNormalizeEventRelPath:
    def test_strips_and_normalizes_separators(self):
        assert (
            LocalFsConnector._normalize_event_rel_path("  a\\b\\c.txt  ")
            == "a/b/c.txt"
        )

    def test_rejects_empty(self):
        # None rather than raising: a single bad path must skip its event, not
        # abandon the whole run.
        assert LocalFsConnector._normalize_event_rel_path("") is None

    def test_rejects_absolute(self):
        assert LocalFsConnector._normalize_event_rel_path("/abs/path") is None

    def test_rejects_dot_segments(self):
        for bad in ("a/./b", "a/../b", "..", ".", "a//b"):
            assert LocalFsConnector._normalize_event_rel_path(bad) is None, bad

    def test_accepts_simple_relative(self):
        assert LocalFsConnector._normalize_event_rel_path("a.txt") == "a.txt"
        assert (
            LocalFsConnector._normalize_event_rel_path("nested/dir/file.txt")
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
        folder_connector.logger.info.assert_not_called()

    async def test_init_accepts_desktop_only_path(self, folder_connector):
        folder_connector.config_service.get_config = AsyncMock(
            return_value={
                "sync": {SYNC_ROOT_PATH_KEY: "/does/not/exist/local-fs-test"}
            }
        )
        ok = await folder_connector.init()
        assert ok is True
        assert folder_connector.sync_root_path == "/does/not/exist/local-fs-test"
        folder_connector.logger.warning.assert_not_called()
        folder_connector.logger.info.assert_not_called()

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
        folder_connector._batch_storage_token_minted_at = time.monotonic()
        with patch(
            "app.connectors.sources.local_fs.connector.generate_jwt",
            new=AsyncMock(return_value="other-tok"),
        ) as gen:
            tok = await folder_connector._storage_token()
            assert tok == "cached-tok"
            gen.assert_not_awaited()

    async def test_remints_when_cached_token_is_past_ttl(self, folder_connector):
        """A parked connector instance outlives the 1h JWT; the cache must expire."""
        folder_connector._batch_storage_token_cache = "stale-tok"
        folder_connector._batch_storage_token_minted_at = (
            time.monotonic() - LOCAL_FS_SERVICE_TOKEN_TTL_SECONDS - 1
        )
        with patch(
            "app.connectors.sources.local_fs.connector.generate_jwt",
            new=AsyncMock(return_value="fresh-tok"),
        ) as gen:
            tok = await folder_connector._storage_token()
            assert tok == "fresh-tok"
            gen.assert_awaited_once()
            assert folder_connector._batch_storage_token_cache == "fresh-tok"

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
class TestDeleteExternalIds:
    async def test_empty_list_does_not_open_transaction(
        self, folder_connector, monkeypatch
    ):
        spy = MagicMock()
        folder_connector.data_store_provider.transaction = spy
        await folder_connector._delete_external_ids([], "user-1")
        spy.assert_not_called()


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
    """run_sync pulls pages from the desktop and derives FULL vs INCREMENTAL
    from the sync point."""

    def _prepare(self, connector: LocalFsConnector, tmp_path: Path, sync_point: dict):
        connector.config_service.get_config = AsyncMock(
            return_value={"sync": {SYNC_ROOT_PATH_KEY: str(tmp_path)}}
        )
        connector._ensure_owner_and_record_group = AsyncMock(
            return_value=(User(email="u@x.com", id="u1", org_id="org-1"), "rg-ext")
        )
        connector._apply_file_event_batch = AsyncMock(
            return_value=LocalFsFileEventBatchStats(processed=1, deleted=0)
        )
        connector.record_sync_point.read_sync_point = AsyncMock(return_value=sync_point)
        connector.record_sync_point.update_sync_point = AsyncMock()
        connector._prune_unseen_records = AsyncMock(return_value=0)

    @staticmethod
    def _page(**kwargs) -> LocalFsPullBatch:
        payload = {
            "connectorId": "connector-instance-1",
            "runId": "run",
            "batchIndex": 0,
            "cursor": "c1",
            "hasMore": False,
            "events": [],
        }
        payload.update(kwargs)
        return LocalFsPullBatch(**payload)

    async def test_desktop_failure_is_raised_not_reported_as_a_finished_sync(
        self, folder_connector: LocalFsConnector, tmp_path: Path
    ):
        # Returning here logged the run as successful, so a connector that can
        # never finish looked healthy on every tick.
        self._prepare(folder_connector, tmp_path, {})
        folder_connector.notify = AsyncMock()
        folder_connector._pull_with_retry = AsyncMock(
            side_effect=LocalFsDesktopUnreachableError(
                3, LocalFsDesktopTimeoutError("slow")
            )
        )

        with pytest.raises(LocalFsDesktopUnreachableError):
            await folder_connector.run_sync()

        folder_connector.notify.assert_awaited_once()
        payload = folder_connector.notify.await_args.kwargs["payload"]
        assert payload["error_code"] == "DESKTOP_TIMEOUT"

    async def test_full_run_failure_says_the_next_run_starts_over(
        self, folder_connector: LocalFsConnector, tmp_path: Path
    ):
        self._prepare(folder_connector, tmp_path, {})
        folder_connector.notify = AsyncMock()
        folder_connector._pull_with_retry = AsyncMock(
            side_effect=LocalFsDesktopRemoteError(
                "RUN_TOO_LONG", "run exceeded 21600s", retryable=False
            )
        )

        with pytest.raises(LocalFsDesktopRemoteError):
            await folder_connector.run_sync()

        message = folder_connector.notify.await_args.kwargs["message"]
        assert "starts this folder over from the beginning" in message

    async def test_incremental_run_failure_says_the_next_run_resumes(
        self, folder_connector: LocalFsConnector, tmp_path: Path
    ):
        self._prepare(folder_connector, tmp_path, {"last_sync_time": 1})
        folder_connector.notify = AsyncMock()
        folder_connector._pull_with_retry = AsyncMock(
            side_effect=LocalFsDesktopRemoteError("INTERNAL", "boom", retryable=False)
        )

        with pytest.raises(LocalFsDesktopRemoteError):
            await folder_connector.run_sync()

        message = folder_connector.notify.await_args.kwargs["message"]
        assert "resumes from where this one stopped" in message

    async def test_no_sync_point_runs_full(
        self, folder_connector: LocalFsConnector, tmp_path: Path
    ):
        self._prepare(folder_connector, tmp_path, {})
        folder_connector._pull_with_retry = AsyncMock(return_value=self._page())

        await folder_connector.run_sync()

        assert folder_connector._pull_with_retry.await_args.kwargs["mode"] == "FULL"
        # A full run is the only one that may prune.
        folder_connector._prune_unseen_records.assert_awaited_once()

    async def test_existing_sync_point_runs_incremental_from_cursor(
        self, folder_connector: LocalFsConnector, tmp_path: Path
    ):
        self._prepare(
            folder_connector, tmp_path, {"last_sync_time": 123, "cursor": "c0"}
        )
        folder_connector._pull_with_retry = AsyncMock(return_value=self._page())

        await folder_connector.run_sync()

        kwargs = folder_connector._pull_with_retry.await_args.kwargs
        assert kwargs["mode"] == "INCREMENTAL"
        assert kwargs["cursor"] == "c0"
        folder_connector._prune_unseen_records.assert_not_awaited()

    async def test_cursor_only_sync_point_still_runs_full(
        self, folder_connector: LocalFsConnector, tmp_path: Path
    ):
        # A full run that died mid-way leaves a cursor but no last_sync_time.
        # A partial enumeration is not a baseline, so the next run is FULL.
        self._prepare(folder_connector, tmp_path, {"cursor": "c9"})
        folder_connector._pull_with_retry = AsyncMock(return_value=self._page())

        await folder_connector.run_sync()

        kwargs = folder_connector._pull_with_retry.await_args.kwargs
        assert kwargs["mode"] == "FULL"
        assert kwargs["cursor"] is None

    async def test_pages_until_has_more_clears(
        self, folder_connector: LocalFsConnector, tmp_path: Path
    ):
        self._prepare(folder_connector, tmp_path, {})
        pages = [
            self._page(batchIndex=0, cursor="c1", hasMore=True),
            self._page(batchIndex=1, cursor="c2", hasMore=True),
            self._page(batchIndex=2, cursor="c3", hasMore=False),
        ]
        folder_connector._pull_with_retry = AsyncMock(side_effect=pages)

        await folder_connector.run_sync()

        assert folder_connector._pull_with_retry.await_count == 3
        assert folder_connector._apply_file_event_batch.await_count == 3
        # Cursor is persisted per page so a crash costs one page of re-work.
        assert folder_connector.record_sync_point.update_sync_point.await_count == 4

    async def test_incremental_carries_last_sync_time_forward(
        self, folder_connector: LocalFsConnector, tmp_path: Path
    ):
        # update_sync_point rewrites the whole document. Dropping last_sync_time
        # on a mid-run write would demote the next run to a destructive FULL.
        self._prepare(
            folder_connector, tmp_path, {"last_sync_time": 555, "cursor": "c0"}
        )
        folder_connector._pull_with_retry = AsyncMock(
            side_effect=[
                self._page(batchIndex=0, cursor="c1", hasMore=True),
                self._page(batchIndex=1, cursor="c2", hasMore=False),
            ]
        )

        await folder_connector.run_sync()

        writes = folder_connector.record_sync_point.update_sync_point.await_args_list
        assert writes[0].args[1]["last_sync_time"] == 555
        assert writes[-1].args[1]["last_sync_time"] > 555

    async def test_offline_desktop_defers_without_writing_sync_point(
        self, folder_connector: LocalFsConnector, tmp_path: Path
    ):
        # Offline is raised so the event service logs the skip instead of a
        # crash; the sync point must stay untouched so nothing is pruned.
        self._prepare(folder_connector, tmp_path, {})
        folder_connector._pull_with_retry = AsyncMock(
            side_effect=LocalFsDesktopOfflineError("asleep")
        )

        with pytest.raises(LocalFsDesktopOfflineError):
            await folder_connector.run_sync()

        folder_connector.record_sync_point.update_sync_point.assert_not_awaited()
        folder_connector._prune_unseen_records.assert_not_awaited()

    async def test_failure_midrun_does_not_finalize_sync_point(
        self, folder_connector: LocalFsConnector, tmp_path: Path
    ):
        self._prepare(folder_connector, tmp_path, {})
        folder_connector.notify = AsyncMock()
        folder_connector._pull_with_retry = AsyncMock(
            side_effect=[
                self._page(batchIndex=0, cursor="c1", hasMore=True),
                LocalFsRootUnavailableError("ROOT_UNREADABLE", "gone"),
            ]
        )

        with pytest.raises(LocalFsRootUnavailableError):
            await folder_connector.run_sync()

        writes = folder_connector.record_sync_point.update_sync_point.await_args_list
        assert len(writes) == 1
        assert "last_sync_time" not in writes[0].args[1]
        folder_connector._prune_unseen_records.assert_not_awaited()
        folder_connector.notify.assert_awaited_once()

    async def test_missing_root_raises_and_notifies_the_user(
        self, folder_connector: LocalFsConnector, tmp_path: Path
    ):
        # A moved folder used to exhaust retries and surface as DESKTOP_OFFLINE.
        self._prepare(
            folder_connector, tmp_path, {"last_sync_time": 1, "cursor": "c0"}
        )
        folder_connector.notify = AsyncMock()
        folder_connector._pull_with_retry = AsyncMock(
            side_effect=LocalFsRootUnavailableError(
                "ROOT_MISSING", "Local sync root folder does not exist"
            )
        )

        with pytest.raises(LocalFsRootUnavailableError):
            await folder_connector.run_sync()

        folder_connector.notify.assert_awaited_once()
        kwargs = folder_connector.notify.await_args.kwargs
        assert kwargs["payload"]["error_code"] == "ROOT_MISSING"
        assert "was moved, renamed or deleted" in kwargs["message"]
        assert "Indexed files are kept" in kwargs["message"]
        assert "update the folder path in connector" in kwargs["message"]
        folder_connector.record_sync_point.update_sync_point.assert_not_awaited()
        folder_connector._prune_unseen_records.assert_not_awaited()

    async def test_unknown_cursor_restarts_once_as_full(
        self, folder_connector: LocalFsConnector, tmp_path: Path
    ):
        # A dead cursor would otherwise be re-sent every run and the connector
        # would never sync again without a manual full sync.
        self._prepare(
            folder_connector, tmp_path, {"last_sync_time": 42, "cursor": "gone"}
        )
        folder_connector._pull_with_retry = AsyncMock(
            side_effect=[
                LocalFsDesktopRemoteError("CURSOR_UNKNOWN", "lost", retryable=False),
                self._page(hasMore=False),
            ]
        )

        await folder_connector.run_sync()

        modes = [
            call.kwargs["mode"]
            for call in folder_connector._pull_with_retry.await_args_list
        ]
        assert modes == ["INCREMENTAL", "FULL"]
        assert folder_connector._pull_with_retry.await_args_list[1].kwargs["cursor"] is None
        # The stale baseline must not survive the restart, or the next run
        # would go incremental again off a cursor that just failed.
        writes = folder_connector.record_sync_point.update_sync_point.await_args_list
        assert writes[0].args[1].get("last_sync_time") != 42
        folder_connector._prune_unseen_records.assert_awaited_once()

    async def test_unknown_cursor_restart_is_one_shot(
        self, folder_connector: LocalFsConnector, tmp_path: Path
    ):
        self._prepare(
            folder_connector, tmp_path, {"last_sync_time": 42, "cursor": "gone"}
        )
        folder_connector._pull_with_retry = AsyncMock(
            side_effect=LocalFsDesktopRemoteError(
                "CURSOR_UNKNOWN", "lost", retryable=False
            )
        )

        with pytest.raises(LocalFsDesktopRemoteError):
            await folder_connector.run_sync()

        assert folder_connector._pull_with_retry.await_count == 2
        folder_connector._prune_unseen_records.assert_not_awaited()

    async def test_pulls_are_routed_to_the_owner_device_from_the_app_document(
        self, folder_connector: LocalFsConnector, tmp_path: Path
    ):
        self._prepare(folder_connector, tmp_path, {"device_id": "stale-sync-point"})
        folder_connector._pull_with_retry = AsyncMock(
            side_effect=[
                self._page(batchIndex=0, cursor="c1", hasMore=True),
                self._page(batchIndex=1, cursor="c2", hasMore=False),
            ]
        )

        await folder_connector.run_sync()

        for call in folder_connector._pull_with_retry.await_args_list:
            assert call.kwargs["expected_device_id"] == OWNER_DEVICE_ID
        for write in folder_connector.record_sync_point.update_sync_point.await_args_list:
            assert "device_id" not in write.args[1]

    async def test_unclaimed_connector_aborts_before_pulling_and_notifies(
        self, folder_connector: LocalFsConnector, tmp_path: Path
    ):
        self._prepare(folder_connector, tmp_path, {"last_sync_time": 1})
        folder_connector.data_entities_processor.get_app_by_id = AsyncMock(
            return_value=_app_metadata(None)
        )
        folder_connector.notify = AsyncMock()
        folder_connector._pull_with_retry = AsyncMock(return_value=self._page())

        with pytest.raises(LocalFsDeviceUnclaimedError):
            await folder_connector.run_sync()

        folder_connector._pull_with_retry.assert_not_awaited()
        folder_connector.record_sync_point.update_sync_point.assert_not_awaited()
        folder_connector.notify.assert_awaited_once()
        payload = folder_connector.notify.await_args.kwargs["payload"]
        assert payload["error_code"] == "DESKTOP_UNCLAIMED"

    async def test_device_mismatch_raises_and_notifies_the_user(
        self, folder_connector: LocalFsConnector, tmp_path: Path
    ):
        # Nothing the connector can do resolves this, and a scheduled sync that
        # swallowed it left the folder silently stuck for ever.
        self._prepare(folder_connector, tmp_path, {"last_sync_time": 1})
        folder_connector.notify = AsyncMock()
        folder_connector._pull_with_retry = AsyncMock(
            side_effect=LocalFsDeviceMismatchError(OWNER_DEVICE_ID, "dev-new")
        )

        with pytest.raises(LocalFsDeviceMismatchError):
            await folder_connector.run_sync()

        folder_connector.notify.assert_awaited_once()
        kwargs = folder_connector.notify.await_args.kwargs
        assert OWNER_DEVICE_NAME in kwargs["message"]
        assert "full sync" not in kwargs["message"].lower()
        assert kwargs["payload"]["expected_device_id"] == OWNER_DEVICE_ID
        assert kwargs["payload"]["actual_device_id"] == "dev-new"
        folder_connector.record_sync_point.update_sync_point.assert_not_awaited()
        folder_connector._prune_unseen_records.assert_not_awaited()

    async def test_desktop_error_notifies_with_the_failure_detail(
        self, folder_connector: LocalFsConnector, tmp_path: Path
    ):
        self._prepare(folder_connector, tmp_path, {"last_sync_time": 1})
        folder_connector.notify = AsyncMock()
        folder_connector._pull_with_retry = AsyncMock(
            side_effect=LocalFsDesktopTimeoutError(
                "Desktop pull timed out after 90s (run=abc-123 batch=0)"
            )
        )

        with pytest.raises(LocalFsDesktopTimeoutError):
            await folder_connector.run_sync()

        folder_connector.notify.assert_awaited_once()
        kwargs = folder_connector.notify.await_args.kwargs
        assert "DESKTOP_TIMEOUT" in kwargs["message"]
        assert "timed out after 90s" in kwargs["message"]
        assert kwargs["payload"]["error_code"] == "DESKTOP_TIMEOUT"

    async def test_desktop_error_message_truncates_the_detail(
        self, folder_connector: LocalFsConnector, tmp_path: Path
    ):
        # A desktop-supplied message is untrusted length; the payload keeps it whole.
        self._prepare(folder_connector, tmp_path, {"last_sync_time": 1})
        folder_connector.notify = AsyncMock()
        folder_connector._pull_with_retry = AsyncMock(
            side_effect=LocalFsDesktopTimeoutError("x" * 500)
        )

        with pytest.raises(LocalFsDesktopTimeoutError):
            await folder_connector.run_sync()

        kwargs = folder_connector.notify.await_args.kwargs
        assert "x" * 200 in kwargs["message"]
        assert "x" * 201 not in kwargs["message"]
        assert kwargs["payload"]["error"] == "x" * 500

    async def test_desktop_remote_error_notifies_with_its_own_code(
        self, folder_connector: LocalFsConnector, tmp_path: Path
    ):
        self._prepare(folder_connector, tmp_path, {"last_sync_time": 1})
        folder_connector.notify = AsyncMock()
        folder_connector._pull_with_retry = AsyncMock(
            side_effect=LocalFsDesktopRemoteError(
                "TOO_MANY_BATCHES", "run exceeded 100000 batches", retryable=False
            )
        )

        with pytest.raises(LocalFsDesktopRemoteError):
            await folder_connector.run_sync()

        assert (
            folder_connector.notify.await_args.kwargs["payload"]["error_code"]
            == "TOO_MANY_BATCHES"
        )

    async def test_cancellation_propagates(
        self, folder_connector: LocalFsConnector, tmp_path: Path
    ):
        # The task manager cancels an in-flight sync when a new one starts;
        # swallowing it here would make the cancel look like a clean finish.
        self._prepare(folder_connector, tmp_path, {})
        folder_connector._pull_with_retry = AsyncMock(
            side_effect=asyncio.CancelledError()
        )

        with pytest.raises(asyncio.CancelledError):
            await folder_connector.run_sync()

    async def test_run_incremental_sync_delegates(
        self, folder_connector: LocalFsConnector, tmp_path: Path
    ):
        self._prepare(folder_connector, tmp_path, {"last_sync_time": 1})
        folder_connector._pull_with_retry = AsyncMock(return_value=self._page())

        await folder_connector.run_incremental_sync()

        folder_connector._pull_with_retry.assert_awaited_once()


@pytest.mark.asyncio
class TestPullWithRetry:
    async def test_retries_same_batch_index_on_timeout(
        self, folder_connector: LocalFsConnector
    ):
        # Re-sending the same (runId, batchIndex) is what makes retry safe:
        # the desktop answers from its idempotency cache without advancing.
        page = LocalFsPullBatch(
            connectorId="connector-instance-1",
            runId="r",
            batchIndex=4,
            hasMore=False,
        )
        folder_connector._request_file_event_batch = AsyncMock(
            side_effect=[LocalFsDesktopTimeoutError("slow"), page]
        )
        with patch("asyncio.sleep", new=AsyncMock()):
            out = await folder_connector._pull_with_retry(
                run_id="r", batch_index=4, cursor="c", mode="FULL", session=MagicMock(),
                expected_device_id=OWNER_DEVICE_ID,
            )
        assert out is page
        assert folder_connector._request_file_event_batch.await_count == 2
        for call in folder_connector._request_file_event_batch.await_args_list:
            assert call.kwargs["batch_index"] == 4
            assert call.kwargs["cursor"] == "c"

    async def test_non_retryable_error_is_not_retried(
        self, folder_connector: LocalFsConnector
    ):
        folder_connector._request_file_event_batch = AsyncMock(
            side_effect=LocalFsDesktopRemoteError(
                "CONFIG_MISMATCH", "x", retryable=False
            )
        )
        with pytest.raises(LocalFsDesktopRemoteError):
            await folder_connector._pull_with_retry(
                run_id="r", batch_index=0, cursor=None, mode="FULL", session=MagicMock(),
                expected_device_id=OWNER_DEVICE_ID,
            )
        folder_connector._request_file_event_batch.assert_awaited_once()

    async def test_missing_root_is_not_retried_or_mapped_to_offline(
        self, folder_connector: LocalFsConnector
    ):
        folder_connector._request_file_event_batch = AsyncMock(
            side_effect=LocalFsDesktopRemoteError(
                "ROOT_MISSING", "gone", retryable=True
            )
        )
        with pytest.raises(LocalFsRootUnavailableError) as ei:
            await folder_connector._pull_with_retry(
                run_id="r", batch_index=0, cursor=None, mode="INCREMENTAL", session=MagicMock(),
                expected_device_id=OWNER_DEVICE_ID,
            )
        assert ei.value.code == "ROOT_MISSING"
        folder_connector._request_file_event_batch.assert_awaited_once()

    async def test_exhausted_retries_keep_the_failing_code(
        self, folder_connector: LocalFsConnector
    ):
        # Not DESKTOP_OFFLINE: that is a silent skip, and a desktop that is
        # connected but keeps timing out is a fault the user can act on.
        folder_connector._request_file_event_batch = AsyncMock(
            side_effect=LocalFsDesktopTimeoutError("slow")
        )
        with patch("asyncio.sleep", new=AsyncMock()):
            with pytest.raises(LocalFsDesktopUnreachableError) as ei:
                await folder_connector._pull_with_retry(
                    run_id="r",
                    batch_index=0,
                    cursor=None,
                    mode="FULL",
                    session=MagicMock(),
                    expected_device_id=OWNER_DEVICE_ID,
                )
        assert ei.value.code == "DESKTOP_TIMEOUT"
        assert not isinstance(ei.value, LocalFsDesktopOfflineError)

    async def test_exhausted_retries_carry_a_remote_error_code(
        self, folder_connector: LocalFsConnector
    ):
        folder_connector._request_file_event_batch = AsyncMock(
            side_effect=LocalFsDesktopRemoteError("INTERNAL", "boom", retryable=True)
        )
        with patch("asyncio.sleep", new=AsyncMock()):
            with pytest.raises(LocalFsDesktopUnreachableError) as ei:
                await folder_connector._pull_with_retry(
                    run_id="r",
                    batch_index=0,
                    cursor=None,
                    mode="INCREMENTAL",
                    session=MagicMock(),
                    expected_device_id=OWNER_DEVICE_ID,
                )
        assert ei.value.code == "INTERNAL"


def _pull_session(status: int) -> MagicMock:
    response = MagicMock(status=status)
    session = MagicMock()
    session.post.return_value.__aenter__ = AsyncMock(return_value=response)
    session.post.return_value.__aexit__ = AsyncMock(return_value=False)
    return session


@pytest.mark.asyncio
class TestRequestFileEventBatchDeviceCheck:
    def _stub_transport(self, connector: LocalFsConnector, body: dict) -> None:
        connector._nodejs_base_url = AsyncMock(return_value="http://node")
        connector._desktop_token = AsyncMock(return_value="tok")
        connector._read_json_body = AsyncMock(return_value=body)

    async def test_failed_pull_from_another_device_is_a_mismatch_not_root_missing(
        self, folder_connector: LocalFsConnector
    ):
        self._stub_transport(
            folder_connector,
            {
                "code": "ROOT_MISSING",
                "error": {
                    "code": "ROOT_MISSING",
                    "message": "Local sync root folder does not exist",
                    "retryable": False,
                    "deviceId": "dev-mac",
                },
            },
        )
        with pytest.raises(LocalFsDeviceMismatchError) as ei:
            await folder_connector._request_file_event_batch(
                run_id="r",
                batch_index=0,
                cursor="c",
                mode="INCREMENTAL",
                session=_pull_session(HttpStatusCode.BAD_GATEWAY.value),
                expected_device_id="dev-windows",
            )
        assert ei.value.expected_device_id == "dev-windows"
        assert ei.value.actual_device_id == "dev-mac"

    async def test_failed_pull_from_the_owner_keeps_its_own_error(
        self, folder_connector: LocalFsConnector
    ):
        self._stub_transport(
            folder_connector,
            {
                "code": "ROOT_MISSING",
                "error": {
                    "code": "ROOT_MISSING",
                    "message": "Local sync root folder does not exist",
                    "retryable": False,
                    "deviceId": "dev-windows",
                },
            },
        )
        with pytest.raises(LocalFsRootUnavailableError):
            await folder_connector._request_file_event_batch(
                run_id="r",
                batch_index=0,
                cursor="c",
                mode="INCREMENTAL",
                session=_pull_session(HttpStatusCode.BAD_GATEWAY.value),
                expected_device_id="dev-windows",
            )

    async def test_request_names_the_device_node_routes_to(
        self, folder_connector: LocalFsConnector
    ):
        self._stub_transport(
            folder_connector,
            {
                "data": {
                    "connectorId": "connector-instance-1",
                    "runId": "r",
                    "batchIndex": 0,
                    "deviceId": "dev-windows",
                    "hasMore": False,
                }
            },
        )
        session = _pull_session(HttpStatusCode.SUCCESS.value)

        batch = await folder_connector._request_file_event_batch(
            run_id="r",
            batch_index=0,
            cursor=None,
            mode="FULL",
            session=session,
            expected_device_id="dev-windows",
        )

        assert batch.deviceId == "dev-windows"
        assert session.post.call_args.kwargs["json"]["deviceId"] == "dev-windows"

    async def test_page_from_another_device_is_a_mismatch(
        self, folder_connector: LocalFsConnector
    ):
        self._stub_transport(
            folder_connector,
            {
                "data": {
                    "connectorId": "connector-instance-1",
                    "runId": "r",
                    "batchIndex": 0,
                    "deviceId": "dev-mac",
                    "hasMore": False,
                }
            },
        )
        with pytest.raises(LocalFsDeviceMismatchError):
            await folder_connector._request_file_event_batch(
                run_id="r",
                batch_index=0,
                cursor=None,
                mode="FULL",
                session=_pull_session(HttpStatusCode.SUCCESS.value),
                expected_device_id="dev-windows",
            )


@pytest.mark.asyncio
class TestOwnerDevice:
    async def test_reads_owner_from_the_app_document_and_caches_it(
        self, folder_connector: LocalFsConnector
    ):
        get_app = folder_connector.data_entities_processor.get_app_by_id

        assert await folder_connector._owner_device() == (
            OWNER_DEVICE_ID,
            OWNER_DEVICE_NAME,
        )
        assert await folder_connector._owner_device() == (
            OWNER_DEVICE_ID,
            OWNER_DEVICE_NAME,
        )
        get_app.assert_awaited_once_with("connector-instance-1")

        await folder_connector.cleanup()
        await folder_connector._owner_device()
        assert get_app.await_count == 2

    async def test_unclaimed_is_not_cached(self, folder_connector: LocalFsConnector):
        # The first enable from the desktop must take effect on the next call.
        get_app = AsyncMock(
            side_effect=[_app_metadata(None), _app_metadata(OWNER_DEVICE_ID)]
        )
        folder_connector.data_entities_processor.get_app_by_id = get_app

        with pytest.raises(LocalFsDesktopRemoteError) as ei:
            await folder_connector._owner_device()
        assert ei.value.code == "DESKTOP_UNCLAIMED"
        assert ei.value.retryable is False
        assert (await folder_connector._owner_device())[0] == OWNER_DEVICE_ID

    async def test_missing_app_document_is_unclaimed(
        self, folder_connector: LocalFsConnector
    ):
        folder_connector.data_entities_processor.get_app_by_id = AsyncMock(
            return_value=None
        )
        with pytest.raises(LocalFsDesktopRemoteError) as ei:
            await folder_connector._owner_device()
        assert ei.value.code == "DESKTOP_UNCLAIMED"


@pytest.mark.asyncio
class TestFetchDesktopContent:
    @staticmethod
    def _record() -> FileRecord:
        return FileRecord(
            record_name="a.txt",
            record_type=RecordType.FILE,
            external_record_id="e1",
            version=0,
            origin=OriginTypes.CONNECTOR,
            connector_name=Connectors.LOCAL_FS,
            connector_id="connector-instance-1",
            is_file=True,
            path="a.txt",
            local_fs_relative_path="a.txt",
            mime_type="text/plain",
            record_group_type=RecordGroupType.DRIVE,
        )

    async def test_content_request_names_the_owner_device(
        self, folder_connector: LocalFsConnector, monkeypatch
    ):
        folder_connector._nodejs_base_url = AsyncMock(return_value="http://node")
        folder_connector._desktop_token = AsyncMock(return_value="tok")
        folder_connector._read_json_body = AsyncMock(return_value={})
        session = _FakeSession(
            [("post", _FakeResponse(HttpStatusCode.CONFLICT.value))]
        )
        _patch_session(monkeypatch, session)

        with pytest.raises(LocalFsDesktopOfflineError) as ei:
            await folder_connector._fetch_desktop_content(self._record())

        assert session.calls[0]["json"]["deviceId"] == OWNER_DEVICE_ID
        assert str(ei.value) == (
            f'The desktop "{OWNER_DEVICE_NAME}" that owns this file is not connected'
        )

    async def test_unclaimed_connector_content_is_terminal_404(
        self, folder_connector: LocalFsConnector
    ):
        folder_connector.data_entities_processor.get_app_by_id = AsyncMock(
            return_value=_app_metadata(None)
        )
        folder_connector._nodejs_base_url = AsyncMock(return_value="http://node")

        with pytest.raises(HTTPException) as ei:
            await folder_connector.stream_record(self._record())

        assert ei.value.status_code == HttpStatusCode.NOT_FOUND.value
        folder_connector._nodejs_base_url.assert_not_awaited()


class TestDesktopErrorFor:
    def test_maps_missing_root_even_when_desktop_marked_it_retryable(
        self, folder_connector: LocalFsConnector
    ):
        err = folder_connector._desktop_error_for(
            HttpStatusCode.BAD_GATEWAY.value,
            {
                "code": "ROOT_MISSING",
                "error": {
                    "code": "ROOT_MISSING",
                    "message": "Local sync root folder does not exist: /old",
                    "retryable": True,
                },
            },
            "run=r batch=0",
        )
        assert isinstance(err, LocalFsRootUnavailableError)
        assert err.code == "ROOT_MISSING"
        assert err.retryable is False

    def test_conflict_is_still_offline(self, folder_connector: LocalFsConnector):
        err = folder_connector._desktop_error_for(
            HttpStatusCode.CONFLICT.value, {}, "run=r batch=0"
        )
        assert isinstance(err, LocalFsDesktopOfflineError)
        assert str(err) == "The desktop that owns this folder is not connected"

    def test_conflict_content_names_the_owning_desktop(
        self, folder_connector: LocalFsConnector
    ):
        err = folder_connector._desktop_error_for(
            HttpStatusCode.CONFLICT.value,
            {},
            "content Resume/file.pdf",
            offline_message=folder_connector._owning_desktop_offline_message(
                "file", "owner-laptop"
            ),
        )
        assert isinstance(err, LocalFsDesktopOfflineError)
        assert str(err) == (
            'The desktop "owner-laptop" that owns this file is not connected'
        )

    def test_owning_desktop_offline_message_omits_blank_name(
        self, folder_connector: LocalFsConnector
    ):
        assert (
            folder_connector._owning_desktop_offline_message("file", None)
            == "The desktop that owns this file is not connected"
        )


# --------------------------------------------------------------------------- #
# Misc small helpers                                                          #
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
class TestMisc:
    async def test_bulk_get_records_dedupes_and_skips_empty(self, folder_connector):
        seen: list[str] = []

        async def _lookup(connector_id, external_record_id):
            seen.append(external_record_id)
            return (
                MagicMock(external_record_id=external_record_id)
                if external_record_id == "x"
                else None
            )

        folder_connector.data_entities_processor.get_record_by_external_id = AsyncMock(
            side_effect=_lookup
        )
        folder_connector.data_entities_processor.get_record_by_external_id = AsyncMock(
            side_effect=_lookup
        )

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
        out = await folder_connector._bulk_get_records_by_external_ids([])
        assert out == {}
        # The processor lookup must NOT have been reached.
        folder_connector.data_entities_processor.get_record_by_external_id.assert_not_called()

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
    """Cover the modified-date filter and its mtime-vs-event-time precedence."""

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
            LocalFsConnector._pass_date_filters(
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
            LocalFsConnector._pass_date_filters(
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
            LocalFsConnector._pass_date_filters(
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
            LocalFsConnector._pass_date_filters(
                ev, FilterCollection(filters=[flt])
            )
            is False
        )

    def test_created_filter_is_ignored(self):
        from app.connectors.core.registry.filters import SyncFilterKey

        # No created filter is registered any more, and a value left over in a
        # stored config must not silently exclude everything.
        ev = LocalFsFileEvent(
            type="CREATED", path="x", timestamp=1000, isDirectory=False,
        )
        flt = self._filter(SyncFilterKey.CREATED.value, 5000, 6000)
        assert (
            LocalFsConnector._pass_date_filters(
                ev, FilterCollection(filters=[flt])
            )
            is True
        )

    def test_modified_filter_uses_mtime_not_event_time(self):
        from app.connectors.core.registry.filters import SyncFilterKey

        # Live events stamp `timestamp` with wall-clock, so a full walk and an
        # incremental run only agree if the filter reads mtimeMs.
        ev = LocalFsFileEvent(
            type="MODIFIED", path="x", timestamp=9000, isDirectory=False,
            mtimeMs=3000,
        )
        flt = self._filter(SyncFilterKey.MODIFIED.value, 2000, 4000)
        assert (
            LocalFsConnector._pass_date_filters(
                ev, FilterCollection(filters=[flt])
            )
            is True
        )

    def test_modified_filter_excludes_when_mtime_out_of_range(self):
        from app.connectors.core.registry.filters import SyncFilterKey

        ev = LocalFsFileEvent(
            type="MODIFIED", path="x", timestamp=3000, isDirectory=False,
            mtimeMs=9000,
        )
        flt = self._filter(SyncFilterKey.MODIFIED.value, 2000, 4000)
        assert (
            LocalFsConnector._pass_date_filters(
                ev, FilterCollection(filters=[flt])
            )
            is False
        )

    def test_modified_filter_falls_back_when_mtime_unusable(self):
        from app.connectors.core.registry.filters import SyncFilterKey

        ev = LocalFsFileEvent(
            type="MODIFIED", path="x", timestamp=3000, isDirectory=False,
            mtimeMs=0,
        )
        flt = self._filter(SyncFilterKey.MODIFIED.value, 2000, 4000)
        assert (
            LocalFsConnector._pass_date_filters(
                ev, FilterCollection(filters=[flt])
            )
            is True
        )

    def test_date_filter_skips_directories(self):
        from app.connectors.core.registry.filters import SyncFilterKey

        ev = LocalFsFileEvent(
            type="MODIFIED", path="docs", timestamp=1000, isDirectory=True,
        )
        flt = self._filter(SyncFilterKey.MODIFIED.value, 2000, 4000)
        assert (
            LocalFsConnector._pass_date_filters(
                ev, FilterCollection(filters=[flt])
            )
            is True
        )


@pytest.mark.asyncio
async def test_create_connector_builds_instance():
    logger = MagicMock()
    data_store_provider = MagicMock()
    config_service = MagicMock()
    processor = MagicMock()
    processor.org_id = "org-1"

    conn = await LocalFsConnector.create_connector(
        logger,
        data_store_provider,
        config_service,
        "conn-id-x",
        "personal",
        "kushagra",
        data_entities_processor=processor,
    )
    assert isinstance(conn, LocalFsConnector)
    assert conn.connector_id == "conn-id-x"
    assert conn.data_entities_processor is processor


# --------------------------------------------------------------------------- #
# _apply_file_event_batch — directory rename branch                   #
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
class TestApplyFileEventBatchDirectoryRename:
    async def _setup(self, folder_connector, tmp_path: Path):
        folder_connector.config_service.get_config = AsyncMock(
            return_value={"sync": {SYNC_ROOT_PATH_KEY: str(tmp_path)}}
        )
        owner = User(email="u@x.com", id="u1", org_id="org-1")
        folder_connector._ensure_owner_and_record_group = AsyncMock(
            return_value=(owner, folder_connector._record_group_external_id())
        )
        folder_connector.data_entities_processor.on_new_records = AsyncMock()
        folder_connector.data_entities_processor.on_records_moved = AsyncMock()
        folder_connector._delete_external_ids = AsyncMock()
        return owner

    async def test_uploaded_directory_rename_uses_on_records_moved(
        self, folder_connector, tmp_path
    ):
        """DIR_RENAMED/DIR_MOVED update the folder's own record in place via
        on_records_moved instead of deleting the old row and creating a
        new one.
        """
        owner = await self._setup(folder_connector, tmp_path)
        folder_connector.config_service.get_config = AsyncMock(
            return_value={"sync": {"customValues": {SYNC_ROOT_PATH_KEY: str(tmp_path / "desktop-only")}}}
        )

        stats = await apply_batch(folder_connector, 
            [
                LocalFsFileEvent(
                    type="DIR_RENAMED",
                    path="docs",
                    oldPath="doc",
                    timestamp=1,
                    isDirectory=True,
                )
            ],
        )

        assert stats.processed == 0
        assert stats.deleted == 0
        folder_connector.data_entities_processor.on_records_moved.assert_awaited_once()
        moves = folder_connector.data_entities_processor.on_records_moved.await_args.args[0]
        assert len(moves) == 1
        old_ext_id, folder_record, perms = moves[0]
        assert old_ext_id == folder_connector._external_record_id_for_rel_path("doc")
        assert folder_record.local_fs_relative_path == "docs"
        assert folder_record.is_file is False
        assert folder_record.mime_type == MimeTypes.FOLDER.value
        assert perms[0].email == owner.email
        folder_connector.data_entities_processor.on_new_records.assert_not_awaited()
        folder_connector._delete_external_ids.assert_not_awaited()


# --------------------------------------------------------------------------- #
# _ensure_owner_and_record_group                                              #
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
class TestEnsureOwnerAndRecordGroup:
    async def test_raises_400_when_owner_cannot_be_resolved(
        self, folder_connector, tmp_path: Path
    ) -> None:
        folder_connector.data_entities_processor.get_user_by_user_id = AsyncMock(
            return_value=None
        )
        with pytest.raises(HTTPException) as ei:
            await folder_connector._ensure_owner_and_record_group(tmp_path)
        assert ei.value.status_code == HttpStatusCode.BAD_REQUEST.value
        assert "owner" in str(ei.value.detail).lower()

    async def test_record_group_name_uses_sync_root_folder_name(
        self, folder_connector, tmp_path: Path
    ) -> None:
        owner = User(email="owner@example.com", id="owner-1", org_id="org-1")
        folder_connector.data_entities_processor.get_user_by_user_id = AsyncMock(
            return_value=owner
        )
        folder_connector.data_entities_processor.on_new_app_users = AsyncMock()
        folder_connector.data_entities_processor.on_new_record_groups = AsyncMock()

        await folder_connector._ensure_owner_and_record_group(tmp_path)

        folder_connector.data_entities_processor.on_new_record_groups.assert_awaited_once()
        payload = folder_connector.data_entities_processor.on_new_record_groups.await_args.args[0]
        record_group = payload[0][0]
        assert record_group.name == tmp_path.name
        assert "Local FS" not in record_group.name

    async def test_record_group_name_uses_leaf_of_windows_path(
        self, folder_connector
    ) -> None:
        owner = User(email="owner@example.com", id="owner-1", org_id="org-1")
        folder_connector.data_entities_processor.get_user_by_user_id = AsyncMock(
            return_value=owner
        )
        folder_connector.data_entities_processor.on_new_app_users = AsyncMock()
        folder_connector.data_entities_processor.on_new_record_groups = AsyncMock()

        await folder_connector._ensure_owner_and_record_group(
            Path(r"C:\Harshit\ITFilefiles")
        )

        payload = folder_connector.data_entities_processor.on_new_record_groups.await_args.args[0]
        assert payload[0][0].name == "ITFilefiles"


class TestClientPathDisplay:
    def test_leaf_of_windows_path(self):
        assert client_path_leaf_name(r"C:\Harshit\ITFilefiles") == "ITFilefiles"

    def test_leaf_of_posix_path(self):
        assert client_path_leaf_name("/Users/me/Documents") == "Documents"

    def test_leaf_of_mixed_separators(self):
        assert client_path_leaf_name(r"C:\Harshit/ITFilefiles") == "ITFilefiles"

    def test_leaf_strips_trailing_slash(self):
        assert client_path_leaf_name("C:\\Harshit\\ITFilefiles\\") == "ITFilefiles"
        assert client_path_leaf_name("/Users/me/Documents/") == "Documents"

    def test_leaf_empty_falls_back(self):
        assert client_path_leaf_name("") == "Local FS"
        assert client_path_leaf_name("   ") == "Local FS"

    def test_display_path_name_matches_leaf_on_any_host(self):
        root = client_path_for_display(r"C:\Harshit\ITFilefiles")
        assert root.name == "ITFilefiles"


# --------------------------------------------------------------------------- #
# _apply_file_event_batch — SHA-256 mismatch skip-with-warning branch #
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
class TestApplyFileEventBatchValidation:
    """Branches inside ``_apply_file_event_batch`` (errors before upload)."""

    async def _base_setup(self, folder_connector, tmp_path: Path):
        folder_connector.config_service.get_config = AsyncMock(
            return_value={"sync": {SYNC_ROOT_PATH_KEY: str(tmp_path)}}
        )
        owner = User(email="u@x.com", id="u1", org_id="org-1")
        folder_connector._ensure_owner_and_record_group = AsyncMock(
            return_value=(owner, folder_connector._record_group_external_id())
        )
        folder_connector._bulk_get_records_by_external_ids = AsyncMock(return_value={})

    async def test_directory_event_upserts_folder_in_processing_loop(
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
        )
        stats = await apply_batch(folder_connector, 
            [ev]
        )

        assert stats.processed == 0
        folder_connector.data_entities_processor.on_new_records.assert_awaited_once()
        records = folder_connector.data_entities_processor.on_new_records.await_args.args[0]
        folder_record, permissions = records[0]
        assert folder_record.local_fs_relative_path == "dir_placeholder"
        assert folder_record.is_file is False
        assert folder_record.mime_type == MimeTypes.FOLDER.value
        assert permissions[0].email == "u@x.com"

    async def test_unsupported_event_type_is_skipped_not_fatal(
        self, folder_connector, tmp_path, monkeypatch
    ) -> None:
        # This runs inside a background sync task, so one unrecognised event
        # must not abandon the rest of the run.
        await self._base_setup(folder_connector, tmp_path)
        folder_connector.data_entities_processor.on_new_records = AsyncMock()
        _patch_session(monkeypatch, _FakeSession([]))

        ev = LocalFsFileEvent(
            type="UNKNOWN_OP",
            path="x.txt",
            timestamp=1,
            isDirectory=False,
        )
        stats = await apply_batch(folder_connector, [ev])
        assert stats.skipped == 1
        assert stats.processed == 0
        folder_connector.data_entities_processor.on_new_records.assert_not_awaited()

    async def test_unusable_path_is_skipped_not_fatal(
        self, folder_connector, tmp_path, monkeypatch
    ) -> None:
        await self._base_setup(folder_connector, tmp_path)
        folder_connector.data_entities_processor.on_new_records = AsyncMock()
        _patch_session(monkeypatch, _FakeSession([]))

        good = LocalFsFileEvent(
            type="CREATED",
            path="ok.txt",
            timestamp=1,
            isDirectory=False,
            sha256=hashlib.sha256(b"ok").hexdigest(),
        )
        bad = LocalFsFileEvent(
            type="CREATED", path="../escape.txt", timestamp=1, isDirectory=False
        )
        stats = await apply_batch(folder_connector, [bad, good])
        assert stats.skipped == 1
        assert stats.processed == 1


@pytest.mark.asyncio
class TestApplyRenameOldBlobGc:
    async def test_rename_does_not_gc_prior_storage_blob(
        self, folder_connector, tmp_path, monkeypatch
    ) -> None:
        """Known limitation: on_records_moved reuses the old vertex and
        overwrites its ``path`` with the plain relative path, so a legacy
        push-flow record's storage:// blob is no longer referenced but also
        no longer explicitly deleted on rename (it's orphaned for later GC,
        same best-effort posture used elsewhere for storage cleanup). This
        replaces the old delete+create behavior, which used to GC the old
        blob synchronously as part of retiring the old row.
        """
        folder_connector.config_service.get_config = AsyncMock(
            return_value={"sync": {SYNC_ROOT_PATH_KEY: str(tmp_path)}}
        )
        owner = User(email="u@x.com", id="u1", org_id="org-1")
        folder_connector._ensure_owner_and_record_group = AsyncMock(
            return_value=(owner, folder_connector._record_group_external_id())
        )
        old_ext = folder_connector._external_record_id_for_rel_path("old_name.txt")
        new_ext = folder_connector._external_record_id_for_rel_path("new_name.txt")
        assert old_ext != new_ext
        folder_connector.data_entities_processor.on_new_records = AsyncMock()
        folder_connector.data_entities_processor.on_records_moved = AsyncMock()
        folder_connector.data_entities_processor.on_record_deleted = AsyncMock()
        folder_connector._delete_storage_document = AsyncMock()
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
            sha256=_h.sha256(content).hexdigest(),
            mimeType="text/plain",
        )
        await apply_batch(folder_connector, 
            [ev]
        )

        folder_connector.data_entities_processor.on_records_moved.assert_awaited_once()
        moves = folder_connector.data_entities_processor.on_records_moved.await_args.args[0]
        assert moves[0][0] == old_ext
        folder_connector._delete_storage_document.assert_not_awaited()
        folder_connector.data_entities_processor.on_record_deleted.assert_not_awaited()


# --------------------------------------------------------------------------- #
# _do_upload — non-JSON 2xx body falls back to raw text                       #
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
class TestAppendFolderUpsertRecords:
    def test_empty_rel_path_is_noop(self, folder_connector, tmp_path):
        buf: list = []
        emitted: set[str] = set()
        ev = LocalFsFileEvent(
            type="DIR_CREATED", path="  /  ", timestamp=1, isDirectory=True,
        )
        folder_connector._append_folder_upsert_records(
            buf, "  /  ", tmp_path, "rg", ev, emitted
        )
        assert buf == []
        assert emitted == set()

    def test_already_emitted_folder_skips_rebuild(self, folder_connector, tmp_path):
        buf: list = []
        emitted = {"docs"}
        ev = LocalFsFileEvent(
            type="DIR_CREATED", path="docs", timestamp=1, isDirectory=True,
        )
        folder_connector._append_folder_upsert_records(
            buf, "docs", tmp_path, "rg", ev, emitted
        )
        # No parents and already emitted → buffer stays empty.
        assert buf == []
        assert emitted == {"docs"}


@pytest.mark.asyncio
class TestHandleDirectoryEventForBatch:
    async def test_dir_deleted_flushes_at_batch_size(self, folder_connector):
        delete_only: list[str] = []
        flush_delete = AsyncMock()
        ev = LocalFsFileEvent(
            type="DIR_DELETED", path="gone", timestamp=1, isDirectory=True,
        )
        await folder_connector._handle_directory_event_for_batch(
            event_type="DIR_DELETED",
            rel_path="gone",
            old_rel_path="",
            root=Path("/tmp"),
            external_record_group_id="rg",
            event=ev,
            owner=User(email="u@x.com", id="u1", org_id="org-1"),
            upsert_buffer=[],
            move_buffer=[],
            delete_only_buffer=delete_only,
            emitted_folder_paths=set(),
            flush_upserts=AsyncMock(),
            flush_moves=AsyncMock(),
            flush_delete_only=flush_delete,
            batch_size=1,
        )
        assert len(delete_only) == 1
        flush_delete.assert_awaited_once()

    async def test_dir_created_flushes_upserts_at_batch_size(
        self, folder_connector, tmp_path
    ):
        upsert_buffer: list = []
        flush_upserts = AsyncMock()
        ev = LocalFsFileEvent(
            type="DIR_CREATED", path="newdir", timestamp=1, isDirectory=True,
        )
        await folder_connector._handle_directory_event_for_batch(
            event_type="DIR_CREATED",
            rel_path="newdir",
            old_rel_path="",
            root=tmp_path,
            external_record_group_id="rg",
            event=ev,
            owner=User(email="u@x.com", id="u1", org_id="org-1"),
            upsert_buffer=upsert_buffer,
            move_buffer=[],
            delete_only_buffer=[],
            emitted_folder_paths=set(),
            flush_upserts=flush_upserts,
            flush_moves=AsyncMock(),
            flush_delete_only=AsyncMock(),
            batch_size=1,
        )
        assert upsert_buffer
        flush_upserts.assert_awaited_once()

    async def test_dir_created_own_folder_keeps_real_times_ancestors_dont(
        self, folder_connector, tmp_path
    ):
        upsert_buffer: list = []
        ev = LocalFsFileEvent(
            type="DIR_CREATED",
            path="a/b/newdir",
            timestamp=9_000,
            mtimeMs=5_000,
            birthtimeMs=1_000,
            isDirectory=True,
        )
        await folder_connector._handle_directory_event_for_batch(
            event_type="DIR_CREATED",
            rel_path="a/b/newdir",
            old_rel_path="",
            root=tmp_path,
            external_record_group_id="rg",
            event=ev,
            owner=User(email="u@x.com", id="u1", org_id="org-1"),
            upsert_buffer=upsert_buffer,
            move_buffer=[],
            delete_only_buffer=[],
            emitted_folder_paths=set(),
            flush_upserts=AsyncMock(),
            flush_moves=AsyncMock(),
            flush_delete_only=AsyncMock(),
            batch_size=100,
        )
        by_path = {r.local_fs_relative_path: r for r, _perms in upsert_buffer}
        assert by_path["a/b/newdir"].source_created_at == 1_000
        assert by_path["a"].source_created_at is None
        assert by_path["a/b"].source_created_at is None

    async def test_dir_renamed_queues_move_and_flushes(
        self, folder_connector, tmp_path
    ):
        upsert_buffer: list = []
        move_buffer: list = []
        flush_moves = AsyncMock()
        ev = LocalFsFileEvent(
            type="DIR_RENAMED", path="new", oldPath="old", timestamp=1, isDirectory=True,
        )
        await folder_connector._handle_directory_event_for_batch(
            event_type="DIR_RENAMED",
            rel_path="new",
            old_rel_path="old",
            root=tmp_path,
            external_record_group_id="rg",
            event=ev,
            owner=User(email="u@x.com", id="u1", org_id="org-1"),
            upsert_buffer=upsert_buffer,
            move_buffer=move_buffer,
            delete_only_buffer=[],
            emitted_folder_paths=set(),
            flush_upserts=AsyncMock(),
            flush_moves=flush_moves,
            flush_delete_only=AsyncMock(),
            batch_size=1,
        )
        assert len(move_buffer) == 1
        old_ext_id, folder_record, _perms = move_buffer[0]
        assert old_ext_id == folder_connector._external_record_id_for_rel_path("old")
        assert folder_record.local_fs_relative_path == "new"
        flush_moves.assert_awaited_once()

    async def test_unsupported_directory_event_is_reported_as_skipped(
        self, folder_connector, tmp_path
    ):
        upsert_buffer: list = []
        ev = LocalFsFileEvent(
            type="DIR_WAT", path="x", timestamp=1, isDirectory=True,
        )
        handled = await folder_connector._handle_directory_event_for_batch(
            event_type="DIR_WAT",
            rel_path="x",
            old_rel_path="",
            root=tmp_path,
            external_record_group_id="rg",
            event=ev,
            owner=User(email="u@x.com", id="u1", org_id="org-1"),
            upsert_buffer=upsert_buffer,
            move_buffer=[],
            delete_only_buffer=[],
            emitted_folder_paths=set(),
            flush_upserts=AsyncMock(),
            flush_moves=AsyncMock(),
            flush_delete_only=AsyncMock(),
            batch_size=10,
        )
        assert handled is False
        assert upsert_buffer == []


class TestApplyFileEventBatchOrdering:
    """A page must be applied in the order the desktop sent it.

    The buffers drain upserts -> moves -> deletes, so without a drain on every
    kind change a CREATED mints a record before the MOVED that re-keys the
    existing one onto the same external id, leaving two vertices behind.
    """

    async def _setup(self, folder_connector, tmp_path: Path) -> list[str]:
        folder_connector.config_service.get_config = AsyncMock(
            return_value={"sync": {SYNC_ROOT_PATH_KEY: str(tmp_path)}}
        )
        folder_connector._ensure_owner_and_record_group = AsyncMock(
            return_value=(
                User(email="u@x.com", id="u1", org_id="org-1"),
                folder_connector._record_group_external_id(),
            )
        )
        folder_connector._bulk_get_records_by_external_ids = AsyncMock(return_value={})

        calls: list[str] = []

        async def on_new(batch) -> None:
            calls.append("new:" + ",".join(r.local_fs_relative_path for r, _p in batch))

        async def on_moved(batch) -> None:
            calls.append("moved:" + ",".join(r.local_fs_relative_path for _o, r, _p in batch))

        async def on_deleted(external_ids, _user_id) -> None:
            calls.append(f"deleted:{len(external_ids)}")

        folder_connector.data_entities_processor.on_new_records = AsyncMock(side_effect=on_new)
        folder_connector.data_entities_processor.on_records_moved = AsyncMock(side_effect=on_moved)
        folder_connector._delete_external_ids = AsyncMock(side_effect=on_deleted)
        return calls

    @staticmethod
    def _event(event_type: str, path: str, old_path: str = "") -> LocalFsFileEvent:
        return LocalFsFileEvent(
            type=event_type,
            path=path,
            oldPath=old_path,
            timestamp=1000,
            size=4,
            isDirectory=False,
            sha256=hashlib.sha256(path.encode()).hexdigest(),
            mimeType="text/plain",
        )

    async def test_buffers_drain_on_every_kind_change(
        self, folder_connector, tmp_path: Path
    ) -> None:
        calls = await self._setup(folder_connector, tmp_path)

        await apply_batch(
            folder_connector,
            [
                self._event("CREATED", "a.txt"),
                self._event("MOVED", "c.txt", "b.txt"),
                self._event("CREATED", "d.txt"),
            ],
        )

        assert calls == ["new:a.txt", "moved:c.txt", "new:d.txt"]

    async def test_directory_move_page_applies_move_before_the_stray_create(
        self, folder_connector, tmp_path: Path
    ) -> None:
        """The reported bug: the watcher reports a move and the OS-level
        create/delete for the same file in one page. The move has to land first
        or the create mints a second vertex at the moved record's external id.
        """
        calls = await self._setup(folder_connector, tmp_path)

        await apply_batch(
            folder_connector,
            [
                self._event("MOVED", "new/photo.jpg", "old/photo.jpg"),
                self._event("CREATED", "new/photo.jpg"),
                self._event("DELETED", "old/photo.jpg"),
            ],
        )

        assert calls.index("moved:new/photo.jpg") < calls.index("new:new/photo.jpg")
        assert calls[-1] == "deleted:1"

    async def test_file_recreated_at_the_vacated_path_survives_the_move(
        self, folder_connector, tmp_path: Path
    ) -> None:
        """`mv a b` then a new file at `a`. Applying the create first would let
        the move re-key the *new* file's record and annihilate it.
        """
        calls = await self._setup(folder_connector, tmp_path)

        await apply_batch(
            folder_connector,
            [
                self._event("MOVED", "b.txt", "a.txt"),
                self._event("CREATED", "a.txt"),
            ],
        )

        assert calls == ["moved:b.txt", "new:a.txt"]

    async def test_moved_records_count_as_seen_for_the_full_run_prune(
        self, folder_connector, tmp_path: Path
    ) -> None:
        await self._setup(folder_connector, tmp_path)
        seen: set[str] = set()

        await apply_batch(
            folder_connector,
            [self._event("MOVED", "b.txt", "a.txt")],
            seen_external_ids=seen,
        )

        assert folder_connector._external_record_id_for_rel_path("b.txt") in seen
