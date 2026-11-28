# Copyright (c) 2026 PipesHub Inc.
# Licensed under the PipesHub Enterprise License. See the LICENSE file in this
# ee folder. Free for development, testing and evaluation. Production use needs
# a PipesHub licence key.

"""ServiceNow: reading who may see what from ServiceNow.

Groups with their flattened members, roles with their holders, companies, departments and locations as groups,
the admins, each knowledge base's user criteria (Can Read, Cannot Read, Can Contribute, Cannot Contribute) and
each article's (with its ownership group) turned into grants. The free connector keeps syncing users, knowledge
bases, categories, articles and attachments and calls these methods; without a licence key a company-wide
connector reads none of this (the processor's licence gate).
"""

import re
from collections import defaultdict
from collections.abc import Iterable
from typing import Dict, List, Optional, Tuple

from app.config.constants.arangodb import (
    AccessRule,
    Connectors,
)
from app.config.constants.http_status_code import HttpStatusCode
from app.connectors.core.constants import CommonStrings
from app.connectors.sources.servicenow.servicenow.common import (
    _EVERYONE,
    _KB_MTOM,
    _CriteriaHidden,
    _CriteriaUnreadable,
    _Listing,
    _raise_if_run_stop,
)
from app.connectors.sources.servicenow.servicenow.constants import (
    ORGANIZATIONAL_ENTITIES,
    OrganizationalEntityConfig,
    ServiceNowDefaults,
    ServiceNowDictKeys,
    ServiceNowFields,
    ServiceNowPrefixes,
    ServiceNowRoles,
    ServiceNowSyncPointKeys,
    ServiceNowTables,
)
from app.models.entities import (
    AppUser,
    AppUserGroup,
)
from app.models.permission import EntityType, Permission, PermissionType
from app.sources.external.servicenow.models import (
    ServiceNowAPIError,
    SysUserGroup,
    SysUserGroupMembership,
    SysUserRole,
    SysUserRoleAssignment,
    SysUserRoleContains,
    TableAPIRecord,
)
from app.utils.time_conversion import datetime_to_epoch_ms

# The name prefixes of the user groups that stand for a company, department or location
_ORG_GROUP_PREFIXES = {
    ServiceNowFields.COMPANY: ServiceNowPrefixes.COMPANY,
    ServiceNowFields.DEPARTMENT: ServiceNowPrefixes.DEPARTMENT,
    ServiceNowFields.LOCATION: ServiceNowPrefixes.LOCATION,
}

_SCRIPT_COMMENTS = re.compile(r"/\*.*?\*/|//[^\n]*", re.S)


