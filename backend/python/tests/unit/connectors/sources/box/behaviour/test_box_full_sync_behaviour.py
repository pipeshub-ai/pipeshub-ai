"""Box full sync, driven over a fake Box API with the real Box SDK underneath.

The connector, ``BoxDataSource`` and ``box_sdk_gen`` (auth, retries, token
refresh) are real; every HTTP request is answered by an in-memory Box and our
databases are in-memory fakes. SDK retry waits are recorded, not slept.
"""

import logging
from typing import Any

import pytest
from box_behaviour_fakes import (
    CONNECTOR_ID,
    ROOT_ID,
    SERVICE_ACCOUNT_ID,
    FakeBoxApi,
    FakeBoxRecordsDb,
    FakeCheckpointStore,
    FakeConfigService,
    ccg_config,
    ready_connector,
)

from app.config.constants.arangodb import MimeTypes
from app.connectors.sources.box.connector import BoxConnector
from app.models.permission import EntityType, PermissionType

ALICE, BOB = "u-alice", "u-bob"
ALICE_EMAIL, BOB_EMAIL = "alice@acme.test", "bob@acme.test"


def enterprise(api: FakeBoxApi, db: FakeBoxRecordsDb) -> None:
    api.add_user(ALICE, ALICE_EMAIL, "Alice")
    api.add_user(BOB, BOB_EMAIL, "Bob")
    db.active_emails.update({ALICE_EMAIL, BOB_EMAIL})


def listings(api: FakeBoxApi, folder_id: str) -> list[Any]:
    return api.calls("GET", f"/2.0/folders/{folder_id}/items")


class TestAuthentication:
    async def test_the_connector_signs_in_with_client_credentials_for_the_enterprise(self, box_api, db, checkpoints) -> None:
        enterprise(box_api, db)
        connector = await ready_connector(db, checkpoints)

        await connector.run_sync()

        assert box_api.token_forms[0]["grant_type"] == "client_credentials"
        assert box_api.token_forms[0]["box_subject_type"] == "enterprise"
        assert box_api.token_forms[0]["box_subject_id"] == "ent-1"
        assert {r.token for r in box_api.requests if r.path != "/oauth2/token"} == {"tok-1"}

    async def test_an_expired_token_is_refreshed_and_the_sync_carries_on(self, box_api, db, checkpoints) -> None:
        enterprise(box_api, db)
        box_api.add_file("file-1", "notes.txt", ALICE)
        connector = await ready_connector(db, checkpoints)
        assert (await connector.test_connection_and_access()) is True
        box_api.expire("tok-1")

        await connector.run_sync()

        assert len(box_api.token_forms) == 2
        assert "file-1" in db.records
        assert box_api.requests[-1].token == "tok-2"

    async def test_missing_credentials_fail_init_without_calling_box(self, box_api, db, checkpoints) -> None:
        config = ccg_config()
        del config["auth"]["clientSecret"]
        connector = BoxConnector(
            logging.getLogger("test.box"), db, checkpoints,
            FakeConfigService(CONNECTOR_ID, config), CONNECTOR_ID, "team", "creator-1",
        )

        assert await connector.init() is False
        assert box_api.requests == []
        assert await connector.test_connection_and_access() is False


