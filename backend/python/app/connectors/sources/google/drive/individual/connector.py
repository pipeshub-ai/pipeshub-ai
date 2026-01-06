import asyncio
import uuid
from logging import Logger
from typing import AsyncGenerator, Dict, List, Optional, Tuple

from fastapi.responses import StreamingResponse

from app.config.configuration_service import ConfigurationService
from app.config.constants.arangodb import (
    Connectors,
    MimeTypes,
    OriginTypes,
    ProgressStatus,
)
from app.connectors.core.base.connector.connector_service import BaseConnector
from app.connectors.core.base.data_processor.data_source_entities_processor import (
    DataSourceEntitiesProcessor,
)
from app.connectors.core.base.data_store.data_store import DataStoreProvider
from app.connectors.core.base.sync_point.sync_point import (
    SyncDataPointType,
    SyncPoint,
)
from app.connectors.core.registry.connector_builder import (
    CommonFields,
    ConnectorBuilder,
    ConnectorScope,
    DocumentationLink,
)
from app.models.entities import AppUser, FileRecord, Record, RecordGroup, RecordGroupType, RecordType
from app.models.permission import Permission, PermissionType, EntityType
from app.connectors.core.registry.filters import (
    FilterCollection,
    FilterField,
    FilterType,
    FilterCategory,
    FilterOptionsResponse,
)
from app.connectors.sources.google.common.apps import GoogleDriveApp
from app.connectors.sources.microsoft.common.msgraph_client import RecordUpdate
from app.sources.client.google.google import GoogleClient
from app.sources.external.google.drive.drive import GoogleDriveDataSource
from app.utils.time_conversion import get_epoch_timestamp_in_ms, parse_timestamp


@ConnectorBuilder("Google Drive")\
    .in_group("Google Workspace")\
    .with_auth_type("OAUTH")\
    .with_description("Sync files and folders from Google Drive")\
    .with_categories(["Storage"])\
    .with_scopes([ConnectorScope.PERSONAL.value])\
    .configure(lambda builder: builder
        .with_icon("/assets/icons/connectors/google-drive.svg")
        .add_documentation_link(DocumentationLink(
            "Google Drive API Setup",
            "https://developers.google.com/workspace/guides/auth-overview",
            "setup"
        ))
        .add_documentation_link(DocumentationLink(
            'Pipeshub Documentation',
            'https://docs.pipeshub.com/connectors/google-workspace/drive',
            'pipeshub'
        ))
        .with_redirect_uri("connectors/oauth/callback/GoogleDrive", True)
        .with_oauth_urls(
            "https://accounts.google.com/o/oauth2/v2/auth",
            "https://oauth2.googleapis.com/token",
            [
                "https://www.googleapis.com/auth/drive.readonly",
                # "https://www.googleapis.com/auth/drive.metadata.readonly",
                # "https://www.googleapis.com/auth/drive.metadata",
                # "https://www.googleapis.com/auth/documents.readonly",
                # "https://www.googleapis.com/auth/spreadsheets.readonly",
                # "https://www.googleapis.com/auth/presentations.readonly",
                # "https://www.googleapis.com/auth/drive.file",
                # "https://www.googleapis.com/auth/drive",
            ]
        )
        .add_auth_field(CommonFields.client_id("Google Cloud Console"))
        .add_auth_field(CommonFields.client_secret("Google Cloud Console"))
        # .add_filter_field(CommonFields.modified_date_filter("Filter files and folders by modification date."))
        # .add_filter_field(CommonFields.created_date_filter("Filter files and folders by creation date."))
        # .add_filter_field(CommonFields.enable_manual_sync_filter())
        # .add_filter_field(CommonFields.file_extension_filter())
        # .add_filter_field(FilterField(
        #     name="shared",
        #     display_name="Index Shared Items",
        #     filter_type=FilterType.BOOLEAN,
        #     category=FilterCategory.INDEXING,
        #     description="Enable indexing of shared items",
        #     default_value=True
        # ))
        .with_sync_strategies(["SCHEDULED", "MANUAL"])
        .with_scheduled_config(True, 60)
        .with_sync_support(True)
        .with_agent_support(True)
    )\
    .build_decorator()
