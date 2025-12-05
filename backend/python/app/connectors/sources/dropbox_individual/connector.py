import asyncio
import mimetypes
import re
import uuid
from datetime import datetime, timezone
from logging import Logger
from typing import AsyncGenerator, Dict, List, Optional, Tuple, Union

from aiolimiter import AsyncLimiter
from dropbox.exceptions import ApiError

# Dropbox SDK specific types
from dropbox.files import (
    DeletedMetadata,
    FileMetadata,
    FolderMetadata,
)
from dropbox.sharing import LinkAudience, SharedLinkSettings
from fastapi import HTTPException
from fastapi.responses import StreamingResponse

# Base connector and service imports
from app.config.configuration_service import ConfigurationService
from app.config.constants.arangodb import (
    Connectors,
    MimeTypes,
    OriginTypes,
)
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
    CommonFields,
    ConnectorBuilder,
    DocumentationLink,
)

# App-specific Dropbox client imports
from app.connectors.sources.dropbox_individual.common.apps import DropboxIndividualApp
from app.connectors.sources.microsoft.common.msgraph_client import RecordUpdate

# Model imports
from app.models.entities import (
    FileRecord,
    Record,
    RecordGroup,
    RecordGroupType,
    RecordType,
)
from app.models.permission import EntityType, Permission, PermissionType
from app.sources.client.dropbox.dropbox_ import (
    DropboxClient,
    DropboxTokenConfig,
)
from app.sources.external.dropbox.dropbox_ import DropboxDataSource
from app.utils.streaming import stream_content


# Helper functions (reused from team connector)
def get_parent_path_from_path(path: str) -> Optional[str]:
    """Extracts the parent path from a file/folder path."""
    if not path or path == "/" or "/" not in path.lstrip("/"):
        return None # Root directory has no parent path in this context
    parent_path = "/".join(path.strip("/").split("/")[:-1])
    return f"/{parent_path}" if parent_path else "/"


def get_file_extension(filename: str) -> Optional[str]:
    """Extracts the extension from a filename."""
    if "." in filename:
        parts = filename.split(".")
        if len(parts) > 1:
            return parts[-1].lower()
    return None


def get_mimetype_enum_for_dropbox(entry: Union[FileMetadata, FolderMetadata]) -> MimeTypes:
    """
    Determines the correct MimeTypes enum member for a Dropbox API entry.

    Args:
        entry: A FileMetadata or FolderMetadata object from the Dropbox SDK.

    Returns:
        The corresponding MimeTypes enum member.
    """
    # 1. Handle folders directly
    if isinstance(entry, FolderMetadata):
        return MimeTypes.FOLDER

    # 2. Handle files by guessing the type from the filename
    if isinstance(entry, FileMetadata):
        # The '.paper' extension is a special Dropbox file type. We can handle it explicitly
        # or let it fall through to the default binary type if not in the enum.
        if entry.name.endswith('.paper'):
            # Assuming .paper files are a form of web content.
            return MimeTypes.HTML

        # Use the mimetypes library to guess from the extension

        mime_type_str, _ = mimetypes.guess_type(entry.name)

        if mime_type_str:
            try:
                # 3. Attempt to convert the guessed string into our MimeTypes enum
                return MimeTypes(mime_type_str)
            except ValueError:
                # 4. If the guessed type is not in our enum (e.g., 'application/zip'),
                # fall back to the generic binary type.
                return MimeTypes.BIN

    # 5. Fallback for any unknown file type or if mimetypes fails
    return MimeTypes.BIN

# @dataclass
# class RecordUpdate:
#     """Track updates to a record"""
#     record: Optional[FileRecord]
#     is_new: bool
#     is_updated: bool
#     is_deleted: bool
#     metadata_changed: bool
#     content_changed: bool
#     permissions_changed: bool
#     old_permissions: Optional[List[Permission]] = None
#     new_permissions: Optional[List[Permission]] = None
#     external_record_id: Optional[str] = None