class TestFullSyncWalk:
    async def test_each_active_user_tree_is_walked_as_that_user(self, box_api, db, checkpoints) -> None:
        enterprise(box_api, db)
        box_api.add_user("u-carol", "carol@acme.test")
        box_api.add_folder("fold-a", "Plans", ALICE)
        box_api.add_file("file-a", "q3.pdf", ALICE, parent="fold-a")
        box_api.add_file("file-b", "bob.docx", BOB)
        box_api.add_file("file-c", "carol.txt", "u-carol")
        connector = await ready_connector(db, checkpoints)

        await connector.run_sync()

        assert set(db.records) == {"fold-a", "file-a", "file-b"}
        assert {r.as_user for r in listings(box_api, ROOT_ID)} == {ALICE, BOB}
        assert {r.as_user for r in listings(box_api, "fold-a")} == {ALICE}
        nested = db.records["file-a"]
        assert nested.parent_external_record_id == "fold-a"
        assert nested.path == "/All Files/Plans/q3.pdf"
        assert nested.external_record_group_id == ALICE
        assert db.records["fold-a"].mime_type == MimeTypes.FOLDER.value
        assert db.records["fold-a"].parent_external_record_id is None
        assert {"u-alice", "0S:alice@acme.test", "u-bob", "0S:bob@acme.test"} <= set(db.record_groups)

    async def test_the_last_page_of_a_folder_ends_the_listing(self, box_api, db, checkpoints) -> None:
        enterprise(box_api, db)
        box_api.max_page = 2
        for n in range(5):
            box_api.add_file(f"file-{n}", f"doc-{n}.txt", ALICE)
        connector = await ready_connector(db, checkpoints)

        await connector.run_sync()

        alice_pages = [r.query["offset"] for r in listings(box_api, ROOT_ID) if r.as_user == ALICE]
        assert alice_pages == ["0", "2", "4"]
        assert {f"file-{n}" for n in range(5)} <= set(db.records)

    async def test_a_page_after_a_subfolder_is_still_listed_as_the_owner(self, box_api, db, checkpoints) -> None:
        enterprise(box_api, db)
        box_api.max_page = 2
        box_api.add_folder("fold-a", "Plans", ALICE)
        box_api.add_file("file-1", "one.txt", ALICE)
        box_api.add_file("file-2", "two.txt", ALICE)
        connector = await ready_connector(db, checkpoints)

        await connector.run_sync()

        assert {r.as_user for r in listings(box_api, ROOT_ID) if r.query["offset"] == "2"} == {ALICE}
        assert "file-2" in db.records

    async def test_a_second_full_sync_finds_new_files_inside_known_folders(self, box_api, db, checkpoints) -> None:
        enterprise(box_api, db)
        box_api.add_folder("fold-a", "Plans", ALICE)
        box_api.add_file("file-1", "one.txt", ALICE, parent="fold-a")
        connector = await ready_connector(db, checkpoints)
        await connector.run_sync()
        box_api.add_file("file-2", "two.txt", ALICE, parent="fold-a")
        checkpoints.sync_points.clear()

        await connector.run_sync()

        assert "file-2" in db.records

    async def test_an_inactive_user_is_not_walked(self, box_api, db, checkpoints) -> None:
        enterprise(box_api, db)
        db.active_emails.discard(BOB_EMAIL)
        box_api.add_file("file-b", "bob.docx", BOB)
        connector = await ready_connector(db, checkpoints)

        await connector.run_sync()

        assert "file-b" not in db.records
        assert {r.as_user for r in listings(box_api, ROOT_ID)} == {ALICE}

    async def test_the_extension_filter_skips_files_but_not_folders(self, box_api, db, checkpoints) -> None:
        enterprise(box_api, db)
        box_api.add_folder("fold-a", "Plans", ALICE)
        box_api.add_file("file-pdf", "a.pdf", ALICE, parent="fold-a")
        box_api.add_file("file-txt", "b.txt", ALICE, parent="fold-a")
        config = ccg_config(sync={"values": {"file_extensions": {"operator": "in", "type": "list", "value": ["pdf"]}}})
        connector = await ready_connector(db, checkpoints, config)

        await connector.run_sync()

        assert set(db.records) == {"fold-a", "file-pdf"}


class TestSharingAndPermissions:
    async def test_collaborators_and_shared_links_become_permissions(self, box_api, db, checkpoints) -> None:
        enterprise(box_api, db)
        box_api.add_group("g-eng", "Engineering", (BOB,))
        box_api.add_file("file-1", "plan.pdf", ALICE, shared_link_access="company")
        box_api.add_file("file-2", "public.pdf", ALICE, shared_link_access="open")
        box_api.collaborate("file-1", BOB, role="editor")
        box_api.collaborate("file-1", "g-eng", role="viewer", kind="group")
        connector = await ready_connector(db, checkpoints)

        await connector.run_sync()

        perms = {p.external_id: p for p in db.permissions["file-1"].values()}
        assert perms[BOB].type == PermissionType.WRITE
        assert perms["g-eng"].entity_type == EntityType.GROUP
        assert perms["ORG_org-1"].entity_type == EntityType.GROUP
        assert db.records["file-1"].is_shared is True
        assert db.access("file-2") == {"PUBLIC"}
        assert {"PUBLIC", "ORG_org-1", "g-eng"} <= set(db.user_groups)

    async def test_every_page_of_a_file_collaborator_list_is_read(self, box_api, db, checkpoints) -> None:
        enterprise(box_api, db)
        box_api.default_page = box_api.max_page = 2
        for n in range(3):
            box_api.add_user(f"u-{n}", f"user{n}@acme.test")
        box_api.add_file("file-1", "plan.pdf", ALICE)
        for n in range(3):
            box_api.collaborate("file-1", f"u-{n}")
        connector = await ready_connector(db, checkpoints)

        await connector.run_sync()

        assert db.access("file-1") == {"user0@acme.test", "user1@acme.test", "user2@acme.test"}

    async def test_a_failed_second_page_of_collaborators_is_read_again_on_the_next_run(self, box_api, db, checkpoints) -> None:
        enterprise(box_api, db)
        box_api.default_page = box_api.max_page = 2
        for n in range(3):
            box_api.add_user(f"u-{n}", f"user{n}@acme.test")
        box_api.add_file("file-1", "plan.pdf", ALICE)
        for n in range(3):
            box_api.collaborate("file-1", f"u-{n}")
        box_api.fail("GET", "/2.0/files/file-1/collaborations", 503, times=5, query={"marker": "2"})
        connector = await ready_connector(db, checkpoints)

        await connector.run_sync()

        assert "file-1" in db.records
        assert checkpoints.cursor() is None

        await connector.run_sync()

        assert db.access("file-1") == {"user0@acme.test", "user1@acme.test", "user2@acme.test"}

    async def test_a_failed_collaborator_read_keeps_the_access_already_stored(self, box_api, db, checkpoints, sdk_sleeps) -> None:
        enterprise(box_api, db)
        box_api.add_file("file-1", "plan.pdf", ALICE)
        box_api.collaborate("file-1", BOB, role="editor")
        connector = await ready_connector(db, checkpoints)
        await connector.run_sync()
        assert db.access("file-1") == {BOB_EMAIL}
        box_api.fail("GET", "/2.0/files/file-1/collaborations", 503, times=10)
        checkpoints.sync_points.clear()

        await connector.run_sync()

        assert len(box_api.calls("GET", "/2.0/files/file-1/collaborations")) == 1 + 5
        assert db.access("file-1") == {BOB_EMAIL}

    async def test_a_forbidden_collaborator_read_is_not_retried(self, box_api, db, checkpoints, sdk_sleeps) -> None:
        enterprise(box_api, db)
        box_api.add_file("file-1", "plan.pdf", ALICE)
        box_api.fail("GET", "/2.0/files/file-1/collaborations", 403, times=10)
        connector = await ready_connector(db, checkpoints)

        await connector.run_sync()

        assert len(box_api.calls("GET", "/2.0/files/file-1/collaborations")) == 1
        assert sdk_sleeps == []
        assert "file-1" in db.records


