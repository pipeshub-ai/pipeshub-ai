"""An agent's owner team (02-lld §3.1): the team whose layer narrows the agent's runs and whose admins may manage it.

Set by an org admin, or by an OWNER of that team (a team admin may hand an agent only to a team they run). The
actor's own teams never become the agent's team by themselves.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Final

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

OWNER_TEAM_FIELD: Final = "ownerTeam"
TEAM_ADMIN_ROLE: Final = "OWNER"
_TEAM_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")


class OwnerTeamRefused(Exception):
    """The owner team asked for is not one this person may give the agent."""

    def __init__(self, reason_code: str, message: str) -> None:
        super().__init__(message)
        self.reason_code = reason_code


async def owner_team_of(
    raw: object,
    *,
    team_in_org: Callable[[str], Awaitable[bool]],
    is_org_admin: Callable[[], Awaitable[bool]],
    team_role: Callable[[str], Awaitable[str | None]],
) -> str | None:
    """The owner team a create or update asks for: ``None`` clears it; a team of the org needs an org admin or its OWNER."""
    if raw is None:
        return None
    if not isinstance(raw, str) or not _TEAM_ID.match(raw):
        raise OwnerTeamRefused("owner_team_invalid", "ownerTeam must be a team id or null")
    if not await team_in_org(raw):
        raise OwnerTeamRefused("owner_team_unknown", "ownerTeam names no team of this organization")
    if await is_org_admin():
        return raw
    if await team_role(raw) == TEAM_ADMIN_ROLE:
        return raw
    raise OwnerTeamRefused("owner_team_forbidden", "only an org admin or an owner of the team can make it the agent's team")
