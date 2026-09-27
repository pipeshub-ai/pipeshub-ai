"""Personal Google Drive sync, driven over a fake Drive API with the real Google client.

The connector, ``GoogleClient``, the discovery-built Drive service, ``execute()`` with
its retry/backoff, google-auth's OAuth credentials and ``MediaIoBaseDownload`` are all
real; every HTTP request is answered by ``DriveWorld`` and our databases are in-memory.
"""

import logging
from collections.abc import AsyncIterator, Callable
from typing import Any, Optional

import pytest
from drive_world import FOLDER, GDOC, DriveWorld
from fastapi.responses import StreamingResponse
from google_behaviour_fakes import (
    ApiRequest,
    FakeConfigService,
    FakeEntitiesProcessor,
    FakeGoogleHttp,
    FakeSyncPointStore,
)
from googleapiclient.errors import HttpError

from app.connectors.sources.google.drive.individual.connector import (
    GoogleDriveIndividualConnector,
)
from app.models.permission import PermissionType

CONNECTOR_ID = "drive-personal-1"
ME = "me@example.com"
ROOT = "root-me"
CHECKPOINT = "/personal_drive"


class Harness:
    def __init__(self, http: FakeGoogleHttp, records: FakeEntitiesProcessor, sync_points: FakeSyncPointStore) -> None:
        self.http = http
        self.records = records
        self.sync_points = sync_points
        self.world = DriveWorld(http)
        self.world.add_user(ME, "Me Myself")
        self.world.aliases["oauth:refresh-1"] = ME
        self.http.accept_token("access-1", ME)
        self.config: dict[str, Any] = {
            "auth": {"oauthConfigId": "oauth-app-1", "connectorScope": "personal"},
            "credentials": {
                "access_token": "access-1",
                "refresh_token": "refresh-1",
                "scope": "https://www.googleapis.com/auth/drive.readonly",
            },
        }
        self.config_service = FakeConfigService(
            CONNECTOR_ID,
            self.config,
            {"drive": [{"_id": "oauth-app-1", "config": {"clientId": "client-1", "clientSecret": "secret-1"}}]},
        )
        self.connector: Optional[GoogleDriveIndividualConnector] = None

    def filters(self, **sync_values: object) -> None:
        self.config["filters"] = {"sync": {"values": sync_values}}

    async def connector_(self) -> GoogleDriveIndividualConnector:
        if self.connector is None:
            self.connector = GoogleDriveIndividualConnector(
                logging.getLogger("drive-individual-behaviour"),
                self.records,
                self.sync_points,
                self.config_service,
                CONNECTOR_ID,
                "personal",
                "user-1",
            )
            assert await self.connector.init()
        return self.connector

    async def sync(self) -> None:
        await (await self.connector_()).run_sync()

    def checkpoint(self) -> Optional[str]:
        value = self.sync_points.value(CHECKPOINT)
        return value.get("pageToken") if value else None

    def names(self) -> set[str]:
        return {r.record_name for r in self.records.records.values() if not r.is_placeholder}


@pytest.fixture
async def drive(google_http: FakeGoogleHttp, records: FakeEntitiesProcessor, sync_points: FakeSyncPointStore) -> AsyncIterator[Harness]:
    harness = Harness(google_http, records, sync_points)
    yield harness
    if harness.connector is not None:
        await harness.connector.cleanup()
    assert not google_http.unrouted, google_http.unrouted


def my_file(world: DriveWorld, file_id: str, name: str, parent: str = ROOT, **kwargs: object) -> None:
    world.add_item(file_id, name, parent=parent, owner=ME, **kwargs)


# --- full sync and pagination -------------------------------------------------


async def test_full_sync_reads_every_page_and_saves_the_token_taken_before_listing(drive: Harness) -> None:
    for n in range(5):
        my_file(drive.world, f"f{n}", f"doc-{n}.txt")
    token_before = str(len(drive.world.log) + 1)

    await drive.sync()

    assert drive.names() == {f"doc-{n}.txt" for n in range(5)}
    listing = [r for r in drive.http.calls("GET", "/drive/v3/files") if "sharedWithMe" not in r.query.get("q", "")]
    assert len(listing) == 3
    assert drive.checkpoint() == token_before
    owner = drive.records.permissions["f0"]
    assert [(p.email, p.type) for p in owner] == [(ME, PermissionType.OWNER)]
    assert drive.records.record_groups[ROOT].name == f"Google Drive - {ME}"


