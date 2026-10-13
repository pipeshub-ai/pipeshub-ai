"""Zammad group and role members when the user list or a group can't be read in full.

Writing a group or role replaces its stored members, so a read that stopped for a
reason that passes (no answer, a rate limit, a server error) must leave them as
stored, while a complete read writes them as Zammad has them, removals included.
A read Zammad refuses (403) repeats every sync, so it fails closed instead.

The connector's run_sync and the real ``ZammadDataSource`` run as they do in
production, over an in-memory Zammad answering HTTP requests. The store is an
in-memory stand-in that replaces members the way the processor does.
"""

import logging
import re
from collections.abc import AsyncIterator
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch
from urllib.parse import urlparse

import pytest
from app.connectors.sources.zammad.connector import ZammadConnector
from app.sources.client.http.http_request import HTTPRequest
from app.sources.external.zammad.zammad import ZammadDataSource
from zammad_behaviour_fakes import FakeConfigService, FakeHttpResponse, FakeStore

CONNECTOR_ID = "zm-members"
SUPPORT, SALES = 1, 2
AGENT_ROLE = 2
# More than one page of users: the connector reads 100 per page.
USER_COUNT = 150


class FakeZammadPeople:
    """Users, groups and roles behind Zammad's REST API, with the failures it can answer with."""

    def __init__(self) -> None:
        self.users: dict[int, dict[str, Any]] = {}
        self.group_members: dict[int, list[int]] = {SUPPORT: [], SALES: []}
        # page number -> HTTP status answered instead of that page of users
        self.users_page_status: dict[int, int] = {}
        self.users_page_unanswered: set[int] = set()
        # group id -> HTTP status answered instead of the group's details
        self.group_status: dict[int, int] = {}

    def add_user(self, user_id: int, groups: tuple[int, ...] = (SUPPORT,), roles: tuple[int, ...] = (AGENT_ROLE,)) -> None:
        self.users[user_id] = {
            "id": user_id, "email": f"u{user_id}@example.com", "firstname": "User", "lastname": str(user_id),
            "active": True, "role_ids": list(roles),
        }
        for group in groups:
            self.group_members[group].append(user_id)

    def remove_user(self, user_id: int) -> None:
        del self.users[user_id]
        for members in self.group_members.values():
            if user_id in members:
                members.remove(user_id)

    async def execute(self, request: HTTPRequest) -> FakeHttpResponse:
        path = urlparse(request.url).path
        params = dict(request.query_params or {})
        page, per_page = int(params.get("page", 1)), int(params.get("per_page", 100))
        if path == "/api/v1/users":
            if page in self.users_page_unanswered:
                raise ConnectionError("connection reset by peer")
            if page in self.users_page_status:
                return FakeHttpResponse(self.users_page_status[page], {"error": "not now"})
            rows = [self.users[uid] for uid in sorted(self.users)]
            return FakeHttpResponse(200, rows[(page - 1) * per_page:page * per_page])
        if path == "/api/v1/groups":
            rows = [{"id": gid, "name": f"group-{gid}", "active": True} for gid in sorted(self.group_members)]
            return FakeHttpResponse(200, rows[(page - 1) * per_page:page * per_page])
        match = re.fullmatch(r"/api/v1/groups/(\d+)", path)
        if match:
            group_id = int(match.group(1))
            if group_id in self.group_status:
                return FakeHttpResponse(self.group_status[group_id], {"error": "not now"})
            return FakeHttpResponse(200, {"id": group_id, "user_ids": list(self.group_members[group_id])})
        if path == "/api/v1/roles":
            rows = [{"id": AGENT_ROLE, "name": "Agent", "active": True}]
            return FakeHttpResponse(200, rows[(page - 1) * per_page:page * per_page])
        return FakeHttpResponse(404, {"error": f"no route {path}"})


class Directory:
    """Groups and roles as the processor stores them: each write replaces the members."""

    def __init__(self) -> None:
        self.org_id = "org-1"
        self.group_members: dict[str, set[str]] = {}
        self.role_members: dict[str, set[str]] = {}
        self.group_writes = 0

    async def on_new_app_users(self, users: list[Any]) -> None:
        return None

    async def on_new_user_groups(self, user_groups: list[tuple[Any, list[Any]]]) -> None:
        self.group_writes += 1
        for group, members in user_groups:
            self.group_members[group.source_user_group_id] = {m.email for m in members}

    async def on_new_app_roles(self, roles: list[tuple[Any, list[Any]]]) -> None:
        for role, members in roles:
            self.role_members[role.source_role_id] = {m.email for m in members}

    async def on_new_record_groups(self, record_groups: list[tuple[Any, list[Any]]]) -> None:
        return None


class World:
    def __init__(self) -> None:
        self.zammad = FakeZammadPeople()
        self.directory = Directory()
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

    await world.sync()

    assert world.members(SUPPORT) == EVERYONE
    assert world.members(SALES) == emails(*range(1, 11))
    assert world.agents() == EVERYONE


async def test_a_user_page_that_gets_no_answer_leaves_members_as_stored(world: World) -> None:
    world.zammad.users_page_unanswered.add(2)

    await world.sync()

    assert world.members(SUPPORT) == EVERYONE
    assert world.agents() == EVERYONE


async def test_a_user_list_that_fails_on_its_first_page_takes_no_one_out(world: World) -> None:
    world.zammad.users_page_status[1] = 502

    await world.sync()

    assert world.members(SUPPORT) == EVERYONE
    assert world.members(SALES) == emails(*range(1, 11))
    assert world.agents() == EVERYONE


async def test_the_next_complete_read_applies_what_changed_meanwhile(world: World) -> None:
    world.zammad.users_page_status[2] = 503
    world.zammad.remove_user(120)
    await world.sync()
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

    await world.sync()

    assert world.members(SUPPORT) == emails(*range(1, 101))
    assert world.agents() == emails(*range(1, 101))


async def test_a_group_whose_members_fail_to_read_keeps_them_and_the_others_update(world: World) -> None:
    world.zammad.group_status[SALES] = 500
    world.zammad.remove_user(3)

    await world.sync()

    assert world.members(SALES) == emails(*range(1, 11))
    assert world.members(SUPPORT) == EVERYONE - emails(3)


async def test_a_group_zammad_refuses_to_show_is_saved_with_no_members(world: World) -> None:
    world.zammad.group_status[SALES] = 403

    await world.sync()

    assert world.members(SALES) == set()
    assert world.members(SUPPORT) == EVERYONE
