"""Decisions for Drive, BookStack, Dropbox, OneDrive, and SharePoint item access."""

from app.connectors.sources.bookstack.access import resolve_bookstack_roles
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


class TestBookStackAccess:
    def test_inheriting_chapter_without_rows_follows_the_book(self) -> None:
        parent = {2: PermissionType.READ}
        effective, inherit = resolve_bookstack_roles(
            {"role_permissions": [], "fallback_permissions": {"inheriting": True}},
            {},
            "chapter",
            parent,
        )
        assert inherit is True
        assert effective == parent

    def test_role_deny_on_an_inheriting_page_replaces_the_parent(self) -> None:
        effective, inherit = resolve_bookstack_roles(
            {
                "role_permissions": [
                    {"role_id": 2, "view": False, "create": False, "update": False, "delete": False}
                ],
                "fallback_permissions": {"inheriting": True},
            },
            {2: {}},
            "page",
            {2: PermissionType.READ, 3: PermissionType.WRITE},
        )
        assert inherit is False
        assert effective == {3: PermissionType.WRITE}

    def test_everyone_else_is_stored_when_inheritance_is_off(self) -> None:
        effective, inherit = resolve_bookstack_roles(
            {
                "role_permissions": [
                    {"role_id": 2, "view": False, "create": False, "update": True, "delete": False}
                ],
                "fallback_permissions": {
                    "inheriting": False, "view": True, "create": False, "update": False, "delete": False
                },
            },
            {2: {}, 4: {}},
            "page",
            {2: PermissionType.READ, 4: PermissionType.READ},
        )
        assert inherit is False
        assert effective == {4: PermissionType.READ}

    def test_inheriting_book_uses_system_roles(self) -> None:
        effective, inherit = resolve_bookstack_roles(
            {"role_permissions": [], "fallback_permissions": {"inheriting": True}},
            {1: {"permissions": ["book-view-all"]}},
            "book",
            None,
        )
        assert inherit is False
        assert effective == {1: PermissionType.READ}

    def test_an_inheriting_book_keeps_view_all_for_roles_without_a_row(self) -> None:
        effective, inherit = resolve_bookstack_roles(
            {
                "role_permissions": [
                    {"role_id": 1, "view": False, "create": False, "update": False, "delete": False}
                ],
                "fallback_permissions": {"inheriting": True},
            },
            {1: {"permissions": ["book-view-all"]}, 2: {"permissions": ["book-view-all"]}},
            "book",
            None,
        )
        assert inherit is False
        assert effective == {2: PermissionType.READ}

    def test_everyone_else_no_view_hides_a_role_with_book_view_all(self) -> None:
        effective, inherit = resolve_bookstack_roles(
            {
                "role_permissions": [],
                "fallback_permissions": {"inheriting": False, "view": False},
            },
            {
                1: {"permissions": ["book-view-all"]},
                2: {"permissions": ["restrictions-manage-all"]},
                3: {"system_name": "admin", "permissions": []},
            },
            "book",
            None,
        )
        assert inherit is False
        assert effective == {3: PermissionType.WRITE}

    def test_admin_still_sees_a_chapter_when_everyone_else_cannot(self) -> None:
        effective, inherit = resolve_bookstack_roles(
            {
                "role_permissions": [],
                "fallback_permissions": {"inheriting": False, "view": False},
            },
            {3: {"system_name": "admin"}},
            "chapter",
            {3: PermissionType.READ},
        )
        assert inherit is False
        assert effective == {3: PermissionType.WRITE}


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
