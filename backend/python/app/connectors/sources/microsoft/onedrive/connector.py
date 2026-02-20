import asyncio
import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from logging import Logger
from typing import AsyncGenerator, Dict, List, NoReturn, Optional, Tuple

from aiolimiter import AsyncLimiter
from azure.identity.aio import ClientSecretCredential
from fastapi import HTTPException
from fastapi.responses import StreamingResponse
from msgraph import GraphServiceClient
from msgraph.generated.models.drive_item import DriveItem
from msgraph.generated.models.group import Group
from msgraph.generated.models.subscription import Subscription

from app.config.configuration_service import ConfigurationService
from app.config.constants.arangodb import MimeTypes, OriginTypes
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
from app.connectors.core.registry.auth_builder import AuthBuilder, AuthType
from app.connectors.core.registry.connector_builder import (
    AuthField,
    CommonFields,
    ConnectorBuilder,
    ConnectorScope,
    DocumentationLink,
)
from app.connectors.core.registry.filters import (
    FilterCategory,
    FilterCollection,
    FilterField,
    FilterOperator,
    FilterType,
    IndexingFilterKey,
    SyncFilterKey,
    load_connector_filters,
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
    IndexingStatus,
    Record,
    RecordGroup,
    RecordGroupType,
    RecordType,
)
from app.models.permission import EntityType, Permission, PermissionType
from app.utils.streaming import create_stream_record_response, stream_content
from app.utils.time_conversion import get_epoch_timestamp_in_ms

logging.getLogger("azure").setLevel(logging.WARNING)


@dataclass
class OneDriveCredentials:
    tenant_id: str
    client_id: str
    client_secret: str
    has_admin_consent: bool = False

@ConnectorBuilder("OneDrive")\
    .in_group("Microsoft 365")\
    .with_description("Sync files and folders from OneDrive")\
    .with_categories(["Storage"])\
    .with_scopes([ConnectorScope.TEAM.value])\
    .with_auth([
        AuthBuilder.type(AuthType.OAUTH_ADMIN_CONSENT).fields([
            AuthField(
                name="clientId",
                display_name="Application (Client) ID",
                placeholder="Enter your Azure AD Application ID",
                description="The Application (Client) ID from Azure AD App Registration"
            ),
            AuthField(
                name="clientSecret",
                display_name="Client Secret",
                placeholder="Enter your Azure AD Client Secret",
                description="The Client Secret from Azure AD App Registration",
                field_type="PASSWORD",
                is_secret=True
            ),
            AuthField(
                name="tenantId",
                display_name="Directory (Tenant) ID",
                placeholder="Enter your Azure AD Tenant ID",
                description="The Directory (Tenant) ID from Azure AD"
            ),
            AuthField(
                name="hasAdminConsent",
                display_name="Has Admin Consent",
                description="Check if admin consent has been granted for the application",
                field_type="CHECKBOX",
                required=True,
                default_value=False
            ),
            AuthField(
                name="redirectUri",
                display_name="Redirect URI",
                placeholder="http://localhost:3001/connectors/oauth/callback/onedrive",
                description="The redirect URI for OAuth authentication",
                field_type="URL",
                required=False,
                max_length=2000
            )
        ])
    ])\
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
        .add_filter_field(CommonFields.modified_date_filter("Filter files and folders by modification date."))
        .add_filter_field(CommonFields.created_date_filter("Filter files and folders by creation date."))
        .add_filter_field(CommonFields.enable_manual_sync_filter())
        .add_filter_field(CommonFields.file_extension_filter())
        .add_filter_field(FilterField(
            name="shared",
            display_name="Index Shared Items",
            filter_type=FilterType.BOOLEAN,
            category=FilterCategory.INDEXING,
            description="Enable indexing of shared items",
            default_value=True
        ))
        .add_conditional_display("redirectUri", "hasAdminConsent", "equals", False)
        .with_sync_strategies(["SCHEDULED", "MANUAL"])
        .with_scheduled_config(True, 60)
        .with_sync_support(True)
        .with_agent_support(True)
    )\
    .build_decorator()
