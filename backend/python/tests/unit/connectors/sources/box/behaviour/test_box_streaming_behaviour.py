"""Opening a Box file, and reindexing, against a fake Box API with the real SDK.

A user opening a file gets either the file or a plain-language reason with a
next step; these tests pin which reason each Box failure turns into.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import pytest
from box_behaviour_fakes import (
    FakeBoxApi,
    FakeBoxRecordsDb,
    FakeCheckpointStore,
    ready_connector,
)
from fastapi import HTTPException

if TYPE_CHECKING:
    from app.connectors.sources.box.connector import BoxConnector

ALICE, ALICE_EMAIL = "u-alice", "alice@acme.test"


async def synced(api: FakeBoxApi, db: FakeBoxRecordsDb, checkpoints: FakeCheckpointStore) -> BoxConnector:
    api.add_user(ALICE, ALICE_EMAIL, "Alice")
    db.active_emails.add(ALICE_EMAIL)
    api.add_file("file-1", "plan.pdf", ALICE)
    connector = await ready_connector(db, checkpoints)
    await connector.run_sync()
    return connector


async def open_error(connector: BoxConnector, db: FakeBoxRecordsDb) -> HTTPException:
    with pytest.raises(HTTPException) as err:
        await connector.get_signed_url(db.records["file-1"])
    return err.value


class TestOpeningAFile:
    async def test_the_download_link_is_fetched_as_the_files_owner(self, box_api, db, checkpoints) -> None:
        connector = await synced(box_api, db, checkpoints)

        url = await connector.get_signed_url(db.records["file-1"])

        assert url == "https://dl.boxcloud.test/d/file-1"
        assert box_api.calls("GET", "/2.0/files/file-1/content")[0].as_user == ALICE

    async def test_a_deleted_file_says_it_no_longer_exists(self, box_api, db, checkpoints, sdk_sleeps) -> None:
        connector = await synced(box_api, db, checkpoints)
        del box_api.items["file-1"]

        error = await open_error(connector, db)

        assert error.status_code == 404
        assert "no longer exists" in error.detail and "deleted or moved" in error.detail
        assert sdk_sleeps == []

    async def test_a_forbidden_file_says_access_was_denied(self, box_api, db, checkpoints) -> None:
        connector = await synced(box_api, db, checkpoints)
        box_api.fail("GET", "/2.0/files/file-1/content", 403)

        error = await open_error(connector, db)

        assert error.status_code == 403
        assert "denied" in error.detail and "permission" in error.detail

    async def test_a_lasting_rate_limit_asks_to_try_again_shortly(self, box_api, db, checkpoints, sdk_sleeps) -> None:
        connector = await synced(box_api, db, checkpoints)
        box_api.fail("GET", "/2.0/files/file-1/content", 429, times=10, headers={"Retry-After": "30"})

        error = await open_error(connector, db)

        assert sdk_sleeps == [30.0] * 4
        assert error.status_code == 429
        assert "try again shortly" in error.detail

    async def test_a_lasting_rate_limit_passes_on_how_long_to_wait(self, box_api, db, checkpoints) -> None:
        connector = await synced(box_api, db, checkpoints)
        box_api.fail("GET", "/2.0/files/file-1/content", 429, times=10, headers={"Retry-After": "30"})

        error = await open_error(connector, db)

        assert error.headers == {"Retry-After": "30"}

    async def test_a_box_outage_asks_to_try_again_later(self, box_api, db, checkpoints) -> None:
        connector = await synced(box_api, db, checkpoints)
        box_api.fail("GET", "/2.0/files/file-1/content", 503, times=10)

        error = await open_error(connector, db)

        assert error.status_code == 502
        assert "try again later" in error.detail

    async def test_a_failed_download_is_logged_without_the_access_token(self, box_api, db, checkpoints, caplog) -> None:
        connector = await synced(box_api, db, checkpoints)
        box_api.fail("GET", "/2.0/files/file-1/content", 503, times=10)

        with caplog.at_level(logging.DEBUG, logger="test.box"):
            await open_error(connector, db)

        assert "Error getting temporary download URL" in caplog.text
        assert "tok-1" not in caplog.text

    async def test_revoked_app_credentials_ask_for_a_reconnect(self, box_api, db, checkpoints) -> None:
        connector = await synced(box_api, db, checkpoints)
        box_api.expire("tok-1")
        box_api.fail("POST", "/oauth2/token", 401, times=10)

        error = await open_error(connector, db)

        assert error.status_code == 409
        assert "Reconnect" in error.detail

    @pytest.mark.xfail(
        strict=True,
        reason=(
            "Left alone: syncing a share for a colleague rewrites the owner's record with no "
            "drive (external_record_group_id=None), and opening or indexing a file uses that "
            "drive as the As-User. Without it Box answers 404, so a file stops opening for "
            "everyone as soon as it is shared. How shared records are grouped is a design call."
        ),
    )
    async def test_a_file_shared_with_a_colleague_can_still_be_opened(self, box_api, db, checkpoints) -> None:
        connector = await synced(box_api, db, checkpoints)
        box_api.add_user("u-bob", "bob@acme.test", "Bob")
        db.active_emails.add("bob@acme.test")
        collab_id = box_api.collaborate("file-1", "u-bob")
        box_api.add_event(
            "COLLABORATION_INVITE",
            {"type": "collaboration", "id": collab_id, "item": {"type": "file", "id": "file-1"},
             "accessible_by": {"type": "user", "id": "u-bob", "login": "bob@acme.test"}},
            created_by={"type": "user", "id": ALICE, "login": ALICE_EMAIL},
            additional_details={"collab_id": collab_id},
        )
        await connector.run_sync()

        assert await connector.get_signed_url(db.records["file-1"]) == "https://dl.boxcloud.test/d/file-1"

    async def test_a_connector_that_never_initialised_says_it_is_not_connected(self, box_api, db, checkpoints) -> None:
        connector = await synced(box_api, db, checkpoints)
        await connector.cleanup()

        with pytest.raises(HTTPException) as err:
            await connector.stream_record(db.records["file-1"])

        assert err.value.status_code == 409
        assert "not connected" in err.value.detail and "settings" in err.value.detail


class TestReindex:
    async def test_changed_files_are_rewritten_and_deleted_ones_are_skipped(self, box_api, db, checkpoints) -> None:
        connector = await synced(box_api, db, checkpoints)
        box_api.add_file("file-2", "gone.pdf", ALICE)
        checkpoints.sync_points.clear()
        await connector.run_sync()
        records = [db.records["file-1"], db.records["file-2"]]
        box_api.items["file-1"]["modified_at"] = "2024-06-01T00:00:00Z"
        del box_api.items["file-2"]
        batches_before = len(db.record_batches)

        await connector.reindex_records(records)

        assert [r.external_record_id for r in db.record_batches[batches_before]] == ["file-1"]
        assert db.reindexed == []
        assert {r.as_user for r in box_api.calls("GET", "/2.0/files/file-1")} == {ALICE}
