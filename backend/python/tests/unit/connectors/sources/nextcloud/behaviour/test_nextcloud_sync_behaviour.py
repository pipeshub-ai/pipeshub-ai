"""Nextcloud sync, driven end to end over a fake Nextcloud server.

The connector, its Nextcloud client, the WebDAV/OCS request builder and the
HTTP client (with whatever transport it configures) are all real; the Nextcloud
server is an in-memory file tree answering at httpx's network hop, and our
databases are in-memory fakes.
"""

import asyncio
import base64
import logging
from datetime import datetime, timezone
from typing import Any, Optional
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from aiolimiter import AsyncLimiter
from fastapi import HTTPException
from fastapi.responses import StreamingResponse
from nextcloud_behaviour_fakes import (
    ACTIVITY_PATH,
    CAPABILITIES_PATH,
    SHARES_PATH,
    USERS_PREFIX,
    WEBDAV_PREFIX,
    FakeConfigService,
    FakeNextcloud,
    FakeRecordsDb,
    FakeStore,
)

from app.config.constants.arangodb import MimeTypes
from app.connectors.sources.microsoft.common.msgraph_client import RecordUpdate
from app.connectors.sources.nextcloud.connector import NextcloudConnector
from app.models.entities import FileRecord
from app.models.permission import EntityType, PermissionType
from app.sources.client.http.http_client import HTTPClient
from app.sources.client.http.http_resilient_transport import ResilientHTTPTransport
from app.sources.client.nextcloud.nextcloud import (
    NextcloudClient,
    NextcloudRESTClientViaUsernamePassword,
)
from app.sources.client.resilience import ResiliencePolicy
from app.sources.external.nextcloud.nextcloud import NextcloudDataSource

CONNECTOR_ID = "nextcloud-1"
BASE = "https://cloud.example.com"
MADE_UP_DOMAIN = "@nextcloud.local"
ALICE_OWNER = [(EntityType.USER, PermissionType.OWNER, "alice@example.com")]
BAD_ANSWERS = [
    pytest.param(httpx.Response(503), id="server-error"),
    pytest.param(httpx.Response(207, content=b""), id="empty-body"),
    pytest.param(httpx.Response(207, content=b"<d:multistatus"), id="garbled-xml"),
    pytest.param(httpx.Response(207, content=b'<d:multistatus xmlns:d="DAV:"/>'), id="no-entries"),
    pytest.param(httpx.ConnectError("connection reset"), id="network-error"),
]


@pytest.fixture
def server(monkeypatch: pytest.MonkeyPatch) -> FakeNextcloud:
    fake = FakeNextcloud()
    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", fake.network())
    return fake


@pytest.fixture
def db() -> FakeRecordsDb:
    return FakeRecordsDb()


@pytest.fixture
def store() -> FakeStore:
    return FakeStore()


@pytest.fixture(autouse=True)
def no_real_pauses(monkeypatch: pytest.MonkeyPatch) -> None:
    """The connector pauses between batches; the pause is kept but made instant."""
    real_sleep = asyncio.sleep

    async def _sleep(delay: float, *args: object, **kwargs: object) -> None:
        await real_sleep(0)

    monkeypatch.setattr("app.connectors.sources.nextcloud.connector.asyncio.sleep", _sleep)


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


def seed_drive(server: FakeNextcloud) -> None:
    server.add_file("Docs/Reports/q1.pdf", b"%PDF-1.7 quarterly", "application/pdf")
    server.add_file("Docs/notes.txt", b"meeting notes")
    server.add_file("Photos/cat.png", b"\x89PNG", "image/png")
    server.add_file("readme.txt", b"top level")


def owners(db: FakeRecordsDb, name: str) -> list[tuple[str, str, str]]:
    return [(p.entity_type, p.type, p.email) for p in db.permissions[db.by_name(name).external_record_id]]


def ids_of(server: FakeNextcloud) -> dict[str, str]:
    return {node.name: node.file_id for node in server.nodes.values() if node.path}


