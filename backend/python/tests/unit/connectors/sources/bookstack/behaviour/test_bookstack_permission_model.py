"""Who can read a BookStack item in PipesHub must match who can view it in BookStack.

BookStack's model (docs: https://www.bookstackapp.com/docs/user/roles-and-permissions/,
source: app/Permissions/JointPermissionBuilder.php, EntityPermissionEvaluator.php and
PermissionApplicator::restrictEntityQuery):

- Each role has view permissions per content type (``book-view-all``, ``page-view-all``,
  ``page-view-own``...). They are the default for an item of that type.
- An item's own permissions override those defaults: a role row for one role, or
  "Everyone Else" for every role without a row. Books and chapters cascade them to
  their content unless the content sets its own.
- Most specific wins: a role row outranks Everyone Else, which outranks role
  permissions, across all of a user's roles. A role row that denies hides the item
  from a user whose other roles allow it only by default; a role row that allows it
  wins over a deny at the same level.
- "Own" permissions apply only to the item's owner, only while no item permission
  applies to that role, and never against a deny. Owning an item grants nothing more.
- The admin role always sees everything.

The test drives the connector against an in-memory BookStack and reads the grants it
writes the way the graph does: a role grant reaches the role's members, a user grant
that user, and an item that inherits also gets its parent's readers.
"""

import logging
from contextlib import asynccontextmanager
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.config.constants.arangodb import CollectionNames, Connectors
from app.connectors.sources.bookstack.connector import BookStackConnector
from app.models.entities import AppUser
from app.models.permission import EntityType, Permission
from app.sources.client.bookstack.bookstack import BookStackResponse

CONNECTOR_ID = "bs-perm"
ADMIN, EDITOR, VIEWER, BOOKY, PAGEY, OWN, NOVIEW = 1, 2, 3, 5, 6, 7, 8
_TYPES = ("bookshelf", "book", "chapter", "page")


def _views(*types: str) -> list[str]:
    return [f"{t}-view-all" for t in types]


class FakeBookStack:
    def __init__(self) -> None:
        self.roles: dict[int, dict[str, Any]] = {}
        self.users: dict[int, dict[str, Any]] = {}
        self.perms: dict[tuple[str, int], dict[str, Any]] = {}
        self.owners: dict[tuple[str, int], int] = {}
        # Users the listing reports in its total but never returns: a cut-off listing.
        self.unlisted_users = 0

    def role(self, role_id: int, name: str, permissions: list[str], system_name: str = "") -> None:
        self.roles[role_id] = {
            "id": role_id, "display_name": name, "system_name": system_name, "permissions": permissions,
        }

    def user(self, user_id: int, email: str, *role_ids: int) -> None:
        self.users[user_id] = {"id": user_id, "name": email.split("@")[0], "email": email, "roles": list(role_ids)}

    def restrict(
        self, content_type: str, content_id: int, *,
        rows: dict[int, bool] | None = None, everyone_else: bool | None = None,
    ) -> None:
        fallback: dict[str, Any] = {"inheriting": True, "view": None, "create": None, "update": None, "delete": None}
        if everyone_else is not None:
            fallback = {"inheriting": False, "view": everyone_else, "create": False, "update": False, "delete": False}
        self.perms[(content_type, content_id)] = {
            "role_permissions": [
                {"role_id": role_id, "view": view, "create": False, "update": False, "delete": False}
                for role_id, view in (rows or {}).items()
            ],
            "fallback_permissions": fallback,
        }

    def own(self, content_type: str, content_id: int, user_id: int) -> None:
        self.owners[(content_type, content_id)] = user_id

    def members(self, role_id: int) -> set[int]:
        return {uid for uid, u in self.users.items() if role_id in u["roles"]}

    def roles_details(self) -> dict[int, dict[str, Any]]:
        return {
            rid: {**role, "users": [{"id": uid, "name": self.users[uid]["name"]} for uid in sorted(self.members(rid))]}
            for rid, role in self.roles.items()
        }

    async def get_content_permissions(self, content_type: str, content_id: int) -> BookStackResponse:
        key = (content_type, int(content_id))
        body = dict(self.perms.get(key) or {
            "role_permissions": [],
            "fallback_permissions": {"inheriting": True, "view": None, "create": None, "update": None, "delete": None},
        })
        owner = self.owners.get(key)
        body["owner"] = {"id": owner, "name": self.users[owner]["name"]} if owner else None
        return BookStackResponse(success=True, data=body)

    async def get_user(self, user_id: int) -> BookStackResponse:
        user = self.users[int(user_id)]
        return BookStackResponse(success=True, data={
            "id": user["id"], "name": user["name"], "email": user["email"],
            "roles": [{"id": rid, "display_name": self.roles[rid]["display_name"]} for rid in user["roles"]],
        })

    async def list_users(self, count: int | None = None, offset: int | None = None, **_: Any) -> BookStackResponse:
        rows = [{"id": u["id"], "name": u["name"], "email": u["email"]} for u in self.users.values()]
        start = offset or 0
        total = len(rows) + self.unlisted_users
        return BookStackResponse(success=True, data={"data": rows[start:start + (count or 100)], "total": total})

    async def list_audit_log(self, **_: Any) -> BookStackResponse:
        return BookStackResponse(success=True, data={"data": [], "total": 0})


