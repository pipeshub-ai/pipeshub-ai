"""Box accounts that leave Box, against a fake Box API with the real Box SDK underneath.

An account whose Box status no longer lets it sign in (``inactive``), or that Box no longer lists (deleted),
loses its PipesHub link and its own grants at the next complete user list, on the full and on the incremental
path; a user list that could not be read in full withdraws no one. Box documents the statuses ``active``,
``inactive``, ``cannot_delete_edit`` and ``cannot_delete_edit_upload``
(https://developer.box.com/reference/resources/user/); the Admin Console says an Inactive user "is prevented
from logging in" and the Active View Only and Active View/Upload Only users "can log in to Box"
(https://docs.box.com/en/box-admin-tools/managing-box-users-and-admins/understanding-user-management/users-groups-settings).
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import AsyncMock

import pytest
from box_behaviour_fakes import (
    FakeBoxApi,
    FakeBoxRecordsDb,
    FakeCheckpointStore,
    ready_connector,
)

if TYPE_CHECKING:
    from app.connectors.sources.box.connector import BoxConnector

ALICE, BOB, CAROL = "u-alice", "u-bob", "u-carol"
ALICE_EMAIL, BOB_EMAIL, CAROL_EMAIL = "alice@acme.test", "bob@acme.test", "carol@acme.test"


def enterprise(api: FakeBoxApi, db: FakeBoxRecordsDb, carol_status: str = "active") -> None:
    api.add_user(ALICE, ALICE_EMAIL, "Alice")
    api.add_user(BOB, BOB_EMAIL, "Bob")
    api.add_user(CAROL, CAROL_EMAIL, "Carol", status=carol_status)
    db.active_emails.update({ALICE_EMAIL, BOB_EMAIL, CAROL_EMAIL})
    # A file Carol and Bob are invited to, a folder Carol is invited to, and a file open to the company.
    api.add_file("file-shared", "plan.pdf", ALICE)
    api.collaborate("file-shared", CAROL)
    api.collaborate("file-shared", BOB)
    api.add_folder("fold-team", "Team", ALICE)
    api.collaborate("fold-team", CAROL)
    api.add_file("file-company", "handbook.pdf", ALICE, shared_link_access="company")


def linked(db: FakeBoxRecordsDb) -> set[str]:
    return set(db.app_users)


def readers(db: FakeBoxRecordsDb, external_id: str) -> set[str]:
    """Who PipesHub lets read the record: linked accounts with a grant, or every linked account for an org grant."""
    grants = db.permissions.get(external_id, {}).values()
    if any(p.entity_type.value == "ORG" for p in grants):
        return linked(db)
    return {p.email for p in grants if p.entity_type.value == "USER"} & linked(db)


async def synced(api: FakeBoxApi, db: FakeBoxRecordsDb, checkpoints: FakeCheckpointStore) -> BoxConnector:
    connector = await ready_connector(db, checkpoints)
    await connector.run_sync()
    assert CAROL_EMAIL in readers(db, "file-shared")
    return connector


async def sync(connector: BoxConnector, checkpoints: FakeCheckpointStore, path: str) -> None:
    """``full`` drops the saved stream position first, so the run takes the full path; ``incremental`` keeps it."""
    if path == "full":
        checkpoints.sync_points.clear()
    await connector.run_sync()


@pytest.mark.parametrize("path", ["full", "incremental"])
class TestAnAccountThatLeftBox:
    async def test_a_deactivated_account_loses_its_link_and_its_grants(self, box_api, db, checkpoints, path) -> None:
        enterprise(box_api, db)
        connector = await synced(box_api, db, checkpoints)
        box_api.users[CAROL]["status"] = "inactive"

        await sync(connector, checkpoints, path)

        assert CAROL_EMAIL not in linked(db)
        assert (CAROL_EMAIL, True) in db.withdrawn
        assert CAROL_EMAIL not in db.access("file-shared")
        assert readers(db, "file-shared") == {BOB_EMAIL}
        assert readers(db, "file-company") == {ALICE_EMAIL, BOB_EMAIL}

    async def test_a_deleted_account_loses_its_link_and_its_grants(self, box_api, db, checkpoints, path) -> None:
        enterprise(box_api, db)
        connector = await synced(box_api, db, checkpoints)
        del box_api.users[CAROL]

        await sync(connector, checkpoints, path)

        assert CAROL_EMAIL not in linked(db)
        assert CAROL_EMAIL not in readers(db, "file-shared")
        assert CAROL_EMAIL not in readers(db, "file-company")

    async def test_the_only_account_that_can_sign_in_is_withdrawn_when_it_is_deactivated(
        self, box_api, db, checkpoints, path
    ) -> None:
        enterprise(box_api, db)
        connector = await synced(box_api, db, checkpoints)
        box_api.users[CAROL]["status"] = "inactive"
        box_api.users[BOB]["status"] = "inactive"
        await sync(connector, checkpoints, path)
        assert linked(db) == {ALICE_EMAIL}
        box_api.users[ALICE]["status"] = "inactive"

        await sync(connector, checkpoints, path)

        assert linked(db) == set()
        assert (ALICE_EMAIL, True) in db.withdrawn
        assert readers(db, "file-company") == set()
        assert ALICE_EMAIL not in db.record_group_access.get(ALICE, set())

    async def test_a_deactivated_account_keeps_no_grant_on_its_drive_after_a_full_sync(
        self, box_api, db, checkpoints, path
    ) -> None:
        enterprise(box_api, db)
        connector = await synced(box_api, db, checkpoints)
        assert CAROL_EMAIL in db.record_group_access[CAROL]
        box_api.users[CAROL]["status"] = "inactive"
        await sync(connector, checkpoints, path)

        await sync(connector, checkpoints, "full")

        assert CAROL_EMAIL not in db.record_group_access.get(CAROL, set())
        assert CAROL_EMAIL not in db.record_group_access.get(f"0S:{CAROL_EMAIL}", set())
        assert CAROL_EMAIL not in db.access("file-shared")

    async def test_a_user_list_cut_short_withdraws_no_one(self, box_api, db, checkpoints, path) -> None:
        box_api.page_cap["/2.0/users"] = 2
        enterprise(box_api, db)
        connector = await synced(box_api, db, checkpoints)
        # Carol is on the second page, which cannot be read; Alice, on the first, now shows inactive.
        box_api.users[ALICE]["status"] = "inactive"
        box_api.fail("GET", "/2.0/users", 503, times=10, query={"marker": "2"})

        await sync(connector, checkpoints, path)

        assert db.withdrawn == []
        assert linked(db) == {ALICE_EMAIL, BOB_EMAIL, CAROL_EMAIL}
        assert CAROL_EMAIL in db.access("file-shared")

    async def test_a_user_list_that_cannot_be_read_withdraws_no_one(self, box_api, db, checkpoints, path) -> None:
        enterprise(box_api, db)
        connector = await synced(box_api, db, checkpoints)
        cursor = checkpoints.cursor()
        box_api.fail("GET", "/2.0/users", 503, times=10)

        await sync(connector, checkpoints, path)

        assert db.withdrawn == []
        assert linked(db) == {ALICE_EMAIL, BOB_EMAIL, CAROL_EMAIL}
        assert checkpoints.cursor() == (None if path == "full" else cursor)

    async def test_a_reactivated_account_gets_its_access_back(self, box_api, db, checkpoints, path) -> None:
        enterprise(box_api, db)
        connector = await synced(box_api, db, checkpoints)
        box_api.users[CAROL]["status"] = "inactive"
        await sync(connector, checkpoints, path)
        assert CAROL_EMAIL not in db.access("fold-team")
        box_api.users[CAROL]["status"] = "active"

        await sync(connector, checkpoints, path)

        assert CAROL_EMAIL in linked(db)
        # Her grants were dropped when she left; the file shared with her is only read from its owner's side.
        assert CAROL_EMAIL in readers(db, "file-shared")
        assert CAROL_EMAIL in readers(db, "fold-team")
        assert CAROL_EMAIL in readers(db, "file-company")
        assert checkpoints.cursor()["cursor"]


def forget_withdrawals(checkpoints: FakeCheckpointStore) -> None:
    """Lose the record of withdrawals, as when the sync points were cleared; the stream position stays."""
    for key in [k for k in checkpoints.sync_points if k.endswith("/withdrawn_accounts")]:
        del checkpoints.sync_points[key]


def owner_walks(api: FakeBoxApi) -> int:
    return len([r for r in api.calls("GET", "/2.0/folders/0/items") if r.as_user == ALICE])


def share_only_the_history_names(api: FakeBoxApi) -> None:
    """Erin has no PipesHub account, so her files are not walked: Carol's share is known from the history alone."""
    api.add_user("u-erin", "erin@acme.test", "Erin")
    api.add_folder("fold-erin", "Erin's", "u-erin")
    api.add_file("file-direct", "direct.pdf", "u-erin", parent="fold-erin")
    collab_id = api.collaborate("file-direct", CAROL)
    api.add_event(
        "COLLABORATION_INVITE",
        {"type": "collaboration", "id": collab_id, "item": {"type": "file", "id": "file-direct"},
         "accessible_by": {"type": "user", "id": CAROL, "login": CAROL_EMAIL}},
        created_by={"type": "user", "id": "u-erin", "login": "erin@acme.test"},
        additional_details={"collab_id": collab_id},
    )


