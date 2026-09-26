# ruff: noqa: ANN201, ANN202
"""CifsConnector and CifsClient tests. pysmb is mocked except for the dialect flag."""

from __future__ import annotations

import asyncio
import logging
import threading
import time
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
from app.connectors.sources.cifs.connector import CifsConnector
from app.connectors.sources.network_share.entry import DirectoryEntry, ShareInfo
from app.connectors.sources.network_share.errors import (
    DialectError,
    DirectoryListingError,
    NetworkShareAuthError,
    ShareListingError,
)
from app.connectors.sources.network_share.record_mapper import revision_id
from app.models.entities import FileRecord, RecordGroupType, RecordType, User
from app.models.permission import EntityType, PermissionType
from app.sources.client.cifs.cifs import CLIENT_NETBIOS_NAME, REPARSE_POINT, CifsClient
from app.sources.external.cifs.cifs import CifsDataSource
from tests.unit.connectors.sources.test_network_share_walker import (
    FakeNetworkShareDataSource,
)

NOW = datetime(2024, 6, 1, tzinfo=timezone.utc)
SHARE = "public"


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
        connector_name=Connectors.CIFS,
        connector_id="cifs-1",
        indexing_status=ProgressStatus.COMPLETED.value,
        is_file=is_file,
    )


@pytest.fixture()
def mock_logger():
    return logging.getLogger("test.cifs")


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
                "server": "192.168.1.10",
                "serverName": "FILESERVER",
                "username": "alice",
                "password": "secret",
                "share": SHARE,
                "port": 445,
            }
        }
    )
    return svc


def _connector(mock_logger, mock_processor, mock_data_store_provider, mock_config_service, scope=ConnectorScope.PERSONAL.value):
    connector = CifsConnector(
        logger=mock_logger,
        data_entities_processor=mock_processor,
        data_store_provider=mock_data_store_provider,
        config_service=mock_config_service,
        connector_id="cifs-1",
        scope=scope,
        created_by="user-1",
    )
    connector.notify = AsyncMock()
    connector.record_sync_point = MagicMock()
    connector.record_sync_point.update_sync_point = AsyncMock()
    connector.record_sync_point.read_sync_point = AsyncMock(return_value=None)
    return connector


@pytest.fixture()
def cifs_connector(mock_logger, mock_processor, mock_data_store_provider, mock_config_service):
    return _connector(mock_logger, mock_processor, mock_data_store_provider, mock_config_service)


def _ds(**kwargs) -> FakeNetworkShareDataSource:
    kwargs.setdefault("shares", [ShareInfo(name=SHARE, share_type="disk")])
    return FakeNetworkShareDataSource(**kwargs)


def _empty_filters():
    return (FilterCollection(), FilterCollection())


