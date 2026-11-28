# Copyright (c) 2026 PipesHub Inc.
# Licensed under the PipesHub Enterprise License. See the LICENSE file in this
# ee folder. Free for development, testing and evaluation. Production use needs
# a PipesHub licence key.

"""Dropbox (team): reading who may see what from Dropbox.

File and folder members, shared-folder members, team groups with their members, and the group and sharing
events that change them. The free connector keeps listing and syncing files and folders and calls these
methods; without a licence key a company-wide connector reads none of this (the processor's licence gate).
"""

import re
from collections.abc import Iterable
from datetime import datetime, timezone
from typing import Optional

from dropbox.team_log import EventCategory, TeamEvent

from app.config.constants.arangodb import (
    CollectionNames,
)
from app.connectors.core.base.sync_point.sync_point import (
    generate_record_sync_point_key,
)
from app.connectors.sources.dropbox.errors import is_auth_error, is_cursor_reset
from app.ee.permission_sync.dropbox_access import DropboxGrants, member_is_direct
from app.models.entities import (
    AppUser,
    AppUserGroup,
)
from app.models.permission import EntityType, Permission, PermissionType

# Team-log sharing events after which an item's members are read again.
SHARING_CHANGE_EVENTS = frozenset({
    "shared_content_add_member",
    "shared_content_remove_member",
    "shared_content_relinquish_membership",
    "shared_content_add_invitees",
    "shared_content_remove_invitees",
    "shared_content_claim_invitation",
    "shared_content_change_member_role",
    "shared_content_unshare",
    "shared_folder_create",
    "shared_folder_nest",
    "shared_folder_transfer_ownership",
    "shared_folder_change_members_inheritance_policy",
})


class GroupAccessRemovalError(Exception):
    """A member removal or group deletion in the group event log that could not be saved.

    Dropbox lists an event once, so the handlers let this one through instead of
    logging it: the group is then queued, and what the event should have removed
    is removed on a later run.
    """

    def __init__(self, message: str, group_id: str, *, group_deleted: bool) -> None:
        super().__init__(message)
        self.group_id = group_id
        self.group_deleted = group_deleted


def _reset_time(error: object) -> datetime | None:
    """The timestamp of a GetTeamEventsContinueError.reset, as the SDK prints it, or None."""
    match = re.search(r"'reset', datetime\.datetime\(([\d, ]+)\)", str(error or ""))
    if not match:
        return None
    parts = [int(p) for p in match.group(1).split(",")]
    return datetime(*parts, tzinfo=timezone.utc)


def _next_cursor(page: object) -> str | None:
    """The cursor of a sharing list page; Dropbox leaves it unset on the last page."""
    cursor = getattr(page, "cursor", None)
    return cursor if isinstance(cursor, str) and cursor else None


# Levels that open a folder's content; traverse, no_access and anything new grant nothing.
_PERMISSION = {
    "owner": PermissionType.OWNER,
    "editor": PermissionType.WRITE,
    "viewer": PermissionType.READ,
    "viewer_no_comment": PermissionType.READ,
}


def folder_grants(users: Iterable, groups: Iterable, invitees: Iterable) -> list[Permission]:
    """A shared folder's own members as grants. Members it inherits stay on the parent folder."""
    grants: list[Permission] = []
    for membership in users:
        user = membership.user
        email = getattr(user, "email", None)
        level = _permission(membership)
        if getattr(membership, "is_inherited", False) is True or not email or email.endswith("#") or level is None:
            continue
        grants.append(Permission(external_id=user.account_id, email=email, type=level, entity_type=EntityType.USER))
    for membership in groups:
        level = _permission(membership)
        if getattr(membership, "is_inherited", False) is True or level is None:
            continue
        grants.append(Permission(external_id=membership.group.group_id, type=level, entity_type=EntityType.GROUP))
    for membership in invitees:
        email = getattr(getattr(membership, "invitee", None), "email", None)
        level = _permission(membership)
        if getattr(membership, "is_inherited", False) is True or not email or level is None:
            continue
        grants.append(Permission(external_id=email, email=email, type=level, entity_type=EntityType.USER))
    return grants


def _permission(membership: object) -> PermissionType | None:
    return _PERMISSION.get(getattr(getattr(membership, "access_type", None), "_tag", None))


