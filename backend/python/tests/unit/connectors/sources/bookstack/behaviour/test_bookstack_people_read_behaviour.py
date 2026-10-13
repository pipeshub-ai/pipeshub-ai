"""BookStack role members and book grants when users or roles can't be read in full.

Writing a role replaces its stored members, and writing a book replaces its stored
grants. So a read that stopped for a reason that passes (no answer, a rate limit, a
server error) must leave them as stored and be tried again on the next sync, while a
complete read writes them as BookStack has them, removals included. A read BookStack
refuses (403) repeats every sync, so it fails closed instead.

The connector's own sync code and the real ``BookStackDataSource`` run as they do in
production, over an in-memory BookStack answering HTTP requests the way BookStack
does: an error is a status with a JSON body ``{"error": {...}}``. The store is an
in-memory stand-in that replaces members and grants the way the processor does.
"""

import logging
import re
from collections.abc import AsyncIterator
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch
from urllib.parse import urlparse

import pytest
from app.connectors.sources.bookstack.connector import BookStackConnector
from app.sources.client.http.http_request import HTTPRequest
from app.sources.external.bookstack.bookstack import BookStackDataSource

CONNECTOR_ID = "bs-people"
EDITOR, VIEWER = 2, 3
# Follows the default role permissions, so every role that may view books is granted.
DEFAULT_BOOK = 10
# Has its own role permissions.
EXPLICIT_BOOK = 20
OWNER = 1


class FakeResponse:
    def __init__(self, status: int, body: object) -> None:
        self.status = status
        self._body = body

    def json(self) -> object:
        return self._body


def error(status: int) -> FakeResponse:
    return FakeResponse(status, {"error": {"code": status, "message": "not now"}})


class FakeBookStackApi:
    """Users, roles and books behind BookStack's REST API, with the failures it can answer with."""

    def __init__(self) -> None:
        self.headers: dict[str, str] = {}
        self.users: dict[int, str] = {}
        self.roles: dict[int, dict[str, Any]] = {
            EDITOR: {"display_name": "Editor", "permissions": ["book-view-all", "book-update-all"], "users": []},
            VIEWER: {"display_name": "Viewer", "permissions": ["book-view-all"], "users": []},
        }
        self.books = {DEFAULT_BOOK: "Handbook", EXPLICIT_BOOK: "Finance"}
        # (path, offset) -> HTTP status answered instead of that page; a path alone fails every call to it.
        self.fail: dict[tuple[str, int | None], int] = {}
        self.unanswered: set[tuple[str, int | None]] = set()

    def get_base_url(self) -> str:
        return "https://bookstack.test"

    def add_user(self, user_id: int, *roles: int) -> None:
        self.users[user_id] = f"u{user_id}@example.com"
        for role in roles:
            self.roles[role]["users"].append(user_id)

    def remove_user(self, user_id: int) -> None:
        del self.users[user_id]
        for role in self.roles.values():
            if user_id in role["users"]:
                role["users"].remove(user_id)

    def _listing(self, rows: list[dict[str, Any]], params: dict[str, str]) -> FakeResponse:
        offset, count = int(params.get("offset", 0)), int(params.get("count", 100))
        return FakeResponse(200, {"data": rows[offset:offset + count], "total": len(rows)})

    async def execute(self, request: HTTPRequest) -> FakeResponse:
        path = urlparse(request.url).path
        params = dict(request.query_params or {})
        offset = int(params["offset"]) if "offset" in params else None
        for key in ((path, offset), (path, None)):
            if key in self.unanswered:
                raise ConnectionError("connection reset by peer")
            if key in self.fail:
                return error(self.fail[key])
        if path == "/api/users":
            rows = [{"id": uid, "name": f"User {uid}", "email": email} for uid, email in sorted(self.users.items())]
            return self._listing(rows, params)
        if match := re.fullmatch(r"/api/users/(\d+)", path):
            uid = int(match.group(1))
            if uid not in self.users:
                return error(404)
            roles = [{"id": rid, "display_name": r["display_name"]} for rid, r in self.roles.items() if uid in r["users"]]
            return FakeResponse(200, {"id": uid, "name": f"User {uid}", "email": self.users[uid], "roles": roles})
        if path == "/api/roles":
            rows = [{"id": rid, "display_name": r["display_name"]} for rid, r in sorted(self.roles.items())]
            return self._listing(rows, params)
        if match := re.fullmatch(r"/api/roles/(\d+)", path):
            rid = int(match.group(1))
            if rid not in self.roles:
                return error(404)
            role = self.roles[rid]
            return FakeResponse(200, {
                "id": rid, "display_name": role["display_name"], "permissions": list(role["permissions"]),
                "users": [{"id": uid, "name": f"User {uid}"} for uid in role["users"]],
            })
        if path == "/api/books":
            return self._listing([{"id": bid, "name": name} for bid, name in sorted(self.books.items())], params)
        if path in ("/api/shelves", "/api/chapters"):
            return self._listing([], params)
        if match := re.fullmatch(r"/api/content-permissions/book/(\d+)", path):
            if int(match.group(1)) == DEFAULT_BOOK:
                return FakeResponse(200, {"owner": {"id": OWNER}, "role_permissions": [],
                                          "fallback_permissions": {"inheriting": True}})
            return FakeResponse(200, {"owner": {"id": OWNER},
                                      "role_permissions": [{"role_id": VIEWER, "view": True}],
                                      "fallback_permissions": {"inheriting": False}})
        return error(404)