class TestReturningAccounts:
    async def test_an_account_coming_back_between_full_syncs_gets_a_full_sync(self, box_api, db, checkpoints) -> None:
        enterprise(box_api, db)
        connector = await synced(box_api, db, checkpoints)
        box_api.users[CAROL]["status"] = "inactive"
        await connector.run_sync()
        walks = owner_walks(box_api)
        box_api.users[CAROL]["status"] = "active"

        await connector.run_sync()

        assert owner_walks(box_api) == walks + 1
        assert CAROL_EMAIL in readers(db, "file-shared")

    async def test_a_new_account_does_not_cost_a_full_sync(self, box_api, db, checkpoints) -> None:
        enterprise(box_api, db)
        connector = await synced(box_api, db, checkpoints)
        walks = owner_walks(box_api)
        box_api.add_user("u-dan", "dan@acme.test", "Dan")

        await connector.run_sync()

        assert "dan@acme.test" in linked(db)
        assert owner_walks(box_api) == walks

    async def test_without_a_record_of_withdrawals_a_newly_linked_account_gets_a_full_sync(
        self, box_api, db, checkpoints
    ) -> None:
        enterprise(box_api, db)
        connector = await synced(box_api, db, checkpoints)
        box_api.users[CAROL]["status"] = "inactive"
        await connector.run_sync()
        forget_withdrawals(checkpoints)
        walks = owner_walks(box_api)
        box_api.users[CAROL]["status"] = "active"

        await connector.run_sync()

        assert owner_walks(box_api) == walks + 1
        assert CAROL_EMAIL in readers(db, "file-shared")

    async def test_a_return_seen_on_a_user_list_cut_short_still_gets_its_full_sync(self, box_api, db, checkpoints) -> None:
        box_api.page_cap["/2.0/users"] = 3
        enterprise(box_api, db)
        box_api.add_user("u-dan", "dan@acme.test", "Dan")
        connector = await synced(box_api, db, checkpoints)
        box_api.users[CAROL]["status"] = "inactive"
        await connector.run_sync()
        forget_withdrawals(checkpoints)
        box_api.users[CAROL]["status"] = "active"
        # Carol is on the first page; the second cannot be read, so this run stops before any full sync.
        box_api.fail("GET", "/2.0/users", 503, times=10, query={"marker": "3"})
        await connector.run_sync()
        box_api.faults.clear()
        walks = owner_walks(box_api)

        await connector.run_sync()

        assert owner_walks(box_api) == walks + 1
        assert CAROL_EMAIL in readers(db, "file-shared")

    async def test_a_returning_account_gets_back_a_share_only_the_share_history_names(self, box_api, db, checkpoints) -> None:
        enterprise(box_api, db)
        share_only_the_history_names(box_api)
        connector = await synced(box_api, db, checkpoints)
        assert CAROL_EMAIL in readers(db, "file-direct")
        box_api.users[CAROL]["status"] = "inactive"
        await connector.run_sync()
        assert CAROL_EMAIL not in readers(db, "file-direct")
        box_api.users[CAROL]["status"] = "active"

        await connector.run_sync()

        assert CAROL_EMAIL in readers(db, "file-direct")

    async def test_a_newly_linked_account_on_the_full_path_gets_its_shares_back_from_the_history(
        self, box_api, db, checkpoints
    ) -> None:
        enterprise(box_api, db)
        share_only_the_history_names(box_api)
        connector = await synced(box_api, db, checkpoints)
        box_api.users[CAROL]["status"] = "inactive"
        await connector.run_sync()
        forget_withdrawals(checkpoints)
        box_api.users[CAROL]["status"] = "active"
        # The stream position expired; the share history position is kept.
        checkpoints.cursor().clear()

        await connector.run_sync()

        assert CAROL_EMAIL in readers(db, "file-direct")

    async def test_a_change_made_before_an_account_returns_is_still_applied(self, box_api, db, checkpoints) -> None:
        enterprise(box_api, db)
        box_api.add_file("file-old", "old.pdf", ALICE)
        connector = await synced(box_api, db, checkpoints)
        box_api.users[CAROL]["status"] = "inactive"
        await connector.run_sync()
        # Alice trashes a file, and Carol is reactivated before the next run reads that change.
        del box_api.items["file-old"]
        box_api.add_event(
            "ITEM_TRASH",
            {"item_type": "file", "item_id": "file-old", "item_name": "old.pdf", "owned_by": {"type": "user", "id": ALICE}},
            created_by={"type": "user", "id": ALICE, "login": ALICE_EMAIL},
        )
        box_api.users[CAROL]["status"] = "active"

        await connector.run_sync()

        assert "file-old" not in db.records
        assert CAROL_EMAIL in readers(db, "file-shared")
        assert not checkpoints.cursor().get("full_sync_pending")

    async def test_a_full_sync_owed_to_a_returning_account_is_run_again_after_a_cut_short_run(
        self, box_api, db, checkpoints
    ) -> None:
        enterprise(box_api, db)
        connector = await synced(box_api, db, checkpoints)
        box_api.users[CAROL]["status"] = "inactive"
        await connector.run_sync()
        box_api.users[CAROL]["status"] = "active"
        box_api.fail("GET", "/2.0/folders/0/items", 503, times=50)
        await connector.run_sync()
        assert checkpoints.cursor()["full_sync_pending"]
        box_api.faults.clear()
        walks = owner_walks(box_api)

        await connector.run_sync()

        assert owner_walks(box_api) == walks + 1
        assert CAROL_EMAIL in readers(db, "file-shared")
        assert not checkpoints.cursor()["full_sync_pending"]

    async def test_a_removal_replayed_without_its_later_regrant_is_undone_by_the_next_run(
        self, box_api, db, checkpoints
    ) -> None:
        enterprise(box_api, db)
        share_only_the_history_names(box_api)
        collab_id = box_api.collaborations["file-direct"][0]["id"]
        for event_type in ("COLLABORATION_REMOVE", "COLLABORATION_INVITE"):
            box_api.add_event(
                event_type,
                {"type": "collaboration", "id": collab_id, "item": {"type": "file", "id": "file-direct"},
                 "accessible_by": {"type": "user", "id": CAROL, "login": CAROL_EMAIL}},
                created_by={"type": "user", "id": "u-erin", "login": "erin@acme.test"},
            )
        box_api.page_cap["/2.0/events"] = 1
        connector = await synced(box_api, db, checkpoints)
        box_api.users[CAROL]["status"] = "inactive"
        await connector.run_sync()
        box_api.users[CAROL]["status"] = "active"
        # The replay from the start applies the removal, then cannot read the page that shares the file again.
        box_api.fail("GET", "/2.0/events", 503, times=5, query={"stream_type": "admin_logs", "stream_position": "2"})
        await connector.run_sync()
        assert checkpoints.cursor()["full_sync_pending"]

        await connector.run_sync()

        assert CAROL_EMAIL in readers(db, "file-direct")

    async def test_a_record_that_cannot_be_saved_withdraws_no_one(self, box_api, db, checkpoints, monkeypatch) -> None:
        enterprise(box_api, db)
        connector = await synced(box_api, db, checkpoints)
        box_api.users[CAROL]["status"] = "inactive"
        monkeypatch.setattr(connector.user_sync_point, "update_sync_point", AsyncMock(side_effect=RuntimeError("down")))

        await connector.run_sync()

        assert db.withdrawn == []


