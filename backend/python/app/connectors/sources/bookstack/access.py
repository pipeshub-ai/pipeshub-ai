"""BookStack content permissions as role grants plus whether the item inherits."""

from app.models.permission import EntityType, Permission, PermissionType


class RoleAccess(dict):
    """Effective role map of one item, plus the roles no content permission override reaches.

    BookStack honours a role's ``*-view-own`` only while no override applies to that
    role on the item or any ancestor; any override row, or an Everyone Else setting,
    switches ownership off for it. ``default_roles`` carries that set down the tree.
    """

    def __init__(self, effective: dict[int, PermissionType], default_roles: frozenset[int]) -> None:
        super().__init__(effective)
        self.default_roles = default_roles


class BookStackGrants(list):
    """Role and owner grants. ``inherit`` is false when this item's own rules replace its parent."""

    def __init__(self, permissions: list, *, inherit: bool, effective: RoleAccess) -> None:
        super().__init__(permissions)
        self.inherit = inherit
        self.effective = effective


def _flag_type(flags: dict) -> PermissionType | None:
    # Update or delete without view is not access to the content.
    if not flags.get("view"):
        return None
    if flags.get("update") or flags.get("delete") or flags.get("create"):
        return PermissionType.WRITE
    return PermissionType.READ


def _is_admin(role_details: dict) -> bool:
    """The default admin role keeps every permission."""
    return role_details.get("system_name") == "admin"


def _system_type(content_type: str, role_details: dict) -> PermissionType | None:
    """Role permissions. They apply only when the item is still inheriting defaults.

    ``restrictions-manage-all`` opens the permissions screen. It does not grant view.
    """
    names = set(role_details.get("permissions") or [])
    if f"{content_type}-view-all" not in names:
        return None
    write = {
        f"{content_type}-create-all",
        f"{content_type}-update-all",
        f"{content_type}-delete-all",
    }
    if not names.isdisjoint(write):
        return PermissionType.WRITE
    return PermissionType.READ


def _system_roles(content_type: str, roles_details: dict) -> dict[int, PermissionType]:
    effective: dict[int, PermissionType] = {}
    for role_id, details in roles_details.items():
        kind = _system_type(content_type, details)
        if kind is not None:
            effective[int(role_id)] = kind
    return effective


def _grant_admins(effective: dict[int, PermissionType], roles_details: dict) -> None:
    for role_id, details in roles_details.items():
        if isinstance(details, dict) and _is_admin(details):
            effective[int(role_id)] = PermissionType.WRITE


def _default_roles(
    *,
    inheriting: bool,
    rows: dict[int, dict],
    roles_details: dict,
    content_type: str,
    parent_roles: dict[int, PermissionType] | None,
) -> frozenset[int]:
    """Roles still on BookStack's default path for this item (no override here or above)."""
    if not inheriting:
        return frozenset()
    if content_type == "book" and parent_roles is None:
        above = frozenset(int(role_id) for role_id in roles_details)
    else:
        # An unknown parent may carry an override, so ownership is not assumed.
        above = getattr(parent_roles, "default_roles", frozenset())
    return above - frozenset(rows)


def resolve_bookstack_roles(
    permissions_data: dict,
    roles_details: dict,
    content_type: str,
    parent_roles: dict[int, PermissionType] | None,
) -> tuple[RoleAccess, bool]:
    """Return ``(effective role map, inherit)``.

    A child that only inherits stores nothing of its own. A role row, including a
    deny, or an Everyone Else setting, replaces inheritance with the effective map
    so a parent grant cannot outvote the child.
    """
    fallback = permissions_data.get("fallback_permissions") or {}
    inheriting = bool(fallback.get("inheriting"))
    rows: dict[int, dict] = {}
    for row in permissions_data.get("role_permissions") or []:
        role_id = row.get("role_id")
        if role_id is not None:
            rows[int(role_id)] = row
    default_roles = _default_roles(
        inheriting=inheriting, rows=rows, roles_details=roles_details,
        content_type=content_type, parent_roles=parent_roles,
    )
    effective, inherit = _resolve_effective(
        inheriting=inheriting, rows=rows, roles_details=roles_details,
        content_type=content_type, parent_roles=parent_roles, fallback=fallback,
    )
    return RoleAccess(effective, default_roles), inherit


