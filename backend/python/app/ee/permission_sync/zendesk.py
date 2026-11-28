# Copyright (c) 2026 PipesHub Inc.
# Licensed under the PipesHub Enterprise License. See the LICENSE file in this
# ee folder. Free for development, testing and evaluation. Production use needs
# a PipesHub licence key.

"""Zendesk: reading who may see what from Zendesk.

Ticket access, from each agent's access level and custom role, the ticket groups' members, the
organizations and their sharing, and the people a ticket names; Zendesk's per-person, all-tickets,
public-groups and per-organization groups are built from it. The free connector keeps listing
and syncing ticket groups, tickets and the Help Center and calls these methods; without a licence
key a company-wide connector reads none of this (the processor's licence gate).
"""

# Zendesk API responses contain fields whose types vary by endpoint and account.
# ruff: noqa: ANN401

from __future__ import annotations

from collections import defaultdict
from functools import partial
from typing import TYPE_CHECKING, Any

import httpx

from app.config.constants.arangodb import Connectors
from app.config.constants.http_status_code import HttpStatusCode
from app.models.entities import AppUser, AppUserGroup
from app.models.permission import EntityType, Permission, PermissionType
from app.sources.client.http.http_retry import call_with_retry
from app.utils.time_conversion import get_epoch_timestamp_in_ms

if TYPE_CHECKING:
    from app.sources.external.zendesk.zendesk import ZendeskDataSource

USERS_SYNC_POINT_KEY = "zendesk_users_incremental"
# Agents, admins, groups and memberships are always exported from here, never from a
# checkpoint. on_new_user_groups deletes every membership edge before re-adding from
# the list it is given, so the list has to be the whole truth — a window cannot tell
# "unchanged" from "removed", and guessing wrong revokes real access. Only the record
# stages (tickets, articles) resume from a sync point.
DEFAULT_INCREMENTAL_START_TIME = 1
# Zendesk rejects an incremental start_time inside the last minute.
INCREMENTAL_SAFETY_LAG_SECONDS = 60
# Admins and agents whose role grants ticket_access "all" read every ticket in Zendesk
# whatever its group, so they cannot be derived from group membership.
ALL_TICKETS_GROUP_ID = "role_all_tickets"
ALL_TICKETS_ACCESS = "all"
# Custom-role ticket_access "within-groups-and-public-groups": Zendesk treats these
# agents as members of every public group and lets them read unassigned tickets.
PUBLIC_GROUPS_ACCESS = "public_groups"
PUBLIC_GROUPS_GROUP_ID = "role_public_groups"
# Per-person and per-organization permission groups. A ticket grants the group, and the
# group's members are rewritten every sync, so a change to the person (suspension, a new
# access level, another organization) takes effect without the ticket changing.
# Staff a ticket names as CC or follower, or as requester once the requester may see it.
NAMED_STAFF_GROUP_PREFIX = "named_"
# A ticket's assignee, while their access is "assigned tickets only".
ASSIGNED_STAFF_GROUP_PREFIX = "assigned_"
# Agents whose access is "tickets in their organization", per organization. Not "org_":
# that prefix is the organization's customers.
ORG_AGENTS_GROUP_PREFIX = "orgagents_"
# Earlier per-staff groups; emptied every sync so grants written with them stay inert.
RETIRED_STAFF_GROUP_PREFIX = "staff_"
ASSIGNED_ONLY_ACCESS = frozenset({"assigned", "assigned_only"})
ORGANIZATION_ACCESS = frozenset({"organization", "organization_only"})
# Custom roles exist on Enterprise plans only; other plans answer with one of these.
CUSTOM_ROLES_UNAVAILABLE_STATUSES = frozenset({403, 404})