class Directory:
    """Roles and record groups as the processor stores them: each write replaces members or grants."""

    def __init__(self) -> None:
        self.org_id = "org-1"
        self.role_members: dict[str, set[str]] = {}
        self.grants: dict[str, set[tuple[str, str]]] = {}

    async def on_new_app_roles(self, roles: list[tuple[Any, list[Any]]]) -> None:
        for role, members in roles:
            self.role_members[role.source_role_id] = {m.email for m in members}

    async def on_app_role_deleted(self, external_role_id: str, connector_id: str) -> None:
        self.role_members.pop(external_role_id, None)

    async def on_new_record_groups(self, record_groups: list[tuple[Any, list[Any]]]) -> None:
        for group, permissions in record_groups:
            self.grants[group.external_group_id] = {
                (p.type.value, p.email or f"role {p.external_id}") for p in permissions
            }


class FakeSyncPoint:
    def __init__(self) -> None:
        self.points: dict[str, dict[str, str]] = {}

    async def read_sync_point(self, key: str) -> dict[str, str]:
        return dict(self.points.get(key, {}))

    async def update_sync_point(self, key: str, data: dict[str, str]) -> None:
        self.points[key] = dict(data)


class World:
    def __init__(self) -> None:
        self.api = FakeBookStackApi()
        self.directory = Directory()
        with patch("app.connectors.sources.bookstack.connector.BookStackApp"), \
             patch("app.connectors.sources.bookstack.connector.SyncPoint", side_effect=lambda **_: FakeSyncPoint()):
            self.connector = BookStackConnector(
                logger=logging.getLogger("bookstack-people"),
                data_entities_processor=self.directory,
                data_store_provider=AsyncMock(),
                config_service=AsyncMock(),
                connector_id=CONNECTOR_ID,
                scope="team",
                created_by="admin",
            )
        client = MagicMock()
        client.get_client.return_value = self.api
        self.connector.data_source = BookStackDataSource(client)
        # Small pages, so a listing takes several requests.
        self.connector.batch_size = 2
        # Pages aren't what these tests are about.
        self.connector._sync_records = AsyncMock(return_value=None)

    def full_resync(self) -> None:
        """What a full resync does first: clear the sync points."""
        self.connector.app_role_sync_point.points.clear()
        self.connector.record_sync_point.points.clear()

    async def sync_roles(self) -> None:
        await self.connector._sync_user_roles()

    async def sync_books(self) -> None:
        await self.connector._sync_record_groups()

    def members(self, role: int) -> set[str] | None:
        """The role's stored members; None when the role isn't stored."""
        return self.directory.role_members.get(str(role))

    def book_grants(self, book: int) -> set[tuple[str, str]]:
        return self.directory.grants[f"book/{book}"]


def emails(*user_ids: int) -> set[str]:
    return {f"u{uid}@example.com" for uid in user_ids}


OWNER_GRANT = ("OWNER", "u1@example.com")
DEFAULT_BOOK_GRANTS = {OWNER_GRANT, ("WRITER", f"role {EDITOR}"), ("READER", f"role {VIEWER}")}


@pytest.fixture
async def world() -> AsyncIterator[World]:
    w = World()
    for uid in (1, 2, 3, 4, 5):
        w.api.add_user(uid, EDITOR)
    for uid in (4, 5):
        w.api.roles[VIEWER]["users"].append(uid)
    await w.sync_roles()
    await w.sync_books()
    assert w.members(EDITOR) == emails(1, 2, 3, 4, 5)
    assert w.members(VIEWER) == emails(4, 5)
    assert w.book_grants(DEFAULT_BOOK) == DEFAULT_BOOK_GRANTS
    w.full_resync()
    yield w