async def test_trashed_files_are_not_synced_on_full_sync(drive: Harness) -> None:
    my_file(drive.world, "keep", "keep.txt")
    my_file(drive.world, "bin", "bin.txt")
    drive.world.trash("bin")

    await drive.sync()

    assert drive.names() == {"keep.txt"}


@pytest.mark.xfail(
    strict=True,
    reason="Full sync stops at the first empty files.list page even when Drive sent a nextPageToken, "
    "then saves the checkpoint, so the files on later pages are never synced.",
)
async def test_an_empty_page_with_a_next_token_does_not_end_the_full_sync(drive: Harness) -> None:
    for n in range(4):
        my_file(drive.world, f"f{n}", f"doc-{n}.txt")
    drive.world.empty_page_at = 1

    await drive.sync()

    assert drive.names() == {f"doc-{n}.txt" for n in range(4)}


# --- change tracking ----------------------------------------------------------


async def test_incremental_sync_applies_create_rename_edit_move_and_delete(drive: Harness) -> None:
    drive.world.folder("dir-a", "A", parent=ROOT, owner=ME)
    drive.world.folder("dir-b", "B", parent=ROOT, owner=ME)
    my_file(drive.world, "f-rename", "old.txt", parent="dir-a")
    my_file(drive.world, "f-edit", "edit.txt", parent="dir-a")
    my_file(drive.world, "f-move", "move.txt", parent="dir-a")
    my_file(drive.world, "f-delete", "delete.txt", parent="dir-a")
    await drive.sync()
    first_checkpoint = drive.checkpoint()

    drive.world.rename("f-rename", "new.txt")
    drive.world.edit("f-edit", b"new content")
    drive.world.move("f-move", "dir-b")
    drive.world.delete("f-delete")
    my_file(drive.world, "f-new", "brand-new.txt", parent="dir-b")
    await drive.sync()

    assert drive.names() == {"A", "B", "new.txt", "edit.txt", "move.txt", "brand-new.txt"}
    assert drive.records.records["f-move"].parent_external_record_id == "dir-b"
    assert [r.external_record_id for r in drive.records.content_updates] == ["f-edit"]
    assert drive.records.records["f-edit"].external_revision_id == "f-edit-rev2"
    assert int(drive.checkpoint()) > int(first_checkpoint)


async def test_trash_in_the_changes_feed_deletes_the_record(drive: Harness) -> None:
    my_file(drive.world, "f1", "one.txt")
    await drive.sync()

    drive.world.trash("f1")
    await drive.sync()

    assert "f1" not in drive.records.records


async def test_every_change_page_is_read_and_each_file_is_written_once(drive: Harness) -> None:
    await drive.sync()
    for n in range(5):
        my_file(drive.world, f"n{n}", f"new-{n}.txt")
    drive.world.rename("n0", "new-0-renamed.txt")

    await drive.sync()

    assert drive.names() == {"new-0-renamed.txt"} | {f"new-{n}.txt" for n in range(1, 5)}
    written = [ext for batch in drive.records.new_record_batches for ext in batch]
    assert sorted(written) == sorted(f"n{n}" for n in range(5))
    assert len(drive.http.calls("GET", "/drive/v3/changes")) == 3


async def test_a_second_run_with_no_changes_keeps_the_checkpoint_and_writes_nothing(drive: Harness) -> None:
    my_file(drive.world, "f1", "one.txt")
    await drive.sync()
    checkpoint, batches = drive.checkpoint(), len(drive.records.new_record_batches)

    await drive.sync()

    assert drive.checkpoint() == checkpoint
    assert len(drive.records.new_record_batches) == batches


async def test_a_failed_change_page_does_not_advance_the_checkpoint(drive: Harness) -> None:
    await drive.sync()
    checkpoint = drive.checkpoint()
    for n in range(4):
        my_file(drive.world, f"n{n}", f"new-{n}.txt")
    drive.http.fail("GET", "/drive/v3/changes", 500, "backendError", when=lambda r: ":" in r.query["pageToken"])

    with pytest.raises(HttpError):
        await drive.sync()
    assert drive.checkpoint() == checkpoint

    drive.http.clear_faults()
    await drive.sync()
    assert drive.names() == {f"new-{n}.txt" for n in range(4)}
    assert int(drive.checkpoint()) > int(checkpoint)


# --- rate limits --------------------------------------------------------------


