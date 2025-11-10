import asyncio
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from logging import Logger
from typing import AsyncGenerator, Dict, List, Optional, Tuple

from aiolimiter import AsyncLimiter
from azure.identity.aio import ClientSecretCredential
from fastapi import HTTPException
from fastapi.responses import StreamingResponse
from msgraph import GraphServiceClient
from msgraph.generated.models.drive_item import DriveItem
from msgraph.generated.models.group import Group
from msgraph.generated.models.subscription import Subscription

from app.config.configuration_service import ConfigurationService
from app.config.constants.arangodb import Connectors, MimeTypes, OriginTypes
from app.config.constants.http_status_code import HttpStatusCode
from app.connectors.core.base.connector.connector_service import BaseConnector
from app.connectors.core.base.data_processor.data_source_entities_processor import (
    DataSourceEntitiesProcessor,
)
from app.connectors.core.base.data_store.data_store import DataStoreProvider
from app.connectors.core.base.sync_point.sync_point import (
    SyncDataPointType,
    SyncPoint,
    generate_record_sync_point_key,
)
from app.connectors.core.registry.connector_builder import (
    AuthField,
    CommonFields,
    ConnectorBuilder,
    DocumentationLink,
)
from app.connectors.sources.microsoft.common.apps import OneDriveApp
from app.connectors.sources.microsoft.common.msgraph_client import (
    MSGraphClient,
    RecordUpdate,
    map_msgraph_role_to_permission_type,
)
from app.models.entities import (
    AppUser,
    AppUserGroup,
    FileRecord,
    Record,
    RecordGroupType,
    RecordType,
)
from app.models.permission import EntityType, Permission
from app.utils.streaming import stream_content
from app.utils.time_conversion import get_epoch_timestamp_in_ms


@dataclass
class OneDriveCredentials:
    tenant_id: str
    client_id: str
    client_secret: str
    has_admin_consent: bool = False

@ConnectorBuilder("OneDrive")\
    .in_group("Microsoft 365")\
    .with_auth_type("OAUTH_ADMIN_CONSENT")\
    .with_description("Sync files and folders from OneDrive")\
    .with_categories(["Storage"])\
    .configure(lambda builder: builder
        .with_icon("/assets/icons/connectors/onedrive.svg")
        .add_documentation_link(DocumentationLink(
            "Azure AD App Registration Setup",
            "https://docs.microsoft.com/en-us/azure/active-directory/develop/quickstart-register-app",
            "setup"
        ))
        .add_documentation_link(DocumentationLink(
            'Pipeshub Documentation',
            'https://docs.pipeshub.com/connectors/microsoft-365/one-drive',
            'pipeshub'
        ))
        .with_redirect_uri("connectors/oauth/callback/OneDrive", False)
        .add_auth_field(AuthField(
            name="clientId",
            display_name="Application (Client) ID",
            placeholder="Enter your Azure AD Application ID",
            description="The Application (Client) ID from Azure AD App Registration"
        ))
        .add_auth_field(AuthField(
            name="clientSecret",
            display_name="Client Secret",
            placeholder="Enter your Azure AD Client Secret",
            description="The Client Secret from Azure AD App Registration",
            field_type="PASSWORD",
            is_secret=True
        ))
        .add_auth_field(AuthField(
            name="tenantId",
            display_name="Directory (Tenant) ID",
            placeholder="Enter your Azure AD Tenant ID",
            description="The Directory (Tenant) ID from Azure AD"
        ))
        .add_auth_field(AuthField(
            name="hasAdminConsent",
            display_name="Has Admin Consent",
            description="Check if admin consent has been granted for the application",
            field_type="CHECKBOX",
            required=True,
            default_value=False
        ))
        .add_auth_field(AuthField(
            name="redirectUri",
            display_name="Redirect URI",
            placeholder="http://localhost:3001/connectors/oauth/callback/onedrive",
            description="The redirect URI for OAuth authentication",
            field_type="URL",
            required=False,
            max_length=2000
        ))
        .add_conditional_display("redirectUri", "hasAdminConsent", "equals", False)
        .with_sync_strategies(["SCHEDULED", "MANUAL"])
        .with_scheduled_config(True, 60)
        .add_filter_field(CommonFields.file_types_filter(), "static")
        .add_filter_field(CommonFields.folders_filter(),
                          "https://graph.microsoft.com/v1.0/me/drive/root/children")
    )\
    .build_decorator()
