"""Who may read a Zammad group's tickets, by Zammad's own rule.

Zammad lets an agent read a ticket when the agent is active, holds ``ticket.agent``,
the ticket's group is active, and the agent has ``read`` or ``full`` access to that
group, either directly or through an active role (``HasGroups#group_access?`` and
``HasRoles#role_access?``). ``change``, ``create`` and ``overview`` do not imply
``read``. A group's ``user_ids`` lists everyone with a direct access row of any level,
so it is not the list of readers.

This module turns the user and role listings into that rule. It reads nothing itself.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import cast

# Access levels that let an agent read a group's tickets (Zammad adds "full" to any check).
READ_ACCESS_LEVELS = frozenset({"read", "full"})
# The permission an agent's group access needs before it counts (HasGroups#groups_access_permission?).
AGENT_PERMISSION = "ticket.agent"
# Permissions that make a user staff of the help desk rather than one of its customers.
STAFF_PERMISSION_PREFIXES = ("admin", "ticket.agent", "knowledge_base")


def group_levels(group_ids: object) -> dict[str, frozenset[str]]:
    """``group_ids`` as Zammad sends it ({"<group id>": ["read", ...]}), keyed by group id string.

    A level may come as one string or a list. Anything else (the plain id list of an
    old shape) gives no levels, so it grants nothing.
    """
    if not isinstance(group_ids, Mapping):
        return {}
    levels: dict[str, frozenset[str]] = {}
    for group_id, access in cast(Mapping[object, object], group_ids).items():
        names = _strings([access] if isinstance(access, str) else access)
        if names is not None:
            levels[str(group_id)] = frozenset(names)
    return levels


def _strings(value: object) -> list[str] | None:
    """The strings in a list or other iterable (not a string or a map), or None for anything else."""
    if isinstance(value, (str, bytes, Mapping)) or not isinstance(value, Iterable):
        return None
    return [item for item in cast(Iterable[object], value) if isinstance(item, str)]


def _grants_agent(permissions: Iterable[str]) -> bool:
    # A permission is granted by itself or by a parent ("ticket" grants "ticket.agent").
    return any(p == AGENT_PERMISSION or AGENT_PERMISSION.startswith(f"{p}.") for p in permissions)


def _grants_staff(permissions: Iterable[str]) -> bool:
    return any(
        p == prefix or p.startswith(f"{prefix}.") or prefix.startswith(f"{p}.")
        for p in permissions for prefix in STAFF_PERMISSION_PREFIXES
    )


@dataclass(frozen=True)
class RoleAccess:
    """What one Zammad role contributes to ticket access."""

    active: bool
    agent: bool
    groups: dict[str, frozenset[str]] = field(default_factory=dict[str, frozenset[str]])
    # Grants an admin, agent or knowledge-base permission: its users are staff, not customers.
    staff: bool = False

    @classmethod
    def from_role(cls, role: Mapping[str, object]) -> "RoleAccess":
        """From a role of ``GET /roles?expand=true`` (``permissions`` holds the permission names)."""
        names = _strings(role.get("permissions")) or []
        return cls(
            active=role.get("active", True) is not False,
            agent=_grants_agent(names),
            groups=group_levels(role.get("group_ids")),
            staff=_grants_staff(names),
        )


@dataclass(frozen=True)
class UserAccess:
    """What one Zammad user brings to ticket access: whether active, their roles, their own group rows."""

    active: bool
    role_ids: frozenset[int]
    groups: dict[str, frozenset[str]] = field(default_factory=dict[str, frozenset[str]])

    @classmethod
    def from_user(cls, user: Mapping[str, object]) -> "UserAccess":
        role_ids: set[int] = set()
        raw = user.get("role_ids")
        for role_id in cast(list[object], raw) if isinstance(raw, list) else []:
            if isinstance(role_id, int) and not isinstance(role_id, bool):
                role_ids.add(role_id)
            elif isinstance(role_id, str) and role_id.isdigit():
                role_ids.add(int(role_id))
        return cls(
            active=user.get("active", True) is not False,
            role_ids=frozenset(role_ids),
            groups=group_levels(user.get("group_ids")),
        )


def is_external(user: UserAccess, roles: Mapping[int, RoleAccess]) -> bool:
    """Whether a user is only a customer of the help desk: no active role of theirs is a staff role.

    PipesHub registers such a user as a guest, so a synced customer is not made a
    member of the organisation (and handed every organisation-wide grant).
    """
    return not any((role := roles.get(role_id)) and role.active and role.staff for role_id in user.role_ids)


class GroupReaders:
    """Answers "who may read the tickets of group G" from what the listings said.

    ``roles`` maps role id to its access; a role missing from it grants nothing,
    so a user whose roles could not be read reads nothing (access fails closed).
    """

    def __init__(self, users: Mapping[int, UserAccess], roles: Mapping[int, RoleAccess]) -> None:
        super().__init__()
        self._users = users
        self._roles = roles

    def _active_roles(self, user: UserAccess) -> list[RoleAccess]:
        return [role for role_id in user.role_ids if (role := self._roles.get(role_id)) and role.active]

    def is_agent(self, user_id: int) -> bool:
        user = self._users.get(user_id)
        return bool(user and user.active and any(role.agent for role in self._active_roles(user)))

    def readers(self, group_id: str | int, *, group_active: bool) -> set[int]:
        """Ids of the users who may read the group's tickets as agents."""
        if not group_active:
            return set()
        key = str(group_id)
        readers: set[int] = set()
        for user_id, user in self._users.items():
            if not user.active:
                continue
            roles = self._active_roles(user)
            if not any(role.agent for role in roles):
                continue
            direct = user.groups.get(key, frozenset())
            through_roles = (role.groups.get(key, frozenset()) for role in roles)
            if direct & READ_ACCESS_LEVELS or any(levels & READ_ACCESS_LEVELS for levels in through_roles):
                readers.add(user_id)
        return readers