@pytest.mark.parametrize(("status", "reason"), [(429, "rateLimitExceeded"), (403, "userRateLimitExceeded"), (403, "rateLimitExceeded")])
async def test_quota_errors_are_retried_with_backoff(drive: Harness, backoff_sleeps: list[float], status: int, reason: str) -> None:
    my_file(drive.world, "f1", "one.txt")
    drive.http.fail("GET", "/drive/v3/files", status, reason, times=2)

    await drive.sync()

    assert drive.names() == {"one.txt"}
    assert len(backoff_sleeps) == 2
    assert backoff_sleeps[1] > 0


async def test_a_quota_error_that_outlasts_the_retries_fails_the_run_without_a_checkpoint(drive: Harness, backoff_sleeps: list[float]) -> None:
    my_file(drive.world, "f1", "one.txt")
    drive.http.fail("GET", "/drive/v3/files", 429, "rateLimitExceeded")

    with pytest.raises(HttpError) as raised:
        await drive.sync()

    assert raised.value.resp.status == 429
    assert len(backoff_sleeps) == 3
    assert drive.checkpoint() is None


async def test_a_permission_403_is_not_retried(drive: Harness, backoff_sleeps: list[float]) -> None:
    drive.http.fail("GET", "/drive/v3/files", 403, "insufficientPermissions")

    with pytest.raises(HttpError):
        await drive.sync()

    assert backoff_sleeps == []


# --- shared with me -----------------------------------------------------------


async def test_items_shared_from_a_shared_drive_are_synced_as_read_with_their_folder_contents(drive: Harness) -> None:
    drive.world.add_user("owner@example.com")
    drive.world.add_drive("sd-1", "Team", {"owner@example.com": "organizer"})
    drive.world.folder("sd-folder", "Shared folder", parent="sd-1", perms=[{"type": "user", "role": "reader", "emailAddress": ME}])
    drive.world.add_item("sd-child", "inside.txt", parent="sd-folder")
    drive.world.add_item("sd-hidden", "not-shared.txt", parent="sd-1")
    drive.world.add_item("personal-share", "from-a-colleague.txt", parent="root-owner", owner="owner@example.com",
                         perms=[{"type": "user", "role": "reader", "emailAddress": ME}])

    await drive.sync()

    assert drive.names() == {"Shared folder", "inside.txt", "from-a-colleague.txt"}
    assert [p.type for p in drive.records.permissions["sd-child"]] == [PermissionType.READ]


async def test_a_rate_limited_shared_folder_walk_fails_the_run_instead_of_skipping_its_contents(drive: Harness) -> None:
    drive.world.add_user("owner@example.com")
    drive.world.add_drive("sd-1", "Team", {"owner@example.com": "organizer"})
    drive.world.folder("sd-folder", "Shared folder", parent="sd-1", perms=[{"type": "user", "role": "reader", "emailAddress": ME}])
    drive.world.add_item("sd-child", "inside.txt", parent="sd-folder")
    drive.http.fail("GET", "/drive/v3/files", 403, "userRateLimitExceeded", when=lambda r: "in parents" in r.query.get("q", ""))

    with pytest.raises(HttpError):
        await drive.sync()

    assert drive.checkpoint() is None


async def test_a_daily_quota_error_walking_a_shared_folder_fails_the_run_instead_of_skipping_its_contents(drive: Harness) -> None:
    drive.world.add_user("owner@example.com")
    drive.world.add_drive("sd-1", "Team", {"owner@example.com": "organizer"})
    drive.world.folder("sd-folder", "Shared folder", parent="sd-1", perms=[{"type": "user", "role": "reader", "emailAddress": ME}])
    drive.world.add_item("sd-child", "inside.txt", parent="sd-folder")
    drive.http.fail("GET", "/drive/v3/files", 403, "dailyLimitExceeded", times=1, when=lambda r: "in parents" in r.query.get("q", ""))

    with pytest.raises(HttpError):
        await drive.sync()
    assert drive.checkpoint() is None

    await drive.sync()
    assert "inside.txt" in drive.names()


