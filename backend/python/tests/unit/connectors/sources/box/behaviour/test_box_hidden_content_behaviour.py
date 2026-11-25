"""Content of an inactive Box owner, against a fake Box API with the real Box SDK underneath.

Box's Admin Console: for an Inactive user, "by default, folders owned by this user are hidden from all
collaborators"; an admin may instead select "Keep this user's content available to existing collaborators", and
"When you reactivate a user, this setting is reset"
(https://docs.box.com/en/box-admin-tools/managing-box-users-and-admins/understanding-user-management/users-groups-settings).
The API reads that choice as ``is_collaborated_content_available_when_owner_inactive``, "Whether collaborators can
access content owned by the user when the user is inactive" (https://developer.box.com/reference/resources/user--full/).
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock

import pytest
from box_behaviour_fakes import (
    FakeBoxApi,
    FakeBoxRecordsDb,
    FakeCheckpointStore,
    ready_connector,
)

from app.connectors.core.sync.full_sync_coverage import FullSyncSeen
from app.connectors.core.sync.sync_runner import _unlink_what_full_sync_missed

if TYPE_CHECKING:
    from app.connectors.sources.box.connector import BoxConnector

ALICE, BOB, CAROL = "u-alice", "u-bob", "u-carol"
ALICE_EMAIL, BOB_EMAIL, CAROL_EMAIL = "alice@acme.test", "bob@acme.test", "carol@acme.test"
CAROLS = ("fold-carol", "file-carol", "file-carol-link", "file-carol-own")


def box_org(api: FakeBoxApi, db: FakeBoxRecordsDb) -> None:
    api.add_user(ALICE, ALICE_EMAIL, "Alice")
    api.add_user(BOB, BOB_EMAIL, "Bob")
    api.add_user(CAROL, CAROL_EMAIL, "Carol")
    db.active_emails.update({ALICE_EMAIL, BOB_EMAIL, CAROL_EMAIL})
    # Carol's folder is shared with Bob; one file in it is open to the company, one has a share of its own.
    api.add_folder("fold-carol", "Carol's", CAROL)
    api.collaborate("fold-carol", BOB)
    api.add_file("file-carol", "notes.pdf", CAROL, parent="fold-carol")
    api.add_file("file-carol-link", "handbook.pdf", CAROL, parent="fold-carol", shared_link_access="company")
    api.add_file("file-carol-own", "budget.pdf", CAROL, parent="fold-carol")
    api.collaborate("file-carol-own", BOB, role="editor")
    # Alice's own folder, shared with Bob, is not Carol's and stays as it is.
    api.add_folder("fold-alice", "Alice's", ALICE)
    api.collaborate("fold-alice", BOB)


def linked(db: FakeBoxRecordsDb) -> set[str]:
    return set(db.app_users)


def readers(db: FakeBoxRecordsDb, external_id: str) -> set[str]:
    """Who PipesHub lets read the record: linked accounts with a grant on it or on a folder above it (records inherit
    from their parent), or every linked account for an org grant."""
    who: set[str] = set()
    record = db.records.get(external_id)
    while record is not None:
        grants = db.permissions.get(record.external_record_id, {}).values()
        if any(p.entity_type.value == "ORG" for p in grants):
            return linked(db)
        who |= {p.email for p in grants if p.entity_type.value == "USER"}
        record = db.records.get(record.parent_external_record_id or "")
    return who & linked(db)


async def synced(api: FakeBoxApi, db: FakeBoxRecordsDb, checkpoints: FakeCheckpointStore) -> BoxConnector:
    connector = await ready_connector(db, checkpoints)
    await connector.run_sync()
    assert {BOB_EMAIL} <= readers(db, "file-carol") and readers(db, "file-carol-link") == linked(db)
    return connector


async def sync(connector: BoxConnector, checkpoints: FakeCheckpointStore, path: str) -> None:
    """``full`` drops the saved stream position first, so the run takes the full path; ``incremental`` keeps it."""
    if path == "full":
        checkpoints.cursor().clear()
    await connector.run_sync()


@pytest.mark.parametrize("path", ["full", "incremental"])
class TestContentOfAnInactiveOwner:
    async def test_by_default_no_collaborator_reads_it_after_the_next_sync(self, box_api, db, checkpoints, path) -> None:
        box_org(box_api, db)
        connector = await synced(box_api, db, checkpoints)
        box_api.deactivate(CAROL)

        await sync(connector, checkpoints, path)

        for item in CAROLS:
            assert readers(db, item) == set(), item
        assert BOB_EMAIL in readers(db, "fold-alice")

    async def test_kept_available_its_collaborators_keep_reading_it(self, box_api, db, checkpoints, path) -> None:
        box_org(box_api, db)
        connector = await synced(box_api, db, checkpoints)
        box_api.deactivate(CAROL, keep_content_available=True)

        await sync(connector, checkpoints, "full")
        await sync(connector, checkpoints, path)

        for item in CAROLS:
            assert readers(db, item) == {BOB_EMAIL}, item

    async def test_hidden_later_by_the_admin_no_collaborator_reads_it(self, box_api, db, checkpoints, path) -> None:
        box_org(box_api, db)
        connector = await synced(box_api, db, checkpoints)
        box_api.deactivate(CAROL, keep_content_available=True)
        await sync(connector, checkpoints, path)
        box_api.users[CAROL]["is_collaborated_content_available_when_owner_inactive"] = False

        await sync(connector, checkpoints, path)

        for item in CAROLS:
            assert readers(db, item) == set(), item

    async def test_reactivated_its_collaborators_read_it_again(self, box_api, db, checkpoints, path) -> None:
        box_org(box_api, db)
        connector = await synced(box_api, db, checkpoints)
        box_api.deactivate(CAROL)
        await sync(connector, checkpoints, path)
        box_api.reactivate(CAROL)

        await sync(connector, checkpoints, path)

        for item in ("fold-carol", "file-carol", "file-carol-own"):
            assert readers(db, item) == {BOB_EMAIL}, item
        assert readers(db, "file-carol-link") == linked(db)
        # Carol reads her own content through her drive.
        assert CAROL_EMAIL in db.record_group_access[CAROL]

    async def test_kept_available_after_it_was_hidden_its_collaborators_read_it_again(
        self, box_api, db, checkpoints, path
    ) -> None:
        box_org(box_api, db)
        connector = await synced(box_api, db, checkpoints)
        box_api.deactivate(CAROL)
        await sync(connector, checkpoints, path)
        box_api.users[CAROL]["is_collaborated_content_available_when_owner_inactive"] = True

        await sync(connector, checkpoints, path)

        for item in CAROLS:
            assert readers(db, item) == {BOB_EMAIL}, item

    async def test_a_user_list_cut_short_before_the_owner_closes_nothing(self, box_api, db, checkpoints, path) -> None:
        box_api.page_cap["/2.0/users"] = 2
        box_org(box_api, db)
        connector = await synced(box_api, db, checkpoints)
        # Carol is on the second page, which cannot be read.
        box_api.deactivate(CAROL)
        box_api.fail("GET", "/2.0/users", 503, times=10, query={"marker": "2"})

        await sync(connector, checkpoints, path)

        assert BOB_EMAIL in readers(db, "fold-carol")
        box_api.faults.clear()
        await sync(connector, checkpoints, path)
        assert readers(db, "fold-carol") == set()


class TestFirstSeenWhileInactive:
    async def test_content_first_seen_kept_available_is_closed_once_hidden(self, box_api, db, checkpoints) -> None:
        box_org(box_api, db)
        box_api.deactivate(CAROL, keep_content_available=True)
        connector = await ready_connector(db, checkpoints)
        await connector.run_sync()
        assert readers(db, "file-carol-own") == {BOB_EMAIL}
        box_api.users[CAROL]["is_collaborated_content_available_when_owner_inactive"] = False

        await connector.run_sync()

        for item in CAROLS:
            assert readers(db, item) == set(), item

    async def test_hidden_content_gets_no_grant_from_the_share_history(self, box_api, db, checkpoints) -> None:
        box_org(box_api, db)
        box_api.deactivate(CAROL)
        box_api.add_event(
            "COLLABORATION_INVITE",
            {"type": "collaboration", "id": "collab-2", "item": {"type": "folder", "id": "fold-carol"},
             "accessible_by": {"type": "user", "id": BOB, "login": BOB_EMAIL}},
            created_by={"type": "user", "id": CAROL, "login": CAROL_EMAIL},
            additional_details={"collab_id": "collab-2"},
        )
        connector = await ready_connector(db, checkpoints)

        await connector.run_sync()

        for item in CAROLS:
            assert readers(db, item) == set(), item


class TestAWalkCutShort:
    async def test_a_full_sync_that_could_not_read_everything_removes_nothing_it_did_not_see(
        self, box_api, db, checkpoints
    ) -> None:
        box_org(box_api, db)
        connector = await synced(box_api, db, checkpoints)
        assert connector.full_sync_coverage().complete
        # Bob's walk stops at his root: what it did not reach must keep its links.
        box_api.fail("GET", "/2.0/folders/0/items", 503, times=10, as_user=BOB)
        checkpoints.cursor().clear()

        await connector.run_sync()

        assert not connector.full_sync_coverage().complete
        seen = FullSyncSeen()
        seen.add("records", "r-1")
        graph = MagicMock()
        graph.unlink_nodes_missed_by_full_sync = AsyncMock(return_value=(0, True))
        await _unlink_what_full_sync_missed(connector, connector.connector_id, graph, logging.getLogger("t"), seen)
        graph.unlink_nodes_missed_by_full_sync.assert_not_awaited()
        assert BOB_EMAIL in readers(db, "fold-alice")