def _connector(fake: FakeBookStack) -> BookStackConnector:
    with patch("app.connectors.sources.bookstack.connector.BookStackApp"), \
         patch("app.connectors.sources.bookstack.connector.SyncPoint") as sync_point:
        sync_point.return_value = AsyncMock()
        processor = AsyncMock()
        processor.org_id = "org-1"
        processor.get_record_by_external_id = AsyncMock(return_value=None)
        provider = MagicMock()

        @asynccontextmanager
        async def _transaction():
            yield AsyncMock()

        provider.transaction = _transaction
        conn = BookStackConnector(
            logger=logging.getLogger("test_bookstack_permission_model"),
            data_entities_processor=processor,
            data_store_provider=provider,
            config_service=AsyncMock(),
            connector_id=CONNECTOR_ID,
            scope="team",
            created_by="creator",
        )
    conn.data_source = fake
    conn.bookstack_base_url = "https://bookstack.test"
    return conn


class Library:
    """The connector's view of one book: book 1, chapter 1 in it, page 1 in the chapter, page 2 in the book."""

    def __init__(self, fake: FakeBookStack) -> None:
        self.fake = fake
        self.conn = _connector(fake)
        self.readers: dict[str, set[str]] = {}

    def _granted(self, grants: list[Permission], inherit: bool, parent: str | None) -> set[str]:
        out = set(self.readers[parent]) if inherit and parent else set()
        for grant in grants or []:
            if grant.entity_type == EntityType.ROLE:
                out |= {self.fake.users[uid]["email"] for uid in self.fake.members(int(grant.external_id))}
            elif grant.entity_type == EntityType.USER:
                out.add(grant.email)
        return out

    async def sync(self) -> dict[str, set[str]]:
        users = await self.conn.get_all_users()
        roles = self.fake.roles_details()
        _, book_grants = await self.conn._create_record_group_with_permissions(
            {"id": 1, "name": "Book"}, "book", roles
        )
        self.readers["book/1"] = self._granted(book_grants, False, None)
        chapter, chapter_grants = await self.conn._create_record_group_with_permissions(
            {"id": 1, "name": "Chapter", "book_id": 1}, "chapter", roles, "book/1"
        )
        self.readers["chapter/1"] = self._granted(chapter_grants, chapter.inherit_permissions, "book/1")
        for page_id, chapter_id in ((1, 1), (2, None)):
            page = {
                "id": page_id, "name": f"Page {page_id}", "book_id": 1, "chapter_id": chapter_id,
                "slug": f"page-{page_id}", "book_slug": "book", "revision_count": 1,
                "created_at": "2026-01-01T00:00:00Z", "updated_at": "2026-01-01T00:00:00Z",
            }
            update = await self.conn._process_bookstack_page(page, roles, users)
            parent = f"chapter/{chapter_id}" if chapter_id else "book/1"
            self.readers[f"page/{page_id}"] = self._granted(
                update.new_permissions, update.record.inherit_permissions, parent
            )
        return self.readers


