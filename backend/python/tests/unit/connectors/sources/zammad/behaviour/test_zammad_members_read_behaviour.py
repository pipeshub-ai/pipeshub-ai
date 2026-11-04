"""Zammad group and role members when the user or role list can't be read in full.

Writing a group or role replaces its stored members, so a read that stopped for a
reason that passes (no answer, a rate limit, a server error) must leave them as
stored, while a complete read writes them as Zammad has them, removals included.
A read Zammad refuses (403) repeats every sync, so it fails closed instead. Either
way the sync ends as failed (``ZammadSyncIncompleteError``) once the rest is written.

A group's members are the users who may read its tickets, worked out from the user
list (each user's own ``group_ids``) and the role list (each role's permissions and
``group_ids``), so both lists gate the members.

The connector's run_sync and the real ``ZammadDataSource`` run as they do in
production, over an in-memory Zammad answering HTTP requests. The store is an
in-memory stand-in that replaces members the way the processor does.
"""

import logging
from collections.abc import AsyncIterator
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from app.connectors.sources.zammad.connector import ZammadConnector, ZammadSyncIncompleteError
from app.sources.external.zammad.zammad import ZammadDataSource
from zammad_behaviour_fakes import (
    AGENT_ROLE_ID,
    FakeConfigService,
    FakeDirectory,
    FakeStore,
    FakeZammadPeople,
)

CONNECTOR_ID = "zm-members"
SUPPORT, SALES = 1, 2
AGENT_ROLE = AGENT_ROLE_ID
# More than one page of users: the connector reads 100 per page.
USER_COUNT = 150


