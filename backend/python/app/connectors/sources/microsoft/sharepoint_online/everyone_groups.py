"""SharePoint's two tenant-wide claims, as user groups PipesHub can hold members for.

"Everyone" opens a site to every account of the tenant, guests included. "Everyone except external users"
leaves the guests out. Granting either to the PipesHub organization handed it to every PipesHub user,
including guests a PipesHub admin invited and people with no account in the Microsoft tenant at all.
Each claim is instead a group whose members are the tenant's enabled accounts, rewritten from the user list
on every sync, so a guest only gets what "Everyone" grants and a disabled account drops out.
"""

from __future__ import annotations

from app.models.entities import AppUser, AppUserGroup

EVERYONE = "sharepoint-everyone"
EVERYONE_EXCEPT_EXTERNAL = "sharepoint-everyone-except-external"

_NAMES = {EVERYONE: "Everyone", EVERYONE_EXCEPT_EXTERNAL: "Everyone except external users"}


def everyone_group_for_claim(login_name: str) -> str | None:
    """The group a tenant-wide claim stands for, or None for any other login name."""
    if "spo-grid-all-users" in login_name:
        return EVERYONE_EXCEPT_EXTERNAL
    if "c:0(.s|true" in login_name:
        return EVERYONE
    return None


def everyone_groups(connector_name: str, connector_id: str, users: list[AppUser]) -> list[tuple[AppUserGroup, list[AppUser]]]:
    """Both groups with their members: enabled accounts, and for the second one only the tenant's own."""
    enabled = [user for user in users if user.is_active is not False and user.email]
    members = {
        EVERYONE: enabled,
        EVERYONE_EXCEPT_EXTERNAL: [user for user in enabled if not user.is_guest],
    }
    return [
        (
            AppUserGroup(
                source_user_group_id=group_id,
                app_name=connector_name,
                connector_id=connector_id,
                name=_NAMES[group_id],
                description="SharePoint tenant-wide claim",
            ),
            group_members,
        )
        for group_id, group_members in members.items()
    ]