@ConnectorBuilder("Dropbox Personal")\
    .in_group("Cloud Storage")\
    .with_auth_type("OAUTH")\
    .with_description("Sync files and folders from Dropbox Personal account")\
    .with_categories(["Storage"])\
    .configure(lambda builder: builder
        .with_icon("/assets/icons/connectors/dropbox.svg")
        .with_realtime_support(True)
        .add_documentation_link(DocumentationLink(
            "Dropbox App Setup",
            "https://developers.dropbox.com/oauth-guide",
            "setup"
        ))
        .add_documentation_link(DocumentationLink(
            'Pipeshub Documentation',
            'https://docs.pipeshub.com/connectors/dropbox/dropbox_personal',
            'pipeshub'
        ))
        .with_redirect_uri("connectors/oauth/callback/Dropbox%20Personal", True)
        .with_oauth_urls(
            "https://www.dropbox.com/oauth2/authorize",
            "https://api.dropboxapi.com/oauth2/token",
            [
                "account_info.read",
                "files.content.read",
                "files.metadata.read",
                "sharing.read",
                "sharing.write"
            ]
        )
        .add_auth_field(CommonFields.client_id("Dropbox App Console"))
        .add_auth_field(CommonFields.client_secret("Dropbox App Console"))
        .with_webhook_config(True, ["file.added", "file.modified", "file.deleted"])
        .with_scheduled_config(True, 60)
        .add_sync_custom_field(CommonFields.batch_size_field())
    )\
    .build_decorator()