class TestRateLimitsAndRetries:
    async def test_a_429_waits_for_retry_after_then_succeeds(self, box_api, db, checkpoints, sdk_sleeps) -> None:
        enterprise(box_api, db)
        box_api.add_file("file-1", "plan.pdf", ALICE)
        box_api.fail("GET", "/2.0/folders/0/items", 429, headers={"Retry-After": "7"}, as_user=ALICE)
        connector = await ready_connector(db, checkpoints)

        await connector.run_sync()

        assert sdk_sleeps == [7.0]
        assert "file-1" in db.records

    async def test_a_brief_server_error_is_retried_with_backoff(self, box_api, db, checkpoints, sdk_sleeps) -> None:
        enterprise(box_api, db)
        box_api.add_file("file-1", "plan.pdf", ALICE)
        box_api.fail("GET", "/2.0/folders/0/items", 502, times=2, as_user=ALICE)
        connector = await ready_connector(db, checkpoints)

        await connector.run_sync()

        assert len(sdk_sleeps) == 2 and all(s > 0 for s in sdk_sleeps)
        assert "file-1" in db.records

    async def test_retries_stop_after_five_attempts_and_other_users_still_sync(self, box_api, db, checkpoints, sdk_sleeps) -> None:
        enterprise(box_api, db)
        box_api.add_file("file-a", "a.txt", ALICE)
        box_api.add_file("file-b", "b.txt", BOB)
        box_api.fail("GET", "/2.0/folders/0/items", 429, times=50, as_user=ALICE)
        connector = await ready_connector(db, checkpoints)

        await connector.run_sync()

        assert len([r for r in listings(box_api, ROOT_ID) if r.as_user == ALICE]) == 5
        assert "file-a" not in db.records
        assert "file-b" in db.records

    async def test_a_missing_folder_is_not_retried(self, box_api, db, checkpoints, sdk_sleeps) -> None:
        enterprise(box_api, db)
        box_api.add_folder("fold-a", "Gone", ALICE)
        box_api.add_file("file-b", "b.txt", BOB)
        box_api.fail("GET", "/2.0/folders/fold-a/items", 404, times=10)
        connector = await ready_connector(db, checkpoints)

        await connector.run_sync()

        assert len(listings(box_api, "fold-a")) == 1
        assert sdk_sleeps == []
        assert "file-b" in db.records


