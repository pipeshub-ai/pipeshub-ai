"""Which Drive grants belong on an item, and whether the item inherits its parent."""


def permission_is_direct(permission: dict) -> bool | None:
    """True when this grant was written on the item, False when every detail is inherited.

    None when ``permissionDetails`` is absent. My Drive omits it, so a missing
    list is not evidence that the grant was inherited.
    """
    details = permission.get("permissionDetails") or []
    if not isinstance(details, list) or not details:
        return None
    for detail in details:
        if not isinstance(detail, dict):
            continue
        # A "file" entry can still be inherited from a parent folder.
        if detail.get("inherited") is False:
            return True
    return False


def is_drive_organizer(permission: dict) -> bool:
    """True when the grant comes from the Manager role on the Shared Drive."""
    details = permission.get("permissionDetails") or []
    return isinstance(details, list) and any(
        isinstance(detail, dict) and detail.get("role") == "organizer" for detail in details
    )


class DrivePermissionBatch(list):
    """The grants to store. ``saw_permission_details`` is false for My Drive and for a drive root."""

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
    organizer_permissions: list = (),
    inherits_from_drive: bool = True,
) -> list:
    """Shared Drive items store only grants written on the item.

    Limited access still marks some entries inherited. Those people can open
    metadata, not the file, so they are not stored as readers. Managers are the
    exception: they keep access, and the item no longer inherits it from the drive.
    An item filed outside its drive (a drive this connector does not sync) has no
    drive group to take the members from, so it keeps the whole list.
    """
    if saw_permission_details and not is_drive:
        if inherited_permissions_disabled:
            return direct_permissions + [p for p in organizer_permissions if p not in direct_permissions]
        if inherits_from_drive:
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