@pytest.mark.parametrize("reason", ["sharingRateLimitExceeded", "someReasonDriveAddsLater", None, pytest.param(["insufficientFilePermissions", "dailyLimitExceeded"], id="refusal+dailyLimit")])
async def test_a_403_walking_a_shared_folder_that_is_not_a_known_refusal_fails_the_run(drive: Harness, reason: Optional[str | list[str]]) -> None:
    drive.world.add_user("owner@example.com")
    drive.world.add_drive("sd-1", "Team", {"owner@example.com": "organizer"})
    drive.world.folder("sd-folder", "Shared folder", parent="sd-1", perms=[{"type": "user", "role": "reader", "emailAddress": ME}])
    drive.world.add_item("sd-child", "inside.txt", parent="sd-folder")
    drive.http.fail("GET", "/drive/v3/files", 403, reason, times=1, when=lambda r: "in parents" in r.query.get("q", ""))

    with pytest.raises(HttpError):
        await drive.sync()
    assert drive.checkpoint() is None

    await drive.sync()
    assert "inside.txt" in drive.names()


async def test_a_shared_folder_whose_access_was_refused_mid_walk_is_skipped_and_the_rest_still_syncs(drive: Harness) -> None:
    drive.world.add_user("owner@example.com")
    drive.world.add_drive("sd-1", "Team", {"owner@example.com": "organizer"})
    drive.world.folder("sd-folder", "Shared folder", parent="sd-1", perms=[{"type": "user", "role": "reader", "emailAddress": ME}])
    drive.world.add_item("sd-file", "shared-file.txt", parent="sd-1", perms=[{"type": "user", "role": "reader", "emailAddress": ME}])
    drive.http.fail("GET", "/drive/v3/files", 403, "insufficientFilePermissions", when=lambda r: "in parents" in r.query.get("q", ""))

    await drive.sync()

    assert drive.names() == {"Shared folder", "shared-file.txt"}
    assert drive.checkpoint() is not None


async def test_a_shared_folder_that_vanished_mid_walk_is_skipped_and_the_rest_still_syncs(drive: Harness) -> None:
    drive.world.add_user("owner@example.com")
    drive.world.add_drive("sd-1", "Team", {"owner@example.com": "organizer"})
    drive.world.folder("sd-folder", "Shared folder", parent="sd-1", perms=[{"type": "user", "role": "reader", "emailAddress": ME}])
    drive.world.add_item("sd-file", "shared-file.txt", parent="sd-1", perms=[{"type": "user", "role": "reader", "emailAddress": ME}])
    drive.http.fail("GET", "/drive/v3/files", 404, "notFound", when=lambda r: "in parents" in r.query.get("q", ""))

    await drive.sync()

    assert drive.names() == {"Shared folder", "shared-file.txt"}
    assert drive.checkpoint() is not None


def shared_folder_world(world: DriveWorld) -> None:
    world.add_user("owner@example.com")
    world.add_drive("sd-1", "Team", {"owner@example.com": "organizer"})
    world.folder("sd-folder", "Shared folder", parent="sd-1", perms=[{"type": "user", "role": "reader", "emailAddress": ME}])
    world.add_item("sd-child", "inside.txt", parent="sd-folder")
    world.add_item("sd-file", "shared-file.txt", parent="sd-1", perms=[{"type": "user", "role": "reader", "emailAddress": ME}])
    my_file(world, "mine", "mine.txt")


def walking(folder_id: str) -> Callable[[ApiRequest], bool]:
    return lambda r: f"'{folder_id}' in parents" in r.query.get("q", "")


@pytest.mark.parametrize("reason", ["someReasonDriveAddsLater", None])
async def test_a_shared_folder_refused_without_a_known_reason_is_skipped_on_the_fifth_run(
    drive: Harness, caplog: pytest.LogCaptureFixture, reason: str | None
) -> None:
    shared_folder_world(drive.world)
    drive.http.fail("GET", "/drive/v3/files", 403, reason, when=walking("sd-folder"))

    for _ in range(4):
        with pytest.raises(HttpError):
            await drive.sync()
        assert drive.checkpoint() is None

    await drive.sync()

    assert drive.checkpoint() is not None
    assert drive.names() == {"mine.txt", "Shared folder", "shared-file.txt"}
    stored = drive.sync_points.value(CHECKPOINT)
    assert stored["skippedSharedFolders"] == ["sd-folder"]
    assert stored["heldSharedFolders"] == []
    assert "Skipping the contents of shared folder sd-folder" in caplog.text

    drive.world.rename("mine", "renamed.txt")
    await drive.sync()
    assert "renamed.txt" in drive.names(), "the change feed runs once the checkpoint is saved"