async def synced(server: FakeNextcloud, db: FakeRecordsDb, store: FakeStore) -> NextcloudConnector:
    """A connector that has finished its first full sync and anchored its activity cursor."""
    seed_drive(server)
    connector = await make_connector(server, db, store)
    await connector.run_sync()
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
        assert [r.url.path for r in server.requests] == [f"{USERS_PREFIX}alice"], "init reads the user over HTTP"

    async def test_the_production_client_is_built_without_a_retrying_transport(self, server, db, store) -> None:
        connector = await make_connector(server, db, store)

        http = connector.data_source.client
        assert http.resilience is None
        assert type(http.client._transport) is httpx.AsyncHTTPTransport, "a plain transport: no retries on 429"

    async def test_the_fake_server_keeps_a_retrying_transport_working(self, server) -> None:
        """Guards the fake: a client that does configure retries must see them run for real."""
        server.add_file("notes.txt")
        http = NextcloudRESTClientViaUsernamePassword(BASE, server.user, server.app_password)
        http.resilience = ResiliencePolicy(max_retries=2, base_delay=0.01, max_delay=0.05)
        fault = server.fail("PROPFIND", lambda p: p.endswith("/notes.txt"), httpx.Response(429))

        response = await NextcloudDataSource(NextcloudClient(http)).list_directory("alice", "notes.txt", depth=0)

        assert type(http.client._transport) is ResilientHTTPTransport
        assert (response.status, fault.hits) == (207, 1), "the 429 was retried and the retry reached the server"
        assert len(server.calls("PROPFIND")) == 2

    async def test_run_sync_initialises_a_connector_that_was_never_initialised(self, server, db, store) -> None:
        seed_drive(server)
        connector = build(server, db, store)

        await connector.run_sync()

        assert "q1.pdf" in db.names()


class TestAppPasswordAuth:
    async def test_the_app_password_is_sent_as_basic_auth_on_every_request(self, server, db, store) -> None:
        seed_drive(server)
        connector = await make_connector(server, db, store)
        await connector.run_sync()

        expected = "Basic " + base64.b64encode(b"alice:" + server.app_password.encode()).decode()
        assert server.requests and {r.headers["authorization"] for r in server.requests} == {expected}
        assert {r.headers.get("ocs-apirequest") for r in server.calls("GET", "/ocs/")} == {"true"}
        assert connector.current_user_email == "alice@example.com"
        assert owners(db, "q1.pdf") == ALICE_OWNER

    async def test_an_app_password_with_colons_and_accents_still_authenticates(self, server, db, store) -> None:
        server.app_password = "pässwörd:with:colons"
        seed_drive(server)
        connector = await make_connector(server, db, store)

        await connector.run_sync()

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
        await connector.run_sync()

        record = db.by_name("q1.pdf")
        assert connector.base_url == BASE
        assert record.weburl == f"{BASE}/f/{record.external_record_id}"

    async def test_nextcloud_installed_under_a_sub_path(self, db, store, monkeypatch) -> None:
        server = FakeNextcloud(base_path="/nextcloud")
        monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", server.network())
        seed_drive(server)
        connector = await make_connector(server, db, store)

        await connector.run_sync()

        assert db.path_of("q1.pdf") == "Docs/Reports/q1.pdf"
        assert db.by_name("readme.txt").parent_external_record_id is None

    async def test_a_user_with_no_email_in_nextcloud_gets_a_stand_in_address(self, server, db, store) -> None:
        server.email = None
        connector = await make_connector(server, db, store)

        assert connector.current_user_email == f"alice{MADE_UP_DOMAIN}"

    async def test_connection_check_uses_the_app_password(self, server, db, store) -> None:
        connector = await make_connector(server, db, store)
        assert await connector.test_connection_and_access() is True

        server.app_password = "revoked"
        assert await connector.test_connection_and_access() is False

        connector.data_source = None
        assert await connector.test_connection_and_access() is False

    async def test_connection_check_survives_a_network_error(self, server, db, store) -> None:
        connector = await make_connector(server, db, store)
        server.fail("GET", lambda p: p == CAPABILITIES_PATH, httpx.ConnectError("refused"))

        assert await connector.test_connection_and_access() is False

    @pytest.mark.xfail(
        strict=True,
        reason=(
            "Bug, left alone because an open PR edits this connector: when Nextcloud rejects the "
            "app password, init still reports success and every sync finishes 'successfully' with "
            "nothing in it, so the user is never told to fix the password."
        ),
    )
    async def test_a_rejected_app_password_is_reported(self, server, db, store) -> None:
        seed_drive(server)
        connector = build(server, db, store, config=auth_config(server, password="wrong-app-password"))

        initialised = await connector.init()
        raised = False
        if initialised:
            try:
                await connector.run_sync()
            except Exception:
                raised = True

        assert not initialised or raised

    @pytest.mark.xfail(
        strict=True,
        reason=(
            "Bug, left alone because an open PR edits this connector: a temporary error reading the "
            "user's profile is treated like 'this user has no email', so files are saved as owned "
            "by a made-up address and the real user can't see them."
        ),
    )
    async def test_a_temporary_profile_error_does_not_invent_an_owner(self, server, db, store) -> None:
        seed_drive(server)
        server.fail("GET", lambda p: p.startswith(USERS_PREFIX), httpx.Response(503))
        connector = build(server, db, store)

        if await connector.init():
            await connector.run_sync()

        emails = {p.email for perms in db.permissions.values() for p in perms}
        assert not any(e.endswith(MADE_UP_DOMAIN) for e in emails)


