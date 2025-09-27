import asyncio
import mimetypes
import uuid
from logging import Logger
from typing import AsyncGenerator, Dict, List, Optional, Tuple, Union
# from datetime import datetime
from datetime import datetime, timezone

from fastapi import HTTPException
from fastapi.responses import StreamingResponse

# Dropbox SDK specific types
from dropbox.files import DeletedMetadata, FileMetadata, FolderMetadata, ListFolderResult
from dropbox.sharing import AccessLevel

# Base connector and service imports
from app.config.configuration_service import ConfigurationService
from app.config.constants.arangodb import Connectors, OriginTypes
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
from app.connectors.core.base.data_store.arango_data_store import ArangoDataStore
from app.connectors.services.base_arango_service import BaseArangoService

# App-specific Dropbox client imports
from app.connectors.sources.dropbox.common.apps import DropboxApp
from app.sources.client.dropbox.dropbox_ import DropboxClient, DropboxResponse, DropboxTokenConfig
from app.config.constants.arangodb import CollectionNames
from app.sources.external.dropbox.dropbox_ import DropboxDataSource

# Model imports
from app.models.entities import (
    FileRecord,
    Record,
    RecordGroup,
    RecordGroupType,
    RecordType,
)
from app.models.permission import EntityType, Permission, PermissionType
from app.utils.streaming import stream_content

from app.models.entities import User, AppUser, AppUserGroup
from app.sources.external.dropbox.pretty_print import to_pretty_json
from aiolimiter import AsyncLimiter

from app.connectors.sources.microsoft.common.msgraph_client import (
    RecordUpdate,
)

from app.utils.time_conversion import get_epoch_timestamp_in_ms
from app.config.constants.arangodb import MimeTypes
from dropbox.team_log import EventCategory
# from dropbox.team import GroupSelector

# Add these helper functions at the top of the file
def get_parent_path_from_path(path: str) -> Optional[str]:
    """Extracts the parent path from a file/folder path."""
    if not path or path == "/" or "/" not in path.lstrip("/"):
        return None  # Root directory has no parent path in this context
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

@ConnectorBuilder("Dropbox")\
    .in_group("Cloud Storage")\
    .with_auth_type("OAUTH")\
    .with_description("Sync files and folders from Dropbox")\
    .with_categories(["Storage"])\
    .configure(lambda builder: builder
        .with_icon("/assets/icons/connectors/dropbox.svg")
        .with_realtime_support(True)
        .add_documentation_link(DocumentationLink(
            "Dropbox App Setup",
            "https://developers.dropbox.com/oauth-guide"
        ))
        .with_redirect_uri("connectors/oauth/callback/Dropbox", True)
        .with_oauth_urls(
            "https://www.dropbox.com/oauth2/authorize",
            "https://api.dropboxapi.com/oauth2/token",
            [
                "account_info.read",
                "files.content.read",
                "files.metadata.read",
                "file_requests.read",
                "groups.read",
                "members.read",
                "sharing.read",
                "team_data.member",
                "team_data.team_space",
                "team_info.read",
                "events.read"
            ]
        )
        .add_auth_field(CommonFields.client_id("Dropbox App Console"))
        .add_auth_field(CommonFields.client_secret("Dropbox App Console"))
        .with_webhook_config(True, ["file.added", "file.modified", "file.deleted"])
        .with_scheduled_config(True, 60)
        .add_sync_custom_field(CommonFields.batch_size_field())
        .add_filter_field(CommonFields.file_types_filter(), "static")
        .add_filter_field(CommonFields.folders_filter(),
                          "https://api.dropboxapi.com/2/files/list_folder")
    )\
    .build_decorator()