def second_shared_folder(world: DriveWorld) -> None:
    world.folder("sd-folder-2", "Second shared folder", parent="sd-1", perms=[{"type": "user", "role": "reader", "emailAddress": ME}])
    world.add_item("sd-child-2", "inside-2.txt", parent="sd-folder-2")


async def runs_until_checkpoint(drive: Harness, limit: int = 30) -> int:
    for attempt in range(1, limit + 1):
        try:
            await drive.sync()
        except HttpError:
            continue
        return attempt
    raise AssertionError(f"no checkpoint after {limit} runs")


async def test_two_shared_folders_refused_without_a_known_reason_use_their_five_runs_together(drive: Harness) -> None:
    shared_folder_world(drive.world)
    second_shared_folder(drive.world)
    for folder_id in ("sd-folder", "sd-folder-2"):
        drive.http.fail("GET", "/drive/v3/files", 403, "someReasonDriveAddsLater", when=walking(folder_id))

    assert await runs_until_checkpoint(drive) == 5

    stored = drive.sync_points.value(CHECKPOINT)
    assert stored["skippedSharedFolders"] == ["sd-folder", "sd-folder-2"]
    assert stored["heldSharedFolders"] == []
    assert {"Shared folder", "Second shared folder", "shared-file.txt", "mine.txt"} <= drive.names()


async def test_a_shared_folder_at_the_limit_keeps_its_count_while_another_still_fails_the_walk(drive: Harness) -> None:
    shared_folder_world(drive.world)
    second_shared_folder(drive.world)
    second_refused = {"on": False}
    drive.http.fail("GET", "/drive/v3/files", 403, "someReasonDriveAddsLater", when=walking("sd-folder"))
    drive.http.fail("GET", "/drive/v3/files", 403, "someReasonDriveAddsLater",
                    when=lambda r: second_refused["on"] and walking("sd-folder-2")(r))

    with pytest.raises(HttpError):
        await drive.sync()
    second_refused["on"] = True
    for _ in range(4):
        with pytest.raises(HttpError):
            await drive.sync()

    stored = drive.sync_points.value(CHECKPOINT)
    assert drive.checkpoint() is None
    assert stored["heldSharedFolders"] == ["sd-folder:5", "sd-folder-2:4"], "the skipped folder is not started over"
    assert stored["skippedSharedFolders"] == ["sd-folder"]

    await drive.sync()

    assert drive.checkpoint() is not None
    stored = drive.sync_points.value(CHECKPOINT)
    assert stored["skippedSharedFolders"] == ["sd-folder", "sd-folder-2"]
    assert stored["heldSharedFolders"] == [], "a later full sync starts every folder fresh"


async def test_a_shared_folder_that_recovers_on_the_third_run_is_synced_and_its_count_cleared(drive: Harness) -> None:
    shared_folder_world(drive.world)
    drive.http.fail("GET", "/drive/v3/files", 403, "someReasonDriveAddsLater", times=2, when=walking("sd-folder"))

    for _ in range(2):
        with pytest.raises(HttpError):
            await drive.sync()
    assert drive.sync_points.value(CHECKPOINT)["heldSharedFolders"] == ["sd-folder:2"]

    await drive.sync()

    assert "inside.txt" in drive.names()
    assert drive.checkpoint() is not None
    assert drive.sync_points.value(CHECKPOINT)["heldSharedFolders"] == []


# --- partial failures ---------------------------------------------------------


async def test_one_unreadable_item_does_not_abort_the_sync(drive: Harness) -> None:
    my_file(drive.world, "good-1", "good-1.txt")
    my_file(drive.world, "bad", "bad.txt")
    my_file(drive.world, "good-2", "good-2.txt")
    drive.world.files["bad"].meta["createdTime"] = "not a timestamp"

    await drive.sync()

    assert drive.names() == {"good-1.txt", "good-2.txt"}
    assert drive.checkpoint() is not None


async def test_a_database_failure_on_a_change_fails_the_run_and_keeps_the_checkpoint(drive: Harness) -> None:
    my_file(drive.world, "f1", "one.txt")
    await drive.sync()
    checkpoint = drive.checkpoint()
    drive.world.rename("f1", "renamed.txt")
    drive.records.fail_writes_for.add("f1")

    with pytest.raises(RuntimeError):
        await drive.sync()

    assert drive.checkpoint() == checkpoint


# --- folder filter ------------------------------------------------------------


