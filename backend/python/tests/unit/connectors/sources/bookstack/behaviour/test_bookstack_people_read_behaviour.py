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

from app.connectors.sources.bookstack.connector import BookStackConnector
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
        """The stored roles; like the graph stores, a failed read answers [] unless raise_on_error."""
        assert collection == "roles" and filters == {"connectorId": CONNECTOR_ID}
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
        self.connector._sync_records = AsyncMock(return_value=None)

    def full_resync(self) -> None:
        """What a full resync does first: clear the sync points."""
        self.connector.app_role_sync_point.points.clear()
        self.connector.record_sync_point.points.clear()

    async def sync_roles(self) -> None:
        await self.connector._sync_user_roles()

    async def sync_users(self) -> None:
        await self.connector._sync_users()

    def role_checkpoint(self) -> dict[str, dict[str, str]]:
        return self.connector.app_role_sync_point.points

    def user_checkpoint(self) -> dict[str, dict[str, str]]:
        return self.connector.user_sync_point.points

    async def sync_books(self) -> None:
        await self.connector._sync_record_groups()

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


# ---- the role list and role details in the full role sync ----

async def test_a_failed_second_role_page_is_read_again_on_the_next_sync(world: World) -> None:
    world.api.roles[AUDITOR] = {"display_name": "Auditor", "permissions": [], "users": [1]}
    world.api.fail_once[("/api/roles", 2)] = 429  # the page holding Auditor

    await world.sync_roles()

    assert world.role_checkpoint() == {}
    assert world.members(AUDITOR) is None
    assert world.members(EDITOR) == emails(1, 2, 3, 4, 5)  # read whole, so written whole

    await world.sync_roles()

    assert world.members(AUDITOR) == emails(1)


async def test_a_failed_role_details_read_in_the_role_sync_is_read_again_on_the_next_sync(world: World) -> None:
    world.api.roles[AUDITOR] = {"display_name": "Auditor", "permissions": [], "users": [1]}
    world.api.fail_once[(f"/api/roles/{AUDITOR}", None)] = 503

    await world.sync_roles()
    assert world.role_checkpoint() == {}
    await world.sync_roles()

    assert world.members(AUDITOR) == emails(1)


@pytest.mark.parametrize("status", [401, 403])
async def test_a_refused_role_keeps_its_members_and_the_role_sync_finishes(world: World, status: int) -> None:
    """A refusal repeats every sync, so it does not hold the checkpoint. The role keeps
    its stored members, noted as seen so the unlink after a full sync leaves them."""
    world.api.fail[(f"/api/roles/{VIEWER}", None)] = status
    world.api.roles[VIEWER]["users"].remove(5)

    await world.sync_roles()

    assert world.role_checkpoint() != {}
    assert world.members(VIEWER) == emails(4, 5)
    assert world.members(EDITOR) == emails(1, 2, 3, 4, 5)  # read whole, so written whole
    assert world.directory.kept_as_seen == {str(VIEWER)}
    assert world.connector.full_sync_coverage().complete

    # Not frozen: once BookStack shows the role again, the next sync reads it whole.
    del world.api.fail[(f"/api/roles/{VIEWER}", None)]
    await world.sync_roles()

    assert world.members(VIEWER) == emails(4)
    assert not rebuild_owed(world)


async def test_a_lasting_refusal_does_not_hold_the_role_checkpoint_on_every_sync(world: World) -> None:
    """Held, every sync would replay the audit log since then, role updates included."""
    await world.sync_roles()
    for point in world.role_checkpoint().values():
        point["timestamp"] = "2026-01-01T00:00:00Z"
    world.api.audit.append({"type": "role_delete", "detail": "(9) Old role"})
    world.api.fail[(f"/api/roles/{VIEWER}", None)] = 403

    await world.sync_roles()

    assert [p["timestamp"] for p in world.role_checkpoint().values()] != ["2026-01-01T00:00:00Z"]
    assert world.members(VIEWER) == emails(4, 5)


async def test_a_refused_role_list_keeps_stored_roles_and_reports_the_full_sync_incomplete(world: World) -> None:
    """A refused page names no roles, so they can't be noted as seen: nothing is unlinked."""
    world.api.fail[("/api/roles", None)] = 403
    world.api.roles[VIEWER]["users"].remove(5)

    await world.sync_roles()

    assert world.role_checkpoint() != {}
    assert world.members(VIEWER) == emails(4, 5)
    assert not world.connector.full_sync_coverage().complete

    del world.api.fail[("/api/roles", None)]
    await world.sync_roles()

    assert world.members(VIEWER) == emails(4)
    assert not rebuild_owed(world)


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


async def test_a_lasting_refusal_reads_every_role_on_each_sync_without_replaying_the_audit_log(world: World) -> None:
    world.api.fail[(f"/api/roles/{VIEWER}", None)] = 403
    await world.sync_roles()
    first = [p["timestamp"] for p in world.role_checkpoint().values()]
    for point in world.role_checkpoint().values():
        point["timestamp"] = "2026-01-01T00:00:00Z"

    await world.sync_roles()

    assert [p["timestamp"] for p in world.role_checkpoint().values()] != ["2026-01-01T00:00:00Z"]
    assert first != []
    assert rebuild_owed(world)
    assert world.members(VIEWER) == emails(4, 5)


