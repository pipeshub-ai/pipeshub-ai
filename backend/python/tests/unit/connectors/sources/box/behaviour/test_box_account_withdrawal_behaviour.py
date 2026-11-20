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


def owner_walks(api: FakeBoxApi) -> int:
    return len([r for r in api.calls("GET", "/2.0/folders/0/items") if r.as_user == ALICE])


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
        # The record of withdrawals is lost; the stream position stays, so the next run is incremental.
        for key in [k for k in checkpoints.sync_points if k.endswith("/withdrawn_accounts")]:
            del checkpoints.sync_points[key]
        walks = owner_walks(box_api)
        box_api.users[CAROL]["status"] = "active"

        await connector.run_sync()

        assert owner_walks(box_api) == walks + 1
        assert CAROL_EMAIL in readers(db, "file-shared")

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