async def test_folder_filter_syncs_only_the_selected_subtree(drive: Harness) -> None:
    drive.world.folder("pick", "Picked", parent=ROOT, owner=ME)
    drive.world.folder("pick-sub", "Picked sub", parent="pick", owner=ME)
    my_file(drive.world, "in-1", "in-1.txt", parent="pick")
    my_file(drive.world, "in-2", "in-2.txt", parent="pick-sub")
    my_file(drive.world, "out", "out.txt", parent=ROOT)
    drive.filters(folder_ids={"operator": "in", "type": "list", "value": ["pick"]})

    await drive.sync()

    assert drive.names() == {"Picked", "Picked sub", "in-1.txt", "in-2.txt"}


async def test_a_file_moved_out_of_the_selected_folder_is_deleted(drive: Harness) -> None:
    drive.world.folder("pick", "Picked", parent=ROOT, owner=ME)
    my_file(drive.world, "leaver", "leaver.txt", parent="pick")
    drive.filters(folder_ids={"operator": "in", "type": "list", "value": ["pick"]})
    await drive.sync()
    assert "leaver.txt" in drive.names()

    drive.world.move("leaver", ROOT)
    await drive.sync()

    assert "leaver" not in drive.records.records


async def test_a_folder_moved_into_the_selected_folder_brings_its_contents(drive: Harness) -> None:
    drive.world.folder("pick", "Picked", parent=ROOT, owner=ME)
    drive.world.folder("outside", "Outside", parent=ROOT, owner=ME)
    my_file(drive.world, "inner", "inner.txt", parent="outside")
    drive.filters(folder_ids={"operator": "in", "type": "list", "value": ["pick"]})
    await drive.sync()
    assert drive.names() == {"Picked"}

    drive.world.move("outside", "pick")
    await drive.sync()

    assert drive.names() == {"Picked", "Outside", "inner.txt"}


async def test_a_transient_error_resolving_a_selected_folder_fails_the_run_instead_of_narrowing_it(drive: Harness) -> None:
    drive.world.folder("pick", "Picked", parent=ROOT, owner=ME)
    drive.world.folder("pick-sub", "Picked sub", parent="pick", owner=ME)
    my_file(drive.world, "deep", "deep.txt", parent="pick-sub")
    drive.filters(folder_ids={"operator": "in", "type": "list", "value": ["pick"]})
    drive.http.fail("GET", "/drive/v3/files/pick", 500, "backendError", times=4)

    with pytest.raises(HttpError):
        await drive.sync()
    assert drive.checkpoint() is None

    await drive.sync()
    assert drive.names() == {"Picked", "Picked sub", "deep.txt"}


@pytest.mark.parametrize("reason", ["dailyLimitExceeded", "quotaExceeded", "someReasonDriveAddsLater", pytest.param(["insufficientFilePermissions", "dailyLimitExceeded"], id="refusal+dailyLimit")])
async def test_a_quota_or_unknown_403_on_a_selected_folder_fails_the_run_instead_of_narrowing_it(
    drive: Harness, backoff_sleeps: list[float], reason: str | list[str]
) -> None:
    drive.world.folder("pick", "Picked", parent=ROOT, owner=ME)
    drive.world.folder("pick-sub", "Picked sub", parent="pick", owner=ME)
    my_file(drive.world, "deep", "deep.txt", parent="pick-sub")
    drive.filters(folder_ids={"operator": "in", "type": "list", "value": ["pick"]})
    drive.http.fail("GET", "/drive/v3/files/pick", 403, reason, times=1)

    with pytest.raises(HttpError):
        await drive.sync()
    assert drive.checkpoint() is None

    await drive.sync()
    assert drive.names() == {"Picked", "Picked sub", "deep.txt"}


async def test_a_selected_folder_the_account_is_refused_is_left_out_without_failing_the_run(drive: Harness) -> None:
    drive.world.folder("pick", "Picked", parent=ROOT, owner=ME)
    drive.world.add_user("owner@example.com")
    drive.world.folder("walled", "Walled", parent="root-owner", owner="owner@example.com")
    my_file(drive.world, "f", "kept.txt", parent="pick")
    drive.world.add_item("w", "walled.txt", parent="walled", owner="owner@example.com")
    drive.filters(folder_ids={"operator": "in", "type": "list", "value": ["pick", "walled"]})
    drive.http.fail("GET", "/drive/v3/files/walled", 403, "insufficientFilePermissions", times=1)

    await drive.sync()

    assert {"Picked", "kept.txt"} <= drive.names()
    assert "walled.txt" not in drive.names()
    assert drive.checkpoint() is not None


