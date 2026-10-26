"""Nextcloud sync, driven end to end over a fake Nextcloud server.

The connector, its Nextcloud client, the WebDAV/OCS request builder and the
HTTP client (with whatever transport it configures) are all real; the Nextcloud
server is an in-memory file tree answering at httpx's network hop, and our
databases are in-memory fakes.
"""

import asyncio
import base64
import logging
import socket
from collections.abc import Awaitable, Callable
from datetime import datetime, timezone
from typing import Any, Optional
from unittest.mock import AsyncMock, MagicMock
from urllib.parse import unquote

import httpx
import pytest
from aiolimiter import AsyncLimiter
from fastapi import HTTPException
from fastapi.responses import StreamingResponse
from nextcloud_behaviour_fakes import (
    ACTIVITY_PATH,
    CURRENT_USER_PATH,
    WEBDAV_PREFIX,
    FakeConfigService,
    FakeNextcloud,
    FakeRecordsDb,
    FakeStore,
)

from app.config.constants.arangodb import MimeTypes, ProgressStatus
from app.connectors.core.base.connector.connector_service import ConnectorInitError
from app.connectors.sources.microsoft.common.msgraph_client import RecordUpdate
from app.connectors.sources.nextcloud import connector as nextcloud_connector
from app.connectors.sources.nextcloud.connector import (
    MAX_HELD_ATTEMPTS,
    NextcloudConnector,
    NextcloudNoFilesystemAccessError,
    NextcloudSyncError,
)
from app.models.entities import AppUser, FileRecord
from app.services.notification.types import NotificationType
from app.sources.client.http.http_client import HTTPClient
from app.sources.client.http.http_resilient_transport import ResilientHTTPTransport
from app.sources.client.nextcloud.nextcloud import (
    NextcloudClient,
    NextcloudGuardedTransport,
    NextcloudRESTClientViaUsernamePassword,
)
from app.sources.client.resilience import ResiliencePolicy
from app.sources.external.nextcloud.nextcloud import NextcloudDataSource

CONNECTOR_ID = "nextcloud-1"
BASE = "https://cloud.example.com"
MADE_UP_DOMAIN = "@nextcloud.local"
# The PipesHub user who created the connector (FakeStore.users), the only one linked to the app.
CREATOR_EMAIL = "creator@pipeshub.test"
# No grant of its own: a record inherits up its folders to the drive, and the drive to the app,
# whose only user is the one who connected it.
INHERITED = ([], True)
# Each makes a fresh answer, so an outage can give one to every retry.
BAD_ANSWERS = [
    pytest.param(lambda: httpx.Response(503), id="server-error"),
    pytest.param(lambda: httpx.Response(207, content=b""), id="empty-body"),
    pytest.param(lambda: httpx.Response(207, content=b"<d:multistatus"), id="garbled-xml"),
    pytest.param(lambda: httpx.Response(207, content=b'<d:multistatus xmlns:d="DAV:"/>'), id="no-entries"),
    pytest.param(lambda: httpx.ConnectError("connection reset"), id="network-error"),
]


@pytest.fixture
def server(monkeypatch: pytest.MonkeyPatch) -> FakeNextcloud:
    fake = FakeNextcloud()
    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", fake.network())
    monkeypatch.setattr(socket, "getaddrinfo", fake.getaddrinfo)
    return fake


@pytest.fixture
def db() -> FakeRecordsDb:
    return FakeRecordsDb()


@pytest.fixture
def store(db: FakeRecordsDb) -> FakeStore:
    return FakeStore(db)