class DropboxIndividualConnector(BaseConnector):
    """
    Connector for synchronizing data from a Dropbox Individual account.
    Simplified version without team-specific features.
    """

    current_user_id: Optional[str] = None
    current_user_email: Optional[str] = None

    def __init__(
        self,
        logger: Logger,
        data_entities_processor: DataSourceEntitiesProcessor,
        data_store_provider: DataStoreProvider,
        config_service: ConfigurationService,
    ) -> None:

        """Initialize the Dropbox Individual connector."""

        super().__init__(
            DropboxIndividualApp(),
            logger,
            data_entities_processor,
            data_store_provider,
            config_service
        )

        self.connector_name = Connectors.DROPBOX_PERSONAL

        # Sync point (Only RECORDS needed for individual)
        # We inline the logic here because we only create one.
        self.dropbox_cursor_sync_point = SyncPoint(
            connector_name=self.connector_name,
            org_id=self.data_entities_processor.org_id,
            sync_data_point_type=SyncDataPointType.RECORDS,
            data_store_provider=self.data_store_provider
        )

        # NOTE: We do NOT initialize user_sync_point or user_group_sync_point
        # because individual accounts do not sync a member directory.

        self.data_source: Optional[DropboxDataSource] = None
        self.batch_size = 100
        self.max_concurrent_batches = 5
        self.rate_limiter = AsyncLimiter(50, 1)  # 50 requests per second

    async def init(self) -> bool:
        """
        Initializes the Dropbox client using credentials from the config service.
        Sets up client for individual account (is_team=False).
        """
        config = await self.config_service.get_config(
            "/services/connectors/dropboxpersonal/config"
        )
        if not config:
            self.logger.error("Dropbox Individual access token not found in configuration.")
            return False

        credentials_config = config.get("credentials")
        access_token = credentials_config.get("access_token")
        refresh_token = credentials_config.get("refresh_token")

        auth_config = config.get("auth")
        app_key = auth_config.get("clientId")
        app_secret = auth_config.get("clientSecret")

        try:
            config = DropboxTokenConfig(
                token=access_token,
                refresh_token=refresh_token,
                app_key=app_key,
                app_secret=app_secret
            )
            client = await DropboxClient.build_with_config(config, is_team=False)
            self.data_source = DropboxDataSource(client)
            self.logger.info("Dropbox Individual client initialized successfully.")
            return True
        except Exception as e:
            self.logger.error(f"Failed to initialize Dropbox Individual client: {e}", exc_info=True)
            return False


    async def _get_current_user_info(self) -> Tuple[str, str]:
        """
        Fetches the current user's account information.
        Returns:
            Tuple of (account_id, email)
        """
        # Check if we already have the current user info
        if self.current_user_id and self.current_user_email:
            return self.current_user_id, self.current_user_email

        # Fetch the current user info from the Dropbox API
        response = await self.data_source.users_get_current_account()

        if not response:
            raise ValueError("Failed to retrieve account information (empty response).")

        if not response.success:
            error_detail = response.error or "Unknown Dropbox API error"
            self.logger.error("Dropbox API rejected users_get_current_account: %s", error_detail)
            raise ValueError(f"Failed to retrieve account information: {error_detail}")

        if not response.data:
            raise ValueError("Failed to retrieve account information (no payload).")

        self.current_user_id = response.data.account_id
        self.current_user_email = response.data.email

        return self.current_user_id, self.current_user_email


    async def _process_dropbox_entry(
        self,
        entry: Union[FileMetadata, FolderMetadata, DeletedMetadata],
        user_id: str,
        user_email: str,
        record_group_id: str
    ) -> Optional[RecordUpdate]:
        """
        Process a single Dropbox entry and detect changes.
        Simplified version without team-specific parameters.
        """
        try:
            # 1. Handle Deleted Items (Deletion from db not implemented yet)
            if isinstance(entry, DeletedMetadata):
                # return None
                # self.logger.info(f"Item at path '{entry.path_lower}' has been deleted.")


                # async with self.data_store_provider.transaction() as tx_store:
                #     record = await tx_store.get_record_by_path(
                #         connector_name=self.connector_name,
                #         path=entry.path_lower,
                #     )

                # print("GOING TO RUN ON_RECORD_DELETED 1: ", record["_key"], record["name"])
                # await self.data_entities_processor.on_record_deleted(
                #     record_id=record["_key"]
                # )


                # return RecordUpdate(
                #     record=None,
                #     external_record_id=entry.id,
                #     is_new=False,
                #     is_updated=False,
                #     is_deleted=True,
                #     metadata_changed=False,
                #     content_changed=False,
                #     permissions_changed=False
                # )
                pass

            # 2. Get existing record from the database
            async with self.data_store_provider.transaction() as tx_store:
                existing_record = await tx_store.get_record_by_external_id(
                    connector_name=self.connector_name,
                    external_id=entry.id
                )

            # 3. Detect changes
            is_new = existing_record is None
            is_updated = False
            metadata_changed = False
            content_changed = False
            permissions_changed = False
            is_file = isinstance(entry, FileMetadata)

            if existing_record:
                if existing_record.record_name != entry.name:
                    metadata_changed = True
                    is_updated = True
                if is_file and existing_record.external_revision_id != entry.rev:
                    content_changed = True
                    is_updated = True

            # 4. Create or Update the FileRecord object
            record_type = RecordType.FILE

            # Conditionally get timestamp: files have it, folders do not.
            if is_file:
                timestamp_ms = int(entry.server_modified.timestamp() * 1000)
            else:
                # Use current time for folders as a fallback.
                timestamp_ms = int(datetime.now(timezone.utc).timestamp() * 1000)

            # 5. Get signed URL for files
            signed_url = None
            if is_file:
                temp_link_result = await self.data_source.files_get_temporary_link(
                    entry.path_lower
                )
                if temp_link_result.success:
                    signed_url = temp_link_result.data.link

            # 5.5 Get preview URL
            # We keep the verbose logging and fallback logic from Teams because
            # Dropbox API often throws "shared_link_already_exists" for individual users too.
            self.logger.info("=" * 50)
            self.logger.info("Processing weburl for path: %s", entry.path_lower)

            preview_url = None
            link_settings = SharedLinkSettings(
                audience=LinkAudience('no_one'),
                allow_download=True
            )

            # First call - try to create link with settings
            shared_link_result = await self.data_source.sharing_create_shared_link_with_settings(
                path=entry.path_lower,
                settings=link_settings
            )

            self.logger.info("Result 1: %s", shared_link_result)

            if shared_link_result.success:
                # Successfully created new link
                preview_url = shared_link_result.data.url
                self.logger.info("Successfully created new link: %s", preview_url)
            else:
                # First call failed - check if link already exists
                error_str = str(shared_link_result.error)
                self.logger.info("First call failed with error type")

                if 'shared_link_already_exists' in error_str:
                    self.logger.info("Link already exists, making second call to retrieve it")

                    # Make second call with settings=None to get the existing link
                    second_result = await self.data_source.sharing_create_shared_link_with_settings(
                        path=entry.path_lower,
                        settings=None
                    )

                    self.logger.info("Result 2 received")

                    if second_result.success:
                        # Unexpectedly succeeded
                        preview_url = second_result.data.url
                        self.logger.info("Second call succeeded: %s", preview_url)
                    else:
                        # Expected to fail - extract URL from error string
                        second_error_str = str(second_result.error)

                        if 'shared_link_already_exists' in second_error_str:
                            # Extract URL using regex - Crucial fallback for Dropbox API quirks
                            url_pattern = r"url='(https://[^']+)'"
                            url_match = re.search(url_pattern, second_error_str)

                            if url_match:
                                preview_url = url_match.group(1)
                                self.logger.info("Successfully extracted URL from error: %s", preview_url)
                            else:
                                self.logger.error("Could not extract URL from second error string")
                                self.logger.debug("Error string: %s", second_error_str[:500]) # Log first 500 chars
                        else:
                            self.logger.error("Unexpected error on second call (not shared_link_already_exists)")
                else:
                    self.logger.error("Unexpected error type on first call (not shared_link_already_exists)")

            # Final check
            if preview_url is None:
                self.logger.error("Failed to retrieve preview URL for %s", entry.path_lower)
            else:
                self.logger.info("Final preview_url: %s", preview_url)

            # 6. Get parent record ID
            parent_path = None
            parent_external_record_id = None
            if entry.path_display != '/':
                parent_path = get_parent_path_from_path(entry.path_lower)

            if parent_path:
                # For individual accounts, we just query the path directly.
                parent_metadata = await self.data_source.files_get_metadata(parent_path)

                if parent_metadata.success:
                    parent_external_record_id = parent_metadata.data.id

            # 7. Create the FileRecord object
            file_record = FileRecord(
                id=existing_record.id if existing_record else str(uuid.uuid4()),
                record_name=entry.name,
                record_type=record_type,
                record_group_type=RecordGroupType.DRIVE.value,
                external_record_group_id=record_group_id, # This is the User's personal drive ID
                external_record_id=entry.id,
                external_revision_id=entry.rev if is_file else None,
                version=0 if is_new else existing_record.version + 1,
                origin=OriginTypes.CONNECTOR.value,
                connector_name=self.connector_name,
                created_at=timestamp_ms,
                updated_at=timestamp_ms,
                source_created_at=timestamp_ms,
                source_updated_at=timestamp_ms,
                weburl=preview_url, # Calculated in step 5.5
                signed_url=signed_url, # Calculated in step 5
                parent_external_record_id=parent_external_record_id,
                size_in_bytes=entry.size if is_file else 0,
                is_file=is_file,
                extension=get_file_extension(entry.name) if is_file else None,
                path=entry.path_lower,
                mime_type=get_mimetype_enum_for_dropbox(entry),
                sha256_hash=entry.content_hash if is_file and hasattr(entry, 'content_hash') else None,
            )

            # 8. Handle Permissions
            new_permissions = []

            try:
                new_permissions.append(
                    Permission(
                        external_id=user_id,
                        email=user_email,
                        type=PermissionType.WRITE,
                        entity_type=EntityType.USER
                    )
                )

            except Exception as perm_ex:
                self.logger.warning(f"Could not fetch permissions for {entry.name}: {perm_ex}")
                # Safe Fallback to owner permission to prevent data invisibility
                new_permissions = [
                    Permission(
                        external_id=user_id,
                        email=user_email,
                        type=PermissionType.OWNER,
                        entity_type=EntityType.USER
                    )
                ]

            # 9. Compare permissions
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
                external_record_id=entry.id
            )

        except Exception as ex:
            self.logger.error(f"Error processing Dropbox entry {getattr(entry, 'id', entry.path_lower)}: {ex}", exc_info=True)
            return None

    async def _process_dropbox_items_generator(
        self,
        entries: List[Union[FileMetadata, FolderMetadata, DeletedMetadata]],
        user_id: str,
        user_email: str,
        record_group_id: str
    ) -> AsyncGenerator[Tuple[Optional[FileRecord], List[Permission], RecordUpdate], None]:
        """
        Process Dropbox entries and yield records with their permissions.
        Generator for non-blocking processing of large datasets.
        """
        for entry in entries:
            try:
                record_update = await self._process_dropbox_entry(
                    entry,
                    user_id,
                    user_email,
                    record_group_id
                )
                if record_update:
                    yield (record_update.record, record_update.new_permissions or [], record_update)
                await asyncio.sleep(0)
            except Exception as e:
                self.logger.error(f"Error processing item in generator: {e}", exc_info=True)
                continue

    async def _run_sync_with_cursor(self, user_id: str, user_email: str) -> None:
        """
        Synchronizes Dropbox files using cursor-based approach.
        """
        # 1. Setup (Let errors bubble up to run_sync if DB fails here)
        sync_point_key = generate_record_sync_point_key(
            RecordType.DRIVE.value,
            "personal_drive",
            user_id
        )

        sync_point = await self.dropbox_cursor_sync_point.read_sync_point(sync_point_key)
        cursor = sync_point.get('cursor')

        self.logger.info(f"Starting sync for {user_email}. Cursor exists: {bool(cursor)}")

        has_more = True

        while has_more:
            try:
                # 2. Rate Limiting (Standard)
                async with self.rate_limiter:
                    if cursor:
                        result = await self.data_source.files_list_folder_continue(cursor)
                    else:
                        result = await self.data_source.files_list_folder(
                            path="",
                            recursive=True
                        )

                if not result.success:
                    self.logger.error(f"Dropbox List Folder failed: {result.error}")
                    has_more = False
                    continue

                # 3. Process Batch
                entries = result.data.entries
                batch_records = []

                async for record, perms, update in self._process_dropbox_items_generator(
                    entries,
                    user_id,
                    user_email,
                    user_id
                ):
                    if update.is_deleted:
                        await self._handle_record_updates(update)
                    elif update.is_updated:
                        await self._handle_record_updates(update)
                    else:
                        batch_records.append((record, perms))

                        if len(batch_records) >= self.batch_size:
                            await self.data_entities_processor.on_new_records(batch_records)
                            batch_records = []
                            await asyncio.sleep(0)

                # Flush remaining
                if batch_records:
                    await self.data_entities_processor.on_new_records(batch_records)

                # 4. Update Cursor
                cursor = result.data.cursor
                has_more = result.data.has_more

                await self.dropbox_cursor_sync_point.update_sync_point(
                    sync_point_key,
                    {'cursor': cursor}
                )

            except ApiError as api_e:
                error_str = str(api_e)
                # Handle known "Stop Sync" errors gracefully
                if 'cursor' in error_str.lower() or 'reset' in error_str.lower():
                    self.logger.warning(f"Dropbox Cursor Invalid/Expired for {user_email}. Stopping sync.")
                    has_more = False
                elif 'path/not_found' in error_str.lower():
                    self.logger.warning(f"Path not found for {user_email}. Stopping sync.")
                    has_more = False
                else:
                    # Re-raise unexpected API errors so run_sync knows we failed
                    self.logger.error(f"Dropbox API Error during sync: {api_e}", exc_info=True)
                    raise api_e

            except Exception as loop_e:
                # Catch generic processing errors to prevent infinite loops
                self.logger.error(f"Error in sync loop: {loop_e}", exc_info=True)
                has_more = False

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

    async def run_sync(self) -> None:
        """
        Runs a full synchronization from the Dropbox individual account.
        Simplified workflow without team/group syncing.
        """
        try:
            self.logger.info("🚀 Starting Dropbox Individual Sync")

            # 1. Identify the User
            user_id, user_email = await self._get_current_user_info()
            self.logger.info(f"Identified current user: {user_email} ({user_id})")

            # 2. Create the 'Drive' (Record Group)
            display_name = f"Dropbox - {user_email}"
            await self._create_personal_record_group(
                user_id,
                user_email,
                display_name
            )
            self.logger.info(f"Ensured Record Group exists for: {display_name}")

            # 3. Start the Sync Engine
            self.logger.info("Starting file traversal...")
            await self._run_sync_with_cursor(user_id, user_email)

            self.logger.info("✅ Dropbox Individual Sync Completed Successfully")

        except Exception as ex:
            self.logger.error(f"❌ Error in Dropbox Individual connector run: {ex}", exc_info=True)
            raise

    async def _create_personal_record_group(self, user_id: str, user_email: str, display_name: str) -> RecordGroup:
        """
        Create a single record group for the individual user's root folder.
        Returns:
            RecordGroup for the user's personal Dropbox
        """
        record_group = RecordGroup(
            id=str(uuid.uuid4()),
            name=display_name,
            group_type=RecordGroupType.DRIVE.value,
            origin=OriginTypes.CONNECTOR.value,
            connector_name=self.connector_name,
            external_group_id=user_id,
            external_user_id=user_id,
            is_active=True
        )
        # Permissions: Owner
        permissions = [Permission(external_id=user_id, email=user_email, type=PermissionType.OWNER, entity_type=EntityType.USER)]

        await self.data_entities_processor.on_new_record_groups([(record_group, permissions)])
        return record_group

    async def run_incremental_sync(self) -> None:
        """Runs an incremental sync using the last known cursor."""
        try:
            self.logger.info("🔄 Starting Dropbox Individual incremental sync.")

            user_id, user_email = await self._get_current_user_info()
            await self._run_sync_with_cursor(user_id, user_email)

            self.logger.info("✅ Dropbox Individual incremental sync completed.")
        except Exception as e:
            self.logger.error(f"❌ Error in incremental sync: {e}", exc_info=True)
            raise

    async def get_signed_url(self, record: Record) -> Optional[str]:
        """
        Generate a temporary signed URL for downloading a file.
        Simplified for individual accounts.
        """
        if not self.data_source:
            return None
        try:
            # Dropbox uses path or file ID for temporary links. ID is more robust.
            target_identifier = record.external_record_id
            if not target_identifier:
                # Fallback: Use path if ID is somehow missing
                target_identifier = getattr(record, 'path', None)
            if not target_identifier:
                self.logger.warning(f"Cannot generate signed URL: Record {record.id} missing external_id")
                return None
            response = await self.data_source.files_get_temporary_link(path=target_identifier)

            return response.data.link
        except Exception as e:
            self.logger.error(f"Error creating signed URL for record {record.id}: {e}")
            return None

    async def stream_record(self, record: Record) -> StreamingResponse:
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
        if not self.data_source:
            return False
        try:
            await self.data_source.users_get_current_account()
            self.logger.info("Dropbox connection test successful.")
            return True
        except Exception as e:
            self.logger.error(f"Dropbox connection test failed: {e}", exc_info=True)
            return False

    def handle_webhook_notification(self, notification: Dict) -> None:
        """Handle webhook notifications by triggering incremental sync."""
        self.logger.info("Dropbox webhook received. Triggering incremental sync.")
        asyncio.create_task(self.run_incremental_sync())

    async def cleanup(self) -> None:
        self.logger.info("Cleaning up Dropbox Individual connector resources.")
        self.data_source = None

    @classmethod
    async def create_connector(
        cls,
        logger,
        data_store_provider: DataStoreProvider,
        config_service: ConfigurationService
    ) -> "BaseConnector":
        data_entities_processor = DataSourceEntitiesProcessor(
            logger, data_store_provider, config_service
        )
        await data_entities_processor.initialize()
        return DropboxIndividualConnector(
            logger,
            data_entities_processor,
            data_store_provider,
            config_service
        )
