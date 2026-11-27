"""SharePoint Online: reading who may see what from SharePoint and Entra ID.

Site, library, list, page and file grants; SharePoint site groups and Entra groups with their
members. The free connector keeps listing and syncing content and calls these methods; without a
licence key a company-wide connector reads none of this (the processor's licence gate).
"""

import asyncio
import re
import uuid
from http import HTTPStatus
from typing import List, Optional, Tuple

from kiota_abstractions.base_request_configuration import RequestConfiguration
from msgraph.generated.drives.item.drive_item_request_builder import (
    DriveItemRequestBuilder,
)
from msgraph.generated.groups.item.group_item_request_builder import (
    GroupItemRequestBuilder,
)
from msgraph.generated.models.drive_item import DriveItem
from msgraph.generated.models.group import Group
from msgraph.generated.models.site import Site

from app.config.constants.arangodb import (
    CollectionNames,
)
from app.connectors.core.base.sync_point.sync_point import (
    SyncDataPointType,
    generate_record_sync_point_key,
)
from app.connectors.sources.microsoft.common.expired_group_delta import (
    GROUPS_READ_AGAIN,
    remove_groups_gone_from_directory,
)
from app.connectors.sources.microsoft.common.msgraph_client import (
    map_msgraph_roles_to_permission_type,
)
from app.connectors.sources.microsoft.common.outlook_constants import (
    is_expired_delta_link_error,
)
from app.connectors.sources.microsoft.common.sharepoint_permission_scope import (
    GUID_PATTERN,
    SHAREPOINT_ITEMS_PAGE_SIZE,
    ListPermissionScope,
    drive_item_unique_id,
)
from app.connectors.sources.microsoft.sharepoint_online.container_tracker import (
    ContainerTracker,
)
from app.connectors.sources.microsoft.sharepoint_online.everyone_groups import (
    everyone_group_for_claim,
)
from app.connectors.sources.microsoft.sharepoint_online.utils import (
    sanitize_azure_error,
)
from app.models.entities import (
    AppUser,
    AppUserGroup,
)
from app.models.permission import EntityType, Permission, PermissionType
from app.services.notification.types import NotificationSeverity, NotificationType
from app.utils.time_conversion import get_epoch_timestamp_in_ms

SP_PRINCIPAL_USER = 1
SP_PRINCIPAL_SECURITY_GROUP = 4
SP_PRINCIPAL_SHAREPOINT_GROUP = 8
# RoleTypeKind 1 is Limited Access: SharePoint grants it so a principal can reach a
# uniquely shared child, and it opens nothing at the level it is assigned on.
SP_LIMITED_ACCESS_ROLE_KIND = 1
SP_LIMITED_ACCESS_ROLE_NAMES = frozenset({"Limited Access", "Web-Only Limited Access"})

TENANT_CLAIM_PATTERN = re.compile(r'\|tenant\|([0-9a-fA-F-]{36})')
DIRECTORY_ROLE_ODATA_TYPE = "#microsoft.graph.directoryRole"
M365_OWNERS_DISPLAY_SUFFIX = " Owners"

_PERMISSION_RANK = {PermissionType.READ: 0, PermissionType.WRITE: 1, PermissionType.OWNER: 2}


def is_org_wide_claim(login_name: str) -> bool:
    """Everyone except external users, or Everyone."""
    return 'spo-grid-all-users' in login_name or 'c:0(.s|true' in login_name


def is_m365_owners_claim(login_name: str) -> bool:
    return 'federateddirectoryclaimprovider' in login_name.lower() and login_name.lower().endswith('_o')


def role_bindings_to_permission_type(bindings: list[dict]) -> PermissionType | None:
    """The role a SharePoint role assignment grants, or None when it grants only Limited Access."""
    granting = [
        b for b in bindings
        if b.get('RoleTypeKind') != SP_LIMITED_ACCESS_ROLE_KIND and b.get('Name') not in SP_LIMITED_ACCESS_ROLE_NAMES
    ]
    if not granting:
        return None
    names = [b.get('Name', '') for b in granting]
    if any(name in ('Full Control', 'Design') for name in names):
        return PermissionType.OWNER
    if any(name in ('Edit', 'Contribute') for name in names):
        return PermissionType.WRITE
    return PermissionType.READ


def unique_file_grants(role_grants: list[Permission], graph_permissions: list[Permission]) -> list[Permission]:
    """Role assignments from SharePoint, plus link grants that only Graph reports."""
    merged: dict[tuple[str, str], Permission] = {}
    for permission in role_grants:
        merge_permission(merged, permission)
    for permission in graph_permissions:
        if permission.entity_type == EntityType.ORG or permission.link_grant:
            merge_permission(merged, permission)
    return list(merged.values())


def merge_permission(permissions: dict[tuple[str, str], Permission], permission: Permission) -> None:
    """Keep one grant per principal, at the strongest role it is given."""
    principal = permission.email or permission.external_id or ''
    key = (permission.entity_type.value, principal.lower())
    current = permissions.get(key)
    if current is None or _PERMISSION_RANK[permission.type] > _PERMISSION_RANK[current.type]:
        permissions[key] = permission


