#cluade-generated code

import asyncio
import uuid
import json
import hashlib
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from logging import Logger
from typing import AsyncGenerator, Dict, List, Optional, Tuple, Any
from urllib.parse import urlencode

import dropbox
from dropbox.exceptions import ApiError, AuthError
from dropbox.files import FileMetadata, FolderMetadata, DeletedMetadata
from dropbox.sharing import SharedLinkMetadata
from aiolimiter import AsyncLimiter
from fastapi import HTTPException
from fastapi.responses import StreamingResponse

from app.config.configuration_service import ConfigurationService
from app.config.constants.arangodb import Connectors, OriginTypes
from app.config.constants.http_status_code import HttpStatusCode
from app.connectors.core.base.connector.connector_service import BaseConnector
from app.connectors.core.base.data_processor.data_source_entities_processor import (
    DataSourceEntitiesProcessor,
)
from app.connectors.core.base.sync_point.sync_point import (
    SyncDataPointType,
    SyncPoint,
    generate_record_sync_point_key,
)
from app.connectors.services.base_arango_service import BaseArangoService
from app.models.entities import (
    FileRecord,
    Record,
    RecordGroupType,
    RecordType,
)
from app.models.permission import EntityType, Permission, PermissionType
from app.models.users import User
from app.utils.streaming import stream_content

from app.connectors.core.interfaces.connector.apps import App, AppGroup
from app.config.constants.arangodb import Connectors, AppGroups

@dataclass
class DropboxCredentials:
    app_key: str
    app_secret: str
    access_token: str
    refresh_token: Optional[str] = None


class DropboxApp(App):
    def __init__(self) -> None:
        super().__init__(Connectors.DROPBOX.value, AppGroups.CLOUD_STORAGE.value)


@dataclass
class RecordUpdate:
    """Represents a record update with change detection"""
    record: Optional[FileRecord]
    external_record_id: str
    is_new: bool = False
    is_updated: bool = False
    is_deleted: bool = False
    metadata_changed: bool = False
    content_changed: bool = False
    permissions_changed: bool = False
    old_permissions: Optional[List[Permission]] = None
    new_permissions: Optional[List[Permission]] = None


