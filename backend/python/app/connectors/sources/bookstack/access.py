"""BookStack view access as PipesHub grants, following BookStack's joint permissions.

Per role (``JointPermissionBuilder::createJointPermissionData``): the admin role always
allows; else the nearest role row on the item → chapter → book chain, as long as no
nearer "Everyone Else" stops the walk (``EntityPermissionEvaluator``); else that
Everyone Else; else the role's own ``{item type}-view-all``. Per user
(``PermissionApplicator::restrictEntityQuery``): the highest status across the user's
roles, so a role-row deny outranks another role's implicit allow.
"""

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from enum import IntEnum

from app.models.permission import EntityType, Permission, PermissionType


class _Status(IntEnum):
    """BookStack's ``PermissionStatus``."""

    IMPLICIT_DENY = 0
    IMPLICIT_ALLOW = 1
    EXPLICIT_DENY = 2
    EXPLICIT_ALLOW = 3


@dataclass(frozen=True)
class BookStackChain:
    """Item permissions in force on an item: role rows nearest first, and the nearest Everyone Else."""

    roles: Mapping[int, PermissionType | None]
    everyone_else_set: bool = False
    everyone_else: PermissionType | None = None


class BookStackGrants(list):
    """The grants an item stores. ``inherit`` means it stores none and takes its parent's.

    ``chain`` is None when the parent's permissions could not be read, so children
    cannot be evaluated from this item either.
    """

    def __init__(
        self,
        permissions: list[Permission],
        *,
        inherit: bool,
        chain: BookStackChain | None,
        full: frozenset[tuple[str, str, str]],
        user_level: bool = False,
        warnings: tuple[str, ...] = (),
    ) -> None:
        super().__init__(permissions)
        self.inherit = inherit
        self.chain = chain
        self.full = full
        # Some grants go to single users because of their role memberships, so a
        # membership change must re-evaluate the item.
        self.user_level = user_level
        self.warnings = warnings


# Shelves and books have no parent to inherit from.
_TOP_LEVEL = frozenset({"bookshelf", "book"})
_RANK = {PermissionType.READ: 0, PermissionType.WRITE: 1}


def _flag_type(flags: dict) -> PermissionType | None:
    # Update or delete without view is not access to the content.
    if not flags.get("view"):
        return None
    if flags.get("update") or flags.get("delete") or flags.get("create"):
        return PermissionType.WRITE
    return PermissionType.READ


def _is_admin(role_details: dict) -> bool:
    return role_details.get("system_name") == "admin"


def _role_default(content_type: str, role_details: dict) -> PermissionType | None:
    """``restrictions-manage-all`` opens the permissions screen. It does not grant view."""
    names = set(role_details.get("permissions") or [])
    if f"{content_type}-view-all" not in names:
        return None
    write = {f"{content_type}-create-all", f"{content_type}-update-all", f"{content_type}-delete-all"}
    return PermissionType.WRITE if not names.isdisjoint(write) else PermissionType.READ


def entity_chain(permissions_data: dict, parent: BookStackChain | None) -> BookStackChain:
    roles: dict[int, PermissionType | None] = {}
    for row in permissions_data.get("role_permissions") or []:
        if row.get("role_id") is not None:
            roles[int(row["role_id"])] = _flag_type(row)
    fallback = permissions_data.get("fallback_permissions") or {}
    if not fallback.get("inheriting"):
        return BookStackChain(roles, True, _flag_type(fallback))
    if parent is None:
        return BookStackChain(roles)
    for role_id, kind in parent.roles.items():
        roles.setdefault(role_id, kind)
    return BookStackChain(roles, parent.everyone_else_set, parent.everyone_else)


def _role_status(
    chain: BookStackChain, content_type: str, role_id: int, details: dict
) -> tuple[_Status, PermissionType | None]:
    if _is_admin(details):
        return _Status.EXPLICIT_ALLOW, PermissionType.WRITE
    if role_id in chain.roles:
        kind = chain.roles[role_id]
        return (_Status.EXPLICIT_ALLOW, kind) if kind else (_Status.EXPLICIT_DENY, None)
    if chain.everyone_else_set:
        kind = chain.everyone_else
    else:
        kind = _role_default(content_type, details)
    return (_Status.IMPLICIT_ALLOW, kind) if kind else (_Status.IMPLICIT_DENY, None)


def _members(roles_details: Mapping[int, dict]) -> dict[int, set[int] | None]:
    """BookStack user ids per role; None when the role's users were not read."""
    out: dict[int, set[int] | None] = {}
    for role_id, details in roles_details.items():
        users = details.get("users") if isinstance(details, dict) else None
        out[int(role_id)] = None if users is None else {int(u["id"]) for u in users if u.get("id") is not None}
    return out


def _implicit_user_grants(
    statuses: dict[int, tuple[_Status, PermissionType | None]],
    roles_details: Mapping[int, dict],
    user_emails: Mapping[int, str],
    warnings: list[str],
) -> list[Permission]:
    """Members of implicitly allowed roles, minus members of a denying role whom no
    explicit allow covers. A role grant would also reach those denied members."""
    members = _members(roles_details)
    denying = [r for r, (s, _) in statuses.items() if s == _Status.EXPLICIT_DENY]
    if any(members.get(r) is None for r in denying):
        warnings.append(f"members of denying roles {sorted(denying)} unknown; default access withheld")
        return []
    denied = set().union(*(members[r] for r in denying))
    covered = set().union(*(
        members.get(r) or set() for r, (s, _) in statuses.items() if s == _Status.EXPLICIT_ALLOW
    ))
    per_user: dict[int, PermissionType] = {}
    for role_id, (status, kind) in statuses.items():
        if status != _Status.IMPLICIT_ALLOW or not kind:
            continue
        if members.get(role_id) is None:
            warnings.append(f"members of role {role_id} unknown; its default access withheld")
            continue
        for user_id in members[role_id] - denied - covered:
            if user_id not in per_user or _RANK[kind] > _RANK[per_user[user_id]]:
                per_user[user_id] = kind
    grants = []
    for user_id, kind in sorted(per_user.items()):
        email = user_emails.get(user_id)
        if not email:
            warnings.append(f"no email for BookStack user {user_id}; access withheld")
            continue
        grants.append(Permission(external_id=str(user_id), email=email, type=kind, entity_type=EntityType.USER))
    return grants