class TestWhoIsLinked:
    async def test_an_inactive_account_is_never_linked_nor_granted(self, box_api, db, checkpoints) -> None:
        enterprise(box_api, db, carol_status="inactive")
        connector = await ready_connector(db, checkpoints)

        await connector.run_sync()

        assert linked(db) == {ALICE_EMAIL, BOB_EMAIL}
        assert CAROL_EMAIL not in db.access("file-shared")
        assert CAROL_EMAIL not in db.access("fold-team")
        # The user cannot sign in to Box to read anything, so their folders are not walked.
        assert not [r for r in box_api.requests if r.as_user == CAROL and r.path.endswith("/items")]

    @pytest.mark.parametrize("status", ["cannot_delete_edit", "cannot_delete_edit_upload"])
    async def test_a_view_only_account_can_sign_in_and_stays_linked(self, box_api, db, checkpoints, status) -> None:
        enterprise(box_api, db, carol_status=status)
        connector = await ready_connector(db, checkpoints)

        await connector.run_sync()

        assert CAROL_EMAIL in readers(db, "file-shared")
        assert CAROL_EMAIL in readers(db, "file-company")

    async def test_an_item_an_inactive_owner_shares_is_not_homed_in_a_drive_that_is_not_synced(
        self, box_api, db, checkpoints
    ) -> None:
        enterprise(box_api, db, carol_status="inactive")
        box_api.add_folder("fold-carol", "Carol's", CAROL)
        box_api.collaborate("fold-carol", BOB)
        connector = await ready_connector(db, checkpoints)

        await connector.run_sync()

        assert CAROL not in db.record_groups
        assert db.records["fold-carol"].external_record_group_id is None
        assert f"0S:{BOB_EMAIL}" in db.shared_links["fold-carol"]

    async def test_a_stored_item_keeps_its_home_when_its_owner_becomes_inactive(self, box_api, db, checkpoints) -> None:
        # The admin kept Carol's content open to her collaborators, so Bob still lists her folder.
        enterprise(box_api, db)
        box_api.add_folder("fold-carol", "Carol's", CAROL)
        box_api.collaborate("fold-carol", BOB)
        connector = await synced(box_api, db, checkpoints)
        assert db.records["fold-carol"].external_record_group_id == CAROL
        box_api.users[CAROL]["status"] = "inactive"

        await sync(connector, checkpoints, "full")

        assert db.records["fold-carol"].external_record_group_id == CAROL
        assert BOB_EMAIL in readers(db, "fold-carol")

    async def test_an_empty_user_list_moves_no_position_on(self, box_api, db, checkpoints) -> None:
        enterprise(box_api, db)
        connector = await synced(box_api, db, checkpoints)
        cursor = dict(checkpoints.cursor())
        box_api.users.clear()
        box_api.add_event(
            "ITEM_UPLOAD", {"item_type": "file", "item_id": "file-shared", "owned_by": {"type": "user", "id": ALICE}}
        )

        await connector.run_sync()

        assert checkpoints.cursor() == cursor
        assert db.withdrawn == []
        checkpoints.sync_points.clear()

        await connector.run_sync()

        assert checkpoints.cursor() is None

    async def test_an_unknown_status_links_no_one(self, box_api, db, checkpoints) -> None:
        enterprise(box_api, db, carol_status="pending_review")
        connector = await ready_connector(db, checkpoints)

        await connector.run_sync()

        assert CAROL_EMAIL not in linked(db)
        assert CAROL_EMAIL not in db.access("file-shared")

    async def test_company_link_content_reaches_only_accounts_that_can_sign_in(self, box_api, db, checkpoints) -> None:
        enterprise(box_api, db, carol_status="inactive")
        connector = await ready_connector(db, checkpoints)

        await connector.run_sync()

        assert {p.entity_type.value for p in db.permissions["file-company"].values()} == {"ORG"}
        assert readers(db, "file-company") == {ALICE_EMAIL, BOB_EMAIL}

    async def test_the_user_list_is_read_by_marker_for_managed_users(self, box_api, db, checkpoints) -> None:
        box_api.page_cap["/2.0/users"] = 2
        enterprise(box_api, db)
        connector = await ready_connector(db, checkpoints)

        await connector.run_sync()

        pages = box_api.calls("GET", "/2.0/users")[:2]
        assert [p.query.get("marker") for p in pages] == [None, "2"]
        assert {p.query.get("user_type") for p in pages} == {"managed"}
        assert "offset" not in pages[0].query
        assert linked(db) == {ALICE_EMAIL, BOB_EMAIL, CAROL_EMAIL}

    async def test_a_failed_read_of_the_linked_accounts_links_and_withdraws_no_one(self, box_api, db, checkpoints) -> None:
        enterprise(box_api, db)
        connector = await synced(box_api, db, checkpoints)
        box_api.users[CAROL]["status"] = "inactive"
        box_api.add_user("u-dan", "dan@acme.test", "Dan")
        db.failing.add("get_all_app_users")
        checkpoints.sync_points.clear()

        await connector.run_sync()

        assert db.withdrawn == []
        assert "dan@acme.test" not in db.app_users
        assert checkpoints.cursor() is None
