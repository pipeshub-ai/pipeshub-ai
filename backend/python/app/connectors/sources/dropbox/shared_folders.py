"""Dropbox shared folders as record groups: one per folder, synced once, its members as its grants.

A shared folder is its own namespace. Every member sees it, usually mounted at a
path of their own, so syncing it from each member's home listing stored the same
items once per member, with that member's path, group and a grant for them. Here
each shared folder is one record group under its canonical name, granted to its
members; its namespace is listed by one member, and home listings leave it out.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from app.models.permission import EntityType, Permission, PermissionType

if TYPE_CHECKING:
    from collections.abc import Iterable

# Dropbox access levels, strongest first; the member listing a folder should be able to read all of it.
_ACCESS_RANK = {"owner": 0, "editor": 1, "viewer": 2, "viewer_no_comment": 3, "traverse": 4}
_PERMISSION = {"owner": PermissionType.OWNER, "editor": PermissionType.WRITE}


@dataclass
class SharedFolder:
    folder_id: str
    name: str
    parent_id: str | None
    inherits: bool
    is_team_folder: bool
    access: dict[str, str] = field(default_factory=dict)  # team member id -> access level

    @property
    def syncer(self) -> str:
        """The member who lists this folder: the strongest access, then the lowest id, so it stays the same run to run."""
        return min(self.access, key=lambda member: (_ACCESS_RANK.get(self.access[member], 9), member))


def plan_shared_folders(listings: dict[str, Iterable]) -> dict[str, SharedFolder]:
    """Shared folders of the team, from each member's ``sharing/list_folders`` entries."""
    folders: dict[str, SharedFolder] = {}
    for member_id, entries in listings.items():
        for entry in entries:
            folder = folders.get(entry.shared_folder_id)
            if folder is None:
                parent_id = getattr(entry, "parent_shared_folder_id", None)
                inheritance = getattr(getattr(entry, "access_inheritance", None), "_tag", "inherit")
                folder = SharedFolder(
                    folder_id=entry.shared_folder_id,
                    name=entry.name,
                    parent_id=parent_id,
                    inherits=parent_id is not None and inheritance != "no_inherit",
                    is_team_folder=bool(getattr(entry, "is_team_folder", False)),
                )
                folders[entry.shared_folder_id] = folder
            folder.access[member_id] = getattr(getattr(entry, "access_type", None), "_tag", "viewer")
    return folders


def parents_first(folders: dict[str, SharedFolder]) -> list[SharedFolder]:
    """Folders ordered so a nested folder's parent group is written before it."""
    def depth(folder: SharedFolder) -> int:
        seen, node = 0, folder
        while node.parent_id in folders and seen < len(folders):
            node, seen = folders[node.parent_id], seen + 1
        return seen
    return sorted(folders.values(), key=lambda folder: (depth(folder), folder.folder_id))


def folder_grants(users: Iterable, groups: Iterable, invitees: Iterable) -> list[Permission]:
    """A shared folder's own members as grants. Members it inherits stay on the parent folder."""
    grants: list[Permission] = []
    for membership in users:
        user = membership.user
        email = getattr(user, "email", None)
        if getattr(membership, "is_inherited", False) is True or not email or email.endswith("#"):
            continue
        grants.append(Permission(external_id=user.account_id, email=email,
                                 type=_permission(membership), entity_type=EntityType.USER))
    for membership in groups:
        if getattr(membership, "is_inherited", False) is True:
            continue
        grants.append(Permission(external_id=membership.group.group_id, type=_permission(membership),
                                 entity_type=EntityType.GROUP))
    for membership in invitees:
        email = getattr(getattr(membership, "invitee", None), "email", None)
        if getattr(membership, "is_inherited", False) is True or not email:
            continue
        grants.append(Permission(external_id=email, email=email, type=_permission(membership),
                                 entity_type=EntityType.USER))
    return grants


def outside_namespace(entry: object, namespace_id: str | None, shared_folder_ids: set[str]) -> bool:
    """Whether a listed entry belongs to another shared folder than the namespace being listed.

    A home listing (``namespace_id`` None) includes every shared folder the member
    has mounted, and a shared folder's listing includes the folders nested in it.
    Those are listed in their own namespace, so they are left out here: the mount
    point itself (it is a record group) and everything inside it. A member's home
    namespace is never a shared folder of the team listing, so its items stay.
    """
    info = getattr(entry, "sharing_info", None)
    if info is None:
        return False
    own = getattr(info, "shared_folder_id", None)
    if own and own != namespace_id and own in shared_folder_ids:
        return True
    parent = getattr(info, "parent_shared_folder_id", None)
    return bool(parent) and parent != namespace_id and parent in shared_folder_ids


def _permission(membership: object) -> PermissionType:
    return _PERMISSION.get(getattr(getattr(membership, "access_type", None), "_tag", "viewer"), PermissionType.READ)