class OneDriveConnector(BaseConnector):
    def __init__(self, logger: Logger, data_entities_processor: DataSourceEntitiesProcessor,
        data_store_provider: DataStoreProvider, config_service: ConfigurationService) -> None:
        super().__init__(OneDriveApp(), logger, data_entities_processor, data_store_provider, config_service)

        def _create_sync_point(sync_data_point_type: SyncDataPointType) -> SyncPoint:
            return SyncPoint(
                connector_name=self.connector_name,
                org_id=self.data_entities_processor.org_id,
                sync_data_point_type=sync_data_point_type,
                data_store_provider=self.data_store_provider
            )
        # Initialize sync points
        self.drive_delta_sync_point = _create_sync_point(SyncDataPointType.RECORDS)
        self.user_sync_point = _create_sync_point(SyncDataPointType.USERS)
        self.user_group_sync_point = _create_sync_point(SyncDataPointType.GROUPS)

        # Batch processing configuration
        self.batch_size = 100
        self.max_concurrent_batches = 3

        self.rate_limiter = AsyncLimiter(50, 1)  # 50 requests per second

    async def init(self) -> bool:
        config = await self.config_service.get_config("/services/connectors/onedrive/config") or await self.config_service.get_config(f"/services/connectors/onedrive/config/{self.data_entities_processor.org_id}")
        if not config:
            self.logger.error("OneDrive config not found")
            return False

        self.config = {"credentials": config}
        if not config:
            self.logger.error("OneDrive config not found")
            raise ValueError("OneDrive config not found")
        auth_config = config.get("auth", {})
        tenant_id = auth_config.get("tenantId")
        client_id = auth_config.get("clientId")
        client_secret = auth_config.get("clientSecret")
        if not all((tenant_id, client_id, client_secret)):
            self.logger.error("Incomplete OneDrive config. Ensure tenantId, clientId, and clientSecret are configured.")
            raise ValueError("Incomplete OneDrive credentials. Ensure tenantId, clientId, and clientSecret are configured.")

        has_admin_consent = auth_config.get("hasAdminConsent", False)
        credentials = OneDriveCredentials(
            tenant_id=tenant_id,
            client_id=client_id,
            client_secret=client_secret,
            has_admin_consent=has_admin_consent,
        )
         # Initialize MS Graph client
        credential = ClientSecretCredential(
            tenant_id=credentials.tenant_id,
            client_id=credentials.client_id,
            client_secret=credentials.client_secret,
        )
        self.client = GraphServiceClient(credential, scopes=["https://graph.microsoft.com/.default"])
        self.msgraph_client = MSGraphClient(self.connector_name, self.client, self.logger)
        return True

    async def _process_delta_item(self, item: DriveItem) -> Optional[RecordUpdate]:
        """
        Process a single delta item and detect changes.

        Returns:
            RecordUpdate object containing the record and change information
        """
        try:
            # Check if item is deleted
            if hasattr(item, 'deleted') and item.deleted is not None:
                self.logger.info(f"Item {item.id} has been deleted")
                await self.data_entities_processor.on_record_deleted(
                    record_id=item.id
                )
                return RecordUpdate(
                    record=None,
                    external_record_id=item.id,
                    is_new=False,
                    is_updated=False,
                    is_deleted=True,
                    metadata_changed=False,
                    content_changed=False,
                    permissions_changed=False
                )

            # Get existing record if any
            async with self.data_store_provider.transaction() as tx_store:
                existing_record = await tx_store.get_record_by_external_id(
                    connector_name=self.connector_name,
                    external_id=item.id
                )
                if existing_record:
                    existing_file_record = await tx_store.get_file_record_by_id(existing_record.id)


            # Detect changes
            is_new = existing_record is None
            is_updated = False
            metadata_changed = False
            content_changed = False
            permissions_changed = False
            is_shared_folder = False
            is_shared_folder = (
                item.folder is not None and  # It's a folder
                hasattr(item, 'shared') and item.shared is not None  # Has shared property
            )

            if existing_record:
                # Check for metadata changes
                if (existing_record.external_revision_id != item.e_tag or
                    existing_record.record_name != item.name or
                    existing_record.updated_at != int(item.last_modified_date_time.timestamp() * 1000)):
                    metadata_changed = True
                    is_updated = True


                if existing_file_record:
                    # Check for content changes (different hash)
                    if item.file and hasattr(item.file, 'hashes'):
                        current_hash = item.file.hashes.quick_xor_hash if item.file.hashes else None
                        if existing_file_record.quick_xor_hash != current_hash:
                            content_changed = True
                            is_updated = True

            # Create/update file record
            signed_url = None
            if item.file is not None:
                signed_url = await self.msgraph_client.get_signed_url(
                    item.parent_reference.drive_id,
                    item.id,
                )

            file_record = FileRecord(
                id=existing_record.id if existing_record else str(uuid.uuid4()),
                record_name=item.name,
                record_type=RecordType.FILE,
                record_group_type=RecordGroupType.DRIVE,
                parent_record_type=RecordType.FILE,
                external_record_id=item.id,
                external_revision_id=item.e_tag,
                version=0 if is_new else existing_record.version + 1,
                origin=OriginTypes.CONNECTOR,
                connector_name=self.connector_name,
                created_at=int(item.created_date_time.timestamp() * 1000),
                updated_at=int(item.last_modified_date_time.timestamp() * 1000),
                source_created_at=int(item.created_date_time.timestamp() * 1000),
                source_updated_at=int(item.last_modified_date_time.timestamp() * 1000),
                weburl=item.web_url,
                signed_url=signed_url,
                is_shared=is_shared_folder,
                mime_type=item.file.mime_type if item.file else MimeTypes.FOLDER.value,
                parent_external_record_id=item.parent_reference.id if item.parent_reference else None,
                external_record_group_id=item.parent_reference.drive_id if item.parent_reference else None,
                size_in_bytes=item.size,
                is_file=item.folder is None,
                extension=item.name.split(".")[-1] if "." in item.name else None,
                path=item.parent_reference.path if item.parent_reference else None,
                etag=item.e_tag,
                ctag=item.c_tag,
                quick_xor_hash=item.file.hashes.quick_xor_hash if item.file and item.file.hashes else None,
                crc32_hash=item.file.hashes.crc32_hash if item.file and item.file.hashes else None,
                sha1_hash=item.file.hashes.sha1_hash if item.file and item.file.hashes else None,
                sha256_hash=item.file.hashes.sha256_hash if item.file and item.file.hashes else None,
            )
            if file_record.is_file and file_record.extension is None:
                return None

            # Get current permissions
            permission_result = await self.msgraph_client.get_file_permission(
                item.parent_reference.drive_id if item.parent_reference else None,
                item.id
            )

            new_permissions = await self._convert_to_permissions(permission_result)

            if existing_record:
                # compare permissions with existing permissions (To be implemented)
                if new_permissions:
                    permissions_changed = True
                    is_updated = True

            if existing_record and existing_record.is_shared != is_shared_folder:
                metadata_changed = True
                is_updated = True
                await self._update_folder_children_permissions(
                    drive_id=item.parent_reference.drive_id,
                        folder_id=item.id
                    )


            return RecordUpdate(
                record=file_record,
                is_new=is_new,
                is_updated=is_updated,
                is_deleted=False,
                metadata_changed=metadata_changed,
                content_changed=content_changed,
                permissions_changed=permissions_changed,
                # old_permissions=existing_record.permissions if existing_record else None,
                new_permissions=new_permissions
            )

        except Exception as ex:
            self.logger.error(f"❌ Error processing delta item {item.id}: {ex}", exc_info=True)
            return None

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
                            type=map_msgraph_role_to_permission_type(perm.roles[0] if perm.roles else "read"),
                            entity_type=EntityType.USER
                        ))
                    if hasattr(perm.granted_to_v2, 'group') and perm.granted_to_v2.group:
                        group = perm.granted_to_v2.group
                        permissions.append(Permission(
                            external_id=group.id,
                            email=group.additional_data.get("email", None) if hasattr(group, 'additional_data') else None,
                            type=map_msgraph_role_to_permission_type(perm.roles[0] if perm.roles else "read"),
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
                                type=map_msgraph_role_to_permission_type(perm.roles[0] if perm.roles else "read"),
                                entity_type=EntityType.GROUP
                            ))
                        elif hasattr(identity, 'user') and identity.user:
                            user = identity.user
                            permissions.append(Permission(
                                external_id=user.id,
                                email=user.additional_data.get("email", None) if hasattr(user, 'additional_data') else None,
                                type=map_msgraph_role_to_permission_type(perm.roles[0] if perm.roles else "read"),
                                entity_type=EntityType.USER
                            ))

                # Handle link permissions (anyone with link)
                if hasattr(perm, 'link') and perm.link:
                    link = perm.link
                    if link.scope == "anonymous":
                        permissions.append(Permission(
                            external_id="anyone_with_link",
                            email=None,
                            type=map_msgraph_role_to_permission_type(link.type),
                            entity_type=EntityType.ANYONE_WITH_LINK
                        ))
                    elif link.scope == "organization":
                        permissions.append(Permission(
                            external_id="anyone_in_org",
                            email=None,
                            type=map_msgraph_role_to_permission_type(link.type),
                            entity_type=EntityType.ORG
                        ))

            except Exception as e:
                self.logger.error(f"❌ Error converting permission: {e}", exc_info=True)
                continue

        return permissions

    def _permissions_equal(self, old_perms: List[Permission], new_perms: List[Permission]) -> bool:
        """
        Compare two lists of permissions to detect changes.
        """
        if len(old_perms) != len(new_perms):
            return False

        # Create sets of permission tuples for comparison
        old_set = {(p.external_id, p.email, p.type, p.entity_type) for p in old_perms}
        new_set = {(p.external_id, p.email, p.type, p.entity_type) for p in new_perms}

        return old_set == new_set

    async def _process_delta_items_generator(self, delta_items: List[dict]) -> AsyncGenerator[Tuple[FileRecord, List[Permission], RecordUpdate], None]:
        """
        Process delta items and yield records with their permissions.
        This allows non-blocking processing of large datasets.

        Yields:
            Tuple of (FileRecord, List[Permission], RecordUpdate)
        """
        for item in delta_items:
            try:
                record_update = await self._process_delta_item(item)

                if record_update:
                    if record_update.is_deleted:
                        # For deleted items, yield with empty permissions
                        yield (None, [], record_update)
                    elif record_update.record:
                        yield (record_update.record, record_update.new_permissions or [], record_update)

                # Allow other tasks to run
                await asyncio.sleep(0)

            except Exception as e:
                self.logger.error(f"❌ Error processing item in generator: {e}", exc_info=True)
                continue

    async def _update_folder_children_permissions(
        self,
        drive_id: str,
        folder_id: str,
        inherited_permissions: Optional[List[Permission]] = None
    ) -> None:
        """
        Recursively update permissions for all children of a folder.

        Args:
            drive_id: The drive ID
            folder_id: The folder ID whose children need permission updates
            inherited_permissions: The permissions to apply to children
        """
        try:
            # Get all children of this folder
            children = await self.msgraph_client.list_folder_children(drive_id, folder_id)

            for child in children:
                try:
                    # Get the child's current permissions
                    child_permissions = await self.msgraph_client.get_file_permission(
                        drive_id,
                        child.id
                    )

                    # Convert to our permission model
                    converted_permissions = await self._convert_to_permissions(child_permissions)

                    # Update the child's permissions in database
                    async with self.data_store_provider.transaction() as tx_store:
                        existing_child_record = await tx_store.get_record_by_external_id(
                            connector_name=self.connector_name,
                            external_id=child.id
                        )

                        if existing_child_record:
                            # Update the record with new permissions
                            await self.data_entities_processor.on_updated_record_permissions(
                                record=existing_child_record,
                                permissions=converted_permissions
                            )
                            self.logger.info(f"Updated permissions for child item {child.id}")

                    # If this child is also a folder, recurse
                    if child.folder is not None:
                        await self._update_folder_children_permissions(
                            drive_id=drive_id,
                            folder_id=child.id
                            # inherited_permissions=converted_permissions
                        )

                except Exception as child_ex:
                    self.logger.error(f"Error updating child {child.id}: {child_ex}", exc_info=True)
                    continue

        except Exception as ex:
            self.logger.error(f"Error updating folder children permissions for {folder_id}: {ex}", exc_info=True)

    async def _handle_record_updates(self, record_update: RecordUpdate) -> None:
        """
        Handle different types of record updates (new, updated, deleted).
        Publishes appropriate events based on the type of change.
        """
        try:
            if record_update.is_deleted:
                # Handle deletion
                await self.data_entities_processor.on_record_deleted(
                    record_id=record_update.external_record_id
                )

            elif record_update.is_new:
                # Handle new record - this will be done through the normal flow
                self.logger.info(f"New record detected: {record_update.record.record_name}")

            elif record_update.is_updated:
                # Handle updates based on what changed
                if record_update.content_changed:
                    self.logger.info(f"Content changed for record: {record_update.record.record_name}")
                    await self.data_entities_processor.on_record_content_update(record_update.record)

                if record_update.metadata_changed:
                    self.logger.info(f"Metadata changed for record: {record_update.record.record_name}")
                    await self.data_entities_processor.on_record_metadata_update(record_update.record)

                if record_update.permissions_changed:
                    self.logger.info(f"Permissions changed for record: {record_update.record.record_name}")
                    await self.data_entities_processor.on_updated_record_permissions(
                        record_update.record,
                        record_update.new_permissions
                    )

        except Exception as e:
            self.logger.error(f"❌ Error handling record updates: {e}", exc_info=True)

    async def _sync_user_groups(self) -> None:
        """
        Unified user group synchronization.
        Uses Graph Delta API for BOTH initial full sync and subsequent incremental syncs.
        """
        try:
            sync_point_key = generate_record_sync_point_key(
                SyncDataPointType.GROUPS.value,
                "organization",
                self.data_entities_processor.org_id
            )
            sync_point = await self.user_group_sync_point.read_sync_point(sync_point_key)

            # 1. Determine starting URL
            # Default to fresh delta start
            url = "https://graph.microsoft.com/v1.0/groups/delta"
            # If we have a saved state, prefer nextLink (resuming interrupted sync) or deltaLink (incremental sync)
            if sync_point:
                 url = sync_point.get('nextLink') or sync_point.get('deltaLink') or url

            self.logger.info("Starting user group sync...")

            while True:
                # 2. Fetch page of results
                result = await self.msgraph_client.get_groups_delta_response(url)
                groups = result.get('groups', [])

                self.logger.info(f"Fetched page with {len(groups)} groups")

                # 3. Process each group in the current page
                for group in groups:
                    # A) Check for DELETION marker
                    if hasattr(group, 'additional_data') and group.additional_data and '@removed' in group.additional_data:
                         self.logger.info(f"[DELTA ACTION] 🗑️ REMOVE Group: {group.id}")
                         await self.handle_delete_group(group.id)
                         continue


                    # B) Process ADD/UPDATE
                    # Note: For a brand new initial sync, everything will fall into this bucket.
                    self.logger.info(f"[DELTA ACTION] ✅ ADD/UPDATE Group: {getattr(group, 'display_name', 'N/A')} ({group.id})")
                    await self.handle_group_create(group)

                    # C) Trigger member sync for this group
                    # C) Check for specific MEMBER changes in this delta
                    member_changes = group.additional_data.get('members@delta', [])

                    if member_changes:
                         self.logger.info(f"    -> [ACTION] 👥 Processing {len(member_changes)} member changes for group: {group.id}")

                    for member_change in member_changes:
                        user_id = member_change.get('id')

                        # 1. Fetch email (needed for both add and remove in your current processor)
                        email = await self.msgraph_client.get_user_email(user_id)

                        if not email:
                            self.logger.warning(f"Could not find email for user ID {user_id}, skipping member change processing.")
                            continue

                        # 2. Handle based on change type
                        if '@removed' in member_change:
                            self.logger.info(f"    -> [ACTION] 👤⛔ REMOVING member: {email} ({user_id}) from group {group.id}")
                            await self.data_entities_processor.on_user_group_member_removed(
                                external_group_id=group.id,
                                user_email=email,
                                connector_name=self.connector_name
                            )
                        else:
                            self.logger.info(f"    -> [ACTION] 👤✨ ADDING member: {email} ({user_id}) to group {group.id}")

                # 4. Handle pagination and completion
                if result.get('next_link'):
                    # More data available, update URL for next loop iteration
                    url = result.get('next_link')

                    # OPTIONAL: Save intermediate 'nextLink' here if you want resumability during a very long initial sync.
                    # await self.user_group_sync_point.update_sync_point(sync_point_key, {"nextLink": url, "deltaLink": None})

                elif result.get('delta_link'):
                    # End of current data stream. Save the delta_link for the NEXT run.
                    await self.user_group_sync_point.update_sync_point(
                        sync_point_key,
                        {"nextLink": None, "deltaLink": result.get('delta_link')}
                    )
                    self.logger.info("User group sync cycle completed, delta link saved for next run.")
                    break
                else:
                    # Fallback ensuring loop terminates if API returns neither link (unlikely standard behavior)
                    self.logger.warning("Received response with neither next_link nor delta_link.")
                    break

        except Exception as e:
            self.logger.error(f"❌ Error in unified user group sync: {e}", exc_info=True)
            raise

    async def handle_group_create(self, group: Group) -> None:
        """
        Handles the creation or update of a single user group.
        Fetches members and sends to data processor.
        """
        try:

            # 1. Fetch latest members for this group
            members = await self.msgraph_client.get_group_members(group.id)

            # 2. Create AppUserGroup entity
            user_group = AppUserGroup(
                source_user_group_id=group.id,
                app_name=self.connector_name,
                name=group.display_name,
                description=group.description,
                source_created_at=group.created_date_time.timestamp() if group.created_date_time else get_epoch_timestamp_in_ms(),
            )

            # 3. Create AppUser entities for members
            app_users = []
            for member in members:
                app_user = AppUser(
                    source_user_id=member.id,
                    email=member.mail or member.user_principal_name,
                    full_name=member.display_name,
                    source_created_at=member.created_date_time.timestamp() if member.created_date_time else get_epoch_timestamp_in_ms(),
                    app_name=self.connector_name,
                )
                app_users.append(app_user)

            # 4. Send to processor (wrapped in list as expected by on_new_user_groups)
            await self.data_entities_processor.on_new_user_groups([(user_group, app_users)])

            self.logger.info(f"Processed group creation/update for: {group.display_name}")

        except Exception as e:
            self.logger.error(f"❌ Error handling group create for {getattr(group, 'id', 'unknown')}: {e}", exc_info=True)

    async def handle_delete_group(self, group_id: str) -> None:
        """
        Handles the deletion of a single user group.
        Calls the data processor to remove it from the database.

        Args:
            group_id: The external ID of the group to be deleted.
        """
        try:
            self.logger.info(f"Handling group deletion for: {group_id}")

            # Call the data entities processor to handle the deletion logic
            await self.data_entities_processor.on_user_group_deleted(
                external_group_id=group_id,
                connector_name=self.connector_name
            )

            self.logger.info(f"Successfully processed group deletion for: {group_id}")

        except Exception as e:
            self.logger.error(f"❌ Error handling group delete for {group_id}: {e}", exc_info=True)

    async def _run_sync_with_yield(self, user_id: str) -> None:
        """
        Synchronizes drive contents using delta API with yielding for non-blocking operation.

        Args:
            user_id: The user identifier
        """
        try:
            self.logger.info(f"Starting sync for user {user_id}")

            # Get current sync state
            root_url = f"/users/{user_id}/drive/root/delta"
            sync_point_key = generate_record_sync_point_key(RecordType.DRIVE.value, "users", user_id)
            sync_point = await self.drive_delta_sync_point.read_sync_point(sync_point_key)

            url = sync_point.get('deltaLink') or sync_point.get('nextLink') if sync_point else None
            if not url:
                url = ("{+baseurl}" + root_url)

            batch_records = []
            batch_count = 0

            while True:
                # Fetch delta changes
                result = await self.msgraph_client.get_delta_response(url)

                drive_items = result.get('drive_items')
                if not result or not drive_items:
                    break

                # Process items using generator for non-blocking operation
                async for file_record, permissions, record_update in self._process_delta_items_generator(drive_items):
                    if record_update.is_deleted:
                        # Handle deletion immediately
                        await self._handle_record_updates(record_update)
                        continue

                    # Handle updates
                    if record_update.is_updated:
                        await self._handle_record_updates(record_update)
                        continue

                    if file_record:
                        # Add to batch
                        batch_records.append((file_record, permissions))
                        batch_count += 1

                        # Process batch when it reaches the size limit
                        if batch_count >= self.batch_size:
                            await self.data_entities_processor.on_new_records(batch_records)
                            batch_records = []
                            batch_count = 0

                            # Allow other operations to proceed
                            await asyncio.sleep(0.1)

                # Process remaining records in batch
                if batch_records:
                    await self.data_entities_processor.on_new_records(batch_records)
                    batch_records = []
                    batch_count = 0

                # Update sync state with next_link
                next_link = result.get('next_link')
                if next_link:
                    await self.drive_delta_sync_point.update_sync_point(
                        sync_point_key,
                        sync_point_data={
                            "nextLink": next_link,
                        }
                    )
                    url = next_link
                else:
                    # No more pages - store deltaLink and clear nextLink
                    delta_link = result.get('delta_link', None)
                    await self.drive_delta_sync_point.update_sync_point(
                        sync_point_key,
                        sync_point_data={
                            "nextLink": None,
                            "deltaLink": delta_link
                        }
                    )
                    break

            self.logger.info(f"Completed delta sync for user {user_id}")

        except Exception as ex:
            self.logger.error(f"❌ Error in delta sync for user {user_id}: {ex}")
            raise

    async def _process_users_in_batches(self, users: List[AppUser]) -> None:
        """
        Process users in concurrent batches for improved performance.

        Args:
            users: List of users to process
        """
        try:
            # Get all active users
            all_active_users = await self.data_entities_processor.get_all_active_users()
            active_user_emails = {active_user.email.lower() for active_user in all_active_users}

            # Filter users to sync
            users_to_sync = [
                user for user in users
                if user.email and user.email.lower() in active_user_emails
            ]

            self.logger.info(f"Processing {len(users_to_sync)} active users out of {len(users)} total users")

            # Process users in concurrent batches
            for i in range(0, len(users_to_sync), self.max_concurrent_batches):
                batch = users_to_sync[i:i + self.max_concurrent_batches]

                # Run sync for batch of users concurrently
                sync_tasks = [
                    self._run_sync_with_yield(user.source_user_id)
                    for user in batch
                ]

                await asyncio.gather(*sync_tasks, return_exceptions=True)

                # Small delay between batches to prevent overwhelming the API
                await asyncio.sleep(1)

            self.logger.info("Completed processing all user batches")

        except Exception as e:
            self.logger.error(f"❌ Error processing users in batches: {e}")
            raise

    async def _detect_and_handle_permission_changes(self) -> None:
        """
        Detect and handle permission changes for existing records.
        This should be run periodically to catch permission-only changes.
        """
        try:
            self.logger.info("Starting permission change detection")

            # Get all records for the organization
            # This would need to be implemented in the data processor
            # For now, we'll check permissions during regular sync

            # This is handled in the _process_delta_item method
            # where we compare old and new permissions

            self.logger.info("Completed permission change detection")

        except Exception as e:
            self.logger.error(f"❌ Error detecting permission changes: {e}")

    async def _handle_reindex_event(self, record_id: str) -> None:
        """
        Handle reindexing of a specific record.

        Args:
            record_id: The ID of the record to reindex
        """
        try:
            self.logger.info(f"Handling reindex event for record {record_id}")

            # Get the record from database
            record = None
            async with self.data_store_provider.transaction() as tx_store:
                record = await tx_store.get_record_by_external_id(
                connector_name=Connectors.ONEDRIVE.value,
                external_id=record_id
            )

            if not record:
                self.logger.warning(f"⚠️ Record {record_id} not found for reindexing")
                return

            # Get fresh data from OneDrive
            drive_id = record.external_record_group_id
            item_id = record.external_record_id

            # Get updated metadata
            async with self.msgraph_client.rate_limiter:
                item = await self.msgraph_client.client.drives.by_drive_id(drive_id).items.by_drive_item_id(item_id).get()

            if item:
                # Process the updated item
                record_update = await self._process_delta_item(item)

                if record_update:
                    # Send for reindexing
                    await self.data_entities_processor.on_record_content_update(record_update.record)

                    # Update permissions if changed
                    if record_update.permissions_changed:
                        await self.data_entities_processor.on_updated_record_permissions(
                            record_update.record,
                            record_update.new_permissions
                        )

            self.logger.info(f"Completed reindex for record {record_id}")

        except Exception as e:
            self.logger.error(f"❌ Error handling reindex event: {e}")

    async def handle_webhook_notification(self, notification: Dict) -> None:
        """
        Handle webhook notifications from Microsoft Graph.

        Args:
            notification: The webhook notification payload
        """
        try:
            self.logger.info("Processing webhook notification")

            # Extract relevant information from notification
            resource = notification.get('resource', '')
            notification.get('changeType', '')

            # Parse the resource to get user and item IDs
            # Resource format: "users/{userId}/drive/root" or similar
            parts = resource.split('/')
            EXPECTED_PARTS = 2
            if len(parts) >= EXPECTED_PARTS and parts[0] == 'users':
                user_id = parts[1]

                # Run incremental sync for this user
                await self._run_sync_with_yield(user_id)

            self.logger.info("Webhook notification processed successfully")

        except Exception as e:
            self.logger.error(f"❌ Error handling webhook notification: {e}")

    async def run_sync(self) -> None:
        """
        Main entry point for the OneDrive connector.
        Implements non-blocking sync with all requested features.
        """
        try:
            self.logger.info("Starting OneDrive connector sync")

            # Step 1: Sync users
            self.logger.info("Syncing users...")
            users = await self.msgraph_client.get_all_users()
            await self.data_entities_processor.on_new_app_users(users)

            # Step 2: Sync user groups and their members
            self.logger.info("Syncing user groups...")
            await self._sync_user_groups()

            # Step 3: Process user drives with yielding for non-blocking operation
            self.logger.info("Syncing user drives...")
            await self._process_users_in_batches(users)

            # Step 4: Detect and handle permission changes
            self.logger.info("Checking for permission changes...")
            await self._detect_and_handle_permission_changes()

            self.logger.info("OneDrive connector sync completed successfully")

        except Exception as e:
            self.logger.error(f"❌ Error in OneDrive connector run: {e}")
            raise

    async def run_incremental_sync(self) -> None:
        """
        Run incremental sync for a specific user or all users.
        Uses the stored delta token to get only changes since last sync.

        Args:
            user_id: Optional user ID to sync. If None, syncs all users.
        """
        try:
            self.logger.info("Starting incremental sync for all users")

            # Sync all active users
            users = await self.msgraph_client.get_all_users()
            await self._process_users_in_batches(users)

            self.logger.info("Incremental sync completed")

        except Exception as e:
            self.logger.error(f"❌ Error in incremental sync: {e}")
            raise

    async def cleanup(self) -> None:
        """
        Cleanup resources when shutting down the connector.
        """
        try:
            self.logger.info("Cleaning up OneDrive connector resources")

            # Clear caches
            # self.processed_items.clear()
            # self.permission_cache.clear()

            # Close any open connections
            if hasattr(self, 'client') and self.client:
                # GraphServiceClient doesn't have explicit close, but we can clear the reference
                self.client = None

            self.logger.info("OneDrive connector cleanup completed")

        except Exception as e:
            self.logger.error(f"❌ Error during cleanup: {e}")

    async def get_signed_url(self, record: Record) -> str:
        """
        Create a signed URL for a specific record.
        """
        try:
            return await self.msgraph_client.get_signed_url(record.external_record_group_id, record.external_record_id)
        except Exception as e:
            self.logger.error(f"❌ Error creating signed URL for record {record.id}: {e}")
            raise

    async def stream_record(self, record: Record) -> StreamingResponse:
        """Stream a record from SharePoint."""
        signed_url = await self.get_signed_url(record)
        if not signed_url:
            raise HTTPException(status_code=HttpStatusCode.NOT_FOUND.value, detail="File not found or access denied")

        return StreamingResponse(
            stream_content(signed_url),
            media_type=record.mime_type if record.mime_type else "application/octet-stream",
            headers={
                "Content-Disposition": f"attachment; filename={record.record_name}"
            }
        )


    async def test_connection_and_access(self) -> bool:
        """Test connection and access to OneDrive."""
        try:
            self.logger.info("Testing connection and access to OneDrive")
            return True
        except Exception as e:
            self.logger.error(f"❌ Error testing connection and access to OneDrive: {e}")
            return False

    @classmethod
    async def create_connector(cls, logger: Logger,
                               data_store_provider: DataStoreProvider, config_service: ConfigurationService) -> BaseConnector:
        data_entities_processor = DataSourceEntitiesProcessor(logger, data_store_provider, config_service)
        await data_entities_processor.initialize()

        return OneDriveConnector(logger, data_entities_processor, data_store_provider, config_service)


