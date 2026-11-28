"""An account that left Zammad loses the connector, and only after a complete user read.

A Zammad team connector's records reach a person only through their link to the
connector: a published answer's organisation grant, a group's or a role's grant all
pass that gate. So an account deactivated in Zammad ("Inactive users aren't able to
login anymore") or deleted must lose its link, with its own grants, at the next sync
that read the whole user list; a list cut short, refused, or holding only the token's
own record (what a token without ticket.agent or admin.user is shown) withdraws nobody.

The connector's run_sync and the real ``ZammadDataSource`` run over an in-memory
Zammad answering HTTP requests; the store keeps links the way the processor does.
"""

import asyncio
import logging
from collections.abc import AsyncIterator
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from zammad_behaviour_fakes import (
    FakeConfigService,
    FakeDirectory,
    FakeHttpResponse,
    FakeStore,
    FakeZammadPeople,
)

from app.connectors.sources.zammad.connector import (
    ZammadConnector,
    ZammadSyncIncompleteError,
)
from app.sources.client.http.http_request import HTTPRequest
from app.sources.external.zammad.zammad import ZammadDataSource

CONNECTOR_ID = "zm-departed"
SUPPORT = 1
# More than one page of users: the connector reads 100 per page.
USER_COUNT = 150


class World:
    def __init__(self) -> None:
        self.zammad = FakeZammadPeople({SUPPORT: "Support"})
        self.zammad.me = 1  # the token's own account is one of the listed users
        self.directory = FakeDirectory()
        with patch("app.connectors.sources.zammad.connector.ZammadApp"):
            self.connector = ZammadConnector(
                logger=logging.getLogger("test.zammad.departed"),
                data_entities_processor=self.directory,
                data_store_provider=FakeStore(),
                config_service=FakeConfigService(),
                connector_id=CONNECTOR_ID,
                scope="team",
                created_by="admin",
            )
        client = MagicMock()
        client.get_client.return_value = self.zammad
        client.get_base_url.return_value = "https://zammad.test"
        self.connector.external_client = client
        self.connector.base_url = "https://zammad.test"
        self.connector._get_fresh_datasource = AsyncMock(return_value=ZammadDataSource(client))
        self.connector._sync_tickets_for_groups = AsyncMock()
        self.connector._remove_tickets_in_excluded_groups = AsyncMock()
        self.connector._sync_knowledge_bases = AsyncMock()

    async def sync(self) -> None:
        await self.connector.run_sync()

    async def sync_incomplete(self) -> list[str]:
        with pytest.raises(ZammadSyncIncompleteError) as raised:
            await self.connector.run_sync()
        return raised.value.parts

    def linked(self) -> set[int]:
        return {int(sid) for sid in self.directory.links}

    def user_reads(self) -> list[str]:
        return [path for path, _ in self.zammad.calls if path.startswith("/api/v1/users/") and path != "/api/v1/users/me"]


EVERYONE = set(range(1, USER_COUNT + 1))


@pytest.fixture
async def world() -> AsyncIterator[World]:
    w = World()
    for uid in EVERYONE:
        w.zammad.add_user(uid)
    await w.sync()
    assert w.linked() == EVERYONE
    yield w


async def test_a_deactivated_account_loses_the_connector_and_its_grants_at_the_next_full_read(world: World) -> None:
    world.zammad.users[7]["active"] = False
    world.zammad.users[130]["active"] = False

    await world.sync()

    assert world.linked() == EVERYONE - {7, 130}
    assert world.directory.withdrawals == [({"7", "130"}, True)]
    assert "u7@example.com" not in world.directory.group_members[str(SUPPORT)]
    assert world.user_reads() == []  # listed as inactive: nothing to confirm


async def test_a_deleted_account_loses_the_connector_once_zammad_confirms_it_is_gone(world: World) -> None:
    world.zammad.remove_user(42)
    world.zammad.calls.clear()

    await world.sync()

    assert world.linked() == EVERYONE - {42}
    assert world.directory.withdrawals == [({"42"}, True)]
    assert world.user_reads() == ["/api/v1/users/42"]


async def test_an_account_whose_email_was_removed_loses_the_connector(world: World) -> None:
    world.zammad.users[9]["email"] = ""

    await world.sync()

    assert world.linked() == EVERYONE - {9}


