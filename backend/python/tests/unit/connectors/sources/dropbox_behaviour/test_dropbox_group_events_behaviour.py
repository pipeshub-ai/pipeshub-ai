"""Dropbox team connector: group changes read from the team event log.

The connector, ``DropboxDataSource`` and the Dropbox SDK are real. The SDK's
HTTP requests are answered by an in-memory stub, and our databases are
in-memory fakes.

Dropbox lists an event once. A member removal or a group deletion that cannot
be saved must therefore not be lost when the cursor moves on: the group is
queued next to the cursor, and every later run tries again until the access
the event took away is gone.
"""

import logging
from unittest.mock import MagicMock

import pytest
import requests
from dropbox_behaviour_fakes import (
    EVENTS,
    MEMBERS,
    MORE_MEMBERS,
    DropboxApiStub,
    FakeCheckpointStore,
    FakeGroupsDb,
    group_deleted,
    group_renamed,
    member_added,
    member_removed,
)

from app.connectors.core.base.sync_point.sync_point import (
    generate_record_sync_point_key,
)
from app.connectors.sources.dropbox.connector import DropboxConnector
from app.sources.client.dropbox.dropbox_ import DropboxClient, DropboxTokenConfig
from app.sources.external.dropbox.dropbox_ import DropboxDataSource

ENG = ("g:eng", "Engineering")
OLD = ("g:old", "Old project")
ANA, BEN, CAL = "ana@acme.com", "ben@acme.com", "cal@acme.com"
GROUP_EVENTS = generate_record_sync_point_key("user_group_events", "team_events", "global")


@pytest.fixture
def api(monkeypatch: pytest.MonkeyPatch) -> DropboxApiStub:
    stub = DropboxApiStub()

    def _session(*_: object, **__: object) -> requests.Session:
        session = requests.Session()
        session.mount("https://", stub)
        return session

    monkeypatch.setattr("dropbox.dropbox_client.create_session", _session)
    # The SDK retries a 5xx answer; its delays are not slept.
    monkeypatch.setattr("dropbox.dropbox_client.time.sleep", lambda _seconds: None)
    return stub


@pytest.fixture
def db() -> FakeGroupsDb:
    groups = FakeGroupsDb()
    groups.add_group(ENG, ANA, BEN)
    groups.add_group(OLD, CAL)
    return groups


@pytest.fixture
async def connector(api: DropboxApiStub, db: FakeGroupsDb) -> DropboxConnector:
    """A connector whose last group sync stopped at cursor ``c1``."""
    connector = DropboxConnector(
        logging.getLogger("test.dropbox"), db, FakeCheckpointStore(), MagicMock(), "dropbox-1", "team", "creator-1",
    )
    client = await DropboxClient.build_with_config(DropboxTokenConfig(token="team-token"), is_team=True)
    connector.data_source = DropboxDataSource(client)
    await connector.dropbox_cursor_sync_point.update_sync_point(GROUP_EVENTS, {"cursor": "c1"})
    return connector


async def saved_cursor(connector: DropboxConnector) -> str:
    return (await connector.dropbox_cursor_sync_point.read_sync_point(GROUP_EVENTS))["cursor"]


async def owed(connector: DropboxConnector) -> tuple[list[str], list[str]]:
    """The groups whose members must be read again, and the groups that must still be deleted."""
    saved = await connector.dropbox_cursor_sync_point.read_sync_point(GROUP_EVENTS)
    return saved.get("pendingGroupMemberReads", []), saved.get("pendingGroupDeletes", [])


async def sync(connector: DropboxConnector) -> None:
    await connector._sync_group_changes_with_cursor()


async def refused_removal_of_ben(connector: DropboxConnector, api: DropboxApiStub, db: FakeGroupsDb) -> None:
    """One run in which Ben's removal from Engineering could not be saved; Dropbox no longer lists him."""
    api.page("c1", [member_removed(ENG, BEN)], next_cursor="c2")
    api.members[ENG[0]] = [ANA]
    db.fail_member_removal.add((ENG[0], BEN))
    await sync(connector)
    db.fail_member_removal.clear()
    assert db.members(ENG) == [ANA, BEN]
    assert await owed(connector) == ([ENG[0]], [])