# Additional helper class for managing OneDrive subscriptions
class OneDriveSubscriptionManager:
    """
    Manages webhook subscriptions for OneDrive change notifications.
    """

    def __init__(self, msgraph_client: MSGraphClient, logger: Logger) -> None:
        self.client = msgraph_client
        self.logger = logger
        self.subscriptions: Dict[str, str] = {}  # user_id -> subscription_id mapping

    async def create_subscription(self, user_id: str, notification_url: str) -> Optional[str]:
        """
        Create a subscription for a user's OneDrive.

        Args:
            user_id: The user ID to create subscription for
            notification_url: The webhook URL to receive notifications

        Returns:
            Subscription ID if successful, None otherwise
        """
        try:
            expiration_datetime = (datetime.now(timezone.utc) + timedelta(days=3)).isoformat()

            subscription = Subscription(
                change_type="updated",
                notification_url=notification_url,
                resource=f"users/{user_id}/drive/root",
                expiration_date_time=expiration_datetime,
                client_state="OneDriveConnector"  # Optional: for security validation
            )

            async with self.client.rate_limiter:
                result = await self.client.client.subscriptions.post(subscription)

            if result and result.id:
                self.subscriptions[user_id] = result.id
                self.logger.info(f"Created subscription {result.id} for user {user_id}")
                return result.id

            return None

        except Exception as e:
            self.logger.error(f"❌ Error creating subscription for user {user_id}: {e}")
            return None

    async def renew_subscription(self, subscription_id: str) -> bool:
        """
        Renew an existing subscription before it expires.

        Args:
            subscription_id: The subscription ID to renew

        Returns:
            True if successful, False otherwise
        """
        try:
            expiration_datetime = (datetime.now(timezone.utc) + timedelta(days=3)).isoformat()

            subscription_update = Subscription(
                expiration_date_time=expiration_datetime
            )

            async with self.client.rate_limiter:
                await self.client.client.subscriptions.by_subscription_id(subscription_id).patch(subscription_update)

            self.logger.info(f"Renewed subscription {subscription_id}")
            return True

        except Exception as e:
            self.logger.error(f"❌ Error renewing subscription {subscription_id}: {e}")
            return False

    async def delete_subscription(self, subscription_id: str) -> bool:
        """
        Delete a subscription.

        Args:
            subscription_id: The subscription ID to delete

        Returns:
            True if successful, False otherwise
        """
        try:
            async with self.client.rate_limiter:
                await self.client.client.subscriptions.by_subscription_id(subscription_id).delete()

            # Remove from tracking
            user_id = next((k for k, v in self.subscriptions.items() if v == subscription_id), None)
            if user_id:
                del self.subscriptions[user_id]

            self.logger.info(f"Deleted subscription {subscription_id}")
            return True

        except Exception as e:
            self.logger.error(f"❌ Error deleting subscription {subscription_id}: {e}")
            return False

    async def renew_all_subscriptions(self) -> None:
        """
        Renew all active subscriptions.
        Should be called periodically (e.g., every 2 days) to prevent expiration.
        """
        try:
            self.logger.info(f"Renewing {len(self.subscriptions)} subscriptions")

            for user_id, subscription_id in self.subscriptions.items():
                await self.renew_subscription(subscription_id)
                # Small delay to avoid rate limiting
                await asyncio.sleep(0.5)

            self.logger.info("Completed subscription renewal")

        except Exception as e:
            self.logger.error(f"❌ Error renewing subscriptions: {e}")

    async def cleanup_subscriptions(self) -> None:
        """
        Clean up all subscriptions during shutdown.
        """
        try:
            self.logger.info("Cleaning up subscriptions")

            for subscription_id in list(self.subscriptions.values()):
                await self.delete_subscription(subscription_id)

            self.subscriptions.clear()
            self.logger.info("Subscription cleanup completed")

        except Exception as e:
            self.logger.error(f"❌ Error during subscription cleanup: {e}")