async def test_an_account_missing_from_the_list_but_still_in_zammad_keeps_the_connector(world: World) -> None:
    """The list pages by offset; a user deleted mid-read pushes one still there off a page."""
    hidden = world.zammad.users.pop(55)
    real_execute = world.zammad.execute

    async def execute(request: HTTPRequest) -> FakeHttpResponse:  # the list misses 55, a read of 55 still finds it
        if request.url.endswith("/api/v1/users/55"):
            world.zammad.users[55] = hidden
            try:
                return await real_execute(request)
            finally:
                world.zammad.users.pop(55)
        return await real_execute(request)

    world.zammad.execute = execute

    await world.sync()

    assert world.linked() == EVERYONE
    assert world.directory.withdrawals == []


@pytest.mark.parametrize("status", [429, 500, 503])
async def test_an_absent_account_zammad_could_not_be_asked_about_keeps_the_connector(world: World, status: int) -> None:
    world.zammad.remove_user(42)
    world.zammad.user_read_status[42] = status

    assert await world.sync_incomplete() == ["user 42"]

    assert world.linked() == EVERYONE


@pytest.mark.parametrize("status", [403, 429, 500, 503])
async def test_a_user_list_cut_short_withdraws_nobody(world: World, status: int) -> None:
    world.zammad.users[7]["active"] = False
    world.zammad.remove_user(42)
    world.zammad.users_page_status[2] = status

    await world.sync_incomplete()

    assert world.linked() == EVERYONE
    assert world.directory.withdrawals == []


async def test_a_user_list_that_gets_no_answer_withdraws_nobody(world: World) -> None:
    world.zammad.remove_user(42)
    world.zammad.users_page_unanswered.add(1)

    await world.sync_incomplete()

    assert world.linked() == EVERYONE


@pytest.mark.parametrize("roles_status", [None, 403], ids=["roles-read", "roles-refused"])
async def test_a_token_shown_only_its_own_record_withdraws_nobody(world: World, roles_status: int | None) -> None:
    """UserPolicy::Scope: without ticket.agent or admin.user the list holds the caller alone. That says nothing of
    anyone else, so links, group members and role members all stay as stored, and the sync does not count as done."""
    groups_before = {gid: set(m) for gid, m in world.directory.group_members.items()}
    roles_before = {rid: set(m) for rid, m in world.directory.role_members.items()}
    assert len(groups_before[str(SUPPORT)]) == USER_COUNT
    world.zammad.lists_everyone = False
    if roles_status:
        world.zammad.roles_page_status[1] = roles_status

    parts = await world.sync_incomplete()

    assert "the user list (interrupted)" in parts
    assert world.linked() == EVERYONE
    assert world.directory.withdrawals == []
    assert world.directory.group_members == groups_before
    assert world.directory.role_members == roles_before


class RecordingNotifications:
    def __init__(self) -> None:
        self.sent: list[dict[str, object]] = []

    async def publish_notification(self, **kwargs: object) -> bool:
        self.sent.append(kwargs)
        return True


async def test_a_token_shown_only_its_own_record_tells_the_owner_once_which_permission_is_missing(
    world: World,
) -> None:
    notices = RecordingNotifications()
    world.connector._notification_service = notices
    world.zammad.lists_everyone = False

    for _ in range(2):
        await world.sync_incomplete()
        await asyncio.gather(*world.connector._background_tasks)

    assert len(notices.sent) == 1
    assert "ticket.agent or admin.user" in str(notices.sent[0]["message"])


async def test_a_token_whose_own_account_cannot_be_read_withdraws_nobody(world: World) -> None:
    world.zammad.lists_everyone = False
    world.zammad.path_status["/api/v1/users/me"] = 503
    groups_before = {gid: set(m) for gid, m in world.directory.group_members.items()}

    await world.sync_incomplete()

    assert world.linked() == EVERYONE
    assert world.directory.group_members == groups_before


async def test_a_failed_read_of_the_links_withdraws_nobody_and_fails_the_sync(world: World) -> None:
    world.zammad.users[7]["active"] = False
    world.directory.fail_link_read = True

    assert await world.sync_incomplete() == ["the linked accounts"]

    assert world.linked() == EVERYONE


async def test_a_reactivated_account_gets_the_connector_back(world: World) -> None:
    world.zammad.users[7]["active"] = False
    await world.sync()
    assert 7 not in world.linked()

    world.zammad.users[7]["active"] = True
    await world.sync()

    assert world.linked() == EVERYONE
    assert "u7@example.com" in world.directory.group_members[str(SUPPORT)]


async def test_an_unchanged_sync_withdraws_nobody_and_asks_nothing_more(world: World) -> None:
    world.zammad.calls.clear()

    await world.sync()

    assert world.directory.withdrawals == []
    assert [path for path, _ in world.zammad.calls if path.startswith("/api/v1/users/")] == []
