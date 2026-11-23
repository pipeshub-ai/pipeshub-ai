"""Box: reading who may see what from Box.

Item collaborators, Box groups with their members, and the clean-up when a collaboration is removed. The free
connector keeps listing and syncing content and calls these methods; without a licence key a company-wide connector
reads none of this (the processor's licence gate).
"""

from collections import deque

from app.models.entities import AppUserGroup
from app.models.permission import EntityType, Permission, PermissionType


class BoxPermissionsMixin:
    """The permission-reading half of ``BoxConnector``."""

    async def _copies_no_source_access(self) -> bool:
        """Without a licence key a company-wide connector reads no access from Box. The processor then keeps every
        stored grant and group whatever it is handed (``skips_permission_writes``), so the empty answers below only
        keep the run whole: None is Box's "could not read", which would mark every item as read in part and hold the
        event stream back on every run."""
        return await self.data_entities_processor.skips_permission_writes(self.connector_id)

    def _explain_missing_group_scope(self, error: object) -> None:
        if str(error).startswith("403"):
            self.logger.error(
                "Box refused to list groups (403), so group access in PipesHub is not being updated. "
                "In the Box Developer Console, turn on the app's 'Manage groups' scope, then have a Box "
                "admin re-authorize the app in the Box Admin Console."
            )

    async def _get_permissions(self, item_id: str, item_type: str) -> list[Permission] | None:
        """
        Fetch permissions for a Box item (file or folder).
        Returns None when a page could not be read, so a short list is never taken as the whole one.
        """
        if await self._copies_no_source_access():
            return []
        permissions = []
        collaborations = []
        marker = None
        try:
            while True:
                if item_type == 'file':
                    response = await self.data_source.collaborations_get_file_collaborations(file_id=item_id, marker=marker)
                else:
                    response = await self.data_source.collaborations_get_folder_collaborations(folder_id=item_id, marker=marker)

                if not response.success:
                    self.logger.warning(f"Could not read collaborators of {item_type} {item_id}: {response.error}")
                    if not self._is_final_answer(response.error):
                        self._mark_read_incomplete(response.error)
                    return None

                data = self._to_dict(response.data)
                collaborations.extend(data.get('entries', []))
                marker = data.get('next_marker')
                if not marker:
                    break

            for collab in collaborations:
                status = collab.get('status')
                if status and status != 'accepted':
                    continue
                created_on = (collab.get('item') or {}).get('id')
                if created_on and str(created_on) != str(item_id):
                    continue
                # An access-only invite does not waterfall; it stays on the nested file.
                if item_type != 'file' and collab.get('is_access_only'):
                    continue
                accessible_by = collab.get('accessible_by') or {}
                if not isinstance(accessible_by, dict):
                    continue
                role = collab.get('role', 'viewer')
                # Box shows an uploader item names, but no preview or download.
                if role == 'uploader':
                    continue

                # Map Box roles to our permission types
                permission_type = PermissionType.READ
                if role in ['editor', 'co-owner']:
                    permission_type = PermissionType.WRITE
                elif role == 'owner':
                    permission_type = PermissionType.OWNER

                # Determine entity type
                entity_type = EntityType.USER
                accessible_by_type = accessible_by.get('type')
                if accessible_by_type == 'group':
                    entity_type = EntityType.GROUP

                # Skip if no ID (invalid collaboration)
                accessible_by_id = accessible_by.get('id')
                if not accessible_by_id:
                    continue

                permissions.append(Permission(
                    external_id=accessible_by_id,
                    email=accessible_by.get('login'),
                    type=permission_type,
                    entity_type=entity_type
                ))

        except Exception as e:
            self.logger.warning(f"Error fetching permissions for {item_type} {item_id}: {e}")
            self._mark_read_incomplete(e)
            return None

        return permissions

    async def _remove_user_access_from_folder_recursively(
        self,
        folder_external_id: str,
        user_id: str
    ) -> None:
        """
        Remove user access from a folder and all its descendant files and folders.
        This ensures that when folder collaboration is revoked, all items inside
        also have the user's permissions removed.
        Args:
            folder_external_id: External ID of the folder
            user_id: Internal user ID whose access should be removed
        """
        if await self._copies_no_source_access():
            return
        try:
            self.logger.info(f"📁 Removing user {user_id} access from folder {folder_external_id} and descendants")

            # Track all items to remove access from
            items_to_process = deque([folder_external_id])
            processed_items = set()

            # Process items recursively (BFS approach)
            while items_to_process:
                current_external_id = items_to_process.popleft()

                if current_external_id in processed_items:
                    continue

                processed_items.add(current_external_id)

                # Remove user access from this item
                try:
                    await self.data_entities_processor.remove_user_access_to_record(
                        connector_id=self.connector_id,
                        external_id=current_external_id,
                        user_id=user_id
                    )
                except Exception as e:
                    self.logger.warning(f"⚠️ Failed to remove access from {current_external_id}: {e}")
                    self._mark_read_incomplete(e)

                # Get children of this item
                try:
                    children = await self.data_entities_processor.get_records_by_parent(
                        connector_id=self.connector_id,
                        parent_external_record_id=current_external_id
                    )

                    if children:
                        # Add children to the processing queue
                        for child in children:
                            if child.external_record_id and child.external_record_id not in processed_items:
                                items_to_process.append(child.external_record_id)
                except Exception as e:
                    self.logger.debug(f"No children found for {current_external_id} or error: {e}")
                    self._mark_read_incomplete(e)

            self.logger.info(f"✅ Removed user access from folder and {len(processed_items) - 1} descendants")

        except Exception as e:
            self.logger.error(f"❌ Failed to remove folder access recursively: {e}", exc_info=True)
            self._mark_read_incomplete(e)

    async def _sync_user_groups(self) -> None:
        """
        Sync Box groups and their memberships.
        Includes Reconciliation: Deletes groups from DB that no longer exist in Box.
        """
        if await self._copies_no_source_access():
            return
        try:
            self.logger.info("Syncing Box groups...")

            all_users = await self.data_entities_processor.get_all_app_users(
                connector_id=self.connector_id
            )
            user_map = {u.email.lower(): u for u in all_users if u.email}

            self.logger.info(f"Pre-fetched {len(user_map)} users for group sync lookup.")

            # Track all IDs found in Box
            found_box_group_ids = set()

            # Add Virtual Group IDs to this set so we don't accidentally delete them
            found_box_group_ids.add("PUBLIC")
            found_box_group_ids.add(f"ORG_{self.data_entities_processor.org_id}")

            offset = 0
            limit = 1000

            while True:
                response = await self.data_source.groups_get_groups(limit=limit, offset=offset)

                if not response.success:
                    self.logger.error(f"Failed to fetch groups: {response.error}")
                    self._explain_missing_group_scope(response.error)
                    self.logger.warning("Skipping removal of deleted groups because the group list from Box is incomplete.")
                    self._mark_read_incomplete(response.error)
                    return

                data = self._to_dict(response.data)
                groups_data = data.get('entries', [])

                if not groups_data:
                    break

                for group in groups_data:
                    group_id = group.get('id')

                    if group_id:
                        found_box_group_ids.add(group_id)

                    group_name = group.get('name', '')

                    app_user_group = AppUserGroup(
                        app_name=self.connector_name,
                        connector_id=self.connector_id,
                        source_user_group_id=group_id,
                        name=group_name,
                        org_id=self.data_entities_processor.org_id,
                        description=group.get('description')
                    )

                    memberships = await self._get_group_memberships(group_id)
                    if memberships is None:
                        # Saving the group replaces its stored members, so an unread list would remove them all.
                        continue

                    group_member_users = []

                    for membership in memberships:
                        user_info = membership.get('user', {})
                        email = user_info.get('login')

                        if email:
                            # Lookup user in our pre-fetched map
                            found_user = user_map.get(email.lower())
                            if found_user:
                                group_member_users.append(found_user)

                    # Sync group and memberships using the in-memory list
                    await self.data_entities_processor.on_new_user_groups([(app_user_group, group_member_users)])

                offset += limit

                if len(groups_data) < limit:
                    break

            # Delete Stale Groups
            await self._reconcile_deleted_groups(found_box_group_ids)

            self.logger.info("Box groups sync completed")

        except Exception as e:
            self.logger.error(f"Error syncing Box groups: {e}", exc_info=True)
            self._mark_read_incomplete(e)

    async def _get_group_memberships(self, group_id: str) -> list[dict] | None:
        """Every membership of a group, or None when a page of them could not be read."""
        memberships: list[dict] = []
        offset = 0
        limit = 1000
        while True:
            response = await self.data_source.groups_get_group_memberships(
                group_id=group_id, limit=limit, offset=offset
            )
            if not response.success:
                self.logger.warning(f"Failed to fetch members of group {group_id}: {response.error}")
                self._explain_missing_group_scope(response.error)
                self._mark_read_incomplete(response.error)
                return None
            data = self._to_dict(response.data)
            entries = data.get('entries', [])
            memberships.extend(entries)
            offset += len(entries)
            if not entries or offset >= data.get('total_count', 0):
                return memberships

    async def _reconcile_deleted_groups(self, active_box_ids: set) -> None:
        """
        Compares Box IDs against DB IDs and deletes stale groups.
        """
        try:
            # 1. Get all groups currently in the DB for this connector
            db_groups = await self.data_entities_processor.get_all_user_groups(
                connector_id=self.connector_id
            )

            # 2. Identify groups in DB that are NOT in the active_box_ids set
            stale_groups = [
                g for g in db_groups
                if g.source_user_group_id not in active_box_ids
            ]

            if not stale_groups:
                self.logger.info("No stale groups found.")
                return

            self.logger.info(f"🧹 Found {len(stale_groups)} stale groups to delete.")

            # 3. Delete
            for group in stale_groups:
                external_id = group.source_user_group_id
                self.logger.info(f"Deleting stale group: {group.name} ({external_id})")

                # Use existing delete handler
                try:
                    await self.data_entities_processor.on_user_group_deleted(
                        external_group_id=external_id,
                        connector_id=self.connector_id
                    )
                except Exception as e:
                    # The other stale groups are still deleted; this one is found
                    # stale again on the next sync.
                    self.logger.error(f"Failed to delete stale group {group.name} ({external_id}): {e}", exc_info=True)

        except Exception as e:
            self.logger.error(f"Error during group reconciliation: {e}", exc_info=True)
