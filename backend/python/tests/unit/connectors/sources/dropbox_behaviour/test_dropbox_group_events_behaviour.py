"""Dropbox team connector: group changes read from the team event log.

The connector, ``DropboxDataSource`` and the Dropbox SDK are real. The SDK's
HTTP requests are answered by an in-memory stub, and our databases are
in-memory fakes.
"""

import logging
from unittest.mock import MagicMock

import pytest
import requests
from dropbox_behaviour_fakes import (
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


async def failed_runs(connector: DropboxConnector) -> int:
    return (await connector.dropbox_cursor_sync_point.read_sync_point(GROUP_EVENTS))["heldAttempts"]


class TestGroupEvents:
    async def test_a_removed_member_loses_group_membership_and_the_cursor_moves_on(self, connector, api, db) -> None:
        api.page("c1", [member_removed(ENG, BEN), group_deleted(OLD)], next_cursor="c2")

        await connector._sync_group_changes_with_cursor()

        assert db.members(ENG) == [ANA]
        assert OLD[0] not in db.groups
        assert await saved_cursor(connector) == "c2"

    async def test_a_member_removal_the_database_refuses_is_read_again_on_the_next_run(self, connector, api, db) -> None:
        api.page("c1", [member_added(ENG, CAL), member_removed(ENG, BEN), group_deleted(OLD)], next_cursor="c2")
        db.fail_member_removal.add((ENG[0], BEN))

        await connector._sync_group_changes_with_cursor()

        assert db.members(ENG) == [ANA, BEN, CAL], "Ben is still a member"
        assert await saved_cursor(connector) == "c1", "Dropbox lists the removal once, so the cursor must not pass it"
        assert await failed_runs(connector) == 1

        db.fail_member_removal.clear()
        await connector._sync_group_changes_with_cursor()

        assert db.members(ENG) == [ANA, CAL], "Ben is out, and reading Cal's addition twice added her once"
        assert OLD[0] not in db.groups
        assert await saved_cursor(connector) == "c2"
        assert await failed_runs(connector) == 0

    async def test_a_refused_removal_on_a_later_page_keeps_the_pages_before_it(self, connector, api, db) -> None:
        api.page("c1", [member_added(ENG, CAL)], next_cursor="c2", has_more=True)
        api.page("c2", [member_removed(ENG, BEN)], next_cursor="c3")
        db.fail_member_removal.add((ENG[0], BEN))

        await connector._sync_group_changes_with_cursor()

        assert db.members(ENG) == [ANA, BEN, CAL]
        assert await saved_cursor(connector) == "c2"

        db.fail_member_removal.clear()
        await connector._sync_group_changes_with_cursor()

        assert api.cursors_read == ["c1", "c2", "c2"], "only the page with the removal is read again"
        assert db.members(ENG) == [ANA, CAL]
        assert await saved_cursor(connector) == "c3"

    async def test_a_group_deletion_the_database_refuses_is_read_again_on_the_next_run(self, connector, api, db) -> None:
        api.page("c1", [group_deleted(OLD)], next_cursor="c2")
        db.fail_group_delete.add(OLD[0])

        await connector._sync_group_changes_with_cursor()

        assert db.members(OLD) == [CAL], "the group still gives Cal its access"
        assert await saved_cursor(connector) == "c1"

        db.fail_group_delete.clear()
        await connector._sync_group_changes_with_cursor()

        assert OLD[0] not in db.groups
        assert await saved_cursor(connector) == "c2"

    async def test_a_removal_the_database_keeps_refusing_is_skipped_after_five_runs(self, connector, api, db) -> None:
        api.page("c1", [member_removed(ENG, BEN), group_deleted(OLD)], next_cursor="c2")
        api.page("c2", [member_removed(ENG, ANA)], next_cursor="c3")
        db.fail_member_removal.add((ENG[0], BEN))

        for run in range(1, 5):
            await connector._sync_group_changes_with_cursor()
            assert (await saved_cursor(connector), await failed_runs(connector)) == ("c1", run)
        assert OLD[0] in db.groups
        await connector._sync_group_changes_with_cursor()

        assert await saved_cursor(connector) == "c2", "one removal that can't be saved must not stop group sync for good"
        assert await failed_runs(connector) == 0
        assert db.members(ENG) == [ANA, BEN]
        assert OLD[0] not in db.groups

        # The next page starts its own count: one failed run there holds it again.
        db.fail_member_removal.add((ENG[0], ANA))
        await connector._sync_group_changes_with_cursor()

        assert (await saved_cursor(connector), await failed_runs(connector)) == ("c2", 1)

    async def test_giving_up_on_one_removal_does_not_give_up_on_the_one_after_it(self, connector, api, db) -> None:
        api.page("c1", [member_removed(ENG, BEN), member_removed(ENG, ANA)], next_cursor="c2")
        db.fail_member_removal.add((ENG[0], BEN))
        for _ in range(4):
            await connector._sync_group_changes_with_cursor()

        # The run that gives up on Ben's removal is the first to reach Ana's, which fails this once.
        db.fail_member_removal.add((ENG[0], ANA))
        await connector._sync_group_changes_with_cursor()

        assert db.members(ENG) == [ANA, BEN]
        assert (await saved_cursor(connector), await failed_runs(connector)) == ("c1", 1), "Ana's removal has its own count"

        db.fail_member_removal.discard((ENG[0], ANA))
        await connector._sync_group_changes_with_cursor()

        assert db.members(ENG) == [BEN], "Ana is out; Ben's removal stays skipped without being counted again"
        assert await saved_cursor(connector) == "c2"

    async def test_a_page_that_cannot_be_fetched_keeps_the_count_of_failed_runs(self, connector, api, db) -> None:
        api.page("c1", [member_removed(ENG, BEN)], next_cursor="c2")
        db.fail_member_removal.add((ENG[0], BEN))
        await connector._sync_group_changes_with_cursor()
        assert await failed_runs(connector) == 1

        api.unavailable = True
        await connector._sync_group_changes_with_cursor()

        assert (await saved_cursor(connector), await failed_runs(connector)) == ("c1", 1)

    async def test_an_event_that_takes_no_access_away_still_does_not_hold_the_cursor(self, connector, api, db) -> None:
        renamed = (ENG[0], "Platform")
        api.page("c1", [group_renamed(renamed, ENG[1]), member_removed(ENG, BEN)], next_cursor="c2")
        db.fail_rename.add(ENG[0])

        await connector._sync_group_changes_with_cursor()

        assert db.groups[ENG[0]]["name"] == ENG[1]
        assert db.members(ENG) == [ANA]
        assert await saved_cursor(connector) == "c2"
