"""Outlook (Microsoft 365, company-wide): reading who may see what from Microsoft Entra ID.

The members of each Microsoft 365 group, and the group's grant on its group mailbox. The free connector keeps
syncing every user's mailbox, granted to its owner (structural: the mailbox is theirs, nothing is read from a
sharing setting), and the group mailboxes with their conversations; without a licence key a company-wide
connector reads no group members and grants no group mailbox (the processor's licence gate).
"""

from msgraph.generated.models.group import Group  # type: ignore
from msgraph.generated.models.user import User  # type: ignore

from app.connectors.sources.microsoft.common.outlook_constants import OutlookAPIFields
from app.models.permission import EntityType, Permission, PermissionType


class OutlookPermissionsMixin:
    """The permission-reading half of ``OutlookConnector``."""

    async def _copies_no_source_access(self) -> bool:
        """Without a licence key a company-wide connector reads no access from Microsoft 365. The processor then
        keeps every stored grant and group with its members (``skips_permission_writes``), so the empty answers
        below only keep the run whole: a group mailbox is still written, and its conversations still sync."""
        return await self.data_entities_processor.skips_permission_writes(self.connector_id)

    async def _group_mailbox_grants(self, group_id: str) -> list[Permission]:
        """The group mailbox's audience: the members of its Microsoft 365 group."""
        if await self._copies_no_source_access():
            return []
        # Create group-level permission for the Microsoft 365 group
        group_permission = Permission(
            external_id=group_id,
            type=PermissionType.READ,
            entity_type=EntityType.GROUP
        )
        return [group_permission]

    async def _get_group_members(self, group_id: str) -> list[User]:
        """Get members of a specific group with pagination."""
        if await self._copies_no_source_access():
            return []
        try:
            if not self.external_users_client:
                raise Exception("External Users Groups client not initialized")

            all_members = []
            next_url = None
            page_num = 1

            while True:
                response = await self.external_users_client.groups_list_transitive_members(
                    group_id=group_id,
                    next_url=next_url,
                    select=OutlookAPIFields.GROUP_MEMBER_SELECT_FIELDS
                )

                if not response.success:
                    # Raise: the member list replaces every membership edge, so a partial
                    # one would revoke the missing members' access for this run.
                    raise Exception(f"Failed to get members page {page_num} for group {group_id}: {response.error}")

                # response.data is DirectoryObjectCollectionResponse with .value containing list[User]
                members_page = response.data.value if response.data.value else []
                all_members.extend(members_page)

                # Check for next page
                next_url = response.data.odata_next_link if response.data.odata_next_link else None
                if not next_url:
                    break

                page_num += 1

            return all_members

        except Exception as e:
            self.logger.error(f"Error getting group members for {group_id}: {e}")
            raise

    async def _get_user_groups(self, user_id: str) -> list[Group]:
        """Get groups that a user is a member of (cached for performance).

        Returns:
            List of Pydantic Group objects
        """
        if await self._copies_no_source_access():
            return []
        try:
            if not self.external_users_client:
                return []

            # Use the existing groups_list_member_of method
            response = await self.external_users_client.groups_list_member_of(
                group_id=user_id,  # Note: This method name is misleading, it actually takes user_id
                select=OutlookAPIFields.USER_GROUP_SELECT_FIELDS
            )

            if not response.success:
                return []

            # response.data is DirectoryObjectCollectionResponse with .value containing list[Group]
            return response.data.value if response.data.value else []

        except Exception as e:
            self.logger.error(f"Error getting user groups for {user_id}: {e}")
            return []