class DropboxPermissionsMixin:
    """The permission-reading half of ``DropboxConnector``."""

    async def _copies_no_source_access(self) -> bool:
        """Without a licence key a company-wide connector reads no access from Dropbox. The processor then keeps every
        stored grant and group whatever is handed to it (``skips_permission_writes``), so the gated answers below are
        empty, not None: None is this connector's "could not read", which reports every file and shared folder as a
        part of the run that failed."""
        return await self.data_entities_processor.skips_permission_writes(self.connector_id)

    async def _convert_dropbox_permissions_to_permissions(
        self,
        file_or_folder_id: str,
        is_file: bool,
        team_member_id: Optional[str] = None,
        shared_folder_id: Optional[str] = None
    ) -> list[Permission] | None:
        """
        Convert Dropbox permissions to our Permission model; None when they could not be read.
        Handles both user and group permissions for files and folders.

        Args:
            file_or_folder_id: The Dropbox file or folder ID
            is_file: True if this is a file, False if it's a folder
            team_member_id: The team member ID to use for the API call
            shared_folder_id: For shared folders, the shared folder ID

        Returns:
            List of Permission objects
        """
        if await self._copies_no_source_access():
            return DropboxGrants([], covered_emails=set())
        permissions = []
        covered_emails: set[str] = set()

        try:
            # Fetch members based on type
            if is_file:
                members_result = await self.data_source.sharing_list_file_members(
                    file=file_or_folder_id,
                    include_inherited=True,
                    team_member_id=team_member_id
                )
            else:
                # For folders, only fetch if it's a shared folder
                if not shared_folder_id:
                    # Not a shared folder, no permissions to fetch
                    return []
                members_result = await self.data_source.sharing_list_folder_members(
                    shared_folder_id=shared_folder_id,
                    team_member_id=team_member_id
                )

            if not members_result.success:
                self.logger.warning(f"Could not fetch permissions for {file_or_folder_id}: {members_result.error}")
                return None
            if is_file:
                # sharing/list_file_members returns at most 100 members a page (sharing.stone); the rest follow.
                cursor = _next_cursor(members_result.data)
                while cursor:
                    page = await self.data_source.sharing_list_file_members_continue(
                        cursor=cursor, team_member_id=team_member_id,
                    )
                    if not page.success:
                        self.logger.warning(f"Could not read every member of {file_or_folder_id}: {page.error}")
                        return None
                    members_result.data.users.extend(page.data.users)
                    members_result.data.groups.extend(page.data.groups)
                    members_result.data.invitees.extend(page.data.invitees)
                    cursor = _next_cursor(page.data)

            # Map Dropbox AccessLevel to PermissionType
            access_level_map = {
                'owner': PermissionType.OWNER,
                'editor': PermissionType.WRITE,
                'viewer': PermissionType.READ,
            }

            if hasattr(members_result.data, 'users') and members_result.data.users:
                for user_membership in members_result.data.users:
                    access_type_tag = user_membership.access_type._tag
                    permission_type = access_level_map.get(access_type_tag, PermissionType.READ)

                    user_info = user_membership.user

                    # Get email and check validity
                    email = user_info.email if hasattr(user_info, 'email') else None

                    # Skip users without email or with email ending in '#'
                    if not email or email.endswith('#'):
                        self.logger.debug(f"Skipping user {user_info.account_id} with invalid email: {email}")
                        continue

                    covered_emails.add(email)
                    if is_file and not member_is_direct(is_inherited=getattr(user_membership, "is_inherited", None)):
                        continue

                    permissions.append(Permission(
                        external_id=user_info.account_id,
                        email=email,
                        type=permission_type,
                        entity_type=EntityType.USER
                    ))

            # Process group permissions
            if hasattr(members_result.data, 'groups') and members_result.data.groups:
                for group_membership in members_result.data.groups:
                    if is_file and not member_is_direct(is_inherited=getattr(group_membership, "is_inherited", None)):
                        continue
                    access_type_tag = group_membership.access_type._tag
                    permission_type = access_level_map.get(access_type_tag, PermissionType.READ)

                    group_info = group_membership.group
                    permissions.append(Permission(
                        external_id=group_info.group_id,
                        email=None,  # Groups don't have emails
                        type=permission_type,
                        entity_type=EntityType.GROUP
                    ))

            if is_file and hasattr(members_result.data, "invitees"):
                for invitee_membership in members_result.data.invitees or []:
                    if not member_is_direct(is_inherited=getattr(invitee_membership, "is_inherited", None)):
                        continue
                    invitee = getattr(invitee_membership, "invitee", None)
                    email = getattr(invitee, "email", None)
                    if not email or str(email).endswith("#"):
                        continue
                    access_type = getattr(invitee_membership, "access_type", None)
                    access_type_tag = getattr(access_type, "_tag", "viewer")
                    permissions.append(Permission(
                        external_id=email,
                        email=email,
                        type=access_level_map.get(access_type_tag, PermissionType.READ),
                        entity_type=EntityType.USER,
                    ))

        except Exception as e:
            self.logger.warning(f"Error converting Dropbox permissions for {file_or_folder_id}: {e}")
            return None

        return DropboxGrants(permissions, covered_emails=covered_emails)

    async def _shared_folder_members(self, folder_id: str, member_id: str) -> list[Permission] | None:
        """A shared folder's own members as grants, every page; None when they could not all be read."""
        if await self._copies_no_source_access():
            return []
        response = await self.data_source.sharing_list_folder_members(shared_folder_id=folder_id, team_member_id=member_id)
        if not response.success:
            self.logger.error(
                f"Could not read the members of shared folder {folder_id}, so its stored grants stay: {response.error}"
            )
            return None
        users, groups, invitees = list(response.data.users), list(response.data.groups), list(response.data.invitees)
        cursor = _next_cursor(response.data)
        while cursor:
            response = await self.data_source.sharing_list_folder_members_continue(cursor=cursor, team_member_id=member_id)
            if not response.success:
                self.logger.error(
                    f"Could not read every member of shared folder {folder_id}, so its stored grants stay: {response.error}"
                )
                return None
            users.extend(response.data.users)
            groups.extend(response.data.groups)
            invitees.extend(response.data.invitees)
            cursor = _next_cursor(response.data)
        return folder_grants(users, groups, invitees)

    async def _sync_user_groups(self) -> None:
        """
        Syncs all Dropbox groups and their members, collecting them into a
        single batch before sending to the processor.
        """
        if await self._copies_no_source_access():
            return
        try:
            self.logger.info("Starting Dropbox user group synchronization")

            # --- 1. Get all groups, with pagination ---
            # A list that could not be read whole writes nothing: the stored groups stay, and the run is not a
            # success, so a full sync does not remove the groups it did not see.
            all_groups_list = []
            groups_response = await self.data_source.team_groups_list()
            while True:
                if not groups_response.success:
                    self.logger.error(f"❌ Failed to fetch the group list: {groups_response.error}")
                    if is_auth_error(groups_response.error):
                        self._health.auth_failed(groups_response.error)
                    else:
                        self._health.skipped("group list", groups_response.error)
                    return
                all_groups_list.extend(groups_response.data.groups)
                if not groups_response.data.has_more:
                    break
                self.logger.info("Fetching more groups...")
                groups_response = await self.data_source.team_groups_list_continue(groups_response.data.cursor)

            self.logger.info(f"Found {len(all_groups_list)} total groups. Now processing members.")

            # --- 2. Define permission mapping (similar to record_groups) ---
            # Dropbox group members are either 'owner' or 'member'

            # This will hold our final list of tuples: List[Tuple[AppUserGroup, List[Permission]]]
            user_groups_batch = []

            # --- 3. Loop through all groups to build the batch (NO processor calls inside loop) ---
            for group in all_groups_list:
                try:
                    # Partial members would replace the stored ones and take access from everyone not read.
                    all_members = await self._fetch_group_members(group.group_id, group.group_name,
                                                                  raise_on_partial=True)

                    processor_group, member_permissions = self._create_user_group_with_permissions(
                        group.group_id, group.group_name, all_members
                    )
                    user_groups_batch.append((processor_group, member_permissions))

                except Exception as e:
                    self.logger.error(f"❌ Failed to process group {group.group_name}: {e}", exc_info=True)
                    self._health.skipped(f"members of group {group.group_name}", e)
                    continue  # Skip this group and move to the next

            # --- 4. Send the ENTIRE batch to the processor ONCE (outside the loop) ---
            if user_groups_batch:
                self.logger.info(f"Submitting {len(user_groups_batch)} user groups to the processor...")
                await self.data_entities_processor.on_new_user_groups(user_groups_batch)
                self.logger.info("Successfully submitted batch to on_new_user_groups.")
            else:
                self.logger.info("No user groups found or processed.")

            self.logger.info("Completed Dropbox user group synchronization.")

        except Exception as e:
            self.logger.error(f"❌ Fatal error in _sync_dropbox_groups: {e}", exc_info=True)
            raise

    async def _sync_group_changes_with_cursor(self) -> None:
        """
        Syncs user group changes incrementally using the team event log cursor.
        """
        if await self._copies_no_source_access():
            return
        try:
            self.logger.info("Starting incremental sync for user groups...")

            # 1. Define a single, global key for the team-wide group event cursor
            sync_point_key = generate_record_sync_point_key(
                "user_group_events", "team_events", "global"
            )

            # 2. Get the last saved cursor from your database
            sync_point = await self.dropbox_cursor_sync_point.read_sync_point(sync_point_key)
            cursor = sync_point.get('cursor')

            if not cursor:
                self.logger.warning("No cursor found for incremental group sync. Running full sync instead.")
                await self._sync_user_groups()
                return

            has_more = True
            latest_cursor_to_save = cursor
            events_processed = 0
            # Before the new events, so that they are applied on top of the corrected groups.
            owed_member_reads, owed_deletes = await self._retry_owed_group_removals(sync_point)

            while has_more:
                try:
                    # 3. Fetch the latest events from the Dropbox audit log
                    async with self.rate_limiter:
                        response = await self.data_source.team_log_get_events_continue(cursor)

                    if not response.success:
                        self.logger.error(f"⚠️ Error fetching team event log: {response.error}")
                        if is_cursor_reset(response.error):
                            # Events since the cursor are lost: read every group again, and take a new cursor.
                            # Removals still owed are kept with it: Dropbox does not list their events again.
                            pending = {"pendingGroupMemberReads": owed_member_reads,
                                       "pendingGroupDeletes": owed_deletes}
                            if await self._initialize_event_cursor(sync_point_key, EventCategory.groups,
                                                                   pending=pending) is not None:
                                # No new cursor: the old one, which resets again next run, keeps them.
                                await self.dropbox_cursor_sync_point.update_sync_point(
                                    sync_point_key, sync_point_data={"cursor": cursor, **pending})
                            await self._sync_user_groups()
                            return
                        self._health.skipped("group changes", response.error)
                        break

                    events = response.data.events
                    self.logger.info(f"Processing {len(events)} new group-related events.")

                    # 4. Process each event individually
                    for event in events:
                        try:
                            await self._process_group_event(event)
                            events_processed += 1
                        except GroupAccessRemovalError as e:
                            # The cursor moves on and Dropbox won't list the event again, so
                            # the group is queued with the cursor: every later run tries again
                            # until what the event should have removed is gone.
                            owed = owed_deletes if e.group_deleted else owed_member_reads
                            if e.group_id not in owed:
                                owed.append(e.group_id)
                            self.logger.error(f"❌ {e}; it will be tried again on the next run", exc_info=True)
                            continue
                        except Exception as e:
                            self.logger.error(f"Error processing group event: {e}", exc_info=True)
                            continue

                    # 5. Update state for the next loop iteration and for final saving
                    latest_cursor_to_save = response.data.cursor
                    has_more = response.data.has_more
                    cursor = latest_cursor_to_save

                except Exception as e:
                    self.logger.error(f"⚠️ Error in group sync loop: {e}", exc_info=True)
                    has_more = False

            # 6. Save the final, most recent cursor back to the database
            if latest_cursor_to_save:
                self.logger.info(f"Storing latest group sync cursor for key {sync_point_key}")
                await self.dropbox_cursor_sync_point.update_sync_point(
                    sync_point_key,
                    sync_point_data={
                        "cursor": latest_cursor_to_save,
                        "pendingGroupMemberReads": owed_member_reads,
                        "pendingGroupDeletes": owed_deletes,
                    }
                )

            self.logger.info(f"Incremental group sync completed. Processed {events_processed} events.")

        except Exception as e:
            self.logger.error(f"⚠️ Fatal error in incremental group sync: {e}", exc_info=True)
            raise

    async def _retry_owed_group_removals(self, sync_point: dict) -> tuple[list[str], list[str]]:
        """
        Try again the group deletions and member removals that earlier runs could not save.

        Returns the ids of the groups whose members still have to be read again, and
        of the groups that still have to be deleted.
        """
        still_to_delete: list[str] = []
        for group_id in sync_point.get('pendingGroupDeletes') or []:
            try:
                await self.data_entities_processor.on_user_group_deleted(
                    external_group_id=group_id,
                    connector_id=self.connector_id
                )
                self.logger.info(f"Deleted group {group_id}, which an earlier run could not delete")
            except Exception as e:
                self.logger.error(
                    f"❌ Deleted group {group_id} still could not be removed, so its members keep "
                    f"its access; will try again next run: {e}",
                    exc_info=True,
                )
                still_to_delete.append(group_id)

        still_to_read: list[str] = []
        for group_id in sync_point.get('pendingGroupMemberReads') or []:
            try:
                await self._replace_stored_group_members(group_id)
            except Exception as e:
                self.logger.error(
                    f"❌ The members of group {group_id} still could not be read and saved, so a member "
                    f"removed from it keeps its access; will try again next run: {e}",
                    exc_info=True,
                )
                still_to_read.append(group_id)

        return still_to_read, still_to_delete

    async def _replace_stored_group_members(self, group_id: str) -> None:
        """
        Read a stored group's current members from Dropbox and save them in place of the
        stored ones, which removes anyone a failed removal left behind. Raises if it can't.
        """
        stored_group = await self.data_entities_processor.get_user_group_by_external_id(
            connector_id=self.connector_id,
            external_id=group_id,
            raise_on_error=True,
        )
        if stored_group is None:
            # Deleted since, so there is no membership left to correct.
            return

        members = await self._fetch_group_members(group_id, stored_group.name, raise_on_partial=True)
        await self.data_entities_processor.on_new_user_groups(
            [self._create_user_group_with_permissions(group_id, stored_group.name, members)]
        )
        self.logger.info(f"Saved the current members of group {group_id}, which an earlier run could not correct")

    async def _process_group_event(self, event) -> None:
        """
        Process a single group-related event from the Dropbox audit log.
        Based on the actual API response structure.
        """
        try:
            # Log the full event for debugging
            self.logger.debug(f"Processing event: {event}")

            event_type = event.event_type._tag

            if event_type == "group_create":
                await self._handle_group_created_event(event)
            elif event_type == "group_delete":
                await self._handle_group_deleted_event(event)
            elif event_type in ["group_add_member", "group_remove_member"]:
                await self._handle_group_membership_event(event, event_type)
            elif event_type in ["group_rename"]:
                await self._handle_group_renamed_event(event)
            elif event_type in ["group_change_member_role"]:
                await self._handle_group_change_member_role_event(event)
            else:
                self.logger.debug(f"Ignoring event type: {event_type}")

        except GroupAccessRemovalError:
            raise
        except Exception as e:
            self.logger.error(f"Error processing group event of type {getattr(event, 'event_type', 'unknown')}: {e}", exc_info=True)

    async def _handle_group_membership_event(self, event, event_type: str) -> None:
        group_id, group_name = None, None
        member_email, member_name = None, None

        # 1. Extract common information (group and user details)
        try:
            for participant in event.participants:
                if participant.is_group():
                    group_info = participant.get_group()
                    group_id = group_info.group_id
                    group_name = group_info.display_name
                elif participant.is_user():
                    user_info = participant.get_user()
                    member_email = user_info.email
                    member_name = user_info.display_name
        except Exception as e:
            self.logger.error(f"Failed to parse participants for event {event_type}: {e}", exc_info=True)
            return

        # 2. Validate that we have the necessary IDs
        if not group_id or not member_email:
            self.logger.warning(f"Could not extract required group_id or member_email from {event_type} event. Skipping.")
            return

        # 3. Perform the appropriate action based on event_type
        if event_type == "group_add_member":
            self.logger.info(f"Adding member '{member_name}' ({member_email}) to group '{group_name}' ({group_id})")

            # Determine permission type (specific to 'add' events)
            permission_type = PermissionType.WRITE  # Default permission
            if hasattr(event.details, 'is_group_owner') and event.details.is_group_owner:
                permission_type = PermissionType.OWNER

            await self.data_entities_processor.on_user_group_member_added(
                external_group_id=group_id,
                user_email=member_email,
                permission_type=permission_type,
                connector_id=self.connector_id
            )

        elif event_type == "group_remove_member":
            self.logger.info(f"Removing member '{member_name}' ({member_email}) from group '{group_name}' ({group_id})")

            try:
                await self.data_entities_processor.on_user_group_member_removed(
                    external_group_id=group_id,
                    user_email=member_email,
                    connector_id=self.connector_id
                )
            except Exception as e:
                raise GroupAccessRemovalError(
                    f"Could not remove {member_email} from group '{group_name}' ({group_id}): {e}",
                    group_id, group_deleted=False,
                ) from e

    async def _handle_group_deleted_event(self, event) -> None:
        """Handle group_delete events from Dropbox audit log."""
        # Extract group_id from participants
        group_id = None
        group_name = None

        for participant in event.participants:
            if participant.is_group():
                group_info = participant.get_group()
                group_id = group_info.group_id
                group_name = group_info.display_name
                self.logger.info(f"Extracted deleted group: {group_name} ({group_id})")
                break

        # Validate we have required information
        if not group_id:
            self.logger.warning("Could not extract group_id from group_delete event")
            return

        self.logger.info(f"Deleting group {group_name} ({group_id})")

        try:
            await self.data_entities_processor.on_user_group_deleted(
                external_group_id=group_id,
                connector_id=self.connector_id
            )
        except Exception as e:
            raise GroupAccessRemovalError(
                f"Could not delete group '{group_name}' ({group_id}): {e}", group_id, group_deleted=True
            ) from e

    async def _handle_group_created_event(self, event) -> None:
        """Handle group_create events from Dropbox audit log."""
        # Extract group info from event participants
        group_id = None
        group_name = None

        for participant in event.participants:
            if participant.is_group():
                group_info = participant.get_group()
                group_id = group_info.group_id
                group_name = group_info.display_name
                break

        if not group_id:
            self.logger.warning("Could not extract group_id from group_create event")
            return

        self.logger.info(f"Creating group {group_name} ({group_id})")

        try:
            # Process the single newly created group
            await self._process_single_group(group_id, group_name)

        except Exception as e:
            self.logger.error(f"Error processing group_create event for group {group_id}: {e}", exc_info=True)

    async def _process_single_group(self, group_id: str, group_name: str) -> None:
        """
        Process a single group by fetching its members and creating the appropriate
        AppUserGroup and permissions. This reuses logic from _sync_user_groups.
        """
        try:
            # Get all members for this group (reused from _sync_user_groups section 3a)
            all_members = await self._fetch_group_members(group_id, group_name)

            # Create the AppUserGroup and permissions (reused from _sync_user_groups section 3b-3c)
            processor_group, member_permissions = self._create_user_group_with_permissions(
                group_id, group_name, all_members
            )

            # Send to processor (reused from _sync_user_groups section 4)
            user_groups_batch = [(processor_group, member_permissions)]

            self.logger.info(f"Submitting newly created group {group_name} to processor...")
            await self.data_entities_processor.on_new_user_groups(user_groups_batch)
            self.logger.info(f"Successfully processed group_create event for {group_name}")

        except Exception as e:
            self.logger.error(f"Failed to process single group {group_name} ({group_id}): {e}", exc_info=True)

    def _create_user_group_with_permissions(self, group_id: str, group_name: str, all_members: list) -> tuple:
        """
        Create AppUserGroup and permissions list from group members.
        Extracted from _sync_user_groups sections 3b-3c for reusability.
        """
        # Permission mapping (from _sync_user_groups)


        # Create the AppUserGroup object (from _sync_user_groups section 3b)
        processor_group = AppUserGroup(
            app_name=self.connector_name,
            connector_id=self.connector_id,
            source_user_group_id=group_id,
            name=group_name,
            org_id=self.data_entities_processor.org_id
        )

        # Create permissions list (from _sync_user_groups section 3c)
        member_permissions = []
        for member in all_members:
            user_permission = AppUser(
                app_name=self.connector_name,
                connector_id=self.connector_id,
                source_user_id=member.profile.team_member_id,
                email=member.profile.email,
                full_name=member.profile.name.display_name,
            )
            member_permissions.append(user_permission)

        return processor_group, member_permissions

    async def _handle_group_renamed_event(self, event) -> None:
        """Handle group_rename events from Dropbox audit log."""
        # Extract group info from event participants
        group_id = None
        new_group_name = None

        for participant in event.participants:
            if participant.is_group():
                group_info = participant.get_group()
                group_id = group_info.group_id
                new_group_name = group_info.display_name  # This should have the updated name
                break


        details_obj = event.details

        # Log the structure for debugging
        self.logger.debug(f"Event details type: {type(details_obj)}")
        self.logger.debug(f"Event details: {details_obj}")

        old_name: Optional[str] = None
        new_name_from_details: Optional[str] = None

        # Try different ways to access the GroupRenameDetails
        if hasattr(details_obj, 'get_group_rename_details'):
            group_rename_details = details_obj.get_group_rename_details()
            old_name = group_rename_details.previous_value
            new_name_from_details = group_rename_details.new_value
            self.logger.debug(
                "Used get_group_rename_details() method: old_name=%s, new_name=%s",
                old_name,
                new_name_from_details,
            )

        new_name = new_name_from_details or new_group_name

        if not group_id or not new_name:
            self.logger.warning(
                f"Could not extract required info from group_rename event. "
                f"group_id={group_id}, new_name={new_name}"
            )
            return

        self.logger.info(f"Renaming group {group_id} from '{old_name}' to '{new_name}'")

        try:
            await self._update_group_name(group_id, new_name, old_name)

        except Exception as e:
            self.logger.error(f"Error processing group_rename event for group {group_id}: {e}", exc_info=True)

    async def _update_group_name(self, group_id: str, new_name: str, old_name: str = None) -> None:
        """Update the name of an existing group in the database."""
        success = await self.data_entities_processor.update_user_group_name(
            external_group_id=group_id,
            new_name=new_name,
            connector_id=self.connector_id,
        )
        if success:
            self.logger.info(
                f"Successfully renamed group {group_id} from '{old_name}' to '{new_name}'"
            )
        else:
            self.logger.warning(
                f"Cannot rename group: Group with external ID {group_id} not found in database"
            )

    async def _handle_group_change_member_role_event(self, event) -> None:
        """Handle group_change_member_role events from Dropbox audit log."""
        # Extract group and user info from event participants
        group_id = None
        group_name = None
        user_email = None

        for participant in event.participants:
            if participant.is_group():
                group_info = participant.get_group()
                group_id = group_info.group_id
                group_name = group_info.display_name
            elif participant.is_user():
                user_info = participant.get_user()
                user_email = user_info.email

        # Extract new role from event details
        new_is_owner = None
        try:
            if hasattr(event.details, 'get_group_change_member_role_details'):
                role_details = event.details.get_group_change_member_role_details()
                new_is_owner = role_details.is_group_owner
            elif hasattr(event.details, 'is_group_owner'):
                new_is_owner = event.details.is_group_owner
            else:
                self.logger.warning(f"Could not extract role details from event: {event.details}")
        except Exception as e:
            self.logger.warning(f"Error extracting role details: {e}")
            self.logger.debug(f"Event details: {event.details}")

        # Validate we have required information
        if not group_id or not user_email or new_is_owner is None:
            self.logger.warning(
                f"Missing required info from group_change_member_role event: "
                f"group_id={group_id}, user_email={user_email}, new_is_owner={new_is_owner}"
            )
            return

        # Convert boolean to permission type
        new_permission_type = PermissionType.OWNER if new_is_owner else PermissionType.WRITE

        self.logger.info(
            f"Changing role for user {user_email} in group '{group_name}' ({group_id}) "
            f"to {'owner' if new_is_owner else 'member'} (permission: {new_permission_type})"
        )

        try:
            success = await self._update_user_group_permission(
                group_id, user_email, new_permission_type
            )

            if success:
                self.logger.info(
                    f"Successfully updated role for {user_email} in group {group_name}"
                )
            else:
                self.logger.warning(
                    f"Failed to update role for {user_email} in group {group_name}"
                )

        except Exception as e:
            self.logger.error(
                f"Error processing group_change_member_role event for user {user_email} "
                f"in group {group_id}: {e}", exc_info=True
            )

    async def _update_user_group_permission(
        self,
        group_id: str,
        user_email: str,
        new_permission_type: PermissionType
    ) -> bool:
        """Update a user's permission level within a group."""
        try:
            user = await self.data_entities_processor.get_user_by_email(user_email)
            if not user:
                self.logger.warning(
                    f"Cannot update group permission: User with email {user_email} not found"
                )
                return False

            user_group = await self.data_entities_processor.get_user_group_by_external_id(
                connector_id=self.connector_id,
                external_id=group_id,
            )
            if not user_group:
                self.logger.warning(
                    f"Cannot update group permission: Group with external ID {group_id} not found"
                )
                return False

            # A membership is the user's grant on the group: typed USER, as access queries expect.
            permission = Permission(
                external_id=user.id,
                email=user_email,
                type=new_permission_type,
                entity_type=EntityType.USER,
            )
            await self.data_entities_processor.upsert_permission_edge(
                connector_id=self.connector_id,
                from_id=user.id,
                from_collection=CollectionNames.USERS.value,
                to_id=user_group.id,
                to_collection=CollectionNames.GROUPS.value,
                permission=permission,
            )
            return True

        except Exception as e:
            self.logger.error(
                f"Failed to update permission for {user_email} in group {group_id}: {e}",
                exc_info=True,
            )
            return False

    async def _sync_sharing_changes_with_cursor(self, sync_point_key: str, cursor: str) -> None:
        """
        Listens for sharing events and triggers the re-sync workflow using the correct
        response structure.
        """
        if await self._copies_no_source_access():
            return
        self.logger.info("Starting incremental sync for sharing events...")
        has_more = True
        latest_cursor_to_save = cursor

        restarted = False
        while has_more:
            try:
                response = await self.data_source.team_log_get_events_continue(cursor)

                if not response.success and is_cursor_reset(response.error) and not restarted:
                    # GetTeamEventsContinueError.reset carries the time of the last event the cursor returned:
                    # read again from there (team_log.stone), and process that page too. Re-reading an item
                    # twice is harmless; dropping the page would lose the changes in it.
                    self.logger.warning(f"Dropbox reset the sharing event cursor: {response.error}")
                    restarted = True
                    restart = _reset_time(response.error)
                    if restart is None:
                        self._health.skipped("sharing changes", "the activity log cursor was reset")
                        await self._initialize_event_cursor(sync_point_key, EventCategory.sharing)
                        return
                    from dropbox.team_common import TimeRange

                    response = await self.data_source.team_log_get_events(
                        category=EventCategory.sharing, time=TimeRange(start_time=restart),
                    )

                if not response.success:
                    self.logger.error(f"⚠️ Error fetching sharing event log: {response.error}")
                    self._health.skipped("sharing changes", response.error)
                    break

                for event in response.data.events:
                    try:
                        event_type = event.event_type._tag

                        # 1. Check for the correct, specific event types
                        if event_type in SHARING_CHANGE_EVENTS:
                            self.logger.info(f"Processing permission change event: {event_type}")

                            member = self._sharing_event_member(event)
                            if member is None:
                                self.logger.warning(f"{event_type} names no team member to read the item as. Skipping.")
                                continue
                            team_member_id, user_email = member

                            # 3. Iterate through assets to find the file/folder ID (CORRECTED LOGIC)
                            if not event.assets:
                                self.logger.warning(f"Event {event_type} has no assets. Skipping.")
                                continue

                            for asset in event.assets:

                                if asset.is_file():
                                    file_info = asset.get_file()
                                    id_to_sync = file_info.file_id
                                    ns_id_for_file = None
                                    if file_info.path and file_info.path.namespace_relative:
                                        ns_id_for_file = file_info.path.namespace_relative.ns_id

                                    await self._resync_record_by_external_id(
                                        external_id=id_to_sync,
                                        team_member_id=team_member_id,
                                        user_email=user_email,
                                        ns_id=ns_id_for_file
                                    )

                                elif asset.is_folder():
                                    # A shared folder is a record group: re-read its name, place and members.
                                    folder_info = asset.get_folder()
                                    if folder_info.path and folder_info.path.namespace_relative:
                                        await self._refresh_shared_folder(
                                            folder_info.path.namespace_relative.ns_id, team_member_id,
                                        )

                    except Exception as e:
                        self.logger.error(f"Error processing a single sharing event: {e}", exc_info=True)
                        continue # Move to the next event

                # 5. Update state for the next loop
                latest_cursor_to_save = response.data.cursor
                has_more = response.data.has_more
                cursor = latest_cursor_to_save

            except Exception as e:
                self.logger.error(f"Fatal error in sharing sync loop: {e}", exc_info=True)
                has_more = False

        # 6. Save the final cursor
        self.logger.info(f"Storing latest sharing sync cursor for key {sync_point_key}")
        await self.dropbox_cursor_sync_point.update_sync_point(
            sync_point_key,
            sync_point_data={"cursor": latest_cursor_to_save}
        )

    @staticmethod
    def _sharing_event_member(event: TeamEvent) -> tuple[str, str] | None:
        """The team member to re-read a shared item as: whose content it is (the event's context).

        The actor is only a fallback. A change made through an app has an app
        actor, which is no member, and the actor need not have access anyway.
        """
        context = event.context
        if context is not None and context.is_team_member():
            info = context.get_team_member()
            return info.team_member_id, info.email
        actor = event.actor
        for is_kind, get in ((actor.is_user, actor.get_user), (actor.is_admin, actor.get_admin)):
            if is_kind():
                info = get()
                if getattr(info, "team_member_id", None):
                    return info.team_member_id, info.email
        return None

    async def _resync_record_by_external_id(
        self,
        external_id: str,
        team_member_id: str,
        user_email: str,
        ns_id: Optional[str] = None
    ) -> None:
        """Re-read one file after a sharing event on it and apply what changed."""
        self.logger.info(f"Re-syncing file due to a permission event: {external_id}")
        try:
            metadata_result = await self.data_source.files_get_metadata(
                path=external_id,
                team_member_id=team_member_id,
                team_folder_id=ns_id
            )
            if not metadata_result or not metadata_result.success:
                self.logger.error(f"Could not fetch metadata for re-sync: {external_id}. Error: {metadata_result.error}")
                return

            existing_record = await self.data_entities_processor.get_record_by_external_id(self.connector_id, external_id)
            if not existing_record:
                self.logger.warning(f"File record {external_id} not found in DB for re-sync. Cannot determine parent group.")
                return
            record_group_id = existing_record.external_record_group_id

            # A sharing change does not show in the file's metadata, so its members are read whatever the
            # listing flag says.
            record_update = await self._process_dropbox_entry(
                entry=metadata_result.data,
                user_id=team_member_id,
                user_email=user_email,
                record_group_id=record_group_id,
                is_person_folder=bool(record_group_id) and record_group_id.startswith("dbmid:"),
                force_members=True,
            )

            if record_update and record_update.record:
                self.logger.info(f"Permissions updated for {record_update.record.record_name}")
                await self._handle_record_updates(record_update)
            else:
                self.logger.info(f"Re-sync for {external_id} did not result in an update.")

        except Exception as e:
            self.logger.error(f"Error during single record re-sync for {external_id}: {e}", exc_info=True)
