"""Decisions for Drive, BookStack, Dropbox, OneDrive, and SharePoint item access."""

from app.connectors.sources.bookstack.access import bookstack_grants
from app.connectors.sources.dropbox.access import (
    fallback_for_uncovered_user,
    member_is_direct,
)
from app.connectors.sources.google.drive.team.drive_access import (
    grants_to_store,
    item_inherits_parent,
    permission_is_direct,
)
from app.connectors.sources.microsoft.onedrive.connector import grants_match
from app.connectors.sources.microsoft.sharepoint_online.connector import (
    unique_file_grants,
)
from app.models.permission import EntityType, Permission, PermissionType


def _grant(email: str, role: PermissionType = PermissionType.READ) -> Permission:
    return Permission(email=email, external_id=email, type=role, entity_type=EntityType.USER)


class TestDriveAccess:
    def test_shared_drive_direct_grant_is_kept(self) -> None:
        assert permission_is_direct({
            "permissionDetails": [
                {"permissionType": "member", "inherited": True, "role": "commenter"},
                {"permissionType": "file", "inherited": False, "role": "writer"},
            ]
        }) is True

    def test_shared_drive_inherited_grant_is_not_stored(self) -> None:
        assert permission_is_direct({
            "permissionDetails": [{"permissionType": "member", "inherited": True, "role": "reader"}]
        }) is False

    def test_inherited_file_share_is_not_direct(self) -> None:
        assert permission_is_direct({
            "permissionDetails": [{"permissionType": "file", "inherited": True, "role": "reader"}]
        }) is False

    def test_limited_folder_drops_metadata_only_readers(self) -> None:
        """A limited folder lists the parent's readers as inherited; they see only metadata."""
        acl = [
            ("test@x.app", [{"permissionType": "file", "inherited": True, "role": "reader"}]),
            ("abhishek@x.app", [{"permissionType": "file", "inherited": True, "role": "reader"}]),
            ("owner@x.app", [{"permissionType": "file", "inherited": False, "role": "owner"}]),
        ]
        everyone = [email for email, _ in acl]
        direct = [email for email, details in acl if permission_is_direct({"permissionDetails": details}) is not False]
        assert grants_to_store(
            everyone, direct, saw_permission_details=True, inherited_permissions_disabled=True, is_drive=False,
        ) == ["owner@x.app"]
        assert item_inherits_parent(
            saw_permission_details=True, inherited_permissions_disabled=True, is_fallback=False
        ) is False

    def test_my_drive_has_no_inherited_flag(self) -> None:
        assert permission_is_direct({"role": "owner"}) is None

    def test_limited_access_does_not_inherit(self) -> None:
        assert item_inherits_parent(
            saw_permission_details=True, inherited_permissions_disabled=True, is_fallback=False
        ) is False

    def test_shared_drive_item_inherits_its_parent(self) -> None:
        assert item_inherits_parent(
            saw_permission_details=True, inherited_permissions_disabled=False, is_fallback=False
        ) is True

    def test_my_drive_flattened_list_does_not_also_inherit(self) -> None:
        assert item_inherits_parent(
            saw_permission_details=False, inherited_permissions_disabled=False, is_fallback=False
        ) is False

    def test_limited_access_stores_only_direct_grants(self) -> None:
        everyone = ["inherited-user"]
        direct = ["direct-user"]
        assert grants_to_store(
            everyone, direct, saw_permission_details=True, inherited_permissions_disabled=True, is_drive=False,
        ) == direct

    def test_fallback_does_not_inherit(self) -> None:
        assert item_inherits_parent(
            saw_permission_details=True, inherited_permissions_disabled=False, is_fallback=True
        ) is False


_INHERIT = {"role_permissions": [], "fallback_permissions": {"inheriting": True}}


def _roles_of(grants: list[Permission]) -> dict[int, PermissionType]:
    return {int(p.external_id): p.type for p in grants if p.entity_type == EntityType.ROLE}


def _users_of(grants: list[Permission]) -> dict[str, PermissionType]:
    return {p.email: p.type for p in grants if p.entity_type == EntityType.USER}


