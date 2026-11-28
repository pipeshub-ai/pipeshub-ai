# Copyright (c) 2026 PipesHub Inc.
# Licensed under the PipesHub Enterprise License. See the LICENSE file in this
# ee folder. Free for development, testing and evaluation. Production use needs
# a PipesHub licence key.

"""Gmail for Workspace: reading who may see what from Google Workspace.

Google groups with their members. The free connector keeps syncing every mailbox and its mail, each granted to
the mailbox owner (structural: the mailbox is theirs, nothing is read from a sharing setting); without a licence
key a company-wide connector reads no Google groups (the processor's licence gate).
"""

from typing import Dict, List, Optional

from app.models.entities import AppUser, AppUserGroup
from app.utils.time_conversion import parse_timestamp


class GmailWorkspacePermissionsMixin:
    """The permission-reading half of ``GoogleGmailTeamConnector``."""

    async def _copies_no_source_access(self) -> bool:
        """Without a licence key a company-wide connector reads no access from Google Workspace. The processor then
        keeps every stored group and its members (``skips_permission_writes``)."""
        return await self.data_entities_processor.skips_permission_writes(self.connector_id)

    async def _sync_user_groups(self) -> None:
        """Sync user groups and their members from Google Workspace Admin API."""
        if await self._copies_no_source_access():
            return
        try:
            if not self.admin_data_source:
                self.logger.error("Admin data source not initialized. Call init() first.")
                raise ValueError("Admin data source not initialized")

            self.logger.info("Fetching all groups from Google Workspace Admin API...")
            page_token: Optional[str] = None
            total_groups_processed = 0

            while True:
                try:
                    # Fetch groups with pagination
                    result = await self.admin_data_source.groups_list(
                        customer="my_customer",
                        orderBy="email",
                        pageToken=page_token,
                        maxResults=200  # Maximum allowed by Google Admin API
                    )

                    groups_data = result.get("groups", [])
                    if not groups_data:
                        break

                    # Process each group
                    for group in groups_data:
                        try:
                            await self._process_group(group)
                            total_groups_processed += 1
                        except Exception as e:
                            self.logger.error(
                                f"Error processing group {group.get('id', 'unknown')}: {e}",
                                exc_info=True
                            )
                            self._sync_health.unread_groups.append(group.get("email") or group.get("id", "unknown"))
                            # Unknown is not empty: keep the stored members of this group.
                            if group.get("email"):
                                await self.data_entities_processor.note_kept_user_group(
                                    self.connector_id, group["email"]
                                )
                            continue

                    # Check for next page
                    page_token = result.get("nextPageToken")
                    if not page_token:
                        break

                    self.logger.info(f"Processed {len(groups_data)} groups (total so far: {total_groups_processed})")

                except Exception as e:
                    self.logger.error(f"Error fetching groups page: {e}", exc_info=True)
                    raise

            self.logger.info(f"✅ Successfully synced {total_groups_processed} user groups")

        except Exception as e:
            self.logger.error(f"❌ Error syncing user groups: {e}", exc_info=True)
            raise

    async def _process_group(self, group: Dict) -> None:
        """
        Process a single group: fetch members and create AppUserGroup with AppUser objects.

        Args:
            group: Group dictionary from Google Admin API
        """
        try:
            group_id = group.get("email")
            if not group_id:
                self.logger.warning("Skipping group without ID")
                return

            # Fetch members for this group
            self.logger.debug(f"Fetching members for group: {group.get('name', group_id)}")
            members = await self._fetch_group_members(group_id)

            # Filter to only include user members (skip groups and customers); a user reached
            # both directly and through a nested group is listed once.
            user_members = list({
                m.get("id") or m.get("email"): m for m in members if m.get("type") == "USER"
            }.values())

            # Create AppUserGroup object
            group_name = group.get("name", "")
            if not group_name:
                group_name = group.get("email", group_id)

            # Convert creation time to epoch milliseconds
            source_created_at = None
            creation_time = group.get("creationTime")
            if creation_time:
                try:
                    source_created_at = parse_timestamp(creation_time)
                except Exception as e:
                    self.logger.warning(f"Failed to parse creation time for group {group_id}: {e}")

            user_group = AppUserGroup(
                source_user_group_id=group_id,
                app_name=self.connector_name,
                connector_id=self.connector_id,
                name=group_name,
                description=group.get("description"),
                source_created_at=source_created_at
            )

            # Create AppUser objects for each member
            app_users: List[AppUser] = []
            for member in user_members:
                try:
                    member_email = member.get("email", "")
                    if not member_email:
                        self.logger.warning(f"Skipping member without email: {member.get('id')}")
                        continue

                    # For group members, we may not have full user details
                    # Use email as fallback for full_name if not available
                    member_id = member.get("id", "")

                    # Try to find user in synced users list for full details
                    full_name = member_email  # Default to email
                    source_created_at_user = None

                    # Look up user in synced users if available
                    if self.synced_users:
                        for synced_user in self.synced_users:
                            if synced_user.source_user_id == member_id or synced_user.email.lower() == member_email.lower():
                                full_name = synced_user.full_name
                                source_created_at_user = synced_user.source_created_at
                                break

                    app_user = AppUser(
                        app_name=self.connector_name,
                        connector_id=self.connector_id,
                        source_user_id=member_id,
                        email=member_email,
                        full_name=full_name,
                        source_created_at=source_created_at_user
                    )
                    app_users.append(app_user)

                except Exception as e:
                    self.logger.error(f"Error processing group member {member.get('id', 'unknown')}: {e}", exc_info=True)
                    continue

            # Send to processor
            if app_users or not user_members:
                # The member read finished (it raises otherwise) and includes nested groups' members,
                # so an empty list is real: write it, so the members of a group that was emptied go.
                await self.data_entities_processor.on_new_user_groups([(user_group, app_users)])
                self.logger.debug(f"Processed group '{group_name}' with {len(app_users)} members")
            else:
                self.logger.warning(f"Group '{group_name}': no member had an email, keeping stored members")
                await self.data_entities_processor.note_kept_user_group(self.connector_id, group_id)

        except Exception as e:
            self.logger.error(f"Error processing group {group.get('id', 'unknown')}: {e}", exc_info=True)
            raise

    async def _fetch_group_members(self, group_id: str) -> List[Dict]:
        """
        Fetch all members of a group with pagination.

        Args:
            group_id: The group ID or email

        Returns:
            List of member dictionaries
        """
        members: List[Dict] = []
        page_token: Optional[str] = None

        while True:
            try:
                result = await self.admin_data_source.members_list(
                    groupKey=group_id,
                    # Members of nested groups too: they are members of this group in Workspace,
                    # so a group of groups is not written as empty.
                    includeDerivedMembership=True,
                    pageToken=page_token,
                    maxResults=200  # Maximum allowed by Google Admin API
                )

                page_members = result.get("members", [])
                if not page_members:
                    break

                members.extend(page_members)

                # Check for next page
                page_token = result.get("nextPageToken")
                if not page_token:
                    break

            except Exception as e:
                self.logger.error(f"Error fetching members for group {group_id}: {e}", exc_info=True)
                raise

        return members
