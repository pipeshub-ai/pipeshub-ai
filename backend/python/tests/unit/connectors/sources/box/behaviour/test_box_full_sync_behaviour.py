"""Box full sync, driven over a fake Box API with the real Box SDK underneath.

The connector, ``BoxDataSource`` and ``box_sdk_gen`` (auth, retries, token
refresh) are real; every HTTP request is answered by an in-memory Box and our
databases are in-memory fakes. SDK retry waits are recorded, not slept.
"""

import logging
from typing import Any

from box_behaviour_fakes import (
    CONNECTOR_ID,
    ROOT_ID,
    FakeBoxApi,
    FakeBoxRecordsDb,
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