@pytest.fixture(autouse=True)
def no_real_pauses(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    """Pauses are kept but made instant: the connector's between batches, and the retry policy's backoff.

    The policy's backoff gate is recorded instead of armed, so a test can check how
    long a retry would have waited without waiting it out.
    """
    real_sleep = asyncio.sleep

    async def _sleep(delay: float, *args: object, **kwargs: object) -> None:
        await real_sleep(0)

    monkeypatch.setattr("app.connectors.sources.nextcloud.connector.asyncio.sleep", _sleep)
    pauses: list[float] = []
    monkeypatch.setattr(ResiliencePolicy, "pause", lambda _policy, seconds: pauses.append(seconds))
    return pauses


def auth_config(server: FakeNextcloud, **overrides: str) -> dict[str, Any]:
    auth = {"authType": "BASIC_AUTH", "baseUrl": f"{BASE}{server.base_path}/",
            "username": server.user, "password": server.app_password}
    auth.update(overrides)
    return {"auth": auth}


def build(server: FakeNextcloud, db: FakeRecordsDb, store: FakeStore,
          config: Optional[dict[str, Any]] = None, filters: Optional[dict[str, Any]] = None) -> NextcloudConnector:
    config = auth_config(server) if config is None else config
    if filters is not None:
        config = {**config, "filters": {"sync": {"values": filters}}}
    return NextcloudConnector(
        logging.getLogger("test.nextcloud"), db, store, FakeConfigService(CONNECTOR_ID, config),
        CONNECTOR_ID, "personal", "creator-user-1",
    )


async def make_connector(server: FakeNextcloud, db: FakeRecordsDb, store: FakeStore,
                         config: Optional[dict[str, Any]] = None, filters: Optional[dict[str, Any]] = None) -> NextcloudConnector:
    connector = build(server, db, store, config=config, filters=filters)
    assert await connector.init() is True
    return connector


async def sync(connector: NextcloudConnector) -> bool:
    """Run a sync the way the runner does; False when it raised NextcloudSyncError (reported as failed)."""
    try:
        await connector.run_sync()
    except NextcloudSyncError:
        return False
    return True


def fail_entries(connector: NextcloudConnector, file_ids: set[str]) -> set[str]:
    """Make processing the listed entries with these file ids raise, until the returned set is cleared."""
    process = connector._process_nextcloud_entry

    async def failing(entry: dict[str, Any], *args: Any, **kwargs: Any) -> Any:  # noqa: ANN401
        if entry.get("file_id") in file_ids:
            raise RuntimeError(f"cannot process {entry.get('file_id')}")
        return await process(entry, *args, **kwargs)

    connector._process_nextcloud_entry = failing
    return file_ids


def seed_drive(server: FakeNextcloud) -> None:
    server.add_file("Docs/Reports/q1.pdf", b"%PDF-1.7 quarterly", "application/pdf")
    server.add_file("Docs/notes.txt", b"meeting notes")
    server.add_file("Photos/cat.png", b"\x89PNG", "image/png")
    server.add_file("readme.txt", b"top level")


def access_of(db: FakeRecordsDb, name: str) -> tuple[list[tuple[str, str, str]], bool]:
    record = db.by_name(name)
    grants = [(p.entity_type, p.type, p.email) for p in db.permissions[record.external_record_id]]
    return grants, record.inherit_permissions


def ids_of(server: FakeNextcloud) -> dict[str, str]:
    return {node.name: node.file_id for node in server.nodes.values() if node.path}


def listen_for_notifications(connector: NextcloudConnector) -> Callable[[], Awaitable[list[dict[str, Any]]]]:
    """Capture what the connector publishes; the returned coroutine waits for the fire-and-forget sends."""
    publish = AsyncMock()
    connector._notification_service = MagicMock(publish_notification=publish)
    connector._notification_cache = {}  # the class-wide repeat filter would carry over between tests

    async def sent() -> list[dict[str, Any]]:
        await asyncio.gather(*connector._background_tasks)
        return [call.kwargs for call in publish.await_args_list]

    return sent


class _RecordList(logging.Handler):
    def __init__(self) -> None:
        super().__init__(logging.DEBUG)
        self.records: list[logging.LogRecord] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(record)


def capture_logs(connector: NextcloudConnector) -> list[logging.LogRecord]:
    """Give the connector a logger of its own and return what it logs.

    The logger is built directly, not through ``logging.getLogger``, so no other test's
    changes to shared loggers (propagate, disabled, level, or the root's handlers, which
    ``caplog`` relies on) can hide a record from this list.
    """
    handler = _RecordList()
    logger = logging.Logger(f"nextcloud-capture-{id(connector)}", logging.DEBUG)
    logger.addHandler(handler)
    connector.logger = logger
    return handler.records


async def synced(server: FakeNextcloud, db: FakeRecordsDb, store: FakeStore) -> NextcloudConnector:
    """A connector that has finished its first full sync and anchored its activity cursor."""
    seed_drive(server)
    connector = await make_connector(server, db, store)
    await sync(connector)
    assert store.cursor() == str(server.latest_activity_id)
    return connector


class TestRealClientStack:
    async def test_connector_talks_to_nextcloud_through_the_real_client_classes(self, server, db, store) -> None:
        connector = await make_connector(server, db, store)

        for cls in (NextcloudConnector, NextcloudDataSource, NextcloudClient,
                    NextcloudRESTClientViaUsernamePassword, HTTPClient, AsyncLimiter, RecordUpdate):
            assert isinstance(cls, type) and not isinstance(cls, MagicMock), f"{cls!r} must be a real class"
        assert type(connector.data_source) is NextcloudDataSource
        assert type(connector.data_source.client) is NextcloudRESTClientViaUsernamePassword
        assert type(connector.rate_limiter) is AsyncLimiter
        assert [r.url.path for r in server.requests] == [CURRENT_USER_PATH], "init reads the signed-in user over HTTP"

    async def test_the_production_client_retries_through_the_shared_policy(self, server, db, store) -> None:
        connector = await make_connector(server, db, store)

        http = connector.data_source.client
        assert http.resilience is connector.resilience
        assert (http.resilience.max_retries, http.resilience.rate_limit) == (3, 50)
        assert type(http.client._transport) is NextcloudGuardedTransport, "every request goes through the origin guard"
        assert type(http.client._transport._inner) is ResilientHTTPTransport

    async def test_the_fake_server_keeps_a_retrying_transport_working(self, server) -> None:
        """Guards the fake: a client that does configure retries must see them run for real."""
        server.add_file("notes.txt")
        http = NextcloudRESTClientViaUsernamePassword(BASE, server.user, server.app_password)
        http.resilience = ResiliencePolicy(max_retries=2, base_delay=0.01, max_delay=0.05)
        fault = server.fail("PROPFIND", lambda p: p.endswith("/notes.txt"), httpx.Response(429))

        response = await NextcloudDataSource(NextcloudClient(http)).list_directory("alice", "notes.txt", depth=0)

        assert type(http.client._transport._inner) is ResilientHTTPTransport
        assert (response.status, fault.hits) == (207, 1), "the 429 was retried and the retry reached the server"
        assert len(server.calls("PROPFIND")) == 2

    async def test_run_sync_initialises_a_connector_that_was_never_initialised(self, server, db, store) -> None:
        seed_drive(server)
        connector = build(server, db, store)

        await sync(connector)

        assert "q1.pdf" in db.names()


class TestAppPasswordAuth:
    async def test_the_app_password_is_sent_as_basic_auth_on_every_request(self, server, db, store) -> None:
        seed_drive(server)
        connector = await make_connector(server, db, store)
        await sync(connector)

        expected = "Basic " + base64.b64encode(b"alice:" + server.app_password.encode()).decode()
        assert server.requests and {r.headers["authorization"] for r in server.requests} == {expected}
        assert {r.headers.get("ocs-apirequest") for r in server.calls("GET", "/ocs/")} == {"true"}
        assert connector.current_user_email == "alice@example.com"
        assert [u.email for u in db.app_users] == [CREATOR_EMAIL], "only the creator is linked to the app"
        assert access_of(db, "q1.pdf") == INHERITED

    async def test_an_app_password_with_colons_and_accents_still_authenticates(self, server, db, store) -> None:
        server.app_password = "pässwörd:with:colons"
        seed_drive(server)
        connector = await make_connector(server, db, store)

        await sync(connector)

        assert "q1.pdf" in db.names()

    @pytest.mark.parametrize(
        "config",
        [
            pytest.param(None, id="no-config"),
            pytest.param({"auth": {}}, id="empty-auth"),
            pytest.param({"auth": {"baseUrl": BASE, "username": "alice"}}, id="no-app-password"),
            pytest.param({"auth": {"baseUrl": BASE, "username": "alice", "password": ""}}, id="empty-app-password"),
            pytest.param({"auth": {"baseUrl": BASE, "password": "pw"}}, id="no-username"),
            pytest.param({"auth": {"username": "alice", "password": "pw"}}, id="no-base-url"),
        ],
    )
    async def test_incomplete_credentials_fail_init_without_calling_nextcloud(self, server, db, store, config) -> None:
        connector = NextcloudConnector(
            logging.getLogger("t"), db, store, FakeConfigService(CONNECTOR_ID, config),
            CONNECTOR_ID, "personal", "creator-user-1",
        )

        assert await connector.init() is False
        assert connector.data_source is None
        assert server.requests == []

    async def test_a_config_read_that_raises_fails_init(self, server, db, store) -> None:
        connector = build(server, db, store)
        connector.config_service.get_config = AsyncMock(side_effect=RuntimeError("etcd down"))

        assert await connector.init() is False

    async def test_base_url_may_sit_in_the_credentials_section(self, server, db, store) -> None:
        config = {"auth": {"username": "alice", "password": server.app_password},
                  "credentials": {"baseUrl": f"{BASE}/"}}
        seed_drive(server)
        connector = await make_connector(server, db, store, config=config)
        await sync(connector)

        record = db.by_name("q1.pdf")
        assert connector.base_url == BASE
        assert record.weburl == f"{BASE}/index.php/f/{record.external_record_id}"

    async def test_nextcloud_installed_under_a_sub_path(self, db, store, monkeypatch) -> None:
        server = FakeNextcloud(base_path="/nextcloud")
        monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", server.network())
        monkeypatch.setattr(socket, "getaddrinfo", server.getaddrinfo)
        seed_drive(server)
        connector = await make_connector(server, db, store)

        await sync(connector)

        assert db.path_of("q1.pdf") == "Docs/Reports/q1.pdf"
        assert db.by_name("readme.txt").parent_external_record_id is None

    async def test_a_user_with_no_email_in_nextcloud_still_syncs_for_the_creator_only(self, server, db, store) -> None:
        server.email = None
        seed_drive(server)
        connector = await make_connector(server, db, store)

        assert await sync(connector)

        assert connector.current_user_email == "alice", "the email is only used in log lines"
        assert "q1.pdf" in db.names()
        assert [u.email for u in db.app_users] == [CREATOR_EMAIL]
        assert not any(u.email.endswith(MADE_UP_DOMAIN) for u in db.app_users)

    async def test_connection_check_uses_the_app_password(self, server, db, store) -> None:
        connector = await make_connector(server, db, store)
        assert await connector.test_connection_and_access() is True

        server.app_password = "revoked"
        with pytest.raises(ConnectorInitError, match="rejected the app password"):
            await connector.test_connection_and_access()

        connector.data_source = None
        with pytest.raises(ConnectorInitError, match="Could not connect"):
            await connector.test_connection_and_access()

    async def test_connection_check_survives_a_network_error(self, server, db, store) -> None:
        connector = await make_connector(server, db, store)
        server.outage("PROPFIND", lambda p: True, lambda: httpx.ConnectError("refused"))

        with pytest.raises(ConnectorInitError, match="Could not reach Nextcloud"):
            await connector.test_connection_and_access()

    async def test_a_rejected_app_password_is_reported(self, server, db, store) -> None:
        seed_drive(server)
        connector = build(server, db, store, config=auth_config(server, password="wrong-app-password"))
        notifications = listen_for_notifications(connector)

        with pytest.raises(ConnectorInitError, match="rejected the app password") as rejected:
            await connector.init()
        with pytest.raises(ConnectorInitError, match="rejected the app password"):
            await connector.run_sync()

        assert "Create a new app password" in str(rejected.value)
        assert "turn this connector off, enter the new password in its settings, save, and turn it back on" in str(rejected.value)
        assert connector.data_source is None
        assert db.records == {} and store.cursor() is None
        sent = await notifications()
        assert [n["type"] for n in sent] == [NotificationType.CONNECTOR_AUTH_ERROR], "the repeat is held back"
        assert sent[0]["message"].endswith(str(rejected.value))
        assert connector.display_name in sent[0]["title"], "the notification names which connector failed"

    @pytest.mark.parametrize("cursor", [pytest.param(True, id="incremental"), pytest.param(False, id="full")])
    async def test_an_app_password_revoked_after_setup_fails_the_sync(self, server, db, store, cursor) -> None:
        connector = await synced(server, db, store)
        if not cursor:
            store.sync_points.clear()
        kept = {r.external_record_id for r in db.records.values()}
        before = store.cursor()
        server.change("Docs/notes.txt")
        server.app_password = "revoked"
        notifications = listen_for_notifications(connector)

        with pytest.raises(ConnectorInitError, match="rejected the app password"):
            await connector.run_sync()

        assert {r.external_record_id for r in db.records.values()} == kept
        assert store.cursor() == before
        assert [n["type"] for n in await notifications()] == [NotificationType.CONNECTOR_AUTH_ERROR]

    @pytest.mark.parametrize(
        "make",
        [
            pytest.param(lambda: httpx.Response(503), id="server-error"),
            pytest.param(lambda: httpx.ConnectError("connection reset"), id="network-error"),
            pytest.param(lambda: httpx.Response(200, content=b"{not json"), id="garbled"),
        ],
    )
    async def test_a_temporary_user_read_error_fails_the_run_until_it_clears(self, server, db, store, make) -> None:
        seed_drive(server)
        outage = server.outage("GET", lambda p: p == CURRENT_USER_PATH, make)
        connector = await make_connector(server, db, store)

        assert not await sync(connector), "the run is reported failed"
        assert connector.current_user_id is None
        assert db.records == {} and db.app_users == [] and store.cursor() is None, "the run is held, not guessed"

        outage.end()
        assert await sync(connector)
        assert [u.email for u in db.app_users] == [CREATOR_EMAIL]
        assert all(not perms for perms in db.permissions.values())
        assert access_of(db, "q1.pdf") == INHERITED


class TestFullSync:
    async def test_the_whole_drive_is_synced_to_the_end_in_batches(self, server, db, store) -> None:
        seed_drive(server)
        connector = await make_connector(server, db, store)
        connector.batch_size = 2

        await sync(connector)

        assert db.names() == {"Docs", "Reports", "Photos", "q1.pdf", "notes.txt", "cat.png", "readme.txt"}
        assert [len(b) for b in db.batches] == [2, 2, 2, 1], "the last, partial batch is flushed too"
        for name, path in [("q1.pdf", "Docs/Reports/q1.pdf"), ("notes.txt", "Docs/notes.txt"),
                           ("cat.png", "Photos/cat.png"), ("readme.txt", "readme.txt"), ("Reports", "Docs/Reports")]:
            assert db.path_of(name) == path
        pdf = db.by_name("q1.pdf")
        assert isinstance(pdf, FileRecord)
        assert (pdf.mime_type, pdf.extension, pdf.is_file, pdf.size_in_bytes) == (MimeTypes.PDF.value, "pdf", True, 18)
        assert pdf.external_revision_id == server.nodes["Docs/Reports/q1.pdf"].etag
        assert pdf.weburl == f"{BASE}/index.php/f/{pdf.external_record_id}"
        folder = db.by_name("Docs")
        assert (folder.mime_type, folder.is_file, folder.extension) == (MimeTypes.FOLDER.value, False, "")
        assert [u.email for u in db.app_users] == [CREATOR_EMAIL]
        assert db.record_group_permissions["alice"] == [] and db.record_groups["alice"].inherit_permissions is True
        listing = server.calls("PROPFIND")
        # One folder per request, so no answer holds the whole drive and no depth is cut off.
        assert sorted(unquote(r.url.path) for r in listing) == sorted(
            f"{WEBDAV_PREFIX}alice/{p}" for p in ["", "Docs", "Docs/Reports", "Photos"]
        )
        assert {r.headers["depth"] for r in listing} == {"1"}
        assert all(b"comments-unread" not in r.content and b"<oc:fileid" in r.content for r in listing)
        anchor = server.calls("GET", ACTIVITY_PATH)[0].url.params
        assert (anchor["sort"], anchor["limit"]) == ("desc", "1")
        assert store.cursor() == str(server.latest_activity_id)

    async def test_an_empty_drive_syncs_without_writing_records(self, server, db, store) -> None:
        connector = await make_connector(server, db, store)

        await sync(connector)

        assert db.records == {} and db.batches == []
        assert store.cursor() == "0", "an empty feed anchors at its start, so the next run is incremental"

    async def test_repeat_full_syncs_neither_duplicate_nor_rewrite(self, server, db, store) -> None:
        seed_drive(server)
        server.activity_enabled = False  # no cursor, so every run is a full sync
        connector = await make_connector(server, db, store)
        await sync(connector)
        first = {r.external_record_id: r.id for r in db.records.values()}
        writes = len(db.batches)

        await sync(connector)
        assert {r.external_record_id: r.id for r in db.records.values()} == first
        assert len(db.batches) == writes, "unchanged files are not written again"
        # Not written, so noted as kept: the unlink after the full sync leaves their links alone.
        assert set(db.kept_records) == set(first.values())

        server.change("Docs/notes.txt", b"v2")
        server.activities.clear()
        await sync(connector)
        notes = db.by_name("notes.txt")
        assert notes.id == first[notes.external_record_id]
        assert notes.external_revision_id == server.nodes["Docs/notes.txt"].etag
        assert notes.version == 1
        assert len(db.records) == len(first)

    async def test_one_entry_that_fails_does_not_stop_the_rest(self, server, db, store) -> None:
        seed_drive(server)
        connector = await make_connector(server, db, store)
        failing = fail_entries(connector, {ids_of(server)["notes.txt"]})

        assert not await sync(connector), "a full sync that skipped an entry is reported failed"
        assert db.names() == {"Docs", "Reports", "Photos", "q1.pdf", "cat.png", "readme.txt"}
        assert store.cursor() is None, "the entry that failed would never be read again past a cursor"

        failing.clear()
        assert await sync(connector)
        assert db.path_of("notes.txt") == "Docs/notes.txt"
        assert store.cursor() == str(server.latest_activity_id)

    @pytest.mark.parametrize(
        "break_it",
        [
            pytest.param(lambda server, db: server.outage("PROPFIND", lambda p: True, lambda: httpx.Response(503)), id="listing-fails"),
            pytest.param(lambda server, db: server.outage("PROPFIND", lambda p: True, lambda: httpx.Response(429, headers={"Retry-After": "1"})), id="listing-rate-limited"),
            pytest.param(lambda server, db: server.outage("PROPFIND", lambda p: True, lambda: httpx.Response(207, content=b"<not xml")), id="listing-garbled"),
            pytest.param(lambda server, db: server.outage("PROPFIND", lambda p: True, lambda: httpx.Response(207, content=b"")), id="listing-empty"),
            pytest.param(lambda server, db: server.outage("PROPFIND", lambda p: True, lambda: httpx.ConnectError("reset")), id="listing-unreachable"),
            pytest.param(lambda server, db: db.fail_write_for.add("Docs"), id="first-write-fails"),
        ],
    )
    async def test_a_failed_first_sync_is_retried_on_the_next_run(self, server, db, store, break_it) -> None:
        seed_drive(server)
        connector = await make_connector(server, db, store)
        break_it(server, db)

        await sync(connector)
        assert store.cursor() is None, "nothing may be anchored over files that were never saved"

        server.faults.clear()
        db.fail_write_for.clear()
        await sync(connector)

        assert {"q1.pdf", "notes.txt", "cat.png", "readme.txt"} <= db.names()
        assert store.cursor() == str(server.latest_activity_id)

    async def test_a_folder_that_fails_during_a_full_sync_takes_its_contents_with_it(self, server, db, store) -> None:
        seed_drive(server)
        connector = await make_connector(server, db, store)
        failing = fail_entries(connector, {ids_of(server)["Docs"]})

        assert not await sync(connector)
        assert store.cursor() is None
        assert not {"Docs", "Reports", "q1.pdf", "notes.txt"} & db.names(), "nothing saved below a folder that wasn't"
        assert {"Photos", "cat.png", "readme.txt"} <= db.names()

        failing.clear()
        await sync(connector)
        assert db.path_of("notes.txt") == "Docs/notes.txt"
        assert db.path_of("q1.pdf") == "Docs/Reports/q1.pdf"
        assert store.cursor() == str(server.latest_activity_id)

    async def test_a_full_sync_relinks_stored_files_under_a_folder_it_stores_again(self, server, db, store) -> None:
        seed_drive(server)
        server.activity_enabled = False  # no cursor, so every run is a full sync
        connector = await make_connector(server, db, store)
        await sync(connector)
        del db.records[ids_of(server)["Docs"]]  # the folder record went, what it held did not

        await sync(connector)

        assert db.path_of("notes.txt") == "Docs/notes.txt"
        assert db.path_of("q1.pdf") == "Docs/Reports/q1.pdf"

    async def test_a_failed_relink_during_a_full_sync_is_retried_by_the_next(self, server, db, store) -> None:
        seed_drive(server)
        server.add_file("later.txt")  # gives the run an activity to anchor the cursor to
        connector = await make_connector(server, db, store)
        await sync(connector)
        store.sync_points.clear()  # back to full syncs
        del db.records[ids_of(server)["Docs"]]  # the folder record went, what it held did not
        db.fail_write_for.add("notes.txt")

        await sync(connector)
        assert store.cursor() is None

        db.fail_write_for.clear()
        await sync(connector)
        assert db.path_of("notes.txt") == "Docs/notes.txt"
        assert store.cursor() == str(server.latest_activity_id)
        assert store.checkpoint()["full_sync_resave"] == []

    async def test_an_update_that_fails_to_save_during_a_full_sync_is_retried(self, server, db, store) -> None:
        seed_drive(server)
        server.activity_enabled = False  # no cursor, so every run is a full sync
        connector = await make_connector(server, db, store)
        await sync(connector)
        server.change("Docs/notes.txt", b"v2")
        server.add_file("later.txt")  # gives the run something to anchor to
        db.fail_write_for.add("notes.txt")

        assert not await sync(connector), "a full sync that could not save everything is reported failed"

        db.fail_write_for.clear()
        assert await sync(connector)
        assert db.by_name("notes.txt").external_revision_id == server.nodes["Docs/notes.txt"].etag

    async def test_a_failed_cursor_anchor_leaves_the_next_run_a_full_sync(self, server, db, store) -> None:
        seed_drive(server)
        server.outage("GET", lambda p: p == ACTIVITY_PATH, lambda: httpx.Response(503))
        connector = await make_connector(server, db, store)

        await sync(connector)

        assert "q1.pdf" in db.names()
        assert store.cursor() is None

    async def test_a_cursor_anchor_that_raises_is_not_fatal(self, server, db, store) -> None:
        seed_drive(server)
        connector = await make_connector(server, db, store)
        server.outage("GET", lambda p: p == ACTIVITY_PATH, lambda: httpx.ReadTimeout("slow"))

        await sync(connector)

        assert "q1.pdf" in db.names() and store.cursor() is None

    async def test_setup_failure_before_listing_fails_the_run(self, server, db, store) -> None:
        connector = await make_connector(server, db, store)
        db.on_new_app_users = AsyncMock(side_effect=RuntimeError("db down"))

        with pytest.raises(RuntimeError, match="db down"):
            await connector.run_sync()


class TestFilters:
    async def run_with(self, server, db, store, filters) -> set[str]:
        seed_drive(server)
        server.add_file("Docs/LICENSE", b"mit")
        connector = await make_connector(server, db, store, filters=filters)
        await sync(connector)
        return db.names() - {"Docs", "Reports", "Photos"}

    async def test_only_listed_extensions(self, server, db, store) -> None:
        names = await self.run_with(server, db, store, {"file_extensions": {"type": "multiselect", "operator": "in", "value": [".PDF", "png"]}})
        assert names == {"q1.pdf", "cat.png"}
        assert {"Docs", "Reports", "Photos"} <= db.names(), "folders are never filtered out"

    async def test_excluded_extensions(self, server, db, store) -> None:
        names = await self.run_with(server, db, store, {"file_extensions": {"type": "multiselect", "operator": "not_in", "value": ["pdf"]}})
        assert names == {"notes.txt", "cat.png", "readme.txt", "LICENSE"}

    async def narrow_to(self, server, db, store, filters) -> NextcloudConnector:
        """Sync everything, then save ``filters``; saving them clears the sync points, so the next run is full."""
        seed_drive(server)
        connector = await make_connector(server, db, store)
        await sync(connector)
        assert "q1.pdf" in db.names()
        connector.config_service.config = {**connector.config_service.config,
                                           "filters": {"sync": {"values": filters}}}
        store.sync_points.clear()
        return connector

    async def test_a_narrowed_extension_filter_removes_the_files_it_now_leaves_out(self, server, db, store) -> None:
        connector = await self.narrow_to(server, db, store, {"file_extensions": {"type": "multiselect", "operator": "not_in", "value": ["pdf"]}})

        await sync(connector)

        assert db.deleted == ["q1.pdf"]
        assert db.names() == {"Docs", "Reports", "Photos", "notes.txt", "cat.png", "readme.txt"}
        assert store.cursor() == str(server.latest_activity_id)

    async def test_a_narrowed_date_filter_removes_the_files_it_now_leaves_out(self, server, db, store) -> None:
        seed_drive(server)
        server.add_file("old.txt", modified=datetime(2020, 1, 1, tzinfo=timezone.utc))
        connector = await make_connector(server, db, store)
        await sync(connector)
        connector.config_service.config = {**connector.config_service.config, "filters": {"sync": {"values": {
            "modified": {"type": "datetime", "operator": "is_after", "value": {"start": 1735689600000}}}}}}
        store.sync_points.clear()

        await sync(connector)

        assert db.deleted == ["old.txt"]

    async def test_a_full_sync_removes_a_file_deleted_while_no_cursor_tracked_it(self, server, db, store) -> None:
        connector = await synced(server, db, store)
        server.delete("Photos/cat.png")
        server.activities.clear()
        store.sync_points.clear()

        await sync(connector)

        assert db.deleted == ["cat.png"]

    async def test_a_full_sync_removes_a_trashed_file_the_drive_no_longer_has(self, server, db, store) -> None:
        connector = await synced(server, db, store)
        db.by_name("cat.png").is_deleted = True
        server.delete("Photos/cat.png")
        server.activities.clear()
        store.sync_points.clear()

        await sync(connector)

        assert db.deleted == ["cat.png"], "the record scan must see trashed records, or this one stays forever"

    @pytest.mark.parametrize("bad", BAD_ANSWERS)
    async def test_a_listing_that_fails_after_a_narrowed_filter_removes_nothing(self, server, db, store, bad) -> None:
        connector = await self.narrow_to(server, db, store, {"file_extensions": {"type": "multiselect", "operator": "not_in", "value": ["pdf"]}})
        server.outage("PROPFIND", lambda p: True, bad)

        await sync(connector)

        assert db.deleted == []
        assert "q1.pdf" in db.names()
        assert store.cursor() is None, "the next run is a full sync again, which finishes the removal"

    async def test_an_entry_that_fails_after_a_narrowed_filter_removes_nothing(self, server, db, store) -> None:
        connector = await self.narrow_to(server, db, store, {"file_extensions": {"type": "multiselect", "operator": "not_in", "value": ["pdf"]}})
        fail_entries(connector, {ids_of(server)["notes.txt"]})

        assert not await sync(connector)

        assert db.deleted == []

    async def test_records_that_cannot_be_read_are_left_alone_and_retried(self, server, db, store) -> None:
        connector = await self.narrow_to(server, db, store, {"file_extensions": {"type": "multiselect", "operator": "not_in", "value": ["pdf"]}})
        db.fail_record_scan = True

        await sync(connector)
        assert db.deleted == []
        assert store.cursor() is None, "no cursor, so the next run is a full sync that tries again"

        db.fail_record_scan = False
        await sync(connector)
        assert db.deleted == ["q1.pdf"]
        assert store.cursor() == str(server.latest_activity_id)

    async def test_one_failed_removal_does_not_stop_the_rest(self, server, db, store) -> None:
        connector = await self.narrow_to(server, db, store, {"file_extensions": {"type": "multiselect", "operator": "in", "value": ["png"]}})
        db.fail_delete_for = {ids_of(server)["notes.txt"]}

        await sync(connector)
        assert set(db.deleted) == {"q1.pdf", "readme.txt"}
        assert "notes.txt" in db.names()
        assert store.cursor() is None

        db.fail_delete_for.clear()
        await sync(connector)
        assert "notes.txt" not in db.names()
        assert store.cursor() == str(server.latest_activity_id)

    async def test_modified_date_window(self, server, db, store) -> None:
        seed_drive(server)
        server.add_file("old.txt", modified=datetime(2020, 1, 1, tzinfo=timezone.utc))
        server.add_file("future.txt", modified=datetime(2031, 1, 1, tzinfo=timezone.utc))
        start = int(datetime(2025, 1, 1, tzinfo=timezone.utc).timestamp() * 1000)
        end = int(datetime(2030, 1, 1, tzinfo=timezone.utc).timestamp() * 1000)
        connector = await make_connector(server, db, store, filters={
            "modified": {"type": "datetime", "operator": "is_between", "value": {"start": start, "end": end}},
            "created": {"type": "datetime", "operator": "is_after", "value": {"start": start}},
        })

        await sync(connector)

        assert "old.txt" not in db.names() and "future.txt" not in db.names()
        assert {"q1.pdf", "notes.txt", "cat.png", "readme.txt"} <= db.names(), "created-date filters can't apply over WebDAV"


    @pytest.mark.parametrize(("operator", "value", "kept"), [
        ("is_after", {"start": 1735689600000}, {"new.txt"}),
        ("is_before", {"end": 1735689600000}, {"old.txt"}),
    ])
    async def test_one_sided_modified_windows(self, server, db, store, operator, value, kept) -> None:
        server.add_file("old.txt", modified=datetime(2020, 1, 1, tzinfo=timezone.utc))
        server.add_file("new.txt", modified=datetime(2026, 1, 1, tzinfo=timezone.utc))
        connector = await make_connector(server, db, store, filters={
            "modified": {"type": "datetime", "operator": operator, "value": value}})

        await sync(connector)

        assert db.names() == kept

    async def test_a_file_whose_date_cannot_be_read_is_left_out_of_a_date_window(self, server, db, store) -> None:
        server.add_file("odd-date.txt")
        listing = await server_listing(server)
        server.fail("PROPFIND", lambda p: True, httpx.Response(207, content=listing.replace(
            server.nodes["odd-date.txt"].modified.strftime("%a, %d %b %Y %H:%M:%S GMT").encode(), b"2026-03-01T09:01:00Z")))
        connector = await make_connector(server, db, store, filters={
            "modified": {"type": "datetime", "operator": "is_after", "value": {"start": 0}}})

        await sync(connector)

        assert "odd-date.txt" not in db.names()

    async def test_an_unreadable_date_without_a_window_still_syncs_the_file(self, server, db, store) -> None:
        server.add_file("odd-date.txt")
        listing = await server_listing(server)
        server.fail("PROPFIND", lambda p: True, httpx.Response(207, content=listing.replace(
            server.nodes["odd-date.txt"].modified.strftime("%a, %d %b %Y %H:%M:%S GMT").encode(), b"yesterday")))
        connector = await make_connector(server, db, store)

        await sync(connector)

        assert "odd-date.txt" in db.names()


async def server_listing(server: FakeNextcloud) -> bytes:
    return server._propfind("", "100").content


class TestIncrementalSync:
    async def test_a_new_file_in_new_folders_gets_its_folders_created_top_down(self, server, db, store) -> None:
        connector = await synced(server, db, store)
        cursor = store.cursor()
        server.add_file("Projects/2026/Plan/brief.pdf", b"brief", "application/pdf")

        await sync(connector)

        assert db.path_of("brief.pdf") == "Projects/2026/Plan/brief.pdf"
        assert db.path_of("Plan") == "Projects/2026/Plan"
        feed = server.calls("GET", ACTIVITY_PATH)[-1].url.params
        assert (feed["since"], feed["sort"], feed["limit"]) == (cursor, "asc", "200")
        assert store.cursor() == str(server.latest_activity_id)
        incremental = server.calls("PROPFIND")[4:]  # the full sync listed the root, Docs, Docs/Reports and Photos
        fetched = {(unquote(r.url.path).removeprefix(f"{WEBDAV_PREFIX}alice/"), r.headers["depth"]) for r in incremental}
        assert ("Projects/2026/Plan/brief.pdf", "0") in fetched, "a changed file is fetched on its own"
        # A created folder is also listed, one level at a time: a copied folder logs nothing for what it holds.
        assert {d for _, d in fetched} <= {"0", "1"}

    async def test_no_new_activity_changes_nothing(self, server, db, store) -> None:
        connector = await synced(server, db, store)
        writes, cursor, propfinds = len(db.batches), store.cursor(), len(server.calls("PROPFIND"))

        await sync(connector)
        await connector.run_incremental_sync()

        assert (len(db.batches), store.cursor(), len(server.calls("PROPFIND"))) == (writes, cursor, propfinds)

    async def test_every_file_of_a_merged_upload_activity_is_synced(self, server, db, store) -> None:
        connector = await synced(server, db, store)
        server.add_files_in_one_upload(["Docs/a.txt", "Docs/b.txt", "Docs/c.txt"])

        await sync(connector)

        assert {"a.txt", "b.txt", "c.txt"} <= db.names()
        assert db.path_of("c.txt") == "Docs/c.txt"

    async def test_a_changed_file_is_updated_in_place(self, server, db, store) -> None:
        connector = await synced(server, db, store)
        before = db.by_name("notes.txt")
        count = len(db.records)
        server.change("Docs/notes.txt", b"new notes")

        await sync(connector)

        after = db.by_name("notes.txt")
        assert (after.id, after.version) == (before.id, before.version + 1)
        assert after.external_revision_id == server.nodes["Docs/notes.txt"].etag
        assert len(db.records) == count
        assert access_of(db, "notes.txt") == INHERITED

    async def test_a_rename_keeps_the_record_and_its_folder(self, server, db, store) -> None:
        connector = await synced(server, db, store)
        before = db.by_name("notes.txt")
        server.move("Docs/notes.txt", "Docs/meeting-notes.txt")

        await sync(connector)

        after = db.by_name("meeting-notes.txt")
        assert after.id == before.id and "notes.txt" not in db.names()
        assert db.path_of("meeting-notes.txt") == "Docs/meeting-notes.txt"

    @pytest.mark.parametrize(("target", "path"), [("Photos/notes.txt", "Photos/notes.txt"), ("notes.txt", "notes.txt"),
                                                   ("Archive/2025/notes.txt", "Archive/2025/notes.txt")])
    async def test_a_move_shows_the_file_only_in_its_new_folder(self, server, db, store, target, path) -> None:
        connector = await synced(server, db, store)
        server.move("Docs/notes.txt", target)

        await sync(connector)

        assert db.path_of("notes.txt") == path

    async def test_renaming_a_folder_carries_its_contents(self, server, db, store) -> None:
        connector = await synced(server, db, store)
        server.move("Docs", "Documents")

        await sync(connector)

        assert db.path_of("q1.pdf") == "Documents/Reports/q1.pdf"
        assert "Docs" not in db.names()

    async def test_a_deleted_file_is_removed_and_a_restored_one_comes_back(self, server, db, store) -> None:
        connector = await synced(server, db, store)
        node = server.delete("Docs/notes.txt")

        await sync(connector)
        assert "notes.txt" not in db.names() and db.deleted == ["notes.txt"]

        server.restore(node)
        await sync(connector)
        assert db.by_name("notes.txt").external_record_id == node.file_id
        assert db.path_of("notes.txt") == "Docs/notes.txt"

    @pytest.mark.parametrize("same_page", [pytest.param(False, id="next-sync"), pytest.param(True, id="same-page")])
    async def test_a_restored_folder_comes_back_with_its_contents(self, server, db, store, same_page) -> None:
        connector = await synced(server, db, store)
        docs = server.delete("Docs")
        if not same_page:
            await sync(connector)
            assert not {"Docs", "Reports", "q1.pdf", "notes.txt"} & db.names()

        server.restore(docs)
        await sync(connector)

        assert db.path_of("q1.pdf") == "Docs/Reports/q1.pdf"
        assert db.path_of("notes.txt") == "Docs/notes.txt"
        assert store.cursor() == str(server.latest_activity_id)

    async def test_deleting_a_file_the_index_never_had_is_harmless(self, server, db, store) -> None:
        connector = await synced(server, db, store)
        server.add_file("tmp.txt", log=False)
        server.delete("tmp.txt")

        await sync(connector)

        assert db.deleted == [] and store.cursor() == str(server.latest_activity_id)

    async def test_deleting_a_folder_removes_what_was_inside(self, server, db, store) -> None:
        connector = await synced(server, db, store)
        server.delete("Docs")

        await sync(connector)

        assert not {"Docs", "Reports", "q1.pdf", "notes.txt"} & db.names()
        assert {"Photos", "cat.png", "readme.txt"} <= db.names(), "only what was inside goes"

    async def test_what_a_half_finished_folder_delete_left_behind_is_removed(self, server, db, store) -> None:
        connector = await synced(server, db, store)
        folder = db.by_name("Docs")
        del db.records[folder.external_record_id]  # the folder went, its contents did not
        server.delete("Docs")

        await sync(connector)

        assert not {"Reports", "q1.pdf", "notes.txt"} & db.names()

    async def test_a_backlog_longer_than_one_page_is_caught_up_in_one_run(self, server, db, store) -> None:
        connector = await synced(server, db, store)
        start = server.latest_activity_id
        for i in range(500):
            server.change("Docs/notes.txt", f"v{i}".encode())
        server.add_file("late.txt")

        assert await sync(connector)
        pages = [r.url.params["since"] for r in server.calls("GET", ACTIVITY_PATH) if "since" in r.url.params]
        # Nextcloud answers at most 200 a page, so a full page is shorter than the 500 once asked for.
        assert pages[-3:] == [str(start), str(start + 200), str(start + 400)], "the run reads every page"
        assert store.cursor() == str(server.latest_activity_id)
        assert "late.txt" in db.names()
        assert db.by_name("notes.txt").external_revision_id == server.nodes["Docs/notes.txt"].etag

    async def test_one_failed_fetch_does_not_stop_the_other_changes(self, server, db, store) -> None:
        connector = await synced(server, db, store)
        server.change("Docs/notes.txt")
        server.change("readme.txt")
        server.outage("PROPFIND", lambda p: p.endswith("/notes.txt"), lambda: httpx.Response(503))

        await sync(connector)

        assert db.by_name("readme.txt").external_revision_id == server.nodes["readme.txt"].etag

    @pytest.mark.parametrize("bad", BAD_ANSWERS)
    async def test_a_bad_answer_for_one_changed_file_does_not_stop_the_others(self, server, db, store, bad) -> None:
        connector = await synced(server, db, store)
        server.change("Docs/notes.txt")
        server.change("readme.txt")
        server.outage("PROPFIND", lambda p: p.endswith("/notes.txt"), bad)

        await sync(connector)

        assert db.by_name("readme.txt").external_revision_id == server.nodes["readme.txt"].etag

    @pytest.mark.parametrize("bad", BAD_ANSWERS)
    async def test_a_bad_answer_for_a_new_folder_still_saves_the_file(self, server, db, store, bad) -> None:
        connector = await synced(server, db, store)
        server.add_file("New/inside.txt")
        server.outage("PROPFIND", lambda p: p.rstrip("/").endswith("/New"), bad)

        await sync(connector)

        assert "inside.txt" in db.names()

    @pytest.mark.parametrize(
        "answer",
        [
            pytest.param(httpx.Response(200, content=b"{not json"), id="garbled"),
            pytest.param(httpx.Response(200, content=b'{"ocs": {"data": {"unexpected": "shape"}}}'), id="unexpected-shape"),
            pytest.param(httpx.Response(200, content=b'{"ocs": {"data": ["junk", {"type": "file_created"}]}}'), id="no-activity-ids"),
        ],
    )
    async def test_a_feed_answer_with_nothing_usable_keeps_the_cursor(self, server, db, store, answer) -> None:
        connector = await synced(server, db, store)
        cursor, writes = store.cursor(), len(db.batches)
        server.add_file("new.txt")
        server.fail("GET", lambda p: p == ACTIVITY_PATH, answer)

        await sync(connector)
        assert (store.cursor(), len(db.batches)) == (cursor, writes)

        await sync(connector)
        assert "new.txt" in db.names(), "the next run reads the same window again"

    async def test_activity_that_is_not_about_files_is_skipped_but_passed(self, server, db, store) -> None:
        connector = await synced(server, db, store)
        server.activities.append({"activity_id": server.latest_activity_id + 1, "type": "calendar_event",
                                  "object_type": "calendar", "object_id": 7, "object_name": "Standup"})
        server.activities.append({"activity_id": server.latest_activity_id + 1, "type": "file_deleted",
                                  "object_type": "files", "object_id": 0, "object_name": "", "objects": {}})
        server.activities.append({"activity_id": server.latest_activity_id + 1, "type": "file_changed",
                                  "object_type": "files", "object_id": 1, "object_name": ""})
        writes = len(db.batches)

        await sync(connector)

        assert len(db.batches) == writes and db.deleted == []
        assert store.cursor() == str(server.latest_activity_id)

    async def test_a_record_group_that_vanished_triggers_a_full_sync(self, server, db, store) -> None:
        connector = await synced(server, db, store)
        db.record_groups.clear()
        server.add_file("extra.txt")

        await sync(connector)

        assert "alice" in db.record_groups and "extra.txt" in db.names()

    async def test_an_activity_feed_that_raises_fails_the_run(self, server, db, store) -> None:
        connector = await synced(server, db, store)
        server.outage("GET", lambda p: p == ACTIVITY_PATH, lambda: httpx.ReadTimeout("slow"))

        with pytest.raises(httpx.ReadTimeout):
            await connector.run_sync()

    async def test_incremental_sync_without_a_client_or_user_fails_without_writing(self, server, db, store) -> None:
        connector = await synced(server, db, store)
        writes = len(db.batches)
        connector.current_user_id = None
        with pytest.raises(NextcloudSyncError, match="not initialized"):
            await connector.run_incremental_sync()
        connector.data_source = None
        with pytest.raises(NextcloudSyncError, match="not initialized"):
            await connector.run_incremental_sync()

        assert len(db.batches) == writes

    async def test_an_unreadable_cursor_does_not_skip_deletions(self, server, db, store) -> None:
        connector = await synced(server, db, store)
        cursor, listings = store.cursor(), len(server.calls("PROPFIND"))
        server.delete("Docs/notes.txt")
        store.fail_reads = 1

        with pytest.raises(RuntimeError, match="database unavailable"):
            await connector.run_sync()
        assert store.cursor() == cursor and len(server.calls("PROPFIND")) == listings, "no full sync in its place"

        await sync(connector)
        assert "notes.txt" not in db.names()
        assert store.cursor() == str(server.latest_activity_id)

    @pytest.mark.parametrize("status", [503, 429])
    async def test_a_failed_activity_feed_does_not_skip_deletions(self, server, db, store, status) -> None:
        connector = await synced(server, db, store)
        cursor, listings = store.cursor(), len(server.calls("PROPFIND"))
        server.delete("Docs/notes.txt")
        outage = server.outage("GET", lambda p: p == ACTIVITY_PATH,
                               lambda: httpx.Response(status, headers={"Retry-After": "1"}))

        await sync(connector)
        assert store.cursor() == cursor and len(server.calls("PROPFIND")) == listings, "no full sync in its place"

        outage.end()
        await sync(connector)
        assert "notes.txt" not in db.names()
        assert store.cursor() == str(server.latest_activity_id)

    @pytest.mark.parametrize("bad", [
        pytest.param(lambda: httpx.Response(503), id="server-error"),
        pytest.param(lambda: httpx.Response(207, content=b"<d:multistatus"), id="garbled-xml"),
        pytest.param(lambda: httpx.ConnectError("connection reset"), id="network-error"),
    ])
    async def test_a_change_that_failed_to_fetch_is_retried_next_run(self, server, db, store, bad) -> None:
        connector = await synced(server, db, store)
        cursor = store.cursor()
        server.change("Docs/notes.txt")
        server.change("readme.txt")
        outage = server.outage("PROPFIND", lambda p: p.endswith("/notes.txt"), bad)

        await sync(connector)
        assert store.cursor() == cursor, "held until the change is applied"
        assert db.by_name("readme.txt").external_revision_id == server.nodes["readme.txt"].etag

        outage.end()
        await sync(connector)
        assert db.by_name("notes.txt").external_revision_id == server.nodes["Docs/notes.txt"].etag
        assert store.cursor() == str(server.latest_activity_id)

    async def test_a_change_that_fails_to_save_is_retried_next_run(self, server, db, store) -> None:
        connector = await synced(server, db, store)
        cursor = store.cursor()
        server.change("Docs/notes.txt")
        db.fail_write_for.add("notes.txt")

        await sync(connector)
        assert store.cursor() == cursor

        db.fail_write_for.clear()
        await sync(connector)
        assert db.by_name("notes.txt").external_revision_id == server.nodes["Docs/notes.txt"].etag

    async def test_a_change_that_never_applies_is_passed_over_after_five_runs(self, server, db, store) -> None:
        connector = await synced(server, db, store)
        cursor = store.cursor()
        server.change("Docs/notes.txt")
        server.outage("PROPFIND", lambda p: p.endswith("/notes.txt"), lambda: httpx.Response(503))

        for _ in range(4):
            await sync(connector)
            assert store.cursor() == cursor
        await sync(connector)

        assert store.cursor() == str(server.latest_activity_id)
        server.add_file("after.txt")
        await sync(connector)
        assert "after.txt" in db.names(), "later changes are no longer held up"

    async def test_a_deletion_that_fails_five_times_is_applied_once_the_fault_clears(self, server, db, store) -> None:
        connector = await synced(server, db, store)
        notes = ids_of(server)["notes.txt"]
        db.fail_delete_for = {notes}
        server.delete("Docs/notes.txt")

        for _ in range(MAX_HELD_ATTEMPTS):
            await sync(connector)
        assert store.cursor() == str(server.latest_activity_id), "the page is given up on after 5 runs"
        assert store.checkpoint()["pending_deletes"] == [notes]
        assert "notes.txt" in db.names()

        await sync(connector)
        assert store.checkpoint()["pending_deletes"] == [notes], "kept while it still fails"

        db.fail_delete_for.clear()
        await connector.run_sync()  # no new activity: the retry runs before the feed is read
        assert "notes.txt" not in db.names()
        assert store.checkpoint()["pending_deletes"] == []

    async def test_the_checkpoint_holds_only_values_neo4j_can_store(self, server, db, store) -> None:
        connector = await synced(server, db, store)
        notes = ids_of(server)["notes.txt"]
        db.fail_delete_for = {notes}
        server.delete("Docs/notes.txt")

        for _ in range(MAX_HELD_ATTEMPTS):
            await sync(connector)

        checkpoint = store.checkpoint()
        assert store.cursor() == str(server.latest_activity_id), "the give-up write went through"
        assert checkpoint["pending_deletes"] == [notes]
        assert checkpoint["pending_delete_paths"] == ["/Docs/notes.txt"], "parallel to pending_deletes"
        for name, value in checkpoint.items():
            items = value if isinstance(value, list) else [value]
            assert all(isinstance(v, (str, int, float, bool)) for v in items if v is not None), name

    async def test_a_requeued_deletion_keeps_the_path_it_was_deleted_from(self, server, db, store) -> None:
        connector = await synced(server, db, store)
        docs = ids_of(server)["Docs"]
        store.checkpoint().update(pending_deletes=[docs], pending_delete_paths=["/Old/Docs"], held_attempts=4)
        db.fail_delete_for = {ids_of(server)["notes.txt"]}
        server.delete("Docs")

        await sync(connector)

        assert store.checkpoint()["pending_deletes"] == [docs]
        assert store.checkpoint()["pending_delete_paths"] == ["/Docs"]

    async def test_a_pending_deletion_whose_record_is_gone_is_dropped_quietly(self, server, db, store) -> None:
        connector = await synced(server, db, store)
        cursor = store.cursor()
        store.checkpoint()["pending_deletes"] = ["99999"]
        logged = capture_logs(connector)

        await sync(connector)

        assert store.checkpoint()["pending_deletes"] == [] and store.cursor() == cursor
        assert db.deleted == []
        assert logged, "the run was logged, so an empty warning list below means something"
        assert not [r for r in logged if r.levelno >= logging.WARNING]

    async def test_a_folder_below_one_that_failed_is_parented_after_the_retry(self, server, db, store) -> None:
        connector = await synced(server, db, store)
        server.add_file("New/Deep/inside.txt")
        # Only the file's own activity is on the page, so its folders exist nowhere but above it.
        server.activities[:] = [a for a in server.activities if a["object_name"] not in ("/New", "/New/Deep")]
        outage = server.outage("PROPFIND", lambda p: p.rstrip("/").endswith("/New"), lambda: httpx.Response(503))

        await sync(connector)
        outage.end()
        await sync(connector)

        assert db.path_of("Deep") == "New/Deep"
        assert db.path_of("inside.txt") == "New/Deep/inside.txt"

    async def test_a_changed_file_keeps_its_folder_when_that_folder_cannot_be_read(self, server, db, store) -> None:
        connector = await synced(server, db, store)
        server.change("Docs/notes.txt", b"v2")
        server.outage("PROPFIND", lambda p: p.rstrip("/").endswith("/Docs"), lambda: httpx.Response(503))

        for _ in range(MAX_HELD_ATTEMPTS):
            await sync(connector)
            assert db.path_of("notes.txt") == "Docs/notes.txt"

        assert store.cursor() == str(server.latest_activity_id), "given up on, and still in its folder"
        assert db.by_name("notes.txt").external_revision_id == server.nodes["Docs/notes.txt"].etag

    async def test_a_new_file_finds_its_stored_folder_when_a_folder_above_cannot_be_read(
        self, server, db, store
    ) -> None:
        connector = await synced(server, db, store)
        server.add_file("Docs/Reports/q2.pdf", b"%PDF-1.7 q2", "application/pdf")
        server.outage("PROPFIND", lambda p: p.rstrip("/").endswith("/Docs"), lambda: httpx.Response(503))

        for _ in range(MAX_HELD_ATTEMPTS):
            await sync(connector)
            assert db.path_of("q2.pdf") == "Docs/Reports/q2.pdf"

        assert store.cursor() == str(server.latest_activity_id), "given up on, and still in its folder"

    async def test_a_new_file_is_not_saved_without_its_folder_when_the_folder_lookup_fails(
        self, server, db, store
    ) -> None:
        connector = await synced(server, db, store)
        server.add_file("Docs/Reports/q2.pdf", b"%PDF-1.7 q2", "application/pdf")
        outage = server.outage("PROPFIND", lambda p: p.rstrip("/").endswith("/Docs"), lambda: httpx.Response(503))
        store.fail_path_lookups = True

        await sync(connector)
        assert "q2.pdf" not in db.names(), "a lookup that failed is not taken to mean there is no folder"

        store.fail_path_lookups = False
        outage.end()
        await sync(connector)
        assert db.path_of("q2.pdf") == "Docs/Reports/q2.pdf"

    async def test_a_file_in_a_new_folder_whose_lookup_failed_is_parented_after_the_retry(
        self, server, db, store
    ) -> None:
        connector = await synced(server, db, store)
        server.add_file("Docs/New/q2.pdf", b"%PDF-1.7 q2", "application/pdf")
        outage = server.outage("PROPFIND", lambda p: p.rstrip("/").endswith("/Docs"), lambda: httpx.Response(503))
        store.fail_path_lookups = True

        await sync(connector)
        assert not {"New", "q2.pdf"} & db.names(), "nothing below a folder that could not be saved is saved"

        store.fail_path_lookups = False
        outage.end()
        await sync(connector)
        assert db.path_of("q2.pdf") == "Docs/New/q2.pdf"

    async def test_a_file_restored_after_its_failed_delete_is_kept(self, server, db, store) -> None:
        connector = await synced(server, db, store)
        kept = db.by_name("notes.txt").id
        db.fail_delete_for = {ids_of(server)["notes.txt"]}
        server.restore(server.delete("Docs/notes.txt"))

        for _ in range(MAX_HELD_ATTEMPTS):
            await sync(connector)
        db.fail_delete_for.clear()
        await sync(connector)

        assert db.by_name("notes.txt").id == kept and "notes.txt" not in db.deleted
        assert store.checkpoint()["pending_deletes"] == [], "a later restore on the page cancels the deletion"

    async def test_a_pending_deletion_of_a_file_that_exists_again_is_dropped(self, server, db, store) -> None:
        connector = await synced(server, db, store)
        notes = server.nodes["Docs/notes.txt"]
        kept = db.by_name("notes.txt").id
        db.fail_delete_for = {notes.file_id}
        server.delete("Docs/notes.txt")
        for _ in range(MAX_HELD_ATTEMPTS):
            await sync(connector)
        assert store.checkpoint()["pending_deletes"] == [notes.file_id]
        db.fail_delete_for.clear()
        server.restore(notes)
        db.record_groups.clear()  # the next run falls back to a full sync, which moves the cursor past the restore

        await sync(connector)
        await sync(connector)

        assert db.by_name("notes.txt").id == kept and "notes.txt" not in db.deleted
        assert store.checkpoint()["pending_deletes"] == []

    async def test_a_later_change_to_a_file_that_is_still_gone_keeps_its_deletion_queued(self, server, db, store) -> None:
        connector = await synced(server, db, store)
        notes = server.nodes["Docs/notes.txt"]
        db.fail_delete_for = {notes.file_id}
        server.delete("Docs/notes.txt")
        # An activity for the file after its deletion, whose fetch now finds nothing.
        server.activities.append({"activity_id": server.latest_activity_id + 1, "type": "file_changed",
                                  "object_type": "files", "object_id": int(notes.file_id),
                                  "object_name": "/Docs/notes.txt", "objects": {notes.file_id: "/Docs/notes.txt"}})
        for _ in range(MAX_HELD_ATTEMPTS):
            await sync(connector)
        assert store.checkpoint()["pending_deletes"] == [notes.file_id]

        db.fail_delete_for.clear()
        await sync(connector)
        assert "notes.txt" not in db.names()
        assert store.checkpoint()["pending_deletes"] == []

    @pytest.mark.parametrize("restored", [pytest.param(True, id="folder-restored"), pytest.param(False, id="folder-gone")])
    async def test_a_queued_folder_whose_record_is_gone_is_checked_before_its_contents_go(
            self, server, db, store, restored) -> None:
        connector = await synced(server, db, store)
        docs = server.nodes["Docs"]
        reports = db.by_name("Reports").external_record_id
        inside = {db.by_name(n).id for n in ("q1.pdf", "notes.txt")}
        db.fail_delete_for = {docs.file_id}
        server.delete("Docs")
        for _ in range(MAX_HELD_ATTEMPTS):
            await sync(connector)
        assert store.checkpoint()["pending_deletes"] == [docs.file_id]
        db.fail_delete_for.clear()
        del db.records[docs.file_id]  # a cascade that committed partway: the folder went, its contents did not
        if restored:
            del db.records[reports]  # a folder inside it went too, so it comes back as a new record as well
            server.restore(docs)  # the queue is retried before the feed that reports this is read

        await sync(connector)

        still_there = {r.id for r in db.records.values()} & inside
        assert still_there == (inside if restored else set()), "restored contents keep their records"
        assert store.checkpoint()["pending_deletes"] == []
        if restored:
            assert db.path_of("notes.txt") == "Docs/notes.txt"
            assert db.path_of("q1.pdf") == "Docs/Reports/q1.pdf"

    @pytest.mark.parametrize("answer", [
        pytest.param(b"<not xml", id="garbled"),
        pytest.param(b'<?xml version="1.0"?><d:error xmlns:d="DAV:"/>', id="not-a-multistatus"),
    ])
    async def test_a_search_answer_that_cannot_be_read_keeps_a_folder_queued(self, server, db, store, answer) -> None:
        connector = await synced(server, db, store)
        docs = server.nodes["Docs"]
        inside = {db.by_name(n).id for n in ("Reports", "q1.pdf", "notes.txt")}
        db.fail_delete_for = {docs.file_id}
        server.delete("Docs")
        for _ in range(MAX_HELD_ATTEMPTS):
            await sync(connector)
        db.fail_delete_for.clear()
        del db.records[docs.file_id]  # a cascade that committed partway: the folder went, its contents did not
        server.outage("SEARCH", lambda p: True, lambda: httpx.Response(207, content=answer))

        await sync(connector)

        assert {r.id for r in db.records.values()} >= inside
        assert store.checkpoint()["pending_deletes"] == [docs.file_id]

    @pytest.mark.parametrize("search", [pytest.param(True, id="found-by-id"), pytest.param(False, id="no-search")])
    async def test_a_queued_folder_that_moved_keeps_its_contents(self, server, db, store, search) -> None:
        connector = await synced(server, db, store)
        docs = server.nodes["Docs"]
        inside = {db.by_name(n).id for n in ("Reports", "q1.pdf", "notes.txt")}
        db.fail_delete_for = {docs.file_id}
        server.delete("Docs")
        for _ in range(MAX_HELD_ATTEMPTS):
            await sync(connector)
        db.fail_delete_for.clear()
        del db.records[docs.file_id]  # a cascade that committed partway: the folder went, its contents did not
        server.restore(docs)
        server.move("Docs", "Archive/Docs")  # back, but not where it was deleted from, so that path 404s
        server.search_supported = search
        if search:
            db.fail_write_for.add("notes.txt")  # the folder is saved, then a file inside it isn't
            await sync(connector)
            assert store.checkpoint()["pending_deletes"] == [docs.file_id], "kept until everything is relinked"
            db.fail_write_for.clear()

        await sync(connector)

        assert {r.id for r in db.records.values()} >= inside, "a 404 at the old path is not proof"
        assert store.checkpoint()["pending_deletes"] == ([] if search else [docs.file_id])
        if search:
            assert db.path_of("notes.txt") == "Archive/Docs/notes.txt"
            assert db.path_of("q1.pdf") == "Archive/Docs/Reports/q1.pdf"

    async def test_a_queued_folder_that_moved_keeps_its_contents_without_a_search(self, server, db, store) -> None:
        server.search_supported = False
        connector = await synced(server, db, store)
        docs = server.nodes["Docs"]
        inside = {db.by_name(n).id for n in ("Reports", "q1.pdf", "notes.txt")}
        db.fail_delete_for = {docs.file_id}
        server.delete("Docs")
        for _ in range(MAX_HELD_ATTEMPTS):
            await sync(connector)
        db.fail_delete_for.clear()
        server.restore(docs)
        server.move("Docs", "Archive/Docs")  # its record is still stored, and its old path 404s

        await sync(connector)

        assert {r.id for r in db.records.values()} >= inside
        assert store.checkpoint()["pending_deletes"] == [docs.file_id]

    @pytest.mark.parametrize("break_path", [
        pytest.param(lambda db, record: db.unreadable_paths.add(record.id), id="path-read-fails"),
        pytest.param(lambda db, record: db.edges.pop(record.id), id="path-is-only-the-name"),
    ])
    async def test_a_queued_deletion_waits_when_the_stored_path_is_not_known(self, server, db, store, break_path) -> None:
        server.search_supported = False  # without a lookup by ID, the stored path is all there is to check
        connector = await synced(server, db, store)
        notes = db.by_name("notes.txt")
        db.fail_delete_for = {notes.external_record_id}
        server.delete("Docs/notes.txt")
        for _ in range(MAX_HELD_ATTEMPTS):
            await sync(connector)
        db.fail_delete_for.clear()
        break_path(db, notes)

        await sync(connector)
        assert notes.external_record_id in {r.external_record_id for r in db.records.values()}
        assert store.checkpoint()["pending_deletes"] == [notes.external_record_id], "kept queued, not guessed"

        db.unreadable_paths.clear()
        db.edges[notes.id] = db.by_name("Docs").id
        await sync(connector)
        assert notes.external_record_id not in {r.external_record_id for r in db.records.values()}
        assert store.checkpoint()["pending_deletes"] == []

    async def test_the_give_up_error_names_what_could_not_be_applied(self, server, db, store) -> None:
        connector = await synced(server, db, store)
        server.change("Docs/notes.txt")
        readme = ids_of(server)["readme.txt"]
        db.fail_delete_for = {readme}
        server.delete("readme.txt")
        server.outage("PROPFIND", lambda p: p.endswith("/notes.txt"), lambda: httpx.Response(503))

        logged = capture_logs(connector)

        for _ in range(MAX_HELD_ATTEMPTS):
            await sync(connector)

        gave_up = [r.getMessage() for r in logged
                   if r.levelno == logging.ERROR and "still could not be applied" in r.getMessage()]
        assert len(gave_up) == 1
        assert "/Docs/notes.txt (fetch failed: HTTP 503)" in gave_up[0]
        assert f"deletion of /readme.txt (ID {readme}) (delete failed for readme.txt)" in gave_up[0]

    def test_a_long_list_of_failures_is_cut_short(self) -> None:
        limit = nextcloud_connector.MAX_NAMED_FAILURES
        failures = {f"/file-{i}.txt": "HTTP 503" for i in range(limit + 3)}

        line = nextcloud_connector.describe_failures(failures)

        assert line.count("HTTP 503") == limit
        assert line.endswith("; and 3 more")

    async def test_a_file_gone_before_the_sync_does_not_hold_the_cursor(self, server, db, store) -> None:
        connector = await synced(server, db, store)
        server.add_file("New/brief.txt")
        server.delete("New")

        await sync(connector)

        assert store.cursor() == str(server.latest_activity_id)
        assert not {"New", "brief.txt"} & db.names()

    @pytest.mark.parametrize(("deleted", "gone"), [
        pytest.param("Docs/notes.txt", {"notes.txt"}, id="file"),
        pytest.param("Docs", {"Docs", "Reports", "q1.pdf", "notes.txt"}, id="folder"),
    ])
    async def test_a_deletion_that_failed_to_apply_is_retried_next_run(self, server, db, store, deleted, gone) -> None:
        connector = await synced(server, db, store)
        cursor = store.cursor()
        db.fail_delete_for = {ids_of(server)["notes.txt"]}
        server.delete(deleted)
        server.add_file("new.txt")

        await sync(connector)
        assert store.cursor() == cursor, "held until the deletion is applied"
        assert "new.txt" in db.names(), "the rest of the page is still applied"

        db.fail_delete_for.clear()
        await sync(connector)
        assert not gone & db.names()
        assert store.cursor() == str(server.latest_activity_id)

    async def test_a_deletion_whose_lookup_fails_is_retried_next_run(self, server, db, store) -> None:
        connector = await synced(server, db, store)
        notes = ids_of(server)["notes.txt"]
        server.delete("Docs/notes.txt")
        db.fail_lookup_for = {notes}

        await sync(connector)
        db.fail_lookup_for.clear()
        await sync(connector)

        assert "notes.txt" not in db.names()

    @pytest.mark.parametrize("answer", [
        pytest.param(httpx.Response(429, headers={"Retry-After": "7"}), id="rate-limited"),
        pytest.param(httpx.Response(503, headers={"Retry-After": "7"}), id="unavailable"),
    ])
    async def test_a_rate_limited_fetch_is_retried_after_waiting(self, server, db, store, no_real_pauses, answer) -> None:
        connector = await synced(server, db, store)
        server.change("Docs/notes.txt")
        fault = server.fail("PROPFIND", lambda p: p.endswith("/notes.txt"), answer)

        await sync(connector)

        assert db.by_name("notes.txt").external_revision_id == server.nodes["Docs/notes.txt"].etag
        assert fault.hits == 1 and len([r for r in server.calls("PROPFIND") if r.url.path.endswith("/notes.txt")]) == 2
        assert [p for p in no_real_pauses if p >= 7] != [], "the retry waited as long as Retry-After asked"
        assert store.cursor() == str(server.latest_activity_id)

    async def test_a_rate_limited_activity_feed_is_retried_in_the_same_run(self, server, db, store) -> None:
        connector = await synced(server, db, store)
        server.delete("Docs/notes.txt")
        server.fail("GET", lambda p: p == ACTIVITY_PATH, httpx.Response(429, headers={"Retry-After": "2"}))

        await sync(connector)

        assert "notes.txt" not in db.names()

    async def test_retries_give_up_after_four_attempts(self, server, db, store) -> None:
        connector = await synced(server, db, store)
        server.change("Docs/notes.txt")
        outage = server.outage("PROPFIND", lambda p: p.endswith("/notes.txt"), lambda: httpx.Response(429))

        await sync(connector)

        assert outage.hits == 4


class TestRateLimiting:
    async def test_every_webdav_listing_waits_for_the_rate_limiter(self, server, db, store) -> None:
        class CountingLimiter(AsyncLimiter):
            entered = 0

            async def __aenter__(self) -> None:
                CountingLimiter.entered += 1
                await super().__aenter__()

        connector = await synced(server, db, store)
        connector.rate_limiter = CountingLimiter(50, 1)
        server.add_file("Deep/er/file.txt")
        before = len(server.calls("PROPFIND"))

        await sync(connector)

        assert CountingLimiter.entered == len(server.calls("PROPFIND")) - before >= 3


class TestPermissions:
    async def test_only_the_connecting_user_gets_access_even_to_shared_files(self, server, db, store) -> None:
        seed_drive(server)
        server.shares["/Docs/Reports/q1.pdf"] = [{"share_type": 0, "share_with": "bob", "permissions": 19}]
        connector = await make_connector(server, db, store)

        await sync(connector)

        assert {name: access_of(db, name) for name in db.names()} == dict.fromkeys(db.names(), INHERITED), (
            "a Nextcloud share never widens access in PipesHub"
        )
        assert [u.email for u in db.app_users] == [CREATOR_EMAIL]

    async def test_a_failed_fetch_writes_no_record_and_no_access(self, server, db, store) -> None:
        connector = await synced(server, db, store)
        server.add_file("secret.txt")
        server.outage("PROPFIND", lambda p: p.endswith("/secret.txt"), lambda: httpx.Response(500))
        permission_sets = len(db.permissions)

        await sync(connector)

        assert "secret.txt" not in db.names() and len(db.permissions) == permission_sets

async def body_of(response: StreamingResponse) -> bytes:
    return b"".join([chunk async for chunk in response.body_iterator])


class TestDownloadAndReindex:
    async def test_a_nested_file_is_downloaded_from_its_folder(self, server, db, store) -> None:
        connector = await synced(server, db, store)

        response = await connector.stream_record(db.by_name("q1.pdf"))

        assert await body_of(response) == b"%PDF-1.7 quarterly"
        assert server.calls("GET", WEBDAV_PREFIX)[-1].url.path == f"{WEBDAV_PREFIX}alice/Docs/Reports/q1.pdf"
        assert await connector.get_signed_url(db.by_name("q1.pdf")) is None

    async def test_download_errors_are_explained(self, server, db, store) -> None:
        connector = await synced(server, db, store)
        record = db.by_name("notes.txt")
        server.delete("Docs/notes.txt")
        with pytest.raises(HTTPException) as gone:
            await connector.stream_record(record)
        assert gone.value.status_code == 404

        unknown = db.by_name("cat.png").model_copy(update={"id": "not-in-db"})
        with pytest.raises(HTTPException) as missing:
            await connector.stream_record(unknown)
        assert missing.value.status_code == 404

        server.outage("GET", lambda p: p.endswith("/cat.png"), lambda: httpx.ConnectError("refused"))
        with pytest.raises(HTTPException) as network:
            await connector.stream_record(db.by_name("cat.png"))
        assert network.value.status_code >= 500

        connector.data_source = None
        with pytest.raises(HTTPException) as not_ready:
            await connector.stream_record(db.by_name("cat.png"))
        assert not_ready.value.status_code == 409

    async def test_an_empty_file_downloads_as_an_empty_file(self, server, db, store) -> None:
        server.add_file("empty.txt", b"")
        connector = await make_connector(server, db, store)
        await sync(connector)

        response = await connector.stream_record(db.by_name("empty.txt"))

        assert response.status_code == 200
        assert await body_of(response) == b""

    async def test_a_file_record_that_cannot_be_read_is_not_reported_as_missing(self, server, db, store) -> None:
        connector = await synced(server, db, store)
        record = db.by_name("q1.pdf")
        db.fail_file_record_for.add(record.id)
        downloads = len(server.calls("GET", WEBDAV_PREFIX))

        with pytest.raises(HTTPException) as failed:
            await connector.stream_record(record)

        assert failed.value.status_code == 500
        assert "try again" in failed.value.detail
        assert len(server.calls("GET", WEBDAV_PREFIX)) == downloads

    @pytest.mark.parametrize("name", ["Docs", "Reports"])
    async def test_a_folder_download_is_refused(self, server, db, store, name) -> None:
        connector = await synced(server, db, store)
        downloads = len(server.calls("GET", WEBDAV_PREFIX))

        with pytest.raises(HTTPException) as folder:
            await connector.stream_record(db.by_name(name))

        assert (folder.value.status_code, folder.value.detail) == (400, "Cannot download folders")
        assert len(server.calls("GET", WEBDAV_PREFIX)) == downloads

    @pytest.mark.parametrize("name", ["report #1.txt", "why?.txt", "50% off.txt", "résumé ✓.txt", "a&b.txt"])
    async def test_files_with_special_characters_sync_and_download(self, server, db, store, name) -> None:
        connector = await synced(server, db, store)
        server.add_file(f"Docs/{name}", b"special")

        await sync(connector)

        assert db.path_of(name) == f"Docs/{name}"
        assert await body_of(await connector.stream_record(db.by_name(name))) == b"special"

    @pytest.mark.parametrize("path", ["Work/files/report.txt", "Work/files/2026/report.txt", "files/report.txt",
                                      "files/alice/report.txt"])
    async def test_a_file_under_a_folder_named_files_downloads(self, server, db, store, path) -> None:
        server.add_file("report.txt", b"a different file at the top level")
        server.add_file(path, b"the real report")
        connector = await make_connector(server, db, store)
        await sync(connector)
        record = next(r for r in db.records.values() if r.external_record_id == server.nodes[path].file_id)

        response = await connector.stream_record(record)

        assert await body_of(response) == b"the real report"
        assert server.calls("GET", WEBDAV_PREFIX)[-1].url.path == f"{WEBDAV_PREFIX}alice/{path}"

    async def test_reindex_refreshes_a_top_level_file(self, server, db, store) -> None:
        connector = await synced(server, db, store)
        server.change("readme.txt", b"v2")

        await connector.reindex_records([db.by_name("readme.txt")])

        assert [r.record_name for r in db.content_updates] == ["readme.txt"]
        assert db.by_name("readme.txt").external_revision_id.startswith(server.nodes["readme.txt"].etag)

    async def test_reindex_skips_what_it_cannot_read(self, server, db, store) -> None:
        connector = await synced(server, db, store)
        await connector.reindex_records([])
        gone = db.by_name("notes.txt")
        server.delete("Docs/notes.txt")
        unknown = db.by_name("cat.png").model_copy(update={"id": "not-in-db"})
        server.fail("PROPFIND", lambda p: p.endswith("/readme.txt"), httpx.Response(207, content=b""))
        server.outage("PROPFIND", lambda p: p.endswith("/cat.png"), lambda: httpx.ConnectError("reset"))

        await connector.reindex_records([gone, unknown, db.by_name("readme.txt"), db.by_name("cat.png"), db.by_name("Docs")])

        assert [r.record_name for r in db.content_updates] == ["Docs"], "one failure doesn't stop the rest"

    @pytest.mark.parametrize(("name", "path"), [("q1.pdf", "Docs/Reports/q1.pdf"), ("Reports", "Docs/Reports"),
                                                 ("notes.txt", "Docs/notes.txt")])
    async def test_reindex_keeps_a_nested_file_in_its_folder(self, server, db, store, name, path) -> None:
        connector = await synced(server, db, store)
        before = db.by_name(name)

        await connector.reindex_records([before])

        assert [r.record_name for r in db.content_updates] == [name]
        assert db.path_of(name) == path
        after = db.by_name(name)
        assert after.parent_external_record_id == before.parent_external_record_id
        assert after.external_revision_id == server.nodes[path].etag, "not mistaken for a move"
        assert db.path_of("q1.pdf") == "Docs/Reports/q1.pdf"


class TestHousekeeping:
    async def test_cleanup_releases_the_client_and_messaging(self, server, db, store) -> None:
        connector = await make_connector(server, db, store)
        db.messaging_producer = MagicMock(spec=["cleanup"], cleanup=AsyncMock(side_effect=RuntimeError("x")))

        await connector.cleanup()

        assert connector.data_source is None
        db.messaging_producer.cleanup.assert_awaited_once()
        db.messaging_producer = MagicMock(spec=["cleanup"], cleanup=AsyncMock())
        await connector.cleanup()
        db.messaging_producer = MagicMock(spec=["stop"], stop=AsyncMock(side_effect=RuntimeError("x")))
        await connector.cleanup()
        db.messaging_producer.stop.assert_awaited_once()
        db.messaging_producer = MagicMock(spec=[])
        await connector.cleanup()
        db.messaging_producer = None
        await connector.cleanup()
        assert connector.data_source is None

    async def test_unsupported_entry_points(self, server, db, store) -> None:
        connector = await NextcloudConnector.create_connector(
            logging.getLogger("t"), store, FakeConfigService(CONNECTOR_ID, auth_config(server)),
            CONNECTOR_ID, "personal", "creator-user-1", db,
        )
        assert isinstance(connector, NextcloudConnector)
        connector.handle_webhook_notification({"event": "x"})
        with pytest.raises(NotImplementedError):
            await connector.get_filter_options("file_extensions")


TRASH_ITEMS = ["report #1.txt.d1767225600", "why?.txt.d1767225600"]


def data_source_for(server: FakeNextcloud) -> NextcloudDataSource:
    return NextcloudDataSource(NextcloudClient(
        NextcloudRESTClientViaUsernamePassword(BASE, server.user, server.app_password)))


class TestTrashbinAddresses:
    @pytest.mark.parametrize("item", TRASH_ITEMS)
    async def test_restoring_a_trash_item_moves_that_item(self, server, item) -> None:
        await data_source_for(server).restore_trashbin_item("alice", item)

        request = server.requests[-1]
        assert request.method == "MOVE"
        assert request.url.path == f"/remote.php/dav/trashbin/alice/trash/{item}"
        assert httpx.URL(request.headers["destination"]).path == f"/remote.php/dav/trashbin/alice/restore/{item}"

    @pytest.mark.parametrize("item", TRASH_ITEMS)
    async def test_deleting_a_trash_item_deletes_that_item(self, server, item) -> None:
        await data_source_for(server).delete_trashbin_item("alice", item)

        request = server.requests[-1]
        assert request.method == "DELETE"
        assert request.url.path == f"/remote.php/dav/trashbin/alice/trash/{item}"


def runner_graph() -> AsyncMock:
    """The graph provider ``run_sync_task`` writes the app's status to; its unlink step strips
    the edges of every node a full sync did not write."""
    from app.config.constants.arangodb import AppStatus

    graph = AsyncMock()
    graph.get_document = AsyncMock(return_value={"id": CONNECTOR_ID, "status": AppStatus.FULL_SYNCING.value})
    graph.unlink_nodes_missed_by_full_sync = AsyncMock(return_value=(0, True))
    return graph


async def run_full_sync_task(connector: NextcloudConnector, graph: AsyncMock) -> None:
    """A full sync as the UI's Full Sync button (or a filter change) runs it: sync points gone, through run_sync_task."""
    from app.config.constants.arangodb import AppStatus
    from app.connectors.core.sync.sync_runner import run_sync_task

    await run_sync_task(
        connector, CONNECTOR_ID, graph, logging.getLogger("test.nextcloud.runner"),
        start_status=AppStatus.FULL_SYNCING.value,
    )


def idle_write(graph: AsyncMock) -> dict[str, Any]:
    from app.config.constants.arangodb import AppStatus

    writes = [c.args[2] for c in graph.update_node.await_args_list if c.args[2].get("status") == AppStatus.IDLE.value]
    assert len(writes) == 1, writes
    return writes[0]


class TestReviewFixes:
    """One or more tests per review finding; each fails on the code before the fix."""

    # ---- S1: only the creator reaches the app ----

    async def test_the_nextcloud_profile_email_never_gets_access(self, server, db, store) -> None:
        server.email = "bob@pipeshub.test"  # another PipesHub user's address, typed into the Nextcloud profile
        seed_drive(server)
        connector = await make_connector(server, db, store)

        assert await sync(connector)

        assert [u.email for u in db.app_users] == [CREATOR_EMAIL]

    async def test_a_creator_who_cannot_be_looked_up_fails_the_sync_before_any_write(self, server, db, store) -> None:
        store.users.clear()
        seed_drive(server)
        connector = await make_connector(server, db, store)

        assert not await sync(connector)

        assert db.app_users == [] and db.records == {} and store.cursor() is None

    # ---- S2 and S4: where requests may go ----

    @pytest.mark.parametrize(
        "base_url",
        [
            pytest.param("https://cloud.example.com/?", id="query"),
            pytest.param("https://cloud.example.com/nc#x", id="fragment"),
            pytest.param("https://user:pw@cloud.example.com", id="credentials"),
            pytest.param("ftp://cloud.example.com", id="not-http"),
            pytest.param("http://cloud.example.com", id="plain-http"),
            pytest.param("https://169.254.169.254", id="cloud-metadata"),
            pytest.param("https://127.0.0.1", id="loopback"),
            pytest.param("https://localhost", id="localhost"),
        ],
    )
    async def test_an_unsafe_server_url_is_refused_before_any_request(
        self, server, db, store, base_url, monkeypatch
    ) -> None:
        monkeypatch.delenv("PIPESHUB_NEXTCLOUD_ALLOW_INSECURE_HTTP", raising=False)
        monkeypatch.delenv("PIPESHUB_BLOCK_PRIVATE_ADDRESSES", raising=False)
        connector = build(server, db, store, config=auth_config(server, baseUrl=base_url))

        with pytest.raises(ConnectorInitError, match="Nextcloud server URL"):
            await connector.init()
        with pytest.raises(ConnectorInitError, match="Nextcloud server URL"):
            await connector.run_sync()

        assert connector.data_source is None
        assert server.requests == []

    async def test_plain_http_needs_the_operator_opt_in(self, server, db, store, monkeypatch) -> None:
        monkeypatch.setenv("PIPESHUB_NEXTCLOUD_ALLOW_INSECURE_HTTP", "true")
        seed_drive(server)
        connector = await make_connector(server, db, store, config=auth_config(server, baseUrl="http://cloud.example.com"))

        assert await sync(connector)

        assert "q1.pdf" in db.names()
        assert {r.url.scheme for r in server.requests} == {"http"}

    async def test_a_host_that_resolves_to_cloud_metadata_is_never_contacted(self, server, db, store) -> None:
        server.dns["cloud.example.com"] = "169.254.169.254"
        connector = build(server, db, store)

        await connector.init()
        assert not await sync(connector)

        assert server.requests == []

    async def test_a_private_server_works_unless_the_deployment_blocks_private_addresses(
        self, server, db, store, monkeypatch
    ) -> None:
        monkeypatch.delenv("PIPESHUB_BLOCK_PRIVATE_ADDRESSES", raising=False)
        server.dns["cloud.example.com"] = "10.20.30.40"
        seed_drive(server)
        assert await sync(await make_connector(server, db, store))
        assert "q1.pdf" in db.names()

        monkeypatch.setenv("PIPESHUB_BLOCK_PRIVATE_ADDRESSES", "true")
        server.requests.clear()
        connector = build(server, db, store)
        await connector.init()
        assert not await sync(connector)
        assert server.requests == []

    async def test_a_download_redirected_to_another_host_is_refused(self, server, db, store) -> None:
        connector = await synced(server, db, store)
        server.redirect_downloads_to = "http://169.254.169.254/latest/meta-data/"

        with pytest.raises(HTTPException) as refused:
            await connector.stream_record(db.by_name("q1.pdf"))

        assert refused.value.status_code >= 500
        assert not [r for r in server.requests if "meta-data" in r.url.path], "the redirect was never followed"

    # ---- S3: shares ----

    async def test_a_folder_shared_with_the_user_is_synced_and_removed_once_unshared(self, server, db, store) -> None:
        connector = await synced(server, db, store)

        server.share_in("SharedProj", {"plan.txt": b"shared plan"})
        assert await sync(connector)
        assert db.path_of("plan.txt") == "SharedProj/plan.txt"

        server.unshare("SharedProj")
        assert await sync(connector)
        assert not {"SharedProj", "plan.txt"} & db.names()
        assert store.cursor() == str(server.latest_activity_id)

    # ---- S6: remote wipe ----

    async def test_a_remote_wipe_removes_everything_synced_and_is_confirmed(self, server, db, store) -> None:
        connector = await synced(server, db, store)
        notifications = listen_for_notifications(connector)
        server.wipe_requested = True

        with pytest.raises(ConnectorInitError, match="remote wipe"):
            await connector.run_sync()

        assert db.records == {}
        assert server.wipe_confirmed
        assert not store.cursor(), "a new app password starts with a full listing"
        sent = await notifications()
        assert any("synced Nextcloud data removed" in n["title"] and connector.display_name in n["title"] for n in sent)

    async def test_a_rejected_password_without_a_wipe_keeps_the_records(self, server, db, store) -> None:
        connector = await synced(server, db, store)
        kept = set(db.records)
        server.app_password = "revoked"

        with pytest.raises(ConnectorInitError, match="rejected the app password"):
            await connector.run_sync()

        assert set(db.records) == kept and not server.wipe_confirmed

    # ---- S7: defusedxml ----

    def test_an_answer_with_entity_declarations_is_refused(self) -> None:
        hostile = (
            b'<?xml version="1.0"?><!DOCTYPE d [<!ENTITY x "boom">]>'
            b'<d:multistatus xmlns:d="DAV:" xmlns:oc="http://owncloud.org/ns"><d:response>'
            b'<d:href>/remote.php/dav/files/alice/&x;.txt</d:href><d:propstat><d:prop>'
            b'<oc:fileid>1</oc:fileid></d:prop></d:propstat></d:response></d:multistatus>'
        )

        assert nextcloud_connector.parse_webdav_propfind_response(hostile) == []
        assert nextcloud_connector.is_multistatus(hostile) is False

    # ---- B1: a full sync that fails unlinks nothing; the next one heals ----

    async def test_a_ui_full_sync_that_cannot_list_the_drive_unlinks_nothing(self, server, db, store) -> None:
        connector = await synced(server, db, store)
        store.sync_points.clear()  # what the Full Sync button and a filter change do first
        server.outage("PROPFIND", lambda p: True, lambda: httpx.Response(503))  # maintenance mode
        graph = runner_graph()

        with pytest.raises(NextcloudSyncError):
            await run_full_sync_task(connector, graph)

        graph.unlink_nodes_missed_by_full_sync.assert_not_awaited()
        assert idle_write(graph).get("pendingFullSync") is True, "a full sync stays owed"
        assert {"q1.pdf", "notes.txt", "cat.png", "readme.txt"} <= db.names()

    async def test_a_ui_full_sync_rewrites_every_record_so_earlier_unlinks_heal(self, server, db, store) -> None:
        connector = await synced(server, db, store)
        written = len(db.batches)
        store.sync_points.clear()
        graph = runner_graph()

        await run_full_sync_task(connector, graph)

        graph.unlink_nodes_missed_by_full_sync.assert_awaited_once()
        seen = graph.unlink_nodes_missed_by_full_sync.await_args.args[1]
        assert {r.id for r in db.records.values()} <= set(seen["records"])
        rewritten = {name for batch in db.batches[written:] for name in batch}
        assert {"q1.pdf", "notes.txt", "cat.png", "readme.txt"} <= rewritten, "unchanged records are written again"

        # The daily reconcile that follows stays cheap: unchanged records are only noted.
        written = len(db.batches)
        await connector._run_full_sync_internal()
        assert len(db.batches) == written

    # ---- B2: changes during the full sync ----

    async def test_a_change_made_while_the_full_sync_runs_is_applied_next(self, server, db, store) -> None:
        seed_drive(server)
        connector = await make_connector(server, db, store)
        listing = connector._sync_user_files

        async def listing_then_upload(*args: Any, **kwargs: Any) -> bool:  # noqa: ANN401
            done = await listing(*args, **kwargs)
            server.add_file("during.txt", b"uploaded mid-sync")
            return done

        connector._sync_user_files = listing_then_upload
        assert await sync(connector)
        connector._sync_user_files = listing
        assert "during.txt" not in db.names()

        assert await sync(connector)
        assert "during.txt" in db.names()

    # ---- B3: what the feed never reports ----

    async def test_the_daily_full_listing_finds_what_the_feed_missed(self, server, db, store) -> None:
        connector = await synced(server, db, store)
        server.add_file("scanned.txt", b"written on disk", log=False)  # occ files:scan logs nothing
        assert await sync(connector)
        assert "scanned.txt" not in db.names()

        store.checkpoint()["last_full_sync_ms"] -= nextcloud_connector.FULL_SYNC_INTERVAL_MS
        assert await sync(connector)
        assert "scanned.txt" in db.names()

    async def test_a_copied_folder_is_saved_with_what_it_holds(self, server, db, store) -> None:
        connector = await synced(server, db, store)

        server.copy("Docs", "DocsCopy")
        assert await sync(connector)

        copied = {r.record_name for r in db.records.values()
                  if r.external_record_id in {n.file_id for p, n in server.nodes.items() if p.startswith("DocsCopy")}}
        assert copied == {"DocsCopy", "Reports", "q1.pdf", "notes.txt"}

    # ---- B4: a change failing past five runs is not dropped ----

    async def test_a_change_that_fails_five_runs_is_retried_until_it_applies(self, server, db, store) -> None:
        connector = await synced(server, db, store)
        notifications = listen_for_notifications(connector)
        server.change("Docs/notes.txt", b"v2")
        outage = server.outage("PROPFIND", lambda p: p.endswith("/Docs/notes.txt"), lambda: httpx.Response(503))

        for _ in range(MAX_HELD_ATTEMPTS):
            await sync(connector)
        assert store.checkpoint()["pending_changes"] == ["/Docs/notes.txt"]
        assert store.cursor() == str(server.latest_activity_id), "later changes are not held up"
        assert any(n["type"] == NotificationType.CONNECTOR_SYNC_ERROR for n in await notifications())

        outage.end()
        assert await sync(connector)
        assert db.by_name("notes.txt").external_revision_id == server.nodes["Docs/notes.txt"].etag
        assert store.checkpoint()["pending_changes"] == []

    # ---- B5: failures are reported ----

    async def test_a_failed_activity_fetch_fails_the_run(self, server, db, store) -> None:
        connector = await synced(server, db, store)
        server.change("Docs/notes.txt", b"v2")
        server.outage("GET", lambda p: p == ACTIVITY_PATH, lambda: httpx.Response(503))

        with pytest.raises(NextcloudSyncError, match="Failed to fetch Nextcloud activities"):
            await connector.run_sync()

    async def test_a_full_sync_that_cannot_list_fails_the_run(self, server, db, store) -> None:
        seed_drive(server)
        connector = await make_connector(server, db, store)
        server.outage("PROPFIND", lambda p: True, lambda: httpx.Response(503))

        with pytest.raises(NextcloudSyncError, match="could not be read or saved in full"):
            await connector.run_sync()

    async def test_a_disabled_activity_app_is_reported_once_and_the_drive_listed_instead(self, server, db, store) -> None:
        connector = await synced(server, db, store)
        notifications = listen_for_notifications(connector)
        server.activity_enabled = False
        server.add_file("new.txt", b"new")

        assert await sync(connector)
        assert await sync(connector)

        assert "new.txt" in db.names()
        warned = [n for n in await notifications() if "Activity app" in n["title"]]
        assert len(warned) == 1 and connector.display_name in warned[0]["title"]

    # ---- B6: never a namesake's bytes ----

    async def test_a_file_whose_folder_link_is_broken_still_downloads_its_own_bytes(self, server, db, store) -> None:
        server.add_file("report.pdf", b"the top-level report")
        server.add_file("Projects/report.pdf", b"the projects report")
        connector = await make_connector(server, db, store)
        assert await sync(connector)
        record = db.records[server.nodes["Projects/report.pdf"].file_id]
        db.edges.pop(record.id)  # the parent link went missing
        db.unreadable_paths.add(record.id)

        response = await connector.stream_record(record)

        assert await body_of(response) == b"the projects report"

    async def test_without_an_id_lookup_a_partial_path_is_not_downloaded(self, server, db, store) -> None:
        server.add_file("report.pdf", b"the top-level report")
        server.add_file("Projects/report.pdf", b"the projects report")
        connector = await make_connector(server, db, store)
        assert await sync(connector)
        server.search_supported = False
        record = db.records[server.nodes["Projects/report.pdf"].file_id]
        db.unreadable_paths.add(record.id)
        downloads = len(server.calls("GET", WEBDAV_PREFIX))

        with pytest.raises(HTTPException):
            await connector.stream_record(record)

        assert len(server.calls("GET", WEBDAV_PREFIX)) == downloads

    # ---- B7: login name is not the user id ----

    async def test_an_email_login_syncs_the_users_own_files(self, server, db, store) -> None:
        server.login = "alice@example.com"
        seed_drive(server)
        connector = await make_connector(server, db, store, config=auth_config(server, username="alice@example.com"))

        assert await sync(connector)

        assert connector.current_user_id == "alice"
        assert "q1.pdf" in db.names()
        assert all(unquote(r.url.path).startswith(f"{WEBDAV_PREFIX}alice/") for r in server.calls("PROPFIND"))

    # ---- B9 ----

    def test_the_created_date_filter_is_not_offered(self) -> None:
        fields = NextcloudConnector._connector_metadata["config"]["filters"]["sync"]["schema"]["fields"]

        assert "created" not in {f["name"] for f in fields}
        assert "modified" in {f["name"] for f in fields}

    # ---- B10 ----

    async def test_a_file_renamed_to_an_excluded_type_leaves_the_index(self, server, db, store) -> None:
        seed_drive(server)
        connector = await make_connector(server, db, store, filters={
            "file_extensions": {"type": "multiselect", "operator": "not_in", "value": ["txt"]}})
        server.add_file("draft.md", b"draft")
        assert await sync(connector)
        assert "draft.md" in db.names()

        server.move("draft.md", "draft.txt")
        assert await sync(connector)

        assert not {"draft.md", "draft.txt"} & db.names()

    # ---- P2 ----

    async def test_cleanup_closes_the_http_client(self, server, db, store) -> None:
        connector = await synced(server, db, store)
        http = connector.data_source.client.client

        await connector.cleanup()

        assert http.is_closed

    # ---- P3 ----

    async def test_a_full_sync_reads_stored_records_once_not_per_file(self, server, db, store) -> None:
        seed_drive(server)
        connector = await make_connector(server, db, store)

        assert await sync(connector)

        assert db.lookups == 0, "no single-record lookup per listed entry"
        assert db.edge_deletes == 0, "a new record has no parent link to delete"

    # ---- O1 ----

    async def test_a_move_keeps_the_real_etag_and_is_not_reindexed(self, server, db, store) -> None:
        connector = await synced(server, db, store)

        server.move("Docs/notes.txt", "Photos/notes.txt")
        assert await sync(connector)

        notes = db.by_name("notes.txt")
        assert notes.external_revision_id == server.nodes["Photos/notes.txt"].etag
        assert db.moves == ["notes.txt"], "saved in place, keeping its indexing state"
        assert db.path_of("notes.txt") == "Photos/notes.txt"

    # ---- O3 ----

    async def test_the_batch_size_setting_is_used(self, server, db, store) -> None:
        seed_drive(server)
        config = {**auth_config(server), "sync": {"batchSize": "2"}}
        connector = await make_connector(server, db, store, config=config)

        assert await sync(connector)

        assert connector.batch_size == 2
        assert max(len(b) for b in db.batches) == 2


def due_for_full_listing(store: FakeStore) -> None:
    store.checkpoint()["last_full_sync_ms"] -= nextcloud_connector.FULL_SYNC_INTERVAL_MS


class TestChecklistAudit:
    """Findings of the connector checklist audit, each failing on the code before its fix."""

    # ---- an app password without filesystem access: the drive looks empty ----

    async def test_an_app_password_without_filesystem_access_removes_nothing(self, server, db, store) -> None:
        connector = await synced(server, db, store)
        kept, cursor = db.names(), store.cursor()
        notifications = listen_for_notifications(connector)
        server.filesystem_access = False
        due_for_full_listing(store)

        with pytest.raises(NextcloudNoFilesystemAccessError, match="Allow filesystem access"):
            await connector.run_sync()

        assert db.names() == kept and db.deleted == [], "an unreadable home is not an empty drive"
        assert store.cursor() == cursor
        sent = await notifications()
        assert [n["type"] for n in sent] == [NotificationType.CONNECTOR_AUTH_ERROR], "told once, with the fix"
        assert connector.display_name in sent[0]["title"]

    async def test_the_connection_test_refuses_an_app_password_without_filesystem_access(self, server, db, store) -> None:
        connector = await make_connector(server, db, store)
        server.filesystem_access = False

        with pytest.raises(NextcloudNoFilesystemAccessError, match="Allow filesystem access"):
            await connector.test_connection_and_access()

    # ---- a mount whose storage is unavailable: Nextcloud lists it as an empty folder with id -1 ----

    async def test_what_was_synced_from_an_unavailable_mount_is_kept(self, server, db, store) -> None:
        server.add_file("Mount/Deep/inside.txt", b"on the mount")
        connector = await synced(server, db, store)
        notifications = listen_for_notifications(connector)
        server.unavailable.add("Mount")
        server.change("readme.txt", b"still synced")
        due_for_full_listing(store)

        assert await sync(connector)

        assert {"Mount", "Deep", "inside.txt"} <= db.names(), "nothing below the mount is removed"
        assert "-1" not in db.records, "no record is keyed by Nextcloud's -1"
        assert db.by_name("readme.txt").external_revision_id == server.nodes["readme.txt"].etag
        warned = [n for n in await notifications() if "could not be read" in n["title"]]
        assert len(warned) == 1 and "/Mount" in warned[0]["message"]
        assert connector.display_name in warned[0]["title"]

    async def test_a_mount_left_out_of_the_listing_after_an_outage_is_kept(self, server, db, store) -> None:
        server.add_file("Mount/Deep/inside.txt", b"on the mount")
        connector = await synced(server, db, store)
        notifications = listen_for_notifications(connector)
        server.unavailable.add("Mount")
        server.left_out.add("Mount")
        due_for_full_listing(store)

        assert await sync(connector)

        assert {"Mount", "Deep", "inside.txt"} <= db.names(), "left out is not deleted: its path still answers"
        warned = [n for n in await notifications() if "could not be read" in n["title"]]
        assert len(warned) == 1 and "Mount" in warned[0]["message"]

    async def test_a_folder_deleted_in_nextcloud_still_leaves_at_the_full_listing(self, server, db, store) -> None:
        server.add_file("Gone/inside.txt", b"deleted later")
        connector = await synced(server, db, store)
        server.delete("Gone")
        due_for_full_listing(store)

        assert await sync(connector)

        assert not {"Gone", "inside.txt"} & db.names()

    async def test_an_unavailable_mount_is_read_again_once_it_is_back(self, server, db, store) -> None:
        server.add_file("Mount/inside.txt", b"on the mount")
        connector = await synced(server, db, store)
        server.unavailable.add("Mount")
        due_for_full_listing(store)
        assert await sync(connector)

        server.unavailable.clear()
        server.add_file("Mount/later.txt", b"written while it was down", log=False)
        due_for_full_listing(store)
        assert await sync(connector)

        assert {"inside.txt", "later.txt"} <= db.names()

    async def test_a_first_sync_skips_an_unavailable_mount_and_saves_the_rest(self, server, db, store) -> None:
        seed_drive(server)
        server.add_file("Mount/inside.txt")
        server.unavailable.add("Mount")
        connector = await make_connector(server, db, store)

        assert await sync(connector)

        assert {"q1.pdf", "notes.txt", "cat.png", "readme.txt"} <= db.names()
        assert "-1" not in db.records and "Mount" not in db.names()

    async def test_a_folder_nextcloud_will_not_list_does_not_fail_the_whole_full_sync(self, server, db, store) -> None:
        connector = await synced(server, db, store)
        server.change("readme.txt", b"v2")
        server.outage("PROPFIND", lambda p: p.rstrip("/").endswith("/Docs"), lambda: httpx.Response(500))
        due_for_full_listing(store)

        assert await sync(connector)

        assert {"Docs", "Reports", "q1.pdf", "notes.txt"} <= db.names(), "what was under it is kept"
        assert db.by_name("readme.txt").external_revision_id == server.nodes["readme.txt"].etag

    # ---- only the creator is linked to the app ----

    async def test_a_full_sync_unlinks_a_user_an_older_worker_linked_after_the_migration(self, server, db, store) -> None:
        connector = await synced(server, db, store)
        # What an older version's worker, still running during a rolling upgrade, writes again.
        await db.on_new_app_users([AppUser(
            app_name=connector.connector_name, connector_id=CONNECTOR_ID, source_user_id="alice",
            email="alice@example.com", full_name="Alice", org_id=db.org_id, is_active=True,
        )])
        due_for_full_listing(store)

        assert await sync(connector)

        assert [u.email for u in db.app_users] == [CREATOR_EMAIL], "the profile email's owner loses the drive"

    # ---- a folder that moves or is deleted between its parent's listing and its own ----

    @staticmethod
    def edit_when_listed(server: FakeNextcloud, path: str, edit: Callable[[], object]) -> None:
        """Make ``edit`` happen just before the walk lists ``path``, which then answers 404 as Nextcloud does."""
        def edit_then_404() -> httpx.Response:
            fault.end()
            edit()
            return httpx.Response(404, content=b"<d:error xmlns:d='DAV:'/>")

        fault = server.outage("PROPFIND", lambda p: p.rstrip("/").endswith(f"/{server.user}/{path}"), edit_then_404)

    async def test_a_folder_renamed_while_the_full_listing_runs_keeps_its_contents(self, server, db, store) -> None:
        connector = await synced(server, db, store)
        inside = {db.by_name(n).id for n in ("Docs", "Reports", "q1.pdf", "notes.txt")}
        self.edit_when_listed(server, "Docs", lambda: server.move("Docs", "Papers"))
        due_for_full_listing(store)

        assert await sync(connector)

        assert {r.id for r in db.records.values()} >= inside, "a 404 at the old path is not a deletion"
        assert db.deleted == []
        assert db.path_of("q1.pdf") == "Papers/Reports/q1.pdf", "listed and saved where it now is"
        assert await sync(connector)
        assert {r.id for r in db.records.values()} >= inside
        assert db.path_of("notes.txt") == "Papers/notes.txt"

    async def test_a_folder_that_moved_while_listed_is_not_listed_twice(self, server, db, store) -> None:
        server.add_folder("Photos/Album")
        connector = await synced(server, db, store)
        inside = {db.by_name(n).id for n in ("Docs", "Reports", "q1.pdf", "notes.txt")}
        self.edit_when_listed(server, "Docs", lambda: server.move("Docs", "Photos/Album/Docs"))
        due_for_full_listing(store)

        assert await sync(connector)

        assert {r.id for r in db.records.values()} >= inside and db.deleted == []
        assert db.path_of("q1.pdf") == "Photos/Album/Docs/Reports/q1.pdf"
        moved = [r for r in server.calls("PROPFIND") if unquote(r.url.path).rstrip("/").endswith("/Photos/Album/Docs")]
        assert len(moved) == 1, "found again in the folder it moved to, it is not walked a second time"

    async def test_a_folder_that_moved_while_listed_and_cannot_be_looked_up_is_kept(self, server, db, store) -> None:
        connector = await synced(server, db, store)
        notifications = listen_for_notifications(connector)
        inside = {db.by_name(n).id for n in ("Docs", "Reports", "q1.pdf", "notes.txt")}
        server.search_supported = False
        self.edit_when_listed(server, "Docs", lambda: server.move("Docs", "Papers"))
        due_for_full_listing(store)

        assert await sync(connector)

        assert {r.id for r in db.records.values()} >= inside and db.deleted == []
        warned = [n for n in await notifications() if "could not be read" in n["title"]]
        assert len(warned) == 1 and "/Docs" in warned[0]["message"]

    async def test_a_folder_deleted_while_the_full_listing_runs_leaves_with_its_contents(self, server, db, store) -> None:
        connector = await synced(server, db, store)
        self.edit_when_listed(server, "Docs", lambda: server.delete("Docs"))
        due_for_full_listing(store)

        assert await sync(connector)

        assert not {"Docs", "Reports", "q1.pdf", "notes.txt"} & db.names(), "confirmed gone by its ID"
        assert {"cat.png", "readme.txt"} <= db.names()

    async def test_a_change_inside_an_unavailable_mount_is_held_not_saved_as_minus_one(self, server, db, store) -> None:
        server.add_file("Mount/inside.txt", b"v1")
        connector = await synced(server, db, store)
        server.change("Mount/inside.txt", b"v2")
        server.unavailable.add("Mount")

        assert not await sync(connector)

        assert "-1" not in db.records
        assert store.cursor() != str(server.latest_activity_id), "held for the next run"

    # ---- the activity feed: 200 a page, grouped rows ----

    async def test_the_cursor_follows_the_last_row_nextcloud_gave(self, server, db, store) -> None:
        connector = await synced(server, db, store)
        server.add_file("one.txt")
        server.add_file("two.txt")
        last_row = server.latest_activity_id + 7  # rows Nextcloud grouped into the activities it returned
        real = server._activity

        def grouped(params: httpx.QueryParams) -> httpx.Response:
            response = real(params)
            if response.status_code == 200:
                response.headers["X-Activity-Last-Given"] = str(last_row)
            return response

        server._activity = grouped
        assert await sync(connector)

        assert store.cursor() == str(last_row)

    # ---- a failed sync is reported ----

    async def test_a_sync_that_fails_is_reported_with_nextclouds_reason(self, server, db, store) -> None:
        seed_drive(server)
        connector = await make_connector(server, db, store)
        notifications = listen_for_notifications(connector)
        server.outage("PROPFIND", lambda p: True, lambda: httpx.Response(503, content=(
            b'<d:error xmlns:d="DAV:" xmlns:s="http://sabredav.org/ns">'
            b"<s:message>System in maintenance mode.</s:message></d:error>")))

        with pytest.raises(NextcloudSyncError):
            await connector.run_sync()

        sent = await notifications()
        assert [n["type"] for n in sent] == [NotificationType.CONNECTOR_SYNC_ERROR]
        assert connector.display_name in sent[0]["title"]

    async def test_changes_held_for_a_retry_are_not_reported_as_a_failed_sync(self, server, db, store) -> None:
        connector = await synced(server, db, store)
        notifications = listen_for_notifications(connector)
        server.change("Docs/notes.txt", b"v2")
        server.outage("PROPFIND", lambda p: p.endswith("/Docs/notes.txt"), lambda: httpx.Response(503))

        assert not await sync(connector)

        assert await notifications() == [], "a retry the next run makes is not a failure to report yet"

    # ---- logs carry Nextcloud's reason, never the app password ----

    async def test_a_failed_listing_logs_nextclouds_reason_and_no_secret(self, server, db, store) -> None:
        seed_drive(server)
        connector = await make_connector(server, db, store)
        logs = capture_logs(connector)
        server.outage("PROPFIND", lambda p: True, lambda: httpx.Response(503, content=(
            b'<d:error xmlns:d="DAV:" xmlns:s="http://sabredav.org/ns">'
            b"<s:message>System in maintenance mode.</s:message></d:error>")))

        with pytest.raises(NextcloudSyncError):
            await connector.run_sync()

        text = "\n".join(r.getMessage() for r in logs)
        assert "HTTP 503: System in maintenance mode." in text
        assert server.app_password not in text
        assert base64.b64encode(f"{server.user}:{server.app_password}".encode()).decode() not in text

    # ---- created time: Nextcloud has one only when the uploading client sent it ----

    async def test_the_created_time_stays_put_when_a_file_is_edited(self, server, db, store) -> None:
        connector = await synced(server, db, store)
        created = db.by_name("readme.txt").source_created_at
        server.change("readme.txt", b"edited")

        assert await sync(connector)

        record = db.by_name("readme.txt")
        assert record.source_updated_at > created
        assert record.source_created_at == record.created_at == created

    async def test_the_creation_time_a_client_sent_is_used(self, server, db, store) -> None:
        server.add_file("dated.txt").created = 1_600_000_000
        await synced(server, db, store)

        assert db.by_name("dated.txt").source_created_at == 1_600_000_000_000

    # ---- a file shared with download disabled: Nextcloud lists it but refuses its content ----

    async def test_a_file_shared_with_download_disabled_is_not_synced(self, server, db, store) -> None:
        server.add_file("Shared/view-only.txt", b"stays in Nextcloud").download = False
        await synced(server, db, store)

        assert "view-only.txt" not in db.names()
        assert "Shared" in db.names()

    async def test_a_file_whose_download_is_turned_off_leaves_the_index(self, server, db, store) -> None:
        server.add_file("Shared/later.txt", b"downloadable at first")
        connector = await synced(server, db, store)
        assert "later.txt" in db.names()
        server.nodes["Shared/later.txt"].download = False
        due_for_full_listing(store)

        assert await sync(connector)

        assert "later.txt" not in db.names()

    @pytest.mark.parametrize("attributes, disabled", [
        ('[{"scope":"permissions","key":"download","value":false}]', True),
        ('[{"scope":"permissions","key":"download","value":true}]', False),
        # Before Nextcloud 30 the attribute's flag was "enabled".
        ('[{"scope":"permissions","key":"download","enabled":false}]', True),
        ('[{"scope":"permissions","key":"download","enabled":true}]', False),
        ("[]", False),
        ("not json", False),
    ])
    def test_download_disabled_reads_the_share_attributes(self, attributes, disabled) -> None:
        assert nextcloud_connector.download_disabled(attributes) is disabled

    async def test_no_nextcloud_session_cookie_is_sent_back(self, server, db, store) -> None:
        """Nextcloud keeps a session's mounts: in a session, an offline mount is a 404 rather than id -1."""
        await synced(server, db, store)

        assert len(server.requests) > 1
        assert not [r for r in server.requests if "cookie" in r.headers]

    # ---- a rejected app password is not sent again: Nextcloud throttles the whole address ----

    async def test_after_a_401_neither_sync_nor_preview_sends_the_password_again(self, server, db, store) -> None:
        connector = await synced(server, db, store)
        record = db.by_name("readme.txt")
        server.app_password = "revoked"
        with pytest.raises(ConnectorInitError, match="rejected the app password"):
            await connector.run_sync()
        sent = len(server.requests)

        with pytest.raises(ConnectorInitError, match="rejected the app password"):
            await connector.run_sync()
        with pytest.raises(HTTPException) as refused:
            await connector.stream_record(record)

        assert len(server.requests) == sent, "nothing more reaches Nextcloud with the rejected password"
        assert refused.value.status_code == 409, "a Nextcloud 401 never signs the user out of PipesHub"

    async def test_a_401_seen_while_streaming_stops_the_next_sync_too(self, server, db, store) -> None:
        connector = await synced(server, db, store)
        server.app_password = "revoked"
        with pytest.raises(HTTPException):
            await connector.stream_record(db.by_name("readme.txt"))
        notifications = listen_for_notifications(connector)

        with pytest.raises(ConnectorInitError, match="rejected the app password"):
            await connector.run_sync()

        assert [n["type"] for n in await notifications()] == [NotificationType.CONNECTOR_AUTH_ERROR]

    async def test_a_401_seen_while_a_sync_runs_is_reported_as_the_rejected_password(self, server, db, store) -> None:
        """Nextcloud throttles an address after failed sign-ins and answers 429 to everything from it.

        A preview or indexing fetch that meets the 401 while the sync runs marks the password
        rejected (as ``stream_record`` does); the sync then sees only 429s.
        """
        connector = await synced(server, db, store)
        kept = set(db.records)
        server.app_password = "revoked"

        def throttled() -> httpx.Response:
            connector.app_password_rejected = True
            return httpx.Response(429, content=b'{"message":"Reached maximum delay"}')

        for method in ("GET", "PROPFIND", "SEARCH", "REPORT"):
            server.outage(method, lambda p: True, throttled)
        notifications = listen_for_notifications(connector)

        with pytest.raises(ConnectorInitError, match="rejected the app password"):
            await connector.run_sync()

        sent = await notifications()
        assert [n["type"] for n in sent] == [NotificationType.CONNECTOR_AUTH_ERROR], "one notice, naming the password"
        assert "app password rejected" in sent[0]["title"]
        assert set(db.records) == kept, "nothing is removed"

    async def test_a_wipe_check_nextcloud_does_not_answer_is_asked_again_by_the_next_sync(
        self, server, db, store
    ) -> None:
        """A throttled address gets 429 for the wipe check too; that is no answer, so a requested wipe still runs."""
        connector = await synced(server, db, store)
        server.wipe_requested = True
        throttled = server.outage("POST", lambda p: p.endswith("/core/wipe/check"), lambda: httpx.Response(429, content=b"{}"))

        with pytest.raises(ConnectorInitError, match="rejected the app password"):
            await connector.run_sync()
        assert db.records and not server.wipe_confirmed
        throttled.end()

        with pytest.raises(ConnectorInitError, match="remote wipe"):
            await connector.run_sync()

        assert db.records == {} and server.wipe_confirmed

    async def test_a_password_nextcloud_has_not_flagged_is_not_asked_about_again(self, server, db, store) -> None:
        connector = await synced(server, db, store)
        server.app_password = "revoked"
        for _ in range(2):
            with pytest.raises(ConnectorInitError, match="rejected the app password"):
                await connector.run_sync()

        assert len(server.calls("POST", "/index.php/core/wipe/check")) == 1, "a 404 is an answer: no wipe"

    async def test_an_unfinished_remote_wipe_is_asked_again_by_the_next_sync(self, server, db, store) -> None:
        connector = await synced(server, db, store)
        server.wipe_requested = True
        failing = {"once": True}

        async def remove_but_fail_once(record_id: str, *args: Any, **kwargs: Any) -> None:  # noqa: ANN401
            if failing.pop("once", False):
                raise RuntimeError("store unavailable")
            await original(record_id=record_id)

        original = connector.data_entities_processor.on_record_deleted
        connector.data_entities_processor.on_record_deleted = remove_but_fail_once
        with pytest.raises(ConnectorInitError, match="remote wipe"):
            await connector.run_sync()
        assert db.records and not server.wipe_confirmed

        with pytest.raises(ConnectorInitError, match="remote wipe"):
            await connector.run_sync()

        assert db.records == {} and server.wipe_confirmed

    # ---- manual indexing ----

    async def test_with_manual_indexing_on_files_are_synced_but_not_indexed(self, server, db, store) -> None:
        seed_drive(server)
        manual = {"indexing": {"values": {"enable_manual_sync": {"operator": "is", "type": "boolean", "value": True}}}}
        connector = await make_connector(server, db, store, config={**auth_config(server), "filters": manual})

        assert await sync(connector)

        files = [r for r in db.records.values() if r.is_file]
        assert files and {r.indexing_status for r in files} == {ProgressStatus.AUTO_INDEX_OFF.value}
        assert all(r.indexing_status != ProgressStatus.AUTO_INDEX_OFF.value for r in db.records.values() if not r.is_file)

    def test_the_manual_indexing_switch_is_declared(self) -> None:
        fields = NextcloudConnector._connector_metadata["config"]["filters"]["indexing"]["schema"]["fields"]
        assert [f["name"] for f in fields] == ["enable_manual_sync"]