# BookStack user 1 is in both roles of a test, user 2 only in the second.
_EMAILS = {1: "both@x.test", 2: "second@x.test"}


def _in(*user_ids: int) -> list[dict]:
    return [{"id": user_id} for user_id in user_ids]


def _row(role_id: int, view: bool, update: bool = False) -> dict:
    return {"role_id": role_id, "view": view, "create": False, "update": update, "delete": False}


class TestBookStackAccess:
    def test_inheriting_chapter_without_rows_follows_the_book(self) -> None:
        roles = {2: {"permissions": ["book-view-all", "chapter-view-all"]}}
        book = bookstack_grants(None, _INHERIT, roles, "book", None)
        chapter = bookstack_grants(None, _INHERIT, roles, "chapter", book)
        assert chapter.inherit is True
        assert list(chapter) == []
        assert _roles_of(book) == {2: PermissionType.READ}

    def test_role_deny_on_an_inheriting_page_replaces_the_parent(self) -> None:
        roles = {
            2: {"permissions": ["book-view-all", "page-view-all"], "users": _in(1)},
            3: {
                "permissions": ["book-view-all", "book-update-all", "page-view-all", "page-update-all"],
                "users": _in(1, 2),
            },
        }
        book = bookstack_grants(None, _INHERIT, roles, "book", None)
        page = bookstack_grants(
            None,
            {"role_permissions": [_row(2, False)], "fallback_permissions": {"inheriting": True}},
            roles,
            "page",
            book,
            user_emails=_EMAILS,
        )
        assert page.inherit is False
        # Role 3 allows by default only: the deny of role 2 outranks it for user 1.
        assert _roles_of(page) == {}
        assert _users_of(page) == {"second@x.test": PermissionType.WRITE}
        assert page.user_level is True

    def test_everyone_else_is_stored_when_inheritance_is_off(self) -> None:
        roles = {
            2: {"permissions": ["book-view-all"], "users": _in(1)},
            4: {"permissions": ["book-view-all"], "users": _in(1, 2)},
        }
        book = bookstack_grants(None, _INHERIT, roles, "book", None)
        page = bookstack_grants(
            None,
            {
                "role_permissions": [_row(2, False, update=True)],
                "fallback_permissions": {
                    "inheriting": False, "view": True, "create": False, "update": False, "delete": False
                },
            },
            roles,
            "page",
            book,
            user_emails=_EMAILS,
        )
        assert page.inherit is False
        assert _users_of(page) == {"second@x.test": PermissionType.READ}

    def test_inheriting_book_uses_system_roles(self) -> None:
        grants = bookstack_grants(None, _INHERIT, {1: {"permissions": ["book-view-all"]}}, "book", None)
        assert grants.inherit is False
        assert _roles_of(grants) == {1: PermissionType.READ}

    def test_an_inheriting_book_keeps_view_all_for_roles_without_a_row(self) -> None:
        grants = bookstack_grants(
            None,
            {"role_permissions": [_row(1, False)], "fallback_permissions": {"inheriting": True}},
            {
                1: {"permissions": ["book-view-all"], "users": _in(1)},
                2: {"permissions": ["book-view-all"], "users": _in(1, 2)},
            },
            "book",
            None,
            user_emails=_EMAILS,
        )
        assert grants.inherit is False
        assert _users_of(grants) == {"second@x.test": PermissionType.READ}

    def test_everyone_else_no_view_hides_a_role_with_book_view_all(self) -> None:
        grants = bookstack_grants(
            None,
            {"role_permissions": [], "fallback_permissions": {"inheriting": False, "view": False}},
            {
                1: {"permissions": ["book-view-all"]},
                2: {"permissions": ["restrictions-manage-all"]},
                3: {"system_name": "admin", "permissions": []},
            },
            "book",
            None,
        )
        assert grants.inherit is False
        assert _roles_of(grants) == {3: PermissionType.WRITE}

    def test_a_shelf_that_inherits_defaults_is_granted_to_the_roles_that_view_shelves(self) -> None:
        """BOOKSTACK-01: a shelf has no parent either, so it was granted to nobody."""
        grants = bookstack_grants(
            None,
            _INHERIT,
            {
                1: {"permissions": ["bookshelf-view-all", "bookshelf-update-all"]},
                2: {"permissions": ["bookshelf-view-all"]},
                3: {"permissions": ["book-view-all"]},
            },
            "bookshelf",
            None,
        )
        assert grants.inherit is False
        assert {(p.external_id, p.type) for p in grants} == {
            ("1", PermissionType.WRITE),
            ("2", PermissionType.READ),
        }

    def test_a_shelf_role_row_overrides_that_role_only(self) -> None:
        grants = bookstack_grants(
            None,
            {"role_permissions": [_row(1, False)], "fallback_permissions": {"inheriting": True}},
            {
                1: {"permissions": ["bookshelf-view-all"], "users": _in(1)},
                2: {"permissions": ["bookshelf-view-all"], "users": _in(1, 2)},
            },
            "bookshelf",
            None,
            user_emails=_EMAILS,
        )
        assert grants.inherit is False
        assert _users_of(grants) == {"second@x.test": PermissionType.READ}

    def test_without_role_members_a_deny_withholds_the_default_access(self) -> None:
        grants = bookstack_grants(
            None,
            {"role_permissions": [_row(1, False)], "fallback_permissions": {"inheriting": True}},
            {1: {"permissions": ["book-view-all"]}, 2: {"permissions": ["book-view-all"]}},
            "book",
            None,
        )
        assert list(grants) == []
        assert grants.warnings

    def test_an_explicit_allow_covers_a_member_of_a_denying_role(self) -> None:
        grants = bookstack_grants(
            None,
            {"role_permissions": [_row(1, False), _row(3, True)], "fallback_permissions": {"inheriting": True}},
            {
                1: {"permissions": ["book-view-all"], "users": _in(1)},
                2: {"permissions": ["book-view-all"], "users": _in(1, 2)},
                3: {"permissions": [], "users": _in(1)},
            },
            "book",
            None,
            user_emails=_EMAILS,
        )
        assert _roles_of(grants) == {3: PermissionType.READ}
        assert _users_of(grants) == {"second@x.test": PermissionType.READ}

    def test_admin_still_sees_a_chapter_when_everyone_else_cannot(self) -> None:
        roles = {3: {"system_name": "admin"}}
        book = bookstack_grants(None, _INHERIT, roles, "book", None)
        chapter = bookstack_grants(
            None,
            {"role_permissions": [], "fallback_permissions": {"inheriting": False, "view": False}},
            roles,
            "chapter",
            book,
        )
        # Admin keeps the chapter through the book's grant: the chapter's own matches it.
        assert chapter.inherit is True
        assert _roles_of(book) == {3: PermissionType.WRITE}

    def test_a_chapter_takes_the_chapter_view_permission_not_the_books(self) -> None:
        """BOOKSTACK-05: role permissions are per content type."""
        roles = {1: {"permissions": ["book-view-all"]}, 2: {"permissions": ["book-view-all", "chapter-view-all"]}}
        book = bookstack_grants(None, _INHERIT, roles, "book", None)
        chapter = bookstack_grants(None, _INHERIT, roles, "chapter", book)
        assert chapter.inherit is False
        assert _roles_of(chapter) == {2: PermissionType.READ}

    def test_a_book_row_cascades_to_a_page_that_sets_nothing(self) -> None:
        roles = {1: {"permissions": []}, 2: {"permissions": ["book-view-all", "page-view-all"]}}
        book = bookstack_grants(
            None,
            {"role_permissions": [_row(1, True)], "fallback_permissions": {"inheriting": True}},
            roles,
            "book",
            None,
        )
        page = bookstack_grants(None, _INHERIT, roles, "page", book)
        assert page.inherit is True
        assert _roles_of(book) == {1: PermissionType.READ, 2: PermissionType.READ}

    def test_everyone_else_on_the_page_stops_a_book_row_from_reaching_it(self) -> None:
        roles = {1: {"permissions": []}, 2: {"permissions": ["book-view-all", "page-view-all"]}}
        book = bookstack_grants(
            None,
            {"role_permissions": [_row(1, True)], "fallback_permissions": {"inheriting": True}},
            roles,
            "book",
            None,
        )
        page = bookstack_grants(
            None,
            {"role_permissions": [], "fallback_permissions": {"inheriting": False, "view": False}},
            roles,
            "page",
            book,
        )
        assert _roles_of(page) == {}

    def test_a_page_whose_parent_could_not_be_read_inherits_unless_a_row_denies(self) -> None:
        roles = {1: {"permissions": ["page-view-all"]}, 9: {"system_name": "admin"}}
        allowed = bookstack_grants(
            None,
            {"role_permissions": [_row(1, True)], "fallback_permissions": {"inheriting": True}},
            roles, "page", None,
        )
        denied = bookstack_grants(
            None,
            {"role_permissions": [_row(1, False)], "fallback_permissions": {"inheriting": True}},
            roles, "page", None,
        )
        assert allowed.inherit is True and _roles_of(allowed) == {1: PermissionType.READ}
        assert denied.inherit is False and _roles_of(denied) == {9: PermissionType.WRITE}


    def test_the_owners_roles_come_from_the_role_members_when_not_given(self) -> None:
        """BOOKSTACK-03: ownership counts through view-own only."""
        owner = Permission(external_id="2", email="second@x.test", type=PermissionType.OWNER, entity_type=EntityType.USER)
        roles = {1: {"permissions": ["page-view-own"], "users": _in(2)}, 2: {"permissions": [], "users": _in(1)}}
        book = bookstack_grants(None, _INHERIT, roles, "book", None)
        own = bookstack_grants(owner, _INHERIT, roles, "page", book)
        roles[1]["permissions"] = []
        not_own = bookstack_grants(owner, _INHERIT, roles, "page", book)
        assert _users_of(own) == {"second@x.test": PermissionType.OWNER}
        assert own.user_level is True
        assert list(not_own) == [] and not_own.inherit is True