def _company() -> FakeBookStack:
    fake = FakeBookStack()
    everything = [p for t in _TYPES for p in (f"{t}-view-all", f"{t}-view-own")]
    fake.role(ADMIN, "Admin", everything, system_name="admin")
    fake.role(EDITOR, "Editor", everything + ["page-update-all"])
    fake.role(VIEWER, "Viewer", everything)
    fake.user(1, "admin@x.test", ADMIN)
    fake.user(2, "both@x.test", EDITOR, VIEWER)
    fake.user(3, "editor@x.test", EDITOR)
    fake.user(4, "viewer@x.test", VIEWER)
    return fake


ALL = {"admin@x.test", "both@x.test", "editor@x.test", "viewer@x.test"}


class TestRoleDefaultsArePerContentType:
    """BOOKSTACK-05: a chapter or page that sets nothing uses its own type's role permissions."""

    async def test_a_role_that_views_books_but_not_chapters_or_pages_sees_the_book_only(self) -> None:
        fake = _company()
        fake.role(BOOKY, "Books only", _views("book"))
        fake.user(5, "books@x.test", BOOKY)

        readers = await Library(fake).sync()

        assert "books@x.test" in readers["book/1"]
        assert "books@x.test" not in readers["chapter/1"]
        assert "books@x.test" not in readers["page/1"]
        assert "books@x.test" not in readers["page/2"]
        assert readers["page/1"] == ALL

    async def test_a_role_that_views_only_pages_sees_the_pages(self) -> None:
        fake = _company()
        fake.role(PAGEY, "Pages only", _views("page"))
        fake.user(6, "pages@x.test", PAGEY)

        readers = await Library(fake).sync()

        assert "pages@x.test" not in readers["book/1"]
        assert "pages@x.test" not in readers["chapter/1"]
        assert "pages@x.test" in readers["page/1"]
        assert "pages@x.test" in readers["page/2"]


class TestAnExplicitDenyOutranksAnotherRolesDefault:
    """BOOKSTACK-04: a role row that denies hides the item from a user whose other role allows it by default."""

    async def test_a_role_row_deny_on_the_book_hides_it_from_a_member_of_an_allowing_role(self) -> None:
        fake = _company()
        fake.restrict("book", 1, rows={VIEWER: False})

        readers = await Library(fake).sync()

        expected = {"admin@x.test", "editor@x.test"}
        assert readers["book/1"] == expected
        assert readers["chapter/1"] == expected
        assert readers["page/1"] == expected
        assert readers["page/2"] == expected

    async def test_a_role_row_deny_on_the_chapter_applies_to_the_chapter_and_its_pages(self) -> None:
        fake = _company()
        fake.restrict("chapter", 1, rows={VIEWER: False})

        readers = await Library(fake).sync()

        assert readers["book/1"] == ALL
        assert readers["chapter/1"] == {"admin@x.test", "editor@x.test"}
        assert readers["page/1"] == {"admin@x.test", "editor@x.test"}
        assert readers["page/2"] == ALL

    async def test_a_role_row_deny_outranks_an_everyone_else_allow(self) -> None:
        fake = _company()
        fake.restrict("page", 2, rows={VIEWER: False}, everyone_else=True)

        readers = await Library(fake).sync()

        assert readers["page/2"] == {"admin@x.test", "editor@x.test"}

    async def test_a_role_row_allow_wins_over_a_role_row_deny_at_the_same_level(self) -> None:
        fake = _company()
        fake.restrict("book", 1, rows={VIEWER: False, EDITOR: True})

        readers = await Library(fake).sync()

        assert readers["book/1"] == {"admin@x.test", "both@x.test", "editor@x.test"}
        assert readers["page/1"] == {"admin@x.test", "both@x.test", "editor@x.test"}


