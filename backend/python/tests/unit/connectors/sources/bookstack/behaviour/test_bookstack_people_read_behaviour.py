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
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch
from urllib.parse import urlparse

import pytest

from app.connectors.sources.bookstack.connector import BookStackConnector, ReadOutcome
from app.sources.client.http.http_request import HTTPRequest
from app.sources.external.bookstack.bookstack import BookStackDataSource

CONNECTOR_ID = "bs-people"
EDITOR, VIEWER, AUDITOR = 2, 3, 4
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

    def bytes(self) -> bytes:
        # The real HTTPResponse's raw body; the data source reads it to tell an empty answer (204) apart.
        return b"" if self._body is None else b"{}"


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
        # The same, for the next call only.
        self.fail_once: dict[tuple[str, int | None], int] = {}
        self.unanswered: set[tuple[str, int | None]] = set()
        self.audit: list[dict[str, Any]] = []

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
            if key in self.fail_once:
                return error(self.fail_once.pop(key))
        if path == "/api/audit-log":
            wanted = params.get("filter[type]")
            return self._listing([e for e in self.audit if e["type"] == wanted], params)
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
        # Users the user sync stored. With require_user_rows, a role write attaches only
        # members whose user is stored, as the processor's get_user_by_email lookup does.
        self.user_rows: set[str] = set()
        self.require_user_rows = False
        self.fail_role_write = False
        # The graph can't be read or written. Like both graph providers, a failed user
        # lookup answers None unless raise_on_error is set, and a failed delete raises.
        self.fail_user_lookup = False
        self.fail_edge_delete = False
        self.fail_stored_read = False
        self.fail_role_list_read = False
        # Roles kept as they are stored and noted as seen by a full sync in progress.
        self.kept_as_seen: set[str] = set()

    async def on_new_app_users(self, users: list[Any]) -> None:
        self.user_rows.update(u.email for u in users)

    async def get_user_by_email(self, email: str, *, raise_on_error: bool = False) -> SimpleNamespace | None:
        if self.fail_user_lookup:
            if raise_on_error:
                raise RuntimeError("graph unavailable")
            return None
        return SimpleNamespace(id=email, email=email) if email in self.user_rows else None

    async def get_user_by_source_id(
        self, source_user_id: str, connector_id: str, *, raise_on_error: bool = False
    ) -> SimpleNamespace | None:
        return await self.get_user_by_email(f"u{source_user_id}@example.com", raise_on_error=raise_on_error)

    async def delete_edges_between_collections(self, from_id: str, *_: object, **__: object) -> None:
        if self.fail_edge_delete:
            raise RuntimeError("graph unavailable")
        for members in self.role_members.values():
            members.discard(from_id)

    async def on_new_app_roles(self, roles: list[tuple[Any, list[Any]]]) -> None:
        if self.fail_role_write:
            raise RuntimeError("graph unavailable")
        for role, members in roles:
            self.role_members[role.source_role_id] = {
                m.email for m in members if not self.require_user_rows or m.email in self.user_rows
            }

    async def has_stored_permissions(
        self, entity: str, connector_id: str, external_id: str, *, keep: bool = False
    ) -> bool:
        """Whether the role has stored members; like the processor, a failed read raises."""
        assert entity == "role"
        if self.fail_stored_read:
            raise RuntimeError("graph unavailable")
        stored = bool(self.role_members.get(external_id))
        if stored and keep:
            self.kept_as_seen.add(external_id)
        return stored

    async def get_nodes_by_filters(
        self, collection: str, filters: dict[str, Any], return_fields: list[str] | None = None,
        *, raise_on_error: bool = False,
    ) -> list[dict[str, Any]]:
        """The stored roles (or record groups); like the graph stores, a failed read answers [] unless raise_on_error."""
        assert filters == {"connectorId": CONNECTOR_ID}
        if collection == "recordGroups":
            return [{"externalGroupId": external_id} for external_id in self.grants]
        assert collection == "roles"
        if self.fail_role_list_read:
            if raise_on_error:
                raise RuntimeError("graph unavailable")
            return []
        return [{"externalRoleId": role_id} for role_id in self.role_members]

    async def on_app_role_deleted(self, external_role_id: str, connector_id: str) -> None:
        self.role_members.pop(external_role_id, None)

    async def on_new_record_groups(self, record_groups: list[tuple[Any, list[Any]]]) -> None:
        for group, permissions in record_groups:
            self.grants[group.external_group_id] = {
                (p.type.value, p.email or f"role {p.external_id}") for p in permissions
            }