class TestCifsDialectGuard:
    def test_support_smb2_is_false_after_client_import(self):
        from smb import smb_structs

        assert smb_structs.SUPPORT_SMB2 is False

    def test_session_using_smb2_is_rejected(self):
        with patch("app.sources.client.cifs.cifs.SMBConnection") as cls:
            conn = MagicMock()
            conn.connect.return_value = True
            conn.isUsingSMB2 = True
            cls.return_value = conn
            client = CifsClient(
                server="192.168.1.10",
                username="u",
                password="p",
                remote_name="FILESERVER",
            )
            with pytest.raises(DialectError):
                client.connect()
            conn.close.assert_called()

    def test_smb1_rejection_is_a_dialect_error(self):
        from smb.base import NotConnectedError
        from smb.smb_structs import ProtocolError

        cases = [
            ProtocolError(
                "Server does not support any of the pysmb dialects. Please email pysmb to add in support for your OS"
            ),
            ProtocolError("Invalid 4-byte protocol field"),
            NotConnectedError("Server disconnected"),
            ConnectionResetError("Connection reset by peer"),
        ]
        for exc in cases:
            with patch("app.sources.client.cifs.cifs.SMBConnection") as cls:
                conn = MagicMock()
                conn.connect.side_effect = exc
                cls.return_value = conn
                client = CifsClient(
                    server="192.168.1.10",
                    username="u",
                    password="p",
                    remote_name="FILESERVER",
                )
                with pytest.raises(DialectError, match="SMB connector"):
                    client.connect()
                conn.close.assert_called()

    def test_session_setup_failure_stays_an_auth_error(self):
        from smb.smb_structs import ProtocolError

        with patch("app.sources.client.cifs.cifs.SMBConnection") as cls:
            conn = MagicMock()
            conn.connect.side_effect = ProtocolError(
                "Unknown status value (0xC000006D) in SMB_COM_SESSION_SETUP_ANDX (with extended security)"
            )
            cls.return_value = conn
            client = CifsClient(
                server="192.168.1.10",
                username="u",
                password="p",
                remote_name="FILESERVER",
            )
            with pytest.raises(NetworkShareAuthError):
                client.connect()

    def test_unreachable_host_stays_an_auth_error(self):
        with patch("app.sources.client.cifs.cifs.SMBConnection") as cls:
            conn = MagicMock()
            conn.connect.side_effect = ConnectionRefusedError("refused")
            cls.return_value = conn
            client = CifsClient(
                server="192.168.1.10",
                username="u",
                password="p",
                remote_name="FILESERVER",
            )
            with pytest.raises(NetworkShareAuthError):
                client.connect()

    def test_netbios_name_and_ports_and_ntlm(self):
        with patch("app.sources.client.cifs.cifs.SMBConnection") as cls:
            conn = MagicMock()
            conn.connect.return_value = True
            conn.isUsingSMB2 = False
            cls.return_value = conn
            CifsClient(
                server="192.168.1.10",
                username="u",
                password="p",
                remote_name="FILESERVER",
                port=445,
                use_ntlm_v2=True,
            ).connect()
            args, kwargs = cls.call_args
            assert args[2] == CLIENT_NETBIOS_NAME
            assert args[3] == "FILESERVER"
            assert kwargs["is_direct_tcp"] is True
            assert kwargs["use_ntlm_v2"] is True

        with patch("app.sources.client.cifs.cifs.SMBConnection") as cls:
            conn = MagicMock()
            conn.connect.return_value = True
            conn.isUsingSMB2 = False
            cls.return_value = conn
            client = CifsClient(
                server="192.168.1.10",
                username="u",
                password="p",
                remote_name="FILESERVER",
                port=139,
                use_ntlm_v2=False,
            )
            client.connect()
            assert client.is_direct_tcp is False
            assert cls.call_args.kwargs["is_direct_tcp"] is False
            assert cls.call_args.kwargs["use_ntlm_v2"] is False

    async def test_build_from_services_uses_ntlm_v1_and_remote_name(self, mock_logger):
        config = AsyncMock()
        config.get_config = AsyncMock(
            return_value={
                "auth": {
                    "server": "fileserver.corp.local",
                    "serverName": "FILESERVER",
                    "username": "u",
                    "password": "p",
                    "port": 445,
                    "ntlmVersion": "v1",
                    "share": SHARE,
                }
            }
        )
        with patch("app.sources.client.cifs.cifs.SMBConnection") as cls:
            conn = MagicMock()
            conn.connect.return_value = True
            conn.isUsingSMB2 = False
            cls.return_value = conn
            client = await CifsClient.build_from_services(mock_logger, config, "cifs-1")
        assert client.remote_name == "FILESERVER"
        assert client.use_ntlm_v2 is False
        assert client.is_direct_tcp is True

    def test_reconnect_then_second_failure(self):
        with patch("app.sources.client.cifs.cifs.SMBConnection") as cls:
            conn = MagicMock()
            conn.connect.return_value = True
            conn.isUsingSMB2 = False
            conn.listPath.side_effect = [
                OSError("connection reset"),
                OSError("connection reset"),
            ]
            cls.return_value = conn
            client = CifsClient(
                server="h", username="u", password="p", remote_name="H"
            )
            with pytest.raises(DirectoryListingError):
                client.list_directory(SHARE, "")
            assert conn.connect.call_count >= 2

    def test_reconnect_succeeds_on_second_attempt(self):
        with patch("app.sources.client.cifs.cifs.SMBConnection") as cls:
            conn = MagicMock()
            conn.connect.return_value = True
            conn.isUsingSMB2 = False
            conn.listPath.side_effect = [OSError("broken pipe"), []]
            cls.return_value = conn
            client = CifsClient(
                server="h", username="u", password="p", remote_name="H"
            )
            assert client.list_directory(SHARE, "") == []

    async def test_connection_lock_serializes_list_and_read(self):
        class SlowClient:
            def __init__(self) -> None:
                self.in_flight = 0
                self.max_in_flight = 0
                self._lock = threading.Lock()

            def list_directory(self, share: str, path: str):
                with self._lock:
                    self.in_flight += 1
                    self.max_in_flight = max(self.max_in_flight, self.in_flight)
                time.sleep(0.05)
                with self._lock:
                    self.in_flight -= 1
                return []

            def read_chunk(self, share: str, path: str, offset: int, chunk_size: int):
                with self._lock:
                    self.in_flight += 1
                    self.max_in_flight = max(self.max_in_flight, self.in_flight)
                time.sleep(0.05)
                with self._lock:
                    self.in_flight -= 1
                return b"" if offset else b"x"

            def close(self) -> None:
                return None

        client = SlowClient()
        ds = CifsDataSource(client)  # type: ignore[arg-type]
        await asyncio.gather(ds.list_directory(SHARE, ""), ds.read_file(SHARE, "a.txt").__anext__())
        assert client.max_in_flight == 1

    async def test_read_file_acquires_the_limiter_once(self):
        class Client:
            def read_chunk(self, share: str, path: str, offset: int, chunk_size: int) -> bytes:
                assert chunk_size == 1024 * 1024
                return b"" if offset else b"abc"

        class Limiter:
            def __init__(self) -> None:
                self.acquires = 0

            async def acquire(self, amount: float = 1) -> None:
                self.acquires += 1

            async def __aenter__(self) -> None:
                self.acquires += 1

            async def __aexit__(self, *args: object) -> None:
                return None

        limiter = Limiter()
        ds = CifsDataSource(Client(), rate_limiter=limiter)  # type: ignore[arg-type]
        chunks = [chunk async for chunk in ds.read_file(SHARE, "a.txt")]
        assert chunks == [b"abc"]
        assert limiter.acquires == 1


