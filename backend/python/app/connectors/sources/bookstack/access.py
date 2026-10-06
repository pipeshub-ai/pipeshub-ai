"""BookStack content permissions as role grants plus whether the item inherits."""

from app.models.permission import EntityType, Permission, PermissionType


class BookStackGrants(list):
    """Role and owner grants. ``inherit`` is false when this item's own rules replace its parent."""

    def __init__(self, permissions: list, *, inherit: bool, effective: dict[int, PermissionType]) -> None:
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


def resolve_bookstack_roles(
    permissions_data: dict,
    roles_details: dict,
    content_type: str,
    parent_roles: dict[int, PermissionType] | None,
) -> tuple[dict[int, PermissionType], bool]:
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


def bookstack_grants(
    owner: Permission | None,
    permissions_data: dict,
    roles_details: dict,
    content_type: str,
    parent_roles: dict[int, PermissionType] | None,
) -> BookStackGrants:
    """Owner plus the role grants this item stores. Inherited roles stay on the parent."""
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
    if owner is not None:
        permissions.append(owner)
    permissions.extend(
        Permission(external_id=str(role_id), type=kind, entity_type=EntityType.ROLE)
        for role_id, kind in stored.items()
    )
    return BookStackGrants(permissions, inherit=inherit, effective=effective)