async def test_a_selected_folder_that_no_longer_exists_does_not_fail_the_run(drive: Harness) -> None:
    drive.world.folder("pick", "Picked", parent=ROOT, owner=ME)
    my_file(drive.world, "f", "kept.txt", parent="pick")
    drive.filters(folder_ids={"operator": "in", "type": "list", "value": ["pick", "deleted-folder"]})

    await drive.sync()

    assert drive.names() == {"Picked", "kept.txt"}
    assert drive.checkpoint() is not None


def two_selected_folders(drive: Harness) -> None:
    drive.world.folder("pick", "Picked", parent=ROOT, owner=ME)
    drive.world.folder("pick-sub", "Picked sub", parent="pick", owner=ME)
    my_file(drive.world, "deep", "deep.txt", parent="pick-sub")
    drive.world.folder("other", "Other", parent=ROOT, owner=ME)
    drive.world.folder("other-sub", "Other sub", parent="other", owner=ME)
    my_file(drive.world, "other-deep", "other-deep.txt", parent="other-sub")
    drive.filters(folder_ids={"operator": "in", "type": "list", "value": ["pick", "other"]})


@pytest.mark.parametrize("reason", ["someReasonDriveAddsLater", None])
async def test_a_selected_folder_refused_without_a_known_reason_is_left_out_on_the_fifth_run(
    drive: Harness, caplog: pytest.LogCaptureFixture, reason: str | None
) -> None:
    two_selected_folders(drive)
    drive.http.fail("GET", "/drive/v3/files/pick", 403, reason)

    for _ in range(4):
        with pytest.raises(HttpError):
            await drive.sync()
        assert drive.checkpoint() is None

    await drive.sync()

    assert drive.checkpoint() is not None
    assert {"Other", "Other sub", "other-deep.txt"} <= drive.names()
    assert "deep.txt" not in drive.names()
    assert drive.sync_points.value(CHECKPOINT)["heldFilterFolders"] == ["pick:5"]
    assert "Leaving selected folder pick out of this sync" in caplog.text

    await drive.sync()
    assert drive.checkpoint() is not None, "a folder already given up on does not fail later runs"


async def test_two_selected_folders_refused_without_a_known_reason_use_their_five_runs_together(drive: Harness) -> None:
    two_selected_folders(drive)
    drive.world.folder("third", "Third", parent=ROOT, owner=ME)
    drive.filters(folder_ids={"operator": "in", "type": "list", "value": ["pick", "other", "third"]})
    drive.http.fail("GET", "/drive/v3/files/pick", 403, "someReasonDriveAddsLater")
    drive.http.fail("GET", "/drive/v3/files/third", 403, None)

    with pytest.raises(HttpError):
        await drive.sync()
    assert drive.sync_points.value(CHECKPOINT)["heldFilterFolders"] == ["pick:1", "third:1"], "every refused seed is counted"

    assert await runs_until_checkpoint(drive) == 4

    assert {"Other", "Other sub", "other-deep.txt"} <= drive.names()
    assert drive.sync_points.value(CHECKPOINT)["heldFilterFolders"] == ["pick:5", "third:5"]


async def test_a_selected_folder_that_recovers_on_the_third_run_is_synced_and_its_count_cleared(drive: Harness) -> None:
    two_selected_folders(drive)
    drive.http.fail("GET", "/drive/v3/files/pick", 403, "someReasonDriveAddsLater", times=2)

    for _ in range(2):
        with pytest.raises(HttpError):
            await drive.sync()
    assert drive.sync_points.value(CHECKPOINT)["heldFilterFolders"] == ["pick:2"]

    await drive.sync()

    assert {"deep.txt", "other-deep.txt"} <= drive.names()
    assert drive.sync_points.value(CHECKPOINT)["heldFilterFolders"] == []


# --- tokens -------------------------------------------------------------------


async def test_a_rotated_access_token_in_config_is_used_on_the_next_call(drive: Harness) -> None:
    my_file(drive.world, "f1", "one.txt")
    await drive.sync()
    drive.http.accept_token("access-2", ME)
    drive.config["credentials"]["access_token"] = "access-2"

    my_file(drive.world, "f2", "two.txt")
    await drive.sync()

    last = drive.http.requests[-1]
    assert last.headers["authorization"] == "Bearer access-2"
    assert drive.names() == {"one.txt", "two.txt"}


