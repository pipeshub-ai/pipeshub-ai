"""Which Drive grants belong on an item, and whether the item inherits its parent."""

DOMAIN_GROUP_PREFIX = "domain:"


def domain_group_id(domain: str) -> str:
    """The user group standing for "anyone at <domain>"; a Google group's id is an email, so it cannot collide."""
    return f"{DOMAIN_GROUP_PREFIX}{domain.lower()}"


def permission_is_direct(permission: dict, *, member_is_direct: bool = False) -> bool | None:
    """True when this grant was written on the item, False when every detail is inherited.

    A "file" detail is a share on this item or on a folder above it; only
    ``inherited`` tells which. On a limited-access item the inherited ones open
    metadata only. None when ``permissionDetails`` is absent, which is not
    evidence that the grant was inherited.

    ``member_is_direct`` keeps a shared drive membership on the item, for a
    drive whose record group carries no members to inherit from.
    """
    details = permission.get("permissionDetails") or []
    if not isinstance(details, list) or not details:
        return None
    for detail in details:
        if not isinstance(detail, dict):
            continue
        if detail.get("inherited") is False:
            return True
        if member_is_direct and detail.get("permissionType") == "member":
            return True
    return False


def opens_metadata_only(permission: dict) -> bool:
    """Google marks a parent's reader who sees only a limited folder's name with ``view: metadata``."""
    return permission.get("view") == "metadata"


class DrivePermissionBatch(list):
    """The grants to store. ``saw_permission_details`` is false for a drive root and when Google sent no details."""

    def __init__(self, permissions: list, *, saw_permission_details: bool) -> None:
        super().__init__(permissions)
        self.saw_permission_details = saw_permission_details


def grants_to_store(
    permissions: list,
    direct_permissions: list,
    *,
    saw_permission_details: bool,
    inherited_permissions_disabled: bool,
    is_drive: bool,
) -> list:
    """An item with permission details stores only grants written on the item.

    Limited access still marks some entries inherited. Those people can open
    metadata, not the file, so they are not stored as readers.
    """
    if saw_permission_details and not is_drive:
        return direct_permissions
    return permissions


def item_inherits_parent(*, saw_permission_details: bool, inherited_permissions_disabled: bool, is_fallback: bool) -> bool:
    """Shared Drive items inherit unless limited access is on. My Drive and fallbacks do not.

    My Drive returns the effective list with no inherited flag. Storing that list
    and also inheriting the parent would keep people a limited folder had removed.
    A fallback is one user's grant, not the item's ACL, so it must not inherit.
    """
    if is_fallback or inherited_permissions_disabled or not saw_permission_details:
        return False
    return True