# ---- role members and the user list ----

@pytest.mark.parametrize("status", [429, 500, 503])
async def test_a_failed_user_page_leaves_role_members_as_stored(world: World, status: int) -> None:
    world.api.fail[("/api/users", 2)] = status

    await world.sync_roles()

    assert world.members(EDITOR) == emails(1, 2, 3, 4, 5)
    assert world.members(VIEWER) == emails(4, 5)


async def test_a_user_page_that_gets_no_answer_leaves_role_members_as_stored(world: World) -> None:
    world.api.unanswered.add(("/api/users", 4))

    await world.sync_roles()

    assert world.members(EDITOR) == emails(1, 2, 3, 4, 5)
    assert world.members(VIEWER) == emails(4, 5)


async def test_a_failed_user_details_read_leaves_role_members_as_stored(world: World) -> None:
    world.api.fail[("/api/users/4", None)] = 500

    await world.sync_roles()

    assert world.members(EDITOR) == emails(1, 2, 3, 4, 5)
    assert world.members(VIEWER) == emails(4, 5)


async def test_a_first_sync_that_cannot_read_every_user_tries_again_next_sync() -> None:
    w = World()
    for uid in (1, 2, 3):
        w.api.add_user(uid, EDITOR)
    w.api.fail[("/api/users", 2)] = 503

    await w.sync_roles()
    assert str(EDITOR) not in w.directory.role_members

    del w.api.fail[("/api/users", 2)]
    await w.sync_roles()

    assert w.members(EDITOR) == emails(1, 2, 3)


async def test_a_complete_read_removes_a_user_who_left(world: World) -> None:
    world.api.remove_user(3)
    world.api.remove_user(5)

    await world.sync_roles()

    assert world.members(EDITOR) == emails(1, 2, 4)
    assert world.members(VIEWER) == emails(4)


async def test_a_user_deleted_between_the_list_and_their_details_is_left_out(world: World) -> None:
    """404 is a definite answer: the user is gone, so the read still counts as complete."""
    world.api.fail[("/api/users/3", None)] = 404

    await world.sync_roles()

    assert world.members(EDITOR) == emails(1, 2, 4, 5)


async def test_a_user_list_bookstack_refuses_fails_closed(world: World) -> None:
    """403 repeats every sync: members not read lose access rather than keep it unchecked."""
    world.api.fail[("/api/users", 2)] = 403

    await world.sync_roles()

    assert world.members(EDITOR) == emails(1, 2)
    assert world.members(VIEWER) == set()


async def test_a_role_update_whose_role_cannot_be_read_keeps_the_role(world: World) -> None:
    world.api.fail[(f"/api/roles/{VIEWER}", None)] = 503

    done = await world.connector._handle_role_update_event(VIEWER, {})

    assert world.members(VIEWER) == emails(4, 5)
    assert done is False  # so the role checkpoint stays and the next sync reads the event again


async def test_a_role_update_with_a_member_that_cannot_be_read_keeps_the_role(world: World) -> None:
    world.api.fail[("/api/users/5", None)] = 502

    done = await world.connector._handle_role_update_event(VIEWER, {})

    assert world.members(VIEWER) == emails(4, 5)
    assert done is False  # so the role checkpoint stays and the next sync reads the event again


# ---- book grants and the role details ----

async def test_a_failed_role_details_read_leaves_book_grants_as_stored(world: World) -> None:
    world.api.fail[(f"/api/roles/{VIEWER}", None)] = 503

    await world.sync_books()

    assert world.book_grants(DEFAULT_BOOK) == DEFAULT_BOOK_GRANTS


async def test_a_failed_role_list_leaves_book_grants_as_stored_and_is_tried_again(world: World) -> None:
    world.api.fail[("/api/roles", None)] = 500

    await world.sync_books()
    assert world.book_grants(DEFAULT_BOOK) == DEFAULT_BOOK_GRANTS
    assert world.connector.record_sync_point.points == {}

    world.api.roles[VIEWER]["permissions"] = []
    del world.api.fail[("/api/roles", None)]
    await world.sync_books()

    assert world.book_grants(DEFAULT_BOOK) == {OWNER_GRANT, ("WRITER", f"role {EDITOR}")}


async def test_a_complete_role_read_removes_a_deleted_role_from_book_grants(world: World) -> None:
    del world.api.roles[VIEWER]

    await world.sync_books()

    assert world.book_grants(DEFAULT_BOOK) == {OWNER_GRANT, ("WRITER", f"role {EDITOR}")}
