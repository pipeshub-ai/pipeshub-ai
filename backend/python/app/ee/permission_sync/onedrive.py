# Copyright (c) 2026 PipesHub Inc.
# Licensed under the PipesHub Enterprise License. See the LICENSE file in this
# ee folder. Free for development, testing and evaluation. Production use needs
# a PipesHub licence key.

"""OneDrive: reading who may see what from OneDrive, SharePoint and Entra ID.

Item access lists, whether an item inherits its folder's access (SharePoint's unique-permission flag), the re-reads of
items whose access is still owed, and Entra groups with their members. The free connector keeps listing and syncing
content and calls these methods; without a licence key a company-wide connector reads none of this (the processor's
licence gate).
"""

import asyncio
from dataclasses import dataclass, field
from http import HTTPStatus
from typing import Any, List, Optional, Tuple

from kiota_abstractions.base_request_configuration import RequestConfiguration
from msgraph.generated.drives.item.drive_item_request_builder import (
    DriveItemRequestBuilder,
)
from msgraph.generated.models.drive_item import DriveItem
from msgraph.generated.models.group import Group
from msgraph.generated.models.o_data_errors.o_data_error import ODataError

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
    ListUniquePermissions,
    SharePointAccessError,
    SharePointRestError,
    drive_item_unique_id,
    etag_unique_id,
)
from app.models.entities import AppUser, AppUserGroup, Record
from app.models.permission import EntityType, Permission
from app.services.notification.types import NotificationSeverity, NotificationType
from app.utils.time_conversion import get_epoch_timestamp_in_ms

# How many runs a delta page is read again for a temporary failure before its
# unreadable items are skipped (keeping what is stored) so the rest can move on.
MAX_PAGE_ATTEMPTS = 5
# What SharePoint last said to the connector's certificate, kept so a run that never asks keeps the card as it was.
SHAREPOINT_HEALTH_OK = "ok"
SHAREPOINT_HEALTH_REFUSED = "refused"
SHAREPOINT_HEALTH_FORBIDDEN = "forbidden"

# A 403 or 404 on one item's members or access won't clear up by trying again.
_PERMANENT_READ_STATUSES = frozenset({403, 404})


class GraphReadFailedError(Exception):
    """A Graph read needed to apply a change failed; ``permanent`` when retrying can't help."""

    def __init__(self, message: str, *, permanent: bool) -> None:
        super().__init__(message)
        self.permanent = permanent


def _read_failure(what: str, error: Exception) -> GraphReadFailedError:
    status = error.response_status_code if isinstance(error, ODataError) else None
    return GraphReadFailedError(f"could not read {what}: {error}", permanent=status in _PERMANENT_READ_STATUSES)


@dataclass
class _FolderWalk:
    """Items below a folder whose access could not be brought up to date."""

    unread_items: list[str] = field(default_factory=list)
    unlisted_folders: list[str] = field(default_factory=list)
    temporary: bool = False
    # Unique items whose inherit edge was dropped because their access couldn't be read.
    hidden_items: list[str] = field(default_factory=list[str])

    @property
    def complete(self) -> bool:
        return not self.unread_items and not self.unlisted_folders


def _held_page_attempts(sync_point: Optional[dict[str, Any]], url: str) -> int:
    """Runs that already failed on this page, when it is the page the checkpoint is held at."""
    if sync_point and sync_point.get("heldPage") == url:
        return int(sync_point.get("heldPageAttempts") or 0)
    return 0