class GoogleDriveIndividualConnector(BaseConnector):
    def __init__(
        self,
        logger: Logger,
        data_entities_processor: DataSourceEntitiesProcessor,
        data_store_provider: DataStoreProvider,
        config_service: ConfigurationService,
        connector_id: str
    ) -> None:
        super().__init__(
            GoogleDriveApp(connector_id),
            logger,
            data_entities_processor,
            data_store_provider,
            config_service,
            connector_id
        )

        def _create_sync_point(sync_data_point_type: SyncDataPointType) -> SyncPoint:
            return SyncPoint(
                connector_id=self.connector_id,
                org_id=self.data_entities_processor.org_id,
                sync_data_point_type=sync_data_point_type,
                data_store_provider=self.data_store_provider
            )

        # Initialize sync points
        self.drive_delta_sync_point = _create_sync_point(SyncDataPointType.RECORDS)
        self.connector_id = connector_id

        # Batch processing configuration
        self.batch_size = 100
        self.max_concurrent_batches = 3

        self.sync_filters: FilterCollection = FilterCollection()
        self.indexing_filters: FilterCollection = FilterCollection()

        # Google Drive client and data source (initialized in init())
        self.google_client: Optional[GoogleClient] = None
        self.drive_data_source: Optional[GoogleDriveDataSource] = None
        self.config: Optional[Dict] = None

    async def init(self) -> bool:
        """Initialize the Google Drive connector with credentials and services."""
        try:
            # Load connector config
            config = await self.config_service.get_config(
                f"/services/connectors/{self.connector_id}/config"
            )
            if not config:
                self.logger.error("Google Drive config not found")
                return False

            self.config = {"credentials": config}

            # Extract auth configuration
            auth_config = config.get("auth", {})
            client_id = auth_config.get("clientId")
            client_secret = auth_config.get("clientSecret")

            if not all((client_id, client_secret)):
                self.logger.error(
                    "Incomplete Google Drive config. Ensure clientId and clientSecret are configured."
                )
                raise ValueError(
                    "Incomplete Google Drive credentials. Ensure clientId and clientSecret are configured."
                )

            # Extract credentials (tokens)
            credentials_data = config.get("credentials", {})
            access_token = credentials_data.get("access_token")
            refresh_token = credentials_data.get("refresh_token")

            if not access_token and not refresh_token:
                self.logger.warning(
                    "No access token or refresh token found. Connector may need OAuth flow completion."
                )

            # Initialize Google Client using build_from_services
            # This will handle token management and credential refresh automatically
            try:
                self.google_client = await GoogleClient.build_from_services(
                    service_name="drive",
                    logger=self.logger,
                    config_service=self.config_service,
                    is_individual=True,  # This is an individual connector
                    version="v3",
                    connector_instance_id=self.connector_id
                )
                
                # Create Google Drive Data Source from the client
                self.drive_data_source = GoogleDriveDataSource(
                    self.google_client.get_client()
                )
                
                self.logger.info(
                    "✅ Google Drive client and data source initialized successfully"
                )
            except Exception as e:
                self.logger.error(
                    f"❌ Failed to initialize Google Drive client: {e}",
                    exc_info=True
                )
                raise ValueError(f"Failed to initialize Google Drive client: {e}") from e

            self.logger.info("✅ Google Drive connector initialized successfully")
            return True

        except Exception as ex:
            self.logger.error(f"❌ Error initializing Google Drive connector: {ex}", exc_info=True)
            raise

    async def _process_drive_file(
        self,
        metadata: dict,
        user_id: str,
        user_email: str,
        record_group_id: str
    ) -> Optional[RecordUpdate]:
        """
        Process a single Google Drive file and detect changes.
        
        Args:
            metadata: Google Drive file metadata dictionary
            user_id: The user's account ID
            user_email: The user's email
            record_group_id: The record group ID (user's drive ID)
            
        Returns:
            RecordUpdate object or None if entry should be skipped
        """
        try:
            file_id = metadata.get("id")
            if not file_id:
                return None
            
            org_id = self.data_entities_processor.org_id
            
            # Get existing record from the database
            # !!! IMPORTANT: Do not use tx_store directly here. Use the data_entities_processor instead.
            async with self.data_store_provider.transaction() as tx_store:
                existing_record = await tx_store.get_record_by_external_id(
                    connector_id=self.connector_id,
                    external_id=file_id
                )
            
            # Detect changes
            is_new = existing_record is None
            is_updated = False
            metadata_changed = False
            content_changed = False
            permissions_changed = False
            
            if existing_record:
                if existing_record.record_name != metadata.get("name", "Untitled"):
                    metadata_changed = True
                    is_updated = True
                if existing_record.external_revision_id != metadata.get("headRevisionId"):
                    content_changed = True
                    is_updated = True
            
            # Determine if it's a file or folder
            mime_type = metadata.get("mimeType", "")
            is_file = mime_type != MimeTypes.GOOGLE_DRIVE_FOLDER.value
            
            # Determine indexing status - shared files are not indexed by default
            is_shared = metadata.get("shared", False) or metadata.get("isSharedWithMe", False)
            status = ProgressStatus.AUTO_INDEX_OFF.value if is_shared else ProgressStatus.NOT_STARTED.value
            
            # Get timestamps
            created_time = metadata.get("createdTime")
            modified_time = metadata.get("modifiedTime")
            timestamp_ms = get_epoch_timestamp_in_ms()
            source_created_at = int(parse_timestamp(created_time)) if created_time else timestamp_ms
            source_updated_at = int(parse_timestamp(modified_time)) if modified_time else timestamp_ms
            
            # Get file extension
            file_extension = metadata.get("fileExtension", None)
            if not file_extension:
                file_name = metadata.get("name", "")
                if "." in file_name:
                    file_extension = file_name.rsplit(".", 1)[-1].lower()
            
            # Create FileRecord directly
            file_record = FileRecord(
                id=existing_record.id if existing_record else str(uuid.uuid4()),
                org_id=org_id,
                record_name=str(metadata.get("name", "Untitled")),
                record_type=RecordType.FILE,
                record_group_type=RecordGroupType.DRIVE.value,
                external_record_group_id=record_group_id,
                external_record_id=str(file_id),
                external_revision_id=metadata.get("headRevisionId", None),
                version=0 if is_new else (existing_record.version + 1 if existing_record else 0),
                origin=OriginTypes.CONNECTOR.value,
                connector_name=Connectors.GOOGLE_DRIVE.value,
                connector_id=self.connector_id,
                created_at=timestamp_ms,
                updated_at=timestamp_ms,
                source_created_at=source_created_at,
                source_updated_at=source_updated_at,
                weburl=metadata.get("webViewLink", None),
                mime_type=mime_type if mime_type else MimeTypes.UNKNOWN.value,
                indexing_status=status,
                is_file=is_file,
                size_in_bytes=int(metadata.get("size", 0) or 0),
                extension=file_extension,
                path=metadata.get("path", None),
                etag=metadata.get("etag", None),
                ctag=metadata.get("ctag", None),
                quick_xor_hash=metadata.get("quickXorHash", None),
                crc32_hash=metadata.get("crc32Hash", None),
                sha1_hash=metadata.get("sha1Checksum", None),
                sha256_hash=metadata.get("sha256Checksum", None),
                md5_hash=metadata.get("md5Checksum", None),
                is_shared=is_shared,
            )
            
            # Handle Permissions
            new_permissions = [
                Permission(
                    external_id=user_id,
                    email=user_email,
                    type=PermissionType.OWNER,
                    entity_type=EntityType.USER
                )
            ]
            
            # Compare permissions
            old_permissions = []
            
            return RecordUpdate(
                record=file_record,
                is_new=is_new,
                is_updated=is_updated,
                is_deleted=False,
                metadata_changed=metadata_changed,
                content_changed=content_changed,
                permissions_changed=permissions_changed,
                old_permissions=old_permissions,
                new_permissions=new_permissions,
                external_record_id=file_id
            )
            
        except Exception as ex:
            self.logger.error(f"Error processing Google Drive file {metadata.get('id', 'unknown')}: {ex}", exc_info=True)
            return None

    async def _process_drive_items_generator(
        self,
        files: List[dict],
        user_id: str,
        user_email: str,
        record_group_id: str
    ) -> AsyncGenerator[Tuple[Optional[FileRecord], List[Permission], RecordUpdate], None]:
        """
        Process Google Drive files and yield records with their permissions.
        Generator for non-blocking processing of large datasets.

        Args:
            files: List of Google Drive file metadata
            user_id: The user's account ID
            user_email: The user's email
            record_group_id: The record group ID (user's drive ID)
        """
        import asyncio
        
        for file_metadata in files:
            try:
                record_update = await self._process_drive_file(
                    file_metadata,
                    user_id,
                    user_email,
                    record_group_id
                )
                if record_update and record_update.record:
                    yield (record_update.record, record_update.new_permissions or [], record_update)
                await asyncio.sleep(0)
            except Exception as e:
                self.logger.error(f"Error processing item in generator: {e}", exc_info=True)
                continue

    async def _handle_record_updates(self, record_update: RecordUpdate) -> None:
        """Handle different types of record updates (new, updated, deleted)."""
        try:
            if record_update.is_deleted:
                await self.data_entities_processor.on_record_deleted(
                    record_id=record_update.external_record_id
                )
            elif record_update.is_new:
                self.logger.info(f"New record detected: {record_update.record.record_name}")
            elif record_update.is_updated:
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
            self.logger.error(f"Error handling record updates: {e}", exc_info=True)

    async def _sync_user_personal_drive(self) -> None:
        """
        Sync user's personal Google Drive.
        
        If sync point doesn't exist, performs full sync using files_list.
        If sync point exists, performs incremental sync using changes_list.
        """
        if not self.drive_data_source:
            self.logger.error("Drive data source not initialized")
            return
        
        # Get user info
        fields = 'user(displayName,emailAddress,permissionId)'
        user_about = await self.drive_data_source.about_get(fields=fields)
        user_id = user_about.get('user', {}).get('permissionId')
        user_email = user_about.get('user', {}).get('emailAddress')
        
        if not user_id or not user_email:
            self.logger.error("Failed to get user information")
            return
        
        sync_point_key = "personal_drive"
        org_id = self.data_entities_processor.org_id
        
        # Check if sync point exists
        sync_point_data = await self.drive_delta_sync_point.read_sync_point(sync_point_key)
        page_token = sync_point_data.get("pageToken") if sync_point_data else None
        
        if not page_token:
            # Full sync: no sync point exists
            self.logger.info("🆕 Starting full sync for Google Drive (no sync point found)")
            await self._perform_full_sync(sync_point_key, org_id, user_id, user_email)
        else:
            # Incremental sync: sync point exists
            self.logger.info(f"🔄 Starting incremental sync for Google Drive (pageToken: {page_token[:20]}...)")
            await self._perform_incremental_sync(sync_point_key, org_id, user_id, user_email, page_token)

    async def _perform_full_sync(self, sync_point_key: str, org_id: str, user_id: str, user_email: str) -> None:
        """
        Perform full sync by fetching all files using files_list.
        
        Args:
            sync_point_key: Key for storing sync point
            org_id: Organization ID
            user_id: User's account ID
            user_email: User's email
        """
        try:
            # Get start page token for future incremental syncs
            start_token_response = await self.drive_data_source.changes_get_start_page_token()
            start_page_token = start_token_response.get("startPageToken")
            
            if not start_page_token:
                self.logger.error("Failed to get start page token")
                return
            
            self.logger.info(f"📋 Start page token: {start_page_token[:20]}...")
            
            # Fetch all files with pagination
            page_token = None
            total_files = 0
            batch_records = []
            batch_size = self.batch_size
            
            while True:
                # Prepare files_list parameters
                # Using fields that match the working example from drive_user_service.py
                # Note: etag, ctag, quickXorHash, crc32Hash are not available in files.list() - they require files.get()
                list_params = {  
                    "fields": "nextPageToken, files(id, name, mimeType, size, createdTime, modifiedTime, webViewLink, fileExtension, headRevisionId, shared, md5Checksum, sha1Checksum, sha256Checksum)",
                }
                
                if page_token:
                    list_params["pageToken"] = page_token
                
                # #region agent log
                import json
                with open('/home/rogue/Programs/pipeshub-ai/.cursor/debug.log', 'a') as f:
                    f.write(json.dumps({"sessionId": "debug-session", "runId": "run1", "hypothesisId": "A", "location": "connector.py:483", "message": "list_params before files_list call", "data": {"list_params_keys": list(list_params.keys()), "has_fields": "fields" in list_params, "has_pageToken": "pageToken" in list_params}, "timestamp": int(__import__("time").time() * 1000)}) + "\n")
                # #endregion
                
                # Fetch files
                self.logger.info(f"📥 Fetching files page (token: {page_token[:20] if page_token else 'initial'}...)")
                files_response = await self.drive_data_source.files_list(**list_params)
                
                files = files_response.get("files", [])
                
                # #region agent log
                if files:
                    sample_file = files[0]
                    with open('/home/rogue/Programs/pipeshub-ai/.cursor/debug.log', 'a') as f:
                        f.write(json.dumps({"sessionId": "debug-session", "runId": "run1", "hypothesisId": "D", "location": "connector.py:491", "message": "sample file fields returned", "data": {"file_keys": list(sample_file.keys()), "has_shared": "shared" in sample_file, "has_owners": "owners" in sample_file, "has_isSharedWithMe": "isSharedWithMe" in sample_file}, "timestamp": int(__import__("time").time() * 1000)}) + "\n")
                # #endregion
                
                if not files:
                    self.logger.info("No more files to process")
                    break
                
                # Process files using generator
                async for record, perms, update in self._process_drive_items_generator(
                    files,
                    user_id,
                    user_email,
                    user_id  # record_group_id is user_id for personal drive
                ):
                    if update.is_deleted:
                        await self._handle_record_updates(update)
                        continue
                    elif update.is_updated:
                        await self._handle_record_updates(update)
                        continue
                    else:
                        batch_records.append((record, perms))
                        total_files += 1
                        
                        # Process in batches
                        if len(batch_records) >= batch_size:
                            self.logger.info(f"💾 Processing batch of {len(batch_records)} records")
                            await self.data_entities_processor.on_new_records(batch_records)
                            batch_records = []
                            await asyncio.sleep(0)
                
                # Check for next page
                page_token = files_response.get("nextPageToken")
                if not page_token:
                    break
            
            # Process remaining records
            if batch_records:
                self.logger.info(f"💾 Processing final batch of {len(batch_records)} records")
                await self.data_entities_processor.on_new_records(batch_records)
            
            # Save start page token to sync point for future incremental syncs
            await self.drive_delta_sync_point.update_sync_point(
                sync_point_key,
                {"pageToken": start_page_token}
            )
            
            self.logger.info(f"✅ Full sync completed. Processed {total_files} files. Saved page token: {start_page_token[:20]}...")
            
        except Exception as e:
            self.logger.error(f"❌ Error during full sync: {e}", exc_info=True)
            raise

    async def _perform_incremental_sync(self, sync_point_key: str, org_id: str, user_id: str, user_email: str, page_token: str) -> None:
        """
        Perform incremental sync by fetching changes using changes_list.
        
        Args:
            sync_point_key: Key for storing sync point
            org_id: Organization ID
            user_id: User's account ID
            user_email: User's email
            page_token: Page token from sync point
        """
        try:
            current_page_token = page_token
            total_changes = 0
            batch_records = []
            batch_size = self.batch_size
            
            while True:
                # Prepare changes_list parameters
                changes_params = {
                    "pageToken": current_page_token,
                    "pageSize": 1000,  # Maximum page size for Google Drive API
                    "includeRemoved": True,  # Include deleted files
                    "restrictToMyDrive": True,  # Only changes in user's drive
                    "supportsAllDrives": False,
                }
                
                # Fetch changes
                self.logger.info(f"📥 Fetching changes page (token: {current_page_token[:20]}...)")
                changes_response = await self.drive_data_source.changes_list(**changes_params)
                
                changes = changes_response.get("changes", [])
                if not changes:
                    self.logger.info("No more changes to process")
                    break
                
                # Extract files from changes
                files = []
                for change in changes:
                    is_removed = change.get("removed", False)
                    file_metadata = change.get("file")
                    
                    if is_removed:
                        # Handle deleted files
                        file_id = change.get("fileId")
                        if file_id:
                            # Create a RecordUpdate for deletion
                            deleted_update = RecordUpdate(
                                record=None,
                                is_new=False,
                                is_updated=False,
                                is_deleted=True,
                                metadata_changed=False,
                                content_changed=False,
                                permissions_changed=False,
                                external_record_id=file_id
                            )
                            await self._handle_record_updates(deleted_update)
                        continue
                    
                    if file_metadata:
                        files.append(file_metadata)
                
                # Process files using generator
                async for record, perms, update in self._process_drive_items_generator(
                    files,
                    user_id,
                    user_email,
                    user_id  # record_group_id is user_id for personal drive
                ):
                    if update.is_deleted:
                        await self._handle_record_updates(update)
                    elif update.is_updated:
                        await self._handle_record_updates(update)
                    else:
                        batch_records.append((record, perms))
                        total_changes += 1
                        
                        # Process in batches
                        if len(batch_records) >= batch_size:
                            self.logger.info(f"💾 Processing batch of {len(batch_records)} records")
                            await self.data_entities_processor.on_new_records(batch_records)
                            batch_records = []
                            await asyncio.sleep(0)
                
                # Get next page token
                next_page_token = changes_response.get("nextPageToken")
                if not next_page_token:
                    break
                
                current_page_token = next_page_token
            
            # Process remaining records
            if batch_records:
                self.logger.info(f"💾 Processing final batch of {len(batch_records)} records")
                await self.data_entities_processor.on_new_records(batch_records)
            
            # Update sync point with latest page token
            if current_page_token:
                await self.drive_delta_sync_point.update_sync_point(
                    sync_point_key,
                    {"pageToken": current_page_token}
                )
                self.logger.info(f"✅ Incremental sync completed. Processed {total_changes} changes. Updated page token: {current_page_token[:20]}...")
            else:
                self.logger.warning("⚠️  No next page token found, sync point not updated")
            
        except Exception as e:
            self.logger.error(f"❌ Error during incremental sync: {e}", exc_info=True)
            raise
    
    async def test_connection_and_access(self) -> bool:
        """Test connection and access to Google Drive."""
        try:
            self.logger.info("Testing connection and access to Google Drive")
            if not self.drive_data_source:
                self.logger.error("Drive data source not initialized. Call init() first.")
                return False

            if not self.google_client:
                self.logger.error("Google client not initialized. Call init() first.")
                return False

            # Try to make a simple API call to test connection
            # For now, just check if client is initialized
            if self.google_client.get_client() is None:
                self.logger.warning("Google Drive API client not initialized")
                return False

            return True
        except Exception as e:
            self.logger.error(f"❌ Error testing connection and access to Google Drive: {e}")
            return False

    def get_signed_url(self, record: Record) -> Optional[str]:
        """Get a signed URL for a specific record."""
        raise NotImplementedError("get_signed_url is not yet implemented for Google Drive")

    def stream_record(self, record: Record) -> StreamingResponse:
        """Stream a record from Google Drive."""
        raise NotImplementedError("stream_record is not yet implemented for Google Drive")

    async def _create_personal_record_group(self, user_id: str, user_email: str, display_name: str) -> RecordGroup:
        """Create a personal record group for the user."""
        record_group = RecordGroup(
            name=display_name,
            group_type=RecordGroupType.DRIVE.value,
            connector_name=self.connector_name,
            connector_id=self.connector_id,
            external_group_id=user_id,
        )

        permissions = [Permission(external_id=user_id, email=user_email, type=PermissionType.OWNER, entity_type=EntityType.USER)]
        await self.data_entities_processor.on_new_record_groups([(record_group, permissions)])
        return record_group
    
    async def _create_app_user(self, user_about: Dict) -> None:
        try:
            
            user = AppUser(
                email=user_about.get('user').get('emailAddress'),
                full_name=user_about.get('user').get('displayName'),
                source_user_id=user_about.get('user').get('permissionId'),
                app_name=Connectors.GOOGLE_DRIVE.value,
                connector_id=self.connector_id
            )
            await self.data_entities_processor.on_new_app_users([user])
        except Exception as e:
            self.logger.error(f"❌ Error creating app user: {e}", exc_info=True)
            raise
        
    async def run_sync(self) -> None:

        self.logger.info("Starting sync for Google Drive Individual")

        # Fetch app user
        fields = 'user(displayName,emailAddress,permissionId),storageQuota(limit,usage,usageInDrive)'
        user_about = await self.drive_data_source.about_get(fields=fields)
        await self._create_app_user(user_about)

        # Create user personal drive
        display_name = f"Google Drive - {user_about.get('user').get('emailAddress')}"
        await self._create_personal_record_group(
            user_about.get('user').get('permissionId'),
            user_about.get('user').get('emailAddress'),
            display_name
        )

        # Sync user's personal drive
        await self._sync_user_personal_drive()
        
        self.logger.info("Sync completed for Google Drive Individual")

    async def run_incremental_sync(self) -> None:
        """Run incremental sync for Google Drive."""
        self.logger.info("Starting incremental sync for Google Drive Individual")
        await self._sync_user_personal_drive()
        self.logger.info("Incremental sync completed for Google Drive Individual")

    def handle_webhook_notification(self, notification: Dict) -> None:
        """Handle webhook notifications from Google Drive."""
        raise NotImplementedError("handle_webhook_notification is not yet implemented for Google Drive")

    async def cleanup(self) -> None:
        """Cleanup resources when shutting down the connector."""
        try:
            self.logger.info("Cleaning up Google Drive connector resources")

            # Clear client and data source references
            if hasattr(self, 'drive_data_source') and self.drive_data_source:
                self.drive_data_source = None

            if hasattr(self, 'google_client') and self.google_client:
                self.google_client = None

            # Clear config
            self.config = None

            self.logger.info("Google Drive connector cleanup completed")

        except Exception as e:
            self.logger.error(f"❌ Error during cleanup: {e}")

    async def reindex_records(self, records: List[Record]) -> None:
        """Reindex records - not implemented for Google Drive yet."""
        raise NotImplementedError("reindex_records is not yet implemented for Google Drive")

    async def get_filter_options(
        self,
        filter_key: str,
        page: int = 1,
        limit: int = 20,
        search: Optional[str] = None,
        cursor: Optional[str] = None
    ) -> FilterOptionsResponse:
        """Google Drive connector does not support dynamic filter options."""
        raise NotImplementedError("Google Drive connector does not support dynamic filter options")

    @classmethod
    async def create_connector(
        cls,
        logger: Logger,
        data_store_provider: DataStoreProvider,
        config_service: ConfigurationService,
        connector_id: str
    ) -> BaseConnector:
        """Create a new instance of the Google Drive connector."""
        data_entities_processor = DataSourceEntitiesProcessor(
            logger,
            data_store_provider,
            config_service
        )
        await data_entities_processor.initialize()

        return GoogleDriveIndividualConnector(
            logger,
            data_entities_processor,
            data_store_provider,
            config_service,
            connector_id
        )