class TestDropboxAccess:
    def test_inherited_file_member_is_not_direct(self) -> None:
        assert member_is_direct(True) is False

    def test_direct_and_unknown_members_are_kept(self) -> None:
        assert member_is_direct(False) is True
        assert member_is_direct(None) is True

    def test_inherited_user_is_not_granted_again(self) -> None:
        assert fallback_for_uncovered_user(grants_empty=True, user_is_covered=True) is None

    def test_private_item_gets_the_owner(self) -> None:
        assert fallback_for_uncovered_user(grants_empty=True, user_is_covered=False) == "owner"

    def test_missing_user_on_a_member_list_gets_write(self) -> None:
        assert fallback_for_uncovered_user(grants_empty=False, user_is_covered=False) == "write"


class TestOneDriveAndSharePoint:
    def test_matching_permission_sets_inherit(self) -> None:
        assert grants_match(
            [_grant("a@x.com", PermissionType.WRITE)],
            [_grant("a@x.com", PermissionType.WRITE)],
        ) is True

    def test_an_extra_person_does_not_match(self) -> None:
        assert grants_match(
            [_grant("a@x.com"), _grant("b@x.com")],
            [_grant("a@x.com")],
        ) is False

    def test_unique_file_keeps_role_assignments_and_org_links(self) -> None:
        role = _grant("legal@x.com", PermissionType.WRITE)
        link = Permission(external_id="anyone_in_org", type=PermissionType.READ, entity_type=EntityType.ORG)
        graph_user = _grant("other@x.com")
        link_user = _grant("link@x.com")
        link_user.link_grant = True
        grants = unique_file_grants([role], [graph_user, link, link_user])
        identities = {(grant.entity_type, grant.email or grant.external_id) for grant in grants}
        assert identities == {
            (EntityType.USER, "legal@x.com"),
            (EntityType.ORG, "anyone_in_org"),
            (EntityType.USER, "link@x.com"),
        }
