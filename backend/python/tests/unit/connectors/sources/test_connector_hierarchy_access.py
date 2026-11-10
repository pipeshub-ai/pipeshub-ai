"""Decisions for Drive, BookStack, Dropbox, OneDrive, and SharePoint item access."""

from app.connectors.sources.dropbox.access import (
    fallback_for_uncovered_user,
    member_is_direct,
)
from app.connectors.sources.google.drive.team.drive_access import (
    grants_to_store,
    is_drive_organizer,
    item_inherits_parent,
    permission_is_direct,
)
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

    def test_limited_access_keeps_inherited_managers_only(self) -> None:
        manager = {"permissionDetails": [{"permissionType": "member", "inherited": True, "role": "organizer"}]}
        content_manager = {"permissionDetails": [{"permissionType": "member", "inherited": True, "role": "fileOrganizer"}]}
        assert is_drive_organizer(manager) is True
        assert is_drive_organizer(content_manager) is False
        assert permission_is_direct(manager) is False

        manager_grant, direct_grant = _grant("manager@x.com"), _grant("direct@x.com")
        everyone = [manager_grant, _grant("content-manager@x.com"), direct_grant]
        assert grants_to_store(
            everyone, [direct_grant], saw_permission_details=True, inherited_permissions_disabled=True,
            is_drive=False, organizer_permissions=[manager_grant],
        ) == [direct_grant, manager_grant]
        assert grants_to_store(
            everyone, [direct_grant], saw_permission_details=True, inherited_permissions_disabled=False,
            is_drive=False, organizer_permissions=[manager_grant],
        ) == [direct_grant]

    def test_fallback_does_not_inherit(self) -> None:
        assert item_inherits_parent(
            saw_permission_details=True, inherited_permissions_disabled=False, is_fallback=True
        ) is False




class TestDropboxAccess:
    def test_inherited_file_member_is_not_direct(self) -> None:
        assert member_is_direct(is_inherited=True) is False

    def test_direct_and_unknown_members_are_kept(self) -> None:
        assert member_is_direct(is_inherited=False) is True
        assert member_is_direct(is_inherited=None) is True

    def test_inherited_user_is_not_granted_again(self) -> None:
        assert fallback_for_uncovered_user(grants_empty=True, user_is_covered=True) is None

    def test_private_item_gets_the_owner(self) -> None:
        assert fallback_for_uncovered_user(grants_empty=True, user_is_covered=False) == "owner"

    def test_missing_user_on_a_member_list_gets_write(self) -> None:
        assert fallback_for_uncovered_user(grants_empty=False, user_is_covered=False) == "write"


class TestOneDriveAndSharePoint:
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