class DropboxClient:
    """Wrapper for Dropbox API with rate limiting and error handling"""
    
    def __init__(self, access_token: str, logger: Logger):
        self.client = dropbox.Dropbox(access_token)
        self.logger = logger
        self.rate_limiter = AsyncLimiter(50, 1)  # 50 requests per second
    
    async def get_metadata(self, path: str) -> Optional[Any]:
        """Get file/folder metadata"""
        try:
            async with self.rate_limiter:
                return self.client.files_get_metadata(path)
        except ApiError as e:
            self.logger.error(f"Error getting metadata for {path}: {e}")
            return None
    
    async def list_folder(self, path: str = "", recursive: bool = False) -> List[Any]:
        """List folder contents"""
        try:
            entries = []
            async with self.rate_limiter:
                result = self.client.files_list_folder(path, recursive=recursive)
                entries.extend(result.entries)
                
                while result.has_more:
                    async with self.rate_limiter:
                        result = self.client.files_list_folder_continue(result.cursor)
                        entries.extend(result.entries)
            
            return entries
        except ApiError as e:
            self.logger.error(f"Error listing folder {path}: {e}")
            return []
    
    async def get_delta(self, cursor: Optional[str] = None) -> Dict[str, Any]:
        """Get file changes using delta API"""
        try:
            async with self.rate_limiter:
                if cursor:
                    result = self.client.files_list_folder_continue(cursor)
                else:
                    # Start from root with recursive listing
                    result = self.client.files_list_folder("", recursive=True)
                
                return {
                    'entries': result.entries,
                    'cursor': result.cursor,
                    'has_more': result.has_more
                }
        except ApiError as e:
            self.logger.error(f"Error getting delta: {e}")
            return {'entries': [], 'cursor': None, 'has_more': False}
    
    async def get_sharing_info(self, path: str) -> List[Dict[str, Any]]:
        """Get sharing information for a file/folder"""
        try:
            permissions = []
            
            # Get shared links
            async with self.rate_limiter:
                try:
                    shared_links = self.client.sharing_list_shared_links(path=path)
                    for link in shared_links.links:
                        permissions.append({
                            'type': 'shared_link',
                            'url': link.url,
                            'visibility': getattr(link, 'link_permissions', {}).get('resolved_visibility', 'public'),
                            'expires': getattr(link, 'expires', None)
                        })
                except ApiError:
                    # File might not have shared links
                    pass
            
            # Get folder members (if it's a shared folder)
            async with self.rate_limiter:
                try:
                    folder_metadata = self.client.sharing_get_folder_metadata(path)
                    if folder_metadata:
                        # Get folder members
                        members = self.client.sharing_list_folder_members(folder_metadata.shared_folder_id)
                        for member in members.users:
                            permissions.append({
                                'type': 'user',
                                'email': member.user.email,
                                'access_type': member.access_type._tag,
                                'user_id': member.user.account_id
                            })
                        for member in members.groups:
                            permissions.append({
                                'type': 'group',
                                'group_id': member.group.group_id,
                                'group_name': member.group.group_name,
                                'access_type': member.access_type._tag
                            })
                except ApiError:
                    # Not a shared folder
                    pass
            
            return permissions
        except Exception as e:
            self.logger.error(f"Error getting sharing info for {path}: {e}")
            return []
    
    async def get_temporary_link(self, path: str) -> Optional[str]:
        """Get temporary download link"""
        try:
            async with self.rate_limiter:
                result = self.client.files_get_temporary_link(path)
                return result.link
        except ApiError as e:
            self.logger.error(f"Error getting temporary link for {path}: {e}")
            return None
    
    async def get_account_info(self) -> Optional[Dict[str, Any]]:
        """Get current account information"""
        try:
            async with self.rate_limiter:
                account = self.client.users_get_current_account()
                return {
                    'account_id': account.account_id,
                    'email': account.email,
                    'name': account.name.display_name,
                    'country': account.country,
                    'locale': account.locale
                }
        except ApiError as e:
            self.logger.error(f"Error getting account info: {e}")
            return None


