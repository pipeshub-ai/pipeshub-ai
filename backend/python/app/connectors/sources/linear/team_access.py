"""Who may read a Linear team's content, as PipesHub permissions.

Linear's rules (linear.app/docs/private-teams, /members-roles):
  * private: the team's members only;
  * public: every workspace member, but not guests, who see only the teams they were
    added to, and not suspended accounts;
  * restricted (a non-private team inside a private team): its members and the members
    of the parent teams up to that private team.

An org-wide grant cannot leave out guests and suspended accounts, so "every workspace
member" is a group the connector keeps: active, non-guest Linear users.
"""

from typing import Any

from app.models.permission import EntityType, Permission, PermissionType

WORKSPACE_MEMBERS_GROUP_ID = "linear-workspace-members"
WORKSPACE_MEMBERS_GROUP_NAME = "Linear workspace members"


def is_full_member(user: dict[str, Any]) -> bool:
    """An active account with an email that is not a guest."""
    return bool(user.get("email")) and user.get("active", True) is not False and not user.get("guest")


def visibility(team: dict[str, Any]) -> str:
    value = str(team.get("visibility") or "").lower()
    if value in ("public", "private", "restricted"):
        return value
    return "private" if team.get("private") else "public"


def boundary_ancestors(team: dict[str, Any], teams_by_id: dict[str, dict[str, Any]]) -> list[str]:
    """Parent team ids from ``team`` up to and including the nearest private one."""
    ids: list[str] = []
    parent_id: str | None = (team.get("parent") or {}).get("id")
    while parent_id and parent_id not in ids:
        ids.append(parent_id)
        parent = teams_by_id.get(parent_id)
        if parent is None or visibility(parent) == "private":
            break
        parent_id = (parent.get("parent") or {}).get("id")
    return ids


def team_permissions(team: dict[str, Any], teams_by_id: dict[str, dict[str, Any]]) -> list[Permission]:
    def group(group_id: str) -> Permission:
        return Permission(entity_type=EntityType.GROUP, external_id=group_id, type=PermissionType.READ)

    permissions = [group(team["id"])]
    kind = visibility(team)
    if kind == "public":
        permissions.append(group(WORKSPACE_MEMBERS_GROUP_ID))
    elif kind == "restricted":
        permissions.extend(group(team_id) for team_id in boundary_ancestors(team, teams_by_id))
    return permissions