class TestFullSync:
    async def test_the_whole_drive_is_synced_to_the_end_in_batches(self, server, db, store) -> None:
        seed_drive(server)
        connector = await make_connector(server, db, store)
        connector.batch_size = 2

        await connector.run_sync()

        assert db.names() == {"Docs", "Reports", "Photos", "q1.pdf", "notes.txt", "cat.png", "readme.txt"}
        assert [len(b) for b in db.batches] == [2, 2, 2, 1], "the last, partial batch is flushed too"
        for name, path in [("q1.pdf", "Docs/Reports/q1.pdf"), ("notes.txt", "Docs/notes.txt"),
                           ("cat.png", "Photos/cat.png"), ("readme.txt", "readme.txt"), ("Reports", "Docs/Reports")]:
            assert db.path_of(name) == path
        pdf = db.by_name("q1.pdf")
        assert isinstance(pdf, FileRecord)
        assert (pdf.mime_type, pdf.extension, pdf.is_file, pdf.size_in_bytes) == (MimeTypes.PDF.value, "pdf", True, 18)
        assert pdf.external_revision_id == server.nodes["Docs/Reports/q1.pdf"].etag
        assert pdf.weburl == f"{BASE}/f/{pdf.external_record_id}"
        folder = db.by_name("Docs")
        assert (folder.mime_type, folder.is_file, folder.extension) == (MimeTypes.FOLDER.value, False, "")
        assert [u.email for u in db.app_users] == ["alice@example.com"]
        assert [(p.email, p.type) for p in db.record_group_permissions["alice"]] == [("alice@example.com", PermissionType.OWNER)]
        listing = server.calls("PROPFIND")
        assert [r.headers["depth"] for r in listing] == ["100"]
        anchor = server.calls("GET", ACTIVITY_PATH)[-1].url.params
        assert (anchor["sort"], anchor["limit"]) == ("desc", "1")
        assert store.cursor() == str(server.latest_activity_id)

    async def test_an_empty_drive_syncs_without_writing_records(self, server, db, store) -> None:
        connector = await make_connector(server, db, store)

        await connector.run_sync()

        assert db.records == {} and db.batches == []
        assert store.cursor() is None, "an empty drive has no activity to anchor to"

    async def test_repeat_full_syncs_neither_duplicate_nor_rewrite(self, server, db, store) -> None:
        seed_drive(server)
        server.activities.clear()  # activity app disabled: no cursor, so every run is a full sync
        connector = await make_connector(server, db, store)
        await connector.run_sync()
        first = {r.external_record_id: r.id for r in db.records.values()}
        writes = len(db.batches)

        await connector.run_sync()
        assert {r.external_record_id: r.id for r in db.records.values()} == first
        assert len(db.batches) == writes, "unchanged files are not written again"

        server.change("Docs/notes.txt", b"v2")
        server.activities.clear()
        await connector.run_sync()
        notes = db.by_name("notes.txt")
        assert notes.id == first[notes.external_record_id]
        assert notes.external_revision_id == server.nodes["Docs/notes.txt"].etag
        assert notes.version == 1
        assert len(db.records) == len(first)

    async def test_one_entry_that_fails_does_not_stop_the_rest(self, server, db, store) -> None:
        seed_drive(server)
        db.fail_lookup_for = {ids_of(server)["notes.txt"]}
        connector = await make_connector(server, db, store)

        await connector.run_sync()

        assert db.names() == {"Docs", "Reports", "Photos", "q1.pdf", "cat.png", "readme.txt"}

    @pytest.mark.parametrize(
        "break_it",
        [
            pytest.param(lambda server, db: server.fail("PROPFIND", lambda p: True, httpx.Response(503)), id="listing-fails"),
            pytest.param(lambda server, db: server.fail("PROPFIND", lambda p: True, httpx.Response(429, headers={"Retry-After": "1"})), id="listing-rate-limited"),
            pytest.param(lambda server, db: server.fail("PROPFIND", lambda p: True, httpx.Response(207, content=b"<not xml")), id="listing-garbled"),
            pytest.param(lambda server, db: server.fail("PROPFIND", lambda p: True, httpx.Response(207, content=b"")), id="listing-empty"),
            pytest.param(lambda server, db: db.fail_write_for.add("Docs"), id="first-write-fails"),
        ],
    )
    @pytest.mark.xfail(
        strict=True,
        reason=(
            "Bug, left alone because an open PR edits this connector: when the first full sync "
            "can't list or save the drive, the error is swallowed and the activity cursor is still "
            "anchored to 'now', so later runs only look for newer changes and the files that were "
            "missed are never synced."
        ),
    )
    async def test_a_failed_first_sync_is_retried_on_the_next_run(self, server, db, store, break_it) -> None:
        seed_drive(server)
        connector = await make_connector(server, db, store)
        break_it(server, db)

        await connector.run_sync()
        db.fail_write_for.clear()
        await connector.run_sync()

        assert {"q1.pdf", "notes.txt", "cat.png", "readme.txt"} <= db.names()

    async def test_a_failed_cursor_anchor_leaves_the_next_run_a_full_sync(self, server, db, store) -> None:
        seed_drive(server)
        server.fail("GET", lambda p: p == ACTIVITY_PATH, httpx.Response(503))
        connector = await make_connector(server, db, store)

        await connector.run_sync()

        assert "q1.pdf" in db.names()
        assert store.cursor() is None

    async def test_a_cursor_anchor_that_raises_is_not_fatal(self, server, db, store) -> None:
        seed_drive(server)
        connector = await make_connector(server, db, store)
        server.fail("GET", lambda p: p == ACTIVITY_PATH, httpx.ReadTimeout("slow"))

        await connector.run_sync()

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
        await connector.run_sync()
        return db.names() - {"Docs", "Reports", "Photos"}

    async def test_only_listed_extensions(self, server, db, store) -> None:
        names = await self.run_with(server, db, store, {"file_extensions": {"type": "multiselect", "operator": "in", "value": [".PDF", "png"]}})
        assert names == {"q1.pdf", "cat.png"}
        assert {"Docs", "Reports", "Photos"} <= db.names(), "folders are never filtered out"

    async def test_excluded_extensions(self, server, db, store) -> None:
        names = await self.run_with(server, db, store, {"file_extensions": {"type": "multiselect", "operator": "not_in", "value": ["pdf"]}})
        assert names == {"notes.txt", "cat.png", "readme.txt", "LICENSE"}

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

        await connector.run_sync()

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

        await connector.run_sync()

        assert db.names() == kept

    async def test_a_file_whose_date_cannot_be_read_is_left_out_of_a_date_window(self, server, db, store) -> None:
        server.add_file("odd-date.txt")
        listing = await server_listing(server)
        server.fail("PROPFIND", lambda p: True, httpx.Response(207, content=listing.replace(
            server.nodes["odd-date.txt"].modified.strftime("%a, %d %b %Y %H:%M:%S GMT").encode(), b"2026-03-01T09:01:00Z")))
        connector = await make_connector(server, db, store, filters={
            "modified": {"type": "datetime", "operator": "is_after", "value": {"start": 0}}})

        await connector.run_sync()

        assert "odd-date.txt" not in db.names()

    async def test_an_unreadable_date_without_a_window_still_syncs_the_file(self, server, db, store) -> None:
        server.add_file("odd-date.txt")
        listing = await server_listing(server)
        server.fail("PROPFIND", lambda p: True, httpx.Response(207, content=listing.replace(
            server.nodes["odd-date.txt"].modified.strftime("%a, %d %b %Y %H:%M:%S GMT").encode(), b"yesterday")))
        connector = await make_connector(server, db, store)

        await connector.run_sync()

        assert "odd-date.txt" in db.names()