class SharePointPermissionsMixin:
    """The permission-reading half of ``SharePointConnector``."""

    async def _copies_no_source_access(self) -> bool:
        """Without a licence key a company-wide connector reads no access from SharePoint. The processor then keeps
        every stored grant and source group, and a record's inherit edge, whatever is handed to it
        (``skips_permission_writes``), so the empty answers below only keep the run whole: None would count each
        site, library and page as unreadable, fail the run and drop new files, though nothing was wrong with them."""
        return await self.data_entities_processor.skips_permission_writes(self.connector_id)

    async def _get_site_permissions(self, site_id: str) -> list[Permission] | None:
        """The site's (web's) role assignments as grants, or None when they can't be read.

        Every principal type counts: SharePoint groups, Entra groups, people granted on the site directly,
        and the tenant-wide claims. None keeps what is stored: an empty list would remove everyone's access.
        """
        if await self._copies_no_source_access():
            return []
        site_metadata = self.site_cache.get(site_id)
        if not site_metadata or not site_metadata.site_url:
            self.logger.error(f"❌ Site metadata/URL not found for {site_id}")
            return None
        return await self._get_role_assignment_grants(
            f"{site_metadata.site_url.rstrip('/')}/_api/web/roleassignments?$expand=Member,RoleDefinitionBindings",
            site_id,
        )

    @staticmethod
    def _graph_member_user(item: object) -> dict | None:
        """A Graph directory object as ``{'id', 'email', 'name'}`` when it is a user with an address."""
        odata_type = getattr(item, 'odata_type', '').lower()
        if 'user' in odata_type or hasattr(item, 'user_principal_name'):
            email = getattr(item, 'mail', None) or getattr(item, 'user_principal_name', None)
            user_id = getattr(item, 'id', None)
            if email and user_id:
                return {'id': user_id, 'email': email, 'name': getattr(item, 'display_name', 'Unknown')}
        return None

    def _require_known_disabled_set(self) -> None:
        """Raises while no disabled set is known: a member list filtered by an incomplete set could include an
        account the tenant has disabled, so what it grants is unknown and the caller keeps what is stored."""
        if self._disabled_set_unknown:
            raise RuntimeError("the disabled accounts are not known yet; no group member list is read")

    async def _fetch_graph_group_members(self, group_id: str, is_owner: bool = False) -> List[dict]:
        """The enabled user accounts of an Entra group (through nested groups), or of its owners.

        Raises when any page can't be read: a partial list saved as the group's members would take
        access away from everyone on the pages that failed. A group Graph answers 404 for has been deleted
        and has nobody in it; SharePoint keeps the claims of deleted Microsoft 365 groups in its site groups
        and role assignments.
        """
        self._require_known_disabled_set()
        group = self.client.groups.by_group_id(group_id)
        builder = group.owners if is_owner else group.transitive_members
        users: list[dict] = []
        try:
            async with self.rate_limiter:
                response = await builder.get()
        except Exception as e:
            if getattr(e, 'response_status_code', None) != HTTPStatus.NOT_FOUND:
                raise
            self.logger.warning(f"⚠️ Entra group {group_id} no longer exists; it grants nobody")
            return []
        while response:
            for item in response.value or []:
                user = self._graph_member_user(item)
                if user and user['email'].lower() not in self._inactive_emails:
                    users.append(user)
            next_link = getattr(response, 'odata_next_link', None)
            if not next_link:
                break
            async with self.rate_limiter:
                response = await builder.with_url(next_link).get()
        return users

    async def _get_drive_permissions(self, site_id: str, drive_id: str) -> List[Permission]:
        """Get permissions for a document library."""
        try:
            permissions = []
            encoded_site_id = self._construct_site_url(site_id)

            async with self.rate_limiter:
                # Use the correct Graph API structure for drive permissions
                # For SharePoint, we need to get the root item first, then its permissions
                root_item = await self._safe_api_call(
                    lambda: self.client.sites.by_site_id(encoded_site_id).drives.by_drive_id(drive_id).root.get()
                )
                if root_item:
                    perms_response = await self._safe_api_call(
                        lambda: self.client.sites.by_site_id(encoded_site_id).drives.by_drive_id(drive_id).items.by_drive_item_id(root_item.id).permissions.get()
                    )
                else:
                    perms_response = None

            if perms_response and perms_response.value:
                permissions = await self._convert_to_permissions(perms_response.value, site_id=site_id)

            return permissions

        except Exception as e:
            self.logger.debug(f"❌ Could not get drive permissions: {e}")
            return []

    async def _get_item_permissions(self, site_id: str, drive_id: str, item_id: str) -> list[Permission] | None:
        """A drive item's Graph permissions, every page of them, or None when they can't be read."""
        try:
            graph_permissions = await self.msgraph_client.get_file_permission(drive_id, item_id, raise_on_error=True)
            return await self._convert_to_permissions(graph_permissions, site_id=site_id)
        except Exception as e:
            self.logger.warning(f"⚠️ Could not read the permissions of item {item_id}: {e}")
            return None

    async def _get_list_permissions(self, site_id: str, list_id: str) -> List[Permission]:
        """Get permissions for a SharePoint list."""
        if await self._copies_no_source_access():
            return []
        try:
            # SharePoint lists don't have direct permissions endpoint
            # Instead, we'll return site-level permissions or empty list
            # This is a limitation of the Microsoft Graph API for SharePoint lists
            self.logger.debug(f"List permissions not directly accessible via Graph API for list {list_id}")
            return []

        except Exception as e:
            self.logger.debug(f"❌ Could not get list permissions: {e}")
            return []

    async def _get_list_item_permissions(self, site_id: str, list_id: str, item_id: str) -> List[Permission]:
        """Get permissions for a list item."""
        if await self._copies_no_source_access():
            return []
        try:
            # SharePoint list items don't have direct permissions endpoint
            # Instead, we'll return site-level permissions or empty list
            # This is a limitation of the Microsoft Graph API for SharePoint list items
            self.logger.debug(f"List item permissions not directly accessible via Graph API for item {item_id}")
            return []

        except Exception as e:
            self.logger.debug(f"❌ Could not get list item permissions: {e}")
            return []

    async def _get_page_permissions(self, site_id: str, page_id: str) -> List[Permission]:
        """Get permissions for a SharePoint page."""
        if await self._copies_no_source_access():
            return []
        try:
            # SharePoint pages don't have direct permissions endpoint
            # Instead, we'll return site-level permissions or empty list
            # This is a limitation of the Microsoft Graph API for SharePoint pages
            self.logger.debug(f"Page permissions not directly accessible via Graph API for page {page_id}")
            return []

        except Exception as e:
            self.logger.debug(f"❌ Could not get page permissions: {e}")
            return []

    async def _convert_to_permissions(self, msgraph_permissions: List, site_id: str | None = None) -> list[Permission]:
        """
        Convert Microsoft Graph permissions to our Permission model.
        Handles user, group and SharePoint site group permissions; site groups
        need the ``site_id`` they were synced under.
        """
        permissions = []


        for perm in msgraph_permissions:
            try:
                # Handle user permissions
                if hasattr(perm, 'granted_to_v2') and perm.granted_to_v2:
                    granted_to = perm.granted_to_v2
                    role = map_msgraph_roles_to_permission_type(perm.roles)
                    if hasattr(granted_to, 'user') and granted_to.user and not self._is_inactive_identity(granted_to.user):
                        user = granted_to.user
                        permissions.append(Permission(
                            external_id=user.id,
                            email=user.additional_data.get("email", None) if hasattr(user, 'additional_data') else None,
                            type=role,
                            entity_type=EntityType.USER
                        ))
                    if hasattr(granted_to, 'group') and granted_to.group:
                        permissions.extend(
                            await self._graph_group_grant(granted_to.group, getattr(granted_to, 'site_user', None), role)
                        )
                    site_group = getattr(granted_to, 'site_group', None)
                    site_group_id = getattr(site_group, 'id', None) if site_group else None
                    if site_id and isinstance(site_group_id, str):
                        permissions.extend(self._site_group_permissions(site_id, site_group_id, role))


                # Handle group permissions
                if hasattr(perm, 'granted_to_identities_v2') and perm.granted_to_identities_v2:
                    for identity in perm.granted_to_identities_v2:
                        if hasattr(identity, 'group') and identity.group:
                            link_groups = await self._graph_group_grant(
                                identity.group,
                                getattr(identity, 'site_user', None),
                                map_msgraph_roles_to_permission_type(perm.roles),
                            )
                            for grant in link_groups:
                                grant.link_grant = True
                            permissions.extend(link_groups)
                        elif hasattr(identity, 'user') and identity.user and not self._is_inactive_identity(identity.user):
                            user = identity.user
                            permissions.append(Permission(
                                external_id=user.id,
                                email=user.additional_data.get("email", None) if hasattr(user, 'additional_data') else None,
                                type=map_msgraph_roles_to_permission_type(perm.roles),
                                entity_type=EntityType.USER,
                                link_grant=True,
                            ))

                # A link grants only the identities Graph lists on it. An organization or
                # anonymous link opens the item only to the people who redeem it. Graph does
                # not list them; on a uniquely permissioned item they arrive through the
                # link's SharePoint group in its role assignments.

            except (AttributeError, TypeError, ValueError) as e:
                # A permission in a shape this code does not know grants nobody here.
                self.logger.error(f"❌ Error converting permission: {e}", exc_info=True)
                continue

        return permissions

    def _user_may_be_granted(self, email: str) -> bool:
        """Whether a grant naming this user is written. Never for an account the tenant has disabled or deleted.
        While no disabled set is known, only for a user the listing showed as active: the others are withheld
        without failing the item, and the next run writes them once the set is known."""
        email = email.lower()
        if email in self._inactive_emails:
            return False
        if self._disabled_set_unknown and email not in self._confirmed_active_emails:
            self._withheld_unknown_user = True
            return False
        return True

    def _is_inactive_identity(self, user: object) -> bool:
        """A Graph identity whose grant is not written (see _user_may_be_granted)."""
        email = (getattr(user, 'additional_data', None) or {}).get("email")
        if isinstance(email, str):
            return not self._user_may_be_granted(email)
        if self._disabled_set_unknown:
            # No email, so the listing cannot have shown this account active: withheld like an unlisted one.
            self._withheld_unknown_user = True
            return True
        return False

    async def _graph_group_grant(self, group: object, site_user: object, role: PermissionType) -> list[Permission]:
        """A Graph group grant. An M365 group's owners claim names the whole group, so it is narrowed to the owners."""
        if await self._is_m365_owners_grant(group, site_user):
            return await self._m365_owner_permissions(group.id, role)
        return [Permission(
            external_id=group.id,
            email=group.additional_data.get("email", None) if hasattr(group, 'additional_data') else None,
            type=role,
            entity_type=EntityType.GROUP
        )]

    async def _is_m365_owners_grant(self, group: object, site_user: object) -> bool:
        # The site user's claim says owners or members outright. Without one, an " Owners"
        # name means the owners grant only on an M365 group: a security group can be named that.
        login_name = getattr(site_user, 'login_name', None) if site_user else None
        if isinstance(login_name, str) and 'federateddirectoryclaimprovider' in login_name.lower():
            return is_m365_owners_claim(login_name)
        display_name = getattr(group, 'display_name', None)
        if not (isinstance(display_name, str) and display_name.endswith(M365_OWNERS_DISPLAY_SUFFIX)):
            return False
        return await self._is_unified_group(group.id) is not False

    async def _is_unified_group(self, group_id: str) -> bool | None:
        """Whether a Graph group is an M365 (Unified) group, or None when it can't be read."""
        if group_id in self._unified_groups:
            return self._unified_groups[group_id]
        is_unified = None
        try:
            query = GroupItemRequestBuilder.GroupItemRequestBuilderGetQueryParameters(select=["groupTypes"])
            async with self.rate_limiter:
                graph_group = await self.client.groups.by_group_id(group_id).get(
                    request_configuration=RequestConfiguration(query_parameters=query)
                )
            is_unified = "Unified" in (getattr(graph_group, 'group_types', None) or [])
        except Exception as e:
            self.logger.warning(
                f"⚠️ Could not read the type of group {group_id}; its Owners grant is narrowed to the group owners: {e}"
            )
        self._unified_groups[group_id] = is_unified
        return is_unified

    @staticmethod
    def _everyone_permission(group_id: str, role: PermissionType) -> Permission:
        """A grant to a tenant-wide claim, held by its group (everyone_groups.py), not by the organization."""
        return Permission(external_id=group_id, email=None, type=role, entity_type=EntityType.GROUP)

    def _site_group_permissions(self, site_id: str, group_id: object, role: PermissionType) -> list[Permission]:
        """A SharePoint site group grant, keyed as _sync_user_groups saves the group.

        Raises for a group whose members this run did not read (its site's groups could not be listed, or a
        reindex ran without listing them): whether it holds a tenant-wide claim is then unknown, so what it
        grants is unknown too, and the caller keeps what is stored.
        """
        if group_id is None or not site_id:
            return []
        if site_id not in self._site_groups_read:
            raise RuntimeError(f"the SharePoint groups of site {site_id} were not read in this run")
        if str(group_id) in self._unreadable_site_groups.get(site_id, set()):
            raise RuntimeError(f"the members of SharePoint group {group_id} on site {site_id} could not be read")
        permissions = [Permission(
            external_id=f"{site_id}-{group_id}",
            email=None,
            type=role,
            entity_type=EntityType.GROUP
        )]
        # A site group can't hold another group as a member in PipesHub, so a claim inside
        # it is granted alongside it.
        for claim_group in self._org_wide_site_groups.get(site_id, {}).get(str(group_id), ()):
            permissions.append(self._everyone_permission(claim_group, role))
        return permissions

    async def _m365_owner_permissions(self, group_id: str, role: PermissionType) -> list[Permission]:
        if group_id not in self._m365_group_owners:
            self._m365_group_owners[group_id] = await self._fetch_graph_group_members(group_id, is_owner=True)
        return [
            Permission(external_id=owner['id'], email=owner['email'], type=role, entity_type=EntityType.USER)
            for owner in self._m365_group_owners[group_id]
        ]

    async def _sharepoint_principal_permissions(self, principal: dict, role: PermissionType, site_id: str) -> list[Permission]:
        """Grants for one SharePoint principal: a role assignment's Member, or a user of an associated site group."""
        login_name = principal.get('LoginName') or ''
        principal_type = principal.get('PrincipalType')

        if 'federateddirectoryclaimprovider' in login_name:
            match = GUID_PATTERN.search(login_name)
            if not match:
                self.logger.warning(f"⚠️ Could not extract a group id from M365 claim {login_name}")
                return []
            group_id = match.group(1)
            if is_m365_owners_claim(login_name):
                return await self._m365_owner_permissions(group_id, role)
            self.logger.info(f"      🔵 Found M365 Group: {principal.get('Title', 'M365 Group')} (ID: {group_id})")
            return [Permission(external_id=group_id, email=None, type=role, entity_type=EntityType.GROUP)]

        claim_group = everyone_group_for_claim(login_name)
        if claim_group:
            return [self._everyone_permission(claim_group, role)]

        if principal_type == SP_PRINCIPAL_SECURITY_GROUP:
            self.logger.info(f"🔒 Found Security Group: {principal.get('Title', 'Security Group')} ({login_name})")
            return await self._tenant_claim_permissions(login_name, role)

        if principal_type == SP_PRINCIPAL_SHAREPOINT_GROUP:
            return self._site_group_permissions(site_id, principal.get('Id'), role)

        if principal_type == SP_PRINCIPAL_USER:
            email = principal.get('Email') or principal.get('UserPrincipalName')
            if email and self._user_may_be_granted(email):
                return [Permission(external_id=str(principal.get('Id')), email=email, type=role, entity_type=EntityType.USER)]
        return []

    async def _tenant_claim_permissions(self, login_name: str, role: PermissionType) -> list[Permission]:
        """A ``c:0t.c|tenant|<guid>`` claim: a security group, or a directory role expanded to its members."""
        match = TENANT_CLAIM_PATTERN.search(login_name)
        if not match:
            self.logger.warning(f"   -> ⚠️ Could not extract GUID from Security Group LoginName: {login_name}")
            return []
        object_id = match.group(1)
        if await self._get_directory_object_type(object_id) == DIRECTORY_ROLE_ODATA_TYPE:
            return [
                Permission(external_id=member['id'], email=member['email'], type=role, entity_type=EntityType.USER)
                for member in await self._get_directory_role_member_users(object_id)
            ]
        return [Permission(external_id=object_id, email=None, type=role, entity_type=EntityType.GROUP)]

    async def _get_directory_object_type(self, object_id: str) -> str | None:
        """The ``@odata.type`` of a directory object, or None when it can't be read."""
        if object_id in self._directory_object_types:
            return self._directory_object_types[object_id]
        object_type = None
        try:
            async with self.rate_limiter:
                directory_object = await self.client.directory_objects.by_directory_object_id(object_id).get()
            odata_type = getattr(directory_object, 'odata_type', None)
            object_type = odata_type if isinstance(odata_type, str) else None
        except Exception as e:
            self.logger.warning(f"⚠️ Could not resolve directory object {object_id}; treating it as a group: {e}")
        self._directory_object_types[object_id] = object_type
        return object_type

    async def _get_directory_role_member_users(self, role_id: str) -> list[dict]:
        """Users holding a directory role, directly or through a role-assignable group. Raises when the
        members can't all be read; a partial answer is not kept for the rest of the run."""
        if role_id in self._directory_role_members:
            return self._directory_role_members[role_id]
        self._require_known_disabled_set()
        users: list[dict] = []
        members = self.client.directory_roles.by_directory_role_id(role_id).members
        async with self.rate_limiter:
            response = await members.get()
        while response:
            for member in response.value or []:
                user = self._graph_member_user(member)
                if user:
                    if user['email'].lower() not in self._inactive_emails:
                        users.append(user)
                elif 'group' in (getattr(member, 'odata_type', None) or '').lower() and getattr(member, 'id', None):
                    users.extend(await self._fetch_graph_group_members(member.id))
            next_link = getattr(response, 'odata_next_link', None)
            if not next_link:
                break
            async with self.rate_limiter:
                response = await members.with_url(next_link).get()
        self._directory_role_members[role_id] = users
        return users

    async def _read_list_permission_scope(self, web_url: str, list_url: str) -> ListPermissionScope | None:
        """A list's unique-permission flag and its uniquely permissioned items, or None when they can't be read."""
        try:
            list_body: dict = {}
            async for body in self._sharepoint_rest_pages(f"{list_url}?$select=Id,HasUniqueRoleAssignments"):
                list_body = body

            unique_items: dict[str, int] = {}
            items_url = f"{list_url}/items?$select=Id,UniqueId,HasUniqueRoleAssignments&$top={SHAREPOINT_ITEMS_PAGE_SIZE}"
            async for body in self._sharepoint_rest_pages(items_url, follow_next=True):
                for item in body.get('results', []):
                    unique_id = item.get('UniqueId')
                    if item.get('HasUniqueRoleAssignments') is True and isinstance(unique_id, str):
                        unique_items[unique_id.lower()] = item.get('Id')

            return ListPermissionScope(
                web_url=web_url,
                list_url=list_url,
                has_unique_role_assignments=list_body.get('HasUniqueRoleAssignments') is True,
                unique_items=unique_items,
            )
        except Exception as e:
            self.logger.warning(
                f"⚠️ Could not read unique permissions of {list_url}; new content is saved without inheritance "
                f"and stored content keeps its access for this run: {e}"
            )
            return None

    async def _get_drive_sharepoint_ids(self, drive_id: str) -> object:
        try:
            query = DriveItemRequestBuilder.DriveItemRequestBuilderGetQueryParameters(select=["id", "sharePointIds"])
            async with self.rate_limiter:
                drive = await self.client.drives.by_drive_id(drive_id).get(
                    request_configuration=RequestConfiguration(query_parameters=query)
                )
            return getattr(drive, 'share_point_ids', None)
        except Exception as e:
            self.logger.warning(f"⚠️ Could not read the SharePoint ids of drive {drive_id}: {e}")
            return None

    async def _get_library_scope(self, drive_id: str, drive: object = None) -> ListPermissionScope | None:
        """The unique permissions of a document library, read once per sync run."""
        if drive_id in self._library_scopes:
            return self._library_scopes[drive_id]

        sharepoint_ids = getattr(drive, 'share_point_ids', None) if drive is not None else None
        if not isinstance(getattr(sharepoint_ids, 'list_id', None), str):
            sharepoint_ids = await self._get_drive_sharepoint_ids(drive_id)
        list_id = getattr(sharepoint_ids, 'list_id', None)
        web_url = getattr(sharepoint_ids, 'site_url', None)

        scope = None
        if isinstance(list_id, str) and list_id and isinstance(web_url, str) and web_url:
            web_url = web_url.rstrip('/')
            scope = await self._read_list_permission_scope(web_url, f"{web_url}/_api/web/lists(guid'{list_id}')")
        else:
            self.logger.warning(
                f"⚠️ No SharePoint list id for drive {drive_id}; new items are saved without inheritance "
                f"and stored items keep their access for this run"
            )
        self._library_scopes[drive_id] = scope
        return scope

    async def _get_site_pages_scope(self, site_id: str, web_url: str | None = None) -> ListPermissionScope | None:
        """The unique permissions of a site's Site Pages library, read once per sync run."""
        if await self._copies_no_source_access():
            return None
        if site_id in self._site_pages_scopes:
            return self._site_pages_scopes[site_id]

        if not isinstance(web_url, str) or not web_url:
            site_metadata = self.site_cache.get(site_id)
            web_url = site_metadata.site_url if site_metadata else None

        scope = None
        if isinstance(web_url, str) and web_url:
            web_url = web_url.rstrip('/')
            scope = await self._read_list_permission_scope(web_url, f"{web_url}/_api/web/lists/getbytitle('Site%20Pages')")
        else:
            self.logger.warning(
                f"⚠️ No web URL for site {site_id}; new pages are saved without inheritance "
                f"and stored pages keep their access for this run"
            )
        self._site_pages_scopes[site_id] = scope
        return scope

    async def _get_role_assignment_grants(self, role_assignments_url: str, site_id: str) -> list[Permission] | None:
        """Grants from a SharePoint role assignments collection, or None when it, or the members of a
        group it names, can't be read."""
        try:
            assignments: list[dict] = []
            async for body in self._sharepoint_rest_pages(role_assignments_url, follow_next=True):
                assignments.extend(body.get('results', []))
            return await self._role_assignments_to_permissions(assignments, site_id)
        except Exception as e:
            self.logger.warning(f"⚠️ Could not read role assignments {role_assignments_url}: {e}")
            return None

    async def _role_assignments_to_permissions(self, assignments: list[dict], site_id: str) -> list[Permission]:
        permissions: dict[tuple[str, str], Permission] = {}
        for assignment in assignments:
            bindings = (assignment.get('RoleDefinitionBindings') or {}).get('results', [])
            role = role_bindings_to_permission_type(bindings)
            if role is None:
                continue
            for permission in await self._sharepoint_principal_permissions(assignment.get('Member') or {}, role, site_id):
                merge_permission(permissions, permission)
        return list(permissions.values())

    async def _get_library_access(self, site_id: str, drive: object) -> tuple[bool, list[Permission] | None]:
        """``(inherit_permissions, grants)`` for a library; the grants are None when they can't be read."""
        drive_id = getattr(drive, 'id', None)
        gated = await self._copies_no_source_access()
        scope = None if gated else await self._get_library_scope(drive_id, drive)
        if scope is None:
            # A library's inherit edge is written from this answer even while grants are skipped, so it is the stored one.
            stored = await self.data_entities_processor.get_record_group_by_external_id(self.connector_id, drive_id)
            inherits = await self._inheritance_when_unreadable(
                CollectionNames.RECORD_GROUPS.value, stored.id if stored else None
            )
            return inherits, [] if gated else None
        if not scope.has_unique_role_assignments:
            return True, []
        return False, await self._get_role_assignment_grants(
            f"{scope.list_url}/roleassignments?$expand=Member,RoleDefinitionBindings", site_id
        )

    async def _drive_item_has_unique_permissions(self, item: DriveItem, drive_id: str) -> bool | None:
        """None when the library's unique permissions can't be read."""
        scope = await self._get_library_scope(drive_id)
        if scope is None:
            return None
        unique_id = drive_item_unique_id(item)
        return unique_id is not None and unique_id in scope.unique_items

    async def _unique_drive_item_role_grants(self, item: DriveItem, drive_id: str, site_id: str) -> list[Permission] | None:
        """Role assignments of a drive item that broke inheritance, or None when they can't be read."""
        scope = await self._get_library_scope(drive_id)
        unique_id = drive_item_unique_id(item)
        list_item_id = scope.unique_items.get(unique_id) if scope is not None and unique_id else None
        if scope is None or list_item_id is None:
            return None
        return await self._get_role_assignment_grants(
            f"{scope.list_url}/items({list_item_id})/roleassignments?$expand=Member,RoleDefinitionBindings",
            site_id,
        )

    async def _get_page_access(
        self, site_id: str, page_id: str, pages_scope: ListPermissionScope | None
    ) -> tuple[bool, list[Permission]] | None:
        """``(inherit_permissions, grants)`` for a page; None when its permissions can't be told."""
        if await self._copies_no_source_access():
            return True, []
        if pages_scope is None or not isinstance(page_id, str):
            return None
        item_id = pages_scope.unique_items.get(page_id.lower())
        if item_id is None:
            return True, []
        grants = await self._get_role_assignment_grants(
            f"{pages_scope.list_url}/items({item_id})/roleassignments?$expand=Member,RoleDefinitionBindings", site_id
        )
        if grants is None:
            return None
        return False, grants

    async def _drive_item_access(self, item: DriveItem, drive_id: str, site_id: str) -> tuple[bool, list[Permission]] | None:
        """``(inherit_permissions, grants)`` for a drive item, or None when they can't be read."""
        if await self._copies_no_source_access():
            return True, []
        has_unique_permissions = await self._drive_item_has_unique_permissions(item, drive_id)
        if has_unique_permissions is None:
            return None
        if not has_unique_permissions:
            return True, []
        role_grants = await self._unique_drive_item_role_grants(item, drive_id, site_id)
        graph_permissions = await self._get_item_permissions(site_id, drive_id, item.id)
        if role_grants is None or graph_permissions is None:
            return None
        return False, unique_file_grants(role_grants, graph_permissions)

    # User and group sync methods
    # TODO: rn sharepoint group doesnt direcctly support incremental sync need to add the logic to remove members from group if removed
    async def _sync_user_groups(self, sites: list[Site] | None = None) -> None:
        """Sync Entra groups and every site's SharePoint groups, with their members.

        A group whose members can't all be read keeps the members stored for it: writing what was read
        would take access away from everyone who was not. Such a group is reported to the run's health.
        """
        if await self._copies_no_source_access():
            return
        try:
            await self._sync_azure_ad_groups()
        except Exception as groups_error:
            self.logger.error(f"❌ Error syncing Azure AD Groups with delta: {groups_error}")
            self._health.skipped("Entra groups", groups_error)

        self.logger.info("Starting SharePoint Site Groups fetch...")
        self._org_wide_site_groups = {}
        self._unreadable_site_groups = {}
        self._site_groups_read = set()
        if sites is None:
            sites = await self._get_all_sites()
        sharepoint_groups_with_members: list[tuple[AppUserGroup, list[AppUser]]] = []
        for site in sites:
            try:
                sharepoint_groups_with_members.extend(await self._site_groups_with_members(site))
            except Exception as site_error:
                self._health.skipped(f"SharePoint groups of site {site.display_name or site.name}",
                                     sanitize_azure_error(site_error))
        if sharepoint_groups_with_members:
            await self.data_entities_processor.on_new_user_groups(sharepoint_groups_with_members)
        self.logger.info(f"Completed SharePoint group synchronization - processed {len(sharepoint_groups_with_members)} groups")

    async def _site_groups_with_members(self, site: Site) -> list[tuple[AppUserGroup, list[AppUser]]]:
        """A site's SharePoint groups with their members. A group whose members can't be read is left out
        (its stored members stay); a group the site no longer has is written with no members."""
        site_id = site.id
        site_web_url = (site.web_url or '').rstrip('/')
        if not site_web_url:
            return []
        groups: list[dict] = []
        async for body in self._sharepoint_rest_pages(f"{site_web_url}/_api/web/sitegroups", follow_next=True):
            groups.extend(body.get('results', []))

        tracker = ContainerTracker(self.drive_delta_sync_point, f"sharepoint-known-site-groups/{site_id}")
        known = await tracker.known()
        listed = {str(group.get('Id')) for group in groups}
        out: list[tuple[AppUserGroup, list[AppUser]]] = []
        for group in groups:
            group_id = group.get('Id')
            title = group.get('Title', 'N/A')
            description = group.get('Description')
            user_group = AppUserGroup(
                id=str(uuid.uuid4()),
                # Site id and group id: SharePoint group ids are only unique within a site collection.
                source_user_group_id=f"{site_id}-{group_id}",
                app_name=self.connector_name,
                connector_id=self.connector_id,
                name=title,
                description=description if description not in (None, 'N/A') else None,
            )
            try:
                principals: list[dict] = []
                async for body in self._sharepoint_rest_pages(
                    f"{site_web_url}/_api/web/sitegroups/GetById({group_id})/users", follow_next=True
                ):
                    principals.extend(body.get('results', []))
                members = await self._site_group_member_users(site_id, str(group_id), principals)
            except Exception as member_error:
                self._health.skipped(f"members of SharePoint group {title} on site {site_id}",
                                     sanitize_azure_error(member_error))
                self._unreadable_site_groups.setdefault(site_id, set()).add(str(group_id))
                continue
            out.append((user_group, members))
        for gone in known - listed:
            # The site no longer has this group: nobody is in it any more.
            out.append((AppUserGroup(source_user_group_id=f"{site_id}-{gone}", app_name=self.connector_name,
                                     connector_id=self.connector_id, name=f"Deleted group {gone}"), []))
        await tracker.remember(listed)
        self._site_groups_read.add(site_id)
        return out

    async def _site_group_member_users(self, site_id: str, group_id: str, principals: list[dict]) -> list[AppUser]:
        """The enabled people a SharePoint group's members stand for. Raises when a member group can't be read.
        A tenant-wide claim is noted for the group (see _site_group_permissions), not expanded."""
        users: list[dict] = []
        for principal in principals:
            login_name = principal.get('LoginName', '') or ''
            claim_group = everyone_group_for_claim(login_name)
            if claim_group:
                self._org_wide_site_groups.setdefault(site_id, {}).setdefault(group_id, set()).add(claim_group)
            elif 'federateddirectoryclaimprovider' in login_name.lower():
                match = GUID_PATTERN.search(login_name)
                if match:
                    # The claim says which half of an M365 group it is; the SharePoint group's
                    # title is free text, and in another language never says "Owner".
                    users.extend(await self._fetch_graph_group_members(
                        match.group(1), is_owner=is_m365_owners_claim(login_name)
                    ))
            elif principal.get('PrincipalType') == SP_PRINCIPAL_SECURITY_GROUP:
                match = TENANT_CLAIM_PATTERN.search(login_name)
                if match:
                    claim_id = match.group(1)
                    if await self._get_directory_object_type(claim_id) == DIRECTORY_ROLE_ODATA_TYPE:
                        users.extend(await self._get_directory_role_member_users(claim_id))
                    else:
                        users.extend(await self._fetch_graph_group_members(claim_id))
            else:
                email = principal.get('Email') or principal.get('UserPrincipalName')
                if email and email.lower() not in self._inactive_emails:
                    users.append({'id': str(principal.get('Id')), 'email': email, 'name': principal.get('Title')})
        return [
            AppUser(source_user_id=user['id'], email=user['email'], full_name=user.get('name'),
                    app_name=self.connector_name, connector_id=self.connector_id)
            for user in users
        ]

    async def _sync_azure_ad_groups(self) -> None:
        """Entra groups: all of them on the first run (or when every membership must be read again), then
        the groups Graph's delta reports, with the groups that contain them."""
        sync_point_key = generate_record_sync_point_key(
            SyncDataPointType.GROUPS.value,
            "organization",
            self.data_entities_processor.org_id
        )
        sync_point = await self.user_group_sync_point.read_sync_point(sync_point_key)
        # A run stopped mid-delta leaves only nextLink. Resuming there reads the page
        # it stopped on again; a full sync would start a new delta link past it, and
        # past the group deletions on the remaining pages.
        delta_link = (sync_point.get('deltaLink') or sync_point.get('nextLink')) if sync_point else None

        read_every_group = delta_link is None or self._refresh_all_groups
        caught_up, expired = True, False
        new_delta_link, link_taken = None, False
        if read_every_group:
            # Taken before the saved link is drained: a group deleted between the drain and a later link would be
            # reported by neither, and the full read does not list it.
            new_delta_link, link_taken = await self._get_initial_delta_link(), True
        if delta_link is not None:
            # Only the stored link reports the groups deleted since it was taken: the full read below lists
            # the groups that exist, and the new link starts after the deletions.
            try:
                caught_up = await self._perform_delta_sync(delta_link, sync_point_key)
            except Exception as e:
                if is_expired_delta_link_error(e):
                    # Kept, the link would fail every later run: no run can catch up on it any more.
                    self.logger.error("The saved Entra group delta link has expired; reading every group again")
                    read_every_group = expired = True
                elif read_every_group:
                    caught_up = False
                    self._health.skipped("Entra group changes", e)
                else:
                    raise
        if read_every_group:
            self.logger.info("Reading every Entra group's members...")
            if not link_taken:
                # The saved link expired: groups deleted before this one are found by the lookup below.
                new_delta_link = await self._get_initial_delta_link()
            complete = await self._perform_initial_full_sync()
            if expired:
                try:
                    await remove_groups_gone_from_directory(
                        self.data_entities_processor, self.connector_id, self.msgraph_client,
                        self._handle_delete_group, self.logger,
                    )
                except Exception as e:
                    # The expired link stays, so the next run looks for them again.
                    caught_up = False
                    self._health.skipped("Entra groups deleted at the source", e)
            if new_delta_link and complete and caught_up:
                await self.user_group_sync_point.update_sync_point(
                    sync_point_key,
                    {"nextLink": None, "deltaLink": new_delta_link}
                )
                if expired:
                    await self.notify(
                        type=NotificationType.CONNECTOR_WARNING,
                        severity=NotificationSeverity.WARNING,
                        title="Every Microsoft group was read again",
                        message=GROUPS_READ_AGAIN,
                        recipient_user_ids=[self.created_by],
                    )
                if self._listing_trusted:
                    # Imported here: the connector module imports this one.
                    from app.connectors.sources.microsoft.sharepoint_online.connector import (
                        INACTIVE_USERS_SYNC_POINT_KEY,
                    )
                    await self.user_sync_point.update_sync_point(
                        INACTIVE_USERS_SYNC_POINT_KEY, sync_point_data={"emails": sorted(self._inactive_emails)}
                    )

    async def _get_initial_delta_link(self) -> Optional[str]:
        """
        Consumes the delta API to obtain a deltaLink checkpoint.
        Called BEFORE initial full sync to ensure no changes are missed.

        Returns:
            The deltaLink string, or None if unable to obtain one.
        """
        url = "https://graph.microsoft.com/v1.0/groups/delta"
        try:
            while True:
                result = await self.msgraph_client.get_groups_delta_response(url)
                if result.get('next_link'):
                    url = result.get('next_link')
                elif result.get('delta_link'):
                    return result.get('delta_link')
                else:
                    self.logger.warning("Delta initialization: no next_link or delta_link received")
                    return None
        except Exception as e:
            self.logger.error(f"❌ Error obtaining initial delta link: {e}", exc_info=True)
            self._health.skipped("Entra group changes", e)
            return None

    def _app_user_group(self, group: object) -> AppUserGroup:
        created = getattr(group, 'created_date_time', None)
        return AppUserGroup(
            source_user_group_id=group.id,
            app_name=self.connector_name,
            connector_id=self.connector_id,
            name=getattr(group, 'display_name', None) or group.id,
            description=getattr(group, 'description', None),
            source_created_at=created.timestamp() if created else get_epoch_timestamp_in_ms(),
        )

    def _member_app_users(self, users: list[dict]) -> list[AppUser]:
        return [
            AppUser(source_user_id=user['id'], email=user['email'], full_name=user.get('name'),
                    app_name=self.connector_name, connector_id=self.connector_id)
            for user in users
        ]

    async def _group_with_members(self, group: object) -> tuple[AppUserGroup, list[AppUser]]:
        """An Entra group and every enabled person in it, through nested groups. Raises when they can't be read."""
        return self._app_user_group(group), self._member_app_users(await self._fetch_graph_group_members(group.id))

    async def _perform_initial_full_sync(self) -> bool:
        """Every Entra group with its members. True when every group's members were read."""
        groups = await self.msgraph_client.get_all_user_groups()
        results = await asyncio.gather(*[self._group_with_members(group) for group in groups], return_exceptions=True)
        group_with_members = []
        complete = True
        for group, result in zip(groups, results):
            if isinstance(result, Exception):
                complete = False
                self._health.skipped(f"members of Entra group {getattr(group, 'display_name', group.id)}", result)
            else:
                group_with_members.append(result)
        if group_with_members:
            await self.data_entities_processor.on_new_user_groups(group_with_members)
        self.logger.info(f"Initial full sync completed: processed {len(groups)} Azure AD groups")
        return complete

    async def _process_single_group(self, group) -> Optional[Tuple[AppUserGroup, List[AppUser]]]:
        """An Entra group with its members, or None when they can't be read (its stored members stay)."""
        try:
            return await self._group_with_members(group)
        except Exception as e:
            self.logger.error(f"❌ Error processing group {getattr(group, 'display_name', group.id)}: {e}")
            return None

    async def _containing_groups(self, group_id: str) -> list[object]:
        """The groups a group is in, at any depth: their flattened members change when its members do."""
        builder = self.client.groups.by_group_id(group_id).transitive_member_of
        groups = []
        async with self.rate_limiter:
            response = await builder.get()
        while response:
            groups.extend(item for item in response.value or []
                          if 'group' in (getattr(item, 'odata_type', None) or '').lower())
            next_link = getattr(response, 'odata_next_link', None)
            if not next_link:
                break
            async with self.rate_limiter:
                response = await builder.with_url(next_link).get()
        return groups

    async def _perform_delta_sync(self, url: str, sync_point_key: str) -> bool:
        """Apply the Entra group changes since the stored delta link. True when every change was applied.

        A changed group has its members read again, and so does every group that contains it: Graph reports
        a member change only on the group the member was added to or removed from, while PipesHub holds
        each group's members flattened. The link moves on only when every change on the page was applied.
        """
        while True:
            result = await self.msgraph_client.get_groups_delta_response(url)
            groups = result.get('groups', [])
            changed: dict[str, object] = {}
            applied = True
            for group in groups:
                if getattr(group, 'additional_data', None) and '@removed' in group.additional_data:
                    if not await self._handle_delete_group(group.id):
                        applied = False
                    continue
                changed[group.id] = group
            for group_id in list(changed):
                try:
                    for parent in await self._containing_groups(group_id):
                        changed.setdefault(parent.id, parent)
                except Exception as e:
                    applied = False
                    self._health.skipped(f"groups containing Entra group {group_id}", e)
            refreshed = []
            for group in changed.values():
                try:
                    refreshed.append(await self._group_with_members(group))
                except Exception as e:
                    applied = False
                    self._health.skipped(f"members of Entra group {getattr(group, 'display_name', group.id)}", e)
            if refreshed:
                await self.data_entities_processor.on_new_user_groups(refreshed)
            if not applied:
                return False
            if result.get('next_link'):
                url = result.get('next_link')
                await self.user_group_sync_point.update_sync_point(sync_point_key, {"nextLink": url, "deltaLink": None})
            elif result.get('delta_link'):
                await self.user_group_sync_point.update_sync_point(
                    sync_point_key, {"nextLink": None, "deltaLink": result.get('delta_link')}
                )
                return True
            else:
                self.logger.warning("Received response with neither next_link nor delta_link")
                return False

    async def _handle_group_create(self, group: Group) -> bool:
        """Write an Entra group with its members. False when they could not be read (stored members stay)."""
        try:
            await self.data_entities_processor.on_new_user_groups([await self._group_with_members(group)])
            return True
        except Exception as e:
            self.logger.error(f"❌ Error handling group create for {getattr(group, 'id', 'unknown')}: {e}", exc_info=True)
            return False

    def _create_app_user_from_member(self, member) -> Optional[AppUser]:
        """
        Helper method to create an AppUser from a Graph API user member.

        Args:
            member: A user object from Microsoft Graph API

        Returns:
            AppUser if successful, None if user has no valid email
        """
        email = getattr(member, 'mail', None) or getattr(member, 'user_principal_name', None)

        if not email:
            self.logger.warning(f"User {member.id} has no email or user_principal_name, skipping")
            return None

        return AppUser(
            app_name=self.connector_name,
            source_user_id=member.id,
            email=email,
            full_name=getattr(member, 'display_name', None),
            source_created_at=member.created_date_time.timestamp() if hasattr(member, 'created_date_time') and member.created_date_time else get_epoch_timestamp_in_ms(),
            connector_id=self.connector_id,
        )

    async def _handle_delete_group(self, group_id: str) -> bool:
        """
        Handles the deletion of a single user group.
        Calls the data processor to remove it from the database.

        Args:
            group_id: The external ID of the group to be deleted.

        Returns:
            True if group deletion was successful, False otherwise.
        """
        try:
            self.logger.info(f"Handling group deletion for: {group_id}")

            # Call the data entities processor to handle the deletion logic
            success = await self.data_entities_processor.on_user_group_deleted(
                external_group_id=group_id,
                connector_id=self.connector_id
            )

            if not success:
                self.logger.error(f"❌ Error handling group delete for {group_id}")
                return False

            return True

        except Exception as e:
            self.logger.error(f"❌ Error handling group delete for {group_id}: {e}", exc_info=True)
            return False

    def _map_group_to_permission_type(self, group_name: str) -> PermissionType:
        """Map SharePoint group names to permission types."""
        if not group_name:
            return PermissionType.READ

        group_name_lower = group_name.lower()

        if any(term in group_name_lower for term in ['owner', 'admin', 'full control']):
            return PermissionType.WRITE
        elif any(term in group_name_lower for term in ['member', 'contributor', 'editor']):
            return PermissionType.WRITE
        else:
            return PermissionType.READ