class TestAMembershipChangeReEvaluatesUserGrants:
    """BOOKSTACK-04: grants to single users follow role memberships, so a membership change re-reads every item."""

    @staticmethod
    def _run(library: Library, stored: dict[str, Any], *, memberships_change: bool) -> AsyncMock:
        conn = library.conn

        async def read(key: str) -> dict[str, Any]:
            return stored.get(key, {})

        async def update(key: str, value: dict[str, Any]) -> None:
            stored[key] = value

        async def sync_users(*_: Any, **__: Any) -> None:
            conn._bookstack_memberships_changed = memberships_change

        async def sync_groups(*_: Any, **__: Any) -> None:
            await library.sync()

        conn.record_sync_point.read_sync_point = read
        conn.record_sync_point.update_sync_point = update
        conn._sync_users = sync_users
        conn._sync_user_roles = AsyncMock()
        conn._sync_record_groups = AsyncMock(side_effect=sync_groups)
        conn._sync_records = AsyncMock()
        return conn._sync_record_groups

    async def _sync(self, library: Library, stored: dict[str, Any], *, memberships_change: bool) -> AsyncMock:
        groups = self._run(library, stored, memberships_change=memberships_change)
        with patch(
            "app.connectors.sources.bookstack.connector.load_connector_filters",
            AsyncMock(return_value=(MagicMock(), MagicMock())),
        ):
            await library.conn.run_sync()
        return groups

    async def test_a_membership_change_re_reads_every_item_once_user_grants_exist(self) -> None:
        fake = _company()
        fake.restrict("book", 1, rows={VIEWER: False})
        library = Library(fake)
        stored: dict[str, Any] = {}

        first = await self._sync(library, stored, memberships_change=False)
        second = await self._sync(library, stored, memberships_change=True)

        first.assert_awaited_once_with(full_sync=False)
        second.assert_awaited_once_with(full_sync=True)
        library.conn._sync_records.assert_awaited_once_with(full_sync=True)

    async def test_without_user_grants_a_membership_change_stays_incremental(self) -> None:
        library = Library(_company())
        stored: dict[str, Any] = {}

        await self._sync(library, stored, memberships_change=False)
        second = await self._sync(library, stored, memberships_change=True)

        second.assert_awaited_once_with(full_sync=False)


class TestOwnershipGivesNoAccessOfItsOwn:
    """BOOKSTACK-03: the owner reads an item only through their roles' permissions."""

    async def test_an_owner_whose_roles_cannot_view_does_not_see_the_item(self) -> None:
        fake = _company()
        fake.role(NOVIEW, "No view", ["page-update-all"])
        fake.user(7, "owner@x.test", NOVIEW)
        fake.own("book", 1, 7)
        fake.own("chapter", 1, 7)
        fake.own("page", 1, 7)

        readers = await Library(fake).sync()

        assert "owner@x.test" not in readers["book/1"]
        assert "owner@x.test" not in readers["chapter/1"]
        assert "owner@x.test" not in readers["page/1"]

    async def test_view_own_lets_the_owner_see_their_own_page_only(self) -> None:
        fake = _company()
        fake.role(OWN, "Own pages", ["page-view-own"])
        fake.user(7, "owner@x.test", OWN)
        fake.own("page", 1, 7)

        readers = await Library(fake).sync()

        assert "owner@x.test" in readers["page/1"]
        assert "owner@x.test" not in readers["page/2"]
        assert "owner@x.test" not in readers["book/1"]

    async def test_everyone_else_on_the_item_overrides_view_own(self) -> None:
        fake = _company()
        fake.role(OWN, "Own pages", ["page-view-own"])
        fake.user(7, "owner@x.test", OWN)
        fake.own("page", 1, 7)
        fake.restrict("page", 1, everyone_else=False)

        readers = await Library(fake).sync()

        assert readers["page/1"] == {"admin@x.test"}

    async def test_a_role_row_deny_overrides_view_own(self) -> None:
        fake = _company()
        fake.role(OWN, "Own pages", ["page-view-own"])
        fake.user(7, "owner@x.test", OWN)
        fake.own("page", 1, 7)
        fake.restrict("page", 1, rows={OWN: False})

        readers = await Library(fake).sync()

        assert "owner@x.test" not in readers["page/1"]


