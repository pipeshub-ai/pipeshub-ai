"""When BookStack ownership opens an item, and when it does not.

BookStack applies a role's ``*-view-own`` only while no content permission override
reaches that role on the item or an ancestor (JointPermissionBuilder: the override
path never sets ``owner_id``). These tests walk book -> chapter -> page the way the
connector does, passing each parent's ``effective`` map down.
"""

from app.connectors.sources.bookstack.access import BookStackGrants, bookstack_grants
from app.models.permission import EntityType, Permission, PermissionType

ADMIN, EDITOR, VIEWER = 1, 2, 3
ROLES = {
    ADMIN: {"system_name": "admin", "permissions": []},
    # Sees only what it owns.
    EDITOR: {"permissions": ["book-view-own", "chapter-view-own", "page-view-own"]},
    VIEWER: {"permissions": ["book-view-all", "chapter-view-all", "page-view-all"]},
}
OWNER = Permission(external_id="7", email="owner@example.com", type=PermissionType.OWNER, entity_type=EntityType.USER)
INHERIT = {"fallback_permissions": {"inheriting": True}, "role_permissions": []}
DENY_EVERYONE = {"fallback_permissions": {"inheriting": False, "view": False}, "role_permissions": []}


def _row(role_id: int, *, view: bool) -> dict:
    return {"role_id": role_id, "view": view, "create": False, "update": False, "delete": False}


def _chain(book: dict, chapter: dict, page: dict, owner_roles: list[int]) -> BookStackGrants:
    b = bookstack_grants(OWNER, book, ROLES, "book", None, owner_roles)
    c = bookstack_grants(OWNER, chapter, ROLES, "chapter", b.effective, owner_roles)
    return bookstack_grants(OWNER, page, ROLES, "page", c.effective, owner_roles)


def _has_owner(grants: BookStackGrants) -> bool:
    return any(p.type == PermissionType.OWNER for p in grants)


def test_owner_with_view_own_sees_an_item_no_override_reaches() -> None:
    assert _has_owner(_chain(INHERIT, INHERIT, INHERIT, [EDITOR]))


def test_restricted_page_hides_from_its_owner() -> None:
    # BS-09: an Admin-only page owned by an Editor stays hidden from the Editor.
    assert not _has_owner(_chain(INHERIT, INHERIT, DENY_EVERYONE, [EDITOR]))


def test_override_for_the_owners_role_on_an_ancestor_reaches_the_page() -> None:
    chapter = {"fallback_permissions": {"inheriting": True}, "role_permissions": [_row(EDITOR, view=False)]}
    assert not _has_owner(_chain(INHERIT, chapter, INHERIT, [EDITOR]))


def test_an_allow_override_also_switches_ownership_off() -> None:
    page = {"fallback_permissions": {"inheriting": True}, "role_permissions": [_row(EDITOR, view=True)]}
    grants = _chain(INHERIT, INHERIT, page, [EDITOR])
    assert not _has_owner(grants)
    assert any(p.entity_type == EntityType.ROLE and p.external_id == str(EDITOR) for p in grants)


def test_override_for_another_role_leaves_the_owner_access() -> None:
    chapter = {"fallback_permissions": {"inheriting": True}, "role_permissions": [_row(VIEWER, view=False)]}
    assert _has_owner(_chain(INHERIT, chapter, INHERIT, [EDITOR]))


def test_owner_already_covered_by_a_role_gets_no_extra_grant() -> None:
    assert not _has_owner(_chain(INHERIT, INHERIT, INHERIT, [VIEWER]))


def test_owner_without_view_own_gets_nothing_from_ownership() -> None:
    assert not _has_owner(_chain(INHERIT, INHERIT, INHERIT, []))


def test_unknown_parent_does_not_assume_ownership() -> None:
    assert not _has_owner(bookstack_grants(OWNER, INHERIT, ROLES, "page", None, [EDITOR]))