class TestGroups:
    async def test_groups_and_their_members_are_stored(self, box_api, db, checkpoints) -> None:
        enterprise(box_api, db)
        box_api.add_group("g-eng", "Engineering", (ALICE, BOB))
        connector = await ready_connector(db, checkpoints)

        await connector.run_sync()

        assert db.user_groups["g-eng"].name == "Engineering"
        assert db.group_members["g-eng"] == [ALICE_EMAIL, BOB_EMAIL]

    async def test_every_page_of_groups_is_read(self, box_api, db, checkpoints) -> None:
        enterprise(box_api, db)
        box_api.default_page = 2
        for n in range(3):
            box_api.add_group(f"g-{n}", f"Group {n}", (ALICE,))
        connector = await ready_connector(db, checkpoints)

        await connector.run_sync()

        assert {"g-0", "g-1", "g-2"} <= set(db.user_groups)

    async def test_every_page_of_a_group_member_list_is_read(self, box_api, db, checkpoints) -> None:
        enterprise(box_api, db)
        box_api.add_user("u-carol", "carol@acme.test")
        box_api.page_cap["/2.0/groups/g-eng/memberships"] = 2
        box_api.add_group("g-eng", "Engineering", (ALICE, BOB, "u-carol"))
        connector = await ready_connector(db, checkpoints)

        await connector.run_sync()

        assert db.group_members["g-eng"] == [ALICE_EMAIL, BOB_EMAIL, "carol@acme.test"]

    async def test_a_failed_member_read_keeps_the_members_already_stored(self, box_api, db, checkpoints) -> None:
        enterprise(box_api, db)
        box_api.add_group("g-eng", "Engineering", (ALICE, BOB))
        connector = await ready_connector(db, checkpoints)
        await connector.run_sync()
        box_api.fail("GET", "/2.0/groups/g-eng/memberships", 500, times=10)
        checkpoints.sync_points.clear()

        await connector.run_sync()

        assert db.group_members["g-eng"] == [ALICE_EMAIL, BOB_EMAIL]

    async def test_a_failed_group_listing_deletes_no_stored_group(self, box_api, db, checkpoints) -> None:
        enterprise(box_api, db)
        box_api.add_group("g-eng", "Engineering", (ALICE,))
        connector = await ready_connector(db, checkpoints)
        await connector.run_sync()
        box_api.fail("GET", "/2.0/groups", 503, times=10)
        checkpoints.sync_points.clear()

        await connector.run_sync()

        assert db.deleted_groups == []
        assert "g-eng" in db.user_groups

    async def test_a_failed_group_listing_leaves_no_cursor_and_the_next_run_grants_the_group(self, box_api, db, checkpoints) -> None:
        enterprise(box_api, db)
        box_api.add_group("g-eng", "Engineering", (BOB,))
        box_api.add_file("file-1", "plan.pdf", ALICE)
        box_api.collaborate("file-1", "g-eng", kind="group")
        box_api.fail("GET", "/2.0/groups", 503, times=5)
        connector = await ready_connector(db, checkpoints)

        await connector.run_sync()

        assert "g-eng" not in db.access("file-1")
        assert checkpoints.cursor() is None

        await connector.run_sync()

        assert "g-eng" in db.user_groups
        assert "g-eng" in db.access("file-1")

    async def test_a_failed_member_read_of_a_new_group_leaves_no_cursor(self, box_api, db, checkpoints) -> None:
        enterprise(box_api, db)
        box_api.add_group("g-eng", "Engineering", (BOB,))
        box_api.add_file("file-1", "plan.pdf", ALICE)
        box_api.collaborate("file-1", "g-eng", kind="group")
        box_api.fail("GET", "/2.0/groups/g-eng/memberships", 503, times=5)
        connector = await ready_connector(db, checkpoints)

        await connector.run_sync()

        assert "g-eng" not in db.user_groups
        assert checkpoints.cursor() is None

        await connector.run_sync()

        assert db.group_members["g-eng"] == [BOB_EMAIL]
        assert "g-eng" in db.access("file-1")

    async def test_a_missing_group_scope_is_explained_and_does_not_force_full_syncs(self, box_api, db, checkpoints, caplog) -> None:
        enterprise(box_api, db)
        box_api.add_file("file-1", "plan.pdf", ALICE)
        box_api.fail("GET", "/2.0/groups", 403, times=10)
        connector = await ready_connector(db, checkpoints)

        with caplog.at_level(logging.ERROR, logger="test.box"):
            await connector.run_sync()

        assert "'Manage groups' scope" in caplog.text and "re-authorize" in caplog.text
        assert checkpoints.cursor() is not None

    async def test_a_group_removed_in_box_is_deleted(self, box_api, db, checkpoints) -> None:
        enterprise(box_api, db)
        box_api.add_group("g-eng", "Engineering", (ALICE,))
        box_api.add_group("g-old", "Old", (ALICE,))
        connector = await ready_connector(db, checkpoints)
        await connector.run_sync()
        del box_api.groups["g-old"]
        checkpoints.sync_points.clear()

        await connector.run_sync()

        assert db.deleted_groups == ["g-old"]
        assert {"g-eng", "PUBLIC", "ORG_org-1"} <= set(db.user_groups)


