"""The team's members as one complete list, and what that list says about who may still read.

`team/members/list` returns at most 1000 members per call (team.stone MembersListArg.limit); the rest come from
`members/list/continue`. Reading only the first page left every later member unsynced and without access. The
list includes active, invited and suspended members; removed members are left out unless asked for
(`include_removed`, team.stone).

Only a member whose status is active can open anything in Dropbox ("Suspended team members lose access to their
Dropbox team account, including all files, folders", Dropbox Help). A member who is suspended, invited or no
longer listed keeps no access in PipesHub: their membership of the connector is withdrawn, and saved again when
they are active. Withdrawal needs the complete list: a member missing from a partial one is not gone.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from app.connectors.sources.dropbox.errors import is_auth_error

if TYPE_CHECKING:
    from app.models.entities import AppUser
    from app.sources.external.dropbox.dropbox_ import DropboxDataSource


@dataclass
class TeamMembers:
    raw: list[Any] = field(default_factory=list)
    complete: bool = False
    error: str | None = None
    auth_error: bool = False

    def active_ids(self) -> set[str]:
        return {m.profile.team_member_id for m in self.raw if m.profile.status.is_active()}


async def read_team_members(data_source: DropboxDataSource) -> TeamMembers:
    """Every member of the team, every page; `complete` only when all pages were read."""
    result = TeamMembers()
    try:
        response = await data_source.team_members_list()
    except Exception as e:
        result.error = str(e)
        return result
    while True:
        if not response.success:
            result.error = str(response.error)
            result.auth_error = is_auth_error(response.error)
            return result
        result.raw.extend(response.data.members)
        if response.data.has_more is not True or not response.data.cursor:
            result.complete = True
            return result
        try:
            response = await data_source.team_members_list_continue(response.data.cursor)
        except Exception as e:  # the data source normally answers failures, it does not raise
            result.error = str(e)
            return result


def withdrawn(stored: list[AppUser], members: TeamMembers) -> list[AppUser]:
    """Stored members of the connector who may read nothing any more: not active, or gone from the team."""
    if not members.complete:
        return []
    active = members.active_ids()
    return [user for user in stored if user.source_user_id and user.source_user_id.startswith("dbmid:")
            and user.source_user_id not in active]