class OneDrivePermissionsMixin:
    """The permission-reading half of ``OneDriveConnector``."""

    async def _copies_no_source_access(self) -> bool:
        """Without a licence key a company-wide connector reads no access from OneDrive, SharePoint or Entra ID. The
        processor then keeps every stored grant, inherit edge and group whatever it is handed
        (``skips_permission_writes``), so the answers below only keep the run whole: an item counts as taking its
        folder's access (nothing of its own to read, so no grants), and nothing is left owed or queued, which would
        hold delta pages back or queue every item to be read again."""
        return await self.data_entities_processor.skips_permission_writes(self.connector_id)

    async def _get_drive_scope(self, drive_id: str) -> ListUniquePermissions | None:
        """Which items of a drive carry their own permissions; None when that can't be read."""
        if drive_id in self._drive_scopes:
            return self._drive_scopes[drive_id]
        if self.scope_reader is None or self._sharepoint_refusal is not None or self.client is None:
            return None
        scope = None
        try:
            query = DriveItemRequestBuilder.DriveItemRequestBuilderGetQueryParameters(select=["id", "sharePointIds"])
            async with self.rate_limiter:
                drive = await self.client.drives.by_drive_id(drive_id).get(
                    request_configuration=RequestConfiguration(query_parameters=query)
                )
            sharepoint_ids = getattr(drive, 'share_point_ids', None)
            list_id = getattr(sharepoint_ids, 'list_id', None)
            web_url = getattr(sharepoint_ids, 'site_url', None)
            if not (isinstance(list_id, str) and list_id and isinstance(web_url, str) and web_url):
                raise ValueError("the drive has no SharePoint list id or site URL")
            web_url = web_url.rstrip('/')
            list_url = f"{web_url}/_api/web/lists(guid'{list_id}')"
            item_count = await self.scope_reader.read_item_count(list_url)
            scope = ListUniquePermissions(self.scope_reader, web_url, list_url, item_count)
            self._sharepoint_health = SHAREPOINT_HEALTH_OK
        except SharePointAccessError as e:
            await self._note_sharepoint_refusal(e)
        except Exception as e:
            self.logger.warning(
                f"⚠️ Could not read which items of drive {drive_id} inherit their access; this run, its items "
                f"keep the access list OneDrive reports for each: {e}"
            )
            if isinstance(e, SharePointRestError) and e.status_code == HTTPStatus.FORBIDDEN:
                if self._sharepoint_health != SHAREPOINT_HEALTH_OK:
                    self._sharepoint_health = SHAREPOINT_HEALTH_FORBIDDEN
                await self._notify_sharepoint_permission_missing()
        self._drive_scopes[drive_id] = scope
        return scope

    async def _inheritance(
        self, drive_id: str | None, unique_id: str | None, *, is_root: bool, item_id: str | None
    ) -> bool | None:
        """Whether SharePoint says an item takes its parent folder's access; None when that can't be told now.

        The root, and an item with no SharePoint id, never inherit: there is nothing to ask.
        """
        if is_root or not drive_id or unique_id is None:
            return False
        scope = await self._get_drive_scope(drive_id)
        if scope is None:
            return None
        try:
            inherits = not await scope.has_unique_permissions(unique_id)
        except SharePointAccessError as e:
            await self._note_sharepoint_refusal(e)
            self._drive_scopes[drive_id] = None
            return None
        except Exception as e:
            if not scope.unreadable:
                # One item SharePoint can't answer for (deleted since, say) keeps its own access list.
                self.logger.warning(f"⚠️ Could not read whether item {unique_id} of drive {drive_id} inherits its access: {e}")
            elif drive_id not in self._unreadable_drives_logged:
                self._unreadable_drives_logged.add(drive_id)
                self.logger.warning(
                    f"⚠️ Could not read which items of drive {drive_id} inherit their access; for the rest of "
                    f"this run, its items keep the access list OneDrive reports for each: {e}"
                )
            return None
        if inherits and item_id is not None and scope.read_in_full:
            self._inheriting_from_full_read.setdefault(drive_id, {})[unique_id] = item_id
        return inherits

    async def _inherits(
        self, drive_id: str | None, unique_id: str | None, *, is_root: bool, item_id: str | None
    ) -> bool:
        """``_inheritance``, with an unknown answer taken as "keeps its own access list" and queued to be asked again."""
        inherits = await self._inheritance(drive_id, unique_id, is_root=is_root, item_id=item_id)
        if inherits is None:
            self._queue_unknown_inheritance(drive_id, item_id)
            return False
        return inherits

    async def _inherits_parent_access(self, item: DriveItem) -> bool | None:
        """``_inheritance`` for a drive item: None when SharePoint couldn't say (it is then queued to ask again)."""
        if await self._copies_no_source_access():
            return True
        parent = item.parent_reference
        drive_id = parent.drive_id if parent else None
        unique_id = drive_item_unique_id(item)
        is_root = getattr(item, 'root', None) is not None or not (parent and parent.id)
        inherits = await self._inheritance(drive_id, unique_id, is_root=is_root, item_id=item.id)
        if inherits is None:
            self._queue_unknown_inheritance(drive_id, item.id)
        return inherits

    async def _convert_to_permissions(self, msgraph_permissions: List) -> List[Permission]:
        """
        Convert Microsoft Graph permissions to our Permission model.
        Handles both user and group permissions.
        """
        permissions = []

        for perm in msgraph_permissions:
            try:
                # Handle user permissions
                if hasattr(perm, 'granted_to_v2') and perm.granted_to_v2:
                    if hasattr(perm.granted_to_v2, 'user') and perm.granted_to_v2.user:
                        user = perm.granted_to_v2.user
                        permissions.append(Permission(
                            external_id=user.id,
                            email=user.additional_data.get("email", None) if hasattr(user, 'additional_data') else None,
                            type=map_msgraph_roles_to_permission_type(perm.roles),
                            entity_type=EntityType.USER
                        ))
                    if hasattr(perm.granted_to_v2, 'group') and perm.granted_to_v2.group:
                        group = perm.granted_to_v2.group
                        permissions.append(Permission(
                            external_id=group.id,
                            email=group.additional_data.get("email", None) if hasattr(group, 'additional_data') else None,
                            type=map_msgraph_roles_to_permission_type(perm.roles),
                            entity_type=EntityType.GROUP
                        ))


                # Handle group permissions
                if hasattr(perm, 'granted_to_identities_v2') and perm.granted_to_identities_v2:
                    for identity in perm.granted_to_identities_v2:
                        if hasattr(identity, 'group') and identity.group:
                            group = identity.group
                            permissions.append(Permission(
                                external_id=group.id,
                                email=group.additional_data.get("email", None) if hasattr(group, 'additional_data') else None,
                                type=map_msgraph_roles_to_permission_type(perm.roles),
                                entity_type=EntityType.GROUP
                            ))
                        elif hasattr(identity, 'user') and identity.user:
                            user = identity.user
                            permissions.append(Permission(
                                external_id=user.id,
                                email=user.additional_data.get("email", None) if hasattr(user, 'additional_data') else None,
                                type=map_msgraph_roles_to_permission_type(perm.roles),
                                entity_type=EntityType.USER
                            ))

                # A link grants only the identities Graph lists on it. An organization or
                # anonymous link opens the item only to the people who redeem it, and Graph
                # does not list them, so they get no grant here: under-shared, never over-shared.

            except Exception as e:
                self.logger.error(f"❌ Error converting permission: {e}", exc_info=True)
                continue

        return permissions

    async def _update_folder_children_permissions(
        self,
        drive_id: str,
        folder_id: str,
        inherited_permissions: Optional[List[Permission]] = None,
        walk: Optional[_FolderWalk] = None,
    ) -> _FolderWalk:
        """
        Recursively update permissions for all children of a folder.

        Args:
            drive_id: The drive ID
            folder_id: The folder ID whose children need permission updates
            inherited_permissions: The permissions to apply to children
            walk: Collects the items that could not be updated, across the recursion

        Returns:
            The items whose access could not be read or saved, and the folders whose
            children could not be listed.
        """
        if await self._copies_no_source_access():
            return _FolderWalk()
        walk = walk if walk is not None else _FolderWalk()
        try:
            children = await self.msgraph_client.list_folder_children(drive_id, folder_id, raise_on_error=True)
        except Exception as ex:
            failure = _read_failure(f"children of folder {folder_id}", ex)
            self.logger.error(f"Error updating folder children permissions: {failure}", exc_info=True)
            walk.unlisted_folders.append(folder_id)
            walk.temporary = walk.temporary or not failure.permanent
            return walk

        for child in children:
            try:
                child_inheritance = await self._inherits_parent_access(child)
                child_inherits = child_inheritance is True
                child_permissions = []
                if not child_inherits:
                    try:
                        child_permissions = await self.msgraph_client.get_file_permission(
                            drive_id,
                            child.id,
                            raise_on_error=True,
                        )
                    except Exception as read_error:
                        failure = _read_failure(f"permissions of child item {child.id}", read_error)
                        self.logger.warning(str(failure))
                        walk.unread_items.append(child.id)
                        walk.temporary = walk.temporary or not failure.permanent
                        child_permissions = None

                existing_child_record = await self.data_entities_processor.get_record_by_external_id(
                    self.connector_id, child.id
                )

                if child_permissions is None and child_inheritance is False and self.scope_reader and existing_child_record:
                    # A stored inherit edge on an item SharePoint calls unique would pass the folder's
                    # new sharing on to it; dropped, its stored grants alone stand until it can be read.
                    existing_child_record.inherit_permissions = False
                    await self.data_entities_processor.on_record_metadata_update(existing_child_record)
                    if child.id:
                        walk.hidden_items.append(child.id)

                if child_permissions is not None and existing_child_record:
                    converted_permissions = await self._convert_to_permissions(child_permissions)
                    existing_child_record.inherit_permissions = child_inherits
                    existing_child_record.rewrite_permissions = True
                    await self.data_entities_processor.on_updated_record_permissions(
                        record=existing_child_record,
                        permissions=converted_permissions
                    )
                    self.logger.info(f"Updated permissions for child item {child.id}")

                # If this child is also a folder, recurse
                if child.folder is not None:
                    await self._update_folder_children_permissions(drive_id=drive_id, folder_id=child.id, walk=walk)

            except Exception as child_ex:
                self.logger.error(f"Error updating child {child.id}: {child_ex}", exc_info=True)
                if child.id not in walk.unread_items:
                    walk.unread_items.append(child.id)
                walk.temporary = True

        return walk

    async def _settle_unread_children(
        self, walk: _FolderWalk, folder_id: str, folder_permissions: list[Permission], *, unshared: bool
    ) -> None:
        """Decide what items whose access couldn't be read keep once the folder is saved.

        After a share, their stored access is kept: it can only be narrower than the
        folder's. After an unshare, keeping it could leave the old share in place, and
        stored grants don't record whether they came from the folder, so those items
        are given the folder's current access instead. That drops any sharing of
        their own too, until the item changes or is reindexed.
        """
        records: list[Record] = []
        for external_id in walk.unread_items:
            record = await self.data_entities_processor.get_record_by_external_id(self.connector_id, external_id)
            if record:
                records.append(record)
        for unlisted_folder in walk.unlisted_folders:
            records.extend(await self._stored_descendants(unlisted_folder))
        # Whether these inherit wasn't read either; asked again, so none keeps an access it shouldn't.
        for record in records:
            self._queue_unknown_inheritance(record.external_record_group_id, record.external_record_id)

        if not unshared:
            self.logger.error(
                f"❌ Folder {folder_id} was shared, but the access of {walk.unread_items + walk.unlisted_folders} "
                "could not be read; they keep their stored access. Reindex them to pick up the new sharing."
            )
            return

        for record in records:
            record.inherit_permissions = False
            record.rewrite_permissions = True
            await self.data_entities_processor.on_updated_record_permissions(record, list(folder_permissions))
        self.logger.error(
            f"❌ Folder {folder_id} was unshared, but the access of {[r.external_record_id for r in records]} could not be "
            "read; they now have the folder's current access, which also drops any sharing of their own. "
            "Reindex them to restore that."
        )

    async def _sync_user_groups(self) -> None:
        """
        Unified user group synchronization.
        - First sync: Uses standard /groups API for clean current state
        - Subsequent syncs: Uses Graph Delta API for incremental changes
        """
        if await self._copies_no_source_access():
            return
        try:
            sync_point_key = generate_record_sync_point_key(
                SyncDataPointType.GROUPS.value,
                "organization",
                self.data_entities_processor.org_id
            )
            sync_point = await self.user_group_sync_point.read_sync_point(sync_point_key)
            if sync_point and sync_point.get('pendingGroupDeletes'):
                await self._retry_pending_group_deletes(sync_point['pendingGroupDeletes'], sync_point_key)

            # A run stopped mid-delta leaves only nextLink; resuming there keeps the
            # group deletions on the remaining pages, which a full sync would not see.
            delta_link = (sync_point.get('deltaLink') or sync_point.get('nextLink')) if sync_point else None

            if delta_link is None:
                self.logger.info("No sync point found, performing initial full sync...")

                await self._start_group_sync_over(sync_point_key)
            elif sync_point.get('fullSyncIncomplete'):
                self.logger.info("Previous full group sync was incomplete, reading every group again...")
                all_groups_read = await self._perform_initial_full_sync()
                attempts = int(sync_point.get('fullSyncAttempts') or 1) + 1
                if all_groups_read:
                    await self.user_group_sync_point.update_sync_point(
                        sync_point_key, {"fullSyncIncomplete": False, "fullSyncAttempts": 0}
                    )
                elif attempts >= MAX_PAGE_ATTEMPTS:
                    self.logger.error(
                        f"❌ Some groups still could not be read after {attempts} full group syncs; "
                        "they keep their stored members and are read again when they next change"
                    )
                    await self.user_group_sync_point.update_sync_point(
                        sync_point_key, {"fullSyncIncomplete": False, "fullSyncAttempts": 0}
                    )
                else:
                    await self.user_group_sync_point.update_sync_point(sync_point_key, {"fullSyncAttempts": attempts})
                # Run after the marker is settled: a group the delta gives up on sets it
                # again, and that must not be cleared by this full sync's outcome.
                await self._apply_group_changes(delta_link, sync_point_key)
            else:
                self.logger.info("Sync point found, performing incremental delta sync...")
                await self._apply_group_changes(delta_link, sync_point_key)

        except ODataError as e:
            self.logger.error(f"❌ Error in user group sync: {e}", exc_info=True)
            error_code = (e.error.code or "") if e.error else ""
            if error_code == "Authorization_RequestDenied" or e.response_status_code == 403:
                await self.notify(
                    type=NotificationType.CONNECTOR_AUTH_ERROR,
                    severity=NotificationSeverity.ERROR,
                    title="User group sync failed",
                    message="Please check your authentication credentials are correct and has Group.Read.All API permission.",
                    payload={
                        "connector_id": self.connector_id,
                        "connector_name": self.connector_name.value,
                        "connector_scope": self.scope,
                    },
                    recipient_user_ids=[self.created_by],
                )
            raise
        except Exception as e:
            self.logger.error(f"❌ Error in user group sync: {e}", exc_info=True)
            raise

    async def _apply_group_changes(self, delta_link: str, sync_point_key: str) -> None:
        try:
            await self._perform_delta_sync(delta_link, sync_point_key)
        except ODataError as e:
            if not is_expired_delta_link_error(e):
                raise
            # Kept, the link would fail every later run.
            self.logger.error("The saved group delta link has expired; reading every group again")
            await self._start_group_sync_over(sync_point_key, expired=True)
            await self.notify(
                type=NotificationType.CONNECTOR_WARNING,
                severity=NotificationSeverity.WARNING,
                title="Every Microsoft group was read again",
                message=GROUPS_READ_AGAIN,
                recipient_user_ids=[self.created_by],
            )

    async def _start_group_sync_over(self, sync_point_key: str, *, expired: bool = False) -> None:
        """Read every group and save a new delta link, in place of one that expired or for the first time."""
        # IMPORTANT: Get delta link BEFORE full sync to avoid missing changes
        # that occur during the sync
        delta_link = await self._get_initial_delta_link()

        all_groups_read = await self._perform_initial_full_sync()
        if expired:
            # Before the link is saved: if this fails, the expired one stays and the next run starts over again.
            await remove_groups_gone_from_directory(
                self.data_entities_processor, self.connector_id, self.msgraph_client, self.handle_delete_group, self.logger,
            )

        # The link is saved even when some groups could not be read: only a delta
        # from before the full sync reports groups deleted since. The marker makes
        # the next run read every group again before applying it.
        if delta_link:
            await self.user_group_sync_point.update_sync_point(
                sync_point_key,
                {
                    "nextLink": None,
                    "deltaLink": delta_link,
                    "fullSyncIncomplete": not all_groups_read,
                    "fullSyncAttempts": 0 if all_groups_read else 1,
                }
            )
            self.logger.info("Initial sync completed and delta link saved for future syncs")
        else:
            self.logger.warning("Initial sync completed but no delta link was obtained")
        if not all_groups_read:
            self.logger.warning("Some groups could not be read; the next run will read every group again")

    async def _get_initial_delta_link(self) -> Optional[str]:
        """
        Consumes the delta API to obtain a deltaLink checkpoint.
        Called BEFORE initial full sync to ensure no changes are missed.

        Returns:
            The deltaLink string, or None if unable to obtain one.
        """
        self.logger.info("Obtaining delta link checkpoint before full sync...")

        url = "https://graph.microsoft.com/v1.0/groups/delta"

        try:
            while True:
                result = await self.msgraph_client.get_groups_delta_response(url)

                # We ignore the data - just consuming to get the deltaLink
                groups_count = len(result.get('groups', []))
                self.logger.debug(f"Delta initialization: skipping page with {groups_count} groups")

                if result.get('next_link'):
                    url = result.get('next_link')
                elif result.get('delta_link'):
                    self.logger.info("Delta link obtained successfully")
                    return result.get('delta_link')
                else:
                    self.logger.warning("Delta initialization: no next_link or delta_link received")
                    return None
        except Exception as e:
            self.logger.error(f"❌ Error obtaining initial delta link: {e}", exc_info=True)
            return None

    async def _perform_initial_full_sync(self) -> bool:
        """
        Performs initial full sync using the standard /groups API.
        Gets current state of all groups and their members.

        Returns:
            True if every group and its members were read.
        """
        self.logger.info("Starting initial full user group synchronization")

        groups = await self.msgraph_client.get_all_user_groups()

        # Process all groups concurrently using asyncio.gather
        results = await asyncio.gather(
            *[self._process_single_group(group) for group in groups],
            return_exceptions=True
        )

        group_with_members = []
        all_groups_read = True
        for group, result in zip(groups, results):
            if isinstance(result, GraphReadFailedError) and result.permanent:
                self.logger.warning(f"Skipping group: {result}; it keeps its stored members")
                await self._keep_stored_group(group.id)
            elif isinstance(result, Exception):
                self.logger.error(f"❌ Error processing group: {result}", exc_info=True)
                all_groups_read = False
                await self._keep_stored_group(group.id)
            elif result is None:
                all_groups_read = False
                await self._keep_stored_group(group.id)
            else:
                group_with_members.append(result)

        if group_with_members:
            await self.data_entities_processor.on_new_user_groups(group_with_members)

        self.logger.info(f"Initial full sync completed: processed {len(groups)} user groups")
        return all_groups_read

    async def _process_single_group(self, group) -> Optional[Tuple[AppUserGroup, List[AppUser]]]:
        """
        Processes a single group and returns a tuple of (user_group, app_users).
        Returns None if processing fails. A permanent member-read failure is raised
        instead, so the full sync can skip the group without counting it as incomplete.
        """
        try:
            app_users = await self._collect_group_users(group)
            return (self._to_app_user_group(group), app_users)

        except GraphReadFailedError as e:
            if e.permanent:
                raise
            self.logger.error(f"❌ Error processing group {group.display_name}: {e}")
            return None
        except Exception as e:
            self.logger.error(f"❌ Error processing group {group.display_name}: {e}", exc_info=True)
            return None

    def _to_app_user_group(self, group: Group) -> AppUserGroup:
        return AppUserGroup(
            source_user_group_id=group.id,
            app_name=self.connector_name,
            connector_id=self.connector_id,
            name=group.display_name,
            description=group.description,
            source_created_at=group.created_date_time.timestamp() if group.created_date_time else get_epoch_timestamp_in_ms(),
        )

    async def _collect_group_users(self, group: Group) -> list[AppUser]:
        """
        Users in a group, including those of nested groups (one level deep).

        Supported member types:
        - User: Added directly
        - Group (nested): Fetch its users and add them (only one level deep)
        - Device, Service Principal, Org Contact: Ignored

        Raises GraphReadFailedError when the group's members, or a nested group's,
        can't be read: saving a partial list would replace the group's stored members.
        """
        try:
            members = await self.msgraph_client.get_group_members(group.id, raise_on_error=True)
        except Exception as e:
            raise _read_failure(f"members of group {group.id}", e) from e

        app_users = []
        for member in members:
            odata_type = getattr(member, 'odata_type', None) or (member.additional_data or {}).get('@odata.type', '')

            if '#microsoft.graph.user' in odata_type:
                app_user = self._create_app_user_from_member(member)
                if app_user:
                    app_users.append(app_user)
            elif '#microsoft.graph.group' in odata_type:
                app_users.extend(await self._get_users_from_nested_group(member))
            else:
                self.logger.debug(f"Skipping member type '{odata_type}' for member {member.id}")

        return app_users

    async def _perform_delta_sync(self, url: str, sync_point_key: str) -> None:
        """
        Performs incremental sync using the Graph Delta API.
        Processes only changes since the last sync.
        """

        if not url:
            self.logger.warning("No valid URL in sync point, falling back to full delta sync")
            url = "https://graph.microsoft.com/v1.0/groups/delta"

        self.logger.info("Starting incremental delta sync...")

        while True:
            result = await self.msgraph_client.get_groups_delta_response(url)
            groups = result.get('groups', [])

            self.logger.info(f"Fetched delta page with {len(groups)} group changes")

            unapplied: list[str] = []
            failed_deletes: list[str] = []
            for group in groups:
                # Handle group DELETION
                if hasattr(group, 'additional_data') and group.additional_data and '@removed' in group.additional_data:
                    self.logger.info(f"[DELTA] 🗑️ REMOVE Group: {group.id}")
                    success = await self.handle_delete_group(group.id)
                    if not success:
                        self.logger.error(f"❌ Error handling group delete for {group.id}")
                        unapplied.append(group.id)
                        failed_deletes.append(group.id)
                    continue

                # Handle ADD/UPDATE
                self.logger.info(f"[DELTA] ✅ ADD/UPDATE Group: {getattr(group, 'display_name', 'N/A')} ({group.id})")
                saved = await self.handle_group_create(group)
                if saved is False:
                    self.logger.error(f"❌ Error handling group create for {group.id}")
                    unapplied.append(group.id)
                if saved:
                    # The saved list replaced the group's stored members and already leaves
                    # out anyone removed here, so there is nothing left to remove.
                    continue

                # Applied even when the member read above failed: a removal listed here needs
                # no other read, and is lost for good once the delta link moves past this page.
                member_changes = (group.additional_data or {}).get('members@delta', [])

                if member_changes:
                    self.logger.info(f"    -> [DELTA] 👥 Processing {len(member_changes)} member changes for group: {group.id}")

                for member_change in member_changes:
                    if not await self._process_member_change(group.id, member_change) and group.id not in unapplied:
                        unapplied.append(group.id)

            # Graph won't send this page again once the link moves past it, so a group
            # that couldn't be applied would lose its changes for good. A page is held
            # for a bounded number of runs so one broken group can't freeze group sync.
            if unapplied:
                stored = await self.user_group_sync_point.read_sync_point(sync_point_key)
                attempts = _held_page_attempts(stored, url) + 1
                if attempts < MAX_PAGE_ATTEMPTS:
                    await self.user_group_sync_point.update_sync_point(
                        sync_point_key, {"heldPage": url, "heldPageAttempts": attempts}
                    )
                    self.logger.warning(
                        f"Groups {unapplied} could not be applied; this page will be read again next run "
                        f"(attempt {attempts} of {MAX_PAGE_ATTEMPTS})"
                    )
                    break
                self.logger.error(
                    f"❌ Groups {unapplied} still could not be applied after {attempts} attempts; "
                    "skipping them so group sync can continue. They keep their stored members, less any "
                    "removals listed on this page"
                )
                # The page moves on without these groups' member changes; a full group
                # sync reads their current members again, within its own attempt limit.
                if any(group_id not in failed_deletes for group_id in unapplied):
                    await self.user_group_sync_point.update_sync_point(
                        sync_point_key, {"fullSyncIncomplete": True, "fullSyncAttempts": 1}
                    )
                # Graph reports a deletion once. Queue it so every later run tries it again,
                # rather than leaving the deleted group's members with its access.
                still_failing = [g for g in failed_deletes if not await self.handle_delete_group(g)]
                if still_failing:
                    queued = sorted(set(stored.get('pendingGroupDeletes') or []) | set(still_failing))
                    await self.user_group_sync_point.update_sync_point(sync_point_key, {"pendingGroupDeletes": queued})
                    self.logger.error(
                        f"❌ Deleted groups {still_failing} could not be removed; their members keep the group's access "
                        "until a later run removes them"
                    )

            # Handle pagination and completion
            if result.get('next_link'):
                url = result.get('next_link')
                await self.user_group_sync_point.update_sync_point(
                    sync_point_key,
                    {"nextLink": url, "deltaLink": None, "heldPage": None, "heldPageAttempts": 0}
                )
            elif result.get('delta_link'):
                await self.user_group_sync_point.update_sync_point(
                    sync_point_key,
                    {"nextLink": None, "deltaLink": result.get('delta_link'), "heldPage": None, "heldPageAttempts": 0}
                )
                self.logger.info("Delta sync completed, delta link saved for next run")
                break
            else:
                self.logger.warning("Received response with neither next_link nor delta_link")
                break

    async def _retry_pending_group_deletes(self, pending: list[str], sync_point_key: str) -> None:
        remaining = [group_id for group_id in pending if not await self.handle_delete_group(group_id)]
        await self.user_group_sync_point.update_sync_point(sync_point_key, {"pendingGroupDeletes": remaining})
        if remaining:
            self.logger.error(f"❌ Deleted groups {remaining} still could not be removed; will try again next run")
        else:
            self.logger.info(f"Removed previously failed group deletions: {pending}")

    async def _process_member_change(self, group_id: str, member_change: dict) -> bool:
        """
        Processes a single member change from the delta response.

        Returns False when a removal could not be applied. Additions need no work
        here: the group's full member list is saved separately.
        """
        if '@removed' not in member_change:
            return True

        user_id = member_change.get('id')
        email = await self.msgraph_client.get_user_email(user_id)

        if not email:
            self.logger.error(f"❌ Could not look up member {user_id} to remove from group {group_id}")
            return False

        self.logger.info(f"    -> [DELTA] 👤⛔ REMOVING member: {email} ({user_id}) from group {group_id}")
        try:
            success = await self.data_entities_processor.on_user_group_member_removed(
                external_group_id=group_id,
                user_email=email,
                connector_id=self.connector_id
            )
        except Exception as e:
            # Reported as not applied rather than raised, so the rest of the page is
            # still applied and the caller holds the page for this group.
            self.logger.error(f"❌ Error removing member {email} from group {group_id}: {e}", exc_info=True)
            return False
        if not success:
            self.logger.error(f"❌ Error removing member {email} from group {group_id}")
        return bool(success)

    async def handle_group_create(self, group: Group) -> bool | None:
        """
        Handles the creation or update of a single user group.
        Fetches members and sends to data processor.

        Returns:
            True once the group's current members are saved. None when they can never
            be read (403/404): the group keeps its stored members and counts as handled.
            False if the change should be tried again later.
        """
        try:
            await self._save_group(group)
            return True

        except GraphReadFailedError as e:
            if e.permanent:
                self.logger.warning(f"Skipping group {group.id}: {e}; it keeps its stored members")
                return None
            self.logger.error(f"❌ Error handling group create for {group.id}: {e}")
            return False
        except Exception as e:
            self.logger.error(f"❌ Error handling group create for {getattr(group, 'id', 'unknown')}: {e}", exc_info=True)
            return False

    async def _save_group(self, group: Group) -> None:
        """Read a group's members and save it. Raises GraphReadFailedError if they can't be read."""
        app_users = await self._collect_group_users(group)
        await self.data_entities_processor.on_new_user_groups([(self._to_app_user_group(group), app_users)])
        self.logger.info(f"Processed group creation/update for: {group.display_name} with {len(app_users)} user members")

    async def _get_users_from_nested_group(self, nested_group) -> List[AppUser]:
        """
        Fetches users from a nested group (one level deep only).

        Args:
            nested_group: A group member object from Microsoft Graph API

        Returns:
            List of AppUser entities from the nested group

        Raises:
            GraphReadFailedError: if the nested group's members can't be read
        """
        nested_group_name = getattr(nested_group, 'display_name', nested_group.id)
        self.logger.info(f"Processing nested group member: {nested_group_name}")

        try:
            nested_members = await self.msgraph_client.get_group_members(nested_group.id, raise_on_error=True)
        except Exception as e:
            raise _read_failure(f"members of nested group {nested_group_name}", e) from e

        app_users = []
        for nested_member in nested_members:
            nested_odata_type = getattr(nested_member, 'odata_type', None) or (nested_member.additional_data or {}).get('@odata.type', '')

            if '#microsoft.graph.user' in nested_odata_type:
                app_user = self._create_app_user_from_member(nested_member)
                if app_user:
                    app_users.append(app_user)
            else:
                self.logger.debug(f"Skipping non-user member '{nested_odata_type}' in nested group {nested_group_name}")

        return app_users

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

    async def handle_delete_group(self, group_id: str) -> bool:
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

    async def _reevaluate_item_access(self, drive_id: str, item_ids: list[str]) -> list[str]:
        """Read again whether stored items inherit, and rewrite their access; returns those still unknown."""
        if await self._copies_no_source_access():
            return item_ids
        remaining: list[str] = []
        for item_id in item_ids:
            record = await self.data_entities_processor.get_record_by_external_id(self.connector_id, item_id)
            if not record:
                continue
            unique_id = etag_unique_id(record.external_revision_id)
            inherits = await self._inheritance(
                drive_id, unique_id, is_root=record.parent_external_record_id is None, item_id=None
            )
            if inherits is None:
                remaining.append(item_id)
                continue
            try:
                await self._write_known_access(record, drive_id, inherits=inherits)
            except Exception as ex:
                self.logger.warning(f"Access of item {item_id} still could not be read or saved: {ex}")
                remaining.append(item_id)
        return remaining

    async def _write_known_access(self, record: Record, drive_id: str, *, inherits: bool) -> None:
        """Rewrite a stored record's access once SharePoint has said whether it inherits."""
        grants: list[Permission] = []
        if not inherits:
            grants = await self._convert_to_permissions(
                await self.msgraph_client.get_file_permission(drive_id, record.external_record_id, raise_on_error=True)
            )
        record.inherit_permissions = inherits
        record.rewrite_permissions = True
        await self.data_entities_processor.on_updated_record_permissions(record, grants)

    async def _verify_scanned_inheritance(self, drive_id: str) -> None:
        """Read the drive's list again and correct items that broke inheritance after the first read.

        Answers from a full read can be minutes old by the time an item is written,
        and the delta link has moved past the change, so an item that became unique in
        between would otherwise keep its folder's access until it next changes.
        """
        decided = self._inheriting_from_full_read.pop(drive_id, {})
        if not decided:
            return
        scope = self._drive_scopes.get(drive_id)
        if scope is None:
            # SharePoint stopped answering after the full read: those answers are checked next run.
            for item_id in decided.values():
                self._queue_unknown_inheritance(drive_id, item_id)
            return
        try:
            changed = await scope.changed_to_unique_since_read(set(decided))
        except Exception as e:
            self.logger.warning(
                f"⚠️ Could not check OneDrive {drive_id} again for items that broke inheritance during this run; "
                f"they are read one by one: {e}"
            )
            for item_id in decided.values():
                self._queue_unknown_inheritance(drive_id, item_id)
            return
        if not changed:
            return
        # Asked one by one from here: the full read is what went stale.
        self._drive_scopes.pop(drive_id, None)
        changed_items = [decided[unique_id] for unique_id in changed]
        try:
            remaining = await self._reevaluate_item_access(drive_id, changed_items)
        except Exception as e:
            self.logger.warning(f"⚠️ Could not rewrite items of OneDrive {drive_id} that broke inheritance; queued: {e}")
            remaining = changed_items
        for item_id in remaining:
            self._queue_unknown_inheritance(drive_id, item_id)

    async def _retry_pending_access_reads(self, pending: list[str], sync_point_key: str) -> list[str]:
        """Read again the access of items saved while it couldn't be read; returns those still unread."""
        if await self._copies_no_source_access():
            return pending
        remaining = []
        for item_id in pending:
            record = await self.data_entities_processor.get_record_by_external_id(self.connector_id, item_id)
            # Deleted, or its page never committed; a replay of that page queues it again.
            if not record:
                continue
            try:
                inherits = await self._inherits(
                    record.external_record_group_id,
                    etag_unique_id(record.external_revision_id),
                    is_root=record.parent_external_record_id is None,
                    item_id=item_id,
                )
                await self._write_known_access(record, record.external_record_group_id, inherits=inherits)
            except Exception as ex:
                self.logger.warning(f"Access of item {item_id} still could not be read or saved: {ex}")
                remaining.append(item_id)
        await self.drive_delta_sync_point.update_sync_point(sync_point_key, sync_point_data={"pendingAccessReads": remaining})
        if remaining:
            self.logger.error(f"❌ The access of {remaining} still could not be read; they are tried again next run")
        return remaining