class TestEventStreamAnchor:
    async def test_a_full_sync_saves_the_stream_position_it_started_from(self, box_api, db, checkpoints) -> None:
        enterprise(box_api, db)
        box_api.add_event("ITEM_UPLOAD", {"item_type": "file", "item_id": "old", "owned_by": {"id": ALICE}})
        connector = await ready_connector(db, checkpoints)

        await connector.run_sync()

        assert checkpoints.cursor()["cursor"] == "1"
        assert isinstance(checkpoints.cursor()["cursor_updated_at"], int)

    async def test_a_full_sync_that_fails_leaves_no_cursor_so_the_next_run_is_full_again(self, box_api, db, checkpoints) -> None:
        enterprise(box_api, db)
        box_api.add_file("file-1", "plan.pdf", ALICE)
        db.fail_active_users = True
        connector = await ready_connector(db, checkpoints)

        with pytest.raises(RuntimeError):
            await connector.run_sync()

        assert checkpoints.cursor() is None

    async def test_a_full_sync_that_could_not_list_users_saves_no_cursor(self, box_api, db, checkpoints) -> None:
        enterprise(box_api, db)
        box_api.fail("GET", "/2.0/users", 503, times=10)
        connector = await ready_connector(db, checkpoints)

        await connector.run_sync()

        assert checkpoints.cursor() is None

    async def test_a_refused_user_list_is_explained_and_leaves_no_cursor(self, box_api, db, checkpoints, caplog) -> None:
        enterprise(box_api, db)
        box_api.add_file("file-a", "a.txt", ALICE)
        box_api.fail("GET", "/2.0/users", 403, times=1)
        connector = await ready_connector(db, checkpoints)

        with caplog.at_level(logging.ERROR, logger="test.box"):
            await connector.run_sync()

        assert "'Manage users' scope" in caplog.text
        assert checkpoints.cursor() is None

        await connector.run_sync()

        assert "file-a" in db.records
        assert checkpoints.cursor() is not None

    async def test_a_user_whose_files_could_not_be_listed_leaves_no_cursor(self, box_api, db, checkpoints, sdk_sleeps) -> None:
        enterprise(box_api, db)
        box_api.add_file("file-a", "a.txt", ALICE)
        box_api.add_file("file-b", "b.txt", BOB)
        box_api.fail("GET", "/2.0/folders/0/items", 503, times=5, as_user=ALICE)
        connector = await ready_connector(db, checkpoints)

        await connector.run_sync()

        assert len(sdk_sleeps) == 4
        assert "file-b" in db.records and "file-a" not in db.records
        assert checkpoints.cursor() is None

        await connector.run_sync()

        assert "file-a" in db.records
        assert checkpoints.cursor() is not None

    async def test_a_failed_page_of_share_history_leaves_no_cursor(self, box_api, db, checkpoints) -> None:
        enterprise(box_api, db)
        box_api.fail("GET", "/2.0/events", 503, times=5, query={"stream_type": "admin_logs"})
        connector = await ready_connector(db, checkpoints)

        await connector.run_sync()

        assert checkpoints.cursor() is None

    async def test_an_unreadable_cursor_fails_the_run_instead_of_starting_over(self, box_api, db, checkpoints) -> None:
        enterprise(box_api, db)
        connector = await ready_connector(db, checkpoints)
        await connector.run_sync()
        saved = dict(checkpoints.cursor())
        box_api.add_event("COLLABORATION_REMOVE", {"type": "file", "id": "file-1"})
        checkpoints.fail_reads = True

        with pytest.raises(RuntimeError):
            await connector.run_sync()

        assert checkpoints.cursor() == saved



def fail_as_user(connector: BoxConnector, user_id: str, nth: int) -> None:
    """Make the nth attempt to act as ``user_id`` raise, as a broken SDK client would."""
    real = connector.data_source.set_as_user_context
    calls = {"n": 0}

    async def wrapped(uid: str) -> None:
        if uid == user_id:
            calls["n"] += 1
            if calls["n"] == nth:
                raise RuntimeError("Failed to set As-User context: client unavailable")
        await real(uid)

    connector.data_source.set_as_user_context = wrapped


def share_history(api: FakeBoxApi, item_id: str, user_id: str) -> None:
    collab_id = api.collaborate(item_id, user_id)
    api.add_event(
        "COLLABORATION_INVITE",
        {"type": "collaboration", "id": collab_id, "item": {"type": api.items[item_id]["type"], "id": item_id},
         "accessible_by": {"type": "user", "id": user_id, "login": api.users[user_id]["login"]}},
        created_by={"type": "user", "id": ALICE, "login": ALICE_EMAIL},
        additional_details={"collab_id": collab_id},
    )