class ServiceNowPermissionsMixin:
    """The permission-reading half of ``ServiceNowConnector``."""

    _CRITERIA_DIMENSIONS = (
        ServiceNowFields.USER, ServiceNowFields.GROUP, ServiceNowFields.ROLE,
        ServiceNowFields.DEPARTMENT, ServiceNowFields.LOCATION, ServiceNowFields.COMPANY,
    )

    async def _copies_no_source_access(self) -> bool:
        """Without a licence key a company-wide connector reads no access from ServiceNow. The processor then keeps
        every stored grant, group and role, and a record's inherit edge, whatever is handed to it
        (``skips_permission_writes``), so the empty answers below only keep the run whole: an unreadable answer
        would skip every knowledge base and article, though nothing was wrong with them."""
        return await self.data_entities_processor.skips_permission_writes(self.connector_id)

    async def _kb_grants(self, kb_data: TableAPIRecord, admin_source_ids: Iterable[str]) -> list[Permission]:
        """A knowledge base's grants (see _sync_knowledge_bases). Raises _CriteriaUnreadable or ServiceNowAPIError
        when its user criteria were not read in full."""
        if await self._copies_no_source_access():
            return []
        kb_sys_id = kb_data.sys_id
        managers = set(self._split_ids(getattr(kb_data, ServiceNowFields.KB_MANAGERS, None)))

        criteria = await self._kb_criteria(kb_sys_id)
        read_permissions = await self._process_criteria_permissions(
            criteria[ServiceNowDictKeys.READ],
            PermissionType.READ,
            deny_criteria_ids=criteria[ServiceNowDictKeys.CANNOT_READ],
        )
        write_permissions = await self._process_criteria_permissions(
            criteria[ServiceNowDictKeys.WRITE],
            PermissionType.WRITE,
            deny_criteria_ids=criteria[ServiceNowDictKeys.CANNOT_WRITE],
        )
        manager_permissions = await self._users_to_permissions(managers, PermissionType.READ)
        admin_permissions = await self._users_to_permissions(set(admin_source_ids), PermissionType.READ)

        # Admins and knowledge admins read every knowledge base
        permission_objects = read_permissions + write_permissions + manager_permissions + admin_permissions

        permission_objects += await self._users_to_permissions(
            set(self._split_ids(kb_data.owner)), PermissionType.OWNER
        )
        return permission_objects

    async def _get_admin_users(self) -> set[str]:
        """
        The sys_ids of the users who read every knowledge base and article without user criteria:
        active holders of the admin or knowledge_admin role (not knowledge_manager, which the
        platform gives every KB owner and manager and which grants nothing by itself). They are
        granted through _users_to_permissions, so one with no synced app user gets nothing. A
        failed read raises: an empty set would be written as "no admins" and remove their grants.
        When an ACL hides some of the rows, the admins of the last full listing are kept as well.
        """
        if await self._copies_no_source_access():
            return set()
        role_assignments: list[TableAPIRecord] = []
        listing = _Listing()
        async for rows in self._iter_table(
            ServiceNowTables.SYS_USER_HAS_ROLE,
            f"{ServiceNowFields.ROLE}.{ServiceNowFields.NAME}IN{ServiceNowRoles.ADMIN},{ServiceNowRoles.KNOWLEDGE_ADMIN}"
            f"^{ServiceNowFields.STATE}={ServiceNowFields.ACTIVE}^{ServiceNowFields.USER}.{ServiceNowFields.ACTIVE}=true",
            [ServiceNowFields.USER], listing=listing, narrow=True,
        ):
            role_assignments.extend(rows)

        admin_sys_ids = {str(a.user) for a in role_assignments if getattr(a, "user", None)}
        complete = sorted(admin_sys_ids)
        if listing.partial:
            stored = await self.user_sync_point.read_sync_point(ServiceNowSyncPointKeys.ADMINS) or {}
            complete = list(stored.get(ServiceNowDictKeys.IDS) or [])
            admin_sys_ids |= set(complete)
            self.logger.warning("Some admin role assignments are hidden from this account; keeping the admins read last")
            self._sync_read_incomplete = True
        # What a run whose role assignments did not change grants again (see _run_sync)
        await self.user_sync_point.update_sync_point(ServiceNowSyncPointKeys.ADMINS, {
            ServiceNowDictKeys.IDS: complete, ServiceNowDictKeys.GRANTED: sorted(admin_sys_ids),
        })
        self.logger.info(f"Found {len(admin_sys_ids)} unique admin users")
        return admin_sys_ids

    async def _sync_user_groups(self) -> _Listing:
        """
        Write every group with its flattened members, also a group with none: the people who
        left it lose it. Returns how the group listing went (see _reconcile_user_groups).
        """
        if await self._copies_no_source_access():
            return _Listing()
        self.logger.info("Starting user group synchronization")
        membership_listing = _Listing()
        memberships_data = await self._fetch_all_memberships(membership_listing)
        listing = _Listing()
        groups_data = await self._fetch_all_groups(listing)
        self._groups = {g.sys_id: g for g in groups_data}

        group_with_permissions = await self._flatten_and_create_user_groups(groups_data, memberships_data)
        # The ownership group of an article grants its members (see _article_access), and group
        # criteria match them (see _members_of).
        self._group_members = {
            g.source_user_group_id: {u.source_user_id for u in users if u.source_user_id}
            for g, users in group_with_permissions
        }
        self._held[ServiceNowFields.GROUP] = (self._group_members, membership_listing.partial or listing.partial)
        if group_with_permissions:
            await self.data_entities_processor.on_new_user_groups(group_with_permissions)

        self.logger.info(f"✅ Processed {len(group_with_permissions)} user groups")
        return listing

    async def _fetch_all_groups(self, listing: _Listing | None = None) -> List[SysUserGroup]:
        """Fetch all groups from ServiceNow (no delta sync), inactive ones too: those are written
        with no members (an inactive group matches nobody).

        Returns:
            List of SysUserGroup Pydantic models
        """
        if await self._copies_no_source_access():
            return []
        all_groups: List[SysUserGroup] = []
        # A failed page raises: a partial list makes the caller delete permission
        # edges that it cannot rebuild.
        async for rows in self._iter_table(
            ServiceNowTables.SYS_USER_GROUP,
            None,
            [
                ServiceNowFields.SYS_ID,
                ServiceNowFields.NAME,
                ServiceNowFields.DESCRIPTION,
                ServiceNowFields.PARENT,
                ServiceNowFields.MANAGER,
                ServiceNowFields.ACTIVE,
                ServiceNowFields.SYS_CREATED_ON,
                ServiceNowFields.SYS_UPDATED_ON,
            ],
            page=ServiceNowDefaults.ID_PAGE_SIZE,
            listing=listing,
        ):
            all_groups.extend(SysUserGroup(**record.model_dump()) for record in rows)

        self.logger.info(f"Fetched {len(all_groups)} groups")
        return all_groups

    async def _fetch_all_memberships(self, listing: _Listing | None = None) -> List[SysUserGroupMembership]:
        """Fetch every user-group membership of an active user from ServiceNow.

        This read must stay full. ``on_new_user_groups`` deletes all permission
        edges that point to a group before it writes the members given to it, so
        a delta read would drop every membership whose ``sys_user_grmember`` row
        did not change inside the delta window.

        Returns:
            List of SysUserGroupMembership Pydantic models
        """
        self.logger.info("Fetching all user memberships")

        all_memberships: List[SysUserGroupMembership] = []
        # A failed page raises: a partial list makes the caller delete permission
        # edges that it cannot rebuild.
        async for rows in self._iter_table(
            ServiceNowTables.SYS_USER_GRMEMBER,
            f"{ServiceNowFields.USER}.{ServiceNowFields.ACTIVE}=true",
            [
                ServiceNowFields.SYS_ID,
                ServiceNowFields.USER,
                ServiceNowFields.GROUP,
                ServiceNowFields.SYS_UPDATED_ON,
            ],
            page=ServiceNowDefaults.ID_PAGE_SIZE,
            listing=listing,
        ):
            all_memberships.extend(SysUserGroupMembership(**record.model_dump()) for record in rows)

        self.logger.info(f"Fetched {len(all_memberships)} memberships")
        return all_memberships

    async def _sync_roles(self) -> tuple[set[str], _Listing]:
        """
        Sync roles as ROLE_-prefixed user groups, each with every active holder, also a role
        with none. sys_user_has_role already holds a row per role a user gets from a group or
        a containing role (inherited=true); the role hierarchy is flattened as well.

        Returns the listed role ids and how the role listing went (see _reconcile_user_groups).
        """
        if await self._copies_no_source_access():
            return set(), _Listing()
        self.logger.info("Starting role synchronization")

        assignment_listing = _Listing()
        role_assignments = await self._fetch_all_role_assignments(assignment_listing)
        # The out-of-box "Has a role" criterion counts a user's roles in every state (see _has_role_users).
        self._role_names = defaultdict(list)
        for assignment in role_assignments:
            self._role_names[assignment.user].append(str(getattr(assignment, ServiceNowFields.ROLE_NAME, "") or ""))
        role_assignments = [
            a for a in role_assignments
            if str(getattr(a, ServiceNowFields.STATE, "") or "").lower() == ServiceNowFields.ACTIVE
        ]
        listing = _Listing()
        roles_data = await self._fetch_all_roles(listing)
        hierarchy_data = await self._fetch_role_hierarchy()

        # A holder of a role holds every role it contains, so a contained role's members
        # include the holders of each role that contains it (a role can contain several).
        containers: dict[str, set[str]] = defaultdict(set)
        for hierarchy_record in hierarchy_data:
            if hierarchy_record.contains and hierarchy_record.role:
                containers[hierarchy_record.contains].add(hierarchy_record.role)

        roles_as_groups = [
            SysUserGroup(
                sys_id=role.sys_id, name=role.name, description=role.description,
                sys_created_on=role.sys_created_on, sys_updated_on=role.sys_updated_on, active="true",
            )
            for role in roles_data
        ]
        role_assignments_as_memberships = [
            SysUserGroupMembership(
                sys_id=assignment.sys_id, user=assignment.user, group=assignment.role,
                sys_updated_on=assignment.sys_updated_on,
            )
            for assignment in role_assignments
        ]
        roles_with_permissions = await self._flatten_and_create_user_groups(
            roles_as_groups, role_assignments_as_memberships, children=containers
        )

        for role_group, _users in roles_with_permissions:
            if not role_group.name.startswith(ServiceNowPrefixes.ROLE):
                role_group.name = f"{ServiceNowPrefixes.ROLE}{role_group.name}"

        self._held[ServiceNowFields.ROLE] = ({
            g.source_user_group_id: {u.source_user_id for u in users if u.source_user_id}
            for g, users in roles_with_permissions
        }, assignment_listing.partial)
        if roles_with_permissions:
            await self.data_entities_processor.on_new_user_groups(roles_with_permissions)

        self.logger.info(f"✅ Processed {len(roles_with_permissions)} roles")
        return {r.sys_id for r in roles_data}, listing

    async def _fetch_all_roles(self, listing: _Listing | None = None) -> List[SysUserRole]:
        """Fetch all roles from sys_user_role table.

        Returns:
            List of SysUserRole Pydantic models
        """
        self.logger.info("Fetching all roles")

        all_roles: List[SysUserRole] = []
        # A failed page raises: a partial list makes the caller delete permission
        # edges that it cannot rebuild.
        async for rows in self._iter_table(
            ServiceNowTables.SYS_USER_ROLE,
            None,
            [
                ServiceNowFields.SYS_ID,
                ServiceNowFields.NAME,
                ServiceNowFields.DESCRIPTION,
                ServiceNowFields.SYS_CREATED_ON,
                ServiceNowFields.SYS_UPDATED_ON,
            ],
            page=ServiceNowDefaults.ID_PAGE_SIZE,
            listing=listing,
        ):
            all_roles.extend(SysUserRole(**record.model_dump()) for record in rows)

        self.logger.info(f"Fetched {len(all_roles)} roles")
        return all_roles

    async def _fetch_all_role_assignments(self, listing: _Listing | None = None) -> List[SysUserRoleAssignment]:
        """
        Fetch the role assignments of active users from sys_user_has_role, in every state, with
        the state and the role's name (role groups take the active ones; see _sync_roles).

        This read must stay full, for the reason given in
        ``_fetch_all_memberships``: roles go through the same
        ``on_new_user_groups`` replace-on-write path.

        Returns:
            List of SysUserRoleAssignment Pydantic models
        """
        self.logger.info("Fetching all active role assignments")
        all_assignments: List[SysUserRoleAssignment] = []
        # A failed page raises: a partial list makes the caller delete permission
        # edges that it cannot rebuild.
        async for rows in self._iter_table(
            ServiceNowTables.SYS_USER_HAS_ROLE,
            f"{ServiceNowFields.USER}.{ServiceNowFields.ACTIVE}=true",
            [
                ServiceNowFields.SYS_ID,
                ServiceNowFields.USER,
                ServiceNowFields.ROLE,
                ServiceNowFields.ROLE_NAME,
                ServiceNowFields.STATE,
                ServiceNowFields.SYS_UPDATED_ON,
            ],
            page=ServiceNowDefaults.ID_PAGE_SIZE,
            listing=listing,
        ):
            all_assignments.extend(SysUserRoleAssignment(**record.model_dump()) for record in rows)

        self.logger.info(f"Fetched {len(all_assignments)} role assignments")
        return all_assignments

    async def _fetch_role_hierarchy(self) -> List[SysUserRoleContains]:
        """
        Fetch role hierarchy from sys_user_role_contains table.

        This is a full sync (no checkpoint) since role hierarchy changes are rare.

        Returns:
            List of SysUserRoleContains Pydantic models
        """
        self.logger.info("Fetching role hierarchy")

        all_hierarchy: List[SysUserRoleContains] = []
        # A failed page raises: a partial list makes the caller delete permission
        # edges that it cannot rebuild. A table closed to the account is read as empty:
        # sys_user_has_role already has a row for every role a user gets from another.
        try:
            async for rows in self._iter_table(
                ServiceNowTables.SYS_USER_ROLE_CONTAINS,
                None,
                [ServiceNowFields.SYS_ID, ServiceNowFields.CONTAINS, ServiceNowFields.ROLE],
                page=ServiceNowDefaults.ID_PAGE_SIZE,
            ):
                all_hierarchy.extend(SysUserRoleContains(**record.model_dump()) for record in rows)
        except ServiceNowAPIError as e:
            if e.status_code != HttpStatusCode.FORBIDDEN.value:
                raise
            self.logger.info("This account cannot read sys_user_role_contains; roles come from sys_user_has_role")
            return []

        self.logger.info(f"Fetched {len(all_hierarchy)} role hierarchy records")
        return all_hierarchy

    async def _reconcile_user_groups(self, role_ids: set[str], group_listing: _Listing, role_listing: _Listing) -> None:
        """Give each company, department and location group exactly its active users, and
        delete the stored groups and roles that ServiceNow no longer has.

        A user's company, department and location are read from every active user each run,
        so a user who moved, or was deactivated, leaves the old one (memberships written one
        at a time could only be added). A group or role missing from a listing that an ACL cut
        short is deleted only once ServiceNow counts no row for it.
        """
        if await self._copies_no_source_access():
            return
        # An inactive group is listed, and not written (see _flatten_and_create_user_groups): a stored one goes.
        listed = {g for g, row in self._groups.items() if self._is_active(row)} | role_ids
        stored = [g for g in await self.data_entities_processor.get_all_user_groups(self.connector_id, raise_on_error=True)
                  if g.source_user_group_id not in listed]
        org_prefixes = tuple(_ORG_GROUP_PREFIXES.values())
        await self._sync_org_memberships([g for g in stored if g.name.startswith(org_prefixes)])

        for group in stored:
            if group.name.startswith(org_prefixes):
                continue
            is_role = group.name.startswith(ServiceNowPrefixes.ROLE)
            table, listing = (
                (ServiceNowTables.SYS_USER_ROLE, role_listing) if is_role else (ServiceNowTables.SYS_USER_GROUP, group_listing)
            )
            if group.source_user_group_id not in self._groups and listing.partial and not await self._gone_at_source(
                table, f"{ServiceNowFields.SYS_ID}={group.source_user_group_id}"
            ):
                self._sync_read_incomplete = True  # kept, not seen: a full sync must not unlink it
                continue
            self.logger.info(f"Removing {'role' if is_role else 'group'} {group.source_user_group_id}, deleted or inactive in ServiceNow")
            await self.data_entities_processor.on_user_group_deleted(group.source_user_group_id, self.connector_id)

    async def _sync_org_memberships(self, org_groups: list[AppUserGroup]) -> None:
        """Replace the members of the stored company, department and location groups with the
        active users whose field names them (exact match: ServiceNow criteria do not include
        child departments, locations or companies)."""
        if not org_groups:
            return
        members: dict[str, set[str]] = defaultdict(set)
        listing = _Listing()
        async for rows in self._iter_table(
            ServiceNowTables.SYS_USER,
            f"{ServiceNowFields.ACTIVE}=true^" + "^OR".join(f"{f}ISNOTEMPTY" for f in _ORG_GROUP_PREFIXES),
            [ServiceNowFields.SYS_ID, *_ORG_GROUP_PREFIXES],
            page=ServiceNowDefaults.ID_PAGE_SIZE, listing=listing,
        ):
            for row in rows:
                for field in _ORG_GROUP_PREFIXES:
                    if org_id := getattr(row, field, None):
                        members[str(org_id)].add(row.sys_id)
        # Department, location and company criteria match exactly these (see _members_of).
        self._held.update(dict.fromkeys(_ORG_GROUP_PREFIXES, (members, listing.partial)))
        users = {u.source_user_id: u for u in await self._all_app_users()}
        await self.data_entities_processor.on_new_user_groups([
            (group, [users[s] for s in sorted(members.get(group.source_user_group_id, ())) if s in users])
            for group in org_groups
        ])

    async def _flatten_and_create_user_groups(
        self,
        groups_data: List[SysUserGroup],
        memberships_data: List[SysUserGroupMembership],
        children: dict[str, set[str]] | None = None,
    ) -> List[Tuple[AppUserGroup, List[AppUser]]]:
        """
        Flatten group hierarchy and create AppUserGroup objects: a group's members are its
        direct members plus those of its child groups (ServiceNow group criteria match child
        group members too). An inactive group matches nobody: it is not written and adds no
        members to its parent (_reconcile_user_groups removes a stored one).

        Args:
            groups_data: List of SysUserGroup Pydantic models
            memberships_data: List of SysUserGroupMembership Pydantic models
            children: group id -> the groups whose members it includes; by default each
                group's child groups (``parent``)

        Returns:
            List of (AppUserGroup, [AppUser]) tuples
        """
        inactive = {g.sys_id for g in groups_data if not self._is_active(g)}
        group_by_id = {group.sys_id: group for group in groups_data}
        if children is None:
            children = defaultdict(set)  # parent_id -> {child_ids}
            for group in groups_data:
                if group.parent:
                    children[group.parent].add(group.sys_id)

        # Build direct user memberships
        direct_users = defaultdict(set)  # group_id -> {user_ids}

        for membership in memberships_data:
            user_id = membership.user
            group_id = membership.group

            if user_id and group_id:
                direct_users[group_id].add(user_id)

        def get_all_users(group_id: str, visited: set) -> set:
            """Get all users including inherited from active child groups."""
            if group_id in visited or group_id in inactive:
                return set()
            visited.add(group_id)
            all_users = set(direct_users.get(group_id, []))
            for child_id in children.get(group_id, []):
                all_users.update(get_all_users(child_id, visited))
            return all_users

        # Create AppUserGroup objects with flattened members
        result = []

        # Get all existing users from database for lookup
        existing_app_users = await self._all_app_users()

        # Create lookup map: source_user_id -> AppUser
        user_lookup = {user.source_user_id: user for user in existing_app_users}

        for group_id, group_data in group_by_id.items():
            if group_id in inactive:
                continue
            # Create AppUserGroup
            user_group = self._transform_to_user_group(group_data)

            if not user_group:
                continue

            app_users = [user_lookup[u] for u in get_all_users(group_id, set()) if u in user_lookup]
            result.append((user_group, app_users))

        self.logger.info(f"Flattened {len(result)} groups")
        return result

    async def _sync_organizational_entities(self) -> None:
        """
        Sync all organizational entities from ServiceNow.

        Syncs in order:
        1. Companies (top-level)
        2. Departments
        3. Locations

        Each entity type creates AppUserGroup nodes with prefix (COMPANY_, DEPARTMENT_, etc.); their
        members are written by _sync_org_memberships.
        """
        if await self._copies_no_source_access():
            return
        self.logger.info("🏢 Starting organizational entities sync")

        # Sync each entity type in order
        for entity_type, config in ORGANIZATIONAL_ENTITIES.items():
            await self._sync_single_organizational_entity(entity_type, config)

        self.logger.info("✅ All organizational entities synced successfully")

    async def _sync_single_organizational_entity(
        self, entity_type: str, config: OrganizationalEntityConfig
    ) -> None:
        """
        Generic sync method for a single organizational entity type: each row becomes an AppUserGroup.

        Args:
            entity_type: Type of entity (company, department, location)
            config: OrganizationalEntityConfig with table name, fields, prefix, and sync point key
        """
        table_name = config["table"]
        fields = config["fields"]
        prefix = config["prefix"]
        sync_point_key = config["sync_point_key"]

        # Get sync point for this entity type
        sync_point = self.org_entity_sync_points.get(entity_type)

        self.logger.info(f"📊 Starting {entity_type} sync from table {table_name}")

        last_sync_time = await self._read_checkpoint(sync_point, sync_point_key)

        query = None
        if last_sync_time:
            since = self._delta_since(last_sync_time)
            self.logger.info(f"🔄 Delta sync: fetching {entity_type} updated after {since}")
            query = f"{ServiceNowFields.SYS_UPDATED_ON}>{since}"
        else:
            self.logger.info(f"🆕 Full sync: fetching all {entity_type}")

        total_synced = 0
        latest_update_time = None

        # A failed page raises: a short list is indistinguishable from a complete one to
        # everything downstream, so the sync would report success over what it missed.
        async for entities_data in self._iter_table(
            table_name, query, fields.split(CommonStrings.COMMA), page=ServiceNowDefaults.ID_PAGE_SIZE
        ):
            # Pages come oldest first, so the last row is the newest seen
            latest_update_time = entities_data[-1].sys_updated_on

            # Transform to AppUserGroup entities
            user_groups = []
            for entity_data in entities_data:
                user_group = self._transform_to_organizational_group(
                    entity_data, prefix
                )
                if user_group:
                    user_groups.append(user_group)

            if user_groups:
                await self.data_entities_processor.batch_upsert_user_groups(user_groups)

                total_synced += len(user_groups)

        # Save checkpoint
        if latest_update_time:
            await sync_point.update_sync_point(
                sync_point_key, {ServiceNowSyncPointKeys.LAST_SYNC_TIME: latest_update_time}
            )

        self.logger.info(
            f"✅ {entity_type.capitalize()} sync complete. Total synced: {total_synced}"
        )

    async def _fetch_kb_permissions_from_criteria(
        self, kb_sys_id: str
    ) -> Dict[str, List[str]]:
        """
        Fetch the user criteria ids of a knowledge base from its four mtom tables: Can Read,
        Cannot Read, Can Contribute and Cannot Contribute.

        Args:
            kb_sys_id: Knowledge base sys_id

        Returns:
            Dict with 'read', 'cannot_read', 'write' and 'cannot_write' lists of criteria sys_ids
        """
        criteria_map: dict[str, list[str]] = {key: [] for key, _ in _KB_MTOM}
        if self._kb_mtom is not None:
            return {key: list(ids) for key, ids in self._kb_mtom.get(kb_sys_id, criteria_map).items()}

        # A failed read raises: an empty Can Read list would remove every reader of the KB,
        # and an empty Cannot Read list would grant the KB to users it denies.
        for key, table in _KB_MTOM:
            listing = _Listing()
            async for rows in self._iter_table(
                table, f"{ServiceNowFields.KB_KNOWLEDGE_BASE}={kb_sys_id}", [ServiceNowFields.USER_CRITERIA],
                listing=listing, narrow=True,
            ):
                criteria_map[key].extend(
                    r.user_criteria for r in rows if getattr(r, ServiceNowFields.USER_CRITERIA, None)
                )
            if listing.partial:
                self._sync_read_incomplete = True
                raise _CriteriaHidden(f"{table} rows of knowledge base {kb_sys_id} are hidden from this account")

        return criteria_map

    async def _load_kb_criteria(self) -> None:
        """Every knowledge base's criteria ids, one listing per mtom table, and their user_criteria
        rows in one read, instead of five reads per knowledge base. A failed read leaves each
        knowledge base to read its own (_fetch_kb_permissions_from_criteria); a run stop raises."""
        if await self._copies_no_source_access():
            return
        try:
            by_kb: dict[str, dict[str, list[str]]] = defaultdict(lambda: {key: [] for key, _ in _KB_MTOM})
            for key, table in _KB_MTOM:
                async for rows in self._iter_table(
                    table, None, [ServiceNowFields.KB_KNOWLEDGE_BASE, ServiceNowFields.USER_CRITERIA],
                    page=ServiceNowDefaults.ID_PAGE_SIZE,
                ):
                    for r in rows:
                        if r.get(ServiceNowFields.KB_KNOWLEDGE_BASE) and r.get(ServiceNowFields.USER_CRITERIA):
                            by_kb[str(r.get(ServiceNowFields.KB_KNOWLEDGE_BASE))][key].append(
                                str(r.get(ServiceNowFields.USER_CRITERIA))
                            )
            self._kb_mtom = dict(by_kb)
            await self._fetch_user_criteria(sorted({c for kinds in by_kb.values() for ids in kinds.values() for c in ids}))
        except ServiceNowAPIError as e:
            _raise_if_run_stop(e)
            self.logger.warning(f"Could not read the knowledge bases' criteria at once; reading them one by one: {e}")
            self._kb_mtom = None

    async def _kb_criteria(self, kb_sys_id: str) -> dict[str, list[str]]:
        """A knowledge base's criteria ids (_fetch_kb_permissions_from_criteria), read once per
        run. A knowledge base with no Can Read and no Can Contribute criteria is readable by every
        user where the instance does not block that (see _resolve_block_no_criteria)."""
        if kb_sys_id not in self._kb_criteria_cache:
            read = await self._fetch_kb_permissions_from_criteria(kb_sys_id)
            criteria = {k: list(read.get(k) or []) for k in (
                ServiceNowDictKeys.READ, ServiceNowDictKeys.CANNOT_READ, ServiceNowDictKeys.WRITE, ServiceNowDictKeys.CANNOT_WRITE
            )}
            if not self.block_kb_without_criteria and not (
                criteria[ServiceNowDictKeys.READ] or criteria[ServiceNowDictKeys.WRITE]
            ):
                criteria[ServiceNowDictKeys.READ] = [_EVERYONE]
            self._kb_criteria_cache[kb_sys_id] = criteria
        return self._kb_criteria_cache[kb_sys_id]

    async def _process_criteria_permissions(
        self, criteria_ids: list[str], permission_type: PermissionType,
        deny_criteria_ids: list[str] | None = None,
    ) -> list[Permission]:
        """
        The grants for "matches any of ``criteria_ids`` and none of ``deny_criteria_ids``".

        The grant model has no deny and no AND. Without a deny and without a Match All over more
        than one field, each criterion becomes a grant per user, group, role, department, location
        and company it names (groups and roles keep up with their members), or an ORG grant when
        it matches everyone. Otherwise the users are computed here (allowed minus denied) and
        granted one by one. A failed read raises _CriteriaUnreadable, so the caller keeps the
        stored grants: unknown is neither allowed nor empty.
        """
        try:
            if not criteria_ids:
                return []

            # Deny rows first: unknown deny must never read as "no deny".
            deny_records = await self._fetch_deny_records(deny_criteria_ids or [])
            can_records = self._allow_rows(await self._fetch_user_criteria(criteria_ids))

            if not deny_records and not any(self._needs_user_resolution(r) for r in can_records):
                if any(self._is_everyone(r) for r in can_records):
                    return [Permission(entity_type=EntityType.ORG, type=permission_type)]
                # Users by email; groups, roles, departments, locations and companies are user groups.
                grants: list[Permission] = []
                for record in can_records:
                    grants += await self._users_to_permissions(
                        set(self._split_ids(getattr(record, ServiceNowFields.USER, None))), permission_type
                    )
                    grants += [
                        Permission(external_id=i, type=permission_type, entity_type=EntityType.GROUP)
                        for dim in self._CRITERIA_DIMENSIONS if dim != ServiceNowFields.USER
                        for i in self._split_ids(getattr(record, dim, None))
                    ]
                return grants

            users = await self._users_allowed_by(can_records, deny_records)
            return await self._users_to_permissions(users, permission_type)

        except _CriteriaUnreadable:
            raise
        except Exception as e:
            self.logger.error(f"Failed to process criteria permissions: {e}", exc_info=True)
            raise _CriteriaUnreadable(str(e)) from e

    async def _fetch_deny_records(self, deny_ids: list[str]) -> list[TableAPIRecord]:
        """Active Cannot Read / Cannot Contribute user_criteria rows. A failed read raises
        _CriteriaUnreadable. An unedited Guest User row that names no synced user changes nobody's
        access here, so it is left out (the KB can then stay one org grant)."""
        try:
            return [
                r for r in await self._fetch_user_criteria(deny_ids)
                if self._criteria_is_active(r) and not (
                    self._is_guest(r)
                    and not await self._users_matching_criteria(r, deny=True) & set(await self._emails_by_source_id())
                )
            ]
        except Exception as e:
            raise _CriteriaUnreadable(str(e)) from e

    def _is_guest(self, record: TableAPIRecord) -> bool:
        """The out-of-box Guest User script, unedited, with no field but the user: the script
        matches exactly the user named guest, so it is evaluated here (see _guest_ids). An edited
        script, or another field, takes the normal path."""
        return (
            " ".join(str(getattr(record, ServiceNowFields.SCRIPT, None) or "").split())
            == " ".join(ServiceNowDefaults.GUEST_USER_CRITERIA_SCRIPT.split())
            and set(self._dims(record)) <= {ServiceNowFields.USER}
        )

    async def _guest_ids(self) -> set[str]:
        """The sys_id of the user named guest (``GlideUser.getUser("guest")``), read once per run.
        Raises if the read fails."""
        if self._guest_cache is None:
            ids: set[str] = set()
            async for rows in self._iter_table(
                ServiceNowTables.SYS_USER, f"{ServiceNowFields.USER_NAME}={ServiceNowDefaults.GUEST_USER_NAME}",
                [ServiceNowFields.SYS_ID], narrow=True,
            ):
                ids.update(r.sys_id for r in rows if getattr(r, "sys_id", None))
            self._guest_cache = ids
        return self._guest_cache

    async def _users_allowed_by(self, can_records: list[TableAPIRecord], deny_records: list[TableAPIRecord]) -> set[str]:
        """Users matching any allow row minus users matching any deny row. A deny whose reach is
        unknown denies everyone it may cover; an allow that cannot be resolved raises."""
        denied: set[str] = set()
        for record in deny_records:
            try:
                users = None if self._deny_reach_unknown(record) else await self._users_matching_criteria(record, deny=True)
            except Exception as e:
                raise _CriteriaUnreadable(str(e)) from e
            if users is None:
                if not self._deny_reach_unknown(record):
                    self._note_criteria("partial", record.sys_id)
                # Whom a scripted, unreadable or partly hidden Cannot Read denies is unknown, so it may
                # deny anyone: nobody is allowed through criteria (privileged readers are added by the callers).
                self.logger.warning(f"Cannot Read / Cannot Contribute {record.sys_id} cannot be evaluated; denying everyone it may cover")
                return set()
            denied |= users
        allowed: set[str] = set()
        for record in can_records:
            allowed |= await self._users_matching_criteria(record) or set()
        return allowed - denied

    async def _effective_users(self, allow_ids: list[str], deny_ids: list[str]) -> set[str]:
        deny_records = await self._fetch_deny_records(deny_ids)
        can_records = self._allow_rows(await self._fetch_user_criteria(allow_ids))
        return await self._users_allowed_by(can_records, deny_records)

    async def _all_app_users(self) -> list[AppUser]:
        """The connector's users, read once per sync run (after the user sync) for groups and grants."""
        if self._app_users is None:
            # Raising: an empty list would write every group with no members.
            self._app_users = await self.data_entities_processor.get_all_app_users(
                connector_id=self.connector_id, raise_on_error=True
            )
        return self._app_users

    async def _emails_by_source_id(self) -> dict[str, str]:
        # One user listing per sync run, not a lookup per user or per article.
        if self._email_by_source_id is None:
            self._email_by_source_id = {
                u.source_user_id: u.email for u in await self._all_app_users() if u.source_user_id and u.email
            }
        return self._email_by_source_id

    async def _users_to_permissions(self, users: set[str], permission_type: PermissionType) -> list[Permission]:
        email_by_id = await self._emails_by_source_id()
        return [
            Permission(email=email_by_id[u], type=permission_type, entity_type=EntityType.USER)
            for u in sorted(users) if u in email_by_id
        ]

    async def _article_access(self, article_data: TableAPIRecord) -> tuple[bool, AccessRule, list[Permission]]:
        """(inherit, access rule, grants) of an article: who reads it is, as ServiceNow decides it,

            PRIV ∪ OG ∪ CONTRIB ∪ ((KR ∩ AR) − AD)

        PRIV: admins, knowledge admins, the KB owner and managers. OG: the members and manager of
        the article's active ownership group (they do not get the KB). KR: KB Can Read minus KB
        Cannot Read. AR: the article's Can Read (everyone when it has none). AD: its Cannot Read.
        CONTRIB: KB Can Contribute minus Cannot Contribute, held to AR and AD only when
        glide.knowman.apply_article_read_criteria is true. (ServiceNow docs, "Managing access to
        knowledge bases and knowledge articles":
        https://www.servicenow.com/docs/r/servicenow-platform/knowledge-management/user-access-knowledge.html)

        The KB's own grants are PRIV ∪ KR ∪ CONTRIB, so:
        - no criteria of its own: inherit, plus the ownership group (a union);
        - Can Read only: RESTRICTED (inherit and a grant): AR ∪ PRIV, and the contributors when the
          property is false. Intersected with the KB's readers this is the rule above;
        - Cannot Read, or Can Read and an ownership group: no inheritance, the users computed.
        Any unreadable rule raises, so the article is skipped and its stored grants stay.
        """
        if await self._copies_no_source_access():
            return True, AccessRule.OPEN, []
        own = self._split_ids(getattr(article_data, ServiceNowFields.CAN_READ_USER_CRITERIA, None))
        deny = self._split_ids(getattr(article_data, ServiceNowFields.CANNOT_READ_USER_CRITERIA, None))
        group = self._active_group(getattr(article_data, ServiceNowFields.OWNERSHIP_GROUP, None))
        kb_id = article_data.kb_knowledge_base
        if not own and not deny:
            return True, AccessRule.OPEN, await self._ownership_grants(group)
        if not deny and group is None:
            grants = await self._process_criteria_permissions(own, PermissionType.READ)
            grants += await self._users_to_permissions(await self._kb_privileged_users(kb_id), PermissionType.READ)
            if not self.apply_article_read_criteria:
                grants += await self._kb_contributor_grants(kb_id)
            return True, AccessRule.RESTRICTED, grants

        criteria = await self._kb_criteria(kb_id)
        held = deny if self.apply_article_read_criteria else []
        readers = await self._effective_users(criteria[ServiceNowDictKeys.READ], criteria[ServiceNowDictKeys.CANNOT_READ] + deny)
        contributors = await self._effective_users(
            criteria[ServiceNowDictKeys.WRITE], criteria[ServiceNowDictKeys.CANNOT_WRITE] + held
        )
        if own:
            article_readers = await self._effective_users(own, [])
            readers &= article_readers
            if self.apply_article_read_criteria:
                contributors &= article_readers
        users = readers | contributors | await self._kb_privileged_users(kb_id)
        if group is not None:
            members, _ = await self._members_of(ServiceNowFields.GROUP, [group.sys_id])
            users |= members | set(self._split_ids(group.manager))
        return False, AccessRule.OPEN, await self._users_to_permissions(users, PermissionType.READ)

    def _active_group(self, group_id: object) -> SysUserGroup | None:
        """The synced, active sys_user_group ``group_id``; None otherwise (an inactive group grants nothing)."""
        group = self._groups.get(str(group_id or ""))
        return group if group is not None and self._is_active(group) else None

    async def _ownership_grants(self, group: SysUserGroup | None) -> list[Permission]:
        """An article's ownership group reads it in every state, and so does the group's manager."""
        if group is None:
            return []
        return [
            Permission(external_id=group.sys_id, type=PermissionType.READ, entity_type=EntityType.GROUP),
            *await self._users_to_permissions(set(self._split_ids(group.manager)), PermissionType.READ),
        ]

    async def _kb_contributor_grants(self, kb_id: str) -> list[Permission]:
        """The KB's contributors (Can Contribute minus Cannot Contribute) as READ grants, per run."""
        if kb_id not in self._kb_contributor_cache:
            criteria = await self._kb_criteria(kb_id)
            self._kb_contributor_cache[kb_id] = await self._process_criteria_permissions(
                criteria[ServiceNowDictKeys.WRITE], PermissionType.READ,
                deny_criteria_ids=criteria[ServiceNowDictKeys.CANNOT_WRITE],
            )
        return self._kb_contributor_cache[kb_id]

    async def _kb_privileged_users(self, kb_id: str | None) -> set[str]:
        """Source ids of the admin users plus the KB's owner and managers (read with the KB in
        _sync_knowledge_bases, else here). Raises if unreadable, so nothing is cached and the
        article keeps its stored grants."""
        if self._admin_source_ids is None:
            self._admin_source_ids = await self._get_admin_users()
        people = set(self._admin_source_ids)
        if kb_id and kb_id not in self._kb_owner_cache:
            ids: set[str] = set()
            async for rows in self._iter_table(
                ServiceNowTables.KB_KNOWLEDGE_BASE,
                f"{ServiceNowFields.SYS_ID}={kb_id}",
                [ServiceNowFields.OWNER, ServiceNowFields.KB_MANAGERS],
            ):
                for row in rows:
                    ids |= set(self._split_ids(getattr(row, ServiceNowFields.OWNER, None)))
                    ids |= set(self._split_ids(getattr(row, ServiceNowFields.KB_MANAGERS, None)))
            self._kb_owner_cache[kb_id] = ids
        return people | self._kb_owner_cache.get(kb_id, set())

    @staticmethod
    def _criteria_is_active(record: TableAPIRecord) -> bool:
        return str(getattr(record, ServiceNowFields.ACTIVE, "true")).lower() != "false"

    def _dims(self, record: TableAPIRecord) -> list[str]:
        """The populated user, group, role, department, location and company fields."""
        return [d for d in self._CRITERIA_DIMENSIONS if self._split_ids(getattr(record, d, None))]

    @staticmethod
    def _match_all(record: TableAPIRecord) -> bool:
        return str(getattr(record, ServiceNowFields.MATCH_ALL, "false")).lower() == "true"

    @staticmethod
    def _unreadable(record: TableAPIRecord) -> bool:
        """A row this account could not read (see _fetch_user_criteria)."""
        return getattr(record, "unreadable", None) == "true"

    @staticmethod
    def _has_script(record: TableAPIRecord) -> bool:
        """ServiceNow runs a criterion's script whenever it sets ``answer``, whatever ``advanced``
        says. The field's default is a comment that sets nothing, so a script that is only
        comments and whitespace is no script; anything else counts as one."""
        return bool(_SCRIPT_COMMENTS.sub("", str(getattr(record, ServiceNowFields.SCRIPT, None) or "")).strip())

    def _is_has_role(self, record: TableAPIRecord) -> bool:
        """The out-of-box "Has atleast one role apart from snc_internal KB" criterion, unedited (its
        script, no field): the script is evaluated here (see _has_role_users). Any other is not."""
        return (
            record.sys_id == ServiceNowDefaults.HAS_ROLE_CRITERIA
            and not self._dims(record)
            and " ".join(str(getattr(record, ServiceNowFields.SCRIPT, None) or "").split())
            == " ".join(ServiceNowDefaults.HAS_ROLE_CRITERIA_SCRIPT.split())
        )

    def _has_unevaluated_script(self, record: TableAPIRecord) -> bool:
        return self._has_script(record) and not self._is_has_role(record) and not self._is_guest(record)

    def _has_unevaluable_condition(self, record: TableAPIRecord) -> bool:
        """A script, or a row that could not be read: conditions not evaluated here."""
        return self._unreadable(record) or self._has_script(record)

    def _is_everyone(self, record: TableAPIRecord) -> bool:
        """An active criterion with no field and no script matches every user (OOB "Any User")."""
        return self._criteria_is_active(record) and not self._has_unevaluable_condition(record) and not self._dims(record)

    def _deny_reach_unknown(self, record: TableAPIRecord) -> bool:
        """Whom a deny matches cannot be told. A script is OR'd with the fields (so it may reach
        anyone), except under Match All with fields, where it is AND'd: the field matches are then
        a superset of the users it denies."""
        if self._unreadable(record):
            return True
        return self._has_unevaluated_script(record) and not (self._match_all(record) and self._dims(record))

    def _allow_rows(self, records: list[TableAPIRecord]) -> list[TableAPIRecord]:
        """The active Can Read / Can Contribute rows that may grant. A condition not evaluated here
        counts as not met: under Match All the row grants nobody, under Match Any only its
        evaluated fields grant (they are all the evaluation below reads). A row that could not be
        read raises _CriteriaHidden: whom it grants is unknown, which is not "nobody"."""
        hidden = [r.sys_id for r in records if self._criteria_is_active(r) and self._unreadable(r)]
        if hidden:
            raise _CriteriaHidden(f"user criteria {', '.join(hidden)} could not be read")
        return [
            r for r in records
            if self._criteria_is_active(r)
            and not (self._match_all(r) and self._has_unevaluated_script(r))
        ]

    def _needs_user_resolution(self, record: TableAPIRecord) -> bool:
        return (
            (self._match_all(record) and len(self._dims(record)) > 1)
            or self._is_has_role(record) or self._is_guest(record)
        )

    async def _fetch_user_criteria(self, criteria_ids: list[str]) -> list[TableAPIRecord]:
        """user_criteria rows by sys_id, each read once per sync run. Raises on an API error.

        An id the listing does not return is deleted when ServiceNow counts no row for it, and
        then left out. Otherwise an ACL hid it: it comes back as an unreadable row, which grants
        nobody and, as a deny, denies everyone it may cover; the full sync unlinks nothing.
        """
        if await self._copies_no_source_access():
            return []
        missing = [c for c in dict.fromkeys(criteria_ids) if c not in self._criteria_rows]
        fields = [
            ServiceNowFields.SYS_ID, ServiceNowFields.ACTIVE, ServiceNowFields.MATCH_ALL,
            ServiceNowFields.ADVANCED, ServiceNowFields.SCRIPT, *self._CRITERIA_DIMENSIONS,
        ]
        for chunk in self._chunk_ids(missing):
            async for page in self._iter_table(
                ServiceNowTables.USER_CRITERIA,
                f"{ServiceNowFields.SYS_ID}IN{CommonStrings.COMMA.join(chunk)}",
                fields,
                narrow=True,
            ):
                for r in page:
                    # A field ACL leaves the key out (an empty field it shows is ""): read without
                    # it, the row could pass for "Any User".
                    shown = r.model_dump()
                    hidden = any(f not in shown for f in fields)
                    if hidden:
                        self.logger.warning(f"User criteria {r.sys_id} has fields hidden from this account; it grants nobody and denies everyone")
                        self._note_criteria("hidden", r.sys_id)
                        self._sync_read_incomplete = True
                    self._criteria_rows[r.sys_id] = TableAPIRecord(
                        sys_id=r.sys_id, active=shown.get(ServiceNowFields.ACTIVE, "true"), unreadable="true"
                    ) if hidden else r
        for c in missing:
            row = self._criteria_rows.get(c)
            if row is not None and self._has_unevaluated_script(row):
                self._note_criteria("scripted", c)
            if c in self._criteria_rows:
                continue
            if await self._gone_at_source(ServiceNowTables.USER_CRITERIA, f"{ServiceNowFields.SYS_ID}={c}"):
                self._criteria_rows[c] = None  # deleted: not asked for again this run
            else:
                self.logger.warning(f"User criteria {c} is hidden from this account; it grants nobody and denies everyone")
                self._note_criteria("hidden", c)
                self._criteria_rows[c] = TableAPIRecord(sys_id=c, active="true", unreadable="true")
                self._sync_read_incomplete = True
        return [r for c in dict.fromkeys(criteria_ids) if (r := self._criteria_rows.get(c)) is not None]

    async def _with_child_groups(self, group_ids: list[str], listings: list[_Listing] | None = None) -> list[str]:
        """The groups and all their active descendants (sys_user_group.parent): ServiceNow group
        criteria match the members of child groups, and an inactive group matches nobody.
        Raises on any read error. ``listings`` gets how each read went."""
        # Not from a group known to be inactive: its active children would match through it. An
        # unknown one is kept, so a deny that names a hidden group still fails closed.
        group_ids = [g for g in group_ids if g not in self._groups or self._is_active(self._groups[g])]
        seen = set(group_ids)
        frontier = list(group_ids)
        while frontier:
            children: list[str] = []
            for chunk in self._chunk_ids(frontier):
                listing = _Listing()
                async for rows in self._iter_table(
                    ServiceNowTables.SYS_USER_GROUP,
                    f"{ServiceNowFields.PARENT}IN{CommonStrings.COMMA.join(chunk)}^{ServiceNowFields.ACTIVE}=true",
                    [ServiceNowFields.SYS_ID],
                    listing=listing, narrow=True,
                ):
                    children.extend(r.sys_id for r in rows if getattr(r, "sys_id", None))
                if listings is not None:
                    listings.append(listing)
            frontier = [c for c in children if c not in seen]
            seen.update(frontier)
        return sorted(seen)

    async def _users_matching_criteria(self, record: TableAPIRecord, *, deny: bool = False) -> set[str] | None:
        """sys_ids of the active users one user_criteria row matches, read once per run. Raises if
        any read fails.

        Populated fields are OR'd, or AND'd under Match All; values inside one field are OR'd.
        A group includes its active child groups; departments, locations and companies match
        exactly. A role matches through sys_user_has_role, which already has a row for each role
        a user gets from a group or a containing role. No field and no script: every user. A
        script is not evaluated here (see _allow_rows and _deny_reach_unknown). As a deny, None
        when an ACL hid some of the rows read: whom it denies is then unknown.
        """
        key = (record.sys_id, deny)
        if key in self._criteria_users:
            return self._criteria_users[key]
        if self._is_everyone(record):
            self._criteria_users[key] = set(await self._emails_by_source_id())  # the same set for every article
            return self._criteria_users[key]
        if self._is_has_role(record):
            users = await self._has_role_users()
            # Unknown (the plugin state could not be read): nobody as an allow, everyone as a deny.
            self._criteria_users[key] = None if users is None and deny else (users or set())
            return self._criteria_users[key]
        partial = False
        per_dimension: list[set[str]] = []
        for dim in self._dims(record):
            ids = self._split_ids(getattr(record, dim, None))
            if dim == ServiceNowFields.USER:
                per_dimension.append(set(ids))
                continue
            users, hidden = await self._members_of(dim, ids)
            partial |= hidden
            per_dimension.append(users)
        if self._is_guest(record):
            per_dimension.append(await self._guest_ids())  # its script, OR'd or AND'd like a field
        if not per_dimension:
            matched: set[str] = set()  # a script and no field: not evaluated here, so nobody
        elif self._match_all(record):
            matched = set.intersection(*per_dimension)
        else:
            matched = set.union(*per_dimension)
        result = None if deny and partial else matched
        self._criteria_users[key] = result
        return result

    async def _members_of(self, dim: str, ids: list[str]) -> tuple[set[str], bool]:
        """The active users that the groups (with their active child groups), roles, departments,
        locations or companies ``ids`` hold, for the criteria field ``dim``, and whether an ACL hid
        some of the rows they come from. From this run's own listings when it made them (see
        _held), else read here. Raises if a read fails."""
        if dim in self._held:
            members, hidden = self._held[dim]
            return set().union(*(members.get(i, ()) for i in ids)), hidden
        active = f"{ServiceNowFields.USER}.{ServiceNowFields.ACTIVE}=true"
        table, filter_field, user_field, condition = {
            ServiceNowFields.GROUP: (
                ServiceNowTables.SYS_USER_GRMEMBER, ServiceNowFields.GROUP, ServiceNowFields.USER,
                f"{active}^{ServiceNowFields.GROUP}.{ServiceNowFields.ACTIVE}=true",
            ),
            ServiceNowFields.ROLE: (
                ServiceNowTables.SYS_USER_HAS_ROLE, ServiceNowFields.ROLE, ServiceNowFields.USER,
                f"{active}^{ServiceNowFields.STATE}={ServiceNowFields.ACTIVE}",
            ),
        }.get(dim) or (ServiceNowTables.SYS_USER, dim, ServiceNowFields.SYS_ID, f"{ServiceNowFields.ACTIVE}=true")
        listings: list[_Listing] = []
        if dim == ServiceNowFields.GROUP:
            ids = await self._with_child_groups(ids, listings)
        users: set[str] = set()
        for chunk in self._chunk_ids(ids):
            listing = _Listing()
            async for rows in self._iter_table(
                table, f"{filter_field}IN{CommonStrings.COMMA.join(chunk)}^{condition}", [user_field],
                page=ServiceNowDefaults.ID_PAGE_SIZE, listing=listing, narrow=True,
            ):
                users.update(str(getattr(r, user_field)) for r in rows if getattr(r, user_field, None))
            listings.append(listing)
        return users, any(listing.partial for listing in listings)

    async def _has_role_users(self) -> set[str] | None:
        """The active users the out-of-box "Has a role" criterion matches, per run. Its script: a
        user with at least one sys_user_has_role row (inherited rows count, whatever their state),
        or, while the plugin com.glide.explicit_roles is active, one with snc_internal and at least
        two rows. None when the plugin's state could not be read."""
        if self._has_role_users_cache is None:
            try:
                plugin = await self._table_get(
                    ServiceNowTables.V_PLUGIN,
                    sysparm_query=f"{ServiceNowFields.ID}={ServiceNowDefaults.EXPLICIT_ROLES_PLUGIN}",
                    sysparm_fields=ServiceNowFields.ACTIVE, sysparm_limit=ServiceNowDefaults.DEFAULT_LIMIT,
                    sysparm_no_count=None,
                )
            except ServiceNowAPIError as e:
                _raise_if_run_stop(e)
                self.logger.warning(f"Could not read whether {ServiceNowDefaults.EXPLICIT_ROLES_PLUGIN} is active: {e}")
                return None
            if plugin.total_count is None or (plugin.total_count and not plugin.result):
                return None  # hidden from this account
            explicit = any(str(r.get(ServiceNowFields.ACTIVE)) == ServiceNowFields.ACTIVE for r in plugin.result)
            roles = self._role_names  # this run's role listing, when it made one (see _sync_roles)
            if roles is None:
                roles = defaultdict(list)
                async for rows in self._iter_table(
                    ServiceNowTables.SYS_USER_HAS_ROLE, f"{ServiceNowFields.USER}.{ServiceNowFields.ACTIVE}=true",
                    [ServiceNowFields.USER, ServiceNowFields.ROLE_NAME], page=ServiceNowDefaults.ID_PAGE_SIZE,
                ):
                    for r in rows:
                        if r.get(ServiceNowFields.USER):
                            roles[str(r.get(ServiceNowFields.USER))].append(str(r.get(ServiceNowFields.ROLE_NAME) or ""))
            self._has_role_users_cache = {
                user for user, names in roles.items()
                if (ServiceNowRoles.SNC_INTERNAL in names and len(names) >= 2 if explicit else len(names) >= 1)
            }
        return self._has_role_users_cache

    def _transform_to_user_group(
        self, group_data: SysUserGroup
    ) -> Optional[AppUserGroup]:
        """
        Transform ServiceNow group to AppUserGroup entity.

        Args:
            group_data: ServiceNow SysUserGroup Pydantic model

        Returns:
            AppUserGroup: Transformed user group entity or None if invalid
        """
        try:
            sys_id = group_data.sys_id
            name = group_data.name or ""

            if not sys_id or not name:
                return None

            # Parse timestamps
            source_created_at = None
            source_updated_at = None
            if group_data.sys_created_on:
                source_created_at = datetime_to_epoch_ms(group_data.sys_created_on, ServiceNowDefaults.DATETIME_FORMAT)
            if group_data.sys_updated_on:
                source_updated_at = datetime_to_epoch_ms(group_data.sys_updated_on, ServiceNowDefaults.DATETIME_FORMAT)

            # Create AppUserGroup (for user groups, not record groups)
            user_group = AppUserGroup(
                app_name=Connectors.SERVICENOW,
                connector_id=self.connector_id,
                source_user_group_id=sys_id,
                name=name,
                org_id=self.data_entities_processor.org_id,
                source_created_at=source_created_at,
                source_updated_at=source_updated_at,
            )

            return user_group

        except Exception as e:
            self.logger.error(f"Error transforming group {getattr(group_data, 'sys_id', 'unknown')}: {e}", exc_info=True)
            self._sync_read_incomplete = True
            return None

    def _transform_to_organizational_group(
        self, entity_data: TableAPIRecord, prefix: str
    ) -> Optional[AppUserGroup]:
        """
        Transform ServiceNow organizational entity to AppUserGroup.

        This is a generic transform method for companies, departments and locations.

        Args:
            entity_data: ServiceNow entity TableAPIRecord (company, department, location)
            prefix: Name prefix (COMPANY_, DEPARTMENT_, LOCATION_)

        Returns:
            AppUserGroup: Transformed organizational group or None if invalid
        """
        try:
            sys_id = entity_data.sys_id
            name = entity_data.name or ""

            if not sys_id or not name:
                return None

            # Parse timestamps
            source_created_at = None
            source_updated_at = None
            if entity_data.sys_created_on:
                source_created_at = datetime_to_epoch_ms(
                    entity_data.sys_created_on, ServiceNowDefaults.DATETIME_FORMAT
                )
            if entity_data.sys_updated_on:
                source_updated_at = datetime_to_epoch_ms(
                    entity_data.sys_updated_on, ServiceNowDefaults.DATETIME_FORMAT
                )

            # Create AppUserGroup with prefix
            org_group = AppUserGroup(
                app_name=Connectors.SERVICENOW,
                connector_id=self.connector_id,
                source_user_group_id=sys_id,
                name=f"{prefix}{name}",
                description=f"ServiceNow {prefix.rstrip('_')}: {name}",
                org_id=self.data_entities_processor.org_id,
                source_created_at=source_created_at,
                source_updated_at=source_updated_at,
            )

            return org_group

        except Exception as e:
            self.logger.error(
                f"Error transforming organizational entity {getattr(entity_data, 'sys_id', 'unknown')}: {e}",
                exc_info=True,
            )
            self._sync_read_incomplete = True
            return None