class TestGroupEvents:
    async def test_a_removed_member_loses_group_membership_and_the_cursor_moves_on(self, connector, api, db) -> None:
        api.page("c1", [member_removed(ENG, BEN), group_deleted(OLD)], next_cursor="c2")

        await sync(connector)

        assert db.members(ENG) == [ANA]
        assert OLD[0] not in db.groups
        assert await saved_cursor(connector) == "c2"
        assert await owed(connector) == ([], [])
        assert api.member_reads() == 0

    async def test_a_member_removal_the_database_refuses_is_made_good_on_the_next_run(self, connector, api, db) -> None:
        api.page("c1", [member_added(ENG, CAL), member_removed(ENG, BEN), group_deleted(OLD)], next_cursor="c2")
        api.members[ENG[0]] = [ANA, CAL]
        db.fail_member_removal.add((ENG[0], BEN))

        await sync(connector)

        assert db.members(ENG) == [ANA, BEN, CAL], "Ben is still a member"
        assert OLD[0] not in db.groups, "the event after the failed one is still applied"
        assert await saved_cursor(connector) == "c2"
        assert await owed(connector) == ([ENG[0]], []), "Dropbox lists the removal once, so it must stay owed"

        db.fail_member_removal.clear()
        await sync(connector)

        assert db.members(ENG) == [ANA, CAL], "the group's members were read again, which takes Ben out"
        assert await owed(connector) == ([], [])

    @pytest.mark.parametrize("broken", ["dropbox", "database"])
    async def test_members_that_cannot_be_read_or_saved_stay_owed(self, connector, api, db, broken: str) -> None:
        await refused_removal_of_ben(connector, api, db)
        if broken == "dropbox":
            api.failing.add(MEMBERS)
        else:
            db.fail_group_write.add(ENG[0])

        await sync(connector)

        assert db.members(ENG) == [ANA, BEN]
        assert await owed(connector) == ([ENG[0]], [])

        api.failing.clear()
        db.fail_group_write.clear()
        await sync(connector)

        assert db.members(ENG) == [ANA]
        assert await owed(connector) == ([], [])

    async def test_part_of_a_member_list_is_not_saved_in_place_of_the_stored_members(self, connector, api, db) -> None:
        await refused_removal_of_ben(connector, api, db)
        api.members[ENG[0]] = [ANA, CAL]
        api.member_page_size = 1
        api.failing.add(MORE_MEMBERS)

        await sync(connector)

        assert db.members(ENG) == [ANA, BEN], "saving the first page alone would have dropped Cal"
        assert await owed(connector) == ([ENG[0]], [])

    async def test_a_group_deletion_the_database_refuses_is_made_good_on_the_next_run(self, connector, api, db) -> None:
        api.page("c1", [group_deleted(OLD), member_removed(ENG, BEN)], next_cursor="c2")
        db.fail_group_delete.add(OLD[0])

        await sync(connector)

        assert db.members(OLD) == [CAL], "the group still gives Cal its access"
        assert db.members(ENG) == [ANA]
        assert await saved_cursor(connector) == "c2"
        assert await owed(connector) == ([], [OLD[0]])

        await sync(connector)

        assert await owed(connector) == ([], [OLD[0]]), "still owed for as long as it fails"

        db.fail_group_delete.clear()
        await sync(connector)

        assert OLD[0] not in db.groups
        assert await owed(connector) == ([], [])

    async def test_a_group_deleted_while_its_members_are_owed_a_read_needs_none(self, connector, api, db) -> None:
        await refused_removal_of_ben(connector, api, db)
        api.page("c2", [group_deleted(ENG)], next_cursor="c3")
        api.failing.add(MEMBERS)
        await sync(connector)
        assert ENG[0] not in db.groups
        reads = api.member_reads()

        await sync(connector)

        assert await owed(connector) == ([], [])
        assert api.member_reads() == reads, "there is no stored group left to correct"

    async def test_what_is_owed_survives_a_run_that_cannot_fetch_the_events(self, connector, api, db) -> None:
        await refused_removal_of_ben(connector, api, db)
        api.failing.update({EVENTS, MEMBERS})

        await sync(connector)

        assert await saved_cursor(connector) == "c2"
        assert await owed(connector) == ([ENG[0]], [])

    async def test_an_event_that_takes_no_access_away_is_not_owed_anything(self, connector, api, db) -> None:
        renamed = (ENG[0], "Platform")
        api.page("c1", [group_renamed(renamed, ENG[1]), member_removed(ENG, BEN)], next_cursor="c2")
        db.fail_rename.add(ENG[0])

        await sync(connector)

        assert db.groups[ENG[0]]["name"] == ENG[1]
        assert db.members(ENG) == [ANA]
        assert await saved_cursor(connector) == "c2"
        assert await owed(connector) == ([], [])

    async def test_every_page_of_events_is_read_once_and_the_last_cursor_is_saved(self, connector, api, db) -> None:
        api.page("c1", [member_added(ENG, CAL)], next_cursor="c2", has_more=True)
        api.page("c2", [member_removed(ENG, BEN)], next_cursor="c3")

        await sync(connector)

        assert api.cursors_read() == ["c1", "c2"]
        assert db.members(ENG) == [ANA, CAL]
        assert await saved_cursor(connector) == "c3"
