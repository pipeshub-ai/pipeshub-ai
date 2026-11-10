"""Who may view a BookStack item, against BookStack's own rules (``access.py``).

Each case is a rule of BookStack's ``JointPermissionBuilder`` and ``PermissionApplicator::restrictEntityQuery``,
checked on a real BookStack v26.05.4 with the connector simulator's differential run.
"""

from app.connectors.sources.bookstack.access import (
    EXPLICIT_ALLOW,
    EXPLICIT_DENY,
    IMPLICIT_ALLOW,
    IMPLICIT_DENY,
    ItemRows,
    item_access,
    role_status,
)
from app.models.permission import EntityType, PermissionType

ADMIN, EDITOR, VIEWER, READERS, AUTHORS = 1, 2, 3, 7, 6
VIEW = ["book-view-all", "chapter-view-all", "page-view-all"]


def roles(**members: list[int]) -> dict[int, dict]:
    defs = {
        ADMIN: {"display_name": "Admin", "system_name": "admin", "permissions": []},
        EDITOR: {"display_name": "Editor", "permissions": [*VIEW, "page-update-all"]},
        VIEWER: {"display_name": "Viewer", "permissions": VIEW},
        READERS: {"display_name": "Readers", "permissions": VIEW},
        # May view books and chapters, but only its own pages.
        AUTHORS: {"display_name": "Authors", "permissions": ["book-view-all", "chapter-view-all", "page-view-own"]},
    }
    names = {"admin": ADMIN, "editor": EDITOR, "viewer": VIEWER, "readers": READERS, "authors": AUTHORS}
    out = {}
    for name, rid in names.items():
        out[rid] = {**defs[rid], "id": rid, "users": [{"id": u} for u in members.get(name, [])]}
    return out


def rows(role_rows: dict[int, bool] | None = None, fallback: bool | None = None, owner: int = 1) -> ItemRows:
    return ItemRows.from_api({
        "owner": {"id": owner},
        "role_permissions": [{"role_id": r, "view": v, "create": False, "update": False, "delete": False}
                             for r, v in (role_rows or {}).items()],
        "fallback_permissions": {"inheriting": fallback is None, "view": fallback, "create": False,
                                 "update": False, "delete": False},
    })


EMAILS = {u: f"u{u}@example.com" for u in range(1, 20)}


def viewers(access) -> tuple[set[str], set[str]]:
    role_ids = {p.external_id for p in access.permissions if p.entity_type == EntityType.ROLE}
    users = {p.email for p in access.permissions if p.entity_type == EntityType.USER}
    return role_ids, users


def test_an_error_body_is_not_permission_rows() -> None:
    assert ItemRows.from_api({"error": {"message": "An error occurred", "code": 429}}) is None
    assert ItemRows.from_api({"role_permissions": []}) is None
    assert ItemRows.from_api(None) is None


def test_a_role_that_may_view_books_may_not_view_their_pages() -> None:
    """book-view-all opens the book, not its pages: page access is decided by page-view-all."""
    r = roles(authors=[5], viewer=[3])
    access = item_access("page", [rows(), rows()], r, EMAILS, owner_id=1)
    role_ids, users = viewers(access)
    assert str(AUTHORS) not in role_ids and "u5@example.com" not in users
    assert str(VIEWER) in role_ids
    book = item_access("book", [rows()], r, EMAILS, owner_id=1)
    assert str(AUTHORS) in viewers(book)[0]


def test_view_own_lets_the_owner_see_their_page_only_on_the_default_path() -> None:
    r = roles(authors=[5])
    own = item_access("page", [rows(owner=5), rows()], r, EMAILS, owner_id=5)
    assert viewers(own)[1] == {"u5@example.com"}
    # Everyone Else set on the book: ownership no longer counts (BookStack passes hasPermissionOwn=false).
    closed = item_access("page", [rows(owner=5), rows(fallback=False)], r, EMAILS, owner_id=5)
    assert viewers(closed) == ({str(ADMIN)}, set())


def test_an_explicit_deny_beats_another_roles_implicit_allow() -> None:
    """Gina is in Readers (may view by role permission) and Viewer (denied the page): BookStack hides it."""
    r = roles(readers=[8, 9], viewer=[8, 3])
    access = item_access("page", [rows({VIEWER: False}), rows()], r, EMAILS, owner_id=1)
    role_ids, users = viewers(access)
    assert str(READERS) not in role_ids, "a Readers grant would reach Gina"
    assert "u8@example.com" not in users
    assert "u9@example.com" in users  # in Readers only: may view, granted as a person
    assert access.membership_dependent


def test_an_explicit_allow_beats_an_explicit_deny() -> None:
    r = roles(readers=[8], viewer=[8])
    access = item_access("page", [rows({VIEWER: False, READERS: True}), rows()], r, EMAILS, owner_id=1)
    assert str(READERS) in viewers(access)[0]


def test_a_deny_with_no_conflicting_member_is_still_membership_dependent() -> None:
    """Nobody is in both roles today; someone who joins Readers while in Viewer must lose the page."""
    r = roles(readers=[9], viewer=[3])
    access = item_access("page", [rows({VIEWER: False}), rows()], r, EMAILS, owner_id=1)
    assert str(READERS) in viewers(access)[0]
    assert access.membership_dependent


def test_everyone_else_applies_to_every_role_without_its_own_row() -> None:
    r = roles(viewer=[3], readers=[9])
    access = item_access("page", [rows(), rows({READERS: True}, fallback=False)], r, EMAILS, owner_id=1)
    assert viewers(access)[0] == {str(ADMIN), str(READERS)}


def test_the_nearest_everyone_else_row_ends_the_walk() -> None:
    """A role row on the book is not reached when the page has its own Everyone Else row."""
    assert role_status("page", [rows(fallback=True), rows({VIEWER: False})], VIEWER,
                       roles()[VIEWER])[0] == IMPLICIT_ALLOW
    assert role_status("page", [rows(), rows({VIEWER: False})], VIEWER, roles()[VIEWER])[0] == EXPLICIT_DENY
    assert role_status("page", [rows({VIEWER: True}), rows(fallback=False)], VIEWER,
                       roles()[VIEWER])[0] == EXPLICIT_ALLOW
    assert role_status("page", [rows(), rows()], AUTHORS, roles()[AUTHORS])[0] == IMPLICIT_DENY


def test_the_admin_role_sees_everything() -> None:
    r = roles(admin=[1])
    access = item_access("page", [rows({ADMIN: False}, fallback=False), rows()], r, EMAILS, owner_id=2)
    assert str(ADMIN) in viewers(access)[0]


def test_a_role_that_may_update_gets_write() -> None:
    r = roles(editor=[2])
    access = item_access("page", [rows(), rows()], r, EMAILS, owner_id=1)
    grant = next(p for p in access.permissions if p.external_id == str(EDITOR))
    assert grant.type == PermissionType.WRITE


def test_a_user_in_no_role_sees_nothing() -> None:
    r = roles(viewer=[3])
    access = item_access("page", [rows(owner=12), rows()], r, {**EMAILS}, owner_id=12)
    assert "u12@example.com" not in viewers(access)[1]


def test_without_a_conflict_grants_do_not_depend_on_memberships() -> None:
    r = roles(viewer=[3], readers=[9])
    access = item_access("page", [rows(), rows()], r, EMAILS, owner_id=1)
    assert not access.membership_dependent
    assert all(p.entity_type == EntityType.ROLE for p in access.permissions)