async def server_listing(server: FakeNextcloud) -> bytes:
    return server._propfind("", "100").content


class TestIncrementalSync:
    async def test_a_new_file_in_new_folders_gets_its_folders_created_top_down(self, server, db, store) -> None:
        connector = await synced(server, db, store)
        cursor = store.cursor()
        server.add_file("Projects/2026/Plan/brief.pdf", b"brief", "application/pdf")

        await connector.run_sync()

        assert db.path_of("brief.pdf") == "Projects/2026/Plan/brief.pdf"
        assert db.path_of("Plan") == "Projects/2026/Plan"
        feed = server.calls("GET", ACTIVITY_PATH)[-1].url.params
        assert (feed["since"], feed["sort"], feed["limit"]) == (cursor, "asc", "500")
        assert store.cursor() == str(server.latest_activity_id)
        assert all(r.headers["depth"] == "0" for r in server.calls("PROPFIND")[1:]), "incremental fetches one item at a time"

    async def test_no_new_activity_changes_nothing(self, server, db, store) -> None:
        connector = await synced(server, db, store)
        writes, cursor, propfinds = len(db.batches), store.cursor(), len(server.calls("PROPFIND"))

        await connector.run_sync()
        await connector.run_incremental_sync()

        assert (len(db.batches), store.cursor(), len(server.calls("PROPFIND"))) == (writes, cursor, propfinds)

    async def test_every_file_of_a_merged_upload_activity_is_synced(self, server, db, store) -> None:
        connector = await synced(server, db, store)
        server.add_files_in_one_upload(["Docs/a.txt", "Docs/b.txt", "Docs/c.txt"])

        await connector.run_sync()

        assert {"a.txt", "b.txt", "c.txt"} <= db.names()
        assert db.path_of("c.txt") == "Docs/c.txt"

    async def test_a_changed_file_is_updated_in_place(self, server, db, store) -> None:
        connector = await synced(server, db, store)
        before = db.by_name("notes.txt")
        count = len(db.records)
        server.change("Docs/notes.txt", b"new notes")

        await connector.run_sync()

        after = db.by_name("notes.txt")
        assert (after.id, after.version) == (before.id, before.version + 1)
        assert after.external_revision_id == server.nodes["Docs/notes.txt"].etag
        assert len(db.records) == count
        assert owners(db, "notes.txt") == ALICE_OWNER

    async def test_a_rename_keeps_the_record_and_its_folder(self, server, db, store) -> None:
        connector = await synced(server, db, store)
        before = db.by_name("notes.txt")
        server.move("Docs/notes.txt", "Docs/meeting-notes.txt")

        await connector.run_sync()

        after = db.by_name("meeting-notes.txt")
        assert after.id == before.id and "notes.txt" not in db.names()
        assert db.path_of("meeting-notes.txt") == "Docs/meeting-notes.txt"

    @pytest.mark.parametrize(("target", "path"), [("Photos/notes.txt", "Photos/notes.txt"), ("notes.txt", "notes.txt"),
                                                   ("Archive/2025/notes.txt", "Archive/2025/notes.txt")])
    async def test_a_move_shows_the_file_only_in_its_new_folder(self, server, db, store, target, path) -> None:
        connector = await synced(server, db, store)
        server.move("Docs/notes.txt", target)

        await connector.run_sync()

        assert db.path_of("notes.txt") == path

    async def test_renaming_a_folder_carries_its_contents(self, server, db, store) -> None:
        connector = await synced(server, db, store)
        server.move("Docs", "Documents")

        await connector.run_sync()

        assert db.path_of("q1.pdf") == "Documents/Reports/q1.pdf"
        assert "Docs" not in db.names()

    async def test_a_deleted_file_is_removed_and_a_restored_one_comes_back(self, server, db, store) -> None:
        connector = await synced(server, db, store)
        node = server.delete("Docs/notes.txt")

        await connector.run_sync()
        assert "notes.txt" not in db.names() and db.deleted == ["notes.txt"]

        server.restore(node)
        await connector.run_sync()
        assert db.by_name("notes.txt").external_record_id == node.file_id
        assert db.path_of("notes.txt") == "Docs/notes.txt"

    async def test_deleting_a_file_the_index_never_had_is_harmless(self, server, db, store) -> None:
        connector = await synced(server, db, store)
        server.add_file("tmp.txt", log=False)
        server.delete("tmp.txt")

        await connector.run_sync()

        assert db.deleted == [] and store.cursor() == str(server.latest_activity_id)

    @pytest.mark.xfail(
        strict=True,
        reason=(
            "Bug, left alone because an open PR edits this connector: deleting a folder in "
            "Nextcloud removes only the folder's own record; the files inside it stay searchable."
        ),
    )
    async def test_deleting_a_folder_removes_what_was_inside(self, server, db, store) -> None:
        connector = await synced(server, db, store)
        server.delete("Docs")

        await connector.run_sync()

        assert not {"Docs", "Reports", "q1.pdf", "notes.txt"} & db.names()

    async def test_a_backlog_longer_than_one_page_is_caught_up_over_runs(self, server, db, store) -> None:
        connector = await synced(server, db, store)
        start = server.latest_activity_id
        for i in range(500):
            server.change("Docs/notes.txt", f"v{i}".encode())
        server.add_file("late.txt")

        await connector.run_sync()
        assert store.cursor() == str(start + 500), "one page of 500 per run; the cursor stops at the page's end"
        assert "late.txt" not in db.names()

        await connector.run_sync()
        assert server.calls("GET", ACTIVITY_PATH)[-1].url.params["since"] == str(start + 500)
        assert "late.txt" in db.names()
        assert db.by_name("notes.txt").external_revision_id == server.nodes["Docs/notes.txt"].etag

    async def test_one_failed_fetch_does_not_stop_the_other_changes(self, server, db, store) -> None:
        connector = await synced(server, db, store)
        server.change("Docs/notes.txt")
        server.change("readme.txt")
        server.fail("PROPFIND", lambda p: p.endswith("/notes.txt"), httpx.Response(503))

        await connector.run_sync()

        assert db.by_name("readme.txt").external_revision_id == server.nodes["readme.txt"].etag

    @pytest.mark.parametrize("bad", BAD_ANSWERS)
    async def test_a_bad_answer_for_one_changed_file_does_not_stop_the_others(self, server, db, store, bad) -> None:
        connector = await synced(server, db, store)
        server.change("Docs/notes.txt")
        server.change("readme.txt")
        server.fail("PROPFIND", lambda p: p.endswith("/notes.txt"), bad)

        await connector.run_sync()

        assert db.by_name("readme.txt").external_revision_id == server.nodes["readme.txt"].etag

    @pytest.mark.parametrize("bad", BAD_ANSWERS)
    async def test_a_bad_answer_for_a_new_folder_still_saves_the_file(self, server, db, store, bad) -> None:
        connector = await synced(server, db, store)
        server.add_file("New/inside.txt")
        server.fail("PROPFIND", lambda p: p.rstrip("/").endswith("/New"), bad, bad)

        await connector.run_sync()

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

        await connector.run_sync()
        assert (store.cursor(), len(db.batches)) == (cursor, writes)

        await connector.run_sync()
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

        await connector.run_sync()

        assert len(db.batches) == writes and db.deleted == []
        assert store.cursor() == str(server.latest_activity_id)

    async def test_a_record_group_that_vanished_triggers_a_full_sync(self, server, db, store) -> None:
        connector = await synced(server, db, store)
        db.record_groups.clear()
        server.add_file("extra.txt")

        await connector.run_sync()

        assert "alice" in db.record_groups and "extra.txt" in db.names()

    async def test_an_activity_feed_that_raises_fails_the_run(self, server, db, store) -> None:
        connector = await synced(server, db, store)
        server.fail("GET", lambda p: p == ACTIVITY_PATH, httpx.ReadTimeout("slow"))

        with pytest.raises(httpx.ReadTimeout):
            await connector.run_sync()

    async def test_incremental_sync_without_a_client_or_user_does_nothing(self, server, db, store) -> None:
        connector = await synced(server, db, store)
        writes = len(db.batches)
        connector.current_user_id = None
        await connector.run_incremental_sync()
        connector.data_source = None
        await connector.run_incremental_sync()

        assert len(db.batches) == writes

    @pytest.mark.xfail(
        strict=True,
        reason=(
            "Bug, left alone because an open PR edits this connector: when the saved cursor can't "
            "be read, the run falls back to a full sync, which doesn't notice deletions and then "
            "moves the cursor past them, so files deleted in Nextcloud stay searchable for good."
        ),
    )
    async def test_an_unreadable_cursor_does_not_skip_deletions(self, server, db, store) -> None:
        connector = await synced(server, db, store)
        server.delete("Docs/notes.txt")
        store.fail_reads = 1

        await connector.run_sync()
        await connector.run_sync()

        assert "notes.txt" not in db.names()

    @pytest.mark.xfail(
        strict=True,
        reason=(
            "Bug, left alone because an open PR edits this connector: when the activity feed fails "
            "(an outage or a 429 rate limit), the run falls back to a full sync, which doesn't notice "
            "deletions and then moves the cursor past them, so deleted files stay searchable."
        ),
    )
    @pytest.mark.parametrize("status", [503, 429])
    async def test_a_failed_activity_feed_does_not_skip_deletions(self, server, db, store, status) -> None:
        connector = await synced(server, db, store)
        server.delete("Docs/notes.txt")
        server.fail("GET", lambda p: p == ACTIVITY_PATH, httpx.Response(status, headers={"Retry-After": "1"}))

        await connector.run_sync()
        await connector.run_sync()

        assert "notes.txt" not in db.names()

    @pytest.mark.xfail(
        strict=True,
        reason=(
            "Bug, left alone because an open PR edits this connector: the cursor moves past a change "
            "whose file couldn't be fetched, so that change is never retried."
        ),
    )
    async def test_a_change_that_failed_to_fetch_is_retried_next_run(self, server, db, store) -> None:
        connector = await synced(server, db, store)
        server.change("Docs/notes.txt")
        server.fail("PROPFIND", lambda p: p.endswith("/notes.txt"), httpx.Response(503))

        await connector.run_sync()
        await connector.run_sync()

        assert db.by_name("notes.txt").external_revision_id == server.nodes["Docs/notes.txt"].etag

    @pytest.mark.xfail(
        strict=True,
        reason=(
            "Bug, left alone because an open PR edits this connector: the cursor moves past a "
            "deletion that failed to apply, so the deleted file stays searchable."
        ),
    )
    async def test_a_deletion_that_failed_to_apply_is_retried_next_run(self, server, db, store) -> None:
        connector = await synced(server, db, store)
        db.fail_delete_for = {ids_of(server)["notes.txt"]}
        server.delete("Docs/notes.txt")

        await connector.run_sync()
        db.fail_delete_for.clear()
        await connector.run_sync()

        assert "notes.txt" not in db.names()

    @pytest.mark.xfail(
        strict=True,
        reason=(
            "Bug, left alone because an open PR edits this connector: a 429 (rate limited) answer "
            "is not retried after the Retry-After wait, because the connector builds its client "
            "without a retry policy; the change is skipped for this run."
        ),
    )
    async def test_a_rate_limited_fetch_is_retried_after_waiting(self, server, db, store) -> None:
        connector = await synced(server, db, store)
        server.change("Docs/notes.txt")
        server.fail("PROPFIND", lambda p: p.endswith("/notes.txt"), httpx.Response(429, headers={"Retry-After": "1"}))

        await connector.run_sync()

        assert db.by_name("notes.txt").external_revision_id == server.nodes["Docs/notes.txt"].etag


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

        await connector.run_sync()

        assert CountingLimiter.entered == len(server.calls("PROPFIND")) - before >= 3