class TestCifsConnectorInit:
    async def test_init_missing_config_notifies(self, cifs_connector):
        cifs_connector.config_service.get_config = AsyncMock(return_value=None)
        assert await cifs_connector.init() is False
        cifs_connector.notify.assert_awaited()

    @patch("app.connectors.sources.cifs.connector.load_connector_filters", new_callable=AsyncMock)
    @patch("app.connectors.sources.cifs.connector.CifsClient.build_from_services", new_callable=AsyncMock)
    async def test_init_rejects_smb2_dialect(self, mock_build, mock_filters, cifs_connector):
        mock_build.side_effect = DialectError("Server negotiated SMB 2")
        mock_filters.return_value = _empty_filters()
        assert await cifs_connector.init() is False
        cifs_connector.notify.assert_awaited()

    @patch("app.connectors.sources.cifs.connector.load_connector_filters", new_callable=AsyncMock)
    @patch("app.connectors.sources.cifs.connector.CifsClient.build_from_services", new_callable=AsyncMock)
    async def test_init_auth_failure(self, mock_build, mock_filters, cifs_connector):
        mock_build.side_effect = NetworkShareAuthError("ACCESS_DENIED")
        mock_filters.return_value = _empty_filters()
        assert await cifs_connector.init() is False

    async def test_test_connection_share_listing_failure_notifies(self, cifs_connector):
        cifs_connector.data_source = FakeNetworkShareDataSource(shares=ShareListingError("listShares failed"))
        cifs_connector.configured_share = None
        assert await cifs_connector.test_connection_and_access() is False
        cifs_connector.notify.assert_awaited()

    async def test_test_connection_rejects_smb2(self, cifs_connector):
        ds = FakeNetworkShareDataSource()

        async def boom(share: str, path: str):
            raise DialectError("SMB2")

        ds.list_directory = boom  # type: ignore[method-assign]
        cifs_connector.data_source = ds
        cifs_connector.configured_share = SHARE
        assert await cifs_connector.test_connection_and_access() is False
        cifs_connector.notify.assert_awaited()

    async def test_handle_webhook_notification(self, cifs_connector):
        with pytest.raises(NotImplementedError):
            cifs_connector.handle_webhook_notification({})

    async def test_cleanup(self, cifs_connector):
        ds = FakeNetworkShareDataSource()
        cifs_connector.data_source = ds
        await cifs_connector.cleanup()
        assert ds.closed is True
        assert cifs_connector.data_source is None