class DropboxConnector(BaseConnector):
    def __init__(self, logger: Logger, data_entities_processor: DataSourceEntitiesProcessor,
        arango_service: BaseArangoService, config_service: ConfigurationService) -> None:
        super().__init__(DropboxApp(), logger, data_entities_processor, arango_service, config_service)

        self.connector_name = Connectors.DROPBOX.value

        def _create_sync_point(sync_data_point_type: SyncDataPointType) -> SyncPoint:
            return SyncPoint(
                connector_name=self.connector_name,
                org_id=self.data_entities_processor.org_id,
                sync_data_point_type=sync_data_point_type,
                arango_service=self.arango_service
            )

        # Initialize sync points
        self.file_sync_point = _create_sync_point(SyncDataPointType.RECORDS)
        self.user_sync_point = _create_sync_point(SyncDataPointType.USERS)

        # Batch processing configuration
        self.batch_size = 100
        self.max_concurrent_batches = 3

        self.rate_limiter = AsyncLimiter(50, 1)  # 50 requests per second

    async def init(self) -> bool:
        """Initialize the Dropbox connector"""
        try:
            credentials_config = await self.config_service.get_config(
                f"/services/connectors/dropbox/config/{self.data_entities_processor.org_id}"
            )
            
            if not credentials_config:
                self.logger.error("Dropbox credentials not found")
                return False

            app_key = credentials_config.get("appKey")
            app_secret = credentials_config.get("appSecret") 
            access_token = credentials_config.get("accessToken")
            refresh_token = credentials_config.get("refreshToken")

            if not all((app_key, app_secret, access_token)):
                self.logger.error("Incomplete Dropbox credentials. Ensure appKey, appSecret, and accessToken are configured.")
                raise ValueError("Incomplete Dropbox credentials")

            self.credentials = DropboxCredentials(
                app_key=app_key,
                app_secret=app_secret,
                access_token=access_token,
                refresh_token=refresh_token
            )

            # Initialize Dropbox client
            self.dropbox_client = DropboxClient(access_token, self.logger)
            
            # Test connection
            account_info = await self.dropbox_client.get_account_info()
            if not account_info:
                self.logger.error("Failed to connect to Dropbox API")
                return False
            
            self.logger.info(f"Successfully connected to Dropbox for account: {account_info.get('email')}")
            return True

        except Exception as e:
            self.logger.error(f"Error initializing Dropbox connector: {e}")
            return False

    def _map_dropbox_access_to_permission_type(self, access_type: str) -> PermissionType:
        """Map Dropbox access types to our permission types"""
        mapping = {
            'owner': PermissionType.OWNER,
            'editor': PermissionType.WRITE,
            'viewer': PermissionType.READ,
            'viewer_no_comment': PermissionType.READ
        }
        return mapping.get(access_type, PermissionType.READ)

    async def _convert_to_permissions(self, sharing_info: List[Dict[str, Any]]) -> List[Permission]:
        """Convert Dropbox sharing info to our Permission model"""
        permissions = []
        
        for share in sharing_info:
            try:
                if share['type'] == 'user':
                    permissions.append(Permission(
                        external_id=share['user_id'],
                        email=share['email'],
                        type=self._map_dropbox_access_to_permission_type(share['access_type']),
                        entity_type=EntityType.USER
                    ))
                elif share['type'] == 'group':
                    permissions.append(Permission(
                        external_id=share['group_id'],
                        email=None,
                        type=self._map_dropbox_access_to_permission_type(share['access_type']),
                        entity_type=EntityType.GROUP
                    ))
                elif share['type'] == 'shared_link':
                    visibility = share.get('visibility', 'public')
                    if visibility == 'public':
                        permissions.append(Permission(
                            external_id="anyone_with_link",
                            email=None,
                            type=PermissionType.READ,
                            entity_type=EntityType.ANYONE_WITH_LINK
                        ))
                    elif visibility == 'team_only':
                        permissions.append(Permission(
                            external_id="team_members",
                            email=None,
                            type=PermissionType.READ,
                            entity_type=EntityType.ORG
                        ))
            except Exception as e:
                self.logger.error(f"Error converting permission: {e}")
                continue
        
        return permissions

    async def _process_dropbox_entry(self, entry: Any) -> Optional[RecordUpdate]:
        """Process a single Dropbox file/folder entry"""
        try:
            # Handle deleted items
            if isinstance(entry, DeletedMetadata):
                self.logger.info(f"Item {entry.path_display} has been deleted")
                await self.data_entities_processor.on_record_deleted(
                    record_id=entry.path_display
                )
                return RecordUpdate(
                    record=None,
                    external_record_id=entry.path_display,
                    is_deleted=True
                )

            # Only process files (not folders for now)
            if not isinstance(entry, FileMetadata):
                return None

            # Get existing record if any
            existing_record = await self.arango_service.get_record_by_external_id(
                connector_name=self.connector_name,
                external_id=entry.path_display
            )

            # Detect changes
            is_new = existing_record is None
            is_updated = False
            metadata_changed = False
            content_changed = False
            permissions_changed = False

            if existing_record:
                # Check for metadata changes
                if (existing_record.external_revision_id != entry.rev or
                    existing_record.record_name != entry.name or
                    existing_record.updated_at != int(entry.client_modified.timestamp() * 1000)):
                    metadata_changed = True
                    is_updated = True

                # Check for content changes (different hash)
                current_hash = entry.content_hash if hasattr(entry, 'content_hash') else None
                if existing_record.sha256_hash != current_hash:
                    content_changed = True
                    is_updated = True

            # Get signed URL for the file
            signed_url = await self.dropbox_client.get_temporary_link(entry.path_display)

            # Create file record
            file_record = FileRecord(
                id=existing_record.id if existing_record else str(uuid.uuid4()),
                record_name=entry.name,
                record_type=RecordType.FILE,
                record_group_type=RecordGroupType.DRIVE.value,
                parent_record_type=RecordType.FILE.value,
                external_record_id=entry.path_display,
                external_revision_id=entry.rev,
                version=0 if is_new else existing_record.version + 1,
                origin=OriginTypes.CONNECTOR.value,
                connector_name=self.connector_name,
                created_at=int(entry.client_modified.timestamp() * 1000),
                updated_at=int(entry.client_modified.timestamp() * 1000),
                source_created_at=int(entry.client_modified.timestamp() * 1000),
                source_updated_at=int(entry.client_modified.timestamp() * 1000),
                weburl=f"https://www.dropbox.com/home{entry.path_display}",
                signed_url=signed_url,
                mime_type=self._get_mime_type_from_extension(entry.name),
                parent_external_record_id=entry.path_display.rsplit('/', 1)[0] if '/' in entry.path_display else None,
                external_record_group_id="dropbox_root",
                size_in_bytes=entry.size,
                is_file=True,
                extension=entry.name.split(".")[-1] if "." in entry.name else None,
                path=entry.path_display.rsplit('/', 1)[0] if '/' in entry.path_display else "/",
                etag=entry.rev,
                sha256_hash=entry.content_hash if hasattr(entry, 'content_hash') else None,
            )

            # Skip files without extensions
            if file_record.is_file and file_record.extension is None:
                return None

            # Get current permissions
            sharing_info = await self.dropbox_client.get_sharing_info(entry.path_display)
            new_permissions = await self._convert_to_permissions(sharing_info)

            # Check for permission changes
            if existing_record and existing_record.permissions:
                old_permissions = existing_record.permissions
                if not self._permissions_equal(old_permissions, new_permissions):
                    permissions_changed = True
                    is_updated = True

            return RecordUpdate(
                record=file_record,
                external_record_id=entry.path_display,
                is_new=is_new,
                is_updated=is_updated,
                is_deleted=False,
                metadata_changed=metadata_changed,
                content_changed=content_changed,
                permissions_changed=permissions_changed,
                old_permissions=existing_record.permissions if existing_record else None,
                new_permissions=new_permissions
            )

        except Exception as e:
            self.logger.error(f"Error processing Dropbox entry {entry.path_display if hasattr(entry, 'path_display') else 'unknown'}: {e}")
            return None

    def _get_mime_type_from_extension(self, filename: str) -> Optional[str]:
        """Get MIME type from file extension"""
        extension = filename.split(".")[-1].lower() if "." in filename else None
        mime_types = {
            'pdf': 'application/pdf',
            'doc': 'application/msword',
            'docx': 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
            'xls': 'application/vnd.ms-excel',
            'xlsx': 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
            'ppt': 'application/vnd.ms-powerpoint',
            'pptx': 'application/vnd.openxmlformats-officedocument.presentationml.presentation',
            'txt': 'text/plain',
            'jpg': 'image/jpeg',
            'jpeg': 'image/jpeg',
            'png': 'image/png',
            'gif': 'image/gif',
            'zip': 'application/zip',
            'mp4': 'video/mp4',
            'mp3': 'audio/mpeg',
        }
        return mime_types.get(extension, 'application/octet-stream')

    def _permissions_equal(self, old_perms: List[Permission], new_perms: List[Permission]) -> bool:
        """Compare two lists of permissions to detect changes"""
        if len(old_perms) != len(new_perms):
            return False

        old_set = {(p.external_id, p.email, p.type, p.entity_type) for p in old_perms}
        new_set = {(p.external_id, p.email, p.type, p.entity_type) for p in new_perms}

        return old_set == new_set

    async def _process_entries_generator(self, entries: List[Any]) -> AsyncGenerator[Tuple[FileRecord, List[Permission], RecordUpdate], None]:
        """Process entries and yield records with their permissions"""
        for entry in entries:
            try:
                record_update = await self._process_dropbox_entry(entry)

                if record_update:
                    if record_update.is_deleted:
                        yield (None, [], record_update)
                    elif record_update.record:
                        yield (record_update.record, record_update.new_permissions or [], record_update)

                # Allow other tasks to run
                await asyncio.sleep(0)

            except Exception as e:
                self.logger.error(f"Error processing entry in generator: {e}")
                continue

    async def _handle_record_updates(self, record_update: RecordUpdate) -> None:
        """Handle different types of record updates"""
        try:
            if record_update.is_deleted:
                await self.data_entities_processor.on_record_deleted(
                    record_id=record_update.external_record_id
                )
            elif record_update.is_new:
                self.logger.info(f"New record detected: {record_update.record.record_name}")
            elif record_update.is_updated:
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
            self.logger.error(f"Error handling record updates: {e}")

    async def _run_delta_sync(self) -> None:
        """Run delta synchronization using Dropbox delta API"""
        try:
            self.logger.info("Starting Dropbox delta sync")

            # Get current sync state
            sync_point_key = generate_record_sync_point_key(RecordType.FILE.value, "dropbox", "files")
            sync_point = await self.file_sync_point.read_sync_point(sync_point_key)
            
            cursor = sync_point.get('cursor') if sync_point else None

            batch_records = []
            batch_count = 0

            while True:
                # Get delta changes
                result = await self.dropbox_client.get_delta(cursor)
                entries = result.get('entries', [])
                
                if not entries:
                    break

                # Process entries using generator
                async for file_record, permissions, record_update in self._process_entries_generator(entries):
                    if record_update.is_deleted:
                        await self._handle_record_updates(record_update)
                        continue

                    if file_record:
                        batch_records.append((file_record, permissions))
                        batch_count += 1

                        # Handle updates if needed
                        if record_update.is_updated:
                            await self._handle_record_updates(record_update)

                        # Process batch when it reaches size limit
                        if batch_count >= self.batch_size:
                            await self.data_entities_processor.on_new_records(batch_records)
                            batch_records = []
                            batch_count = 0
                            await asyncio.sleep(0.1)

                # Process remaining records in batch
                if batch_records:
                    await self.data_entities_processor.on_new_records(batch_records)
                    batch_records = []
                    batch_count = 0

                # Update cursor
                new_cursor = result.get('cursor')
                if new_cursor:
                    await self.file_sync_point.update_sync_point(
                        sync_point_key,
                        sync_point_data={'cursor': new_cursor}
                    )
                    cursor = new_cursor

                if not result.get('has_more', False):
                    break

            self.logger.info("Completed Dropbox delta sync")

        except Exception as e:
            self.logger.error(f"Error in delta sync: {e}")
            raise

    async def _sync_account_user(self) -> None:
        """Sync the Dropbox account user"""
        try:
            account_info = await self.dropbox_client.get_account_info()
            if account_info:
                user = User(
                    id=str(uuid.uuid4()),
                    source_user_id=account_info['account_id'],
                    email=account_info['email'],
                    name=account_info['name'],
                    connector_name=self.connector_name,
                    origin=OriginTypes.CONNECTOR.value,
                    is_active=True
                )
                
                await self.data_entities_processor.on_new_users([user])
                self.logger.info(f"Synced user: {user.email}")
            else:
                self.logger.error("Failed to get account information")

        except Exception as e:
            self.logger.error(f"Error syncing account user: {e}")
            raise

    async def run_sync(self) -> None:
        """Main entry point for the Dropbox connector"""
        try:
            self.logger.info("Starting Dropbox connector sync")

            # Step 1: Sync account user
            self.logger.info("Syncing account user...")
            await self._sync_account_user()

            # Step 2: Run delta sync for files
            self.logger.info("Syncing files...")
            await self._run_delta_sync()

            self.logger.info("Dropbox connector sync completed successfully")

        except Exception as e:
            self.logger.error(f"Error in Dropbox connector run: {e}")
            raise

    async def run_incremental_sync(self) -> None:
        """Run incremental sync using stored cursor"""
        try:
            self.logger.info("Starting incremental sync")
            await self._run_delta_sync()
            self.logger.info("Incremental sync completed")

        except Exception as e:
            self.logger.error(f"Error in incremental sync: {e}")
            raise

    async def get_signed_url(self, record: Record) -> str:
        """Create a signed URL for a specific record"""
        try:
            return await self.dropbox_client.get_temporary_link(record.external_record_id)
        except Exception as e:
            self.logger.error(f"Error creating signed URL for record {record.id}: {e}")
            raise

    async def stream_record(self, record: Record) -> StreamingResponse:
        """Stream a record from Dropbox"""
        signed_url = await self.get_signed_url(record)
        if not signed_url:
            raise HTTPException(
                status_code=HttpStatusCode.NOT_FOUND.value, 
                detail="File not found or access denied"
            )

        return StreamingResponse(
            stream_content(signed_url),
            media_type=record.mime_type,
            headers={
                "Content-Disposition": f"attachment; filename={record.record_name}"
            }
        )

    async def test_connection_and_access(self) -> bool:
        """Test connection and access to Dropbox"""
        try:
            self.logger.info("Testing connection and access to Dropbox")
            account_info = await self.dropbox_client.get_account_info()
            return account_info is not None
        except Exception as e:
            self.logger.error(f"Error testing connection and access to Dropbox: {e}")
            return False

    async def cleanup(self) -> None:
        """Cleanup resources when shutting down the connector"""
        try:
            self.logger.info("Cleaning up Dropbox connector resources")
            # Close any open connections if needed
            self.dropbox_client = None
            self.logger.info("Dropbox connector cleanup completed")
        except Exception as e:
            self.logger.error(f"Error during cleanup: {e}")

    @classmethod
    async def create_connector(cls, logger: Logger,
        arango_service: BaseArangoService, config_service: ConfigurationService) -> BaseConnector:
        """Create a new DropboxConnector instance"""
        data_entities_processor = DataSourceEntitiesProcessor(logger, arango_service, config_service)
        await data_entities_processor.initialize()

        return DropboxConnector(logger, data_entities_processor, arango_service, config_service)