class TestPermissions:
    async def test_only_the_connecting_user_gets_access_even_to_shared_files(self, server, db, store) -> None:
        seed_drive(server)
        server.shares["/Docs/Reports/q1.pdf"] = [{"share_type": 0, "share_with": "bob", "permissions": 19}]
        connector = await make_connector(server, db, store)

        await connector.run_sync()

        assert {name: owners(db, name) for name in db.names()} == dict.fromkeys(db.names(), ALICE_OWNER)
        emails = {p.email for perms in db.permissions.values() for p in perms}
        assert emails == {"alice@example.com"}, "a Nextcloud share never widens access in PipesHub"

    async def test_a_failed_fetch_writes_no_record_and_no_access(self, server, db, store) -> None:
        connector = await synced(server, db, store)
        server.add_file("secret.txt")
        server.fail("PROPFIND", lambda p: p.endswith("/secret.txt"), httpx.Response(500))
        permission_sets = len(db.permissions)

        await connector.run_sync()

        assert "secret.txt" not in db.names() and len(db.permissions) == permission_sets

    async def test_share_lookup_resolves_the_path_inside_the_users_home(self, server, db, store) -> None:
        connector = await make_connector(server, db, store)
        server.shares["/Docs/q1.pdf"] = [
            {"share_type": 0, "share_with": " bob ", "permissions": 19},
            {"share_type": "x", "share_with": "carol", "permissions": 99},
        ]

        shares = await connector._get_file_shares(f"{WEBDAV_PREFIX}alice/Docs/q1.pdf", "alice")
        at_home = await connector._get_file_shares(f"{WEBDAV_PREFIX}alice/", "alice")

        assert shares == [{"share_type": 0, "share_with": "bob", "permissions": 19},
                          {"share_with": "carol", "permissions": 1}]
        assert at_home == []
        assert [r.url.params["path"] for r in server.calls("GET", SHARES_PATH)] == ["/Docs/q1.pdf"]


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

        server.fail("GET", lambda p: p.endswith("/readme.txt"), httpx.Response(200, content=b""))
        with pytest.raises(HTTPException) as empty:
            await connector.stream_record(db.by_name("readme.txt"))
        assert empty.value.status_code == 404

        unknown = db.by_name("cat.png").model_copy(update={"id": "not-in-db"})
        with pytest.raises(HTTPException) as missing:
            await connector.stream_record(unknown)
        assert missing.value.status_code == 404

        server.fail("GET", lambda p: p.endswith("/cat.png"), httpx.ConnectError("refused"))
        with pytest.raises(HTTPException) as network:
            await connector.stream_record(db.by_name("cat.png"))
        assert network.value.status_code >= 500

        connector.data_source = None
        with pytest.raises(HTTPException) as not_ready:
            await connector.stream_record(db.by_name("cat.png"))
        assert not_ready.value.status_code == 409

    @pytest.mark.xfail(
        strict=True,
        reason=(
            "Bug, left alone because an open PR edits this connector: the 'cannot download a "
            "folder' check compares the stored MIME type text with an enum, so it never matches "
            "and a folder download request is sent to Nextcloud instead of being refused."
        ),
    )
    async def test_a_folder_download_is_refused(self, server, db, store) -> None:
        connector = await synced(server, db, store)
        downloads = len(server.calls("GET", WEBDAV_PREFIX))

        with pytest.raises(HTTPException) as folder:
            await connector.stream_record(db.by_name("Docs"))

        assert folder.value.status_code == 400
        assert len(server.calls("GET", WEBDAV_PREFIX)) == downloads

    @pytest.mark.parametrize("name", ["report #1.txt", "why?.txt", "50% off.txt", "résumé ✓.txt", "a&b.txt"])
    async def test_files_with_special_characters_sync_and_download(self, server, db, store, name) -> None:
        connector = await synced(server, db, store)
        server.add_file(f"Docs/{name}", b"special")

        await connector.run_sync()

        assert db.path_of(name) == f"Docs/{name}"
        assert await body_of(await connector.stream_record(db.by_name(name))) == b"special"

    @pytest.mark.xfail(
        strict=True,
        reason=(
            "Bug, left alone because an open PR edits this connector: a file inside a folder named "
            "'files' is downloaded from the wrong place (the path is cut at '/files/', which is "
            "meant to match only Nextcloud's own URL prefix), so it can't be opened, or another "
            "file with the same name is served instead."
        ),
    )
    @pytest.mark.parametrize("path", ["Work/files/report.txt", "Work/files/2026/report.txt"])
    async def test_a_file_under_a_folder_named_files_downloads(self, server, db, store, path) -> None:
        server.add_file("report.txt", b"a different file at the top level")
        server.add_file(path, b"the real report")
        connector = await make_connector(server, db, store)
        await connector.run_sync()
        record = next(r for r in db.records.values() if r.external_record_id == server.nodes[path].file_id)

        response = await connector.stream_record(record)

        assert await body_of(response) == b"the real report"

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
        server.fail("PROPFIND", lambda p: p.endswith("/cat.png"), httpx.ConnectError("reset"))

        await connector.reindex_records([gone, unknown, db.by_name("readme.txt"), db.by_name("cat.png"), db.by_name("Docs")])

        assert [r.record_name for r in db.content_updates] == ["Docs"], "one failure doesn't stop the rest"

    @pytest.mark.xfail(
        strict=True,
        reason=(
            "Bug, left alone because an open PR edits this connector: reindexing a file inside a "
            "folder saves it with no folder, which detaches it; its path then points at the top "
            "level, so opening or re-indexing it fails with 'not found'."
        ),
    )
    async def test_reindex_keeps_a_nested_file_in_its_folder(self, server, db, store) -> None:
        connector = await synced(server, db, store)

        await connector.reindex_records([db.by_name("q1.pdf")])

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