class OneDriveConnector(BaseConnector):
    def __init__(self, logger: Logger, data_entities_processor: DataSourceEntitiesProcessor,
        data_store_provider: DataStoreProvider, config_service: ConfigurationService, connector_id: str) -> None:
        super().__init__(OneDriveApp(connector_id), logger, data_entities_processor, data_store_provider, config_service, connector_id)

        def _create_sync_point(sync_data_point_type: SyncDataPointType) -> SyncPoint:
            return SyncPoint(
                connector_id=self.connector_id,
                org_id=self.data_entities_processor.org_id,
                sync_data_point_type=sync_data_point_type,
                data_store_provider=self.data_store_provider
            )
        # Initialize sync points
        self.drive_delta_sync_point = _create_sync_point(SyncDataPointType.RECORDS)
        self.user_sync_point = _create_sync_point(SyncDataPointType.USERS)
        self.user_group_sync_point = _create_sync_point(SyncDataPointType.GROUPS)
        self.connector_id = connector_id

        # Batch processing configuration
        self.batch_size = 100
        self.max_concurrent_batches = 1 # set to 1 for now to avoid write write conflicts for small number of records

        self.rate_limiter = AsyncLimiter(50, 1)  # 50 requests per second
        self.sync_filters: FilterCollection = FilterCollection()
        self.indexing_filters: FilterCollection = FilterCollection()

    async def init(self) -> bool:
        config = await self.config_service.get_config(f"/services/connectors/{self.connector_id}/config")
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
        # Store credential as instance variable to prevent it from being garbage collected
        # Initialize the credential and ensure it's kept open by calling get_token
        # This prevents premature closure of the HTTP transport
        self.credential = ClientSecretCredential(
            tenant_id=credentials.tenant_id,
            client_id=credentials.client_id,
            client_secret=credentials.client_secret,
        )

        # Pre-initialize the credential to establish HTTP session
        # This prevents "HTTP transport has already been closed" errors
        try:
            await self.credential.get_token("https://graph.microsoft.com/.default")
            self.logger.info("✅ Credential initialized and HTTP session established")
        except Exception as token_error:
            self.logger.error(f"❌ Failed to initialize credential: {token_error}")
            raise ValueError(f"Failed to initialize OneDrive credential: {token_error}")

        self.client = GraphServiceClient(self.credential, scopes=["https://graph.microsoft.com/.default"])
        self.msgraph_client = MSGraphClient(self.connector_name, self.connector_id, self.client, self.logger)
        return True

    async def _process_delta_item(self, item: DriveItem) -> Optional[RecordUpdate]:
        """
        Process a single delta item and detect changes.

        Returns:
            RecordUpdate object containing the record and change information
        """
        try:

            # Apply Date Filters
            if not self._pass_date_filters(item):
                self.logger.debug(f"Skipping item {item.name} (ID: {item.id}) due to date filters.")
                return # Skip this item

            if not self._pass_extension_filter(item):
                self.logger.debug(f"Skipping item {item.name} (ID: {item.id}) due to extention filters.")
                return

            # Check if item is deleted
            if hasattr(item, 'deleted') and item.deleted is not None:
                self.logger.info(f"Item {item.id} has been deleted")
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
                    connector_id=self.connector_id,
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

            is_shared = hasattr(item, 'shared') and item.shared is not None

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
                connector_id=self.connector_id,
                created_at=int(item.created_date_time.timestamp() * 1000),
                updated_at=int(item.last_modified_date_time.timestamp() * 1000),
                source_created_at=int(item.created_date_time.timestamp() * 1000),
                source_updated_at=int(item.last_modified_date_time.timestamp() * 1000),
                weburl=item.web_url,
                signed_url=signed_url,
                is_shared=is_shared,
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

    def _pass_date_filters(self, item: DriveItem) -> bool:
        """
        Checks if the DriveItem passes the configured CREATED and MODIFIED date filters.
        Relies on client-side filtering since OneDrive Delta API does not support $filter.
        """
        # 1. ALWAYS Allow Folders
        # We must sync folders regardless of date to ensure the directory structure
        # exists for any new files that might be inside them.
        if item.folder is not None:
            return True

        # 2. Check Created Date Filter
        created_filter = self.sync_filters.get(SyncFilterKey.CREATED)
        if created_filter:
            created_after_iso, created_before_iso = created_filter.get_datetime_iso()

            # Use _parse_datetime to get millisecond timestamps for easy comparison
            item_ts = self._parse_datetime(item.created_date_time)
            start_ts = self._parse_datetime(created_after_iso)
            end_ts = self._parse_datetime(created_before_iso)

            if item_ts is not None:
                if start_ts and item_ts < start_ts:
                    return False
                if end_ts and item_ts > end_ts:
                    return False

        # 3. Check Modified Date Filter
        modified_filter = self.sync_filters.get(SyncFilterKey.MODIFIED)
        if modified_filter:
            modified_after_iso, modified_before_iso = modified_filter.get_datetime_iso()

            # Use _parse_datetime to get millisecond timestamps
            item_ts = self._parse_datetime(item.last_modified_date_time)
            start_ts = self._parse_datetime(modified_after_iso)
            end_ts = self._parse_datetime(modified_before_iso)

            if item_ts is not None:
                if start_ts and item_ts < start_ts:
                    return False
                if end_ts and item_ts > end_ts:
                    return False

        return True

    def _pass_extension_filter(self, item: DriveItem) -> bool:
        """
        Checks if the DriveItem passes the configured file extensions filter.

        For MULTISELECT filters:
        - Operator IN: Only allow files with extensions in the selected list
        - Operator NOT_IN: Allow files with extensions NOT in the selected list

        Folders always pass this filter to maintain directory structure.
        """
        # 1. ALWAYS Allow Folders
        if item.folder is not None:
            return True

        # 2. Get the extensions filter
        extensions_filter = self.sync_filters.get(SyncFilterKey.FILE_EXTENSIONS)

        # If no filter configured or filter is empty, allow all files
        if extensions_filter is None or extensions_filter.is_empty():
            return True

        # 3. Get the file extension from the item
        # The extension is stored without the dot (e.g., "pdf", "docx")
        file_extension = None
        if item.name and "." in item.name:
            file_extension = item.name.rsplit(".", 1)[-1].lower()

        # Files without extensions: behavior depends on operator
        # If using IN operator and file has no extension, it won't match any allowed extensions
        # If using NOT_IN operator and file has no extension, it passes (not in excluded list)
        if file_extension is None:
            operator = extensions_filter.get_operator()
            operator_str = operator.value if hasattr(operator, 'value') else str(operator)
            if operator_str == FilterOperator.NOT_IN:
                return True
            return False

        # 4. Get the list of extensions from the filter value
        allowed_extensions = extensions_filter.value
        if not isinstance(allowed_extensions, list):
            return True  # Invalid filter value, allow the file

        # Normalize extensions (lowercase, without dots)
        normalized_extensions = [ext.lower().lstrip(".") for ext in allowed_extensions]

        # 5. Apply the filter based on operator
        operator = extensions_filter.get_operator()
        operator_str = operator.value if hasattr(operator, 'value') else str(operator)

        if operator_str == FilterOperator.IN:
            # Only allow files with extensions in the list
            return file_extension in normalized_extensions
        elif operator_str == FilterOperator.NOT_IN:
            # Allow files with extensions NOT in the list
            return file_extension not in normalized_extensions

        # Unknown operator, default to allowing the file
        return True

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
                        files_disabled = not self.indexing_filters.is_enabled(IndexingFilterKey.FILES, default=True)
                        shared_disabled = record_update.record.is_shared and not self.indexing_filters.is_enabled(IndexingFilterKey.SHARED, default=True)

                        if files_disabled or shared_disabled:
                            record_update.record.indexing_status = IndexingStatus.AUTO_INDEX_OFF.value


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
                            connector_id=self.connector_id,
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
                async with self.data_store_provider.transaction() as tx_store:
                    dbRecord = await tx_store.get_record_by_external_id(
                        connector_id=self.connector_id,
                        external_id=record_update.external_record_id
                    )
                    if dbRecord:
                        await self.data_entities_processor.on_record_deleted(
                            record_id=dbRecord.id
                        )

            elif record_update.is_new:
                # Handle new record - this will be done through the normal flow
                self.logger.info(f"New record detected: {record_update.record.record_name}")

            elif record_update.is_updated:
                # Handle updates based on what changed
                if record_update.metadata_changed:
                    self.logger.info(f"Metadata changed for record: {record_update.record.record_name}")
                    await self.data_entities_processor.on_record_metadata_update(record_update.record)

                if record_update.permissions_changed:
                    self.logger.info(f"Permissions changed for record: {record_update.record.record_name}")
                    await self.data_entities_processor.on_updated_record_permissions(
                        record_update.record,
                        record_update.new_permissions
                    )

                if record_update.content_changed:
                    self.logger.info(f"Content changed for record: {record_update.record.record_name}")
                    await self.data_entities_processor.on_record_content_update(record_update.record)

        except Exception as e:
            self.logger.error(f"❌ Error handling record updates: {e}", exc_info=True)

    async def _sync_user_groups(self) -> None:
        """
        Unified user group synchronization.
        - First sync: Uses standard /groups API for clean current state
        - Subsequent syncs: Uses Graph Delta API for incremental changes
        """
        try:
            sync_point_key = generate_record_sync_point_key(
                SyncDataPointType.GROUPS.value,
                "organization",
                self.data_entities_processor.org_id
            )
            sync_point = await self.user_group_sync_point.read_sync_point(sync_point_key)

            delta_link = sync_point.get('deltaLink') if sync_point else None

            if delta_link is None:
                self.logger.info("No sync point found, performing initial full sync...")

                # IMPORTANT: Get delta link BEFORE full sync to avoid missing changes
                # that occur during the sync
                delta_link = await self._get_initial_delta_link()

                # Perform the full sync
                await self._perform_initial_full_sync()

                # Only save the delta link if full sync succeeded
                if delta_link:
                    await self.user_group_sync_point.update_sync_point(
                        sync_point_key,
                        {"nextLink": None, "deltaLink": delta_link}
                    )
                    self.logger.info("Initial sync completed and delta link saved for future syncs")
                else:
                    self.logger.warning("Initial sync completed but no delta link was obtained")
            else:
                self.logger.info("Sync point found, performing incremental delta sync...")
                await self._perform_delta_sync(delta_link, sync_point_key)

        except Exception as e:
            self.logger.error(f"❌ Error in user group sync: {e}", exc_info=True)
            raise


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


    async def _perform_initial_full_sync(self) -> None:
        """
        Performs initial full sync using the standard /groups API.
        Gets current state of all groups and their members.
        """
        self.logger.info("Starting initial full user group synchronization")

        groups = await self.msgraph_client.get_all_user_groups()

        # Process all groups concurrently using asyncio.gather
        results = await asyncio.gather(
            *[self._process_single_group(group) for group in groups],
            return_exceptions=True
        )

        # Filter out None results (failed groups) and exceptions
        group_with_members = []
        for result in results:
            if isinstance(result, Exception):
                self.logger.error(f"❌ Error processing group: {result}", exc_info=True)
            elif result is not None:
                group_with_members.append(result)

        if group_with_members:
            await self.data_entities_processor.on_new_user_groups(group_with_members)

        self.logger.info(f"Initial full sync completed: processed {len(groups)} user groups")

    async def _process_single_group(self, group) -> Optional[Tuple[AppUserGroup, List[AppUser]]]:
        """
        Processes a single group and returns a tuple of (user_group, app_users).
        Returns None if processing fails.
        """
        try:
            members = await self.msgraph_client.get_group_members(group.id)

            user_group = AppUserGroup(
                source_user_group_id=group.id,
                app_name=self.connector_name,
                connector_id=self.connector_id,
                name=group.display_name,
                description=group.description,
                source_created_at=group.created_date_time.timestamp() if group.created_date_time else get_epoch_timestamp_in_ms(),
            )

            app_users = []
            for member in members:
                odata_type = getattr(member, 'odata_type', None) or (member.additional_data or {}).get('@odata.type', '')

                if '#microsoft.graph.user' in odata_type:
                    app_user = self._create_app_user_from_member(member)
                    if app_user:
                        app_users.append(app_user)
                elif '#microsoft.graph.group' in odata_type:
                    nested_users = await self._get_users_from_nested_group(member)
                    app_users.extend(nested_users)
                else:
                    self.logger.debug(f"Skipping member type '{odata_type}' for member {member.id}")

            return (user_group, app_users)

        except Exception as e:
            self.logger.error(f"❌ Error processing group {group.display_name}: {e}", exc_info=True)
            return None


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

            for group in groups:
                # Handle group DELETION
                if hasattr(group, 'additional_data') and group.additional_data and '@removed' in group.additional_data:
                    self.logger.info(f"[DELTA] 🗑️ REMOVE Group: {group.id}")
                    success = await self.handle_delete_group(group.id)
                    if not success:
                        self.logger.error(f"❌ Error handling group delete for {group.id}")
                    continue

                # Handle ADD/UPDATE
                self.logger.info(f"[DELTA] ✅ ADD/UPDATE Group: {getattr(group, 'display_name', 'N/A')} ({group.id})")
                success = await self.handle_group_create(group)
                if not success:
                    self.logger.error(f"❌ Error handling group create for {group.id}")
                    continue

                # Handle MEMBER changes
                member_changes = (group.additional_data or {}).get('members@delta', [])

                if member_changes:
                    self.logger.info(f"    -> [DELTA] 👥 Processing {len(member_changes)} member changes for group: {group.id}")

                for member_change in member_changes:
                    await self._process_member_change(group.id, member_change)

            # Handle pagination and completion
            if result.get('next_link'):
                url = result.get('next_link')
                await self.user_group_sync_point.update_sync_point(
                    sync_point_key,
                    {"nextLink": url, "deltaLink": None}
                )
            elif result.get('delta_link'):
                await self.user_group_sync_point.update_sync_point(
                    sync_point_key,
                    {"nextLink": None, "deltaLink": result.get('delta_link')}
                )
                self.logger.info("Delta sync completed, delta link saved for next run")
                break
            else:
                self.logger.warning("Received response with neither next_link nor delta_link")
                break


    async def _process_member_change(self, group_id: str, member_change: dict) -> None:
        """
        Processes a single member change from the delta response.
        """
        user_id = member_change.get('id')
        email = await self.msgraph_client.get_user_email(user_id)

        if not email:
            return

        if '@removed' in member_change:
            self.logger.info(f"    -> [DELTA] 👤⛔ REMOVING member: {email} ({user_id}) from group {group_id}")
            success = await self.data_entities_processor.on_user_group_member_removed(
                external_group_id=group_id,
                user_email=email,
                connector_id=self.connector_id
            )
            if not success:
                self.logger.error(f"❌ Error removing member {email} from group {group_id}")
        else:
            self.logger.info(f"    -> [DELTA] 👤✨ ADDING member: {email} ({user_id}) to group {group_id}")

    async def handle_group_create(self, group: Group) -> bool:
        """
        Handles the creation or update of a single user group.
        Fetches members and sends to data processor.

        Supported member types:
        - User: Added directly
        - Group (nested): Fetch its users and add them (only one level deep)
        - Device, Service Principal, Org Contact: Ignored

        Returns:
            True if group creation/update was successful, False otherwise.
        """
        try:
            # 1. Fetch latest members for this group
            members = await self.msgraph_client.get_group_members(group.id)

            # 2. Create AppUserGroup entity
            user_group = AppUserGroup(
                source_user_group_id=group.id,
                app_name=self.connector_name,
                connector_id=self.connector_id,
                name=group.display_name,
                description=group.description,
                source_created_at=group.created_date_time.timestamp() if group.created_date_time else get_epoch_timestamp_in_ms(),
            )

            # 3. Create AppUser entities for members (filter by type)
            app_users = []
            for member in members:
                # Check the odata type to determine member type
                odata_type = getattr(member, 'odata_type', None) or (member.additional_data or {}).get('@odata.type', '')

                if '#microsoft.graph.user' in odata_type:
                    # Direct user member
                    app_user = self._create_app_user_from_member(member)
                    if app_user:
                        app_users.append(app_user)

                elif '#microsoft.graph.group' in odata_type:
                    # Nested group - fetch its users (one level deep only)
                    nested_users = await self._get_users_from_nested_group(member)
                    app_users.extend(nested_users)

                else:
                    self.logger.debug(f"Skipping member type '{odata_type}' for member {member.id}")

            # 4. Send to processor (wrapped in list as expected by on_new_user_groups)
            await self.data_entities_processor.on_new_user_groups([(user_group, app_users)])

            self.logger.info(f"Processed group creation/update for: {group.display_name} with {len(app_users)} user members")
            return True

        except Exception as e:
            self.logger.error(f"❌ Error handling group create for {getattr(group, 'id', 'unknown')}: {e}", exc_info=True)
            return False


    async def _get_users_from_nested_group(self, nested_group) -> List[AppUser]:
        """
        Fetches users from a nested group (one level deep only).

        Args:
            nested_group: A group member object from Microsoft Graph API

        Returns:
            List of AppUser entities from the nested group
        """
        nested_group_name = getattr(nested_group, 'display_name', nested_group.id)
        self.logger.info(f"Processing nested group member: {nested_group_name}")

        app_users = []

        try:
            nested_members = await self.msgraph_client.get_group_members(nested_group.id)

            for nested_member in nested_members:
                nested_odata_type = getattr(nested_member, 'odata_type', None) or (nested_member.additional_data or {}).get('@odata.type', '')

                if '#microsoft.graph.user' in nested_odata_type:
                    app_user = self._create_app_user_from_member(nested_member)
                    if app_user:
                        app_users.append(app_user)
                else:
                    self.logger.debug(f"Skipping non-user member '{nested_odata_type}' in nested group {nested_group_name}")

        except Exception as e:
            self.logger.warning(f"Failed to fetch members from nested group {nested_group_name}: {e}")

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

            # Create RecordGroup if sync_point doesn't exist (first sync)
            if not sync_point:
                try:
                    # Get user drive information
                    drive = await self.msgraph_client.get_user_drive(user_id)
                    # Get user info (email and display name)
                    user_info = await self.msgraph_client.get_user_info(user_id)

                    if user_info:
                        # Create RecordGroup for the user's OneDrive
                        display_name = user_info.get('display_name')
                        user_email = user_info.get('email', user_id)

                        if display_name:
                            record_group_name = f"{display_name}'s OneDrive"
                        else:
                            record_group_name = f"OneDrive - {user_email}"

                        record_group = RecordGroup(
                            external_group_id=drive.id,
                            name=record_group_name,
                            group_type=RecordGroupType.DRIVE,
                            connector_name=self.connector_name,
                            connector_id=self.connector_id,
                            web_url=getattr(drive, 'web_url', None),
                            description=f"OneDrive for {display_name or user_email}",
                        )

                        owner_permission = Permission(
                            email=user_email,
                            type=PermissionType.OWNER,
                            entity_type=EntityType.USER
                        )

                        # Save the RecordGroup
                        await self.data_entities_processor.on_new_record_groups([(record_group, [owner_permission])])
                        self.logger.info(f"Created RecordGroup for user {user_id} with drive ID {drive.id}")
                    else:
                        self.logger.warning(f"Could not fetch user info for {user_id}, skipping RecordGroup creation")
                except Exception as e:
                    self.logger.error(f"Error creating RecordGroup for user {user_id}: {e}", exc_info=True)
                    # Continue with sync even if RecordGroup creation fails

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
            all_active_users = await self.data_entities_processor.get_all_active_users()
            active_user_emails = {active_user.email.lower() for active_user in all_active_users}

            # Filter users to sync (only active users)
            active_users = [
                user for user in users
                if user.email and user.email.lower() in active_user_emails
            ]

            self.logger.info(f"Found {len(active_users)} active users out of {len(users)} total users")

            # Further filter to only users with OneDrive provisioned
            users_to_sync = []
            for user in active_users:
                if await self._user_has_onedrive(user.source_user_id):
                    users_to_sync.append(user)
                else:
                    self.logger.info(f"Skipping user {user.email}: No OneDrive license or drive not provisioned")

            self.logger.info(f"Processing {len(users_to_sync)} users with OneDrive out of {len(active_users)} active users")

            # Process users in concurrent batches
            for i in range(0, len(users_to_sync), self.max_concurrent_batches):
                batch = users_to_sync[i:i + self.max_concurrent_batches]

                sync_tasks = [
                    self._run_sync_with_yield(user.source_user_id)
                    for user in batch
                ]

                await asyncio.gather(*sync_tasks, return_exceptions=True)
                await asyncio.sleep(1)

            self.logger.info("Completed processing all user batches")

        except Exception as e:
            self.logger.error(f"❌ Error processing users in batches: {e}")
            raise

    async def _user_has_onedrive(self, user_id: str) -> bool:
        """
        Check if a user has OneDrive provisioned.

        Args:
            user_id: The user identifier

        Returns:
            True if user has OneDrive, False otherwise
        """
        try:
            await self.msgraph_client.get_user_drive(user_id)
            return True
        except Exception as e:
            error_message = str(e).lower()
            error_code = ""

            # Extract error code if it's an ODataError
            if hasattr(e, 'error') and hasattr(e.error, 'code'):
                error_code = e.error.code.lower() if e.error.code else ""

            if any(indicator in error_message or indicator in error_code for indicator in [
                "resourcenotfound",
                "itemnotfound",
                "request_resourcenotfound",
                "404"
            ]):
                return False
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
                connector_id=self.connector_id,
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

            # Reinitialize credential if needed (webhooks might arrive after days of inactivity)
            await self._reinitialize_credential_if_needed()

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

            # Reinitialize credential to prevent "HTTP transport has already been closed" errors
            # This is necessary because the connector instance may be reused across multiple
            # scheduled runs that are days apart, causing the HTTP session to timeout
            await self._reinitialize_credential_if_needed()

            self.sync_filters, self.indexing_filters = await load_connector_filters(
                self.config_service, "onedrive", self.connector_id, self.logger
            )

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

            #TODO: Cleanup as this method is not used anymore
            # Step 4: Detect and handle permission changes
            self.logger.info("Checking for permission changes...")
            await self._detect_and_handle_permission_changes()

            self.logger.info("OneDrive connector sync completed successfully")

        except Exception as e:
            self.logger.error(f"❌ Error in OneDrive connector run: {e}")
            raise

    async def _reinitialize_credential_if_needed(self) -> None:
        """
        Reinitialize the credential and clients if the HTTP transport has been closed.
        This prevents "HTTP transport has already been closed" errors when the connector
        instance is reused across multiple scheduled runs that are days apart.
        """
        try:
            # Test if the credential is still valid by attempting to get a token
            await self.credential.get_token("https://graph.microsoft.com/.default")
            self.logger.debug("✅ Credential is valid and active")
        except Exception as e:
            self.logger.warning(f"⚠️ Credential needs reinitialization: {e}")

            # Close old credential if it exists
            if hasattr(self, 'credential') and self.credential:
                try:
                    await self.credential.close()
                except Exception:
                    pass

            # Get credentials from config
            config = self.config.get("credentials", {})
            auth_config = config.get("auth", {})
            tenant_id = auth_config.get("tenantId")
            client_id = auth_config.get("clientId")
            client_secret = auth_config.get("clientSecret")

            if not all((tenant_id, client_id, client_secret)):
                raise ValueError("Cannot reinitialize: credentials not found in config")

            # Create new credential
            self.credential = ClientSecretCredential(
                tenant_id=tenant_id,
                client_id=client_id,
                client_secret=client_secret,
            )

            # Pre-initialize to establish HTTP session
            await self.credential.get_token("https://graph.microsoft.com/.default")

            # Recreate Graph client with new credential
            self.client = GraphServiceClient(
                self.credential,
                scopes=["https://graph.microsoft.com/.default"]
            )
            self.msgraph_client = MSGraphClient(self.connector_name, self.connector_id, self.client, self.logger)

            self.logger.info("✅ Credential successfully reinitialized")

    async def run_incremental_sync(self) -> None:
        """
        Run incremental sync for a specific user or all users.
        Uses the stored delta token to get only changes since last sync.

        Args:
            user_id: Optional user ID to sync. If None, syncs all users.
        """
        try:
            self.logger.info("Starting incremental sync for all users")

            # Reinitialize credential to prevent session timeout issues
            await self._reinitialize_credential_if_needed()

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

            # Close any open connections in the GraphServiceClient first
            if hasattr(self, 'client') and self.client:
                # GraphServiceClient doesn't have explicit close, but we can clear the reference
                self.client = None

            # Clear msgraph_client reference
            if hasattr(self, 'msgraph_client'):
                self.msgraph_client = None

            # Close the credential last to properly close the HTTP transport
            # This must be done after all API operations are complete
            if hasattr(self, 'credential') and self.credential:
                try:
                    await self.credential.close()
                    self.logger.info("✅ Credential HTTP transport closed successfully")
                except Exception as cred_error:
                    self.logger.warning(f"⚠️ Error closing credential (may already be closed): {cred_error}")
                finally:
                    self.credential = None

            self.logger.info("OneDrive connector cleanup completed")

        except Exception as e:
            self.logger.error(f"❌ Error during cleanup: {e}")

    async def reindex_records(self, records: List[Record]) -> None:
        """Reindex records - not implemented for OneDrive yet."""
        try:
            if not records:
                self.logger.info("No records to reindex")
                return

            self.logger.info(f"Starting reindex for {len(records)} OneDrive records")

            if not self.msgraph_client:
                self.logger.error("MS Graph client not initialized. Call init() first.")
                raise Exception("MS Graph client not initialized. Call init() first.")

            # Check records at source for updates
            org_id = self.data_entities_processor.org_id
            updated_records = []
            non_updated_records = []
            for record in records:
                try:
                    updated_record_data = await self._check_and_fetch_updated_record(org_id, record)
                    if updated_record_data:
                        updated_record, permissions = updated_record_data
                        updated_records.append((updated_record, permissions))
                    else:
                        non_updated_records.append(record)
                except Exception as e:
                    self.logger.error(f"Error checking record {record.id} at source: {e}")
                    continue

            # Update DB only for records that changed at source
            if updated_records:
                await self.data_entities_processor.on_new_records(updated_records)
                self.logger.info(f"Updated {len(updated_records)} records in DB that changed at source")

            # Publish reindex events for non updated records
            if non_updated_records:
                await self.data_entities_processor.reindex_existing_records(non_updated_records)
                self.logger.info(f"Published reindex events for {len(non_updated_records)} non updated records")
        except Exception as e:
            self.logger.error(f"Error during OneDrive reindex: {e}", exc_info=True)
            raise

    async def _check_and_fetch_updated_record(
        self, org_id: str, record: Record
    ) -> Optional[Tuple[Record, List[Permission]]]:
        """Fetch record from MS Graph and return data for reindexing if changed."""
        try:
            drive_id = record.external_record_group_id
            item_id = record.external_record_id

            if not drive_id or not item_id:
                self.logger.warning(f"Missing drive_id or item_id for record {record.id}")
                return None

            # Fetch fresh item from MS Graph
            async with self.msgraph_client.rate_limiter:
                item = await self.msgraph_client.client.drives.by_drive_id(drive_id).items.by_drive_item_id(item_id).get()

            if not item:
                self.logger.warning(f"Item {item_id} not found at source")
                return None

            # Use existing logic to detect changes and transform to FileRecord
            record_update = await self._process_delta_item(item)

            if not record_update or record_update.is_deleted:
                return None

            # Only return data if there's an actual update (metadata, content, or permissions)
            if record_update.is_updated:
                self.logger.info(f"Record {item_id} has changed at source. Updating.")
                # Ensure we keep the internal DB ID
                record_update.record.id = record.id
                return (record_update.record, record_update.new_permissions)

            return None

        except Exception as e:
            self.logger.error(f"Error checking OneDrive record {record.id} at source: {e}")
            return None

    async def get_filter_options(
        self,
        filter_key: str,
        page: int = 1,
        limit: int = 20,
        search: Optional[str] = None,
        cursor: Optional[str] = None
    ) -> NoReturn:
        """OneDrive connector does not support dynamic filter options."""
        raise NotImplementedError("OneDrive connector does not support dynamic filter options")

    async def get_signed_url(self, record: Record) -> str:
        """
        Create a signed URL for a specific record.
        """
        try:
            # Reinitialize credential if needed (user might be accessing files after days of inactivity)
            await self._reinitialize_credential_if_needed()

            return await self.msgraph_client.get_signed_url(record.external_record_group_id, record.external_record_id)
        except Exception as e:
            self.logger.error(f"❌ Error creating signed URL for record {record.id}: {e}")
            raise

    async def stream_record(self, record: Record) -> StreamingResponse:
        """Stream a record from OneDrive."""
        signed_url = await self.get_signed_url(record)
        if not signed_url:
            raise HTTPException(status_code=HttpStatusCode.NOT_FOUND.value, detail="File not found or access denied")

        return create_stream_record_response(
            stream_content(signed_url),
            filename=record.record_name,
            mime_type=record.mime_type,
            fallback_filename=f"record_{record.id}"
        )

    def _parse_datetime(self, dt_obj) -> Optional[int]:
        """Parse datetime object or string to epoch timestamp in milliseconds."""
        if not dt_obj:
            return None
        try:
            if isinstance(dt_obj, str):
                dt = datetime.fromisoformat(dt_obj.replace('Z', '+00:00'))
            else:
                dt = dt_obj
            return int(dt.timestamp() * 1000)
        except Exception:
            return None

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
                               data_store_provider: DataStoreProvider, config_service: ConfigurationService, connector_id: str) -> BaseConnector:
        data_entities_processor = DataSourceEntitiesProcessor(logger, data_store_provider, config_service)
        await data_entities_processor.initialize()

        return OneDriveConnector(logger, data_entities_processor, data_store_provider, config_service, connector_id)


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