class DropboxConnector(BaseConnector):
    """
    Connector for synchronizing data from a Dropbox account.
    """

    current_user_id: Optional[str] = None

    def __init__(
        self,
        logger: Logger,
        data_entities_processor: DataSourceEntitiesProcessor,
        data_store_provider: DataStoreProvider,
        config_service: ConfigurationService,
    ) -> None:
        
        super().__init__(DropboxApp(), logger, data_entities_processor, data_store_provider, config_service)

        self.connector_name = Connectors.DROPBOX



        # Initialize sync point for tracking record changes
        def _create_sync_point(sync_data_point_type: SyncDataPointType) -> SyncPoint:
            return SyncPoint(
                connector_name=self.connector_name,
                org_id=self.data_entities_processor.org_id,
                sync_data_point_type=sync_data_point_type,
                data_store_provider=self.data_store_provider
            )
        # Initialize sync points
        self.dropbox_cursor_sync_point = _create_sync_point(SyncDataPointType.RECORDS)
        self.user_sync_point = _create_sync_point(SyncDataPointType.USERS)
        self.user_group_sync_point = _create_sync_point(SyncDataPointType.GROUPS)

        self.data_source: Optional[DropboxDataSource] = None
        self.batch_size = 100
        self.max_concurrent_batches = 5
        self.rate_limiter = AsyncLimiter(50, 1)  # 50 requests per second

    async def init(self) -> bool:
        """Initializes the Dropbox client using credentials from the config service."""
        config = await self.config_service.get_config(
            f"/services/connectors/dropbox/config" 
        )
        if not config:
            self.logger.error("Dropbox access token not found in configuration.")
            return False
        
        credentials_config = config.get("credentials")
        access_token = credentials_config.get("access_token")
        is_team = credentials_config.get("isTeam", True)

        print("!!!!!!!!!!!!! got access_token: ", access_token)
        print("!!!!!!!!!!!!! got is_team: ", is_team)

        try:
            config = DropboxTokenConfig(token=access_token)
            client = await DropboxClient.build_with_config(config, is_team=is_team)
            self.data_source = DropboxDataSource(client)
            self.logger.info("Dropbox client initialized successfully.")
            return True
        except Exception as e:
            self.logger.error(f"Failed to initialize Dropbox client: {e}", exc_info=True)
            return False

    # Place these new methods inside the DropboxConnector class

    def _permissions_equal(self, old_perms: List[Permission], new_perms: List[Permission]) -> bool:
        """
        Compare two lists of permissions to detect changes.
        """
        if not old_perms and not new_perms:
            return True
        if not old_perms or not new_perms: # Catches one list being empty and the other not
            return False
        if len(old_perms) != len(new_perms):
            return False

        # Create sets of permission tuples for comparison
        old_set = {(p.external_id, p.type, p.entity_type) for p in old_perms}
        new_set = {(p.external_id, p.type, p.entity_type) for p in new_perms}

        return old_set == new_set

    async def _process_dropbox_entry(
        self, entry: Union[FileMetadata, FolderMetadata, DeletedMetadata],
         user_id: str, user_email: str,
          record_group_id: str,
          is_person_folder: bool
    ) -> Optional[RecordUpdate]:
        """
        Process a single Dropbox entry and detect changes.

        Returns:
            RecordUpdate object containing the record and change information.
        """
        try:
            # 1. Handle Deleted Items (Deletion from db not implemented yet)
            if isinstance(entry, DeletedMetadata):
                pass
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
                print("!!!!!!!!!!!! got existing record !!!!!!!!!!!!!!: ", existing_record.record_name  )
                if existing_record.record_name != entry.name:
                    metadata_changed = True
                    is_updated = True
                if is_file and existing_record.external_revision_id != entry.rev:
                    print("!!!!!!!!!!!! content changed !!!!!!!!!!!!!!")
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
                    entry.path_lower,
                    team_member_id=user_id,
                    team_folder_id=record_group_id if not is_person_folder else None
                )
                if temp_link_result.success:
                    signed_url = temp_link_result.data.link
            
            # 6. Get parent record ID
            parent_path = None
            parent_external_record_id = None
            if entry.path_display != '/':
                parent_path = get_parent_path_from_path(entry.path_lower)
                print("!!!!!!!!!!!! got parent path !!!!!!!!!!!!!!: ", parent_path)
            parent_metadata = None
            if parent_path:
                parent_metadata = await self.data_source.files_get_metadata(
                    parent_path,
                    team_member_id=user_id,
                    team_folder_id=record_group_id if not is_person_folder else None,
                )
                if parent_metadata.success:
                    parent_external_record_id = parent_metadata.data.id

            file_record = FileRecord(
                id=existing_record.id if existing_record else str(uuid.uuid4()),
                record_name=entry.name,
                record_type=record_type,
                record_group_type=RecordGroupType.DRIVE.value,
                external_record_group_id=record_group_id, # Use the passed-in folder_id or user_id
                external_record_id=entry.id,
                external_revision_id=entry.rev if is_file else None,
                version=0 if is_new else existing_record.version + 1,
                origin=OriginTypes.CONNECTOR.value,
                connector_name=self.connector_name,
                created_at=timestamp_ms,
                updated_at=timestamp_ms,
                source_created_at=timestamp_ms,
                source_updated_at=timestamp_ms,
                weburl=f"https://www.dropbox.com/home{entry.path_display}",
                signed_url=signed_url,
                parent_external_record_id=parent_external_record_id,
                size_in_bytes=entry.size if is_file else 0,
                is_file=is_file,
                extension=get_file_extension(entry.name) if is_file else None,
                path=entry.path_lower,
                mime_type=get_mimetype_enum_for_dropbox(entry),
                sha256_hash=entry.content_hash if is_file and hasattr(entry, 'content_hash') else None,
            )

            # async with self.data_store_provider.transaction() as tx_store:
            #     user = await tx_store.get_user_by_id(user_id=user_id)
            # 5. Handle Permissions
            new_permissions = [
                Permission(external_id=user_id, email=user_email, type=PermissionType.WRITE, entity_type=EntityType.USER)
            ]
            
            old_permissions = []
            # if existing_record:
            #     # NOTE: This assumes your arango_service has a method to fetch permissions for a record.
            #     old_permissions = await self.arango_service.get_permissions_for_record(existing_record.id) or []
            
            # if not self._permissions_equal(old_permissions, new_permissions):
            #     permissions_changed = True
            #     is_updated = True

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
        self, entries: List[Union[FileMetadata, FolderMetadata, DeletedMetadata]], user_id: str, user_email: str, record_group_id: str, is_person_folder: bool
    ) -> AsyncGenerator[Tuple[Optional[FileRecord], List[Permission], RecordUpdate], None]:
        """
        Process Dropbox entries and yield records with their permissions.
        This allows non-blocking processing of large datasets.
        """
        for entry in entries:
            try:
                record_update = await self._process_dropbox_entry(entry, user_id, user_email, record_group_id, is_person_folder)
                if record_update:
                    yield (record_update.record, record_update.new_permissions or [], record_update)
                await asyncio.sleep(0)
            except Exception as e:
                self.logger.error(f"Error processing item in generator: {e}", exc_info=True)
                continue

    async def _process_entry(
        self, entry: Union[FileMetadata, FolderMetadata, DeletedMetadata]
    ) -> Optional[Tuple[FileRecord, List[Permission]]]:
        """Processes a single entry from Dropbox and converts it to internal models."""
        if isinstance(entry, DeletedMetadata):
            # Dropbox API for deleted items doesn't provide an ID, only path.
            # A robust implementation would need to look up the record by its path.
            self.logger.info(f"Item '{entry.name}' at path '{entry.path_lower}' was deleted. Deletion requires path-based lookup.")
            # Example: await self.data_entities_processor.on_record_deleted(record_path=entry.path_lower)
            return None

        is_file = isinstance(entry, FileMetadata)
        record_type = RecordType.FILE if is_file else RecordType.FOLDER
        mime_type, _ = mimetypes.guess_type(entry.name) if is_file else (None, None)

        file_record = FileRecord(
            id=str(uuid.uuid4()),
            record_name=entry.name,
            record_type=record_type,
            record_group_type=RecordGroupType.DRIVE.value,
            external_record_id=entry.id,
            external_revision_id=entry.rev if is_file else None,
            origin=OriginTypes.CONNECTOR.value,
            connector_name=self.connector_name,
            updated_at=int(entry.server_modified.timestamp() * 1000) if is_file else None,
            source_updated_at=int(entry.server_modified.timestamp() * 1000) if is_file else None,
            web_url=f"https://www.dropbox.com/home{entry.path_display}",
            parent_external_record_id=get_parent_path_from_path(entry.path_lower),
            size_in_bytes=entry.size if is_file else 0,
            is_file=is_file,
            extension=get_file_extension(entry.name) if is_file else None,
            path=entry.path_lower,
            mime_type=mime_type,
            sha256_hash=entry.content_hash if is_file and hasattr(entry, 'content_hash') else None,
        )

        permissions = await self._get_permissions(entry)
        return file_record, permissions

    async def _get_permissions(
        self, entry: Union[FileMetadata, FolderMetadata]
    ) -> List[Permission]:
        """Fetches and converts permissions for a Dropbox entry."""
        if not self.data_source:
            return []

        permissions = []
        try:
            members_result = None
            if isinstance(entry, FileMetadata):
                members_result = await self.data_source.sharing_list_file_members(file=entry.id)
            elif hasattr(entry, 'shared_folder_id') and entry.shared_folder_id:
                members_result = await self.data_source.sharing_list_folder_members(shared_folder_id=entry.shared_folder_id)

            if not members_result:
                return []

            all_members = members_result.data.users + members_result.data.groups

            for member in all_members:
                # Map Dropbox AccessLevel to our internal PermissionType
                access_type = getattr(member, 'access_type', None)
                perm_type = PermissionType.WRITE if access_type in (AccessLevel.owner, AccessLevel.editor) else PermissionType.READ
                
                member_info = getattr(member, 'user', getattr(member, 'group', None))
                if not member_info: continue

                entity_type = EntityType.USER if hasattr(member_info, 'account_id') else EntityType.GROUP
                external_id = member_info.account_id if entity_type == EntityType.USER else member_info.group_id
                email = getattr(member_info, 'email', None)

                permissions.append(
                    Permission(external_id=external_id, email=email, type=perm_type, entity_type=entity_type)
                )
        except Exception as e:
            # Not all items are shared, so API calls can fail. This is expected.
            self.logger.debug(f"Could not fetch permissions for '{entry.name}': {e}")
        return permissions

    async def _sync_from_source(self, path: str = "", cursor: Optional[str] = None):
        """Helper to sync a folder, handling pagination and cursor management."""
        if not self.data_source:
            raise ConnectionError("Dropbox connector is not initialized.")

        has_more = True
        batch_records = []
        sync_point_key = generate_record_sync_point_key(RecordType.DRIVE.value, "root")

        while has_more:
            try:
                if cursor:
                    response = await self.data_source.files_list_folder_continue(cursor=cursor)
                else:
                    response = await self.data_source.files_list_folder(path=path, recursive=True, include_deleted=True)
                
                result: ListFolderResult = response.data

                for entry in result.entries:
                    processed_data = await self._process_entry(entry)
                    if processed_data:
                        batch_records.append(processed_data)

                    if len(batch_records) >= self.batch_size:
                        await self.data_entities_processor.on_new_records(batch_records)
                        batch_records = []
                
                cursor = result.data.cursor
                has_more = result.data.has_more
                await self.record_sync_point.update_sync_point(sync_point_key, {'cursor': cursor})

            except Exception as e:
                self.logger.error(f"Error during Dropbox folder sync: {e}", exc_info=True)
                has_more = False
        
        if batch_records:
            await self.data_entities_processor.on_new_records(batch_records)

    async def _process_users_in_batches(self, users: List[User]):
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
                    self._run_sync_with_yield(user.source_user_id, user.email)
                    
                    for user in batch
                ]

                
                print("Going to run sync for these users:", batch);

                await asyncio.gather(*sync_tasks, return_exceptions=True)

                # Small delay between batches to prevent overwhelming the API
                await asyncio.sleep(1)

            self.logger.info("Completed processing all user batches")

        except Exception as e:
            self.logger.error(f"Error processing users in batches: {e}")
            raise
    
    
    
    async def _run_sync_with_yield(self, user_id: str, user_email: str) -> None:
        """
        Synchronizes Dropbox files for a given user using the cursor-based approach.

        This function first lists all shared folders, then loops through the
        personal folder (root) and each shared folder, running a separate sync
        operation with a unique cursor for each.

        Args:
            user_id: The Dropbox team member ID of the user to sync.
            user_email: The email of the user to sync.
        """
        try:
            self.logger.info(f"Starting Dropbox sync with yield for user {user_email}")

            # List all shared folders the user has access to
            shared_folders = await self.data_source.sharing_list_folders(team_member_id=user_id)
            
            # Create a list of folders to sync: None for personal, plus all shared folder IDs
            folders_to_sync = [None]
            if shared_folders.success:
                self.logger.info(f"Found {len(shared_folders.data.entries)} shared folders for user {user_email}")
                for folder in shared_folders.data.entries:
                    self.logger.info(f"  - Will sync Folder: {folder.name}, ID: {folder.shared_folder_id}")
                    folders_to_sync.append(folder.shared_folder_id)
            else:
                self.logger.warning(f"Could not list shared folders for user {user_email}: {shared_folders.error}")


            # Loop through each folder (personal + shared) and run a separate sync
            for folder_id in folders_to_sync:
                
                # 1. Determine sync parameters for this specific folder
                if folder_id is None:
                    # This is the user's personal root folder
                    sync_context_id = user_id
                    sync_group = "users"
                    sync_log_name = f"personal folder for user {user_email}"
                    current_record_group_id = user_id
                else:
                    # This is a shared folder
                    sync_context_id = f"{user_id}_{folder_id}" # Safer key than using '/'
                    sync_group = "shared_folders"
                    sync_log_name = f"shared folder {folder_id} for user {user_email}"
                    current_record_group_id = folder_id

                self.logger.info(f"Starting sync loop for: {sync_log_name}")

                # 2. Get current sync state from the database *for this folder*
                sync_point_key = generate_record_sync_point_key(RecordType.DRIVE.value, sync_group, sync_context_id)
                sync_point = await self.dropbox_cursor_sync_point.read_sync_point(sync_point_key)
                cursor = sync_point.get('cursor')

                self.logger.info(f"Sync point key: {sync_point_key}")
                self.logger.info(f"Retrieved sync point: {sync_point}")
                self.logger.info(f"Cursor value: {cursor}")

                # Reset batching and state for each folder sync
                batch_records = []
                batch_count = 0
                has_more = True

                print("!!!!!!!!!!!!! folders to sync: ", folders_to_sync)
                if folder_id:
                    print("!!!!!!!!!!!!! got folder id: ", folder_id, len(folders_to_sync))
                else:
                    print("!!!!!!!!!!!!! doing personal folder !")
                while has_more:
                    # 3. Fetch changes from Dropbox
                    print("!!!!!!!!! going to call api")
                    try:
                        async with self.rate_limiter:
                            if cursor:
                                self.logger.info(f"[{sync_log_name}] Calling files_list_folder_continue...")
                                result = await self.data_source.files_list_folder_continue(
                                    cursor,
                                    team_member_id=user_id,
                                    team_folder_id=folder_id,
                                )
                                
                            else:
                                print("!!!!!!!!! going to call api 1")
                                # This is the first sync for this folder
                                # self.logger.info(f"[{sync_log_name}] Calling files_list_folder for path: ")
                                try:
                                    result = await self.data_source.files_list_folder(
                                        path="", 
                                        team_member_id=user_id,
                                        team_folder_id=folder_id,
                                        recursive=True
                                    )
                                    print("!!!!!!!!! called api 1")
                                except Exception as e:
                                    print("error in api call:", e)
                        if not result.success:
                            self.logger.error(f"[{sync_log_name}] Dropbox API call failed: {result.error}")
                            # Stop syncing this folder on API error
                            has_more = False
                            continue # Skip to the next 'while' iteration (which will exit)
                        
                        print(result)
                        self.logger.info(f"[{sync_log_name}] Got {len(result.data.entries)} entries. Has_more: {result.data.has_more}")
                        entries = result.data.entries

                        # 4. Process the entries from the current page
                        async for file_record, permissions, record_update in self._process_dropbox_items_generator(
                            entries, user_id, user_email, current_record_group_id, folder_id is None
                        ):
                            if record_update.is_deleted:
                                await self._handle_record_updates(record_update)
                                continue
                            
                            if record_update.is_updated:
                                await self._handle_record_updates(record_update)
                                continue
                            
                            if file_record:
                                batch_records.append((file_record, permissions))
                                batch_count += 1
                                    
                                if batch_count >= self.batch_size:
                                    self.logger.info(f"[{sync_log_name}] Processing batch of {batch_count} records.")
                                    await self.data_entities_processor.on_new_records(batch_records)
                                    batch_records = []
                                    batch_count = 0
                                    await asyncio.sleep(0.1)

                        # Process any remaining records in the batch from the last page
                        if batch_records:
                            self.logger.info(f"[{sync_log_name}] Processing final batch of {len(batch_records)} records.")
                            await self.data_entities_processor.on_new_records(batch_records)
                            batch_records = []
                            batch_count = 0

                        # 5. Update the sync state for the next iteration
                        cursor = result.data.cursor
                        self.logger.info(f"[{sync_log_name}] Storing new cursor for key {sync_point_key}")
                        await self.dropbox_cursor_sync_point.update_sync_point(
                            sync_point_key,
                            sync_point_data={"cursor": cursor}
                        )

                        has_more = result.data.has_more
                    
                    except ApiError as api_ex:
                        self.logger.error(f"Dropbox API Error during sync for {sync_log_name}: {api_ex}")
                        # If path not found, stop this folder's sync and continue to the next
                        if 'path/not_found' in str(api_ex):
                            self.logger.warning(f"[{sync_log_name}] Path not found. Stopping sync for this folder.")
                            has_more = False # Stop this 'while' loop
                        else:
                            raise # Re-raise other critical API errors
                    except Exception as loop_ex:
                        self.logger.error(f"Error in 'while has_more' loop for {sync_log_name}: {loop_ex}", exc_info=True)
                        has_more = False # Stop this 'while' loop to be safe

                self.logger.info(f"Completed sync loop for: {sync_log_name}")

            self.logger.info(f"Completed all Dropbox sync loops for user {user_id}")

        except ApiError as ex:
            # Error during initial shared folder list
            self.logger.error(f"Dropbox API Error during sync setup for user {user_id}: {ex}")
            raise
        except Exception as ex:
            self.logger.error(f"Unhandled error in Dropbox sync for user {user_id}: {ex}", exc_info=True)
            raise
    
    async def _handle_record_updates(self, record_update: RecordUpdate) -> None:
        """
        Handle different types of record updates (new, updated, deleted).
        """
        try:
            if record_update.is_deleted:
                await self.data_entities_processor.on_record_deleted(
                    record_id=record_update.external_record_id
                )
                print("should be deleted by now 2")
            elif record_update.is_new:
                self.logger.info(f"New record detected: {record_update.record.record_name}")
            elif record_update.is_updated:
                async with self.data_store_provider.transaction() as tx_store:
                    if record_update.content_changed:
                        self.logger.info(f"Content changed for record: {record_update.record.record_name}")
                        await self.data_entities_processor.on_record_content_update(record_update.record, tx_store)
                    if record_update.metadata_changed:
                        self.logger.info(f"Metadata changed for record: {record_update.record.record_name}")
                        await self.data_entities_processor.on_record_metadata_update(record_update.record, tx_store)
                    if record_update.permissions_changed:
                        self.logger.info(f"Permissions changed for record: {record_update.record.record_name}")
                        await self.data_entities_processor.on_updated_record_permissions(
                            record_update.record,
                            record_update.new_permissions,
                            tx_store
                        )
        except Exception as e:
            self.logger.error(f"Error handling record updates: {e}", exc_info=True)

    def get_app_users(self, users: DropboxResponse):
        app_users: List[AppUser] = []
        for member in users.data.members:
            profile = member.profile
            app_users.append(
                AppUser(
                    app_name="DROPBOX",
                    source_user_id=profile.team_member_id,
                    full_name=profile.name.display_name,
                    email=profile.email,
                    is_active=(profile.status._tag == "active"),
                    title=member.role._tag,
                )
            )
        return app_users

    async def run_sync(self) -> None:
        """Runs a full synchronization from the Dropbox account root."""
        try:
            self.logger.info("Starting Dropbox full sync.")
            # self.logger.info("Code will excecute here!")

            # step 1: fetch and sync all users / use continue here
            self.logger.info("Syncing users...")
            users = await self.data_source.team_members_list()
            app_users = self.get_app_users(users)

            await self.data_entities_processor.on_new_app_users(app_users)

            # Step 2: fetch and sync all user groups
            #for later
            # self.logger.info("Syncing user groups...")
            # await self._sync_user_groups()

            group_sync_key = generate_record_sync_point_key("user_group_events", "team_events", "global")
            group_sync_point = await self.dropbox_cursor_sync_point.read_sync_point(group_sync_key)
            
            if not group_sync_point.get('cursor'):
                self.logger.info("Running a FULL sync for user groups...")
                await self._sync_user_groups()
                
                # Initialize cursor after full sync by getting current state
                try:
                    response = await self.data_source.team_log_get_events(category=EventCategory.groups, limit=1)
                    if response.success and response.data.cursor:
                        await self.dropbox_cursor_sync_point.update_sync_point(
                            group_sync_key, {"cursor": response.data.cursor}
                        )
                        self.logger.info("Initialized cursor for future incremental group syncs")
                except Exception as e:
                    self.logger.warning(f"Could not initialize group sync cursor: {e}")
            else:
                self.logger.info("Running an INCREMENTAL sync for user groups...")
                await self._sync_group_changes_with_cursor()

            #Step 3: List all shared folders within a team and create record groups / use continue here
            self.logger.info("Syncing record groups...")
            await self.sync_record_groups(app_users)
            #Step 3.5: Create all personal folder record groups
            await self.sync_personal_record_groups(app_users)

            

            # Step 4: fetch and sync all user drives
            self.logger.info("Syncing User Drives")
            await self._process_users_in_batches(app_users)
            
            self.logger.info("Dropbox full sync completed.")
        except Exception as ex:
            self.logger.error(f"❌ Error in DropBox connector run: {ex}")
            raise
    
    async def _sync_user_groups(self) -> None:
        """
        Syncs all Dropbox groups and their members, collecting them into a 
        single batch before sending to the processor.
        """
        try:
            self.logger.info("Starting Dropbox user group synchronization")

            # --- 1. Get all groups, with pagination ---
            all_groups_list = []
            try:
                groups_response = await self.data_source.team_groups_list()
                if not groups_response.success:
                    raise Exception(f"Error fetching groups list: {groups_response.error}")

                all_groups_list.extend(groups_response.data.groups)
                cursor = groups_response.data.cursor
                has_more = groups_response.data.has_more

                while has_more:
                    self.logger.info("Fetching more groups...")
                    groups_response = await self.data_source.team_groups_list_continue(cursor)
                    if not groups_response.success:
                        self.logger.error(f"Error fetching more groups: {groups_response.error}")
                        break  # Stop pagination on error
                    all_groups_list.extend(groups_response.data.groups)
                    cursor = groups_response.data.cursor
                    has_more = groups_response.data.has_more
            
            except Exception as e:
                self.logger.error(f"❌ Failed to fetch full group list: {e}", exc_info=True)
                raise  # Stop the sync if we can't get the groups

            self.logger.info(f"Found {len(all_groups_list)} total groups. Now processing members.")

            # --- 2. Define permission mapping (similar to record_groups) ---
            # Dropbox group members are either 'owner' or 'member'
            dropbox_group_to_permission_type = {
                'owner': PermissionType.OWNER,
                'member': PermissionType.WRITE,  # 'member' implies edit access to the group itself
            }

            # This will hold our final list of tuples: List[Tuple[AppUserGroup, List[Permission]]]
            user_groups_batch = []

            # --- 3. Loop through all groups to build the batch (NO processor calls inside loop) ---
            for group in all_groups_list:
                try:
                    processor_group, member_permissions = self._create_user_group_with_permissions(
                        group.group_id, group.group_name, all_members
                    )

                    # --- 3d. Add the tuple to our batch list ---
                    user_groups_batch.append((processor_group, member_permissions))

                except Exception as e:
                    self.logger.error(f"❌ Failed to process group {group.group_name}: {e}", exc_info=True)
                    continue  # Skip this group and move to the next

            # --- 4. Send the ENTIRE batch to the processor ONCE (outside the loop) ---
            if user_groups_batch:
                self.logger.info(f"Submitting {len(user_groups_batch)} user groups to the processor...")
                await self.data_entities_processor.on_new_user_groups(user_groups_batch)
                self.logger.info("Successfully submitted batch to on_new_user_groups.")
            else:
                self.logger.info("No user groups found or processed.")

            self.logger.info(f"Completed Dropbox user group synchronization.")

        except Exception as e:
            self.logger.error(f"❌ Fatal error in _sync_dropbox_groups: {e}", exc_info=True)
            raise
    
    async def _sync_group_changes_with_cursor(self) -> None:
        """
        Syncs user group changes incrementally using the team event log cursor.
        """
        try:
            print("!!!!!!!!!!!!!!!!!!!!!! running incremental sync for user groups")
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

            while has_more:
                try:
                    # 3. Fetch the latest events from the Dropbox audit log
                    async with self.rate_limiter:
                        response = await self.data_source.team_log_get_events_continue(cursor)

                    if not response.success:
                        self.logger.error(f"⚠️ Error fetching team event log: {response.error}")
                        break

                    events = response.data.events
                    self.logger.info(f"Processing {len(events)} new group-related events.")

                    # 4. Process each event individually
                    for event in events:
                        try:
                            await self._process_group_event(event)
                            events_processed += 1
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
                    sync_point_data={"cursor": latest_cursor_to_save}
                )
            
            self.logger.info(f"Incremental group sync completed. Processed {events_processed} events.")

        except Exception as e:
            self.logger.error(f"⚠️ Fatal error in incremental group sync: {e}", exc_info=True)
            raise

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
                print("!!!!!!!!!! got group create")
                await self._handle_group_created_event(event)
            elif event_type == "group_delete":
                print("!!!!!!!!!!! got group delete")
                await self._handle_group_deleted_event(event)
            elif event_type in ["group_add_member", "group_remove_member"]:
                print("!!!!!!!!!!! got group membership")
                await self._handle_group_membership_event(event, event_type)
            elif event_type in ["group_rename"]:
                print("!!!!!!!!!!! got group rename")
                await self._handle_group_renamed_event(event)
            elif event_type in ["group_change_member_role"]:
                print("!!!!!!!!!!! got group change member role")
                await self._handle_group_change_member_role_event(event)
                
            else:
                self.logger.debug(f"Ignoring event type: {event_type}")

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
                connector_name=self.connector_name
            )
        
        elif event_type == "group_remove_member":
            self.logger.info(f"Removing member '{member_name}' ({member_email}) from group '{group_name}' ({group_id})")
            
            await self.data_entities_processor.on_user_group_member_removed(
                external_group_id=group_id,
                user_email=member_email,
                connector_name=self.connector_name
            )

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
                print(f"Extracted deleted group: {group_name} ({group_id})")
                break
        
        # Validate we have required information
        if not group_id:
            self.logger.warning("Could not extract group_id from group_delete event")
            return
        
        self.logger.info(f"Deleting group {group_name} ({group_id})")
        
        await self.data_entities_processor.on_user_group_deleted(
            external_group_id=group_id,
            connector_name=self.connector_name
        )

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

    # Update _process_group_event method to uncomment this line:
    # if event_type == "group_create":
    #     await self._handle_group_created_event(event)

    async def _fetch_group_members(self, group_id: str, group_name: str) -> list:
        """
        Fetch all members for a group with pagination.
        Extracted from _sync_user_groups section 3a for reusability.
        """
        all_members = []

        members_response = await self.data_source.team_groups_members_list(group=group_id)
        if not members_response.success:
            raise Exception(f"Error fetching members for group {group_name}: {members_response.error}")

        all_members.extend(members_response.data.members)
        member_cursor = members_response.data.cursor
        member_has_more = members_response.data.has_more
        
        while member_has_more:
            self.logger.debug(f"Fetching more members for {group_name}...")
            members_response = await self.data_source.team_groups_members_list_continue(member_cursor)
            
            if not members_response.success:
                self.logger.error(f"Error during member pagination for {group_name}: {members_response.error}")
                break
                
            all_members.extend(members_response.data.members)
            member_cursor = members_response.data.cursor
            member_has_more = members_response.data.has_more
        
        return all_members

    def _create_user_group_with_permissions(self, group_id: str, group_name: str, all_members: list) -> tuple:
        """
        Create AppUserGroup and permissions list from group members.
        Extracted from _sync_user_groups sections 3b-3c for reusability.
        """
        # Permission mapping (from _sync_user_groups)
        dropbox_group_to_permission_type = {
            'owner': PermissionType.OWNER,
            'member': PermissionType.WRITE,
        }

        # Create the AppUserGroup object (from _sync_user_groups section 3b)
        processor_group = AppUserGroup(
            app_name=self.connector_name,
            source_user_group_id=group_id,
            name=group_name,
            org_id=self.data_entities_processor.org_id
        )

        # Create permissions list (from _sync_user_groups section 3c)
        member_permissions = []
        for member in all_members:
            access_level_tag = member.access_type._tag
            
            # Map the tag to our PermissionType enum
            permission_type = dropbox_group_to_permission_type.get(access_level_tag, PermissionType.READ)
            
            user_permission = Permission(
                external_id=member.profile.team_member_id,
                email=member.profile.email,
                type=permission_type,
                entity_type=EntityType.USER
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
        
        # Extract old and new names from event details
        # old_name = None
        # if hasattr(event.details, 'previous_value'):
        #     old_name = event.details.previous_value

        details_obj = event.details
        
        # Log the structure for debugging
        self.logger.debug(f"Event details type: {type(details_obj)}")
        self.logger.debug(f"Event details: {details_obj}")
        
        # Try different ways to access the GroupRenameDetails
        if hasattr(details_obj, 'get_group_rename_details'):
            group_rename_details = details_obj.get_group_rename_details()
            old_name = group_rename_details.previous_value
            new_name = group_rename_details.new_value
            self.logger.debug("Used get_group_rename_details() method: old_name=%s, new_name=%s", old_name, new_name)

        if not group_id or not new_group_name:
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
        """
        Update the name of an existing group in the database.
        """
        try:
            async with self.data_store_provider.transaction() as tx_store:
                # 1. Look up the existing group by external ID
                existing_group = await tx_store.get_user_group_by_external_id(
                    connector_name=self.connector_name,
                    external_id=group_id
                )
                
                if not existing_group:
                    self.logger.warning(
                        f"Cannot rename group: Group with external ID {group_id} not found in database"
                    )
                    return
                
                # 2. Update the group name and timestamp
                existing_group.name = new_name
                existing_group.updated_at = get_epoch_timestamp_in_ms()
                
                # 3. Upsert the updated group
                await tx_store.batch_upsert_user_groups([existing_group])
                
                self.logger.info(
                    f"Successfully renamed group {group_id} from '{old_name}' to '{new_name}' "
                    f"(internal_id: {existing_group.id})"
                )
                
        except Exception as e:
            self.logger.error(f"Failed to update group name for {group_id}: {e}", exc_info=True)
            raise
    
    async def _handle_group_change_member_role_event(self, event) -> None:
        """Handle group_change_member_role events from Dropbox audit log."""
        # Extract group and user info from event participants
        group_id = None
        group_name = None
        user_email = None
        user_team_member_id = None
        
        for participant in event.participants:
            if participant.is_group():
                group_info = participant.get_group()
                group_id = group_info.group_id
                group_name = group_info.display_name
            elif participant.is_user():
                user_info = participant.get_user()
                user_email = user_info.email
                user_team_member_id = user_info.team_member_id
        
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
        """
        Update a user's permission level within a group.
        """
        try:
            async with self.data_store_provider.transaction() as tx_store:
                # 1. Look up the user by email
                user = await tx_store.get_user_by_email(user_email)
                if not user:
                    self.logger.warning(
                        f"Cannot update group permission: User with email {user_email} not found"
                    )
                    return False
                
                # 2. Look up the group by external ID
                user_group = await tx_store.get_user_group_by_external_id(
                    connector_name=self.connector_name,
                    external_id=group_id
                )
                if not user_group:
                    self.logger.warning(
                        f"Cannot update group permission: Group with external ID {group_id} not found"
                    )
                    return False
                
                # 3. Construct edge keys
                from_key = f"{CollectionNames.USERS.value}/{user.id}"
                to_key = f"{CollectionNames.GROUPS.value}/{user_group.id}"
                
                # 4. Check if permission edge exists
                existing_edge = await tx_store.get_edge(from_key, to_key, CollectionNames.PERMISSION.value)
                if not existing_edge:
                    self.logger.warning(
                        f"No existing permission found between user {user_email} and group {user_group.name}. "
                        f"Creating new permission with type {new_permission_type}"
                    )
                    # Create new permission edge
                    permission = Permission(
                        external_id=user.id,
                        email=user_email,
                        type=new_permission_type,
                        entity_type=EntityType.USER
                    )
                    permission_edge = permission.to_arango_permission(from_key, to_key)
                    await tx_store.batch_create_edges([permission_edge], CollectionNames.PERMISSION.value)
                    return True
                
                # 5. Check if permission type has changed
                current_permission_type = existing_edge.get('permissionType')
                if current_permission_type == new_permission_type.value:
                    self.logger.info(
                        f"Permission type already correct for {user_email} in group {user_group.name}: {new_permission_type}"
                    )
                    return True
                
                # 6. Update the permission by deleting old edge and creating new one
                self.logger.info(
                    f"Updating permission for {user_email} in group {user_group.name} "
                    f"from {current_permission_type} to {new_permission_type}"
                )
                
                # Delete old edge
                await tx_store.delete_edge(from_key, to_key, CollectionNames.PERMISSION.value)
                
                # Create new edge with updated permission
                permission = Permission(
                    external_id=user.id,
                    email=user_email,
                    type=new_permission_type,
                    entity_type=EntityType.USER
                )
                permission_edge = permission.to_arango_permission(from_key, to_key)
                await tx_store.batch_create_edges([permission_edge], CollectionNames.PERMISSION.value)
                
                return True
                
        except Exception as e:
            self.logger.error(
                f"Failed to update user group permission for {user_email} in group {group_id}: {e}", 
                exc_info=True
            )
            return False

    async def sync_record_groups(self, users: List[AppUser]):
        # Find a team admin user
        team_admin_user = None
        for user in users:
            if user.title == "team_admin":
                team_admin_user = user
                break
        
        if not team_admin_user:
            self.logger.error("No team admin user found. Cannot sync record groups.")
            return
        
        self.logger.info(f"Using team admin user: {team_admin_user.email} (ID: {team_admin_user.source_user_id})")
        
        team_folders = await self.data_source.team_team_folder_list()
        record_groups = []

        dropbox_to_permission_type = {
            'owner': PermissionType.OWNER,
            'editor': PermissionType.WRITE,
            'viewer': PermissionType.READ,
        }

        for folder in team_folders.data.team_folders:
            team_folder_members = await self.data_source.sharing_list_folder_members(
                shared_folder_id=folder.team_folder_id,
                team_member_id=team_admin_user.source_user_id,
                as_admin=True
            )

            if folder.status._tag != "active":
                continue
                
            record_group = RecordGroup(
                name=folder.name,
                org_id=self.data_entities_processor.org_id,
                external_group_id=folder.team_folder_id,
                description="Team Folder",
                connector_name=Connectors.DROPBOX,
                group_type=RecordGroupType.DRIVE,
            )

            # --- Create permissions list from folder members ---
            permissions_list = []
            
            if team_folder_members.success:
                # Handle USER permissions
                if team_folder_members.data.users:
                    for user_info in team_folder_members.data.users:
                        # Get the permission type string (e.g., 'editor')
                        access_level_tag = user_info.access_type._tag
                        
                        # Map it to our internal PermissionType enum
                        permission_type = dropbox_to_permission_type.get(access_level_tag, PermissionType.READ)
                        
                        # Create the permission object
                        user_permission = Permission(
                            email=user_info.user.email,
                            type=permission_type,
                            entity_type=EntityType.USER
                        )
                        permissions_list.append(user_permission)
                
                # Handle GROUP permissions
                if team_folder_members.data.groups:
                    for group_info in team_folder_members.data.groups:
                        # Get the permission type string (e.g., 'editor')
                        access_level_tag = group_info.access_type._tag
                        
                        # Map it to our internal PermissionType enum
                        permission_type = dropbox_to_permission_type.get(access_level_tag, PermissionType.READ)
                        
                        # Create the permission object for group
                        group_permission = Permission(
                            external_id=group_info.group.group_id,  # Use group_id as external_id
                            type=permission_type,
                            entity_type=EntityType.GROUP
                            # Note: groups don't have email, so we use external_id instead
                        )
                        permissions_list.append(group_permission)
            # ---
            
            # Append the record group and the list of user/group permissions
            record_groups.append((record_group, permissions_list))
        
        await self.data_entities_processor.on_new_record_groups(record_groups)

    async def sync_personal_record_groups(self, users: List[AppUser]):
        record_groups = []
        for user in users:
            record_group = RecordGroup(
                name=user.full_name,
                org_id=self.data_entities_processor.org_id,
                description="Personal Folder",
                external_group_id=user.source_user_id,
                connector_name=Connectors.DROPBOX,
                group_type=RecordGroupType.DRIVE,
            )
            
            # Create permission for the user (OWNER)
            user_permission = Permission(
                email=user.email,
                type=PermissionType.OWNER,
                entity_type=EntityType.USER
            )
            
            # Append the record group and its associated permissions
            record_groups.append((record_group, [user_permission]))
        
        await self.data_entities_processor.on_new_record_groups(record_groups)
    
    async def run_incremental_sync(self) -> None:
        """Runs an incremental sync using the last known cursor."""
        self.logger.info("Starting Dropbox incremental sync.")
        sync_point_key = generate_record_sync_point_key(RecordType.DRIVE.value, "root", )
        sync_point = await self.record_sync_point.dropbox_cursor_sync_point(sync_point_key)
        
        cursor = sync_point.get('cursor') if sync_point else None
        if not cursor:
            self.logger.warning("No cursor found. Running a full sync instead.")
            await self.run_sync()
            return
            
        await self._sync_from_source(cursor=cursor)
        self.logger.info("Dropbox incremental sync completed.")

    async def get_signed_url(self, record: Record) -> Optional[str]:
        if not self.data_source: return None
        try:
            # Dropbox uses path or file ID for temporary links. ID is more robust.
            response = await self.data_source.files_get_temporary_link(path=record.external_record_id)
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
            media_type=record.mime_type,
            headers={"Content-Disposition": f"attachment; filename=\"{record.record_name}\""}
        )

    async def test_connection_and_access(self) -> bool:
        if not self.data_source: return False
        try:
            await self.data_source.users_get_current_account()
            self.logger.info("Dropbox connection test successful.")
            return True
        except Exception as e:
            self.logger.error(f"Dropbox connection test failed: {e}", exc_info=True)
            return False

    def handle_webhook_notification(self, notification: Dict) -> None:
        """Handles a webhook notification by triggering an incremental sync."""
        self.logger.info("Dropbox webhook received. Triggering incremental sync.")
        asyncio.create_task(self.run_incremental_sync())

    def cleanup(self) -> None:
        self.logger.info("Cleaning up Dropbox connector resources.")
        self.data_source = None

    @classmethod
    async def create_connector(
        cls, logger, arango_service: BaseArangoService, config_service: ConfigurationService
    ) -> "BaseConnector":
        data_entities_processor = DataSourceEntitiesProcessor(
            logger, arango_service, config_service
        )
        await data_entities_processor.initialize()
        return DropboxConnector(
            logger, data_entities_processor, arango_service, config_service
        )