async def test_a_rejected_access_token_is_refreshed_once_and_the_call_retried(drive: Harness) -> None:
    my_file(drive.world, "f1", "one.txt")
    drive.config["credentials"]["access_token"] = "expired-token"
    drive.http.accept_token("expired-token", ME)
    await drive.connector_()
    drive.http._token_owner.pop("expired-token")

    await drive.sync()

    assert drive.names() == {"one.txt"}
    refreshes = [t for t in drive.http.token_requests if t.get("grant_type") == "refresh_token"]
    assert refreshes and refreshes[0]["refresh_token"] == "refresh-1"
    assert refreshes[0]["client_id"] == "client-1"


async def test_a_revoked_refresh_token_fails_the_run_without_a_checkpoint(drive: Harness) -> None:
    my_file(drive.world, "f1", "one.txt")
    await drive.connector_()
    drive.http._token_owner.pop("access-1")
    drive.http.revoked_refresh_tokens.add("refresh-1")

    with pytest.raises(Exception, match="invalid_grant"):
        await drive.sync()

    assert drive.checkpoint() is None
    assert drive.names() == set()


# --- streaming ----------------------------------------------------------------


async def _body(response: StreamingResponse) -> bytes:
    return b"".join([chunk async for chunk in response.body_iterator])


async def test_streaming_downloads_a_binary_file_and_exports_a_google_doc(drive: Harness) -> None:
    my_file(drive.world, "bin", "report.txt", content=b"0123456789" * 5)
    drive.world.add_item("doc", "Notes", parent=ROOT, owner=ME, mime=GDOC, content=b"doc body")
    await drive.sync()
    connector = await drive.connector_()

    download = await connector.stream_record(drive.records.records["bin"])
    export = await connector.stream_record(drive.records.records["doc"])

    assert await _body(download) == b"0123456789" * 5
    assert await _body(export) == b"exported:doc body"
    assert drive.http.calls("GET", "/drive/v3/files/doc/export")[0].query["mimeType"].endswith("wordprocessingml.document")


async def test_streaming_a_file_deleted_at_source_reports_not_found(drive: Harness) -> None:
    my_file(drive.world, "gone", "gone.txt")
    await drive.sync()
    drive.world.delete("gone")
    connector = await drive.connector_()

    with pytest.raises(Exception) as raised:
        await connector.stream_record(drive.records.records["gone"])

    assert getattr(raised.value, "status_code", None) == 404


async def test_reindex_refreshes_changed_records_and_republishes_unchanged_ones(drive: Harness) -> None:
    drive.world.folder("d", "Docs", parent=ROOT, owner=ME)
    my_file(drive.world, "same", "same.txt", parent="d")
    my_file(drive.world, "changed", "changed.txt", parent="d")
    await drive.sync()
    drive.world.rename("changed", "changed-at-source.txt")
    connector = await drive.connector_()

    await connector.reindex_records([drive.records.records["same"], drive.records.records["changed"]])

    assert drive.records.records["changed"].record_name == "changed-at-source.txt"
    assert [r.external_record_id for r in drive.records.reindexed] == ["same"]


@pytest.mark.xfail(
    strict=True,
    reason="A file at the top of My Drive always looks moved: its stored parent is empty (the drive "
    "root is dropped) but the fresh parent is the root folder id. Reindex then re-saves it as "
    "'changed' instead of asking for a reindex, and an already-indexed file is never re-indexed.",
)
async def test_reindex_of_an_unchanged_file_at_the_top_of_my_drive_asks_for_a_reindex(drive: Harness) -> None:
    my_file(drive.world, "top", "top.txt")
    await drive.sync()
    connector = await drive.connector_()

    await connector.reindex_records([drive.records.records["top"]])

    assert [r.external_record_id for r in drive.records.reindexed] == ["top"]


async def test_folders_are_recorded_as_folders(drive: Harness) -> None:
    drive.world.folder("d", "Docs", parent=ROOT, owner=ME)
    my_file(drive.world, "f", "a.txt", parent="d")

    await drive.sync()

    assert drive.records.records["d"].mime_type == FOLDER
    assert drive.records.records["d"].is_file is False
    assert drive.records.records["f"].parent_external_record_id == "d"
    assert drive.records.records["f"].extension == "txt"
