"""A Zammad group's members in PipesHub are the agents who may read its tickets.

Zammad lets an agent read a group's tickets with ``read`` or ``full`` access to
the group, held directly or through an active role, and only while the agent is
active and holds ``ticket.agent``. A group's ``user_ids`` lists every access
level, so ``change``, ``create`` or ``overview`` alone used to make a member who
could open tickets in PipesHub that Zammad would not show them.

The connector's run_sync and the real ``ZammadDataSource`` run as they do in
production, over an in-memory Zammad answering HTTP requests.
"""

import logging
from unittest.mock import AsyncMock, MagicMock, patch

from zammad_behaviour_fakes import (
    AGENT_ROLE_ID,
    FakeConfigService,
    FakeDirectory,
    FakeStore,
    FakeZammadPeople,
    agent_role,
)

from app.connectors.sources.zammad.connector import ZammadConnector
from app.sources.external.zammad.zammad import ZammadDataSource

SUPPORT, SALES = 1, 2
CUSTOMER_ROLE, TEAM_ROLE = 3, 7


class World:
    def __init__(self) -> None:
        self.zammad = FakeZammadPeople({SUPPORT: "Support", SALES: "Sales"})
        self.zammad.roles[CUSTOMER_ROLE] = agent_role(CUSTOMER_ROLE, name="Customer", permissions=("ticket.customer",))
        self.directory = FakeDirectory()
        with patch("app.connectors.sources.zammad.connector.ZammadApp"):
            self.connector = ZammadConnector(
                logger=logging.getLogger("test.zammad.readers"),
                data_entities_processor=self.directory,
                data_store_provider=FakeStore(),
                config_service=FakeConfigService(),
                connector_id="zm-readers",
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

    def members(self, group_id: int) -> set[str]:
        return self.directory.group_members[str(group_id)]

    def role_members(self, role_id: int) -> set[str]:
        return self.directory.role_members[str(role_id)]


def emails(*user_ids: int) -> set[str]:
    return {f"u{uid}@example.com" for uid in user_ids}


async def test_read_or_full_access_makes_a_member_and_change_create_or_overview_do_not() -> None:
    world = World()
    world.zammad.add_user(1, access=("full",))
    world.zammad.add_user(2, access=("read",))
    world.zammad.add_user(3, access=("change",))
    world.zammad.add_user(4, access=("create", "overview"))
    world.zammad.add_user(5, access=("read", "change"))

    await world.sync()

    assert world.members(SUPPORT) == emails(1, 2, 5)
    # Zammad's own list of the group's users has everyone with any row: it is not the readers.
    assert world.zammad.members_of(SUPPORT) == {1, 2, 3, 4, 5}


async def test_group_access_through_a_role_makes_a_member() -> None:
    world = World()
    world.zammad.roles[TEAM_ROLE] = agent_role(TEAM_ROLE, name="Sales team", permissions=(),
                                               group_ids={str(SALES): ["read"]})
    world.zammad.add_user(1, groups=(), roles=(AGENT_ROLE_ID, TEAM_ROLE))
    world.zammad.add_user(2, groups=(), roles=(AGENT_ROLE_ID,))

    await world.sync()

    assert world.members(SALES) == emails(1)
    assert world.members(SUPPORT) == set()


async def test_a_user_without_ticket_agent_is_no_member_whatever_their_access() -> None:
    world = World()
    world.zammad.add_user(1, roles=(CUSTOMER_ROLE,), access=("full",))
    world.zammad.add_user(2, roles=(AGENT_ROLE_ID,), access=("full",))

    await world.sync()

    assert world.members(SUPPORT) == emails(2)


async def test_the_ticket_permission_grants_ticket_agent() -> None:
    world = World()
    world.zammad.roles[AGENT_ROLE_ID] = agent_role(permissions=("ticket",))
    world.zammad.add_user(1, access=("read",))

    await world.sync()

    assert world.members(SUPPORT) == emails(1)


async def test_an_inactive_role_grants_nothing_and_is_written_with_no_members() -> None:
    world = World()
    world.zammad.roles[TEAM_ROLE] = agent_role(TEAM_ROLE, name="Sales team", group_ids={str(SALES): ["full"]})
    world.zammad.add_user(1, groups=(), roles=(TEAM_ROLE,))
    await world.sync()
    assert world.members(SALES) == emails(1)
    assert world.role_members(TEAM_ROLE) == emails(1)

    world.zammad.roles[TEAM_ROLE]["active"] = False
    await world.sync()

    assert world.members(SALES) == set()
    assert world.role_members(TEAM_ROLE) == set()


async def test_an_inactive_user_is_no_member_of_a_group_or_role() -> None:
    world = World()
    world.zammad.add_user(1)
    world.zammad.add_user(2)
    await world.sync()
    assert world.members(SUPPORT) == emails(1, 2)

    world.zammad.users[2]["active"] = False
    await world.sync()

    assert world.members(SUPPORT) == emails(1)
    assert world.role_members(AGENT_ROLE_ID) == emails(1)


async def test_an_inactive_group_has_no_members_and_no_record_group() -> None:
    world = World()
    world.zammad.add_user(1, groups=(SUPPORT, SALES))
    world.zammad.groups[SALES]["active"] = False

    await world.sync()

    assert world.members(SUPPORT) == emails(1)
    assert world.members(SALES) == set()
    assert set(world.directory.record_groups) == {"group_1"}


async def test_access_lowered_to_change_takes_the_member_out_on_the_next_sync() -> None:
    world = World()
    world.zammad.add_user(1, access=("full",))
    world.zammad.add_user(2, access=("read",))
    await world.sync()
    assert world.members(SUPPORT) == emails(1, 2)

    world.zammad.set_access(2, SUPPORT, ("change",))
    await world.sync()

    assert world.members(SUPPORT) == emails(1)
