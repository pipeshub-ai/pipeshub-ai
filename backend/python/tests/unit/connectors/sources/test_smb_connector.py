# ruff: noqa: ANN201, ANN202
"""SmbConnector tests with a fake data source and processor."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from app.config.constants.arangodb import Connectors, OriginTypes, ProgressStatus
from app.connectors.core.base.sync_point.sync_point import (
    generate_record_sync_point_key,
)
from app.connectors.core.registry.connector_builder import ConnectorScope
from app.connectors.core.registry.filters import FilterCollection
from app.connectors.sources.network_share.entry import DirectoryEntry, ShareInfo
from app.connectors.sources.network_share.errors import (
    NetworkShareAuthError,
    ShareListingError,
)
from app.connectors.sources.network_share.record_mapper import revision_id
from app.connectors.sources.smb.connector import SmbConnector
from app.models.entities import FileRecord, RecordGroupType, RecordType, User
from app.models.permission import EntityType, PermissionType
from tests.unit.connectors.sources.test_network_share_walker import (
    FakeNetworkShareDataSource,
)

NOW = datetime(2024, 6, 1, tzinfo=timezone.utc)
SHARE = "departments"


def _entry(
    name: str,
    *,
    is_directory: bool = False,
    size: int = 10,
    file_id: int | None = 11,
    last_write_time: datetime | None = NOW,
) -> DirectoryEntry:
    return DirectoryEntry(
        name=name,
        is_directory=is_directory,
        is_symlink=False,
        size=size,
        created_time=last_write_time,
        last_write_time=last_write_time,
        file_id=file_id,
    )


def _file_record(
    *,
    ext_id: str,
    revision: str,
    record_id: str = "rec-1",
    is_file: bool = True,
    indexing_status: str = ProgressStatus.COMPLETED.value,
) -> FileRecord:
    return FileRecord(
        id=record_id,
        record_name=ext_id.rsplit("/", 1)[-1],
        record_type=RecordType.FILE,
        record_group_type=RecordGroupType.FILE_SHARE.value,
        external_record_group_id=SHARE,
        external_record_id=ext_id,
        external_revision_id=revision,
        version=1,
        origin=OriginTypes.CONNECTOR.value,
        connector_name=Connectors.SMB,
        connector_id="smb-1",
        indexing_status=indexing_status,
        is_file=is_file,
    )


@pytest.fixture()
def mock_logger():
    return logging.getLogger("test.smb")


@pytest.fixture()
def mock_processor():
    proc = MagicMock()
    proc.org_id = "org-1"
    proc.on_new_app_users = AsyncMock()
    proc.on_new_record_groups = AsyncMock()
    proc.on_new_records = AsyncMock()
    proc.on_records_moved = AsyncMock()
    proc.on_record_deleted = AsyncMock()
    proc.reindex_existing_records = AsyncMock()
    proc.get_all_active_users = AsyncMock(return_value=[])
    proc.get_record_by_external_id = AsyncMock(return_value=None)
    proc.get_record_by_external_revision_id = AsyncMock(return_value=None)
    proc.get_records_by_record_type = AsyncMock(return_value=[])
    proc.ensure_team_app_edge = AsyncMock()
    proc.get_user_by_user_id = AsyncMock(
        return_value=User(
            email="user@test.com",
            source_user_id="src-1",
            org_id="org-1",
            full_name="Test User",
            title="Title",
        )
    )
    return proc


@pytest.fixture()
def mock_data_store_provider():
    provider = MagicMock()
    mock_tx = MagicMock()
    mock_tx.get_record_by_external_id = AsyncMock(return_value=None)
    mock_tx.get_user_by_user_id = AsyncMock(return_value={"email": "user@test.com"})
    mock_tx.__aenter__ = AsyncMock(return_value=mock_tx)
    mock_tx.__aexit__ = AsyncMock(return_value=None)
    provider.transaction.return_value = mock_tx
    return provider


@pytest.fixture()
def mock_config_service():
    svc = AsyncMock()
    svc.get_config = AsyncMock(
        return_value={
            "auth": {
                "server": "fileserver.example.com",
                "username": "alice",
                "password": "secret",
                "share": SHARE,
            }
        }
    )
    return svc


def _connector(mock_logger, mock_processor, mock_data_store_provider, mock_config_service, scope=ConnectorScope.PERSONAL.value):
    connector = SmbConnector(
        logger=mock_logger,
        data_entities_processor=mock_processor,
        data_store_provider=mock_data_store_provider,
        config_service=mock_config_service,
        connector_id="smb-1",
        scope=scope,
        created_by="user-1",
    )
    connector.notify = AsyncMock()
    connector.record_sync_point = MagicMock()
    connector.record_sync_point.update_sync_point = AsyncMock()
    connector.record_sync_point.read_sync_point = AsyncMock(return_value=None)
    return connector


@pytest.fixture()
def smb_connector(mock_logger, mock_processor, mock_data_store_provider, mock_config_service):
    return _connector(mock_logger, mock_processor, mock_data_store_provider, mock_config_service)


def _ds(**kwargs) -> FakeNetworkShareDataSource:
    kwargs.setdefault("shares", [ShareInfo(name=SHARE, share_type="disk")])
    return FakeNetworkShareDataSource(**kwargs)


def _empty_filters():
    return (FilterCollection(), FilterCollection())


class TestSmbConnectorInit:
    async def test_init_missing_config_notifies(self, smb_connector):
        smb_connector.config_service.get_config = AsyncMock(return_value=None)
        assert await smb_connector.init() is False
        smb_connector.notify.assert_awaited()

    async def test_init_missing_password_notifies(self, smb_connector):
        smb_connector.config_service.get_config = AsyncMock(
            return_value={"auth": {"server": "h", "username": "u"}}
        )
        assert await smb_connector.init() is False
        smb_connector.notify.assert_awaited()

    @patch("app.connectors.sources.smb.connector.load_connector_filters", new_callable=AsyncMock)
    @patch("app.connectors.sources.smb.connector.SmbClient.build_from_services", new_callable=AsyncMock)
    async def test_init_connection_failure_notifies(self, mock_build, mock_filters, smb_connector):
        mock_build.side_effect = NetworkShareAuthError("LOGON_FAILURE")
        mock_filters.return_value = _empty_filters()
        assert await smb_connector.init() is False
        smb_connector.notify.assert_awaited()

    @patch("app.connectors.sources.smb.connector.load_connector_filters", new_callable=AsyncMock)
    @patch("app.connectors.sources.smb.connector.SmbClient.build_from_services", new_callable=AsyncMock)
    async def test_init_success(self, mock_build, mock_filters, smb_connector):
        mock_build.return_value = MagicMock()
        mock_filters.return_value = _empty_filters()
        assert await smb_connector.init() is True
        assert smb_connector.configured_share == SHARE
        assert smb_connector.creator_email == "user@test.com"


class TestSmbConnectorConnection:
    async def test_test_connection_success(self, smb_connector):
        ds = _ds(tree={(SHARE, ""): [_entry("a.txt")]})
        smb_connector.data_source = ds
        smb_connector.configured_share = SHARE
        assert await smb_connector.test_connection_and_access() is True

    async def test_test_connection_share_listing_failure_notifies(self, smb_connector):
        smb_connector.data_source = _ds(shares=ShareListingError("NetrShareEnum failed"))
        smb_connector.configured_share = None
        assert await smb_connector.test_connection_and_access() is False
        smb_connector.notify.assert_awaited()

    async def test_test_connection_auth_failure_notifies(self, smb_connector):
        ds = _ds(fail_dirs={(SHARE, "")})
        smb_connector.data_source = ds
        smb_connector.configured_share = SHARE
        assert await smb_connector.test_connection_and_access() is False
        smb_connector.notify.assert_awaited()

    async def test_handle_webhook_notification_not_implemented(self, smb_connector):
        with pytest.raises(NotImplementedError):
            smb_connector.handle_webhook_notification({})

    async def test_get_signed_url_is_none(self, smb_connector):
        assert await smb_connector.get_signed_url(_file_record(ext_id=f"{SHARE}/a.txt", revision="r")) is None

    async def test_cleanup_closes_data_source(self, smb_connector):
        ds = FakeNetworkShareDataSource()
        smb_connector.data_source = ds
        smb_connector._thread_pool_lease = None
        await smb_connector.cleanup()
        assert ds.closed is True
        assert smb_connector.data_source is None

    def test_cleanup_clears_smb_client_cache(self):
        from app.sources.client.smb.smb import SmbClient

        client = SmbClient(server="h", username="u", password="p")
        client._connection_cache["session"] = object()
        client._registered = True
        with patch.object(client, "_smbclient") as smbclient:
            smbclient.return_value.reset_connection_cache = MagicMock()
            client.close()
        assert client.connection_cache() == {}
        assert client._registered is False


class TestSmbConnectorSync:
    @patch("app.connectors.sources.smb.connector.load_connector_filters", new_callable=AsyncMock)
    async def test_run_sync_creates_groups_walks_and_prunes(self, mock_filters, smb_connector, mock_processor):
        mock_filters.return_value = _empty_filters()
        ds = FakeNetworkShareDataSource(
            tree={(SHARE, ""): [_entry("a.txt", file_id=5)]},
            shares=[ShareInfo(name=SHARE, share_type="disk")],
        )
        smb_connector.data_source = ds
        smb_connector.configured_share = SHARE
        stale = _file_record(ext_id=f"{SHARE}/gone.txt", revision="old")
        mock_processor.get_records_by_record_type = AsyncMock(return_value=[stale])
        await smb_connector.run_sync()
        mock_processor.on_new_record_groups.assert_awaited()
        mock_processor.on_new_records.assert_awaited()
        mock_processor.on_record_deleted.assert_awaited_with(stale.id)
        written = smb_connector.record_sync_point.update_sync_point.await_args.args[1]
        assert written["last_sync_time"] == int(NOW.timestamp() * 1000)

    @patch("app.connectors.sources.smb.connector.load_connector_filters", new_callable=AsyncMock)
    async def test_run_sync_skips_prune_when_listing_incomplete(self, mock_filters, smb_connector, mock_processor):
        mock_filters.return_value = _empty_filters()
        ds = _ds(fail_dirs={(SHARE, "")})
        smb_connector.data_source = ds
        smb_connector.configured_share = SHARE
        await smb_connector.run_sync()
        mock_processor.get_records_by_record_type.assert_not_awaited()
        mock_processor.on_record_deleted.assert_not_awaited()
        smb_connector.record_sync_point.update_sync_point.assert_not_awaited()

    @patch("app.connectors.sources.smb.connector.load_connector_filters", new_callable=AsyncMock)
    async def test_same_revision_reuses_existing_id(self, mock_filters, smb_connector, mock_processor):
        mock_filters.return_value = _empty_filters()
        item = _entry("a.txt", file_id=9, size=10)
        existing = _file_record(
            ext_id=f"{SHARE}/a.txt",
            revision=revision_id(SHARE, item, "a.txt"),
        )
        mock_processor.get_record_by_external_id = AsyncMock(return_value=existing)
        ds = _ds(tree={(SHARE, ""): [item]})
        smb_connector.data_source = ds
        smb_connector.configured_share = SHARE
        await smb_connector.run_sync()
        mock_processor.on_records_moved.assert_not_awaited()
        batch = mock_processor.on_new_records.await_args.args[0]
        record, _perms = batch[0]
        assert record.id == existing.id
        assert record.external_revision_id == existing.external_revision_id

    @patch("app.connectors.sources.smb.connector.load_connector_filters", new_callable=AsyncMock)
    async def test_changed_revision_is_upsert_not_move(self, mock_filters, smb_connector, mock_processor):
        mock_filters.return_value = _empty_filters()
        item = _entry("a.txt", file_id=9, size=99)
        existing = _file_record(ext_id=f"{SHARE}/a.txt", revision="stale-rev")
        mock_processor.get_record_by_external_id = AsyncMock(return_value=existing)
        ds = _ds(tree={(SHARE, ""): [item]})
        smb_connector.data_source = ds
        smb_connector.configured_share = SHARE
        await smb_connector.run_sync()
        mock_processor.on_records_moved.assert_not_awaited()
        batch = mock_processor.on_new_records.await_args.args[0]
        record, _perms = batch[0]
        assert record.external_record_id == f"{SHARE}/a.txt"
        assert record.external_revision_id != existing.external_revision_id

    @patch("app.connectors.sources.smb.connector.load_connector_filters", new_callable=AsyncMock)
    async def test_nonzero_file_id_at_new_path_calls_on_records_moved(self, mock_filters, smb_connector, mock_processor):
        mock_filters.return_value = _empty_filters()
        item = _entry("renamed.txt", file_id=44, size=10)
        rev = revision_id(SHARE, item, "renamed.txt")
        old = _file_record(ext_id=f"{SHARE}/old.txt", revision=rev, record_id="keep-me")
        mock_processor.get_record_by_external_revision_id = AsyncMock(return_value=old)
        ds = _ds(tree={(SHARE, ""): [item]})
        smb_connector.data_source = ds
        smb_connector.configured_share = SHARE
        await smb_connector.run_sync()
        mock_processor.on_records_moved.assert_awaited()
        moved = mock_processor.on_records_moved.await_args.args[0]
        old_id, record, _perms = moved[0]
        assert old_id == f"{SHARE}/old.txt"
        assert record.external_record_id == f"{SHARE}/renamed.txt"
        assert record.id == "keep-me"

    @patch("app.connectors.sources.smb.connector.load_connector_filters", new_callable=AsyncMock)
    async def test_personal_scope_owner_permission(self, mock_filters, smb_connector, mock_processor):
        mock_filters.return_value = _empty_filters()
        smb_connector.creator_email = "user@test.com"
        ds = _ds(tree={(SHARE, ""): [_entry("a.txt")]})
        smb_connector.data_source = ds
        smb_connector.configured_share = SHARE
        await smb_connector.run_sync()
        mock_processor.on_new_app_users.assert_awaited()
        mock_processor.ensure_team_app_edge.assert_not_awaited()
        batch = mock_processor.on_new_records.await_args.args[0]
        _record, perms = batch[0]
        assert perms[0].type == PermissionType.OWNER
        assert perms[0].entity_type == EntityType.USER
        assert perms[0].email == "user@test.com"

    @patch("app.connectors.sources.smb.connector.load_connector_filters", new_callable=AsyncMock)
    async def test_team_scope_ensure_team_app_edge(
        self, mock_filters, mock_logger, mock_processor, mock_data_store_provider, mock_config_service
    ):
        mock_filters.return_value = _empty_filters()
        connector = _connector(
            mock_logger, mock_processor, mock_data_store_provider, mock_config_service, scope=ConnectorScope.TEAM.value
        )
        ds = _ds(tree={(SHARE, ""): [_entry("a.txt")]})
        connector.data_source = ds
        connector.configured_share = SHARE
        await connector.run_sync()
        mock_processor.ensure_team_app_edge.assert_awaited_with("smb-1")
        mock_processor.on_new_app_users.assert_not_awaited()
        batch = mock_processor.on_new_records.await_args.args[0]
        _record, perms = batch[0]
        assert perms[0].type == PermissionType.READ
        assert perms[0].entity_type == EntityType.ORG
        assert perms[0].external_id == "org-1"

    @patch("app.connectors.sources.smb.connector.load_connector_filters", new_callable=AsyncMock)
    async def test_incremental_sync_lists_directories_older_than_the_checkpoint(
        self, mock_filters, smb_connector, mock_processor
    ):
        mock_filters.return_value = _empty_filters()
        checkpoint = int(datetime(2025, 1, 1, tzinfo=timezone.utc).timestamp() * 1000)
        smb_connector.record_sync_point.read_sync_point = AsyncMock(
            return_value={"last_sync_time": checkpoint}
        )
        old = datetime(2020, 1, 1, tzinfo=timezone.utc)
        newer = datetime(2024, 1, 1, tzinfo=timezone.utc)
        folder = _entry("docs", is_directory=True, file_id=2, last_write_time=old)
        created = _entry("new.txt", file_id=8, last_write_time=newer)
        renamed = _entry("renamed.txt", file_id=44, size=10, last_write_time=old)
        ds = FakeNetworkShareDataSource(
            tree={
                (SHARE, ""): [folder, renamed],
                (SHARE, "docs"): [created],
            },
            shares=[ShareInfo(name=SHARE, share_type="disk")],
        )
        smb_connector.data_source = ds
        smb_connector.configured_share = SHARE
        moved = _file_record(
            ext_id=f"{SHARE}/old.txt",
            revision=revision_id(SHARE, renamed, "renamed.txt"),
            record_id="keep-me",
        )
        stale = _file_record(ext_id=f"{SHARE}/gone.txt", revision="old", record_id="gone")
        mock_processor.get_record_by_external_revision_id = AsyncMock(
            side_effect=lambda _connector_id, rev: moved if rev == moved.external_revision_id else None
        )
        mock_processor.get_records_by_record_type = AsyncMock(return_value=[stale])
        await smb_connector.run_incremental_sync()
        assert (SHARE, "docs") in ds.list_calls
        upserted = [
            record.external_record_id
            for call in mock_processor.on_new_records.await_args_list
            for record, _perms in call.args[0]
        ]
        assert f"{SHARE}/docs/new.txt" in upserted
        old_id, record, _perms = mock_processor.on_records_moved.await_args.args[0][0]
        assert old_id == f"{SHARE}/old.txt"
        assert record.external_record_id == f"{SHARE}/renamed.txt"
        mock_processor.on_record_deleted.assert_awaited_with(stale.id)
        key, payload = smb_connector.record_sync_point.update_sync_point.await_args.args
        assert key == generate_record_sync_point_key(RecordType.FILE.value, "share", SHARE)
        assert payload["last_sync_time"] == checkpoint


class TestSmbConnectorStreamAndFilters:
    async def test_sharing_violation_raises_stream_error_not_raw_oserror(self, smb_connector):
        ds = FakeNetworkShareDataSource()
        ds.read_file = MagicMock(side_effect=OSError("STATUS_SHARING_VIOLATION"))
        smb_connector.data_source = ds
        record = _file_record(ext_id=f"{SHARE}/locked.docx", revision="r")
        with pytest.raises(HTTPException) as exc:
            await smb_connector.stream_record(record)
        assert not isinstance(exc.value, OSError)
        assert exc.value.status_code == 500

    async def test_directory_is_not_downloadable(self, smb_connector):
        smb_connector.data_source = FakeNetworkShareDataSource()
        record = _file_record(ext_id=f"{SHARE}/folder", revision="r", is_file=False)
        with pytest.raises(HTTPException) as exc:
            await smb_connector.stream_record(record)
        assert exc.value.status_code == 400

    async def test_get_filter_options_pages_and_unknown_key(self, smb_connector):
        ds = FakeNetworkShareDataSource(
            shares=[
                ShareInfo(name="alpha", share_type="disk"),
                ShareInfo(name="bravo", share_type="disk"),
                ShareInfo(name="IPC$", share_type="ipc"),
                ShareInfo(name="ADMIN$", share_type="disk"),
                ShareInfo(name="charlie", share_type="disk"),
            ]
        )
        smb_connector.data_source = ds
        page1 = await smb_connector.get_filter_options("shares", page=1, limit=2)
        assert [opt.id for opt in page1.options] == ["alpha", "bravo"]
        assert page1.has_more is True
        page2 = await smb_connector.get_filter_options("shares", page=2, limit=2)
        assert [opt.id for opt in page2.options] == ["charlie"]
        with pytest.raises(ValueError):
            await smb_connector.get_filter_options("nope")

    async def test_get_filter_options_enum_failure_returns_configured_share(self, smb_connector):
        ds = FakeNetworkShareDataSource(shares=ShareListingError("NetrShareEnum failed"))
        smb_connector.data_source = ds
        smb_connector.configured_share = SHARE
        result = await smb_connector.get_filter_options("shares")
        assert result.success is True
        assert [opt.id for opt in result.options] == [SHARE]

    async def test_reindex_changed_vs_unchanged(self, smb_connector, mock_processor):
        item = _entry("a.txt", file_id=3, size=10)
        unchanged_rev = revision_id(SHARE, item, "a.txt")
        unchanged = _file_record(ext_id=f"{SHARE}/a.txt", revision=unchanged_rev, record_id="same")
        changed = _file_record(ext_id=f"{SHARE}/b.txt", revision="old", record_id="upd")
        ds = FakeNetworkShareDataSource(
            stats={
                (SHARE, "a.txt"): item,
                (SHARE, "b.txt"): _entry("b.txt", file_id=4, size=50),
            }
        )
        smb_connector.data_source = ds
        smb_connector.indexing_filters = FilterCollection()
        await smb_connector.reindex_records([unchanged, changed])
        mock_processor.reindex_existing_records.assert_awaited()
        assert mock_processor.reindex_existing_records.await_args.args[0][0].id == "same"
        mock_processor.on_new_records.assert_awaited()
        updated = mock_processor.on_new_records.await_args.args[0][0][0]
        assert updated.id == "upd"
        assert updated.external_revision_id != "old"

    @patch("app.connectors.sources.smb.connector.NetworkShareEntitiesProcessor.initialize", new_callable=AsyncMock)
    async def test_create_connector(self, mock_init, mock_logger, mock_data_store_provider, mock_config_service, mock_processor):
        created = await SmbConnector.create_connector(
            mock_logger,
            mock_data_store_provider,
            mock_config_service,
            "smb-1",
            mock_processor,
            org_id="org-1",
            scope=ConnectorScope.TEAM.value,
            created_by="user-1",
        )
        assert isinstance(created, SmbConnector)
        assert created.scope == ConnectorScope.TEAM.value
        mock_init.assert_awaited()
