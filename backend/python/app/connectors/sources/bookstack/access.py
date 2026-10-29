"""Who may view a BookStack book, chapter or page, worked out as BookStack works it out.

BookStack decides per item and per role (``JointPermissionBuilder``), then per user over all of the user's
roles (``PermissionApplicator::restrictEntityQuery``):

- the admin role may view everything;
- otherwise the content permission rows of the item, then of its chapter, then of its book, nearest first: a
  row for the role decides (an explicit allow or deny); else an "Everyone Else" row decides (an implicit allow
  or deny), and the walk stops at the first level that has one;
- with no row on the way, the role's own permission for that type of item decides: ``page-view-all`` for a
  page, ``chapter-view-all`` for a chapter, ``book-view-all`` for a book. A role that may view a book may
  therefore still not view its pages;
- ``<type>-view-own`` lets the role see what the user owns, but only on that last path (no row applies);
- a user sees the item when their roles' highest status is an allow, or when they own it, a role gives them
  "own" and that highest status is not an explicit deny. An explicit deny on one role beats an implicit allow
  from another; an explicit allow beats everything.

PipesHub grants are unions, so the last rule cannot be written as role grants alone. Each item therefore gets
its own complete grants (it inherits nothing): role grants where every member of the role may view it, and user
grants for the people a role grant cannot cover exactly. Those user grants depend on who is in which role, so
the item is flagged ``membership_dependent`` and must be worked out again when memberships change.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.models.permission import EntityType, Permission, PermissionType

IMPLICIT_DENY, IMPLICIT_ALLOW, EXPLICIT_DENY, EXPLICIT_ALLOW = 0, 1, 2, 3
_ALLOW = (IMPLICIT_ALLOW, EXPLICIT_ALLOW)
_WRITE_FLAGS = ("create", "update", "delete")


@dataclass(frozen=True)
class ItemRows:
    """The content permission rows of one item, as ``GET /api/content-permissions/{type}/{id}`` returns them."""

    roles: dict[int, dict[str, bool]]
    fallback: dict[str, bool] | None
    owner_id: int | None = None

    @classmethod
    def from_api(cls, data: object) -> "ItemRows | None":
        """The rows, or None when the body is not a content permission answer (an error body, for one)."""
        if not isinstance(data, dict) or not isinstance(data.get("fallback_permissions"), dict) \
                or not isinstance(data.get("role_permissions"), list):
            return None
        roles: dict[int, dict[str, bool]] = {}
        for row in data["role_permissions"]:
            if isinstance(row, dict) and row.get("role_id") is not None:
                roles[int(row["role_id"])] = {k: bool(row.get(k)) for k in ("view", *_WRITE_FLAGS)}
        fb = data["fallback_permissions"]
        fallback = None if fb.get("inheriting", True) else {k: bool(fb.get(k)) for k in ("view", *_WRITE_FLAGS)}
        owner = data.get("owner")
        owner_id = int(owner["id"]) if isinstance(owner, dict) and owner.get("id") is not None else None
        return cls(roles, fallback, owner_id)

    @property
    def has_explicit_deny(self) -> bool:
        return any(not flags["view"] for flags in self.roles.values())


@dataclass
class ItemAccess:
    """An item's grants, and whether they hold only for today's role memberships."""

    permissions: list[Permission] = field(default_factory=list)
    membership_dependent: bool = False


def _is_admin(role: dict) -> bool:
    return role.get("system_name") == "admin"


def role_status(content_type: str, chain: list[ItemRows], role_id: int, role: dict) -> tuple[int, bool, bool]:
    """(status, own counts, may write) for one role on one item; ``chain`` is the item's rows first."""
    if _is_admin(role):
        return EXPLICIT_ALLOW, True, True
    role_flags: dict[str, bool] | None = None
    fallback: dict[str, bool] | None = None
    for rows in chain:
        if role_flags is None and role_id in rows.roles:
            role_flags = rows.roles[role_id]
        if fallback is None and rows.fallback is not None:
            fallback = rows.fallback
            break
    if role_flags is not None:
        return (EXPLICIT_ALLOW if role_flags["view"] else EXPLICIT_DENY), False, \
            any(role_flags[k] for k in _WRITE_FLAGS)
    if fallback is not None:
        return (IMPLICIT_ALLOW if fallback["view"] else IMPLICIT_DENY), False, any(fallback[k] for k in _WRITE_FLAGS)
    names = set(role.get("permissions") or [])
    status = IMPLICIT_ALLOW if f"{content_type}-view-all" in names else IMPLICIT_DENY
    own = f"{content_type}-view-own" in names
    write = any(f"{content_type}-{k}-all" in names for k in _WRITE_FLAGS)
    return status, own, write


def _members(role: dict) -> set[int]:
    return {int(u["id"]) for u in role.get("users") or [] if isinstance(u, dict) and u.get("id") is not None}


def item_access(
    content_type: str,
    chain: list[ItemRows],
    roles: dict[int, dict],
    user_emails: dict[int, str],
    owner_id: int | None,
) -> ItemAccess:
    """The grants that let exactly BookStack's viewers of one item find it.

    ``roles`` are the role details (with their ``users`` and ``permissions``) keyed by id, ``user_emails``
    maps a BookStack user id to the email PipesHub matches people by, ``owner_id`` is the item's owner.
    """
    status: dict[int, tuple[int, bool, bool]] = {
        int(rid): role_status(content_type, chain, int(rid), role) for rid, role in roles.items()}
    user_roles: dict[int, set[int]] = {}
    for rid, role in roles.items():
        for uid in _members(role):
            user_roles.setdefault(uid, set()).add(int(rid))

    def best(uid: int) -> int:
        return max((status[r][0] for r in user_roles.get(uid, ())), default=-1)

    def visible(uid: int) -> bool:
        top = best(uid)
        if top in _ALLOW:
            return True
        owns = owner_id is not None and uid == owner_id
        return owns and top != EXPLICIT_DENY and any(status[r][1] for r in user_roles.get(uid, ()))

    access = ItemAccess()
    statuses = {st for st, _, _ in status.values()}
    # Someone who joins an implicitly allowed role while in an explicitly denied one loses the item.
    access.membership_dependent = EXPLICIT_DENY in statuses and IMPLICIT_ALLOW in statuses
    covered: set[int] = set()
    for rid in sorted(status):
        st, _, write = status[rid]
        if st not in _ALLOW:
            continue
        members = _members(roles[rid])
        # A role grant reaches every member: give it only when every member may view the item.
        if all(visible(uid) for uid in members):
            access.permissions.append(Permission(
                external_id=str(rid), type=PermissionType.WRITE if write else PermissionType.READ,
                entity_type=EntityType.ROLE))
            covered |= members
        else:
            access.membership_dependent = True
    for uid in sorted(set(user_roles) | ({owner_id} if owner_id is not None else set())):
        if uid in covered or not visible(uid):
            continue
        email = user_emails.get(uid)
        if not email:
            continue
        is_owner = owner_id is not None and uid == owner_id
        access.permissions.append(Permission(
            external_id=str(uid), email=email, type=PermissionType.OWNER if is_owner else PermissionType.READ,
            entity_type=EntityType.USER))
        # A direct grant follows the person, not the role: it is right only while memberships stay as they are.
        access.membership_dependent = True
    return access