class TestActingAsEachUser:
    async def test_an_unknown_service_account_leaves_no_cursor(self, box_api, db, checkpoints) -> None:
        enterprise(box_api, db)
        box_api.add_file("file-a", "a.txt", ALICE)
        box_api.fail("GET", "/2.0/users/me", 503, times=100)
        connector = await ready_connector(db, checkpoints)

        await connector.run_sync()

        assert "file-a" not in db.records
        assert checkpoints.cursor() is None

        box_api.faults.clear()
        await connector.run_sync()

        assert "file-a" in db.records

    async def test_a_walk_that_cannot_act_as_the_user_leaves_no_cursor(self, box_api, db, checkpoints) -> None:
        enterprise(box_api, db)
        box_api.add_file("file-a", "a.txt", ALICE)
        connector = await ready_connector(db, checkpoints)
        fail_as_user(connector, ALICE, nth=2)

        await connector.run_sync()

        assert "file-a" not in db.records
        assert checkpoints.cursor() is None

        await connector.run_sync()

        assert "file-a" in db.records

    async def test_an_unknown_service_account_gives_no_one_its_files(self, box_api, db, checkpoints) -> None:
        enterprise(box_api, db)
        box_api.add_file("svc-file", "service.txt", SERVICE_ACCOUNT_ID)
        box_api.fail("GET", "/2.0/users/me", 503, times=100)
        connector = await ready_connector(db, checkpoints)

        await connector.run_sync()

        assert "svc-file" not in db.records

    async def test_a_walk_that_cannot_act_as_the_user_gives_them_no_other_files(self, box_api, db, checkpoints) -> None:
        enterprise(box_api, db)
        box_api.add_file("svc-file", "service.txt", SERVICE_ACCOUNT_ID)
        connector = await ready_connector(db, checkpoints)
        fail_as_user(connector, ALICE, nth=2)

        await connector.run_sync()

        assert "svc-file" not in db.records

    async def test_an_unread_root_leaves_no_cursor_and_the_next_run_links_shared_files(self, box_api, db, checkpoints) -> None:
        enterprise(box_api, db)
        box_api.add_file("file-1", "plan.pdf", ALICE)
        share_history(box_api, "file-1", BOB)
        box_api.fail("GET", "/2.0/folders/0", 503, times=5, as_user=BOB)
        connector = await ready_connector(db, checkpoints)

        await connector.run_sync()

        assert f"0S:{BOB_EMAIL}" not in db.shared_links["file-1"]
        assert checkpoints.cursor() is None

        await connector.run_sync()

        assert f"0S:{BOB_EMAIL}" in db.record_groups
        assert f"0S:{BOB_EMAIL}" in db.shared_links["file-1"]

    async def test_a_drive_that_cannot_act_as_the_user_leaves_no_cursor(self, box_api, db, checkpoints) -> None:
        enterprise(box_api, db)
        box_api.add_file("file-1", "plan.pdf", ALICE)
        share_history(box_api, "file-1", BOB)
        connector = await ready_connector(db, checkpoints)
        fail_as_user(connector, BOB, nth=1)

        await connector.run_sync()

        assert f"0S:{BOB_EMAIL}" not in db.record_groups
        assert checkpoints.cursor() is None

    async def test_a_drive_that_cannot_be_saved_leaves_no_cursor(self, box_api, db, checkpoints) -> None:
        enterprise(box_api, db)
        box_api.add_file("file-1", "plan.pdf", ALICE)
        db.failing.add("on_new_record_groups")
        connector = await ready_connector(db, checkpoints)

        await connector.run_sync()

        assert checkpoints.cursor() is None

        db.failing.clear()
        await connector.run_sync()

        assert {ALICE, f"0S:{ALICE_EMAIL}"} <= set(db.record_groups)