class TestUsersGoneFromBookStack:
    """BOOKSTACK-07: a user deleted in BookStack loses the BookStack roles that granted them content."""

    @staticmethod
    def _stored(conn: BookStackConnector, *emails: str) -> None:
        conn.data_entities_processor.get_all_app_users = AsyncMock(return_value=[
            AppUser(app_name=Connectors.BOOKSTACK, connector_id=CONNECTOR_ID, source_user_id=str(i), email=e, full_name=e)
            for i, e in enumerate(emails, start=100)
        ])
        conn.data_entities_processor.get_user_by_email = AsyncMock(
            side_effect=lambda email: MagicMock(id=f"pipeshub-{email}")
        )
        conn.data_entities_processor.delete_edges_between_collections = AsyncMock()
        conn.data_entities_processor.remove_app_users_absent_from_source = AsyncMock()
        conn.user_sync_point.read_sync_point = AsyncMock(return_value={"timestamp": "2026-01-01T00:00:00Z"})

    async def test_an_incremental_sync_removes_the_roles_of_a_user_bookstack_no_longer_lists(self) -> None:
        fake = _company()
        conn = _connector(fake)
        self._stored(conn, "editor@x.test", "gone@x.test")

        await conn._sync_users()

        conn.data_entities_processor.delete_edges_between_collections.assert_awaited_once_with(
            "pipeshub-gone@x.test", CollectionNames.USERS.value, CollectionNames.PERMISSION.value,
            CollectionNames.ROLES.value, to_connector_id=CONNECTOR_ID,
        )
        listed = conn.data_entities_processor.remove_app_users_absent_from_source.await_args.args[1]
        assert {u.email for u in listed} == ALL
        assert conn._bookstack_memberships_changed is True

    async def test_a_cut_off_user_listing_removes_nobody(self) -> None:
        fake = _company()
        fake.unlisted_users = 5
        conn = _connector(fake)
        self._stored(conn, "editor@x.test", "gone@x.test")

        await conn._sync_users()

        conn.data_entities_processor.delete_edges_between_collections.assert_not_awaited()
        conn.data_entities_processor.remove_app_users_absent_from_source.assert_not_awaited()


class TestContainersLinkToBookStack:
    """BOOKSTACK-09: shelves, books and chapters carry their BookStack URL like pages do."""

    async def test_shelves_books_and_chapters_have_a_web_url(self) -> None:
        conn = _connector(_company())
        roles: dict[int, dict[str, Any]] = {}
        shelf, _ = await conn._create_record_group_with_permissions(
            {"id": 1, "name": "Shelf", "slug": "team"}, "bookshelf", roles
        )
        book, _ = await conn._create_record_group_with_permissions(
            {"id": 1, "name": "Book", "slug": "handbook"}, "book", roles
        )
        chapter, _ = await conn._create_record_group_with_permissions(
            {"id": 1, "name": "Chapter", "slug": "intro", "book_id": 1, "book_slug": "handbook"},
            "chapter", roles, "book/1",
        )
        assert shelf.web_url == "https://bookstack.test/shelves/team"
        assert book.web_url == "https://bookstack.test/books/handbook"
        assert chapter.web_url == "https://bookstack.test/books/handbook/chapter/intro"

    async def test_a_chapter_without_its_books_slug_has_no_web_url(self) -> None:
        conn = _connector(_company())
        chapter, _ = await conn._create_record_group_with_permissions(
            {"id": 1, "name": "Chapter", "slug": "intro", "book_id": 1}, "chapter", {}, "book/1",
        )
        assert chapter.web_url is None