class TestCifsConnectorSync:
    @patch("app.connectors.sources.cifs.connector.load_connector_filters", new_callable=AsyncMock)
    async def test_run_sync_walks_and_prunes(self, mock_filters, cifs_connector, mock_processor):
        mock_filters.return_value = _empty_filters()
        ds = _ds(tree={(SHARE, ""): [_entry("a.txt", file_id=5)]})
        cifs_connector.data_source = ds
        cifs_connector.configured_share = SHARE
        stale = _file_record(ext_id=f"{SHARE}/gone.txt", revision="old")
        mock_processor.get_records_by_record_type = AsyncMock(return_value=[stale])
        await cifs_connector.run_sync()
        mock_processor.on_new_record_groups.assert_awaited()
        mock_processor.on_record_deleted.assert_awaited_with(stale.id)
        written = cifs_connector.record_sync_point.update_sync_point.await_args.args[1]
        assert written["last_sync_time"] == int(NOW.timestamp() * 1000)

    @patch("app.connectors.sources.cifs.connector.load_connector_filters", new_callable=AsyncMock)
    async def test_incomplete_listing_skips_prune(self, mock_filters, cifs_connector, mock_processor):
        mock_filters.return_value = _empty_filters()
        ds = _ds(fail_dirs={(SHARE, "")})
        cifs_connector.data_source = ds
        cifs_connector.configured_share = SHARE
        await cifs_connector.run_sync()
        mock_processor.get_records_by_record_type.assert_not_awaited()
        cifs_connector.record_sync_point.update_sync_point.assert_not_awaited()

    @patch("app.connectors.sources.cifs.connector.load_connector_filters", new_callable=AsyncMock)
    async def test_same_revision_reuses_id(self, mock_filters, cifs_connector, mock_processor):
        mock_filters.return_value = _empty_filters()
        item = _entry("a.txt", file_id=9, size=10)
        existing = _file_record(ext_id=f"{SHARE}/a.txt", revision=revision_id(SHARE, item, "a.txt"))
        mock_processor.get_record_by_external_id = AsyncMock(return_value=existing)
        cifs_connector.data_source = _ds(tree={(SHARE, ""): [item]})
        cifs_connector.configured_share = SHARE
        await cifs_connector.run_sync()
        mock_processor.on_records_moved.assert_not_awaited()
        record, _ = mock_processor.on_new_records.await_args.args[0][0]
        assert record.id == existing.id
        assert record.external_revision_id == existing.external_revision_id

    @patch("app.connectors.sources.cifs.connector.load_connector_filters", new_callable=AsyncMock)
    async def test_changed_revision_is_upsert(self, mock_filters, cifs_connector, mock_processor):
        mock_filters.return_value = _empty_filters()
        item = _entry("a.txt", file_id=9, size=80)
        existing = _file_record(ext_id=f"{SHARE}/a.txt", revision="old")
        mock_processor.get_record_by_external_id = AsyncMock(return_value=existing)
        cifs_connector.data_source = _ds(tree={(SHARE, ""): [item]})
        cifs_connector.configured_share = SHARE
        await cifs_connector.run_sync()
        mock_processor.on_records_moved.assert_not_awaited()
        record, _ = mock_processor.on_new_records.await_args.args[0][0]
        assert record.external_revision_id != "old"

    @patch("app.connectors.sources.cifs.connector.load_connector_filters", new_callable=AsyncMock)
    async def test_move_by_file_id(self, mock_filters, cifs_connector, mock_processor):
        mock_filters.return_value = _empty_filters()
        item = _entry("renamed.txt", file_id=44, size=10)
        old = _file_record(
            ext_id=f"{SHARE}/old.txt",
            revision=revision_id(SHARE, item, "renamed.txt"),
            record_id="keep-me",
        )
        mock_processor.get_record_by_external_revision_id = AsyncMock(return_value=old)
        cifs_connector.data_source = _ds(tree={(SHARE, ""): [item]})
        cifs_connector.configured_share = SHARE
        await cifs_connector.run_sync()
        old_id, record, _ = mock_processor.on_records_moved.await_args.args[0][0]
        assert old_id == f"{SHARE}/old.txt"
        assert record.id == "keep-me"

    @patch("app.connectors.sources.cifs.connector.load_connector_filters", new_callable=AsyncMock)
    async def test_personal_owner_and_team_edge(
        self, mock_filters, cifs_connector, mock_processor, mock_logger, mock_data_store_provider, mock_config_service
    ):
        mock_filters.return_value = _empty_filters()
        cifs_connector.creator_email = "user@test.com"
        cifs_connector.data_source = _ds(tree={(SHARE, ""): [_entry("a.txt")]})
        cifs_connector.configured_share = SHARE
        await cifs_connector.run_sync()
        mock_processor.on_new_app_users.assert_awaited()
        mock_processor.ensure_team_app_edge.assert_not_awaited()
        _record, perms = mock_processor.on_new_records.await_args.args[0][0]
        assert perms[0].type == PermissionType.OWNER
        assert perms[0].entity_type == EntityType.USER
        assert perms[0].email == "user@test.com"

        mock_processor.on_new_app_users.reset_mock()
        team = _connector(
            mock_logger, mock_processor, mock_data_store_provider, mock_config_service, scope=ConnectorScope.TEAM.value
        )
        team.data_source = _ds(tree={(SHARE, ""): [_entry("a.txt")]})
        team.configured_share = SHARE
        await team.run_sync()
        mock_processor.ensure_team_app_edge.assert_awaited_with("cifs-1")
        mock_processor.on_new_app_users.assert_not_awaited()
        _record, perms = mock_processor.on_new_records.await_args.args[0][0]
        assert perms[0].type == PermissionType.READ
        assert perms[0].entity_type == EntityType.ORG
        assert perms[0].external_id == "org-1"

    @patch("app.connectors.sources.cifs.connector.load_connector_filters", new_callable=AsyncMock)
    async def test_incremental_sync_lists_directories_older_than_the_checkpoint(
        self, mock_filters, cifs_connector, mock_processor
    ):
        mock_filters.return_value = _empty_filters()
        checkpoint = int(datetime(2025, 1, 1, tzinfo=timezone.utc).timestamp() * 1000)
        cifs_connector.record_sync_point.read_sync_point = AsyncMock(
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
        cifs_connector.data_source = ds
        cifs_connector.configured_share = SHARE
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
        await cifs_connector.run_incremental_sync()
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
        key, payload = cifs_connector.record_sync_point.update_sync_point.await_args.args
        assert key == generate_record_sync_point_key(RecordType.FILE.value, "share", SHARE)
        assert payload["last_sync_time"] == checkpoint


class TestCifsConnectorStreamAndFilters:
    async def test_sharing_violation_maps_to_stream_error(self, cifs_connector):
        ds = FakeNetworkShareDataSource()
        ds.read_file = MagicMock(side_effect=OSError("STATUS_SHARING_VIOLATION"))
        cifs_connector.data_source = ds
        with pytest.raises(HTTPException) as exc:
            await cifs_connector.stream_record(_file_record(ext_id=f"{SHARE}/a.doc", revision="r"))
        assert exc.value.status_code == 500

    async def test_list_shares_drops_ipc_and_admin(self, cifs_connector):
        cifs_connector.data_source = FakeNetworkShareDataSource(
            shares=[
                ShareInfo(name="public", share_type="disk"),
                ShareInfo(name="IPC$", share_type="ipc"),
                ShareInfo(name="ADMIN$", share_type="disk"),
            ]
        )
        result = await cifs_connector.get_filter_options("shares")
        assert [opt.id for opt in result.options] == ["public"]

    def test_reparse_attribute_is_not_a_symlink(self):
        client = CifsClient(server="h", username="u", password="p", remote_name="HOST")

        class _File:
            filename = "deduped.bin"
            file_attributes = REPARSE_POINT
            file_size = 8
            file_id = 3
            create_time = None
            last_write_time = None

            def isDirectory(self) -> bool:
                return False

        class _Junction:
            filename = "junction"
            file_attributes = REPARSE_POINT | 0x10
            file_size = 0
            file_id = 4
            create_time = None
            last_write_time = None

            def isDirectory(self) -> bool:
                return True

        deduped = client._from_shared_file(_File())
        junction = client._from_shared_file(_Junction())
        assert deduped.is_symlink is False
        assert deduped.is_reparse is True
        assert deduped.is_directory is False
        assert junction.is_symlink is False
        assert junction.is_reparse is True
        assert junction.is_directory is True

    async def test_filter_options_page_and_enum_fallback(self, cifs_connector):
        cifs_connector.data_source = FakeNetworkShareDataSource(
            shares=[
                ShareInfo(name="a", share_type="disk"),
                ShareInfo(name="b", share_type="disk"),
                ShareInfo(name="c", share_type="disk"),
            ]
        )
        page = await cifs_connector.get_filter_options("shares", page=2, limit=1)
        assert [opt.id for opt in page.options] == ["b"]
        cifs_connector.data_source = FakeNetworkShareDataSource(shares=ShareListingError("down"))
        cifs_connector.configured_share = SHARE
        fallback = await cifs_connector.get_filter_options("shares")
        assert [opt.id for opt in fallback.options] == [SHARE]
        with pytest.raises(ValueError):
            await cifs_connector.get_filter_options("other")

    async def test_reindex_records(self, cifs_connector, mock_processor):
        item = _entry("a.txt", file_id=3, size=10)
        unchanged = _file_record(ext_id=f"{SHARE}/a.txt", revision=revision_id(SHARE, item, "a.txt"), record_id="same")
        changed = _file_record(ext_id=f"{SHARE}/b.txt", revision="old", record_id="upd")
        cifs_connector.data_source = FakeNetworkShareDataSource(
            stats={(SHARE, "a.txt"): item, (SHARE, "b.txt"): _entry("b.txt", file_id=4, size=50)}
        )
        cifs_connector.indexing_filters = FilterCollection()
        await cifs_connector.reindex_records([unchanged, changed])
        assert mock_processor.reindex_existing_records.await_args.args[0][0].id == "same"
        assert mock_processor.on_new_records.await_args.args[0][0][0].id == "upd"

    @patch("app.connectors.sources.cifs.connector.NetworkShareEntitiesProcessor.initialize", new_callable=AsyncMock)
    async def test_create_connector(self, mock_init, mock_logger, mock_data_store_provider, mock_config_service, mock_processor):
        created = await CifsConnector.create_connector(
            mock_logger,
            mock_data_store_provider,
            mock_config_service,
            "cifs-1",
            mock_processor,
            org_id="org-1",
            created_by="user-1",
        )
        assert isinstance(created, CifsConnector)
        mock_init.assert_awaited()