async def test_a_role_update_bookstack_refuses_keeps_the_role(world: World) -> None:
    """The same as the full role sync: a refused role keeps its members and is read again."""
    await world.sync_roles()
    world.api.fail[(f"/api/roles/{VIEWER}", None)] = 403
    world.api.roles[VIEWER]["users"].remove(5)
    world.connector._sync_records = AsyncMock(return_value=None)

    done = await world.connector._handle_role_update_event(VIEWER, {})

    assert done is True
    assert world.members(VIEWER) == emails(4, 5)
    assert world.directory.kept_as_seen == {str(VIEWER)}

    world.api.audit.append({"type": "role_update", "detail": f"({VIEWER}) Viewer"})
    await world.sync_roles()
    assert rebuild_owed(world)

    del world.api.fail[(f"/api/roles/{VIEWER}", None)]
    await world.sync_roles()

    assert world.members(VIEWER) == emails(4)


async def test_a_refused_role_whose_stored_members_cannot_be_read_is_read_again(world: World) -> None:
    world.api.fail[(f"/api/roles/{VIEWER}", None)] = 403
    world.directory.fail_stored_read = True

    await world.sync_roles()

    assert world.role_checkpoint() == {}
    assert world.members(VIEWER) == emails(4, 5)
    assert not world.connector.full_sync_coverage().complete


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

async def test_a_first_sync_whose_user_list_fails_writes_roles_only_once_every_user_is_stored() -> None:
    w = World()
    w.directory.require_user_rows = True
    for uid in (1, 2, 3, 4, 5):
        w.api.add_user(uid, EDITOR)
    w.api.fail_once[("/api/users", 2)] = 429  # hit by the user sync's listing only

    await w.sync_users()
    await w.sync_roles()

    assert w.user_checkpoint() == {}
    assert w.role_checkpoint() == {}

    await w.sync_users()
    await w.sync_roles()

    assert w.members(EDITOR) == emails(1, 2, 3, 4, 5)


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


async def test_a_user_update_whose_role_write_fails_is_applied_again(synced_users: World) -> None:
    world = synced_users
    user_updated(world, 4)
    world.directory.fail_role_write = True

    await world.sync_users()

    assert [p["timestamp"] for p in world.user_checkpoint().values()] == ["2026-01-01T00:00:00Z"]


@pytest.mark.parametrize("status", [403, 404])
async def test_a_user_withdrawal_whose_user_lookup_fails_is_applied_again(synced_users: World, status: int) -> None:
    """A failed lookup is not "never stored": the stored roles stay, and so does the checkpoint."""
    world = synced_users
    user_updated(world, 5)
    world.api.fail[("/api/users/5", None)] = status
    world.directory.fail_user_lookup = True

    await world.sync_users()

    assert [p["timestamp"] for p in world.user_checkpoint().values()] == ["2026-01-01T00:00:00Z"]
    assert world.members(EDITOR) == emails(1, 2, 3, 4, 5)


async def test_a_user_withdrawal_whose_edge_delete_fails_is_applied_again(synced_users: World) -> None:
    world = synced_users
    user_updated(world, 5)
    world.api.fail[("/api/users/5", None)] = 403
    world.directory.fail_edge_delete = True

    await world.sync_users()

    assert [p["timestamp"] for p in world.user_checkpoint().values()] == ["2026-01-01T00:00:00Z"]


async def test_a_user_update_whose_user_lookup_fails_is_applied_again(synced_users: World) -> None:
    world = synced_users
    user_updated(world, 4)
    world.directory.fail_user_lookup = True

    await world.sync_users()

    assert [p["timestamp"] for p in world.user_checkpoint().values()] == ["2026-01-01T00:00:00Z"]


async def test_a_user_update_for_a_user_never_stored_still_counts_as_applied(synced_users: World) -> None:
    """A lookup that answers "no such user" is a definite answer, so the checkpoint moves on."""
    world = synced_users
    user_updated(world, 4)
    world.directory.user_rows.discard("u4@example.com")

    await world.sync_users()

    assert [p["timestamp"] for p in world.user_checkpoint().values()] != ["2026-01-01T00:00:00Z"]


async def test_the_rebuild_owed_after_a_refusal_stops_once_every_role_is_read(world: World) -> None:
    """The stores merge sync points, so the flag must be written back as False."""
    world.api.fail[(f"/api/roles/{VIEWER}", None)] = 403
    await world.sync_roles()
    assert rebuild_owed(world)

    del world.api.fail[(f"/api/roles/{VIEWER}", None)]
    await world.sync_roles()
    assert not rebuild_owed(world)

    full_read = AsyncMock(wraps=world.connector._sync_user_roles_full)
    world.connector._sync_user_roles_full = full_read
    await world.sync_roles()
    full_read.assert_not_awaited()


async def test_a_complete_role_read_removes_a_role_bookstack_no_longer_has(world: World) -> None:
    """Writing roles replaces only the roles written, so a deleted role must be removed."""
    world.api.fail[("/api/roles", None)] = 403
    await world.sync_roles()
    del world.api.fail[("/api/roles", None)]
    del world.api.roles[VIEWER]  # deleted while the role list was refused

    await world.sync_roles()

    assert world.members(VIEWER) is None
    assert world.members(EDITOR) == emails(1, 2, 3, 4, 5)


async def test_a_complete_role_read_whose_stored_roles_cannot_be_listed_is_read_again(world: World) -> None:
    del world.api.roles[VIEWER]
    world.directory.fail_role_list_read = True

    await world.sync_roles()

    assert world.role_checkpoint() == {}
    assert world.members(VIEWER) == emails(4, 5)
    assert not world.connector.full_sync_coverage().complete