class DropboxWebhookManager:
    """Manages Dropbox webhooks for real-time notifications"""

    def __init__(self, dropbox_client: DropboxClient, logger: Logger, app_secret: str):
        self.client = dropbox_client
        self.logger = logger
        self.app_secret = app_secret

    def verify_webhook_signature(self, signature: str, body: bytes) -> bool:
        """Verify webhook signature from Dropbox"""
        try:
            expected_signature = hashlib.sha256(
                self.app_secret.encode('utf-8') + body
            ).hexdigest()
            return signature == expected_signature
        except Exception as e:
            self.logger.error(f"Error verifying webhook signature: {e}")
            return False

    async def handle_webhook_notification(self, notification: Dict[str, Any]) -> None:
        """Handle webhook notification from Dropbox"""
        try:
            self.logger.info("Processing Dropbox webhook notification")
            
            # Dropbox webhook notifications contain account IDs
            accounts = notification.get('list_folder', {}).get('accounts', [])
            
            for account in accounts:
                account_id = account
                self.logger.info(f"Processing changes for account: {account_id}")
                
                # Trigger incremental sync for this account
                # This would typically call the run_incremental_sync method
                
            self.logger.info("Webhook notification processed successfully")

        except Exception as e:
            self.logger.error(f"Error handling webhook notification: {e}")

    def get_webhook_verification_response(self, challenge: str) -> str:
        """Return the challenge for webhook verification"""
        return challenge