class TestDatabaseFailuresDuringAFullSync:
    async def test_an_item_that_could_not_be_processed_leaves_no_cursor(self, box_api, db, checkpoints) -> None:
        enterprise(box_api, db)
        box_api.add_file("file-a", "a.txt", ALICE)
        db.fail_lookup_for.add("file-a")
        connector = await ready_connector(db, checkpoints)

        await connector.run_sync()

        assert "file-a" not in db.records
        assert checkpoints.cursor() is None

        db.fail_lookup_for.clear()
        await connector.run_sync()

        assert "file-a" in db.records

    async def test_an_update_that_could_not_be_saved_leaves_no_cursor(self, box_api, db, checkpoints) -> None:
        enterprise(box_api, db)
        box_api.add_file("file-a", "a.txt", ALICE)
        connector = await ready_connector(db, checkpoints)
        await connector.run_sync()
        checkpoints.sync_points.clear()
        box_api.items["file-a"]["modified_at"] = "2024-06-01T00:00:00Z"
        db.fail_write_for.add("file-a")

        await connector.run_sync()

        assert checkpoints.cursor() is None

    async def test_virtual_groups_that_could_not_be_saved_leave_no_cursor(self, box_api, db, checkpoints) -> None:
        enterprise(box_api, db)
        box_api.add_file("file-a", "a.txt", ALICE, shared_link_access="company")
        db.fail_group_write_for.add("PUBLIC")
        connector = await ready_connector(db, checkpoints)

        await connector.run_sync()

        assert "ORG_org-1" not in db.access("file-a")
        assert checkpoints.cursor() is None

        db.fail_group_write_for.clear()
        await connector.run_sync()

        assert "ORG_org-1" in db.access("file-a")

    async def test_a_share_whose_collaborator_could_not_be_looked_up_leaves_no_cursor(self, box_api, db, checkpoints) -> None:
        enterprise(box_api, db)
        box_api.add_file("file-1", "plan.pdf", ALICE)
        share_history(box_api, "file-1", BOB)
        db.failing.add("get_app_user_by_email")
        connector = await ready_connector(db, checkpoints)

        await connector.run_sync()

        assert f"0S:{BOB_EMAIL}" not in db.shared_links.get("file-1", set())
        assert checkpoints.cursor() is None

        db.failing.clear()
        await connector.run_sync()

        assert f"0S:{BOB_EMAIL}" in db.shared_links["file-1"]


def revoke_history(api: FakeBoxApi, item_id: str, user_id: str, *, with_login: bool = True, with_item: bool = True) -> str:
    """Remove ``user_id``'s collaboration on ``item_id`` in Box and log the removal event."""
    collab = next(c for c in api.collaborations[item_id] if c["accessible_by"]["id"] == user_id)
    api.collaborations[item_id].remove(collab)
    accessible_by = {"type": "user", "id": user_id}
    if with_login:
        accessible_by["login"] = api.users[user_id]["login"]
    source: dict[str, Any] = {"type": "collaboration", "id": collab["id"], "accessible_by": accessible_by}
    if with_item:
        source["item"] = {"type": api.items[item_id]["type"], "id": item_id}
    api.add_event("COLLABORATION_REMOVE", source, created_by={"type": "user", "id": ALICE, "login": ALICE_EMAIL})
    return collab["id"]


class TestReplayedShareLookups:
    async def test_a_grantee_who_could_not_be_looked_up_leaves_no_cursor(self, box_api, db, checkpoints) -> None:
        enterprise(box_api, db)
        box_api.add_file("file-1", "plan.pdf", ALICE)
        collab_id = box_api.collaborate("file-1", BOB)
        box_api.add_event(
            "COLLABORATION_INVITE",
            {"type": "collaboration", "id": collab_id, "item": {"type": "file", "id": "file-1"}, "accessible_by": {"type": "user", "id": BOB}},
            created_by={"type": "user", "id": ALICE, "login": ALICE_EMAIL},
        )
        box_api.fail("GET", f"/2.0/users/{BOB}", 503, times=5)
        connector = await ready_connector(db, checkpoints)

        await connector.run_sync()

        assert f"0S:{BOB_EMAIL}" not in db.shared_links["file-1"]
        assert checkpoints.cursor() is None

        await connector.run_sync()

        assert f"0S:{BOB_EMAIL}" in db.shared_links["file-1"]

    async def test_an_owner_who_could_not_be_looked_up_leaves_no_cursor(self, box_api, db, checkpoints) -> None:
        enterprise(box_api, db)
        box_api.add_file("file-1", "plan.pdf", ALICE)
        collab_id = box_api.collaborate("file-1", BOB)
        box_api.add_event(
            "COLLABORATION_INVITE",
            {"type": "collaboration", "id": collab_id, "item": {"type": "file", "id": "file-1"},
             "accessible_by": {"type": "user", "id": BOB, "login": BOB_EMAIL}},
        )
        box_api.fail("GET", "/2.0/files/file-1", 503, times=5)
        connector = await ready_connector(db, checkpoints)

        await connector.run_sync()

        assert checkpoints.cursor() is None

    async def test_a_removed_collaborator_who_could_not_be_looked_up_leaves_no_cursor(self, box_api, db, checkpoints) -> None:
        enterprise(box_api, db)
        box_api.add_file("file-1", "plan.pdf", ALICE)
        box_api.collaborate("file-1", BOB)
        connector = await ready_connector(db, checkpoints)
        await connector.run_sync()
        checkpoints.sync_points.clear()
        revoke_history(box_api, "file-1", BOB, with_login=False)
        box_api.fail("GET", f"/2.0/users/{BOB}", 503, times=5)

        await connector.run_sync()

        assert BOB_EMAIL in db.access("file-1")
        assert checkpoints.cursor() is None

        await connector.run_sync()

        assert BOB_EMAIL not in db.access("file-1")

    async def test_a_removal_whose_collaboration_could_not_be_looked_up_leaves_no_cursor(self, box_api, db, checkpoints) -> None:
        enterprise(box_api, db)
        box_api.add_file("file-1", "plan.pdf", ALICE)
        box_api.collaborate("file-1", BOB)
        connector = await ready_connector(db, checkpoints)
        await connector.run_sync()
        checkpoints.sync_points.clear()
        collab_id = revoke_history(box_api, "file-1", BOB, with_item=False)
        box_api.fail("GET", f"/2.0/collaborations/{collab_id}", 503, times=5)

        await connector.run_sync()

        assert checkpoints.cursor() is None

    @pytest.mark.parametrize("failing", ["remove_user_access_to_record", "get_records_by_parent"])
    async def test_a_folder_removal_that_could_not_be_applied_leaves_no_cursor(self, box_api, db, checkpoints, failing) -> None:
        enterprise(box_api, db)
        box_api.add_folder("fold-a", "Team", ALICE)
        box_api.add_file("file-1", "plan.pdf", ALICE, parent="fold-a")
        box_api.collaborate("fold-a", BOB)
        box_api.collaborate("file-1", BOB)
        connector = await ready_connector(db, checkpoints)
        await connector.run_sync()
        checkpoints.sync_points.clear()
        revoke_history(box_api, "fold-a", BOB)
        box_api.collaborations["file-1"].clear()
        db.failing.add(failing)

        await connector.run_sync()

        assert BOB_EMAIL in db.access("file-1")
        assert checkpoints.cursor() is None

        db.failing.clear()
        await connector.run_sync()

        assert BOB_EMAIL not in db.access("file-1")


