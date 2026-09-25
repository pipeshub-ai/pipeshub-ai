"""Box incremental sync over the enterprise event stream, against a fake Box API.

Each test first runs a full sync (which anchors the stream cursor), then stages
events and runs the connector again, which takes the incremental path because
the cursor is fresh. The Box SDK underneath is real; its retry waits are recorded.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Any

import pytest
from box_behaviour_fakes import (
    ROOT_ID,
    FakeBoxApi,
    FakeBoxRecordsDb,
    FakeCheckpointStore,
    ready_connector,
)

if TYPE_CHECKING:
    from app.connectors.sources.box.connector import BoxConnector

ALICE, BOB = "u-alice", "u-bob"
ALICE_EMAIL, BOB_EMAIL = "alice@acme.test", "bob@acme.test"


def enterprise(api: FakeBoxApi, db: FakeBoxRecordsDb) -> None:
    api.add_user(ALICE, ALICE_EMAIL, "Alice")
    api.add_user(BOB, BOB_EMAIL, "Bob")
    db.active_emails.update({ALICE_EMAIL, BOB_EMAIL})


def item_event(item_id: str, item_type: str = "file", owner: str = ALICE) -> dict[str, Any]:
    return {"item_type": item_type, "item_id": item_id, "item_name": item_id, "owned_by": {"type": "user", "id": owner}}


def collab_event_source(api: FakeBoxApi, item_id: str, user_id: str, collab_id: str) -> dict[str, Any]:
    return {
        "type": "collaboration", "id": collab_id,
        "item": {"type": api.items[item_id]["type"], "id": item_id},
        "accessible_by": {"type": "user", "id": user_id, "login": api.users[user_id]["login"]},
    }


def by(user_id: str, api: FakeBoxApi) -> dict[str, Any]:
    return {"type": "user", "id": user_id, "login": api.users[user_id]["login"]}


async def synced_connector(api: FakeBoxApi, db: FakeBoxRecordsDb, checkpoints: FakeCheckpointStore) -> BoxConnector:
    connector = await ready_connector(db, checkpoints)
    await connector.run_sync()
    return connector


def full_walks(api: FakeBoxApi) -> int:
    return len([r for r in api.calls("GET", f"/2.0/folders/{ROOT_ID}/items") if r.as_user == ALICE])


class TestCursor:
    async def test_new_events_are_applied_and_the_cursor_moves_to_the_stream_head(self, box_api, db, checkpoints) -> None:
        enterprise(box_api, db)
        connector = await synced_connector(box_api, db, checkpoints)
        box_api.add_file("file-1", "new.pdf", ALICE)
        box_api.add_event("ITEM_UPLOAD", item_event("file-1"), created_by=by(ALICE, box_api))

        await connector.run_sync()

        assert full_walks(box_api) == 1
        assert "file-1" in db.records
        assert box_api.calls("GET", "/2.0/files/file-1")[0].as_user == ALICE
        assert checkpoints.cursor()["cursor"] == box_api.stream_head == "1"

    async def test_a_cursor_older_than_box_keeps_events_triggers_a_full_sync(self, box_api, db, checkpoints) -> None:
        enterprise(box_api, db)
        connector = await synced_connector(box_api, db, checkpoints)
        old = datetime.now(timezone.utc) - timedelta(days=15)
        checkpoints.cursor()["cursor_updated_at"] = int(old.timestamp() * 1000)

        await connector.run_sync()

        assert full_walks(box_api) == 2
        assert checkpoints.cursor()["cursor_updated_at"] > int(old.timestamp() * 1000)

    async def test_a_full_sync_starts_the_retry_count_afresh(self, box_api, db, checkpoints) -> None:
        enterprise(box_api, db)
        connector = await synced_connector(box_api, db, checkpoints)
        old = datetime.now(timezone.utc) - timedelta(days=15)
        checkpoints.cursor().update(cursor_updated_at=int(old.timestamp() * 1000), held_attempts=4)

        await connector.run_sync()

        assert full_walks(box_api) == 2
        assert checkpoints.cursor()["held_attempts"] == 0

    async def test_a_failed_group_refresh_holds_the_batch_until_the_group_is_stored(self, box_api, db, checkpoints) -> None:
        enterprise(box_api, db)
        connector = await synced_connector(box_api, db, checkpoints)
        box_api.add_group("g-new", "New team", (BOB,))
        box_api.add_file("file-1", "new.pdf", ALICE)
        box_api.collaborate("file-1", "g-new", kind="group")
        box_api.add_event("ITEM_UPLOAD", item_event("file-1"), created_by=by(ALICE, box_api))
        before = checkpoints.cursor()["cursor"]
        box_api.fail("GET", "/2.0/groups", 503, times=5)

        await connector.run_sync()

        assert "g-new" not in db.access("file-1")
        assert checkpoints.cursor()["cursor"] == before

        await connector.run_sync()

        assert "g-new" in db.access("file-1")
        assert checkpoints.cursor()["cursor"] == box_api.stream_head

    async def test_a_refresh_that_raises_holds_an_id_only_grant(self, box_api, db, checkpoints) -> None:
        enterprise(box_api, db)
        box_api.add_file("file-1", "plan.pdf", ALICE)
        connector = await synced_connector(box_api, db, checkpoints)
        collab_id = box_api.collaborate("file-1", BOB)
        box_api.add_event(
            "COLLABORATION_INVITE",
            {"type": "collaboration", "id": collab_id, "item": {"type": "file", "id": "file-1"}, "accessible_by": {"type": "user", "id": BOB}},
            created_by=by(ALICE, box_api),
        )
        before = checkpoints.cursor()["cursor"]
        db.failing.add("on_new_app_users")

        await connector.run_sync()

        assert checkpoints.cursor()["cursor"] == before

        db.failing.clear()
        await connector.run_sync()

        assert db.records["file-1"].shared_with_me_record_group_ids == [f"0S:{BOB_EMAIL}"]
        assert checkpoints.cursor()["cursor"] == box_api.stream_head

    async def test_after_giving_up_on_a_failed_refresh_the_run_stops_at_that_page(self, box_api, db, checkpoints) -> None:
        enterprise(box_api, db)
        connector = await synced_connector(box_api, db, checkpoints)
        box_api.page_cap["/2.0/events"] = 1
        for n in (1, 2):
            box_api.add_group(f"g-{n}", f"Team {n}", (BOB,))
            box_api.add_file(f"file-{n}", f"new-{n}.pdf", ALICE)
            box_api.collaborate(f"file-{n}", f"g-{n}", kind="group")
            box_api.add_event("ITEM_UPLOAD", item_event(f"file-{n}"), created_by=by(ALICE, box_api))
        second_event = str(int(box_api.stream_head) - 1)
        box_api.fail("GET", "/2.0/groups", 503, times=25)

        for _ in range(5):
            await connector.run_sync()

        assert "g-2" not in db.access("file-2")
        assert checkpoints.cursor()["cursor"] == second_event

        await connector.run_sync()

        assert "g-2" in db.access("file-2")
        assert checkpoints.cursor()["cursor"] == box_api.stream_head

    async def test_a_failed_event_page_leaves_the_cursor_where_it_was(self, box_api, db, checkpoints, sdk_sleeps) -> None:
        enterprise(box_api, db)
        connector = await synced_connector(box_api, db, checkpoints)
        box_api.add_file("file-1", "new.pdf", ALICE)
        box_api.add_event("ITEM_UPLOAD", item_event("file-1"), created_by=by(ALICE, box_api))
        before = checkpoints.cursor()["cursor"]
        box_api.fail("GET", "/2.0/events", 503, times=10)

        await connector.run_sync()

        assert len(sdk_sleeps) == 4
        assert checkpoints.cursor()["cursor"] == before
        assert "file-1" not in db.records

    async def test_an_event_batch_that_crashes_leaves_the_cursor_where_it_was(self, box_api, db, checkpoints) -> None:
        enterprise(box_api, db)
        box_api.add_file("file-1", "plan.pdf", ALICE)
        collab_id = box_api.collaborate("file-1", BOB)
        connector = await synced_connector(box_api, db, checkpoints)
        box_api.add_event("COLLABORATION_REMOVE", collab_event_source(box_api, "file-1", BOB, collab_id))
        before = checkpoints.cursor()["cursor"]
        db.fail_lookup_for.add("file-1")

        await connector.run_sync()

        assert checkpoints.cursor()["cursor"] == before

    async def test_a_changed_file_box_would_not_return_holds_the_cursor_for_a_retry(self, box_api, db, checkpoints) -> None:
        enterprise(box_api, db)
        connector = await synced_connector(box_api, db, checkpoints)
        box_api.add_file("file-1", "new.pdf", ALICE)
        box_api.add_event("ITEM_UPLOAD", item_event("file-1"), created_by=by(ALICE, box_api))
        before = checkpoints.cursor()["cursor"]
        box_api.fail("GET", "/2.0/files/file-1", 503, times=5)

        await connector.run_sync()

        assert checkpoints.cursor()["cursor"] == before
        assert checkpoints.cursor()["held_attempts"] == 1

        await connector.run_sync()

        assert "file-1" in db.records
        assert checkpoints.cursor()["cursor"] == box_api.stream_head
        assert checkpoints.cursor()["held_attempts"] == 0

    async def test_a_batch_that_keeps_failing_is_passed_over_after_five_attempts(self, box_api, db, checkpoints) -> None:
        enterprise(box_api, db)
        connector = await synced_connector(box_api, db, checkpoints)
        box_api.add_file("file-1", "stuck.pdf", ALICE)
        box_api.add_event("ITEM_UPLOAD", item_event("file-1"), created_by=by(ALICE, box_api))
        before = checkpoints.cursor()["cursor"]
        box_api.fail("GET", "/2.0/files/file-1", 503, times=1000)

        for _ in range(4):
            await connector.run_sync()
            assert checkpoints.cursor()["cursor"] == before
        await connector.run_sync()

        assert checkpoints.cursor()["cursor"] == box_api.stream_head
        assert checkpoints.cursor()["held_attempts"] == 0

        box_api.add_file("file-2", "next.pdf", ALICE)
        box_api.add_event("ITEM_UPLOAD", item_event("file-2"), created_by=by(ALICE, box_api))
        after_skip = checkpoints.cursor()["cursor"]
        box_api.fail("GET", "/2.0/files/file-2", 503, times=5)

        await connector.run_sync()

        assert checkpoints.cursor()["cursor"] == after_skip
        assert checkpoints.cursor()["held_attempts"] == 1

    async def test_an_upload_whose_second_collaborator_page_fails_is_granted_on_the_retry(self, box_api, db, checkpoints) -> None:
        enterprise(box_api, db)
        connector = await synced_connector(box_api, db, checkpoints)
        box_api.default_page = box_api.max_page = 2
        for n in range(3):
            box_api.add_user(f"u-{n}", f"user{n}@acme.test")
        box_api.add_file("file-1", "new.pdf", ALICE)
        for n in range(3):
            box_api.collaborate("file-1", f"u-{n}")
        box_api.add_event("ITEM_UPLOAD", item_event("file-1"), created_by=by(ALICE, box_api))
        before = checkpoints.cursor()["cursor"]
        box_api.fail("GET", "/2.0/files/file-1/collaborations", 503, times=5, query={"marker": "2"})

        await connector.run_sync()

        assert checkpoints.cursor()["cursor"] == before

        await connector.run_sync()

        assert db.access("file-1") == {"user0@acme.test", "user1@acme.test", "user2@acme.test"}
        assert checkpoints.cursor()["cursor"] == box_api.stream_head

    async def test_a_box_error_is_logged_without_the_access_token(self, box_api, db, checkpoints, caplog) -> None:
        enterprise(box_api, db)
        connector = await synced_connector(box_api, db, checkpoints)
        box_api.fail("GET", "/2.0/events", 503, times=10)

        with caplog.at_level(logging.DEBUG, logger="test.box"):
            await connector.run_sync()

        assert "Failed to fetch events: 503 staged failure" in caplog.text
        assert "tok-1" not in caplog.text and "Bearer" not in caplog.text

    async def test_a_webhook_with_an_unreadable_cursor_does_not_skip_to_now(self, box_api, db, checkpoints) -> None:
        enterprise(box_api, db)
        connector = await synced_connector(box_api, db, checkpoints)
        box_api.add_file("file-1", "new.pdf", ALICE)
        box_api.add_event("ITEM_UPLOAD", item_event("file-1"), created_by=by(ALICE, box_api))
        saved = dict(checkpoints.cursor())
        polls_before = len(box_api.calls("GET", "/2.0/events"))
        checkpoints.fail_reads = True

        await connector.run_incremental_sync()

        assert len(box_api.calls("GET", "/2.0/events")) == polls_before
        assert checkpoints.cursor() == saved


class TestContentEvents:
    async def test_an_upload_into_a_new_folder_also_stores_the_folder(self, box_api, db, checkpoints) -> None:
        enterprise(box_api, db)
        connector = await synced_connector(box_api, db, checkpoints)
        box_api.add_folder("fold-new", "New", ALICE)
        box_api.add_file("file-1", "new.pdf", ALICE, parent="fold-new")
        box_api.add_event("ITEM_UPLOAD", item_event("file-1"), created_by=by(ALICE, box_api))

        await connector.run_sync()

        assert db.records["file-1"].parent_external_record_id == "fold-new"
        assert "fold-new" in db.records

    async def test_a_moved_file_gets_its_new_parent(self, box_api, db, checkpoints) -> None:
        enterprise(box_api, db)
        box_api.add_folder("fold-a", "A", ALICE)
        box_api.add_folder("fold-b", "B", ALICE)
        box_api.add_file("file-1", "plan.pdf", ALICE, parent="fold-a")
        connector = await synced_connector(box_api, db, checkpoints)
        box_api.items["file-1"]["parent"] = "fold-b"
        box_api.add_event("ITEM_MOVE", item_event("file-1"), created_by=by(ALICE, box_api))

        await connector.run_sync()

        assert db.records["file-1"].parent_external_record_id == "fold-b"
        assert db.records["file-1"].path == "/All Files/B/plan.pdf"

    async def test_a_new_folder_event_syncs_the_folder_and_everything_in_it(self, box_api, db, checkpoints) -> None:
        enterprise(box_api, db)
        connector = await synced_connector(box_api, db, checkpoints)
        box_api.add_folder("fold-new", "New", ALICE)
        box_api.add_folder("fold-sub", "Sub", ALICE, parent="fold-new")
        box_api.add_file("file-deep", "deep.txt", ALICE, parent="fold-sub")
        box_api.add_event("ITEM_CREATE", item_event("fold-new", "folder"), created_by=by(ALICE, box_api))

        await connector.run_sync()

        assert {"fold-new", "fold-sub", "file-deep"} <= set(db.records)

    async def test_a_repeated_event_is_applied_once(self, box_api, db, checkpoints) -> None:
        enterprise(box_api, db)
        connector = await synced_connector(box_api, db, checkpoints)
        box_api.add_file("file-1", "new.pdf", ALICE)
        box_api.add_event("ITEM_UPLOAD", item_event("file-1"), created_by=by(ALICE, box_api))
        box_api.events.append(dict(box_api.events[0]))

        await connector.run_sync()

        assert len(box_api.calls("GET", "/2.0/files/file-1")) == 1

    @pytest.mark.xfail(
        strict=True,
        reason=(
            "Left alone: trash and delete events are recognised but never applied "
            "(_execute_deletions only logs 'Backend support pending'), so a file "
            "deleted in Box stays searchable. Turning deletion on is a product change."
        ),
    )
    async def test_a_trashed_file_is_deleted(self, box_api, db, checkpoints) -> None:
        enterprise(box_api, db)
        box_api.add_file("file-1", "plan.pdf", ALICE)
        connector = await synced_connector(box_api, db, checkpoints)
        record_id = db.records["file-1"].id
        box_api.add_event("ITEM_TRASH", item_event("file-1"), created_by=by(ALICE, box_api))

        await connector.run_sync()

        assert db.deleted_records == [record_id]

    async def test_a_delete_followed_by_a_restore_keeps_the_file(self, box_api, db, checkpoints) -> None:
        enterprise(box_api, db)
        box_api.add_file("file-1", "plan.pdf", ALICE)
        connector = await synced_connector(box_api, db, checkpoints)
        box_api.add_event("ITEM_TRASH", item_event("file-1"), created_by=by(ALICE, box_api))
        box_api.add_event("ITEM_UNDELETE_VIA_TRASH", item_event("file-1"), created_by=by(ALICE, box_api))

        await connector.run_sync()

        assert db.deleted_records == []
        assert len(box_api.calls("GET", "/2.0/files/file-1")) == 1


class TestSharingEvents:
    async def test_an_invite_puts_the_file_in_the_collaborators_shared_with_me(self, box_api, db, checkpoints) -> None:
        enterprise(box_api, db)
        box_api.add_file("file-1", "plan.pdf", ALICE)
        connector = await synced_connector(box_api, db, checkpoints)
        collab_id = box_api.collaborate("file-1", BOB, role="editor")
        box_api.add_event(
            "COLLABORATION_INVITE", collab_event_source(box_api, "file-1", BOB, collab_id),
            created_by=by(ALICE, box_api), additional_details={"collab_id": collab_id},
        )

        await connector.run_sync()

        assert BOB_EMAIL in db.access("file-1")
        assert db.records["file-1"].shared_with_me_record_group_ids == [f"0S:{BOB_EMAIL}"]
        assert {r.as_user for r in box_api.calls("GET", "/2.0/files/file-1")} >= {BOB}

    async def test_a_removed_collaborator_loses_the_file(self, box_api, db, checkpoints) -> None:
        enterprise(box_api, db)
        box_api.add_file("file-1", "plan.pdf", ALICE)
        collab_id = box_api.collaborate("file-1", BOB)
        connector = await synced_connector(box_api, db, checkpoints)
        assert BOB_EMAIL in db.access("file-1")
        box_api.collaborations["file-1"].clear()
        box_api.add_event("COLLABORATION_REMOVE", collab_event_source(box_api, "file-1", BOB, collab_id))

        await connector.run_sync()

        assert BOB_EMAIL not in db.access("file-1")

    async def test_a_removed_folder_collaborator_loses_everything_inside(self, box_api, db, checkpoints) -> None:
        enterprise(box_api, db)
        box_api.add_folder("fold-a", "Team", ALICE)
        box_api.add_file("file-1", "plan.pdf", ALICE, parent="fold-a")
        collab_id = box_api.collaborate("fold-a", BOB)
        box_api.collaborate("file-1", BOB)
        connector = await synced_connector(box_api, db, checkpoints)
        bob_id = db.app_users[BOB_EMAIL].id
        box_api.add_event("COLLABORATION_REMOVE", collab_event_source(box_api, "fold-a", BOB, collab_id))

        await connector.run_sync()

        assert set(db.removed_access) == {("fold-a", bob_id), ("file-1", bob_id)}
        assert BOB_EMAIL not in db.access("file-1")

    async def test_an_invite_revoked_in_the_same_batch_is_not_shared(self, box_api, db, checkpoints) -> None:
        enterprise(box_api, db)
        box_api.add_file("file-1", "plan.pdf", ALICE)
        connector = await synced_connector(box_api, db, checkpoints)
        source = {"type": "collaboration", "id": "collab-x", "item": {"type": "file", "id": "file-1"},
                  "accessible_by": {"type": "user", "id": BOB, "login": BOB_EMAIL}}
        details = {"collab_id": "collab-x"}
        box_api.add_event("COLLABORATION_INVITE", source, created_by=by(ALICE, box_api), additional_details=details)
        box_api.add_event("COLLABORATION_REMOVE", source, created_by=by(ALICE, box_api), additional_details=details)

        await connector.run_sync()

        assert not [r for r in box_api.calls("GET", "/2.0/files/file-1") if r.as_user == BOB]
        assert BOB_EMAIL not in db.access("file-1")

    async def test_a_failed_read_of_a_replayed_shared_folder_leaves_no_cursor(self, box_api, db, checkpoints) -> None:
        enterprise(box_api, db)
        box_api.add_folder("fold-a", "Team", ALICE)
        box_api.add_file("file-1", "plan.pdf", ALICE, parent="fold-a")
        collab_id = box_api.collaborate("fold-a", BOB)
        box_api.add_event(
            "COLLABORATION_INVITE", collab_event_source(box_api, "fold-a", BOB, collab_id),
            created_by=by(ALICE, box_api), additional_details={"collab_id": collab_id},
        )
        box_api.fail("GET", "/2.0/folders/fold-a/items", 503, times=5, as_user=BOB)
        connector = await ready_connector(db, checkpoints)

        await connector.run_sync()

        assert checkpoints.cursor() is None

        await connector.run_sync()

        assert db.records["file-1"].shared_with_me_record_group_ids == [f"0S:{BOB_EMAIL}"]

    async def test_a_full_sync_replays_shares_made_before_the_connector_existed(self, box_api, db, checkpoints) -> None:
        enterprise(box_api, db)
        box_api.add_file("file-1", "plan.pdf", ALICE)
        collab_id = box_api.collaborate("file-1", BOB)
        box_api.add_event(
            "COLLABORATION_INVITE", collab_event_source(box_api, "file-1", BOB, collab_id),
            created_by=by(ALICE, box_api), additional_details={"collab_id": collab_id},
        )

        await synced_connector(box_api, db, checkpoints)

        history = [r for r in box_api.calls("GET", "/2.0/events") if r.query.get("stream_type") == "admin_logs"]
        assert history and history[0].query["stream_position"] == "0"
        assert db.records["file-1"].shared_with_me_record_group_ids == [f"0S:{BOB_EMAIL}"]