class World:
    def __init__(self) -> None:
        self.zammad = FakeZammadPeople({SUPPORT: "Support", SALES: "Sales"})
        self.directory = FakeDirectory()
        with patch("app.connectors.sources.zammad.connector.ZammadApp"):
            self.connector = ZammadConnector(
                logger=logging.getLogger("test.zammad.members"),
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
        # Tickets and the knowledge base aren't what these tests are about.
        self.connector._sync_tickets_for_groups = AsyncMock()
        self.connector._remove_tickets_in_excluded_groups = AsyncMock()
        self.connector._sync_knowledge_bases = AsyncMock()

    async def sync(self) -> None:
        await self.connector.run_sync()

    async def sync_incomplete(self) -> list[str]:
        """A sync that could not read every part: it writes the rest, then ends as failed."""
        with pytest.raises(ZammadSyncIncompleteError) as raised:
            await self.connector.run_sync()
        return raised.value.parts

    def members(self, group_id: int) -> set[str]:
        return self.directory.group_members[str(group_id)]

    def agents(self) -> set[str]:
        return self.directory.role_members[str(AGENT_ROLE)]


def emails(*user_ids: int) -> set[str]:
    return {f"u{uid}@example.com" for uid in user_ids}


EVERYONE = emails(*range(1, USER_COUNT + 1))


@pytest.fixture
async def world() -> AsyncIterator[World]:
    w = World()
    for uid in range(1, USER_COUNT + 1):
        w.zammad.add_user(uid, groups=(SUPPORT, SALES) if uid <= 10 else (SUPPORT,))
    await w.sync()
    assert w.members(SUPPORT) == EVERYONE
    assert w.members(SALES) == emails(*range(1, 11))
    assert w.agents() == EVERYONE
    yield w


@pytest.mark.parametrize("status", [429, 500, 503])
async def test_a_user_page_that_fails_leaves_group_and_role_members_as_stored(world: World, status: int) -> None:
    world.zammad.users_page_status[2] = status

    assert await world.sync_incomplete() == ["the user list (interrupted)"]

    assert world.members(SUPPORT) == EVERYONE
    assert world.members(SALES) == emails(*range(1, 11))
    assert world.agents() == EVERYONE


async def test_a_user_page_that_gets_no_answer_leaves_members_as_stored(world: World) -> None:
    world.zammad.users_page_unanswered.add(2)

    await world.sync_incomplete()

    assert world.members(SUPPORT) == EVERYONE
    assert world.agents() == EVERYONE


async def test_a_user_list_that_fails_on_its_first_page_takes_no_one_out(world: World) -> None:
    world.zammad.users_page_status[1] = 502

    await world.sync_incomplete()

    assert world.members(SUPPORT) == EVERYONE
    assert world.members(SALES) == emails(*range(1, 11))
    assert world.agents() == EVERYONE


async def test_the_next_complete_read_applies_what_changed_meanwhile(world: World) -> None:
    world.zammad.users_page_status[2] = 503
    world.zammad.remove_user(120)
    await world.sync_incomplete()
    assert world.members(SUPPORT) == EVERYONE  # left as stored while the list was cut short

    del world.zammad.users_page_status[2]
    await world.sync()

    assert world.members(SUPPORT) == EVERYONE - emails(120)
    assert world.agents() == EVERYONE - emails(120)


async def test_a_complete_read_removes_a_user_who_left(world: World) -> None:
    world.zammad.remove_user(5)
    world.zammad.remove_user(140)

    await world.sync()

    assert world.members(SUPPORT) == EVERYONE - emails(5, 140)
    assert world.members(SALES) == emails(*range(1, 11)) - emails(5)
    assert world.agents() == EVERYONE - emails(5, 140)


async def test_a_user_list_zammad_refuses_fails_closed(world: World) -> None:
    """403 repeats every sync: members not read lose access rather than keep it unchecked."""
    world.zammad.users_page_status[2] = 403

    assert await world.sync_incomplete() == ["the user list (refused)"]

    assert world.members(SUPPORT) == emails(*range(1, 101))
    assert world.agents() == emails(*range(1, 101))


@pytest.mark.parametrize("status", [429, 500, 503])
async def test_a_role_list_that_fails_leaves_group_and_role_members_as_stored(world: World, status: int) -> None:
    """Who reads a group depends on the roles, so a role list cut short changes no member."""
    world.zammad.roles_page_status[1] = status
    world.zammad.remove_user(3)
    groups_written, roles_written = world.directory.group_writes, world.directory.role_writes

    assert await world.sync_incomplete() == ["the role list (interrupted)"]

    assert world.members(SALES) == emails(*range(1, 11))
    assert world.members(SUPPORT) == EVERYONE
    assert world.agents() == EVERYONE
    assert (world.directory.group_writes, world.directory.role_writes) == (groups_written, roles_written)


async def test_a_role_list_that_gets_no_answer_leaves_members_as_stored(world: World) -> None:
    world.zammad.roles_page_unanswered.add(1)
    world.zammad.remove_user(3)

    await world.sync_incomplete()

    assert world.members(SUPPORT) == EVERYONE
    assert world.agents() == EVERYONE


async def test_a_role_list_zammad_refuses_fails_closed(world: World) -> None:
    """With no role read, no user is known to hold ticket.agent, so no group keeps a member."""
    world.zammad.roles_page_status[1] = 403

    assert await world.sync_incomplete() == ["the role list (refused)"]

    assert world.members(SUPPORT) == set()
    assert world.members(SALES) == set()


@pytest.mark.xfail(strict=True, reason=(
    "Suspected gap: with the role list refused no AppRole is written, so every role keeps its stored "
    "members (and the knowledge-base access granted to the role) although the read failed closed for groups."
))
async def test_a_role_list_zammad_refuses_takes_role_members_away(world: World) -> None:
    world.zammad.roles_page_status[1] = 403

    await world.sync_incomplete()

    assert world.agents() == set()


async def test_a_role_list_without_permission_names_leaves_members_as_stored(world: World) -> None:
    """Without the names nobody would count as an agent, so every group would be emptied."""
    world.zammad.honours_expand = False
    world.zammad.remove_user(3)

    assert await world.sync_incomplete() == ["the role list (interrupted)"]

    assert world.members(SUPPORT) == EVERYONE
    assert world.agents() == EVERYONE


async def test_group_members_come_from_the_lists_not_a_read_of_each_group(world: World) -> None:
    world.zammad.calls.clear()

    await world.sync()

    assert world.zammad.group_detail_reads() == []
    role_reads = [params for path, params in world.zammad.calls if path == "/api/v1/roles"]
    assert role_reads and all(params.get("expand") == "true" for params in role_reads)