def _resolve_effective(
    *,
    inheriting: bool,
    rows: dict[int, dict],
    roles_details: dict,
    content_type: str,
    parent_roles: dict[int, PermissionType] | None,
    fallback: dict,
) -> tuple[dict[int, PermissionType], bool]:
    # While Everyone Else inherits defaults, a book has no parent map, so the
    # role permissions are that default. A role row then overrides one role.
    # When Everyone Else does not inherit, role permissions are not used.
    if inheriting and content_type == "book" and parent_roles is None:
        parent_roles = _system_roles(content_type, roles_details)

    if inheriting and not rows:
        if content_type == "book":
            effective = dict(parent_roles or {})
            _grant_admins(effective, roles_details)
            return effective, False
        if parent_roles is not None:
            return dict(parent_roles), True
        return {}, True

    if inheriting and parent_roles is not None:
        effective = dict(parent_roles)
        for role_id, row in rows.items():
            if _is_admin(roles_details.get(role_id) or {}):
                continue
            kind = _flag_type(row)
            if kind is None:
                effective.pop(role_id, None)
            else:
                effective[role_id] = kind
        _grant_admins(effective, roles_details)
        return effective, False

    if inheriting:
        # The parent's roles are not loaded. A deny cannot be expressed by
        # inheriting that unknown parent, so the child stops inheriting.
        explicit: dict[int, PermissionType] = {}
        has_deny = False
        for role_id, row in rows.items():
            if _is_admin(roles_details.get(role_id) or {}):
                continue
            kind = _flag_type(row)
            if kind is None:
                has_deny = True
            else:
                explicit[role_id] = kind
        _grant_admins(explicit, roles_details)
        return explicit, not has_deny

    # Everyone Else covers every role without its own row. Role permissions do not.
    base = _flag_type(fallback)
    known = {int(role_id) for role_id in roles_details}
    known.update(rows)
    effective: dict[int, PermissionType] = {}
    for role_id in known:
        if _is_admin(roles_details.get(role_id) or {}):
            continue
        if role_id in rows:
            kind = _flag_type(rows[role_id])
            if kind is not None:
                effective[role_id] = kind
        elif base is not None:
            effective[role_id] = base
    _grant_admins(effective, roles_details)
    return effective, False


def owner_has_own_access(
    owner_role_ids: list[int], effective: RoleAccess, roles_details: dict, content_type: str
) -> bool:
    """True when the owner sees the item only because they own it.

    A role already in ``effective`` reaches the owner as a role grant, so the owner
    grant is stored only where ownership is what opens the item.
    """
    for role_id in owner_role_ids:
        if role_id not in effective.default_roles or role_id in effective:
            continue
        details = roles_details.get(role_id) or roles_details.get(str(role_id)) or {}
        names = set(details.get("permissions") or [])
        if f"{content_type}-view-own" in names:
            return True
    return False


def bookstack_grants(
    owner: Permission | None,
    permissions_data: dict,
    roles_details: dict,
    content_type: str,
    parent_roles: dict[int, PermissionType] | None,
    owner_role_ids: list[int] | None = None,
) -> BookStackGrants:
    """Owner, when ownership grants access, plus the role grants this item stores.

    Inherited roles stay on the parent.
    """
    effective, inherit = resolve_bookstack_roles(
        permissions_data, roles_details, content_type, parent_roles
    )
    stored: dict[int, PermissionType] = {}
    if inherit:
        for row in permissions_data.get("role_permissions") or []:
            role_id = row.get("role_id")
            if role_id is None:
                continue
            kind = _flag_type(row)
            if kind is not None:
                stored[int(role_id)] = kind
    else:
        stored = effective
    permissions: list[Permission] = []
    if owner is not None and owner_has_own_access(owner_role_ids or [], effective, roles_details, content_type):
        permissions.append(owner)
    permissions.extend(
        Permission(external_id=str(role_id), type=kind, entity_type=EntityType.ROLE)
        for role_id, kind in stored.items()
    )
    return BookStackGrants(permissions, inherit=inherit, effective=effective)