class ZendeskPermissionsMixin:
    """The permission-reading half of ``ZendeskConnector``."""

    async def _copies_no_source_access(self) -> bool:
        """Without a licence key a company-wide connector reads no access from Zendesk. The processor then keeps
        every stored grant, group and member, whatever is handed to it (``skips_permission_writes``), so the answers
        below only keep the run whole: lists read in full and empty, tickets with no grants of their own, and no
        users revoked. Reported as unread, every group and organization would hold the tickets back."""
        return await self.data_entities_processor.skips_permission_writes(self.connector_id)

    async def _ticket_group_members(
        self, datasource: ZendeskDataSource, user_email_map: dict[str, AppUser]
    ) -> tuple[dict[str, list[AppUser]], list[AppUser], bool]:
        """Who reads each ticket group's tickets: its members whose access level reaches it, and the agents who
        read every public group. Third value: whether the membership list was read to the end."""
        if await self._copies_no_source_access():
            return {}, [], True
        memberships, memberships_complete = await self._fetch_paginated_list_checked(
            datasource.list_group_memberships,
            "group_memberships",
        )
        members_by_group: dict[str, list[AppUser]] = defaultdict(list)
        for membership in memberships:
            group_id = str(membership.get("group_id", ""))
            user_id = str(membership.get("user_id", ""))
            user = user_email_map.get(user_id)
            user_data = self._user_id_to_data.get(user_id, {})
            if group_id and user and self._has_group_ticket_access(
                user_data, self._role_ticket_access
            ):
                members_by_group[group_id].append(user)
        public_groups_agents = self._public_groups_agents(user_email_map)
        return members_by_group, public_groups_agents, memberships_complete

    async def _sync_ticket_access_groups(
        self,
        group_user_groups: list[tuple[AppUserGroup, list[AppUser]]],
        *,
        users_complete: bool,
        memberships_complete: bool,
    ) -> None:
        """Write the ticket groups' members, the all-tickets and public-groups groups, and each staff member's own
        groups, from the user, membership and custom role lists read this sync."""
        if await self._copies_no_source_access():
            return
        # Without the roles an agent's access is unknown, so memberships are only added
        # to: a rebuild would drop every custom-role agent until the next good sync.
        replace_members = self._roles_complete
        if users_complete and memberships_complete:
            group_user_groups = await self._append_empty_removed_user_groups(
                group_user_groups,
                prefixes=("group_",),
            )
            if group_user_groups:
                await self.data_entities_processor.on_new_user_groups(
                    group_user_groups, replace_members=replace_members
                )
        elif group_user_groups:
            self._skip("group memberships")
            self.logger.error(
                "Zendesk: skipping group membership sync — the %s export was truncated "
                "and on_new_user_groups would rebuild each group from partial data",
                "user" if not users_complete else "group membership",
            )
        all_access_group, roles_complete = await self._build_all_tickets_group(
            self._role_ticket_access, roles_complete=self._roles_complete
        )
        if users_complete:
            person_groups = await self._append_empty_removed_user_groups(
                self._build_person_groups(),
                prefixes=(
                    NAMED_STAFF_GROUP_PREFIX,
                    ASSIGNED_STAFF_GROUP_PREFIX,
                    RETIRED_STAFF_GROUP_PREFIX,
                ),
            )
            role_based: list[tuple[AppUserGroup, list[AppUser]]] = []
            role_free: list[tuple[AppUserGroup, list[AppUser]]] = []
            for entry in person_groups:
                assigned = entry[0].source_user_group_id.startswith(ASSIGNED_STAFF_GROUP_PREFIX)
                (role_based if assigned else role_free).append(entry)
            await self.data_entities_processor.on_new_user_groups(
                [all_access_group, self._build_public_groups_group(), *role_based],
                replace_members=replace_members,
            )
            # Who may sign in needs no roles, so these are rebuilt even without them.
            await self.data_entities_processor.on_new_user_groups(role_free, replace_members=True)
        else:
            self._skip("agent access")
            self.logger.error(
                "Zendesk: skipping all-tickets access group — the user export was "
                "truncated and the group is rebuilt from scratch on every write"
            )
        if not replace_members:
            self._skip("custom role access", left_unread=False)
            self.logger.error(
                "Zendesk: custom role export failed — keeping existing group and "
                "all-tickets members, adding only agents whose access is known"
            )

    async def _sync_organizations(self, *, users_complete: bool) -> tuple[int, bool]:
        """Write each organization's groups: its organization-limited agents, and its customers while it shares
        tickets. Returns how many organizations were read and whether the export was read to the end."""
        if await self._copies_no_source_access():
            return 0, True
        replace_members = self._roles_complete
        org_user_groups, orgs_complete = await self._fetch_organizations()
        if orgs_complete:
            org_user_groups = await self._add_organizations_missing_from_export(org_user_groups)
        if users_complete:
            # Before the tickets: a grant to a group that does not exist yet is dropped.
            org_ids = sorted(
                group.source_user_group_id.removeprefix("org_") for group, _ in org_user_groups
            )
            org_agent_groups = await self._append_empty_removed_user_groups(
                self._build_org_agent_groups(org_ids), prefixes=(ORG_AGENTS_GROUP_PREFIX,)
            )
            if org_agent_groups:
                await self.data_entities_processor.on_new_user_groups(
                    org_agent_groups, replace_members=replace_members
                )
        if org_user_groups:
            shared_org_groups: list[AppUserGroup] = []
            emptied_org_groups = []
            for group, _ in org_user_groups:
                org_id = group.source_user_group_id.removeprefix("org_")
                if self._org_data_shares_tickets(self._org_id_to_data.get(org_id)):
                    shared_org_groups.append(group)
                else:
                    emptied_org_groups.append((group, []))
            if emptied_org_groups:
                await self.data_entities_processor.on_new_user_groups(
                    emptied_org_groups, replace_members=True
                )
            await self._sync_shared_org_members(shared_org_groups)
        if not orgs_complete:
            self._skip("organizations")
            self.logger.error(
                "Zendesk: organization export was truncated — organizations past the "
                "break have no group, so their org-wide ticket grant is withheld"
            )
        self.logger.info(f"Zendesk: synced {len(org_user_groups)} organizations")
        return len(org_user_groups), orgs_complete

    async def _append_empty_removed_user_groups(
        self,
        current_groups: list[tuple[AppUserGroup, list[AppUser]]],
        *,
        prefixes: tuple[str, ...],
    ) -> list[tuple[AppUserGroup, list[AppUser]]]:
        """Keep removed Zendesk groups as empty nodes so stale grants are withdrawn."""
        current_ids = {group.source_user_group_id for group, _ in current_groups}
        async with self.data_store_provider.transaction() as tx_store:
            stored_groups = await tx_store.get_user_groups(
                self.connector_id,
                self.data_entities_processor.org_id,
            )
        for old_group in stored_groups:
            external_id = old_group.source_user_group_id
            if (
                external_id.startswith(prefixes)
                and external_id not in current_ids
            ):
                current_groups.append((old_group, []))
        return current_groups

    async def _fetch_custom_role_ticket_access(self) -> tuple[dict[str, str], bool]:
        """Map custom role id -> ticket_access, plus whether the list is trustworthy.

        Plans without custom roles answer 403/404 and have no agent carrying a
        custom_role_id either, so that is an empty-but-complete answer. Anything else
        is a truncated export: the all-tickets group is rebuilt from scratch from it.
        """
        if await self._copies_no_source_access():
            return {}, True
        datasource = await self._get_fresh_datasource()
        try:
            response = await call_with_retry(
                partial(self._call_api, datasource.list_custom_roles),
                logger=self.logger,
                label="zendesk/list_custom_roles",
            )
        except httpx.HTTPStatusError as e:
            self.logger.error(f"Zendesk list_custom_roles gave up after retries: {e}")
            return {}, False
        if response.status_code in CUSTOM_ROLES_UNAVAILABLE_STATUSES:
            self.logger.info(
                "Zendesk: custom roles unavailable on this plan (HTTP %s) — only the "
                "built-in admin and agent roles apply", response.status_code,
            )
            return {}, True
        if not response.success:
            self.logger.error(f"Zendesk list_custom_roles failed: {response.error}")
            return {}, False
        if not isinstance(response.data, dict) or not isinstance(
            response.data.get("custom_roles"), list
        ):
            self.logger.error("Zendesk list_custom_roles omitted custom_roles")
            return {}, False
        access: dict[str, str] = {}
        for role in self._extract_list(response.data, "custom_roles"):
            role_id = role.get("id")
            if role_id is None:
                continue
            ticket_access = (role.get("configuration") or {}).get("ticket_access")
            access[str(role_id)] = str(ticket_access or "")
        return access, True

    def _has_all_tickets_access(
        self, user_data: dict[str, Any], role_ticket_access: dict[str, str]
    ) -> bool:
        """Whether Zendesk lets this user read every ticket regardless of group."""
        if user_data.get("active") is False or user_data.get("suspended"):
            return False
        role = user_data.get("role")
        if role == "admin":
            return True
        if role != "agent":
            return False
        ticket_access = self._effective_ticket_access(user_data, role_ticket_access)
        return ticket_access == ALL_TICKETS_ACCESS

    def _effective_ticket_access(
        self, user_data: dict[str, Any], role_ticket_access: dict[str, str]
    ) -> str:
        """Resolve role access while honoring a more restrictive agent override."""
        aliases = {
            "within-groups": "groups",
            "within_groups": "groups",
            "within-groups-and-public-groups": PUBLIC_GROUPS_ACCESS,
            "within-organization": "organization",
            "assigned-only": "assigned",
            "requested-only": "requested",
            "organization-only": "organization",
        }
        # Zendesk reports the agent's own limit as ticket_restriction; null is unrestricted.
        own_access = str(user_data.get("ticket_restriction") or "").lower()
        own_access = aliases.get(own_access, own_access)
        custom_role_id = user_data.get("custom_role_id")
        if custom_role_id is None:
            role_access = ALL_TICKETS_ACCESS
        else:
            role_access = str(role_ticket_access.get(str(custom_role_id)) or "").lower()
            role_access = aliases.get(role_access, role_access)
            if not role_access:
                if own_access in {
                    "assigned", "assigned_only", "requested", "groups",
                    "organization", "organization_only",
                }:
                    return own_access
                # Unknown, not "groups": the role may be assigned-only. Existing
                # memberships survive because the failed export makes writes additive.
                return ""
        access_rank = {
            "assigned": 0,
            "assigned_only": 0,
            "requested": 0,
            "organization": 1,
            "organization_only": 1,
            "groups": 1,
            PUBLIC_GROUPS_ACCESS: 2,
            "all": 3,
        }
        # The agent's own field has no public-groups value: such an agent reports
        # "groups", which narrows nothing.
        if role_access == PUBLIC_GROUPS_ACCESS and own_access == "groups":
            return PUBLIC_GROUPS_ACCESS
        values = [value for value in (role_access, own_access) if value in access_rank]
        if not values:
            return role_access
        return min(values, key=access_rank.__getitem__)

    def _has_group_ticket_access(
        self, user_data: dict[str, Any], role_ticket_access: dict[str, str]
    ) -> bool:
        if user_data.get("active") is False or user_data.get("suspended"):
            return False
        access = self._effective_ticket_access(user_data, role_ticket_access)
        return access in {"all", "groups", PUBLIC_GROUPS_ACCESS}

    def _has_public_groups_access(
        self, user_data: dict[str, Any], role_ticket_access: dict[str, str]
    ) -> bool:
        if user_data.get("active") is False or user_data.get("suspended"):
            return False
        return self._effective_ticket_access(user_data, role_ticket_access) == PUBLIC_GROUPS_ACCESS

    async def _build_all_tickets_group(
        self,
        role_ticket_access: dict[str, str] | None = None,
        *,
        roles_complete: bool | None = None,
    ) -> tuple[tuple[AppUserGroup, list[AppUser]], bool]:
        """One group of every admin and all-access agent, granted on every ticket.

        Always returned, even when empty, so the grant on tickets never points at a
        group that does not exist. Built from the agents and admins fetched this sync.
        """
        if role_ticket_access is None or roles_complete is None:
            role_ticket_access, complete = await self._fetch_custom_role_ticket_access()
        else:
            complete = roles_complete
        members = [
            app_user
            for user_id, app_user in self._user_id_to_app_user.items()
            if self._has_all_tickets_access(
                self._user_id_to_data.get(user_id, {}), role_ticket_access
            )
        ]
        group = AppUserGroup(
            app_name=Connectors.ZENDESK,
            connector_id=self.connector_id,
            source_user_group_id=ALL_TICKETS_GROUP_ID,
            name="Zendesk: all tickets access",
            org_id=self.data_entities_processor.org_id,
        )
        self.logger.info(f"Zendesk: {len(members)} users have all-tickets access")
        return (group, members), complete

    def _public_groups_agents(self, users: dict[str, AppUser]) -> list[AppUser]:
        return [
            app_user
            for user_id, app_user in users.items()
            if self._has_public_groups_access(
                self._user_id_to_data.get(user_id, {}), self._role_ticket_access
            )
        ]

    def _build_public_groups_group(self) -> tuple[AppUserGroup, list[AppUser]]:
        """Agents who may read unassigned tickets; always returned so the grant resolves."""
        group = AppUserGroup(
            app_name=Connectors.ZENDESK,
            connector_id=self.connector_id,
            source_user_group_id=PUBLIC_GROUPS_GROUP_ID,
            name="Zendesk: public groups access",
            org_id=self.data_entities_processor.org_id,
        )
        return group, self._public_groups_agents(self._user_id_to_app_user)

    def _build_person_groups(self) -> list[tuple[AppUserGroup, list[AppUser]]]:
        """Two permission groups per staff member, granted by the tickets that name them.

        ``named_`` holds them while they can sign in; ``assigned_`` while their access is
        "assigned tickets only". Both are written for every staff member, empty or not,
        so a ticket's grant always lands and only membership has to change later.
        """
        groups: list[tuple[AppUserGroup, list[AppUser]]] = []
        for user_id, app_user in self._user_id_to_app_user.items():
            user_data = self._user_id_to_data.get(user_id, {})
            active = user_data.get("active") is not False and not user_data.get("suspended")
            assigned_only = active and self._effective_ticket_access(
                user_data, self._role_ticket_access
            ) in ASSIGNED_ONLY_ACCESS
            name = user_data.get("name") or user_id
            groups.append((
                self._person_group(f"{NAMED_STAFF_GROUP_PREFIX}{user_id}", f"Zendesk: {name}"),
                [app_user] if active else [],
            ))
            groups.append((
                self._person_group(
                    f"{ASSIGNED_STAFF_GROUP_PREFIX}{user_id}", f"Zendesk: assigned to {name}"
                ),
                [app_user] if assigned_only else [],
            ))
        return groups

    def _build_org_agent_groups(self, org_ids: list[str]) -> list[tuple[AppUserGroup, list[AppUser]]]:
        """One group per organization holding its organization-limited agents.

        Zendesk lets such an agent read the tickets requested by end users in their
        organization, whatever the organization's ticket sharing says (that setting is
        for end users).
        """
        members: dict[str, list[AppUser]] = defaultdict(list)
        for user_id, app_user in self._user_id_to_app_user.items():
            user_data = self._user_id_to_data.get(user_id, {})
            if user_data.get("active") is False or user_data.get("suspended"):
                continue
            if self._effective_ticket_access(
                user_data, self._role_ticket_access
            ) in ORGANIZATION_ACCESS and user_data.get("organization_id"):
                members[str(user_data["organization_id"])].append(app_user)
        return [
            (
                self._person_group(
                    f"{ORG_AGENTS_GROUP_PREFIX}{org_id}",
                    f"Zendesk: agents of {(self._org_id_to_data.get(org_id) or {}).get('name') or org_id}",
                ),
                members.get(org_id, []),
            )
            # An agent's organization may be missing from a cut-short export.
            for org_id in sorted(set(org_ids) | members.keys())
        ]

    async def _fetch_organizations(self) -> tuple[list[tuple[AppUserGroup, list[AppUser]]], bool]:
        """Sync Zendesk organizations as user groups.

        Members here are only the agents and admins already fetched. End users join
        their organization's group as the ticket pages sideload them
        (``_add_sideloaded_org_members``), since listing them all is what this avoids.
        Not RecordGroups: nothing files a record under one, so they would render empty;
        tickets carry the org permission.

        The second return value flags a truncated export.
        """
        orgs_data: list[dict[str, Any]] = []
        start_time = DEFAULT_INCREMENTAL_START_TIME
        complete = True
        while True:
            response = await self._call_incremental(
                "incremental_organizations",
                start_time=start_time,
            )
            if response is None or not response.success:
                error = response.error if response else "retries exhausted"
                self.logger.error(f"Zendesk incremental_organizations failed: {error}")
                complete = False
                break
            if not response.data:
                self.logger.error(
                    "Zendesk incremental_organizations returned no usable payload"
                )
                complete = False
                break
            if not isinstance(response.data, dict) or not isinstance(
                response.data.get("organizations"), list
            ):
                self.logger.error(
                    "Zendesk incremental_organizations omitted organizations"
                )
                complete = False
                break
            payload = response.data
            orgs_data.extend(self._extract_list(payload, "organizations"))
            end_time = payload.get("end_time")
            if payload.get("end_of_stream", True):
                break
            # More pages remain but a stalled end_time cannot reach them, so the list is short.
            if not end_time or end_time <= start_time:
                self.logger.error(
                    "Zendesk incremental_organizations stopped advancing before end_of_stream"
                )
                complete = False
                break
            start_time = end_time

        members_by_org: dict[str, list[AppUser]] = defaultdict(list)

        user_groups: list[tuple[AppUserGroup, list[AppUser]]] = []
        for org_data in orgs_data:
            org_id = org_data.get("id")
            if not org_id:
                continue
            org_id = str(org_id)
            self._org_id_to_data[org_id] = org_data
            user_groups.append((self._org_user_group(org_id), members_by_org.get(org_id, [])))

        return user_groups, complete

    async def _sync_shared_org_members(self, groups: list[AppUserGroup]) -> None:
        """Read every customer of each ticket-sharing organization in this sync.

        A colleague who never raised a ticket is never sideloaded by a ticket page, so
        waiting for one would leave them without the organization's tickets.
        """
        if not groups:
            return
        datasource = await self._get_fresh_datasource()
        complete_groups: list[tuple[AppUserGroup, list[AppUser]]] = []
        partial_groups: list[tuple[AppUserGroup, list[AppUser]]] = []
        emails: set[str] = set()
        for group in groups:
            org_id = group.source_user_group_id.removeprefix("org_")
            users_data, complete = await self._fetch_paginated_list_checked(
                datasource.list_organization_users,
                "users",
                organization_id=int(org_id),
            )
            members: list[AppUser] = []
            for user_data in users_data:
                if not self._is_signed_in_customer(user_data):
                    continue
                app_user = self._to_app_user(user_data)
                if app_user:
                    members.append(app_user)
                    emails.add(app_user.email)
            # A truncated list must not drop members another page or sync added.
            (complete_groups if complete else partial_groups).append((group, members))
        if complete_groups:
            await self.data_entities_processor.on_new_user_groups(
                complete_groups, replace_members=True
            )
        if partial_groups:
            await self.data_entities_processor.on_new_user_groups(
                partial_groups, replace_members=False
            )
        if emails:
            await self.data_entities_processor.on_external_app_users(
                sorted(emails), self.connector_id
            )

    async def _keep_organization_group(self, org_id: str) -> None:
        """Keep an unanswered organization's group through a full sync's unlink.

        The org is also recorded so a ticket that already had its org_ grant keeps it
        this sync.

        A failed keep must not stop the tickets and Help Center: the run is reported
        incomplete instead, so nothing is unlinked and a full sync stays owed.
        """
        self._unanswered_org_ids.add(org_id)
        try:
            await self.data_entities_processor.note_kept_user_group(
                self.connector_id, f"org_{org_id}"
            )
        except Exception as e:
            self._skip("organization groups")
            self.logger.warning(
                f"Zendesk: could not keep the group of organization {org_id}; "
                f"this full sync unlinks nothing: {e}"
            )

    async def _add_organizations_missing_from_export(
        self, org_user_groups: list[tuple[AppUserGroup, list[AppUser]]]
    ) -> list[tuple[AppUserGroup, list[AppUser]]]:
        """Settle each organization that has groups here but is not in the export.

        Absence alone is not deletion: an organization edited moments ago can be missing
        from a complete export, and emptying its group would cut its customers off until
        the next sync. Zendesk is asked about each one. Gone: its groups are deleted,
        which also takes their grant off every ticket. Still there: it is synced like the
        rest. No answer: left as it is.
        """
        exported = {group.source_user_group_id.removeprefix("org_") for group, _ in org_user_groups}
        async with self.data_store_provider.transaction() as tx_store:
            stored = await tx_store.get_user_groups(
                self.connector_id, self.data_entities_processor.org_id
            )
        missing = sorted({
            group.source_user_group_id.removeprefix("org_").removeprefix(ORG_AGENTS_GROUP_PREFIX)
            for group in stored
            if group.source_user_group_id.startswith(("org_", ORG_AGENTS_GROUP_PREFIX))
        } - exported)
        if not missing:
            return org_user_groups
        datasource = await self._get_fresh_datasource()
        for org_id in missing:
            if not org_id.isdigit():
                continue
            try:
                response = await self._call_api_with_retry(
                    datasource.show_organization,
                    "zendesk/show_organization",
                    organization_id=int(org_id),
                )
            except Exception as e:
                self.logger.warning(f"Zendesk: could not check organization {org_id}: {e}")
                await self._keep_organization_group(org_id)
                continue
            if response.status_code == HttpStatusCode.NOT_FOUND.value:
                for external_id in (f"org_{org_id}", f"{ORG_AGENTS_GROUP_PREFIX}{org_id}"):
                    await self.data_entities_processor.on_user_group_deleted(
                        external_id, self.connector_id
                    )
                continue
            org_data = (
                self._extract_required_object(response.data, "organization")
                if response.success else {}
            )
            if str(org_data.get("id")) != org_id:
                self.logger.warning(
                    f"Zendesk: organization {org_id} missing from the export and unreadable"
                )
                await self._keep_organization_group(org_id)
                continue
            self._org_id_to_data[org_id] = org_data
            org_user_groups.append((self._org_user_group(org_id), []))
        return org_user_groups

    @staticmethod
    def _is_signed_in_customer(user_data: dict[str, Any]) -> bool:
        """An end user who can sign in. Organization sharing is an end-user rule: an
        agent's reach comes from their access level, never from their organization."""
        return (
            user_data.get("role") == "end-user"
            and user_data.get("active") is not False
            and not user_data.get("suspended")
        )

    def _org_user_group(self, org_id: str) -> AppUserGroup:
        org_data = self._org_id_to_data[org_id]
        return AppUserGroup(
            app_name=Connectors.ZENDESK,
            connector_id=self.connector_id,
            source_user_group_id=f"org_{org_id}",
            name=org_data.get("name") or f"Organization {org_id}",
            org_id=self.data_entities_processor.org_id,
            source_created_at=self._parse_datetime(org_data.get("created_at")),
            source_updated_at=self._parse_datetime(org_data.get("updated_at")),
        )

    async def _add_sideloaded_org_members(self, payload: dict[str, Any]) -> None:
        """Put the end users a ticket page names into their organization's group.

        Only organizations that share tickets matter: no other org's group is ever
        granted on a ticket. Additive, never a rebuild — a page shows a slice of an
        org's people, and rebuilding from it would drop everyone else.
        """
        if await self._copies_no_source_access():
            return
        members_by_org: dict[str, dict[str, AppUser]] = defaultdict(dict)
        for user_data in self._extract_list(payload, "users"):
            org_id = str(user_data.get("organization_id") or "")
            if not self._org_data_shares_tickets(self._org_id_to_data.get(org_id)):
                continue
            if not self._is_signed_in_customer(user_data):
                continue
            app_user = self._to_app_user(user_data)
            if app_user:
                members_by_org[org_id][app_user.source_user_id] = app_user
        if members_by_org:
            await self.data_entities_processor.on_new_user_groups(
                [
                    (self._org_user_group(org_id), list(members.values()))
                    for org_id, members in members_by_org.items()
                ],
                replace_members=False,
            )

    async def _users_revoked_since_last_sync(self) -> tuple[bool, int | None]:
        """Whether anyone was deleted or suspended since the last completed ticket sync.

        Neither moves the updated_at of the tickets the user is on, and a customer is
        granted on each ticket by email, so their grants can only be withdrawn by
        re-reading the tickets. Second value is the checkpoint to save once that has
        happened; None leaves the saved one so the next run asks again.
        """
        if await self._copies_no_source_access():
            return False, None
        checkpoint = get_epoch_timestamp_in_ms() // 1000 - INCREMENTAL_SAFETY_LAG_SECONDS
        if self._rebuild_ticket_edges:
            return False, checkpoint
        start_time = (await self.records_sync_point.read_sync_point(USERS_SYNC_POINT_KEY)).get(
            "lastEndTime"
        )
        if not start_time:
            return False, checkpoint
        revoked = False
        cursor: str | None = None
        while True:
            response = await self._call_incremental(
                "incremental_users", start_time=int(start_time), cursor=cursor
            )
            payload = response.data if response is not None and response.success else None
            if not isinstance(payload, dict) or not isinstance(payload.get("users"), list):
                self.logger.warning(
                    "Zendesk: could not check for deleted or suspended users (%s) — will "
                    "retry next sync",
                    response.error if response is not None else "retries exhausted",
                )
                return False, None
            if any(
                user.get("active") is False or user.get("suspended")
                for user in payload["users"]
            ):
                revoked = True
            if payload.get("end_of_stream", True):
                return revoked, checkpoint
            next_cursor = payload.get("after_cursor")
            if not next_cursor or next_cursor == cursor:
                return revoked, None
            cursor = next_cursor

    async def _fetch_ticket_permissions(self, ticket_id: str) -> list[Permission]:
        """Re-derive a ticket's grants for the streaming path, which only has its Record."""
        if await self._copies_no_source_access():
            return []
        datasource = await self._get_fresh_datasource()
        # The requester is usually an end user, who is not held between syncs.
        response = await self._call_api_with_retry(
            datasource.show_ticket,
            "zendesk/show_ticket",
            ticket_id=int(ticket_id),
            include="users",
        )
        ticket = self._extract_required_object(response.data, "ticket") if response.success else None
        if not ticket:
            raise ValueError(f"Failed to fetch Zendesk ticket {ticket_id} for its permissions")
        self._cache_sideloads(response.data)
        return self._record_permissions(
            ticket.get("group_id"),
            self._user_data(ticket.get("requester_id")),
            ticket.get("organization_id"),
            collaborators=ticket.get("collaborator_ids") or [],
            assignee_id=ticket.get("assignee_id"),
            requester_id=ticket.get("requester_id"),
        )

    def _org_shares_tickets(self, organization_id: Any) -> bool:
        """Whether an organization's members may read each other's tickets.

        Zendesk's ``shared_tickets`` is false by default; an org missing from the cache
        was never exported, so withhold rather than guess.
        """
        org_data = self._org_id_to_data.get(str(organization_id))
        if org_data is None:
            self.logger.warning(
                "Zendesk: organization %s not in cache — withholding its org-wide grant",
                organization_id,
            )
            return False
        return self._org_data_shares_tickets(org_data)

    @staticmethod
    def _org_data_shares_tickets(org_data: dict[str, Any] | None) -> bool:
        # The incremental export keeps deleted organizations, with shared_tickets intact.
        return bool(
            org_data
            and org_data.get("shared_tickets")
            and not org_data.get("deleted_at")
        )

    def _record_permissions(
        self,
        group_id: Any,
        requester: dict[str, Any],
        organization_id: Any = None,
        *,
        collaborators: list[Any] | None = None,
        include_customer_access: bool = True,
        assignee_id: Any = None,
        requester_id: Any = None,
        keeps_org_grant: bool = False,
    ) -> list[Permission]:
        permissions: list[Permission] = [self._all_tickets_permission()]
        granted: set[str] = set()

        def grant_group(external_id: str) -> None:
            if external_id not in granted:
                granted.add(external_id)
                permissions.append(Permission(
                    external_id=external_id,
                    type=PermissionType.READ,
                    entity_type=EntityType.GROUP,
                ))

        if group_id:
            grant_group(f"group_{group_id}")
        else:
            permissions.append(self._public_groups_permission())
        # Staff are granted through their own groups, never by email: a group's members
        # are rewritten every sync, so suspension or a new access level reaches tickets
        # that never change again.
        if assignee_id and str(assignee_id) in self._user_id_to_data:
            grant_group(f"{ASSIGNED_STAFF_GROUP_PREFIX}{assignee_id}")
        known_org = bool(organization_id) and str(organization_id) in self._org_id_to_data
        if known_org:
            grant_group(f"{ORG_AGENTS_GROUP_PREFIX}{organization_id}")
        # Granted whether or not the org shares tickets: run_sync empties the group of
        # an org that does not, so toggling sharing needs no change to the tickets.
        # An unanswered org's group is left as it is: a ticket that had its grant keeps it.
        if include_customer_access and (known_org or keeps_org_grant):
            grant_group(f"org_{organization_id}")

        emails: set[str] = set()

        def grant_person(user_id: Any, user_data: dict[str, Any], *, works_the_ticket: bool) -> None:
            if str(user_id) in self._user_id_to_data:
                # A CC or follower works the ticket; an agent who only requested it
                # sees it the way an end user would.
                if works_the_ticket or include_customer_access:
                    grant_group(f"{NAMED_STAFF_GROUP_PREFIX}{user_id}")
                return
            # A suspended or deleted customer cannot sign in to see anything.
            if not include_customer_access or user_data.get("active") is False or user_data.get("suspended"):
                return
            email = user_data.get("email")
            if email and email.lower() not in emails:
                emails.add(email.lower())
                permissions.append(Permission(
                    email=email,
                    type=PermissionType.READ,
                    entity_type=EntityType.USER,
                ))

        for collaborator_id in collaborators or []:
            if collaborator_id:
                grant_person(collaborator_id, self._user_data(collaborator_id), works_the_ticket=True)
        if requester_id or requester:
            grant_person(requester_id, requester, works_the_ticket=False)
        if len(permissions) == 1:
            self.logger.warning(
                "Zendesk: no group or requester grant for a record (group_id=%s) — "
                "only all-tickets users will see it",
                group_id,
            )
        return permissions
