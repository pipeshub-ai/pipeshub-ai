# Copyright (c) 2026 PipesHub Inc.
# Licensed under the PipesHub Enterprise License. See the LICENSE file in this
# ee folder. Free for development, testing and evaluation. Production use needs
# a PipesHub licence key.

"""Linear: reading who may see what from Linear.

Team members, the parent teams a restricted team's readers come from, the grants a team's
visibility gives (``linear_team_access``) and the workspace-members group. The free connector
keeps listing teams and syncing their content and calls these methods; without a licence key a
company-wide connector reads none of this (the processor's licence gate).
"""

from typing import Any
from uuid import uuid4

from app.config.constants.arangodb import Connectors
from app.connectors.sources.linear.api_gateway import NESTED_PAGE_SIZE, TEAM_PAGE_SIZE
from app.ee.permission_sync.linear_team_access import (
    WORKSPACE_MEMBERS_GROUP_ID,
    WORKSPACE_MEMBERS_GROUP_NAME,
    boundary_ancestors,
    team_permissions,
    visibility,
)
from app.models.entities import AppUser, AppUserGroup
from app.models.permission import Permission


class LinearPermissionsMixin:
    """The permission-reading half of ``LinearConnector``."""

    async def _copies_no_source_access(self) -> bool:
        """Without a licence key a company-wide connector reads no access from Linear. The processor then keeps
        every stored grant, group and membership whatever is handed to it (``skips_permission_writes``), so the
        empty answers below only keep the run whole: teams are still written as record groups."""
        return await self.data_entities_processor.skips_permission_writes(self.connector_id)

    async def _read_team_members(self, team: dict[str, Any]) -> None:
        """Read a team's members past the 10 Linear lists inline; they become the team's user group."""
        if await self._copies_no_source_access():
            return
        await self._api.complete(team, "members", "teamMembers", ["team", "members"],
                                 page_size=NESTED_PAGE_SIZE)

    async def _team_grants(self, team: dict[str, Any], teams_by_id: dict[str, dict[str, Any]]) -> list[Permission]:
        """Who may read a team's content (``team_permissions``); nobody new while unlicensed."""
        if await self._copies_no_source_access():
            return []
        return team_permissions(team, teams_by_id)

    def _workspace_members_group(
        self, user_email_map: dict[str, AppUser]
    ) -> tuple[AppUserGroup, list[AppUser]]:
        """The group public teams are granted to: active accounts that are not guests."""
        group = AppUserGroup(
            id=str(uuid4()),
            org_id=self.data_entities_processor.org_id,
            source_user_group_id=WORKSPACE_MEMBERS_GROUP_ID,
            connector_id=self.connector_id,
            app_name=Connectors.LINEAR,
            name=WORKSPACE_MEMBERS_GROUP_NAME,
            description="Everyone in the Linear workspace except guests",
        )
        members = [user for email, user in user_email_map.items() if email in self._full_member_emails]
        return group, members

    async def _add_missing_ancestor_teams(self, teams_by_id: dict[str, dict[str, Any]]) -> None:
        """Fetch the parent teams restricted teams need but the team filter left out."""
        if await self._copies_no_source_access():
            return
        for _ in range(10):  # parent chains are short; this bounds a malformed one
            missing = sorted({
                ancestor
                for team in list(teams_by_id.values()) if visibility(team) == "restricted"
                for ancestor in boundary_ancestors(team, teams_by_id)
                if ancestor not in teams_by_id
            })
            if not missing:
                return
            found: list[dict[str, Any]] = []
            async for teams_list, _, _ in self._api.pages(
                "parent teams", ["teams"], page_size=TEAM_PAGE_SIZE,
                fetch=lambda ds, first, after, ids=missing: ds.teams(first=first, after=after, filter={"id": {"in": ids}}),
            ):
                for team in teams_list:
                    await self._api.complete(team, "members", "teamMembers", ["team", "members"],
                                             page_size=NESTED_PAGE_SIZE)
                found.extend(teams_list)
            found = [t for t in found if t.get("id") in missing]
            if not found:
                return  # gone or invisible to this account: nobody is granted through them
            teams_by_id.update({t["id"]: t for t in found})