def _owner_sees_by_view_own(
    owner: Permission | None,
    statuses: dict[int, tuple[_Status, PermissionType | None]],
    chain: BookStackChain,
    content_type: str,
    roles_details: Mapping[int, dict],
    owner_role_ids: Iterable[int] | None,
) -> bool:
    """Ownership counts only through a role's ``{type}-view-own``, only for a role no item
    permission applies to, and not when the owner's roles otherwise deny the item."""
    if owner is None or not str(owner.external_id).isdigit():
        return False
    if owner_role_ids is None:
        user_id = int(owner.external_id)
        owner_role_ids = [r for r, users in _members(roles_details).items() if users and user_id in users]
    roles = [int(r) for r in owner_role_ids]
    best = max((statuses[r][0] for r in roles if r in statuses), default=_Status.IMPLICIT_DENY)
    if best != _Status.IMPLICIT_DENY:
        # Allowed by a role grant already, or denied by a role row.
        return False
    return any(
        r not in chain.roles
        and not chain.everyone_else_set
        and f"{content_type}-view-own" in ((roles_details.get(r) or {}).get("permissions") or [])
        for r in roles
    )


def _key(permission: Permission) -> tuple[str, str, str]:
    principal = permission.email if permission.entity_type == EntityType.USER else permission.external_id
    return (permission.entity_type.value, str(principal), permission.type.value)


def _role_grant(role_id: int, kind: PermissionType) -> Permission:
    return Permission(external_id=str(role_id), type=kind, entity_type=EntityType.ROLE)


def _parentless_child(permissions_data: dict, roles_details: Mapping[int, dict]) -> BookStackGrants:
    """A chapter or page whose parent could not be read: keep its own allow rows and the
    parent's stored grants, unless a row denies, which inheriting cannot express. Whether
    view-own applies depends on that unread chain, so the owner gets nothing of their own."""
    stored: dict[int, PermissionType] = {}
    has_deny = False
    for row in permissions_data.get("role_permissions") or []:
        role_id = row.get("role_id")
        if role_id is None or _is_admin(roles_details.get(int(role_id)) or {}):
            continue
        kind = _flag_type(row)
        if kind is None:
            has_deny = True
        else:
            stored[int(role_id)] = kind
    if has_deny:
        for role_id, details in roles_details.items():
            if isinstance(details, dict) and _is_admin(details):
                stored[int(role_id)] = PermissionType.WRITE
    grants = [_role_grant(r, k) for r, k in stored.items()]
    return BookStackGrants(grants, inherit=not has_deny, chain=None, full=frozenset(_key(g) for g in grants))


def bookstack_grants(
    owner: Permission | None,
    permissions_data: dict,
    roles_details: Mapping[int, dict],
    content_type: str,
    parent: BookStackGrants | None,
    *,
    user_emails: Mapping[int, str] | None = None,
    owner_role_ids: Iterable[int] | None = None,
) -> BookStackGrants:
    """Grants for one item. ``parent`` is the result for its book or chapter: None for a
    shelf or book, and for a child whose parent's permissions could not be read.
    ``user_emails`` maps BookStack user ids to emails, for grants to single users;
    ``owner_role_ids`` are the owner's roles, else read from the roles' members."""
    top_level = content_type in _TOP_LEVEL
    if not top_level and (parent is None or parent.chain is None):
        if (permissions_data.get("fallback_permissions") or {}).get("inheriting"):
            return _parentless_child(permissions_data, roles_details)
    chain = entity_chain(permissions_data, None if top_level or parent is None else parent.chain)

    statuses = {
        role_id: _role_status(chain, content_type, role_id, roles_details.get(role_id) or {})
        for role_id in sorted({int(r) for r in roles_details} | set(chain.roles))
    }
    grants = [_role_grant(r, k) for r, (s, k) in statuses.items() if s == _Status.EXPLICIT_ALLOW and k]
    implicit = [_role_grant(r, k) for r, (s, k) in statuses.items() if s == _Status.IMPLICIT_ALLOW and k]
    warnings: list[str] = []
    user_level = False
    if implicit and any(s == _Status.EXPLICIT_DENY for s, _ in statuses.values()):
        user_grants = _implicit_user_grants(statuses, roles_details, user_emails or {}, warnings)
        grants += user_grants
        user_level = bool(user_grants)
    else:
        grants += implicit
    if _owner_sees_by_view_own(owner, statuses, chain, content_type, roles_details, owner_role_ids):
        grants.append(owner)
        user_level = True

    full = frozenset(_key(g) for g in grants)
    # Children store their own grants unless they match the parent's exactly: a role's
    # defaults differ per content type.
    inherit = not top_level and parent is not None and parent.full == full
    return BookStackGrants(
        [] if inherit else grants, inherit=inherit, chain=chain, full=full,
        user_level=user_level, warnings=tuple(warnings),
    )