def history_calls(api: FakeBoxApi) -> list[str]:
    return [r.query["stream_position"] for r in api.calls("GET", "/2.0/events") if r.query.get("stream_type") == "admin_logs"]


def history_position(checkpoints: FakeCheckpointStore) -> str | None:
    for key, value in checkpoints.sync_points.items():
        if key.endswith("/records/share_history_position"):
            return value.get("position")
    return None


class TestShareHistoryLimit:
    async def test_shares_past_the_history_page_cap_are_applied_on_the_next_run(self, box_api, db, checkpoints) -> None:
        enterprise(box_api, db)
        box_api.page_cap["/2.0/events"] = 1
        for _ in range(200):
            box_api.add_event("COLLABORATION_INVITE", {})
        box_api.add_file("file-1", "plan.pdf", ALICE)
        share_history(box_api, "file-1", BOB)
        connector = await ready_connector(db, checkpoints)

        await connector.run_sync()

        assert f"0S:{BOB_EMAIL}" not in db.shared_links.get("file-1", set())
        assert history_position(checkpoints) == "200"
        assert checkpoints.cursor() is None

        await connector.run_sync()

        assert history_calls(box_api)[200] == "200"
        assert f"0S:{BOB_EMAIL}" in db.shared_links["file-1"]
        assert checkpoints.cursor() is not None

    async def test_a_later_full_sync_resumes_the_history_instead_of_starting_over(self, box_api, db, checkpoints) -> None:
        enterprise(box_api, db)
        box_api.add_file("file-1", "plan.pdf", ALICE)
        share_history(box_api, "file-1", BOB)
        connector = await ready_connector(db, checkpoints)
        await connector.run_sync()
        first_run = len(history_calls(box_api))
        box_api.add_file("file-2", "later.pdf", ALICE)
        share_history(box_api, "file-2", BOB)
        checkpoints.cursor()["cursor_updated_at"] = 0

        await connector.run_sync()

        assert history_calls(box_api)[first_run] == "1"
        assert f"0S:{BOB_EMAIL}" in db.shared_links["file-2"]

    async def test_a_failed_history_page_is_read_again_from_where_it_stopped(self, box_api, db, checkpoints) -> None:
        enterprise(box_api, db)
        box_api.page_cap["/2.0/events"] = 1
        box_api.add_file("file-1", "one.pdf", ALICE)
        box_api.add_file("file-2", "two.pdf", ALICE)
        share_history(box_api, "file-1", BOB)
        share_history(box_api, "file-2", BOB)
        box_api.fail("GET", "/2.0/events", 503, times=5, query={"stream_type": "admin_logs", "stream_position": "1"})
        connector = await ready_connector(db, checkpoints)

        await connector.run_sync()

        assert history_position(checkpoints) == "1"
        assert checkpoints.cursor() is None

        await connector.run_sync()

        assert history_calls(box_api)[-2:] == ["1", "2"]
        assert f"0S:{BOB_EMAIL}" in db.shared_links["file-2"]