class FakeSyncPoint:
    """Sync points as both graph stores keep them: an update merges into what is stored,
    so a key the update leaves out keeps its old value."""

    def __init__(self) -> None:
        self.points: dict[str, dict[str, object]] = {}

    async def read_sync_point(self, key: str) -> dict[str, object]:
        return dict(self.points.get(key, {}))

    async def update_sync_point(self, key: str, data: dict[str, object]) -> None:
        self.points.setdefault(key, {}).update(data)


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
        self.connector._sync_records_full = AsyncMock(return_value=None)

    def full_resync(self) -> None:
        """What a full resync does first: clear the sync points."""
        self.connector.app_role_sync_point.points.clear()
        self.connector.record_sync_point.points.clear()

    async def sync_roles(self) -> None:
        # The role pass of a sync: it writes members only once every user is stored.
        users = await self.connector.get_all_users()
        if users:
            await self.connector._sync_users_full(users)
        self.connector._users_stored = self.connector._user_list_read is not ReadOutcome.INTERRUPTED
        if not await self.connector._sync_user_roles_full():
            # As run_sync does: an unfinished role pass fails the run, so a full sync unlinks nothing.
            self.connector._health.fail("the roles and their members")

    async def sync_users(self) -> None:
        """The user part of a sync: the user list, then the user events of the audit log since the last sync."""
        users = await self.connector.get_all_users()
        if users:
            await self.connector._sync_users_full(users)
        events, self.api.audit = [e for e in self.api.audit if e["type"] in ("user_create", "user_update")], []
        if events:
            self.connector._users_stored = self.connector._user_list_read is not ReadOutcome.INTERRUPTED
            if not await self.connector._handle_user_upsert_event(events, users):
                # Not applied: the cursor stays, so the next sync reads the same events again.
                self.api.audit = events + self.api.audit

    def role_checkpoint(self) -> dict[str, dict[str, str]]:
        return self.connector.app_role_sync_point.points

    def user_checkpoint(self) -> dict[str, dict[str, str]]:
        return self.connector.user_sync_point.points

    async def sync_books(self) -> None:
        users = await self.connector.get_all_users()
        self.connector._user_emails = {int(u.source_user_id): u.email for u in users if u.email}
        if await self.connector._load_roles_for_access():
            await self.connector._sync_groups_full()

    def members(self, role: int) -> set[str] | None:
        """The role's stored members; None when the role isn't stored."""
        return self.directory.role_members.get(str(role))

    def book_grants(self, book: int) -> set[tuple[str, str]]:
        return self.directory.grants[f"book/{book}"]


def rebuild_owed(world: World) -> bool:
    """Whether the role sync point asks the next sync to read every role again."""
    return any(p.get("rebuildOwed") for p in world.role_checkpoint().values())


def emails(*user_ids: int) -> set[str]:
    return {f"u{uid}@example.com" for uid in user_ids}


# The owner (user 1) is an editor, so the editor role grant is what opens the
# book to them; no separate owner grant is stored.
DEFAULT_BOOK_GRANTS = {("WRITER", f"role {EDITOR}"), ("READER", f"role {VIEWER}")}


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

    assert world.book_grants(DEFAULT_BOOK) == {("WRITER", f"role {EDITOR}")}


async def test_a_complete_role_read_removes_a_deleted_role_from_book_grants(world: World) -> None:
    del world.api.roles[VIEWER]

    await world.sync_books()

    assert world.book_grants(DEFAULT_BOOK) == {("WRITER", f"role {EDITOR}")}


# ---- the role list and role details in the full role sync ----











async def test_a_first_sync_whose_role_list_is_refused_creates_the_roles_once_it_can_read_them() -> None:
    w = World()
    for uid in (1, 2):
        w.api.add_user(uid, EDITOR)
    w.api.fail[("/api/roles", None)] = 403

    await w.sync_roles()
    assert w.members(EDITOR) is None

    del w.api.fail[("/api/roles", None)]
    await w.sync_roles()

    assert w.members(EDITOR) == emails(1, 2)








async def test_an_interrupted_role_read_reports_the_full_sync_incomplete(world: World) -> None:
    """Roles it did not write are kept as stored, so the unlink after a full sync must not run."""
    world.api.fail[(f"/api/roles/{VIEWER}", None)] = 503

    await world.sync_roles()

    assert world.members(VIEWER) == emails(4, 5)
    assert not world.connector.full_sync_coverage().complete


async def test_record_groups_kept_on_an_interrupted_role_read_report_the_full_sync_incomplete(world: World) -> None:
    world.api.fail[("/api/roles", None)] = 500

    await world.sync_books()

    assert world.book_grants(DEFAULT_BOOK) == DEFAULT_BOOK_GRANTS
    assert not world.connector.full_sync_coverage().complete


async def test_a_complete_read_reports_the_full_sync_complete(world: World) -> None:
    await world.sync_roles()
    await world.sync_books()

    assert world.connector.full_sync_coverage().complete
    assert world.directory.kept_as_seen == set()


# ---- the user sync, which stores the users a role write attaches ----



@pytest.fixture
async def synced_users(world: World) -> AsyncIterator[World]:
    """The world after a full user sync, with the user checkpoint at a known value."""
    world.directory.require_user_rows = True
    await world.sync_users()
    for point in world.user_checkpoint().values():
        point["timestamp"] = "2026-01-01T00:00:00Z"
    yield world


def user_updated(world: World, user_id: int) -> None:
    world.api.audit.append({"type": "user_update", "detail": f"({user_id}) User {user_id}"})


async def test_a_user_update_bookstack_refuses_to_show_withdraws_their_roles(synced_users: World) -> None:
    world = synced_users
    user_updated(world, 5)
    world.api.fail[("/api/users/5", None)] = 403

    await world.sync_users()

    assert world.members(EDITOR) == emails(1, 2, 3, 4)
    assert world.members(VIEWER) == emails(4)


async def test_a_user_gone_from_bookstack_is_taken_out_of_their_stored_roles(synced_users: World) -> None:
    world = synced_users
    user_updated(world, 5)
    del world.api.users[5]

    await world.sync_users()

    assert world.members(EDITOR) == emails(1, 2, 3, 4)
    assert world.members(VIEWER) == emails(4)














async def test_a_complete_role_read_removes_a_role_bookstack_no_longer_has(world: World) -> None:
    """Writing roles replaces only the roles written, so a deleted role must be removed."""
    world.api.fail[("/api/roles", None)] = 403
    await world.sync_roles()
    del world.api.fail[("/api/roles", None)]
    del world.api.roles[VIEWER]  # deleted while the role list was refused

    await world.sync_roles()

    assert world.members(VIEWER) is None
    assert world.members(EDITOR) == emails(1, 2, 3, 4, 5)


